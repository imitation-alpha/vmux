"""Optional token-usage + quota tracking via the tokscale CLI.

vmux shells out to tokscale (https://github.com/junhoyeo/tokscale) on a slow
cadence, normalizes its JSON into a stable snake_case contract, and serves it
to the apps over REST. Design mirrors push.py: the parsing/transition logic is
pure and unit-testable, the runtime class is isolated, and nothing here can
disturb the attention-router poll loop.

Stays a graceful no-op when tokscale isn't installed: the API reports
available:false with a reason and no subprocess is ever spawned.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from .config import Config

# tokscale's report scan is CPU-heavy (parallel Rust over every session log),
# so report commands get a generous cap; `usage` just hits provider APIs.
QUOTA_TIMEOUT = 30.0
REPORT_TIMEOUT = 120.0
MAX_STDOUT = 16 * 1024 * 1024

PERIODS = ("hourly", "daily", "monthly")

_TOTAL_KEYS = ("input", "output", "cache_read", "cache_write", "reasoning")


# --------------------------------------------------------------------------- #
# pure helpers — no I/O, fixture-tested
# --------------------------------------------------------------------------- #

def parse_reset(raw: Optional[str]) -> Optional[float]:
    """tokscale resets_at -> epoch seconds. Accepts full ISO-8601 (with offset
    or Z) and bare YYYY-MM-DD (Copilot), which parses as midnight UTC. None on
    anything else — callers fall back to showing the raw string."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    try:
        if len(s) == 10:
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _opt_num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _empty_totals() -> dict:
    t = {k: 0 for k in _TOTAL_KEYS}
    t.update({"total": 0, "cost": 0.0, "messages": 0})
    return t


def normalize_quota(raw) -> List[dict]:
    """tokscale `usage --json` (a list of provider blocks) -> contract quotas.
    Each block is normalized independently: a malformed one degrades to
    metrics:[] (or is dropped if it has no provider) without poisoning
    siblings."""
    out: List[dict] = []
    for block in (raw if isinstance(raw, list) else []):
        try:
            if not isinstance(block, dict):
                continue
            provider = str(block.get("provider") or "").strip()
            if not provider:
                continue
            metrics: List[dict] = []
            raw_metrics = block.get("metrics")
            for m in (raw_metrics if isinstance(raw_metrics, list) else []):
                try:
                    if not isinstance(m, dict) or not m.get("label"):
                        continue
                    resets_raw = m.get("resets_at")
                    resets_raw = str(resets_raw) if resets_raw is not None else None
                    label = m.get("remaining_label")
                    metrics.append({
                        "label": str(m["label"]),
                        "used_percent": _opt_num(m.get("used_percent")),
                        "remaining_percent": _opt_num(m.get("remaining_percent")),
                        "remaining_label": str(label) if label is not None else None,
                        "resets_at": parse_reset(resets_raw),
                        "resets_at_raw": resets_raw,
                    })
                except Exception:
                    continue
            plan = block.get("plan")
            email = block.get("email")
            out.append({
                "provider": provider,
                "plan": str(plan) if plan is not None else None,
                "account": str(email) if email is not None else None,
                "metrics": metrics,
            })
        except Exception:
            continue
    return out


def _entry_totals(e: dict) -> dict:
    t = {
        "input": int(_num(e.get("input"))),
        "output": int(_num(e.get("output"))),
        "cache_read": int(_num(e.get("cacheRead"))),
        "cache_write": int(_num(e.get("cacheWrite"))),
        "reasoning": int(_num(e.get("reasoning"))),
        "cost": _num(e.get("cost")),
        "messages": int(_num(e.get("messageCount"))),
    }
    t["total"] = sum(t[k] for k in _TOTAL_KEYS)
    return t


def _bucket(label: str, totals: dict, by_client=None, by_model=None,
            clients=None, models=None) -> dict:
    return {
        "bucket": label,
        "totals": totals,
        "by_client": by_client or [],
        "by_model": by_model or [],
        "clients": clients or [],
        "models": models or [],
    }


def normalize_hourly(raw) -> List[dict]:
    out = []
    entries = (raw or {}).get("entries") if isinstance(raw, dict) else None
    for e in (entries if isinstance(entries, list) else []):
        try:
            out.append(_bucket(
                str(e["hour"]), _entry_totals(e),
                clients=[str(c) for c in e.get("clients") or []],
                models=[str(m) for m in e.get("models") or []],
            ))
        except Exception:
            continue
    return sorted(out, key=lambda b: b["bucket"])


def normalize_monthly(raw) -> List[dict]:
    out = []
    entries = (raw or {}).get("entries") if isinstance(raw, dict) else None
    for e in (entries if isinstance(entries, list) else []):
        try:
            out.append(_bucket(
                str(e["month"]), _entry_totals(e),
                models=[str(m) for m in e.get("models") or []],
            ))
        except Exception:
            continue
    return sorted(out, key=lambda b: b["bucket"])


def normalize_graph(raw) -> List[dict]:
    """tokscale `graph` contributions -> daily buckets with per-client and
    per-model aggregations (graph is the only source that has them)."""
    out = []
    contribs = (raw or {}).get("contributions") if isinstance(raw, dict) else None
    for day in (contribs if isinstance(contribs, list) else []):
        try:
            brk = day.get("tokenBreakdown") or {}
            day_totals = day.get("totals") or {}
            totals = {
                "input": int(_num(brk.get("input"))),
                "output": int(_num(brk.get("output"))),
                "cache_read": int(_num(brk.get("cacheRead"))),
                "cache_write": int(_num(brk.get("cacheWrite"))),
                "reasoning": int(_num(brk.get("reasoning"))),
                "total": int(_num(day_totals.get("tokens"))),
                "cost": _num(day_totals.get("cost")),
                "messages": int(_num(day_totals.get("messages"))),
            }
            by_client: Dict[str, dict] = {}
            by_model: Dict[str, dict] = {}
            for c in day.get("clients") or []:
                try:
                    toks = c.get("tokens") or {}
                    n = sum(int(_num(toks.get(k))) for k in
                            ("input", "output", "cacheRead", "cacheWrite", "reasoning"))
                    cost = _num(c.get("cost"))
                    msgs = int(_num(c.get("messages")))
                    ckey = str(c.get("client") or "unknown")
                    mkey = str(c.get("modelId") or "unknown")
                    for table, key, field in ((by_client, ckey, "client"),
                                              (by_model, mkey, "model")):
                        row = table.setdefault(key, {field: key, "cost": 0.0,
                                                     "total": 0, "messages": 0})
                        row["cost"] += cost
                        row["total"] += n
                        row["messages"] += msgs
                except Exception:
                    continue
            out.append(_bucket(
                str(day["date"]), totals,
                by_client=sorted(by_client.values(), key=lambda r: -r["cost"]),
                by_model=sorted(by_model.values(), key=lambda r: -r["cost"]),
                clients=sorted(by_client.keys()),
                models=sorted(by_model.keys()),
            ))
        except Exception:
            continue
    return sorted(out, key=lambda b: b["bucket"])


def build_daily_summary(daily_buckets: List[dict], today: date) -> Optional[dict]:
    """Today's totals + vs-yesterday delta + top models/clients by cost."""
    by_date = {b["bucket"]: b for b in daily_buckets or []}
    t = by_date.get(today.isoformat())
    if t is None:
        return None
    y = by_date.get((today - timedelta(days=1)).isoformat())
    delta = None
    if y and y["totals"]["cost"] > 0:
        delta = round(
            (t["totals"]["cost"] - y["totals"]["cost"]) / y["totals"]["cost"] * 100.0, 1
        )
    return {
        "date": today.isoformat(),
        "totals": dict(t["totals"]),
        "yesterday": dict(y["totals"]) if y else None,
        "cost_delta_pct": delta,
        "top_models": t["by_model"][:3],
        "top_clients": t["by_client"][:3],
    }


def collect_quota_alerts(
    prev: List[dict],
    new: List[dict],
    alerted: Dict[str, float],
    now: float,
    *,
    threshold: float,
    cooldown: float = 3600.0,
) -> List[dict]:
    """Pure transition detector: which quota metrics just dropped below the
    threshold? Fires only on an *observed* crossing (metric was previously
    known above the threshold), so a vmux restart doesn't re-alert for every
    quota that was already low. `alerted` is updated in place; an entry is
    dropped once its metric climbs back above the threshold (quota reset), so
    the next exhaustion re-alerts."""
    if threshold <= 0:
        return []

    def pct_map(quotas: List[dict]) -> Dict[str, dict]:
        out = {}
        for q in quotas or []:
            for m in q.get("metrics", []):
                if m.get("remaining_percent") is None:
                    continue
                out["%s|%s" % (q["provider"], m["label"])] = {
                    "provider": q["provider"], **m,
                }
        return out

    before, after = pct_map(prev), pct_map(new)
    alerts: List[dict] = []
    for key, m in after.items():
        pct = m["remaining_percent"]
        if pct > threshold:
            alerted.pop(key, None)   # re-arm after a quota reset
            continue
        old = before.get(key)
        if old is None or old["remaining_percent"] <= threshold:
            continue
        if key in alerted and now - alerted[key] < cooldown:
            continue
        alerted[key] = now
        alerts.append(m)

    for key in list(alerted):
        if key not in after:
            del alerted[key]
    return alerts


def fmt_reset(resets_at: Optional[float], resets_at_raw: Optional[str],
              now: Optional[float] = None) -> str:
    """'resets in 3h 12m' / 'resets in 4d' / falls back to the raw string."""
    if resets_at:
        secs = resets_at - (now if now is not None else time.time())
        if secs > 0:
            if secs >= 48 * 3600:
                return "resets in %dd" % (secs // 86400)
            h, m = int(secs // 3600), int(secs % 3600 // 60)
            return "resets in %dh %02dm" % (h, m) if h else "resets in %dm" % max(m, 1)
    return ("resets %s" % resets_at_raw) if resets_at_raw else ""


# --------------------------------------------------------------------------- #
# the collector
# --------------------------------------------------------------------------- #

class UsageCollector:
    """Owns the tokscale caches and the slow background refresh loop.

    Four slots (quota / hourly / daily / monthly), each holding last-good data:
    a failed refresh keeps serving the previous payload flagged stale=True.
    """

    def __init__(self, cfg: Config, push=None):
        self.cfg = cfg
        self.push = push                      # PushManager or None
        self.slots: Dict[str, dict] = {
            k: {"data": None, "fetched_at": 0.0, "error": "", "stale": False}
            for k in ("quota", "hourly", "daily", "monthly")
        }
        self._alerted: Dict[str, float] = {}
        # Created lazily so asyncio primitives bind to the active server loop.
        # at construction, and UsageCollector is built before the server loop exists
        self._lock: Optional[asyncio.Lock] = None
        self._wake: Optional[asyncio.Event] = None
        self._stop = False
        self._warned_version = False

    # -- command resolution -------------------------------------------------- #
    def argv(self) -> List[str]:
        try:
            return shlex.split(self.cfg.usage_command or "")
        except ValueError:
            return []

    def resolved_path(self) -> Optional[str]:
        argv = self.argv()
        return shutil.which(argv[0]) if argv else None

    # -- subprocess (arg list, never shell — same invariant as tmux.py) ------ #
    async def _exec(self, extra_args: List[str], timeout: float):
        argv = self.argv()
        exe = shutil.which(argv[0]) if argv else None
        if exe is None:
            raise FileNotFoundError("tokscale not found (usage.command=%r)"
                                    % self.cfg.usage_command)
        proc = await asyncio.create_subprocess_exec(
            exe, *argv[1:], *extra_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError("tokscale %s timed out after %.0fs"
                               % (extra_args[0] if extra_args else "", timeout))
        if proc.returncode != 0:
            msg = (err or b"").decode("utf-8", "replace").strip()
            raise RuntimeError("tokscale exited %d: %s" % (proc.returncode, msg[:300]))
        if len(out) > MAX_STDOUT:
            raise RuntimeError("tokscale output too large (%d bytes)" % len(out))
        return json.loads(out)

    # -- refreshes ------------------------------------------------------------ #
    async def _refresh_slot(self, name: str, args: List[str], timeout: float,
                            normalize) -> bool:
        slot = self.slots[name]
        try:
            raw = await self._exec(args, timeout)
            data = normalize(raw)
            slot.update(data=data, fetched_at=time.time(), error="", stale=False)
            if name == "daily":
                self._check_version(raw)
            return True
        except FileNotFoundError as exc:
            slot.update(error=str(exc), stale=slot["data"] is not None)
        except TimeoutError as exc:
            slot.update(error=str(exc), stale=slot["data"] is not None)
        except Exception as exc:
            slot.update(error="%s" % exc, stale=slot["data"] is not None)
        return False

    def _check_version(self, raw) -> None:
        if self._warned_version:
            return
        try:
            ver = str((raw.get("meta") or {}).get("version", ""))
            if ver and ver.split(".")[0] != "3":
                print("[vmux] usage: tokscale %s detected; parsers were written "
                      "against 3.x — output may degrade" % ver)
                self._warned_version = True
        except Exception:
            pass

    async def refresh_quota(self) -> None:
        prev = self.slots["quota"]["data"] or []
        # `usage` rejects --no-spinner (report-only flag), so no extra args here.
        if await self._refresh_slot("quota", ["usage", "--json"],
                                    QUOTA_TIMEOUT, normalize_quota):
            self._fire_alerts(prev, self.slots["quota"]["data"] or [])

    async def refresh_reports(self, *, include_monthly: bool) -> None:
        # Sequential on purpose: each scan is an ~18s CPU burst; never stack them.
        await self._refresh_slot("hourly", ["hourly", "--json", "--today", "--no-spinner"],
                                 REPORT_TIMEOUT, normalize_hourly)
        today = date.today()
        since = (today - timedelta(days=29)).isoformat()
        await self._refresh_slot(
            "daily",
            ["graph", "--since", since, "--until", today.isoformat(), "--no-spinner"],
            REPORT_TIMEOUT, normalize_graph)
        if include_monthly:
            await self._refresh_slot("monthly", ["monthly", "--json", "--no-spinner"],
                                     REPORT_TIMEOUT, normalize_monthly)

    def _fire_alerts(self, prev: List[dict], new: List[dict]) -> None:
        if self.push is None:
            return
        try:
            alerts = collect_quota_alerts(
                prev, new, self._alerted, time.time(),
                threshold=self.cfg.usage_alert_threshold,
            )
            for _alert in alerts:
                # Provider names, quota labels, percentages, and reset times can
                # reveal account and usage details. Keep APNs generic and let
                # the authenticated app fetch current usage after it opens.
                self.push.fire_message(
                    "vmux", "Usage needs your attention.",
                    thread="vmux-usage",
                    extra={"type": "quota"},
                )
        except Exception as exc:   # alerts must never break the refresh path
            print("[vmux] usage alert error:", exc)

    async def refresh(self, scope: str = "all") -> None:
        """Forced refresh (the POST endpoint). The lock coalesces concurrent
        callers: the second waits for the first pass and then skips re-running
        anything refreshed seconds ago."""
        if not self.cfg.usage_enabled:
            return
        async with self._refresh_lock():
            now = time.time()
            if scope in ("quota", "all") and now - self.slots["quota"]["fetched_at"] > 2:
                await self.refresh_quota()
            if scope in ("reports", "all") and now - self.slots["daily"]["fetched_at"] > 2:
                await self.refresh_reports(include_monthly=True)

    def _refresh_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # -- the background loop -------------------------------------------------- #
    async def run(self) -> None:
        if self._wake is None:
            self._wake = asyncio.Event()
        await asyncio.sleep(5)   # let the first pane poll win the startup race
        while not self._stop:
            try:
                now = time.time()
                if now - self.slots["quota"]["fetched_at"] >= self.cfg.usage_quota_refresh:
                    async with self._refresh_lock():
                        await self.refresh_quota()
                if now - self.slots["daily"]["fetched_at"] >= self.cfg.usage_report_refresh:
                    async with self._refresh_lock():
                        monthly_age = now - self.slots["monthly"]["fetched_at"]
                        await self.refresh_reports(
                            include_monthly=monthly_age >= 6 * self.cfg.usage_report_refresh)
            except Exception as exc:   # a bad tick never kills the loop
                print("[vmux] usage refresh error:", exc)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=15)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass

    def kick(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def stop(self) -> None:
        self._stop = True
        if self._wake is not None:
            self._wake.set()

    # -- payloads -------------------------------------------------------------- #
    def _availability(self) -> Optional[dict]:
        """None when data can be served; otherwise the available:false stub."""
        if not self.cfg.usage_enabled:
            return self._unavailable("disabled", "usage tracking is disabled in config")
        if self.resolved_path() is None:
            return self._unavailable(
                "not_installed",
                "tokscale not found — install with `npm i -g tokscale` or "
                "`bun i -g tokscale` (or set usage.command to an absolute path)")
        return None

    @staticmethod
    def _unavailable(reason: str, detail: str) -> dict:
        return {"available": False, "reason": reason, "detail": detail,
                "fetched_at": 0.0, "stale": False}

    def _error_stub(self, slot: dict) -> dict:
        reason = "timeout" if "timed out" in (slot["error"] or "") else "error"
        return self._unavailable(reason, slot["error"] or "no data yet")

    def usage_payload(self) -> dict:
        stub = self._availability()
        if stub is not None:
            return dict(stub, quotas=[], today=None)
        q, d = self.slots["quota"], self.slots["daily"]
        if q["data"] is None and d["data"] is None:
            err = q if q["error"] else d
            return dict(self._error_stub(err) if err["error"] else
                        self._unavailable("error", "no data yet"),
                        quotas=[], today=None)
        return {
            "available": True, "reason": None, "detail": None,
            "fetched_at": q["fetched_at"] or d["fetched_at"],
            "stale": bool(q["stale"] or d["stale"]),
            "quotas": q["data"] or [],
            "today": build_daily_summary(d["data"] or [], date.today()),
        }

    def history_payload(self, period: str, days: Optional[int] = None) -> dict:
        if period not in PERIODS:
            raise ValueError("bad period: %s" % period)
        stub = self._availability()
        if stub is not None:
            return dict(stub, period=period, buckets=[])
        slot = self.slots[period]
        if slot["data"] is None:
            base = self._error_stub(slot) if slot["error"] else \
                self._unavailable("error", "no data yet")
            return dict(base, period=period, buckets=[])
        buckets = slot["data"]
        if period == "daily" and days:
            buckets = buckets[-max(1, min(30, int(days))):]
        return {
            "available": True, "reason": None, "detail": None, "period": period,
            "fetched_at": slot["fetched_at"], "stale": slot["stale"],
            "buckets": buckets,
        }

    def info(self) -> dict:
        now = time.time()

        def age(slot):
            ts = self.slots[slot]["fetched_at"]
            return round(now - ts, 1) if ts else None

        return {
            "enabled": bool(self.cfg.usage_enabled),
            "installed": self.resolved_path() is not None,
            "resolved_path": self.resolved_path(),
            "quota_age": age("quota"),
            "reports_age": age("daily"),
            "last_error": next((self.slots[s]["error"] for s in self.slots
                                if self.slots[s]["error"]), ""),
        }

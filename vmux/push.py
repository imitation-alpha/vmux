"""Optional APNs push: alert your phone the moment a pane starts needing you.

Stays a silent no-op unless BOTH are true:
  * config.yaml has a `push:` section pointing at a local APNs auth key
  * the optional deps are installed:  pip install 'vmux-agent[push]'

Design mirrors the rest of vmux: the transition logic is pure and unit-testable
(`collect_alerts`), the network client is lazy (imported only when actually
sending), and a failure to push never disturbs the poll loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Dict, List, Optional

from .config import Config
from .models import STATUS_ERROR, STATUS_NEEDS_INPUT, PaneState

APNS_HOSTS = {
    "sandbox": "https://api.sandbox.push.apple.com",
    "production": "https://api.push.apple.com",
}

# APNs device tokens are hex (32 bytes today, Apple says treat length as opaque).
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{16,200}$")

# Refresh the provider JWT well inside Apple's 20–60 minute validity window.
_JWT_MAX_AGE = 40 * 60


def valid_device_token(token: str) -> bool:
    return bool(_TOKEN_RE.match(token or ""))


class DeviceRegistry:
    """Registered device tokens, persisted as JSON next to the settings overlay."""

    def __init__(self, path: Optional[str]):
        self.path = path
        self.devices: List[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as fh:
                data = json.load(fh)
            self.devices = [d for d in data.get("devices", []) if valid_device_token(d.get("token", ""))]
        except (OSError, ValueError):
            self.devices = []

    def _save(self) -> None:
        if not self.path:
            return
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"devices": self.devices}, fh, indent=2)
        os.replace(tmp, self.path)  # atomic

    def add(self, token: str, name: str = "", platform: str = "ios") -> bool:
        """Register a token (idempotent). Returns True if it was new."""
        if not valid_device_token(token):
            raise ValueError("bad device token")
        for d in self.devices:
            if d["token"] == token:
                d["name"] = (name or d.get("name") or "")[:80]
                self._save()
                return False
        self.devices.append({
            "token": token,
            "name": (name or "")[:80],
            "platform": (platform or "ios")[:20],
            "added": time.time(),
        })
        self._save()
        return True

    def remove(self, token: str) -> bool:
        before = len(self.devices)
        self.devices = [d for d in self.devices if d["token"] != token]
        if len(self.devices) != before:
            self._save()
            return True
        return False

    def tokens(self) -> List[str]:
        return [d["token"] for d in self.devices]

    def public_list(self) -> List[dict]:
        """Device list safe to show in the settings UI (token truncated)."""
        return [
            {"token": d["token"][:8] + "…", "name": d.get("name", ""),
             "platform": d.get("platform", ""), "added": d.get("added", 0)}
            for d in self.devices
        ]


def collect_alerts(
    prev: Dict[str, PaneState],
    new: Dict[str, PaneState],
    last_alert: Dict[str, float],
    now: float,
    *,
    alert_on_error: bool = False,
    cooldown: float = 30.0,
) -> List[PaneState]:
    """Pure transition detector: which panes just started wanting a human?

    Alerts only on an *observed* transition (pane was previously known with a
    different status), so a vmux restart doesn't re-alert for every pane that
    was already waiting. `last_alert` is updated in place with `now` for each
    alerted pane; entries for vanished panes are dropped.
    """
    wanted = {STATUS_NEEDS_INPUT}
    if alert_on_error:
        wanted.add(STATUS_ERROR)

    alerts: List[PaneState] = []
    for pid, st in new.items():
        if st.status not in wanted:
            continue
        before = prev.get(pid)
        if before is None or before.status == st.status:
            continue
        if pid in last_alert and now - last_alert[pid] < cooldown:
            continue
        last_alert[pid] = now
        alerts.append(st)

    for pid in list(last_alert):
        if pid not in new:
            del last_alert[pid]
    return alerts


def _is_yes(label: str) -> bool:
    s = (label or "").strip().lower()
    return s == "y" or s.startswith("yes")


def _is_no(label: str) -> bool:
    s = (label or "").strip().lower()
    return s == "n" or s.startswith("no") or s.startswith("cancel")


def classify_category(menu) -> str:
    """Pick the APNs notification category for a parsed menu.

    iOS fixes notification-action *titles* when the app registers categories, so
    the server can only *choose* a category per push. We map the common Claude
    Code shapes to friendly confirm categories ("Yes"/"No"…) and fall back to a
    numbered menu (the push body carries a legend) for anything ambiguous. Pure
    and unit-testable, in the spirit of `collect_alerts`.
    """
    opts = list(menu or [])
    n = len(opts)
    if n == 0:
        return "vmux.generic"
    labels = [o.label for o in opts]
    if n == 2 and _is_yes(labels[0]) and _is_no(labels[1]):
        return "vmux.confirm2"
    if n == 3 and _is_yes(labels[0]) and _is_no(labels[2]):
        return "vmux.confirm3"
    return "vmux.menu"


class PushManager:
    """Owns the registry + transition state and sends APNs alerts.

    `configured` — the YAML push section is filled in.
    `available`  — the optional deps (httpx, PyJWT) import cleanly.
    Sending happens only when both hold and at least one device registered.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.registry = DeviceRegistry(cfg.push_store_path)
        self._last_alert: Dict[str, float] = {}
        self._jwt: str = ""
        self._jwt_ts: float = 0.0
        self._client = None          # lazy httpx.AsyncClient
        self._warned = False

    @property
    def configured(self) -> bool:
        c = self.cfg
        return bool(c.apns_key_path and c.apns_key_id and c.apns_team_id and c.apns_topic)

    @property
    def available(self) -> bool:
        try:
            import httpx  # noqa: F401
            import jwt  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def can_send(self) -> bool:
        return self.configured and self.available and bool(self.registry.devices)

    def info(self) -> dict:
        return {
            "configured": self.configured,
            "available": self.available,
            "enabled": self.configured and self.available,
            "devices": len(self.registry.devices),
        }

    # -- transition tracking (called from the poll loop) -------------------- #
    def collect(self, prev: Dict[str, PaneState], new: Dict[str, PaneState]) -> List[PaneState]:
        if not (self.configured and self.available):
            if self.configured and not self.available and not self._warned:
                print("[vmux] push: configured but deps missing — pip install 'vmux-agent[push]'")
                self._warned = True
            return []
        return collect_alerts(
            prev, new, self._last_alert, time.time(),
            alert_on_error=self.cfg.push_on_error,
            cooldown=self.cfg.push_cooldown,
        )

    def fire(self, alerts: List[PaneState]) -> None:
        """Schedule sends without blocking the poll loop. Never raises."""
        if not alerts or not self.can_send:
            return
        try:
            asyncio.get_running_loop().create_task(
                self._send_payloads([self._payload(p) for p in alerts]))
        except RuntimeError:
            pass  # no loop (unit tests) — sending is best-effort anyway

    def fire_message(self, title: str, body: str, *, thread: str = "vmux",
                     extra: Optional[dict] = None) -> None:
        """Schedule a one-off alert that isn't tied to a pane (e.g. a quota
        warning from the usage collector). Same best-effort semantics as
        fire(): silent no-op unless push is fully configured."""
        if not self.can_send:
            return
        payload = {
            "aps": {
                "alert": {"title": title[:120], "body": body[:500]},
                "sound": "default",
                "thread-id": thread,
                "interruption-level": "time-sensitive",
            },
            "vmux": extra or {},
        }
        try:
            asyncio.get_running_loop().create_task(self._send_payloads([payload]))
        except RuntimeError:
            pass

    # -- APNs plumbing ------------------------------------------------------ #
    def _provider_jwt(self) -> str:
        now = time.time()
        if self._jwt and now - self._jwt_ts < _JWT_MAX_AGE:
            return self._jwt
        import jwt as pyjwt
        with open(self.cfg.apns_key_path, "r") as fh:
            key = fh.read()
        self._jwt = pyjwt.encode(
            {"iss": self.cfg.apns_team_id, "iat": int(now)},
            key, algorithm="ES256", headers={"kid": self.cfg.apns_key_id},
        )
        self._jwt_ts = now
        return self._jwt

    def _payload(self, pane: PaneState) -> dict:
        if pane.status == STATUS_ERROR:
            body = "Error detected"
        else:
            body = (pane.question or "").strip() or "Needs your input"
        category = classify_category(pane.menu)
        # Numbered menus get opaque "1/2/3" buttons, so spell the options out in
        # the body; confirm2/confirm3 use friendly titles and need no legend.
        if category == "vmux.menu" and pane.menu:
            legend = "  ".join(
                "%d) %s" % (i + 1, (o.label or "").strip()) for i, o in enumerate(pane.menu[:6])
            )
            body = (body + "\n" + legend) if body else legend
        return {
            "aps": {
                "alert": {"title": pane.name or pane.target, "body": body[:500]},
                "sound": "default",
                "thread-id": pane.target,
                "interruption-level": "time-sensitive",
                "relevance-score": 1.0 if pane.status == STATUS_NEEDS_INPUT else 0.5,
                "category": category,
            },
            "vmux": {
                "id": pane.id,
                "target": pane.target,
                "status": pane.status,
                # action ids vmux.opt.<i> index into this list -> key -> /api/select
                "menu": [
                    {"i": i, "key": o.key, "label": (o.label or "")[:40]}
                    for i, o in enumerate(pane.menu)
                ],
            },
        }

    async def _send_payloads(self, payloads: List[dict]) -> None:
        try:
            import httpx
            if self._client is None:
                self._client = httpx.AsyncClient(
                    http2=True,
                    base_url=APNS_HOSTS.get(self.cfg.apns_environment, APNS_HOSTS["sandbox"]),
                    timeout=10.0,
                )
            headers = {
                "authorization": "bearer " + self._provider_jwt(),
                "apns-topic": self.cfg.apns_topic,
                "apns-push-type": "alert",
                "apns-priority": "10",
            }
            for payload in payloads:
                for token in self.registry.tokens():
                    try:
                        r = await self._client.post("/3/device/" + token, json=payload, headers=headers)
                        if r.status_code in (400, 410):
                            reason = ""
                            try:
                                reason = r.json().get("reason", "")
                            except ValueError:
                                pass
                            if reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
                                self.registry.remove(token)
                                print("[vmux] push: dropped dead device token (%s)" % reason)
                        elif r.status_code != 200:
                            print("[vmux] push: APNs %s: %s" % (r.status_code, r.text[:200]))
                    except Exception as exc:
                        print("[vmux] push: send failed: %s" % exc)
        except Exception as exc:  # never let push trouble reach the poll loop
            print("[vmux] push error:", exc)

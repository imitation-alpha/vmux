"""The live loop: capture every tracked pane, detect status, broadcast diffs.

A single Hub owns the latest snapshot and the set of connected websockets. The
loop wakes every `poll_interval`, or immediately when an action calls `kick()`
(so tapping a button feels instant instead of waiting for the next tick).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Dict, List, Optional

from . import tmux
from .agents.models import PaneObservation, fingerprint_terminal
from .agents.observers import runtime_from_command
from .agents.service import AgentService
from .config import Config
from .detectors import classify_kind, detect, is_spinner
from .models import (
    KIND_CLAUDE,
    KIND_CODEX,
    STATUS_OFFLINE,
    PaneState,
)
from .naming import SmartNamer
from .push import PushManager


def _strip_spinner(s: str) -> str:
    t = (s or "").strip()
    while t and is_spinner(t[0]):
        t = t[1:].strip()
    return t


_TARGET_RE = re.compile(r"^(.+):([0-9]+)\.([0-9]+)$")


def _target_parts(target: str) -> Optional[tuple[str, str, str]]:
    m = _TARGET_RE.match(target or "")
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def choose_name(mode, *, title, window, target, command, override_name, smart_name=None):
    """Pick a pane's display name. A manual override always wins; otherwise the
    chosen source (spinner-stripped where it's a title); empty -> target."""
    if override_name:
        return override_name
    parts = _target_parts(target)
    if mode == "pane":
        cand = parts[2] if parts else target
    elif mode == "window_pane":
        cand = "%s:%s" % (parts[1], parts[2]) if parts else target
    elif mode == "session_pane":
        cand = "%s:%s" % (parts[0], parts[2]) if parts else target
    elif mode == "session_window_pane":
        cand = "%s:%s:%s" % parts if parts else target
    elif mode == "window":
        cand = _strip_spinner(window)
    elif mode == "target":
        cand = target
    elif mode == "command":
        cand = (command or "").split("/")[-1]
    elif mode == "smart":
        cand = smart_name
    else:  # "title" (default)
        cand = _strip_spinner(title)
    return cand or target


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


class Hub:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.states: Dict[str, PaneState] = {}
        self.order: List[str] = []
        self.clients: Dict[str, dict] = {}   # sid -> {ws, ip, ua, ts}
        self._meta: Dict[str, dict] = {}   # id -> {hash, updated}
        self.interactions: Dict[str, float] = {}   # pane id -> epoch of last user send
        self.push = PushManager(cfg)
        self.agents = AgentService(cfg, push=self.push, kick=self.kick)
        # Created in run() so asyncio.Event binds to the active server loop.
        # at construction, and Hub is built before the server loop exists
        self._wake: Optional[asyncio.Event] = None
        self._stop = False
        self.namer = SmartNamer(cfg, on_update=self.kick)

    def mark_interaction(self, pane_id: str) -> None:
        """Record that the user just sent input to this pane (for the 'recently sent' sort)."""
        self.interactions[pane_id] = time.time()

    # -- selection of which panes to show ---------------------------------- #
    def _included(self, pane: dict, kind: str) -> bool:
        target = pane["target"]
        if target in self.cfg.overrides:
            return True
        if not self.cfg.auto_discover:
            return False
        if kind == "shell" and not self.cfg.include_shells:
            return False
        return True

    # -- one polling pass --------------------------------------------------- #
    async def poll_once(self) -> None:
        panes = await asyncio.to_thread(tmux.list_panes)
        present_targets = {p["target"] for p in panes}

        # capture all panes concurrently (with configured scrollback depth)
        captures = await asyncio.gather(
            *[asyncio.to_thread(tmux.capture, p["id"], self.cfg.capture_lines) for p in panes]
        )

        now = time.time()
        new_states: Dict[str, PaneState] = {}
        new_order: List[str] = []
        agent_observations: List[PaneObservation] = []

        for pane, text in zip(panes, captures):
            pid = pane["id"]
            target = pane["target"]
            override = self.cfg.overrides.get(target)
            text = text or ""

            kind = (override.kind if override and override.kind
                    else classify_kind(pane["cmd"], pane["title"], text))

            digest = _hash(text)
            prev = self._meta.get(pid)
            changed = prev is None or prev["hash"] != digest
            updated = now if changed else (prev["updated"] if prev else now)
            self._meta[pid] = {"hash": digest, "updated": updated}

            res = detect(text, kind, changed, self.cfg, pane["title"])
            runtime = runtime_from_command(pane["cmd"])
            if runtime is None:
                runtime = {
                    KIND_CLAUDE: "claude",
                    KIND_CODEX: "codex",
                }.get(kind)
            if runtime in ("codex", "claude"):
                try:
                    pane_created = float(pane.get("created") or 0)
                except (TypeError, ValueError):
                    pane_created = 0.0
                agent_observations.append(PaneObservation(
                    pane_id=pid,
                    target=target,
                    command=pane["cmd"],
                    title=pane["title"],
                    cwd=pane.get("path", ""),
                    pid=str(pane.get("pid", "")),
                    pane_created=pane_created,
                    runtime=runtime,
                    status=res.status,
                    question=res.question,
                    menu=tuple(item.to_dict() for item in res.menu_list()),
                    prompt_fingerprint=fingerprint_terminal(text),
                    observed_at=now,
                ))

            # Agent observation is independent of whether the terminal pane is
            # included in the pane workspace/navigation.
            if not self._included(pane, kind):
                continue

            override_name = override.name if override else None
            smart_name = None
            if self.cfg.naming_mode == "smart" and not override_name:
                smart_name = self.namer.name(pane, text, target)
            name = choose_name(
                self.cfg.naming_mode,
                title=pane["title"], window=pane.get("window", ""),
                target=target, command=pane["cmd"],
                override_name=override_name,
                smart_name=smart_name,
            )

            st = PaneState(
                id=pid,
                target=target,
                name=name,
                kind=kind,
                status=res.status,
                title=pane["title"],
                question=res.question,
                menu=res.menu_list(),
                lines=text.splitlines(),
                updated=updated,
                changed=changed,
                window=pane.get("window", ""),
                starred=bool(override and override.star),
                interacted=self.interactions.get(pid, 0.0),
            )
            new_states[pid] = st
            new_order.append(pid)

        # configured panes that aren't present right now -> offline cards
        for target, ov in self.cfg.overrides.items():
            if target in present_targets:
                continue
            pid = "cfg:" + target
            new_states[pid] = PaneState(
                id=pid,
                target=target,
                name=ov.name or target,
                kind=ov.kind or "generic",
                status=STATUS_OFFLINE,
                starred=ov.star,
            )
            new_order.append(pid)

        review_policy = self.agents.review_notification_policy()
        alerts = self.push.collect(
            self.states,
            new_states,
            alert_on_needs_input=not review_policy["batching_enabled"],
            alert_on_error=review_policy["urgent_pane_errors"],
        )
        self.states = new_states
        self.order = new_order
        schedule_now = time.time()
        if self.agents.review_schedule_is_due(now=schedule_now):
            # A due queue must include semantic events visible in this same
            # pane capture. Serial processing also prevents an older queued
            # batch from being applied after the due-window snapshot.
            await self.agents.process_now(agent_observations)
        else:
            self.agents.submit(agent_observations)
        # drop interaction timestamps for panes that no longer exist
        self.interactions = {k: v for k, v in self.interactions.items() if k in new_states}
        self.push.fire(alerts)   # async, best-effort; never blocks the poll
        # Use the same clock sample as the due check above. If the boundary is
        # crossed during this tick, the next tick ingests first and then claims.
        self._process_review_schedule(now=schedule_now)

    # -- snapshot + broadcast ---------------------------------------------- #
    def snapshot(self) -> dict:
        return {
            "type": "state",
            "panes": [self.states[pid].to_dict() for pid in self.order if pid in self.states],
        }

    def review_payload(self, *, now: Optional[float] = None) -> dict:
        """Combine durable agent review state with safe live pane references."""
        panes = [self.states[pid] for pid in self.order if pid in self.states]
        return self.agents.review_payload(panes, now=now)

    def _process_review_schedule(self, *, now: Optional[float] = None) -> None:
        """Claim due review windows and fan out one generic invalidation/digest."""
        if not self.agents.enabled:
            return
        settings = self.agents.get_review_settings()
        next_due_at = settings.get("next_due_at")
        schedule_now = float(now if now is not None else time.time())
        if (
            not settings.get("enabled")
            or next_due_at is None
            or schedule_now < float(next_due_at)
        ):
            return
        payload = self.review_payload(now=schedule_now)
        if not payload["due"]["is_due"]:
            return
        claimed = self.agents.claim_review_due(
            has_work=payload["due"]["has_work"],
            now=payload["generated_at"],
        )
        if not claimed["claimed"] or not claimed["has_work"]:
            return
        self.push.fire_review_digest()
        self.agents.publish(
            "review_due",
            "",
            0,
            resources=["review"],
        )

    async def broadcast(self) -> None:
        if not self.clients:
            return
        payload = self.snapshot()
        dead = []
        for sid, c in list(self.clients.items()):
            try:
                await c["ws"].send_json(payload)
            except Exception:
                dead.append(sid)
        for sid in dead:
            self.clients.pop(sid, None)

    # -- client/session tracking ------------------------------------------ #
    def add_client(self, sid, ws, ip, ua, ts):
        self.clients[sid] = {"ws": ws, "ip": ip, "ua": ua, "ts": ts}

    def remove_client(self, sid):
        self.clients.pop(sid, None)

    def sessions(self):
        now = time.time()
        return [
            {"id": sid, "ip": c["ip"], "ua": c["ua"], "age": round(now - c["ts"], 1)}
            for sid, c in self.clients.items()
        ]

    async def kill_client(self, sid):
        c = self.clients.get(sid)
        if not c:
            return False
        try:
            await c["ws"].close(code=4001)
        except Exception:
            pass
        self.clients.pop(sid, None)
        return True

    # -- action helpers (used by the API) ---------------------------------- #
    def resolve_id(self, pane_id: str) -> Optional[str]:
        """Map an incoming id to a real tmux target we can drive."""
        st = self.states.get(pane_id)
        if st and not pane_id.startswith("cfg:"):
            return st.id
        if tmux.valid_pane_id(pane_id):
            return pane_id
        return None

    def do_select(self, pane_id: str, key: str) -> None:
        st = self.states.get(pane_id)
        real = self.resolve_id(pane_id)
        if real is None:
            raise tmux.TmuxError("unknown pane")
        kind = st.kind if st else "generic"
        if kind == KIND_CLAUDE:
            tmux.send_chars(real, key)            # digit press selects the option
        elif kind == KIND_CODEX:
            option = next((item for item in (st.menu if st else []) if item.key == key), None)
            if key == "enter":
                tmux.send_key(real, "Enter")
            elif option is not None and option.freeform:
                # Stage "None of the above" without submitting so the web
                # composer can collect notes for the selected Codex answer.
                tmux.send_chars(real, key)
            else:
                tmux.send_chars(real, key)
                tmux.send_key(real, "Enter")
        elif key == "enter":
            tmux.send_key(real, "Enter")
        else:
            tmux.send_literal(real, key, enter=True)
        self.mark_interaction(real)

    def kick(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # -- main loop ---------------------------------------------------------- #
    async def run(self) -> None:
        if self._wake is None:
            self._wake = asyncio.Event()
        try:
            await self.agents.start()
        except Exception as exc:
            self.agents.disable("startup failed: %s" % type(exc).__name__)
            print("[vmux] agent context disabled:", exc)
        while not self._stop:
            try:
                await self.poll_once()
                await self.broadcast()
            except Exception as exc:  # never let one bad tick kill the loop
                print("[vmux] poll error:", exc)
                # A transient tmux capture failure must not starve already
                # persisted Review work. Atomic claims still prevent repeats.
                try:
                    self._process_review_schedule()
                except Exception as review_exc:
                    print("[vmux] review schedule error:", review_exc)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.poll_interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def stop(self) -> None:
        self._stop = True
        self.namer.stop()
        self.agents.stop()
        if self._wake is not None:
            self._wake.set()

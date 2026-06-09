"""The live loop: capture every tracked pane, detect status, broadcast diffs.

A single Hub owns the latest snapshot and the set of connected websockets. The
loop wakes every `poll_interval`, or immediately when an action calls `kick()`
(so tapping a button feels instant instead of waiting for the next tick).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Dict, List, Optional

from . import tmux
from .config import Config
from .detectors import classify_kind, detect, is_spinner
from .models import (
    KIND_CLAUDE,
    STATUS_OFFLINE,
    PaneState,
)


def _strip_spinner(s: str) -> str:
    t = (s or "").strip()
    while t and is_spinner(t[0]):
        t = t[1:].strip()
    return t


def choose_name(mode, *, title, window, target, command, override_name):
    """Pick a pane's display name. A manual override always wins; otherwise the
    chosen source (spinner-stripped where it's a title); empty -> target."""
    if override_name:
        return override_name
    if mode == "window":
        cand = _strip_spinner(window)
    elif mode == "target":
        cand = target
    elif mode == "command":
        cand = (command or "").split("/")[-1]
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
        self._wake = asyncio.Event()
        self._stop = False

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

        # capture all panes concurrently
        captures = await asyncio.gather(
            *[asyncio.to_thread(tmux.capture, p["id"]) for p in panes]
        )

        now = time.time()
        new_states: Dict[str, PaneState] = {}
        new_order: List[str] = []

        for pane, text in zip(panes, captures):
            pid = pane["id"]
            target = pane["target"]
            override = self.cfg.overrides.get(target)
            text = text or ""

            kind = (override.kind if override and override.kind
                    else classify_kind(pane["cmd"], pane["title"], text))

            if not self._included(pane, kind):
                continue

            digest = _hash(text)
            prev = self._meta.get(pid)
            changed = prev is None or prev["hash"] != digest
            updated = now if changed else (prev["updated"] if prev else now)
            self._meta[pid] = {"hash": digest, "updated": updated}

            res = detect(text, kind, changed, self.cfg, pane["title"])
            name = choose_name(
                self.cfg.naming_mode,
                title=pane["title"], window=pane.get("window", ""),
                target=target, command=pane["cmd"],
                override_name=(override.name if override else None),
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
            )
            new_order.append(pid)

        self.states = new_states
        self.order = new_order

    # -- snapshot + broadcast ---------------------------------------------- #
    def snapshot(self) -> dict:
        return {
            "type": "state",
            "panes": [self.states[pid].to_dict() for pid in self.order if pid in self.states],
        }

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
        elif key == "enter":
            tmux.send_key(real, "Enter")
        else:
            tmux.send_literal(real, key, enter=True)

    def kick(self) -> None:
        self._wake.set()

    # -- main loop ---------------------------------------------------------- #
    async def run(self) -> None:
        while not self._stop:
            try:
                await self.poll_once()
                await self.broadcast()
            except Exception as exc:  # never let one bad tick kill the loop
                print("[vmux] poll error:", exc)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.poll_interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

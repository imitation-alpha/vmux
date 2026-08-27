"""The live loop: capture every tracked pane, detect status, broadcast diffs.

A single Hub owns the latest snapshot and the set of connected websockets. The
loop wakes every `poll_interval`, or immediately when an action calls `kick()`
(so tapping a button feels instant instead of waiting for the next tick).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading
import time
from typing import Callable, Dict, List, Optional

from . import tmux
from .agents.controllers import TmuxRuntimeController
from .agents.models import PaneObservation, fingerprint_terminal
from .agents.observers import runtime_from_command
from .agents.service import AgentService
from .config import Config, save_overlay
from .detectors import DetectResult, agent_kind_from_cmd, classify_kind, detect, is_spinner
from .lifecycle import LifecycleEvidence, LifecycleKernel, project_legacy_status
from .models import (
    KIND_CLAUDE,
    KIND_CODEX,
    KIND_GENERIC,
    STATUS_IDLE,
    STATUS_WORKING,
    PaneState,
)
from .naming import SmartNamer
from .push import PushManager
from .workspaces import WorkspaceResolver


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


ACTIVITY_GRACE_SECONDS = 2.0


class Hub:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.states: Dict[str, PaneState] = {}
        self.order: List[str] = []
        self.clients: Dict[str, dict] = {}   # sid -> {ws, ip, ua, ts, revision}
        self._meta: Dict[str, dict] = {}   # id -> {hash, updated}
        self.interactions: Dict[str, float] = {}   # pane id -> epoch of last user send
        self._interaction_generations: Dict[str, int] = {}
        # Shells created through vmux stay visible even when general shell
        # discovery is disabled, so clients can open the successful result.
        self.created_panes = set()
        self.push = PushManager(cfg)
        self.workspaces = WorkspaceResolver(
            cfg.creation_roots,
            creation_enabled=cfg.creation_configured,
            creation_unavailable_reason=cfg.creation_setup_reason,
        )
        self.creation_workspace_resolver = self.workspaces
        self.agents = AgentService(
            cfg,
            push=self.push,
            kick=self.kick,
            workspace_resolver=self.workspaces,
            controller=TmuxRuntimeController(action_runner=self._perform_pane_action),
        )
        # Created in run() so asyncio.Event binds to the active server loop.
        # at construction, and Hub is built before the server loop exists
        self._wake: Optional[asyncio.Event] = None
        self._stop = False
        self._agent_startup_complete = False
        self.namer = SmartNamer(cfg, on_update=self.kick)
        self._snapshot_revision = 0
        self._snapshot_signature: Optional[tuple] = None
        self.lifecycle = LifecycleKernel()
        self._lifecycle_lock = threading.RLock()

    def mark_interaction(self, pane_id: str) -> None:
        """Record that the user just sent input to this pane (for the 'recently sent' sort)."""
        with self._lifecycle_lock:
            self.interactions[pane_id] = time.time()
            self._interaction_generations[pane_id] = (
                self._interaction_generations.get(pane_id, 0) + 1
            )

    def mark_created(self, pane_id: str) -> None:
        self.created_panes.add(pane_id)

    def lifecycle_diagnostics(self, pane_id: str, limit: int = 32) -> dict:
        with self._lifecycle_lock:
            if pane_id not in self.states:
                raise KeyError(pane_id)
            return self.lifecycle.diagnostics(pane_id, limit)

    def acknowledge_lifecycle(self, pane_id: str, expected_revision: int) -> dict:
        with self._lifecycle_lock:
            if pane_id not in self.states:
                raise KeyError(pane_id)
            summary = self.lifecycle.acknowledge(pane_id, expected_revision, now=time.time())
            state = self.states[pane_id]
            state.lifecycle = summary.to_dict()
            state.status = project_legacy_status(summary.state)
            self._update_snapshot_revision()
            result = self.lifecycle.diagnostics(pane_id)
        self.kick()
        return result

    def acknowledge_done_after_action(self, pane_id: str) -> None:
        with self._lifecycle_lock:
            summary = self.lifecycle.acknowledge_current_done(pane_id, now=time.time())
            if summary is None or pane_id not in self.states:
                return
            self.states[pane_id].lifecycle = summary.to_dict()
            self.states[pane_id].status = project_legacy_status(summary.state)
            self._update_snapshot_revision()

    # -- selection of which panes to show ---------------------------------- #
    def _included(self, pane: dict, kind: str) -> bool:
        if pane.get("id") in self.created_panes:
            return True
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
        self.created_panes.intersection_update(pane["id"] for pane in panes)
        present_targets = {p["target"] for p in panes}
        with self._lifecycle_lock:
            capture_generations = {
                pane["id"]: self._interaction_generations.get(pane["id"], 0)
                for pane in panes
            }

        # capture all panes concurrently (with configured scrollback depth)
        captures = await asyncio.gather(
            *[asyncio.to_thread(tmux.capture, p["id"], self.cfg.capture_lines) for p in panes]
        )
        resolved_workspaces = await self.workspaces.resolve_active(
            pane.get("path", "") for pane in panes
        )
        workspace_by_path = {
            path: (value.identity if value else None)
            for path, value in resolved_workspaces.items()
        }
        pane_created_by_id: Dict[str, float] = {}
        pane_incarnation_by_id: Dict[str, str] = {}
        structured_by_id: Dict[str, Optional[dict]] = {}
        for pane in panes:
            pid = pane["id"]
            try:
                pane_created = float(pane.get("created") or 0)
            except (TypeError, ValueError):
                pane_created = 0.0
            incarnation_raw = "%s\0%s\0%s" % (
                pid, str(pane.get("pid", "")), pane_created,
            )
            incarnation = hashlib.sha256(incarnation_raw.encode()).hexdigest()[:24]
            pane_created_by_id[pid] = pane_created
            pane_incarnation_by_id[pid] = incarnation
            if self.agents.runtime_active:
                structured_by_id[pid] = self.agents.lifecycle_evidence(pid, incarnation)

        with self._lifecycle_lock:
            now = time.time()
            new_states: Dict[str, PaneState] = {}
            new_order: List[str] = []
            agent_observations: List[PaneObservation] = []

            for pane, captured_text in zip(panes, captures):
                pid = pane["id"]
                target = pane["target"]
                override = self.cfg.overrides.get(target)
                previous_state = self.states.get(pid)
                capture_precedes_interaction = (
                    capture_generations[pid]
                    != self._interaction_generations.get(pid, 0)
                )
                capture_available = (
                    captured_text is not None and not capture_precedes_interaction
                )
                text = captured_text if capture_available else "\n".join(
                    previous_state.lines if previous_state else ()
                )

                kind = (override.kind if override and override.kind
                        else previous_state.kind if not capture_available and previous_state
                        else classify_kind(pane["cmd"], pane["title"], text))

                prev = self._meta.get(pid)
                if capture_available:
                    digest = _hash(text)
                    changed = prev is None or prev["hash"] != digest
                    activity_changed = prev is not None and prev["hash"] != digest
                    updated = now if changed else (prev["updated"] if prev else now)
                    self._meta[pid] = {"hash": digest, "updated": updated}
                    res = detect(text, kind, activity_changed, self.cfg, pane["title"])
                else:
                    changed = False
                    updated = prev["updated"] if prev else (
                        previous_state.updated if previous_state else now
                    )
                    res = DetectResult(
                        status=previous_state.status if previous_state else STATUS_IDLE,
                        question=previous_state.question if previous_state else None,
                        menu=list(previous_state.menu) if previous_state else None,
                        reason=(
                            "capture_precedes_interaction"
                            if capture_precedes_interaction
                            else "capture_unavailable"
                        ),
                        authority="fallback",
                        confidence="low",
                    )
                # Generic/Codex detectors use changed output as their working hint.
                # A single quiet capture is common while tmux redraws, so do not
                # turn a just-active pane idle until it has been quiet briefly.
                # Attention and errors always win without delay.
                if (
                    kind in (KIND_GENERIC, KIND_CODEX)
                    and res.status == STATUS_IDLE
                    and previous_state is not None
                    and previous_state.status == STATUS_WORKING
                    and now - previous_state.updated < ACTIVITY_GRACE_SECONDS
                ):
                    res.status = STATUS_WORKING
                    res.reason = "activity_grace"
                    res.authority = "terminal_activity"
                    res.confidence = "medium"
                pane_created = pane_created_by_id[pid]
                incarnation = pane_incarnation_by_id[pid]
                # Runtime-log observation is part of the opt-in experimental
                # workspace. Do not even construct observations while it is off.
                if self.agents.runtime_active:
                    runtime = runtime_from_command(pane["cmd"])
                    if runtime is None:
                        runtime = {
                            KIND_CLAUDE: "claude",
                            KIND_CODEX: "codex",
                        }.get(kind)
                else:
                    runtime = None
                if capture_available and self.agents.runtime_active and runtime in ("codex", "claude"):
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

                lifecycle_evidence = [LifecycleEvidence(
                    state="unknown" if not capture_available else {
                        "needs_input": "blocked", "error": "error",
                        "working": "working", "idle": "idle",
                    }.get(res.status, "unknown"),
                    reason=res.reason, authority=res.authority,
                    confidence=res.confidence, observed_at=now,
                )]
                structured = (
                    None
                    if capture_precedes_interaction
                    else structured_by_id.get(pid)
                )
                if structured:
                    lifecycle_evidence.append(LifecycleEvidence(**structured))
                if override:
                    identity = ({"reason": "configuration_override", "authority": "user", "confidence": "high"},)
                elif agent_kind_from_cmd(pane["cmd"]):
                    identity = ({"reason": "recognized_process_command", "authority": "process", "confidence": "high"},)
                elif kind not in (KIND_GENERIC, "shell"):
                    identity = ({"reason": "strong_screen_signature", "authority": "terminal_ui", "confidence": "medium"},)
                else:
                    identity = ({"reason": "generic_fallback", "authority": "fallback", "confidence": "low"},)
                lifecycle = self.lifecycle.observe(
                    pid, incarnation, lifecycle_evidence, identity=identity,
                    process_present=True, now=now,
                )

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
                    status=project_legacy_status(lifecycle.state),
                    title=pane["title"],
                    question=res.question,
                    menu=res.menu_list(),
                    lines=text.splitlines(),
                    updated=updated,
                    changed=changed,
                    window=pane.get("window", ""),
                    starred=bool(override and override.star),
                    interacted=self.interactions.get(pid, 0.0),
                    lifecycle=lifecycle.to_dict(),
                    workspace=(
                        workspace_by_path.get(os.path.realpath(pane.get("path", ""))).to_dict()
                        if workspace_by_path.get(os.path.realpath(pane.get("path", "")))
                        else None
                    ),
                )
                new_states[pid] = st
                new_order.append(pid)

            # configured panes that aren't present right now -> offline cards
            for target, ov in self.cfg.overrides.items():
                if target in present_targets:
                    continue
                pid = "cfg:" + target
                lifecycle = self.lifecycle.observe(
                    pid, pid, (),
                    identity=({"reason": "configuration_override", "authority": "user", "confidence": "high"},),
                    process_present=False, now=now,
                )
                new_states[pid] = PaneState(
                    id=pid,
                    target=target,
                    name=ov.name or target,
                    kind=ov.kind or "generic",
                    status=project_legacy_status(lifecycle.state),
                    starred=ov.star,
                    lifecycle=lifecycle.to_dict(),
                )
                new_order.append(pid)

            review_policy = self.agents.review_notification_policy()
            self.push.suppress_done_alerts = review_policy["batching_enabled"]
            alerts = self.push.collect(
                self.states,
                new_states,
                alert_on_needs_input=not review_policy["batching_enabled"],
                alert_on_error=review_policy["urgent_pane_errors"],
            )
            self.states = new_states
            self.order = new_order
            self.lifecycle.prune(new_states)
            self._update_snapshot_revision()
            self.push.fire(alerts)
            self.interactions = {
                key: value for key, value in self.interactions.items()
                if key in new_states
            }
            self._interaction_generations = {
                key: value for key, value in self._interaction_generations.items()
                if key in new_states
            }

        schedule_now = time.time()
        if self.agents.runtime_active:
            if self.agents.review_schedule_is_due(now=schedule_now):
                # A due queue must include semantic events visible in this same
                # pane capture. Serial processing also prevents an older queued
                # batch from being applied after the due-window snapshot.
                await self.agents.process_now(agent_observations)
            else:
                self.agents.submit(agent_observations)
        self.created_panes.intersection_update(new_states)
        # Use the same clock sample as the due check above. If the boundary is
        # crossed during this tick, the next tick ingests first and then claims.
        if self.agents.runtime_active:
            self._process_review_schedule(now=schedule_now)

    # -- snapshot + broadcast ---------------------------------------------- #
    def snapshot(self) -> dict:
        with self._lifecycle_lock:
            return {
                "type": "state",
                "panes": [self.states[pid].to_dict() for pid in self.order if pid in self.states],
            }

    def _update_snapshot_revision(self) -> None:
        """Advance only for a wire-visible pane snapshot change."""
        panes = tuple(self.states[pid].to_dict() for pid in self.order if pid in self.states)
        if panes != self._snapshot_signature:
            self._snapshot_signature = panes
            self._snapshot_revision += 1

    def review_payload(self, *, now: Optional[float] = None) -> dict:
        """Combine durable agent review state with safe live pane references."""
        with self._lifecycle_lock:
            panes = [self.states[pid] for pid in self.order if pid in self.states]
            return self.agents.review_payload(panes, now=now)

    def _process_review_schedule(self, *, now: Optional[float] = None) -> None:
        """Claim due review windows and fan out one generic invalidation/digest."""
        if not self.agents.runtime_active:
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
        dead = []
        for sid, c in list(self.clients.items()):
            if c.get("revision") == self._snapshot_revision:
                continue
            try:
                await c["ws"].send_json(self.snapshot())
                c["revision"] = self._snapshot_revision
            except Exception:
                dead.append(sid)
        for sid in dead:
            self.clients.pop(sid, None)

    async def broadcast_config_changed(self) -> None:
        """Tell every connected PWA to refetch server-managed settings."""
        dead = []
        for sid, client in list(self.clients.items()):
            try:
                await client["ws"].send_json({"type": "config_changed"})
            except Exception:
                dead.append(sid)
        for sid in dead:
            self.clients.pop(sid, None)

    async def transition_agent_workspace(self, enabled: bool) -> None:
        """Start or stop the experimental runtime within this server process."""
        if enabled:
            await self.agents.start()
            if not self.agents.runtime_active:
                raise RuntimeError(
                    self.agents.info().get("degraded_reason")
                    or "agent workspace did not become available"
                )
        else:
            await self.agents.stop_runtime()
        self.kick()

    # -- client/session tracking ------------------------------------------ #
    def add_client(self, sid, ws, ip, ua, ts):
        self.clients[sid] = {"ws": ws, "ip": ip, "ua": ua, "ts": ts, "revision": None}

    async def send_snapshot(self, sid: str) -> None:
        """Deliver the current state immediately to one newly connected client."""
        client = self.clients.get(sid)
        if client is None:
            return
        await client["ws"].send_json(self.snapshot())
        client["revision"] = self._snapshot_revision

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

    def _perform_pane_action(
        self, pane_id: str, sender: Callable[[str], None]
    ) -> str:
        with self._lifecycle_lock:
            real = self.resolve_id(pane_id)
            if real is None:
                raise tmux.TmuxError("unknown pane")
            self._interaction_generations[real] = (
                self._interaction_generations.get(real, 0) + 1
            )
            sender(real)
            self.interactions[real] = time.time()
            self.acknowledge_done_after_action(real)
            return real

    def send_key(self, pane_id: str, key: str) -> None:
        self._perform_pane_action(
            pane_id, lambda real: tmux.send_key(real, key)
        )

    def send_text(self, pane_id: str, text: str, enter: bool) -> None:
        self._perform_pane_action(
            pane_id, lambda real: tmux.send_literal(real, text, enter=enter)
        )

    def do_select(self, pane_id: str, key: str) -> None:
        def send(real: str) -> None:
            st = self.states.get(pane_id)
            kind = st.kind if st else "generic"
            if kind == KIND_CLAUDE:
                tmux.send_chars(real, key)
            elif kind == KIND_CODEX:
                option = next((item for item in (st.menu if st else []) if item.key == key), None)
                if key == "enter":
                    tmux.send_key(real, "Enter")
                elif option is not None and option.freeform:
                    tmux.send_chars(real, key)
                else:
                    tmux.send_chars(real, key)
                    tmux.send_key(real, "Enter")
            elif key == "enter":
                tmux.send_key(real, "Enter")
            else:
                tmux.send_literal(real, key, enter=True)

        self._perform_pane_action(pane_id, send)

    def kick(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # -- main loop ---------------------------------------------------------- #
    async def start_agent_runtime(self) -> None:
        """Finish the initial agent transition before clients receive config."""
        if self._agent_startup_complete:
            return
        try:
            await self.agents.start()
        except Exception as exc:
            self.cfg.experimental_agent_workspace_enabled = False
            self.agents.disable("startup failed: %s" % type(exc).__name__)
            try:
                await asyncio.to_thread(save_overlay, self.cfg)
            except OSError as persist_exc:
                print("[vmux] could not persist agent workspace startup rollback:", persist_exc)
            print("[vmux] agent context disabled:", exc)
        self._agent_startup_complete = True

    async def run(self) -> None:
        if self._wake is None:
            self._wake = asyncio.Event()
        await self.start_agent_runtime()
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

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
from .config import Config, save_overlay
from .detectors import classify_kind, detect, is_spinner
from .models import (
    KIND_CLAUDE,
    KIND_CODEX,
    KIND_GENERIC,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_NEEDS_INPUT,
    STATUS_OFFLINE,
    STATUS_WORKING,
    PaneState,
)
from .naming import SmartNamer
from .push import PushManager
from .terminals import TerminalProvider, provider_for_config
from .terminals.actions import TerminalActionService, prepare_guard
from .terminals.base import EndpointSnapshot, ProviderError

# Retain this module attribute for the established monkeypatch surface while
# terminal calls themselves go through providers.
TMUX_COMPAT_MODULE = tmux


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

_NATIVE_KINDS = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "codex": "codex",
    "grok": "grok",
    "opencode": "opencode",
    "antigravity_cli": "antigravity",
    "antigravity": "antigravity",
    "agy": "antigravity",
}


def _endpoint_pane(endpoint: EndpointSnapshot) -> dict:
    metadata = endpoint.provider_metadata
    return {
        "id": endpoint.public_id,
        "target": endpoint.persistent_target,
        "cmd": endpoint.command,
        "title": endpoint.title,
        "window": endpoint.window,
        "path": endpoint.cwd,
        "pid": metadata.get("pid", ""),
        "created": metadata.get("created", ""),
        "window_id": metadata.get("window_id", ""),
    }


def _hierarchy_name(endpoint: EndpointSnapshot, mode: str) -> str:
    nodes = {node.kind: node for node in endpoint.hierarchy}
    pane = nodes.get("pane")
    tab = nodes.get("tab") or nodes.get("window")
    workspace = nodes.get("workspace") or nodes.get("session")
    if mode == "pane" and pane:
        return pane.label
    if mode in ("window", "window_pane") and tab:
        return tab.label if mode == "window" or not pane else "%s:%s" % (tab.label, pane.label)
    if mode in ("session_pane", "session_window_pane") and workspace:
        parts = [workspace.label]
        if mode == "session_window_pane" and tab:
            parts.append(tab.label)
        if pane:
            parts.append(pane.label)
        return ":".join(part for part in parts if part)
    return endpoint.title or (pane.label if pane else endpoint.persistent_target)


class Hub:
    def __init__(self, cfg: Config, provider: Optional[TerminalProvider] = None):
        self.cfg = cfg
        self.provider = provider or provider_for_config(cfg)
        self.states: Dict[str, PaneState] = {}
        self.order: List[str] = []
        self.clients: Dict[str, dict] = {}   # sid -> {ws, ip, ua, ts, revision}
        self._meta: Dict[str, dict] = {}   # id -> {hash, updated}
        self.interactions: Dict[str, float] = {}   # pane id -> epoch of last user send
        # Shells created through vmux stay visible even when general shell
        # discovery is disabled, so clients can open the successful result.
        self.created_panes = set()
        self.push = PushManager(cfg)
        if self.provider.name != "tmux":
            cfg.experimental_agent_workspace_enabled = False
        self.agents = AgentService(cfg, push=self.push, kick=self.kick)
        if self.provider.name != "tmux":
            self.agents.disable("unsupported by the selected terminal provider")
        self.actions = TerminalActionService(self, self.provider)
        # Created in run() so asyncio.Event binds to the active server loop.
        # at construction, and Hub is built before the server loop exists
        self._wake: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = False
        self._agent_startup_complete = False
        self.namer = SmartNamer(cfg, on_update=self.kick)
        self._snapshot_revision = 0
        self._snapshot_signature: Optional[tuple] = None

    def mark_interaction(self, pane_id: str) -> None:
        """Record that the user just sent input to this pane (for the 'recently sent' sort)."""
        self.interactions[pane_id] = time.time()

    def mark_created(self, pane_id: str) -> None:
        self.created_panes.add(pane_id)

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
        discovery = await asyncio.to_thread(self.provider.discover)
        if not discovery.authoritative:
            # A failed provider read is not evidence that endpoints disappeared.
            # Retain the last good in-memory snapshot, but make it read-only.
            for state in self.states.values():
                if not state.id.startswith("cfg:"):
                    state.stale = True
                    state.actionable = False
            self._update_snapshot_revision()
            return

        endpoints = list(discovery.endpoints)
        panes = [_endpoint_pane(endpoint) for endpoint in endpoints]
        if self.provider.name == "tmux":
            self.created_panes.intersection_update(pane["id"] for pane in panes)
        else:
            self.created_panes.clear()
        present_targets = {endpoint.persistent_target for endpoint in endpoints}

        captures = await asyncio.gather(
            *[
                asyncio.to_thread(
                    self.provider.capture,
                    endpoint,
                    lines=self.cfg.capture_lines,
                )
                for endpoint in endpoints
            ],
            return_exceptions=True,
        )

        now = time.time()
        new_states: Dict[str, PaneState] = {}
        new_order: List[str] = []
        agent_observations: List[PaneObservation] = []

        for endpoint, pane, capture in zip(endpoints, panes, captures):
            pid = endpoint.public_id
            target = endpoint.persistent_target
            override = self.cfg.overrides.get(target)
            capture_failed = isinstance(capture, BaseException)
            previous_state = self.states.get(pid)
            if capture_failed:
                text = "\n".join(previous_state.lines) if previous_state is not None else ""
            else:
                text = capture.text

            native_kind = endpoint.native_agent.kind if endpoint.native_agent else None
            kind = (
                override.kind if override and override.kind
                else _NATIVE_KINDS.get(str(native_kind or "").lower())
                or classify_kind(pane["cmd"], pane["title"], text)
            )

            digest = _hash(text)
            prev = self._meta.get(pid)
            changed = not capture_failed and (prev is None or prev["hash"] != digest)
            updated = now if changed else (prev["updated"] if prev else now)
            self._meta[pid] = {"hash": digest, "updated": updated}

            res = detect(text, kind, changed, self.cfg, pane["title"])
            native_status = endpoint.native_agent.status if endpoint.native_agent else None
            # Terminal evidence still wins for parsed questions and errors.
            if res.status != STATUS_NEEDS_INPUT and native_status == "blocked":
                res.status = STATUS_NEEDS_INPUT
                if not res.question:
                    res.question = "This agent is waiting for input."
            elif res.status not in (STATUS_NEEDS_INPUT, STATUS_ERROR) and native_status == "working":
                res.status = STATUS_WORKING
            elif (
                res.status not in (STATUS_NEEDS_INPUT, STATUS_ERROR, STATUS_WORKING)
                and native_status in ("idle", "done")
            ):
                res.status = STATUS_IDLE
            # Generic/Codex output gets a short quiet grace, as it did before
            # provider extraction. Native done/idle deliberately ends the grace.
            if (
                native_status not in ("idle", "done")
                and kind in (KIND_GENERIC, KIND_CODEX)
                and res.status == STATUS_IDLE
                and previous_state is not None
                and previous_state.status == STATUS_WORKING
                and now - previous_state.updated < ACTIVITY_GRACE_SECONDS
            ):
                res.status = STATUS_WORKING

            if self.agents.runtime_active:
                runtime = runtime_from_command(pane["cmd"])
                if runtime is None:
                    runtime = {KIND_CLAUDE: "claude", KIND_CODEX: "codex"}.get(kind)
            else:
                runtime = None
            if self.agents.runtime_active and runtime in ("codex", "claude"):
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

            if not self._included(pane, kind):
                continue

            override_name = override.name if override else None
            smart_name = None
            if self.cfg.naming_mode == "smart" and not override_name:
                smart_name = self.namer.name(pane, text, target)
            if self.provider.name == "herdr" and not override_name and self.cfg.naming_mode != "smart":
                name = _hierarchy_name(endpoint, self.cfg.naming_mode)
            else:
                name = choose_name(
                    self.cfg.naming_mode,
                    title=pane["title"], window=pane.get("window", ""),
                    target=target, command=pane["cmd"],
                    override_name=override_name,
                    smart_name=smart_name,
                )

            menu = res.menu_list()
            action_guard = None
            if self.provider.name == "herdr" and not capture_failed:
                prompt, options, menu = prepare_guard(text, res.question, menu)
                action_guard = {
                    "endpoint_revision": endpoint.native_revision,
                    "prompt_fingerprint": prompt,
                    "options_fingerprint": options,
                }
            actionable = bool(
                not capture_failed
                and discovery.health.status == "ready"
                and endpoint.capabilities.input in ("legacy", "guarded_v1")
            )
            state = PaneState(
                id=pid,
                target=target,
                name=name,
                kind=kind,
                status=res.status,
                title=pane["title"],
                question=res.question,
                menu=menu,
                lines=text.splitlines(),
                updated=updated,
                changed=changed,
                window=pane.get("window", ""),
                starred=bool(override and override.star),
                interacted=self.interactions.get(pid, 0.0),
                provider=self.provider.name,
                hierarchy=[node.to_dict() for node in endpoint.hierarchy],
                capabilities=endpoint.capabilities.to_dict(),
                native_agent=endpoint.native_agent.to_dict() if endpoint.native_agent else None,
                action_guard=action_guard,
                actionable=actionable,
                stale=capture_failed,
            )
            new_states[pid] = state
            new_order.append(pid)

        for target, override in self.cfg.overrides.items():
            if target in present_targets:
                continue
            pid = "cfg:" + target
            new_states[pid] = PaneState(
                id=pid,
                target=target,
                name=override.name or target,
                kind=override.kind or "generic",
                status=STATUS_OFFLINE,
                starred=override.star,
                provider=self.provider.name,
                actionable=False,
                stale=True,
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
        self._meta = {key: value for key, value in self._meta.items() if key in new_states}
        self._update_snapshot_revision()
        schedule_now = time.time()
        if self.agents.runtime_active:
            if self.agents.review_schedule_is_due(now=schedule_now):
                await self.agents.process_now(agent_observations)
            else:
                self.agents.submit(agent_observations)
        self.interactions = {key: value for key, value in self.interactions.items() if key in new_states}
        self.created_panes.intersection_update(new_states)
        self.push.fire(alerts)
        self.provider.configure_events(
            [endpoint.ref.native_endpoint_id for endpoint in endpoints],
            self.kick_from_thread,
        )
        if self.agents.runtime_active:
            self._process_review_schedule(now=schedule_now)

    # -- snapshot + broadcast ---------------------------------------------- #
    def snapshot(self) -> dict:
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
        if enabled and self.provider.name != "tmux":
            raise RuntimeError("unsupported by the selected terminal provider")
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
        """Resolve only tmux legacy targets; Herdr handles stay registry-only."""
        endpoint = self.provider.resolve(pane_id)
        if endpoint is None or endpoint.ref.provider != "tmux":
            return None
        return endpoint.ref.native_endpoint_id

    def do_select(self, pane_id: str, key: str) -> None:
        self.actions.legacy_select(pane_id, key)

    def kick(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def kick_from_thread(self) -> None:
        """Thread-safe wake target for optional provider event readers."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.kick)

    # -- main loop ---------------------------------------------------------- #
    async def start_agent_runtime(self) -> None:
        """Finish the initial agent transition before clients receive config."""
        if self._agent_startup_complete:
            return
        if self.provider.name != "tmux":
            self._agent_startup_complete = True
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
        self._loop = asyncio.get_running_loop()
        if self.provider.name == "herdr":
            try:
                health = getattr(self.provider, "health", None)
                if getattr(health, "status", None) != "ready":
                    await asyncio.to_thread(self.provider.probe)
                # Subscribe before publishing the initial level snapshot. Any
                # event during the following poll sets the coalesced wake flag
                # and causes another authoritative reconciliation.
                initial = await asyncio.to_thread(self.provider.discover)
                if initial.authoritative:
                    self.provider.configure_events(
                        [endpoint.ref.native_endpoint_id for endpoint in initial.endpoints],
                        self.kick_from_thread,
                    )
            except ProviderError as exc:
                print("[vmux] terminal provider=herdr event=probe status=unavailable reason=%s" % exc.category)
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
        self.provider.close()
        if self._wake is not None:
            self._wake.set()

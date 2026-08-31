"""Agent-context orchestration independent of the terminal pane state model."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

from .. import tmux
from ..detectors import classify_kind
from ..models import KIND_CLAUDE, KIND_CODEX
from .controllers import TmuxRuntimeController
from .models import (
    PaneObservation,
    bounded_text,
    default_capabilities,
    fingerprint_terminal,
    project_events,
)
from .observers import built_in_observers, runtime_from_command
from .store import (
    MAX_REVIEW_INTERVAL_MINUTES,
    MIN_REVIEW_INTERVAL_MINUTES,
    AgentStore,
)


def _live_runtime(pane: Dict[str, Any], text: str) -> Optional[str]:
    """Resolve explicit and strongly inferred runtime identity consistently."""
    runtime = runtime_from_command(str(pane.get("cmd") or ""))
    if runtime is not None:
        return runtime
    kind = classify_kind(
        str(pane.get("cmd") or ""),
        str(pane.get("title") or ""),
        text,
    )
    return {KIND_CLAUDE: "claude", KIND_CODEX: "codex"}.get(kind)


class AgentNotFound(LookupError):
    pass


class AgentUnavailable(RuntimeError):
    pass


class AgentConflict(RuntimeError):
    def __init__(self, message: str, current: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.current = current


def _norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _similar(left: str, right: str) -> bool:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    aw, bw = set(a.split()), set(b.split())
    return len(aw & bw) / max(1, min(len(aw), len(bw))) >= 0.6


def _prompt_matches(structured: str, visible: str) -> bool:
    """Require exact normalized prompt equality before terminal control.

    Even small added words can invert intent ("delete" vs "do not delete"),
    so TUI chrome is handled by the detector and never fuzzily stripped here.
    """
    expected, actual = _norm(structured), _norm(visible)
    return bool(expected and expected == actual)


class AgentService:
    """Owns adapter polling, normalized storage, live invalidations, and control."""

    def __init__(self, cfg, *, push=None, kick: Optional[Callable[[], None]] = None):
        self.cfg = cfg
        path = cfg.agent_store_path or os.path.expanduser("~/.vmux/vmux-agents.sqlite3")
        self.store = AgentStore(path, cfg.agent_retention_days)
        self.observers = built_in_observers(cfg.agent_codex_home, cfg.agent_claude_home)
        self.controller = TmuxRuntimeController()
        self.push = push
        self.kick = kick or (lambda: None)
        self._latest: Dict[str, PaneObservation] = {}
        self._observation_generation = 0
        self._verified_observations: Dict[str, Tuple[int, PaneObservation]] = {}
        self._latest_lock = threading.RLock()
        self._process_lock = threading.Lock()
        self._api_lock = threading.RLock()
        self._action_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._control_lock = threading.RLock()
        self._event_lock = threading.RLock()
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = False
        self._subscribers: Dict[asyncio.Queue, int] = {}
        self._event_cursor = 0
        self._history: Deque[Dict[str, Any]] = deque(maxlen=500)
        self._last_prune = 0.0
        self._last_enqueued = 0.0
        self._disabled_reason: Optional[str] = None
        self._runtime_active = False
        self._history_generation: Dict[str, int] = defaultdict(int)

    @property
    def enabled(self) -> bool:
        return bool(
            self.cfg.experimental_agent_workspace_enabled
            and not self._disabled_reason
        )

    @property
    def runtime_active(self) -> bool:
        """Whether server-facing APIs and background work may run."""
        return bool(self.enabled and self._runtime_active)

    def disable(self, reason: str) -> None:
        self._disabled_reason = bounded_text(reason, 300) or "startup_failed"
        self._runtime_active = False

    def info(self) -> Dict[str, Any]:
        enabled = self.runtime_active
        value = {
            "enabled": enabled,
            "runtimes": ["codex", "claude"],
            "websocket": enabled,
            "websocket_path": "/ws/agents" if enabled else None,
            "mode": "log_observer" if enabled else "disabled",
            "retention_days": self.cfg.agent_retention_days,
            "persistence": "structured_only",
            "chat": "confirmed_idle_only",
            "decisions": "verified_structured_only",
            "degraded_reason": self._disabled_reason,
        }
        if enabled:
            value["recovery"] = {
                "version": 1,
                "path_template": "/api/agents/{id}/recovery",
                "default_recent_messages": 20,
                "default_recent_timeline": 20,
                "default_recent_activity": 20,
                "max_recent_messages": 50,
                "max_recent_timeline": 50,
                "max_recent_activity": 50,
                "activity_order": "oldest_to_newest",
            }
        return value

    def review_info(self) -> Dict[str, Any]:
        enabled = self.runtime_active
        return {
            "enabled": enabled,
            "version": 1,
            "scheduling": enabled,
            "min_interval_minutes": MIN_REVIEW_INTERVAL_MINUTES,
            "max_interval_minutes": MAX_REVIEW_INTERVAL_MINUTES,
        }

    async def start(self) -> None:
        if not self.cfg.experimental_agent_workspace_enabled or self._runtime_active:
            return
        self._disabled_reason = None
        try:
            await asyncio.to_thread(self._open_store)
            self._loop = asyncio.get_running_loop()
            self._last_prune = time.time()
            self._queue = asyncio.Queue(maxsize=1)
            self._stop = False
            self._task = asyncio.create_task(self._worker())
            self._runtime_active = True
        except Exception:
            self._runtime_active = False
            self._queue = None
            self._task = None
            await asyncio.to_thread(self._close_store)
            raise

    def _open_store(self) -> None:
        with self._api_lock:
            self.store.open()
            self.store.prune()

    def _close_store(self) -> None:
        with self._api_lock:
            self.store.close()

    @contextlib.contextmanager
    def api_guard(self):
        """Serialize API calls against runtime shutdown and store closure."""
        with self._api_lock:
            yield

    def stop(self) -> None:
        self._stop = True
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(None)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def stop_runtime(self, reason: str = "workspace_disabled") -> None:
        """Stop observation, writes, and invalidations while retaining history."""
        self._runtime_active = False
        self._stop = True
        stop_push = getattr(self.push, "stop_agent_notifications", None)
        if callable(stop_push):
            stop_push()
        self._close_subscribers(reason)
        task = self._task
        if task is not None:
            self.stop()
            with contextlib.suppress(asyncio.CancelledError):
                await task  # lets an in-flight to_thread extraction finish
        self._task = None
        self._queue = None
        self._loop = None
        with self._latest_lock:
            self._latest = {}
            self._observation_generation += 1
            self._verified_observations = {}
        await asyncio.to_thread(self._close_store)

    async def aclose(self) -> None:
        """Cancel and await the worker before closing its SQLite connection."""
        await self.stop_runtime("server_shutdown")

    def submit(self, observations: Sequence[PaneObservation]) -> None:
        if not self.enabled:
            return
        with self._latest_lock:
            self._latest = {obs.pane_id: obs for obs in observations}
            self._observation_generation += 1
            self._verified_observations = {}
        if self._queue is None:
            return
        now = time.monotonic()
        if now - self._last_enqueued < 3.0:
            return
        self._last_enqueued = now
        # Latest-wins backpressure: semantic extraction must never delay tmux's
        # primary pane poll/broadcast loop.
        try:
            self._queue.put_nowait(tuple(observations))
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(tuple(observations))
            except asyncio.QueueFull:
                pass

    async def process_now(self, observations: Sequence[PaneObservation]) -> None:
        """Process the latest batch before a due-window queue is computed."""
        if not self.enabled:
            return
        with self._latest_lock:
            self._latest = {obs.pane_id: obs for obs in observations}
            self._observation_generation += 1
            self._verified_observations = {}
        # Drop an older queued batch before yielding to the worker. If the
        # worker already owns one, _process_lock orders this current batch
        # after it; a stale queued batch can therefore never run afterward.
        if self._queue is not None:
            while True:
                try:
                    queued = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if queued is None:
                    self._queue.put_nowait(None)
                    break
        await asyncio.to_thread(self._process_serial, tuple(observations))

    async def _worker(self) -> None:
        assert self._queue is not None
        while not self._stop:
            observations = await self._queue.get()
            if observations is None:
                break
            try:
                await asyncio.to_thread(self._process_serial, observations)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print("[vmux] agent context error:", exc)

    def _process_serial(self, observations: Sequence[PaneObservation]) -> None:
        with self._process_lock:
            self._process_sync(observations)

    def _process_sync(self, observations: Sequence[PaneObservation]) -> None:
        if not self.enabled:
            return
        if time.time() - self._last_prune > 86400:
            self.store.prune()
            self._last_prune = time.time()
        relevant = [obs for obs in observations if obs.runtime in ("codex", "claude")]
        groups: Dict[Tuple[str, str], List[PaneObservation]] = defaultdict(list)
        for obs in relevant:
            groups[(obs.runtime or "", os.path.realpath(obs.cwd))].append(obs)

        active_session_ids: set[str] = set()
        for (runtime, _cwd), panes in groups.items():
            observer = next((item for item in self.observers if item.runtime == runtime), None)
            if observer is None:
                continue
            candidates = observer.discover(panes[0])
            if not candidates:
                continue
            by_id = {candidate.native_session_id: candidate for candidate in candidates}
            tied = [
                candidate for candidate in candidates
                if any(self._candidate_tied(candidate, pane) for pane in panes)
            ]
            manual: List[Any] = []
            for candidate in candidates:
                existing = self.store.find_session(
                    candidate.runtime, candidate.native_session_id, internal=True
                )
                if existing and existing.get("_binding_source") == "manual":
                    manual.append(candidate)
            if len(panes) == 1 and (tied or manual):
                # A newer transcript in the only matching pane supersedes an
                # incumbent even when the pane's shell is long-lived. Without
                # start-time evidence it stays probable/read-only.
                newest = candidates[0]
                incumbent = max(tied or manual, key=lambda c: (c.started_at, c.modified_at))
                selected = [newest] if (
                    newest.native_session_id != incumbent.native_session_id
                    and (newest.started_at, newest.modified_at)
                    > (incumbent.started_at, incumbent.modified_at)
                ) else [incumbent]
            else:
                selected = tied + [candidate for candidate in manual if candidate not in tied]
            selected = selected or candidates[:1]
            # Deduplicate while retaining newest-first discovery order.
            selected = [by_id[key] for key in dict.fromkeys(c.native_session_id for c in selected)]
            ambiguous = len(selected) != 1 or len(panes) != 1

            for candidate in selected:
                agent = self.store.upsert_session(
                    candidate.runtime, candidate.native_session_id, candidate.path,
                    candidate.cwd, candidate.parser_version,
                )
                active_session_ids.add(agent["id"])
                manual_obs = None
                if agent.get("_binding_source") == "manual":
                    manual_obs = next((p for p in panes if p.pane_id == agent.get("pane_id")), None)
                automatic_obs = panes[0] if (
                    not ambiguous and self._candidate_tied(candidate, panes[0])
                ) else None
                bound_obs = manual_obs or automatic_obs
                if bound_obs:
                    association = "confirmed"
                elif ambiguous:
                    association = "ambiguous"
                else:
                    association = "probable"
                capabilities = default_capabilities(association)
                if bound_obs and bound_obs.status == "idle":
                    capabilities["chat_send"] = "idle_only"
                capabilities.update({
                    "runtime": candidate.runtime,
                    "parser_version": candidate.parser_version,
                    "observed_lag": max(0.0, time.time() - candidate.modified_at),
                })
                with self._control_lock:
                    if bound_obs:
                        self._invalidate_other_bindings(
                            agent["id"], bound_obs.pane_id, bound_obs.incarnation
                        )
                    agent = self.store.update_binding(
                        agent["id"], association=association,
                        pane_id=bound_obs.pane_id if bound_obs else None,
                        target=bound_obs.target if bound_obs else None,
                        pane_pid=bound_obs.pid if bound_obs else None,
                        pane_created=bound_obs.pane_created if bound_obs else None,
                        pane_incarnation=bound_obs.incarnation if bound_obs else None,
                        source="manual" if manual_obs else "automatic", capabilities=capabilities,
                    )
                self._ingest_candidate(observer, candidate, agent, bound_obs, capabilities)
                if bound_obs:
                    with self._latest_lock:
                        if self._latest.get(bound_obs.pane_id) == bound_obs:
                            self._verified_observations[agent["id"]] = (
                                self._observation_generation,
                                bound_obs,
                            )

        agents, cursor = self.store.list_agents(limit=100)
        while True:
            for public_agent in agents:
                internal = self.store.get_agent(public_agent["id"], internal=True)
                if not internal:
                    continue
                if public_agent["id"] in active_session_ids:
                    continue
                if (public_agent.get("association") == "unavailable"
                        and internal["context"].get("lifecycle") in ("offline", "completed")):
                    continue
                caps = default_capabilities("unavailable")
                with self._control_lock:
                    self.store.update_binding(
                        public_agent["id"], association="unavailable", pane_id=None, target=None,
                        pane_pid=None, pane_created=None, pane_incarnation=None, source="automatic",
                        capabilities=caps,
                    )
                context = dict(internal["context"])
                if context.get("lifecycle") != "completed":
                    context["lifecycle"] = "offline"
                context["last_updated"] = time.time()
                snapshot, _, _ = self.store.apply_projection(public_agent["id"], context, [], [], [])
                if snapshot:
                    self.publish("agent_updated", public_agent["id"],
                                 snapshot["context"].get("revision", 0))
            if not cursor:
                break
            agents, cursor = self.store.list_agents(cursor=cursor, limit=100)

    def _invalidate_other_bindings(self, keep_agent_id: str, pane_id: str,
                                   pane_incarnation: str) -> None:
        agents, cursor = self.store.list_agents(limit=100)
        while True:
            for agent in agents:
                if agent["id"] == keep_agent_id or agent.get("pane_id") != pane_id:
                    continue
                internal = self.store.get_agent(agent["id"], internal=True)
                if not internal or internal.get("_pane_incarnation") != pane_incarnation:
                    continue
                self.store.update_binding(
                    agent["id"], association="unavailable", pane_id=None, target=None,
                    pane_pid=None, pane_created=None, pane_incarnation=None,
                    source="automatic", capabilities=default_capabilities("unavailable"),
                )
                context = dict(internal["context"])
                if context.get("lifecycle") != "offline":
                    context["lifecycle"] = "offline"
                    context["last_updated"] = time.time()
                    snapshot, _, _ = self.store.apply_projection(agent["id"], context, [], [], [])
                    if snapshot:
                        self.publish("agent_updated", agent["id"],
                                     snapshot["context"].get("revision", 0))
            if not cursor:
                break
            agents, cursor = self.store.list_agents(cursor=cursor, limit=100)

    @staticmethod
    def _candidate_tied(candidate, pane: PaneObservation) -> bool:
        return bool(
            pane.pane_created > 0 and candidate.started_at > 0
            and candidate.started_at >= pane.pane_created - 5
            and candidate.started_at <= pane.pane_created + 180
        )

    def _ingest_candidate(self, observer, candidate, agent, bound_obs,
                          capabilities: Dict[str, Any]) -> None:
        with self._control_lock:
            history_generation = self._history_generation[agent["id"]]
        result = observer.read(
            candidate, int(agent.get("_log_offset") or 0), agent.get("_log_inode")
        )
        projected, messages, decisions, resolved = project_events(
            agent.get("context") or {}, list(result.events)
        )
        projected["extraction_health"] = "degraded" if result.error else "ok"
        for decision in decisions:
            match = self._match_decision(decision, bound_obs) if bound_obs else None
            decision.update({
                "status": "pending" if match else "unverified",
                "input_map": match or {},
                "prompt_fingerprint": bound_obs.prompt_fingerprint if match else "",
            })
        newly_verified: List[str] = []
        verified_snapshots = []
        # Reconciliation can resolve or expose an actionable decision, so it
        # shares the same lock as terminal replies. File parsing stays outside.
        with self._control_lock:
            self.store.update_cursor(
                agent["id"], result.offset, result.inode, result.parser_version, error=result.error
            )
            if self._history_generation[agent["id"]] != history_generation:
                return
            resolved_decision_ids = self.store.decision_ids_for_native(agent["id"], resolved)
            snapshot, decision_ids, message_ids = self.store.apply_projection(
                agent["id"], projected, messages, decisions, resolved
            )
            if bound_obs:
                for decision in self.store.list_unverified_decisions(agent["id"]):
                    match = self._match_decision(decision, bound_obs)
                    if not match:
                        continue
                    verified = self.store.verify_decision(
                        decision["id"], match, bound_obs.prompt_fingerprint
                    )
                    verified_snapshot = self.store.sync_verified_decision(agent["id"], verified)
                    if verified_snapshot:
                        verified_snapshots.append(verified_snapshot)
                    newly_verified.append(decision["id"])
                    self._fire_agent_decision(verified)
            pending, _ = self.store.list_decisions(
                status="pending", session_id=agent["id"], limit=100
            )
            if bound_obs and pending:
                capabilities["decision_reply"] = "verified_terminal"
            self.store.update_capabilities(agent["id"], capabilities)
        for verified_snapshot in verified_snapshots:
            self.publish("agent_updated", agent["id"],
                         verified_snapshot["context"].get("revision", 0))
        if snapshot:
            self.publish("agent_updated", agent["id"], snapshot["context"].get("revision", 0))
        for decision_id in decision_ids + newly_verified:
            decision = self.store.get_decision(decision_id)
            if decision and decision["status"] == "pending":
                self.publish("decision_updated", agent["id"], decision["revision"],
                             decision_id=decision_id)
                if decision_id in decision_ids:
                    self._fire_agent_decision(decision)
        for decision_id in resolved_decision_ids:
            decision = self.store.get_decision(decision_id)
            if decision:
                self.publish("decision_updated", agent["id"], decision["revision"],
                             decision_id=decision_id)
        for message_id in message_ids:
            self.publish("message_updated", agent["id"], projected.get("revision", 0),
                         message_id=message_id)

    def _fire_agent_decision(self, decision: Dict[str, Any]) -> None:
        if not self.enabled or self.push is None:
            return
        settings = self.store.get_review_settings()
        if (
            settings.get("interval_minutes") is not None
            and decision.get("priority") not in ("high", "critical")
        ):
            return
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self.push.fire_agent_decision, decision)

    def _match_decision(self, decision: Dict[str, Any], obs: PaneObservation) -> Optional[Dict[str, str]]:
        if not obs.question or not obs.menu or obs.status != "needs_input":
            return None
        prompt = str(decision.get("description") or decision.get("title") or "")
        if not _prompt_matches(prompt, obs.question):
            return None
        options = list(decision.get("options", []))
        if len(options) != len(obs.menu):
            return None
        result: Dict[str, str] = {}
        for option, found in zip(options, obs.menu):
            label = str(option.get("label") or "")
            if _norm(label) != _norm(str(found.get("label") or "")):
                return None
            key = str(found.get("key") or "")
            if not re.fullmatch(r"(?:enter|[A-Za-z0-9])", key):
                return None
            result[str(option.get("id"))] = key
        return result or None

    def _latest_observation(self, pane_id: str) -> Optional[PaneObservation]:
        with self._latest_lock:
            return self._latest.get(pane_id)

    def _validate_live(self, agent: Dict[str, Any], expected_binding_revision: int,
                       *, require_idle: bool, prompt_fingerprint: Optional[str] = None) -> PaneObservation:
        if int(agent["binding_revision"]) != int(expected_binding_revision):
            raise AgentConflict("stale binding revision", self.store.get_agent(agent["id"]))
        if agent.get("association") != "confirmed" or not agent.get("pane_id"):
            raise AgentUnavailable("agent session is read-only until its pane binding is confirmed")
        obs = self._latest_observation(agent["pane_id"])
        if not obs or time.time() - obs.observed_at > max(10.0, self.cfg.poll_interval * 4):
            raise AgentConflict("pane observation is stale", self.store.get_agent(agent["id"]))
        if require_idle and obs.status != "idle":
            raise AgentConflict("agent is not at an idle prompt", self.store.get_agent(agent["id"]))
        panes = tmux.list_panes()
        live = next((pane for pane in panes if pane.get("id") == obs.pane_id), None)
        if not live:
            raise AgentConflict("bound pane is no longer running", self.store.get_agent(agent["id"]))
        live_created = float(live.get("created") or 0)
        if str(live.get("pid") or "") != obs.pid or (obs.pane_created and live_created != obs.pane_created):
            raise AgentConflict("bound pane was replaced", self.store.get_agent(agent["id"]))
        if os.path.realpath(str(live.get("path") or "")) != os.path.realpath(agent["_source_cwd"]):
            raise AgentConflict("bound pane changed working directory", self.store.get_agent(agent["id"]))
        current = tmux.capture(obs.pane_id, 0)
        if current is None or _live_runtime(live, current) != agent["runtime"]:
            raise AgentConflict("bound pane is no longer running this agent runtime",
                                self.store.get_agent(agent["id"]))
        if current is None or fingerprint_terminal(current) != obs.prompt_fingerprint:
            raise AgentConflict("pane prompt changed; refresh before sending", self.store.get_agent(agent["id"]))
        if prompt_fingerprint and obs.prompt_fingerprint != prompt_fingerprint:
            raise AgentConflict("decision prompt changed", self.store.get_agent(agent["id"]))
        return obs

    # -- public query/control surface ------------------------------------ #
    def _decorate_agent(self, agent: Dict[str, Any]) -> Dict[str, Any]:
        value = dict(agent)
        value["binding_candidates"] = []
        if agent.get("association") == "confirmed":
            return value
        internal = self.store.get_agent(agent["id"], internal=True)
        if not internal:
            return value
        with self._latest_lock:
            matches = [
                obs for obs in self._latest.values()
                if obs.runtime == agent["runtime"]
                and os.path.realpath(obs.cwd) == os.path.realpath(internal["_source_cwd"])
            ]
        value["binding_candidates"] = [{
            "id": obs.pane_id, "pane_id": obs.pane_id,
            "target": obs.target, "pane_target": obs.target,
            "label": obs.title or obs.target, "confidence": 0.5,
        } for obs in matches]
        return value

    def get_agent(self, session_id: str) -> Dict[str, Any]:
        agent = self.store.get_agent(session_id)
        if not agent:
            raise AgentNotFound(session_id)
        return self._decorate_agent(agent)

    def list_agents(self, cursor=None, limit=50):
        agents, next_cursor = self.store.list_agents(cursor, limit)
        return [self._decorate_agent(agent) for agent in agents], next_cursor

    def resume(self, session_id: str):
        value = self.store.resume(session_id)
        if not value:
            raise AgentNotFound(session_id)
        value["agent"] = self._decorate_agent(value["agent"])
        return value

    def recovery(self, session_id: str, **kwargs):
        with self._latest_lock:
            verified_observation = self._verified_observations.get(session_id)
            observation = verified_observation[1] if verified_observation else None
            runtime_observation = (
                {
                    "incarnation": observation.incarnation,
                    "observed_at": observation.observed_at,
                }
                if observation
                else None
            )
        value = self.store.recovery(
            session_id, runtime_observation=runtime_observation, **kwargs
        )
        if not value:
            raise AgentNotFound(session_id)
        with self._latest_lock:
            if self._verified_observations.get(session_id) != verified_observation:
                value["freshness"]["observed_at"] = None
                value["freshness"]["runtime_session"] = "unknown"
        return value

    def visit(self, session_id: str, snapshot_id: str):
        self.get_agent(session_id)
        try:
            return self.store.visit(session_id, snapshot_id)
        except KeyError:
            raise AgentConflict("snapshot does not belong to this agent", self.store.resume(session_id))

    def list_timeline(self, session_id=None, cursor=None, limit=50):
        if session_id:
            self.get_agent(session_id)
        return self.store.timeline(session_id, cursor, limit)

    def list_messages(
        self,
        session_id,
        cursor=None,
        limit=100,
        *,
        q=None,
        role=None,
        after=None,
        before=None,
        with_metadata=False,
    ):
        self.get_agent(session_id)
        return self.store.list_messages(
            session_id,
            cursor,
            limit,
            q=q,
            role=role,
            after=after,
            before=before,
            with_metadata=with_metadata,
        )

    @staticmethod
    def _public_review_settings(value: Dict[str, Any]) -> Dict[str, Any]:
        interval = value.get("interval_minutes")
        return {
            "enabled": interval is not None,
            "interval_minutes": interval,
            "next_due_at": value.get("next_due_at"),
            "last_digest_at": value.get("last_digest_at"),
            "urgent_bypass": {
                "high_critical_decisions": True,
                "pane_errors": bool(value.get("urgent_pane_errors")),
            },
            "min_interval_minutes": MIN_REVIEW_INTERVAL_MINUTES,
            "max_interval_minutes": MAX_REVIEW_INTERVAL_MINUTES,
            "presets": [30, 60],
        }

    def _group_has_current_pending_prompt(
        self, group: Dict[str, Any]
    ) -> bool:
        """Return true only for the live prompt that verified a pending item."""
        agent = group.get("agent") or {}
        pane_id = str(agent.get("pane_id") or "")
        if not pane_id or agent.get("association") != "confirmed":
            return False
        observation = self._latest_observation(pane_id)
        if (
            observation is None
            or observation.status != "needs_input"
            or time.time() - observation.observed_at
            > max(10.0, self.cfg.poll_interval * 4)
        ):
            return False
        for public_decision in group.get("decisions", []):
            if public_decision.get("status") != "pending":
                continue
            decision = self.store.get_decision(
                str(public_decision.get("id") or ""), internal=True
            )
            if (
                not decision
                or decision.get("status") != "pending"
                or decision.get("agent_id") != agent.get("id")
                or not decision.get("_prompt_fingerprint")
                or decision.get("_prompt_fingerprint")
                != observation.prompt_fingerprint
            ):
                continue
            live_map = self._match_decision(decision, observation)
            if live_map and live_map == decision.get("_input_map"):
                return True
        return False

    def review_payload(
        self, panes: Sequence[Any], *, now: Optional[float] = None
    ) -> Dict[str, Any]:
        """Build the server-wide Review queue without acknowledging any work."""
        generated_at = float(now if now is not None else time.time())
        groups = self.store.review_groups()
        raw_settings = self.store.get_review_settings()
        settings = self._public_review_settings(raw_settings)

        panes_by_id = {
            str(getattr(pane, "id", "")): pane
            for pane in panes
            if getattr(pane, "id", None)
        }
        represented: set[str] = set()
        for group in groups:
            pane_id = str(group.get("agent", {}).get("pane_id") or "")
            pane = panes_by_id.get(pane_id)
            if not pane:
                continue
            status = str(getattr(pane, "status", ""))
            if (
                status == "needs_input"
                and self._group_has_current_pending_prompt(group)
            ):
                represented.add(pane_id)
            elif status == "error" and any(
                reason in ("agent_error", "extraction_degraded")
                for reason in group.get("attention_reasons", [])
            ):
                represented.add(pane_id)

        terminal_items: List[Dict[str, Any]] = []
        for pane_id, pane in panes_by_id.items():
            status = str(getattr(pane, "status", ""))
            if status not in ("needs_input", "error") or pane_id in represented:
                continue
            terminal_items.append(
                {
                    "id": "pane:" + pane_id,
                    "pane_id": pane_id,
                    "status": status,
                    "kind": str(getattr(pane, "kind", "") or "generic"),
                    "updated_at": float(getattr(pane, "updated", 0.0) or 0.0),
                    "acknowledgeable": False,
                }
            )
        terminal_items.sort(
            key=lambda item: (
                0 if item["status"] == "error" else 1,
                item["updated_at"],
                item["pane_id"],
            )
        )

        pending_count = sum(
            1
            for group in groups
            for decision in group.get("decisions", [])
            if decision.get("status") == "pending"
        )
        urgent_decisions = sum(
            1
            for group in groups
            for decision in group.get("decisions", [])
            if decision.get("status") == "pending"
            and decision.get("priority") in ("high", "critical")
        )
        urgent_pane_errors = (
            sum(
                1
                for pane in panes_by_id.values()
                if str(getattr(pane, "status", "")) == "error"
            )
            if raw_settings.get("urgent_pane_errors")
            else 0
        )
        has_work = bool(groups or terminal_items)
        next_due_at = raw_settings.get("next_due_at")
        is_due = bool(
            settings["enabled"]
            and next_due_at is not None
            and generated_at >= float(next_due_at)
        )
        urgent_items = urgent_decisions + urgent_pane_errors
        return {
            "version": 1,
            "generated_at": generated_at,
            "settings": settings,
            "due": {
                "is_due": is_due,
                "urgent": bool(urgent_items),
                "has_work": has_work,
                "next_due_at": next_due_at,
            },
            "counts": {
                "agents_changed": sum(
                    1 for group in groups if group.get("has_changes")
                ),
                "pending_decisions": pending_count,
                "terminal_requests": len(terminal_items),
                "total_cards": len(groups) + len(terminal_items),
                "urgent_items": urgent_items,
            },
            "groups": groups,
            "terminal_items": terminal_items,
        }

    def get_review_settings(self) -> Dict[str, Any]:
        return self._public_review_settings(self.store.get_review_settings())

    def review_notification_policy(self) -> Dict[str, bool]:
        if not self.enabled:
            return {
                "batching_enabled": False,
                "urgent_pane_errors": bool(self.cfg.push_on_error),
            }
        value = self.store.get_review_settings()
        batching = value.get("interval_minutes") is not None
        return {
            "batching_enabled": batching,
            "urgent_pane_errors": (
                bool(value.get("urgent_pane_errors"))
                if batching
                else bool(self.cfg.push_on_error)
            ),
        }

    def claim_review_due(
        self, *, has_work: bool, now: Optional[float] = None
    ) -> Dict[str, Any]:
        return self.store.claim_review_due(has_work=has_work, now=now)

    def review_schedule_is_due(self, *, now: Optional[float] = None) -> bool:
        if not self.enabled:
            return False
        settings = self.store.get_review_settings()
        next_due_at = settings.get("next_due_at")
        return bool(
            settings.get("interval_minutes") is not None
            and next_due_at is not None
            and float(now if now is not None else time.time())
            >= float(next_due_at)
        )

    def update_review_settings(
        self,
        *,
        interval_present: bool,
        interval_minutes: Optional[int],
        urgent_pane_errors: Optional[bool],
    ) -> Dict[str, Any]:
        if (
            interval_present
            and interval_minutes is not None
            and urgent_pane_errors is None
            and self.store.get_review_settings().get("interval_minutes") is None
        ):
            # Preserve an existing pane-error alert policy when batching is
            # first enabled. A PATCH that explicitly supplies false still
            # disables the bypass.
            urgent_pane_errors = bool(self.cfg.push_on_error)
        value = self.store.update_review_settings(
            interval_present=interval_present,
            interval_minutes=interval_minutes,
            urgent_pane_errors=urgent_pane_errors,
        )
        self.publish("review_settings_updated", "", 0, resources=["review"])
        self.kick()
        return self._public_review_settings(value)

    def acknowledge_review(self, session_id: str, snapshot_id: str) -> Dict[str, Any]:
        self.get_agent(session_id)
        try:
            value = self.store.review(session_id, snapshot_id)
        except KeyError:
            raise AgentConflict(
                "snapshot does not belong to this agent",
                self.store.resume(session_id),
            )
        self.publish(
            "review_updated",
            session_id,
            value["snapshot_sequence"],
            snapshot_id=value["snapshot_id"],
            resources=["review", "agents"],
        )
        self.kick()
        return value

    def list_decisions(self, cursor=None, limit=50, status=None, session_id=None):
        return self.store.list_decisions(cursor, limit, status, session_id)

    def get_decision(self, decision_id):
        value = self.store.get_decision(decision_id)
        if not value or value.get("status") == "unverified":
            raise AgentNotFound(decision_id)
        return value

    def send_message(self, session_id: str, text: str, client_message_id: str,
                     expected_binding_revision: int) -> Dict[str, Any]:
        text = bounded_text(text, 8_000)
        if not text:
            raise ValueError("message text is required")
        if not client_message_id or len(client_message_id) > 160:
            raise ValueError("client_message_id is required and must be at most 160 characters")
        agent = self.store.get_agent(session_id, internal=True)
        if not agent:
            raise AgentNotFound(session_id)
        with self._control_lock:
            repeat = self.store.get_message_by_client_id(session_id, client_message_id)
            if repeat:
                return repeat
            # Re-read under the same lock used by automatic/manual binding
            # updates so the checked revision is the one we actually drive.
            agent = self.store.get_agent(session_id, internal=True)
            assert agent is not None
            obs = self._validate_live(agent, expected_binding_revision, require_idle=True)
            message = self.store.reserve_sent_message(session_id, text, client_message_id)
            try:
                self.controller.send_message(obs.pane_id, text)
            except Exception as exc:
                message = self.store.set_message_status(message["id"], "unknown")
                raise AgentConflict(
                    "message delivery is uncertain; inspect the terminal before retrying", message
                ) from exc
            message = self.store.set_message_status(message["id"], "sent")
        self.publish("message_updated", session_id, agent["context"].get("revision", 0),
                     message_id=message["id"])
        self.kick()
        return message

    def reply_decision(self, decision_id: str, option_id: str, idempotency_key: str,
                       expected_revision: int, expected_binding_revision: int,
                       prompt_fingerprint: str,
                       custom_text: Optional[str] = None) -> Dict[str, Any]:
        if custom_text:
            raise AgentUnavailable("custom decision replies are not safe through the terminal adapter")
        if not idempotency_key or len(idempotency_key) > 160:
            raise ValueError("idempotency_key is required and must be at most 160 characters")
        decision = self.store.get_decision(decision_id, internal=True)
        if not decision or decision.get("status") == "unverified":
            raise AgentNotFound(decision_id)
        used = self.store.get_decision_by_reply_key(idempotency_key, internal=True)
        if used:
            if used["id"] == decision_id:
                return self.store.get_decision(decision_id)
            raise AgentConflict("idempotency key belongs to another decision",
                                self.store.get_decision(used["id"]))
        with self._control_lock:
            decision = self.store.get_decision(decision_id, internal=True)
            if not decision or decision.get("status") == "unverified":
                raise AgentNotFound(decision_id)
            used = self.store.get_decision_by_reply_key(idempotency_key, internal=True)
            if used:
                if used["id"] == decision_id:
                    return self.store.get_decision(decision_id)
                raise AgentConflict("idempotency key belongs to another decision",
                                    self.store.get_decision(used["id"]))
            if decision["status"] != "pending" or int(decision["revision"]) != int(expected_revision):
                raise AgentConflict("decision is stale", self.store.get_decision(decision_id))
            if not prompt_fingerprint or prompt_fingerprint != decision.get("_prompt_fingerprint"):
                raise AgentConflict("decision prompt fingerprint is stale", self.store.get_decision(decision_id))
            agent = self.store.get_agent(decision["agent_id"], internal=True)
            assert agent is not None
            key = decision.get("_input_map", {}).get(option_id)
            if not key:
                raise ValueError("unknown or unavailable option_id")
            obs = self._validate_live(
                agent, expected_binding_revision, require_idle=False,
                prompt_fingerprint=decision.get("_prompt_fingerprint"),
            )
            if obs.status != "needs_input":
                raise AgentConflict("decision is no longer visible", self.store.get_decision(decision_id))
            updated = self.store.mark_decision_submitting(decision_id, option_id, idempotency_key)
            try:
                self.controller.reply(obs.pane_id, key, agent["runtime"])
            except Exception as exc:
                updated = self.store.mark_decision_unknown(decision_id)
                raise AgentConflict(
                    "decision delivery is uncertain; inspect the terminal before retrying", updated
                ) from exc
        self.publish("decision_updated", agent["id"], updated["revision"], decision_id=decision_id)
        self.kick()
        return updated

    def bind(self, session_id: str, pane_id: str, expected_binding_revision: int) -> Dict[str, Any]:
        with self._control_lock:
            agent = self.store.get_agent(session_id, internal=True)
            if not agent:
                raise AgentNotFound(session_id)
            if int(agent["binding_revision"]) != int(expected_binding_revision):
                raise AgentConflict("stale binding revision", self.store.get_agent(session_id))
            obs = self._latest_observation(pane_id)
            if not obs:
                raise AgentNotFound(pane_id)
            if obs.runtime != agent["runtime"] or os.path.realpath(obs.cwd) != os.path.realpath(agent["_source_cwd"]):
                raise AgentConflict("pane runtime or working directory does not match this session", agent)
            caps = default_capabilities("confirmed")
            caps.update({"runtime": agent["runtime"], "parser_version": agent["_parser_version"],
                         "chat_send": "idle_only" if obs.status == "idle" else "unavailable"})
            self._invalidate_other_bindings(session_id, obs.pane_id, obs.incarnation)
            result = self.store.update_binding(
                session_id, association="confirmed", pane_id=obs.pane_id, target=obs.target,
                pane_pid=obs.pid, pane_created=obs.pane_created, pane_incarnation=obs.incarnation,
                source="manual", capabilities=caps,
            )
        self.publish("agent_updated", session_id, result["context"].get("revision", 0))
        return self.store.get_agent(session_id)

    def unbind(self, session_id: str, expected_binding_revision: int) -> Dict[str, Any]:
        with self._control_lock:
            agent = self.store.get_agent(session_id, internal=True)
            if not agent:
                raise AgentNotFound(session_id)
            if int(agent["binding_revision"]) != int(expected_binding_revision):
                raise AgentConflict("stale binding revision", self.store.get_agent(session_id))
            result = self.store.update_binding(
                session_id, association="unavailable", pane_id=None, target=None, pane_pid=None,
                pane_created=None, pane_incarnation=None, source="automatic",
                capabilities=default_capabilities("unavailable"),
            )
        self.publish("agent_updated", session_id, result["context"].get("revision", 0))
        return self.store.get_agent(session_id)

    def delete_history(self, session_id: str) -> None:
        with self._control_lock:
            self._history_generation[session_id] += 1
            if not self.store.delete_history(session_id):
                raise AgentNotFound(session_id)
        self.publish("history_deleted", session_id, 0)

    # -- agent websocket invalidations ----------------------------------- #
    @property
    def event_cursor(self) -> int:
        with self._event_lock:
            return self._event_cursor

    def publish(self, kind: str, agent_id: str, revision: int, **extra) -> None:
        if not self.enabled:
            return
        with self._event_lock:
            self._event_cursor += 1
            envelope = {
                "type": "agent_event", "cursor": self._event_cursor,
                "event": {"kind": kind, "agent_id": agent_id, "revision": revision, **extra},
            }
            self._history.append(envelope)
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._fanout, envelope)

    def _fanout(self, envelope: Dict[str, Any]) -> None:
        with self._event_lock:
            subscribers = list(self._subscribers.items())
        for queue, seen_cursor in subscribers:
            if int(envelope["cursor"]) <= seen_cursor:
                continue
            try:
                queue.put_nowait(envelope)
                with self._event_lock:
                    self._subscribers[queue] = int(envelope["cursor"])
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait({
                        "type": "reset", "cursor": self._event_cursor,
                        "reason": "subscriber_lagged",
                    })
                    with self._event_lock:
                        self._subscribers[queue] = self._event_cursor
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def subscribe(self, after: Optional[int] = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._event_lock:
            seen_cursor = self._event_cursor if after is None else after
            if after is not None:
                events = [item for item in self._history if int(item["cursor"]) > after]
                if (events and int(events[0]["cursor"]) == after + 1
                        and len(events) <= queue.maxsize):
                    for item in events:
                        queue.put_nowait(item)
                    seen_cursor = int(events[-1]["cursor"])
                elif after < self._event_cursor:
                    queue.put_nowait({"type": "reset", "cursor": self._event_cursor,
                                      "reason": "cursor_expired"})
                    seen_cursor = self._event_cursor
            self._subscribers[queue] = seen_cursor
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._event_lock:
            self._subscribers.pop(queue, None)

    def _close_subscribers(self, reason: str) -> None:
        """Wake every agent socket so the server can close it immediately."""
        with self._event_lock:
            queues = list(self._subscribers)
        envelope = {"type": "workspace_disabled", "reason": reason}
        for queue in queues:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(envelope)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    # Shutdown is already in progress, so a racing subscriber
                    # queue needs no further delivery or recovery attempt.
                    pass

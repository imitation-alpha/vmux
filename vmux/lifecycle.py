"""Server-owned pane lifecycle arbitration and bounded in-memory history.

This module is deliberately pure: callers provide already-sanitized evidence and
the kernel returns a stable public contract.  It never receives terminal text,
paths, prompts, commands, or runtime/session identifiers.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Tuple

LIFECYCLE_VERSION = 1
HISTORY_LIMIT = 32
STRUCTURED_FRESH_SECONDS = 8.0
STRUCTURED_MAX_AGE_SECONDS = 15.0

STATES = {"blocked", "error", "working", "done", "idle", "offline", "unknown"}
AUTHORITIES = {"process", "structured_log", "terminal_ui", "terminal_activity", "fallback", "user"}
CONFIDENCES = {"high", "medium", "low"}
FRESHNESSES = {"fresh", "aging", "stale"}


@dataclass(frozen=True)
class LifecycleEvidence:
    state: str
    reason: str
    authority: str
    confidence: str
    observed_at: float
    freshness: str = "fresh"

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError("invalid lifecycle state")
        if self.authority not in AUTHORITIES:
            raise ValueError("invalid lifecycle authority")
        if self.confidence not in CONFIDENCES:
            raise ValueError("invalid lifecycle confidence")
        if self.freshness not in FRESHNESSES:
            raise ValueError("invalid lifecycle freshness")
        if not self.reason or len(self.reason) > 80:
            raise ValueError("invalid lifecycle reason")

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "reason": self.reason,
            "authority": self.authority,
            "confidence": self.confidence,
            "freshness": self.freshness,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class LifecycleSummary:
    state: str
    reason: str
    authority: str
    confidence: str
    freshness: str
    transitioned_at: float
    revision: int
    conflicted: bool = False

    def to_dict(self) -> dict:
        return {
            "version": LIFECYCLE_VERSION,
            "state": self.state,
            "reason": self.reason,
            "authority": self.authority,
            "confidence": self.confidence,
            "freshness": self.freshness,
            "transitioned_at": self.transitioned_at,
            "revision": self.revision,
            "conflicted": self.conflicted,
        }


@dataclass(frozen=True)
class LifecycleTransition:
    from_state: str
    to_state: str
    reason: str
    authority: str
    transitioned_at: float
    revision: int

    def to_dict(self) -> dict:
        return {
            "from": self.from_state,
            "to": self.to_state,
            "reason": self.reason,
            "authority": self.authority,
            "transitioned_at": self.transitioned_at,
            "revision": self.revision,
        }


@dataclass
class _PaneMachine:
    incarnation: str
    revision_base: int = 0
    summary: Optional[LifecycleSummary] = None
    history: Deque[LifecycleTransition] = field(default_factory=lambda: deque(maxlen=HISTORY_LIMIT))
    identity: Tuple[dict, ...] = ()
    winning: Optional[LifecycleEvidence] = None
    rejected: Tuple[LifecycleEvidence, ...] = ()


def project_legacy_status(state: str) -> str:
    return {
        "blocked": "needs_input",
        "error": "error",
        "working": "working",
        "done": "idle",
        "idle": "idle",
        "offline": "offline",
        "unknown": "idle",
    }.get(state, "idle")


def structured_freshness(observed_at: float, now: float) -> str:
    age = max(0.0, now - observed_at)
    if age <= STRUCTURED_FRESH_SECONDS:
        return "fresh"
    if age <= STRUCTURED_MAX_AGE_SECONDS:
        return "aging"
    return "stale"


def _confidence_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(value, 0)


def arbitrate(evidence: Iterable[LifecycleEvidence], *, process_present: bool, now: float) -> Tuple[LifecycleEvidence, List[LifecycleEvidence], bool]:
    """Choose one source according to the pane lifecycle authority contract."""
    items = [
        LifecycleEvidence(
            item.state, item.reason, item.authority, item.confidence,
            item.observed_at,
            structured_freshness(item.observed_at, now)
            if item.authority == "structured_log" else item.freshness,
        )
        for item in evidence
    ]
    if not process_present:
        winner = LifecycleEvidence("offline", "process_missing", "process", "high", now)
        conflicted = any(
            item.state != "offline" and item.freshness in ("fresh", "aging")
            and _confidence_rank(item.confidence) >= 1
            for item in items
        )
        return winner, items, conflicted

    eligible: List[LifecycleEvidence] = []
    for item in items:
        if item.authority == "structured_log":
            if item.freshness == "stale" or item.state in ("offline", "blocked"):
                continue
        eligible.append(item)

    def priority(item: LifecycleEvidence) -> Tuple[int, int, float]:
        # Verified terminal prompts are the only author of blocked. Structured
        # state then precedes explicit UI, error matches, activity, and fallback.
        if item.state == "blocked" and item.authority == "terminal_ui" and item.confidence == "high":
            base = 700
        elif item.authority == "structured_log":
            base = 600
        elif item.authority == "terminal_ui" and item.state in ("working", "idle"):
            base = 500
        elif item.authority == "terminal_ui" and item.state == "error":
            base = 400
        elif item.authority == "terminal_activity":
            base = 300
        elif item.authority == "fallback":
            base = 100
        else:
            base = 0
        return base, _confidence_rank(item.confidence), item.observed_at

    if eligible:
        winner = max(eligible, key=priority)
    else:
        winner = LifecycleEvidence("unknown", "no_eligible_evidence", "fallback", "low", now)
    rejected = [item for item in items if item != winner]
    conflicted = any(
        item.state != winner.state
        and item.freshness in ("fresh", "aging")
        and _confidence_rank(item.confidence) >= 1
        for item in rejected
    )
    return winner, rejected, conflicted


class LifecycleConflict(RuntimeError):
    def __init__(self, current: dict):
        super().__init__("lifecycle revision changed")
        self.current = current


class LifecycleKernel:
    """Per-process collection of state machines isolated by pane incarnation."""

    def __init__(self, history_limit: int = HISTORY_LIMIT):
        self.history_limit = max(1, min(HISTORY_LIMIT, int(history_limit)))
        self._panes: Dict[str, _PaneMachine] = {}

    def observe(self, pane_id: str, incarnation: str, evidence: Iterable[LifecycleEvidence], *, identity: Iterable[dict] = (), process_present: bool = True, now: float) -> LifecycleSummary:
        machine = self._panes.get(pane_id)
        if machine is None or machine.incarnation != incarnation:
            revision_base = machine.summary.revision if machine and machine.summary else 0
            machine = _PaneMachine(
                incarnation=incarnation, revision_base=revision_base,
                history=deque(maxlen=self.history_limit),
            )
            self._panes[pane_id] = machine

        winner, rejected, conflicted = arbitrate(evidence, process_present=process_present, now=now)
        previous = machine.summary
        next_state = winner.state
        next_reason = winner.reason
        next_authority = winner.authority
        # Completion is a derived edge, never an initial observation.
        if previous and previous.state == "working" and winner.state == "idle":
            next_state, next_reason = "done", "work_became_idle"
        elif previous and previous.state == "done" and winner.state in ("working", "idle", "unknown"):
            next_state = "done"
            next_reason, next_authority = previous.reason, previous.authority

        transitioned = previous is None or previous.state != next_state
        revision = (previous.revision if previous else machine.revision_base) + (1 if transitioned else 0)
        transitioned_at = now if transitioned else previous.transitioned_at
        summary = LifecycleSummary(
            next_state, next_reason, next_authority, winner.confidence,
            winner.freshness, transitioned_at, revision, conflicted,
        )
        if transitioned:
            machine.history.append(LifecycleTransition(
                previous.state if previous else "unknown", next_state,
                next_reason, next_authority, now, revision,
            ))
        machine.summary = summary
        machine.identity = tuple(dict(item) for item in identity)
        machine.winning = winner
        machine.rejected = tuple(rejected)
        return summary

    def acknowledge(self, pane_id: str, expected_revision: int, *, now: float) -> LifecycleSummary:
        machine = self._panes.get(pane_id)
        if machine is None or machine.summary is None:
            raise KeyError(pane_id)
        current = machine.summary
        if int(expected_revision) != current.revision:
            raise LifecycleConflict(self.diagnostics(pane_id))
        if current.state != "done":
            return current
        revision = current.revision + 1
        summary = LifecycleSummary(
            "idle", "done_acknowledged", "user", "high", "fresh", now, revision, current.conflicted,
        )
        machine.history.append(LifecycleTransition("done", "idle", "done_acknowledged", "user", now, revision))
        machine.summary = summary
        prior = ([machine.winning] if machine.winning else []) + list(machine.rejected)
        machine.winning = LifecycleEvidence("idle", "done_acknowledged", "user", "high", now)
        machine.rejected = tuple(prior)
        return summary

    def acknowledge_current_done(self, pane_id: str, *, now: float) -> Optional[LifecycleSummary]:
        machine = self._panes.get(pane_id)
        if not machine or not machine.summary or machine.summary.state != "done":
            return machine.summary if machine else None
        return self.acknowledge(pane_id, machine.summary.revision, now=now)

    def current(self, pane_id: str) -> Optional[LifecycleSummary]:
        machine = self._panes.get(pane_id)
        return machine.summary if machine else None

    def diagnostics(self, pane_id: str, limit: int = HISTORY_LIMIT) -> dict:
        machine = self._panes.get(pane_id)
        if machine is None or machine.summary is None:
            raise KeyError(pane_id)
        bounded = max(1, min(self.history_limit, int(limit)))
        return {
            "id": pane_id,
            "identity": [dict(item) for item in machine.identity],
            "current": machine.summary.to_dict(),
            "winning_evidence": machine.winning.to_dict() if machine.winning else None,
            "rejected_evidence": [item.to_dict() for item in machine.rejected],
            "history": [item.to_dict() for item in list(machine.history)[-bounded:]],
        }

    def prune(self, pane_ids: Iterable[str]) -> None:
        live = set(pane_ids)
        self._panes = {pane_id: value for pane_id, value in self._panes.items() if pane_id in live}

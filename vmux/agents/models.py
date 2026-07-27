"""Internal and wire models for the agent-context subsystem.

The dataclasses are deliberately dependency-free.  FastAPI/Pydantic owns HTTP
validation; these types define the narrow boundary between pane observation,
runtime adapters, projection, and storage.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAX_MESSAGE_CHARS = 20_000
MAX_SUMMARY_CHARS = 2_000
MAX_ITEM_CHARS = 500


def bounded_text(value: Any, limit: int = MAX_ITEM_CHARS) -> str:
    """Return bounded display text with terminal control characters removed."""
    if not isinstance(value, str):
        return ""
    # Keep newlines/tabs for visible chat, but remove terminal escapes and other
    # C0 controls.  Session-log tool outputs never reach this helper.
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32)
    value = value.strip()
    if len(value) > limit:
        return value[: max(0, limit - 1)].rstrip() + "…"
    return value


def fingerprint_terminal(text: str) -> str:
    """Fingerprint only the current visible prompt region; never persist it."""
    lines = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    tail = "\n".join(lines[-16:])
    return hashlib.sha256(tail.encode("utf-8", "replace")).hexdigest()


@dataclass(frozen=True)
class PaneObservation:
    pane_id: str
    target: str
    command: str
    title: str
    cwd: str
    pid: str
    pane_created: float
    runtime: Optional[str]
    status: str
    question: Optional[str]
    menu: Tuple[Dict[str, Any], ...]
    prompt_fingerprint: str
    observed_at: float = field(default_factory=time.time)

    @property
    def incarnation(self) -> str:
        raw = "%s\0%s\0%s" % (self.pane_id, self.pid, self.pane_created)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class RuntimeCandidate:
    runtime: str
    native_session_id: str
    path: str
    cwd: str
    modified_at: float
    inode: int
    started_at: float = 0.0
    parser_version: str = "1"


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    kind: str
    created_at: float
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ReadResult:
    events: Tuple[RuntimeEvent, ...]
    offset: int
    inode: int
    parser_version: str
    error: Optional[str] = None


def empty_context(session_id: str, runtime: str) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "runtime": runtime,
        "goal": "",
        "current_task": "",
        "progress_summary": "",
        "completed_items": [],
        "decisions": [],
        "blockers": [],
        "next_action": "",
        "progress": None,
        "estimated_completion": None,
        "lifecycle": "observing",
        "extraction_health": "ok",
        "revision": 0,
        "last_updated": time.time(),
        "provenance": {},
    }


def default_capabilities(association: str = "unavailable") -> Dict[str, Any]:
    return {
        "association": association,
        "context": "structured",
        "chat_send": "unavailable",
        "decision_reply": "open_terminal",
        "delivery_ack": "log_observed",
    }


def semantic_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Compute a deterministic, raw-content-free delta between projections."""
    before_done = {str(i.get("id")): i for i in before.get("completed_items", []) if i.get("id")}
    after_done = {str(i.get("id")): i for i in after.get("completed_items", []) if i.get("id")}
    before_block = {str(i.get("id")): i for i in before.get("blockers", []) if i.get("id")}
    after_block = {str(i.get("id")): i for i in after.get("blockers", []) if i.get("id")}
    before_decisions = {str(i.get("id")): i for i in before.get("decisions", []) if i.get("id")}
    after_decisions = {str(i.get("id")): i for i in after.get("decisions", []) if i.get("id")}
    delta: Dict[str, Any] = {
        "completed": [item for key, item in after_done.items() if key not in before_done],
        "new_blockers": [item for key, item in after_block.items() if key not in before_block],
        "resolved_blockers": [item for key, item in before_block.items() if key not in after_block],
        "decisions_added": [item for key, item in after_decisions.items() if key not in before_decisions],
        "decisions_resolved": [item for key, item in before_decisions.items() if key not in after_decisions],
    }
    for key in ("goal", "current_task", "next_action", "lifecycle"):
        if before.get(key) != after.get(key):
            delta[key + "_changed"] = {"from": before.get(key), "to": after.get(key)}
    before_health = before.get("extraction_health", "ok")
    after_health = after.get("extraction_health", "ok")
    if before_health != after_health:
        delta["extraction_health_changed"] = {
            "from": before_health,
            "to": after_health,
        }
    return {key: value for key, value in delta.items() if value not in ([], {}, None)}


def project_events(
    context: Dict[str, Any], events: List[RuntimeEvent]
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Project normalized events into context, visible messages, and decisions.

    Runtime adapters have already discarded reasoning, tool arguments/results,
    and other non-display records.  This projector therefore accepts only the
    explicit event kinds below.
    """
    out = dict(context)
    out["completed_items"] = list(context.get("completed_items", []))
    out["blockers"] = list(context.get("blockers", []))
    out["provenance"] = dict(context.get("provenance", {}))
    messages: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    resolved: List[str] = []
    completed = {str(item.get("id")): item for item in out["completed_items"] if item.get("id")}
    blockers = {str(item.get("id")): item for item in out["blockers"] if item.get("id")}

    for event in events:
        payload = event.payload
        if event.kind in ("user_message", "assistant_message"):
            role = "user" if event.kind == "user_message" else "assistant"
            content = bounded_text(payload.get("content"), MAX_MESSAGE_CHARS)
            if not content:
                continue
            messages.append({
                "native_event_id": event.event_id,
                "role": role,
                "content": content,
                "created_at": event.created_at,
                "status": "observed",
            })
            if role == "user":
                first = bounded_text(content.splitlines()[0], MAX_ITEM_CHARS)
                if first:
                    if not out.get("goal"):
                        out["goal"] = first
                        out["provenance"]["goal"] = "visible_user_message"
                    out["current_task"] = first
                    out["provenance"]["current_task"] = "visible_user_message"
                out["lifecycle"] = "working"
            else:
                out["progress_summary"] = bounded_text(content, MAX_SUMMARY_CHARS)
                out["provenance"]["progress_summary"] = "visible_assistant_message"
        elif event.kind == "plan":
            # update_plan/TodoWrite payloads are full plan snapshots.  Items
            # omitted from the new plan are no longer plan-sourced blockers.
            blockers = {
                key: item for key, item in blockers.items()
                if item.get("source") != "runtime_plan"
            }
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            normalized: List[Dict[str, Any]] = []
            for index, item in enumerate(items[:100]):
                if not isinstance(item, dict):
                    continue
                label = bounded_text(item.get("step") or item.get("content") or item.get("title"))
                if not label:
                    continue
                item_id = str(item.get("id") or "%s:%d" % (event.event_id, index))
                status = str(item.get("status") or "pending").lower()
                normalized.append({"id": item_id, "title": label, "status": status})
                if status in ("completed", "complete", "done"):
                    completed[item_id] = {"id": item_id, "title": label, "completed_at": event.created_at}
                if status in ("blocked", "blocking"):
                    blockers[item_id] = {
                        "id": item_id, "title": label, "created_at": event.created_at,
                        "source": "runtime_plan",
                    }
                else:
                    blockers.pop(item_id, None)
            active = next((i for i in normalized if i["status"] in ("in_progress", "active", "working")), None)
            pending = next((i for i in normalized if i["status"] in ("pending", "todo")), None)
            chosen = active or pending
            if chosen:
                out["current_task"] = chosen["title"]
                out["next_action"] = chosen["title"]
                out["provenance"]["current_task"] = "runtime_plan"
                out["provenance"]["next_action"] = "runtime_plan"
            total = len(normalized)
            done = sum(1 for i in normalized if i["status"] in ("completed", "complete", "done"))
            out["progress"] = (
                {"completed": done, "total": total, "percent": round(done * 100 / total), "source": "runtime_plan"}
                if total else None
            )
        elif event.kind == "task_update":
            task_id = str(payload.get("id") or event.event_id)
            title = bounded_text(payload.get("title") or payload.get("subject"))
            status = str(payload.get("status") or "in_progress").lower()
            if status in ("completed", "complete", "done") and title:
                completed[task_id] = {"id": task_id, "title": title, "completed_at": event.created_at}
                blockers.pop(task_id, None)
            elif status in ("blocked", "blocking") and title:
                blockers[task_id] = {
                    "id": task_id, "title": title, "created_at": event.created_at,
                    "source": "runtime_task",
                }
            elif title:
                blockers.pop(task_id, None)
                out["current_task"] = title
                out["next_action"] = title
                out["provenance"]["current_task"] = "runtime_task"
                out["provenance"]["next_action"] = "runtime_task"
        elif event.kind == "decision":
            decision = dict(payload)
            decision["native_event_id"] = event.event_id
            decision["created_at"] = event.created_at
            decisions.append(decision)
            out["lifecycle"] = "waiting"
        elif event.kind == "decision_resolved":
            native_id = str(payload.get("native_event_id") or "")
            if native_id:
                resolved.append(native_id)
        elif event.kind == "lifecycle":
            state = str(payload.get("state") or "")
            if state in ("observing", "working", "idle", "waiting", "completed", "error", "offline"):
                out["lifecycle"] = state

    out["completed_items"] = list(completed.values())[-100:]
    out["blockers"] = list(blockers.values())[-100:]
    if events:
        out["last_updated"] = max(event.created_at for event in events)
    return out, messages, decisions, resolved

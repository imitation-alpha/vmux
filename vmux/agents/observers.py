"""Version-tolerant, read-only adapters for Codex and Claude session JSONL.

Only visible user/assistant messages, explicit plans, and explicit structured
questions are normalized.  Reasoning, encrypted reasoning, tool arguments,
tool results, terminal output, and arbitrary event payloads are discarded.
"""

from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import (
    PaneObservation,
    ReadResult,
    RuntimeCandidate,
    RuntimeEvent,
    bounded_text,
)

MAX_READ_BYTES = 4 * 1024 * 1024
MAX_READ_RECORDS = 5_000
MAX_LINE_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 256 * 1024
MAX_METADATA_LINE_BYTES = 64 * 1024


def runtime_from_command(command: str) -> Optional[str]:
    base = os.path.basename(command or "").lower()
    if base == "codex" or base.startswith("codex-") or base.startswith("codex."):
        return "codex"
    if base == "claude" or base.startswith("claude-") or base.startswith("claude."):
        return "claude"
    return None


def _timestamp(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            from datetime import datetime

            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            pass
    return fallback


def _json_maybe(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _content_text(content: Any, *, input_types: Iterable[str]) -> str:
    if isinstance(content, str):
        return bounded_text(content, 20_000)
    if not isinstance(content, list):
        return ""
    accepted = set(input_types)
    chunks: List[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in accepted:
            continue
        text = bounded_text(block.get("text") or block.get("content"), 20_000)
        if text:
            chunks.append(text)
    return bounded_text("\n".join(chunks), 20_000)


def _decision_payload(name: str, arguments: Dict[str, Any], index: int = 0) -> Optional[Dict[str, Any]]:
    questions = arguments.get("questions")
    if not isinstance(questions, list) or index >= len(questions) or not isinstance(questions[index], dict):
        if name == "ExitPlanMode":
            return {
                "title": "Approve the plan?",
                "description": "The agent is waiting to leave plan mode.",
                "kind": "plan_approval",
                "priority": "normal",
                "options": [
                    {"id": "approve", "label": "Approve", "description": "Continue with implementation."},
                    {"id": "reject", "label": "Reject", "description": "Keep planning."},
                ],
                "recommendation": None,
                "allow_custom": False,
            }
        return None
    question = questions[index]
    priority = str(question.get("priority") or "normal").strip().lower()
    if priority not in ("low", "normal", "high", "critical"):
        priority = "normal"
    title = bounded_text(question.get("header") or question.get("question") or "Decision required", 160)
    description = bounded_text(question.get("question") or title, 2_000)
    options: List[Dict[str, Any]] = []
    raw_options = question.get("options") if isinstance(question.get("options"), list) else []
    for opt_index, option in enumerate(raw_options[:20]):
        if isinstance(option, str):
            label = bounded_text(option, 240)
            desc = ""
            option_id = str(opt_index + 1)
        elif isinstance(option, dict):
            label = bounded_text(option.get("label") or option.get("value"), 240)
            desc = bounded_text(option.get("description"), 500)
            option_id = str(option.get("id") or option.get("value") or opt_index + 1)
        else:
            continue
        if label:
            options.append({"id": option_id, "label": label, "description": desc})
    if not description or not options:
        return None
    recommendation = question.get("recommendation")
    recommendation_id = None
    if recommendation is not None:
        rec = _norm_for_option(str(recommendation))
        for option in options:
            if rec in (_norm_for_option(option["id"]), _norm_for_option(option["label"])):
                recommendation_id = option["id"]
                break
    if recommendation_id is None:
        recommended = [option for option in options if "(recommended)" in option["label"].lower()]
        if len(recommended) == 1:
            recommendation_id = recommended[0]["id"]
    for option in options:
        option["recommended"] = option["id"] == recommendation_id
    return {
        "title": title,
        "description": description,
        "kind": "question",
        "priority": priority,
        "options": options,
        "recommendation": recommendation_id,
        "allow_custom": False,
    }


def _norm_for_option(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower())).replace(" recommended", "").strip()


class _JSONLObserver:
    runtime = ""
    parser_version = "1"

    def __init__(self, root: str):
        self.root = os.path.expanduser(root)
        self._meta_cache: Dict[str, Tuple[int, int, int, RuntimeCandidate]] = {}

    def matches(self, pane: PaneObservation) -> bool:
        return pane.runtime == self.runtime or runtime_from_command(pane.command) == self.runtime

    def _paths(self) -> List[str]:
        return []

    def _metadata(self, path: str) -> Optional[RuntimeCandidate]:
        raise NotImplementedError

    def discover(self, pane: PaneObservation) -> List[RuntimeCandidate]:
        found: List[RuntimeCandidate] = []
        # Newest files first and cap the inspection budget.  Metadata is cached
        # by inode/size, so the hot path does not reparse old logs.
        paths = self._paths()
        live_paths = set(paths)
        for cached_path in list(self._meta_cache):
            if cached_path not in live_paths:
                self._meta_cache.pop(cached_path, None)
        paths.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
        for path in paths[:250]:
            try:
                candidate = self._metadata(path)
            except (OSError, ValueError):
                continue
            if candidate and os.path.realpath(candidate.cwd) == os.path.realpath(pane.cwd):
                found.append(candidate)
        return found

    def read(self, candidate: RuntimeCandidate, offset: int, inode: Optional[int]) -> ReadResult:
        events: List[RuntimeEvent] = []
        read_error = None
        try:
            stat = os.stat(candidate.path)
            if inode is not None and inode != stat.st_ino:
                offset = 0
            if offset < 0 or offset > stat.st_size:
                offset = 0
            with open(candidate.path, "rb") as fh:
                mid_discard = False
                if offset > 0:
                    fh.seek(offset - 1)
                    mid_discard = fh.read(1) != b"\n"
                fh.seek(offset)
                bytes_read = 0
                records_read = 0
                while True:
                    if bytes_read >= MAX_READ_BYTES or records_read >= MAX_READ_RECORDS:
                        break
                    line_start = fh.tell()
                    allowance = min(MAX_LINE_BYTES + 1, MAX_READ_BYTES - bytes_read)
                    raw = fh.readline(allowance)
                    if not raw:
                        break
                    bytes_read += len(raw)
                    if len(raw) > MAX_LINE_BYTES:
                        # Advance one bounded chunk. A later pass continues
                        # from the middle and eventually reaches/discards the
                        # newline, without scanning an unbounded record now.
                        read_error = "oversized_line_discarded"
                        if not raw.endswith(b"\n"):
                            break
                        continue
                    if not raw.endswith(b"\n"):
                        if mid_discard:
                            read_error = "oversized_line_discarded"
                            if len(raw) == allowance and fh.tell() < stat.st_size:
                                break
                            continue
                        if allowance < MAX_LINE_BYTES + 1 and len(raw) == allowance:
                            fh.seek(line_start)  # pass byte budget ended mid-record
                            break
                        fh.seek(line_start)
                        break  # writer has not completed this JSON object yet
                    records_read += 1
                    try:
                        record = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    events.extend(self._parse(record, line_start, stat.st_mtime))
                new_offset = fh.tell()
            # Codex can emit the same visible message once as event_msg and
            # once as response_item.  Collapse only those cross-variant twins
            # with the exact runtime timestamp; repeated user messages remain.
            if self.runtime == "codex":
                filtered: List[RuntimeEvent] = []
                twins: Dict[Tuple[str, str, float], str] = {}
                for event in events:
                    source = str(event.payload.get("_source") or "")
                    key = (event.kind, str(event.payload.get("content") or ""), event.created_at)
                    prior = twins.get(key)
                    if prior and prior != source and {prior, source} == {"event_msg", "response_item"}:
                        continue
                    twins[key] = source
                    filtered.append(event)
                events = filtered
            return ReadResult(tuple(events), new_offset, stat.st_ino, self.parser_version, read_error)
        except OSError as exc:
            return ReadResult(tuple(), offset, inode or 0, self.parser_version, type(exc).__name__)

    def _parse(self, record: Dict[str, Any], line_offset: int, mtime: float) -> List[RuntimeEvent]:
        raise NotImplementedError


class CodexObserver(_JSONLObserver):
    runtime = "codex"
    parser_version = "codex-jsonl-v1"

    def _paths(self) -> List[str]:
        return glob.glob(os.path.join(self.root, "sessions", "**", "*.jsonl"), recursive=True)

    def _metadata(self, path: str) -> Optional[RuntimeCandidate]:
        stat = os.stat(path)
        cache = self._meta_cache.get(path)
        if cache and cache[0] == stat.st_ino and cache[1] == stat.st_size and cache[2] == stat.st_mtime_ns:
            return cache[3]
        native_id = ""
        cwd = ""
        started_at = 0.0
        with open(path, "rb") as fh:
            metadata_bytes = 0
            for _ in range(40):
                if metadata_bytes >= MAX_METADATA_BYTES:
                    break
                raw = fh.readline(min(MAX_METADATA_LINE_BYTES, MAX_METADATA_BYTES - metadata_bytes))
                metadata_bytes += len(raw)
                if not raw:
                    break
                try:
                    record = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
                if not started_at:
                    started_at = _timestamp(record.get("timestamp") or payload.get("timestamp"), 0.0)
                if record.get("type") == "session_meta" or payload.get("type") == "session_meta":
                    native_id = str(payload.get("id") or payload.get("session_id") or "")
                    cwd = str(payload.get("cwd") or "")
                    break
        if not native_id:
            name = Path(path).stem
            native_id = name.rsplit("-", 1)[-1]
        if not cwd:
            return None
        candidate = RuntimeCandidate(
            self.runtime, native_id, path, cwd, stat.st_mtime, stat.st_ino,
            started_at, self.parser_version
        )
        self._meta_cache[path] = (stat.st_ino, stat.st_size, stat.st_mtime_ns, candidate)
        return candidate

    def _parse(self, record: Dict[str, Any], line_offset: int, mtime: float) -> List[RuntimeEvent]:
        outer_type = str(record.get("type") or "")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        when = _timestamp(record.get("timestamp") or payload.get("timestamp"), mtime)
        base_id = str(record.get("id") or payload.get("id") or "offset:%d" % line_offset)
        events: List[RuntimeEvent] = []

        if outer_type == "event_msg":
            kind = str(payload.get("type") or "")
            if kind == "user_message":
                text = bounded_text(payload.get("message"), 20_000)
                if text:
                    events.append(RuntimeEvent(base_id, "user_message", when, {
                        "content": text, "_source": "event_msg",
                    }))
            elif kind in ("agent_message", "assistant_message"):
                text = bounded_text(payload.get("message"), 20_000)
                if text:
                    events.append(RuntimeEvent(base_id, "assistant_message", when, {
                        "content": text, "_source": "event_msg",
                    }))
            elif kind in ("task_started", "turn_started"):
                events.append(RuntimeEvent(base_id, "lifecycle", when, {"state": "working"}))
            elif kind in ("task_complete", "turn_complete", "turn_completed"):
                events.append(RuntimeEvent(base_id, "lifecycle", when, {"state": "idle"}))
            return events

        if outer_type != "response_item":
            return events
        item_type = str(payload.get("type") or "")
        # Explicitly ignore chain-of-thought and encrypted/internal records.
        if item_type in ("reasoning", "analysis", "encrypted_content", "function_call_output"):
            if item_type == "function_call_output" and payload.get("call_id"):
                events.append(RuntimeEvent(base_id, "decision_resolved", when, {
                    "native_event_id": str(payload.get("call_id")),
                }))
            return events
        if item_type == "message":
            role = str(payload.get("role") or "")
            if role not in ("user", "assistant"):
                return events
            text = _content_text(payload.get("content"), input_types=("input_text", "output_text", "text"))
            if text:
                events.append(RuntimeEvent(base_id, role + "_message", when, {
                    "content": text, "_source": "response_item",
                }))
        elif item_type in ("function_call", "tool_call"):
            name = str(payload.get("name") or "")
            arguments = _json_maybe(payload.get("arguments") or payload.get("input"))
            call_id = str(payload.get("call_id") or base_id)
            if name == "update_plan":
                plan = arguments.get("plan") or arguments.get("items")
                if isinstance(plan, list):
                    events.append(RuntimeEvent(call_id, "plan", when, {"items": plan}))
            elif name == "request_user_input":
                questions = arguments.get("questions") if isinstance(arguments.get("questions"), list) else []
                for index in range(len(questions)):
                    decision = _decision_payload(name, arguments, index)
                    if decision:
                        events.append(RuntimeEvent("%s:%d" % (call_id, index), "decision", when, decision))
        return events


class ClaudeObserver(_JSONLObserver):
    runtime = "claude"
    parser_version = "claude-jsonl-v1"

    def _paths(self) -> List[str]:
        # Recursive globbing also picks up nested subagent transcripts, which
        # can reuse the parent session id.  Root transcripts are direct
        # children of the encoded project directory.
        return glob.glob(os.path.join(self.root, "projects", "*", "*.jsonl"))

    def _metadata(self, path: str) -> Optional[RuntimeCandidate]:
        stat = os.stat(path)
        cache = self._meta_cache.get(path)
        if cache and cache[0] == stat.st_ino and cache[1] == stat.st_size and cache[2] == stat.st_mtime_ns:
            return cache[3]
        native_id = Path(path).stem
        cwd = ""
        started_at = 0.0
        with open(path, "rb") as fh:
            metadata_bytes = 0
            for _ in range(80):
                if metadata_bytes >= MAX_METADATA_BYTES:
                    break
                raw = fh.readline(min(MAX_METADATA_LINE_BYTES, MAX_METADATA_BYTES - metadata_bytes))
                metadata_bytes += len(raw)
                if not raw:
                    break
                try:
                    record = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                native_id = str(record.get("sessionId") or record.get("session_id") or native_id)
                cwd = str(record.get("cwd") or cwd)
                if not started_at:
                    started_at = _timestamp(record.get("timestamp"), 0.0)
                if cwd:
                    break
        if not cwd:
            return None
        candidate = RuntimeCandidate(
            self.runtime, native_id, path, cwd, stat.st_mtime, stat.st_ino,
            started_at, self.parser_version
        )
        self._meta_cache[path] = (stat.st_ino, stat.st_size, stat.st_mtime_ns, candidate)
        return candidate

    def _parse(self, record: Dict[str, Any], line_offset: int, mtime: float) -> List[RuntimeEvent]:
        record_type = str(record.get("type") or "")
        when = _timestamp(record.get("timestamp"), mtime)
        base_id = str(record.get("uuid") or record.get("id") or "offset:%d" % line_offset)
        events: List[RuntimeEvent] = []
        # Claude's progress, queue, file-history, and tool-result records are not
        # display conversation and intentionally never enter vmux storage.
        if record_type in ("progress", "file-history-snapshot", "queue-operation", "summary"):
            return events
        if record_type == "result":
            state = "error" if record.get("is_error") else "idle"
            return [RuntimeEvent(base_id, "lifecycle", when, {"state": state})]
        if record_type not in ("user", "assistant"):
            return events
        if record.get("isMeta") or record.get("isSidechain") or record.get("isCompactSummary"):
            return events
        if record.get("metadata") and isinstance(record.get("metadata"), dict):
            metadata = record["metadata"]
            if metadata.get("isMeta") or metadata.get("isSidechain") or metadata.get("system"):
                return events
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = message.get("content")
        if record_type == "user":
            # Tool results may contain commands, files, or full outputs.  Accept
            # only the runtime's plain visible user-message string/text blocks.
            text = _content_text(content, input_types=("text", "input_text"))
            if text:
                events.append(RuntimeEvent(base_id, "user_message", when, {"content": text}))
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id"):
                        events.append(RuntimeEvent(base_id + ":resolved", "decision_resolved", when, {
                            "native_event_id": str(block.get("tool_use_id")),
                        }))
            return events

        text = _content_text(content, input_types=("text", "output_text"))
        if text:
            events.append(RuntimeEvent(base_id, "assistant_message", when, {"content": text}))
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                tool_id = str(block.get("id") or base_id)
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                if name == "TodoWrite":
                    items = arguments.get("todos")
                    if isinstance(items, list):
                        events.append(RuntimeEvent(tool_id, "plan", when, {"items": items}))
                elif name in ("TaskCreate", "TaskUpdate"):
                    title = arguments.get("subject") or arguments.get("title") or arguments.get("description")
                    task_id = arguments.get("taskId") or arguments.get("id") or tool_id
                    events.append(RuntimeEvent(tool_id, "task_update", when, {
                        "id": str(task_id),
                        "title": bounded_text(title, 500),
                        "status": str(arguments.get("status") or "in_progress"),
                    }))
                elif name in ("AskUserQuestion", "ExitPlanMode"):
                    questions = arguments.get("questions") if isinstance(arguments.get("questions"), list) else []
                    count = len(questions) or 1
                    for index in range(count):
                        decision = _decision_payload(name, arguments, index)
                        if decision:
                            event_id = tool_id if count == 1 else "%s:%d" % (tool_id, index)
                            events.append(RuntimeEvent(event_id, "decision", when, decision))
        return events


def built_in_observers(codex_home: str, claude_home: str):
    return [CodexObserver(codex_home), ClaudeObserver(claude_home)]

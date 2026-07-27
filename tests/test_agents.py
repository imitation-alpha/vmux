"""Backend contracts and safety invariants for structured Agent Context."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux.agents.models import PaneObservation, RuntimeEvent, empty_context, project_events
from vmux.agents.observers import ClaudeObserver, CodexObserver
from vmux.agents.service import AgentConflict, AgentService
from vmux.config import Config
from vmux.server import create_app


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def _append_jsonl(path, records):
    with path.open("a") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _obs(tmp_path, *, runtime="codex", status="idle", question=None, menu=(), created=None):
    return PaneObservation(
        pane_id="%7", target="work:1.0", command=runtime, title="agent",
        cwd=str(tmp_path / "project"), pid="123", pane_created=created or time.time() - 1,
        runtime=runtime, status=status, question=question, menu=tuple(menu),
        prompt_fingerprint="prompt-hash", observed_at=time.time(),
    )


def _cfg(tmp_path):
    return Config(
        agent_store_path=str(tmp_path / "state" / "agents.sqlite3"),
        agent_codex_home=str(tmp_path / "codex"),
        agent_claude_home=str(tmp_path / "claude"),
    )


def _codex_meta(session_id, cwd, timestamp):
    return {
        "timestamp": timestamp, "type": "session_meta",
        "payload": {"id": session_id, "cwd": str(cwd)},
    }


NODE_CODEX_QUESTIONNAIRE = """\
  Question 1/1 (1 unanswered)
  Which API should we use?

  › 1. REST (Recommended)  Simple
    2. gRPC                Typed

  tab to add notes | enter to submit answer | esc to interrupt
"""


def test_codex_parser_keeps_visible_records_and_discards_reasoning_and_tool_output(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    log = tmp_path / "codex" / "sessions" / "2026" / "07" / "rollout-session-1.jsonl"
    now = time.time()
    _write_jsonl(log, [
        _codex_meta("session-1", cwd, now),
        {"timestamp": now, "type": "event_msg",
         "payload": {"type": "user_message", "message": "Build the API"}},
        {"timestamp": now, "type": "response_item",
         "payload": {"type": "reasoning", "summary": "HIDDEN SECRET PLAN"}},
        {"timestamp": now, "type": "response_item",
         "payload": {"type": "function_call", "name": "shell", "arguments": "PRIVATE_COMMAND"}},
        {"timestamp": now, "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "not-a-decision",
                     "output": "PRIVATE TOOL OUTPUT"}},
        {"timestamp": now, "type": "event_msg",
         "payload": {"type": "agent_message", "message": "The API is ready."}},
    ])
    observer = CodexObserver(str(tmp_path / "codex"))
    candidates = observer.discover(_obs(tmp_path, created=now - 1))
    assert len(candidates) == 1 and candidates[0].native_session_id == "session-1"
    result = observer.read(candidates[0], 0, None)
    encoded = json.dumps([event.payload for event in result.events])
    assert "Build the API" in encoded and "The API is ready" in encoded
    assert "HIDDEN" not in encoded and "PRIVATE_COMMAND" not in encoded
    assert "PRIVATE TOOL OUTPUT" not in encoded


def test_jsonl_reader_waits_for_a_complete_line(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    log = tmp_path / "codex" / "sessions" / "rollout-session-2.jsonl"
    now = time.time()
    _write_jsonl(log, [_codex_meta("session-2", cwd, now)])
    with log.open("ab") as fh:
        fh.write(b'{"type":"event_msg","payload":{"type":"user_message"')
    observer = CodexObserver(str(tmp_path / "codex"))
    candidate = observer.discover(_obs(tmp_path, created=now - 1))[0]
    result = observer.read(candidate, 0, None)
    assert result.offset < log.stat().st_size
    assert not any(event.kind == "user_message" for event in result.events)


def test_claude_observer_excludes_nested_subagent_logs_and_parses_tasks(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    root = tmp_path / "claude" / "projects" / "encoded-project"
    now = time.time()
    top = root / "main-session.jsonl"
    nested = root / "main-session" / "subagents" / "agent-1.jsonl"
    records = [
        {"type": "user", "sessionId": "main-session", "cwd": str(cwd),
         "timestamp": now, "message": {"content": "Implement it"}},
        {"type": "assistant", "sessionId": "main-session", "cwd": str(cwd),
         "timestamp": now, "message": {"content": [
             {"type": "tool_use", "id": "task-evt", "name": "TaskCreate",
              "input": {"subject": "Backend", "status": "in_progress"}},
             {"type": "text", "text": "Starting backend."},
         ]}},
    ]
    _write_jsonl(top, records)
    _write_jsonl(nested, records)
    observer = ClaudeObserver(str(tmp_path / "claude"))
    candidates = observer.discover(_obs(tmp_path, runtime="claude", created=now - 1))
    assert [candidate.path for candidate in candidates] == [str(top)]
    result = observer.read(candidates[0], 0, None)
    assert any(event.kind == "task_update" for event in result.events)


def test_projector_does_not_treat_assistant_commentary_as_idle_and_tracks_blockers():
    context = empty_context("agent", "codex")
    working_events = [
        RuntimeEvent("start", "lifecycle", 1, {"state": "working"}),
        RuntimeEvent("comment", "assistant_message", 2, {"content": "Still running tests."}),
        RuntimeEvent("plan", "plan", 3, {"items": [
            {"id": "a", "step": "API", "status": "completed"},
            {"id": "b", "step": "Waiting for schema", "status": "blocked"},
        ]}),
    ]
    projected, _, _, _ = project_events(context, working_events)
    assert projected["lifecycle"] == "working"
    assert projected["blockers"] == [{
        "id": "b", "title": "Waiting for schema", "created_at": 3,
        "source": "runtime_plan",
    }]
    completed, _, _, _ = project_events(projected, [
        RuntimeEvent("plan2", "plan", 4, {"items": [
            {"id": "b", "step": "Waiting for schema", "status": "completed"},
        ]}),
        RuntimeEvent("done", "lifecycle", 5, {"state": "idle"}),
    ])
    assert completed["lifecycle"] == "idle" and completed["blockers"] == []


def test_store_is_private_and_resume_visit_is_shared_and_monotonic(tmp_path):
    service = AgentService(_cfg(tmp_path))
    service.store.open()
    mode = stat.S_IMODE(os.stat(service.store.path).st_mode)
    assert mode == 0o600
    agent = service.store.upsert_session("codex", "native", "/tmp/log", "/tmp/project", "v1")
    context = dict(agent["context"])
    context.update({"goal": "Ship API", "current_task": "Tests", "last_updated": 2})
    first, _, _ = service.store.apply_projection(agent["id"], context, [], [], [])
    context = dict(service.store.get_agent(agent["id"])["context"])
    context.update({"next_action": "Review", "last_updated": 3})
    second, _, _ = service.store.apply_projection(agent["id"], context, [], [], [])
    resume = service.store.resume(agent["id"])
    assert resume["as_of_snapshot_id"] == second["id"]
    service.store.visit(agent["id"], second["id"])
    service.store.visit(agent["id"], first["id"])
    assert service.store.resume(agent["id"])["baseline_snapshot_id"] == second["id"]


def test_false_neighbor_log_is_probable_read_only_until_manual_binding(tmp_path):
    cfg = _cfg(tmp_path)
    cwd = tmp_path / "project"
    cwd.mkdir()
    pane_created = time.time()
    log = tmp_path / "codex" / "sessions" / "rollout-old.jsonl"
    _write_jsonl(log, [_codex_meta("old-session", cwd, pane_created - 120)])
    os.utime(log, (time.time(), time.time()))  # recently written does not imply same incarnation
    service = AgentService(cfg)
    obs = _obs(tmp_path, created=pane_created)
    asyncio.run(service.process_now([obs]))
    agent = service.list_agents()[0][0]
    assert agent["association"] == "probable"
    assert agent["capabilities"]["chat_send"] == "unavailable"
    bound = service.bind(agent["id"], obs.pane_id, agent["binding_revision"])
    assert bound["association"] == "confirmed"


@pytest.mark.parametrize(
    ("live_command", "live_capture"),
    (("codex", "live prompt"), ("node", NODE_CODEX_QUESTIONNAIRE)),
)
def test_verified_decision_and_guarded_reply_require_all_revisions_and_fingerprint(
    tmp_path, monkeypatch, live_command, live_capture
):
    cfg = _cfg(tmp_path)
    cwd = tmp_path / "project"
    cwd.mkdir()
    now = time.time()
    log = tmp_path / "codex" / "sessions" / "rollout-live.jsonl"
    _write_jsonl(log, [
        _codex_meta("live", cwd, now),
        {"timestamp": now, "type": "response_item", "payload": {
            "type": "function_call", "name": "request_user_input", "call_id": "call-1",
            "arguments": json.dumps({"questions": [{
                "header": "API", "question": "Which API should we use?",
                "options": [
                    {"id": "rest", "label": "REST (Recommended)", "description": "Simple"},
                    {"id": "grpc", "label": "gRPC", "description": "Typed"},
                ],
            }]})}},
    ])
    obs = _obs(
        tmp_path, created=now - 1, status="needs_input",
        question="Which API should we use?",
        menu=(
            {"key": "1", "label": "REST (Recommended)", "selected": True},
            {"key": "2", "label": "gRPC", "selected": False},
        ),
    )
    service = AgentService(cfg)
    asyncio.run(service.process_now([obs]))
    agent = service.list_agents()[0][0]
    decisions, _ = service.list_decisions()
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["recommendation"] == "rest"
    assert decision["prompt_fingerprint"] == "prompt-hash"
    sent = []
    monkeypatch.setattr("vmux.agents.service.tmux.list_panes", lambda: [{
        "id": "%7", "pid": "123", "created": str(obs.pane_created),
        "cmd": live_command, "title": "Codex", "path": str(cwd),
    }])
    monkeypatch.setattr("vmux.agents.service.tmux.capture", lambda pane, scrollback=0: live_capture)
    monkeypatch.setattr("vmux.agents.service.fingerprint_terminal", lambda text: "prompt-hash")
    service.controller.reply = lambda pane, key, runtime: sent.append((pane, key, runtime))
    with pytest.raises(AgentConflict):
        service.reply_decision(
            decision["id"], "rest", "reply-1", decision["revision"],
            agent["binding_revision"], "wrong-hash",
        )
    updated = service.reply_decision(
        decision["id"], "rest", "reply-1", decision["revision"],
        agent["binding_revision"], decision["prompt_fingerprint"],
    )
    assert updated["status"] == "submitting" and sent == [("%7", "1", "codex")]
    # Idempotency returns the same resource without another terminal write.
    service.reply_decision(
        decision["id"], "rest", "reply-1", updated["revision"],
        agent["binding_revision"], decision["prompt_fingerprint"],
    )
    assert sent == [("%7", "1", "codex")]
    _append_jsonl(log, [{
        "timestamp": now + 1, "type": "response_item",
        "payload": {"type": "function_call_output", "call_id": "call-1", "output": "ignored"},
    }])
    asyncio.run(service.process_now([_obs(tmp_path, created=now - 1, status="idle")]))
    resolved = service.get_decision(decision["id"])
    assert resolved["status"] == "resolved"
    assert service.get_agent(agent["id"])["pending_decisions_count"] == 0
    assert any(
        event["event"].get("decision_id") == decision["id"]
        and event["event"]["kind"] == "decision_updated"
        for event in service._history
    )


def test_agent_api_contract_and_capability_advertisement(tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    cfg = _cfg(tmp_path)
    cfg.token = "secret"
    cfg.server_instance_id = "server-test"
    app = create_app(cfg)
    store = app.state.hub.agents.store
    agent = store.upsert_session("codex", "api-native", "/tmp/log", "/tmp/project", "v1")
    context = dict(agent["context"])
    context.update({"goal": "API contract", "last_updated": time.time()})
    store.apply_projection(agent["id"], context, [], [], [])
    client = TestClient(app)
    auth = {"Authorization": "Bearer secret"}
    config = client.get("/api/config", headers=auth).json()
    feature = config["_info"]["capabilities"]["agent_context_v1"]
    assert feature["enabled"] is True and feature["websocket"] is True
    assert config["_info"]["server_instance_id"] == "server-test"
    listing = client.get("/api/agents", headers=auth).json()
    assert set(listing) == {"agents", "next_cursor"}
    detail = client.get("/api/agents/" + agent["id"], headers=auth).json()
    assert detail["id"] == agent["id"] and "agent" not in detail
    timeline = client.get("/api/timeline", headers=auth).json()
    assert set(timeline) == {"events", "next_cursor"}
    assert {"type", "title", "occurred_at"} <= set(timeline["events"][0])

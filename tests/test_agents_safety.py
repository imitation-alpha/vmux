"""Adversarial/race/privacy tests for Agent Context backend controls."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux.agents.models import PaneObservation
from vmux.agents.observers import MAX_READ_RECORDS, ClaudeObserver, CodexObserver
from vmux.agents.service import AgentConflict, AgentService
from vmux.agents.store import AgentStore
from vmux.config import Config
from vmux.poller import Hub
from vmux.push import DeviceRegistry, PushManager


def config(tmp_path):
    return Config(
        agent_store_path=str(tmp_path / "state" / "agents.sqlite3"),
        agent_codex_home=str(tmp_path / "codex"),
        agent_claude_home=str(tmp_path / "claude"),
    )


def observation(tmp_path, pane_id, created, *, runtime="codex", status="idle",
                question=None, menu=()):
    return PaneObservation(
        pane_id=pane_id, target="work:1.%s" % pane_id.lstrip("%"), command=runtime,
        title="Agent " + pane_id, cwd=str(tmp_path / "project"), pid=pane_id.lstrip("%"),
        pane_created=created, runtime=runtime, status=status, question=question,
        menu=tuple(menu), prompt_fingerprint="hash-" + pane_id, observed_at=time.time(),
    )


def write_log(path, session_id, cwd, started, records=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    values = [{
        "timestamp": started, "type": "session_meta",
        "payload": {"id": session_id, "cwd": str(cwd)},
    }, *records]
    path.write_text("".join(json.dumps(value) + "\n" for value in values))


def test_two_panes_two_sessions_are_all_ambiguous_with_binding_candidates(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    now = time.time()
    write_log(tmp_path / "codex" / "sessions" / "one.jsonl", "session-one", cwd, now)
    write_log(tmp_path / "codex" / "sessions" / "two.jsonl", "session-two", cwd, now + 1)
    service = AgentService(config(tmp_path))
    panes = [observation(tmp_path, "%1", now), observation(tmp_path, "%2", now + 1)]
    asyncio.run(service.process_now(panes))
    agents, _ = service.list_agents()
    assert {agent["native_session_id"] for agent in agents} == {"session-one", "session-two"}
    assert {agent["association"] for agent in agents} == {"ambiguous"}
    for agent in agents:
        assert {candidate["id"] for candidate in agent["binding_candidates"]} == {"%1", "%2"}
        assert all(isinstance(candidate["confidence"], float) for candidate in agent["binding_candidates"])


def test_missing_pane_fails_closed_and_snapshots_offline(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    now = time.time()
    write_log(tmp_path / "codex" / "sessions" / "live.jsonl", "live", cwd, now)
    service = AgentService(config(tmp_path))
    asyncio.run(service.process_now([observation(tmp_path, "%1", now)]))
    before = service.list_agents()[0][0]
    assert before["association"] == "confirmed"
    asyncio.run(service.process_now([]))
    after = service.get_agent(before["id"])
    assert after["association"] == "unavailable" and after["pane_id"] is None
    assert after["context"]["lifecycle"] == "offline"
    assert after["capabilities"]["chat_send"] == "unavailable"


def test_hostile_terminal_menu_key_never_verifies(tmp_path):
    service = AgentService(config(tmp_path))
    obs = observation(
        tmp_path, "%1", time.time(), status="needs_input", question="Proceed?",
        menu=({"key": "--help", "label": "Yes"}, {"key": "n", "label": "No"}),
    )
    decision = {
        "title": "Proceed?", "description": "Proceed?",
        "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
    }
    assert service._match_decision(decision, obs) is None


def test_similar_or_extra_terminal_question_cannot_match_decision(tmp_path):
    service = AgentService(config(tmp_path))
    decision = {
        "title": "Database", "description": "Delete staging database?",
        "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
    }
    production = observation(
        tmp_path, "%1", time.time(), status="needs_input",
        question="Delete production database?",
        menu=({"key": "y", "label": "Yes"}, {"key": "n", "label": "No"}),
    )
    assert service._match_decision(decision, production) is None
    extra = observation(
        tmp_path, "%1", time.time(), status="needs_input",
        question="Delete staging database?",
        menu=({"key": "y", "label": "Yes"}, {"key": "n", "label": "No"},
              {"key": "3", "label": "Always"}),
    )
    assert service._match_decision(decision, extra) is None
    negated = observation(
        tmp_path, "%1", time.time(), status="needs_input",
        question="Do not delete staging database?",
        menu=({"key": "y", "label": "Yes"}, {"key": "n", "label": "No"}),
    )
    assert service._match_decision(decision, negated) is None


def test_claude_meta_and_sidechain_user_records_are_not_visible(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    root = tmp_path / "claude" / "projects" / "encoded"
    log = root / "session.jsonl"
    root.mkdir(parents=True)
    now = time.time()
    values = [
        {"type": "user", "sessionId": "session", "cwd": str(cwd), "timestamp": now,
         "isMeta": True, "message": {"content": "PRIVATE META PROMPT"}},
        {"type": "user", "sessionId": "session", "cwd": str(cwd), "timestamp": now + 1,
         "isSidechain": True, "message": {"content": "PRIVATE SIDECHAIN"}},
        {"type": "user", "sessionId": "session", "cwd": str(cwd), "timestamp": now + 2,
         "message": {"content": "Visible request"}},
    ]
    log.write_text("".join(json.dumps(value) + "\n" for value in values))
    observer = ClaudeObserver(str(tmp_path / "claude"))
    candidate = observer.discover(observation(tmp_path, "%1", now, runtime="claude"))[0]
    result = observer.read(candidate, 0, None)
    encoded = json.dumps([event.payload for event in result.events])
    assert "Visible request" in encoded
    assert "PRIVATE META" not in encoded and "PRIVATE SIDECHAIN" not in encoded


def test_reader_is_incremental_and_discards_oversized_line(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    now = time.time()
    log = tmp_path / "codex" / "sessions" / "large.jsonl"
    records = [{
        "timestamp": now + i / 1000, "type": "event_msg",
        "payload": {"type": "user_message", "message": "m%d" % i},
    } for i in range(MAX_READ_RECORDS + 10)]
    write_log(log, "large", cwd, now, records)
    observer = CodexObserver(str(tmp_path / "codex"))
    candidate = observer.discover(observation(tmp_path, "%1", now))[0]
    first = observer.read(candidate, 0, None)
    assert first.offset < log.stat().st_size
    second = observer.read(candidate, first.offset, first.inode)
    assert second.offset == log.stat().st_size and second.events

    huge = tmp_path / "codex" / "sessions" / "huge.jsonl"
    write_log(huge, "huge", cwd, now)
    with huge.open("a") as fh:
        fh.write(json.dumps({"type": "event_msg", "payload": {
            "type": "user_message", "message": "x" * (1024 * 1024 + 20),
        }}) + "\n")
    huge_candidate = next(c for c in observer.discover(observation(tmp_path, "%1", now))
                          if c.native_session_id == "huge")
    result = observer.read(huge_candidate, 0, None)
    assert result.error == "oversized_line_discarded"
    offsets = [result.offset]
    while result.offset < huge.stat().st_size:
        result = observer.read(huge_candidate, result.offset, result.inode)
        offsets.append(result.offset)
    assert offsets == sorted(set(offsets)) and len(offsets) <= 3
    assert not any(event.kind == "user_message" for event in result.events)

    no_newline = tmp_path / "codex" / "sessions" / "no-newline.jsonl"
    write_log(no_newline, "no-newline", cwd, now)
    with no_newline.open("ab") as fh:
        fh.write(b"x" * (3 * 1024 * 1024))
    candidate = next(c for c in observer.discover(observation(tmp_path, "%1", now))
                     if c.native_session_id == "no-newline")
    offsets = []
    offset = 0
    for _ in range(3):
        chunk = observer.read(candidate, offset, None)
        offsets.append(chunk.offset)
        offset = chunk.offset
    assert offsets[0] > 0 and offsets == sorted(set(offsets))


def test_metadata_cache_invalidates_same_size_rewrite(tmp_path):
    first_cwd = tmp_path / "project-a"
    second_cwd = tmp_path / "project-b"
    first_cwd.mkdir()
    second_cwd.mkdir()
    log = tmp_path / "codex" / "sessions" / "rewrite.jsonl"
    now = time.time()
    write_log(log, "session-a", first_cwd, now)
    observer = CodexObserver(str(tmp_path / "codex"))
    first = observer.discover(PaneObservation(
        pane_id="%1", target="w:1.1", command="codex", title="", cwd=str(first_cwd),
        pid="1", pane_created=now, runtime="codex", status="idle", question=None,
        menu=(), prompt_fingerprint="x",
    ))
    assert first[0].native_session_id == "session-a"
    original_size = log.stat().st_size
    write_log(log, "session-b", second_cwd, now)
    assert log.stat().st_size == original_size
    future_ns = log.stat().st_mtime_ns + 1_000_000_000
    os.utime(log, ns=(future_ns, future_ns))
    second = observer.discover(PaneObservation(
        pane_id="%2", target="w:1.2", command="codex", title="", cwd=str(second_cwd),
        pid="2", pane_created=now, runtime="codex", status="idle", question=None,
        menu=(), prompt_fingerprint="y",
    ))
    assert second[0].native_session_id == "session-b"


def test_old_messages_are_not_exposed_on_first_import(tmp_path):
    service = AgentService(config(tmp_path))
    service.store.open()
    agent = service.store.upsert_session("codex", "old", "/tmp/log", "/tmp/project", "v1")
    old = time.time() - 40 * 86400
    service.store.apply_projection(agent["id"], agent["context"], [{
        "native_event_id": "old-message", "role": "user", "content": "expired",
        "created_at": old, "status": "observed",
    }], [], [])
    assert service.list_messages(agent["id"])[0] == []


def test_chat_idempotency_lookup_is_not_limited_to_latest_page(tmp_path):
    service = AgentService(config(tmp_path))
    store = service.store
    agent = store.upsert_session("codex", "messages", "/tmp/log", "/tmp/project", "v1")
    first = store.reserve_sent_message(agent["id"], "first", "client-first")
    store.set_message_status(first["id"], "sent")
    for index in range(205):
        row = store.reserve_sent_message(agent["id"], "m%d" % index, "client-%d" % index)
        store.set_message_status(row["id"], "sent")
    service.controller.send_message = lambda *_: pytest.fail("idempotent retry wrote to terminal")
    repeated = service.send_message(agent["id"], "first", "client-first", 0)
    assert repeated["id"] == first["id"]


def test_chat_reservation_fails_closed_after_uncertain_terminal_error(tmp_path, monkeypatch):
    cwd = tmp_path / "project"
    cwd.mkdir()
    now = time.time()
    write_log(tmp_path / "codex" / "sessions" / "live.jsonl", "live", cwd, now)
    service = AgentService(config(tmp_path))
    obs = observation(tmp_path, "%1", now)
    asyncio.run(service.process_now([obs]))
    agent = service.list_agents()[0][0]
    monkeypatch.setattr("vmux.agents.service.tmux.list_panes", lambda: [{
        "id": "%1", "pid": "1", "created": str(now),
        "cmd": "codex", "path": str(cwd),
    }])
    monkeypatch.setattr("vmux.agents.service.tmux.capture", lambda *_: "prompt")
    monkeypatch.setattr("vmux.agents.service.fingerprint_terminal", lambda _: "hash-%1")
    calls = []

    def uncertain(*_):
        calls.append(1)
        raise RuntimeError("enter failed after literal write")

    service.controller.send_message = uncertain
    with pytest.raises(AgentConflict):
        service.send_message(agent["id"], "continue", "client-once", agent["binding_revision"])
    retry = service.send_message(agent["id"], "continue", "client-once", agent["binding_revision"])
    assert retry["status"] == "unknown" and calls == [1]


def test_live_validation_rejects_runtime_and_cwd_changes(tmp_path, monkeypatch):
    cwd = tmp_path / "project"
    cwd.mkdir()
    now = time.time()
    write_log(tmp_path / "codex" / "sessions" / "live.jsonl", "live", cwd, now)
    service = AgentService(config(tmp_path))
    obs = observation(tmp_path, "%1", now)
    asyncio.run(service.process_now([obs]))
    agent = service.list_agents()[0][0]
    monkeypatch.setattr("vmux.agents.service.tmux.capture", lambda *_: "prompt")
    monkeypatch.setattr("vmux.agents.service.fingerprint_terminal", lambda _: "hash-%1")
    sent = []
    service.controller.send_message = lambda *args: sent.append(args)
    monkeypatch.setattr("vmux.agents.service.tmux.list_panes", lambda: [{
        "id": "%1", "pid": "1", "created": str(now), "cmd": "zsh", "path": str(cwd),
    }])
    with pytest.raises(AgentConflict, match="runtime"):
        service.send_message(agent["id"], "x", "wrong-runtime", agent["binding_revision"])
    monkeypatch.setattr("vmux.agents.service.tmux.list_panes", lambda: [{
        "id": "%1", "pid": "1", "created": str(now), "cmd": "codex", "path": str(tmp_path),
    }])
    with pytest.raises(AgentConflict, match="directory"):
        service.send_message(agent["id"], "x", "wrong-cwd", agent["binding_revision"])
    assert sent == []


def test_new_session_in_same_pane_replaces_old_manual_binding(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    now = time.time()
    old_log = tmp_path / "codex" / "sessions" / "old.jsonl"
    write_log(old_log, "old", cwd, now)
    service = AgentService(config(tmp_path))
    obs = observation(tmp_path, "%1", now)
    asyncio.run(service.process_now([obs]))
    old = service.list_agents()[0][0]
    # Make the original binding explicit, then start a newer native session in
    # the same pane incarnation.
    old = service.bind(old["id"], "%1", old["binding_revision"])
    write_log(tmp_path / "codex" / "sessions" / "new.jsonl", "new", cwd, now + 10)
    asyncio.run(service.process_now([obs]))
    agents = {agent["native_session_id"]: agent for agent in service.list_agents()[0]}
    assert agents["new"]["association"] == "confirmed" and agents["new"]["pane_id"] == "%1"
    assert agents["old"]["association"] == "unavailable" and agents["old"]["pane_id"] is None
    assert agents["old"]["context"]["lifecycle"] == "offline"


def test_new_session_in_long_lived_pane_surfaces_probable_and_invalidates_old(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    pane_started = time.time() - 1000
    old_log = tmp_path / "codex" / "sessions" / "old.jsonl"
    write_log(old_log, "old", cwd, pane_started + 10)
    service = AgentService(config(tmp_path))
    obs = observation(tmp_path, "%1", pane_started)
    asyncio.run(service.process_now([obs]))
    old = service.list_agents()[0][0]
    old = service.bind(old["id"], "%1", old["binding_revision"])
    write_log(tmp_path / "codex" / "sessions" / "new.jsonl", "new", cwd, time.time())
    asyncio.run(service.process_now([obs]))
    agents = {agent["native_session_id"]: agent for agent in service.list_agents()[0]}
    assert agents["new"]["association"] == "probable"
    assert agents["new"]["binding_candidates"][0]["pane_id"] == "%1"
    assert agents["old"]["association"] == "unavailable"


def test_delete_racing_ingest_cannot_reinsert_deleted_messages(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    now = time.time()
    log = tmp_path / "codex" / "sessions" / "race.jsonl"
    write_log(log, "race", cwd, now)
    service = AgentService(config(tmp_path))
    obs = observation(tmp_path, "%1", now)
    asyncio.run(service.process_now([obs]))
    agent = service.list_agents()[0][0]
    with log.open("a") as fh:
        fh.write(json.dumps({
            "timestamp": now + 1, "type": "event_msg",
            "payload": {"type": "user_message", "message": "must stay deleted"},
        }) + "\n")
    observer = next(item for item in service.observers if item.runtime == "codex")
    original = observer.read
    entered = threading.Event()
    release = threading.Event()

    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        release.wait(2)
        return result

    observer.read = paused
    thread = threading.Thread(target=service._process_sync, args=([obs],))
    thread.start()
    assert entered.wait(1)
    service.delete_history(agent["id"])
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert service.list_messages(agent["id"])[0] == []


def test_resume_is_net_delta_and_has_top_level_contract_fields(tmp_path):
    service = AgentService(config(tmp_path))
    store = service.store
    agent = store.upsert_session("codex", "resume", "/tmp/log", "/tmp/project", "v1")
    context = dict(agent["context"])
    context.update({"goal": "A", "current_task": "one", "last_updated": 1})
    first, _, _ = store.apply_projection(agent["id"], context, [], [], [])
    store.visit(agent["id"], first["id"])
    context = dict(store.get_agent(agent["id"])["context"])
    context.update({"goal": "B", "current_task": "two", "last_updated": 2})
    store.apply_projection(agent["id"], context, [], [], [])
    context = dict(store.get_agent(agent["id"])["context"])
    context.update({"goal": "C", "current_task": "three", "last_updated": 3})
    store.apply_projection(agent["id"], context, [], [], [])
    resume = service.resume(agent["id"])
    assert resume["changes"]["goal_changed"] == {"from": "A", "to": "C"}
    assert resume["goal"] == "C" and resume["current_task"] == "three"
    assert {"progress", "pending_decisions", "next_action", "estimated_completion"} <= set(resume)


def test_agent_startup_failure_does_not_stop_primary_hub_loop(tmp_path):
    async def scenario():
        hub = Hub(config(tmp_path))
        called = []

        async def fail():
            raise OSError("database unavailable")

        async def poll():
            called.append("poll")
            hub.stop()

        hub.agents.start = fail
        hub.poll_once = poll
        await hub.run()
        assert called == ["poll"]
        assert hub.agents.enabled is False

    asyncio.run(scenario())


def test_agent_decision_push_is_generic_for_every_registry_mode(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.server_instance_id = "server-id"
    cfg.push_store_path = str(tmp_path / "private" / "push.json")
    manager = PushManager(cfg)
    decision = {
        "id": "decision", "agent_id": "agent", "revision": 2,
        "title": "NEVER SEND THIS TITLE",
        "description": "NEVER SEND THIS DESCRIPTION /Users/alice/project/file.py",
        "prompt": "NEVER SEND THIS PROMPT",
        "options": [{"id": "private-option", "label": "Never send this"}],
    }
    generic, contextual, sensitive = manager._agent_decision_payloads(decision)
    assert sensitive is False
    assert contextual == generic
    assert generic["aps"]["alert"] == {
        "title": "vmux",
        "body": "An agent needs your decision.",
    }
    assert contextual["vmux"]["type"] == "decision"
    assert contextual["vmux"]["server_instance_id"] == "server-id"

    generic_secret, contextual_secret, secret = manager._agent_decision_payloads(
        dict(decision, description="NEVER SEND THIS SECRET password")
    )
    assert secret is True
    assert contextual_secret == generic_secret

    opted_in = "ab" * 32
    opted_out = "cd" * 32
    manager.registry.add(opted_in, contextual=True)
    manager.registry.add(opted_out, contextual=False)
    monkeypatch.setattr(PushManager, "can_send", property(lambda self: True))
    sent = []

    async def capture(pairs):
        sent.extend(pairs)

    async def scenario():
        manager._send_token_payloads = capture
        manager.fire_agent_decision(decision)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert {token for token, _ in sent} == {opted_in, opted_out}
    assert all(payload == generic for _, payload in sent)
    encoded = json.dumps([payload for _, payload in sent])
    for private_copy in (
        "NEVER SEND THIS TITLE",
        "NEVER SEND THIS DESCRIPTION",
        "NEVER SEND THIS PROMPT",
        "/Users/alice",
        "private-option",
        "Never send this",
    ):
        assert private_copy not in encoded

    registry = DeviceRegistry(cfg.push_store_path)
    registrations = {item["token"]: item for item in registry.registrations()}
    assert registrations[opted_in]["contextual"] is True
    assert registrations[opted_out]["contextual"] is False
    assert (os.stat(cfg.push_store_path).st_mode & 0o777) == 0o600
    assert (os.stat(os.path.dirname(cfg.push_store_path)).st_mode & 0o777) == 0o700


def test_worker_shutdown_waits_for_inflight_extraction_and_publish_cross_thread(tmp_path):
    async def scenario():
        service = AgentService(config(tmp_path))
        await service.start()
        queue = service.subscribe()
        original = service._process_sync

        def slow(_):
            time.sleep(0.06)

        service._process_sync = slow
        service.submit([])
        await asyncio.sleep(0.01)
        started = time.monotonic()
        await service.aclose()
        assert time.monotonic() - started >= 0.04
        service._process_sync = original

        # A fresh active-loop service wakes subscribers safely from a worker.
        service = AgentService(config(tmp_path / "second"))
        await service.start()
        queue = service.subscribe()
        await asyncio.to_thread(service.publish, "agent_updated", "a", 1)
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event["cursor"] == 1 and event["event"]["agent_id"] == "a"
        await service.aclose()

    asyncio.run(scenario())


def test_large_websocket_replay_resets_instead_of_overflowing_queue(tmp_path):
    service = AgentService(config(tmp_path))
    for revision in range(150):
        service.publish("agent_updated", "agent", revision)
    queue = service.subscribe(after=0)
    assert queue.qsize() == 1
    frame = queue.get_nowait()
    assert frame == {"type": "reset", "cursor": 150, "reason": "cursor_expired"}


@pytest.mark.parametrize(
    ("command", "capture"),
    (
        ("codex", "Codex prompt"),
        (
            "node",
            "  Question 1/1 (1 unanswered)\n"
            "  Which API should we use?\n\n"
            "  › 1. REST  Simple\n"
            "    2. gRPC  Typed\n\n"
            "  enter to submit answer | esc to interrupt\n",
        ),
    ),
)
def test_agent_observation_ignores_pane_workspace_filter(
    tmp_path, monkeypatch, command, capture
):
    cfg = config(tmp_path)
    cfg.auto_discover = False
    hub = Hub(cfg)
    pane = {
        "id": "%3", "target": "work:1.3", "cmd": command, "title": "Codex",
        "window": "work", "path": str(tmp_path / "project"), "pid": "333",
        "window_id": "@1", "created": str(time.time()),
    }
    monkeypatch.setattr("vmux.poller.tmux.list_panes", lambda: [pane])
    monkeypatch.setattr("vmux.poller.tmux.capture", lambda *_: capture)
    observed = []
    hub.agents.submit = lambda values: observed.extend(values)
    asyncio.run(hub.poll_once())
    assert hub.states == {} and hub.order == []
    assert len(observed) == 1
    assert observed[0].pane_id == "%3" and observed[0].runtime == "codex"


def test_private_files_do_not_chmod_existing_shared_parent(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    store = AgentStore(str(shared / "agents.sqlite3"))
    store.open()
    assert (os.stat(shared).st_mode & 0o777) == 0o755
    assert (os.stat(store.path).st_mode & 0o777) == 0o600
    store.close()

    registry = DeviceRegistry(str(shared / "push.json"))
    registry.add("cd" * 32)
    assert (os.stat(shared).st_mode & 0o777) == 0o755
    assert (os.stat(shared / "push.json").st_mode & 0o777) == 0o600

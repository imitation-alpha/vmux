"""Recovery-v1 consistency, traversal, retention, and privacy contracts."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from vmux.agents.store import MAX_RECOVERY_ITEMS, AgentStore


def _agent(store: AgentStore, native: str = "native"):
    return store.upsert_session(
        "codex",
        native,
        "/private/source/SECRET-log.jsonl",
        "/private/worktree/SECRET-cwd",
        "v1",
    )


def _project(store: AgentStore, agent_id: str, index: int, *, message_time: float):
    context = dict(store.get_agent(agent_id)["context"])
    context.update(
        {
            "goal": "Ship recovery",
            "current_task": "step-%d" % index,
            "next_action": "next-%d" % index,
            "last_updated": message_time,
        }
    )
    return store.apply_projection(
        agent_id,
        context,
        [
            {
                "native_event_id": "visible-%d" % index,
                "role": "user" if index % 2 else "assistant",
                "content": "visible message %d" % index,
                "status": "observed",
                "created_at": message_time,
            }
        ],
        [],
        [],
    )[0]


def _all_older_pages(store: AgentStore, agent_id: str, first: dict, limit: int):
    pages = [first["entries"]]
    cursor = first["older_cursor"]
    while cursor:
        page = store.recovery(
            agent_id, activity_limit=limit, activity_cursor=cursor
        )["recent_activity"]
        pages.append(page["entries"])
        cursor = page["older_cursor"]
    # The default page is newest; each individual page is chronological.
    return [entry for page in reversed(pages) for entry in page]


def test_recovery_activity_is_bounded_typed_and_deterministic_across_ties(
    tmp_path, monkeypatch
):
    fixed = time.time()
    monkeypatch.setattr("vmux.agents.store.time.time", lambda: fixed)
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = _agent(store)
    for index in range(1, 7):
        _project(store, agent["id"], index, message_time=fixed)
    # A duplicate/out-of-order source event remains one retained resource.
    context = dict(store.get_agent(agent["id"])["context"])
    store.apply_projection(
        agent["id"],
        context,
        [{
            "native_event_id": "visible-1",
            "role": "user",
            "content": "duplicate must not cross recovery",
            "status": "observed",
            "created_at": fixed - 100,
        }],
        [],
        [],
    )

    response = store.recovery(
        agent["id"], message_limit=999, timeline_limit=999, activity_limit=3
    )
    assert response["consistency"] == "single_read"
    assert response["freshness"]["model_context"] == "runtime_owned_unverified"
    assert len(response["recent_messages"]["messages"]) <= MAX_RECOVERY_ITEMS
    assert len(response["recent_timeline"]["events"]) <= MAX_RECOVERY_ITEMS
    activity = response["recent_activity"]
    assert activity["order"] == "oldest_to_newest"
    assert activity["tie_break"] == "occurred_at_kind_source_order_resource_id"

    pages = [activity]
    while pages[-1]["older_cursor"]:
        pages.append(
            store.recovery(
                agent["id"],
                activity_limit=3,
                activity_cursor=pages[-1]["older_cursor"],
            )["recent_activity"]
        )
    entries = [entry for page in reversed(pages) for entry in page["entries"]]
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids)) == 12
    assert {entry["kind"] for entry in entries} == {
        "visible_message",
        "semantic_event",
    }
    # At equal timestamps, visible messages sort before semantic snapshots;
    # source insertion order then breaks ties within each kind.
    assert [entry["kind"] for entry in entries] == [
        *(["visible_message"] * 6),
        *(["semantic_event"] * 6),
    ]
    assert "duplicate must not cross recovery" not in json.dumps(response)
    # Starting at the oldest page, newer cursors deterministically return to
    # the original newest page without repeating a resource.
    newer_pages = [pages[-1]]
    while newer_pages[-1]["newer_cursor"]:
        newer_pages.append(
            store.recovery(
                agent["id"],
                activity_limit=3,
                activity_cursor=newer_pages[-1]["newer_cursor"],
            )["recent_activity"]
        )
    newer_ids = [entry["id"] for page in newer_pages for entry in page["entries"]]
    assert len(newer_ids) == len(set(newer_ids)) == 12
    assert newer_ids == ids


def test_recovery_long_session_remains_bounded_without_skips(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = _agent(store)
    now = time.time()
    for index in range(75):
        _project(store, agent["id"], index, message_time=now + index / 1000)
    newest = store.recovery(agent["id"], activity_limit=100000)["recent_activity"]
    assert len(newest["entries"]) == MAX_RECOVERY_ITEMS
    entries = _all_older_pages(store, agent["id"], newest, MAX_RECOVERY_ITEMS)
    assert len(entries) == 150
    assert len({entry["id"] for entry in entries}) == 150
    assert [entry["occurred_at"] for entry in entries] == sorted(
        entry["occurred_at"] for entry in entries
    )
    assert len(store.recovery(agent["id"], activity_limit=0)["recent_activity"]["entries"]) == 1


def test_recovery_cursor_anchor_excludes_concurrent_projection_and_survives_restart(
    tmp_path
):
    path = tmp_path / "agents.sqlite3"
    store = AgentStore(str(path))
    agent = _agent(store)
    now = time.time()
    for index in range(1, 5):
        _project(store, agent["id"], index, message_time=now + index)
    first = store.recovery(agent["id"], activity_limit=2)["recent_activity"]
    assert first["older_cursor"]

    new_snapshot = _project(store, agent["id"], 5, message_time=now - 500)
    anchored = _all_older_pages(store, agent["id"], first, 2)
    anchored_ids = {entry["resource_id"] for entry in anchored}
    assert new_snapshot["id"] not in anchored_ids
    assert "visible-5" not in {
        entry["resource"].get("native_event_id") for entry in anchored
    }
    latest_ids = {
        entry["resource_id"]
        for entry in _all_older_pages(
            store,
            agent["id"],
            store.recovery(agent["id"], activity_limit=2)["recent_activity"],
            2,
        )
    }
    assert new_snapshot["id"] in latest_ids

    cursor = first["older_cursor"]
    expected_page_ids = [
        entry["id"]
        for entry in store.recovery(
            agent["id"], activity_limit=2, activity_cursor=cursor
        )["recent_activity"]["entries"]
    ]
    store.close()
    reopened = AgentStore(str(path))
    page = reopened.recovery(
        agent["id"], activity_limit=2, activity_cursor=cursor
    )["recent_activity"]
    assert page["cursor_status"] == "valid"
    assert [entry["id"] for entry in page["entries"]] == expected_page_ids


def test_recovery_single_read_cannot_mix_a_concurrent_projection(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = _agent(store)
    now = time.time()
    before_snapshot = _project(store, agent["id"], 1, message_time=now)
    entered = threading.Event()
    writer_started = threading.Event()
    release = threading.Event()
    original_page = store._recovery_page
    calls = 0

    def paused_page(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert writer_started.wait(1)
            # The writer has attempted projection but cannot acquire the store
            # lock until this complete read transaction releases it.
            assert not release.wait(0.05)
        return original_page(*args, **kwargs)

    store._recovery_page = paused_page
    projected = []

    def writer():
        assert entered.wait(1)
        writer_started.set()
        projected.append(_project(store, agent["id"], 2, message_time=now + 1))
        release.set()

    thread = threading.Thread(target=writer)
    thread.start()
    response = store.recovery(agent["id"])
    thread.join(2)
    assert not thread.is_alive() and projected
    assert response["agent"]["revision"] == 1
    assert response["changes"]["as_of_snapshot_id"] == before_snapshot["id"]
    assert projected[0]["id"] not in json.dumps(response)
    assert store.recovery(agent["id"])["agent"]["revision"] == 2


def test_recovery_get_does_not_advance_any_baseline_or_write_state(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = _agent(store)
    snapshot = _project(store, agent["id"], 1, message_time=time.time())
    store.visit(agent["id"], snapshot["id"])
    store.review(agent["id"], snapshot["id"])
    before = {
        "changes": store.conn.total_changes,
        "visit": dict(store.conn.execute(
            "SELECT * FROM session_visits WHERE session_id=?", (agent["id"],)
        ).fetchone()),
        "review": dict(store.conn.execute(
            "SELECT * FROM session_reviews WHERE session_id=?", (agent["id"],)
        ).fetchone()),
        "context": dict(store.conn.execute(
            "SELECT * FROM agent_contexts WHERE session_id=?", (agent["id"],)
        ).fetchone()),
    }
    response = store.recovery(agent["id"])
    assert response["changes"]["basis"] == "shared_review"
    assert store.conn.total_changes == before["changes"]
    assert dict(store.conn.execute(
        "SELECT * FROM session_visits WHERE session_id=?", (agent["id"],)
    ).fetchone()) == before["visit"]
    assert dict(store.conn.execute(
        "SELECT * FROM session_reviews WHERE session_id=?", (agent["id"],)
    ).fetchone()) == before["review"]
    assert dict(store.conn.execute(
        "SELECT * FROM agent_contexts WHERE session_id=?", (agent["id"],)
    ).fetchone()) == before["context"]


def test_recovery_reports_message_and_semantic_gaps_independently(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"), retention_days=1)
    agent = _agent(store)
    old_secret = "EXPIRED_VISIBLE_SECRET"
    store.apply_projection(
        agent["id"],
        agent["context"],
        [{
            "native_event_id": "expired",
            "role": "assistant",
            "content": old_secret,
            "status": "observed",
            "created_at": time.time() - 2 * 86400,
        }],
        [],
        [],
    )
    first = _project(store, agent["id"], 1, message_time=time.time())
    second = _project(store, agent["id"], 2, message_time=time.time() + 1)
    initial = store.recovery(agent["id"])
    assert initial["recent_messages"]["coverage"]["history_truncated"] is True
    assert initial["recent_timeline"]["coverage"]["history_truncated"] is False
    assert old_secret not in json.dumps(initial)

    with store.transaction() as conn:
        conn.execute("DELETE FROM agent_snapshots WHERE id=?", (first["id"],))
    limited = store.recovery(agent["id"])
    assert limited["recent_timeline"]["coverage"] == {
        "retained_from": pytest.approx(second["created_at"]),
        "retained_to": pytest.approx(second["created_at"]),
        "history_truncated": True,
        "unavailable_reason": "expired_or_deleted",
        "more_retained_older": False,
        "more_retained_newer": False,
    }
    assert limited["recent_messages"]["coverage"]["unavailable_reason"] == "expired_or_deleted"


def test_truncated_available_history_uses_oldest_retained_change_baseline(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = _agent(store)
    now = time.time()
    first = _project(store, agent["id"], 1, message_time=now)
    _project(store, agent["id"], 2, message_time=now + 1)
    _project(store, agent["id"], 3, message_time=now + 2)
    with store.transaction() as conn:
        conn.execute("DELETE FROM agent_snapshots WHERE id=?", (first["id"],))

    changes = store.recovery(agent["id"])["changes"]
    assert changes["basis"] == "available_history"
    assert changes["history_truncated"] is True
    assert changes["unavailable_reason"] == "expired_or_deleted"
    assert "goal_changed" not in changes["delta"]
    assert changes["delta"]["current_task_changed"] == {
        "from": "step-2",
        "to": "step-3",
    }

    with store.transaction() as conn:
        conn.execute("DELETE FROM agent_snapshots WHERE session_id=?", (agent["id"],))
    no_history = store.recovery(agent["id"])["changes"]
    assert no_history["history_truncated"] is True
    assert no_history["delta"] == {}


def test_old_session_age_does_not_claim_visible_message_loss(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"), retention_days=1)
    empty = _agent(store, native="old-empty")
    recent = _agent(store, native="old-recent")
    old_created_at = time.time() - 2 * 86400
    with store.transaction() as conn:
        conn.execute(
            "UPDATE agent_sessions SET created_at=? WHERE id IN (?,?)",
            (old_created_at, empty["id"], recent["id"]),
        )
    _project(store, recent["id"], 1, message_time=time.time())

    for agent_id in (empty["id"], recent["id"]):
        recovery_coverage = store.recovery(agent_id)["recent_messages"]["coverage"]
        assert recovery_coverage["history_truncated"] is False
        assert recovery_coverage["unavailable_reason"] is None
        _, _, message_metadata = store.list_messages(
            agent_id, with_metadata=True
        )
        assert message_metadata["history_truncated"] is False


def test_observer_to_recovery_excludes_hidden_tool_terminal_and_path_data(tmp_path):
    from vmux.agents.models import PaneObservation
    from vmux.agents.service import AgentService
    from vmux.config import Config

    cwd = tmp_path / "PRIVATE_CWD_CANARY"
    cwd.mkdir()
    log = tmp_path / "codex" / "sessions" / "recovery.jsonl"
    log.parent.mkdir(parents=True)
    now = time.time()
    records = [
        {
            "timestamp": now,
            "type": "session_meta",
            "payload": {"id": "native-recovery", "cwd": str(cwd)},
        },
        {
            "timestamp": now + 1,
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Visible recovery request"},
        },
        {
            "timestamp": now + 2,
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": "HIDDEN_REASONING_CANARY"},
        },
        {
            "timestamp": now + 3,
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "arguments": "TOOL_ARGUMENT_CANARY --token SECRET_VALUE_CANARY",
            },
        },
        {
            "timestamp": now + 4,
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "ordinary-tool",
                "output": "TOOL_RESULT_CANARY",
            },
        },
        {
            "timestamp": now + 5,
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "Visible recovery answer"},
        },
    ]
    log.write_text("".join(json.dumps(record) + "\n" for record in records))
    cfg = Config(
        experimental_agent_workspace_enabled=True,
        agent_store_path=str(tmp_path / "agents.sqlite3"),
        agent_codex_home=str(tmp_path / "codex"),
        agent_claude_home=str(tmp_path / "claude"),
    )
    service = AgentService(cfg)
    observation = PaneObservation(
        pane_id="%7",
        target="work:1.0",
        command="codex",
        title="TERMINAL_CAPTURE_CANARY",
        cwd=str(cwd),
        pid="7",
        pane_created=now - 1,
        runtime="codex",
        status="idle",
        question=None,
        menu=(),
        prompt_fingerprint="TERMINAL_PROMPT_CANARY",
        observed_at=now + 6,
    )
    asyncio.run(service.process_now([observation]))
    agent = service.list_agents()[0][0]
    recovery = service.recovery(agent["id"])
    assert recovery["freshness"]["observed_at"] == observation.observed_at
    assert recovery["freshness"]["runtime_session"] == "live_bound"
    encoded = json.dumps(recovery, sort_keys=True)
    assert "Visible recovery request" in encoded
    assert "Visible recovery answer" in encoded
    for prohibited in (
        "PRIVATE_CWD_CANARY",
        "HIDDEN_REASONING_CANARY",
        "TOOL_ARGUMENT_CANARY",
        "TOOL_RESULT_CANARY",
        "SECRET_VALUE_CANARY",
        "TERMINAL_CAPTURE_CANARY",
        "TERMINAL_PROMPT_CANARY",
    ):
        assert prohibited not in encoded

    stale_observation = PaneObservation(
        pane_id=observation.pane_id,
        target=observation.target,
        command=observation.command,
        title=observation.title,
        cwd=observation.cwd,
        pid=observation.pid,
        pane_created=observation.pane_created,
        runtime=observation.runtime,
        status=observation.status,
        question=observation.question,
        menu=observation.menu,
        prompt_fingerprint=observation.prompt_fingerprint,
        observed_at=time.time() - 11,
    )
    asyncio.run(service.process_now([stale_observation]))
    stale = service.recovery(agent["id"])["freshness"]
    assert stale["observed_at"] is None
    assert stale["runtime_session"] == "unknown"
    asyncio.run(service.process_now([observation]))

    newer_raw_observation = PaneObservation(
        pane_id=observation.pane_id,
        target=observation.target,
        command=observation.command,
        title=observation.title,
        cwd=observation.cwd,
        pid=observation.pid,
        pane_created=observation.pane_created,
        runtime=observation.runtime,
        status=observation.status,
        question=observation.question,
        menu=observation.menu,
        prompt_fingerprint=observation.prompt_fingerprint,
        observed_at=observation.observed_at + 1,
    )
    entered = threading.Event()
    release = threading.Event()
    original_recovery = service.store.recovery

    def paused_recovery(*args, **kwargs):
        value = original_recovery(*args, **kwargs)
        entered.set()
        assert release.wait(1)
        return value

    service.store.recovery = paused_recovery
    raced_recoveries = []
    thread = threading.Thread(
        target=lambda: raced_recoveries.append(service.recovery(agent["id"]))
    )
    thread.start()
    assert entered.wait(1)
    service.submit([newer_raw_observation])
    release.set()
    thread.join(1)
    assert not thread.is_alive()
    unverified = raced_recoveries[0]["freshness"]
    assert unverified["observed_at"] is None
    assert unverified["runtime_session"] == "unknown"


def test_deleted_cursor_never_projects_future_history_and_private_metadata_is_absent(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = _agent(store)
    now = time.time()
    for index in range(1, 4):
        _project(store, agent["id"], index, message_time=now + index)
    first = store.recovery(agent["id"], activity_limit=1)["recent_activity"]
    cursor = first["older_cursor"]
    assert cursor
    assert store.delete_history(agent["id"]) is True
    _project(store, agent["id"], 9, message_time=now - 1000)

    stale = store.recovery(
        agent["id"], activity_limit=50, activity_cursor=cursor
    )["recent_activity"]
    assert stale["cursor_status"] in ("data_unavailable", "exhausted")
    assert all("step-9" not in json.dumps(entry) for entry in stale["entries"])
    encoded = json.dumps(store.recovery(agent["id"]), sort_keys=True)
    assert "SECRET-log" not in encoded
    assert "SECRET-cwd" not in encoded
    assert "runtime_owned_unverified" in encoded


def test_recovery_api_is_authenticated_advertised_no_store_and_clamps_limits(tmp_path):
    from fastapi.testclient import TestClient

    from vmux.config import Config
    from vmux.server import create_app

    cfg = Config(
        experimental_agent_workspace_enabled=True,
        agent_store_path=str(tmp_path / "api-agents.sqlite3"),
        agent_codex_home=str(tmp_path / "codex"),
        agent_claude_home=str(tmp_path / "claude"),
    )
    cfg.token = "secret"
    cfg.server_instance_id = "server-fixture"
    app = create_app(cfg)
    agent = _agent(app.state.hub.agents.store, native="api")
    _project(app.state.hub.agents.store, agent["id"], 1, message_time=time.time())
    with TestClient(app) as client:
        unauthorized = client.get("/api/agents/%s/recovery" % agent["id"])
        assert unauthorized.status_code == 401
        assert unauthorized.headers["cache-control"] == "no-store, max-age=0"
        auth = {"Authorization": "Bearer secret"}
        capability = client.get("/api/config", headers=auth).json()["_info"][
            "capabilities"
        ]["agent_context_v1"]["recovery"]
        assert capability == {
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
        response = client.get(
            "/api/agents/%s/recovery" % agent["id"],
            headers=auth,
            params={
                "message_limit": 100000,
                "timeline_limit": -1,
                "activity_limit": 100000,
            },
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, max-age=0"
        assert response.json()["server_instance_id"] == "server-fixture"
        for path in (
            "/api/agents",
            "/api/agents/%s" % agent["id"],
            "/api/agents/%s/resume" % agent["id"],
            "/api/agents/%s/messages" % agent["id"],
            "/api/agents/%s/timeline" % agent["id"],
            "/api/timeline",
            "/api/review",
            "/api/decisions",
        ):
            structured = client.get(path, headers=auth)
            assert structured.status_code == 200
            assert structured.headers["cache-control"] == "no-store, max-age=0"
        missing = client.get("/api/agents/missing/recovery", headers=auth)
        assert missing.status_code == 404
        assert missing.headers["cache-control"] == "no-store, max-age=0"
        malformed = client.get(
            "/api/agents/%s/recovery" % agent["id"],
            headers=auth,
            params={"activity_cursor": "rc1." + "a" * 1001},
        )
        assert malformed.status_code == 400
        assert malformed.headers["cache-control"] == "no-store, max-age=0"


def test_recovery_freshness_rejects_uncorrelated_and_previous_process_observations(tmp_path):
    from vmux.agents.models import PaneObservation, default_capabilities
    from vmux.agents.service import AgentService
    from vmux.config import Config

    cfg = Config(
        experimental_agent_workspace_enabled=True,
        agent_store_path=str(tmp_path / "freshness.sqlite3"),
    )
    service = AgentService(cfg)
    agent = _agent(service.store, native="freshness")
    observed_at = time.time() - 5
    observation = PaneObservation(
        pane_id="%9",
        target="work:1.0",
        command="codex",
        title="private terminal title",
        cwd="/private/worktree/SECRET-cwd",
        pid="9",
        pane_created=observed_at - 10,
        runtime="codex",
        status="idle",
        question=None,
        menu=(),
        prompt_fingerprint="private-fingerprint",
        observed_at=observed_at,
    )
    service.store.update_binding(
        agent["id"],
        association="confirmed",
        pane_id=observation.pane_id,
        target=observation.target,
        pane_pid=observation.pid,
        pane_created=observation.pane_created,
        pane_incarnation=observation.incarnation,
        source="automatic",
        capabilities=default_capabilities("confirmed"),
    )

    before_observation = service.recovery(agent["id"])["freshness"]
    assert before_observation["observed_at"] is None
    assert before_observation["runtime_session"] == "unknown"

    service.submit([observation])
    uncorrelated = service.recovery(agent["id"])["freshness"]
    assert uncorrelated["observed_at"] is None
    assert uncorrelated["runtime_session"] == "unknown"

    restarted = AgentService(cfg)
    after_restart = restarted.recovery(agent["id"])["freshness"]
    assert after_restart["observed_at"] is None
    assert after_restart["runtime_session"] == "unknown"


def test_canonical_recovery_fixture_tracks_the_discriminated_contract():
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parents[1] / "docs/reference/recovery-v1.fixture.json").read_text()
    )
    assert set(fixture) == {
        "version",
        "generated_at",
        "server_instance_id",
        "consistency",
        "agent",
        "changes",
        "recent_messages",
        "recent_timeline",
        "recent_activity",
        "freshness",
    }
    assert fixture["recent_activity"]["order"] == "oldest_to_newest"
    assert {entry["kind"] for entry in fixture["recent_activity"]["entries"]} == {
        "visible_message",
        "semantic_event",
    }
    assert fixture["freshness"]["model_context"] == "runtime_owned_unverified"


def test_disabled_capability_omits_recovery_metadata_and_api_is_unavailable(tmp_path):
    from fastapi.testclient import TestClient

    from vmux.agents.service import AgentService
    from vmux.config import Config
    from vmux.server import create_app

    cfg = Config(
        experimental_agent_workspace_enabled=False,
        agent_store_path=str(tmp_path / "disabled.sqlite3"),
    )
    cfg.token = "secret"
    assert "recovery" not in AgentService(cfg).info()
    with TestClient(create_app(cfg)) as client:
        auth = {"Authorization": "Bearer secret"}
        capability = client.get("/api/config", headers=auth).json()["_info"][
            "capabilities"
        ]["agent_context_v1"]
        assert "recovery" not in capability
        response = client.get("/api/agents/anything/recovery", headers=auth)
        assert response.status_code == 503
        assert response.json()["detail"] == "agent context is disabled"
        assert response.headers["cache-control"] == "no-store, max-age=0"


def test_recovery_empty_missing_and_cursor_validation(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = _agent(store)
    empty = store.recovery(
        agent["id"], message_limit=0, timeline_limit=-100, activity_limit=0
    )
    assert empty["recent_activity"]["entries"] == []
    assert empty["recent_messages"]["messages"] == []
    assert empty["recent_timeline"]["events"] == []
    assert store.recovery("missing") is None
    for cursor in ("not-opaque", "rc1.a", "rc1.____"):
        with pytest.raises(ValueError, match="bad recovery cursor"):
            store.recovery(agent["id"], activity_cursor=cursor)

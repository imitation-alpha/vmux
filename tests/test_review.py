from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time

import pytest

from vmux.agents.models import PaneObservation, default_capabilities
from vmux.agents.observers import _decision_payload
from vmux.agents.service import AgentService
from vmux.agents.store import AgentStore
from vmux.config import Config
from vmux.models import PaneState
from vmux.server import create_app


def config(tmp_path) -> Config:
    return Config(
        experimental_agent_workspace_enabled=True,
        agent_store_path=str(tmp_path / "agents.sqlite3"),
        agent_codex_home=str(tmp_path / "codex"),
        agent_claude_home=str(tmp_path / "claude"),
    )


def add_snapshot(store: AgentStore, agent_id: str, **updates):
    context = dict(store.get_agent(agent_id)["context"])
    context.update(updates)
    context.setdefault("last_updated", time.time())
    snapshot, _, _ = store.apply_projection(agent_id, context, [], [], [])
    assert snapshot is not None
    return snapshot


def add_decision(
    store: AgentStore,
    agent_id: str,
    native_id: str,
    *,
    priority: str = "normal",
    created_at: float | None = None,
):
    context = dict(store.get_agent(agent_id)["context"])
    context["last_updated"] = created_at or time.time()
    snapshot, ids, _ = store.apply_projection(
        agent_id,
        context,
        [],
        [
            {
                "native_event_id": native_id,
                "title": "Choose a safe option",
                "description": "Which reviewed option should continue?",
                "kind": "question",
                "priority": priority,
                "options": [
                    {"id": "a", "label": "Option A", "description": "First"},
                    {"id": "b", "label": "Option B", "description": "Second"},
                ],
                "input_map": {"a": "1", "b": "2"},
                "recommendation": "a",
                "allow_custom": False,
                "prompt_fingerprint": "opaque-prompt-hash",
                "status": "pending",
                "created_at": created_at or time.time(),
            }
        ],
        [],
    )
    assert snapshot is not None and len(ids) == 1
    return store.get_decision(ids[0])


def test_v2_migration_seeds_visits_once_and_preserves_later_legacy_visits(tmp_path):
    path = tmp_path / "agents.sqlite3"
    store = AgentStore(str(path))
    first = store.upsert_session("codex", "first", "/private/log", "/project", "v1")
    first_snapshot = add_snapshot(store, first["id"], goal="Already reviewed")
    store.visit(first["id"], first_snapshot["id"])
    store.close()

    # Re-create the exact upgrade boundary: a v1 database with visit data but
    # no v2 tables or migration record.
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE session_reviews")
    conn.execute("DROP TABLE review_settings")
    conn.execute("DELETE FROM schema_migrations WHERE version=2")
    conn.commit()
    conn.close()

    upgraded = AgentStore(str(path))
    upgraded.open()
    seeded = upgraded.conn.execute(
        "SELECT * FROM session_reviews WHERE session_id=?", (first["id"],)
    ).fetchone()
    assert seeded["snapshot_id"] == first_snapshot["id"]
    assert seeded["snapshot_sequence"] == first_snapshot["sequence"]
    assert [
        row["version"]
        for row in upgraded.conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ] == [1, 2]

    second = upgraded.upsert_session(
        "codex", "second", "/private/log-2", "/project", "v1"
    )
    second_snapshot = add_snapshot(upgraded, second["id"], goal="Legacy visit after v2")
    upgraded.visit(second["id"], second_snapshot["id"])
    upgraded.close()

    reopened = AgentStore(str(path))
    reopened.open()
    assert (
        reopened.conn.execute(
            "SELECT 1 FROM session_reviews WHERE session_id=?", (second["id"],)
        ).fetchone()
        is None
    )


def test_v2_migration_is_safe_when_two_stores_open_together(tmp_path):
    path = tmp_path / "agents.sqlite3"
    initial = AgentStore(str(path))
    agent = initial.upsert_session(
        "codex", "concurrent", "/private/log", "/project", "v1"
    )
    snapshot = add_snapshot(initial, agent["id"], goal="Seed once")
    initial.visit(agent["id"], snapshot["id"])
    initial.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE session_reviews")
    conn.execute("DROP TABLE review_settings")
    conn.execute("DELETE FROM schema_migrations WHERE version=2")
    conn.commit()
    conn.close()

    barrier = threading.Barrier(3)
    errors = []

    def open_store():
        candidate = AgentStore(str(path))
        barrier.wait()
        try:
            candidate.open()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            candidate.close()

    threads = [threading.Thread(target=open_store) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    checked = AgentStore(str(path))
    assert checked.conn.execute(
        "SELECT COUNT(*) AS n FROM session_reviews WHERE session_id=?",
        (agent["id"],),
    ).fetchone()["n"] == 1
    assert checked.conn.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()["version"] == 2


def test_review_ack_is_monotonic_resets_timer_and_history_delete_removes_it(
    tmp_path, monkeypatch
):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = store.upsert_session("codex", "review", "/private/log", "/project", "v1")
    first = add_snapshot(store, agent["id"], goal="First")
    second = add_snapshot(store, agent["id"], goal="Second")
    store.update_review_settings(interval_present=True, interval_minutes=30, now=100)

    monkeypatch.setattr("vmux.agents.store.time.time", lambda: 2_000)
    latest = store.review(agent["id"], second["id"])
    reset_due = store.get_review_settings()["next_due_at"]
    assert reset_due == 3_800
    monkeypatch.setattr("vmux.agents.store.time.time", lambda: 3_000)
    stale = store.review(agent["id"], first["id"])
    assert latest["snapshot_id"] == second["id"]
    assert stale["snapshot_id"] == second["id"] and stale["advanced"] is False
    assert store.get_review_settings()["next_due_at"] == reset_due

    assert store.delete_history(agent["id"]) is True
    assert (
        store.conn.execute(
            "SELECT 1 FROM session_reviews WHERE session_id=?", (agent["id"],)
        ).fetchone()
        is None
    )


def test_review_group_ranking_decision_status_and_option_fingerprint(tmp_path):
    service = AgentService(config(tmp_path))
    store = service.store
    now = time.time()

    changed = store.upsert_session("codex", "changed", "/private/a", "/project", "v1")
    add_snapshot(store, changed["id"], goal="Changed")

    blocker = store.upsert_session("codex", "blocker", "/private/b", "/project", "v1")
    add_snapshot(
        store,
        blocker["id"],
        blockers=[
            {
                "id": "blocked",
                "title": "Schema unavailable",
                "created_at": now - 40,
            }
        ],
    )

    pending = store.upsert_session("codex", "pending", "/private/c", "/project", "v1")
    add_decision(store, pending["id"], "normal", created_at=now - 30)

    broken = store.upsert_session("claude", "broken", "/private/d", "/project", "v1")
    add_snapshot(store, broken["id"], lifecycle="error")

    urgent = store.upsert_session("codex", "urgent", "/private/e", "/project", "v1")
    urgent_decision = add_decision(
        store, urgent["id"], "urgent", priority="critical", created_at=now - 10
    )
    unknown = add_decision(
        store, urgent["id"], "unknown", priority="normal", created_at=now - 5
    )
    store.mark_decision_submitting(unknown["id"], "a", "unknown-key")
    store.mark_decision_unknown(unknown["id"])

    groups = service.review_payload([])["groups"]
    assert [group["rank_reason"] for group in groups] == [
        "urgent_decision",
        "error",
        "pending_decision",
        "new_blocker",
        "changed",
    ]
    urgent_group = groups[0]
    assert [item["review_status"] for item in urgent_group["decisions"]] == [
        "actionable",
        "terminal_required",
    ]
    expected = hashlib.sha256(
        json.dumps(
            urgent_decision["options"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    assert urgent_group["decisions"][0]["options_fingerprint"] == expected
    assert urgent_group["attention_reasons"][:2] == [
        "urgent_decision",
        "pending_decision",
    ]


def test_unknown_only_is_terminal_required_not_ranked_as_pending(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = store.upsert_session(
        "codex", "unknown-only", "/private/log", "/project", "v1"
    )
    decision = add_decision(store, agent["id"], "unknown-only")
    store.mark_decision_submitting(decision["id"], "a", "unknown-only-key")
    store.mark_decision_unknown(decision["id"])

    group = store.review_groups()[0]
    assert group["rank_reason"] == "changed"
    assert group["oldest_pending_decision_at"] is None
    assert group["attention_reasons"] == [
        "terminal_required_decision",
        "semantic_change",
    ]
    assert group["decisions"][0]["review_status"] == "terminal_required"


def test_submitting_decisions_are_deliberately_omitted_from_review(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    agent = store.upsert_session(
        "codex", "submitting", "/private/log", "/project", "v1"
    )
    decision = add_decision(store, agent["id"], "submitting")
    store.mark_decision_submitting(decision["id"], "a", "submitting-key")

    group = store.review_groups()[0]
    assert group["decisions"] == []
    assert group["rank_reason"] == "changed"


def test_reviewed_errors_clear_and_snapshotless_sessions_never_make_cards(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    fresh = store.upsert_session(
        "codex", "fresh", "/private/fresh", "/project", "v1"
    )
    assert store.review_groups() == []

    errored = store.upsert_session(
        "codex", "errored", "/private/error", "/project", "v1"
    )
    error_snapshot = add_snapshot(store, errored["id"], lifecycle="error")
    error_group = next(
        group
        for group in store.review_groups()
        if group["agent_id"] == errored["id"]
    )
    assert error_group["rank_reason"] == "error"
    assert "agent_error" in error_group["attention_reasons"]
    store.review(errored["id"], error_snapshot["id"])
    assert all(
        group["agent_id"] != errored["id"] for group in store.review_groups()
    )

    degraded = store.upsert_session(
        "claude", "degraded", "/private/degraded", "/project", "v1"
    )
    store.update_cursor(
        degraded["id"], 0, 1, "v1", error="parse_failed"
    )
    degraded_snapshot = add_snapshot(
        store, degraded["id"], extraction_health="degraded"
    )
    degraded_group = next(
        group
        for group in store.review_groups()
        if group["agent_id"] == degraded["id"]
    )
    assert degraded_group["rank_reason"] == "error"
    assert "extraction_degraded" in degraded_group["attention_reasons"]
    store.review(degraded["id"], degraded_snapshot["id"])
    assert all(
        group["agent_id"] != degraded["id"] for group in store.review_groups()
    )

    changed = add_snapshot(store, fresh["id"], goal="History gets deleted")
    assert changed["id"]
    assert store.delete_history(fresh["id"]) is True
    assert all(
        group["agent_id"] != fresh["id"] for group in store.review_groups()
    )


def test_terminal_review_items_are_reference_only_and_deduplicated(tmp_path):
    service = AgentService(config(tmp_path))
    store = service.store
    agent = store.upsert_session("codex", "bound", "/private/log", "/project", "v1")
    add_decision(store, agent["id"], "pending")
    store.update_binding(
        agent["id"],
        association="confirmed",
        pane_id="%1",
        target="secret:1.1",
        pane_pid="123",
        pane_created=1,
        pane_incarnation="incarnation",
        source="manual",
        capabilities=default_capabilities("confirmed"),
    )
    panes = [
        PaneState(
            id="%1",
            target="secret:1.1",
            name="Private pending prompt",
            kind="claude-code",
            status="needs_input",
            question="Do not put this prompt in Review",
            lines=["raw terminal capture"],
            updated=10,
        ),
        PaneState(
            id="%2",
            target="/private/path",
            name="Private error title",
            kind="shell",
            status="error",
            question="secret",
            lines=["traceback and credentials"],
            updated=20,
        ),
        PaneState(
            id="%3",
            target="other:1.1",
            name="Unstructured request",
            kind="generic",
            status="needs_input",
            question="raw question",
            lines=["raw prompt"],
            updated=30,
        ),
    ]
    # A durable pending row alone is insufficient to hide the terminal item.
    assert "%1" in {
        item["pane_id"] for item in service.review_payload(panes)["terminal_items"]
    }
    service.submit(
        [
            PaneObservation(
                pane_id="%1",
                target="secret:1.1",
                command="claude",
                title="private",
                cwd="/project",
                pid="123",
                pane_created=1,
                runtime="codex",
                status="needs_input",
                question="Which reviewed option should continue?",
                menu=(
                    {"key": "1", "label": "Option A"},
                    {"key": "2", "label": "Option B"},
                ),
                prompt_fingerprint="opaque-prompt-hash",
            )
        ]
    )
    payload = service.review_payload(panes)
    assert [item["pane_id"] for item in payload["terminal_items"]] == ["%2", "%3"]
    assert set(payload["terminal_items"][0]) == {
        "id",
        "pane_id",
        "status",
        "kind",
        "updated_at",
        "acknowledgeable",
    }
    encoded = json.dumps(payload["terminal_items"])
    for secret in (
        "Private",
        "secret:1.1",
        "/private/path",
        "prompt",
        "traceback",
        "credentials",
    ):
        assert secret not in encoded

    pending = payload["groups"][0]["decisions"][0]
    store.mark_decision_submitting(
        pending["id"], "a", "terminal-required-key"
    )
    store.mark_decision_unknown(pending["id"])
    assert "%1" in {
        item["pane_id"] for item in service.review_payload(panes)["terminal_items"]
    }


def test_message_search_role_dates_metadata_and_bounded_pagination(tmp_path):
    service = AgentService(config(tmp_path))
    store = service.store
    agent = store.upsert_session("codex", "messages", "/private/log", "/project", "v1")
    now = time.time()
    store.apply_projection(
        agent["id"],
        agent["context"],
        [
            {
                "native_event_id": "one",
                "role": "user",
                "content": "Alpha request",
                "status": "observed",
                "created_at": now - 30,
            },
            {
                "native_event_id": "two",
                "role": "assistant",
                "content": "Alpha response",
                "status": "observed",
                "created_at": now - 20,
            },
            {
                "native_event_id": "three",
                "role": "assistant",
                "content": "Beta response",
                "status": "observed",
                "created_at": now - 10,
            },
        ],
        [],
        [],
    )
    messages, cursor, metadata = service.list_messages(
        agent["id"],
        limit=1,
        q="alpha",
        role="assistant",
        after=now - 25,
        before=now - 15,
        with_metadata=True,
    )
    assert cursor is None
    assert [item["content"] for item in messages] == ["Alpha response"]
    assert metadata["retained_from"] == pytest.approx(now - 30)
    assert metadata["retained_to"] == pytest.approx(now - 10)
    assert metadata["reviewed_at"] is None
    assert metadata["reviewed_snapshot_id"] is None
    assert metadata["reviewed_snapshot_sequence"] is None
    assert metadata["reviewed_snapshot_at"] is None
    assert metadata["filters"] == {
        "q": "alpha",
        "role": "assistant",
        "after": pytest.approx(now - 25),
        "before": pytest.approx(now - 15),
    }
    all_messages, cursor, _ = service.list_messages(
        agent["id"], limit=1, with_metadata=True
    )
    assert len(all_messages) == 1 and cursor == "1"
    with pytest.raises(ValueError, match="role"):
        service.list_messages(agent["id"], role="tool")


def test_message_metadata_keeps_review_boundary_after_card_is_acknowledged(tmp_path):
    service = AgentService(config(tmp_path))
    store = service.store
    agent = store.upsert_session(
        "codex", "reviewed-messages", "/private/log", "/project", "v1"
    )
    snapshot = add_snapshot(store, agent["id"], goal="Review transcript")

    before = time.time()
    acknowledged = store.review(agent["id"], snapshot["id"])
    after = time.time()
    assert acknowledged["advanced"] is True
    assert store.review_groups() == []

    messages, cursor, metadata = service.list_messages(
        agent["id"], with_metadata=True
    )
    assert messages == [] and cursor is None
    assert metadata["reviewed_snapshot_id"] == snapshot["id"]
    assert metadata["reviewed_snapshot_sequence"] == snapshot["sequence"]
    assert metadata["reviewed_snapshot_at"] == pytest.approx(snapshot["created_at"])
    assert before <= metadata["reviewed_at"] <= after


def test_message_metadata_discloses_content_dropped_at_retention_boundary(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"), retention_days=1)
    agent = store.upsert_session(
        "codex", "truncated", "/private/log", "/project", "v1"
    )
    secret = "old visible content that must not be retained"
    store.apply_projection(
        agent["id"],
        agent["context"],
        [
            {
                "native_event_id": "expired",
                "role": "assistant",
                "content": secret,
                "status": "observed",
                "created_at": time.time() - 2 * 86400,
            }
        ],
        [],
        [],
    )

    messages, cursor, metadata = store.list_messages(
        agent["id"], with_metadata=True
    )
    assert messages == [] and cursor is None
    assert metadata["history_truncated"] is True
    assert secret not in store.get_agent(agent["id"])["context"].values()
    assert store.review_groups() == []


def test_api_review_is_read_only_settings_are_partial_and_ack_is_explicit(tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    cfg = config(tmp_path)
    cfg.token = "secret"
    cfg.server_instance_id = "server-test"
    app = create_app(cfg)
    store = app.state.hub.agents.store
    agent = store.upsert_session("codex", "api", "/private/log", "/project", "v1")
    first = add_snapshot(store, agent["id"], goal="Review API")
    client = TestClient(app)
    client.__enter__()  # run the real server lifespan before capability checks
    auth = {"Authorization": "Bearer secret"}

    capability = client.get("/api/config", headers=auth).json()["_info"][
        "capabilities"
    ]
    assert capability["agent_review_v1"] == {
        "enabled": True,
        "version": 1,
        "scheduling": True,
        "finish_batch": True,
        "min_interval_minutes": 5,
        "max_interval_minutes": 1440,
    }
    review_payload = client.get("/api/review", headers=auth).json()
    assert set(review_payload) == {
        "version",
        "generated_at",
        "settings",
        "due",
        "counts",
        "groups",
        "terminal_items",
    }
    assert set(review_payload["settings"]) == {
        "enabled",
        "interval_minutes",
        "next_due_at",
        "last_digest_at",
        "urgent_bypass",
        "min_interval_minutes",
        "max_interval_minutes",
        "presets",
    }
    assert set(review_payload["due"]) == {
        "is_due",
        "urgent",
        "has_work",
        "next_due_at",
    }
    assert set(review_payload["counts"]) == {
        "agents_changed",
        "pending_decisions",
        "terminal_requests",
        "total_cards",
        "urgent_items",
    }
    for path in (
        "/api/agents/" + agent["id"],
        "/api/agents/" + agent["id"] + "/resume",
        "/api/agents/" + agent["id"] + "/messages",
        "/api/agents/" + agent["id"] + "/timeline",
    ):
        assert client.get(path, headers=auth).status_code == 200
    assert (
        store.conn.execute(
            "SELECT 1 FROM session_reviews WHERE session_id=?", (agent["id"],)
        ).fetchone()
        is None
    )

    enabled = client.patch(
        "/api/review/settings",
        headers=auth,
        json={"interval_minutes": 30},
    ).json()
    assert enabled["enabled"] is True and enabled["next_due_at"] is not None
    original_due = enabled["next_due_at"]
    urgent_only = client.patch(
        "/api/review/settings",
        headers=auth,
        json={"urgent_pane_errors": True},
    ).json()
    assert urgent_only["interval_minutes"] == 30
    assert urgent_only["next_due_at"] == original_due
    same = client.patch(
        "/api/review/settings",
        headers=auth,
        json={"interval_minutes": 30},
    ).json()
    assert same["next_due_at"] == original_due

    acknowledged = client.put(
        "/api/agents/" + agent["id"] + "/review",
        headers=auth,
        json={"snapshot_id": first["id"]},
    ).json()
    assert acknowledged["advanced"] is True
    assert acknowledged["snapshot_id"] == first["id"]
    assert acknowledged["snapshot_at"] == pytest.approx(first["created_at"])
    assert store.get_review_settings()["next_due_at"] >= original_due

    disabled = client.patch(
        "/api/review/settings",
        headers=auth,
        json={"interval_minutes": None},
    ).json()
    assert disabled["enabled"] is False
    assert disabled["interval_minutes"] is None
    assert disabled["next_due_at"] is None
    client.__exit__(None, None, None)


def test_finish_review_is_atomic_monotonic_and_resets_timer_once(tmp_path, monkeypatch):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    first_agent = store.upsert_session(
        "codex", "batch-first", "/private/first", "/project", "v1"
    )
    second_agent = store.upsert_session(
        "claude", "batch-second", "/private/second", "/project", "v1"
    )
    first_snapshot = add_snapshot(store, first_agent["id"], goal="First")
    second_snapshot = add_snapshot(store, second_agent["id"], goal="Second")
    store.update_review_settings(interval_present=True, interval_minutes=30, now=100)

    monkeypatch.setattr("vmux.agents.store.time.time", lambda: 2_000)
    finished = store.finish_review(
        [
            {"agent_id": first_agent["id"], "snapshot_id": first_snapshot["id"]},
            {"agent_id": second_agent["id"], "snapshot_id": second_snapshot["id"]},
        ]
    )
    assert finished == {
        "requested": 2,
        "advanced": 2,
        "unchanged": 0,
        "processed_at": 2_000,
        "next_due_at": 3_800,
    }
    assert {
        row["session_id"]: row["snapshot_id"]
        for row in store.conn.execute("SELECT * FROM session_reviews")
    } == {
        first_agent["id"]: first_snapshot["id"],
        second_agent["id"]: second_snapshot["id"],
    }

    monkeypatch.setattr("vmux.agents.store.time.time", lambda: 3_000)
    replay = store.finish_review(
        [
            {"agent_id": first_agent["id"], "snapshot_id": first_snapshot["id"]},
            {"agent_id": second_agent["id"], "snapshot_id": second_snapshot["id"]},
        ]
    )
    assert replay["advanced"] == 0
    assert replay["unchanged"] == 2
    assert replay["next_due_at"] == 3_800


def test_finish_review_invalid_target_rolls_back_every_baseline(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    first_agent = store.upsert_session(
        "codex", "rollback-first", "/private/first", "/project", "v1"
    )
    second_agent = store.upsert_session(
        "claude", "rollback-second", "/private/second", "/project", "v1"
    )
    first_snapshot = add_snapshot(store, first_agent["id"], goal="First")
    second_snapshot = add_snapshot(store, second_agent["id"], goal="Second")

    with pytest.raises(KeyError):
        store.finish_review(
            [
                {
                    "agent_id": first_agent["id"],
                    "snapshot_id": first_snapshot["id"],
                },
                {
                    "agent_id": first_agent["id"],
                    "snapshot_id": second_snapshot["id"],
                },
            ]
        )
    assert store.conn.execute("SELECT COUNT(*) AS n FROM session_reviews").fetchone()[
        "n"
    ] == 0


def test_finish_review_api_validation_auth_and_event(tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    cfg = config(tmp_path)
    cfg.token = "secret"
    app = create_app(cfg)
    store = app.state.hub.agents.store
    first_agent = store.upsert_session(
        "codex", "api-batch-first", "/private/first", "/project", "v1"
    )
    second_agent = store.upsert_session(
        "claude", "api-batch-second", "/private/second", "/project", "v1"
    )
    first_snapshot = add_snapshot(store, first_agent["id"], goal="First")
    second_snapshot = add_snapshot(store, second_agent["id"], goal="Second")
    targets = [
        {"agent_id": first_agent["id"], "snapshot_id": first_snapshot["id"]},
        {"agent_id": second_agent["id"], "snapshot_id": second_snapshot["id"]},
    ]

    with TestClient(app) as client:
        assert client.put("/api/review/finish", json={"targets": targets}).status_code == 401
        duplicate = client.put(
            "/api/review/finish",
            headers={"Authorization": "Bearer secret"},
            json={"targets": [targets[0], targets[0]]},
        )
        assert duplicate.status_code == 422
        oversized = client.put(
            "/api/review/finish",
            headers={"Authorization": "Bearer secret"},
            json={"targets": [
                {"agent_id": "agent-%d" % index, "snapshot_id": "snapshot"}
                for index in range(11)
            ]},
        )
        assert oversized.status_code == 422
        mismatched = client.put(
            "/api/review/finish",
            headers={"Authorization": "Bearer secret"},
            json={"targets": [targets[0], {
                "agent_id": second_agent["id"],
                "snapshot_id": first_snapshot["id"],
            }]},
        )
        assert mismatched.status_code == 409
        assert store.conn.execute(
            "SELECT COUNT(*) AS n FROM session_reviews"
        ).fetchone()["n"] == 0

        finished = client.put(
            "/api/review/finish",
            headers={"Authorization": "Bearer secret"},
            json={"targets": targets},
        )
        assert finished.status_code == 200
        assert finished.json()["advanced"] == 2
        assert any(
            item["event"]["kind"] == "review_finished"
            for item in app.state.hub.agents._history
        )


def test_priority_is_only_explicit_runtime_metadata():
    base = {
        "questions": [
            {
                "question": "This wording says URGENT but has no metadata",
                "options": [{"id": "yes", "label": "Yes"}],
            }
        ]
    }
    assert _decision_payload("request_user_input", base)["priority"] == "normal"
    base["questions"][0]["priority"] = "HIGH"
    assert _decision_payload("request_user_input", base)["priority"] == "high"
    base["questions"][0]["priority"] = "emergency"
    assert _decision_payload("request_user_input", base)["priority"] == "normal"

from __future__ import annotations

import asyncio
import time

from vmux.agents.service import AgentService
from vmux.agents.store import AgentStore
from vmux.config import Config
from vmux.models import PaneState
from vmux.poller import Hub
from vmux.push import collect_alerts


def config(tmp_path) -> Config:
    cfg = Config(
        agent_store_path=str(tmp_path / "agents.sqlite3"),
        agent_codex_home=str(tmp_path / "codex"),
        agent_claude_home=str(tmp_path / "claude"),
    )
    cfg.push_on_error = True
    return cfg


def add_changed_agent(store: AgentStore):
    agent = store.upsert_session(
        "codex", "scheduled", "/private/log", "/project", "v1"
    )
    context = dict(agent["context"])
    context.update({"goal": "Review scheduled work", "last_updated": time.time()})
    snapshot, _, _ = store.apply_projection(agent["id"], context, [], [], [])
    assert snapshot is not None
    return agent


def test_due_claim_recovers_after_restart_and_never_duplicates(tmp_path):
    path = tmp_path / "agents.sqlite3"
    store = AgentStore(str(path))
    store.open()
    store.update_review_settings(
        interval_present=True, interval_minutes=5, now=100
    )
    store.close()

    restarted = AgentStore(str(path))
    first = restarted.claim_review_due(has_work=True, now=401)
    assert first == {
        "claimed": True,
        "has_work": True,
        "next_due_at": 701,
        "last_digest_at": 401,
    }
    restarted.close()

    again = AgentStore(str(path))
    duplicate = again.claim_review_due(has_work=True, now=401)
    assert duplicate["claimed"] is False
    assert duplicate["next_due_at"] == 701
    assert duplicate["last_digest_at"] == 401


def test_empty_due_window_advances_without_recording_a_digest(tmp_path):
    store = AgentStore(str(tmp_path / "agents.sqlite3"))
    store.update_review_settings(
        interval_present=True, interval_minutes=5, now=100
    )
    claimed = store.claim_review_due(has_work=False, now=400)
    assert claimed["claimed"] is True
    assert claimed["next_due_at"] == 700
    assert claimed["last_digest_at"] is None
    assert store.claim_review_due(has_work=False, now=400)["claimed"] is False


def test_structured_pushes_batch_normal_priority_but_urgent_bypasses(tmp_path):
    class Push:
        def __init__(self):
            self.decisions = []

        def fire_agent_decision(self, decision):
            self.decisions.append(decision["id"])

    async def scenario():
        push = Push()
        service = AgentService(config(tmp_path), push=push)
        service.store.open()
        service._loop = asyncio.get_running_loop()

        service._fire_agent_decision({"id": "off-normal", "priority": "normal"})
        await asyncio.sleep(0)
        assert push.decisions == ["off-normal"]

        service.store.update_review_settings(
            interval_present=True, interval_minutes=30
        )
        service._fire_agent_decision({"id": "batched-low", "priority": "low"})
        service._fire_agent_decision({"id": "batched-normal", "priority": "normal"})
        service._fire_agent_decision({"id": "urgent-high", "priority": "high"})
        service._fire_agent_decision(
            {"id": "urgent-critical", "priority": "critical"}
        )
        await asyncio.sleep(0)
        assert push.decisions == [
            "off-normal",
            "urgent-high",
            "urgent-critical",
        ]

    asyncio.run(scenario())


def test_pane_transition_policy_batches_input_and_can_bypass_errors():
    previous = {
        "%input": PaneState(id="%input", target="a", name="a", status="idle"),
        "%error": PaneState(id="%error", target="b", name="b", status="idle"),
    }
    current = {
        "%input": PaneState(
            id="%input", target="a", name="a", status="needs_input"
        ),
        "%error": PaneState(id="%error", target="b", name="b", status="error"),
    }
    last_alert = {}
    alerts = collect_alerts(
        previous,
        current,
        last_alert,
        100,
        alert_on_needs_input=False,
        alert_on_error=True,
    )
    assert [pane.id for pane in alerts] == ["%error"]
    assert last_alert == {"%error": 100}


def test_hub_due_work_sends_one_digest_updates_state_and_publishes(tmp_path):
    hub = Hub(config(tmp_path))
    add_changed_agent(hub.agents.store)
    hub.agents.store.update_review_settings(
        interval_present=True, interval_minutes=5, now=100
    )
    sent = []
    hub.push.fire_review_digest = lambda: sent.append("digest")

    hub._process_review_schedule()
    assert sent == ["digest"]
    settings = hub.agents.store.get_review_settings()
    assert settings["last_digest_at"] is not None
    assert settings["next_due_at"] > time.time()
    assert any(
        event["event"]["kind"] == "review_due"
        for event in hub.agents._history
    )

    hub._process_review_schedule()
    assert sent == ["digest"]


def test_hub_empty_window_suppresses_digest_and_invalidation(tmp_path):
    hub = Hub(config(tmp_path))
    hub.agents.store.update_review_settings(
        interval_present=True, interval_minutes=5, now=100
    )
    sent = []
    hub.push.fire_review_digest = lambda: sent.append("digest")

    hub._process_review_schedule()
    assert sent == []
    settings = hub.agents.store.get_review_settings()
    assert settings["last_digest_at"] is None
    assert settings["next_due_at"] > time.time()
    assert not any(
        event["event"]["kind"] == "review_due"
        for event in hub.agents._history
    )


def test_hub_poll_uses_database_batching_policy(tmp_path, monkeypatch):
    hub = Hub(config(tmp_path))
    hub.agents.store.update_review_settings(
        interval_present=True,
        interval_minutes=30,
        urgent_pane_errors=True,
    )
    observed = []

    def collect(_previous, _current, **kwargs):
        observed.append(kwargs)
        return []

    monkeypatch.setattr("vmux.poller.tmux.list_panes", lambda: [])
    hub.push.collect = collect
    hub.push.fire = lambda _: None
    submitted = []
    hub.agents.submit = lambda observations: submitted.append(observations)
    hub._process_review_schedule = lambda **_: None
    asyncio.run(hub.poll_once())

    assert observed == [
        {"alert_on_needs_input": False, "alert_on_error": True}
    ]
    assert submitted == [[]]


def test_due_poll_ingests_current_observations_before_claiming_window(
    tmp_path, monkeypatch
):
    hub = Hub(config(tmp_path))
    hub.agents.store.update_review_settings(
        interval_present=True, interval_minutes=5, now=100
    )
    order = []

    async def process_now(observations):
        assert observations == []
        order.append("process_now")

    monkeypatch.setattr("vmux.poller.tmux.list_panes", lambda: [])
    hub.push.collect = lambda *_args, **_kwargs: []
    hub.push.fire = lambda _: None
    hub.agents.process_now = process_now
    hub.agents.submit = lambda _: order.append("submit")
    hub._process_review_schedule = lambda **_: order.append("claim")

    asyncio.run(hub.poll_once())

    assert order == ["process_now", "claim"]


def test_enabling_batching_preserves_legacy_error_bypass_unless_overridden(
    tmp_path
):
    service = AgentService(config(tmp_path))
    inherited = service.update_review_settings(
        interval_present=True,
        interval_minutes=30,
        urgent_pane_errors=None,
    )
    assert inherited["urgent_bypass"]["pane_errors"] is True

    other_cfg = config(tmp_path / "explicit")
    other = AgentService(other_cfg)
    explicit = other.update_review_settings(
        interval_present=True,
        interval_minutes=30,
        urgent_pane_errors=False,
    )
    assert explicit["urgent_bypass"]["pane_errors"] is False

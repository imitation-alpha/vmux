"""Focused tests for server-scheduled Review digest notifications."""

import asyncio
import json

from vmux.config import Config
from vmux.push import PushManager


def manager(tmp_path):
    cfg = Config(
        apns_key_path="key.p8",
        apns_key_id="KEY",
        apns_team_id="TEAM",
        apns_topic="dev.example.vmux",
    )
    cfg.push_store_path = str(tmp_path / "push.json")
    cfg.server_instance_id = "server-review-test"
    return PushManager(cfg)


def test_review_digest_payload_is_generic_and_privacy_minimized(tmp_path):
    payload = manager(tmp_path)._review_digest_payload()

    assert payload["aps"]["alert"] == {
        "title": "vmux",
        "body": "Your agent review is ready.",
    }
    assert payload["aps"]["thread-id"] == "vmux-agents"
    assert payload["aps"]["interruption-level"] == "active"
    assert payload["aps"]["category"] == "vmux.agent-review"
    assert payload["vmux"] == {
        "type": "review_due",
        "server_instance_id": "server-review-test",
    }

    encoded = json.dumps(payload)
    for forbidden in (
        "agent_id",
        "decision_id",
        "pane",
        "prompt",
        "question",
        "option",
        "transcript",
        "command",
        "path",
        "count",
    ):
        assert forbidden not in encoded


def test_review_digest_fans_out_to_every_registration(tmp_path, monkeypatch):
    push = manager(tmp_path)
    tokens = ["ab" * 32, "cd" * 32]
    push.registry.add(tokens[0], contextual=False)
    push.registry.add(tokens[1], contextual=True)
    monkeypatch.setattr(PushManager, "can_send", property(lambda self: True))
    sent = []

    async def capture(pairs):
        sent.extend(pairs)

    push._send_token_payloads = capture

    async def scenario():
        push.fire_review_digest()
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert [token for token, _ in sent] == tokens
    assert len({json.dumps(payload, sort_keys=True) for _, payload in sent}) == 1
    assert sent[0][1] == push._review_digest_payload()


def test_review_digest_is_best_effort_without_send_capability(tmp_path, monkeypatch):
    push = manager(tmp_path)
    called = False

    async def capture(_payloads):
        nonlocal called
        called = True

    push._send_payloads = capture
    monkeypatch.setattr(PushManager, "can_send", property(lambda self: False))

    push.fire_review_digest()

    assert called is False


def test_review_digest_is_best_effort_without_running_loop(tmp_path, monkeypatch):
    push = manager(tmp_path)
    push.registry.add("ab" * 32)
    monkeypatch.setattr(PushManager, "can_send", property(lambda self: True))

    push.fire_review_digest()

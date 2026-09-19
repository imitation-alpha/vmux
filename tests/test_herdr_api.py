"""Public monitor/respond contract for a selected Herdr provider."""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from vmux.config import Config
from vmux.poller import Hub
from vmux.server import create_app
from vmux.terminals.base import (
    CaptureResult,
    DiscoveryResult,
    EndpointCapabilities,
    EndpointRef,
    EndpointSnapshot,
    NativeAgentState,
    ProviderHealth,
    TerminalProvider,
    VerifiedEndpoint,
)

DIALOG = """\
╭─────────────────────────────────────╮
│ Do you want to continue?            │
│ ❯ 1. Yes                            │
│   2. No                             │
╰─────────────────────────────────────╯
"""


class ApiHerdr(TerminalProvider):
    name = "herdr"

    def __init__(self):
        self.health = ProviderHealth(status="ready", protocol=20, version="test")
        self.endpoint = EndpointSnapshot(
            ref=EndpointRef("herdr", "named", "w1:p1", "incarnation"),
            public_id="h:api-test",
            persistent_target="herdr:stable",
            hierarchy=(),
            command="claude",
            title="Claude",
            native_revision="12",
            native_agent=NativeAgentState(True, "claude", "Claude", "blocked", 4, True),
            capabilities=EndpointCapabilities(
                input="guarded_v1", keys=("Enter", "Escape", "C-c"), broadcast=False,
                native_agent_state=True,
            ),
            provider_metadata={"terminal_id": "t1", "workspace_id": "w1", "tab_id": "w1:t1"},
        )
        self.sent = []
        self.authoritative = True

    def probe(self):
        return self.health

    def discover(self):
        if self.authoritative:
            return DiscoveryResult(self.health, (self.endpoint,), True)
        degraded = ProviderHealth(status="degraded", last_error="timeout", protocol=20)
        return DiscoveryResult(degraded, (self.endpoint,), False)

    def capture(self, endpoint, *, lines):
        return CaptureResult(DIALOG, endpoint.native_revision)

    def resolve(self, public_id):
        return self.endpoint if public_id == self.endpoint.public_id else None

    def revalidate(self, public_id, expected):
        return VerifiedEndpoint(self.endpoint, DIALOG)

    def send_literal(self, endpoint, text):
        self.sent.append(("text", text))

    def send_key(self, endpoint, key):
        self.sent.append(("key", key))

    def send_menu_key(self, endpoint, key):
        self.sent.append(("menu", key))

    def capability(self):
        return {
            "version": 1, "provider": "herdr", "mode": "monitor_respond",
            "guarded_input": True, "native_agent_state": True,
            "creation": False, "deletion": False,
            "event_subscription": {"supported": False, "active": False, "minimum_protocol": 20},
            "health": self.health.to_dict(),
        }


def wait_for_pane(client):
    for _ in range(100):
        panes = client.get("/api/state").json()["panes"]
        if panes:
            return panes[0]
        time.sleep(0.01)
    raise AssertionError("Herdr pane was not polled")


def test_herdr_state_capabilities_and_guarded_api_are_additive():
    provider = ApiHerdr()
    cfg = Config(
        terminal_provider="herdr",
        herdr_session="named",
        include_shells=True,
        poll_interval=0.2,
    )
    app = create_app(cfg, provider=provider)

    with TestClient(app) as client:
        pane = wait_for_pane(client)
        assert pane["provider"] == "herdr"
        assert pane["status"] == "needs_input"
        assert pane["native_agent"]["status"] == "blocked"
        assert pane["capabilities"]["input"] == "guarded_v1"
        assert pane["capabilities"]["create"] is False
        assert pane["actionable"] is True
        assert pane["action_guard"]["endpoint_revision"] == "12"
        assert pane["menu"][0]["id"].startswith("o:")

        info = client.get("/api/config").json()["_info"]["capabilities"]
        assert info["terminal_provider_v1"]["provider"] == "herdr"
        assert info["tmux_create_v1"]["enabled"] is False
        assert info["agent_context_v1"]["enabled"] is False
        assert "unsupported" in info["agent_context_v1"]["degraded_reason"]

        legacy = client.post("/api/select", json={"id": pane["id"], "key": "1"})
        assert legacy.status_code == 409
        assert legacy.json()["detail"]["reason"] == "guarded_input_required"
        assert provider.sent == []

        guarded = client.post("/api/input", json={
            "id": pane["id"],
            "operation": "select",
            "option_id": pane["menu"][0]["id"],
            "expected": pane["action_guard"],
            "idempotency_key": "api-select-1",
        })
        assert guarded.status_code == 200
        assert guarded.json() == {"ok": True, "delivery": "accepted"}
        assert provider.sent == [("menu", "1")]


def test_failed_discovery_retains_stale_read_only_snapshot():
    provider = ApiHerdr()
    cfg = Config(terminal_provider="herdr", herdr_session="named", include_shells=True)
    hub = Hub(cfg, provider=provider)
    asyncio.run(hub.poll_once())
    before = hub.states[provider.endpoint.public_id]
    assert before.actionable is True

    provider.authoritative = False
    asyncio.run(hub.poll_once())

    retained = hub.states[provider.endpoint.public_id]
    assert retained.lines == before.lines
    assert retained.stale is True
    assert retained.actionable is False


def test_guarded_api_rejects_wrong_fields_and_agent_context_enable():
    provider = ApiHerdr()
    cfg = Config(terminal_provider="herdr", herdr_session="named", include_shells=True)
    app = create_app(cfg, provider=provider)

    with TestClient(app) as client:
        pane = wait_for_pane(client)
        bad = client.post("/api/input", json={
            "id": pane["id"],
            "operation": "key",
            "key": "Escape",
            "text": "must not be accepted",
            "expected": {"endpoint_revision": "12"},
            "idempotency_key": "bad-fields",
        })
        assert bad.status_code == 400
        assert provider.sent == []

        workspace = client.patch(
            "/api/config", json={"experimental_agent_workspace_enabled": True}
        )
        assert workspace.status_code == 400
        assert cfg.experimental_agent_workspace_enabled is False

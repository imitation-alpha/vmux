"""Public monitor/respond contract for a selected Herdr provider."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from vmux.config import Config, PaneOverride
from vmux.poller import Hub
from vmux.push import PushManager
from vmux.server import create_app
from vmux.terminals.base import (
    CaptureResult,
    DiscoveryResult,
    EndpointCapabilities,
    EndpointRef,
    EndpointSnapshot,
    HierarchyNode,
    NativeAgentState,
    ProviderError,
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


@pytest.mark.parametrize("operation,values", [
    ("key", {"key": "Enter"}),
    ("text", {"text": "continue", "enter": True}),
])
def test_native_blocked_display_enrichment_preserves_action_evidence(monkeypatch, operation, values):
    provider = ApiHerdr()
    evidence = "Awaiting your next instruction"
    monkeypatch.setattr(provider, "capture", lambda *a, **kw: CaptureResult(
        "Display history differs from detection", detection_text=evidence,
    ))
    monkeypatch.setattr(provider, "revalidate", lambda *a: VerifiedEndpoint(provider.endpoint, evidence))
    hub = Hub(Config(terminal_provider="herdr", herdr_session="named"), provider=provider)
    asyncio.run(hub.poll_once())
    state = hub.states[provider.endpoint.public_id]
    assert state.question == "This agent is waiting for input."
    assert state.status == "needs_input"
    assert state.lines == ["Display history differs from detection"]
    result = hub.actions.guarded_input(
        pane_id=state.id, operation=operation, expected=state.action_guard,
        idempotency_key="native-blocked", **values,
    )
    assert result["delivery"] == "accepted"
    assert provider.sent == ([("key", "Enter")] if operation == "key" else [("text", "continue"), ("key", "Enter")])


def test_native_blocked_does_not_override_terminal_error(monkeypatch):
    provider = ApiHerdr()
    monkeypatch.setattr(provider, "capture", lambda *a, **kw: CaptureResult("fatal: process failed"))
    hub = Hub(Config(terminal_provider="herdr", herdr_session="named"), provider=provider)
    asyncio.run(hub.poll_once())
    state = hub.states[provider.endpoint.public_id]
    assert state.status == "error"
    assert state.question is None


@pytest.mark.parametrize("evidence,status", [
    ("esc to interrupt", "working"),
    ("fatal: process failed", "error"),
    (DIALOG.replace("continue?", "apply changes?"), "needs_input"),
])
def test_failed_capture_preserves_interpretation_without_false_push(monkeypatch, evidence, status):
    provider = ApiHerdr()
    provider.endpoint = replace(
        provider.endpoint,
        native_agent=replace(provider.endpoint.native_agent, status="working"),
    )
    monkeypatch.setattr(provider, "capture", lambda *a, **kw: CaptureResult(DIALOG, detection_text=evidence))
    monkeypatch.setattr(PushManager, "configured", property(lambda self: True))
    monkeypatch.setattr(PushManager, "available", property(lambda self: True))
    hub = Hub(Config(terminal_provider="herdr", herdr_session="named"), provider=provider)
    alerts = []
    monkeypatch.setattr(hub.push, "fire", lambda batch: alerts.extend(batch))
    asyncio.run(hub.poll_once())
    before = hub.states[provider.endpoint.public_id].to_dict()
    assert before["status"] == status
    alerts.clear()

    def fail_capture(*args, **kwargs):
        raise ProviderError("capture failed", category="timeout")

    monkeypatch.setattr(provider, "capture", fail_capture)
    provider.endpoint = replace(
        provider.endpoint,
        native_agent=replace(provider.endpoint.native_agent, status="blocked"),
    )
    for _ in range(2):
        asyncio.run(hub.poll_once())
        retained = hub.snapshot()["panes"][0]
        assert retained == {**before, "stale": True, "actionable": False, "changed": False}
        assert alerts == []

    monkeypatch.setattr(provider, "capture", lambda *a, **kw: CaptureResult(DIALOG, detection_text=evidence))
    provider.endpoint = replace(
        provider.endpoint,
        native_agent=replace(provider.endpoint.native_agent, status="working"),
    )
    asyncio.run(hub.poll_once())
    recovered = hub.states[provider.endpoint.public_id]
    assert recovered.stale is False
    assert recovered.actionable is True
    assert recovered.status == status
    assert recovered.action_guard == before["action_guard"]
    assert alerts == []


@pytest.mark.parametrize("mode,expected", [
    ("command", "codex"),
    ("target", "herdr:stable"),
    ("title", "Review"),
    ("pane", "Worker"),
    ("window", "Tab"),
    ("window_pane", "Tab:Worker"),
    ("session_pane", "Project:Worker"),
    ("session_window_pane", "Project:Tab:Worker"),
    ("smart", "Suggested name"),
])
@pytest.mark.parametrize("override", [None, "Pinned"])
def test_herdr_naming_modes_preserve_selected_source(monkeypatch, mode, expected, override):
    provider = ApiHerdr()
    provider.endpoint = replace(
        provider.endpoint,
        command="/usr/bin/codex",
        title="✳ Review",
        hierarchy=(
            HierarchyNode("session", "hs:1", "agents"),
            HierarchyNode("workspace", "hw:1", "Project"),
            HierarchyNode("tab", "ht:1", "Tab"),
            HierarchyNode("pane", "hp:1", "Worker"),
        ),
    )
    cfg = Config(terminal_provider="herdr", herdr_session="named", naming_mode=mode)
    if override:
        cfg.overrides[provider.endpoint.persistent_target] = PaneOverride(
            target=provider.endpoint.persistent_target, name=override,
        )
    hub = Hub(cfg, provider=provider)
    monkeypatch.setattr(hub.namer, "name", lambda *a: "Suggested name")
    asyncio.run(hub.poll_once())
    assert hub.snapshot()["panes"][0]["name"] == (override or expected)

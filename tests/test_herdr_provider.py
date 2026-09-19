"""Herdr provider parsing, routing, and failure-safety coverage."""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from vmux.terminals.base import ProviderError
from vmux.terminals.herdr_provider import HERDR_KEY_MAP, HerdrProvider

SESSION = "vmux-test-herdr"


def status():
    return {
        "client": {"version": "0.8.2", "protocol": 20, "session": SESSION},
        "server": {
            "running": True,
            "compatible": True,
            "protocol": 20,
            "version": "0.8.2",
            "session": SESSION,
            "socket": "/tmp/vmux-herdr-test.sock",
        },
    }


def snapshot(*, revision=7, tab_id="w1:t1"):
    return {
        "id": "snapshot-1",
        "result": {
            "type": "session_snapshot",
            "snapshot": {
                "version": "0.8.2",
                "protocol": 20,
                "workspaces": [{
                    "workspace_id": "w1", "number": 1, "label": "Workspace", "focused": True,
                    "pane_count": 1, "tab_count": 1, "active_tab_id": "w1:t1", "agent_status": "blocked",
                }],
                "tabs": [{
                    "tab_id": "w1:t1", "workspace_id": "w1", "number": 2, "label": "Review",
                    "focused": True, "pane_count": 1, "agent_status": "blocked",
                }],
                "panes": [{
                    "pane_id": "w1:p1", "terminal_id": "terminal-1", "workspace_id": "w1",
                    "tab_id": tab_id, "focused": True, "agent_status": "blocked", "revision": revision,
                    "agent": "claude", "title": "Claude", "foreground_cwd": "/private/project",
                }],
                "layouts": [],
                "agents": [{
                    "terminal_id": "terminal-1", "pane_id": "w1:p1", "workspace_id": "w1",
                    "tab_id": tab_id, "focused": True, "revision": revision, "agent_status": "blocked",
                    "agent": "claude", "state_change_seq": 11, "interactive_ready": True,
                }],
                "focused_workspace_id": "w1", "focused_tab_id": "w1:t1", "focused_pane_id": "w1:p1",
            },
        },
    }


def install_fake_subprocess(monkeypatch, *, bad_snapshot=False):
    calls = []

    class Process:
        def __init__(self, stdout):
            self.stdout = io.BytesIO(stdout)
            self.stderr = io.BytesIO()
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        command = argv[1:-2]
        if command == ["status", "--json"]:
            value = status()
        elif command == ["session", "list", "--json"]:
            value = {"sessions": [{
                "name": SESSION, "running": True, "socket_path": "/tmp/vmux-herdr-test.sock",
            }]}
        elif command == ["api", "schema", "--json"]:
            value = {
                "requests": [
                    "session.snapshot", "pane.get", "pane.read", "pane.send_text",
                    "pane.send_keys", "events.subscribe",
                ],
                "events": ["pane.agent_status_changed"],
            }
        elif command == ["api", "snapshot"]:
            value = snapshot(tab_id="missing" if bad_snapshot else "w1:t1")
        elif command[:2] == ["pane", "read"]:
            return Process(b"line one\nline two\n")
        elif command[:2] in (["pane", "send-text"], ["pane", "send-keys"]):
            return Process(b"")
        else:  # pragma: no cover - makes unexpected authority expansion obvious
            raise AssertionError(command)
        return Process(json.dumps(value).encode())

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr("vmux.terminals.herdr_provider.HerdrEventSubscriber.validate_socket", lambda _path: True)
    return calls


def provider():
    return HerdrProvider(
        session=SESSION,
        binary="/bin/echo",
        events="off",
        server_instance_id="server-id",
    )


def test_probe_and_every_command_use_exact_trailing_session(monkeypatch):
    calls = install_fake_subprocess(monkeypatch)
    selected = provider()

    health = selected.probe()
    discovery = selected.discover()
    endpoint = discovery.endpoints[0]
    capture = selected.capture(endpoint, lines=1)
    selected.send_literal(endpoint, "--leading ' quote ☃")
    selected.send_key(endpoint, "Escape")

    assert health.status == "ready"
    assert capture.text == "line two"
    assert endpoint.public_id.startswith("h:")
    assert endpoint.persistent_target.startswith("herdr:")
    assert endpoint.native_agent.status == "blocked"
    assert [node.kind for node in endpoint.hierarchy] == ["session", "workspace", "tab", "pane"]
    assert endpoint.capabilities.input == "guarded_v1"
    assert tuple(endpoint.capabilities.keys) == tuple(HERDR_KEY_MAP)
    for argv, kwargs in calls:
        assert argv[-2:] == ["--session", SESSION]
        assert kwargs["shell"] is False
        assert kwargs["env"]["HERDR_SESSION"] == SESSION
    assert any(call[0][1:-2] == ["pane", "send-text", "w1:p1", "--leading ' quote ☃"] for call in calls)


def test_capture_does_not_trust_route_revision_for_terminal_output(monkeypatch):
    calls = install_fake_subprocess(monkeypatch)
    selected = provider()
    selected.probe()
    endpoint = selected.discover().endpoints[0]

    assert selected.capture(endpoint, lines=200) == selected.capture(endpoint, lines=200)

    reads = [argv for argv, _ in calls if argv[1:3] == ["pane", "read"]]
    assert len(reads) == 4
    assert "detection" in reads[1]
    assert "recent-unwrapped" in reads[0]
    assert reads[0][-2:] == ["--session", SESSION]


def test_invalid_hierarchy_is_non_authoritative_and_never_adopted(monkeypatch):
    calls = install_fake_subprocess(monkeypatch)
    selected = provider()
    selected.probe()
    assert selected.discover().authoritative is True

    monkeypatch.setattr(selected, "_snapshot", lambda: snapshot(tab_id="missing")["result"]["snapshot"])
    result = selected.discover()

    assert result.authoritative is False
    assert result.health.status == "degraded"
    assert selected.resolve("w1:p1") is None
    assert len(result.endpoints) == 1
    assert calls


def test_unknown_public_id_never_decodes_to_a_native_target(monkeypatch):
    install_fake_subprocess(monkeypatch)
    selected = provider()
    selected.probe()
    selected.discover()

    assert selected.resolve("w1:p1") is None
    assert selected.resolve("h:not-a-live-token") is None


def test_runner_bounds_output_and_classifies_uncertain_mutation_timeout(monkeypatch):
    class Process:
        def __init__(self, output=b"", timeout=False):
            self.stdout = io.BytesIO(output)
            self.stderr = io.BytesIO()
            self.returncode = 0
            self.timeout = timeout
            self.killed = False

        def wait(self, timeout=None):
            if self.timeout and not self.killed:
                raise subprocess.TimeoutExpired("herdr", timeout)
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    monkeypatch.setattr("vmux.terminals.herdr_provider.MAX_OUTPUT_BYTES", 8)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process(b"123456789"))
    selected = provider()
    with pytest.raises(ProviderError) as caught:
        selected._run(["status"])
    assert caught.value.category == "oversized"

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process(timeout=True))
    with pytest.raises(ProviderError) as caught:
        selected._run(["pane", "send-keys", "w1:p1", "enter"], timeout=0.01, mutation=True)
    assert caught.value.category == "delivery_unknown"


def test_bad_configuration_and_unverified_keys_fail_closed(monkeypatch):
    with pytest.raises(ProviderError, match="explicit"):
        HerdrProvider(session="", binary="/bin/echo")
    selected = provider()
    with pytest.raises(ProviderError) as caught:
        selected.send_key(snapshot, "Tab")  # type: ignore[arg-type]
    assert caught.value.category == "capability_unavailable"
    with pytest.raises(ProviderError) as caught:
        selected.send_literal(snapshot, "first\nsecond")  # type: ignore[arg-type]
    assert caught.value.category == "capability_unavailable"


@pytest.mark.parametrize("failure", ["socket", "schema", "session"])
def test_discovery_and_revalidation_require_successful_health_probe(monkeypatch, failure):
    calls = install_fake_subprocess(monkeypatch)
    selected = provider()
    original_json = selected._json
    healthy = True

    def response(args, **kwargs):
        value = original_json(args, **kwargs)
        if not healthy and failure == "schema" and args == ["api", "schema", "--json"]:
            return {"requests": []}
        if not healthy and failure == "session" and args == ["status", "--json"]:
            value["server"]["session"] = "other"
        return value

    monkeypatch.setattr(selected, "_json", response)
    monkeypatch.setattr(
        "vmux.terminals.herdr_provider.HerdrEventSubscriber.validate_socket",
        lambda path: healthy or failure != "socket",
    )
    healthy = False
    with pytest.raises(ProviderError):
        selected.probe()
    failed = selected.discover()
    assert failed.authoritative is False
    assert failed.health.status != "ready"
    assert failed.endpoints == ()
    assert not any(argv[1:3] == ["api", "snapshot"] for argv, _ in calls)

    healthy = True
    recovered = selected.discover()
    assert recovered.authoritative is True
    endpoint = recovered.endpoints[0]
    healthy = False
    with pytest.raises(ProviderError):
        selected.revalidate(endpoint.public_id, endpoint)
    retained = selected.discover()
    assert retained.authoritative is False
    assert retained.endpoints == (endpoint,)
    assert retained.health.status != "ready"
    assert not any(argv[1:3] in (["pane", "send-text"], ["pane", "send-keys"]) for argv, _ in calls)

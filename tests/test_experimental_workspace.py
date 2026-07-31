"""Opt-in Agent Workspace configuration and live runtime lifecycle."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vmux import server as server_module
from vmux.config import Config
from vmux.poller import Hub
from vmux.server import create_app


def runtime_config(tmp_path, *, enabled: bool = False) -> Config:
    cfg = Config(
        experimental_agent_workspace_enabled=enabled,
        agent_store_path=str(tmp_path / "vmux-agents.sqlite3"),
        agent_codex_home=str(tmp_path / "codex"),
        agent_claude_home=str(tmp_path / "claude"),
    )
    cfg.overlay_path = str(tmp_path / "vmux-settings.json")
    return cfg


def quiet_tmux(monkeypatch) -> None:
    monkeypatch.setattr("vmux.poller.tmux.list_panes", lambda: [])


def test_live_enable_disable_reenable_preserves_history_and_closes_socket(
    tmp_path, monkeypatch
):
    quiet_tmux(monkeypatch)
    cfg = runtime_config(tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        initial = client.get("/api/config").json()
        assert initial["experimental_agent_workspace_enabled"] is False
        assert initial["_info"]["capabilities"]["agent_context_v1"]["enabled"] is False
        invalid = client.patch(
            "/api/config",
            json={"experimental_agent_workspace_enabled": "true"},
        )
        assert invalid.status_code == 400
        assert initial == client.get("/api/config").json()
        for path in ("/api/agents", "/api/review", "/api/timeline", "/api/decisions"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["detail"] == "agent context is disabled"

        enabled = client.patch(
            "/api/config",
            json={"experimental_agent_workspace_enabled": True},
        )
        assert enabled.status_code == 200
        assert enabled.json()["_info"]["capabilities"]["agent_context_v1"]["enabled"] is True
        assert app.state.hub.agents.runtime_active is True

        store = app.state.hub.agents.store
        agent = store.upsert_session(
            "codex", "retained", "/private/runtime.jsonl", "/project", "v1"
        )
        assert client.get("/api/agents").json()["agents"][0]["id"] == agent["id"]

        with client.websocket_connect("/ws/agents") as websocket:
            assert websocket.receive_json()["type"] == "hello"
            disabled = client.patch(
                "/api/config",
                json={"experimental_agent_workspace_enabled": False},
            )
            assert disabled.status_code == 200
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == 1000

        assert app.state.hub.agents.runtime_active is False
        assert client.get("/api/agents").status_code == 503
        assert (tmp_path / "vmux-agents.sqlite3").exists()

        restored = client.patch(
            "/api/config",
            json={"experimental_agent_workspace_enabled": True},
        )
        assert restored.status_code == 200
        agents = client.get("/api/agents").json()["agents"]
        assert [item["id"] for item in agents] == [agent["id"]]


def test_failed_enable_and_persistence_failure_roll_back_runtime(tmp_path, monkeypatch):
    quiet_tmux(monkeypatch)
    cfg = runtime_config(tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        def fail_open():
            raise OSError("observer storage unavailable")

        monkeypatch.setattr(app.state.hub.agents.store, "open", fail_open)
        failed_start = client.patch(
            "/api/config",
            json={"experimental_agent_workspace_enabled": True},
        )
        assert failed_start.status_code == 500
        assert "could not enable experimental agent workspace" in failed_start.json()["detail"]
        assert cfg.experimental_agent_workspace_enabled is False
        assert app.state.hub.agents.runtime_active is False

        monkeypatch.undo()
        quiet_tmux(monkeypatch)

        def fail_save(_cfg):
            raise OSError("overlay is read-only")

        monkeypatch.setattr(server_module, "save_overlay", fail_save)
        failed_persist = client.patch(
            "/api/config",
            json={"experimental_agent_workspace_enabled": True},
        )
        assert failed_persist.status_code == 500
        assert "could not persist settings" in failed_persist.json()["detail"]
        assert cfg.experimental_agent_workspace_enabled is False
        assert app.state.hub.agents.runtime_active is False


def test_successful_switch_change_invalidates_other_pwa_config_clients(
    tmp_path, monkeypatch
):
    quiet_tmux(monkeypatch)
    app = create_app(runtime_config(tmp_path))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json()["type"] == "hello"
            assert websocket.receive_json()["type"] == "state"
            response = client.patch(
                "/api/config",
                json={"experimental_agent_workspace_enabled": True},
            )
            assert response.status_code == 200
            messages = [websocket.receive_json() for _ in range(3)]
            assert "config_changed" in {message["type"] for message in messages}


def test_failed_disable_persistence_restores_enabled_runtime(tmp_path, monkeypatch):
    quiet_tmux(monkeypatch)
    cfg = runtime_config(tmp_path, enabled=True)
    app = create_app(cfg)

    with TestClient(app) as client:
        assert app.state.hub.agents.runtime_active is True

        def fail_save(_cfg):
            raise OSError("overlay is read-only")

        monkeypatch.setattr(server_module, "save_overlay", fail_save)
        response = client.patch(
            "/api/config",
            json={"experimental_agent_workspace_enabled": False},
        )
        assert response.status_code == 500
        assert cfg.experimental_agent_workspace_enabled is True
        assert app.state.hub.agents.runtime_active is True
        assert client.get("/api/agents").status_code == 200


def test_disabled_poll_does_not_construct_observations_or_open_storage(
    tmp_path, monkeypatch
):
    cfg = runtime_config(tmp_path)
    hub = Hub(cfg)
    monkeypatch.setattr(
        "vmux.poller.tmux.list_panes",
        lambda: [{
            "id": "%1",
            "target": "work:1.0",
            "cmd": "codex",
            "title": "Codex",
            "path": str(tmp_path),
            "pid": "123",
            "created": 1,
            "window": "work",
        }],
    )
    monkeypatch.setattr("vmux.poller.tmux.capture", lambda *_args: "ready")

    def forbidden_observation(*_args, **_kwargs):
        raise AssertionError("disabled workspace constructed an observation")

    monkeypatch.setattr("vmux.poller.PaneObservation", forbidden_observation)
    monkeypatch.setattr(hub.agents, "submit", forbidden_observation)
    monkeypatch.setattr(hub, "_process_review_schedule", forbidden_observation)
    asyncio.run(hub.poll_once())

    assert [pane["id"] for pane in hub.snapshot()["panes"]] == ["%1"]
    assert not (tmp_path / "vmux-agents.sqlite3").exists()

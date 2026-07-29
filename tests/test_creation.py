"""Filesystem authorization, runtime presets, and creation API contracts."""

from __future__ import annotations

import os
import threading

import pytest
from fastapi.testclient import TestClient

from vmux import tmux
from vmux.config import Config, CreationRoot
from vmux.creation import CreationProblem, CreationService
from vmux.server import create_app


def configured(root, *, token="", runtimes=None):
    return Config(
        token=token,
        creation_enabled=True,
        creation_roots=[CreationRoot("Workspace", str(root))],
        creation_runtimes=runtimes or {},
    )


def test_creation_disabled_by_default_and_capability_is_additive(monkeypatch):
    monkeypatch.setattr(tmux, "available", lambda: True)
    app = create_app(Config())
    client = TestClient(app)

    info = client.get("/api/tmux/creation")
    capability = client.get("/api/config").json()["_info"]["capabilities"]["tmux_create_v1"]

    assert info.status_code == 200
    assert info.json()["enabled"] is False
    assert "disabled" in info.json()["reason"].lower()
    assert capability == {
        "version": 1,
        "supported": True,
        "enabled": False,
        "reason": "Creation is disabled in server configuration.",
    }


def test_creation_endpoints_require_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(tmux, "available", lambda: True)
    client = TestClient(create_app(configured(tmp_path, token="secret")))

    for method, path in [
        ("get", "/api/tmux/creation"),
        ("get", "/api/tmux/directories?path=" + str(tmp_path)),
        ("post", "/api/tmux/create"),
    ]:
        response = client.post(path, json={}) if method == "post" else client.get(path)
        assert response.status_code == 401


def test_directory_browser_canonicalizes_bounds_and_filters_symlink_escape(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "inside-link").symlink_to(child, target_is_directory=True)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "file.txt").write_text("not a directory")
    monkeypatch.setattr(tmux, "available", lambda: True)
    service = CreationService(configured(root))

    payload = service.browse(str(root / "."))

    assert payload["path"] == str(root.resolve())
    assert payload["parent"] is None
    assert payload["root"] == {"label": "Workspace", "path": str(root.resolve())}
    assert payload["directories"] == [{"name": "child", "path": str(child.resolve())}]
    assert payload["truncated"] is False
    assert service.browse(str(child))["parent"] == str(root.resolve())

    with pytest.raises(CreationProblem) as escaped:
        service.resolve_directory(str(root / "escape"))
    assert escaped.value.status_code == 403
    with pytest.raises(CreationProblem) as traversed:
        service.resolve_directory(str(root / ".." / "outside"))
    assert traversed.value.status_code == 403
    with pytest.raises(CreationProblem) as missing:
        service.resolve_directory(str(root / "gone"))
    assert missing.value.status_code == 404
    with pytest.raises(CreationProblem) as relative:
        service.resolve_directory("child")
    assert relative.value.status_code == 400


def test_directory_browser_caps_results_at_500(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    for index in range(503):
        (root / ("dir-%03d" % index)).mkdir()
    monkeypatch.setattr(tmux, "available", lambda: True)

    payload = CreationService(configured(root)).browse(str(root))

    assert len(payload["directories"]) == 500
    assert payload["truncated"] is True
    assert payload["directories"][0]["name"] == "dir-000"


def test_unreadable_directory_is_rejected_and_omitted(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "private"
    child.mkdir()
    monkeypatch.setattr(tmux, "available", lambda: True)
    service = CreationService(configured(root))
    real_access = os.access
    monkeypatch.setattr(
        "vmux.creation.os.access",
        lambda path, mode: False if path == str(child) else real_access(path, mode),
    )

    assert service.browse(str(root))["directories"] == []
    with pytest.raises(CreationProblem) as unavailable:
        service.resolve_directory(str(child))
    assert unavailable.value.status_code == 404


def test_tilde_paths_and_recent_directories_are_filtered_and_deduplicated(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "project"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(tmux, "available", lambda: True)
    monkeypatch.setattr(tmux, "list_panes", lambda: [
        {"path": str(child)},
        {"path": str(child / ".")},
        {"path": str(outside)},
        {"path": str(root / "missing")},
    ])
    service = CreationService(configured(root))

    assert service.resolve_directory("~/root/project")[0] == str(child.resolve())
    assert service.recent_directories() == [{
        "path": str(child.resolve()),
        "name": "project",
        "root_label": "Workspace",
    }]


def test_runtime_availability_and_arbitrary_runtime_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(tmux, "available", lambda: True)
    monkeypatch.setattr("vmux.creation.shutil.which", lambda command: "/bin/" + command if command == "codex" else None)
    service = CreationService(configured(tmp_path, runtimes={
        "codex": ["codex"],
        "claude": ["missing-claude"],
    }))

    values = {item["id"]: item for item in service.runtime_info()}
    assert values["shell"]["available"] is True
    assert values["codex"]["available"] is True
    assert values["claude"]["available"] is False
    assert values["agy"]["available"] is False
    assert values["agy"]["label"] == "Antigravity"
    assert values["grok"]["label"] == "Grok Build"
    assert values["grok"]["available"] is False

    with pytest.raises(CreationProblem) as error:
        service.create({"type": "session", "cwd": str(tmp_path), "runtime": "evil"})
    assert error.value.status_code == 400

    with pytest.raises(CreationProblem) as unavailable:
        service.create({"type": "session", "cwd": str(tmp_path), "runtime": "claude"})
    assert unavailable.value.status_code == 503


def test_create_all_types_and_response_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(tmux, "available", lambda: True)
    monkeypatch.setattr("vmux.creation.shutil.which", lambda command: "/bin/" + command)
    monkeypatch.setattr(tmux, "session_names", lambda: [])
    monkeypatch.setattr(tmux, "window_names", lambda session: [])
    monkeypatch.setattr(tmux, "session_exists", lambda session: session == "work")
    monkeypatch.setattr(tmux, "exists", lambda pane: pane in {"%4", "%10", "%11", "%12"})
    calls = []
    monkeypatch.setattr(tmux, "create_session", lambda *args: calls.append(("session", args)) or {"pane_id": "%10", "target": "api:0.0"})
    monkeypatch.setattr(tmux, "create_window", lambda *args: calls.append(("window", args)) or {"pane_id": "%11", "target": "work:2.0"})
    monkeypatch.setattr(tmux, "create_pane", lambda *args: calls.append(("pane", args)) or {"pane_id": "%12", "target": "work:2.1"})
    client = TestClient(create_app(configured(tmp_path, runtimes={
        "codex": ["codex"], "claude": ["claude"], "agy": ["agy"], "grok": ["grok"],
    })))

    session = client.post("/api/tmux/create", json={
        "type": "session", "cwd": str(tmp_path), "runtime": "codex", "name": "api",
    })
    window = client.post("/api/tmux/create", json={
        "type": "window", "parent_session": "work", "cwd": str(tmp_path),
        "runtime": "shell", "name": "api",
    })
    pane = client.post("/api/tmux/create", json={
        "type": "pane", "parent_pane_id": "%4", "cwd": str(tmp_path),
        "runtime": "claude", "split": "stacked", "size_percent": 65,
    })
    grok = client.post("/api/tmux/create", json={
        "type": "session", "cwd": str(tmp_path), "runtime": "grok", "name": "grok-build",
    })

    assert session.status_code == window.status_code == pane.status_code == grok.status_code == 201
    assert session.json() == {"pane_id": "%10", "target": "api:0.0"}
    assert window.json() == {"pane_id": "%11", "target": "work:2.0"}
    assert pane.json() == {"pane_id": "%12", "target": "work:2.1"}
    assert grok.json() == {"pane_id": "%10", "target": "api:0.0"}
    assert calls == [
        ("session", ("api", str(tmp_path), ["codex"])),
        ("window", ("work", "api", str(tmp_path), None)),
        ("pane", ("%4", str(tmp_path), "stacked", 65, ["claude"])),
        ("session", ("grok-build", str(tmp_path), ["grok"])),
    ]


def test_parent_disappearance_name_conflict_and_immediate_exit_are_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(tmux, "available", lambda: True)
    service = CreationService(configured(tmp_path))

    monkeypatch.setattr(tmux, "session_exists", lambda name: False)
    with pytest.raises(CreationProblem) as vanished:
        service.create({
            "type": "window", "parent_session": "gone", "cwd": str(tmp_path),
            "runtime": "shell", "name": "api",
        })
    assert (vanished.value.status_code, vanished.value.detail) == (
        404, "The parent session is no longer available.",
    )

    monkeypatch.setattr(tmux, "exists", lambda pane: False)
    with pytest.raises(CreationProblem) as pane_vanished:
        service.create({
            "type": "pane", "parent_pane_id": "%4", "cwd": str(tmp_path),
            "runtime": "shell", "split": "side_by_side", "size_percent": 50,
        })
    assert (pane_vanished.value.status_code, pane_vanished.value.detail) == (
        404, "The parent pane is no longer available.",
    )

    monkeypatch.setattr(tmux, "session_names", lambda: ["api"])
    with pytest.raises(CreationProblem) as conflict:
        service.create({"type": "session", "cwd": str(tmp_path), "runtime": "shell", "name": "api"})
    assert conflict.value.status_code == 409

    monkeypatch.setattr(tmux, "session_names", lambda: [])
    monkeypatch.setattr(tmux, "create_session", lambda *args: {"pane_id": "%99", "target": "root:0.0"})
    monkeypatch.setattr(tmux, "exists", lambda pane: False)
    with pytest.raises(CreationProblem) as exited:
        service.create({"type": "session", "cwd": str(tmp_path), "runtime": "shell", "name": None})
    assert exited.value.status_code == 503
    assert str(tmp_path) not in exited.value.detail


def test_automatic_names_suffix_serially_under_concurrency(tmp_path, monkeypatch):
    root = tmp_path / "my project"
    root.mkdir()
    monkeypatch.setattr(tmux, "available", lambda: True)
    names = []
    names_lock = threading.Lock()
    monkeypatch.setattr(tmux, "session_names", lambda: list(names))

    def create(name, cwd, command):
        with names_lock:
            names.append(name)
            index = len(names)
        return {"pane_id": "%%%d" % index, "target": "%s:0.0" % name}

    monkeypatch.setattr(tmux, "create_session", create)
    monkeypatch.setattr(tmux, "exists", lambda pane: True)
    service = CreationService(configured(root))
    results = []

    def run():
        results.append(service.create({
            "type": "session", "cwd": str(root), "runtime": "shell", "name": None,
        }))

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert names == ["my-project", "my-project-2", "my-project-3"]
    assert len(results) == 3


@pytest.mark.parametrize("payload", [
    {},
    {"type": "session", "cwd": "/tmp", "runtime": "shell", "name": "bad name"},
    {"type": "pane", "cwd": "/tmp", "runtime": "shell", "parent_pane_id": "%1", "size_percent": 9},
    {"type": "pane", "cwd": "/tmp", "runtime": "shell", "parent_pane_id": "1"},
    {"type": "session", "cwd": "/tmp", "runtime": "shell", "command": ["sh"]},
])
def test_invalid_requests_return_400(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(tmux, "available", lambda: True)
    response = TestClient(create_app(configured(tmp_path))).post("/api/tmux/create", json=payload)
    assert response.status_code == 400

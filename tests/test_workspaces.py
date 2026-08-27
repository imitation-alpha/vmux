"""Repository/worktree identity resolution and public contract tests."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest

from vmux.config import Config, CreationRoot
from vmux.creation import CreationProblem, CreationService
from vmux.models import PaneState
from vmux.poller import Hub
from vmux.workspaces import WorkspaceResolver


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "vmux@example.test")
    _git(path, "config", "user.name", "vmux tests")
    (path / "README").write_text("root\n")
    _git(path, "add", "README")
    _git(path, "commit", "-qm", "initial")
    return path


def test_resolver_groups_nested_symlink_and_linked_worktrees(tmp_path):
    repo = _repo(tmp_path / "repo")
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    linked = tmp_path / "repo-release"
    _git(repo, "worktree", "add", "-qb", "release/1.0", str(linked))
    resolver = WorkspaceResolver([str(tmp_path)])

    values = asyncio.run(resolver.resolve_many([str(nested), str(alias), str(linked)]))

    assert list(values) == [str(nested.resolve()), str(alias.resolve()), str(linked.resolve())]
    primary = values[str(nested.resolve())]
    symlinked = values[str(alias.resolve())]
    release = values[str(linked.resolve())]
    assert primary is not None and symlinked == primary
    assert release is not None
    assert primary.workspace_id == release.workspace_id
    assert primary.worktree_id != release.worktree_id
    assert primary.is_primary is True and release.is_primary is False
    assert release.branch == "release/1.0" and release.detached_commit is None
    assert primary.launchable is True and release.launchable is True
    encoded = json.dumps([value.to_dict() for value in values.values() if value])
    assert str(repo.resolve()) not in encoded
    assert str(linked.resolve()) not in encoded


def test_resolver_detached_submodule_absence_and_cache_expiry(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--detach", commit)
    outside = tmp_path / "outside"
    resolver = WorkspaceResolver([str(outside)], cache_ttl=0.2)

    detached = resolver.resolve(str(repo))
    assert detached is not None
    assert detached.branch is None
    assert detached.detached_commit == commit[:8]
    assert detached.worktree_name == repo.name
    assert detached.launchable is False
    assert detached.launch_unavailable_reason

    non_git = tmp_path / "plain"
    non_git.mkdir()
    assert resolver.resolve(str(non_git)) is None

    resolver.resolve(str(repo))
    calls = 0
    original = resolver._inspect

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(resolver, "_inspect", counted)
    assert resolver.resolve(str(repo)) is not None
    assert resolver.resolve(str(repo)) is not None
    assert calls == 0  # existing cached value
    time.sleep(0.22)
    assert resolver.resolve(str(repo)) is not None
    assert calls == 1


def test_resolver_failure_timeout_vanished_and_id_stability(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    first = WorkspaceResolver([str(tmp_path)]).resolve(str(repo))
    second = WorkspaceResolver([str(tmp_path)]).resolve(str(repo / "."))
    assert first is not None and second is not None
    assert (first.workspace_id, first.worktree_id) == (second.workspace_id, second.worktree_id)

    resolver = WorkspaceResolver([str(tmp_path)])
    monkeypatch.setattr(resolver, "_run_git", lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 1)))
    assert resolver.resolve(str(repo)) is None
    assert WorkspaceResolver([str(tmp_path)], git_executable="/definitely/missing/git").resolve(str(repo)) is None
    vanished = tmp_path / "vanished"
    vanished.mkdir()
    vanished.rmdir()
    assert WorkspaceResolver([str(tmp_path)]).resolve(str(vanished)) is None



def test_linked_worktree_registry_launches_by_opaque_id(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    linked = tmp_path / "repo-feature"
    _git(repo, "worktree", "add", "-qb", "feature", str(linked))
    resolver = WorkspaceResolver([str(tmp_path)])
    values = asyncio.run(resolver.resolve_active([str(repo), str(linked)]))
    identity = values[str(linked.resolve())].identity
    calls = []
    service = CreationService(
        Config(creation_enabled=True, creation_roots=[CreationRoot("Root", str(tmp_path))]),
        workspace_resolver=resolver,
    )
    monkeypatch.setattr("vmux.creation.tmux.available", lambda: True)
    monkeypatch.setattr("vmux.creation.tmux.session_names", lambda: [])
    monkeypatch.setattr("vmux.creation.tmux.create_session", lambda name, cwd, command: calls.append(cwd) or {"pane_id": "%9", "target": "x:0.0"})
    monkeypatch.setattr("vmux.creation.tmux.exists", lambda pane: True)

    service.create({"type": "session", "worktree_id": identity.worktree_id, "runtime": "shell"})

    assert calls == [str(linked.resolve())]

def test_pane_contract_adds_nullable_workspace_without_losing_legacy_fields():
    pane = PaneState(id="%1", target="work:0.0", name="agent")
    payload = pane.to_dict()
    legacy = {
        "id", "target", "name", "kind", "status", "title", "question", "menu",
        "preview", "lines", "updated", "changed", "window", "starred",
        "interacted", "lifecycle",
    }
    assert legacy <= set(payload)
    assert payload["workspace"] is None


def test_creation_accepts_exactly_one_of_cwd_or_active_worktree_id(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    resolver = WorkspaceResolver([str(tmp_path)])
    identity = resolver.resolve(str(repo))
    assert identity is not None
    resolver.replace_active([identity])
    service = CreationService(
        Config(creation_enabled=True, creation_roots=[CreationRoot("Root", str(tmp_path))]),
        workspace_resolver=resolver,
    )
    monkeypatch.setattr("vmux.creation.tmux.available", lambda: True)
    monkeypatch.setattr("vmux.creation.tmux.session_names", lambda: [])
    monkeypatch.setattr("vmux.creation.tmux.create_session", lambda name, cwd, command: {"pane_id": "%9", "target": "x:0.0"})
    monkeypatch.setattr("vmux.creation.tmux.exists", lambda pane: True)

    result = service.create({"type": "session", "worktree_id": identity.worktree_id, "runtime": "shell"})
    assert result == {"pane_id": "%9", "target": "x:0.0"}

    with pytest.raises(CreationProblem) as both:
        service.create({"type": "session", "cwd": str(repo), "worktree_id": identity.worktree_id, "runtime": "shell"})
    assert both.value.status_code == 400
    with pytest.raises(CreationProblem) as neither:
        service.create({"type": "session", "runtime": "shell"})
    assert neither.value.status_code == 400
    with pytest.raises(CreationProblem) as stale:
        service.create({"type": "session", "worktree_id": "wt_stale", "runtime": "shell"})
    assert stale.value.status_code == 404

    outside_resolver = WorkspaceResolver([str(tmp_path / "other")])
    outside_identity = outside_resolver.resolve(str(repo))
    assert outside_identity is not None and outside_identity.launchable is False
    outside_resolver.replace_active([outside_identity])
    outside_service = CreationService(
        Config(creation_enabled=True, creation_roots=[CreationRoot("Root", str(tmp_path))]),
        workspace_resolver=outside_resolver,
    )
    with pytest.raises(CreationProblem) as forbidden:
        outside_service.create({"type": "session", "worktree_id": outside_identity.worktree_id, "runtime": "shell"})
    assert forbidden.value.status_code == 403


def test_hub_applies_workspace_to_panes_agents_and_active_registry(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    cfg = Config(
        include_shells=True,
        creation_enabled=True,
        creation_roots=[CreationRoot("Root", str(tmp_path))],
    )
    hub = Hub(cfg)
    monkeypatch.setattr("vmux.poller.tmux.list_panes", lambda: [{
        "id": "%1", "target": "work:0.0", "cmd": "zsh", "title": "shell",
        "window": "work", "path": str(repo), "pid": "123", "window_id": "@1",
    }])
    monkeypatch.setattr("vmux.poller.tmux.capture", lambda pane, lines: "prompt")

    asyncio.run(hub.poll_once())

    identity = hub.states["%1"].workspace
    assert identity is not None
    assert identity["workspace_name"] == "repo"
    assert hub.workspaces.active_path(identity["worktree_id"]) == str(repo.resolve())
    assert hub.creation_workspace_resolver is hub.workspaces


def test_agent_service_decorates_active_agent_from_private_source_cwd(tmp_path):
    repo = _repo(tmp_path / "repo")
    cfg = Config(
        experimental_agent_workspace_enabled=True,
        creation_roots=[CreationRoot("Root", str(tmp_path))],
        agent_store_path=str(tmp_path / "agents.sqlite3"),
    )
    resolver = WorkspaceResolver(cfg.creation_roots)
    from vmux.agents.service import AgentService
    service = AgentService(cfg, workspace_resolver=resolver)
    stored = service.store.upsert_session("codex", "native", "/tmp/log", str(repo), "v1")

    public = service.get_agent(stored["id"])

    assert public["workspace"] is not None
    assert public["workspace"]["workspace_name"] == "repo"
    assert str(repo.resolve()) not in json.dumps(public)


def test_workspaces_capability_is_additive_and_reports_git_unavailability(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vmux.server import create_app
    app = create_app(Config())
    capability = TestClient(app).get("/api/config").json()["_info"]["capabilities"]["workspaces_v1"]
    assert capability["version"] == 1
    assert capability["supported"] is True
    assert set(capability) == {"version", "supported", "enabled", "reason"}


def test_agent_only_worktree_is_registered_for_launch(tmp_path):
    repo = _repo(tmp_path / "repo")
    resolver = WorkspaceResolver([str(tmp_path)])
    identity = resolver.resolve(str(repo))
    assert identity is not None
    resolver.refresh_active([str(repo)])
    assert resolver.active_path(identity.worktree_id) == str(repo.resolve())


def test_resolver_single_flight_deduplicates_concurrent_canonical_paths(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    nested = repo / "nested"
    nested.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    resolver = WorkspaceResolver([str(tmp_path)], cache_ttl=0)
    calls = 0
    original = resolver._inspect

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(resolver, "_inspect", counted)
    values = asyncio.run(resolver.resolve_many([str(repo), str(alias), str(repo)]))

    assert calls == 1
    assert list(values) == [str(repo.resolve())]
    assert values[str(repo.resolve())] is not None


def test_agent_registry_expires_completed_agents_and_keeps_active_agents(tmp_path):
    repo = _repo(tmp_path / "repo")
    cfg = Config(
        experimental_agent_workspace_enabled=True,
        creation_roots=[CreationRoot("Root", str(tmp_path))],
        agent_store_path=str(tmp_path / "agents.sqlite3"),
    )
    resolver = WorkspaceResolver(cfg.creation_roots)
    from vmux.agents.service import AgentService
    service = AgentService(cfg, workspace_resolver=resolver)
    active = service.store.upsert_session("codex", "active", "/tmp/active", str(repo), "v1")
    completed = service.store.upsert_session("claude", "completed", "/tmp/completed", str(repo), "v1")
    context = dict(completed["context"])
    context["lifecycle"] = "completed"
    context["last_updated"] = time.time()
    service.store.apply_projection(completed["id"], context, [], [], [])

    agents, _ = service.list_agents()
    identity = next(agent["workspace"] for agent in agents if agent["id"] == active["id"] )
    assert resolver.active_path(identity["worktree_id"]) == str(repo.resolve())

    active_context = dict(service.store.get_agent(active["id"])["context"])
    active_context["lifecycle"] = "offline"
    active_context["last_updated"] = time.time()
    service.store.apply_projection(active["id"], active_context, [], [], [])
    service.list_agents()
    assert resolver.active_path(identity["worktree_id"]) is None


def test_invalid_git_executable_disables_capability(tmp_path):
    resolver = WorkspaceResolver([], git_executable=str(tmp_path / "missing-git"))
    assert resolver.capability() == {
        "version": 1, "supported": True, "enabled": False,
        "reason": "Git is unavailable on this server.",
    }


def test_creation_unavailable_reason_is_part_of_workspace_identity(tmp_path):
    repo = _repo(tmp_path / "repo")
    resolver = WorkspaceResolver(
        [str(tmp_path)], creation_enabled=False,
        creation_unavailable_reason="Creation is disabled in server configuration.",
    )
    identity = resolver.resolve(str(repo))
    assert identity is not None
    assert identity.launchable is False
    assert identity.launch_unavailable_reason == "Creation is disabled in server configuration."


def test_agent_registry_refresh_uses_all_pages_not_only_requested_page(tmp_path):
    first_repo = _repo(tmp_path / "first")
    second_repo = _repo(tmp_path / "second")
    cfg = Config(
        experimental_agent_workspace_enabled=True,
        creation_enabled=True,
        creation_roots=[CreationRoot("Root", str(tmp_path))],
        agent_store_path=str(tmp_path / "agents.sqlite3"),
    )
    resolver = WorkspaceResolver(cfg.creation_roots)
    from vmux.agents.service import AgentService
    service = AgentService(cfg, workspace_resolver=resolver)
    first = service.store.upsert_session("codex", "first", "/tmp/first", str(first_repo), "v1")
    second = service.store.upsert_session("claude", "second", "/tmp/second", str(second_repo), "v1")

    page, next_cursor = service.list_agents(limit=1)

    assert len(page) == 1 and next_cursor is not None
    identities = {
        agent_id: service.store.get_agent(agent_id, internal=True)["_source_cwd"]
        for agent_id in (first["id"], second["id"])
    }
    for agent_id, cwd in identities.items():
        identity = resolver.resolve(cwd)
        assert identity is not None, agent_id
        assert resolver.active_path(identity.worktree_id) == str(Path(cwd).resolve())


def test_review_nested_public_agent_includes_workspace_identity(tmp_path):
    repo = _repo(tmp_path / "repo")
    cfg = Config(
        experimental_agent_workspace_enabled=True,
        creation_enabled=True,
        creation_roots=[CreationRoot("Root", str(tmp_path))],
        agent_store_path=str(tmp_path / "agents.sqlite3"),
    )
    resolver = WorkspaceResolver(cfg.creation_roots)
    from vmux.agents.service import AgentService
    service = AgentService(cfg, workspace_resolver=resolver)
    agent = service.store.upsert_session("codex", "review", "/tmp/review", str(repo), "v1")
    context = dict(agent["context"])
    context.update({"goal": "Review work", "lifecycle": "working", "last_updated": time.time()})
    service.store.apply_projection(agent["id"], context, [], [], [])

    payload = service.review_payload([])

    assert len(payload["groups"]) == 1
    nested = payload["groups"][0]["agent"]
    assert nested["workspace"]["workspace_name"] == "repo"
    assert str(repo.resolve()) not in json.dumps(nested)

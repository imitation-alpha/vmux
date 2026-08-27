"""Safe, cached discovery of active Git repositories and worktrees.

Only opaque identities and user-facing repository/branch names cross the public
API. Canonical paths remain private inside the resolver registry.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional, Sequence, Tuple

WORKSPACE_VERSION = 1
DEFAULT_TIMEOUT = 1.0
DEFAULT_CACHE_TTL = 15.0


@dataclass(frozen=True)
class WorkspaceIdentity:
    workspace_id: str
    workspace_name: str
    worktree_id: str
    worktree_name: str
    branch: Optional[str]
    detached_commit: Optional[str]
    is_primary: bool
    launchable: bool
    launch_unavailable_reason: Optional[str]

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "worktree_id": self.worktree_id,
            "worktree_name": self.worktree_name,
            "branch": self.branch,
            "detached_commit": self.detached_commit,
            "is_primary": self.is_primary,
            "launchable": self.launchable,
            "launch_unavailable_reason": self.launch_unavailable_reason,
        }


@dataclass(frozen=True)
class _ResolvedWorkspace:
    identity: WorkspaceIdentity
    workspace_path: str
    worktree_path: str


class WorkspaceResolver:
    """Resolve live working directories without blocking the async poll loop."""

    def __init__(
        self,
        creation_roots: Sequence[object] = (),
        *,
        git_executable: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        cache_ttl: float = DEFAULT_CACHE_TTL,
        creation_enabled: bool = True,
        creation_unavailable_reason: Optional[str] = None,
    ):
        self.git_executable = shutil.which(git_executable or "git")
        self.timeout = max(0.01, float(timeout))
        self.cache_ttl = max(0.0, float(cache_ttl))
        self._creation_enabled = bool(creation_enabled)
        self._creation_unavailable_reason = creation_unavailable_reason
        self._creation_status_provider: Optional[Callable[[], Tuple[bool, str]]] = None
        self._creation_roots = tuple(
            self._canonical(getattr(root, "path", root))
            for root in creation_roots
            if isinstance(getattr(root, "path", root), str)
        )
        self._cache: Dict[str, Tuple[float, Optional[_ResolvedWorkspace]]] = {}
        self._pane_active: Dict[str, _ResolvedWorkspace] = {}
        self._agent_active: Dict[str, _ResolvedWorkspace] = {}
        self._active: Dict[str, _ResolvedWorkspace] = {}
        self._lock = threading.RLock()

    def set_creation_status_provider(
        self, provider: Optional[Callable[[], Tuple[bool, str]]]
    ) -> None:
        self._creation_status_provider = provider
        with self._lock:
            self._cache.clear()

    def capability(self) -> dict:
        enabled = bool(self.git_executable)
        return {
            "version": WORKSPACE_VERSION,
            "supported": True,
            "enabled": enabled,
            "reason": None if enabled else "Git is unavailable on this server.",
        }

    def resolve(self, raw_path: object) -> Optional[WorkspaceIdentity]:
        value = self._resolve_private(raw_path)
        return value.identity if value else None

    async def resolve_many(self, raw_paths: Iterable[object]) -> Dict[str, Optional[WorkspaceIdentity]]:
        values = await self.resolve_active(raw_paths)
        return {path: (value.identity if value else None) for path, value in values.items()}

    async def resolve_active(self, raw_paths: Iterable[object]) -> Dict[str, Optional[_ResolvedWorkspace]]:
        paths = []
        seen = set()
        for raw in raw_paths:
            canonical = self._canonical(raw)
            if canonical and canonical not in seen:
                seen.add(canonical)
                paths.append(canonical)
        values = await asyncio.gather(
            *(asyncio.to_thread(self._resolve_private, path) for path in paths)
        )
        result = dict(zip(paths, values))
        self._replace_pane_active(value for value in values if value is not None)
        return result

    def refresh_active(self, raw_paths: Iterable[object]) -> None:
        resolved = []
        for raw in raw_paths:
            value = self._resolve_private(raw)
            if value is not None:
                resolved.append(value)
        self._replace_agent_active(resolved)

    def replace_active(self, identities: Iterable[WorkspaceIdentity]) -> None:
        """Test/support hook: retain matching previously resolved private entries."""
        ids = {identity.worktree_id for identity in identities if identity is not None}
        with self._lock:
            entries = [value for _expiry, value in self._cache.values() if value is not None]
            self._pane_active = {
                value.identity.worktree_id: value
                for value in entries
                if value.identity.worktree_id in ids
            }
            self._agent_active = {}
            self._merge_active_locked()

    def active_identity(self, worktree_id: str) -> Optional[WorkspaceIdentity]:
        with self._lock:
            value = self._active.get(worktree_id)
            return value.identity if value else None

    def active_path(self, worktree_id: str) -> Optional[str]:
        with self._lock:
            value = self._active.get(worktree_id)
        if value is None or not os.path.isdir(value.worktree_path):
            return None
        return value.worktree_path

    def _replace_pane_active(self, values: Iterable[_ResolvedWorkspace]) -> None:
        with self._lock:
            self._pane_active = {value.identity.worktree_id: value for value in values}
            self._merge_active_locked()

    def _replace_agent_active(self, values: Iterable[_ResolvedWorkspace]) -> None:
        with self._lock:
            self._agent_active = {value.identity.worktree_id: value for value in values}
            self._merge_active_locked()

    def _replace_active_private(self, values: Iterable[_ResolvedWorkspace]) -> None:
        self._replace_pane_active(values)

    def _merge_active_locked(self) -> None:
        self._active = dict(self._agent_active)
        self._active.update(self._pane_active)

    def _resolve_private(self, raw_path: object) -> Optional[_ResolvedWorkspace]:
        path = self._canonical(raw_path)
        if not path or not self.git_executable or not os.path.isdir(path):
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[0] >= now:
                return cached[1]
        try:
            value = self._inspect(path)
        except (OSError, ValueError, subprocess.SubprocessError):
            value = None
        with self._lock:
            self._cache[path] = (now + self.cache_ttl, value)
        return value

    def _inspect(self, cwd: str) -> Optional[_ResolvedWorkspace]:
        output = self._run_git(
            cwd,
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
            "--git-common-dir",
            "--git-dir",
        )
        lines = output.splitlines()
        if len(lines) != 3:
            return None
        worktree_path = self._canonical(lines[0])
        common_git_dir = self._canonical(lines[1])
        git_dir = self._canonical(lines[2])
        if not worktree_path or not common_git_dir or not git_dir or not os.path.isdir(worktree_path):
            return None

        branch = self._optional_git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
        detached = None if branch else self._optional_git(cwd, "rev-parse", "--short=8", "HEAD")
        if not branch and not detached:
            return None

        common_name = os.path.basename(common_git_dir.rstrip(os.sep))
        if common_name == ".git":
            workspace_root = os.path.dirname(common_git_dir)
        else:
            workspace_root = common_git_dir
        workspace_name = os.path.basename(workspace_root.rstrip(os.sep)) or "Repository"
        worktree_name = os.path.basename(worktree_path.rstrip(os.sep)) or workspace_name
        workspace_id = "ws_" + self._opaque(common_git_dir)
        worktree_id = "wt_" + self._opaque(worktree_path)
        primary_git_dir = self._canonical(os.path.join(worktree_path, ".git"))
        is_primary = common_git_dir == git_dir or primary_git_dir == common_git_dir
        launchable, reason = self._launch_status(worktree_path)
        identity = WorkspaceIdentity(
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            worktree_id=worktree_id,
            worktree_name=worktree_name,
            branch=branch,
            detached_commit=detached,
            is_primary=is_primary,
            launchable=launchable,
            launch_unavailable_reason=reason,
        )
        return _ResolvedWorkspace(identity, common_git_dir, worktree_path)

    def _run_git(self, cwd: str, *args: str) -> str:
        if not self.git_executable:
            raise FileNotFoundError("git")
        result = subprocess.run(
            [self.git_executable, "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args)
        return result.stdout.strip()

    def _optional_git(self, cwd: str, *args: str) -> Optional[str]:
        try:
            value = self._run_git(cwd, *args).strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return value or None

    def _launch_status(self, path: str) -> Tuple[bool, Optional[str]]:
        if self._creation_status_provider is not None:
            try:
                enabled, reason = self._creation_status_provider()
            except Exception:
                enabled, reason = False, "Creation is unavailable on this server."
            if not enabled:
                return False, reason or "Creation is unavailable on this server."
        elif not self._creation_enabled:
            return (
                False,
                self._creation_unavailable_reason
                or "Creation is unavailable on this server.",
            )
        if self._root_for(path) is None:
            return False, "This checkout is outside the configured creation roots."
        return True, None

    def _root_for(self, path: str) -> Optional[str]:
        matches = []
        for root in self._creation_roots:
            try:
                if os.path.commonpath([path, root]) == root:
                    matches.append(root)
            except ValueError:
                continue
        return max(matches, key=len, default=None)

    @staticmethod
    def _canonical(value: object) -> Optional[str]:
        if not isinstance(value, str) or not value or "\x00" in value:
            return None
        try:
            return os.path.realpath(value)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _opaque(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()[:24]

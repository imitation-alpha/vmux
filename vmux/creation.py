"""Authorized tmux target creation and filesystem browsing.

The client selects only a configured root, directory, and runtime identifier.
Filesystem boundaries and executable argument arrays remain server-owned.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import unicodedata
from typing import Dict, List, Optional, Tuple

from . import tmux
from .config import CREATION_RUNTIME_IDS, Config, CreationRoot
from .workspaces import WorkspaceResolver

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PANE_ID_RE = re.compile(r"^%\d+$")
CREATE_TYPES = {"session", "window", "pane"}
SPLIT_DIRECTIONS = {"side_by_side", "stacked"}
RUNTIME_LABELS = {
    "shell": "Shell",
    "codex": "Codex",
    "claude": "Claude",
    "agy": "Antigravity",
    "grok": "Grok Build",
    "opencode": "OpenCode",
}


class CreationProblem(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class CreationService:
    def __init__(self, cfg: Config, *, workspace_resolver: Optional[WorkspaceResolver] = None):
        self.cfg = cfg
        self.workspace_resolver = workspace_resolver
        # The critical section includes uniqueness lookup, final parent/runtime
        # validation, and tmux creation. This prevents two HTTP workers from
        # choosing the same automatic suffix.
        self._lock = threading.Lock()

    # -- public metadata ------------------------------------------------- #
    def setup_status(self) -> Tuple[bool, str]:
        if not self.cfg.creation_configured:
            return False, self.cfg.creation_setup_reason
        if not tmux.available():
            return False, "tmux is unavailable on this server."
        return True, ""

    def capability(self) -> dict:
        enabled, reason = self.setup_status()
        return {
            "version": 1,
            "supported": True,
            "enabled": enabled,
            "reason": reason or None,
        }

    def info(self) -> dict:
        enabled, reason = self.setup_status()
        return {
            "enabled": enabled,
            "reason": reason or None,
            "roots": [self._root_payload(root) for root in self.cfg.creation_roots],
            "recent_directories": self.recent_directories(),
            "runtimes": self.runtime_info(),
        }

    def runtime_info(self) -> List[dict]:
        tmux_ready = tmux.available()
        values = []
        for runtime_id in ("shell",) + CREATION_RUNTIME_IDS:
            available, reason = self._runtime_status(runtime_id, tmux_ready=tmux_ready)
            values.append({
                "id": runtime_id,
                "label": RUNTIME_LABELS[runtime_id],
                "available": available,
                "reason": reason or None,
            })
        return values

    def recent_directories(self, limit: int = 20) -> List[dict]:
        found: Dict[str, dict] = {}
        for pane in tmux.list_panes():
            raw = pane.get("path", "")
            try:
                path, root = self.resolve_directory(raw)
            except CreationProblem:
                continue
            if path in found:
                continue
            found[path] = {
                "path": path,
                "name": os.path.basename(path) or path,
                "root_label": root.label,
            }
        return sorted(found.values(), key=lambda item: item["path"].lower())[:max(0, limit)]

    # -- filesystem authorization -------------------------------------- #
    def resolve_directory(self, raw_path: object) -> Tuple[str, CreationRoot]:
        if not isinstance(raw_path, str):
            raise CreationProblem(400, "Directory path must be a string.")
        value = raw_path.strip()
        if not value or len(value) > 4096 or "\x00" in value:
            raise CreationProblem(400, "Directory path is invalid.")
        if value == "~" or value.startswith("~/"):
            value = os.path.expanduser(value)
        elif not os.path.isabs(value):
            raise CreationProblem(400, "Directory path must be absolute or start with ~/.")
        try:
            canonical = os.path.realpath(value)
        except (OSError, ValueError):
            raise CreationProblem(400, "Directory path is invalid.")

        root = self._root_for(canonical)
        if root is None:
            raise CreationProblem(403, "Directory is outside the configured creation roots.")
        try:
            if not os.path.isdir(canonical) or not os.access(canonical, os.R_OK | os.X_OK):
                raise CreationProblem(404, "Directory is unavailable.")
        except OSError:
            raise CreationProblem(404, "Directory is unavailable.")
        return canonical, root

    def browse(self, raw_path: object) -> dict:
        path, root = self.resolve_directory(raw_path)
        children: List[dict] = []
        seen = set()
        try:
            with os.scandir(path) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.lower())
        except OSError:
            raise CreationProblem(404, "Directory is unavailable.")
        truncated = False
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=True):
                    continue
                canonical = os.path.realpath(entry.path)
                child_root = self._root_for(canonical)
                if child_root is None or canonical in seen:
                    continue
                if not os.access(canonical, os.R_OK | os.X_OK):
                    continue
            except (OSError, ValueError):
                continue
            seen.add(canonical)
            if len(children) >= 500:
                truncated = True
                break
            children.append({"name": entry.name, "path": canonical})

        parent = None
        if path != root.path:
            candidate = os.path.dirname(path)
            if self._root_for(candidate) is not None:
                parent = candidate
        return {
            "path": path,
            "root": self._root_payload(root),
            "parent": parent,
            "directories": children,
            "truncated": truncated,
        }

    # -- creation ------------------------------------------------------- #
    def create(self, raw: object) -> dict:
        req = self._validate_request(raw)
        enabled, reason = self.setup_status()
        if not enabled:
            raise CreationProblem(503, reason or "Creation is unavailable.")
        if "worktree_id" in req:
            if self.workspace_resolver is None:
                raise CreationProblem(404, "Worktree is no longer active.")
            identity = self.workspace_resolver.active_identity(req["worktree_id"])
            if identity is None:
                raise CreationProblem(404, "Worktree is no longer active.")
            if not identity.launchable:
                raise CreationProblem(403, identity.launch_unavailable_reason or "Worktree launch is unavailable.")
            raw_cwd = self.workspace_resolver.active_path(req["worktree_id"])
            if raw_cwd is None:
                raise CreationProblem(404, "Worktree is no longer active.")
        else:
            raw_cwd = req["cwd"]
        cwd, _root = self.resolve_directory(raw_cwd)
        runtime_id = req["runtime"]

        with self._lock:
            available, unavailable_reason = self._runtime_status(runtime_id)
            if not available:
                raise CreationProblem(503, unavailable_reason or "The selected runtime is unavailable.")
            command = None if runtime_id == "shell" else list(self.cfg.creation_runtimes[runtime_id])
            create_type = req["type"]
            explicit_name = req.get("name")

            if create_type == "session":
                existing = set(tmux.session_names())
                name = self._creation_name(cwd, explicit_name, existing, "session")
                result = self._call_tmux(
                    lambda: tmux.create_session(name, cwd, command),
                    create_type=create_type,
                    explicit_name=explicit_name,
                )
            elif create_type == "window":
                parent_session = req["parent_session"]
                if not tmux.session_exists(parent_session):
                    raise CreationProblem(404, "The parent session is no longer available.")
                existing = set(tmux.window_names(parent_session))
                name = self._creation_name(cwd, explicit_name, existing, "window")
                # This lookup is intentionally the final operation before the
                # create call so a disappearing parent becomes a clean 404.
                if not tmux.session_exists(parent_session):
                    raise CreationProblem(404, "The parent session is no longer available.")
                result = self._call_tmux(
                    lambda: tmux.create_window(parent_session, name, cwd, command),
                    create_type=create_type,
                    explicit_name=explicit_name,
                    parent_session=parent_session,
                )
            else:
                parent_pane_id = req["parent_pane_id"]
                if not tmux.exists(parent_pane_id):
                    raise CreationProblem(404, "The parent pane is no longer available.")
                result = self._call_tmux(
                    lambda: tmux.create_pane(
                        parent_pane_id,
                        cwd,
                        req["split"],
                        req["size_percent"],
                        command,
                    ),
                    create_type=create_type,
                    explicit_name=None,
                    parent_pane_id=parent_pane_id,
                )

            pane_id = result.get("pane_id", "")
            target = result.get("target", "")
            if not PANE_ID_RE.match(pane_id) or not target or not tmux.exists(pane_id):
                raise CreationProblem(503, "The new pane exited before it became available.")
            return {"pane_id": pane_id, "target": target}

    def _call_tmux(
        self,
        operation,
        *,
        create_type: str,
        explicit_name: Optional[str],
        parent_session: Optional[str] = None,
        parent_pane_id: Optional[str] = None,
    ) -> dict:
        try:
            return operation()
        except tmux.TmuxError:
            if not tmux.available():
                raise CreationProblem(503, "tmux is unavailable on this server.")
            if parent_session is not None and not tmux.session_exists(parent_session):
                raise CreationProblem(404, "The parent session is no longer available.")
            if parent_pane_id is not None and not tmux.exists(parent_pane_id):
                raise CreationProblem(404, "The parent pane is no longer available.")
            if create_type in ("session", "window"):
                if explicit_name is not None:
                    raise CreationProblem(409, "That name is already in use.")
                raise CreationProblem(409, "tmux could not create a uniquely named target.")
            raise CreationProblem(409, "tmux could not create the requested pane.")

    def _creation_name(
        self,
        cwd: str,
        explicit: Optional[str],
        existing: set,
        fallback: str,
    ) -> str:
        if explicit is not None:
            if explicit in existing:
                raise CreationProblem(409, "That name is already in use.")
            return explicit
        base = self.suggested_name(cwd, fallback=fallback)
        if base not in existing:
            return base
        index = 2
        while True:
            suffix = "-%d" % index
            candidate = base[:64 - len(suffix)].rstrip("-_") + suffix
            if candidate not in existing:
                return candidate
            index += 1

    @staticmethod
    def suggested_name(cwd: str, fallback: str = "session") -> str:
        basename = os.path.basename(cwd.rstrip(os.sep))
        normalized = unicodedata.normalize("NFKD", basename).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", normalized).strip("-_").lower()
        slug = slug[:64].rstrip("-_")
        return slug or fallback

    # -- validation helpers -------------------------------------------- #
    def _validate_request(self, raw: object) -> dict:
        if not isinstance(raw, dict):
            raise CreationProblem(400, "Creation request must be a JSON object.")
        create_type = raw.get("type")
        if create_type not in CREATE_TYPES:
            raise CreationProblem(400, "Creation type must be session, window, or pane.")
        allowed = {
            "session": {"type", "cwd", "worktree_id", "runtime", "name"},
            "window": {"type", "parent_session", "cwd", "worktree_id", "runtime", "name"},
            "pane": {"type", "parent_pane_id", "cwd", "worktree_id", "runtime", "split", "size_percent"},
        }[create_type]
        if set(raw) - allowed:
            raise CreationProblem(400, "Creation request contains unsupported fields.")
        has_cwd = "cwd" in raw
        has_worktree = "worktree_id" in raw
        if has_cwd == has_worktree:
            raise CreationProblem(400, "Supply exactly one of cwd or worktree_id.")
        runtime_id = raw.get("runtime", "shell")
        if runtime_id not in ("shell",) + CREATION_RUNTIME_IDS:
            raise CreationProblem(400, "Unknown runtime preset.")
        req = {"type": create_type, "runtime": runtime_id}
        if has_cwd:
            req["cwd"] = raw["cwd"]
        else:
            worktree_id = raw.get("worktree_id")
            if not isinstance(worktree_id, str) or not re.fullmatch(r"wt_[0-9a-f]{24}", worktree_id):
                raise CreationProblem(404, "Worktree is no longer active.")
            req["worktree_id"] = worktree_id

        if create_type in ("session", "window"):
            name = raw.get("name")
            if name is not None and (not isinstance(name, str) or not NAME_RE.match(name)):
                raise CreationProblem(
                    400,
                    "Name must be 1–64 letters, numbers, underscores, or hyphens.",
                )
            req["name"] = name
        if create_type == "window":
            parent = raw.get("parent_session")
            if not isinstance(parent, str) or not parent or len(parent) > 128 or "\x00" in parent:
                raise CreationProblem(400, "Parent session is invalid.")
            req["parent_session"] = parent
        if create_type == "pane":
            parent_pane_id = raw.get("parent_pane_id")
            if not isinstance(parent_pane_id, str) or not PANE_ID_RE.match(parent_pane_id):
                raise CreationProblem(400, "Parent pane ID is invalid.")
            split = raw.get("split", "side_by_side")
            if split not in SPLIT_DIRECTIONS:
                raise CreationProblem(400, "Split direction is invalid.")
            size = raw.get("size_percent", 50)
            if isinstance(size, bool) or not isinstance(size, int) or not 10 <= size <= 90:
                raise CreationProblem(400, "Split size must be an integer from 10 to 90.")
            req.update({
                "parent_pane_id": parent_pane_id,
                "split": split,
                "size_percent": size,
            })
        return req

    def _runtime_status(self, runtime_id: str, *, tmux_ready: Optional[bool] = None) -> Tuple[bool, str]:
        if tmux_ready is None:
            tmux_ready = tmux.available()
        if not tmux_ready:
            return False, "tmux is unavailable on this server."
        if runtime_id == "shell":
            return True, ""
        command = self.cfg.creation_runtimes.get(runtime_id)
        if not command:
            return False, "This runtime is not configured on the server."
        if shutil.which(command[0]) is None:
            return False, "This runtime executable is unavailable on the server."
        return True, ""

    def _root_for(self, path: str) -> Optional[CreationRoot]:
        matches = []
        for root in self.cfg.creation_roots:
            try:
                if os.path.commonpath([path, root.path]) == root.path:
                    matches.append(root)
            except ValueError:
                continue
        return max(matches, key=lambda item: len(item.path), default=None)

    @staticmethod
    def _root_payload(root: CreationRoot) -> dict:
        return {"label": root.label, "path": root.path}

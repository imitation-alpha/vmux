"""Smart pane naming inspired by auto-naming-tmux.

This module only chooses vmux display names. It does not rename tmux windows,
install tmux hooks, or change pane border formats.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional

from .config import Config
from .detectors import is_spinner

SHELL_RE = re.compile(r"^-?(bash|zsh|fish|sh|ksh)$")
AGENT_RE = re.compile(r"^([0-9]+\.[0-9]|claude(\.exe)?|codex|agy|antigravity|opencode|oc)$")
EDITOR_RE = re.compile(r"^(n?vim|vi|git|less|man|htop|btop)$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]")


def command_basename(command: str) -> str:
    return os.path.basename(command or "").strip()


def path_basename(path: str) -> str:
    path = (path or "").rstrip("/")
    return os.path.basename(path) if path else ""


def strip_spinner(text: str) -> str:
    out = (text or "").strip()
    while out and is_spinner(out[0]):
        out = out[1:].strip()
    return out


def sanitize_ai_name(raw: str, max_len: int = 24) -> str:
    """Match auto-naming-tmux's safe lowercase kebab-case name shape."""
    text = (raw or "").splitlines()[0].lower().replace(" ", "-")
    text = re.sub(r"[^a-z0-9:_-]", "", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text


def ssh_host_from_args(args: str) -> str:
    """Extract a destination host from an ssh argv string."""
    toks = args.split()
    i = 1
    value_flags = set("bcDEeFIiLlmOopQRSWw")
    while i < len(toks):
        tok = toks[i]
        if tok.startswith("-"):
            if len(tok) == 2 and tok[-1] in value_flags:
                i += 2
            else:
                i += 1
            continue
        return tok.split("@", 1)[-1]
    return ""


def _run(argv: List[str], *, input_text: Optional[str] = None, timeout: Optional[float] = 3.0) -> str:
    try:
        proc = subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout if timeout and timeout > 0 else None,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _child_pids(parent: str) -> List[str]:
    if not parent or not parent.isdigit():
        return []
    out = _run(["pgrep", "-P", parent], timeout=0.5)
    return [p.strip() for p in out.splitlines() if p.strip().isdigit()]


def _descendant_pids(parent: str) -> List[str]:
    out: List[str] = []
    for child in _child_pids(parent):
        out.append(child)
        out.extend(_descendant_pids(child))
    return out


def find_process_args(root_pid: str, command_re: re.Pattern) -> str:
    for pid in _descendant_pids(root_pid):
        comm = _run(["ps", "-o", "comm=", "-p", pid], timeout=0.5).strip()
        if command_re.match(command_basename(comm)):
            return _run(["ps", "-o", "args=", "-p", pid], timeout=0.5).strip()
    return ""


def meaningful_agent_title(title: str, command: str, path: str) -> str:
    title = strip_spinner(title)
    cmd = command_basename(command)
    base = path_basename(path)
    if not title or title == cmd or title == "%s:%s" % (cmd, base):
        return ""
    return title


def heuristic_name(pane: dict, *, fallback: str = "") -> str:
    cmd = command_basename(pane.get("cmd", ""))
    path = pane.get("path", "")
    base = path_basename(path)
    if cmd == "ssh":
        host = ssh_host_from_args(find_process_args(str(pane.get("pid", "")), re.compile(r"^ssh$")))
        return "ssh:%s" % host if host else "ssh"
    if SHELL_RE.match(cmd):
        return base or fallback
    if AGENT_RE.match(cmd):
        return meaningful_agent_title(pane.get("title", ""), cmd, path) or base or cmd or fallback
    if EDITOR_RE.match(cmd):
        return "%s:%s" % (cmd, base) if base else (cmd or fallback)
    return cmd or base or fallback


def is_ambiguous_ai_program(cfg: Config, command: str) -> bool:
    cmd = command_basename(command)
    return bool(VERSION_RE.match(cmd) or cmd in set(cfg.auto_naming_ai_programs))


def prefix_for_command(cfg: Config, command: str) -> str:
    cmd = command_basename(command)
    if VERSION_RE.match(cmd):
        return "cc"
    return cfg.auto_naming_prefix_apps.get(cmd, "")


def _cache_key(pane: dict) -> str:
    raw = "%s|%s" % (command_basename(pane.get("cmd", "")), pane.get("path", ""))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def _git_branch(path: str) -> str:
    if not path:
        return ""
    return _run(["git", "-C", path, "branch", "--show-current"], timeout=1.0).strip()


class SmartNamer:
    def __init__(self, cfg: Config, on_update: Optional[Callable[[], None]] = None):
        self.cfg = cfg
        self.on_update = on_update
        self.cache: Dict[str, str] = self._load_cache()
        self._inflight: Dict[str, asyncio.Task] = {}

    def name(self, pane: dict, text: str, fallback: str) -> str:
        base = heuristic_name(pane, fallback=fallback)
        if not (self.cfg.auto_naming_ai_enabled and is_ambiguous_ai_program(self.cfg, pane.get("cmd", ""))):
            return base

        key = _cache_key(pane)
        cached = self.cache.get(key)
        if cached:
            return cached

        self._schedule(key, pane, text)
        return base

    def stop(self) -> None:
        for task in self._inflight.values():
            task.cancel()
        self._inflight.clear()

    def _schedule(self, key: str, pane: dict, text: str) -> None:
        if key in self._inflight:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._inflight[key] = loop.create_task(self._resolve_ai_name(key, dict(pane), text))

    async def _resolve_ai_name(self, key: str, pane: dict, text: str) -> None:
        try:
            raw = await asyncio.to_thread(self._call_backend, pane, text)
            name = sanitize_ai_name(raw, self.cfg.auto_naming_max_len)
            if not name:
                return
            prefix = prefix_for_command(self.cfg, pane.get("cmd", ""))
            if prefix:
                name = "%s:%s" % (prefix, name)
            self.cache[key] = name
            self._save_cache()
            if self.on_update:
                self.on_update()
        except Exception:
            return
        finally:
            self._inflight.pop(key, None)

    def _call_backend(self, pane: dict, text: str) -> str:
        content = self._content(pane, text)
        timeout = self.cfg.auto_naming_timeout
        prompt = self.cfg.auto_naming_system_prompt
        backend = self.cfg.auto_naming_ai_backend
        if backend == "claude":
            return _run(
                [self.cfg.auto_naming_claude_bin, "-p", prompt, "--model", self.cfg.auto_naming_claude_model],
                input_text=content,
                timeout=timeout,
            )
        if backend == "local":
            return self._call_local_backend(content, timeout)
        if backend == "codex":
            return self._call_codex_backend(content, timeout)
        if backend in ("agy", "antigravity"):
            argv = [self.cfg.auto_naming_antigravity_bin, "-p", prompt]
            if self.cfg.auto_naming_antigravity_model:
                argv += ["--model", self.cfg.auto_naming_antigravity_model]
            argv += list(self.cfg.auto_naming_antigravity_flags)
            return _run(argv, input_text=content, timeout=timeout)
        return ""

    def _content(self, pane: dict, text: str) -> str:
        lines = (text or "").splitlines()[-40:]
        parts = [
            "target: %s" % pane.get("target", ""),
            "command: %s" % command_basename(pane.get("cmd", "")),
            "path: %s" % pane.get("path", ""),
        ]
        branch = _git_branch(pane.get("path", ""))
        if branch:
            parts.append("git branch: %s" % branch)
        parts.append("--- pane output ---")
        parts.extend(lines)
        return "\n".join(parts)

    def _call_local_backend(self, content: str, timeout: float) -> str:
        body = {
            "model": self.cfg.auto_naming_local_model,
            "max_tokens": 24,
            "messages": [
                {"role": "system", "content": self.cfg.auto_naming_system_prompt},
                {"role": "user", "content": content},
            ],
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.cfg.auto_naming_local_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.cfg.auto_naming_local_api_key:
            req.add_header("Authorization", "Bearer " + self.cfg.auto_naming_local_api_key)
        try:
            with urllib.request.urlopen(req, timeout=timeout if timeout and timeout > 0 else None) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            return ""
        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            return ""

    def _call_codex_backend(self, content: str, timeout: float) -> str:
        fd, path = tempfile.mkstemp(prefix="vmux-name-", text=True)
        os.close(fd)
        try:
            argv = [
                self.cfg.auto_naming_codex_bin,
                "exec",
                "-s",
                "read-only",
                "--skip-git-repo-check",
                "-o",
                path,
            ]
            if self.cfg.auto_naming_codex_model:
                argv += ["-m", self.cfg.auto_naming_codex_model]
            _run(argv, input_text="%s\n\n%s" % (self.cfg.auto_naming_system_prompt, content), timeout=timeout)
            try:
                with open(path, "r") as fh:
                    return fh.read()
            except OSError:
                return ""
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _load_cache(self) -> Dict[str, str]:
        path = self.cfg.auto_naming_cache_path
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if str(k) and str(v)}

    def _save_cache(self) -> None:
        path = self.cfg.auto_naming_cache_path
        if not path:
            return
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.cache, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)

"""Tests for native smart pane naming."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux.config import Config
from vmux.naming import (
    SmartNamer,
    _cache_key,
    heuristic_name,
    is_ambiguous_ai_program,
    prefix_for_command,
    sanitize_ai_name,
    ssh_host_from_args,
)


def pane(**kw):
    base = {
        "id": "%1",
        "target": "work:1.1",
        "cmd": "zsh",
        "title": "",
        "window": "work",
        "path": "/tmp/project",
        "pid": "",
        "window_id": "@1",
    }
    base.update(kw)
    return base


def test_heuristic_shell_uses_directory():
    assert heuristic_name(pane(cmd="zsh", path="/tmp/project"), fallback="work:1.1") == "project"


def test_heuristic_editor_uses_command_and_directory():
    assert heuristic_name(pane(cmd="nvim", path="/tmp/project"), fallback="work:1.1") == "nvim:project"


def test_heuristic_agent_prefers_meaningful_title():
    assert heuristic_name(
        pane(cmd="codex", title="✳ implement auth redirect", path="/tmp/project"),
        fallback="work:1.1",
    ) == "implement auth redirect"


def test_heuristic_agent_falls_back_to_directory_for_unhelpful_title():
    assert heuristic_name(pane(cmd="codex", title="codex:project"), fallback="work:1.1") == "project"


def test_heuristic_unknown_uses_command():
    assert heuristic_name(pane(cmd="node", path="/tmp/project"), fallback="work:1.1") == "node"


def test_ssh_host_from_args():
    assert ssh_host_from_args("ssh user@example.com") == "example.com"
    assert ssh_host_from_args("ssh -p 22 host") == "host"
    assert ssh_host_from_args("ssh -i key -p 22 user@host") == "host"
    assert ssh_host_from_args("ssh") == ""


def test_sanitize_ai_name_matches_tmux_plugin_shape():
    assert sanitize_ai_name("Fix Auth Redirect", 24) == "fix-auth-redirect"
    assert sanitize_ai_name("CC:Fix Auth!", 24) == "cc:fix-auth"
    assert sanitize_ai_name("-a---b-", 24) == "a-b"
    assert sanitize_ai_name("abcdefghij", 5) == "abcde"
    assert sanitize_ai_name("one\ntwo", 24) == "one"


def test_ai_ambiguity_and_prefixes():
    c = Config()
    assert is_ambiguous_ai_program(c, "2.1.168") is True
    assert is_ambiguous_ai_program(c, "codex") is True
    assert is_ambiguous_ai_program(c, "zsh") is False
    assert prefix_for_command(c, "2.1.168") == "cc"
    assert prefix_for_command(c, "codex") == "cx"


def test_smart_namer_uses_cache_before_heuristic(tmp_path):
    c = Config(naming_mode="smart", auto_naming_ai_enabled=True)
    c.auto_naming_cache_path = str(tmp_path / "names.json")
    n = SmartNamer(c)
    p = pane(cmd="codex", path="/tmp/project")
    n.cache[_cache_key(p)] = "cx:cached"
    assert n.name(p, "", "work:1.1") == "cx:cached"


def test_smart_namer_schedules_ai_and_returns_heuristic(tmp_path):
    async def run():
        kicked = []
        c = Config(naming_mode="smart", auto_naming_ai_enabled=True)
        c.auto_naming_cache_path = str(tmp_path / "names.json")
        n = SmartNamer(c, on_update=lambda: kicked.append(True))
        p = pane(cmd="codex", title="", path="/tmp/project")
        n._call_backend = lambda pane, text: "Fix Auth"
        assert n.name(p, "visible output", "work:1.1") == "project"
        while n._inflight:
            await asyncio.sleep(0.01)
        assert n.name(p, "", "work:1.1") == "cx:fix-auth"
        assert kicked == [True]

    asyncio.run(run())

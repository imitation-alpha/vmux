"""Poller integration checks for display-name selection."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux import tmux
from vmux.config import Config, PaneOverride
from vmux.poller import Hub

PANE = {
    "id": "%1",
    "target": "work:1.1",
    "cmd": "codex",
    "title": "",
    "window": "work",
    "path": "/tmp/project",
    "pid": "",
    "window_id": "@1",
}


def test_poller_uses_smart_name(monkeypatch):
    monkeypatch.setattr(tmux, "list_panes", lambda: [PANE])
    monkeypatch.setattr(tmux, "capture", lambda pane_id, scrollback: "output")

    h = Hub(Config(naming_mode="smart"))
    monkeypatch.setattr(h.namer, "name", lambda pane, text, fallback: "Smart Name")

    asyncio.run(h.poll_once())

    assert h.states["%1"].name == "Smart Name"


def test_manual_override_skips_smart_name(monkeypatch):
    monkeypatch.setattr(tmux, "list_panes", lambda: [PANE])
    monkeypatch.setattr(tmux, "capture", lambda pane_id, scrollback: "output")

    cfg = Config(
        naming_mode="smart",
        overrides={"work:1.1": PaneOverride(target="work:1.1", name="Manual")},
    )
    h = Hub(cfg)

    def fail_if_called(pane, text, fallback):
        raise AssertionError("smart namer should not run for manual overrides")

    monkeypatch.setattr(h.namer, "name", fail_if_called)

    asyncio.run(h.poll_once())

    assert h.states["%1"].name == "Manual"

"""Live polling delivery and status-stabilization coverage."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux import tmux
from vmux.config import Config
from vmux.models import PaneState
from vmux.poller import Hub

PANE = {
    "id": "%1", "target": "work:1.1", "cmd": "node", "title": "worker",
    "window": "work", "path": "/tmp", "pid": "", "window_id": "@1",
}


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def test_snapshot_revision_changes_only_for_wire_visible_state(monkeypatch):
    captures = iter(["running", "running", "running", "new output"])
    monkeypatch.setattr(tmux, "list_panes", lambda: [PANE])
    monkeypatch.setattr(tmux, "capture", lambda *_: next(captures))
    hub = Hub(Config())

    asyncio.run(hub.poll_once())
    first = hub._snapshot_revision
    asyncio.run(hub.poll_once())  # changed flips to false once
    settled = hub._snapshot_revision
    asyncio.run(hub.poll_once())
    assert first < settled
    assert hub._snapshot_revision == settled

    asyncio.run(hub.poll_once())
    assert hub._snapshot_revision == settled + 1


def test_each_client_receives_a_revision_once_and_new_clients_get_state():
    hub = Hub(Config())
    hub.states = {"%1": PaneState(id="%1", target="work:1.1", name="one")}
    hub.order = ["%1"]
    hub._update_snapshot_revision()
    first, second = Socket(), Socket()
    hub.add_client("first", first, "127.0.0.1", "test", 0)

    asyncio.run(hub.send_snapshot("first"))
    asyncio.run(hub.broadcast())
    assert len(first.messages) == 1

    hub.add_client("second", second, "127.0.0.1", "test", 0)
    asyncio.run(hub.send_snapshot("second"))
    assert len(second.messages) == 1

    hub.states["%1"].name = "renamed"
    hub._update_snapshot_revision()
    asyncio.run(hub.broadcast())
    assert len(first.messages) == 2
    assert len(second.messages) == 2


def test_generic_working_has_a_short_quiet_grace_and_attention_overrides(monkeypatch):
    captures = iter(["running", "running", "Continue? (y/n)", "Traceback (most recent call last):"])
    monkeypatch.setattr(tmux, "list_panes", lambda: [PANE])
    monkeypatch.setattr(tmux, "capture", lambda *_: next(captures))
    hub = Hub(Config())

    asyncio.run(hub.poll_once())
    assert hub.states["%1"].status == "working"
    asyncio.run(hub.poll_once())
    assert hub.states["%1"].status == "working"
    asyncio.run(hub.poll_once())
    assert hub.states["%1"].status == "needs_input"
    asyncio.run(hub.poll_once())
    assert hub.states["%1"].status == "error"


def test_generic_working_grace_expires(monkeypatch):
    captures = iter(["running", "running"])
    monkeypatch.setattr(tmux, "list_panes", lambda: [PANE])
    monkeypatch.setattr(tmux, "capture", lambda *_: next(captures))
    hub = Hub(Config())

    asyncio.run(hub.poll_once())
    hub.states["%1"].updated = time.time() - 3
    asyncio.run(hub.poll_once())
    assert hub.states["%1"].status == "idle"


def test_initial_quiet_generic_pane_never_becomes_done(monkeypatch):
    captures = iter(["ready", "ready"])
    monkeypatch.setattr(tmux, "list_panes", lambda: [PANE])
    monkeypatch.setattr(tmux, "capture", lambda *_: next(captures))
    hub = Hub(Config())

    asyncio.run(hub.poll_once())
    assert hub.states["%1"].lifecycle["state"] == "idle"
    hub.states["%1"].updated = time.time() - 3
    asyncio.run(hub.poll_once())
    assert hub.states["%1"].lifecycle["state"] == "idle"

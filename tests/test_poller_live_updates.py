"""Live polling delivery and status-stabilization coverage."""

import asyncio
import os
import sys
import threading
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


def test_failed_capture_is_unknown_and_preserves_content_identity(monkeypatch):
    pane = {**PANE, "cmd": "claude", "title": "Claude Code"}
    working = "Claude Code\n⠋ Working… (1s)"
    captures = iter([working, None, working])
    monkeypatch.setattr(tmux, "list_panes", lambda: [pane])
    monkeypatch.setattr(tmux, "capture", lambda *_: next(captures))
    hub = Hub(Config())

    asyncio.run(hub.poll_once())
    assert hub.states["%1"].lifecycle["state"] == "working"
    content_hash = hub._meta["%1"]["hash"]

    asyncio.run(hub.poll_once())
    assert hub.states["%1"].lifecycle["state"] == "unknown"
    assert hub.states["%1"].lines == working.splitlines()
    assert hub._meta["%1"]["hash"] == content_hash

    asyncio.run(hub.poll_once())
    assert hub.states["%1"].lifecycle["state"] == "working"
    assert hub.states["%1"].changed is False


def test_acknowledgment_cannot_be_overwritten_by_inflight_poll(monkeypatch):
    pane = {**PANE, "cmd": "claude", "title": "Claude Code"}
    captures = iter(["Claude Code\n⠋ Working… (1s)", "Claude Code\n? for shortcuts"])
    monkeypatch.setattr(tmux, "list_panes", lambda: [pane])
    monkeypatch.setattr(tmux, "capture", lambda *_: next(captures))
    hub = Hub(Config())
    asyncio.run(hub.poll_once())

    observed_done = threading.Event()
    release_poll = threading.Event()
    acknowledgment_done = threading.Event()
    original_observe = hub.lifecycle.observe

    def paused_observe(*args, **kwargs):
        summary = original_observe(*args, **kwargs)
        if summary.state == "done":
            observed_done.set()
            release_poll.wait(2)
        return summary

    monkeypatch.setattr(hub.lifecycle, "observe", paused_observe)
    poll_thread = threading.Thread(target=lambda: asyncio.run(hub.poll_once()))
    poll_thread.start()
    assert observed_done.wait(2)

    def acknowledge():
        hub.acknowledge_lifecycle("%1", 2)
        acknowledgment_done.set()

    acknowledgment_thread = threading.Thread(target=acknowledge)
    acknowledgment_thread.start()
    assert not acknowledgment_done.wait(0.05)
    release_poll.set()
    poll_thread.join(2)
    acknowledgment_thread.join(2)

    assert not poll_thread.is_alive()
    assert not acknowledgment_thread.is_alive()
    assert hub.states["%1"].lifecycle["state"] == "idle"
    assert hub.states["%1"].lifecycle["revision"] == 3


def test_action_invalidates_capture_taken_before_interaction(monkeypatch):
    pane = {**PANE, "cmd": "claude", "title": "Claude Code"}
    captures = iter(["Claude Code\n⠋ Working… (1s)", "Claude Code\n? for shortcuts"])
    monkeypatch.setattr(tmux, "list_panes", lambda: [pane])
    monkeypatch.setattr(tmux, "capture", lambda *_: next(captures))
    hub = Hub(Config())
    asyncio.run(hub.poll_once())

    capture_finished = threading.Event()
    release_poll = threading.Event()
    original_resolve = hub.workspaces.resolve_active

    async def paused_resolve(paths):
        capture_finished.set()
        release_poll.wait(2)
        return await original_resolve(paths)

    monkeypatch.setattr(hub.workspaces, "resolve_active", paused_resolve)
    poll_thread = threading.Thread(target=lambda: asyncio.run(hub.poll_once()))
    poll_thread.start()
    assert capture_finished.wait(2)

    hub.mark_interaction("%1")
    hub.acknowledge_done_after_action("%1")
    release_poll.set()
    poll_thread.join(2)

    assert not poll_thread.is_alive()
    assert hub.states["%1"].lifecycle["state"] == "unknown"
    assert hub.states["%1"].lifecycle["reason"] == "capture_precedes_interaction"

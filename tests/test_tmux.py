"""Unit checks for the safe tmux argument builders (no live tmux needed —
_run is monkeypatched so we assert on the argument list it would receive)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux import tmux


def _record_args(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux, "_run", lambda args, **kw: (calls.append(args), "out")[1])
    return calls


def test_capture_visible_only_by_default(monkeypatch):
    calls = _record_args(monkeypatch)
    tmux.capture("%1")
    assert calls == [["capture-pane", "-p", "-J", "-t", "%1"]]


def test_capture_includes_scrollback(monkeypatch):
    calls = _record_args(monkeypatch)
    tmux.capture("%3", scrollback=200)
    assert calls == [["capture-pane", "-p", "-J", "-S", "-200", "-t", "%3"]]


def test_capture_rejects_bad_pane_id(monkeypatch):
    calls = _record_args(monkeypatch)
    assert tmux.capture("; rm -rf /", scrollback=200) is None
    assert calls == []   # never reached the subprocess


def test_disable_automatic_rename_sets_global_window_option(monkeypatch):
    calls = _record_args(monkeypatch)
    tmux.disable_automatic_rename()
    assert calls == [["set-window-option", "-g", "automatic-rename", "off"]]


def test_list_panes_parses_smart_naming_metadata(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return "%1\twork:2.3\tcodex\tFix auth\tapi\t/tmp/project\t1234\t@7\n"

    monkeypatch.setattr(tmux, "_run", fake_run)

    panes = tmux.list_panes()

    assert panes == [{
        "id": "%1",
        "target": "work:2.3",
        "cmd": "codex",
        "title": "Fix auth",
        "window": "api",
        "path": "/tmp/project",
        "pid": "1234",
        "window_id": "@7",
    }]
    assert "#{pane_current_path}" in calls[0][-1]
    assert "#{pane_pid}" in calls[0][-1]
    assert "#{window_id}" in calls[0][-1]

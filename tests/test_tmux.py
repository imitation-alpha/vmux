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
    tmux._PROCESS_START_CACHE.clear()

    def fake_run(args, **kw):
        calls.append(args)
        return "%1\twork:2.3\tcodex\tFix auth\tapi\t/tmp/project\t1234\t@7\n"

    monkeypatch.setattr(tmux, "_run", fake_run)
    monkeypatch.setattr(tmux, "_process_starts", lambda pids: {})

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


def test_list_panes_uses_cached_process_start_as_incarnation(monkeypatch):
    tmux._PROCESS_START_CACHE.clear()
    monkeypatch.setattr(
        tmux, "_run",
        lambda args, **kw: "%9\twork:1.0\tcodex\tAgent\twork\t/tmp/project\t4321\t@1\n",
    )
    calls = []
    monkeypatch.setattr(
        tmux, "_process_starts",
        lambda pids: calls.append(list(pids)) or {pid: 123456.0 for pid in pids},
    )
    first = tmux.list_panes()
    second = tmux.list_panes()
    assert first[0]["created"] == "123456.0"
    assert second[0]["created"] == "123456.0"
    assert calls == [["4321"]]


def test_process_start_failure_leaves_binding_timestamp_absent(monkeypatch):
    tmux._PROCESS_START_CACHE.clear()
    monkeypatch.setattr(
        tmux, "_run",
        lambda args, **kw: "%9\twork:1.0\tcodex\tAgent\twork\t/tmp/project\t4321\t@1\n",
    )
    monkeypatch.setattr(tmux, "_process_starts", lambda pids: {})
    assert "created" not in tmux.list_panes()[0]


def test_process_start_parser(monkeypatch):
    class Result:
        returncode = 0
        stdout = "4321 Thu Jul 16 09:30:00 2026\n"

    monkeypatch.setattr(tmux.subprocess, "run", lambda *args, **kwargs: Result())
    assert isinstance(tmux._process_started("4321"), float)
    assert tmux._process_started("not-a-pid") is None


def test_create_session_uses_detached_argument_list(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tmux,
        "_run",
        lambda args, **kw: calls.append(args) or "%7\twork:0.0\n",
    )

    result = tmux.create_session("work", "/srv/products", ["codex", "--safe"])

    assert result == {"pane_id": "%7", "target": "work:0.0"}
    assert calls == [[
        "new-session", "-d", "-P", "-F", tmux._CREATE_FORMAT,
        "-s", "work", "-c", "/srv/products", "--", "codex", "--safe",
    ]]


def test_create_window_uses_detached_argument_list(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tmux,
        "_run",
        lambda args, **kw: calls.append(args) or "%8\twork:2.0\n",
    )

    result = tmux.create_window("work", "api", "/srv/api")

    assert result == {"pane_id": "%8", "target": "work:2.0"}
    assert calls == [[
        "new-window", "-d", "-P", "-F", tmux._CREATE_FORMAT,
        "-t", "work", "-n", "api", "-c", "/srv/api",
    ]]


def test_create_pane_maps_direction_and_size(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tmux,
        "_run",
        lambda args, **kw: calls.append(args) or "%9\twork:2.1\n",
    )

    result = tmux.create_pane("%8", "/srv/api", "side_by_side", 40, ["claude"])

    assert result == {"pane_id": "%9", "target": "work:2.1"}
    assert calls == [[
        "split-window", "-d", "-P", "-F", tmux._CREATE_FORMAT,
        "-t", "%8", "-h", "-p", "40", "-c", "/srv/api", "--", "claude",
    ]]

    calls.clear()
    tmux.create_pane("%8", "/srv/api", "stacked", 50)
    assert "-v" in calls[0]

"""Thin, safe wrappers around the tmux CLI.

All calls use argument lists (never a shell string), so pane content and text
can't break out into shell execution. Named keys are allow-listed; pane ids are
format-checked. Literal text is always sent with `send-keys -l --`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from datetime import datetime
from typing import Dict, List, Optional

# Fields we pull for every pane. Order matters: parsed positionally below.
_PANE_FORMAT = (
    "#{pane_id}\t"
    "#{session_name}:#{window_index}.#{pane_index}\t"
    "#{pane_current_command}\t"
    "#{pane_title}\t"
    "#{window_name}\t"
    "#{pane_current_path}\t"
    "#{pane_pid}\t"
    "#{window_id}"
)

# Named keys the API is allowed to send. Anything else is rejected.
ALLOWED_KEYS = {
    "Enter", "Escape", "Tab", "BTab", "Space", "BSpace",
    "Up", "Down", "Left", "Right",
    "Home", "End", "PageUp", "PageDown",
    "C-c", "C-d", "C-z", "C-a", "C-e", "C-u", "C-k", "C-l",
    "C-r", "C-w", "C-o", "C-n", "C-p",
}

_PANE_ID_RE = re.compile(r"^%\d+$")
_TARGET_RE = re.compile(r"^[\w.\-]+:\d+\.\d+$")
_PROCESS_START_CACHE: Dict[tuple[str, str], float] = {}
_PROCESS_START_LOCK = threading.RLock()
_PROCESS_START_QUERY_LOCK = threading.Lock()


class TmuxError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("tmux") is not None


def disable_automatic_rename() -> None:
    """Turn off tmux's global automatic window renaming."""
    _run(["set-window-option", "-g", "automatic-rename", "off"])


def _run(args: List[str], timeout: float = 3.0) -> str:
    try:
        out = subprocess.run(
            ["tmux"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise TmuxError("tmux not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise TmuxError("tmux call timed out: %s" % " ".join(args)) from exc
    if out.returncode != 0:
        raise TmuxError(out.stderr.strip() or "tmux exited %d" % out.returncode)
    return out.stdout


def valid_pane_id(pane_id: str) -> bool:
    return bool(_PANE_ID_RE.match(pane_id) or _TARGET_RE.match(pane_id))


def list_panes() -> List[Dict[str, str]]:
    """Every pane on the server, as dicts with tmux metadata."""
    try:
        raw = _run(["list-panes", "-a", "-F", _PANE_FORMAT])
    except TmuxError:
        return []
    panes: List[Dict[str, str]] = []
    for line in raw.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        while len(parts) < 8:
            parts.append("")
        panes.append(
            {"id": parts[0], "target": parts[1], "cmd": parts[2],
             "title": parts[3], "window": parts[4], "path": parts[5],
             "pid": parts[6], "window_id": parts[7]}
        )
    live_keys = {(pane["id"], pane["pid"]) for pane in panes if pane["pid"].isdigit()}
    with _PROCESS_START_LOCK:
        for key in list(_PROCESS_START_CACHE):
            if key not in live_keys:
                _PROCESS_START_CACHE.pop(key, None)
    with _PROCESS_START_QUERY_LOCK:
        with _PROCESS_START_LOCK:
            missing = [pane["pid"] for pane in panes
                       if pane["pid"].isdigit()
                       and (pane["id"], pane["pid"]) not in _PROCESS_START_CACHE]
        starts = _process_starts(missing) if missing else {}
        with _PROCESS_START_LOCK:
            for pane in panes:
                key = (pane["id"], pane["pid"])
                if pane["pid"] in starts:
                    _PROCESS_START_CACHE.setdefault(key, starts[pane["pid"]])
    for pane in panes:
        key = (pane["id"], pane["pid"])
        with _PROCESS_START_LOCK:
            started = _PROCESS_START_CACHE.get(key)
        if started:
            pane["created"] = str(started)
    return panes


def _process_started(pid: str) -> Optional[float]:
    """Best-effort POSIX process start time for a pane incarnation.

    tmux has no portable pane-created format field.  `ps` is invoked with an
    argument list and a numeric PID only; failure means the binding remains
    read-only.
    """
    return _process_starts([pid]).get(pid)


def _process_starts(pids: List[str]) -> Dict[str, float]:
    """Resolve process start times in bounded batches (one `ps` per 128 PIDs)."""
    safe = list(dict.fromkeys(pid for pid in pids if pid.isdigit()))
    starts: Dict[str, float] = {}
    for index in range(0, len(safe), 128):
        chunk = safe[index:index + 128]
        try:
            result = subprocess.run(
                ["ps", "-o", "pid=,lstart=", "-p", ",".join(chunk)],
                capture_output=True, text=True, timeout=2.0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or parts[0] not in chunk:
                continue
            try:
                starts[parts[0]] = datetime.strptime(
                    " ".join(parts[1].split()), "%a %b %d %H:%M:%S %Y"
                ).astimezone().timestamp()
            except ValueError:
                continue
    return starts


def capture(pane_id: str, scrollback: int = 0) -> Optional[str]:
    """Pane content as plain text, or None if the pane is gone.

    With scrollback > 0, also include that many lines of history above the
    visible screen (tmux `-S -N`), so the detail view and link extraction see
    more than just the current screen. scrollback == 0 keeps the visible-only
    behaviour. `-J` joins wrapped lines, so a wrapped URL stays on one line.
    """
    if not valid_pane_id(pane_id):
        return None
    args = ["capture-pane", "-p", "-J"]
    if scrollback > 0:
        args += ["-S", "-%d" % scrollback]
    args += ["-t", pane_id]
    try:
        return _run(args)
    except TmuxError:
        return None


def exists(pane_id: str) -> bool:
    if not valid_pane_id(pane_id):
        return False
    for p in list_panes():
        if p["id"] == pane_id:
            return True
    return False


def send_key(pane_id: str, key: str) -> None:
    if not valid_pane_id(pane_id):
        raise TmuxError("bad pane id")
    if key not in ALLOWED_KEYS:
        raise TmuxError("key not allowed: %s" % key)
    _run(["send-keys", "-t", pane_id, key])


def send_literal(pane_id: str, text: str, enter: bool = False) -> None:
    if not valid_pane_id(pane_id):
        raise TmuxError("bad pane id")
    # -l literal, -- ends option parsing so leading dashes in text are safe.
    _run(["send-keys", "-t", pane_id, "-l", "--", text])
    if enter:
        _run(["send-keys", "-t", pane_id, "Enter"])


def send_chars(pane_id: str, chars: str) -> None:
    """Send raw characters as a keypress (e.g. a menu digit '1').

    Unlike send_literal this does not use -l, so single characters register as
    discrete key presses the TUI reacts to immediately.
    """
    if not valid_pane_id(pane_id):
        raise TmuxError("bad pane id")
    if not chars or len(chars) > 8 or not chars.isprintable():
        raise TmuxError("bad chars")
    _run(["send-keys", "-t", pane_id, "--", chars])

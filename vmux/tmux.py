"""Thin, safe wrappers around the tmux CLI.

All calls use argument lists (never a shell string), so pane content and text
can't break out into shell execution. Named keys are allow-listed; pane ids are
format-checked. Literal text is always sent with `send-keys -l --`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Dict, List, Optional

# Fields we pull for every pane. Order matters: parsed positionally below.
_PANE_FORMAT = "#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_command}\t#{pane_title}\t#{window_name}"

# Named keys the API is allowed to send. Anything else is rejected.
ALLOWED_KEYS = {
    "Enter", "Escape", "Tab", "Space", "BSpace",
    "Up", "Down", "Left", "Right",
    "Home", "End", "PageUp", "PageDown",
    "C-c", "C-d", "C-z", "C-a", "C-e", "C-u", "C-k", "C-l", "C-r", "C-w",
}

_PANE_ID_RE = re.compile(r"^%\d+$")
_TARGET_RE = re.compile(r"^[\w.\-]+:\d+\.\d+$")


class TmuxError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("tmux") is not None


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
    """Every pane on the server, as dicts: id, target, cmd, title."""
    try:
        raw = _run(["list-panes", "-a", "-F", _PANE_FORMAT])
    except TmuxError:
        return []
    panes: List[Dict[str, str]] = []
    for line in raw.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        while len(parts) < 5:
            parts.append("")
        panes.append(
            {"id": parts[0], "target": parts[1], "cmd": parts[2],
             "title": parts[3], "window": parts[4]}
        )
    return panes


def capture(pane_id: str) -> Optional[str]:
    """Visible pane content as plain text, or None if the pane is gone."""
    if not valid_pane_id(pane_id):
        return None
    try:
        # -p print to stdout, -J join wrapped lines, visible screen only.
        return _run(["capture-pane", "-p", "-J", "-t", pane_id])
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
    _run(["send-keys", "-t", pane_id, chars])

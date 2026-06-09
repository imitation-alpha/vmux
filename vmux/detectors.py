"""Turn raw pane text into a status + a tappable menu.

This is the "route" and "cheapen" half of the pipeline. Two strategies:

  * claude-code: parse the TUI selection box (the `╭ │ ❯` characters) so the
    numbered choices become buttons.
  * generic: regex for `(y/n)`, "Do you want to...", "Press enter to...".

Everything here is a pure function of text (plus a `changed` hint), which makes
it unit-testable without a live tmux. See tests/test_detectors.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import (
    KIND_CLAUDE,
    KIND_GENERIC,
    KIND_SHELL,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_NEEDS_INPUT,
    STATUS_WORKING,
    MenuOption,
)

# Shell process names → an idle pane is just a prompt.
SHELL_CMDS = {"zsh", "bash", "fish", "sh", "dash", "ksh", "tcsh",
              "-zsh", "-bash", "-fish", "-sh"}

# Glyphs Claude Code cycles through in its working spinner / pane title.
SPINNER_GLYPHS = set("✳✶✻✺✢✽✿❋·◐◓◑◒✷*")


def is_spinner(ch: str) -> bool:
    """True for the star glyphs above or anything in the Braille block, which
    Claude (and many CLIs) use for animated spinners (⠂ ⠐ ⣾ ...)."""
    return bool(ch) and (ch in SPINNER_GLYPHS or 0x2800 <= ord(ch) <= 0x28FF)

# Strong textual signals that a pane is running Claude Code.
CLAUDE_TEXT_MARKERS = (
    "esc to interrupt",
    "? for shortcuts",
    "claude code",
    "welcome to claude",
    "bypassing permissions",
    "/help for help",
)

# Box-drawing chars to peel off the ends of a captured line.
_BOX_CHARS = "│┃|─━╭╮╰╯┌┐└┘├┤┬┴┼ ╌╍ "

# A numbered option line, after box stripping: "❯ 1. Yes" / "2) No".
_OPTION_RE = re.compile(r"^(?P<cur>[❯»▶➤>*])?\s*(?P<num>\d+)[.)]\s+(?P<label>.+?)\s*$")

_SELECT_CURSORS = "❯»▶➤>*"


# Hard wall-clock cap for matching user-supplied patterns. The `regex` module
# raises TimeoutError if a single match exceeds this — so even a catastrophic
# pattern (any shape, incl. alternation overlap the linter can't catch) can't
# wedge the poll loop. On timeout we treat it as "no match".
_RX_TIMEOUT = 0.05


def _safe_search(rx, text):
    try:
        return rx.search(text, timeout=_RX_TIMEOUT)
    except TimeoutError:
        return None


@dataclass
class DetectResult:
    status: str
    question: Optional[str] = None
    menu: Optional[List[MenuOption]] = None

    def menu_list(self) -> List[MenuOption]:
        return self.menu or []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _clean(line: str) -> str:
    """Strip box-drawing chrome from both ends of a captured line."""
    return line.strip().strip(_BOX_CHARS).strip()


def _is_border(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return all(c in _BOX_CHARS or c in "─━╌╍" for c in s)


def _last_lines(text: str, n: int) -> List[str]:
    lines = text.splitlines()
    # drop trailing blank lines tmux leaves behind
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-n:]


# --------------------------------------------------------------------------- #
# kind classification
# --------------------------------------------------------------------------- #

def looks_like_claude(text: str, title: str) -> bool:
    low = text.lower()
    if any(m in low for m in CLAUDE_TEXT_MARKERS):
        return True
    t = title.strip()
    if t and is_spinner(t[0]):
        return True
    return False


def classify_kind(cmd: str, title: str, text: str) -> str:
    if looks_like_claude(text, title):
        return KIND_CLAUDE
    base = (cmd or "").split("/")[-1]
    if base in SHELL_CMDS:
        return KIND_SHELL
    return KIND_GENERIC


# --------------------------------------------------------------------------- #
# claude-code menu parsing
# --------------------------------------------------------------------------- #

def parse_claude_menu(lines: List[str]) -> Tuple[Optional[str], List[MenuOption]]:
    """Find a numbered selection box near the bottom of the screen.

    Returns (question, options). Empty options means no active dialog.
    """
    cleaned = [_clean(ln) for ln in lines]

    # locate option lines
    opt_idx: List[int] = []
    parsed = {}
    for i, c in enumerate(cleaned):
        m = _OPTION_RE.match(c)
        if m:
            opt_idx.append(i)
            parsed[i] = m

    if not opt_idx:
        return None, []

    # take the last contiguous-ish block of options (allow 1-line gaps)
    block = [opt_idx[-1]]
    for i in reversed(opt_idx[:-1]):
        if block[0] - i <= 2:
            block.insert(0, i)
        else:
            break

    options: List[MenuOption] = []
    seen = set()
    any_cursor = False
    for i in block:
        m = parsed[i]
        num = m.group("num")
        if num in seen:
            continue
        seen.add(num)
        selected = bool(m.group("cur") and m.group("cur") in _SELECT_CURSORS)
        any_cursor = any_cursor or selected
        options.append(MenuOption(key=num, label=m.group("label").strip(), selected=selected))

    # Confidence gate: a real Claude selection box always marks the active
    # choice with a cursor (❯). Requiring it avoids reading a plain numbered
    # list in the agent's output as a dialog.
    if not any_cursor:
        return None, []

    # question: nearest meaningful line above the block
    question = None
    for i in range(block[0] - 1, -1, -1):
        c = cleaned[i]
        if not c or _is_border(c):
            continue
        if _OPTION_RE.match(c):
            continue
        question = c
        break

    return question, options


# --------------------------------------------------------------------------- #
# generic prompt parsing
# --------------------------------------------------------------------------- #

def _build_generic_menu(line: str) -> List[MenuOption]:
    low = line.lower()
    if re.search(r"\(y/n\)|\[y/n\]|\by/n\b|\(yes/no\)", low):
        # default = the capitalised letter, e.g. "[Y/n]" defaults to Yes
        yes_default = bool(re.search(r"Y/n|\[Y|\(Y", line))
        no_default = bool(re.search(r"y/N|\[N|\(N", line))
        return [
            MenuOption(key="y", label="Yes", selected=yes_default),
            MenuOption(key="n", label="No", selected=no_default),
        ]
    if re.search(r"press \[?enter\]?|press return|press any key", low):
        return [MenuOption(key="enter", label="Continue", selected=True)]
    return []


def _generic_needs_input(lines: List[str], cfg) -> Tuple[Optional[str], List[MenuOption]]:
    # check the last few lines for a prompt pattern
    tail = lines[-6:]
    for ln in reversed(tail):
        capped = ln[:2000]   # cap input fed to user-configurable regexes (ReDoS defense)
        for rx in cfg.generic_re:
            if _safe_search(rx, capped):
                return _clean(ln) or ln.strip(), _build_generic_menu(ln)
    return None, []


# --------------------------------------------------------------------------- #
# top-level detect
# --------------------------------------------------------------------------- #

def _has_error(text: str, cfg) -> bool:
    # cap input length fed to user-configurable regexes (defense against ReDoS)
    tail = "\n".join(text.splitlines()[-15:])[-4000:]
    return any(_safe_search(rx, tail) for rx in cfg.error_re)


def detect(text: str, kind: str, changed: bool, cfg, title: str = "") -> DetectResult:
    """Decide a pane's status (and any menu) from its captured text.

    Priority: needs_input > error > working > idle. needs_input wins because a
    blocked agent is the whole reason this tool exists.
    """
    if text is None:
        return DetectResult(status=STATUS_IDLE)

    lines = _last_lines(text, 40)
    low = text.lower()

    if kind == KIND_CLAUDE:
        question, options = parse_claude_menu(lines)
        if options:
            return DetectResult(STATUS_NEEDS_INPUT, question, options)
        working = "esc to interrupt" in low
        if working:
            return DetectResult(STATUS_WORKING)
        if _has_error(text, cfg):
            return DetectResult(STATUS_ERROR)
        # title spinner with fresh output but no explicit interrupt line
        if changed and is_spinner(title.strip()[:1]):
            return DetectResult(STATUS_WORKING)
        return DetectResult(STATUS_IDLE)

    # generic + shell share the regex strategy
    question, options = _generic_needs_input(lines, cfg)
    if question is not None:
        return DetectResult(STATUS_NEEDS_INPUT, question, options)
    if _has_error(text, cfg):
        return DetectResult(STATUS_ERROR)
    if changed:
        return DetectResult(STATUS_WORKING)
    return DetectResult(STATUS_IDLE)

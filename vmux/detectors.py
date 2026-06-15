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

# Strong textual signals that a pane is running Claude Code. These appear in the
# body even when the title is plain, so kind detection doesn't rely on the glyph.
CLAUDE_TEXT_MARKERS = (
    "esc to interrupt",
    "? for shortcuts",
    "claude code",
    "welcome to claude",
    "bypassing permissions",
    "/help for help",
    "/clear to save",
    "shift+tab to cycle",
    "auto mode on",
)

# Box-drawing chars to peel off the ends of a captured line.
_BOX_CHARS = "│┃|─━╭╮╰╯┌┐└┘├┤┬┴┼ ╌╍ "

# A numbered option line, after box stripping. Accepts "❯ 1. Yes", "2) No",
# "3: Maybe", and "[4] label" (Claude/Codex/CLI dialogs vary).
_OPTION_RE = re.compile(
    r"^(?P<cur>[❯»▶➤›▸→>*])?\s*(?:\[(?P<bnum>\d+)\]|(?P<num>\d+)[.):])\s+(?P<label>.+?)\s*$"
)

_SELECT_CURSORS = "❯»▶➤›▸→>*"

# Claude's live "working" line: a verb with an ellipsis and a running counter,
# e.g. "Photosynthesizing… (59s · ↓ 3.4k tokens)". The ellipsis + "(<n>s" is the
# tell — a *finished* turn reads "Cooked for 27s" (past tense, no ellipsis), so
# this never fires on an idle pane.
_CLAUDE_WORKING_RE = re.compile(r"(?:…|\.\.\.)\s*\(\s*\d+\s*[smhd]")

# Claude's composer/idle chrome: when any of these show, Claude is at the prompt
# waiting for a new instruction (idle), not running.
_CLAUDE_IDLE_RE = re.compile(r"--\s*INSERT\s*--|new task\?|/clear to save|\? for shortcuts")

# Words that mark a line as a real prompt introducing a choice list (used to gate
# the cursor-less numbered-menu detector against ordinary numbered prose).
_PROMPT_WORDS = re.compile(
    r"\b(select|choose|choice|which|want|allow|approve|approval|proceed|continue|"
    r"overwrite|confirm|replace|apply|run|trust|permission|grant|accept|reject|"
    r"do you|would you|press)\b",
    re.IGNORECASE,
)

# Option labels that, when chosen, drop the user into a free-text reply instead
# of committing a canned choice ("No, and tell Claude what to do differently
# (esc)", Codex's "tell Codex what to do"). Used only to *annotate* an option
# that already passed the menu gates — never to create a dialog.
_FREEFORM_RE = re.compile(
    r"tell (?:claude|codex|the agent|it) what|type your own|write your own|"
    r"something else|none of (?:these|the above)|\bdifferently\b|\(esc\)|\bother\b",
    re.IGNORECASE,
)


def _is_freeform(label: str) -> bool:
    return bool(_FREEFORM_RE.search(label or ""))


def _is_braille(ch: str) -> bool:
    """True only for Braille-block glyphs (⠂ ⣾ …), Claude's *animated* spinner.
    Deliberately excludes ✳ and the star glyphs, which are static brand marks and
    would otherwise flap an idle pane to 'working' on every redraw."""
    return bool(ch) and 0x2800 <= ord(ch) <= 0x28FF


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


# Bounds for how much prompt text above a menu we carry into `question`. Enough
# to keep a multi-line prompt intact (a command/diff preview, a plan body, a
# trust warning) without ever pulling in unbounded scrollback.
_Q_MAX_LINES = 6
_Q_MAX_LINE_CHARS = 200
_Q_MAX_CHARS = 600

# A box top/bottom border or a section rule contains these. Checked on the RAW
# line (a cleaned border collapses to ""), and deliberately stricter than
# _BOX_CHARS so an inner padding line ("│       │", just sides + spaces) reads as
# blank, not as a boundary.
_BORDER_RULE_CHARS = set("─━╌╍═╭╮╰╯┌┐└┘├┤┬┴┼")


def _question_above(lines: List[str], cleaned: List[str], start_idx: int) -> Optional[str]:
    """Collect the prompt text directly above a menu block, newline-joined.

    Walks up from start_idx-1: skips interleaved option lines, stops at a box
    border / section rule or a paragraph gap (2+ blank lines), tolerates a single
    blank line inside the prompt, and is bounded by line/char caps. Runs only
    AFTER a menu passed its gates, so it never manufactures a dialog — it only
    chooses which already-visible lines become the question string. A single-line
    prompt comes back as that one line (no newline), preserving prior behaviour.
    """
    collected: List[str] = []
    total = 0
    blanks = 0
    for i in range(start_idx - 1, -1, -1):
        if any(ch in _BORDER_RULE_CHARS for ch in lines[i]):
            break                         # a box border / section rule bounds the prompt
        c = cleaned[i]
        if not c:
            if not collected:
                continue                  # padding between the box top and the options
            blanks += 1
            if blanks >= 2:
                break                     # a paragraph gap marks the top of this prompt
            continue
        if _OPTION_RE.match(c):
            continue                      # an interleaved / earlier option line
        blanks = 0
        if len(collected) >= _Q_MAX_LINES:
            break
        line = c[:_Q_MAX_LINE_CHARS]
        collected.insert(0, line)
        total += len(line)
        if total >= _Q_MAX_CHARS:
            break
    if not collected:
        return None
    text = "\n".join(collected)
    if len(text) > _Q_MAX_CHARS:
        text = text[:_Q_MAX_CHARS].rstrip() + "…"
    return text


# --------------------------------------------------------------------------- #
# kind classification
# --------------------------------------------------------------------------- #

def looks_like_claude(text: str, title: str) -> bool:
    low = text.lower()
    if any(m in low for m in CLAUDE_TEXT_MARKERS):
        return True
    t = title.strip()
    if t and is_spinner(t[0]):            # ✳/braille brand glyph in the pane title
        return True
    return False


def classify_kind(cmd: str, title: str, text: str) -> str:
    base = (cmd or "").split("/")[-1]
    if base == "codex":
        return KIND_GENERIC               # Codex CLI -> generic path (cursor-less dialogs)
    if looks_like_claude(text, title):
        return KIND_CLAUDE
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
        num = m.group("num") or m.group("bnum")
        if num in seen:
            continue
        seen.add(num)
        selected = bool(m.group("cur") and m.group("cur") in _SELECT_CURSORS)
        any_cursor = any_cursor or selected
        label = m.group("label").strip()
        options.append(MenuOption(key=num, label=label, selected=selected, freeform=_is_freeform(label)))

    # Confidence gate: a real Claude selection box always marks the active
    # choice with a cursor (❯). Requiring it avoids reading a plain numbered
    # list in the agent's output as a dialog.
    if not any_cursor:
        return None, []

    # question: the prompt text directly above the block (multi-line aware, so a
    # command/diff/plan preview or trust warning is carried, not truncated).
    question = _question_above(lines, cleaned, block[0])

    return question, options


def parse_question_menu(lines: List[str]) -> Tuple[Optional[str], List[MenuOption]]:
    """A numbered choice list introduced by a question, even with no ❯ cursor —
    e.g. Codex/CLI approval dialogs ("1) Allow  2) Deny", "1. Apply patch ...").

    Conservative on purpose: the options must (a) be 2+, (b) sit at the very
    bottom of the screen (only blanks/borders after them), and (c) be introduced
    by a real prompt line (ends with "?" or contains an approval word). This keeps
    ordinary numbered prose in agent output from being read as a dialog.
    """
    cleaned = [_clean(ln) for ln in lines]
    opt_idx: List[int] = []
    parsed = {}
    for i, c in enumerate(cleaned):
        m = _OPTION_RE.match(c)
        if m:
            opt_idx.append(i)
            parsed[i] = m
    if len(opt_idx) < 2:
        return None, []

    block = [opt_idx[-1]]
    for i in reversed(opt_idx[:-1]):
        if block[0] - i <= 2:
            block.insert(0, i)
        else:
            break
    if len(block) < 2:
        return None, []

    # nothing but blanks/borders may follow the block (it's the active dialog)
    for c in cleaned[block[-1] + 1:]:
        if c and not _is_border(c):
            return None, []

    question = _question_above(lines, cleaned, block[0])
    if not question or "optional" in question.lower():
        return None, []
    # the prompt-introducing line is the last one (closest to the options); keep
    # the gate exactly as strict as before by checking only that line.
    last = question.splitlines()[-1]
    if not (last.rstrip().endswith("?") or _PROMPT_WORDS.search(last)):
        return None, []

    options: List[MenuOption] = []
    seen = set()
    for i in block:
        m = parsed[i]
        num = m.group("num") or m.group("bnum")
        if num in seen:
            continue
        seen.add(num)
        sel = bool(m.group("cur") and m.group("cur") in _SELECT_CURSORS)
        label = m.group("label").strip()
        options.append(MenuOption(key=num, label=label, selected=sel, freeform=_is_freeform(label)))
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
    # single-default confirm, e.g. npm's "Ok to proceed? (y)" or "... (N)"
    m = re.search(r"\(([yn])\)\s*[:?]?\s*$", line.strip(), re.IGNORECASE)
    if m:
        d = m.group(1).lower()
        return [
            MenuOption(key="y", label="Yes", selected=(d == "y")),
            MenuOption(key="n", label="No", selected=(d == "n")),
        ]
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
        # working: an explicit interrupt line or the live "Verbing… (12s …)" spinner
        if "esc to interrupt" in low or _CLAUDE_WORKING_RE.search(text):
            return DetectResult(STATUS_WORKING)
        # composer/prompt visible -> Claude is waiting for a new instruction (idle).
        # Checked before error/title-spinner so a ready prompt never reads as either.
        if _CLAUDE_IDLE_RE.search(text):
            return DetectResult(STATUS_IDLE)
        if _has_error(text, cfg):
            return DetectResult(STATUS_ERROR)
        # last resort: an *animated* braille spinner in the title with fresh output
        # (the static ✳ brand glyph is intentionally excluded — see _is_braille)
        if changed and _is_braille(title.strip()[:1]):
            return DetectResult(STATUS_WORKING)
        return DetectResult(STATUS_IDLE)

    # generic + shell (incl. Codex CLI): selection box, then cursor-less numbered
    # dialog, then the regex prompts (y/n, "press enter", user patterns)
    question, options = parse_claude_menu(lines)
    if options:
        return DetectResult(STATUS_NEEDS_INPUT, question, options)
    question, options = parse_question_menu(lines)
    if options:
        return DetectResult(STATUS_NEEDS_INPUT, question, options)
    question, options = _generic_needs_input(lines, cfg)
    if question is not None:
        return DetectResult(STATUS_NEEDS_INPUT, question, options)
    if "esc to interrupt" in low:
        return DetectResult(STATUS_WORKING)
    if _has_error(text, cfg):
        return DetectResult(STATUS_ERROR)
    if changed:
        return DetectResult(STATUS_WORKING)
    return DetectResult(STATUS_IDLE)

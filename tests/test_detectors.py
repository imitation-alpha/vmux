"""Unit tests for the detector pure-functions, no live tmux needed.

Fixtures mimic real captured panes: Claude Code selection boxes, working
spinners, idle prompts, and generic (y/n) shell prompts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux.config import Config
from vmux.detectors import classify_kind, detect, parse_claude_menu, parse_question_menu
from vmux.models import (
    KIND_CLAUDE,
    KIND_GENERIC,
    KIND_SHELL,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_NEEDS_INPUT,
    STATUS_WORKING,
    MenuOption,
)

CFG = Config()

CLAUDE_DIALOG = """\
 Some earlier output from the agent doing work.
 Read auth.py (42 lines)

╭─────────────────────────────────────────────────────────╮
│ Do you want to make this edit to auth.py?                 │
│                                                           │
│ ❯ 1. Yes                                                  │
│   2. Yes, and don't ask again this session                │
│   3. No, and tell Claude what to do differently (esc)     │
╰─────────────────────────────────────────────────────────╯
"""

CLAUDE_WORKING = """\
● I'll refactor the auth module now.

  Editing auth.py...

✳ Cogitating… (12s · ↑ 1.4k tokens · esc to interrupt)
"""

CLAUDE_IDLE = """\
● Done. The refactor is complete and tests pass.

╭──────────────────────────────────────────────────────────╮
│ >                                                          │
╰──────────────────────────────────────────────────────────╯
  ? for shortcuts
"""

SHELL_YN = """\
rlalpha@box ~/proj $ rm -rf build
remove build? (y/n)
"""

SHELL_IDLE = """\
rlalpha@box ~/proj $ ls
README.md  src  tests
rlalpha@box ~/proj $
"""

SHELL_ERROR = """\
rlalpha@box ~/proj $ python app.py
Traceback (most recent call last):
  File "app.py", line 3, in <module>
    import nope
ModuleNotFoundError: No module named 'nope'
"""


def test_classify_claude_by_text():
    assert classify_kind("node", "✳ doing things", CLAUDE_WORKING) == KIND_CLAUDE


def test_classify_claude_by_title_glyph():
    assert classify_kind("2.1.168", "✳ Understand the goal", "random text") == KIND_CLAUDE


def test_classify_shell():
    assert classify_kind("zsh", "alpha-machine", SHELL_IDLE) == KIND_SHELL


def test_classify_generic():
    assert classify_kind("node", "webpack", "Compiling modules 45%") == KIND_GENERIC


def test_claude_menu_parsed():
    question, options = parse_claude_menu(CLAUDE_DIALOG.splitlines())
    assert question == "Do you want to make this edit to auth.py?"
    assert [o.key for o in options] == ["1", "2", "3"]
    assert options[0].label == "Yes"
    assert options[0].selected is True
    assert options[1].selected is False


def test_claude_needs_input():
    res = detect(CLAUDE_DIALOG, KIND_CLAUDE, True, CFG, title="✳ task")
    assert res.status == STATUS_NEEDS_INPUT
    assert len(res.menu_list()) == 3


def test_claude_working():
    res = detect(CLAUDE_WORKING, KIND_CLAUDE, True, CFG, title="✳ task")
    assert res.status == STATUS_WORKING


def test_claude_idle():
    res = detect(CLAUDE_IDLE, KIND_CLAUDE, False, CFG, title="task")
    assert res.status == STATUS_IDLE


def test_shell_yn_needs_input():
    res = detect(SHELL_YN, KIND_SHELL, True, CFG, title="")
    assert res.status == STATUS_NEEDS_INPUT
    keys = [o.key for o in res.menu_list()]
    assert "y" in keys and "n" in keys


def test_shell_idle():
    res = detect(SHELL_IDLE, KIND_SHELL, False, CFG, title="")
    assert res.status == STATUS_IDLE


def test_shell_error():
    res = detect(SHELL_ERROR, KIND_SHELL, False, CFG, title="")
    assert res.status == STATUS_ERROR


def test_generic_working_on_change():
    res = detect("Compiling 12%\nCompiling 13%", KIND_GENERIC, True, CFG, title="")
    assert res.status == STATUS_WORKING


def test_no_false_menu_from_numbered_list():
    # A plain numbered list in output (no ❯ cursor) must NOT read as a dialog.
    text = "Here are steps:\n1. clone\n2. build\nDone, back to work."
    res = detect(text, KIND_CLAUDE, True, CFG, title="task")
    assert res.status != STATUS_NEEDS_INPUT
    question, options = parse_claude_menu(text.splitlines())
    assert options == []


# --- real-capture-grounded regressions: stable active/idle + option lists --- #

# Claude at the prompt after finishing a turn: the composer chrome + a past-tense
# "for Ns" line. This must stay IDLE even when the screen just changed (the bug
# was the ✳ brand glyph flapping it to "working").
CLAUDE_IDLE_COMPOSER = """\
✻ Cooked for 27s
※ recap: fixed three buttons and shipped build 20.0.4.
─────────────────────────────────── fix-non-functional-buttons ──
❯
──────────────────────────────────────────────────────────────────
  -- INSERT -- ⏵⏵ auto mode on (shift+tab to cycle)   new task? /clear to save 198.6k tokens
"""

# Claude mid-turn: the live "Verbing… (Ns · ↓ tokens)" counter, NO "esc to
# interrupt" visible (it can scroll off behind the slash-command popup).
CLAUDE_SPINNER_ONLY = """\
· Photosynthesizing… (59s · ↓ 3.4k tokens)
─────────────────────────────────── vmux-pin-panes-tree-view ──
❯ /
"""

# Codex / generic CLI approval dialog: numbered choices with no ❯ cursor.
CODEX_APPROVAL = """\
codex wants to run: rm -rf build/

Allow this command?
  1) Yes, run it
  2) Yes, and don't ask again
  3) No, tell Codex what to do
"""

# An *optional* survey must not nag as needs_input.
CLAUDE_OPTIONAL_SURVEY = """\
※ recap: published 19 reels.
● How is Claude doing this session? (optional)
  1: Bad    2: Fine   3: Good   0: Dismiss
─────────────────────────────────── launchd-queue-publisher ──
❯
  -- INSERT -- auto mode on   new task? /clear to save 884.9k tokens
"""


def test_claude_idle_does_not_flap_when_changed():
    # changed=True and a ✳ spinner-glyph title — must still be IDLE, not working.
    res = detect(CLAUDE_IDLE_COMPOSER, KIND_CLAUDE, True, CFG, title="✳ fix-buttons")
    assert res.status == STATUS_IDLE


def test_claude_working_from_spinner_line():
    res = detect(CLAUDE_SPINNER_ONLY, KIND_CLAUDE, True, CFG, title="⠐ vmux")
    assert res.status == STATUS_WORKING


def test_codex_routed_to_generic_by_cmd():
    assert classify_kind("codex", "alpha", "some text") == KIND_GENERIC


def test_codex_cursorless_menu_detected():
    res = detect(CODEX_APPROVAL, KIND_GENERIC, True, CFG, title="")
    assert res.status == STATUS_NEEDS_INPUT
    assert [o.key for o in res.menu_list()] == ["1", "2", "3"]
    q, opts = parse_question_menu(CODEX_APPROVAL.splitlines())
    # multi-line capture now carries the command preview above the question
    assert q == "codex wants to run: rm -rf build/\nAllow this command?"


def test_optional_survey_not_needs_input():
    res = detect(CLAUDE_OPTIONAL_SURVEY, KIND_CLAUDE, True, CFG, title="✳ task")
    assert res.status == STATUS_IDLE
    # and the generic path also refuses an "(optional)" prompt
    _, opts = parse_question_menu(CLAUDE_OPTIONAL_SURVEY.splitlines())
    assert opts == []


def test_question_menu_ignores_plain_numbered_prose():
    # numbered list not at the bottom and no prompt word -> not a dialog
    text = "Plan:\n1. clone repo\n2. build it\nThen I'll continue."
    q, opts = parse_question_menu(text.splitlines())
    assert opts == []


# --- multi-line / compound question capture (user report #1) ---------------- #

# A Claude box whose decision body (a command preview) sits above the question.
CLAUDE_MULTILINE = """\
 earlier output above the box
╭───────────────────────────────────────────╮
│ Bash command                              │
│   rm -rf build/                           │
│ Do you want to proceed?                    │
│ ❯ 1. Yes                                   │
│   2. No                                    │
╰───────────────────────────────────────────╯
"""


def test_claude_single_line_question_unchanged():
    # regression: a one-line prompt still yields exactly that line, no newline.
    q, _ = parse_claude_menu(CLAUDE_DIALOG.splitlines())
    assert q == "Do you want to make this edit to auth.py?"
    assert "\n" not in q


def test_claude_multiline_question_joined():
    q, opts = parse_claude_menu(CLAUDE_MULTILINE.splitlines())
    assert [o.key for o in opts] == ["1", "2"]
    assert "\n" in q
    assert "rm -rf build/" in q
    assert q.endswith("Do you want to proceed?")
    # the box border bounds it — content above the ╭ is not pulled in.
    assert "earlier output above the box" not in q


def test_codex_multiline_question_captures_command():
    q, _ = parse_question_menu(CODEX_APPROVAL.splitlines())
    assert q == "codex wants to run: rm -rf build/\nAllow this command?"


def test_question_stops_at_blank_gap():
    text = "\n".join([
        "lots of earlier output here",
        "",
        "",
        "Allow this command?",
        "  1) Yes",
        "  2) No",
    ])
    q, opts = parse_question_menu(text.splitlines())
    assert q == "Allow this command?"          # 2+ blank lines bound the prompt
    assert [o.key for o in opts] == ["1", "2"]


def test_question_bounded_to_six_lines():
    ctx = ["context line %d" % i for i in range(12)]
    text = "\n".join(ctx + ["Proceed?", "  1) Yes", "  2) No"])
    q, _ = parse_question_menu(text.splitlines())
    assert q.count("\n") <= 5                   # at most 6 lines carried
    assert q.splitlines()[-1] == "Proceed?"


# --- freeform option flag (user report #2, detection half) ------------------ #

def test_claude_freeform_option_flagged():
    _, opts = parse_claude_menu(CLAUDE_DIALOG.splitlines())
    assert opts[0].freeform is False           # "Yes"
    assert opts[2].freeform is True            # "No, and tell Claude what to do differently (esc)"


def test_codex_freeform_option_flagged():
    _, opts = parse_question_menu(CODEX_APPROVAL.splitlines())
    assert opts[0].freeform is False           # "Yes, run it"
    assert opts[2].freeform is True            # "No, tell Codex what to do"


def test_freeform_never_creates_a_menu():
    # a "differently" label in plain numbered prose (no ❯ cursor) is not a dialog
    text = "Here are options:\n1. do X\n2. do Y differently\nback to work."
    res = detect(text, KIND_CLAUDE, True, CFG, title="task")
    assert res.status != STATUS_NEEDS_INPUT
    _, opts = parse_claude_menu(text.splitlines())
    assert opts == []


def test_menuoption_exposes_freeform():
    d = MenuOption(key="1", label="Yes").to_dict()
    assert d["freeform"] is False
    assert set(d) == {"key", "label", "selected", "freeform"}


# --- npm single-default confirm "(y)" --------------------------------------- #

NPM_CONFIRM = """\
rlalpha@box ~/proj $ npm create vite@latest
Need to install the following packages: create-vite
Ok to proceed? (y)
"""


def test_npm_single_default_confirm_buttons():
    res = detect(NPM_CONFIRM, KIND_GENERIC, True, CFG, title="")
    assert res.status == STATUS_NEEDS_INPUT
    assert [o.key for o in res.menu_list()] == ["y", "n"]
    assert res.menu_list()[0].selected is True   # (y) marks Yes as the default


# --- negative regressions: chrome must NOT become a fake dialog ------------- #

CLAUDE_SLASH_POPUP = """\
● working on it
─────────────────────────────────── my-project ──
❯ /co
  /compact      Compact the conversation
  /config       Open the config
  /context      Show context usage
  -- INSERT -- auto mode on   new task? /clear to save 12.0k tokens
"""

GENERIC_PROGRESS = """\
Building project…
[####------] 40%
Step 3/10 compiling modules
"""


def test_slash_popup_stays_non_needs_input():
    res = detect(CLAUDE_SLASH_POPUP, KIND_CLAUDE, True, CFG, title="✳ proj")
    assert res.status != STATUS_NEEDS_INPUT


def test_progress_output_stays_working():
    res = detect(GENERIC_PROGRESS, KIND_GENERIC, True, CFG, title="")
    assert res.status == STATUS_WORKING
    _, opts = parse_question_menu(GENERIC_PROGRESS.splitlines())
    assert opts == []

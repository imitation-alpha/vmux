"""Unit tests for the detector pure-functions, no live tmux needed.

Fixtures mimic real captured panes: Claude Code selection boxes, working
spinners, idle prompts, and generic (y/n) shell prompts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux.config import Config
from vmux.detectors import classify_kind, detect, parse_claude_menu
from vmux.models import (
    KIND_CLAUDE,
    KIND_GENERIC,
    KIND_SHELL,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_NEEDS_INPUT,
    STATUS_WORKING,
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

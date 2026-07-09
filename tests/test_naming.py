"""Tests for choose_name — the pane display-name selection logic."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux.poller import choose_name

BASE = dict(title="✳ do the thing", window="⠂ my-window", target="edu:1.1", command="/usr/bin/zsh")


def test_override_always_wins():
    assert choose_name("title", override_name="Pinned", **BASE) == "Pinned"
    assert choose_name("target", override_name="Pinned", **BASE) == "Pinned"


def test_title_mode_strips_spinner():
    assert choose_name("title", override_name=None, **BASE) == "do the thing"


def test_window_mode_strips_spinner():
    assert choose_name("window", override_name=None, **BASE) == "my-window"


def test_target_mode():
    assert choose_name("target", override_name=None, **BASE) == "edu:1.1"


def test_stable_target_part_modes():
    base = {**BASE, "target": "work:1.3"}
    assert choose_name("pane", override_name=None, **base) == "3"
    assert choose_name("window_pane", override_name=None, **base) == "1:3"
    assert choose_name("session_pane", override_name=None, **base) == "work:3"
    assert choose_name("session_window_pane", override_name=None, **base) == "work:1:3"


def test_malformed_target_part_mode_falls_back_to_target():
    base = {**BASE, "target": "not-a-canonical-target"}
    assert choose_name("session_window_pane", override_name=None, **base) == "not-a-canonical-target"


def test_command_mode_basename():
    assert choose_name("command", override_name=None, **BASE) == "zsh"


def test_smart_mode_uses_supplied_smart_name():
    assert choose_name("smart", override_name=None, smart_name="cc:fix-auth", **BASE) == "cc:fix-auth"


def test_smart_mode_falls_back_to_target_without_smart_name():
    assert choose_name("smart", override_name=None, smart_name="", **BASE) == "edu:1.1"


def test_empty_source_falls_back_to_target():
    assert choose_name("title", override_name=None,
                       title="   ", window="", target="edu:1.1", command="") == "edu:1.1"


def test_unknown_mode_defaults_to_title():
    assert choose_name("nonsense", override_name=None, **BASE) == "do the thing"

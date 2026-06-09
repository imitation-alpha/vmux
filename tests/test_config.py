"""Tests for editable settings: apply_patch validation, regex recompile, and the
overlay round-trip that keeps the hand-authored config.yaml intact."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux import config
from vmux.detectors import detect
from vmux.models import KIND_GENERIC, STATUS_NEEDS_INPUT


def test_editable_dict_has_expected_keys():
    d = config.Config().editable_dict()
    assert set(d) == {
        "poll_interval", "auto_discover", "include_shells", "naming_mode",
        "overrides", "generic_prompt_patterns", "error_patterns",
    }


def test_naming_mode():
    c = config.Config()
    assert c.naming_mode == "title"
    c.apply_patch({"naming_mode": "window"})
    assert c.naming_mode == "window"
    with pytest.raises(ValueError):
        c.apply_patch({"naming_mode": "bogus"})


def test_poll_interval_clamped():
    c = config.Config()
    c.apply_patch({"poll_interval": 99})
    assert c.poll_interval == 10.0
    c.apply_patch({"poll_interval": 0.001})
    assert c.poll_interval == 0.2


def test_booleans_and_overrides():
    c = config.Config()
    c.apply_patch({"include_shells": True, "auto_discover": False,
                   "overrides": [{"target": "a:1.1", "name": "API", "kind": "generic"}]})
    assert c.include_shells is True and c.auto_discover is False
    assert c.overrides["a:1.1"].name == "API"
    assert c.overrides["a:1.1"].kind == "generic"


def test_bad_kind_rejected():
    with pytest.raises(ValueError):
        config.Config().apply_patch({"overrides": [{"target": "a:1.1", "kind": "nope"}]})


def test_bad_regex_rejected():
    with pytest.raises(ValueError):
        config.Config().apply_patch({"generic_prompt_patterns": ["("]})  # unbalanced paren


def test_catastrophic_regex_rejected():
    c = config.Config()
    for bad in [r"(a+)+$", r"(.*)*", r"(a+){2,}", r"(\d+)+"]:
        with pytest.raises(ValueError):
            c.apply_patch({"generic_prompt_patterns": [bad]})
    # the shipped defaults must still pass the guard
    c.apply_patch({"generic_prompt_patterns": list(config.DEFAULT_GENERIC_PROMPTS),
                   "error_patterns": list(config.DEFAULT_ERROR_PATTERNS)})


def test_alternation_redos_is_time_bounded():
    # (a|a)+$ slips past the nested-quantifier linter but backtracks
    # catastrophically. The regex-module timeout in detect() must keep this
    # from hanging the poll loop — detect should return promptly, not wedge.
    from vmux.detectors import detect
    from vmux.models import KIND_GENERIC
    c = config.Config()
    c.apply_patch({"generic_prompt_patterns": [r"(a|a)+$"]})
    res = detect("trigger:\n" + ("a" * 44) + "X", KIND_GENERIC, False, c, "")
    assert res is not None  # returned at all == the timeout fired instead of hanging


def test_too_many_patterns_rejected():
    with pytest.raises(ValueError):
        config.Config().apply_patch({"error_patterns": ["x"] * (config.MAX_PATTERNS + 1)})


def test_patterns_recompile_and_affect_detection():
    c = config.Config()
    c.apply_patch({"generic_prompt_patterns": [r"DEPLOY NOW\?"]})
    res = detect("about to ship\nDEPLOY NOW?", KIND_GENERIC, False, c, "")
    assert res.status == STATUS_NEEDS_INPUT


def test_overlay_roundtrip_preserves_yaml(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("server:\n  token: secret123\npoll_interval: 0.7\n")

    c = config.load(str(cfgfile))
    assert c.token == "secret123"

    c.apply_patch({"poll_interval": 2.5, "include_shells": True,
                   "overrides": [{"target": "work:1.1", "name": "Refactor", "kind": "claude-code"}]})
    config.save_overlay(c)
    assert (tmp_path / "vmux-settings.json").exists()

    # reload: overlay merged, original YAML (token) untouched
    c2 = config.load(str(cfgfile))
    assert c2.token == "secret123"
    assert c2.poll_interval == 2.5
    assert c2.include_shells is True
    assert c2.overrides["work:1.1"].name == "Refactor"
    # the hand-authored YAML file itself was not rewritten
    assert "secret123" in cfgfile.read_text()


def test_corrupt_overlay_ignored(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("poll_interval: 0.5\n")
    (tmp_path / "vmux-settings.json").write_text("{ not valid json ")
    c = config.load(str(cfgfile))  # should not raise
    assert c.poll_interval == 0.5

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
        "poll_interval", "capture_lines", "auto_discover", "include_shells", "naming_mode",
        "overrides", "generic_prompt_patterns", "error_patterns",
        "usage_enabled", "usage_quota_refresh", "usage_report_refresh",
        "usage_alert_threshold",
    }
    # the exec'd command must never be exposed to (or settable from) the UI
    assert "usage_command" not in d


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


def test_capture_lines_default_and_clamp():
    c = config.Config()
    assert c.capture_lines == 200            # bumped default (was visible-only)
    c.apply_patch({"capture_lines": 99999})
    assert c.capture_lines == 2000           # clamped to ceiling
    c.apply_patch({"capture_lines": 1})
    assert c.capture_lines == 40             # clamped to floor
    with pytest.raises(ValueError):
        c.apply_patch({"capture_lines": "lots"})


def test_capture_lines_yaml_clamped(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("capture_lines: 5\n")   # below floor -> clamped up
    c = config.load(str(cfgfile))
    assert c.capture_lines == 40


def test_booleans_and_overrides():
    c = config.Config()
    c.apply_patch({"include_shells": True, "auto_discover": False,
                   "overrides": [{"target": "a:1.1", "name": "API", "kind": "generic"}]})
    assert c.include_shells is True and c.auto_discover is False
    assert c.overrides["a:1.1"].name == "API"
    assert c.overrides["a:1.1"].kind == "generic"


def test_override_star_roundtrips():
    c = config.Config()
    c.apply_patch({"overrides": [{"target": "a:1.1", "star": True}]})
    assert c.overrides["a:1.1"].star is True
    assert c.editable_dict()["overrides"][0]["star"] is True
    # unstarring a pane that has no name/kind drops the override entirely
    c.apply_patch({"overrides": [{"target": "a:1.1", "star": False}]})
    assert "a:1.1" not in c.overrides
    # star coexists with a rename
    c.apply_patch({"overrides": [{"target": "b:1.1", "name": "X", "star": True}]})
    assert c.overrides["b:1.1"].star is True and c.overrides["b:1.1"].name == "X"
    # legacy "pin" key still loads as star (back-compat)
    c.apply_patch({"overrides": [{"target": "c:1.1", "pin": True}]})
    assert c.overrides["c:1.1"].star is True


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


def test_usage_yaml_section(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text(
        "usage:\n"
        "  enabled: false\n"
        "  command: /opt/bin/tokscale\n"
        "  quota_refresh: 5\n"        # below floor -> clamped up
        "  report_refresh: 99999\n"   # above ceiling -> clamped down
        "  alert_threshold: 150\n"
    )
    c = config.load(str(cfgfile))
    assert c.usage_enabled is False
    assert c.usage_command == "/opt/bin/tokscale"
    assert c.usage_quota_refresh == 30.0
    assert c.usage_report_refresh == 3600.0
    assert c.usage_alert_threshold == 100.0


def test_usage_yaml_can_enable_tracking(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("usage:\n  enabled: true\n")
    c = config.load(str(cfgfile))
    assert c.usage_enabled is True


def test_usage_defaults():
    c = config.Config()
    assert c.usage_enabled is False
    assert c.usage_command == "tokscale"
    assert c.usage_quota_refresh == 180.0
    assert c.usage_report_refresh == 300.0
    assert c.usage_alert_threshold == 20.0


def test_usage_patch_clamps_and_command_immutable():
    c = config.Config()
    c.apply_patch({"usage_enabled": False, "usage_quota_refresh": 1,
                   "usage_report_refresh": 999999, "usage_alert_threshold": -5})
    assert c.usage_enabled is False
    assert c.usage_quota_refresh == 30.0
    assert c.usage_report_refresh == 3600.0
    assert c.usage_alert_threshold == 0.0
    with pytest.raises(ValueError):
        c.apply_patch({"usage_quota_refresh": "soon"})
    # usage_command silently ignored by apply_patch — HTTP must not set it
    c.apply_patch({"usage_command": "/tmp/evil"})
    assert c.usage_command == "tokscale"


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

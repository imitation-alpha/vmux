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
        "usage_alert_threshold", "usage_hidden_quota_providers",
        "usage_hidden_quota_metrics", "experimental_agent_workspace_enabled",
    }
    # the exec'd command must never be exposed to (or settable from) the UI
    assert "usage_command" not in d
    assert not any(k.startswith("auto_naming_") for k in d)


def test_naming_mode():
    c = config.Config()
    assert c.naming_mode == "session_window_pane"
    c.apply_patch({"naming_mode": "window"})
    assert c.naming_mode == "window"
    c.apply_patch({"naming_mode": "pane"})
    assert c.naming_mode == "pane"
    c.apply_patch({"naming_mode": "window_pane"})
    assert c.naming_mode == "window_pane"
    c.apply_patch({"naming_mode": "session_pane"})
    assert c.naming_mode == "session_pane"
    c.apply_patch({"naming_mode": "smart"})
    assert c.naming_mode == "smart"
    c.apply_patch({"naming_mode": "session_window_pane"})
    assert c.naming_mode == "session_window_pane"
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


def test_tmux_auto_rename_disabled_by_default():
    assert config.Config().disable_tmux_auto_rename is True


def test_tmux_auto_rename_yaml_can_opt_out(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("tmux:\n  disable_auto_rename: false\n")
    c = config.load(str(cfgfile))
    assert c.disable_tmux_auto_rename is False


def test_creation_enabled_requires_a_yaml_boolean(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text('creation:\n  enabled: "false"\n')

    with pytest.raises(SystemExit, match="creation.enabled must be true or false"):
        config.load(str(cfgfile))


def test_naming_mode_loads_from_yaml(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("naming_mode: smart\n")
    c = config.load(str(cfgfile))
    assert c.naming_mode == "smart"


def test_auto_naming_yaml_section_is_yaml_only(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text(
        "auto_naming:\n"
        "  ai_enabled: true\n"
        "  ai_backend: local\n"
        "  ai_programs: claude,codex\n"
        "  prefix_apps:\n"
        "    codex: cx\n"
        "  max_len: 999\n"
        "  timeout: 999\n"
        "  local_url: http://127.0.0.1:9999/v1/chat/completions\n"
        "  local_api_key: secret-token\n"
        "  antigravity_flags: [--sandbox]\n"
    )
    c = config.load(str(cfgfile))
    assert c.auto_naming_ai_enabled is True
    assert c.auto_naming_ai_backend == "local"
    assert c.auto_naming_ai_programs == ["claude", "codex"]
    assert c.auto_naming_prefix_apps["codex"] == "cx"
    assert c.auto_naming_max_len == 80
    assert c.auto_naming_timeout == 300.0
    assert c.auto_naming_local_url == "http://127.0.0.1:9999/v1/chat/completions"
    assert c.auto_naming_local_api_key == "secret-token"
    assert c.auto_naming_antigravity_flags == ["--sandbox"]
    assert c.auto_naming_cache_path == str(tmp_path / "vmux-names.json")
    assert "local_api_key" not in c.editable_dict()


def test_bad_auto_naming_backend_exits(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("auto_naming:\n  ai_backend: nope\n")
    with pytest.raises(SystemExit):
        config.load(str(cfgfile))


def test_booleans_and_overrides():
    c = config.Config()
    c.apply_patch({"include_shells": True, "auto_discover": False,
                   "overrides": [{"target": "a:1.1", "name": "API", "kind": "generic"}]})
    assert c.include_shells is True and c.auto_discover is False
    assert c.overrides["a:1.1"].name == "API"
    assert c.overrides["a:1.1"].kind == "generic"


def test_agent_kind_overrides():
    c = config.Config()
    c.apply_patch({"overrides": [
        {"target": "g:1.1", "kind": "grok"},
        {"target": "o:1.1", "kind": "opencode"},
        {"target": "a:1.2", "kind": "antigravity"},
    ]})
    assert c.overrides["g:1.1"].kind == "grok"
    assert c.overrides["o:1.1"].kind == "opencode"
    assert c.overrides["a:1.2"].kind == "antigravity"


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


def test_yaml_pane_star_and_legacy_pin(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text(
        "panes:\n"
        "  - target: work:1.1\n"
        "    name: API refactor\n"
        "    kind: claude-code\n"
        "    star: true\n"
        "  - target: old:2.1\n"
        "    pin: true\n"
    )
    c = config.load(str(cfgfile))
    assert c.overrides["work:1.1"].star is True
    assert c.overrides["work:1.1"].name == "API refactor"
    assert c.overrides["work:1.1"].kind == "claude-code"
    assert c.overrides["old:2.1"].star is True


def test_bad_kind_rejected():
    with pytest.raises(ValueError):
        config.Config().apply_patch({"overrides": [{"target": "a:1.1", "kind": "nope"}]})


def test_codex_kind_override_is_accepted():
    cfg = config.Config()
    cfg.apply_patch({"overrides": [{"target": "a:1.1", "kind": "codex"}]})
    assert cfg.overrides["a:1.1"].kind == "codex"


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
    assert c.usage_hidden_quota_providers == []
    assert c.usage_hidden_quota_metrics == []


def test_usage_quota_visibility_is_trimmed_deduplicated_and_exact():
    c = config.Config()
    c.apply_patch({
        "usage_hidden_quota_providers": [" Copilot ", "Copilot", "copilot", ""],
        "usage_hidden_quota_metrics": [
            {"provider": " Copilot ", "label": " Premium requests "},
            {"provider": "Copilot", "label": "Premium requests"},
            {"provider": "copilot", "label": "Premium requests"},
            {"provider": "Copilot", "label": ""},
        ],
    })
    assert c.usage_hidden_quota_providers == ["Copilot", "copilot"]
    assert c.usage_hidden_quota_metrics == [
        {"provider": "Copilot", "label": "Premium requests"},
        {"provider": "copilot", "label": "Premium requests"},
    ]


@pytest.mark.parametrize("patch", [
    {"usage_hidden_quota_providers": "Copilot"},
    {"usage_hidden_quota_providers": [1]},
    {"usage_hidden_quota_metrics": {}},
    {"usage_hidden_quota_metrics": ["Copilot:Premium requests"]},
    {"usage_hidden_quota_metrics": [{"provider": "Copilot"}]},
    {"usage_hidden_quota_providers": ["x"] * (config.MAX_HIDDEN_QUOTA_ENTRIES + 1)},
    {"usage_hidden_quota_metrics": [
        {"provider": "Copilot", "label": str(index)}
        for index in range(config.MAX_HIDDEN_QUOTA_ENTRIES + 1)
    ]},
])
def test_usage_quota_visibility_rejects_invalid_bounded_values_without_partial_update(patch):
    c = config.Config(
        usage_hidden_quota_providers=["Existing"],
        usage_hidden_quota_metrics=[{"provider": "Existing", "label": "Daily"}],
    )
    before = c.editable_dict()
    with pytest.raises(ValueError):
        c.apply_patch(patch)
    assert c.editable_dict() == before


def test_usage_quota_visibility_overlay_roundtrip(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("usage:\n  enabled: true\n")
    c = config.load(str(cfgfile))
    c.apply_patch({
        "usage_hidden_quota_providers": ["Copilot", "Temporarily absent"],
        "usage_hidden_quota_metrics": [
            {"provider": "Antigravity", "label": "Daily requests"},
        ],
    })
    config.save_overlay(c)

    restored = config.load(str(cfgfile))
    assert restored.usage_hidden_quota_providers == ["Copilot", "Temporarily absent"]
    assert restored.usage_hidden_quota_metrics == [
        {"provider": "Antigravity", "label": "Daily requests"},
    ]


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


def test_experimental_agent_workspace_is_strict_opt_in_and_overlay_persisted(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("agents:\n  enabled: true\n  retention_days: 45\n")

    fresh = config.load(str(cfgfile))
    assert fresh.experimental_agent_workspace_enabled is False
    assert fresh.agent_retention_days == 45

    fresh.apply_patch({"experimental_agent_workspace_enabled": True})
    config.save_overlay(fresh)
    restored = config.load(str(cfgfile))
    assert restored.experimental_agent_workspace_enabled is True
    assert config._load_overlay(restored.overlay_path)[
        "experimental_agent_workspace_enabled"
    ] is True


@pytest.mark.parametrize("invalid", [1, 0, "true", "false", None, [], {}])
def test_experimental_agent_workspace_rejects_non_booleans(invalid):
    cfg = config.Config()
    with pytest.raises(
        ValueError,
        match="experimental_agent_workspace_enabled must be true or false",
    ):
        cfg.apply_patch({"experimental_agent_workspace_enabled": invalid})
    assert cfg.experimental_agent_workspace_enabled is False


def test_upgraded_overlay_without_experimental_key_defaults_off(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("agents:\n  enabled: true\n")
    (tmp_path / "vmux-settings.json").write_text('{"poll_interval": 2.0}')

    loaded = config.load(str(cfgfile))

    assert loaded.poll_interval == 2.0
    assert loaded.experimental_agent_workspace_enabled is False


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


def test_creation_yaml_is_canonical_and_not_editable(tmp_path):
    root = tmp_path / "products"
    root.mkdir()
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text(
        "creation:\n"
        "  enabled: true\n"
        "  roots:\n"
        "    - label: Products\n"
        f"      path: {root}\n"
        "  runtimes:\n"
        "    codex: [codex, --safe]\n"
        "    grok: [grok]\n"
    )

    c = config.load(str(cfgfile))

    assert c.creation_configured is True
    assert c.creation_roots == [config.CreationRoot("Products", str(root.resolve()))]
    assert c.creation_runtimes == {"codex": ["codex", "--safe"], "grok": ["grok"]}
    editable = c.editable_dict()
    assert not any(key.startswith("creation") for key in editable)


def test_creation_rejects_invalid_roots_and_disables_without_valid_root(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text(
        "creation:\n"
        "  enabled: true\n"
        "  roots:\n"
        "    - label: Missing\n"
        "      path: /definitely/not/a/vmux/root\n"
    )

    c = config.load(str(cfgfile))

    assert c.creation_roots == []
    assert c.creation_configured is False
    assert c.creation_setup_reason == "No valid creation roots are configured."


def test_creation_rejects_unknown_runtime_preset(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("creation:\n  runtimes:\n    arbitrary: [sh]\n")
    with pytest.raises(SystemExit, match="unsupported presets"):
        config.load(str(cfgfile))

"""Unit tests for the CLI startup path. uvicorn is stubbed so no server starts."""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux import __main__ as cli
from vmux import tmux


def _stub_startup(monkeypatch):
    calls = {}
    monkeypatch.setattr(cli.tmux, "available", lambda: True)
    monkeypatch.setattr(cli.tmux, "list_panes", lambda: [{"id": "%1", "target": "w:1.1"}])
    monkeypatch.setattr(cli, "create_app", lambda cfg: {"cfg": cfg})

    def run(app, host, port, log_level):
        calls["run"] = {
            "app": app,
            "host": host,
            "port": port,
            "log_level": log_level,
        }

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=run))
    return calls


def test_main_herdr_probes_explicit_session_without_touching_tmux(tmp_path, monkeypatch):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("terminal:\n  provider: herdr\n  herdr:\n    session: agents\n")
    calls = {}

    class Provider:
        def probe(self):
            calls["probe"] = True

    monkeypatch.setattr(cli, "provider_for_config", lambda cfg: Provider())
    monkeypatch.setattr(cli.tmux, "available", lambda: (_ for _ in ()).throw(AssertionError("tmux checked")))
    monkeypatch.setattr(cli.tmux, "list_panes", lambda: (_ for _ in ()).throw(AssertionError("tmux listed")))
    monkeypatch.setattr(cli.tmux, "disable_automatic_rename", lambda: (_ for _ in ()).throw(AssertionError("tmux mutated")))
    monkeypatch.setattr(cli, "create_app", lambda cfg, provider=None: {"cfg": cfg, "provider": provider})
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *args, **kwargs: None))

    assert cli.main(["-c", str(cfgfile)]) == 0
    assert calls == {"probe": True}


def test_main_disables_tmux_automatic_rename_by_default(monkeypatch):
    _stub_startup(monkeypatch)
    rename_calls = []
    monkeypatch.setattr(
        cli.tmux,
        "disable_automatic_rename",
        lambda: rename_calls.append("disabled"),
        raising=False,
    )

    assert cli.main([]) == 0

    assert rename_calls == ["disabled"]


def test_main_skips_auto_rename_when_config_opts_out(tmp_path, monkeypatch):
    _stub_startup(monkeypatch)
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("tmux:\n  disable_auto_rename: false\n")
    rename_calls = []
    monkeypatch.setattr(
        cli.tmux,
        "disable_automatic_rename",
        lambda: rename_calls.append("disabled"),
        raising=False,
    )

    assert cli.main(["-c", str(cfgfile)]) == 0

    assert rename_calls == []


def test_main_warns_and_continues_when_auto_rename_disable_fails(monkeypatch, capsys):
    calls = _stub_startup(monkeypatch)

    def fail():
        raise tmux.TmuxError("permission denied")

    monkeypatch.setattr(cli.tmux, "disable_automatic_rename", fail, raising=False)

    assert cli.main([]) == 0

    assert calls["run"]["host"] == "127.0.0.1"
    assert "could not disable tmux automatic rename: permission denied" in capsys.readouterr().err


def test_main_keeps_no_server_auto_rename_error_quiet(monkeypatch, capsys):
    _stub_startup(monkeypatch)

    def fail():
        raise tmux.TmuxError("no server running on /private/tmp/tmux-501/default")

    monkeypatch.setattr(cli.tmux, "disable_automatic_rename", fail, raising=False)

    assert cli.main([]) == 0

    assert "automatic rename" not in capsys.readouterr().err


def test_main_prints_app_address_hint_for_token_lan_bind(monkeypatch, capsys):
    _stub_startup(monkeypatch)
    monkeypatch.setattr(cli.tmux, "disable_automatic_rename", lambda: None, raising=False)

    assert cli.main(["--host", "0.0.0.0", "--token", "s3cret"]) == 0

    captured = capsys.readouterr()
    out = captured.out
    assert "app server address: <this-machine>:8787" in out
    assert "s3cret" not in out
    assert "listener serves plain HTTP" in captured.err
    assert "never expose bare vmux HTTP publicly" in captured.err


def test_main_refuses_non_loopback_bind_without_token(monkeypatch):
    _stub_startup(monkeypatch)

    with pytest.raises(SystemExit, match="Refusing to bind 0.0.0.0 with an empty token"):
        cli.main(["--host", "0.0.0.0"])


def test_main_omits_app_address_hint_on_localhost(monkeypatch, capsys):
    _stub_startup(monkeypatch)
    monkeypatch.setattr(cli.tmux, "disable_automatic_rename", lambda: None, raising=False)

    assert cli.main([]) == 0

    captured = capsys.readouterr()
    assert "app server address" not in captured.out
    assert "plain HTTP" not in captured.err


@pytest.mark.parametrize("override", ["session", "provider"])
def test_main_merges_terminal_overrides_before_validation(tmp_path, monkeypatch, override):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("terminal:\n  provider: herdr\ntmux:\n  disable_auto_rename: false\n")
    calls = _stub_startup(monkeypatch)
    providers = []

    class Provider:
        def probe(self):
            providers.append("probed")

    monkeypatch.setattr(cli, "provider_for_config", lambda cfg: Provider())
    monkeypatch.setattr(cli, "create_app", lambda cfg, provider=None: {"cfg": cfg})
    args = ["--herdr-session", " agents "] if override == "session" else ["--terminal-provider", "tmux"]
    assert cli.main(["-c", str(cfgfile), *args]) == 0
    cfg = calls["run"]["app"]["cfg"]
    assert cfg.terminal_provider == ("herdr" if override == "session" else "tmux")
    assert cfg.herdr_session == ("agents" if override == "session" else "")
    assert providers == (["probed"] if override == "session" else [])


def test_main_still_rejects_missing_effective_herdr_session(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("terminal:\n  provider: herdr\n")
    with pytest.raises(SystemExit, match="terminal.herdr.session is required"):
        cli.main(["-c", str(cfgfile)])

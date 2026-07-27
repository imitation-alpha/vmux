"""Selection semantics that depend on the detected pane kind."""

from vmux.config import Config
from vmux.models import KIND_CODEX, MenuOption, PaneState
from vmux.poller import Hub


def _codex_hub() -> Hub:
    hub = Hub(Config())
    hub.states["%1"] = PaneState(
        id="%1",
        target="work:1.1",
        name="Codex",
        kind=KIND_CODEX,
        status="needs_input",
        menu=[
            MenuOption(key="1", label="Use the API"),
            MenuOption(key="2", label="None of the above", freeform=True),
        ],
    )
    return hub


def test_codex_normal_option_sends_digit_then_enter(monkeypatch):
    hub = _codex_hub()
    calls = []
    monkeypatch.setattr("vmux.poller.tmux.send_chars", lambda pane, key: calls.append(("chars", pane, key)))
    monkeypatch.setattr("vmux.poller.tmux.send_key", lambda pane, key: calls.append(("key", pane, key)))
    monkeypatch.setattr("vmux.poller.tmux.send_literal", lambda *args, **kwargs: calls.append(("literal", args, kwargs)))

    hub.do_select("%1", "1")

    assert calls == [("chars", "%1", "1"), ("key", "%1", "Enter")]


def test_codex_freeform_option_is_staged_without_enter(monkeypatch):
    hub = _codex_hub()
    calls = []
    monkeypatch.setattr("vmux.poller.tmux.send_chars", lambda pane, key: calls.append(("chars", pane, key)))
    monkeypatch.setattr("vmux.poller.tmux.send_key", lambda pane, key: calls.append(("key", pane, key)))

    hub.do_select("%1", "2")

    assert calls == [("chars", "%1", "2")]

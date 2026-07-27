"""Guarded tmux control used only after AgentService revalidates a binding."""

from __future__ import annotations

from .. import tmux


class TmuxRuntimeController:
    """A deliberately tiny controller shared by supported TUI runtimes.

    Runtime-specific safety lives in the observer/service capability checks.
    This class only performs literal, argument-list tmux calls.
    """

    runtime = "terminal"

    def send_message(self, pane_id: str, text: str) -> None:
        tmux.send_literal(pane_id, text, enter=True)

    def reply(self, pane_id: str, key: str, runtime: str) -> None:
        if key == "enter":
            tmux.send_key(pane_id, "Enter")
        elif runtime == "claude":
            tmux.send_chars(pane_id, key)
        else:
            tmux.send_literal(pane_id, key, enter=True)

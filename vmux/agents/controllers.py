"""Guarded tmux control used only after AgentService revalidates a binding."""

from __future__ import annotations

from typing import Callable, Optional

from .. import tmux


class TmuxRuntimeController:
    """A deliberately tiny controller shared by supported TUI runtimes.

    Runtime-specific safety lives in the observer/service capability checks.
    This class only performs literal, argument-list tmux calls.
    """

    runtime = "terminal"

    def __init__(
        self,
        action_runner: Optional[Callable[[str, Callable[[str], None]], str]] = None,
    ):
        self._action_runner = action_runner

    def _perform(self, pane_id: str, sender: Callable[[str], None]) -> None:
        if self._action_runner is None:
            sender(pane_id)
            return
        self._action_runner(pane_id, sender)

    def send_message(self, pane_id: str, text: str) -> None:
        self._perform(
            pane_id, lambda real: tmux.send_literal(real, text, enter=True)
        )

    def reply(self, pane_id: str, key: str, runtime: str) -> None:
        def send(real: str) -> None:
            if key == "enter":
                tmux.send_key(real, "Enter")
            elif runtime == "claude":
                tmux.send_chars(real, key)
            else:
                tmux.send_literal(real, key, enter=True)

        self._perform(pane_id, send)

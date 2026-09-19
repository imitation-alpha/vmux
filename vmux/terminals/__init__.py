"""Terminal provider selection."""

from __future__ import annotations

from .base import ProviderError, TerminalProvider
from .herdr_provider import HerdrProvider
from .tmux_provider import TmuxProvider


def provider_for_config(cfg) -> TerminalProvider:
    if cfg.terminal_provider == "herdr":
        return HerdrProvider(
            session=cfg.herdr_session,
            binary=cfg.herdr_binary,
            events=cfg.herdr_events,
            server_instance_id=cfg.server_instance_id,
        )
    return TmuxProvider()


__all__ = ["HerdrProvider", "ProviderError", "TerminalProvider", "TmuxProvider", "provider_for_config"]

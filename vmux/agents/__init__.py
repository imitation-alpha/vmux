"""Structured agent-session observation, persistence, and guarded control.

This package intentionally sits beside the pane model.  Pane capture remains a
terminal feature; agent context is reconstructed from the runtimes' structured,
append-only session records and only normalized data is persisted by vmux.
"""

from .service import AgentConflict, AgentNotFound, AgentService, AgentUnavailable

__all__ = ["AgentConflict", "AgentNotFound", "AgentService", "AgentUnavailable"]

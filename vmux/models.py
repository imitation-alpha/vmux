"""The state contract shared by the poller, the API, and the web UI.

One PaneState per tmux pane. This dict shape is the single source of truth that
the backend produces and the frontend renders. Keep it stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# Status values, in rough order of "how much it wants you".
STATUS_NEEDS_INPUT = "needs_input"  # a dialog is waiting on a human  (red, pulsing)
STATUS_ERROR = "error"              # recent traceback / error pattern (orange)
STATUS_WORKING = "working"          # output is scrolling             (yellow)
STATUS_IDLE = "idle"               # at a prompt, nothing to do       (green)
STATUS_OFFLINE = "offline"         # pane is gone                     (grey)

# Pane kinds.
KIND_CLAUDE = "claude-code"
KIND_GENERIC = "generic"
KIND_SHELL = "shell"


@dataclass
class MenuOption:
    """One tappable choice parsed out of an agent dialog."""

    key: str          # what identifies the choice ("1", "y", "enter")
    label: str        # human text shown on the button
    selected: bool = False  # currently highlighted in the TUI (the default)

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "selected": self.selected}


@dataclass
class PaneState:
    id: str                                   # tmux pane id, e.g. "%12" (stable key)
    target: str                               # session:window.pane (display + fallback)
    name: str                                 # friendly name (config override or derived)
    kind: str = KIND_SHELL                    # claude-code | generic | shell
    status: str = STATUS_IDLE
    title: str = ""                           # tmux pane title
    question: Optional[str] = None            # the prompt text, when needs_input
    menu: List[MenuOption] = field(default_factory=list)
    lines: List[str] = field(default_factory=list)   # captured visible lines (detail view)
    updated: float = 0.0                      # epoch seconds of last *change*
    changed: bool = False                     # changed since previous poll (working hint)

    def preview(self, n: int = 6) -> List[str]:
        """Last n non-empty-ish lines, for the grid card snippet."""
        trimmed = [ln for ln in self.lines if ln.strip()]
        return trimmed[-n:]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target": self.target,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "question": self.question,
            "menu": [m.to_dict() for m in self.menu],
            "preview": self.preview(),
            "lines": self.lines,
            "updated": self.updated,
            "changed": self.changed,
        }

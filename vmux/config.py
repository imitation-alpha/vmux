"""Config loading. YAML in, a validated Config object out.

Everything has a sane default so vmux runs with no config file at all
(pure auto-discovery against the live tmux server).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

DEFAULT_GENERIC_PROMPTS = [
    r"\(y/n\)",
    r"\(y/N\)",
    r"\[Y/n\]",
    r"\[y/N\]",
    r"Do you want to",
    r"Press enter to",
    r"Press \[enter\]",
    r"Continue\?",
    r"Proceed\?",
    r"\? \(y",
    r"Overwrite\?",
]

DEFAULT_ERROR_PATTERNS = [
    r"Traceback \(most recent call last\)",
    r"^\s*Error:",
    r"^\s*ERROR\b",
    r"panic:",
    r"fatal:",
    r"Unhandled exception",
    r"command not found",
]


@dataclass
class PaneOverride:
    target: str                      # session:window.pane to match
    name: Optional[str] = None
    kind: Optional[str] = None


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8787
    token: str = ""
    poll_interval: float = 0.7
    auto_discover: bool = True
    include_shells: bool = False
    overrides: Dict[str, PaneOverride] = field(default_factory=dict)  # keyed by target
    generic_prompt_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_GENERIC_PROMPTS))
    error_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_ERROR_PATTERNS))

    # compiled, filled in __post_init__
    generic_re: List["re.Pattern"] = field(default_factory=list, repr=False)
    error_re: List["re.Pattern"] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.generic_re = [re.compile(p) for p in self.generic_prompt_patterns]
        self.error_re = [re.compile(p, re.MULTILINE) for p in self.error_patterns]

    def validate(self) -> None:
        # The one footgun the README promises to fail-fast on.
        if self.host not in ("127.0.0.1", "localhost", "::1") and not self.token:
            raise SystemExit(
                "Refusing to bind %s with an empty token. Either bind 127.0.0.1 "
                "(reach it over SSH/Tailscale) or set server.token for LAN mode." % self.host
            )


def load(path: Optional[str]) -> Config:
    data: dict = {}
    if path:
        if not os.path.exists(path):
            raise SystemExit("config file not found: %s" % path)
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}

    server = data.get("server", {}) or {}
    discovery = data.get("discovery", {}) or {}
    detectors = data.get("detectors", {}) or {}

    overrides: Dict[str, PaneOverride] = {}
    for entry in data.get("panes", []) or []:
        target = entry.get("target")
        if not target:
            continue
        overrides[target] = PaneOverride(
            target=target, name=entry.get("name"), kind=entry.get("kind")
        )

    cfg = Config(
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8787)),
        token=str(server.get("token", "") or ""),
        poll_interval=float(data.get("poll_interval", 0.7)),
        auto_discover=bool(discovery.get("auto", True)),
        include_shells=bool(discovery.get("include_shells", False)),
        overrides=overrides,
        generic_prompt_patterns=detectors.get("generic_prompt_patterns", list(DEFAULT_GENERIC_PROMPTS)),
        error_patterns=detectors.get("error_patterns", list(DEFAULT_ERROR_PATTERNS)),
    )
    return cfg

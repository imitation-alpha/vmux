"""Config loading. YAML in, a validated Config object out.

Everything has a sane default so vmux runs with no config file at all
(pure auto-discovery against the live tmux server).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import regex  # supports per-call timeout= for bounded-time matching
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

# Limits for UI-supplied detector patterns (the editor holds the token, so this
# guards against fat-finger mistakes, not malice).
PANE_KINDS = {"claude-code", "generic", "shell"}
NAMING_MODES = {"title", "window", "target", "command"}
MAX_PATTERNS = 40
MAX_PATTERN_LEN = 200

# Crude ReDoS guard: reject a group containing * or + that is itself quantified —
# e.g. (a+)+, (.*)*, (a+){2,} — the dominant catastrophic-backtracking shape.
# Not exhaustive (a determined token-holder can still craft one, but they already
# have shell access), but it blocks the realistic fat-finger / content-trigger case.
_NESTED_QUANT = re.compile(r"\([^()]*[*+][^()]*\)\s*[*+{]")


@dataclass
class PaneOverride:
    target: str                      # session:window.pane to match
    name: Optional[str] = None
    kind: Optional[str] = None
    star: bool = False               # keep at top + visible even when offline


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8787
    token: str = ""
    poll_interval: float = 0.7
    auto_discover: bool = True
    include_shells: bool = False
    naming_mode: str = "title"   # title | window | target | command
    overrides: Dict[str, PaneOverride] = field(default_factory=dict)  # keyed by target
    generic_prompt_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_GENERIC_PROMPTS))
    error_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_ERROR_PATTERNS))

    # optional APNs push (YAML `push:` section only — not editable from the UI,
    # since it points at local key material). See vmux/push.py.
    apns_key_path: str = ""          # path to the APNs auth key
    apns_key_id: str = ""            # 10-char key id from the developer portal
    apns_team_id: str = ""           # 10-char Apple team id
    apns_topic: str = ""             # app bundle id, e.g. dev.example.vmux
    apns_environment: str = "sandbox"   # sandbox | production
    push_on_error: bool = False      # also push on error status (not just needs_input)
    push_cooldown: float = 30.0      # min seconds between pushes for the same pane

    # where registered device tokens persist (set by load(); like overlay_path)
    push_store_path: Optional[str] = field(default=None, repr=False)

    # optional tokscale usage/quota tracking (YAML `usage:` section). The
    # command itself is YAML-only — a string that gets exec'd must not be
    # settable over HTTP. See vmux/usage.py.
    usage_enabled: bool = True
    usage_command: str = "tokscale"      # may include args, e.g. "npx -y tokscale"
    usage_quota_refresh: float = 180.0   # seconds between `tokscale usage` calls
    usage_report_refresh: float = 300.0  # seconds between report scans (CPU-heavy)
    usage_alert_threshold: float = 20.0  # push when quota drops below this %; 0 = off

    # compiled, filled in __post_init__
    generic_re: List["re.Pattern"] = field(default_factory=list, repr=False)
    error_re: List["re.Pattern"] = field(default_factory=list, repr=False)

    # where UI-managed settings persist (set by load(); not part of editable_dict)
    overlay_path: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._recompile()

    def _recompile(self) -> None:
        # compiled with the `regex` module so detectors can match with a timeout=
        self.generic_re = [regex.compile(p) for p in self.generic_prompt_patterns]
        self.error_re = [regex.compile(p, regex.MULTILINE) for p in self.error_patterns]

    # -- the slice the Settings UI may read/write ------------------------- #
    def editable_dict(self) -> dict:
        return {
            "poll_interval": self.poll_interval,
            "auto_discover": self.auto_discover,
            "include_shells": self.include_shells,
            "naming_mode": self.naming_mode,
            "overrides": [
                {"target": o.target, "name": o.name, "kind": o.kind, "star": o.star}
                for o in self.overrides.values()
            ],
            "generic_prompt_patterns": list(self.generic_prompt_patterns),
            "error_patterns": list(self.error_patterns),
            "usage_enabled": self.usage_enabled,
            "usage_quota_refresh": self.usage_quota_refresh,
            "usage_report_refresh": self.usage_report_refresh,
            "usage_alert_threshold": self.usage_alert_threshold,
        }

    def apply_patch(self, data: dict) -> None:
        """Validate + apply a partial settings update in place. Raises ValueError
        on bad input (the server maps that to HTTP 400). Recompiles regexes so
        the change takes effect on the next poll."""
        if "poll_interval" in data:
            try:
                pi = float(data["poll_interval"])
            except (TypeError, ValueError):
                raise ValueError("poll_interval must be a number")
            self.poll_interval = min(10.0, max(0.2, pi))
        if "auto_discover" in data:
            self.auto_discover = bool(data["auto_discover"])
        if "include_shells" in data:
            self.include_shells = bool(data["include_shells"])
        if "naming_mode" in data:
            m = data["naming_mode"]
            if m not in NAMING_MODES:
                raise ValueError("bad naming_mode: %s" % m)
            self.naming_mode = m
        if "overrides" in data:
            ov: Dict[str, PaneOverride] = {}
            for e in (data["overrides"] or []):
                target = str(e.get("target") or "").strip()
                if not target:
                    continue
                kind = e.get("kind") or None
                if kind is not None and kind not in PANE_KINDS:
                    raise ValueError("bad kind: %s" % kind)
                name = e.get("name")
                if name is not None:
                    name = str(name)[:80] or None
                star = bool(e.get("star") or e.get("pin"))   # "pin" kept for back-compat
                if not (name or kind or star):
                    continue   # an override with nothing set is dropped
                ov[target] = PaneOverride(target=target, name=name, kind=kind, star=star)
            self.overrides = ov
        if "usage_enabled" in data:
            self.usage_enabled = bool(data["usage_enabled"])
        if "usage_quota_refresh" in data:
            try:
                v = float(data["usage_quota_refresh"])
            except (TypeError, ValueError):
                raise ValueError("usage_quota_refresh must be a number")
            self.usage_quota_refresh = min(3600.0, max(30.0, v))
        if "usage_report_refresh" in data:
            try:
                v = float(data["usage_report_refresh"])
            except (TypeError, ValueError):
                raise ValueError("usage_report_refresh must be a number")
            self.usage_report_refresh = min(3600.0, max(60.0, v))
        if "usage_alert_threshold" in data:
            try:
                v = float(data["usage_alert_threshold"])
            except (TypeError, ValueError):
                raise ValueError("usage_alert_threshold must be a number")
            self.usage_alert_threshold = min(100.0, max(0.0, v))
        # usage_command is deliberately NOT patchable: it is exec'd, so it may
        # only come from the local YAML file, never over the HTTP API.
        for key in ("generic_prompt_patterns", "error_patterns"):
            if key in data:
                pats = data[key]
                if not isinstance(pats, list):
                    raise ValueError("%s must be a list" % key)
                if len(pats) > MAX_PATTERNS:
                    raise ValueError("too many patterns (max %d)" % MAX_PATTERNS)
                clean: List[str] = []
                for p in pats:
                    p = str(p)
                    if len(p) > MAX_PATTERN_LEN:
                        raise ValueError("pattern too long (max %d chars)" % MAX_PATTERN_LEN)
                    if _NESTED_QUANT.search(p):
                        raise ValueError("rejected possibly-catastrophic regex (nested quantifier): %r" % p)
                    try:
                        regex.compile(p)
                    except regex.error as exc:
                        raise ValueError("bad regex %r: %s" % (p, exc))
                    clean.append(p)
                setattr(self, key, clean)
        self._recompile()

    def validate(self) -> None:
        # The one footgun the README promises to fail-fast on.
        if self.host not in ("127.0.0.1", "localhost", "::1") and not self.token:
            raise SystemExit(
                "Refusing to bind %s with an empty token. Either bind 127.0.0.1 "
                "(reach it over SSH/Tailscale) or set server.token for LAN mode." % self.host
            )


def _overlay_path_for(config_path: Optional[str]) -> str:
    """Where UI-managed settings live: next to the config file if one was given,
    else ~/.vmux/settings.json. Kept separate so the hand-authored config.yaml
    (comments + token) is never rewritten."""
    if config_path:
        d = os.path.dirname(os.path.abspath(config_path)) or "."
        return os.path.join(d, "vmux-settings.json")
    return os.path.expanduser("~/.vmux/settings.json")


def _load_overlay(path: str) -> Optional[dict]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_overlay(cfg: "Config") -> None:
    if not cfg.overlay_path:
        return
    d = os.path.dirname(cfg.overlay_path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = cfg.overlay_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cfg.editable_dict(), fh, indent=2)
    os.replace(tmp, cfg.overlay_path)  # atomic


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
    push = data.get("push", {}) or {}
    usage = data.get("usage", {}) or {}

    apns_env = str(push.get("environment", "sandbox") or "sandbox")
    if apns_env not in ("sandbox", "production"):
        raise SystemExit("push.environment must be 'sandbox' or 'production', got: %s" % apns_env)

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
        apns_key_path=os.path.expanduser(str(push.get("apns_key_path", "") or "")),
        apns_key_id=str(push.get("apns_key_id", "") or ""),
        apns_team_id=str(push.get("apns_team_id", "") or ""),
        apns_topic=str(push.get("apns_topic", "") or ""),
        apns_environment=apns_env,
        push_on_error=bool(push.get("on_error", False)),
        push_cooldown=min(3600.0, max(5.0, float(push.get("cooldown", 30.0)))),
        usage_enabled=bool(usage.get("enabled", True)),
        usage_command=str(usage.get("command", "tokscale") or "tokscale")[:200],
        usage_quota_refresh=min(3600.0, max(30.0, float(usage.get("quota_refresh", 180.0)))),
        usage_report_refresh=min(3600.0, max(60.0, float(usage.get("report_refresh", 300.0)))),
        usage_alert_threshold=min(100.0, max(0.0, float(usage.get("alert_threshold", 20.0)))),
    )
    # layer UI-managed settings (if any) over the YAML — overlay wins
    cfg.overlay_path = _overlay_path_for(path)
    cfg.push_store_path = os.path.join(os.path.dirname(cfg.overlay_path), "vmux-push.json")
    overlay = _load_overlay(cfg.overlay_path)
    if overlay:
        try:
            cfg.apply_patch(overlay)
        except ValueError:
            pass  # ignore a corrupt overlay rather than refuse to start
    return cfg

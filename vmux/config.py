"""Config loading. YAML in, a validated Config object out.

Everything has a sane default so vmux runs with no config file at all
(pure auto-discovery against the live tmux server).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import uuid
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
PANE_KINDS = {
    "claude-code",
    "codex",
    "grok",
    "opencode",
    "antigravity",
    "generic",
    "shell",
}
NAMING_MODES = {
    "pane",
    "window_pane",
    "session_pane",
    "session_window_pane",
    "title",
    "window",
    "target",
    "command",
    "smart",
}
AUTO_NAMING_BACKENDS = {"claude", "local", "codex", "agy", "antigravity"}
DEFAULT_AUTO_NAMING_AI_PROGRAMS = ["claude", "node", "python", "bun", "deno", "codex", "agy"]
DEFAULT_AUTO_NAMING_PREFIX_APPS = {
    "claude": "cc",
    "node": "node",
    "nvim": "nvim",
    "codex": "cx",
    "agy": "ay",
    "antigravity": "ay",
    "opencode": "oc",
    "oc": "oc",
}
DEFAULT_AUTO_NAMING_SYSTEM_PROMPT = (
    "Generate a SHORT kebab-case tmux pane title (max 4 words) describing what "
    "this terminal is working on. Prioritize the git branch name, then the "
    "task/file/topic visible. Output ONLY the title — no quotes, no punctuation, "
    "no explanation."
)
MAX_PATTERNS = 40
MAX_PATTERN_LEN = 200

CREATION_RUNTIME_IDS = ("codex", "claude", "agy", "grok", "opencode")

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


@dataclass(frozen=True)
class CreationRoot:
    """One canonically resolved filesystem boundary exposed to clients."""

    label: str
    path: str


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8787
    token: str = ""
    poll_interval: float = 0.7
    capture_lines: int = 200     # lines of scrollback captured per pane (40-2000)
    auto_discover: bool = True
    include_shells: bool = False
    disable_tmux_auto_rename: bool = True
    naming_mode: str = "session_window_pane"
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
    usage_enabled: bool = False
    usage_command: str = "tokscale"      # may include args, e.g. "npx -y tokscale"
    usage_quota_refresh: float = 180.0   # seconds between `tokscale usage` calls
    usage_report_refresh: float = 300.0  # seconds between report scans (CPU-heavy)
    usage_alert_threshold: float = 20.0  # push when quota drops below this %; 0 = off

    # optional smart pane naming (`naming_mode: smart`). The heuristic layer is
    # always local; the AI layer is YAML-only because it can capture pane text,
    # read local executable paths, and call external backends.
    auto_naming_ai_enabled: bool = False
    auto_naming_ai_backend: str = "claude"
    auto_naming_ai_programs: List[str] = field(default_factory=lambda: list(DEFAULT_AUTO_NAMING_AI_PROGRAMS))
    auto_naming_prefix_apps: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_AUTO_NAMING_PREFIX_APPS))
    auto_naming_max_len: int = 24
    auto_naming_timeout: float = 60.0
    auto_naming_system_prompt: str = DEFAULT_AUTO_NAMING_SYSTEM_PROMPT
    auto_naming_claude_bin: str = "claude"
    auto_naming_claude_model: str = "haiku"
    auto_naming_local_url: str = "http://localhost:8080/v1/chat/completions"
    auto_naming_local_model: str = "default"
    auto_naming_local_api_key: str = ""
    auto_naming_codex_bin: str = "codex"
    auto_naming_codex_model: str = ""
    auto_naming_antigravity_bin: str = "agy"
    auto_naming_antigravity_model: str = ""
    auto_naming_antigravity_flags: List[str] = field(default_factory=list)
    auto_naming_cache_path: Optional[str] = field(default=None, repr=False)

    # Structured Agent Context is one opt-in experimental workspace bundle.
    # Activation is controlled only by the UI-managed settings overlay; YAML
    # configures retention and observer locations but cannot enable it.
    experimental_agent_workspace_enabled: bool = False
    agent_retention_days: int = 30
    agent_store_path: Optional[str] = field(default=None, repr=False)
    agent_codex_home: str = field(default_factory=lambda: os.path.expanduser("~/.codex"), repr=False)
    agent_claude_home: str = field(default_factory=lambda: os.path.expanduser("~/.claude"), repr=False)
    server_instance_id: str = field(default_factory=lambda: str(uuid.uuid4()), repr=False)

    # Optional tmux target creation. This is deliberately YAML-only: roots and
    # executable argument arrays grant filesystem/process authority and must
    # never be writable through the settings API or JSON overlay.
    creation_enabled: bool = False
    creation_roots: List[CreationRoot] = field(default_factory=list)
    creation_runtimes: Dict[str, List[str]] = field(default_factory=dict, repr=False)
    creation_setup_reason: str = field(default="Creation is disabled in server configuration.", repr=False)

    # compiled, filled in __post_init__
    generic_re: List["re.Pattern"] = field(default_factory=list, repr=False)
    error_re: List["re.Pattern"] = field(default_factory=list, repr=False)

    # where UI-managed settings persist (set by load(); not part of editable_dict)
    overlay_path: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.naming_mode not in NAMING_MODES:
            raise ValueError("bad naming_mode: %s" % self.naming_mode)
        if self.auto_naming_ai_backend not in AUTO_NAMING_BACKENDS:
            raise ValueError("bad auto_naming.ai_backend: %s" % self.auto_naming_ai_backend)
        self._validate_creation()
        self._recompile()

    def _validate_creation(self) -> None:
        """Resolve configured roots once and fail closed on invalid entries.

        Invalid roots are rejected from the authorization set. A partially
        valid list remains usable; an empty valid set disables creation.
        """
        valid_roots: List[CreationRoot] = []
        seen = set()
        for raw in self.creation_roots:
            if isinstance(raw, CreationRoot):
                label, path = raw.label, raw.path
            elif isinstance(raw, dict):
                label, path = raw.get("label"), raw.get("path")
            else:
                continue
            if not isinstance(label, str) or not isinstance(path, str):
                continue
            label = label.strip()
            path = path.strip()
            if not label or len(label) > 80 or not path or "\x00" in path:
                continue
            try:
                expanded = os.path.expanduser(path)
                resolved = os.path.realpath(expanded)
                info = os.stat(resolved)
            except (OSError, ValueError):
                continue
            if not stat.S_ISDIR(info.st_mode) or not os.access(resolved, os.R_OK | os.X_OK):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            valid_roots.append(CreationRoot(label=label, path=resolved))
        self.creation_roots = valid_roots

        valid_runtimes: Dict[str, List[str]] = {}
        for runtime_id in CREATION_RUNTIME_IDS:
            raw_command = self.creation_runtimes.get(runtime_id)
            if not isinstance(raw_command, list) or not raw_command:
                continue
            command = []
            malformed = False
            for arg in raw_command:
                if not isinstance(arg, str) or not arg or "\x00" in arg or len(arg) > 1000:
                    malformed = True
                    break
                command.append(arg)
            if not malformed and len(command) <= 64:
                valid_runtimes[runtime_id] = command
        self.creation_runtimes = valid_runtimes

        if not self.creation_enabled:
            self.creation_setup_reason = "Creation is disabled in server configuration."
        elif not self.creation_roots:
            self.creation_setup_reason = "No valid creation roots are configured."
        else:
            self.creation_setup_reason = ""

    @property
    def creation_configured(self) -> bool:
        return self.creation_enabled and bool(self.creation_roots)

    def _recompile(self) -> None:
        # compiled with the `regex` module so detectors can match with a timeout=
        self.generic_re = [regex.compile(p) for p in self.generic_prompt_patterns]
        self.error_re = [regex.compile(p, regex.MULTILINE) for p in self.error_patterns]

    # -- the slice the Settings UI may read/write ------------------------- #
    def editable_dict(self) -> dict:
        return {
            "poll_interval": self.poll_interval,
            "capture_lines": self.capture_lines,
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
            "experimental_agent_workspace_enabled": self.experimental_agent_workspace_enabled,
        }

    def apply_patch(self, data: dict) -> None:
        """Validate + apply a partial settings update in place. Raises ValueError
        on bad input (the server maps that to HTTP 400). Recompiles regexes so
        the change takes effect on the next poll."""
        if "experimental_agent_workspace_enabled" in data:
            value = data["experimental_agent_workspace_enabled"]
            if not isinstance(value, bool):
                raise ValueError("experimental_agent_workspace_enabled must be true or false")
        if "poll_interval" in data:
            try:
                pi = float(data["poll_interval"])
            except (TypeError, ValueError):
                raise ValueError("poll_interval must be a number")
            self.poll_interval = min(10.0, max(0.2, pi))
        if "capture_lines" in data:
            try:
                cl = int(data["capture_lines"])
            except (TypeError, ValueError):
                raise ValueError("capture_lines must be an integer")
            self.capture_lines = min(2000, max(40, cl))
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
        if "experimental_agent_workspace_enabled" in data:
            self.experimental_agent_workspace_enabled = data[
                "experimental_agent_workspace_enabled"
            ]
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


def _server_instance_id(path: str) -> str:
    """Load or create the non-secret stable id used to validate deep links."""
    try:
        if os.path.exists(path):
            with open(path, "r") as fh:
                value = fh.read().strip()
            return str(uuid.UUID(value))
    except (OSError, ValueError):
        pass
    value = str(uuid.uuid4())
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(value + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass
    return value


def _string_list(value, default: List[str], *, split_shell: bool = False) -> List[str]:
    if value is None:
        return list(default)
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        if split_shell:
            return [str(v).strip() for v in shlex.split(value) if str(v).strip()]
        return [p.strip() for p in value.split(",") if p.strip()]
    return list(default)


def _string_map(value, default: Dict[str, str]) -> Dict[str, str]:
    out = dict(default)
    if value is None:
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k).strip()
            val = str(v).strip()
            if key and val:
                out[key] = val
        return out
    if isinstance(value, str):
        for pair in value.split(","):
            if ":" not in pair:
                continue
            key, val = pair.split(":", 1)
            key = key.strip()
            val = val.strip()
            if key and val:
                out[key] = val
    return out


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
    tmux_settings = data.get("tmux", {}) or {}
    discovery = data.get("discovery", {}) or {}
    detectors = data.get("detectors", {}) or {}
    push = data.get("push", {}) or {}
    usage = data.get("usage", {}) or {}
    auto_naming = data.get("auto_naming", {}) or {}
    agents = data.get("agents", {}) or {}
    creation = data.get("creation", {}) or {}

    if not isinstance(creation, dict):
        raise SystemExit("creation must be a mapping")
    creation_enabled = creation.get("enabled", False)
    if not isinstance(creation_enabled, bool):
        raise SystemExit("creation.enabled must be true or false")
    raw_creation_roots = creation.get("roots", []) or []
    if not isinstance(raw_creation_roots, list):
        raise SystemExit("creation.roots must be a list")
    creation_roots: List[CreationRoot] = []
    for entry in raw_creation_roots:
        if not isinstance(entry, dict):
            continue
        if not isinstance(entry.get("label"), str) or not isinstance(entry.get("path"), str):
            continue
        creation_roots.append(CreationRoot(
            label=entry["label"],
            path=entry["path"],
        ))
    raw_creation_runtimes = creation.get("runtimes", {}) or {}
    if not isinstance(raw_creation_runtimes, dict):
        raise SystemExit("creation.runtimes must be a mapping")
    unknown_creation_runtimes = set(raw_creation_runtimes) - set(CREATION_RUNTIME_IDS)
    if unknown_creation_runtimes:
        raise SystemExit(
            "creation.runtimes contains unsupported presets: %s"
            % ", ".join(sorted(str(value) for value in unknown_creation_runtimes))
        )

    apns_env = str(push.get("environment", "sandbox") or "sandbox")
    if apns_env not in ("sandbox", "production"):
        raise SystemExit("push.environment must be 'sandbox' or 'production', got: %s" % apns_env)
    auto_backend = str(auto_naming.get("ai_backend", "claude") or "claude")
    if auto_backend not in AUTO_NAMING_BACKENDS:
        raise SystemExit("auto_naming.ai_backend must be one of %s, got: %s" % (
            ", ".join(sorted(AUTO_NAMING_BACKENDS)),
            auto_backend,
        ))

    overrides: Dict[str, PaneOverride] = {}
    for entry in data.get("panes", []) or []:
        target = entry.get("target")
        if not target:
            continue
        overrides[target] = PaneOverride(
            target=target,
            name=entry.get("name"),
            kind=entry.get("kind"),
            star=bool(entry.get("star") or entry.get("pin")),
        )

    cfg = Config(
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8787)),
        token=str(server.get("token", "") or ""),
        poll_interval=float(data.get("poll_interval", 0.7)),
        capture_lines=min(2000, max(40, int(data.get("capture_lines", 200)))),
        auto_discover=bool(discovery.get("auto", True)),
        include_shells=bool(discovery.get("include_shells", False)),
        disable_tmux_auto_rename=bool(tmux_settings.get("disable_auto_rename", True)),
        naming_mode=str(data.get("naming_mode", "session_window_pane") or "session_window_pane"),
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
        usage_enabled=bool(usage.get("enabled", False)),
        usage_command=str(usage.get("command", "tokscale") or "tokscale")[:200],
        usage_quota_refresh=min(3600.0, max(30.0, float(usage.get("quota_refresh", 180.0)))),
        usage_report_refresh=min(3600.0, max(60.0, float(usage.get("report_refresh", 300.0)))),
        usage_alert_threshold=min(100.0, max(0.0, float(usage.get("alert_threshold", 20.0)))),
        auto_naming_ai_enabled=bool(auto_naming.get("ai_enabled", False)),
        auto_naming_ai_backend=auto_backend,
        auto_naming_ai_programs=_string_list(
            auto_naming.get("ai_programs"),
            DEFAULT_AUTO_NAMING_AI_PROGRAMS,
        ),
        auto_naming_prefix_apps=_string_map(
            auto_naming.get("prefix_apps"),
            DEFAULT_AUTO_NAMING_PREFIX_APPS,
        ),
        auto_naming_max_len=min(80, max(8, int(auto_naming.get("max_len", 24)))),
        auto_naming_timeout=max(0.0, min(300.0, float(auto_naming.get("timeout", 60.0)))),
        auto_naming_system_prompt=str(
            auto_naming.get("system_prompt", DEFAULT_AUTO_NAMING_SYSTEM_PROMPT)
            or DEFAULT_AUTO_NAMING_SYSTEM_PROMPT
        )[:1000],
        auto_naming_claude_bin=str(auto_naming.get("claude_bin", "claude") or "claude")[:300],
        auto_naming_claude_model=str(auto_naming.get("claude_model", "haiku") or "haiku")[:100],
        auto_naming_local_url=str(
            auto_naming.get("local_url", "http://localhost:8080/v1/chat/completions")
            or "http://localhost:8080/v1/chat/completions"
        )[:500],
        auto_naming_local_model=str(auto_naming.get("local_model", "default") or "default")[:100],
        auto_naming_local_api_key=str(auto_naming.get("local_api_key", "") or "")[:500],
        auto_naming_codex_bin=str(auto_naming.get("codex_bin", "codex") or "codex")[:300],
        auto_naming_codex_model=str(auto_naming.get("codex_model", "") or "")[:100],
        auto_naming_antigravity_bin=str(auto_naming.get("antigravity_bin", "agy") or "agy")[:300],
        auto_naming_antigravity_model=str(auto_naming.get("antigravity_model", "") or "")[:100],
        auto_naming_antigravity_flags=_string_list(
            auto_naming.get("antigravity_flags"),
            [],
            split_shell=True,
        ),
        agent_retention_days=min(3650, max(1, int(agents.get("retention_days", 30)))),
        agent_codex_home=os.path.expanduser(str(agents.get("codex_home", "~/.codex") or "~/.codex")),
        agent_claude_home=os.path.expanduser(str(agents.get("claude_home", "~/.claude") or "~/.claude")),
        creation_enabled=creation_enabled,
        creation_roots=creation_roots,
        creation_runtimes=dict(raw_creation_runtimes),
    )
    # layer UI-managed settings (if any) over the YAML — overlay wins
    cfg.overlay_path = _overlay_path_for(path)
    cfg.push_store_path = os.path.join(os.path.dirname(cfg.overlay_path), "vmux-push.json")
    cfg.auto_naming_cache_path = os.path.join(os.path.dirname(cfg.overlay_path), "vmux-names.json")
    cfg.agent_store_path = os.path.join(os.path.dirname(cfg.overlay_path), "vmux-agents.sqlite3")
    cfg.server_instance_id = _server_instance_id(
        os.path.join(os.path.dirname(cfg.overlay_path), "server-instance-id")
    )
    overlay = _load_overlay(cfg.overlay_path)
    if overlay:
        try:
            cfg.apply_patch(overlay)
        except ValueError:
            pass  # ignore a corrupt overlay rather than refuse to start
    return cfg

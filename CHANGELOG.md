# Changelog

All notable changes to vmux are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Scrollback capture + link extraction.** Pane detail captures 200 lines of
  scrollback by default (configurable 40–2000), joins wrapped tmux lines, and
  surfaces detected URLs with open/copy actions.
- **Starred panes and larger-swarm navigation.** Tree, active, all, and starred
  views keep busy tmux workspaces navigable, with interaction timestamps used
  for recent-activity sorting.
- **Snippets and shortcut keys.** Saved phrase snippets and customizable action
  buttons backed by the server key allowlist.
- **Freeform reply flow.** Parsed menu options can open a text reply instead of
  sending a fixed key immediately.
- **Optional APNs push.** Local registered-device storage and best-effort APNs
  alerts when panes need input, with optional error alerts.
- **Opt-in tokscale usage tracking.** Quota, usage summary, history API, and low
  quota push alerts when `usage.enabled: true` is configured.
- **Smart pane naming.** A new `naming_mode: smart` option ports the
  auto-naming-tmux heuristic/AI naming strategy into vmux display names without
  installing tmux hooks or renaming tmux windows.
- **Companion app docs.** A backend contract reference for client implementers
  (`docs/COMPANION_APP_BACKEND.md`) and a push-notification guide covering
  APNs setup and its team-scoping constraint
  (`docs/PUSH_NOTIFICATIONS.md`).
- When bound to a non-loopback host with a token set, the startup banner now
  prints the ready-to-paste app server address.

### Changed

- The intended public PyPI distribution name is `vmux-agent`; the command and
  Python import remain `vmux`.
- Settings now include scrollback capture depth, per-pane star state,
  customizable shortcut buttons, snippets, and opt-in usage tracking toggles.
- The service worker cache was refreshed for the updated PWA.
- vmux now disables tmux `automatic-rename` by default at startup; set
  `tmux.disable_auto_rename: false` in YAML to leave tmux's option unchanged.
- QUICKSTART was expanded for app onboarding: keep-it-running and token
  rotation guidance, usage-tracking setup, and troubleshooting keyed to the
  app's exact error messages.

### Security

- tmux and tokscale subprocesses use argument lists, not shell strings; pane ids
  are validated, named keys are allow-listed, and literal text uses
  `send-keys -l --`.
- Push device tokens are validated and truncated in UI/API info responses; APNs
  key paths and `usage.command` remain YAML-only.

## [0.1.0] — 2026-06-08

First public release.

### Added

- **Attention-router core.** FastAPI + WebSocket backend that polls tmux panes,
  detects status (idle / working / needs-input / error / offline), parses Claude
  Code TUI selection boxes into tappable menus, and drives panes via
  `tmux send-keys`. Generic agents detected via configurable regex.
- **Native PWA UI** (single-file React + htm, vendored, no build step):
  platform-adaptive — macOS sidebar split-view on desktop, iOS bottom-sheet on
  mobile — with Apple "Liquid Glass" styling and automatic light/dark.
- **Triage** grid/sidebar ordered by who needs you, with tappable menu buttons,
  a detail view with action keys + text input, and **broadcast** to many agents.
- **Settings** page: browser prefs (theme, glass, ambient motion, sound,
  notifications, default filter) plus live server config (poll interval,
  discovery, per-pane rename/kind, detector patterns, pane-name source),
  persisted to an overlay so a hand-authored `config.yaml` is never rewritten.
- **Connected-sessions** monitor with per-device disconnect.
- **Token gate** UI for unauthorized clients (paste token instead of a silent
  empty grid).
- **Notifications** (sound + system notification) when an agent needs input.
- Auto-discovery of tmux panes (no config required); optional `config.yaml`.

### Security

- Constant-time token comparison (`hmac.compare_digest`) on REST + WebSocket.
- Vendored React/htm same-origin (no third-party CDN / supply-chain exposure).
- ReDoS-bounded detection: user regexes run with a hard per-match timeout, plus
  a nested-quantifier linter and input caps.
- Fail-fast when binding a non-loopback address with an empty token.

### Packaging

- Installable via `pipx`/`pip`; the web UI ships inside the wheel.
- MIT licensed; CI (pytest + ruff + wheel-contents check) and a PyPI release
  workflow.

[Unreleased]: https://github.com/imitation-alpha/vmux/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/imitation-alpha/vmux/releases/tag/v0.1.0

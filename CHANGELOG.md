# Changelog

All notable changes to vmux are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

# Changelog

All notable changes to vmux are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-06-15

First public release.

### Added

- **Attention-router core.** FastAPI + WebSocket backend that polls tmux panes,
  detects status (idle / working / needs-input / error / offline), parses Claude
  Code TUI selection boxes into tappable menus, and drives panes via
  `tmux send-keys`. Generic agents detected via configurable regex.
- **Scrollback capture + link extraction.** Pane detail captures 200 lines of
  scrollback by default (configurable 40–2000), joins wrapped tmux lines, and
  surfaces detected URLs with open/copy actions.
- **Native PWA UI** (single-file React + htm, vendored, no build step):
  platform-adaptive — macOS sidebar split-view on desktop, iOS bottom-sheet on
  mobile — with Apple "Liquid Glass" styling and automatic light/dark.
- **Triage** grid/sidebar ordered by who needs you, with tappable menu buttons,
  starred panes, tree/active/all views, a detail view with action keys + text
  input, freeform replies from the queue, and **broadcast** to many agents.
- **Settings** page: browser prefs (theme, glass, ambient motion, sound,
  notifications, default filter) plus live server config (poll interval,
  scrollback capture, discovery, per-pane rename/kind/star, detector patterns,
  pane-name source), persisted to an overlay so a hand-authored `config.yaml` is
  never rewritten.
- **Snippets and shortcut keys.** Saved phrase snippets and customizable action
  buttons backed by the server key allowlist.
- **Connected-sessions** monitor with per-device disconnect.
- **Token gate** UI for unauthorized clients (paste token instead of a silent
  empty grid).
- **Notifications and APNs push.** Local sound/system notifications and optional
  APNs server push when an agent needs input, with optional error alerts.
- **Opt-in tokscale usage tracking.** Quota, usage summary, history API, and low
  quota push alerts when `usage.enabled: true` is configured.
- Auto-discovery of tmux panes (no config required); optional `config.yaml`.

### Security

- Constant-time token comparison (`hmac.compare_digest`) on REST + WebSocket.
- Vendored React/htm same-origin (no third-party CDN / supply-chain exposure).
- ReDoS-bounded detection: user regexes run with a hard per-match timeout, plus
  a nested-quantifier linter and input caps.
- Fail-fast when binding a non-loopback address with an empty token.
- tmux and tokscale subprocesses use argument lists, not shell strings; pane ids
  are validated, named keys are allow-listed, and literal text uses
  `send-keys -l --`.
- Push device tokens are validated and truncated in UI/API info responses; APNs
  key paths and `usage.command` remain YAML-only.

### Packaging

- Installable via `pipx`/`pip` as `vmux-agent`; the command and import package
  remain `vmux`, and the web UI ships inside the wheel.
- MIT licensed; CI (pytest + ruff + wheel-contents check) and a PyPI release
  workflow.

[Unreleased]: https://github.com/imitation-alpha/vmux/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/imitation-alpha/vmux/releases/tag/v0.1.0

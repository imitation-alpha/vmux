# Changelog

All notable changes to vmux are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/) with the pre-1.0 policy described in
the documentation.

## [Unreleased]

This section is the release candidate for v0.1.0. It will become
`## [0.1.0] - YYYY-MM-DD` only in the commit that is tagged for publication.

### Added

- FastAPI and WebSocket backend that polls tmux panes, detects agent status,
  parses Claude Code selection dialogs into tappable choices, and safely sends
  text and allow-listed keys back through tmux.
- Installable, responsive PWA with triage, detail, broadcast, settings,
  connected-session management, notifications, light/dark themes, and vendored
  React/htm assets that require no frontend build or third-party CDN.
- Automatic pane discovery, stable pane naming, per-pane names/kinds/stars,
  tree and active filters, scrollback capture, extracted links, snippets,
  customizable shortcut keys, and free-form reply flows.
- Optional APNs push support and opt-in tokscale usage/quota reporting with
  low-quota alerts.
- Smart pane naming with local heuristic and optional explicitly configured AI
  backends.
- Configuration overlays so live UI edits do not rewrite the hand-authored
  YAML file.
- A complete MkDocs Material documentation site, contributor policies, issue
  forms, reproducible dependency lockfile, package smoke tests, and hardened CI,
  Pages, security-scanning, dependency-review, and release workflows.

### Changed

- The distribution is named `vmux-agent`; the command and Python import remain
  `vmux`.
- Python 3.10 is now the minimum supported version, with CI coverage through
  Python 3.14.
- Runtime, CLI, and FastAPI versions now resolve from installed distribution
  metadata, with an explicit source-tree fallback.
- vmux disables tmux `automatic-rename` by default; set
  `tmux.disable_auto_rename: false` to preserve tmux's existing setting.
- Developer and documentation tools use PEP 735 dependency groups and a
  committed uv lockfile.

### Security

- Non-loopback binds require a bearer token and print a plain-HTTP/TLS warning.
  Public-internet access is supported only behind correctly configured HTTPS
  termination; localhost, Tailscale, and SSH forwarding remain the documented
  deployment choices.
- REST and WebSocket authentication use constant-time token comparison.
- tmux and tokscale subprocesses use argument lists rather than shell strings;
  pane IDs are validated, named keys are allow-listed, and literal text uses
  `send-keys -l --`.
- User detector regexes have input caps, a nested-quantifier check, and bounded
  execution time.
- Push tokens are validated and redacted; APNs key paths and executable usage
  commands remain YAML-only.
- Starlette is constrained to 1.3.1 or newer to exclude
  GHSA-82w8-qh3p-5jfq, a denial-of-service flaw in URL-encoded form parsing.

[Unreleased]: https://github.com/imitation-alpha/vmux/commits/main

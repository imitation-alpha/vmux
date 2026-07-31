# Changelog

All notable changes to vmux are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/) with the pre-1.0 policy described in
the documentation.

## [Unreleased]

## [0.1.1] - 2026-07-31

### Fixed

- Live pane updates now suppress unchanged WebSocket snapshots, retain generic
  and Codex activity through short quiet captures, and coalesce ordinary list
  reordering for two seconds while preserving immediate attention updates.

## [0.1.0] - 2026-07-29

### Added

- Opt-in authenticated tmux session, window, and split-pane creation across the
  backend, responsive PWA, and native iOS app, with canonical configured roots,
  fixed server runtime presets including Antigravity and Grok Build, contextual
  actions, and automatic pane opening.

- FastAPI and WebSocket backend that polls tmux panes, detects agent status,
  parses Claude Code selection dialogs into tappable choices, and safely sends
  text and allow-listed keys back through tmux.
- First-class Codex questionnaire detection with bounded question, option label,
  and option description extraction, including node-hosted Codex sessions and
  staged `None of the above` replies.
- Installable, responsive PWA with triage, detail, broadcast, settings,
  connected-session management, notifications, light/dark themes, and vendored
  React/htm assets that require no frontend build or third-party CDN.
- Width-adaptive compact, medium, and wide PWA workspaces: a four-destination
  phone dock, tablet master/detail split, and desktop three-column command
  center with command-palette and keyboard pane navigation.
- A first-class Stats destination for today's cost, tokens, messages, provider
  quotas, top clients/models, and accessible SVG history charts across Today,
  7D, 30D, and Months ranges.
- Explicit connection recovery states, compatibility guidance, retry controls,
  REST fallback, offline snapshot inspection, and sanitized technical details.
- Faithful no-wrap and optional wrapped terminal modes, full-screen output,
  follow-tail suspension, a Latest control, safe link tools, snippets, and
  Enter/no-Enter sending.
- Authenticated temporary PNG, JPEG, WebP, and GIF uploads shared by terminal
  and structured-agent composers, with image picker/paste, progress,
  cancellation, retry, expiry feedback, and explicit submission.
- Broadcast recipient filtering, progress and completion feedback, per-pane
  partial-error reporting, offline exclusions, and failed-recipient retry data.
- Automatic pane discovery, stable pane naming, per-pane names/kinds/stars,
  tree and active filters, scrollback capture, extracted links, snippets,
  customizable shortcut keys, and free-form reply flows.
- Optional APNs push support and opt-in tokscale usage/quota reporting with
  low-quota alerts and best-effort Antigravity synchronization before reports.
- Smart pane naming with local heuristic and optional explicitly configured AI
  backends.
- Configuration overlays so live UI edits do not rewrite the hand-authored
  YAML file.
- Read-only client compatibility metadata in `/api/config`, including protocol
  version 1 and the minimum supported iOS marketing version.
- A separate Agent Context subsystem for Codex and Claude Code with normalized
  goals/tasks/progress, 30-day local SQLite snapshots, shared resume deltas,
  visible chat, verified decisions, and a cursor-based invalidation socket.
- Agent workspace destinations in the PWA and native iOS client: live agent
  cards, resume summaries, “what changed,” unified Quick/Plan Review, deep
  retained context search, chat, and semantic timeline.
- Server-wide Review baselines and optional scheduled batching with generic
  APNs digests, explicit urgent bypass, deterministic agent ranking, and
  privacy-minimized terminal-only references.
- Contextual native decision notifications with sensitive-copy fallback and
  server-identity-validated deep links.
- PNG home-screen and maskable app icons plus a vendored, licensed Lucide icon
  sprite for same-origin navigation and action glyphs.
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
- The former single-file PWA is now a minimal HTML shell, shared stylesheet, and
  focused native ES modules for state/transport, shared UI, usage, settings, and
  application composition. Runtime dependencies and the no-build workflow are
  unchanged.
- Browser preferences migrate to version 2. Legacy Needs/Working destinations
  become Queue/Active; new profiles default to system appearance, ambient
  motion and notifications off, Queue, and width-appropriate list/tree views.
- Settings now use Appearance & Alerts, Input Shortcuts & Snippets, Server &
  Discovery, Agent Overrides & Detectors, Usage, Sessions, and Connection &
  About categories.

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
- An incoming `?token=` is stored once and removed from browser history without
  disturbing other query parameters. Sign-out clears credentials and purges
  legacy credential-bearing cache entries.
- The service worker caches only an explicit unauthenticated shell allowlist.
  API, WebSocket, query-bearing, and authorized requests are never cached;
  offline fallback is navigation-only and pane content remains memory-only.
- Image bodies are streamed only after authentication, validated against MIME
  declarations and file signatures, bounded to 20 MiB each and 200 MiB total,
  and atomically stored under a private `~/.vmux/uploads` directory. Rejected,
  interrupted, and 24-hour-expired files are removed and are never web-served.
- Captured terminal output continues to render only as plain React text, never
  as injected HTML.
- Agent log observers are read-only and discard hidden reasoning, raw tool I/O,
  commands, arbitrary events, and terminal scrollback. Chat and decision input
  fail closed on stale bindings, pane incarnations, revisions, or prompts.
- Opening Review, an agent, or retained history never acknowledges work. Plan
  drafts contain guarded identifiers and fingerprints only, refetch each item,
  and never use broadcast or automatically retry conflicts.
- Starlette is constrained to 1.3.1 or newer to exclude
  GHSA-82w8-qh3p-5jfq, a denial-of-service flaw in URL-encoded form parsing.

[Unreleased]: https://github.com/imitation-alpha/vmux/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/imitation-alpha/vmux/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/imitation-alpha/vmux/releases/tag/v0.1.0

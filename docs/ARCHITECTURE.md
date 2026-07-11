# Architecture

vmux is one local pipeline: capture tmux, classify attention, publish a state
snapshot, and turn an authenticated client action back into a safe tmux call.

~~~text
tmux ── capture-pane ──▶ poller ── detectors ──▶ PaneState snapshot
  ▲                         │                         │
  │                         └── optional push         └── REST + WebSocket
  │                                                        │
  └──────────── allow-listed keys / literal text ◀─────────┘
~~~

## Runtime components

### CLI and configuration

`vmux.__main__` parses CLI overrides, verifies tmux, loads YAML plus the JSON
overlay, validates the bind/token boundary, and starts Uvicorn. vmux disables
tmux `automatic-rename` by default unless configuration opts out.

### tmux adapter

`tmux.py` invokes the tmux executable with argument lists. It lists panes,
captures joined scrollback, checks pane identifiers, sends allow-listed named
keys, and sends user text in literal mode.

### Poller and detector

`Hub.poll_once()` lists panes and captures them concurrently. For each included
pane it:

1. hashes captured text and records whether it changed
2. classifies the process as `claude-code`, `generic`, or `shell`
3. detects `needs_input`, `error`, `working`, or `idle`
4. resolves a display name and manual override
5. builds a `PaneState`

Configured targets that are absent become `offline` states. The loop runs every
`poll_interval`, 0.7 seconds by default, and an action wakes it for an immediate
new pass.

Detector matching is bounded. Claude Code has a dedicated selection-box path;
Codex and other agents currently use conservative generic numbered-menu and
regex paths.

### Server and PWA

FastAPI exposes authenticated REST actions and a WebSocket. Every WebSocket tick
sends a **full state snapshot**, not a patch. The server then mounts
`vmux/web/` at the root for same-origin static delivery.

The PWA is a single React/htm application with vendored runtime files and no
frontend build step or third-party CDN. Browser preferences, snippets, shortcuts,
and the token are stored locally in that browser profile.

### Optional subsystems

- `usage.py` invokes an explicitly configured tokscale command and normalizes
  quota/history output. It is disabled by default.
- `push.py` stores registered device tokens locally and can send prompt-derived
  APNs alerts when optional dependencies and credentials are present.
- `naming.py` supplies local naming heuristics and an opt-in AI naming layer.

Failures in these optional paths are designed not to terminate the pane polling
loop.

## Configuration model

The Settings API reads and writes only a validated subset of fields. It persists
the complete editable subset to a JSON overlay:

~~~text
built-in defaults < YAML < JSON overlay < CLI overrides
~~~

The overlay never rewrites YAML. Bind, token, tmux auto-rename, APNs
credentials, `usage.command`, and AI backend settings stay YAML/CLI-only.

## Trust boundaries

### tmux boundary

An authenticated client can intentionally cause input to be sent to a pane.
That pane may be a shell or an agent capable of running commands as the vmux OS
user. The bearer token therefore authorizes a high-impact capability.

### network boundary

vmux serves plain HTTP. Localhost is the default; Tailscale is the recommended
remote route. Non-loopback binds require a token. Public access additionally
requires HTTPS/WSS termination while the vmux listener remains private.

The WebSocket token is a query parameter and can enter proxy logs. Reverse
proxies must suppress or redact it.

### content boundary

Captured pane output may contain secrets or private source. It is sent to every
authenticated client, may be stored in browser memory, and can leave the host
through explicitly enabled AI naming or APNs push. Those features are off or
unconfigured by default.

## Security invariants

Changes must preserve all of these:

- subprocesses receive argument lists; tmux actions never construct a shell
  command
- live pane ids and configured targets are format-checked before tmux use
- named keys are allow-listed
- text uses `tmux send-keys -l --` so it remains literal
- REST and WebSocket bearer comparisons use `hmac.compare_digest`
- a non-loopback bind with an empty token fails before serving
- public documentation never presents bare HTTP as safe for public exposure
- custom detector regex input and execution time are bounded
- API config edits cannot replace the bearer token, executable usage command,
  APNs credentials, or AI backend configuration
- overlays, naming caches, and push registries never rewrite `config.yaml`
- the token is never included in an API response
- vendored PWA assets remain same-origin
- React `style` properties in the single-file PWA remain objects, not strings

## Compatibility surface

The documented CLI flags and exit categories, YAML/overlay semantics, REST and
WebSocket contract, `PaneState` shape, and the security invariants above form
the public v0.x compatibility surface. See
[Compatibility and versioning](https://imitation-alpha.github.io/vmux/reference/compatibility/) before changing one.

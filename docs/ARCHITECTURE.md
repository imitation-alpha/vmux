# Architecture

vmux has two local, cooperating pipelines. The terminal pipeline captures tmux
and publishes `PaneState`. The agent-context pipeline observes supported
runtime logs and publishes structured, resumable session state. Terminal state
remains the fallback and the source of live prompt verification.

~~~text
tmux ── capture-pane ──▶ poller ── detectors ──▶ PaneState snapshot
  ▲                │                                │
  │                ├── runtime association          └── REST + /ws
  │                ▼
  │        Codex / Claude log observers ──▶ AgentService ──▶ SQLite
  │                                               │            │
  │                                               └── REST + /ws/agents
  │                                                            │
  └──── revision- and prompt-verified terminal input ◀─────────┘
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
2. classifies the process as `claude-code`, `codex`, `grok`, `opencode`, `antigravity`, `generic`, or `shell`
3. detects `needs_input`, `error`, `working`, or `idle`
4. resolves a display name and manual override
5. builds a `PaneState`

Configured targets that are absent become `offline` states. The loop runs every
`poll_interval`, 0.7 seconds by default, and an action wakes it for an immediate
new pass.

Detector matching is bounded. Claude Code has a dedicated selection-box path.
Codex has a dedicated questionnaire parser that separates aligned labels and
descriptions; legacy approvals and other agents retain the conservative generic
numbered-menu and regex paths.

### Agent context subsystem

`AgentService` is deliberately separate from the pane state model. The poller
passes it small live pane observations through a latest-wins queue, so log
extraction cannot delay terminal polling. Runtime observers discover and tail
Codex and Claude Code JSONL sessions read-only. They normalize an allowlist of
user-visible messages, explicit plans/tasks, explicit structured questions,
and lifecycle events; hidden reasoning, tool arguments/results, arbitrary
events, and captured terminal output are discarded at the adapter boundary.

The projector builds a canonical context and a semantic delta. `AgentStore`
commits context, new snapshots, visible messages, decision candidates, and
resolution events atomically to a local SQLite database. Historical data is
pruned after 30 days by default; current context and unresolved decisions are
kept. The legacy `SessionVisit` and v2 `SessionReview` records are separate
server-side monotonic baselines. Review reads are non-mutating; only an
acknowledgement naming an exact displayed snapshot advances `SessionReview`.

`AgentService.review_payload()` joins deterministic SQLite groups to
privacy-minimized current pane references. It ranks explicit urgent decisions,
errors, oldest decisions, new blockers, then other semantic changes. A
singleton review-settings row provides the optional persisted timer. Due
windows are claimed transactionally before one generic APNs digest and one
`review_due` invalidation are published.

Association and control are distinct. A probable or ambiguous log/pane match
is readable but cannot receive input. Chat requires a confirmed current pane
incarnation, matching binding revision, unchanged prompt fingerprint, and a
live idle prompt. Decision replies additionally require a structured question
whose options still match the live menu. The server revalidates immediately
before a literal tmux action and uses client idempotency keys to prevent replay.

`/ws/agents` carries small invalidation events with a resumable cursor; agent
clients rehydrate authoritative objects over REST. It is separate from `/ws`,
so a parser or agent-protocol failure cannot make terminal monitoring appear
offline.

### Server and PWA

FastAPI exposes authenticated REST actions and a WebSocket. Every WebSocket tick
sends a **full state snapshot**, not a patch. The server then mounts
`vmux/web/` at the root for same-origin static delivery.

The additive `POST /api/images` path is separate from pane state and static
delivery. `ImageStore` streams authenticated raw bodies into private partial
files under `~/.vmux/uploads`, enforces signature/type/size/quota boundaries,
then atomically renames a valid image. Startup, hourly, and pre-quota cleanup
remove files after 24 hours. The response exposes an absolute path and a
shell-quoted form for the existing literal-text composers; there is no image
download route and no new WebSocket frame.

The PWA keeps the no-build model but is no longer one HTML implementation file.
`index.html` is a minimal shell; `styles.css` owns the visual system; and native
ES modules divide runtime primitives, state/transport, shared UI, usage,
settings, image upload, and application composition. React, ReactDOM, and htm remain vendored
same-origin files, and the Lucide subset is a vendored SVG sprite with its
license notice.

The client first reads `/api/config` and `/api/state`, evaluates compatibility,
normalizes the snapshot, and only then adopts the workspace. Successive full
snapshots reuse unchanged pane objects so terminal scroll state and unaffected
cards survive updates. Unknown statuses and agent kinds receive neutral generic
presentation instead of being omitted.

Connection recovery has explicit states: Connecting, Live, Updating via REST,
Offline, Unauthorized, and Incompatible. A live socket is considered silent
after `max(3 seconds, poll_interval × 2 + 1 second)`. REST fallback runs every
2 seconds while needed. WebSocket retries start at 0.5 seconds, double to an
8-second cap, and add up to 0.3 seconds of jitter. A failure must persist for
10 seconds before the UI changes from Connecting to Offline. The last snapshot
stays available for read-only inspection throughout recovery.

Layout depends only on viewport width:

- below 820 pixels, Queue, Active, All, and Stats use a four-item bottom dock;
  pane detail and the tree use focus-managed full-height sheets
- from 820 through 1199 pixels, a 360-pixel master column sits beside pane
  detail; Stats replaces the split workspace and the tree is a leading drawer
- at 1200 pixels and above, the navigator, attention queue, and inspector form
  a three-column workspace; Stats retains the navigator and spans the other two
  columns

Browser preferences, snippets, shortcuts, terminal wrapping, and the token are
stored locally in that browser profile. Plan Review additionally persists only
server/decision/option ids, revisions, and opaque prompt/options fingerprints;
authoritative refreshes purge resolved, missing, or changed drafts. Pane
snapshots, terminal output, agent responses, prompt and option copy, action
state, and other pending work stay in browser memory only. The server-side
normalized agent history is the separate SQLite store described above.

The service worker has an explicit application-shell allowlist. It never handles
API or WebSocket traffic and never caches query-bearing or authorized requests.
Navigation can fall back to the canonical cached shell after a short network
timeout; missing modules and images never receive the HTML fallback. A waiting
worker activates only after the visible update prompt asks it to do so.

### Optional subsystems

- `usage.py` invokes an explicitly configured tokscale command and normalizes
  quota/history output. The PWA renders that data as quota meters, summary
  cards, local SVG charts, and an equivalent table. It is disabled by default.
- `push.py` stores registered device tokens locally and can send APNs alerts
  when optional dependencies and credentials are present. Pane and agent
  decision alerts use generic copy; agent decision routing carries only opaque
  identifiers and a revision. Scheduled Review digests are also generic and
  contain only an event type and opaque server id.
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
authenticated client and stored in browser memory. Selected recent lines can
leave the host through explicitly enabled AI naming. Pane and agent-decision
APNs copy is generic; decision titles, descriptions, prompts, and options
remain on the vmux server. Those network features are off or unconfigured by
default.

Supported runtime logs can contain the same sensitive material plus internal
runtime records. Agent observers read those files with the vmux OS user's
permissions, but persist only the normalized allowlist described above. The
database and its containing directory are permission-restricted where the host
supports POSIX modes. The authenticated agent APIs expose normalized state to
every bearer-token holder; there is no per-session authorization layer.

Terminal output is untrusted text. The PWA interpolates it into React text nodes
and never uses `innerHTML` or HTML injection for captured content. Link tools
extract only HTTP(S) strings from that text; they do not make terminal output
executable markup.

## Security invariants

Changes must preserve all of these:

- subprocesses receive argument lists; tmux actions never construct a shell
  command
- live pane ids and configured targets are format-checked before tmux use
- named keys are allow-listed
- text uses `tmux send-keys -l --` so it remains literal
- agent control requires a current confirmed pane incarnation and matching
  binding revision; decisions also require a matching structured prompt
  fingerprint and revision
- Review GET/open/history actions never acknowledge or answer work; review
  acknowledgements advance an exact displayed snapshot monotonically
- staged decision choices contain metadata only and are refetched, compared,
  and submitted sequentially through individual guarded replies
- terminal-review responses and scheduled digest payloads exclude prompt,
  transcript, path, menu, preview, and terminal-capture content
- unverified decision candidates and runtime log paths never enter public API
  responses
- REST and WebSocket bearer comparisons use `hmac.compare_digest`
- a non-loopback bind with an empty token fails before serving
- public documentation never presents bare HTTP as safe for public exposure
- custom detector regex input and execution time are bounded
- API config edits cannot replace the bearer token, executable usage command,
  APNs credentials, or AI backend configuration
- overlays, naming caches, and push registries never rewrite `config.yaml`
- the token is never included in an API response
- vendored PWA assets remain same-origin
- an incoming `?token=` is removed from browser history immediately after local
  persistence; all other query parameters remain intact
- sign-out clears browser credentials and purges legacy credential-bearing
  cache entries
- the service worker never caches API, WebSocket, authorized, or query-bearing
  requests, and never persists pane data
- captured terminal content remains plain text; React `style` properties remain
  objects rather than strings

## Compatibility surface

The documented CLI flags and exit categories, YAML/overlay semantics, REST and
WebSocket contract, `PaneState` shape, and the security invariants above form
the public v0.x compatibility surface. See
[Compatibility and versioning](https://imitation-alpha.github.io/vmux/reference/compatibility/) before changing one.

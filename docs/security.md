# Security and privacy

The short threat model is: **a client that has the token and network reach can
send input to your tmux panes, which can lead to command execution as you.**

The canonical vulnerability-reporting policy is
[`SECURITY.md`](https://github.com/imitation-alpha/vmux/blob/main/SECURITY.md).

## Supported deployments

- Localhost is the default and safest.
- Tailscale plus a bearer token is the recommended remote route.
- SSH local forwarding is supported while the vmux listener stays on loopback.
- Direct trusted-LAN access requires a bearer token.
- Public-internet access requires a bearer token and HTTPS/WSS through a
  correctly configured reverse proxy; the plain vmux port stays private.

WebRTC, PeerJS, signaling/hosted relays, automatic port forwarding,
unauthenticated non-loopback access, and public bare HTTP are unsupported. See
[Remote access](remote-access.md) for exact requirements.

## Protect the token

- Generate a long random value.
- Never commit it or include it in an issue.
- Use a permission-restricted config for a long-running service.
- Rotate it by restarting vmux and updating each client.
- Do not place it in the initial page URL when you can paste it into the prompt.
- Redact `/ws` request targets in reverse-proxy and tracing logs.

REST uses an Authorization header. WebSocket auth necessarily uses
`/ws?token=...`, so the query string is the main logging caveat. The PWA stores
the token in browser `localStorage`; do not use an untrusted shared browser
profile. If the initial page has a `?token=` parameter, the PWA persists it once
and immediately removes only that parameter with `history.replaceState`. This
reduces browser-history exposure but cannot undo an initial URL already recorded
by a proxy, browser extension, or access log, so pasting into the token prompt is
still preferred.

Sign-out removes the credential from browser storage and asks the service worker
to purge legacy credential-bearing entries. REST clients reject token query
parameters; only the WebSocket contract uses one. Connection diagnostics are
restricted to host, endpoint path, HTTP status, client/server/protocol versions,
category, and timestamp.

Loopback without a token is not a complete boundary on a shared OS host.

## Data handled

The backend captures pane scrollback and serves it to authenticated clients.
That data can include source, prompts, terminal output, URLs, and secrets.

The Agent Context subsystem also reads local Codex and Claude Code session logs
with the permissions of the vmux process. Those logs can contain sensitive
messages and runtime internals. The adapters discard hidden reasoning, raw tool
arguments/results, commands, arbitrary events, and terminal captures, then
store normalized visible messages, explicit plans/tasks, verified decisions,
and semantic snapshots in a local SQLite database. Historical records are kept
for 30 days by default. Disable observation with `agents.enabled: false` and use
the authenticated history-deletion endpoint to erase one session's retained
timeline. Disabling does not itself delete the database.

The PWA keeps pane snapshots, terminal output, usage responses, action state,
and pending work in memory. Its service worker caches a public application
shell only. An offline relaunch can display recovery chrome, but it cannot
recover pane content or queue an action for later delivery.

Authenticated clients can upload a temporary image for insertion into a draft.
Screenshots and photos can contain source, credentials, personal information,
or other sensitive material. vmux stores them only on the server host under
`~/.vmux/uploads`, returns the absolute local path to the authenticated client,
and removes them after 24 hours. Expiry is best-effort deletion, not secure
erasure from storage snapshots or backups. Upload alone never sends the path to
tmux or an agent; after explicit submission, the target process can read the
file and may transmit it according to that tool's own behavior and policy.

The self-hosted vmux server and PWA have no hosted account, developer telemetry,
analytics service, ads, or third-party runtime CDN. Optional features change
data flow:

- AI smart naming can send target metadata and recent pane lines to the
  configured command or endpoint.
- Pane and agent-decision APNs alerts use generic copy. Agent decision alerts
  carry only opaque routing ids and a revision; decision titles, descriptions,
  prompts, and options stay on the vmux server.
- Usage tracking runs the configured tokscale executable and exposes its
  normalized results to authenticated clients.
- Beginning with native iOS version 1.0.1, the separate app can send anonymous
  product analytics to PostHog only after explicit consent. Demo Mode, the PWA,
  and the self-hosted server never send those events. See the
  [privacy policy](https://github.com/imitation-alpha/vmux/blob/main/PRIVACY.md).

Smart naming, APNs, and usage tracking are off, unavailable, or unconfigured by
default. Local Agent Context observation is enabled by default. Review its
[data and control model](guides/agent-context.md) before exposing vmux beyond a
single-user host.

## Built-in controls

- non-loopback binds fail with an empty token
- bearer comparisons are constant-time
- named tmux keys are allow-listed
- pane ids are validated
- text is sent with tmux literal mode through argument-list subprocess calls
- custom regex matching has size limits and a hard timeout
- API settings cannot modify token, APNs credential, executable usage, or AI
  backend fields
- the token and key material never appear in API responses
- PWA dependencies are vendored and same-origin
- incoming setup tokens are removed from browser history after local
  persistence, and sign-out purges credentials plus legacy sensitive cache keys
- the service worker uses an explicit shell allowlist and never caches API,
  WebSocket, authorized, or query-bearing requests
- cached-shell fallback is navigation-only; missing scripts and images do not
  receive HTML
- captured terminal output is rendered as plain text without `innerHTML` or
  other HTML injection
- image-upload authentication runs before body streaming; PNG, JPEG, WebP, and
  GIF declarations must match their file signatures, each body is limited to
  20 MiB, and the private upload directory is limited to 200 MiB
- image files use opaque names, `0700`/`0600` directory/file permissions, and
  atomic finalization; interrupted and rejected partial files are removed
- uploaded bytes are outside the static mount and service-worker cache, and the
  returned path is appended to a draft without automatic terminal or agent
  submission
- pane actions are disabled for offline panes and for Offline, Unauthorized, or
  Incompatible connections
- agent chat is disabled unless a runtime log is bound to a current idle pane;
  decision input additionally requires matching object/binding revisions and a
  matching live prompt fingerprint
- Review reads never acknowledge or answer work; acknowledgements name an exact
  displayed snapshot and advance monotonically
- Plan Review persists identifiers, revisions, and opaque fingerprints only;
  every staged item is refetched and revalidated sequentially before using the
  individual guarded reply endpoint
- terminal-only Review entries expose an opaque pane reference and status, not
  names, targets, prompts, menus, previews, paths, or capture
- unverified decision candidates, runtime log paths, hidden reasoning, and raw
  tool I/O are not exposed through agent APIs

## Report privately

Do not open a public issue. Use GitHub's **Security** tab and **Report a
vulnerability** to submit a private report. Include a redacted reproduction,
impact, affected version/commit, and any proposed mitigation. Never send a live
token or private key.

Security fixes support the latest release only. Before v0.1.0, fixes land on
`main`.

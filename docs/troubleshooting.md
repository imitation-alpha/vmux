# Troubleshooting

Start with the local URL. If <http://127.0.0.1:8787> works, vmux itself is
running and the remaining problem is discovery, authentication, or the chosen
network path.

## The command exits immediately

### tmux is not on PATH

vmux exits with status 2 when it cannot find tmux. Check:

~~~bash
command -v tmux
tmux list-panes -a
~~~

Install tmux, start a session, and run vmux again. A service manager needs an
explicit `PATH` and must run as the user who owns the tmux server.

### Non-loopback bind has no token

This refusal is intentional:

~~~text
Refusing to bind 0.0.0.0 with an empty token
~~~

Either return to `127.0.0.1` and use an SSH tunnel, or supply a strong token.
Read [Remote access](remote-access.md) before retrying.

### Config file is missing or invalid

Confirm the exact path:

~~~bash
vmux --config /absolute/path/to/config.yaml
~~~

Invalid YAML, a bad naming backend, or an unsupported APNs environment stops
startup. Begin with a small file and compare it with the
[annotated example](https://github.com/imitation-alpha/vmux/blob/main/config.example.yaml).

## The PWA loads but the pane list is empty

Run:

~~~bash
tmux list-panes -a
vmux --include-shells
~~~

If panes appear only with `--include-shells`, discovery is working and the
visible panes are ordinary shells. Start an agent in one of them or keep shell
inclusion enabled.

If tmux lists panes but vmux does not, check:

- vmux and tmux run as the same OS user
- `discovery.auto` is true
- a UI overlay is not overriding the YAML value
- the service can reach the same tmux socket and executable `PATH`

See [Pane discovery](guides/pane-discovery.md).

## The token screen keeps returning

A protected REST request returns `401` with
`{"detail":"bad or missing token"}`. Stop vmux, restart it with a known token,
and paste that exact value:

~~~bash
VMUX_TOKEN="$(openssl rand -hex 32)"
printf 'vmux token: %s\n' "$VMUX_TOKEN"
vmux --host 0.0.0.0 --token "$VMUX_TOKEN"
~~~

Paste only the generated value after `vmux token:`. Common causes are trailing
whitespace, connecting to another vmux instance, or rotating the server token
without updating the client. Clearing site data also removes the token stored by
the PWA; the iOS app clears its saved token after a confirmed authentication
failure so you can enter the new value.

If a setup link contains `?token=`, the PWA stores that value once and
immediately removes only the token parameter from the visible URL and browser
history. Other query parameters and the fragment remain intact. **Settings →
Connection & About → Sign out** clears the saved credential and purges legacy
credential-bearing cache entries before returning to the token screen.

Do not include a real token in logs or an issue.

## Live updates reconnect repeatedly

The PWA bootstraps through `/api/config` and `/api/state`, then prefers `/ws` for
live snapshots. The connection badge distinguishes:

| State | Meaning |
| --- | --- |
| Connecting | Initial bootstrap or a failure still within the 10-second offline grace period. |
| Live | Full state snapshots are arriving over WebSocket. |
| Updating via REST | The socket is unavailable or silent, but authenticated state refreshes still succeed. |
| Offline | Neither transport has succeeded for 10 seconds; the last snapshot is read-only. |
| Unauthorized | REST returned `401` or the socket closed with `1008`; enter the correct token. |
| Incompatible | Compatibility metadata or a state payload is malformed, or protocol 1 is not supported; actions are blocked. |

A socket that sends no state is closed after
`max(3 seconds, poll_interval × 2 + 1 second)`. While the socket is unhealthy,
REST fallback runs every 2 seconds. WebSocket retry begins at 0.5 seconds,
doubles to an 8-second cap, and adds up to 0.3 seconds of jitter. Use the
connection badge or **Settings → Connection & About → Retry now** to restart the
bootstrap immediately.

Check:

- the browser network panel for `/ws`
- REST auth with `GET /api/state`
- a reverse proxy's WebSocket upgrade support
- idle timeouts on the proxy, VPN, or firewall
- that HTTPS pages reach `wss://`, not `ws://`

A wrong WebSocket token closes the connection with code `1008`. A session
disconnected from the PWA's connected-sessions screen closes with code `4001`
and remains Offline until **Retry now** starts a new session.

## The shell opens offline or an update seems stuck

The service worker stores only the unauthenticated application shell. An offline
relaunch can therefore show vmux chrome and connection recovery, but it cannot
restore pane names, terminal output, pending actions, or a previous Stats
snapshot. Those values are memory-only. Reconnect before sending anything.

When a new worker has installed, vmux shows an **Update ready** banner. Choose
**Reload** to activate it in a controlled step. If the banner repeatedly
returns, close other vmux tabs/windows, reload once, and check that a reverse
proxy is not serving stale `/sw.js`, `/js/*`, or `/styles.css` responses. As a
last resort, clear this origin's site data; doing so also removes the saved
token and browser preferences.

The worker never caches `/api/*`, `/ws`, requests with an `Authorization`
header, or any URL containing a query string. A missing JavaScript, stylesheet,
or icon receives a normal network error rather than the cached HTML shell.

## Tailscale or LAN connection times out

Confirm the startup line shows a reachable bind, not `127.0.0.1`. Then verify:

1. the URL includes port `8787`
2. both Tailscale devices are online and allowed by tailnet policy
3. the selected Tailscale hostname or IP resolves from the client
4. a LAN address has not changed
5. the host firewall permits the intended private interface/network
6. vmux is still running

From a reachable client:

~~~bash
curl -H "Authorization: Bearer $VMUX_TOKEN" \
  http://<host>:8787/api/state
~~~

If this works but the PWA does not update, investigate the WebSocket path. If it
fails, fix routing, bind, firewall, or token before debugging the UI.

### iPhone or iPad still cannot connect

Use the vmux server root as the address, not an API endpoint or a browser page.
These are supported forms:

- `http://machine.tailnet-name.ts.net:8787` or
  `machine.tailnet-name.ts.net:8787` over Tailscale
- `http://100.x.y.z:8787` using the vmux host's Tailscale IP
- `http://192.168.1.20:8787` on a trusted LAN
- `https://vmux.example.com` through a reverse proxy with valid HTTPS/WSS

Do not enter `/api/config`, `/api/state`, or another trailing path. On the phone,
`localhost` and `127.0.0.1` point back to the phone rather than the tmux host.

If the app reports that Local Network access is denied, open **Settings → Apps →
vmux → Local Network** (or **Settings → Privacy & Security → Local Network** on
versions that show it there), enable access, return to vmux, and tap **Retry**.
If the switch has not appeared, retry the connection and accept the iOS Local
Network prompt. This permission must be tested on a physical device; Simulator
does not reproduce Local Network privacy behavior.

For Tailscale, confirm that both devices are signed in and online, tailnet policy
allows the connection, and the hostname or `100.x.y.z` address resolves from the
phone. For a direct LAN connection, use a trusted network, put both devices on a
reachable Wi-Fi/LAN segment, avoid guest-network client isolation, and check the
host firewall. In either case, vmux must bind a reachable interface such as
`0.0.0.0` or the host's specific private address rather than `127.0.0.1`.

The iOS app validates both `/api/config` and `/api/state`. A `404`, an HTML page,
non-vmux JSON, or another unexpected response usually means the address points
to the wrong port, includes an extra path, or has incorrect reverse-proxy
routing. A `5xx` response or an incompatible vmux payload requires checking the
server output and updating vmux before retrying. Do not weaken HTTPS certificate
validation to work around a TLS error.

### The PWA or app reports an incompatible or unverified version

In the PWA, check **Settings → Connection & About** for the web client, backend,
protocol, and compatibility result. It expects protocol 1 and a compatible
server at version 0.1.0 or newer. A malformed compatibility object, malformed
version, known protocol mismatch, or malformed state payload blocks pane
actions. Update vmux on the tmux host and choose **Retry now**.

`minimum_ios_version` describes native iOS clients only. The web client displays
or ignores it as informational metadata and never uses it to reject a server.
For a native client, follow that client's update guidance as well.

A **Compatibility unverified** warning means the server predates the additive
`_info.compatibility` metadata. The client may connect after its normal
`/api/config` and `/api/state` validation succeeds, but updating the backend to
a release that advertises compatibility is recommended. Do not treat an absent
compatibility object as permission to ignore a failed schema handshake.

## A reverse-proxied page loads but actions or state fail

The proxy must:

- forward all paths, including `/api/*`, `/js/*`, `/vendor/*`, icons,
  `/styles.css`, `/manifest.webmanifest`, `/sw.js`, and `/ws`
- support WebSocket Upgrade and Connection semantics
- preserve `Authorization`
- serve valid HTTPS so the browser uses WSS
- avoid caching API and WebSocket responses

Do not expose the upstream 8787 port publicly. Redact the `/ws` query string
from logs because it contains the token.

## Reporting a connection problem safely

Open the connection badge or **Settings → Connection & About** in the PWA; a
native client may expose the same information in its recovery card. Copy only
the sanitized technical details. A useful report includes:

- host and port (replace a private hostname if necessary)
- failing endpoint
- stable issue category
- HTTP status or native URL error code
- client, server, and protocol versions, when known
- timestamp and whether the route was Tailscale, trusted LAN, or valid HTTPS

Review the text before sharing it. Never include the bearer token, an
`Authorization` header, a query string (especially `/ws?token=...`), or a
response body. Rotate the server token immediately if it was exposed. Describe
what the app displayed and which recovery checks you completed instead of
attaching an unredacted network capture.

## YAML changes have no effect

The Settings UI overlay wins over YAML. With `--config /path/config.yaml`, look
for `/path/vmux-settings.json`. With no config path, look for
`~/.vmux/settings.json`.

Change the setting through the UI, or stop vmux and move the overlay aside to
reset live settings. Do not edit the overlay while vmux is writing it.

## Actions target the wrong pane or fail

Refresh the state first. tmux pane ids are live identifiers and an old id may
refer to a pane that disappeared. Actions against an unknown pane return `404`;
disallowed named keys return `400`.

For a persistent pane entry, configure its `session:window.pane` target. An
offline configured card remains visible but cannot accept actions until the live
pane returns.

All menu, key, and text actions remain non-optimistic and display pending,
success, or error feedback. Stars update optimistically and roll back if saving
fails. If a button stays disabled, check for an identical pending action, an
offline pane, or an Offline/Unauthorized/Incompatible connection before
reloading.

Broadcast excludes offline or otherwise non-actionable panes before sending.
Completion reports the attempted and sent counts plus partial errors; retry only
the failed recipients rather than repeating a successful broadcast to everyone.

## Terminal wrapping or follow-tail is surprising

**Faithful** is the default terminal mode: lines do not wrap and the output can
scroll horizontally. Choose **Wrap** to wrap long lines; this preference is
stored only in the current browser profile. Full screen uses the same plain-text
output and controls.

The terminal follows new output only while its scroll position remains at the
bottom. If you scroll up, follow-tail pauses so selection and reading are not
interrupted. A visible **Latest** button appears after new output arrives; choose
it to return to the bottom and resume following. Extracted links are convenience
tools only—captured output is never interpreted as HTML.

## Smart names are stale

AI-derived names are cached in `vmux-names.json` beside the overlay. Stop vmux,
remove only that cache, and restart to regenerate names. Local heuristic names
do not require the AI layer.

## Usage is unavailable or stale

`GET /api/config` exposes `_info.usage`. Confirm usage is enabled, the configured
command resolves in vmux's `PATH`, and tokscale is compatible. A failed refresh
preserves the last-good payload and marks it stale instead of stopping vmux.

Use **Settings → Usage** to enable collection and edit quota refresh, report
refresh, and warning threshold values. `usage.command` remains YAML-only. The
Stats page distinguishes disabled, not installed, timeout, error, stale, empty,
and loading states. Its manual refresh can take longer than ordinary API calls
because report scanning is CPU-heavy; pane monitoring remains independent.

## Agent context is missing, stale, or read-only

First inspect `GET /api/config` →
`_info.capabilities.agent_context_v1`. If it is absent, use the terminal
workspace; if `enabled` is false, turn on **Settings → Experimental → Enable
Agent Workspace**. YAML `agents.enabled` values are not activation controls.

Structured context currently requires a Codex or Claude Code pane whose
runtime-owned session log reports the same working directory. Confirm vmux runs
as the same OS user and can read the configured `codex_home` or `claude_home`.
`extraction_health` reports parser/read failures without disabling pane
monitoring. A runtime update can require an observer compatibility update; do
not work around that by exposing or editing its logs.

`probable`, `ambiguous`, or `unavailable` associations are intentionally
read-only. Choose a matching candidate pane to bind it manually, or keep using
the terminal fallback. A `409` from chat or a decision means the binding,
revision, pane incarnation, idle prompt, or decision fingerprint changed.
Refresh the session and review current state; do not automatically replay the
mutation.

The local history database sits beside `vmux-settings.json`. Deleting a
session's history is distinct from disabling future observation. See
[Agent context and decision inbox](guides/agent-context.md) for the exact data
boundary and retention behavior.

## Supported browsers and Safari release checks

vmux targets modern browsers with native ES modules, service workers, SVG, and
the CSS features used by the adaptive shell:

| Browser | Support target | Install notes |
| --- | --- | --- |
| Safari on iPhone and iPad | Current and immediately previous major iOS/iPadOS releases | Installed Home Screen PWA is a release-gated path. |
| Safari on macOS | Current and immediately previous major macOS/Safari releases | Browser use is supported; installed web-app availability follows the OS. |
| Chrome or Edge on desktop | Current and immediately previous stable major releases | Browser use and the browser-provided install flow are supported. |
| Chrome on Android | Current and immediately previous stable major releases | Compact installed PWA is supported. |
| Firefox on desktop | Current and immediately previous stable major releases | Browser use is supported; installation is not a vmux release gate. |
| Embedded/in-app browsers and older engines | Unsupported | Open the URL in a supported system browser. |

Before release, manual checks are required in installed Safari PWAs on a
physical iPhone and iPad. Verify safe-area chrome in portrait and landscape,
software-keyboard dismissal, focus restoration and VoiceOver traversal, 200%
zoom/reflow, system/light/dark appearance, reduced motion, terminal text
selection and horizontal scrolling, standalone/offline launch recovery, and the
Update ready activation/reload path. These checks complement source and package
tests; they are not replaced by Simulator alone.

## Push is unavailable

The native client under `ios/` is not publicly distributed today. For a signed
development build or another compatible client, inspect `_info.push` and follow
[Push notifications](guides/push-notifications.md). The most common errors are
missing optional dependencies, an unreadable `.p8` file, a team/topic mismatch,
or the wrong APNs environment.

## Service-manager checklist

For launchd, systemd, or another supervisor:

- run as the tmux owner
- set an explicit `PATH` containing tmux, vmux, and optional tools
- use absolute config, APNs-key, and executable paths
- give the process a writable directory for overlays/caches
- keep the config and key permission-restricted
- stop and restart the service to rotate the server token

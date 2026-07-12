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

Do not include a real token in logs or an issue.

## Live updates reconnect repeatedly

The PWA prefers `/ws`. On disconnect it probes `/api/state` to distinguish an
authentication failure from a network failure, then retries the WebSocket.

Check:

- the browser network panel for `/ws`
- REST auth with `GET /api/state`
- a reverse proxy's WebSocket upgrade support
- idle timeouts on the proxy, VPN, or firewall
- that HTTPS pages reach `wss://`, not `ws://`

A wrong WebSocket token closes the connection with code `1008`. A session
disconnected from the PWA's connected-sessions screen closes with code `4001`
and should reconnect as a new session.

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

### The app reports an incompatible or unverified version

Check **Settings → Connection** for the iOS version, backend version, protocol
version, and compatibility result. If the app requests a server update, update
vmux on the tmux host and tap **Retry Now**. If the server requires a newer iOS
version or protocol, update the app from the App Store before retrying. These
known mismatches block connection but do not delete the saved address or token.

A **Compatibility unverified** warning means the server predates the additive
`_info.compatibility` metadata. The app may connect after its normal
`/api/config` and `/api/state` validation succeeds, but updating the backend to
a release that advertises compatibility is recommended. Do not treat an absent
compatibility object as permission to ignore a failed schema handshake.

## A reverse-proxied page loads but actions or state fail

The proxy must:

- forward all paths, including `/api/*`, `/vendor/*`, and `/ws`
- support WebSocket Upgrade and Connection semantics
- preserve `Authorization`
- serve valid HTTPS so the browser uses WSS
- avoid caching API and WebSocket responses

Do not expose the upstream 8787 port publicly. Redact the `/ws` query string
from logs because it contains the token.

## Reporting a connection problem safely

In the iOS app, expand **Technical Details** in the recovery card and copy the
sanitized summary. A useful report includes only:

- host and port (replace a private hostname if necessary)
- failing endpoint
- stable issue category
- HTTP status or `URLError` code
- app and server versions, when known
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

## Smart names are stale

AI-derived names are cached in `vmux-names.json` beside the overlay. Stop vmux,
remove only that cache, and restart to regenerate names. Local heuristic names
do not require the AI layer.

## Usage is unavailable or stale

`GET /api/config` exposes `_info.usage`. Confirm usage is enabled, the configured
command resolves in vmux's `PATH`, and tokscale is compatible. A failed refresh
preserves the last-good payload and marks it stale instead of stopping vmux.

## Push is unavailable

There is no public native client today. For compatible-client development,
inspect `_info.push` and follow [Push notifications](guides/push-notifications.md).
The most common errors are missing optional dependencies, an unreadable `.p8`
file, a team/topic mismatch, or the wrong APNs environment.

## Service-manager checklist

For launchd, systemd, or another supervisor:

- run as the tmux owner
- set an explicit `PATH` containing tmux, vmux, and optional tools
- use absolute config, APNs-key, and executable paths
- give the process a writable directory for overlays/caches
- keep the config and key permission-restricted
- stop and restart the service to rotate the server token

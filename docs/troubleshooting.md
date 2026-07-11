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
printf '%s\n' "$VMUX_TOKEN"
vmux --host 0.0.0.0 --token "$VMUX_TOKEN"
~~~

Common causes are trailing whitespace, connecting to another vmux instance, or
rotating the server token without updating the browser. Clearing site data also
removes the token stored by the PWA.

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

## A reverse-proxied page loads but actions or state fail

The proxy must:

- forward all paths, including `/api/*`, `/vendor/*`, and `/ws`
- support WebSocket Upgrade and Connection semantics
- preserve `Authorization`
- serve valid HTTPS so the browser uses WSS
- avoid caching API and WebSocket responses

Do not expose the upstream 8787 port publicly. Redact the `/ws` query string
from logs because it contains the token.

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

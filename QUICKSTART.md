# vmux quickstart

Run the vmux backend on the machine that has your tmux agent panes, confirm it
works locally, then connect from the web UI or companion app with a server
address and bearer token.

Two clients talk to this backend today: the built-in **web UI / PWA** (works
in any browser; "Add to Home Screen" makes it feel native) and the native
**iOS companion app** on the App Store
<!-- TODO(launch): link the App Store listing here -->, which adds real push
notifications and Keychain token storage. The app is closed-source for now;
this repo is the backend it talks to.

vmux is local-first: it binds to `127.0.0.1` by default, has no hosted account,
and treats the bearer token as the security boundary when you expose it to a
phone over Tailscale or your LAN.

## What you need

- Python 3.9+
- `tmux`
- `pipx` for an isolated install, or `uv` if you are running from a checkout
- Tailscale on your computer and phone for the easiest phone/app setup
- One or more coding agents running inside tmux panes

## Install the backend

```bash
pipx install vmux-agent
```

The PyPI package is `vmux-agent`; the command is `vmux`.

If you need the latest GitHub version instead:

```bash
pipx install git+https://github.com/imitation-alpha/vmux
```

From a local checkout, use:

```bash
uv run python -m vmux
```

## Start locally first

Start or attach a tmux session, run one or more agents in panes, then start
vmux in a normal terminal:

```bash
vmux
```

Open `http://127.0.0.1:8787` on the same computer.

vmux auto-discovers tmux panes, classifies each pane as an agent or shell, and
shows the agents that need input first. Plain idle shells are hidden by default;
if you want to confirm discovery before agents are running, start vmux with:

```bash
vmux --include-shells
```

Success looks like a grid/sidebar with at least one agent pane. If the page
loads but the list is empty, leave vmux running and start an agent inside tmux,
or restart with `vmux --include-shells` while testing with plain shell panes.

## Connect from your phone or app

The recommended setup is Tailscale. It gives your phone a private route to the
computer without exposing vmux to the public internet, an address that never
changes, and encryption on the wire. A plain LAN IP (`192.168.x.x:8787`) works
too when both devices share your home network — just mind the firewall and
that the IP can change. Never use a public internet address.

On the computer running tmux:

```bash
VMUX_TOKEN="$(openssl rand -hex 16)"
echo "$VMUX_TOKEN"
vmux --host 0.0.0.0 --token "$VMUX_TOKEN"
```

Keep that terminal open. The `echo` line prints the exact token to paste into
the companion app or web token prompt. On startup vmux confirms the bind with
a line like `vmux 0.1.0 -> http://<this-machine>:8787  (token set)` plus an
`app server address:` hint showing the `host:port` to paste into the app; it
deliberately never prints the token. That's also why this two-line form beats
the one-liner shown on the app's connect screen
(`… --token $(openssl rand -hex 16)`) — the inline form generates a token you
never get to see.

In the companion app:

- Server address: `<machine>.<tailnet>.ts.net:8787`
- Token: the value printed by `echo "$VMUX_TOKEN"`

The address field takes `host:port` — a Tailscale name
(`my-mac.tail-net.ts.net:8787`) or a LAN IP (`192.168.1.5:8787`) — or a full
`http://` / `https://` URL; plain `host:port` is treated as `http://`. The
token is stored in the iOS Keychain.

In a phone browser, open:

```text
http://<machine>.<tailnet>.ts.net:8787/?token=<VMUX_TOKEN>
```

If the browser does not include the token, vmux shows a paste-token screen.

`--host 0.0.0.0` with an empty token is a hard error by design. The token is the
security boundary; use Tailscale or an SSH tunnel, and do not expose vmux
directly to the public internet.

vmux itself serves plain HTTP. The app accepts `https://` addresses, so if you
want TLS, put vmux behind Tailscale Serve or your own reverse proxy — and keep
the plain-HTTP port inside your private network either way.

To verify the backend from another device before using the app, open the browser
URL above or run this from a machine that can reach the server:

```bash
curl -H "Authorization: Bearer $VMUX_TOKEN" \
  http://<machine>.<tailnet>.ts.net:8787/api/state
```

## Keep it running

vmux stops with the terminal that started it. The simplest fix is to give it
its own tmux session on the same machine:

```bash
tmux new-session -d -s vmux "vmux --host 0.0.0.0 --token $VMUX_TOKEN"
```

(`tmux kill-session -t vmux` stops it.) If you run vmux under launchd or
systemd instead, use absolute paths in `config.yaml` for anything it executes
or reads — `usage.command`, `push.apns_key_path` — because service managers
don't inherit your shell's `PATH` or expand `~` the way your terminal does.

To rotate the token, restart vmux with a new `--token` and re-enter it on each
device. The token is checked on every request, so old clients get `401`
immediately; the app then shows its token prompt again.

## Use the control panel

- Grid/sidebar: panes are ordered so the ones that need input float to the top.
- Dialog buttons: parsed menu options become tappable choices where possible.
- Detail view: inspect scrollback, open links, send text, use snippets, and send
  common keys like `Ctrl+C`, `Esc`, arrows, and `Enter`.
- Broadcast: select multiple panes and send one message to all of them.
- Settings: adjust discovery, poll interval, snippets, shortcuts, detectors, and
  optional usage or push features.

## Optional config

No config file is required for the first run.

```bash
cp config.example.yaml config.yaml
vmux -c config.yaml
```

See `config.example.yaml` for bind host/port, token, poll interval, scrollback
capture, pane naming, tmux auto-rename behavior, discovery, starred panes,
optional APNs push, optional tokscale usage tracking, and detector patterns. UI
edits persist to `vmux-settings.json`; vmux does not rewrite your hand-authored
`config.yaml`.

Pane naming alone has nine `naming_mode` values — from `session_window_pane`
(the default) to `smart`, which labels panes by task using local heuristics
plus optional AI backends (`claude` / `local` / `codex` / `agy` /
`antigravity`; the AI settings are YAML-only).

For native app or PWA integration details, see
[`docs/COMPANION_APP_BACKEND.md`](docs/COMPANION_APP_BACKEND.md).

## Optional: usage & quota tracking

vmux can show provider quotas and token-usage history in the app's stats view
via [tokscale](https://github.com/junhoyeo/tokscale). Install it yourself —
vmux never auto-downloads it:

```bash
npm i -g tokscale     # or: bun i -g tokscale
```

Then set `usage.enabled: true` in `config.yaml` (see the `usage:` block in
`config.example.yaml`) or flip the toggle in Settings; the exec'd
`usage.command` string itself stays YAML-only. When tokscale is missing or
tracking is off, the usage endpoints report `available: false` instead of
failing, and clients hide the stats. With push configured,
`usage.alert_threshold` also alerts you when a quota runs low.

## Optional: push notifications

The backend can push to the native iOS app over APNs when a pane needs input:

```bash
pip install "vmux-agent[push]"
```

then configure the `push:` block in `config.yaml`. One honest caveat: APNs
keys are scoped to the Apple team that signs the app, so self-hosted push
currently works only if you build the iOS app yourself. See
[`docs/PUSH_NOTIFICATIONS.md`](docs/PUSH_NOTIFICATIONS.md) for the full
walkthrough and details.

## Troubleshooting

**No tmux panes found**

Start a tmux session and run your coding agents inside tmux panes. To show plain
shell panes while testing discovery, run `vmux --include-shells`.

**The app says "Invalid server address"**

The address must be `host:port` or a full `http://` / `https://` URL — e.g.
`192.168.1.5:8787` or `my-mac.tail-net.ts.net:8787`.

**The app says "Bad or missing token"** (or the browser shows the paste-token
screen)

The server answered `401`. Stop vmux and restart it with a visible token:

```bash
VMUX_TOKEN="$(openssl rand -hex 16)"
echo "$VMUX_TOKEN"
vmux --host 0.0.0.0 --token "$VMUX_TOKEN"
```

Paste the printed token into the app exactly — no trailing whitespace. After
rotating a token, every device has to re-enter it.

**"Could not connect to the server." or timeouts**

Work down the checklist:

- vmux is running and its startup line shows the right bind, e.g.
  `vmux 0.1.0 -> http://<this-machine>:8787  (token set)`. If it shows
  `127.0.0.1`, restart with `--host 0.0.0.0`.
- The address includes port `8787`.
- Tailscale: both devices are in the same tailnet and Tailscale is up on both.
- LAN IP: the computer firewall allows inbound traffic to port `8787`, and the
  IP has not changed.
- From any machine that should reach the server:
  `curl -H "Authorization: Bearer $VMUX_TOKEN" http://<address>:8787/api/state`

Do not use a public internet address unless you put vmux behind your own
authenticated private access layer.

**vmux exits immediately, complaining about the token**

`--host 0.0.0.0` with an empty token is a hard error by design. Supply
`--token`, or bind `127.0.0.1` and reach it over an SSH tunnel.

**Live updates stall but taps still work**

The WebSocket dropped (a wrong token closes it with code `1008`); the app
falls back to polling every couple of seconds. Re-enter the token or
reconnect; if it keeps happening, check the network path.

**Push notifications do not work**

Push is optional and needs backend setup — see
[`docs/PUSH_NOTIFICATIONS.md`](docs/PUSH_NOTIFICATIONS.md), including why push
currently requires building the iOS app yourself.

## Contributor checks

```bash
uv run --extra dev pytest -q
```

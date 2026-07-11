# vmux

**Attention router for CLI coding-agent swarms in tmux—monitor and respond from your phone.**

vmux watches your tmux panes, identifies which agent needs attention, turns
supported terminal dialogs into tappable choices, and sends your response back
through tmux. The backend and installable PWA run on your machine: no hosted
account, telemetry, or cloud control plane.

<p align="center">
  <img src="https://raw.githubusercontent.com/imitation-alpha/vmux/main/docs/images/panel-list-view.jpg" width="360" alt="vmux pane list showing coding agents grouped and color-coded by status">
  <img src="https://raw.githubusercontent.com/imitation-alpha/vmux/main/docs/images/panel-session-view.jpg" width="360" alt="vmux pane detail showing captured output, a response composer, and shortcut keys">
</p>

> [!IMPORTANT]
> vmux is a pre-release beta. macOS is used daily; Linux should work but needs
> broader verification, and WSL support is not yet verified.

## Requirements

- Python 3.10–3.14
- tmux
- [pipx](https://pipx.pypa.io/) for an isolated source install

The planned PyPI distribution name is `vmux-agent`, but it is **not published
yet**. Until v0.1.0 is released, install the current source:

~~~bash
pipx install git+https://github.com/imitation-alpha/vmux.git
~~~

After publication, the command will become `pipx install vmux-agent`. The
distribution is named `vmux-agent`; the CLI command and Python import are both
`vmux`.

## 60-second local quickstart

Start at least one coding agent inside tmux, then run:

~~~bash
vmux
~~~

Open <http://127.0.0.1:8787>. vmux discovers agent panes automatically and
places panes needing input first. To include ordinary shell panes while testing:

~~~bash
vmux --include-shells
~~~

The PWA is the currently available client. Use your browser's **Add to Home
Screen** action for an app-like launcher. A separate native iOS companion is in
development, is not publicly available, and is not part of this repository.

## Remote access

vmux sends keystrokes to processes running as you, so network access is
security-sensitive:

- **Localhost** is the default and safest mode.
- **Tailscale** is the recommended way to reach vmux remotely. Bind a reachable
  interface and always set a bearer token.
- **SSH local-port forwarding** is supported while vmux remains bound to
  localhost. The tunnel must run on the device that opens the browser; on a
  phone, this requires an SSH client that exposes a local forward.
- **Direct LAN access** requires a bearer token and should be used only on a
  trusted network.
- **Public-internet access** requires a bearer token **and HTTPS** at a correctly
  configured reverse proxy. Keep vmux's plain-HTTP listener private.

Never expose bare vmux HTTP to the public internet. WebRTC, PeerJS, signaling or
hosted relays, automatic port forwarding, and unauthenticated public exposure
are outside the supported model. See the
[remote-access guide](https://imitation-alpha.github.io/vmux/remote-access/)
before using a non-loopback bind.

## What it does

- Discovers tmux panes and classifies Claude Code, generic agents (including
  Codex), and shells.
- Ranks `needs_input`, `error`, `working`, `idle`, and `offline` states.
- Parses Claude Code and conservative numbered dialogs; configurable regexes
  cover common prompts from other CLIs.
- Shows captured scrollback, extracted links, snippets, customizable allow-listed
  shortcut keys, pane stars, and connected sessions.
- Sends literal text, menu choices, or one broadcast message to multiple panes.
- Supports optional smart pane names, tokscale usage views, and an APNs backend
  for compatible companion clients.
- Vendors the PWA runtime assets; no third-party CDN is loaded.

## Documentation

- [Getting started](https://imitation-alpha.github.io/vmux/getting-started/)
- [Remote access](https://imitation-alpha.github.io/vmux/remote-access/)
- [Configuration](https://imitation-alpha.github.io/vmux/configuration/)
- [Troubleshooting](https://imitation-alpha.github.io/vmux/troubleshooting/)
- [Architecture and client API](https://imitation-alpha.github.io/vmux/reference/architecture/)

For a compact repository-local path, see
[QUICKSTART.md](https://github.com/imitation-alpha/vmux/blob/main/QUICKSTART.md).

## Contributing and support

Bug fixes and objective documentation corrections may be submitted directly.
Significant features—and all networking, authentication, wire-contract, or
dependency-expanding changes—need maintainer agreement in an issue first. Read
[CONTRIBUTING.md](https://github.com/imitation-alpha/vmux/blob/main/CONTRIBUTING.md),
the [Code of Conduct](https://github.com/imitation-alpha/vmux/blob/main/CODE_OF_CONDUCT.md),
and [SECURITY.md](https://github.com/imitation-alpha/vmux/blob/main/SECURITY.md)
before opening a pull request or security report.

## License

[MIT](https://github.com/imitation-alpha/vmux/blob/main/LICENSE) © imitation-alpha

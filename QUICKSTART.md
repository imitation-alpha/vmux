# vmux quickstart

The canonical guide is the
[vmux Getting started documentation](https://imitation-alpha.github.io/vmux/getting-started/).
This file keeps the repository path deliberately short.

## Requirements

- Python 3.10–3.14
- tmux with at least one agent running in a pane (default), or Herdr 0.8.2
- pipx

## Install the pre-release source

`vmux-agent` is not published to PyPI yet. Install from GitHub until v0.1.0 is
released:

~~~bash
pipx install git+https://github.com/imitation-alpha/vmux.git
~~~

## Run locally

~~~bash
vmux
~~~

Open <http://127.0.0.1:8787>. If an empty pane list is expected because you are
testing with ordinary shells, restart with `vmux --include-shells`.

To monitor and respond through Herdr instead, explicitly select one already
running named session. vmux never starts or stops it:

~~~yaml
terminal:
  provider: herdr
  herdr:
    session: my-agents
    binary: herdr
    events: auto
~~~

Run `vmux --config config.yaml`. Herdr creation/deletion, broadcast, Agent
Workspace, and all Herdr lifecycle/layout control remain unavailable. See the
[configuration guide](https://imitation-alpha.github.io/vmux/configuration/#terminal-provider)
for the guarded-input and compatibility details.

## Reach another device

Keep localhost for an
[SSH local-port forward](https://imitation-alpha.github.io/vmux/remote-access/#ssh-local-port-forwarding),
or use
[Tailscale](https://imitation-alpha.github.io/vmux/remote-access/#tailscale-recommended)
with a strong bearer token. Direct LAN access also requires a token. Public
internet access additionally requires HTTPS termination at a reverse proxy;
never publish vmux's plain-HTTP port.

For configuration, expected results, PWA installation, and troubleshooting, use
the [full Getting started guide](https://imitation-alpha.github.io/vmux/getting-started/).

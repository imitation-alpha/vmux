# CLI reference

The installed console command and `python -m vmux` call the same entry point.

## Flags

| Flag | Meaning |
| --- | --- |
| `-c PATH`, `--config PATH` | Load YAML from `PATH`. |
| `--host HOST` | Override `server.host`. Default: `127.0.0.1`. |
| `--port PORT` | Override `server.port`. Default: `8787`. |
| `--token TOKEN` | Override `server.token`. |
| `--include-shells` | Include ordinary shell panes for this run. |
| `--terminal-provider tmux\|herdr` | Override the YAML provider selection. |
| `--herdr-session NAME` | Override the one explicit named Herdr session. |
| `--version` | Print `vmux <version>` and exit. |
| `-h`, `--help` | Print help and exit. |

CLI values are applied after YAML and the Settings overlay. `--include-shells`
can only enable shell visibility; use configuration or Settings to disable an
overlay that already enables it.

## Startup checks

Before serving, vmux:

1. loads and validates configuration
2. refuses an empty token on a non-loopback bind
3. for tmux, verifies the executable, optionally disables automatic rename,
   and warns (without failing) when no panes exist
4. for Herdr, resolves/pins its executable and requires the exact configured
   session to be running and protocol-20 compatible; vmux never starts it

A non-loopback bind prints a warning that the listener is plain HTTP and that
public exposure requires TLS termination.

## Exit behavior

| Outcome | Exit status |
| --- | --- |
| Help or version requested | 0 |
| Normal server shutdown | 0 |
| argparse usage error | 2 |
| selected provider executable missing | 2 |
| configured Herdr session unavailable/incompatible | 2 |
| Missing/invalid config or unsafe bind | Non-zero |

Automation should rely on zero versus non-zero and may distinguish the
documented preflight status 2. Human-readable startup lines are diagnostics, not
a machine-readable protocol.

## Examples

~~~bash
# Default localhost
vmux

# Config file
vmux --config ~/.config/vmux/config.yaml

# Private reachable interface; token required
vmux --host 0.0.0.0 --token "$VMUX_TOKEN"

# Diagnostic shell discovery
vmux --include-shells

# One already-running explicit Herdr session
vmux --config ~/.config/vmux/config.yaml --terminal-provider herdr --herdr-session my-agents
~~~

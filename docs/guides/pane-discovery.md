# Pane discovery

vmux queries the tmux server for every pane, captures output, classifies each
pane, and decides whether it belongs in the state feed.

## Default behavior

With no configuration:

- `discovery.auto` is on.
- agent and other non-shell panes appear.
- ordinary shells are hidden.
- pane display names use `session_window_pane`.

To expose shells for a diagnostic run:

~~~bash
vmux --include-shells
~~~

The durable equivalent is:

~~~yaml
discovery:
  include_shells: true
~~~

## Inspect targets

Pane overrides use tmux's stable display target shape,
`session:window-index.pane-index`. Inspect it directly:

~~~bash
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'
~~~

The API uses `pane_id` values such as `%12` for live actions and exposes the
target separately for display and durable overrides.

## Restrict discovery

Turn automatic discovery off to show only configured targets:

~~~yaml
discovery:
  auto: false

panes:
  - target: "work:1.0"
    name: "API agent"
    kind: claude-code
    star: true
~~~

Configured targets always participate. If one is absent, vmux emits an
`offline` card with an internal id beginning `cfg:`. Offline cards cannot
receive actions until the pane returns.

## Override a pane

An override can set any combination of:

- `name`: display name, capped at 80 characters when edited through the API
- `kind`: `claude-code`, `generic`, or `shell`
- `star`: keep near the top and visible while offline

The Settings UI edits the full override list in the JSON overlay. Because that
overlay wins over YAML, later YAML edits to `panes` may be shadowed until the
overlay is changed or removed.

## Why a pane is missing

Check these in order:

1. The agent is running in tmux, not a standalone terminal.
2. vmux runs as the same OS user and can reach the same tmux server.
3. `discovery.auto` is true, or the exact target is configured.
4. `discovery.include_shells` is true if the current pane process is a shell.
5. A service manager has the correct `PATH` and user.

See [Troubleshooting](../troubleshooting.md) for connection and service checks.

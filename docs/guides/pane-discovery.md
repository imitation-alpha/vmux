# Pane discovery

vmux queries the selected terminal provider, captures output, classifies each
pane, and decides whether it belongs in the state feed. tmux remains the
default; Herdr uses one configured named session.

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

## Herdr discovery

Herdr polling uses one atomic snapshot, validates exact workspace/tab/pane and
agent links, and exposes that native hierarchy separately from display names.
Opaque public `h:…` handles exist only in the current server registry. Native
labels, numbers, titles, and agent names are untrusted display data and never
resolve or authorize an action.

Herdr 0.8.2 pane revision is a route/layout revision and does not advance for
ordinary output. vmux therefore reads every included pane rather than caching
terminal output by revision. It requests at least 200 `recent-unwrapped` rows
and trims locally because smaller reads can return empty. Failed discovery is
non-authoritative: the last good pane set stays visible with `stale:true` and
`actionable:false` until a valid snapshot returns.

Native state enriches the existing status but never grants input authority.
Optional status events only wake this same authoritative poll.

## Inspect targets

Tmux pane overrides use the stable display target shape,
`session:window-index.pane-index`. Inspect it directly:

~~~bash
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'
~~~

The API uses tmux `pane_id` values such as `%12` for live actions and exposes
the target separately for display and durable overrides.

Herdr uses opaque action and target values. Copy a Herdr target from state when
creating an override; never reconstruct it from labels. It is deliberately
route-qualified and changes after a move, so vmux will not silently transfer a
star/name to a same-labeled or moved endpoint.

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
- `kind`: `claude-code`, `codex`, `grok`, `opencode`, `antigravity`, `generic`, or `shell`
- `star`: keep near the top and visible while offline

The Settings UI edits the full override list in the JSON overlay. Because that
overlay wins over YAML, later YAML edits to `panes` may be shadowed until the
overlay is changed or removed.

## Why a pane is missing

Check these in order:

1. The agent is running in the selected tmux/Herdr provider, not a standalone terminal.
2. vmux runs as the same OS user and can reach the same tmux server or exact named Herdr session.
3. `discovery.auto` is true, or the exact target is configured.
4. `discovery.include_shells` is true if the current pane process is a shell.
5. A service manager has the correct `PATH` and user.

See [Troubleshooting](../troubleshooting.md) for connection and service checks.

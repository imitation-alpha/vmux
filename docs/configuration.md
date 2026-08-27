# Configuration

vmux runs without a configuration file. YAML is for durable server-side choices;
the Settings UI is for the validated subset that can change while vmux is
running.

The complete annotated source is
[`config.example.yaml`](https://github.com/imitation-alpha/vmux/blob/main/config.example.yaml).

## Load a file

~~~bash
vmux --config /path/to/config.yaml
~~~

The short form is `vmux -c /path/to/config.yaml`. Paths in fields such as
`push.apns_key_path` and executable commands should be absolute when vmux runs
under a service manager.

## Precedence and persistence

Configuration is resolved in this order, from lowest to highest precedence:

1. built-in defaults
2. the selected YAML file
3. the Settings UI JSON overlay
4. CLI overrides (`--host`, `--port`, `--token`, and `--include-shells`)

When a config path is used, the overlay is `vmux-settings.json` beside that
file. Without a config path it is `~/.vmux/settings.json`. UI updates replace
the relevant overlay values atomically; vmux never rewrites the YAML.

Because the overlay wins, changing a live-editable value in YAML may appear to
have no effect if an older value remains in the overlay. Change it in Settings
or stop vmux and remove the overlay if you deliberately want to reset all UI
edits.

Related local state is stored in the same directory:

- `vmux-push.json` for registered APNs device tokens
- `vmux-names.json` for smart-name cache entries
- `vmux-agents.sqlite3` (plus SQLite WAL files while running) for normalized
  Agent Context, messages, decisions, snapshots, and visit baselines
- `server-instance-id` for the non-secret stable id used to validate native
  notification routes

Protect these files and never commit them.

Browser-only preferences are separate from the server overlay. Each browser
profile stores its theme, alert choices, default destination, navigator
view/sort, terminal wrapping, shortcut buttons, snippets, and bearer token in
local storage. These values do not modify YAML and do not roam through vmux.
Pane snapshots and terminal output are not browser-persisted.

## PWA Settings

Settings are grouped into:

- **Appearance & Alerts**
- **Input Shortcuts & Snippets**
- **Server & Discovery**
- **Experimental**
- **Agent Overrides & Detectors**
- **Usage**
- **Sessions**
- **Connection & About**

Compact layouts drill into one category at a time; medium and wide layouts use
category navigation beside the selected section. Browser-local switches and
selectors save immediately. Server switches and selectors show pending state
and roll back if the server rejects them. Numeric values, agent names/kinds,
shortcut/snippet lists, and detector patterns use drafts with an explicit
**Save** action so overlapping full-config updates cannot overwrite one another.

New browser profiles use system appearance, ambient motion off, notifications
off, Queue as the default destination, list navigation below 1200 pixels, and
tree navigation at 1200 pixels and above. Version-2 preference migration keeps
recognized existing choices and maps legacy `needs`/`working` destinations to
`queue`/`active`.

## Core fields

| YAML field | Default | Live-editable | Meaning |
| --- | --- | --- | --- |
| `server.host` | `127.0.0.1` | No | Bind address. Any non-loopback value requires a token. |
| `server.port` | `8787` | No | HTTP and WebSocket port. |
| `server.token` | empty | No | Bearer token. Use a long random value for any non-loopback bind. |
| `poll_interval` | `0.7` seconds | Yes | Delay between capture passes; UI values clamp to 0.2–10 seconds. |
| `capture_lines` | `200` | Yes | Scrollback lines captured per pane; values clamp to 40–2000. |
| `naming_mode` | `session_window_pane` | Yes | Source used for display names. |
| `tmux.disable_auto_rename` | `true` | No | Disables tmux's global `automatic-rename` option at startup. |
| `discovery.auto` | `true` | Yes | Include panes found from the live tmux server. |
| `discovery.include_shells` | `false` | Yes | Include ordinary idle shell panes. |
| `agents.retention_days` | `30` | No | Retain historical agent snapshots, visible messages, and resolved decisions. |

Disabling automatic rename is a tmux-wide change. Set
`tmux.disable_auto_rename: false` if another tmux workflow owns window names.

## Server examples

Localhost needs no file:

~~~yaml
server:
  host: 127.0.0.1
  port: 8787
  token: ""
~~~

A non-loopback bind needs a token:

~~~yaml
server:
  host: 0.0.0.0
  port: 8787
  token: "replace-with-a-long-random-token"
~~~

YAML values generally do not perform shell or environment-variable expansion;
the creation roots documented below are the exception and expand `~`. Restrict
the file's permissions, keep it outside version control, and follow
[Remote access](remote-access.md) before changing the bind.

## Discovery and pane overrides

The `panes` list can rename, reclassify, or star a stable tmux target:

~~~yaml
discovery:
  auto: true
  include_shells: false

panes:
  - target: "work:1.1"
    name: "API refactor"
    kind: claude-code
    star: true
~~~

Allowed kinds are `claude-code`, `codex`, `grok`, `opencode`, `antigravity`,
`generic`, and `shell`. A configured target stays visible as `offline` when its pane
disappears. Manual names and stars can also be edited live. See
[Pane discovery](guides/pane-discovery.md).

## Tmux creation

Creation is opt-in and YAML-only. It remains unavailable unless `enabled` is
true and at least one root exists, is a directory, and is readable/searchable
by the vmux process:

~~~yaml
creation:
  enabled: true
  roots:
    - label: Products
      path: ~/dev/repos/products
  runtimes:
    codex: [codex]
    claude: [claude]
    agy: [agy]
    grok: [grok]
    opencode: [opencode]
~~~

Root paths expand `~` and are resolved canonically at startup. Invalid roots
are excluded; if none remain, creation is disabled with a setup reason. Every
typed, recent, or browsed working directory is canonicalized again for each
request and must stay inside a configured root. A symlink that escapes a root
is therefore neither browsable nor usable for creation.

`shell` is implicit and launches tmux's default shell. The `agy` wire/config ID
is retained and displayed as **Antigravity**; `grok` is displayed as **Grok
Build**. The other keys are the
only runtime IDs clients may submit. Each value is a server-owned argument
array: the first item is the executable and later items are fixed arguments.
Clients cannot submit a command, arbitrary arguments, environment variables,
or a runtime ID outside this allowlist. Presets whose executable is missing are
advertised as unavailable rather than attempted.

The same bearer token that authorizes pane input authorizes filesystem browsing
within these roots and process creation as the vmux OS user. Keep roots narrow,
do not point them at a home directory or filesystem root, and protect the token.
Creation settings are intentionally absent from `PATCH /api/config` and require
a server restart after YAML changes.

## Detector patterns

Both lists are live-editable:

~~~yaml
detectors:
  generic_prompt_patterns:
    - "\\(y/n\\)"
    - "Do you want to"
    - "Press enter to"
  error_patterns:
    - "Traceback \\(most recent call last\\)"
    - "^\\s*Error:"
~~~

UI-supplied lists are limited to 40 patterns of 200 characters each, patterns
with obvious nested quantifiers are rejected, and matches run with a hard
timeout. Details and testing advice are in [Agent detectors](guides/agent-detectors.md).

## Usage tracking

| YAML field | Default | Live-editable |
| --- | --- | --- |
| `usage.enabled` | `false` | Yes |
| `usage.command` | `tokscale` | No |
| `usage.quota_refresh` | `180` seconds | Yes, 30–3600 |
| `usage.report_refresh` | `300` seconds | Yes, 60–3600 |
| `usage.alert_threshold` | `20` percent | Yes, 0–100 |

`usage.command` is deliberately YAML-only because vmux executes it. It is parsed
into an argument list and is never passed to a shell. See
[Usage tracking](guides/usage-tracking.md).

The usage controls appear under **Settings → Usage**. Enabling the
collector saves immediately; quota/report intervals and the warning threshold
are drafted and saved together. **Displayed quotas** adds server-wide provider
and meter switches backed by `usage_hidden_quota_providers` and
`usage_hidden_quota_metrics` in `vmux-settings.json`. Names are trimmed,
deduplicated, bounded, and matched exactly against tokscale's normalized
provider and label names. Newly discovered entries are visible by default;
saved choices for temporarily absent entries are retained. Hiding a provider
preserves its meter choices, and **Show all** clears both hidden lists.

Visibility changes only the provider cards and meters on Stats. Cost summaries,
history, activity breakdowns, `/api/usage`, the Stats warning badge, warning
notices, and APNs alerts continue to use the complete quota snapshot. The
section also reports whether the configured collector is installed. The Stats destination represents disabled,
not-installed, timeout, error, stale, empty, loading, and refresh states without
disabling pane monitoring.

## Experimental Agent Context

Agent Context, Review, Timeline, structured decisions/chat, observation, and
their local database writes are one server-wide experimental bundle. It is off
by default and is enabled only with **Settings → Experimental → Enable Agent
Workspace**. The switch is persisted in `vmux-settings.json` and starts or stops
the runtime without restarting vmux. An `agents.enabled` YAML value is ignored.

The remaining `agents` fields are YAML-only and take effect after restart:

~~~yaml
agents:
  retention_days: 30
  codex_home: ~/.codex
  claude_home: ~/.claude
~~~

Home paths are expanded but should point only at runtime data owned by the same
user as vmux. `retention_days` accepts 1–3650 days and applies to historical
snapshots, visible messages, and resolved decisions; current context and pending
decisions are retained while active. Turning the workspace off does not delete
the existing database; re-enabling restores access to retained history. See
[Agent context and decision inbox](guides/agent-context.md)
for retention, deletion, parser compatibility, and safe-control behavior.

## Push

All `push` fields are YAML-only:

~~~yaml
push:
  apns_key_path: /absolute/path/to/AuthKey_ID.p8
  apns_key_id: ABC123DEFG
  apns_team_id: XYZ1234567
  apns_topic: com.example.vmux-client
  environment: sandbox
  on_error: false
  cooldown: 30
~~~

`environment` is `sandbox` or `production`. Key material never appears in the
Settings API. The native client under `ios/` remains under development and is
not publicly available. Read
[Push notifications](guides/push-notifications.md) before enabling it.

## Smart naming

`naming_mode: smart` enables local heuristics. The optional AI layer is off by
default and all of its backend settings are YAML-only because it can send recent
pane output to a configured command or endpoint:

~~~yaml
naming_mode: smart
auto_naming:
  ai_enabled: false
  ai_backend: claude
  max_len: 24
  timeout: 60
~~~

The full backend-specific fields are documented in
[Smart naming](guides/smart-naming.md) and in the annotated example.

## Live-editable schema

`GET /api/config` returns the current editable fields and a read-only `_info`
object. `PATCH /api/config` accepts a partial object containing:

- `poll_interval`
- `capture_lines`
- `auto_discover`
- `include_shells`
- `naming_mode`
- `overrides`
- `generic_prompt_patterns`
- `error_patterns`
- `usage_enabled`
- `usage_quota_refresh`
- `usage_report_refresh`
- `usage_alert_threshold`
- `usage_hidden_quota_providers` (array of exact provider names)
- `usage_hidden_quota_metrics` (array of `{provider, label}` objects)
- `experimental_agent_workspace_enabled`

The bearer token, bind, tmux auto-rename choice, push credentials,
`usage.command`, Agent Context retention/runtime paths, and AI backend settings cannot be
changed through this API.

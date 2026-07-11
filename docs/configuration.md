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

Protect these files and never commit them.

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

YAML values do not perform shell or environment-variable expansion. Restrict
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

Allowed kinds are `claude-code`, `generic`, and `shell`. A configured target
stays visible as `offline` when its pane disappears. Manual names and stars can
also be edited live. See [Pane discovery](guides/pane-discovery.md).

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
Settings API. There is no publicly available native client; this subsystem is
for compatible companion-client implementations. Read
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

The bearer token, bind, tmux auto-rename choice, push credentials,
`usage.command`, and AI backend settings cannot be changed through this API.

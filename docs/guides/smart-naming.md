# Smart naming

Display names help distinguish many similar agent panes. A manual pane override
always wins; otherwise `naming_mode` chooses the source.

## Naming modes

| Mode | Result |
| --- | --- |
| `pane` | Pane index |
| `window_pane` | `window-index:pane-index` |
| `session_pane` | `session:pane-index` |
| `session_window_pane` | `session:window-index:pane-index` (default) |
| `title` | tmux pane title with spinner glyphs removed |
| `window` | tmux window name |
| `target` | Exact `session:window-index.pane-index` target |
| `command` | Current command basename |
| `smart` | Local heuristic, then optional cached AI name |

Choose a mode in Settings or YAML:

~~~yaml
naming_mode: smart
~~~

Empty names fall back to the pane target.

## Local smart heuristic

The heuristic does not contact a network service:

- shells prefer the current directory
- editors and tools combine command and directory where useful
- agents prefer a meaningful pane title, then directory or command
- SSH panes try to identify the destination host from local process arguments

It changes only vmux display names. It does not rename tmux windows or install
tmux hooks.

## Optional AI layer

The AI layer is off by default:

~~~yaml
auto_naming:
  ai_enabled: false
  ai_backend: claude
  max_len: 24
  timeout: 60
~~~

Supported backend names are `claude`, `local`, `codex`, `agy`, and
`antigravity`. Executable paths, models, local endpoint/API key, prompt, program
matching, prefix mapping, and flags are all YAML-only. Consult the
[annotated example](https://github.com/imitation-alpha/vmux/blob/main/config.example.yaml)
for their exact keys.

!!! warning "Pane output may leave vmux"

    When AI naming is enabled, vmux supplies the pane target, command, path,
    current Git branch, and up to the last 40 captured pane lines to the selected
    command or endpoint. That output can contain source code, prompts,
    credentials, or customer data. Enable only a backend you trust.

The sanitized result is lowercase kebab-case and cached in `vmux-names.json`
beside the settings overlay. The cache key is based on command and path; remove
the cache while vmux is stopped if you need every name regenerated.

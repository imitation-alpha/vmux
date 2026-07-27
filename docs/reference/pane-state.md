# `PaneState` wire format

`PaneState.to_dict()` defines the object sent by `GET /api/state` and WebSocket
state frames.

## Example

~~~json
{
  "id": "%12",
  "target": "work:1.1",
  "name": "work:api:1",
  "kind": "claude-code",
  "status": "needs_input",
  "title": "",
  "question": "Allow this edit?",
  "menu": [
    {
      "key": "1",
      "label": "Yes",
      "description": "Apply the proposed edit.",
      "selected": true,
      "freeform": false
    }
  ],
  "preview": ["Allow this edit?", "1. Yes", "2. No"],
  "lines": ["Allow this edit?", "1. Yes", "2. No"],
  "updated": 1720000000.0,
  "changed": false,
  "window": "api",
  "starred": false,
  "interacted": 0.0
}
~~~

## Fields

| Field | Type | Semantics |
| --- | --- | --- |
| `id` | string | Live tmux pane id such as `%12`. Configured offline placeholders begin `cfg:`. |
| `target` | string | Durable display/config target, normally `session:window.pane`. |
| `name` | string | Resolved display name after manual override/naming mode. |
| `kind` | enum | `claude-code`, `codex`, `grok`, `opencode`, `antigravity`, `generic`, or `shell`. |
| `status` | enum | `needs_input`, `error`, `working`, `idle`, or `offline`. |
| `title` | string | Raw tmux pane title. |
| `question` | string or null | Bounded prompt text when a dialog was recognized. |
| `menu` | array | Parsed `MenuOption` objects; empty when no supported options exist. |
| `preview` | string array | Last six non-empty captured lines. |
| `lines` | string array | Captured pane output split into lines. |
| `updated` | number | Unix epoch seconds of the most recent observed output change. |
| `changed` | boolean | Whether captured output changed in this poll pass. |
| `window` | string | tmux window name. |
| `starred` | boolean | Current per-target star override. |
| `interacted` | number | Unix epoch seconds of the last action vmux sent to this live pane, or 0. |

`updated` is not the snapshot time. It stays constant while output is unchanged.
`changed` is a transient hint and may become false on the next snapshot.

## `MenuOption`

| Field | Type | Semantics |
| --- | --- | --- |
| `key` | string | Value `POST /api/select` sends for this choice. |
| `label` | string | Human-readable button label. |
| `description` | string | Bounded supporting text. The server serializes `""` when unavailable. |
| `selected` | boolean | The TUI currently highlights this/default option. |
| `freeform` | boolean | The choice is expected to open or invite a free-text reply. |

Clients should display `label` but submit `key` unchanged.

## Client rules

- Replace the previous pane set when a full state snapshot arrives.
- Use `id` for immediate actions and `target` for persistent user configuration.
- Do not send actions to `cfg:` offline ids.
- Treat unknown fields as additive and ignore them safely.
- Preserve unknown enum values as an “unknown” UI state rather than crashing.
- Do not assume pane ids remain valid after a pane disappears.
- Treat `lines` and `question` as untrusted terminal text; render them as text,
  not HTML.

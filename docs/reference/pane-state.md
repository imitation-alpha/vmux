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
  "interacted": 0.0,
  "provider": "tmux",
  "hierarchy": [],
  "capabilities": {
    "capture": true,
    "input": "legacy",
    "literal_text": true,
    "menu_select": true,
    "keys": ["Enter", "Escape"],
    "broadcast": true,
    "create": true,
    "delete": false,
    "native_agent_state": false
  },
  "native_agent": null,
  "action_guard": null,
  "actionable": true,
  "stale": false
}
~~~

## Fields

| Field | Type | Semantics |
| --- | --- | --- |
| `id` | string | Opaque immediate action handle. tmux retains `%12`; Herdr uses an unparseable `h:…` token. Configured offline placeholders begin `cfg:`. |
| `target` | string | Provider target for persistence/config. tmux uses `session:window.pane`; Herdr uses an opaque route-qualified value that changes after a move. |
| `name` | string | Resolved display name after manual override/naming mode. |
| `kind` | enum | `claude-code`, `codex`, `grok`, `opencode`, `antigravity`, `generic`, or `shell`. |
| `status` | enum | `needs_input`, `error`, `working`, `idle`, or `offline`. |
| `title` | string | Untrusted provider pane title, for display/classification only. |
| `question` | string or null | Detected prompt text, or a generic waiting-for-input message for native blocked state without a parsed question. Display text is not an action guard. |
| `menu` | array | Parsed `MenuOption` objects; empty when no supported options exist. |
| `preview` | string array | Last six non-empty captured lines. |
| `lines` | string array | Captured pane output split into lines. |
| `updated` | number | Unix epoch seconds of the most recent observed output change. |
| `changed` | boolean | Whether captured output changed in this poll pass. |
| `window` | string | Provider window/tab display label. |
| `starred` | boolean | Current per-target star override. |
| `interacted` | number | Unix epoch seconds of the last action vmux sent to this live pane, or 0. |
| `provider` | string | `tmux` or `herdr`. Unknown providers must be treated as read-only. |
| `hierarchy` | array | Ordered opaque nodes (`session`, optional `workspace`, `window`/`tab`, `pane`) with display-only label and nullable position. |
| `capabilities` | object | Per-endpoint capture/input mode, safe keys, broadcast/create/delete, and native-state support. |
| `native_agent` | object or null | Herdr's bounded native kind/name/status (`idle`, `working`, `blocked`, `done`, `unknown`), state sequence, and readiness. It is display/state evidence, never authorization. |
| `action_guard` | object or null | Herdr route revision plus opaque prompt/options fingerprints required by `POST /api/input`. |
| `actionable` | boolean | Server decision that current input is safe to attempt. Clients must honor `false`. |
| `stale` | boolean | Read-only state retained after discovery/capture failure, or an unavailable configured target. |

`updated` is not the snapshot time. It stays constant while output is unchanged.
`changed` is a transient hint and may become false on the next snapshot.

## `MenuOption`

| Field | Type | Semantics |
| --- | --- | --- |
| `id` | string, optional | Opaque Herdr option identity submitted to guarded input. Absent for legacy tmux menus. |
| `key` | string | Legacy tmux selection key and display shortcut. Herdr clients submit `id`, never a raw key. |
| `label` | string | Human-readable button label. |
| `description` | string | Bounded supporting text. The server serializes `""` when unavailable. |
| `selected` | boolean | The TUI currently highlights this/default option. |
| `freeform` | boolean | The choice is expected to open or invite a free-text reply. |

Clients should display `label`. tmux clients submit `key`; guarded Herdr clients
submit only the opaque option `id` with the current action guard.

## Client rules

- Replace the previous pane set when a full state snapshot arrives.
- Use `id` for immediate actions and `target` for persistent user configuration; never parse either Herdr value.
- Honor `actionable`, `stale`, and `capabilities.input`. Use `/api/input` only for `guarded_v1`; never fall back to legacy actions.
- Render hierarchy/native labels as untrusted text and never use them for identity, association, or authorization.
- Do not send actions to `cfg:` offline ids.
- Treat unknown fields as additive and ignore them safely.
- Preserve unknown enum values as an “unknown” UI state rather than crashing.
- Do not assume pane ids remain valid after a pane disappears.
- Treat `lines` and `question` as untrusted terminal text; render them as text,
  not HTML.

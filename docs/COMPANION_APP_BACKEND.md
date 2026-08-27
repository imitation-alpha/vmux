# REST and WebSocket API

The backend contract is used by the bundled PWA and can be implemented by other
clients. The server owns the tmux panes and defaults to
`http://127.0.0.1:8787`.

## Authentication

When `server.token` is non-empty:

- REST sends `Authorization: Bearer <token>`.
- WebSocket connects to `/ws?token=<url-encoded-token>`.
- bad REST auth returns `401` and
  `{"detail":"bad or missing token"}`.
- bad WebSocket auth closes with code `1008` before acceptance.

When the configured token is empty, auth is not required. That mode is supported
only on a loopback bind and should still use a token on shared hosts.

Token comparison is constant-time. The token never appears in an API response.
Because WebSocket auth is in the query string, clients and proxies must prevent
full request targets from entering logs.

## State

### `GET /api/state`

Returns a full snapshot:

~~~json
{
  "type": "state",
  "panes": []
}
~~~

Each entry follows the [`PaneState` wire format](https://imitation-alpha.github.io/vmux/reference/pane-state/).

## Pane actions

All action bodies identify the pane in JSON so ids such as `%12` never need to
be path-encoded.

| Endpoint | Request body | Success |
| --- | --- | --- |
| `POST /api/key` | `{"id":"%12","key":"Enter"}` | `{"ok":true}` |
| `POST /api/text` | `{"id":"%12","text":"continue","enter":true}` | `{"ok":true}` |
| `POST /api/select` | `{"id":"%12","key":"1"}` | `{"ok":true}` |
| `POST /api/broadcast` | `{"ids":["%12","%13"],"text":"run tests","enter":true}` | `{"ok":true,"sent":2,"errors":[]}` |
| `POST /api/star` | `{"target":"work:1.1","starred":true}` | `{"ok":true}` |

Named keys must come from `GET /api/config` → `_info.allowed_keys`. Text is sent
literally and optionally followed by Enter. `select` interprets a Claude Code
option key as a direct character press; generic `enter` becomes Enter, and other
generic values are sent literally plus Enter.

For key/text actions, an id that cannot be resolved or validated returns `404`.
A well-formed but stale tmux id can instead reach tmux and return `400`; select
and other tmux action errors also return `400`. Broadcast reports individual
unresolved ids/errors in `errors` while returning an overall successful
response. Refresh state before retrying any stale action.

## Temporary image uploads

### `POST /api/images`

Uploads one image for later use by a pane or structured-agent composer. Send the
file bytes as the request body, not JSON, base64, or multipart form data. The
request uses the same bearer authentication as every REST action and declares
the representation with `Content-Type: image/png`, `image/jpeg`, `image/webp`,
or `image/gif`.

~~~http
POST /api/images HTTP/1.1
Authorization: Bearer <token>
Content-Type: image/png

<raw PNG bytes>
~~~

Success is `201` with `Cache-Control: no-store`:

~~~json
{
  "id": "b8af268e-733b-405b-93ca-a1a72e09e438",
  "path": "/Users/example/.vmux/uploads/b8af268e733b405b93caa1a72e09e438.png",
  "terminal_text": "/Users/example/.vmux/uploads/b8af268e733b405b93caa1a72e09e438.png",
  "mime_type": "image/png",
  "size": 12345,
  "expires_at": 1785182400
}
~~~

`path` is an absolute path on the vmux host. `terminal_text` is the same path
quoted as one POSIX shell token when quoting is required. Clients should append
`terminal_text` to the current draft with one separating space when needed.
Uploading does **not** call `/api/text`, send an agent message, or press Enter;
submission remains a separate, explicit user action through the existing text
or agent-message endpoint.

The server streams the body to a private temporary file, enforces a 20 MiB
per-image limit and a 200 MiB total quota, then checks both the declared media
type and the file signature before atomically exposing the final opaque name.
An empty body, malformed signature, unsupported type, or mismatch between the
header and bytes returns `415`. An image over 20 MiB returns `413`. A quota or
filesystem failure returns `507` with a bounded message. Partial files from a
cancelled or interrupted request are removed.

Files live under `~/.vmux/uploads` with directory mode `0700` and file mode
`0600`. They are not available through the static web mount or any download
endpoint. vmux removes files after 24 hours, attempting cleanup at startup,
hourly, and before evaluating quota. `expires_at` is an epoch-second deadline;
clients should explain that a path retained in a draft can expire and offer a
new upload rather than automatically retrying or submitting it.

Browser and native clients should expose upload progress, cancellation, and a
retry that keeps the existing draft. Image paste should be intercepted only
when the clipboard contains an image, leaving ordinary text paste unchanged.
The image action must be disabled wherever composer submission is disabled,
including offline, unauthorized, incompatible, unbound, or otherwise
unavailable-pane states.

Photo pickers may return HEIC or another unsupported representation. Native
iOS clients must render those representations to JPEG (for example with
`UIImage.jpegData`) before upload, declare `image/jpeg`, use an authenticated
`URLSessionUploadTask` so progress and cancellation follow existing transport
conventions, and decode the response above. A confirmed `401` follows the
client's normal authentication-recovery path; `413`, `415`, and `507` should be
shown as actionable upload failures without clearing the draft. Clipboard and
photo-picker success both append `terminal_text` and still require the user to
tap Send.

This mechanism assumes vmux and the target tmux process share a host and
filesystem namespace. A path returned by one host will not work inside a
nested SSH session; second-hop transfer is outside this contract.

## Tmux creation

The creation endpoints use normal REST bearer authentication. They are present
in protocol 1, but clients must show creation only when
`_info.capabilities.tmux_create_v1` exists. Its `enabled` value and `reason`
describe current server setup and tmux availability.

### `GET /api/tmux/creation`

Returns the effective state, canonical configured roots, up to 20 unique recent
in-root pane directories, and the fixed runtime allowlist:

~~~json
{
  "enabled": true,
  "reason": null,
  "roots": [{"label":"Products","path":"/Users/me/dev/repos/products"}],
  "recent_directories": [
    {"name":"vmux","path":"/Users/me/dev/repos/products/vmux","root_label":"Products"}
  ],
  "runtimes": [
    {"id":"shell","label":"Shell","available":true,"reason":null},
    {"id":"codex","label":"Codex","available":true,"reason":null},
    {"id":"agy","label":"Antigravity","available":true,"reason":null},
    {"id":"grok","label":"Grok Build","available":true,"reason":null}
  ]
}
~~~

Shell is implicit. `agy` remains the compatible ID for Antigravity, and `grok`
selects Grok Build. Other runtime IDs map to YAML-owned argument arrays; no
command or arguments are returned to or accepted from clients.

### `GET /api/tmux/directories?path=…`

`path` must be absolute or begin with `~/`. Success returns its canonical path,
owning root, in-root parent, and at most 500 name-sorted child directories:

~~~json
{
  "path":"/Users/me/dev/repos/products",
  "root":{"label":"Products","path":"/Users/me/dev/repos/products"},
  "parent":null,
  "directories":[{"name":"vmux","path":"/Users/me/dev/repos/products/vmux"}],
  "truncated":false
}
~~~

Unreadable entries and symlinks whose canonical destination escapes all roots
are omitted.

### `POST /api/tmux/create`

Accepted bodies are exact and type-specific:

~~~json
{"type":"session","cwd":"/path","runtime":"codex","name":null}
{"type":"window","parent_session":"work","cwd":"/path","runtime":"shell","name":"api"}
{"type":"pane","parent_pane_id":"%4","cwd":"/path","runtime":"claude","split":"side_by_side","size_percent":50}
~~~

Each body must contain exactly one location selector: either `cwd`, as above,
or an opaque `worktree_id` from a currently represented pane or agent's
[`workspace` identity](#workspace-identity). For example:

~~~json
{"type":"session","worktree_id":"wt_0123456789abcdef01234567","runtime":"codex","name":null}
~~~

The server resolves the id from its current in-memory registry and repeats the
same canonical creation-root check used for `cwd`; clients must not persist or
derive worktree ids.

Pane split direction is `side_by_side` or `stacked`; size is an integer from
10 through 90 and defaults to 50. Session/window names are 1–64 ASCII letters,
numbers, underscores, or hyphens. `null` asks the server to slugify the working
directory basename and serialize conflict-free suffix selection (`name`,
`name-2`, …). A non-null conflicting name is never silently changed.

Success is `201` after the returned pane is verified live:

~~~json
{"pane_id":"%12","target":"work:1.0"}
~~~

New targets are detached and do not change an attached host client. Errors are
sanitized: `400` invalid shape/value, `403` outside configured roots, `404`
missing or stale worktree/directory or vanished parent, `409` name/tmux
conflict, and `503` disabled creation, unavailable tmux/runtime, or an
immediately exited pane.
Responses never expose subprocess arguments, environment, or stderr.

## Configuration

### `GET /api/config`

Returns the live-editable fields plus:

~~~json
{
  "experimental_agent_workspace_enabled": true,
  "_info": {
    "host": "127.0.0.1",
    "port": 8787,
    "token_set": false,
    "version": "0.1.2",
    "compatibility": {
      "protocol_version": 1,
      "minimum_ios_version": "1.0.0"
    },
    "targets": [],
    "allowed_keys": [],
    "push": {},
    "usage": {},
    "server_instance_id": "b2d9659e-...",
    "capabilities": {
      "agent_context_v1": {
        "enabled": true,
        "runtimes": ["codex", "claude"],
        "websocket": true,
        "websocket_path": "/ws/agents",
        "mode": "log_observer",
        "retention_days": 30,
        "persistence": "structured_only",
        "chat": "confirmed_idle_only",
        "decisions": "verified_structured_only",
        "degraded_reason": null
      },
      "agent_review_v1": {
        "enabled": true,
        "version": 1,
        "scheduling": true,
        "finish_batch": true,
        "min_interval_minutes": 5,
        "max_interval_minutes": 1440
      },
      "pane_lifecycle_v1": {"version": 1, "history_limit": 32},
      "workspaces_v1": {
        "version": 1,
        "supported": true,
        "enabled": true,
        "reason": null
      },
      "tmux_create_v1": {
        "version": 1,
        "supported": true,
        "enabled": true,
        "reason": null
      }
    }
  }
}
~~~

`version` is the backend software version. `compatibility.protocol_version`
identifies the REST/WebSocket contract, while `minimum_ios_version` is the
oldest iOS marketing version supported by this server. `targets` contains
currently represented tmux targets. Push and usage info report
capability/availability without exposing credentials.

`capabilities.agent_context_v1` gates the independent agent context, and
`capabilities.agent_review_v1` gates the cross-client Review workflow. Clients
must fall back to the terminal workspace when it is absent or disabled.
`experimental_agent_workspace_enabled` is the authoritative server-persisted
switch; clients must not hydrate agent REST resources or open `/ws/agents`
unless it is `true` and the corresponding capability is enabled.
`capabilities.pane_lifecycle_v1` gates the additive lifecycle summary and
diagnostics, while `capabilities.workspaces_v1` gates workspace identities.
`capabilities.tmux_create_v1` independently gates all creation entry points;
older clients ignore it and updated clients hide creation when it is absent. The
opaque `server_instance_id` lets a native client reject a notification route
created by a different vmux server. Capability names and string values are an
allowlist: an unknown future value must remain read-only until the client
understands its safety rules.

### Workspace identity

When `workspaces_v1.enabled` is true, a live `PaneState` or `AgentSession` may
contain this additive `workspace` object:

~~~json
{
  "workspace_id": "ws_0123456789abcdef01234567",
  "workspace_name": "vmux",
  "worktree_id": "wt_0123456789abcdef01234567",
  "worktree_name": "vmux-feature",
  "branch": "feature/lifecycle",
  "detached_commit": null,
  "is_primary": false,
  "launchable": true,
  "launch_unavailable_reason": null
}
~~~

`workspace_id` groups checkouts from the same Git repository;
`worktree_id` identifies one checkout. Exactly one of `branch` and
`detached_commit` is normally non-null. Names and branch/commit labels are for
display only. Paths and Git-directory metadata never cross the API. The object
is `null` when Git is unavailable, the working directory is not a resolvable
checkout, or identity resolution fails. `launchable` reflects current tmux
creation setup and root authorization; clients must still handle a stale id or
changed launch status when they submit a creation request.

## Agent context workspace

Agent context endpoints are authenticated like every other REST endpoint. List
responses use `next_cursor`; clients should treat cursors and unknown object
fields as opaque and forward-compatible. Agent, timeline, and message lists
accept opaque `cursor` plus a bounded `limit`; the decision list additionally
accepts `status` and `agent_id` filters.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/agents` | `{agents:[AgentSession],next_cursor}` |
| `GET /api/agents/{id}` | Current `AgentSession` |
| `GET /api/agents/{id}/resume` | Resume object shown below |
| `PUT /api/agents/{id}/visit` | Advance the shared baseline with `{"snapshot_id":"..."}` |
| `GET /api/agents/{id}/timeline` | `{events:[TimelineEvent],next_cursor}`; events include their context |
| `GET /api/timeline` | `{events:[TimelineEvent],next_cursor}` across sessions; event context is `null` |
| `GET /api/agents/{id}/messages` | `{messages:[ChatMessage],next_cursor,retained_from,retained_to,reviewed_at,reviewed_snapshot_id,reviewed_snapshot_sequence,reviewed_snapshot_at,history_truncated,filters}`; optional `q`, `role`, `after`, and `before` filters search retained visible messages |
| `POST /api/agents/{id}/messages` | `202` with `{message:ChatMessage}` after sending visible chat to a confirmed idle session |
| `PUT /api/agents/{id}/binding` | Manually bind an ambiguous session to a candidate pane |
| `DELETE /api/agents/{id}/binding` | Remove a manual binding |
| `DELETE /api/agents/{id}/history` | Delete retained snapshots, messages, decisions, plus visit and Review baselines; returns `{"ok":true}` |
| `GET /api/decisions` | `{decisions:[DecisionItem],next_cursor}`; unverified candidates are excluded |
| `GET /api/decisions/{id}` | One verified decision |
| `POST /api/decisions/{id}/reply` | `202` with `{decision:DecisionItem}` after submitting one still-matching runtime option |

An `AgentSession` contains identity/runtime fields, lifecycle and association,
`binding_revision`, reported capabilities, extraction health, and a canonical
`context`. It also carries the additive [`workspace`](#workspace-identity)
identity when one can be resolved:

~~~json
{
  "session_id": "...",
  "runtime": "codex",
  "goal": "Refactor authentication",
  "current_task": "Run integration tests",
  "progress_summary": "The API layer is complete.",
  "completed_items": [],
  "decisions": [],
  "blockers": [],
  "next_action": "Review refresh-token strategy",
  "progress": {"completed": 3, "total": 4, "percent": 75},
  "estimated_completion": null,
  "lifecycle": "waiting",
  "revision": 12,
  "last_updated": 1784217600.0,
  "provenance": {}
}
~~~

Context is an observed projection, not hidden model reasoning. Clients should
show unknown/empty values honestly and use the per-session `capabilities`
rather than assuming that every Codex or Claude release supports every action.
The currently defined safe-control values are `chat_send: "idle_only"` and
`decision_reply: "verified_terminal"`; `"unavailable"` and
`"open_terminal"` are read-only fallbacks. Association is one of
`confirmed`, `probable`, `ambiguous`, or `unavailable`. Probable and ambiguous
sessions can include public `binding_candidates` with opaque pane ids/targets,
display labels, and numeric confidence; source-log paths are never returned.

Message metadata exposes the current shared Review boundary even after that
agent no longer has a queued Review card. Clients use `reviewed_snapshot_at`
for the Deep Context “since last review” filter, with `reviewed_at` as a
fallback if retention has removed the snapshot. The id and sequence are opaque
continuity metadata and do not acknowledge or mutate anything.

### Resume response

The resume resource has these exact top-level fields:

~~~json
{
  "agent": {"id": "...", "context": {}},
  "changes": {
    "completed": [],
    "new_blockers": [],
    "resolved_blockers": [],
    "decisions_added": [],
    "decisions_resolved": [],
    "goal_changed": {"from": "Old goal", "to": "New goal"}
  },
  "decisions": [],
  "pending_decisions": [],
  "goal": "New goal",
  "current_task": "Run integration tests",
  "progress": {"completed": 3, "total": 4, "percent": 75},
  "blockers": [],
  "next_action": "Review refresh-token strategy",
  "estimated_completion": null,
  "baseline_snapshot_id": "...",
  "as_of_snapshot_id": "...",
  "history_truncated": false
}
~~~

`decisions` and `pending_decisions` currently contain the same pending verified
items; clients should prefer `pending_decisions` for its explicit meaning but
accept either during the v1 transition. Other context fields, including
`progress_summary` and `completed_items`, remain under `agent.context` rather
than being duplicated at the top level. A change object may omit any unchanged
key. `history_truncated` means the saved visit baseline expired before the
oldest retained snapshot, so the delta starts at the oldest available context.

### Review workflow

Review has a separate server-wide baseline. Reading an agent, resume, message,
timeline, or Review resource never advances it. Only an explicit
`PUT /api/agents/{id}/review` with a displayed `snapshot_id` acknowledges work,
and acknowledgements are monotonic. The legacy visit baseline and endpoint
remain available to older clients.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/review` | Settings, due state, counts, ranked structured groups, and privacy-minimized terminal references |
| `PATCH /api/review/settings` | Set `interval_minutes` to `null` or 5–1440, and optionally set `urgent_pane_errors`; returns the updated settings object |
| `PUT /api/review/finish` | Atomically acknowledge 1–10 unique `{"agent_id":"...","snapshot_id":"..."}` targets |
| `PUT /api/agents/{id}/review` | Monotonically acknowledge `{"snapshot_id":"..."}` and return the effective `snapshot_id`, `snapshot_sequence`, `snapshot_at`, `reviewed_at`, and `advanced` state; an advance resets an enabled timer |

`PUT /api/review/finish` accepts `{"targets":[...]}` and returns
`{"requested":2,"advanced":2,"unchanged":0,"processed_at":...,"next_due_at":...}`.
The server validates every target before writing. A missing snapshot or a
snapshot belonging to another agent returns `409` with no baseline advances.
Valid targets advance together in one transaction, and the shared Review timer
is reset once when at least one baseline advances. Replaying the same batch is
a no-op. Clients must derive targets from an immutable displayed manifest;
Watch clients send only their opaque run id to the iPhone companion, never
server targets.

### Apple Watch relay v2

The Watch relay schema is version 2 while the server Review payload remains
version 1. Its public state contracts are `WatchRelayReviewStatus`,
`WatchRelayReviewRun`, and `WatchRelayFinishSummary`. Commands are
`beginReview`, run-bound structured decision replies, `finishReview`, and
`setReviewSchedule`; incompatible schemas and unexpected kind-specific fields
are rejected without downgrade. `finishReview` carries only `reviewRunID`.

The iPhone keeps each exact ordered run manifest in memory for 15 minutes and
clears it on expiry, disconnect, server replacement, capability change, or
phone restart. Finish is available only when every frozen decision has final
delivery and all original Review work was representable on Watch. Delivery
uncertainty stops the sprint and requires an authoritative iPhone refresh;
mutations are never retried automatically. Schedule changes are limited to
Off plus the presets advertised by `GET /api/review`; cached Watch state is
read-only.

`GET /api/review` returns:

~~~json
{
  "version": 1,
  "generated_at": 1784217600.0,
  "settings": {
    "enabled": true,
    "interval_minutes": 30,
    "next_due_at": 1784219400.0,
    "last_digest_at": null,
    "urgent_bypass": {
      "high_critical_decisions": true,
      "pane_errors": false
    },
    "min_interval_minutes": 5,
    "max_interval_minutes": 1440,
    "presets": [30, 60]
  },
  "due": {
    "is_due": false,
    "urgent": false,
    "has_work": true,
    "next_due_at": 1784219400.0
  },
  "counts": {
    "agents_changed": 1,
    "pending_decisions": 1,
    "terminal_requests": 0,
    "total_cards": 1,
    "urgent_items": 0
  },
  "groups": [],
  "terminal_items": []
}
~~~

Each group contains the public agent, current and reviewed snapshot
id/sequence/time fields, `reviewed_at`, `has_changes`, `history_truncated`,
semantic `changes`, unresolved `decisions`, `oldest_pending_decision_at`,
`rank_reason`, and `attention_reasons`. `rank_reason` is one of
`urgent_decision`, `error`, `pending_decision`, `new_blocker`, or `changed`.
Pending decisions have `review_status: "actionable"`; uncertain decisions have
`review_status: "terminal_required"`. Accepted `submitting` decisions are
omitted from Review. An uncertain-only card is ordered with other changes, not
as an actionable pending decision.

Every public `DecisionItem` includes `options_fingerprint`, a SHA-256 digest of
the canonical option array. Plan Review clients persist only the server
instance, decision/option ids, decision and binding revisions, prompt
fingerprint, and options fingerprint. Before each sequential reply they must
refetch and compare every guard and option set. They must never use broadcast
or automatically retry a conflict.

Terminal items deliberately contain only `id`, `pane_id`, `status`, `kind`,
`updated_at`, and `acknowledgeable`. The field is `true` only when `status` is
`done`; it is `false` for blocked, needs-input, and error items. They never
contain pane names, targets, paths, prompts, menus, previews, or terminal
capture. Reading Review never acknowledges an item. When a client directly
opens an acknowledgeable done pane, it matches `pane_id` to the current
`PaneState` and sends that lifecycle revision to
`PUT /api/panes/lifecycle/acknowledge`. Done items then leave Review after the
refreshed pane state arrives. Other terminal items remain until the live pane
state clears through an applicable pane action or terminal-side change.

Batching is off by default. Enabling or changing the interval schedules the
next window at `now + interval`; disabling clears it. At a due window, vmux
atomically advances the persisted timer and sends one generic digest to every
registered device only when work exists. Explicit runtime-provided
high/critical decisions and opted-in pane errors bypass the timer. Priority is
never inferred from decision wording. Acknowledging a displayed snapshot that
advances the shared baseline resets the timer; replaying the same or an older
snapshot does not postpone work.

### Chat control

~~~json
{
  "text": "Summarize what remains.",
  "client_message_id": "client-generated-uuid",
  "expected_binding_revision": 4
}
~~~

The server sends only when the binding is still current and the associated pane
still shows the same confirmed idle prompt. `client_message_id` is idempotent.
Message status is `sent` after tmux accepts input and `observed` after the
runtime records the matching visible message; neither status promises task
completion.

### Decisions and bindings

Decision replies carry all stale guards:

~~~json
{
  "option_id": "rest",
  "custom_text": null,
  "idempotency_key": "client-generated-uuid",
  "expected_revision": 2,
  "expected_binding_revision": 4,
  "prompt_fingerprint": "..."
}
~~~

The server exposes only decisions originating in an explicit structured
runtime question whose text/options also match the current live prompt. It
revalidates the decision revision, pane incarnation, binding revision, prompt
fingerprint, and option mapping immediately before sending input. Terminal
adapters do not support arbitrary custom decision text in v1; use agent chat to
ask for clarification. When present, `recommendation` is a stable option id,
not display text; `allow_custom` is false for the v1 terminal adapters.

Manual binding uses:

~~~json
{"pane_id":"%12","expected_binding_revision":4}
~~~

Unbinding uses
`DELETE /api/agents/{id}/binding?expected_binding_revision=4`. Probable or
ambiguous automatic associations remain read-only until explicitly bound.

Malformed JSON or a body/query that fails the declared request schema returns
`422`. A schema-valid value rejected by agent-domain validation returns `400`.
Missing or intentionally hidden objects return `404`; unavailable safe-control
capabilities and stale revisions, bindings, pane incarnations, or prompts return
`409`, with current state in `detail.current` where available. If Agent Context
is disabled, its REST endpoints return `503` and `/ws/agents` closes with code
`1013` before acceptance; active sockets close cleanly when the workspace is
disabled. Clients should rehydrate instead of automatically replaying a conflicted
mutation.

## Configuration updates

### `PATCH /api/config`

Accepts a partial object from the
[live-editable schema](https://imitation-alpha.github.io/vmux/configuration/#live-editable-schema). Values are
validated, applied immediately, and persisted to `vmux-settings.json`. Bad
values return `400`; persistence failure returns `500`.

The editable usage visibility fields are
`usage_hidden_quota_providers: string[]` and
`usage_hidden_quota_metrics: {provider: string, label: string}[]`. They match
tokscale's normalized names exactly and affect only quota cards and meter rows
on Stats. `GET /api/usage`, warning counts, and alerting remain unfiltered.

`_info`, `version`, and `compatibility` are server-owned and read-only. A
`PATCH` body cannot override them. Older vmux servers may omit `compatibility`;
clients can complete their normal schema handshake and label that connection
as unverified instead of assuming incompatibility.

### `POST /api/star`

This is a focused convenience endpoint that merges one target's star into the
override list and persists the overlay.

## Connected sessions

### `GET /api/sessions`

~~~json
{
  "sessions": [
    {
      "id": "a1b2c3d4",
      "ip": "127.0.0.1",
      "ua": "client user agent",
      "age": 12.3
    }
  ]
}
~~~

`age` is seconds since WebSocket registration.

### `POST /api/sessions/kill`

Send `{"id":"a1b2c3d4"}`. Success is `{"ok":true}`; an unknown session returns
`404`. The selected socket closes with code `4001`.

## Optional usage

| Endpoint | Behavior |
| --- | --- |
| `GET /api/usage` | Quotas and current summary, or `available:false`. |
| `GET /api/usage/history?period=daily&days=30` | `hourly`, `daily`, or `monthly` buckets. |
| `POST /api/usage/refresh` | Body scope is `quota`, `reports`, or `all`. |

An invalid period or scope returns `400`.

## Optional push

| Endpoint | Request body |
| --- | --- |
| `POST /api/push/register` | `{"token":"<apns-hex>","name":"Phone","platform":"ios","contextual":true}` |
| `POST /api/push/unregister` | `{"token":"<apns-hex>"}` |

Registration is accepted even when APNs credentials or optional dependencies
are not ready. See [Push notifications](https://imitation-alpha.github.io/vmux/guides/push-notifications/).

## WebSocket

Connect to `ws://host:port/ws`, adding `?token=...` when configured. For an
HTTPS page use `wss://`.

After acceptance, the server sends:

1. `{"type":"hello","sid":"a1b2c3d4"}`
2. a full `{"type":"state","panes":[...]}` snapshot
3. a new full state snapshot after each polling pass

The client should keep reading snapshots. It may send occasional text frames for
keepalive/disconnect detection; the current server ignores their content.

Clients should treat unknown object fields as forward-compatible, replace their
current state on each snapshot, and reconnect with backoff. Use
`GET /api/state` to distinguish bad auth from a failed WebSocket/network path.

### Agent invalidation WebSocket

Connect independently to `/ws/agents`, with the same token query parameter and
an optional integer `cursor` query parameter. The server sends
`{"type":"hello","cursor":42}`, small invalidations, and a ping after 20
seconds without an invalidation:

~~~json
{
  "type": "agent_event",
  "cursor": 42,
  "event": {
    "kind": "decision_updated",
    "agent_id": "...",
    "decision_id": "...",
    "revision": 3
  }
}
~~~

These are cache invalidations, not authoritative patches. Re-fetch the affected
REST resource. A `reset` frame means the cursor is too old or the subscriber
lagged and the client must rehydrate all agent collections. A failure on this
socket must not mark the separate pane `/ws` connection offline.

Current event kinds are `agent_updated`, `decision_updated`, `message_updated`,
`history_deleted`, `review_updated`, `review_settings_updated`, and
`review_due`. Depending on the kind, `event` may additionally carry a
`decision_id`, `message_id`, `snapshot_id`, or resource hints. Clients must
ignore unknown kinds and fields and refetch Review after any review invalidation.

vmux does not enable cross-origin resource sharing. Browser clients are expected
to use the same origin as the server-hosted PWA; native clients are not subject
to browser CORS enforcement.

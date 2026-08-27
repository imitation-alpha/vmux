# Pane lifecycle

vmux servers attach a `lifecycle` object to every pane state. The kernel is
always enabled, process-memory-only, and resets when the server restarts.
Protocol version 1 is unchanged because the field and endpoints are additive.

States are `blocked`, `error`, `working`, `done`, `idle`, `offline`, and
`unknown`. Every summary includes a stable reason, authority, confidence,
freshness, transition time, revision, and conflict flag. Legacy `status` remains:
blocked projects to `needs_input`; error, working, and offline project directly;
done, idle, and unknown project to `idle`.

## Authority and completion

Evidence resolves in this order: missing process; verified live question/menu;
healthy structured state from a confirmed current-incarnation Agent Context
binding; explicit terminal working/idle UI; bounded terminal error; recent
output/activity grace; and quiet low-confidence idle. Structured evidence is
fresh for 8 seconds, aging through 15 seconds, then excluded. Structured
`waiting` cannot make a pane blocked without a matching visible prompt.

`done` is derived only from working to idle and stays latched until a direct
pane open or successful key, text, select, or broadcast action acknowledges it.
Initial idle never becomes done. Revisions change only on transitions and
acknowledgments.

## API

`GET /api/config` advertises `pane_lifecycle_v1` version 1 and a 32-entry
history limit. Authenticated diagnostics are available at
`GET /api/panes/lifecycle?id=%12&limit=32`.

Acknowledgment uses `PUT /api/panes/lifecycle/acknowledge` with
`{"id":"%12","expected_revision":4}`. A stale revision returns HTTP 409 with
current diagnostics. Acknowledgment is global across authenticated clients.

## Privacy and compatibility

Diagnostics never include transcript or terminal content, prompts, native
session identifiers, commands, cwd/source paths, or log paths. Evidence uses
only enumerated reasons and authorities. Structured evidence is unavailable
while Agent Context is off. Older clients can keep using `status`; newer
clients fall back to it if the capability or lifecycle field is absent.

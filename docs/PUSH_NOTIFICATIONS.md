# Push notifications

vmux includes an optional backend that can send APNs alerts when a pane first
enters `needs_input` and, optionally, `error`. It can also notify compatible
native clients when Agent Context verifies a new human decision. It is not
needed for the PWA or normal pane control.

!!! important "No public native client is available"

    The native iOS companion under `ios/` is in development. It is not on the
    App Store or publicly available. APNs setup therefore remains for client
    implementers who control their own app signing and bundle id.

## Data path

The vmux host talks directly to Apple's APNs service over HTTP/2. There is no
vmux relay. A compatible client registers its device token at
`POST /api/push/register`, and vmux stores it locally in `vmux-push.json`.

Pane alerts use generic copy and omit names, targets, questions, and option
labels. Common confirmation categories carry only the opaque pane id and the
minimal input-key mapping needed by a registered action.

Agent decision notifications always use generic alert copy and opaque routing
identifiers. Decision titles, descriptions, prompts, options, transcript
content, and terminal input keys stay on the vmux server. The legacy
`contextual` device-registration field is accepted and stored for compatibility
but does not change notification content.

Scheduled Review digests are always generic. The payload contains only a
`review_due` event and the opaque server-instance identifier; queue contents
remain on the vmux server until the client fetches `GET /api/review`.

## Install the optional backend

From a source checkout:

~~~bash
uv sync --locked --extra push
uv run vmux --config config.yaml
~~~

After the PyPI release, the published extra will be installable as
`vmux-agent[push]`. Do not use that PyPI command until the release exists.

## APNs requirements

You need:

- an Apple Developer team
- an APNs `.p8` signing key, Key ID, and Team ID
- a compatible app signed by that same team
- an `apns_topic` exactly matching that app's bundle id

APNs credentials are team-scoped. A key from one team cannot push to an app
signed by another.

## Configure

Keep the key outside the repository:

~~~yaml
push:
  apns_key_path: /absolute/path/to/AuthKey_ABC123DEFG.p8
  apns_key_id: ABC123DEFG
  apns_team_id: XYZ1234567
  apns_topic: com.example.vmux-client
  environment: sandbox
  on_error: false
  cooldown: 30
~~~

Use `sandbox` for a development build and `production` for TestFlight or App
Store provisioning. Key paths and identifiers are YAML-only and do not appear
in API responses.

## Client registration

A compatible authenticated client sends:

~~~http
POST /api/push/register
Authorization: Bearer <vmux-token>
Content-Type: application/json

{"token":"<apns-device-token-hex>","name":"My iPhone","platform":"ios","contextual":true}
~~~

It unregisters with:

~~~json
{"token":"<apns-device-token-hex>"}
~~~

at `POST /api/push/unregister`.

`contextual` defaults to true for older-client compatibility and can be changed
by re-registering the same token, but it no longer changes notification copy.
Notifications use pane categories
`vmux.confirm2`, `vmux.confirm3`, and `vmux.generic`, plus
`vmux.agent-decision` for a foreground route to the verified Review item and
`vmux.agent-review` for a scheduled digest route. A client may register matching
notification actions; clients without them still receive ordinary alerts.
Agent decision notifications never contain a terminal input key and never
bypass live server revalidation.

An Agent Context notification uses this routing envelope. Treat every value as
an opaque cache key and fetch the current decision after opening it:

~~~json
{
  "aps": {
    "category": "vmux.agent-decision",
    "thread-id": "vmux-agents"
  },
  "vmux": {
    "type": "decision",
    "server_instance_id": "...",
    "agent_id": "...",
    "decision_id": "...",
    "revision": 3
  }
}
~~~

The client must ignore the route when `server_instance_id` does not match its
active server. Alert text is fixed and generic. The payload never includes
decision titles, descriptions, prompts, source-log paths, working directories,
transcript messages, decision options, commands, tool output, or a terminal
input key.

A scheduled digest uses this privacy-minimized envelope and is sent to every
registered iOS device:

~~~json
{
  "aps": {
    "alert": {"title": "vmux", "body": "Your agent review is ready."},
    "category": "vmux.agent-review",
    "thread-id": "vmux-agents"
  },
  "vmux": {
    "type": "review_due",
    "server_instance_id": "..."
  }
}
~~~

## Alert timing

vmux alerts only on an observed transition into a watched state. A pane already
waiting when vmux starts does not immediately re-alert. `push.cooldown`, 30
seconds by default, suppresses repeats for the same pane.

An agent decision alert is scheduled once when a structured request becomes a
new verified pending decision. The client fetches current state after a tap, so
a resolved or changed prompt cannot be answered from stale notification data.

Review batching is disabled by default. When enabled with
`PATCH /api/review/settings`, normal/low structured decisions and ordinary
`needs_input` transitions are accumulated until the persisted due time. A due
window is claimed atomically so restarts do not duplicate a digest. Empty
windows advance without sending; a completed review resets the next due time.
Explicit runtime-provided high/critical decisions always bypass batching.
Pane errors bypass it only when `urgent_pane_errors` is enabled. vmux never
infers urgency from prompt wording.

## Troubleshoot

Query `GET /api/config` and inspect `_info.push`:

- `configured` means the required `apns_*` fields are present.
- `available` means the optional Python dependencies imported.
- `devices` must be at least one.

Common failures are the wrong APNs environment, a topic that differs from the
bundle id, a device token from another environment, an unreadable key path, or
a service account that cannot access the key.

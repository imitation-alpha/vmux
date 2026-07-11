# Push notifications

vmux includes an optional backend that can send APNs alerts when a pane first
enters `needs_input` and, optionally, `error`. It is not needed for the PWA or
normal pane control.

!!! important "No public native client is available"

    The native iOS companion is a separate project under development. It is not
    on the App Store, is not publicly available, and is not included in this
    repository. The APNs support described here is currently for compatible
    client implementers who control their own app signing and bundle id.

## Data path

The vmux host talks directly to Apple's APNs service over HTTP/2. There is no
vmux relay. A compatible client registers its device token at
`POST /api/push/register`, and vmux stores it locally in `vmux-push.json`.

An alert can include the pane display name, prompt/question, and menu labels.
That content is sent to Apple and can appear on a device lock screen. Do not
enable push for panes containing information that should not traverse APNs or
appear in notifications.

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

{"token":"<apns-device-token-hex>","name":"My iPhone","platform":"ios"}
~~~

It unregisters with:

~~~json
{"token":"<apns-device-token-hex>"}
~~~

at `POST /api/push/unregister`.

Notifications use categories `vmux.confirm2`, `vmux.confirm3`, `vmux.menu`,
and `vmux.generic`. A client may register matching notification actions; clients
without them still receive ordinary alerts.

## Alert timing

vmux alerts only on an observed transition into a watched state. A pane already
waiting when vmux starts does not immediately re-alert. `push.cooldown`, 30
seconds by default, suppresses repeats for the same pane.

## Troubleshoot

Query `GET /api/config` and inspect `_info.push`:

- `configured` means the required `apns_*` fields are present.
- `available` means the optional Python dependencies imported.
- `devices` must be at least one.

Common failures are the wrong APNs environment, a topic that differs from the
bundle id, a device token from another environment, an unreadable key path, or
a service account that cannot access the key.

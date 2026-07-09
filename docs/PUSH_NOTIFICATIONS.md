# Push notifications

vmux can send real APNs push notifications to the native iOS app when a pane
needs your input, so you get alerted even when the app is closed. Push is
optional — the web UI and the app both work fully without it, and still show
in-app alerts while they are open.

## How vmux push works

- The backend talks to Apple's APNs servers **directly** over HTTP/2, signing
  requests with an APNs auth key (`.p8`) you configure. There is no relay, no
  maintainer server, and no third-party service in the path.
- It needs the optional dependencies:

  ```bash
  pip install "vmux-agent[push]"
  # or, if you installed with pipx:
  pipx install "vmux-agent[push]" --force
  ```

- It needs a `push:` section in `config.yaml` (see below). These fields are
  YAML-only by design — key material is never editable from the Settings UI
  and never appears in API responses.
- The app registers its device token via `POST /api/push/register`; tokens are
  stored locally in `vmux-push.json` next to the settings overlay.
- An alert fires when vmux **observes** a pane transition to `needs_input`
  (and optionally `error`). Restarting vmux does not re-alert panes that were
  already waiting, and a per-pane cooldown (default 30 s) prevents repeats.

## Who can use push today

Apple scopes APNs auth keys to the developer team that signs the app. Your
`.p8` key can only push to apps built under **your** Apple team, with a
`push.apns_topic` matching **that app's** bundle id.

- **Official App Store app:** it is signed by the maintainer's team, so a
  self-hosted backend cannot push to it with your own key. Push for the
  store-distributed app is **not currently possible** with a self-hosted
  backend; a solution is being explored — follow the repo's issue tracker.
  Everything else in the app works without push.
- **Self-built app:** if you build the iOS app under your own team and bundle
  id, push works end to end. The walkthrough below is for you.

## Setup for self-built apps

1. **Create an APNs auth key.** In the
   [Apple Developer portal](https://developer.apple.com/account) go to
   *Certificates, Identifiers & Profiles → Keys*, create a key with the
   *Apple Push Notifications service* enabled, download the `.p8` file (one
   chance only), and note the **Key ID** and your **Team ID**.
2. **Build the app under your own identity.** Set your own development team
   and a bundle id you control in the app project, and enable the Push
   Notifications capability for that bundle id.
3. **Install the push extra** on the machine running vmux (commands above).
4. **Configure `config.yaml`:**

   ```yaml
   push:
     apns_key_path: /path/to/AuthKey_ABC123DEFG.p8
     apns_key_id: ABC123DEFG      # 10-char key id
     apns_team_id: XYZ1234567     # 10-char team id
     apns_topic: com.you.vmux     # MUST equal your app's bundle id
     environment: sandbox         # sandbox = Xcode-run builds,
                                  # production = TestFlight/App Store builds
   ```

   Keep the `.p8` outside the repo, and keep `config.yaml` local — it is
   gitignored for a reason.
5. **Register and verify.** Start vmux with the config, enable notifications
   in the app (it registers its device token automatically), then make an
   agent ask a question. Within one poll tick you should get a push with the
   parsed options as notification actions.

## Tuning

- `push.on_error: true` also alerts when a pane enters the `error` state.
- `push.cooldown` (seconds, default 30) is the minimum interval between alerts
  for the same pane.
- With usage tracking enabled, `usage.alert_threshold` sends a push when a
  provider quota drops below the given percentage (see `config.example.yaml`).

## Troubleshooting

**vmux prints `push: configured but deps missing`** — install the extra
(`pip install "vmux-agent[push]"`). Device registrations are still accepted in
the meantime; pushes start once the deps import.

**No alerts, no error anywhere** — check the pieces one by one:
`curl -H "Authorization: Bearer $VMUX_TOKEN" http://127.0.0.1:8787/api/config`
and look at `_info.push`: `configured` means all four `apns_*` fields are set,
`available` means the deps import, `devices` must be at least 1. If `devices`
is 0, re-enable notifications in the app so it re-registers.

**Wrong `environment`** — device tokens are environment-specific. An app run
from Xcode needs `sandbox`; a TestFlight or App Store build needs
`production`. A mismatch is rejected by APNs (or silently dropped), with no
alert delivered.

**`apns_topic` doesn't match the app's bundle id** — APNs rejects the push.
The topic must be the bundle id of the app that receives it, and the key must
belong to the team that signs it.

**Alerts arrive once, then stop** — that's the per-pane cooldown; raise or
lower `push.cooldown` to taste. Also remember only *observed* transitions
alert: a pane already waiting when vmux starts won't re-alert until it changes
state again.

**Running under launchd/systemd** — use an absolute `apns_key_path` and make
sure the service user can read the `.p8`; service managers don't see your
shell's working directory or `~` expansion the way your terminal does.

# Companion app backend contract

This is the backend contract used by the vmux PWA and native companion apps.
Most users should start with [QUICKSTART.md](../QUICKSTART.md); this page is for
client integration, debugging, and release checks.

The backend runs on the machine that owns the tmux panes. It is local-first:
`vmux` binds `127.0.0.1:8787` by default. For a phone, run it on Tailscale or a
trusted LAN with a bearer token:

```bash
VMUX_TOKEN="$(openssl rand -hex 16)"
echo "$VMUX_TOKEN"
vmux --host 0.0.0.0 --token "$VMUX_TOKEN"
```

Do not expose vmux directly to the public internet. The token gates the ability
to send keystrokes into tmux panes.

## Client setup

- Server address: accept `host:port` and full `http://...` or `https://...`
  URLs. Default scheme is `http`.
- REST auth: send `Authorization: Bearer <token>` when a token is configured.
- WebSocket auth: connect to `/ws?token=<token>`.
- Auth failure: REST returns `401` with `{"detail":"bad or missing token"}`;
  `/ws` closes with code `1008` before accepting. Map both to a re-enter-token
  state rather than a generic network error.
- State source: prefer `/ws`; use `GET /api/state` as reconnect or polling
  fallback.
- Token storage: store the token in platform secure storage when available.

Example smoke test:

```bash
curl -H "Authorization: Bearer $VMUX_TOKEN" \
  http://127.0.0.1:8787/api/state
```

## State feed

`GET /api/state` returns:

```json
{
  "panes": [
    {
      "id": "%12",
      "target": "work:1.1",
      "name": "work:api:1",
      "kind": "claude-code",
      "status": "needs_input",
      "title": "",
      "question": "Allow this edit?",
      "menu": [
        {"key": "1", "label": "Yes", "selected": true, "freeform": false}
      ],
      "preview": ["..."],
      "lines": ["..."],
      "updated": 1720000000.0,
      "changed": false,
      "window": "api",
      "starred": false,
      "interacted": 0.0
    }
  ]
}
```

`/ws` sends a first `{"type":"hello","sid":"..."}` frame, then full state
snapshots shaped like `GET /api/state` on each poll. Treat unknown fields as
forward-compatible and preserve the known enum values:

- `status`: `needs_input`, `error`, `working`, `idle`, `offline`
- `kind`: `claude-code`, `generic`, `shell`

## Actions

All action endpoints require the same bearer auth as `/api/state`.

| Endpoint | Body | Purpose |
| --- | --- | --- |
| `POST /api/key` | `{"id":"%12","key":"Enter"}` | Send an allow-listed tmux key. |
| `POST /api/text` | `{"id":"%12","text":"continue","enter":true}` | Send literal text, optionally followed by Enter. |
| `POST /api/select` | `{"id":"%12","key":"1"}` | Choose a parsed menu option. |
| `POST /api/broadcast` | `{"ids":["%12"],"text":"run tests","enter":true}` | Send one message to many panes. |
| `POST /api/star` | `{"target":"work:1.1","starred":true}` | Persist a starred pane override. |

Successful simple actions return `{"ok":true}`. Broadcast returns
`{"ok":true,"sent":1,"errors":[]}`. A stale pane id returns `404`.

## Settings and sessions

- `GET /api/config` returns live-editable settings plus read-only `_info`.
- `PATCH /api/config` accepts a partial settings object and persists it to
  `vmux-settings.json`; it never rewrites `config.yaml`.
- `_info.allowed_keys` is the key allowlist clients should use for shortcut
  pickers.
- `GET /api/sessions` lists connected browser/app sessions.
- `POST /api/sessions/kill` with `{"id":"..."}` disconnects a session.

YAML-only fields stay server-local: bearer token, APNs key paths,
`usage.command`, and smart-naming AI backend settings.

## Optional push

Push is not required for first run. To enable APNs support on the backend:

```bash
pip install "vmux-agent[push]"
```

Configure the `push:` section in `config.yaml`, then let the iOS app register
its device token:

| Endpoint | Body | Purpose |
| --- | --- | --- |
| `POST /api/push/register` | `{"token":"<apns-hex>","name":"iPhone","platform":"ios"}` | Register or refresh a device token. |
| `POST /api/push/unregister` | `{"token":"<apns-hex>"}` | Remove a device token. |

The server stores device tokens in `vmux-push.json` next to the settings
overlay, exposes only counts/truncated labels in config info, and sends alerts
directly to APNs.

Each alert carries an APNs `category` so native clients can register matching
notification actions (clients without registered categories still receive
plain alerts):

| Category | Menu shape |
| --- | --- |
| `vmux.confirm2` | Two options: Yes / No. |
| `vmux.confirm3` | Three options: Yes / … / No (Claude Code confirm dialogs). |
| `vmux.menu` | Any other parsed menu; the option legend is embedded in the body. |
| `vmux.generic` | No parsed menu. |

`push.apns_topic` must equal the bundle id of the app that receives the
alerts, and the signing key must belong to the team that signs that app — see
[PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md) for what that means for the
official app versus self-built apps.

## Optional usage

Usage tracking is disabled by default. When `usage.enabled: true` is configured,
the server shells out to the YAML-configured `tokscale` command and serves:

- `GET /api/usage`
- `GET /api/usage/history?period=hourly|daily|monthly&days=30`
- `POST /api/usage/refresh` with `{"scope":"quota"|"reports"|"all"}`

When tokscale is not installed or tracking is disabled, these endpoints return
`available:false` with a reason instead of failing the app.

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

## Configuration

### `GET /api/config`

Returns the live-editable fields plus:

~~~json
{
  "_info": {
    "host": "127.0.0.1",
    "port": 8787,
    "token_set": false,
    "version": "0.1.0",
    "compatibility": {
      "protocol_version": 1,
      "minimum_ios_version": "1.0.0"
    },
    "targets": [],
    "allowed_keys": [],
    "push": {},
    "usage": {}
  }
}
~~~

`version` is the backend software version. `compatibility.protocol_version`
identifies the REST/WebSocket contract, while `minimum_ios_version` is the
oldest iOS marketing version supported by this server. `targets` contains
currently represented tmux targets. Push and usage info report
capability/availability without exposing credentials.

### `PATCH /api/config`

Accepts a partial object from the
[live-editable schema](https://imitation-alpha.github.io/vmux/configuration/#live-editable-schema). Values are
validated, applied immediately, and persisted to `vmux-settings.json`. Bad
values return `400`; persistence failure returns `500`.

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
| `POST /api/push/register` | `{"token":"<apns-hex>","name":"Phone","platform":"ios"}` |
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

vmux does not enable cross-origin resource sharing. Browser clients are expected
to use the same origin as the server-hosted PWA; native clients are not subject
to browser CORS enforcement.

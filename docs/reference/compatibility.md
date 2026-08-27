# Compatibility and versioning

vmux follows Semantic Versioning style with an explicit pre-1.0 policy.

## v0.x policy

- A patch release should preserve documented behavior while fixing bugs,
  packaging, or security issues.
- A minor v0.x release may make a documented breaking change when keeping the old
  behavior would unduly complicate a small project.
- Breaking changes are called out in the changelog and, when practical, include
  a migration path.
- v1.0 will mark a stronger compatibility commitment.

The first published backend and PWA release is v0.1.0.

## Public surface

The compatibility surface consists of:

- documented CLI flags and exit categories
- YAML field names/defaults and overlay precedence
- REST paths, authentication, request/response bodies, and errors
- WebSocket authentication, hello frame, and full state frames
- `PaneState` and `MenuOption` field meanings
- the security invariants in [Architecture](architecture.md)

The HTML structure, CSS classes, browser-local preference representation, and
internal Python modules/functions are implementation details unless explicitly
documented as part of an integration contract.

## Change process

A change to networking, authentication, REST/WebSocket behavior, `PaneState`,
configuration semantics, or any dependency footprint (runtime, development,
documentation, build, or workflow) requires prior maintainer acceptance in an
issue. The pull request must include tests, documentation, a changelog entry,
and client/migration impact.

Feature clients should:

- ignore additive unknown fields
- tolerate an unknown enum with a safe fallback
- read the server version from `GET /api/config` → `_info.version`
- read the compatibility policy from `GET /api/config` →
  `_info.compatibility`
- reconnect and replace state rather than applying assumed incremental patches

The bundled PWA additionally normalizes unknown status/kind values to neutral
presentation and structurally reuses unchanged pane objects between full
snapshots. These are client implementation choices; they do not change the wire
contract.

## Client/server protocol

Current servers advertise this read-only policy in `GET /api/config`:

~~~json
{
  "_info": {
    "version": "0.1.0",
    "compatibility": {
      "protocol_version": 1,
      "minimum_ios_version": "1.0.0"
    }
  }
}
~~~

`protocol_version` changes only when a breaking REST or WebSocket contract
change requires clients to make different assumptions. Additive fields do not
by themselves increment it. `minimum_ios_version` uses the iOS marketing
version and does not include the App Store build number. It is informational to
the web client and is never treated as `minimum_web_version`.

The `codex` pane kind and `MenuOption.description` are additive protocol 1
values. Clients that do not recognize the kind already fall back to an unknown
or generic presentation, and clients may ignore the description while still
submitting the opaque option `key` unchanged.

`POST /api/images` is an additive authenticated REST endpoint in protocol 1.
It does not change `PaneState`, either WebSocket frame family, or any existing
text-action body. Existing clients never call the endpoint and continue to
work unchanged. A client that offers image upload must treat `404` from an
older compatible server as feature unavailability, keep the draft, and leave
ordinary text submission available.

`tmux_create_v1` plus `GET /api/tmux/creation`,
`GET /api/tmux/directories`, and `POST /api/tmux/create` are also additive
protocol 1 surfaces. Updated clients hide creation when the capability is
absent; older clients ignore the unknown capability and continue monitoring and
acting on panes. Adding `grok` to the server-owned runtime allowlist is additive:
the existing `agy` ID still means Antigravity, while clients display the new
entry as Grok Build. That runtime-list addition changed no existing request,
pane, or WebSocket frame.

`pane_lifecycle_v1` and `workspaces_v1` are additive protocol 1 capabilities.
Lifecycle and workspace objects are additive `PaneState` fields, and workspace
identity is also additive on agent resources. Older clients ignore them and
continue using legacy pane status. Updated clients must gate the new semantics
on their respective capability and tolerate a missing or null workspace.
`POST /api/tmux/create` additionally accepts an opaque active `worktree_id` in
place of `cwd`; existing path-based bodies are unchanged.

The bundled web client expects protocol 1 and a compatible server version of
0.1.0 or newer. Missing server version or compatibility metadata is a legacy,
**Unverified** condition: the PWA may continue after `/api/config` and
`/api/state` validate normally. A malformed compatibility object, malformed
known server version, known protocol mismatch, or malformed state payload is
**Incompatible** and blocks actions with update guidance.

The initial compatibility matrix is:

| Client | Backend | Protocol | Result |
| --- | --- | --- | --- |
| iOS 1.0.0 build 26 or newer compatible build | 0.1.0 or newer compatible release | 1 | Verified |
| Any existing client | Server without compatibility metadata | Unknown | Complete the normal handshake; report Unverified |
| Bundled 0.1.0 web client | 0.1.0 or newer compatible release | 1 | Verified |
| Bundled web client | Server without version/compatibility metadata | Unknown | Validate config/state; continue as Unverified if both are sound |
| Bundled web client | Malformed metadata or known mismatch | Mismatched/invalid | Block actions as Incompatible |

Clients must block a known protocol mismatch. Native clients must also enforce
app-specific minimum-version policy; the PWA does not apply the iOS minimum.
Missing metadata is a legacy condition, not proof of a mismatch. The full wire
shape is documented in the [client API](client-api.md).

## Supported versions

Security fixes support the latest release only and land on `main` for the next
release. No long-term-support branch is promised.

The supported Python range is 3.10–3.14. macOS is the daily-use platform; Linux
is expected but needs broader verification, and WSL is not yet verified.

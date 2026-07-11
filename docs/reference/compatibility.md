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

The current tree is the v0.1.0 release candidate; no v0.1.0 PyPI release, tag,
or GitHub Release exists yet.

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
- reconnect and replace state rather than applying assumed incremental patches

## Supported versions

Security fixes support the latest release only. Before the first release,
security fixes land on `main`. No long-term-support branch is promised.

The supported Python range is 3.10–3.14. macOS is the daily-use platform; Linux
is expected but needs broader verification, and WSL is not yet verified.

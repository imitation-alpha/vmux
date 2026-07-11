# Governance

vmux is currently a small, solo-maintained open-source project.

## Maintainer

[`@imitation-alpha`](https://github.com/imitation-alpha) is the initial
maintainer and final decision maker for project scope, architecture, releases,
repository access, and security response. The maintainer may delegate triage or
specific decisions while retaining responsibility for the project.

## Decision making

Design and scope decisions are discussed in GitHub Issues. The project seeks
useful consensus and considers concrete user evidence, but decisions ultimately
prioritize:

1. A simple, local-first system with no hosted control plane.
2. A small and understandable security boundary.
3. Compatibility for CLI, configuration, and companion clients.
4. Maintainability and the available maintainer capacity.

A feature is ready for implementation only after the maintainer applies the
`accepted` label to a scoped issue. Acceptance records agreement on direction;
it does not guarantee a schedule, merge, or release. The maintainer may revise
or withdraw acceptance if implementation reveals a security, compatibility, or
maintenance cost that was not understood during discussion.

## Contributor and maintainer roles

Anyone may report bugs, improve objective documentation, review changes, or
submit contributions under [CONTRIBUTING.md](CONTRIBUTING.md). Labels and issue
comments are used to make actionable work and acceptance criteria visible.

Contributors who demonstrate sustained, constructive participation and sound
judgment may be invited to help with issue triage. Triage contributors who build
a record of careful reviews, compatible design decisions, and reliable project
stewardship may be invited to become maintainers. Access is granted by the
maintainer based on project need; there is no contribution-count threshold.

## Releases and compatibility

The maintainer controls releases and signing/publishing credentials.
`pyproject.toml` is the version source. vmux follows semantic-versioning-style
versions; while the project is `0.x`, minor releases may make documented
breaking changes. Security fixes support only the latest release.

The public compatibility surface comprises CLI behavior, YAML and overlay
semantics, the REST/WebSocket companion-client contract, `PaneState`, and the
documented security invariants. Material changes to it require an accepted issue
and release notes.

## Security and emergency changes

Vulnerability handling occurs privately under [SECURITY.md](SECURITY.md). The
maintainer may bypass the normal pull-request path only for an urgent security
fix or repository/release-infrastructure incident. Any bypass must be limited to
the emergency and documented publicly after disclosure is safe.

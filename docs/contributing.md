# Contributing

The authoritative contributor policy is
[`CONTRIBUTING.md`](https://github.com/imitation-alpha/vmux/blob/main/CONTRIBUTING.md).
This page gives the route into it.

## Choose the right route

- Bug fixes and objective documentation corrections may be submitted directly.
- Significant features require a maintainer-accepted issue before implementation.
- A small detector addition may proceed from a focused bug/support issue with a
  realistic redacted fixture.
- Networking, authentication, wire-contract, security-invariant, or
  dependency-expanding changes always need prior maintainer acceptance.

An accepted feature becomes a scoped issue before code begins. WebRTC, PeerJS,
hosted relays, automatic port forwarding, and an unauthenticated/public
transport are outside the current project boundary.

## Set up

~~~bash
git clone https://github.com/imitation-alpha/vmux.git
cd vmux
uv sync --locked --group dev --group docs

uv run pytest -q
uv run ruff check vmux tests
uv run mkdocs build --strict
uv build
~~~

## Pull-request evidence

Keep the change focused and complete before requesting review. Include:

- the problem and linked accepted issue when the change is a feature
- tests that fail before and pass after
- exact commands and results
- before/after screenshots or video for UI changes, verified on desktop and
  mobile widths
- documentation and changelog impact
- security analysis for tmux, subprocess, regex, network, token, or auth changes
- an AI-assistance disclosure when AI materially helped, plus the human
  verification performed

Draft pull requests can make implementation progress visible; feature design
still belongs in the issue. Maintainer review is best-effort and has no response
time SLA.

## Contributor responsibility

AI-assisted code is welcome, but the human contributor owns every submitted
line, must understand and explain it, and remains responsible for licensing,
security, tests, and correctness. Read the canonical
[AI policy](https://github.com/imitation-alpha/vmux/blob/main/AI_POLICY.md) and
[Code of Conduct](https://github.com/imitation-alpha/vmux/blob/main/CODE_OF_CONDUCT.md).

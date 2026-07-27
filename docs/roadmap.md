# Roadmap and changelog

vmux is preparing v0.1.0. There is no published PyPI package, v0.1.0 tag, or
GitHub Release yet.

## v0.1.0 launch gate

The first release is gated on:

- truthful source/PyPI and platform messaging
- reproducible Python 3.10–3.14 development and package checks
- complete strict-built documentation and GitHub Pages deployment
- focused contributor, support, governance, AI, and security policies
- hardened CI, dependency review, and security scanning
- package-content and clean-install smoke tests
- a verified PyPI Trusted Publishing path

The [Unreleased changelog](https://github.com/imitation-alpha/vmux/blob/main/CHANGELOG.md#unreleased)
remains the source of truth until the release commit assigns a date.

## Candidate follow-up work

The following are directions, not promises or contributor-ready architecture
projects:

- broader Linux and WSL verification
- fixture-backed detector improvements for Codex, Gemini CLI, and other agents
- documentation and troubleshooting gaps found by first users
- small UI accessibility, mobile, and regression-test improvements
- better local ranking of which agent needs attention most
- additional structured runtime observers after stable, documented log formats
- cross-session search and carefully scoped cross-agent dependency views

Work becomes contributor-ready only after an accepted issue defines the problem,
scope, and acceptance criteria.

## Out of scope

vmux is local-first plumbing between tmux and user-controlled clients. The
roadmap does not include WebRTC/PeerJS, a signaling service, hosted relays, a
cloud control plane, accounts, telemetry, or automatic public exposure.

The native iOS companion under `ios/` remains non-public and is not a v0.1.0
release deliverable.

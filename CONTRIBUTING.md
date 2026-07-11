# Contributing to vmux

Thanks for helping improve vmux. The project is intentionally small: it routes
attention between tmux and a phone, rather than becoming a hosted agent
platform. Contributions should preserve that focus, keep the security boundary
understandable, and avoid unnecessary dependencies.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
For usage help, see [SUPPORT.md](SUPPORT.md). Report vulnerabilities through the
private process in [SECURITY.md](SECURITY.md), never in an issue or pull request.

## Choose the right contribution route

You may open a pull request directly for:

- A reproducible bug fix.
- An objective documentation correction.
- A small, well-scoped agent-detector improvement tied to a bug or support
  issue, with realistic fixture coverage.

Open a feature-proposal issue and wait for the maintainer to mark it
`accepted` before implementing:

- New user-facing behavior or a significant change to existing behavior.
- Networking, authentication, authorization, or token handling changes.
- Changes to CLI behavior, YAML/overlay semantics, REST or WebSocket contracts,
  or the `PaneState` wire format.
- Any new dependency or expansion of an existing dependency, including
  runtime, development, documentation, build, and workflow dependencies.

Acceptance means the problem and a workable scope have prior agreement. It is
not a promise that the feature will ship or a reservation of the work. Feature
design belongs in the issue; a draft pull request is welcome once the design is
accepted and implementation has started.

Issues labeled `good first issue` have a precise solution and acceptance
criteria. Issues labeled `help wanted` are accepted work for which maintainer
guidance is available. Please comment before starting so concurrent work is
visible.

## Development setup

vmux supports Python 3.10 through 3.14. You need
[`uv`](https://docs.astral.sh/uv/) for the reproducible contributor environment.
tmux is required to run vmux manually, but the automated test suite does not
require a live tmux server.

```bash
git clone https://github.com/imitation-alpha/vmux
cd vmux
uv sync --locked --group dev --group docs

uv run python -m vmux
```

The local interface is then available at `http://127.0.0.1:8787` by default.
Never commit a real token or local configuration; `config.yaml`, settings
overlays, device registries, keys, and logs may contain secrets.

## Checks

Run the checks relevant to your change before requesting review:

```bash
uv lock --check
uv run pytest -q
uv run ruff check vmux tests
uv run mkdocs build --strict
uv build
```

Behavior changes need tests. Detector changes should use realistic, redacted
pane-output fixtures and assert both status and parsed menu data. Documentation
changes must keep internal links and examples valid under the strict MkDocs
build.

For web-interface changes, include reproducible before-and-after evidence at
the affected desktop and mobile widths. Screenshots or a short recording should
show light and dark themes when the change affects colors. Verify keyboard use,
focus visibility, readable contrast, and meaningful accessible labels. In
`vmux/web/index.html`, React `style` props must remain objects, not strings.

## Compatibility and safety

The public compatibility surface includes:

- CLI flags, output relied on by scripts, and exit behavior.
- YAML configuration fields, defaults, validation, and overlay behavior.
- The REST and WebSocket companion-client contract.
- The `PaneState` wire format.
- The security invariants in [the architecture reference](docs/ARCHITECTURE.md).

Changes to that surface require an accepted issue and documentation. In
particular, preserve these invariants:

- tmux commands use argument lists rather than a shell; named keys are
  allow-listed, pane IDs are validated, and literal text uses `send-keys -l --`.
- REST and WebSocket authentication use constant-time token comparison, and
  tokens never appear in API responses.
- User-provided regular expressions always run with a timeout.
- The settings overlay and push registry never rewrite `config.yaml`.
- Bare vmux HTTP is never presented as safe for public-internet exposure.

WebRTC, PeerJS, hosted relays, automatic port forwarding, an unauthenticated
public listener, telemetry, and an account/control-plane service are outside the
project's current scope.

## Project layout

```text
vmux/
  __main__.py    CLI parsing, validation, and server startup
  config.py      YAML loading, validation, and settings overlay
  tmux.py        safe tmux subprocess wrappers
  detectors.py   pure pane-text detection and menu parsing
  models.py      PaneState wire contract
  poller.py      polling loop, snapshots, and WebSocket hub
  server.py      FastAPI REST/WebSocket app and static UI
  web/           PWA, service worker, and vendored browser assets
  push.py        optional APNs support
  usage.py       optional tokscale integration
tests/           pytest suite
docs/            documentation source
```

## Pull requests and review

Keep each pull request focused and complete enough to review. Explain the
problem, the chosen scope, any compatibility or security effects, and the exact
commands and manual checks you ran. Link the accepted issue for a feature. Do
not mix drive-by refactors with a functional change.

Maintainer review is best effort and has no response-time SLA. The maintainer
may ask for tests, documentation, visual evidence, a smaller scope, or an
alternative implementation. The human contributor who opens the pull request
should respond to review and be able to explain the implementation. Draft pull
requests are fine for implementation visibility, but review should be requested
only when the described work and verification are complete.

AI-assisted contributions are welcome under [AI_POLICY.md](AI_POLICY.md). If AI
materially assisted, disclose that in the pull request and summarize your human
review and verification; transcripts are not required.

## Versioning and releases

vmux uses semantic-versioning-style `MAJOR.MINOR.PATCH` versions. During the
`0.x` series, a minor release may contain a clearly documented breaking change;
patch releases should not intentionally break the public compatibility surface.
Security fixes support the latest release only.

Releases are maintainer-only. `pyproject.toml` is the version source; the tag,
package metadata, changelog section, and release artifacts must agree.

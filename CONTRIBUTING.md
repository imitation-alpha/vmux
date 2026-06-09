# Contributing to vmux

Thanks for considering a contribution! vmux is intentionally small — "plumbing between tmux and a phone, not a platform" — so the bar is: keep it simple, keep it secure, keep it working.

## Dev setup

You'll need **tmux** and **Python 3.9+**. [`uv`](https://docs.astral.sh/uv/) is recommended.

```bash
git clone https://github.com/imitation-alpha/vmux
cd vmux
uv sync --extra dev          # creates .venv with runtime + dev deps

uv run python -m vmux        # run it (auto-discovers your tmux panes) → http://127.0.0.1:8787
uv run pytest -q             # run the tests (pure Python, no live tmux needed)
uv run ruff check vmux tests # lint
```

No `uv`? `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"`.

## Project layout

```
vmux/
  __main__.py    CLI entrypoint (argparse, bind, runs uvicorn)
  config.py      config load + the live-editable settings (apply_patch, overlay persistence)
  tmux.py        safe tmux subprocess wrappers (capture-pane, send-keys, key allowlist)
  detectors.py   pure functions: pane text -> status + parsed menu  ← most contributions land here
  models.py      PaneState — the JSON contract shared with the UI
  poller.py      the async poll loop + WebSocket hub + session tracking
  server.py      FastAPI app: REST + WebSocket + static UI
  web/           single-file React+htm PWA (index.html), vendored libs, service worker
tests/           pytest (detectors, config, naming, sessions) — all pure, run in CI
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data flow.

## Adding support for a new agent

Detection is pure and unit-testable. To improve how an agent's dialogs are parsed:

1. Add or refine logic in `vmux/detectors.py` (`detect()`, `parse_claude_menu()`, or the generic regex path). Keep functions pure — input is pane text, output is a `DetectResult`.
2. Add a fixture-based test in `tests/test_detectors.py` (paste realistic captured pane text; assert the status + parsed menu).
3. For a brand-new "kind", thread it through `classify_kind()` and `models.py`.

User-supplied regexes run through the `regex` module with a timeout, so they can't hang the poll loop — keep that invariant if you touch matching.

## Conventions

- Small and stdlib-first. Avoid adding dependencies unless they clearly earn it.
- In `web/index.html`, **`style` props must be objects**, not strings (React requirement — string styles silently blank the app).
- Run `ruff check` and `pytest` before opening a PR; both run in CI across Python 3.9–3.13.
- **Never commit secrets.** `config.yaml` and `vmux-settings.json` are gitignored — keep your token out of git.

## Pull requests

1. Fork, branch, commit with clear messages.
2. Make sure `uv run pytest -q` and `uv run ruff check vmux tests` pass.
3. Open a PR describing the change and how you tested it. Screenshots help for UI changes.

## Releases (maintainers)

Bump `version` in `pyproject.toml` and `__version__` in `vmux/__init__.py`, update `CHANGELOG.md`, tag `vX.Y.Z`, and push the tag — the release workflow builds and publishes to PyPI (via Trusted Publishing).

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind.

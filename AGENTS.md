# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Run Python tests with `uv run pytest`; documentation validation requires `uv run --group docs mkdocs build --strict`.
- `docs/COMPANION_APP_BACKEND.md` is the canonical client API source included by `docs/reference/client-api.md`; edit the canonical source rather than the include file.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

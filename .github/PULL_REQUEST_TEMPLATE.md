<!--
Thanks for contributing to vmux.

Feature changes require an issue labeled `accepted` before implementation.
Bug fixes and objective documentation corrections may be submitted directly.
Do not include tokens, credentials, private pane contents, or undisclosed
vulnerability details in a pull request.
-->

## Problem and scope

<!-- What problem does this solve, for whom, and what is intentionally out of scope? -->

## Related issue

<!-- Use "Fixes #123" where appropriate. A linked accepted issue is required for features. -->

## Implementation

<!-- Explain the approach, important tradeoffs, and compatibility effects. -->

## Verification

<!-- List exact commands, targeted tests, and manual steps with results. Do not write only "CI". -->

- [ ] `uv lock --check`
- [ ] `uv run pytest -q`
- [ ] `uv run ruff check vmux tests`
- [ ] `uv run mkdocs build --strict` (documentation changes)
- [ ] `uv build` (packaging or dependency changes)

## UI evidence

<!--
For UI changes, attach before/after screenshots or a short recording at affected
desktop and mobile widths. Include light/dark themes when colors change, plus
keyboard/focus/accessibility verification. Write "Not applicable" otherwise.
-->

## Documentation and release impact

<!-- Identify updated docs/changelog, or explain why neither needs an update. -->

- [ ] Tests cover behavior changes, including realistic fixtures for detector changes.
- [ ] Public CLI, configuration, REST/WebSocket, and `PaneState` compatibility is preserved or the accepted breaking change is documented.
- [ ] Documentation and `CHANGELOG.md` are updated when user-visible behavior changes.

## Security review

<!-- Check every item touched by this change; leave unrelated items unchecked. Explain material risk above. -->

- [ ] tmux target validation, key allow-listing, and literal `send-keys` behavior
- [ ] Network listener, reverse proxy, REST, or WebSocket behavior
- [ ] Authentication, token transport, storage, logging, or comparison
- [ ] User-controlled regexes and their execution timeout
- [ ] Subprocess construction and avoidance of shell execution
- [ ] New or changed dependencies and vendored browser assets
- [ ] No real secrets, tokens, private pane contents, or generated local state are committed

## AI-assistance disclosure

<!--
State "No material AI assistance" or briefly describe what AI helped produce
and how you personally reviewed and verified it. Transcripts/model names are not
required. See AI_POLICY.md.
-->

- [ ] I understand every submitted change, personally reviewed and tested it, and can explain it during review.
- [ ] This pull request is focused and complete; if it is a draft, design discussion remains in the accepted issue.

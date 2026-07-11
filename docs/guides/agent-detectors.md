# Agent detectors

Detection is intentionally conservative: a false “needs input” alert is noisy,
and selecting a falsely parsed menu could send the wrong input.

## Kinds

- `claude-code` uses Claude Code markers, title/spinner signals, and its
  selection-box format.
- `generic` covers Codex and other non-shell processes.
- `shell` uses the generic path when shells are included.

Codex is currently classified as generic. Dedicated Codex and Gemini parsers are
not part of v0.1.0.

## Status priority

The detector resolves states in this order:

1. `needs_input`
2. `error`
3. `working`
4. `idle`

Claude Code selection boxes become menu options. Generic panes also recognize
conservative, prompt-led numbered menus and common confirmations such as
`(y/n)` or “Press enter.” For a generic pane, newly changed output is a working
hint; unchanged output settles to idle unless another signal is present.

## Tune generic prompts

Add patterns only for text that strongly means the process is blocked:

~~~yaml
detectors:
  generic_prompt_patterns:
    - "\\(y/n\\)"
    - "\\[Y/n\\]"
    - "Do you want to"
    - "Press enter to"
~~~

Prompt patterns inspect the last six lines. Each line is capped before custom
matching. A match may create Yes/No or Continue buttons for recognized prompt
shapes; otherwise vmux marks the pane `needs_input` and leaves free-text reply
available.

## Tune errors

~~~yaml
detectors:
  error_patterns:
    - "Traceback \\(most recent call last\\)"
    - "^\\s*ERROR\\b"
    - "fatal:"
~~~

Error matching examines a bounded tail of the pane capture. Prefer a specific
marker over a broad word such as `error`, which commonly appears in successful
logs and documentation.

## Regex safeguards

Live-edited pattern lists:

- accept at most 40 entries per list
- cap each pattern at 200 characters
- reject a common nested-quantifier shape
- run each search through the `regex` module with a 50 ms timeout

These controls limit accidental catastrophic backtracking. They do not turn
detectors into a sandbox, and only a client already allowed to reach the
Settings API can edit them.

## Contribute a detector fix

Capture a short, redacted, realistic terminal fixture and define the expected
status, question, and options. Detector functions are pure, so changes should
include regression tests. Small detector fixes may start from a focused bug or
support issue; a new pane kind or wire-format change needs maintainer acceptance
first. See [Contributing](../contributing.md).

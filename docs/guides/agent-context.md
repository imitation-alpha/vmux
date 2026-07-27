# Agent context and Review

Agent Context turns a supported Codex or Claude Code session into a resumable
workspace. It is separate from `PaneState`: terminal capture remains available
as a fallback, while the agent workspace is built from runtime-owned session
logs and a small local SQLite database beside the settings overlay (by default,
`~/.vmux/vmux-agents.sqlite3`).

The feature is enabled by default. A normal local configuration needs no new
fields:

~~~yaml
agents:
  enabled: true
  retention_days: 30
~~~

Advanced installations can point the read-only observers at different runtime
homes:

~~~yaml
agents:
  codex_home: ~/.codex
  claude_home: ~/.claude
~~~

These fields are YAML-only. Restart vmux after changing them.

## What vmux stores

vmux tails supported JSONL session logs without modifying them. It normalizes
only user-visible user/assistant messages, explicit plan or task updates,
explicit structured questions, and lifecycle events. It does **not** copy
hidden reasoning, encrypted reasoning, tool arguments, tool results, raw
terminal scrollback, commands, or arbitrary runtime event payloads into the
agent database.

The normalized database contains current contexts, semantic snapshots,
verified decisions, visible chat messages, and one shared legacy visit
baseline per session. Schema v2 also keeps a separate shared Review baseline
and optional digest schedule. It keeps runtime/session identifiers,
working-directory and source-log metadata locally so observation can be
incremental and sessions can be associated with panes. Public APIs expose the
native session identifier but never return the source path or working-directory
metadata.

By default, historical snapshots, messages, and resolved decisions are retained
for 30 days. Current context and unresolved decisions remain available while
the session is active. Use the session's **Delete history** action or:

~~~http
DELETE /api/agents/{id}/history
~~~

to remove its retained timeline, messages, decisions, and visit baseline. The
Review baseline is removed at the same time. The current session
identity/context remains, and continued observation can create
new history. Disabling the subsystem stops observation but does not silently
delete the existing local database.

## Resume and “what changed”

Each semantic change creates a snapshot. The legacy resume response combines
the current context with deltas newer than the shared visit baseline, including
completed work, new or resolved blockers, changed goals/tasks, and pending
decisions. Updated Review-capable clients use the separate Review baseline and
do not mutate either baseline when an agent is opened. The visit endpoint
remains for older clients, which can still acknowledge a displayed resume
snapshot under the v1 behavior.

If the saved baseline aged out under the retention policy, the response sets
`history_truncated: true` and computes the available delta from the oldest
remaining snapshot. The exact response and mutation schemas are in the
[REST and WebSocket API](../reference/client-api.md#agent-context-workspace).

**Resume** is deliberately non-mutating: it opens and focuses chat but never
sends a message or resumes terminal execution on its own.

## Review sessions

Review combines changes across structured sessions with privacy-minimized
references to unstructured panes that currently need input or report an error.
Opening an agent, its history, or Review itself never acknowledges work.
**Done**, **Next**, or **Mark reviewed** explicitly advances the shared Review
baseline to the snapshot that was displayed. A stale acknowledgement cannot
move the baseline backward or postpone the next scheduled review.

Structured cards are ranked by explicit high/critical decisions, agent or
extraction errors, oldest unresolved decisions, new blockers, then other
semantic changes. Unstructured `needs_input` and error panes stay in a separate
Terminal review section. Its references contain no prompt, name, path, menu,
preview, or captured terminal output, and can only open the current live pane.

Quick Review refetches and submits one decision through the guarded reply
endpoint before advancing. Plan Review may stage identifiers locally, but it
must never persist prompt or conversation text. Before each sequential
submission it refetches the decision and verifies status, decision and binding
revisions, prompt fingerprint, option id, and `options_fingerprint`.

Review batching is disabled by default. An interval from 5 to 1440 minutes
sends one generic digest only when the due queue contains work. Explicit
runtime-provided high/critical decisions and opted-in pane errors bypass the
timer; vmux never guesses urgency from words in a prompt.

## Session association and safe control

Log observation is read-only, but chat and decision replies eventually become
terminal input. vmux therefore reports capabilities per session and refuses to
send unless all relevant checks still hold:

- the runtime log and tmux pane are associated with the same runtime and
  working directory;
- the binding belongs to the current pane incarnation and its revision matches
  the client request;
- chat is sent only at a confirmed idle prompt;
- a decision comes from an explicit structured runtime question and its title,
  options, live terminal menu, and prompt fingerprint still agree; and
- an idempotency key prevents a retry from sending the same input twice.

Ambiguous sessions remain readable. Link one of the matching panes manually to
enable controls, or use **Open terminal**. A prompt change, pane replacement,
stale binding, or unsupported custom decision reply returns a conflict instead
of guessing.

Delivery status is conservative. `sent` means vmux wrote input to tmux;
`observed` means the runtime later wrote the matching visible message to its
log. It does not claim that an agent obeyed or completed a request.

## Runtime compatibility

Codex and Claude Code session files are not a stable cross-vendor protocol.
Observers are version-tolerant, bounded, and fail read-only, but a runtime
release can temporarily reduce extraction quality. The UI shows extraction and
association health; terminal monitoring continues independently if structured
context is unavailable.

Other pane detectors such as Grok, OpenCode, and Antigravity still work in the
terminal workspace, but they do not yet provide structured agent context.

## Notifications

Agent decision notifications always use generic text. Only opaque
agent/decision identifiers, the decision revision, and a server-instance
identifier used for safe routing pass through Apple Push Notification service.
Decision titles, descriptions, prompts, options, transcript content, and
terminal input keys remain on the vmux server. The legacy `contextual`
registration preference is accepted for older-client compatibility but does
not alter notification copy. Notification actions open the verified decision
in vmux and do not bypass the live binding and prompt checks above.

Scheduled Review digests are always generic. They contain only the
`review_due` type and opaque server-instance identifier; clients fetch the
current queue after opening them.

See [Security and privacy](../security.md) and
[Push notifications](push-notifications.md) before enabling remote alerts.

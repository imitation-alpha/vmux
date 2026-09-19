# Snippets and shortcuts

The PWA offers two browser-local ways to reply quickly: saved text snippets and
named shortcut-key buttons.

## Snippets

Defaults are `continue`, `yes`, and `no`. In **Settings → Snippets** you can add,
edit, reorder, or remove them. Selecting a snippet fills the reply composer; you
still choose when to send it.

## Shortcut keys

Shortcut buttons can be relabeled and reordered. For tmux, clients use the
server-provided `_info.allowed_keys` list from `GET /api/config`. For guarded
Herdr panes, the PWA filters shortcuts using each pane's `capabilities.keys`.
The [pane-action API](../reference/client-api.md#pane-actions) owns key and
literal-text rules, including Herdr's control-character restriction.

## Browser-local persistence

Snippets, shortcut layout, theme, sounds, notification preferences, view, and
sort choices live in the browser's `localStorage` under `vmux_prefs`. They are:

- local to that browser profile and vmux origin
- not written to `config.yaml` or the server overlay
- not synchronized between devices
- removed if you clear site data

The bearer token is also stored locally, under `vmux_token`. Anyone with access
to the unlocked browser profile may be able to use it. Do not use vmux from an
untrusted shared browser.

## Broadcast

Broadcast sends the same literal text to multiple selected panes that advertise
broadcast support and presses Enter by default. Review the destination list:
vmux deliberately does not infer whether the same instruction is safe in every
pane. See [Terminal provider](../configuration.md#terminal-provider) for provider
availability.

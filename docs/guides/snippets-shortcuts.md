# Snippets and shortcuts

The PWA offers two browser-local ways to reply quickly: saved text snippets and
named shortcut-key buttons.

## Snippets

Defaults are `continue`, `yes`, and `no`. In **Settings → Snippets** you can add,
edit, reorder, or remove them. Selecting a snippet fills the reply composer; you
still choose when to send it.

## Shortcut keys

Shortcut buttons can be relabeled and reordered. A client should use the
server-provided `_info.allowed_keys` list from `GET /api/config`. The current
allowlist is:

~~~text
Enter Escape Tab BTab Space BSpace
Up Down Left Right Home End PageUp PageDown
C-c C-d C-z C-a C-e C-u C-k C-l C-r C-w C-o C-n C-p
~~~

The backend rejects anything outside this list. Literal text is sent with tmux
`send-keys -l --`, so leading dashes and shell metacharacters remain text rather
than becoming a shell invocation.

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

Broadcast sends the same literal text to multiple selected pane ids and presses
Enter by default. Review the destination list: vmux deliberately does not infer
whether the same instruction is safe in every pane.

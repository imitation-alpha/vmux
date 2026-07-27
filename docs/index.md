<p class="vmux-kicker">Local-first tmux control panel</p>

# Know which coding agent needs you

<p class="vmux-lead">
vmux watches a swarm of CLI coding agents in tmux, brings the panes needing
attention to the top, and turns supported terminal prompts into responses you
can tap from the built-in PWA.
</p>

<span class="vmux-status">Pre-release beta · v0.1.0 in preparation</span>

[Install from source](getting-started.md){ .md-button .md-button--primary }
[Choose remote access](remote-access.md){ .md-button }

<div class="vmux-screenshots">
  <img src="images/panel-list-view.jpg" alt="vmux pane list showing coding agents grouped and color-coded by status">
  <img src="images/panel-session-view.jpg" alt="vmux pane detail showing captured output, a response composer, and shortcut keys">
</div>

<div class="vmux-grid" markdown>
<div class="vmux-card" markdown>
### Route attention

Statuses distinguish panes that need input, are working, encountered an error,
are idle, or went offline. Tree, active, all, and starred views keep larger tmux
sessions navigable.
</div>

<div class="vmux-card" markdown>
### Make replies cheap

Supported dialogs become buttons. You can also send literal text, allow-listed
keys, saved snippets, or one broadcast message to several panes.
</div>

<div class="vmux-card" markdown>
### Resume the reasoning state

Supported Codex and Claude Code sessions expose structured goals, progress,
blockers, verified decisions, visible chat, and “what changed” without making
you reconstruct the session from terminal scrollback.
</div>

<div class="vmux-card" markdown>
### Keep control local

The FastAPI backend, WebSocket, and PWA run on your machine. React and htm are
vendored; there is no hosted vmux account, telemetry service, or runtime CDN.
</div>
</div>

## Start on localhost

vmux requires Python 3.10–3.14 and tmux. The PyPI project is not published yet,
so install the current source:

~~~bash
pipx install git+https://github.com/imitation-alpha/vmux.git
vmux
~~~

Open <http://127.0.0.1:8787>. A working tmux agent appears automatically; use
`vmux --include-shells` if you want ordinary shell panes visible too.

!!! warning "Remote access changes the threat model"

    Anyone who has the bearer token and network reach can send input to your tmux
    panes. Tailscale is the recommended remote route. Direct LAN access requires
    a token; public-internet access additionally requires an HTTPS reverse proxy.
    Never expose vmux's plain-HTTP listener publicly.

## Current client and platform status

The installable PWA is the public client included here. The native companion
under `ios/` is also under development and is not publicly available. vmux is
used daily on macOS; Linux should work but needs more field verification, and
WSL remains unverified.

## Continue

- [Run vmux for the first time](getting-started.md)
- [Choose Tailscale, SSH, LAN, or HTTPS](remote-access.md)
- [Understand configuration and live overlays](configuration.md)
- [Resume agents from structured context](guides/agent-context.md)
- [Integrate a client against the API](reference/client-api.md)
- [Contribute a focused improvement](contributing.md)

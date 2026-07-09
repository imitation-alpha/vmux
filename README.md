# vmux

**An attention router for your CLI coding-agent swarm — drive Claude Code / Codex / Gemini from your phone.**

When you run a swarm of CLI coding agents, the hard part isn't running them — it's knowing *which one needs you right now*, and answering it without diving into a terminal, hunting the pane, and reconstructing context. `vmux` reads your tmux panes, figures out each agent's state (idle / working / **needs input** / error / offline), parses the dialog an agent is blocked on, and ships the menu choices to your phone (or desktop) as tappable buttons. You tap **Yes** from the couch.

It's the missing control panel for [running 10+ coding agents in parallel](https://imitation-alpha.github.io/blog/orchestrating-coding-agents.html) without it collapsing into chaos.

![vmux PWA — grid of agent sessions, color-coded by status](docs/images/panel-list-view.jpg)
![vmux PWA — session detail with menu options and action keys](docs/images/panel-session-view.jpg)

> Like it? A ⭐ genuinely helps — it's the signal that keeps this maintained.

---

## Install

vmux needs **tmux** and **Python 3.9+**.

```bash
# from PyPI
pipx install vmux-agent

# or from GitHub for the latest unreleased backend
pipx install git+https://github.com/imitation-alpha/vmux
```

(`pip install` works too; `pipx`/`uv` just keep it isolated. The package name is
`vmux-agent`; the command and Python import remain `vmux`.)

## Quickstart

```bash
vmux
```

Open **http://127.0.0.1:8787** on the same computer. That's it — vmux auto-discovers every tmux pane, classifies each as `claude-code` / `generic` / `shell`, and shows your agents. No config required. (Idle plain shells are hidden by default; `vmux --include-shells` shows them.)

## Reach it from your phone

vmux binds `127.0.0.1` on purpose. Two safe ways to your phone:

- **[Tailscale](https://tailscale.com) (easiest):**
  ```bash
  VMUX_TOKEN="$(openssl rand -hex 16)"
  echo "$VMUX_TOKEN"
  vmux --host 0.0.0.0 --token "$VMUX_TOKEN"
  ```
  then open `http://<machine-name>.<tailnet>.ts.net:8787/?token=<VMUX_TOKEN>` on your phone. In the companion app, enter `<machine-name>.<tailnet>.ts.net:8787` as the server address and paste the token printed by `echo "$VMUX_TOKEN"`.
- **SSH tunnel:** `ssh -L 8787:localhost:8787 you@box`, then open `http://localhost:8787` on the phone.

`--host 0.0.0.0` with an empty token is a hard error, by design — see [SECURITY.md](SECURITY.md).

## Get the app

- **PWA (zero install):** open the vmux URL in your phone browser and "Add to Home Screen" — it runs full-screen and notifies you when an agent needs you while it's open.
- **Native iOS app:** the same control panel as a native app, plus real APNs push (get pinged even with the app closed) and Keychain token storage. It's on the App Store <!-- TODO(launch): add App Store link/badge --> and closed-source for now; the PWA covers everything else today.

See [QUICKSTART.md](QUICKSTART.md) for the full backend and companion-app onboarding flow, and [docs/PUSH_NOTIFICATIONS.md](docs/PUSH_NOTIFICATIONS.md) for push setup.
Native/PWA client implementers can use [docs/COMPANION_APP_BACKEND.md](docs/COMPANION_APP_BACKEND.md) for the backend contract.

## What it does

- **Triage grid / sidebar** — one card per agent, color-coded and ordered so the ones that need you float to the top: 🔴 needs input · 🟠 error · 🟡 working · 🟢 idle · ⚫ offline. Tree, active, all, and starred views keep large swarms navigable.
- **Dialog parsing, not screen-scraping** — for Claude Code, vmux parses the TUI selection box (`╭ │ ❯`) and turns the choices into native buttons. You tap **Yes / No / Edit** without arrow keys. Other agents fall back to configurable regex (`(y/n)`, "Do you want to…", "Press enter to…").
- **Detail view** — scrollback-backed pane output, extracted links with open/copy actions, the menu, a text box with quick **snippets** (saved phrases you tap to drop into the message), and a **customizable shortcut-key row** (defaults to `Ctrl+C` `Esc` `Tab` `⇧Tab` `↵` `↑` `↓` `^R` `^O` `^E`).
- **Broadcast** — send one message to several agents at once.
- **Settings** — theme (auto/light/dark), Liquid Glass on/off, notifications/sound, **custom shortcut-key buttons** (relabel, reorder, pick the key from the server's allowlist) and **custom snippets**, plus live server config: poll interval, scrollback capture, discovery, per-agent rename/kind/star, detector patterns, stable pane naming (nine `naming_mode` values, `session:window:pane` by default), and optional smart task names (`naming_mode: smart`) inspired by `auto-naming-tmux`.
- **Connected sessions** — see every device connected and disconnect any of them.
- **Optional push + usage stats API** — APNs alerts for panes that need input, and opt-in tokscale quota/usage endpoints plus low-quota push alerts when you enable `usage.enabled`.
- **Native feel** — a platform-adaptive PWA: macOS sidebar split-view on desktop, iOS bottom-sheet on mobile, Apple "Liquid Glass" styling, light/dark.
- **Stays on your network** — localhost by default, bearer token for LAN/Tailscale, no cloud, no account, no telemetry.

## How it works

- **Backend** — FastAPI + WebSocket. Polls each tmux pane (`tmux capture-pane`) ~every 700 ms, captures 200 lines of scrollback by default, runs detectors, broadcasts state diffs. Sends keystrokes back via `tmux send-keys -l` (literal, shell-safe), and disables tmux `automatic-rename` by default so agent windows keep stable names. User-supplied detector regexes run with a hard timeout, so a bad pattern can't wedge the loop.
- **Frontend** — a single HTML file with React + htm (vendored, no CDN, no build step), installable as a PWA.
- **Config** — none needed (auto-discovery). Optional `config.yaml` and a live Settings UI; UI edits persist to an overlay file so your hand-authored config stays intact.

More detail for contributors: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Configuration

```bash
cp config.example.yaml config.yaml
vmux -c config.yaml
```

See `config.example.yaml` for bind host/port, token, poll interval, scrollback
capture, stable pane naming modes, smart pane naming, the tmux automatic-rename
opt-out, discovery, starred panes, push, usage tracking, and detector patterns.
Most runtime settings are editable live from in-app **Settings** and are written
to an overlay, never to your `config.yaml`; token material, APNs key paths, and
the exec'd `usage.command` stay YAML-only. Smart naming's optional AI backend
settings are YAML-only too, because enabling them can send recent pane output to
the configured local CLI or endpoint.

## Status & roadmap

Beta — runs daily on macOS, should work on Linux. Known gaps / next up: Linux/WSL path polish, dedicated Codex/Gemini dialog parsers (today they use the generic regex path), cross-agent piping, and smart triage/ranking (sort by *who needs you most*). Ideas and PRs welcome.

## Contributing

Contributions are very welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup (`uv sync --extra dev`), tests, project layout, and how to add a new agent detector. Please also read the [Code of Conduct](CODE_OF_CONDUCT.md). For security issues, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © imitation-alpha · [@imitation-alpha](https://github.com/imitation-alpha) · [X](https://x.com/imitation_alpha)

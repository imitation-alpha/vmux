# Security Policy

## The model in one line

vmux sends keystrokes into your tmux panes over the network — so **the bearer token is the entire security boundary**. Anyone with the token (and network reach) can drive your agents, i.e. run commands as you. Protect the token; the design protects everything else.

What that means in practice:

- **Localhost by default.** vmux binds `127.0.0.1`. Reach it from other devices over an SSH tunnel or [Tailscale](https://tailscale.com), not the open internet.
- **Fail-fast on footguns.** Binding a non-loopback address (`0.0.0.0`, a LAN IP) with an **empty token** is a hard startup error, not a silent exposure.
- **Constant-time auth.** The token is checked with `hmac.compare_digest` on both REST and WebSocket paths.
- **No third-party CDN.** React/htm are vendored same-origin, so a compromised CDN can't inject script and steal your token from the page.
- **Bounded detectors.** User-supplied detection regexes run with a hard timeout, so a bad pattern can't hang the server (ReDoS).
- **Stays local.** No cloud, no account, no telemetry.

Caveats worth knowing: on a shared/multi-user host, loopback is not a trust boundary (any local user can reach an empty-token instance) — set a token. The token travels in the WebSocket URL's query string, so it can appear in proxy/access logs; prefer SSH/Tailscale where that's your own infra.

## Supported versions

This is a young project; security fixes land on `main` and the latest release. Please run a recent version.

## Reporting a vulnerability

**Please do not open a public issue for security problems.** Report privately via:

- GitHub's **private vulnerability reporting** (the repository's *Security* tab → *Report a vulnerability*), or
- a DM to [@imitation_alpha](https://x.com/imitation_alpha) on X.

Include what you found, how to reproduce it, and the impact. **Never paste a real token** into a report or issue. You'll get an acknowledgement as soon as possible, and credit in the changelog if you'd like it.

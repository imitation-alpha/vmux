# Security and privacy

The short threat model is: **a client that has the token and network reach can
send input to your tmux panes, which can lead to command execution as you.**

The canonical vulnerability-reporting policy is
[`SECURITY.md`](https://github.com/imitation-alpha/vmux/blob/main/SECURITY.md).

## Supported deployments

- Localhost is the default and safest.
- Tailscale plus a bearer token is the recommended remote route.
- SSH local forwarding is supported while the vmux listener stays on loopback.
- Direct trusted-LAN access requires a bearer token.
- Public-internet access requires a bearer token and HTTPS/WSS through a
  correctly configured reverse proxy; the plain vmux port stays private.

WebRTC, PeerJS, signaling/hosted relays, automatic port forwarding,
unauthenticated non-loopback access, and public bare HTTP are unsupported. See
[Remote access](remote-access.md) for exact requirements.

## Protect the token

- Generate a long random value.
- Never commit it or include it in an issue.
- Use a permission-restricted config for a long-running service.
- Rotate it by restarting vmux and updating each client.
- Do not place it in the initial page URL when you can paste it into the prompt.
- Redact `/ws` request targets in reverse-proxy and tracing logs.

REST uses an Authorization header. WebSocket auth necessarily uses
`/ws?token=...`, so the query string is the main logging caveat. The PWA stores
the token in browser `localStorage`; do not use an untrusted shared browser
profile.

Loopback without a token is not a complete boundary on a shared OS host.

## Data handled

The backend captures pane scrollback and serves it to authenticated clients.
That data can include source, prompts, terminal output, URLs, and secrets.

By default vmux has no hosted account, telemetry, analytics, ads, or third-party
runtime CDN. Optional features change data flow:

- AI smart naming can send target metadata and recent pane lines to the
  configured command or endpoint.
- APNs push sends a pane name, prompt, menu labels, and identifiers to Apple.
- Usage tracking runs the configured tokscale executable and exposes its
  normalized results to authenticated clients.

All three are off, unavailable, or unconfigured by default. Review their guides
before enabling them.

## Built-in controls

- non-loopback binds fail with an empty token
- bearer comparisons are constant-time
- named tmux keys are allow-listed
- pane ids are validated
- text is sent with tmux literal mode through argument-list subprocess calls
- custom regex matching has size limits and a hard timeout
- API settings cannot modify token, APNs credential, executable usage, or AI
  backend fields
- the token and key material never appear in API responses
- PWA dependencies are vendored and same-origin

## Report privately

Do not open a public issue. Use GitHub's **Security** tab and **Report a
vulnerability** to submit a private report. Include a redacted reproduction,
impact, affected version/commit, and any proposed mitigation. Never send a live
token or private key.

Security fixes support the latest release only. Before v0.1.0, fixes land on
`main`.

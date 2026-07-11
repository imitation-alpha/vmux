# Security Policy

## Security model

vmux can send keystrokes into tmux panes. Anyone who can reach an instance and
authenticate with its bearer token can drive the connected agents and may be
able to run commands with the privileges of the vmux user. There is no
per-client or per-pane authorization layer: treat the bearer token like a
password with command-execution authority.

The core project has no hosted service, account system, or telemetry. Optional
push notifications communicate with Apple Push Notification service when
enabled; optional usage collection invokes the separately installed tokscale
tool. Review those integrations before enabling them.

## Supported deployment models

vmux serves plain HTTP. A token authenticates a request but does not encrypt it.
Use the narrowest listener and network path that meets your needs.

| Deployment | Support | Requirements |
| --- | --- | --- |
| Local browser on the vmux host | Recommended; safest default | Keep the default `127.0.0.1` bind. On a shared host, loopback is not a trust boundary, so configure a token. |
| SSH local-port forwarding | Supported | Keep vmux bound to loopback and forward the port over SSH. A token is recommended and is required if the forwarded endpoint is shared. |
| Tailscale | Recommended for remote access | Configure a long random token. Prefer binding only to the machine's Tailscale address; if binding all interfaces, use host firewall rules to prevent unintended LAN or public reachability. |
| Direct private-LAN address | Supported with caution | A non-empty bearer token is required. Use only on a trusted private network; plain HTTP can expose the token and session contents to network observers. |
| Public address through a reverse proxy | Supported with caution | A non-empty bearer token and correctly configured HTTPS termination are required. Keep the vmux HTTP listener private or on loopback, restrict proxy access, and do not permit an HTTP downgrade. |
| Bare vmux HTTP on the public internet | Unsupported | Do not expose it. The bearer token and pane traffic would travel without transport encryption. |

WebRTC, PeerJS, hosted relays, automatic port forwarding, and unauthenticated
public access are not supported network paths.

vmux refuses to bind a non-loopback address with an empty token. That check is
a last line of defense, not proof that a deployment is safe. A token does not
replace TLS, firewalling, operating-system isolation, or access controls on the
underlying tmux session.

## Token handling

- Generate a long, random, unique token; do not reuse a password or API key.
- Supply and store it so it does not enter shell history, source control,
  screenshots, support issues, or CI logs.
- Rotate it by restarting vmux with a new value, then update every client.
- Rotate immediately after suspected disclosure. Existing clients using the old
  token will be rejected on their next request.
- Restrict access to reverse-proxy and service logs.

REST clients send the token in an `Authorization: Bearer ...` header. The
WebSocket client contract uses a `token` query parameter, and browser setup may
also use a token-bearing URL. Query strings can be retained in browser history,
terminal history, screenshots, and reverse-proxy/access logs. Configure proxies
to omit or redact query strings, never share a token-bearing URL, and treat any
logged token as compromised.

## Security invariants

The implementation is designed so that:

- Authentication comparisons are constant-time on REST and WebSocket paths.
- A non-loopback bind with an empty token fails at startup.
- tmux subprocesses use argument lists rather than a shell, pane identifiers are
  validated, named keys are allow-listed, and literal text is sent literally.
- User-configured regular expressions have execution timeouts.
- Browser runtime assets are vendored and served from the same origin rather
  than loaded from a third-party CDN.
- Tokens are not returned by API responses or written back through the settings
  overlay.

See [the architecture reference](docs/ARCHITECTURE.md) for the complete public
contract and safety boundaries.

## Supported versions

Security fixes are provided for the latest published release only. Fixes are
developed on `main`, but unreleased commits are not a supported release channel.

| Version | Supported |
| --- | --- |
| Latest published release | Yes |
| Older releases | No |

Upgrade to the latest release before reporting a problem that may already be
fixed.

## Reporting a vulnerability

Do not publish a suspected vulnerability in an issue, pull request, or any
other public channel. Use GitHub's
[private vulnerability reporting](https://github.com/imitation-alpha/vmux/security/advisories/new)
as the primary and only vulnerability-reporting channel.

Include, where possible:

- The affected vmux version and installation source.
- The deployment and operating-system context.
- Reproduction steps or a minimal proof of concept using dummy credentials.
- The likely impact and any known mitigations.
- Whether you want public credit after a fix is available.

Never include a real vmux token, API key, APNs key, pane contents containing
secrets, or other live credentials. The maintainer will acknowledge and assess
reports as capacity allows and will share substantive status changes through
the private advisory. This volunteer project does not promise an acknowledgement
or remediation SLA. Please allow a reasonable opportunity to investigate and
coordinate a fix before public disclosure.

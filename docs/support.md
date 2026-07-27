# vmux Agent Console Support

Get help with the vmux Agent Console iPhone and iPad app and the self-hosted
vmux server.

[Email support](mailto:support@imitationalpha.com){ .md-button .md-button--primary }

You can also write directly to
[support@imitationalpha.com](mailto:support@imitationalpha.com). Support is
provided on a best-effort basis; no response-time or resolution-time SLA is
offered.

## Connection and authentication

- Confirm the vmux server is running and that the address includes `http://` or
  `https://` and the correct port.
- Keep the phone and server on the same trusted network, or use a supported
  private-network or SSH-forwarding setup.
- Confirm the bearer token in the app matches the server token. If it may have
  been exposed, rotate it before doing anything else.
- On iPhone or iPad, check **Settings → Privacy & Security → Local Network** and
  allow vmux to find and connect to the server.
- See [Troubleshooting](troubleshooting.md) for TLS, WebSocket, compatibility,
  and connection recovery guidance.

## Jarvis voice assistant

- Live Jarvis is optional and remains off until you review the OpenAI disclosure,
  explicitly consent, and add your own OpenAI API key.
- Check microphone permission. Speech Recognition permission is requested only
  when the fallback speech path needs it.
- Confirm the API key is active and has available project budget. You can remove
  the key and revoke consent from **Settings → Jarvis** at any time.
- Background listening is a separate opt-in. Pause or Stop Jarvis when you no
  longer want microphone capture or spoken playback.

## Notifications

- Enable notifications in both vmux Settings and the iOS Settings app.
- Confirm the connected server reports push support and has valid APNs
  configuration.
- Reconnect after changing the server token or notification settings so the
  device registration can be refreshed.

## What to include

Tell us the app version and build, vmux server version, iOS version and device,
connection type, what you expected, what happened, and the smallest sequence
that reproduces the problem. Include the exact error text or a screenshot only
when it is safe to do so.

!!! warning "Redact private information"
    Never send API keys, bearer tokens, passwords, private IP addresses or
    hostnames, repository names, confidential terminal content, or unredacted
    logs. Replace sensitive values with clear placeholders.

## Privacy and security

Read the [Privacy Policy](https://github.com/imitation-alpha/vmux/blob/main/PRIVACY.md).
Report suspected vulnerabilities through the
[private security reporting process](https://github.com/imitation-alpha/vmux/security/policy),
not by email or a public issue.

For non-sensitive bugs and feature requests, GitHub Issues is an
[optional public channel](https://github.com/imitation-alpha/vmux/issues/new/choose).

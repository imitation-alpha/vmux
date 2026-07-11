# Remote access

vmux can send text and keystrokes to terminals running as your OS user. Treat
the ability to connect as equivalent to interactive access to those panes.

## Supported boundary

| Route | vmux bind | Bearer token | Transport requirement | Use |
| --- | --- | --- | --- | --- |
| Localhost | `127.0.0.1` | Optional on a single-user host | Local HTTP | Default and safest |
| Tailscale | Tailscale IP or `0.0.0.0` | Required | Tailnet encryption; vmux serves HTTP | Recommended remote route |
| SSH local forward | `127.0.0.1` | Optional; recommended on shared hosts | SSH encryption | Supported without exposing vmux |
| Direct trusted LAN | LAN IP or `0.0.0.0` | Required | vmux serves plain HTTP | Supported with caveats |
| Public internet | `127.0.0.1` behind a proxy | Required | Public side must be HTTPS/WSS | Supported only with correct TLS termination |

Any non-loopback bind with an empty token is a startup error. A non-loopback
startup also warns that vmux serves plain HTTP and must not be exposed directly
to the public internet.

!!! note "Browser secure-context features"

    Pane monitoring and actions work over private HTTP, but browsers commonly
    reserve service workers, install prompts, and system notifications for
    HTTPS or localhost. Use HTTPS when those PWA features are required remotely.

## Localhost

The default command needs no network configuration:

~~~bash
vmux
~~~

Only clients on the same host can reach <http://127.0.0.1:8787>. On a shared or
multi-user host, loopback is not a complete trust boundary: set a token because
another local user may be able to connect.

## Tailscale (recommended)

Install and sign in to Tailscale on the vmux host and the client device. Generate
a token, print it once so you can paste it, and bind a reachable interface:

~~~bash
VMUX_TOKEN="$(openssl rand -hex 32)"
printf '%s\n' "$VMUX_TOKEN"
vmux --host 0.0.0.0 --token "$VMUX_TOKEN"
~~~

`0.0.0.0` also listens on LAN interfaces. To narrow the listener, pass the
host's specific Tailscale IP to `--host` instead.

On the remote device, open:

~~~text
http://<tailscale-hostname-or-ip>:8787
~~~

Paste the token into vmux's token screen. Avoid putting it in the ordinary page
URL: URL query strings can enter browser history and access logs.

Tailscale encrypts the network path, while vmux still serves HTTP at the
application layer. Keep the vmux token even inside a tailnet: it is the
application authorization boundary and protects against other reachable peers.

## SSH local-port forwarding

Leave vmux on its default loopback bind:

~~~bash
vmux
~~~

Run the tunnel **on the device that will open the browser**:

~~~bash
ssh -N -L 8787:127.0.0.1:8787 user@vmux-host
~~~

Then open <http://127.0.0.1:8787> on that same device. A tunnel running on your
laptop does not make the laptop's `localhost` reachable from your phone. Mobile
use therefore requires an SSH client that provides a local forward to the phone's
browser.

The SSH connection encrypts the path and the vmux listener remains private.
Add a vmux token as defense in depth on shared hosts.

## Direct LAN

On a trusted private network:

~~~bash
VMUX_TOKEN="$(openssl rand -hex 32)"
printf '%s\n' "$VMUX_TOKEN"
vmux --host 0.0.0.0 --token "$VMUX_TOKEN"
~~~

Open `http://<lan-ip>:8787` and paste the token. Ensure the host firewall allows
only the intended private network.

This is still plain HTTP at the vmux layer. The token prevents unauthenticated
control but does not itself encrypt traffic. Prefer Tailscale, SSH, or local TLS
if other devices on the LAN are not trusted.

## Public internet through HTTPS

Do not publish port 8787 or bind vmux's plain HTTP listener directly to a public
interface. Instead:

1. Keep vmux bound to `127.0.0.1` and configure a strong token.
2. Put a reverse proxy on the same host or private network.
3. Serve a valid `https://` endpoint and redirect or disable public HTTP.
4. Proxy both normal HTTP requests and WebSocket upgrades to vmux.
5. Keep the upstream vmux port unreachable from the public network.
6. Preserve the `Authorization` header.
7. Suppress or redact query strings for `/ws` in access, error, and tracing logs.

The PWA automatically uses `wss://` when loaded over HTTPS. WebSocket auth uses
`/ws?token=...`, so a reverse proxy that records full request targets can log the
bearer token. The initial PWA URL can also accept `?token=...`, but pasting the
token into the prompt avoids putting it in browser history.

HTTPS protects the transport; the bearer token remains required for
authorization. A generic TLS proxy is not an additional vmux authentication
layer unless you explicitly configure one.

## Rotate a token

Stop vmux, generate a new token, and restart. Authentication is checked on each
REST request and at every WebSocket connection, so the old token stops working
immediately for new requests and reconnects. Update each client separately.

Do not commit tokens, paste real tokens into issues, or store them in shell
history. For long-running services, prefer a permission-restricted config file
over a token literal in a service command line.

## Unsupported network designs

The project intentionally does not support:

- WebRTC or PeerJS transports
- signaling servers or hosted relays
- a hosted vmux control plane or account system
- UPnP, NAT-PMP, or other automatic port forwarding
- unauthenticated non-loopback or public access
- bare vmux HTTP on the public internet

These add transport, signaling, authentication, or infrastructure paths outside
vmux's local-first scope.

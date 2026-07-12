# Getting started

This path starts vmux on the same computer as your tmux server, verifies the PWA
locally, and only then expands network access.

## Requirements

- Python 3.10–3.14
- tmux available on `PATH`
- at least one tmux session
- pipx for an isolated install

macOS is the daily-use platform. Linux is expected to work but still needs
broader verification; WSL is not yet verified.

## Install

The distribution will be named `vmux-agent`, while the command and Python import
remain `vmux`. The PyPI project is not published yet. Until the v0.1.0 release,
install directly from the repository:

~~~bash
pipx install git+https://github.com/imitation-alpha/vmux.git
~~~

To work from a checkout instead:

~~~bash
git clone https://github.com/imitation-alpha/vmux.git
cd vmux
uv sync --locked --group dev --group docs
uv run vmux
~~~

After v0.1.0 is published and independently smoke-tested, the normal install
command will be `pipx install vmux-agent`.

## First run

Create or attach a tmux session and start an agent in a pane:

~~~bash
tmux new-session -s agents
~~~

Leave the session running, then start vmux from another terminal:

~~~bash
vmux
~~~

Open <http://127.0.0.1:8787>.

### Expected result

The PWA should load and show the agent pane. A pane waiting at an ordinary shell
prompt is intentionally hidden by default. To verify discovery with plain
shells:

~~~bash
vmux --include-shells
~~~

The status order is:

1. `needs_input`
2. `error`
3. `working`
4. `idle`
5. `offline`

Open a pane to inspect captured output, send text, select a parsed option, or use
an allow-listed shortcut key.

## Install the PWA

Open the vmux URL in a supported browser, then choose the browser's **Add to Home
Screen** or **Install app** action. The PWA and all of its runtime libraries are
served by vmux itself.

### Tailscale HTTPS for iPhone and iPad

!!! important "Using Tailscale? Use the HTTPS address"

    Remote PWA features such as installation, service workers, and notifications
    require a secure browser context. Do not open vmux through its Tailscale IP or
    a plain `http://` URL. Use [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
    to give vmux a private `https://<machine>.<tailnet>.ts.net` address.

Install Tailscale on the vmux host and the iPhone or iPad, sign both devices in
to the same tailnet, and ensure Tailscale is connected on the mobile device.
Then generate a vmux token and keep the vmux listener on localhost:

~~~bash
VMUX_TOKEN="$(openssl rand -hex 32)"
printf 'vmux token: %s\n' "$VMUX_TOKEN"
vmux --host 127.0.0.1 --port 8787 --token "$VMUX_TOKEN"
~~~

In a second terminal on the vmux host, publish that loopback listener privately
to the tailnet over HTTPS:

~~~bash
tailscale serve --bg http://127.0.0.1:8787
tailscale serve status
~~~

The first command may print a consent URL for enabling MagicDNS and HTTPS
certificates. Follow that URL; tailnet administrator permission may be required.
Tailscale then prints an address similar to:

~~~text
https://my-mac.example-tailnet.ts.net/
~~~

On the iPhone or iPad:

1. Open the exact full `.ts.net` address in Safari. Do not append `:8787` or a
   path such as `/vmux`, and do not substitute a Tailscale IP or short hostname.
2. Paste the vmux token into the access screen. Do not add the token to the URL,
   where it could enter browser history or logs.
3. After vmux connects, choose **Share → Add to Home Screen**.

Use `tailscale serve`, not `tailscale funnel`: Serve remains private to devices
allowed by the tailnet policy, while Funnel publishes the endpoint to the public
internet. Stop the HTTPS route with `tailscale serve off`. See
[Remote access](remote-access.md) for the full security model and other routes.

Browser notifications and sounds are local, in-page attention aids. They depend
on browser permission and the PWA being active; they are not a substitute for
background native push.

The native iOS companion is a separate project under development. It is not
publicly available and its source is not included here.

## Optional configuration

No file is required. To start with the annotated example:

~~~bash
curl -O https://raw.githubusercontent.com/imitation-alpha/vmux/main/config.example.yaml
vmux --config config.example.yaml
~~~

For a checkout, copy the included file instead:

~~~bash
cp config.example.yaml config.yaml
uv run vmux --config config.yaml
~~~

The Settings UI writes a JSON overlay and never rewrites your YAML. Learn the
precedence and which fields are live-editable in [Configuration](configuration.md).

## Next steps

- Keep the default bind and use vmux locally.
- Follow [Remote access](remote-access.md) before opening it from another device.
- Use [Troubleshooting](troubleshooting.md) if the page loads without panes or
  authentication fails.

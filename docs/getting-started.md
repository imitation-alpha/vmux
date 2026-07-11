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

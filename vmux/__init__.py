"""vmux — attention router for a swarm of CLI coding agents running in tmux.

The whole tool is one pipeline:

    route  -> triage which pane deserves your attention   (status detection)
    cheapen -> make the decision a single tap             (menu parsing)
    deliver -> put it on whatever screen you're at        (web UI + notifications)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vmux-agent")
except PackageNotFoundError:
    # Source trees can be imported without installing the project. Release and
    # editable installs always resolve the version from pyproject metadata.
    __version__ = "0+unknown"

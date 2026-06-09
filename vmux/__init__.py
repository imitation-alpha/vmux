"""vmux — attention router for a swarm of CLI coding agents running in tmux.

The whole tool is one pipeline:

    route  -> triage which pane deserves your attention   (status detection)
    cheapen -> make the decision a single tap             (menu parsing)
    deliver -> put it on whatever screen you're at        (web UI + notifications)
"""

__version__ = "0.1.0"

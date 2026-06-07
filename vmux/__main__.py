"""`python -m vmux` / `vmux` entrypoint."""

from __future__ import annotations

import argparse
import sys

from . import __version__, config, tmux
from .server import create_app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="vmux",
        description="Attention router for a swarm of CLI coding agents in tmux.",
    )
    parser.add_argument("-c", "--config", help="path to config.yaml (optional)")
    parser.add_argument("--host", help="override bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="override bind port (default 8787)")
    parser.add_argument("--token", help="override bearer token")
    parser.add_argument("--include-shells", action="store_true",
                        help="also show plain idle shells, not just agents")
    parser.add_argument("--version", action="version", version="vmux " + __version__)
    args = parser.parse_args(argv)

    if not tmux.available():
        print("vmux: tmux is not on PATH. Install tmux and start a session first.",
              file=sys.stderr)
        return 2

    cfg = config.load(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.token is not None:
        cfg.token = args.token
    if args.include_shells:
        cfg.include_shells = True
    cfg.validate()

    if not tmux.list_panes():
        print("vmux: no tmux panes found. Start some agents in tmux, then reload.",
              file=sys.stderr)
        # not fatal — server still starts so the UI can show panes as they appear

    import uvicorn

    app = create_app(cfg)
    scheme_host = cfg.host if cfg.host not in ("0.0.0.0", "::") else "<this-machine>"
    print("vmux %s -> http://%s:%d  (%s)" % (
        __version__, scheme_host, cfg.port,
        "token set" if cfg.token else "no token, localhost only",
    ))
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

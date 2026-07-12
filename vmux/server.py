"""FastAPI app: REST for actions, a websocket for live state, static for the UI.

Endpoints (all action bodies carry the pane `id` so tmux ids like "%12" never
have to be URL-encoded):

    GET  /api/state                      -> {panes:[...]}
    POST /api/key      {id, key}         -> send a named key (Enter, C-c, ...)
    POST /api/text     {id, text, enter} -> send literal text, optional Enter
    POST /api/select   {id, key}         -> tap a parsed menu option
    POST /api/broadcast{ids, text, enter}-> send text to many panes
    GET  /api/config                     -> editable server settings + read-only info
    PATCH /api/config  {partial}         -> update settings live, persist to overlay
    GET  /api/usage                      -> provider quotas + today's usage summary
    GET  /api/usage/history?period=&days=-> hourly | daily | monthly usage buckets
    POST /api/usage/refresh {scope}      -> force a tokscale re-scan, return fresh usage
    WS   /ws[?token=]                    -> push full state every tick
"""

from __future__ import annotations

import hmac
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, tmux
from .compatibility import compatibility_info
from .config import Config, save_overlay
from .poller import Hub
from .usage import PERIODS, UsageCollector

WEB_DIR = Path(__file__).resolve().parent / "web"   # packaged inside vmux/ so it ships in the wheel


class KeyReq(BaseModel):
    id: str
    key: str


class TextReq(BaseModel):
    id: str
    text: str
    enter: bool = False


class SelectReq(BaseModel):
    id: str
    key: str


class BroadcastReq(BaseModel):
    ids: List[str]
    text: str
    enter: bool = True


class KillReq(BaseModel):
    id: str


class StarReq(BaseModel):
    target: str
    starred: bool


class PushRegisterReq(BaseModel):
    token: str
    name: str = ""
    platform: str = "ios"


class PushUnregisterReq(BaseModel):
    token: str


class UsageRefreshReq(BaseModel):
    scope: str = "all"   # quota | reports | all


def create_app(cfg: Config) -> FastAPI:
    hub = Hub(cfg)
    usage = UsageCollector(cfg, push=hub.push)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio
        task = asyncio.create_task(hub.run())
        usage_task = asyncio.create_task(usage.run()) if cfg.usage_enabled else None
        try:
            yield
        finally:
            hub.stop()
            task.cancel()
            if usage_task is not None:
                usage.stop()
                usage_task.cancel()

    app = FastAPI(title="vmux", version=__version__, lifespan=lifespan)
    app.state.hub = hub
    app.state.usage = usage

    def require_auth(authorization: Optional[str] = Header(None)):
        if not cfg.token:
            return
        expected = "Bearer " + cfg.token
        if not (authorization and hmac.compare_digest(authorization, expected)):
            raise HTTPException(status_code=401, detail="bad or missing token")

    def _resolve(pane_id: str) -> str:
        real = hub.resolve_id(pane_id)
        if real is None:
            raise HTTPException(status_code=404, detail="unknown pane")
        return real

    @app.get("/api/state")
    def get_state(_=Depends(require_auth)):
        return hub.snapshot()

    @app.post("/api/key")
    def post_key(req: KeyReq, _=Depends(require_auth)):
        real = _resolve(req.id)
        try:
            tmux.send_key(real, req.key)
        except tmux.TmuxError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        hub.mark_interaction(real)
        hub.kick()
        return {"ok": True}

    @app.post("/api/text")
    def post_text(req: TextReq, _=Depends(require_auth)):
        real = _resolve(req.id)
        try:
            tmux.send_literal(real, req.text, enter=req.enter)
        except tmux.TmuxError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        hub.mark_interaction(real)
        hub.kick()
        return {"ok": True}

    @app.post("/api/select")
    def post_select(req: SelectReq, _=Depends(require_auth)):
        try:
            hub.do_select(req.id, req.key)
        except tmux.TmuxError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        hub.kick()
        return {"ok": True}

    @app.post("/api/broadcast")
    def post_broadcast(req: BroadcastReq, _=Depends(require_auth)):
        sent, errors = 0, []
        for pid in req.ids:
            real = hub.resolve_id(pid)
            if real is None:
                errors.append(pid)
                continue
            try:
                tmux.send_literal(real, req.text, enter=req.enter)
                hub.mark_interaction(real)
                sent += 1
            except tmux.TmuxError as exc:
                errors.append("%s: %s" % (pid, exc))
        hub.kick()
        return {"ok": True, "sent": sent, "errors": errors}

    def _config_payload():
        d = cfg.editable_dict()
        d["_info"] = {
            "host": cfg.host,
            "port": cfg.port,
            "token_set": bool(cfg.token),
            "version": app.version,
            "compatibility": compatibility_info(),
            "targets": [hub.states[pid].target for pid in hub.order if pid in hub.states],
            "allowed_keys": sorted(tmux.ALLOWED_KEYS),
            "push": hub.push.info(),
            "usage": usage.info(),
        }
        return d

    @app.get("/api/config")
    def get_config(_=Depends(require_auth)):
        return _config_payload()

    @app.patch("/api/config")
    def patch_config(payload: dict, _=Depends(require_auth)):
        try:
            cfg.apply_patch(payload)            # validates + recompiles regexes
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            save_overlay(cfg)                  # persist to overlay (config.yaml untouched)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="could not persist settings: %s" % exc)
        hub.kick()                             # apply on the next (immediate) poll
        return _config_payload()

    @app.get("/api/sessions")
    def get_sessions(_=Depends(require_auth)):
        return {"sessions": hub.sessions()}

    @app.post("/api/sessions/kill")
    async def kill_session(req: KillReq, _=Depends(require_auth)):
        if not await hub.kill_client(req.id):
            raise HTTPException(status_code=404, detail="unknown session")
        return {"ok": True}

    @app.post("/api/star")
    def post_star(req: StarReq, _=Depends(require_auth)):
        # merge the star change into this target's override (preserving name/kind),
        # persist to the overlay, apply on the next (immediate) poll
        overrides = [
            {"target": o.target, "name": o.name, "kind": o.kind, "star": o.star}
            for o in cfg.overrides.values() if o.target != req.target
        ]
        cur = cfg.overrides.get(req.target)
        overrides.append({
            "target": req.target,
            "name": cur.name if cur else None,
            "kind": cur.kind if cur else None,
            "star": req.starred,
        })
        try:
            cfg.apply_patch({"overrides": overrides})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        save_overlay(cfg)
        hub.kick()
        return {"ok": True}

    @app.get("/api/usage")
    def get_usage(_=Depends(require_auth)):
        return usage.usage_payload()

    @app.get("/api/usage/history")
    def get_usage_history(period: str = "daily", days: Optional[int] = None,
                          _=Depends(require_auth)):
        if period not in PERIODS:
            raise HTTPException(status_code=400, detail="bad period: %s" % period)
        return usage.history_payload(period, days)

    @app.post("/api/usage/refresh")
    async def post_usage_refresh(req: UsageRefreshReq, _=Depends(require_auth)):
        if req.scope not in ("quota", "reports", "all"):
            raise HTTPException(status_code=400, detail="bad scope: %s" % req.scope)
        await usage.refresh(req.scope)
        return usage.usage_payload()

    @app.post("/api/push/register")
    def push_register(req: PushRegisterReq, _=Depends(require_auth)):
        # Tokens are accepted even while APNs isn't configured yet, so enabling
        # push server-side later doesn't require re-registering every device.
        try:
            fresh = hub.push.registry.add(req.token, name=req.name, platform=req.platform)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        info = hub.push.info()
        info.update({"ok": True, "registered": True, "new": fresh})
        return info

    @app.post("/api/push/unregister")
    def push_unregister(req: PushUnregisterReq, _=Depends(require_auth)):
        return {"ok": True, "removed": hub.push.registry.remove(req.token)}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        token = websocket.query_params.get("token", "") or ""
        if cfg.token and not hmac.compare_digest(token, cfg.token):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        sid = uuid.uuid4().hex[:8]
        ip = websocket.client.host if websocket.client else "?"
        ua = websocket.headers.get("user-agent", "")
        hub.add_client(sid, websocket, ip, ua, time.time())
        try:
            await websocket.send_json({"type": "hello", "sid": sid})
            await websocket.send_json(hub.snapshot())
            while True:
                await websocket.receive_text()  # keepalive / disconnect detection
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            hub.remove_client(sid)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:
        @app.get("/")
        def no_ui():
            return JSONResponse({"error": "web/ not found", "api": "/api/state"})

    return app

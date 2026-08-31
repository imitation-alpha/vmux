"""FastAPI app: REST for actions, a websocket for live state, static for the UI.

Endpoints (all action bodies carry the pane `id` so tmux ids like "%12" never
have to be URL-encoded):

    GET  /api/state                      -> {panes:[...]}
    POST /api/images   <raw image bytes> -> private temporary filesystem path
    POST /api/key      {id, key}         -> send a named key (Enter, C-c, ...)
    POST /api/text     {id, text, enter} -> send literal text, optional Enter
    POST /api/select   {id, key}         -> tap a parsed menu option
    POST /api/broadcast{ids, text, enter}-> send text to many panes
    GET  /api/config                     -> editable server settings + read-only info
    PATCH /api/config  {partial}         -> update settings live, persist to overlay
    GET  /api/usage                      -> provider quotas + today's usage summary
    GET  /api/usage/history?period=&days=-> hourly | daily | monthly usage buckets
    POST /api/usage/refresh {scope}      -> force a tokscale re-scan, return fresh usage
    WS   /ws[?token=]                    -> push state when it changes
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from . import __version__, tmux
from .agents import AgentConflict, AgentNotFound, AgentUnavailable
from .compatibility import compatibility_info
from .config import Config, save_overlay
from .creation import CreationProblem, CreationService
from .images import (
    CLEANUP_INTERVAL_SECONDS,
    ImageStorageUnavailable,
    ImageStore,
    ImageTooLarge,
    UnsupportedImage,
    UploadQuotaExceeded,
)
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
    token: str = Field(min_length=16, max_length=200)
    name: str = Field(default="", max_length=80)
    platform: str = Field(default="ios", max_length=20)
    contextual: bool = True


class PushUnregisterReq(BaseModel):
    token: str


class UsageRefreshReq(BaseModel):
    scope: str = "all"   # quota | reports | all


class AgentVisitReq(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=160)


class ReviewSettingsReq(BaseModel):
    interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    urgent_pane_errors: Optional[bool] = None


class AgentBindingReq(BaseModel):
    pane_id: str = Field(min_length=1, max_length=64)
    expected_binding_revision: int = Field(ge=0)


class AgentMessageReq(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)
    client_message_id: str = Field(min_length=1, max_length=160)
    expected_binding_revision: int = Field(ge=0)


class DecisionReplyReq(BaseModel):
    option_id: str = Field(min_length=1, max_length=160)
    custom_text: Optional[str] = Field(default=None, max_length=8_000)
    idempotency_key: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)
    expected_binding_revision: int = Field(ge=0)
    prompt_fingerprint: str = Field(min_length=1, max_length=160)


class ImageUploadResponse(BaseModel):
    id: str
    path: str
    terminal_text: str
    mime_type: str
    size: int
    expires_at: int


def create_app(cfg: Config, *, image_store: Optional[ImageStore] = None) -> FastAPI:
    hub = Hub(cfg)
    usage = UsageCollector(cfg, push=hub.push)
    images = image_store or ImageStore()
    creation = CreationService(cfg)
    config_transition_lock = asyncio.Lock()

    async def cleanup_images() -> None:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            with contextlib.suppress(ImageStorageUnavailable):
                await images.cleanup_expired()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Upload storage is optional to the monitoring loop. Attempt cleanup at
        # startup, but keep the core server available if the host filesystem is
        # temporarily unwritable; uploads will return a bounded 507 instead.
        with contextlib.suppress(ImageStorageUnavailable):
            await images.cleanup_expired()
        # Capability discovery must not race the initial persisted workspace
        # transition: the first config response is authoritative to the PWA.
        await hub.start_agent_runtime()
        task = asyncio.create_task(hub.run())
        usage_task = asyncio.create_task(usage.run()) if cfg.usage_enabled else None
        image_cleanup_task = asyncio.create_task(cleanup_images())
        try:
            yield
        finally:
            hub.stop()
            await hub.agents.aclose()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if usage_task is not None:
                usage.stop()
                usage_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await usage_task
            image_cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await image_cleanup_task

    app = FastAPI(title="vmux", version=__version__, lifespan=lifespan)
    app.state.hub = hub
    app.state.usage = usage
    app.state.images = images
    app.state.creation = creation

    @app.middleware("http")
    async def prevent_structured_context_caching(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if request.method == "GET" and (
            path == "/api/review"
            or path == "/api/timeline"
            or path == "/api/decisions"
            or path.startswith("/api/agents")
            or path.startswith("/api/decisions/")
        ):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

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

    def _agent_call(fn, *args, **kwargs):
        with hub.agents.api_guard():
            if not hub.agents.runtime_active:
                raise HTTPException(status_code=503, detail="agent context is disabled")
            try:
                return fn(*args, **kwargs)
            except AgentNotFound as exc:
                raise HTTPException(status_code=404, detail="unknown agent resource: %s" % exc)
            except AgentConflict as exc:
                raise HTTPException(status_code=409, detail={"message": str(exc), "current": exc.current})
            except AgentUnavailable as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

    def _creation_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CreationProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    @app.get("/api/state")
    def get_state(_=Depends(require_auth)):
        return hub.snapshot()

    @app.post("/api/images", status_code=201, response_model=ImageUploadResponse)
    async def post_image(request: Request, response: Response, _=Depends(require_auth)):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        raw_length = request.headers.get("content-length")
        try:
            content_length = int(raw_length) if raw_length is not None else None
        except ValueError:
            content_length = None
        error_headers = {"Cache-Control": "no-store, max-age=0"}
        try:
            stored = await images.store(
                request.stream(),
                request.headers.get("content-type"),
                content_length=content_length,
            )
        except ImageTooLarge:
            raise HTTPException(413, "image exceeds the 20 MiB limit", headers=error_headers)
        except UnsupportedImage:
            raise HTTPException(415, "supported images are PNG, JPEG, WebP, and GIF", headers=error_headers)
        except UploadQuotaExceeded:
            raise HTTPException(507, "temporary image upload quota exceeded", headers=error_headers)
        except ImageStorageUnavailable:
            raise HTTPException(507, "temporary image storage is unavailable", headers=error_headers)
        except ClientDisconnect:
            raise HTTPException(400, "image upload was interrupted", headers=error_headers)
        return stored.as_dict()

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
            "server_instance_id": cfg.server_instance_id,
            "capabilities": {
                "agent_context_v1": hub.agents.info(),
                "agent_review_v1": hub.agents.review_info(),
                "tmux_create_v1": creation.capability(),
            },
        }
        return d

    @app.get("/api/config")
    def get_config(_=Depends(require_auth)):
        return _config_payload()

    @app.patch("/api/config")
    async def patch_config(payload: dict, _=Depends(require_auth)):
        async with config_transition_lock:
            previous = cfg.editable_dict()
            previous_enabled = cfg.experimental_agent_workspace_enabled
            try:
                cfg.apply_patch(payload)        # validates + recompiles regexes
            except ValueError as exc:
                cfg.apply_patch(previous)
                raise HTTPException(status_code=400, detail=str(exc))

            next_enabled = cfg.experimental_agent_workspace_enabled
            transitioned = next_enabled != previous_enabled
            try:
                if transitioned:
                    await hub.transition_agent_workspace(next_enabled)
            except Exception as exc:
                cfg.apply_patch(previous)
                # A failed enable may have opened storage before failing; a
                # failed disable must restore the prior running service.
                try:
                    await hub.transition_agent_workspace(previous_enabled)
                except Exception as rollback_exc:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "could not change experimental agent workspace and "
                            "runtime rollback also failed: %s"
                            % type(rollback_exc).__name__
                        ),
                    ) from exc
                action = "enable" if next_enabled else "disable"
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "could not %s experimental agent workspace: %s; "
                        "the previous setting remains active"
                        % (action, str(exc) or type(exc).__name__)
                    ),
                ) from exc

            try:
                # The overlay remains the source of truth across restarts.
                await asyncio.to_thread(save_overlay, cfg)
            except OSError as exc:
                cfg.apply_patch(previous)
                try:
                    if transitioned:
                        await hub.transition_agent_workspace(previous_enabled)
                except Exception as rollback_exc:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "could not persist settings and runtime rollback failed: %s"
                            % type(rollback_exc).__name__
                        ),
                    ) from exc
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "could not persist settings: %s; the previous setting "
                        "and runtime remain active" % exc
                    ),
                ) from exc

            hub.kick()                         # apply on the next immediate poll
            await hub.broadcast_config_changed()
            return _config_payload()

    @app.get("/api/sessions")
    def get_sessions(_=Depends(require_auth)):
        return {"sessions": hub.sessions()}

    @app.get("/api/tmux/creation")
    def get_tmux_creation(_=Depends(require_auth)):
        return creation.info()

    @app.get("/api/tmux/directories")
    def get_tmux_directories(path: Optional[str] = None, _=Depends(require_auth)):
        return _creation_call(creation.browse, path)

    @app.post("/api/tmux/create", status_code=201)
    def post_tmux_create(payload: Any = Body(...), _=Depends(require_auth)):
        result = _creation_call(creation.create, payload)
        hub.mark_created(result["pane_id"])
        hub.kick()
        return result

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

    # -- structured agent workspace -------------------------------------- #
    @app.get("/api/agents")
    def get_agents(cursor: Optional[str] = None, limit: int = Query(50, ge=1, le=100),
                   _=Depends(require_auth)):
        agents, next_cursor = _agent_call(hub.agents.list_agents, cursor, limit)
        return {"agents": agents, "next_cursor": next_cursor}

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str, _=Depends(require_auth)):
        return _agent_call(hub.agents.get_agent, agent_id)

    @app.get("/api/agents/{agent_id}/resume")
    def get_agent_resume(agent_id: str, _=Depends(require_auth)):
        return _agent_call(hub.agents.resume, agent_id)

    @app.get("/api/agents/{agent_id}/recovery")
    def get_agent_recovery(
        agent_id: str,
        message_limit: int = 20,
        timeline_limit: int = 20,
        activity_limit: int = 20,
        message_cursor: Optional[str] = Query(default=None, max_length=1000),
        timeline_cursor: Optional[str] = Query(default=None, max_length=1000),
        activity_cursor: Optional[str] = Query(default=None, max_length=1000),
        _=Depends(require_auth),
    ):
        value = _agent_call(
            hub.agents.recovery,
            agent_id,
            message_limit=message_limit,
            timeline_limit=timeline_limit,
            activity_limit=activity_limit,
            message_cursor=message_cursor,
            timeline_cursor=timeline_cursor,
            activity_cursor=activity_cursor,
        )
        value["server_instance_id"] = cfg.server_instance_id
        return value

    @app.get("/api/review")
    def get_review(_=Depends(require_auth)):
        return _agent_call(hub.review_payload)

    @app.patch("/api/review/settings")
    def patch_review_settings(req: ReviewSettingsReq, _=Depends(require_auth)):
        fields_set = getattr(req, "model_fields_set", None)
        if fields_set is None:  # Pydantic v1 compatibility
            fields_set = req.__fields_set__
        return _agent_call(
            hub.agents.update_review_settings,
            interval_present="interval_minutes" in fields_set,
            interval_minutes=req.interval_minutes,
            urgent_pane_errors=req.urgent_pane_errors,
        )

    @app.put("/api/agents/{agent_id}/visit")
    def put_agent_visit(agent_id: str, req: AgentVisitReq, _=Depends(require_auth)):
        return _agent_call(hub.agents.visit, agent_id, req.snapshot_id)

    @app.put("/api/agents/{agent_id}/review")
    def put_agent_review(agent_id: str, req: AgentVisitReq, _=Depends(require_auth)):
        return _agent_call(
            hub.agents.acknowledge_review, agent_id, req.snapshot_id
        )

    @app.put("/api/agents/{agent_id}/binding")
    def put_agent_binding(agent_id: str, req: AgentBindingReq, _=Depends(require_auth)):
        return _agent_call(
            hub.agents.bind, agent_id, req.pane_id, req.expected_binding_revision
        )

    @app.delete("/api/agents/{agent_id}/binding")
    def delete_agent_binding(agent_id: str, expected_binding_revision: int,
                             _=Depends(require_auth)):
        return _agent_call(hub.agents.unbind, agent_id, expected_binding_revision)

    @app.get("/api/agents/{agent_id}/timeline")
    def get_agent_timeline(agent_id: str, cursor: Optional[str] = None,
                           limit: int = Query(50, ge=1, le=100),
                           _=Depends(require_auth)):
        events, next_cursor = _agent_call(
            hub.agents.list_timeline, agent_id, cursor, limit
        )
        return {"events": events, "next_cursor": next_cursor}

    @app.get("/api/timeline")
    def get_timeline(cursor: Optional[str] = None, limit: int = Query(50, ge=1, le=100),
                     _=Depends(require_auth)):
        events, next_cursor = _agent_call(hub.agents.list_timeline, None, cursor, limit)
        return {"events": events, "next_cursor": next_cursor}

    @app.get("/api/agents/{agent_id}/messages")
    def get_agent_messages(agent_id: str, cursor: Optional[str] = None,
                           limit: int = Query(100, ge=1, le=200),
                           q: Optional[str] = Query(default=None, max_length=200),
                           role: Optional[str] = None,
                           after: Optional[float] = None,
                           before: Optional[float] = None,
                           _=Depends(require_auth)):
        messages, next_cursor, metadata = _agent_call(
            hub.agents.list_messages,
            agent_id,
            cursor,
            limit,
            q=q,
            role=role,
            after=after,
            before=before,
            with_metadata=True,
        )
        return {
            "messages": messages,
            "next_cursor": next_cursor,
            **metadata,
        }

    @app.post("/api/agents/{agent_id}/messages", status_code=202)
    def post_agent_message(agent_id: str, req: AgentMessageReq, _=Depends(require_auth)):
        message = _agent_call(
            hub.agents.send_message, agent_id, req.text, req.client_message_id,
            req.expected_binding_revision,
        )
        return {"message": message}

    @app.get("/api/decisions")
    def get_decisions(cursor: Optional[str] = None, limit: int = Query(50, ge=1, le=100),
                      status: Optional[str] = None, agent_id: Optional[str] = None,
                      _=Depends(require_auth)):
        decisions, next_cursor = _agent_call(
            hub.agents.list_decisions, cursor, limit, status, agent_id
        )
        return {"decisions": decisions, "next_cursor": next_cursor}

    @app.get("/api/decisions/{decision_id}")
    def get_decision(decision_id: str, _=Depends(require_auth)):
        return _agent_call(hub.agents.get_decision, decision_id)

    @app.post("/api/decisions/{decision_id}/reply", status_code=202)
    def post_decision_reply(decision_id: str, req: DecisionReplyReq,
                            _=Depends(require_auth)):
        decision = _agent_call(
            hub.agents.reply_decision, decision_id, req.option_id, req.idempotency_key,
            req.expected_revision, req.expected_binding_revision,
            req.prompt_fingerprint, req.custom_text,
        )
        return {"decision": decision}

    @app.delete("/api/agents/{agent_id}/history")
    def delete_agent_history(agent_id: str, _=Depends(require_auth)):
        _agent_call(hub.agents.delete_history, agent_id)
        return {"ok": True}

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
            fresh = hub.push.registry.add(
                req.token, name=req.name, platform=req.platform, contextual=req.contextual
            )
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
            await hub.send_snapshot(sid)
            while True:
                await websocket.receive_text()  # keepalive / disconnect detection
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            hub.remove_client(sid)

    @app.websocket("/ws/agents")
    async def ws_agents(websocket: WebSocket):
        token = websocket.query_params.get("token", "") or ""
        if cfg.token and not hmac.compare_digest(token, cfg.token):
            await websocket.close(code=1008)
            return
        if not hub.agents.runtime_active:
            await websocket.close(code=1013)
            return
        try:
            after = int(websocket.query_params.get("cursor", ""))
        except (TypeError, ValueError):
            after = None
        await websocket.accept()
        queue = hub.agents.subscribe(after)
        try:
            await websocket.send_json({"type": "hello", "cursor": hub.agents.event_cursor})
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    payload = {"type": "ping", "cursor": hub.agents.event_cursor}
                if payload.get("type") == "workspace_disabled":
                    await websocket.close(code=1000, reason="agent workspace disabled")
                    return
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            hub.agents.unsubscribe(queue)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:
        @app.get("/")
        def no_ui():
            return JSONResponse({"error": "web/ not found", "api": "/api/state"})

    return app

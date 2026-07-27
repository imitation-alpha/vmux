"""Deterministic same-origin FastAPI fixture used by the browser test suite.

The fixture deliberately implements the public vmux wire contract instead of
mocking requests in Playwright.  That exercises the real REST/WebSocket
transport and the packaged static assets together.
"""

from __future__ import annotations

import asyncio
import copy
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse

WEB_ROOT = Path(__file__).resolve().parents[2] / "vmux" / "web"
FIXED_NOW = 1_784_044_800.0  # 2026-07-14T12:00:00Z


def _totals(cost: float, total: int, messages: int) -> dict[str, Any]:
    return {
        "input": int(total * 0.55),
        "output": int(total * 0.25),
        "cache_read": int(total * 0.15),
        "cache_write": int(total * 0.05),
        "reasoning": 0,
        "total": total,
        "cost": cost,
        "messages": messages,
    }


def fixture_panes() -> list[dict[str, Any]]:
    common = {"changed": False, "interacted": FIXED_NOW - 600}
    return [
        {
            **common,
            "id": "%1",
            "target": "launch:0.0",
            "name": "Release captain",
            "kind": "claude-code",
            "status": "needs_input",
            "title": "Claude Code",
            "question": "Ship the reviewed change to production?",
            "menu": [
                {"key": "1", "label": "Ship now (Recommended)", "description": "Deploy the reviewed build using the completed release checks.", "selected": True, "freeform": False},
                {"key": "2", "label": "Run checks again", "description": "Repeat the focused validation before making any production change.", "selected": False, "freeform": False},
                {"key": "3", "label": "Tell Claude what to change", "description": "Stage this option and add release notes before submitting.", "selected": False, "freeform": True},
                {"key": "4", "label": "Cancel", "description": "Leave production unchanged and return to the terminal.", "selected": False, "freeform": False},
            ],
            "preview": ["All release checks passed.", "Waiting for approval."],
            "lines": [
                "$ uv run pytest -q",
                "187 passed in 8.42s",
                "Review: https://example.test/reviews/42",
                "<script>window.fixtureInjected = true</script>",
                "Ship the reviewed change to production?",
            ],
            "updated": FIXED_NOW - 45,
            "window": "release",
            "starred": True,
        },
        {
            **common,
            "id": "%2",
            "target": "launch:0.1",
            "name": "API investigator",
            "kind": "codex",
            "status": "error",
            "title": "Codex",
            "question": None,
            "menu": [],
            "preview": ["AssertionError: expected 200, got 503"],
            "lines": ["Running integration checks…", "AssertionError: expected 200, got 503"],
            "updated": FIXED_NOW - 120,
            "window": "release",
            "starred": False,
        },
        {
            **common,
            "id": "%3",
            "target": "research:2.0",
            "name": "Docs researcher",
            "kind": "gemini",
            "status": "working",
            "title": "Gemini CLI",
            "question": None,
            "menu": [],
            "preview": ["Comparing compatibility notes…"],
            "lines": ["Comparing compatibility notes…", "Drafting browser support table."],
            "updated": FIXED_NOW - 15,
            "window": "docs",
            "starred": False,
        },
        {
            **common,
            "id": "%4",
            "target": "research:2.1",
            "name": "Test watcher",
            "kind": "opencode",
            "status": "idle",
            "title": "OpenCode",
            "question": None,
            "menu": [],
            "preview": ["Ready."],
            "lines": ["Ready for the next task."],
            "updated": FIXED_NOW - 300,
            "window": "docs",
            "starred": False,
        },
        {
            **common,
            "id": "cfg:archived:0.0",
            "target": "archived:0.0",
            "name": "Archived worker",
            "kind": "future-agent",
            "status": "offline",
            "title": "",
            "question": None,
            "menu": [],
            "preview": ["Last seen yesterday."],
            "lines": ["Last seen yesterday."],
            "updated": FIXED_NOW - 86_400,
            "window": "offline",
            "starred": False,
        },
    ]


def fixture_config() -> dict[str, Any]:
    return {
        "poll_interval": 0.7,
        "capture_lines": 200,
        "auto_discover": True,
        "include_shells": True,
        "naming_mode": "smart",
        "overrides": [],
        "generic_prompt_patterns": [r"(?m)^[$>]\\s*$"],
        "error_patterns": [r"(?i)error|traceback"],
        "usage_enabled": True,
        "usage_quota_refresh": 180,
        "usage_report_refresh": 300,
        "usage_alert_threshold": 20,
        "_info": {
            "host": "127.0.0.1",
            "port": 8765,
            "token_set": False,
            "version": "0.1.0",
            "compatibility": {"protocol_version": 1, "minimum_ios_version": "1.0.0"},
            "targets": [pane["target"] for pane in fixture_panes()],
            "allowed_keys": ["C-c", "Down", "Enter", "Escape", "Tab", "Up"],
            "push": {"enabled": False, "registered": 0},
            "usage": {
                "enabled": True,
                "installed": True,
                "resolved_path": "/fixture/tokscale",
                "quota_age": 12.0,
                "reports_age": 18.0,
                "last_error": "",
            },
        },
    }


def fixture_buckets() -> list[dict[str, Any]]:
    rows = []
    for index, (day, cost, total, messages) in enumerate(
        [
            ("2026-07-08", 2.10, 82_000, 19),
            ("2026-07-09", 2.85, 101_000, 25),
            ("2026-07-10", 3.40, 128_000, 31),
            ("2026-07-11", 2.60, 94_000, 22),
            ("2026-07-12", 4.15, 151_000, 38),
            ("2026-07-13", 3.20, 119_000, 29),
            ("2026-07-14", 4.80, 168_000, 42),
        ]
    ):
        alpha_total = int(total * 0.62)
        rows.append(
            {
                "bucket": day,
                "totals": _totals(cost, total, messages),
                "by_client": [
                    {"client": "Claude Code", "cost": round(cost * 0.62, 2), "total": alpha_total, "messages": int(messages * 0.6)},
                    {"client": "Codex", "cost": round(cost * 0.38, 2), "total": total - alpha_total, "messages": messages - int(messages * 0.6)},
                ],
                "by_model": [
                    {"model": "claude-sonnet", "cost": round(cost * 0.62, 2), "total": alpha_total, "messages": int(messages * 0.6)},
                    {"model": "gpt-5", "cost": round(cost * 0.38, 2), "total": total - alpha_total, "messages": messages - int(messages * 0.6)},
                ],
                "clients": ["Claude Code", "Codex"],
                "models": ["claude-sonnet", "gpt-5"],
                "fixture_index": index,
            }
        )
    return rows


def fixture_usage(*, stale: bool = False, empty: bool = False) -> dict[str, Any]:
    if empty:
        return {
            "available": True,
            "reason": None,
            "detail": None,
            "fetched_at": FIXED_NOW,
            "stale": False,
            "quotas": [],
            "today": None,
        }
    today = fixture_buckets()[-1]
    yesterday = fixture_buckets()[-2]
    return {
        "available": True,
        "reason": None,
        "detail": "Newest scan failed; serving the previous snapshot." if stale else None,
        "fetched_at": FIXED_NOW - (600 if stale else 18),
        "stale": stale,
        "quotas": [
            {
                "provider": "Claude",
                "plan": "Team",
                "account": "Fixture account",
                "metrics": [
                    {
                        "label": "Five-hour window",
                        "used_percent": 86,
                        "remaining_percent": 14,
                        "remaining_label": "14% remaining",
                        "resets_at": None,
                        "resets_at_raw": "at 17:00",
                    },
                    {
                        "label": "Weekly window",
                        "used_percent": 42,
                        "remaining_percent": 58,
                        "remaining_label": "58% remaining",
                        "resets_at": None,
                        "resets_at_raw": "Monday",
                    },
                ],
            },
            {
                "provider": "OpenAI",
                "plan": "Plus",
                "account": "Fixture account",
                "metrics": [
                    {
                        "label": "Weekly window",
                        "used_percent": 31,
                        "remaining_percent": 69,
                        "remaining_label": "69% remaining",
                        "resets_at": None,
                        "resets_at_raw": "Friday",
                    }
                ],
            },
        ],
        "today": {
            "date": today["bucket"],
            "totals": today["totals"],
            "yesterday": yesterday["totals"],
            "cost_delta_pct": 50.0,
            "top_clients": today["by_client"],
            "top_models": today["by_model"],
        },
    }


def fixture_agents(scenario: str = "") -> list[dict[str, Any]]:
    agents = [
        {
            "id": "agent-codex",
            "session_id": "native-codex-thread",
            "name": "Authentication refactor",
            "runtime": "codex",
            "runtime_display_name": "Codex",
            "lifecycle": "waiting",
            "goal": "Replace session tokens without breaking active clients",
            "current_task": "Reviewing refresh-token strategy",
            "progress_summary": "API and tests are complete",
            "next_action": "Choose the refresh-token strategy",
            "completed_items": [{"id": "api", "title": "Backend API finished"}, {"id": "tests", "title": "Tests passed"}],
            "blockers": [{"id": "decision", "title": "Refresh-token approval"}],
            "progress": {"completed": 7, "total": 9, "percent": 78, "source": "runtime_plan"},
            "pending_decisions_count": 1,
            "last_updated": FIXED_NOW - 32,
            "association": "confirmed",
            "binding": {"pane_id": "%2", "target": "launch:0.1", "revision": 4, "association": "confirmed", "candidates": []},
            "capabilities": {
                "association": "confirmed",
                "context": "structured",
                "chat_send": "idle_only",
                "decision_reply": "verified_terminal",
                "delivery_ack": "log_observed",
            },
            "extraction_health": "healthy",
            "revision": 12,
        },
        {
            "id": "agent-claude",
            "session_id": "native-claude-session",
            "name": "Frontend polish",
            "runtime": "claude-code",
            "runtime_display_name": "Claude Code",
            "lifecycle": "idle",
            "goal": "Finish the responsive workspace",
            "current_task": "Waiting for a pane association",
            "progress_summary": "Context is readable; controls are locked",
            "next_action": "Link the active Claude pane",
            "completed_items": [],
            "blockers": [],
            "last_updated": FIXED_NOW - 120,
            "association": "ambiguous",
            "binding": {
                "revision": 2,
                "association": "ambiguous",
                "candidates": [
                    {"pane_id": "%1", "name": "Release captain", "confidence": "high"},
                    {"pane_id": "%4", "name": "Test watcher", "confidence": "probable"},
                ],
            },
            "capabilities": {
                "association": "ambiguous",
                "context": "structured",
                "chat_send": "unavailable",
                "decision_reply": "unavailable",
            },
            "extraction_health": "healthy",
            "revision": 5,
        },
    ]
    if scenario == "agent_safety_locked":
        codex = next(row for row in agents if row["id"] == "agent-codex")
        codex["capabilities"] = {
            **codex["capabilities"],
            "chat_send": "open_terminal",
            "decision_reply": "open_terminal",
        }
        claude = next(row for row in agents if row["id"] == "agent-claude")
        claude["binding"] = {**claude["binding"], "candidates": []}
    elif scenario == "agent_pagination":
        agents.append(
            {
                "id": "agent-gemini",
                "session_id": "native-gemini-session",
                "name": "Database migration",
                "runtime": "gemini-cli",
                "runtime_display_name": "Gemini CLI",
                "lifecycle": "working",
                "goal": "Prepare the database migration",
                "current_task": "Checking backward compatibility",
                "progress_summary": "Migration plan is ready",
                "next_action": "Review the rollout window",
                "completed_items": [{"id": "plan", "title": "Migration plan drafted"}],
                "blockers": [],
                "last_updated": FIXED_NOW - 180,
                "association": "confirmed",
                "binding": {
                    "pane_id": "%4",
                    "target": "launch:1.1",
                    "revision": 1,
                    "association": "confirmed",
                    "candidates": [],
                },
                "capabilities": {
                    "association": "confirmed",
                    "context": "structured",
                    "chat_send": "unavailable",
                    "decision_reply": "unavailable",
                },
                "extraction_health": "healthy",
                "revision": 2,
            }
        )
    return agents


def fixture_decisions(scenario: str = "") -> list[dict[str, Any]]:
    decisions = [
        {
            "id": "decision-refresh",
            "agent_id": "agent-codex",
            "title": "Choose refresh-token strategy",
            "description": "Rotating tokens reduce replay risk but require a short migration window.",
            "kind": "request_user_input",
            "priority": "high",
            "status": "pending",
            "created_at": FIXED_NOW - 90,
            "revision": 3,
            "binding_revision": 4,
            "prompt_fingerprint": "fixture-refresh-v3",
            "options_fingerprint": "fixture-refresh-options-v3",
            "recommended_option_id": "rotate",
            "allow_custom": True,
            "options": [
                {"id": "rotate", "label": "Rotate on every use", "description": "Stronger replay protection."},
                {"id": "stable", "label": "Keep stable tokens", "description": "Simpler compatibility path."},
            ],
        }
    ]
    if scenario in {"agent_review", "agent_review_plan"}:
        decisions.append(
            {
                "id": "decision-rollout",
                "agent_id": "agent-codex",
                "title": "Choose rollout window",
                "description": "Choose when the reviewed token migration should begin.",
                "kind": "request_user_input",
                "priority": "normal",
                "status": "pending",
                "created_at": FIXED_NOW - 240,
                "revision": 1,
                "binding_revision": 4,
                "prompt_fingerprint": "fixture-rollout-v1",
                "options_fingerprint": "fixture-rollout-options-v1",
                "recommended_option_id": "staged",
                "allow_custom": False,
                "options": [
                    {
                        "id": "staged",
                        "label": "Use a staged rollout",
                        "description": "Begin with a small compatibility window.",
                    },
                    {
                        "id": "immediate",
                        "label": "Roll out immediately",
                        "description": "Apply the migration to every active client.",
                    },
                ],
            }
        )
    elif scenario == "agent_pagination":
        decisions.extend(
            [
                {
                    "id": "decision-database",
                    "agent_id": "agent-codex",
                    "title": "Approve database migration window",
                    "description": "The compatibility window controls when legacy readers are retired.",
                    "kind": "request_user_input",
                    "priority": "normal",
                    "status": "pending",
                    "created_at": FIXED_NOW - 720,
                    "revision": 1,
                    "binding_revision": 4,
                    "prompt_fingerprint": "fixture-database-v1",
                    "options_fingerprint": "fixture-database-options-v1",
                    "recommended_option_id": "staged",
                    "allow_custom": False,
                    "options": [
                        {"id": "staged", "label": "Use a staged window", "description": "Keep legacy readers during rollout."},
                        {"id": "immediate", "label": "Migrate immediately", "description": "Finish the migration in one step."},
                    ],
                },
                {
                    "id": "decision-rollback",
                    "agent_id": "agent-codex",
                    "title": "Approve emergency rollback plan",
                    "description": "This pending item is outside the capped general history window.",
                    "kind": "request_user_input",
                    "priority": "high",
                    "status": "pending",
                    "created_at": FIXED_NOW - 900,
                    "revision": 1,
                    "binding_revision": 4,
                    "prompt_fingerprint": "fixture-rollback-v1",
                    "options_fingerprint": "fixture-rollback-options-v1",
                    "recommended_option_id": "approve",
                    "allow_custom": False,
                    "options": [
                        {"id": "approve", "label": "Approve rollback", "description": "Keep the emergency escape hatch."},
                        {"id": "revise", "label": "Revise the plan", "description": "Ask the agent for another pass."},
                    ],
                },
            ]
        )
    return decisions


def fixture_agent_timeline() -> list[dict[str, Any]]:
    return [
        {
            "id": "event-3",
            "agent_id": "agent-codex",
            "type": "decision_added",
            "title": "Refresh-token approval needed",
            "occurred_at": FIXED_NOW - 90,
            "delta": {
                "decisions_added": [
                    {"id": "decision-refresh", "title": "Refresh-token approval"}
                ]
            },
        },
        {
            "id": "event-2",
            "agent_id": "agent-codex",
            "type": "completed",
            "title": "Integration tests passed",
            "occurred_at": FIXED_NOW - 240,
            "delta": {
                "completed_items": [
                    {"id": "tests", "title": "Integration tests passed"}
                ]
            },
        },
        {
            "id": "event-1",
            "agent_id": "agent-codex",
            "type": "activity",
            "title": "Authentication API updated",
            "occurred_at": FIXED_NOW - 480,
            "delta": {
                "current_task_changed": {
                    "from": "Map authentication flow",
                    "to": "Implement token API",
                }
            },
        },
    ]


@dataclass
class FixtureState:
    scenario: str = "live"
    panes: list[dict[str, Any]] = field(default_factory=fixture_panes)
    config: dict[str, Any] = field(default_factory=fixture_config)
    requests: list[dict[str, Any]] = field(default_factory=list)
    resolved_decisions: set[str] = field(default_factory=set)
    invalidated_decisions: set[str] = field(default_factory=set)
    reviewed_snapshots: dict[str, str] = field(default_factory=dict)
    review_interval_minutes: int | None = None
    review_next_due_at: float | None = None
    review_urgent_pane_errors: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reset(self) -> None:
        with self.lock:
            self.scenario = "live"
            self.panes = fixture_panes()
            self.config = fixture_config()
            self.requests = []
            self.resolved_decisions = set()
            self.invalidated_decisions = set()
            self.reviewed_snapshots = {}
            self.review_interval_minutes = None
            self.review_next_due_at = None
            self.review_urgent_pane_errors = False

    def set_scenario(self, name: str) -> None:
        allowed = {
            "live",
            "rest_fallback",
            "silent_socket",
            "offline",
            "unauthorized",
            "incompatible",
            "malformed_compatibility",
            "unverified",
            "slow_actions",
            "action_failure",
            "slow_action_failure",
            "partial_broadcast",
            "usage_disabled",
            "usage_not_installed",
            "usage_timeout",
            "usage_error",
            "usage_stale",
            "usage_empty",
            "agent_workspace",
            "agent_safety_locked",
            "agent_pagination",
            "agent_cursor_loop",
            "agent_review",
            "agent_review_plan",
            "image_slow",
            "image_failure",
            "image_too_large",
            "image_unsupported",
        }
        if name not in allowed:
            raise ValueError(f"unknown fixture scenario: {name}")
        with self.lock:
            self.scenario = name

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"type": "state", "panes": copy.deepcopy(self.panes)}

    def record(self, endpoint: str, body: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append({"endpoint": endpoint, "body": copy.deepcopy(body)})


def create_fixture_app() -> FastAPI:
    app = FastAPI(title="vmux deterministic web fixture")
    state = FixtureState()
    app.state.fixture = state

    def scenario() -> str:
        with state.lock:
            return state.scenario

    def reject_if_unavailable() -> None:
        current = scenario()
        if current == "unauthorized":
            raise HTTPException(status_code=401, detail="fixture authorization required")
        if current == "offline":
            raise HTTPException(status_code=503, detail="fixture server offline")

    def current_decisions() -> list[dict[str, Any]]:
        values = copy.deepcopy(fixture_decisions(scenario()))
        with state.lock:
            resolved = set(state.resolved_decisions)
            invalidated = set(state.invalidated_decisions)
        for decision in values:
            if decision["id"] in resolved:
                decision["status"] = "resolved"
                decision["revision"] += 1
            elif decision["id"] in invalidated:
                decision["revision"] += 1
                decision["prompt_fingerprint"] += "-changed"
                decision["options_fingerprint"] += "-changed"
        return values

    @app.get("/__test__/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/__test__/reset")
    async def reset() -> dict[str, bool]:
        state.reset()
        return {"ok": True}

    @app.post("/__test__/scenario")
    async def set_scenario(payload: dict[str, Any]) -> dict[str, str]:
        try:
            state.set_scenario(str(payload.get("name", "")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"scenario": scenario()}

    @app.get("/__test__/requests")
    async def requests() -> dict[str, Any]:
        with state.lock:
            return {"requests": copy.deepcopy(state.requests)}

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        reject_if_unavailable()
        with state.lock:
            result = copy.deepcopy(state.config)
        current = scenario()
        if current == "incompatible":
            result["_info"]["compatibility"]["protocol_version"] = 9
        elif current == "malformed_compatibility":
            result["_info"]["compatibility"] = "not-an-object"
        elif current == "unverified":
            result["_info"].pop("version", None)
            result["_info"].pop("compatibility", None)
        elif current in {
            "agent_workspace",
            "agent_safety_locked",
            "agent_pagination",
            "agent_cursor_loop",
            "agent_review",
            "agent_review_plan",
        }:
            result["_info"]["capabilities"] = {
                "agent_context_v1": {"enabled": True, "mode": "log_observer"}
            }
            if current in {"agent_review", "agent_review_plan"}:
                result["_info"]["server_instance_id"] = "fixture-review-server"
                result["_info"]["capabilities"]["agent_review_v1"] = {
                    "enabled": True,
                    "mode": "manual_and_scheduled",
                }
        return result

    @app.patch("/api/config")
    async def patch_config(payload: dict[str, Any]) -> dict[str, Any]:
        reject_if_unavailable()
        state.record("/api/config", payload)
        with state.lock:
            for key, value in payload.items():
                if key != "_info":
                    state.config[key] = copy.deepcopy(value)
            return copy.deepcopy(state.config)

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        reject_if_unavailable()
        return state.snapshot()

    @app.post("/api/images", status_code=201)
    async def post_image(request: Request) -> JSONResponse:
        reject_if_unavailable()
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        state.record("/api/images", {"content_type": content_type, "size": len(body)})
        current = scenario()
        if current == "image_slow":
            await asyncio.sleep(0.35)
        if current == "image_failure":
            raise HTTPException(status_code=503, detail="fixture image storage unavailable")
        if current == "image_too_large":
            raise HTTPException(status_code=413, detail="image exceeds the 20 MiB limit")
        if current == "image_unsupported":
            raise HTTPException(status_code=415, detail="supported images are PNG, JPEG, WebP, and GIF")
        with state.lock:
            index = len([row for row in state.requests if row["endpoint"] == "/api/images"])
        path = f"/private/tmp/vmux-fixture-image-{index}.png"
        return JSONResponse(
            {
                "id": f"fixture-image-{index}",
                "path": path,
                "terminal_text": path,
                "mime_type": "image/png",
                "size": len(body),
                "expires_at": int(FIXED_NOW + 86_400),
            },
            status_code=201,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    async def action(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        reject_if_unavailable()
        state.record(endpoint, payload)
        current = scenario()
        if current in {"slow_actions", "slow_action_failure"}:
            await asyncio.sleep(0.45)
        if current in {"action_failure", "slow_action_failure"}:
            raise HTTPException(status_code=500, detail="deterministic action failure")
        return {"ok": True}

    @app.post("/api/key")
    async def post_key(payload: dict[str, Any]) -> dict[str, Any]:
        return await action("/api/key", payload)

    @app.post("/api/text")
    async def post_text(payload: dict[str, Any]) -> dict[str, Any]:
        return await action("/api/text", payload)

    @app.post("/api/select")
    async def post_select(payload: dict[str, Any]) -> dict[str, Any]:
        return await action("/api/select", payload)

    @app.post("/api/star")
    async def post_star(payload: dict[str, Any]) -> dict[str, Any]:
        result = await action("/api/star", payload)
        with state.lock:
            for pane in state.panes:
                if pane["target"] == payload.get("target"):
                    pane["starred"] = bool(payload.get("starred"))
        return result

    @app.post("/api/broadcast")
    async def post_broadcast(payload: dict[str, Any]) -> dict[str, Any]:
        result = await action("/api/broadcast", payload)
        ids = list(payload.get("ids") or [])
        if scenario() == "partial_broadcast" and ids:
            return {**result, "sent": max(0, len(ids) - 1), "errors": [f"{ids[-1]}: fixture failure"]}
        return {**result, "sent": len(ids), "errors": []}

    @app.get("/api/agents")
    async def get_agents(cursor: str | None = None) -> dict[str, Any]:
        reject_if_unavailable()
        current = scenario()
        if current == "agent_pagination":
            state.record("/api/agents", {"cursor": cursor})
            agents = fixture_agents(current)
            if cursor is None:
                return {"agents": agents[:2], "next_cursor": "agents-page-2"}
            if cursor == "agents-page-2":
                return {"agents": [copy.deepcopy(agents[1]), agents[2]], "next_cursor": None}
            return {"agents": [], "next_cursor": None}
        return {"agents": fixture_agents(current), "next_cursor": None}

    @app.get("/api/agents/{agent_id}")
    async def get_agent(agent_id: str) -> dict[str, Any]:
        reject_if_unavailable()
        agent = next((row for row in fixture_agents(scenario()) if row["id"] == agent_id), None)
        if not agent:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent

    @app.get("/api/agents/{agent_id}/resume")
    async def get_agent_resume(agent_id: str) -> dict[str, Any]:
        agent = next((row for row in fixture_agents(scenario()) if row["id"] == agent_id), None)
        if not agent:
            raise HTTPException(status_code=404, detail="agent not found")
        return {
            "context": agent,
            "changes": {
                "completed_items": [{"id": "tests", "title": "Integration tests passed"}],
                "decisions_added": [{"id": "decision-refresh", "title": "Refresh-token approval needed"}],
            },
            "pending_decisions": fixture_decisions(scenario()) if agent_id == "agent-codex" else [],
            "next_action": agent["next_action"],
            "baseline_snapshot_id": "snapshot-8",
            "as_of_snapshot_id": "snapshot-12",
            "history_truncated": False,
        }

    @app.get("/api/review")
    async def get_review() -> dict[str, Any]:
        reject_if_unavailable()
        if scenario() not in {"agent_review", "agent_review_plan"}:
            raise HTTPException(status_code=404, detail="review unavailable")
        with state.lock:
            interval = state.review_interval_minutes
            next_due_at = state.review_next_due_at
            pane_errors = state.review_urgent_pane_errors
            reviewed = state.reviewed_snapshots.get("agent-codex")
        decisions = [
            {**decision, "review_status": "actionable"}
            for decision in current_decisions()
            if decision["status"] == "pending"
        ]
        group_visible = reviewed != "snapshot-12" or bool(decisions)
        groups = []
        if group_visible:
            agent = copy.deepcopy(fixture_agents(scenario())[0])
            groups.append(
                {
                    "agent_id": agent["id"],
                    "agent": agent,
                    "as_of_snapshot_id": "snapshot-12",
                    "as_of_snapshot_sequence": 12,
                    "as_of_snapshot_at": FIXED_NOW - 32,
                    "reviewed_snapshot_id": "snapshot-8",
                    "reviewed_snapshot_sequence": 8,
                    "reviewed_snapshot_at": FIXED_NOW - 900,
                    "reviewed_at": FIXED_NOW - 850,
                    "has_changes": True,
                    "history_truncated": False,
                    "changes": {
                        "completed_items": [
                            {"id": "tests", "title": "Integration tests passed"}
                        ],
                        "current_task_changed": {
                            "from": "Implement token API",
                            "to": "Reviewing refresh-token strategy",
                        },
                        "new_blockers": [
                            {"id": "approval", "title": "Refresh-token approval"}
                        ],
                    },
                    "decisions": decisions,
                    "oldest_pending_decision_at": min(
                        (decision["created_at"] for decision in decisions),
                        default=None,
                    ),
                    "rank_reason": "urgent_decision" if decisions else "new_blocker",
                    "attention_reasons": [
                        "urgent_decision",
                        "pending_decision",
                        "new_blocker",
                        "semantic_change",
                    ],
                }
            )
        terminal_items = [
            {
                "id": "pane:%1",
                "pane_id": "%1",
                "status": "needs_input",
                "kind": "claude-code",
                "updated_at": FIXED_NOW - 45,
                "acknowledgeable": False,
            }
        ]
        pending_count = len(decisions)
        settings = {
            "enabled": interval is not None,
            "interval_minutes": interval,
            "next_due_at": next_due_at,
            "last_digest_at": None,
            "urgent_bypass": {
                "high_critical_decisions": True,
                "pane_errors": pane_errors,
            },
            "min_interval_minutes": 5,
            "max_interval_minutes": 1440,
            "presets": [30, 60],
        }
        return {
            "version": 1,
            "generated_at": FIXED_NOW,
            "settings": settings,
            "due": {
                "is_due": False,
                "urgent": pending_count > 0,
                "has_work": bool(groups or terminal_items),
                "next_due_at": next_due_at,
            },
            "counts": {
                "agents_changed": len(groups),
                "pending_decisions": pending_count,
                "terminal_requests": len(terminal_items),
                "total_cards": len(groups) + len(terminal_items),
                "urgent_items": 1 if pending_count else 0,
            },
            "groups": groups,
            "terminal_items": terminal_items,
        }

    @app.patch("/api/review/settings")
    async def patch_review_settings(payload: dict[str, Any]) -> dict[str, Any]:
        state.record("/api/review/settings", payload)
        with state.lock:
            if "interval_minutes" in payload:
                interval = payload["interval_minutes"]
                state.review_interval_minutes = (
                    int(interval) if interval is not None else None
                )
                state.review_next_due_at = (
                    FIXED_NOW + state.review_interval_minutes * 60
                    if state.review_interval_minutes is not None
                    else None
                )
            if "urgent_pane_errors" in payload:
                state.review_urgent_pane_errors = bool(payload["urgent_pane_errors"])
        return (await get_review())["settings"]

    @app.put("/api/agents/{agent_id}/visit")
    async def put_agent_visit(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state.record(f"/api/agents/{agent_id}/visit", payload)
        return {"ok": True, "snapshot_id": payload.get("snapshot_id")}

    @app.put("/api/agents/{agent_id}/review")
    async def put_agent_review(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state.record(f"/api/agents/{agent_id}/review", payload)
        snapshot_id = str(payload.get("snapshot_id") or "")
        with state.lock:
            state.reviewed_snapshots[agent_id] = snapshot_id
            if state.review_interval_minutes is not None:
                state.review_next_due_at = (
                    FIXED_NOW + state.review_interval_minutes * 60
                )
        return {
            "agent_id": agent_id,
            "snapshot_id": snapshot_id,
            "snapshot_sequence": 12,
            "reviewed_at": FIXED_NOW,
            "advanced": True,
        }

    @app.get("/api/agents/{agent_id}/messages")
    async def get_agent_messages(
        agent_id: str,
        cursor: str | None = None,
        q: str | None = None,
        role: str | None = None,
        after: float | None = None,
        before: float | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        messages = [
            {"id": "message-1", "role": "user", "content": "Refactor authentication safely.", "status": "observed", "created_at": FIXED_NOW - 600},
            {"id": "message-2", "role": "assistant", "content": "The API and compatibility tests are complete.", "status": "observed", "created_at": FIXED_NOW - 300},
        ] if agent_id == "agent-codex" else []
        if scenario() in {"agent_review", "agent_review_plan"} and agent_id == "agent-codex":
            messages = [
                {
                    "id": "message-0",
                    "role": "assistant",
                    "content": "I mapped the existing authentication flow.",
                    "status": "observed",
                    "created_at": FIXED_NOW - 1200,
                },
                *messages,
            ]
            filtered = [
                message
                for message in messages
                if (not q or q.lower() in message["content"].lower())
                and (not role or role == message["role"])
                and (after is None or message["created_at"] >= after)
                and (before is None or message["created_at"] <= before)
            ]
            offset = int(cursor or 0)
            descending = list(reversed(filtered))
            selected = list(reversed(descending[offset : offset + min(limit, 2)]))
            next_value = (
                str(offset + min(limit, 2))
                if len(descending) > offset + min(limit, 2)
                else None
            )
            state.record(
                f"/api/agents/{agent_id}/messages",
                {
                    "cursor": cursor,
                    "q": q,
                    "role": role,
                    "after": after,
                    "before": before,
                    "limit": limit,
                },
            )
            if q == "slow query":
                await asyncio.sleep(0.75)
            return {
                "messages": selected,
                "next_cursor": next_value,
                "retained_from": messages[0]["created_at"],
                "retained_to": messages[-1]["created_at"],
                "reviewed_snapshot_id": "snapshot-8",
                "reviewed_snapshot_sequence": 8,
                "reviewed_snapshot_at": FIXED_NOW - 900,
                "reviewed_at": FIXED_NOW - 850,
                "history_truncated": True,
                "filters": {
                    "q": q,
                    "role": role,
                    "after": after,
                    "before": before,
                },
            }
        if scenario() == "agent_pagination" and agent_id == "agent-codex":
            state.record(f"/api/agents/{agent_id}/messages", {"cursor": cursor})
            if cursor is None:
                return {"messages": messages, "next_cursor": "messages-page-2"}
            if cursor == "messages-page-2":
                return {
                    "messages": [
                        {"id": "message-0", "role": "assistant", "content": "I mapped the existing authentication flow.", "status": "observed", "created_at": FIXED_NOW - 900},
                        copy.deepcopy(messages[0]),
                    ],
                    "next_cursor": None,
                }
            return {"messages": [], "next_cursor": None}
        return {"messages": messages, "next_cursor": None}

    @app.post("/api/agents/{agent_id}/messages")
    async def post_agent_message(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state.record(f"/api/agents/{agent_id}/messages", payload)
        return {
            "message": {
                "id": "message-sent",
                "client_message_id": payload.get("client_message_id"),
                "role": "user",
                "content": payload.get("text"),
                "status": "sent",
                "created_at": FIXED_NOW,
            }
        }

    @app.get("/api/agents/{agent_id}/timeline")
    async def get_agent_timeline(agent_id: str, cursor: str | None = None) -> dict[str, Any]:
        events = [row for row in fixture_agent_timeline() if row["agent_id"] == agent_id]
        if scenario() == "agent_pagination" and agent_id == "agent-codex":
            state.record(f"/api/agents/{agent_id}/timeline", {"cursor": cursor})
            if cursor is None:
                return {"events": events[:2], "next_cursor": "agent-timeline-page-2"}
            if cursor == "agent-timeline-page-2":
                return {"events": [copy.deepcopy(events[1]), events[2]], "next_cursor": None}
            return {"events": [], "next_cursor": None}
        return {"events": events, "next_cursor": None}

    @app.put("/api/agents/{agent_id}/binding")
    async def put_agent_binding(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state.record(f"/api/agents/{agent_id}/binding", payload)
        agent = next((copy.deepcopy(row) for row in fixture_agents(scenario()) if row["id"] == agent_id), None)
        if not agent:
            raise HTTPException(status_code=404, detail="agent not found")
        agent["association"] = "confirmed"
        agent["binding"] = {"pane_id": payload.get("pane_id"), "revision": agent["binding"]["revision"] + 1, "association": "confirmed", "candidates": []}
        agent["capabilities"] = {**agent["capabilities"], "association": "confirmed", "chat_send": "idle_only", "decision_reply": "verified_terminal"}
        return {"agent": agent}

    @app.delete("/api/agents/{agent_id}/binding")
    async def delete_agent_binding(agent_id: str, expected_binding_revision: int) -> dict[str, Any]:
        state.record(f"/api/agents/{agent_id}/binding", {"expected_binding_revision": expected_binding_revision})
        return {"ok": True}

    @app.get("/api/decisions")
    async def get_decisions(cursor: str | None = None, status: str | None = None) -> dict[str, Any]:
        current = scenario()
        decisions = current_decisions()
        if current == "agent_pagination":
            state.record("/api/decisions", {"cursor": cursor, "status": status})
            if status == "submitting":
                return {"decisions": [], "next_cursor": None}
            if status == "pending":
                if cursor is None:
                    return {"decisions": decisions[:1], "next_cursor": "pending-decisions-page-2"}
                if cursor == "pending-decisions-page-2":
                    return {
                        "decisions": [copy.deepcopy(decisions[0]), decisions[1], decisions[2]],
                        "next_cursor": None,
                    }
                return {"decisions": [], "next_cursor": None}
            if cursor is None:
                return {"decisions": decisions[:1], "next_cursor": "decisions-page-2"}
            if cursor == "decisions-page-2":
                return {"decisions": [copy.deepcopy(decisions[0]), decisions[1]], "next_cursor": None}
            return {"decisions": [], "next_cursor": None}
        if status:
            decisions = [decision for decision in decisions if decision["status"] == status]
        return {"decisions": decisions, "next_cursor": None}

    @app.get("/api/decisions/{decision_id}")
    async def get_decision(decision_id: str) -> dict[str, Any]:
        decision = next((row for row in current_decisions() if row["id"] == decision_id), None)
        if not decision:
            raise HTTPException(status_code=404, detail="decision not found")
        return decision

    @app.post("/api/decisions/{decision_id}/reply")
    async def post_decision_reply(decision_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state.record(f"/api/decisions/{decision_id}/reply", payload)
        decision = next((copy.deepcopy(row) for row in current_decisions() if row["id"] == decision_id), None)
        if not decision:
            raise HTTPException(status_code=404, detail="decision not found")
        if (
            payload.get("expected_revision") != decision["revision"]
            or payload.get("expected_binding_revision") != decision["binding_revision"]
            or payload.get("prompt_fingerprint") != decision["prompt_fingerprint"]
        ):
            raise HTTPException(status_code=409, detail="fixture decision changed")
        current = scenario()
        with state.lock:
            state.resolved_decisions.add(decision_id)
            if current == "agent_review_plan" and decision_id == "decision-refresh":
                state.invalidated_decisions.add("decision-rollout")
        decision["status"] = "resolved"
        decision["revision"] += 1
        return {"decision": decision}

    @app.get("/api/timeline")
    async def get_global_timeline(cursor: str | None = None) -> dict[str, Any]:
        events = fixture_agent_timeline()
        current = scenario()
        if current == "agent_pagination":
            state.record("/api/timeline", {"cursor": cursor})
            if cursor is None:
                return {"events": events[:2], "next_cursor": "timeline-page-2"}
            if cursor == "timeline-page-2":
                return {"events": [copy.deepcopy(events[1]), events[2]], "next_cursor": None}
            return {"events": [], "next_cursor": None}
        if current == "agent_cursor_loop":
            state.record("/api/timeline", {"cursor": cursor})
            if cursor is None:
                return {"events": events[:1], "next_cursor": "timeline-loop"}
            return {
                "events": [
                    copy.deepcopy(events[0]),
                    {"id": "event-loop", "agent_id": "agent-codex", "type": "activity", "title": "Cursor loop page retrieved", "occurred_at": FIXED_NOW - 180},
                ],
                "next_cursor": "timeline-loop",
            }
        return {"events": events, "next_cursor": None}

    @app.get("/api/sessions")
    async def get_sessions() -> dict[str, Any]:
        reject_if_unavailable()
        return {
            "sessions": [
                {
                    "id": "fixture-session",
                    "ip": "127.0.0.1",
                    "ua": "Playwright fixture",
                    "age": 120.0,
                }
            ]
        }

    @app.post("/api/sessions/kill")
    async def kill_session(payload: dict[str, Any]) -> dict[str, Any]:
        state.record("/api/sessions/kill", payload)
        return {"ok": True}

    def unavailable_usage(reason: str, detail: str) -> dict[str, Any]:
        return {
            "available": False,
            "reason": reason,
            "detail": detail,
            "fetched_at": 0,
            "stale": False,
            "quotas": [],
            "today": None,
        }

    def usage_for_scenario() -> dict[str, Any]:
        current = scenario()
        if current == "usage_disabled":
            return unavailable_usage("disabled", "usage tracking is disabled in config")
        if current == "usage_not_installed":
            return unavailable_usage("not_installed", "tokscale is not installed")
        if current == "usage_timeout":
            return unavailable_usage("timeout", "fixture collection timed out")
        if current == "usage_error":
            return unavailable_usage("error", "fixture collection failed")
        return fixture_usage(stale=current == "usage_stale", empty=current == "usage_empty")

    @app.get("/api/usage")
    async def get_usage() -> dict[str, Any]:
        reject_if_unavailable()
        return usage_for_scenario()

    @app.get("/api/usage/history")
    async def get_usage_history(period: str = "daily", days: int | None = None) -> dict[str, Any]:
        reject_if_unavailable()
        summary = usage_for_scenario()
        if not summary["available"]:
            return {**summary, "period": period, "buckets": []}
        buckets = fixture_buckets()
        if period == "monthly":
            buckets = [
                {
                    "bucket": "2026-07",
                    "totals": _totals(23.10, 843_000, 206),
                    "by_client": [],
                    "by_model": [],
                    "clients": [],
                    "models": [],
                }
            ]
        elif days:
            buckets = buckets[-max(1, min(30, days)) :]
        return {
            "available": True,
            "reason": None,
            "detail": None,
            "period": period,
            "fetched_at": FIXED_NOW - 18,
            "stale": scenario() == "usage_stale",
            "buckets": buckets,
        }

    @app.post("/api/usage/refresh")
    async def refresh_usage(payload: dict[str, Any]) -> dict[str, Any]:
        state.record("/api/usage/refresh", payload)
        return usage_for_scenario()

    @app.websocket("/ws")
    async def websocket_state(websocket: WebSocket) -> None:
        current = scenario()
        if current == "unauthorized":
            await websocket.close(code=1008)
            return
        await websocket.accept()
        if current in {"offline", "rest_fallback"}:
            await websocket.close(code=1012)
            return
        await websocket.send_json({"type": "hello", "sid": "fixture"})
        try:
            while True:
                current = scenario()
                if current == "unauthorized":
                    await websocket.close(code=1008)
                    return
                if current in {"offline", "rest_fallback"}:
                    await websocket.close(code=1012)
                    return
                if current == "silent_socket":
                    await asyncio.sleep(0.2)
                    continue
                await websocket.send_json(state.snapshot())
                await asyncio.sleep(0.5)
        except Exception:
            return

    @app.websocket("/ws/agents")
    async def websocket_agents(websocket: WebSocket) -> None:
        if scenario() not in {"agent_workspace", "agent_pagination", "agent_cursor_loop"}:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_json({"type": "hello", "cursor": "fixture-agent-cursor"})
        try:
            while True:
                await asyncio.sleep(0.5)
        except Exception:
            return

    @app.get("/{asset_path:path}")
    async def static_asset(asset_path: str, request: Request):
        requested = (WEB_ROOT / asset_path).resolve()
        try:
            requested.relative_to(WEB_ROOT.resolve())
        except ValueError:
            return JSONResponse({"detail": "not found"}, status_code=404)
        if asset_path and requested.is_file():
            return FileResponse(requested)
        if not asset_path or "text/html" in request.headers.get("accept", ""):
            return FileResponse(WEB_ROOT / "index.html", media_type="text/html")
        return JSONResponse({"detail": "not found"}, status_code=404)

    return app

"""Compatibility adapter over vmux's existing safe tmux wrappers."""

from __future__ import annotations

import re
import time
from typing import Optional

from .. import tmux
from .base import (
    CaptureResult,
    DiscoveryResult,
    EndpointCapabilities,
    EndpointRef,
    EndpointSnapshot,
    HierarchyNode,
    ProviderHealth,
    TerminalProvider,
)

_TARGET_RE = re.compile(r"^(.+):([0-9]+)\.([0-9]+)$")


class TmuxProvider(TerminalProvider):
    name = "tmux"

    def __init__(self):
        self._endpoints: dict[str, EndpointSnapshot] = {}
        self._health = ProviderHealth(status="ready")

    def probe(self) -> ProviderHealth:
        if tmux.available():
            self._health = ProviderHealth(status="ready", last_success_at=time.time())
        else:
            self._health = ProviderHealth(status="unavailable", last_error="binary_missing")
        return self._health

    def discover(self) -> DiscoveryResult:
        panes = tmux.list_panes()
        endpoints = []
        for pane in panes:
            pane_id = pane["id"]
            target = pane["target"]
            match = _TARGET_RE.match(target)
            hierarchy = ()
            if match:
                session, window, pane_index = match.groups()
                hierarchy = (
                    HierarchyNode("session", "tmux-session:" + session, session),
                    HierarchyNode(
                        "window",
                        str(pane.get("window_id") or "tmux-window:" + session + ":" + window),
                        str(pane.get("window") or window),
                        int(window),
                    ),
                    HierarchyNode("pane", pane_id, str(pane.get("title") or pane_index), int(pane_index)),
                )
            incarnation = "%s:%s:%s" % (
                pane_id,
                pane.get("pid", ""),
                pane.get("created", ""),
            )
            endpoint = EndpointSnapshot(
                ref=EndpointRef("tmux", "server", pane_id, incarnation),
                public_id=pane_id,
                persistent_target=target,
                hierarchy=hierarchy,
                command=pane.get("cmd", ""),
                title=pane.get("title", ""),
                cwd=pane.get("path", ""),
                window=pane.get("window", ""),
                native_revision=incarnation,
                capabilities=EndpointCapabilities(
                    input="legacy",
                    keys=tuple(sorted(tmux.ALLOWED_KEYS)),
                    broadcast=True,
                    create=True,
                ),
                provider_metadata={
                    "pid": str(pane.get("pid", "")),
                    "created": str(pane.get("created", "")),
                    "window_id": str(pane.get("window_id", "")),
                },
            )
            endpoints.append(endpoint)
        self._endpoints = {endpoint.public_id: endpoint for endpoint in endpoints}
        self._health = ProviderHealth(status="ready", last_success_at=time.time())
        return DiscoveryResult(self._health, tuple(endpoints), authoritative=True)

    def capture(self, endpoint: EndpointSnapshot, *, lines: int) -> CaptureResult:
        text = tmux.capture(endpoint.ref.native_endpoint_id, lines)
        if text is None:
            raise RuntimeError("tmux capture failed")
        return CaptureResult(text=text, native_revision=endpoint.native_revision)

    def resolve(self, public_id: str) -> Optional[EndpointSnapshot]:
        endpoint = self._endpoints.get(public_id)
        if endpoint is not None:
            return endpoint
        # Preserve the historical direct tmux id/target fallback for old clients.
        if tmux.valid_pane_id(public_id):
            return EndpointSnapshot(
                ref=EndpointRef("tmux", "server", public_id, public_id),
                public_id=public_id,
                persistent_target=public_id,
                hierarchy=(),
                capabilities=EndpointCapabilities(
                    input="legacy",
                    keys=tuple(sorted(tmux.ALLOWED_KEYS)),
                    broadcast=True,
                    create=True,
                ),
            )
        return None

    def send_literal(self, endpoint: EndpointSnapshot, text: str) -> None:
        tmux.send_literal(endpoint.ref.native_endpoint_id, text)

    def send_key(self, endpoint: EndpointSnapshot, key: str) -> None:
        tmux.send_key(endpoint.ref.native_endpoint_id, key)

    def send_menu_key(self, endpoint: EndpointSnapshot, key: str) -> None:
        tmux.send_chars(endpoint.ref.native_endpoint_id, key)

    def capability(self) -> dict:
        return {
            "version": 1,
            "provider": "tmux",
            "mode": "legacy",
            "guarded_input": False,
            "native_agent_state": False,
            "creation": True,
            "deletion": False,
            "event_subscription": {
                "supported": False,
                "active": False,
                "minimum_protocol": None,
            },
            "health": self._health.to_dict(),
        }

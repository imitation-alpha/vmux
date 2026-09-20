"""Herdr monitor-and-respond provider.

Only public read and pane-input CLI surfaces are used.  Every command is routed
to the one configured session with a trailing ``--session`` argument.  This
module intentionally contains no creation, deletion, focus, move, rename,
agent-start, or server/session lifecycle operation.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Optional, Sequence

from .base import (
    CaptureResult,
    DiscoveryResult,
    EndpointCapabilities,
    EndpointRef,
    EndpointSnapshot,
    HierarchyNode,
    NativeAgentState,
    ProviderError,
    ProviderHealth,
    TerminalProvider,
    VerifiedEndpoint,
)
from .herdr_events import HerdrEventSubscriber

MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MIN_PROTOCOL = 20
HERDR_KEY_MAP = {
    # This deliberately contains only mappings verified against the supported
    # protocol floor. Menu characters use the separately guarded menu path.
    "Enter": "enter",
    "Escape": "esc",
    "C-c": "ctrl+c",
    "C-u": "ctrl+u",
}
_NATIVE_STATUSES = {"idle", "working", "blocked", "done", "unknown"}


def _bounded(value: Any, limit: int = 200) -> str:
    return str(value or "")[:limit]


def _integer(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _status_session_matches(record: dict, session: str) -> bool:
    if "session" not in record:
        return False
    value = record["session"]
    # Herdr 0.8.2 serializes the implicit default session as JSON null. Named
    # sessions carry their name here; the session-list and socket checks below
    # remain the authoritative identity proof for both forms.
    return value == session or (session == "default" and value is None)


def _schema_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if isinstance(value, str):
        tokens.add(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                tokens.add(key)
            tokens.update(_schema_tokens(item))
    elif isinstance(value, list):
        for item in value:
            tokens.update(_schema_tokens(item))
    return tokens


class HerdrProvider(TerminalProvider):
    name = "herdr"

    def __init__(
        self,
        *,
        session: str,
        binary: str = "herdr",
        events: str = "auto",
        server_instance_id: str = "",
    ):
        if not isinstance(session, str) or not session.strip() or "\x00" in session or len(session) > 128:
            raise ProviderError("a valid explicit Herdr session is required", category="bad_config")
        resolved = binary if os.path.isabs(binary) else shutil.which(binary)
        if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
            raise ProviderError("Herdr executable is unavailable", category="binary_missing")
        if events not in ("auto", "off"):
            raise ProviderError("Herdr events must be auto or off", category="bad_config")
        self.session = session.strip()
        self.binary = os.path.realpath(resolved)
        self.events_mode = events
        self.server_instance_id = server_instance_id
        self._endpoints: dict[str, EndpointSnapshot] = {}
        self._public_by_incarnation: dict[str, str] = {}
        self._health = ProviderHealth(status="unavailable")
        self._event_subscriber: Optional[HerdrEventSubscriber] = None
        self._socket_path = ""
        self._schema_events = False

    @property
    def health(self) -> ProviderHealth:
        subscriber = self._event_subscriber
        return ProviderHealth(
            status=self._health.status,
            last_success_at=self._health.last_success_at,
            last_error=self._health.last_error,
            protocol=self._health.protocol,
            version=self._health.version,
            events_supported=self._health.events_supported,
            events_active=bool(subscriber and subscriber.active),
        )

    def _run(self, args: Sequence[str], *, timeout: float = 5.0, mutation: bool = False) -> str:
        argv = [self.binary, *args, "--session", self.session]
        env = os.environ.copy()
        env["HERDR_SESSION"] = self.session
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise ProviderError("Herdr executable is unavailable", category="binary_missing") from exc
        except OSError as exc:
            raise ProviderError("Herdr call failed", category="unavailable") from exc

        buffers = [bytearray(), bytearray()]
        oversized = threading.Event()

        def drain(stream, index: int) -> None:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                remaining = MAX_OUTPUT_BYTES + 1 - len(buffers[index])
                if remaining > 0:
                    buffers[index].extend(chunk[:remaining])
                if len(buffers[index]) > MAX_OUTPUT_BYTES:
                    oversized.set()
                    with contextlib.suppress(OSError):
                        process.kill()
                    return

        readers = [
            threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
        ]
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()
            for reader in readers:
                reader.join(timeout=1.0)
            category = "delivery_unknown" if mutation else "timeout"
            raise ProviderError("Herdr call timed out", category=category) from exc
        for reader in readers:
            reader.join(timeout=1.0)
        if oversized.is_set():
            category = "delivery_unknown" if mutation else "oversized"
            raise ProviderError("Herdr response exceeded the safety limit", category=category)
        if returncode != 0:
            category = "mutation_rejected" if mutation else "command_failed"
            raise ProviderError("Herdr rejected the request", category=category)
        return bytes(buffers[0]).decode("utf-8", "replace")

    def _json(self, args: Sequence[str], *, timeout: float = 5.0) -> dict:
        try:
            value = json.loads(self._run(args, timeout=timeout))
        except json.JSONDecodeError as exc:
            raise ProviderError("Herdr returned malformed JSON", category="malformed") from exc
        if not isinstance(value, dict):
            raise ProviderError("Herdr returned an invalid response", category="malformed")
        if isinstance(value.get("error"), dict):
            raise ProviderError("Herdr returned an error", category="command_failed")
        return value

    def probe(self) -> ProviderHealth:
        try:
            status = self._json(["status", "--json"])
            client = status.get("client")
            server = status.get("server")
            if not isinstance(client, dict) or not isinstance(server, dict):
                raise ProviderError("Herdr status is incomplete", category="malformed")
            protocol = _integer(server.get("protocol"))
            client_protocol = _integer(client.get("protocol"))
            if (
                server.get("running") is not True
                or server.get("compatible") is not True
                or protocol is None
                or protocol < MIN_PROTOCOL
                or client_protocol != protocol
                or not _status_session_matches(client, self.session)
                or not _status_session_matches(server, self.session)
            ):
                raise ProviderError("configured Herdr session is not compatible", category="incompatible")
            sessions = self._json(["session", "list", "--json"]).get("sessions")
            if not isinstance(sessions, list) or any(not isinstance(item, dict) for item in sessions):
                raise ProviderError("Herdr session collection is malformed", category="malformed")
            matches = [item for item in sessions if item.get("name") == self.session]
            expected_default = self.session == "default"
            if (
                len(matches) != 1
                or matches[0].get("running") is not True
                or matches[0].get("default") is not expected_default
            ):
                raise ProviderError("configured Herdr session is unavailable", category="session_unavailable")
            socket_path = str(server.get("socket") or "")
            if (
                matches[0].get("socket_path") != socket_path
                or not HerdrEventSubscriber.validate_socket(socket_path)
            ):
                raise ProviderError("Herdr socket identity is inconsistent", category="socket_invalid")
            schema = self._json(["api", "schema", "--json"])
            schema_tokens = _schema_tokens(schema)
            required_methods = {
                "session.snapshot", "pane.get", "pane.read", "pane.send_text", "pane.send_keys",
            }
            if not required_methods.issubset(schema_tokens):
                raise ProviderError("Herdr schema lacks a required method", category="incompatible")
            self._schema_events = {
                "events.subscribe", "pane.agent_status_changed",
            }.issubset(schema_tokens)
            self._socket_path = socket_path
            self._health = ProviderHealth(
                status="ready",
                last_success_at=time.time(),
                protocol=protocol,
                version=_bounded(server.get("version"), 80) or None,
                events_supported=bool(self.events_mode == "auto" and self._schema_events),
            )
        except ProviderError as exc:
            self._health = ProviderHealth(status="unavailable", last_error=exc.category)
            raise
        return self.health

    def _snapshot(self) -> dict:
        envelope = self._json(["api", "snapshot"], timeout=8.0)
        result = envelope.get("result")
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("type") != "session_snapshot" or not isinstance(snapshot, dict):
            raise ProviderError("Herdr snapshot is malformed", category="malformed")
        protocol = _integer(snapshot.get("protocol"))
        if protocol is None or protocol < MIN_PROTOCOL:
            raise ProviderError("Herdr snapshot protocol is incompatible", category="incompatible")
        return snapshot

    def _opaque(self, prefix: str, value: str) -> str:
        digest = hashlib.sha256(
            (self.server_instance_id + "\0" + self.session + "\0" + value).encode("utf-8", "replace")
        ).digest()[:18]
        return prefix + ":" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def _public_id(self, incarnation: str) -> str:
        current = self._public_by_incarnation.get(incarnation)
        if current:
            return current
        return "h:" + secrets.token_urlsafe(18)

    def _parse_snapshot(self, snapshot: dict) -> tuple[EndpointSnapshot, ...]:
        workspaces = snapshot.get("workspaces")
        tabs = snapshot.get("tabs")
        panes = snapshot.get("panes")
        agents = snapshot.get("agents")
        if not all(isinstance(value, list) for value in (workspaces, tabs, panes, agents)):
            raise ProviderError("Herdr snapshot collections are malformed", category="malformed")

        def unique(items: list, key: str) -> dict[str, dict]:
            mapped: dict[str, dict] = {}
            for item in items:
                if not isinstance(item, dict):
                    raise ProviderError("Herdr snapshot item is malformed", category="malformed")
                value = item.get(key)
                if not isinstance(value, str) or not value or value in mapped:
                    raise ProviderError("Herdr snapshot identity is ambiguous", category="identity_ambiguous")
                mapped[value] = item
            return mapped

        workspace_by_id = unique(workspaces, "workspace_id")
        tab_by_id = unique(tabs, "tab_id")
        pane_by_id = unique(panes, "pane_id")
        agent_by_pane: dict[str, dict] = {}
        for agent in agents:
            if not isinstance(agent, dict):
                raise ProviderError("Herdr agent entry is malformed", category="malformed")
            pane_id = agent.get("pane_id")
            terminal_id = agent.get("terminal_id")
            pane = pane_by_id.get(str(pane_id))
            if (
                pane is None
                or pane.get("terminal_id") != terminal_id
                or pane.get("workspace_id") != agent.get("workspace_id")
                or pane.get("tab_id") != agent.get("tab_id")
                or pane_id in agent_by_pane
            ):
                raise ProviderError("Herdr agent identity is ambiguous", category="identity_ambiguous")
            agent_by_pane[str(pane_id)] = agent

        endpoints = []
        next_public: dict[str, str] = {}
        seen_terminal_ids = set()
        capabilities = EndpointCapabilities(
            input="guarded_v1",
            keys=tuple(HERDR_KEY_MAP),
            broadcast=False,
            create=False,
            delete=False,
            native_agent_state=True,
        )
        for pane_id, pane in pane_by_id.items():
            workspace_id = pane.get("workspace_id")
            tab_id = pane.get("tab_id")
            terminal_id = pane.get("terminal_id")
            workspace = workspace_by_id.get(str(workspace_id))
            tab = tab_by_id.get(str(tab_id))
            if (
                workspace is None
                or tab is None
                or tab.get("workspace_id") != workspace_id
                or not isinstance(terminal_id, str)
                or not terminal_id
                or terminal_id in seen_terminal_ids
            ):
                raise ProviderError("Herdr hierarchy is inconsistent", category="identity_ambiguous")
            seen_terminal_ids.add(terminal_id)
            revision = pane.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, (str, int)):
                raise ProviderError("Herdr pane revision is invalid", category="malformed")
            revision_text = str(revision)
            # Route fields are all part of the action incarnation. A move rotates
            # the public handle even if Herdr preserves terminal_id.
            incarnation = "\0".join((self.session, terminal_id, pane_id, str(workspace_id), str(tab_id)))
            public_id = self._public_id(incarnation)
            next_public[incarnation] = public_id
            agent = agent_by_pane.get(pane_id)
            status_source = agent or pane
            native_status = str(status_source.get("agent_status") or "unknown")
            if native_status not in _NATIVE_STATUSES:
                native_status = "unknown"
            agent_kind = _bounded(status_source.get("agent"), 80) or None
            display_name = _bounded(status_source.get("display_agent") or status_source.get("name"), 120) or None
            seq = _integer(status_source.get("state_change_seq"))
            interactive = status_source.get("interactive_ready")
            native = NativeAgentState(
                present=bool(agent_kind or agent),
                kind=agent_kind,
                name=display_name,
                status=native_status,
                state_change_seq=seq,
                interactive_ready=interactive if isinstance(interactive, bool) else None,
            )
            workspace_number = _integer(workspace.get("number"))
            tab_number = _integer(tab.get("number"))
            hierarchy = (
                HierarchyNode("session", self._opaque("hs", self.session), self.session),
                HierarchyNode(
                    "workspace",
                    self._opaque("hw", str(workspace_id)),
                    _bounded(workspace.get("label"), 160) or "Workspace",
                    workspace_number,
                ),
                HierarchyNode(
                    "tab",
                    self._opaque("ht", str(tab_id)),
                    _bounded(tab.get("label"), 160) or "Tab",
                    tab_number,
                ),
                HierarchyNode(
                    "pane",
                    self._opaque("hp", pane_id),
                    _bounded(pane.get("label") or pane.get("title") or pane_id, 160),
                ),
            )
            endpoints.append(EndpointSnapshot(
                ref=EndpointRef("herdr", self.session, pane_id, incarnation),
                public_id=public_id,
                # Route-qualified persistence avoids assuming terminal_id stays
                # stable/non-reused across moves or server restores. Stars and
                # overrides intentionally require re-selection after a move.
                persistent_target=self._opaque("herdr", incarnation),
                hierarchy=hierarchy,
                command=agent_kind or "",
                title=_bounded(pane.get("title") or pane.get("terminal_title_stripped"), 500),
                cwd=_bounded(pane.get("foreground_cwd") or pane.get("cwd"), 4096),
                window=_bounded(tab.get("label"), 160),
                native_revision=revision_text,
                native_agent=native,
                capabilities=capabilities,
                provider_metadata={
                    "terminal_id": terminal_id,
                    "workspace_id": str(workspace_id),
                    "tab_id": str(tab_id),
                },
            ))
        self._public_by_incarnation = next_public
        return tuple(endpoints)

    def discover(self) -> DiscoveryResult:
        try:
            self.probe()
            endpoints = self._parse_snapshot(self._snapshot())
        except ProviderError as exc:
            self._health = ProviderHealth(
                status="degraded",
                last_success_at=self._health.last_success_at,
                last_error=exc.category,
                protocol=self._health.protocol,
                version=self._health.version,
                events_supported=self._health.events_supported,
            )
            return DiscoveryResult(self.health, tuple(self._endpoints.values()), authoritative=False)
        self._endpoints = {endpoint.public_id: endpoint for endpoint in endpoints}
        self._health = ProviderHealth(
            status="ready",
            last_success_at=time.time(),
            protocol=self._health.protocol,
            version=self._health.version,
            events_supported=self._health.events_supported,
        )
        return DiscoveryResult(self.health, endpoints, authoritative=True)

    def _read(self, pane_id: str, *, source: str, lines: int) -> str:
        request_lines = max(200, min(2000, int(lines)))
        text = self._run([
            "pane", "read", pane_id,
            "--source", source,
            "--lines", str(request_lines),
            "--format", "text",
        ], timeout=8.0)
        split = text.splitlines()
        if len(split) > lines:
            split = split[-lines:]
        return "\n".join(split)

    def capture(self, endpoint: EndpointSnapshot, *, lines: int) -> CaptureResult:
        # Herdr 0.8.2's pane revision is a route/layout revision: terminal input
        # and output do not advance it. Always recapture so monitor state cannot
        # become permanently stale.
        return CaptureResult(
            text=self._read(endpoint.ref.native_endpoint_id, source="recent-unwrapped", lines=lines),
            detection_text=self._read(endpoint.ref.native_endpoint_id, source="detection", lines=200),
            native_revision=endpoint.native_revision,
        )

    def resolve(self, public_id: str) -> Optional[EndpointSnapshot]:
        # Unlike tmux, native-looking Herdr IDs never fall back to direct use.
        return self._endpoints.get(public_id)

    @staticmethod
    def _same_route(left: EndpointSnapshot, right: EndpointSnapshot) -> bool:
        keys = ("terminal_id", "workspace_id", "tab_id")
        return bool(
            left.ref.scope == right.ref.scope
            and left.ref.native_endpoint_id == right.ref.native_endpoint_id
            and left.ref.incarnation == right.ref.incarnation
            and all(left.provider_metadata.get(key) == right.provider_metadata.get(key) for key in keys)
        )

    def _fresh_expected(self, expected: EndpointSnapshot) -> EndpointSnapshot:
        endpoints = self._parse_snapshot(self._snapshot())
        matches = [endpoint for endpoint in endpoints if endpoint.ref.incarnation == expected.ref.incarnation]
        if len(matches) != 1 or not self._same_route(matches[0], expected):
            raise ProviderError("endpoint identity changed", category="endpoint_moved")
        fresh = matches[0]
        # Keep the exact public handle that was resolved before validation.
        return EndpointSnapshot(
            **{**fresh.__dict__, "public_id": expected.public_id, "persistent_target": expected.persistent_target}
        )

    def revalidate(self, public_id: str, expected: EndpointSnapshot) -> VerifiedEndpoint:
        self.probe()
        current = self.resolve(public_id)
        if current is None or current != expected or self.health.status != "ready":
            raise ProviderError("endpoint is stale", category="endpoint_stale")
        first = self._fresh_expected(expected)
        if first.native_revision != expected.native_revision:
            raise ProviderError("endpoint revision changed", category="revision_stale")
        detection = self._read(first.ref.native_endpoint_id, source="detection", lines=200)
        second = self._fresh_expected(first)
        if second.native_revision != first.native_revision:
            raise ProviderError("endpoint changed during validation", category="revision_stale")
        return VerifiedEndpoint(endpoint=second, detection_text=detection)

    def send_literal(self, endpoint: EndpointSnapshot, text: str) -> None:
        # Herdr writes control bytes literally to the PTY. In particular a
        # newline submits the current shell/agent buffer, violating vmux's
        # separate text/Enter contract. Reject all terminal controls here.
        if any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ProviderError("control characters are unavailable for Herdr text", category="capability_unavailable")
        self._run(
            ["pane", "send-text", endpoint.ref.native_endpoint_id, text],
            timeout=8.0,
            mutation=True,
        )

    def send_key(self, endpoint: EndpointSnapshot, key: str) -> None:
        native = HERDR_KEY_MAP.get(key)
        if native is None:
            raise ProviderError("key is not available for Herdr", category="capability_unavailable")
        self._run(
            ["pane", "send-keys", endpoint.ref.native_endpoint_id, native],
            timeout=8.0,
            mutation=True,
        )

    def send_menu_key(self, endpoint: EndpointSnapshot, key: str) -> None:
        if not isinstance(key, str) or len(key) != 1 or not key.isprintable() or key.isspace():
            raise ProviderError("menu key is not safe", category="capability_unavailable")
        self._run(
            ["pane", "send-keys", endpoint.ref.native_endpoint_id, key],
            timeout=8.0,
            mutation=True,
        )

    def configure_events(self, endpoint_ids: Sequence[str], wake) -> None:
        if not self.health.events_supported or self.events_mode != "auto":
            return
        if self._event_subscriber is None:
            self._event_subscriber = HerdrEventSubscriber(self._socket_path)
        self._event_subscriber.update(endpoint_ids, wake)

    def capability(self) -> dict:
        health = self.health
        return {
            "version": 1,
            "provider": "herdr",
            "mode": "monitor_respond",
            "guarded_input": True,
            "native_agent_state": True,
            "creation": False,
            "deletion": False,
            "event_subscription": {
                "supported": health.events_supported,
                "active": health.events_active,
                "minimum_protocol": MIN_PROTOCOL,
            },
            "health": health.to_dict(),
        }

    def close(self) -> None:
        if self._event_subscriber is not None:
            self._event_subscriber.close()

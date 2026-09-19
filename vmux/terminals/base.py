"""Provider-neutral terminal discovery and action contracts.

Providers own native identifiers and subprocess/socket details.  The rest of vmux
only receives normalized endpoint snapshots and opaque public action handles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence


@dataclass(frozen=True)
class EndpointRef:
    provider: str
    scope: str
    native_endpoint_id: str
    incarnation: str


@dataclass(frozen=True)
class HierarchyNode:
    kind: str
    opaque_id: str
    label: str
    position: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.opaque_id,
            "label": self.label,
            "position": self.position,
        }


@dataclass(frozen=True)
class NativeAgentState:
    present: bool
    kind: Optional[str]
    name: Optional[str]
    status: str
    state_change_seq: Optional[int] = None
    interactive_ready: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "present": self.present,
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "state_change_seq": self.state_change_seq,
            "interactive_ready": self.interactive_ready,
        }


@dataclass(frozen=True)
class EndpointCapabilities:
    capture: bool = True
    input: str = "legacy"
    literal_text: bool = True
    menu_select: bool = True
    keys: tuple[str, ...] = ()
    broadcast: bool = True
    create: bool = False
    delete: bool = False
    native_agent_state: bool = False

    def to_dict(self) -> dict:
        return {
            "capture": self.capture,
            "input": self.input,
            "literal_text": self.literal_text,
            "menu_select": self.menu_select,
            "keys": list(self.keys),
            "broadcast": self.broadcast,
            "create": self.create,
            "delete": self.delete,
            "native_agent_state": self.native_agent_state,
        }


@dataclass(frozen=True)
class EndpointSnapshot:
    ref: EndpointRef
    public_id: str
    persistent_target: str
    hierarchy: tuple[HierarchyNode, ...]
    command: str = ""
    title: str = ""
    cwd: str = ""
    window: str = ""
    native_revision: str = ""
    native_agent: Optional[NativeAgentState] = None
    capabilities: EndpointCapabilities = field(default_factory=EndpointCapabilities)
    provider_metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureResult:
    text: str
    native_revision: str = ""
    truncated: bool = False
    detection_text: Optional[str] = None


@dataclass(frozen=True)
class ProviderHealth:
    status: str
    last_success_at: float = 0.0
    last_error: Optional[str] = None
    protocol: Optional[int] = None
    version: Optional[str] = None
    events_supported: bool = False
    events_active: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "protocol": self.protocol,
            "version": self.version,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    health: ProviderHealth
    endpoints: tuple[EndpointSnapshot, ...]
    authoritative: bool = True


@dataclass(frozen=True)
class VerifiedEndpoint:
    endpoint: EndpointSnapshot
    detection_text: str


class ProviderError(RuntimeError):
    """A bounded provider failure safe to classify without exposing stderr."""

    def __init__(self, message: str, *, category: str = "provider_error"):
        super().__init__(message)
        self.category = category


class TerminalProvider:
    name = "unknown"

    def probe(self) -> ProviderHealth:
        raise NotImplementedError

    def discover(self) -> DiscoveryResult:
        raise NotImplementedError

    def capture(self, endpoint: EndpointSnapshot, *, lines: int) -> CaptureResult:
        raise NotImplementedError

    def resolve(self, public_id: str) -> Optional[EndpointSnapshot]:
        raise NotImplementedError

    def revalidate(self, public_id: str, expected: EndpointSnapshot) -> VerifiedEndpoint:
        raise ProviderError("guarded input is unavailable", category="capability_unavailable")

    def send_literal(self, endpoint: EndpointSnapshot, text: str) -> None:
        raise ProviderError("literal input is unavailable", category="capability_unavailable")

    def send_key(self, endpoint: EndpointSnapshot, key: str) -> None:
        raise ProviderError("key input is unavailable", category="capability_unavailable")

    def send_menu_key(self, endpoint: EndpointSnapshot, key: str) -> None:
        raise ProviderError("menu input is unavailable", category="capability_unavailable")

    def configure_events(self, endpoint_ids: Sequence[str], wake) -> None:
        """Update optional wake-only event subscriptions."""

    def close(self) -> None:
        """Close client-side resources. Providers must not stop terminal servers."""

    def capability(self) -> Dict[str, Any]:
        health = self.probe()
        return {
            "version": 1,
            "provider": self.name,
            "mode": "monitor_respond",
            "guarded_input": self.name == "herdr",
            "native_agent_state": self.name == "herdr",
            "creation": self.name == "tmux",
            "deletion": False,
            "event_subscription": {
                "supported": health.events_supported,
                "active": health.events_active,
                "minimum_protocol": 20 if self.name == "herdr" else None,
            },
            "health": health.to_dict(),
        }

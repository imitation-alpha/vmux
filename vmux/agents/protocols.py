"""Adapter interfaces for read-only observation and guarded runtime control."""

from __future__ import annotations

from typing import List, Optional, Protocol

from .models import PaneObservation, ReadResult, RuntimeCandidate


class RuntimeObserver(Protocol):
    runtime: str
    parser_version: str

    def matches(self, pane: PaneObservation) -> bool: ...

    def discover(self, pane: PaneObservation) -> List[RuntimeCandidate]: ...

    def read(self, candidate: RuntimeCandidate, offset: int, inode: Optional[int]) -> ReadResult: ...


class RuntimeController(Protocol):
    runtime: str

    def send_message(self, pane_id: str, text: str) -> None: ...

    def reply(self, pane_id: str, key: str, runtime: str) -> None: ...

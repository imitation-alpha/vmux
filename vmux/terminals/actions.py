"""Provider-neutral terminal actions and guarded Herdr response validation."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict, defaultdict
from dataclasses import replace
from typing import Any, Optional

from .. import tmux
from ..detectors import detect
from ..models import KIND_CLAUDE, KIND_CODEX, MenuOption
from .base import EndpointSnapshot, ProviderError, TerminalProvider


def _digest(prefix: str, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return prefix + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def prompt_fingerprint(text: str, question: Optional[str]) -> str:
    if question:
        basis = " ".join(question.split())
    else:
        lines = [" ".join(line.split()) for line in (text or "").splitlines() if line.strip()]
        basis = "\n".join(lines[-20:])
    return _digest("sha256:", basis)


def prepare_guard(text: str, question: Optional[str], menu: list[MenuOption]) -> tuple[str, str, list[MenuOption]]:
    prompt = prompt_fingerprint(text, question)
    canonical = [
        {
            "index": index,
            "key": option.key,
            "label": " ".join(option.label.split()),
            "description": " ".join(option.description.split()),
            "freeform": option.freeform,
        }
        for index, option in enumerate(menu)
    ]
    options = _digest("sha256:", canonical)
    guarded = [
        replace(option, id=_digest("o:", {"prompt": prompt, "options": options, **item}))
        for option, item in zip(menu, canonical)
    ]
    return prompt, options, guarded


class ActionProblem(RuntimeError):
    def __init__(self, status_code: int, reason: str, *, current: Optional[dict] = None):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.current = current

    def detail(self) -> dict:
        payload = {"reason": self.reason}
        if self.current is not None:
            payload["current"] = self.current
        return payload


class TerminalActionService:
    def __init__(self, hub, provider: TerminalProvider):
        self.hub = hub
        self.provider = provider
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._idempotency: OrderedDict[str, tuple[str, dict | ActionProblem]] = OrderedDict()
        self._idempotency_lock = threading.Lock()

    def _endpoint(self, pane_id: str, *, legacy: bool = False) -> EndpointSnapshot:
        endpoint = self.provider.resolve(pane_id)
        if endpoint is None:
            raise ActionProblem(404, "unknown_pane")
        if legacy and endpoint.ref.provider != "tmux":
            raise ActionProblem(409, "guarded_input_required")
        return endpoint

    # -- unchanged tmux compatibility routes ----------------------------- #
    def legacy_key(self, pane_id: str, key: str) -> None:
        endpoint = self._endpoint(pane_id, legacy=True)
        try:
            self.provider.send_key(endpoint, key)
        except (ProviderError, tmux.TmuxError) as exc:
            raise ActionProblem(400, str(exc)) from exc
        self.hub.mark_interaction(endpoint.public_id)

    def legacy_text(self, pane_id: str, text: str, enter: bool = False) -> None:
        endpoint = self._endpoint(pane_id, legacy=True)
        try:
            # Keep the exact historical tmux call shape, including text+Enter.
            tmux.send_literal(endpoint.ref.native_endpoint_id, text, enter=enter)
        except tmux.TmuxError as exc:
            raise ActionProblem(400, str(exc)) from exc
        self.hub.mark_interaction(endpoint.public_id)

    def legacy_select(self, pane_id: str, key: str) -> None:
        endpoint = self._endpoint(pane_id, legacy=True)
        state = self.hub.states.get(pane_id)
        kind = state.kind if state else "generic"
        native = endpoint.ref.native_endpoint_id
        try:
            if kind == KIND_CLAUDE:
                tmux.send_chars(native, key)
            elif kind == KIND_CODEX:
                option = next((item for item in (state.menu if state else []) if item.key == key), None)
                if key == "enter":
                    tmux.send_key(native, "Enter")
                elif option is not None and option.freeform:
                    tmux.send_chars(native, key)
                else:
                    tmux.send_chars(native, key)
                    tmux.send_key(native, "Enter")
            elif key == "enter":
                tmux.send_key(native, "Enter")
            else:
                tmux.send_literal(native, key, enter=True)
        except tmux.TmuxError as exc:
            raise ActionProblem(400, str(exc)) from exc
        self.hub.mark_interaction(endpoint.public_id)

    # -- guarded provider input ------------------------------------------ #
    @staticmethod
    def _signature(payload: dict) -> str:
        return _digest("request:", payload)

    def _idempotent(self, key: str, signature: str, execute) -> dict:
        with self._idempotency_lock:
            prior = self._idempotency.get(key)
            if prior is not None:
                old_signature, outcome = prior
                if old_signature != signature:
                    raise ActionProblem(409, "idempotency_key_reused")
                if isinstance(outcome, ActionProblem):
                    raise ActionProblem(outcome.status_code, outcome.reason, current=outcome.current)
                return dict(outcome)
            # Reserve before any I/O. The per-endpoint lock prevents another
            # thread from observing this temporary marker for the same action.
            self._idempotency[key] = (signature, ActionProblem(409, "request_in_progress"))
            while len(self._idempotency) > 1024:
                self._idempotency.popitem(last=False)
        try:
            result = execute()
        except ActionProblem as exc:
            with self._idempotency_lock:
                self._idempotency[key] = (signature, exc)
            raise
        with self._idempotency_lock:
            self._idempotency[key] = (signature, dict(result))
        return result

    def guarded_input(
        self,
        *,
        pane_id: str,
        operation: str,
        expected: dict,
        idempotency_key: str,
        text: Optional[str] = None,
        key: Optional[str] = None,
        option_id: Optional[str] = None,
        enter: bool = False,
    ) -> dict:
        payload = {
            "id": pane_id,
            "operation": operation,
            "expected": expected,
            "text": text,
            "key": key,
            "option_id": option_id,
            "enter": enter,
        }
        signature = self._signature(payload)
        lock = self._locks[pane_id]
        with lock:
            return self._idempotent(
                idempotency_key,
                signature,
                lambda: self._guarded_once(
                    pane_id=pane_id,
                    operation=operation,
                    expected=expected,
                    text=text,
                    key=key,
                    option_id=option_id,
                    enter=enter,
                ),
            )

    def _guarded_once(
        self,
        *,
        pane_id: str,
        operation: str,
        expected: dict,
        text: Optional[str],
        key: Optional[str],
        option_id: Optional[str],
        enter: bool,
    ) -> dict:
        endpoint = self._endpoint(pane_id)
        if endpoint.ref.provider != "herdr" or endpoint.capabilities.input != "guarded_v1":
            raise ActionProblem(409, "capability_unavailable")
        state = self.hub.states.get(pane_id)
        if state is None or state.stale or not state.actionable:
            raise ActionProblem(409, "endpoint_stale")
        current_guard = state.action_guard or {}
        if expected.get("endpoint_revision") != current_guard.get("endpoint_revision"):
            raise ActionProblem(409, "revision_stale", current=current_guard)
        prompt_guarded = operation in ("text", "select") or (operation == "key" and key == "Enter")
        if prompt_guarded and expected.get("prompt_fingerprint") != current_guard.get("prompt_fingerprint"):
            raise ActionProblem(409, "prompt_changed", current=current_guard)
        if operation == "select" and expected.get("options_fingerprint") != current_guard.get("options_fingerprint"):
            raise ActionProblem(409, "options_changed", current=current_guard)
        try:
            verified = self.provider.revalidate(pane_id, endpoint)
        except ProviderError as exc:
            raise ActionProblem(409, exc.category) from exc
        fresh = verified.endpoint
        if fresh.native_revision != expected.get("endpoint_revision"):
            raise ActionProblem(409, "revision_stale")
        result = detect(verified.detection_text, state.kind, False, self.hub.cfg, fresh.title)
        prompt, options, menu = prepare_guard(
            verified.detection_text,
            result.question,
            result.menu_list(),
        )
        if prompt_guarded and prompt != expected.get("prompt_fingerprint"):
            raise ActionProblem(409, "prompt_changed")
        if operation == "select" and options != expected.get("options_fingerprint"):
            raise ActionProblem(409, "options_changed")

        try:
            if operation == "key":
                if key not in fresh.capabilities.keys:
                    raise ActionProblem(409, "capability_unavailable")
                self.provider.send_key(fresh, str(key))
            elif operation == "text":
                value = text if isinstance(text, str) else ""
                if not value and not enter:
                    raise ActionProblem(400, "text_required")
                if value:
                    self.provider.send_literal(fresh, value)
                if enter:
                    try:
                        self.provider.send_key(fresh, "Enter")
                    except ProviderError as exc:
                        raise ActionProblem(409, "partial_delivery_unknown") from exc
            elif operation == "select":
                option = next((item for item in menu if item.id == option_id), None)
                if option is None:
                    raise ActionProblem(409, "options_changed")
                if state.kind == KIND_CLAUDE or option.freeform:
                    self.provider.send_menu_key(fresh, option.key)
                elif state.kind == KIND_CODEX:
                    self.provider.send_menu_key(fresh, option.key)
                    try:
                        self.provider.send_key(fresh, "Enter")
                    except ProviderError as exc:
                        raise ActionProblem(409, "partial_delivery_unknown") from exc
                elif option.key == "enter":
                    self.provider.send_key(fresh, "Enter")
                else:
                    self.provider.send_literal(fresh, option.key)
                    try:
                        self.provider.send_key(fresh, "Enter")
                    except ProviderError as exc:
                        raise ActionProblem(409, "partial_delivery_unknown") from exc
            else:
                raise ActionProblem(400, "bad_operation")
        except ActionProblem:
            raise
        except ProviderError as exc:
            if exc.category == "delivery_unknown":
                raise ActionProblem(409, "delivery_unknown") from exc
            raise ActionProblem(409, exc.category) from exc
        self.hub.mark_interaction(pane_id)
        self.hub.kick()
        return {"ok": True, "delivery": "accepted"}

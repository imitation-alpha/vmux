"""Guarded terminal input and legacy-route separation."""

from __future__ import annotations

import pytest

from vmux.config import Config
from vmux.detectors import detect
from vmux.models import KIND_CLAUDE, KIND_CODEX, PaneState
from vmux.terminals.actions import ActionProblem, TerminalActionService, prepare_guard
from vmux.terminals.base import (
    EndpointCapabilities,
    EndpointRef,
    EndpointSnapshot,
    ProviderError,
    ProviderHealth,
    TerminalProvider,
    VerifiedEndpoint,
)

DIALOG = """\
╭────────────────────────────────────────╮
│ Do you want to continue?               │
│ ❯ 1. Yes                               │
│   2. No, and tell Claude what to do    │
╰────────────────────────────────────────╯
"""


class FakeHerdr(TerminalProvider):
    name = "herdr"

    def __init__(self):
        self.endpoint = EndpointSnapshot(
            ref=EndpointRef("herdr", "named", "w1:p1", "named\0t1\0w1:p1\0w1\0w1:t1"),
            public_id="h:opaque",
            persistent_target="herdr:persistent",
            hierarchy=(),
            title="Claude",
            native_revision="9",
            capabilities=EndpointCapabilities(
                input="guarded_v1", keys=("Enter", "Escape", "C-c"), broadcast=False,
            ),
            provider_metadata={"terminal_id": "t1", "workspace_id": "w1", "tab_id": "w1:t1"},
        )
        self.sent = []
        self.text = DIALOG
        self.fail_enter = False

    def probe(self):
        return ProviderHealth(status="ready")

    def resolve(self, public_id):
        return self.endpoint if public_id == self.endpoint.public_id else None

    def revalidate(self, public_id, expected):
        assert public_id == self.endpoint.public_id
        assert expected == self.endpoint
        return VerifiedEndpoint(self.endpoint, self.text)

    def send_literal(self, endpoint, text):
        self.sent.append(("text", endpoint.ref.native_endpoint_id, text))

    def send_key(self, endpoint, key):
        if self.fail_enter and key == "Enter":
            raise ProviderError("uncertain", category="delivery_unknown")
        self.sent.append(("key", endpoint.ref.native_endpoint_id, key))

    def send_menu_key(self, endpoint, key):
        self.sent.append(("menu", endpoint.ref.native_endpoint_id, key))


class FakeHub:
    def __init__(self, provider):
        self.cfg = Config()
        self.states = {}
        self.provider = provider
        self.interactions = []
        self.kicks = 0

    def mark_interaction(self, pane_id):
        self.interactions.append(pane_id)

    def kick(self):
        self.kicks += 1


def guarded_service():
    provider = FakeHerdr()
    hub = FakeHub(provider)
    result = detect(DIALOG, KIND_CLAUDE, False, hub.cfg, "Claude")
    prompt, options, menu = prepare_guard(DIALOG, result.question, result.menu_list())
    hub.states[provider.endpoint.public_id] = PaneState(
        id=provider.endpoint.public_id,
        target=provider.endpoint.persistent_target,
        name="Claude",
        kind=KIND_CLAUDE,
        status="needs_input",
        menu=menu,
        provider="herdr",
        capabilities=provider.endpoint.capabilities.to_dict(),
        action_guard={
            "endpoint_revision": provider.endpoint.native_revision,
            "prompt_fingerprint": prompt,
            "options_fingerprint": options,
        },
        actionable=True,
    )
    return TerminalActionService(hub, provider), hub, provider


def expected(state):
    return dict(state.action_guard)


def test_guarded_select_reparses_exact_option_and_idempotency_never_resends():
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    option = state.menu[0]
    payload = dict(
        pane_id=state.id,
        operation="select",
        expected=expected(state),
        idempotency_key="request-1",
        option_id=option.id,
    )

    first = service.guarded_input(**payload)
    second = service.guarded_input(**payload)

    assert first == second == {"ok": True, "delivery": "accepted"}
    assert provider.sent == [("menu", "w1:p1", "1")]
    assert hub.interactions == ["h:opaque"]
    assert hub.kicks == 1


def test_guarded_input_rejects_stale_revision_prompt_and_option_without_send():
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    option = state.menu[0]
    cases = [
        ({**expected(state), "endpoint_revision": "8"}, "revision_stale"),
        ({**expected(state), "prompt_fingerprint": "sha256:old"}, "prompt_changed"),
        ({**expected(state), "options_fingerprint": "sha256:old"}, "options_changed"),
    ]
    for index, (guard, reason) in enumerate(cases):
        with pytest.raises(ActionProblem) as caught:
            service.guarded_input(
                pane_id=state.id,
                operation="select",
                expected=guard,
                idempotency_key=f"stale-{index}",
                option_id=option.id,
            )
        assert caught.value.reason == reason
    assert provider.sent == []


def test_guarded_text_reports_partial_delivery_and_repeat_does_not_retype():
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    provider.fail_enter = True
    payload = dict(
        pane_id=state.id,
        operation="text",
        expected=expected(state),
        idempotency_key="partial-1",
        text="continue",
        enter=True,
    )

    for _ in range(2):
        with pytest.raises(ActionProblem) as caught:
            service.guarded_input(**payload)
        assert caught.value.reason == "partial_delivery_unknown"

    assert provider.sent == [("text", "w1:p1", "continue")]


def test_legacy_route_cannot_drive_herdr_and_idempotency_key_cannot_change_request():
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    with pytest.raises(ActionProblem) as caught:
        service.legacy_text(state.id, "unsafe", True)
    assert caught.value.reason == "guarded_input_required"

    service.guarded_input(
        pane_id=state.id,
        operation="key",
        expected=expected(state),
        idempotency_key="same-key",
        key="Escape",
    )
    with pytest.raises(ActionProblem) as caught:
        service.guarded_input(
            pane_id=state.id,
            operation="key",
            expected=expected(state),
            idempotency_key="same-key",
            key="C-c",
        )
    assert caught.value.reason == "idempotency_key_reused"


def test_enter_key_requires_current_prompt_but_interrupt_key_does_not():
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    route_only = {"endpoint_revision": state.action_guard["endpoint_revision"]}
    with pytest.raises(ActionProblem) as caught:
        service.guarded_input(
            pane_id=state.id,
            operation="key",
            expected=route_only,
            idempotency_key="enter-without-prompt",
            key="Enter",
        )
    assert caught.value.reason == "prompt_changed"

    accepted = service.guarded_input(
        pane_id=state.id,
        operation="key",
        expected=route_only,
        idempotency_key="interrupt-route-only",
        key="C-c",
    )
    assert accepted["delivery"] == "accepted"


def test_stale_or_unknown_endpoint_is_never_actionable():
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    state.stale = True
    with pytest.raises(ActionProblem) as caught:
        service.guarded_input(
            pane_id=state.id,
            operation="key",
            expected=expected(state),
            idempotency_key="stale-pane",
            key="Escape",
        )
    assert caught.value.reason == "endpoint_stale"

    with pytest.raises(ActionProblem) as caught:
        service.guarded_input(
            pane_id="h:unknown",
            operation="key",
            expected={"endpoint_revision": "9"},
            idempotency_key="unknown-pane",
            key="Escape",
        )
    assert caught.value.status_code == 404


@pytest.mark.parametrize("operation,values", [
    ("key", {"key": "Enter"}),
    ("text", {"text": "answer", "enter": True}),
    ("select", {}),
])
@pytest.mark.parametrize("change", ["label", "selection", "cached"])
def test_submitting_selection_revalidates_options(operation, values, change):
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    guard = expected(state)
    if change == "label":
        provider.text = DIALOG.replace("1. Yes", "1. Delete")
    elif change == "selection":
        provider.text = DIALOG.replace("❯ 1.", "  1.").replace("  2.", "❯ 2.")
    else:
        guard["options_fingerprint"] = "sha256:old"
    with pytest.raises(ActionProblem) as caught:
        service.guarded_input(
            pane_id=state.id, operation=operation, expected=guard,
            idempotency_key="changed-options", option_id=state.menu[0].id, **values,
        )
    assert caught.value.reason == "options_changed"
    assert provider.sent == []


@pytest.mark.parametrize("before,after", [
    ("rm 'a b'", "rm 'a  b'"),
    ("first\n\nsecond", "first\nsecond"),
    ("run\tcommand", "run command"),
])
def test_lossless_terminal_evidence_rejects_changed_prompt(before, after):
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    provider.text = before + "\n" + DIALOG
    result = detect(provider.text, state.kind, False, hub.cfg, "Claude")
    prompt, options, _ = prepare_guard(provider.text, result.question, result.menu_list())
    state.action_guard.update(prompt_fingerprint=prompt, options_fingerprint=options)
    provider.text = after + "\n" + DIALOG
    with pytest.raises(ActionProblem) as caught:
        service.guarded_input(
            pane_id=state.id, operation="text", expected=expected(state),
            idempotency_key="changed-whitespace", text="continue", enter=True,
        )
    assert caught.value.reason == "prompt_changed"
    assert provider.sent == []


def test_codex_continue_option_sends_only_enter():
    service, hub, provider = guarded_service()
    state = hub.states[provider.endpoint.public_id]
    state.kind = KIND_CODEX
    provider.text = "Press enter to continue"
    result = detect(provider.text, state.kind, False, hub.cfg)
    prompt, options, menu = prepare_guard(provider.text, result.question, result.menu_list())
    state.action_guard.update(prompt_fingerprint=prompt, options_fingerprint=options)
    assert menu[0].key == "enter"
    service.guarded_input(
        pane_id=state.id, operation="select", expected=expected(state),
        idempotency_key="continue", option_id=menu[0].id,
    )
    assert provider.sent == [("key", "w1:p1", "Enter")]

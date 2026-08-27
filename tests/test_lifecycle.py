import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux.config import Config
from vmux.lifecycle import (
    HISTORY_LIMIT,
    LifecycleConflict,
    LifecycleEvidence,
    LifecycleKernel,
    arbitrate,
    structured_freshness,
)
from vmux.models import PaneState
from vmux.server import create_app


def ev(state, reason="test", authority="terminal_ui", confidence="high", at=100.0):
    return LifecycleEvidence(state, reason, authority, confidence, at)


def test_authority_precedence_and_conflict():
    winner, rejected, conflict = arbitrate([
        ev("working", authority="structured_log", at=99),
        ev("blocked", reason="menu", authority="terminal_ui", at=100),
    ], process_present=True, now=100)
    assert winner.state == "blocked" and rejected and conflict


def test_structured_freshness_thresholds_and_stale_exclusion():
    assert structured_freshness(92, 100) == "fresh"
    assert structured_freshness(91.9, 100) == "aging"
    assert structured_freshness(85, 100) == "aging"
    assert structured_freshness(84.9, 100) == "stale"
    winner, _, _ = arbitrate([
        ev("working", authority="structured_log", at=84), ev("idle", at=100),
    ], process_present=True, now=100)
    assert winner.state == "idle"


def test_missing_process_wins():
    winner, _, _ = arbitrate([ev("working")], process_present=False, now=100)
    assert winner.state == "offline" and winner.authority == "process"


def test_done_latches_acknowledges_and_rejects_race():
    kernel = LifecycleKernel()
    assert kernel.observe("%1", "a", [ev("idle")], now=100).state == "idle"
    kernel.observe("%1", "a", [ev("working", at=101)], now=101)
    done = kernel.observe("%1", "a", [ev("idle", at=102)], now=102)
    assert done.state == "done"
    assert kernel.observe("%1", "a", [ev("working", at=103)], now=103).state == "done"
    with pytest.raises(LifecycleConflict):
        kernel.acknowledge("%1", done.revision - 1, now=104)
    ack = kernel.acknowledge("%1", done.revision, now=104)
    assert ack.state == "idle" and ack.revision == done.revision + 1


def test_incarnation_isolation_history_bound_and_stable_revision():
    kernel = LifecycleKernel()
    old = kernel.observe("%1", "old", [ev("working")], now=100)
    first = kernel.observe("%1", "new", [ev("idle")], now=101)
    same = kernel.observe("%1", "new", [ev("idle", at=102)], now=102)
    assert first.state == "idle" and first.revision > old.revision
    assert first.revision == same.revision
    for index in range(HISTORY_LIMIT + 10):
        state = "working" if index % 2 else "idle"
        kernel.observe("%1", "new", [ev(state, at=103 + index)], now=103 + index)
        if kernel.current("%1").state == "done":
            kernel.acknowledge_current_done("%1", now=103.5 + index)
    assert len(kernel.diagnostics("%1")["history"]) == HISTORY_LIMIT


def test_api_auth_capability_diagnostics_and_acknowledgment_race():
    app = create_app(Config(token="secret"))
    hub = app.state.hub
    hub.states["%1"] = PaneState(id="%1", target="work:1.1", name="private")
    hub.order = ["%1"]
    hub.lifecycle.observe("%1", "a", [ev("working")], now=100)
    done = hub.lifecycle.observe("%1", "a", [ev("idle", at=101)], now=101)
    hub.states["%1"].lifecycle = done.to_dict()
    client = TestClient(app)

    assert client.get("/api/panes/lifecycle?id=%251").status_code == 401
    headers = {"Authorization": "Bearer secret"}
    capability = client.get("/api/config", headers=headers).json()["_info"]["capabilities"]["pane_lifecycle_v1"]
    assert capability == {"version": 1, "history_limit": 32}
    diagnostics = client.get("/api/panes/lifecycle?id=%251&limit=32", headers=headers)
    assert diagnostics.status_code == 200
    assert set(diagnostics.json()) == {"id", "identity", "current", "winning_evidence", "rejected_evidence", "history"}
    assert client.get("/api/panes/lifecycle?id=%2599", headers=headers).status_code == 404

    stale = client.put("/api/panes/lifecycle/acknowledge", headers=headers, json={"id": "%1", "expected_revision": done.revision - 1})
    assert stale.status_code == 409 and stale.json()["current"]["state"] == "done"
    acknowledged = client.put("/api/panes/lifecycle/acknowledge", headers=headers, json={"id": "%1", "expected_revision": done.revision})
    assert acknowledged.status_code == 200 and acknowledged.json()["current"]["state"] == "idle"

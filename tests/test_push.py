"""Tests for the optional APNs push layer: transition detection, cooldown,
device registry persistence, and the no-op guarantees when unconfigured."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from vmux.config import Config
from vmux.models import (
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_NEEDS_INPUT,
    STATUS_WORKING,
    PaneState,
)
from vmux.poller import Hub
from vmux.push import DeviceRegistry, PushManager, collect_alerts, valid_device_token


def pane(pid, status, question=None):
    return PaneState(id=pid, target="w:1.1", name="agent", status=status, question=question)


# -- collect_alerts: the pure transition detector --------------------------- #

def test_alert_on_transition_to_needs_input():
    prev = {"%1": pane("%1", STATUS_WORKING)}
    new = {"%1": pane("%1", STATUS_NEEDS_INPUT, "Allow edit?")}
    last = {}
    got = collect_alerts(prev, new, last, now=1000.0)
    assert [p.id for p in got] == ["%1"]
    assert last["%1"] == 1000.0


def test_no_alert_for_unknown_pane_first_seen():
    # a pane already needing input when vmux starts must not re-alert
    new = {"%1": pane("%1", STATUS_NEEDS_INPUT)}
    assert collect_alerts({}, new, {}, now=1000.0) == []


def test_no_alert_when_status_unchanged():
    prev = {"%1": pane("%1", STATUS_NEEDS_INPUT)}
    new = {"%1": pane("%1", STATUS_NEEDS_INPUT)}
    assert collect_alerts(prev, new, {}, now=1000.0) == []


def test_cooldown_suppresses_flapping():
    last = {}
    prev = {"%1": pane("%1", STATUS_IDLE)}
    new = {"%1": pane("%1", STATUS_NEEDS_INPUT)}
    assert len(collect_alerts(prev, new, last, now=1000.0)) == 1
    # answered, then needs input again 5s later -> still inside the 30s cooldown
    prev2 = {"%1": pane("%1", STATUS_WORKING)}
    assert collect_alerts(prev2, new, last, now=1005.0) == []
    # ...but after the cooldown a new transition alerts again
    assert len(collect_alerts(prev2, new, last, now=1031.0)) == 1


def test_error_alert_gated_by_flag():
    prev = {"%1": pane("%1", STATUS_WORKING)}
    new = {"%1": pane("%1", STATUS_ERROR)}
    assert collect_alerts(prev, new, {}, now=1.0) == []
    got = collect_alerts(prev, new, {}, now=1.0, alert_on_error=True)
    assert [p.status for p in got] == [STATUS_ERROR]


def test_last_alert_pruned_for_vanished_panes():
    last = {"%dead": 5.0}
    collect_alerts({}, {"%1": pane("%1", STATUS_IDLE)}, last, now=10.0)
    assert "%dead" not in last


# -- DeviceRegistry --------------------------------------------------------- #

def test_registry_roundtrip(tmp_path):
    path = str(tmp_path / "push.json")
    reg = DeviceRegistry(path)
    tok = "ab" * 32
    assert reg.add(tok, name="my phone") is True
    assert reg.add(tok, name="renamed") is False     # idempotent, updates name
    assert reg.tokens() == [tok]

    reloaded = DeviceRegistry(path)
    assert reloaded.tokens() == [tok]
    assert reloaded.devices[0]["name"] == "renamed"
    assert reloaded.remove(tok) is True
    assert reloaded.remove(tok) is False
    assert DeviceRegistry(path).tokens() == []


def test_registry_rejects_garbage_tokens(tmp_path):
    reg = DeviceRegistry(str(tmp_path / "push.json"))
    with pytest.raises(ValueError):
        reg.add("not a token!")
    assert not valid_device_token("xyz")
    assert valid_device_token("0123456789abcdef" * 4)


def test_registry_public_list_truncates_tokens(tmp_path):
    reg = DeviceRegistry(str(tmp_path / "push.json"))
    reg.add("ab" * 32, name="phone")
    pub = reg.public_list()
    assert len(pub) == 1 and ("ab" * 32) not in pub[0]["token"]


# -- PushManager / Hub wiring ----------------------------------------------- #

def test_unconfigured_manager_is_inert(tmp_path):
    cfg = Config()
    cfg.push_store_path = str(tmp_path / "push.json")
    mgr = PushManager(cfg)
    assert mgr.configured is False
    assert mgr.can_send is False
    prev = {"%1": pane("%1", STATUS_IDLE)}
    new = {"%1": pane("%1", STATUS_NEEDS_INPUT)}
    assert mgr.collect(prev, new) == []     # disabled -> no transition tracking
    mgr.fire([pane("%1", STATUS_NEEDS_INPUT)])   # must not raise (no loop, no config)
    info = mgr.info()
    assert info["devices"] == 0 and info["enabled"] is False


def test_hub_has_push_manager():
    h = Hub(Config())
    assert h.push.info()["configured"] is False


def test_configured_manager_collects(tmp_path):
    cfg = Config(
        apns_key_path=str(tmp_path / "key.pem"), apns_key_id="K", apns_team_id="T",
        apns_topic="dev.example.vmux",
    )
    cfg.push_store_path = str(tmp_path / "push.json")
    mgr = PushManager(cfg)
    assert mgr.configured is True
    prev = {"%1": pane("%1", STATUS_IDLE)}
    new = {"%1": pane("%1", STATUS_NEEDS_INPUT, "Continue?")}
    if mgr.available:   # httpx + PyJWT installed
        assert [p.id for p in mgr.collect(prev, new)] == ["%1"]
    else:               # deps missing -> collect degrades to inert, never raises
        assert mgr.collect(prev, new) == []


def test_payload_shape(tmp_path):
    cfg = Config(apns_key_path="k", apns_key_id="K", apns_team_id="T", apns_topic="b")
    cfg.push_store_path = str(tmp_path / "push.json")
    mgr = PushManager(cfg)
    p = mgr._payload(pane("%1", STATUS_NEEDS_INPUT, "Allow Bash?"))
    assert p["aps"]["alert"] == {"title": "agent", "body": "Allow Bash?"}
    assert p["aps"]["thread-id"] == "w:1.1"
    assert p["vmux"]["id"] == "%1"
    e = mgr._payload(pane("%2", STATUS_ERROR))
    assert e["aps"]["alert"]["body"] == "Error detected"


def test_provider_jwt_shape(tmp_path):
    pyjwt = pytest.importorskip("jwt")
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    from cryptography.hazmat.primitives import serialization

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_path = tmp_path / "apns-test-key.pem"
    key_path.write_bytes(pem)

    cfg = Config(
        apns_key_path=str(key_path), apns_key_id="KEY123", apns_team_id="TEAM456",
        apns_topic="dev.example.vmux",
    )
    cfg.push_store_path = str(tmp_path / "push.json")
    mgr = PushManager(cfg)
    tok = mgr._provider_jwt()
    assert mgr._provider_jwt() == tok    # cached
    header = pyjwt.get_unverified_header(tok)
    assert header["alg"] == "ES256" and header["kid"] == "KEY123"
    claims = pyjwt.decode(tok, key.public_key(), algorithms=["ES256"])
    assert claims["iss"] == "TEAM456"

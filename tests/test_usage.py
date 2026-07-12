"""Tests for the tokscale usage/quota layer: pure normalizers against real
captured fixtures (contract freeze), the daily summary math, the quota-alert
transition detector, and the collector's no-op/degradation guarantees."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from vmux.config import Config
from vmux.usage import (
    UsageCollector,
    build_daily_summary,
    collect_quota_alerts,
    fmt_reset,
    normalize_graph,
    normalize_hourly,
    normalize_monthly,
    normalize_quota,
    parse_reset,
)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tokscale")

BUCKET_KEYS = {"bucket", "totals", "by_client", "by_model", "clients", "models"}
TOTAL_KEYS = {"input", "output", "cache_read", "cache_write", "reasoning",
              "total", "cost", "messages"}
METRIC_KEYS = {"label", "used_percent", "remaining_percent", "remaining_label",
               "resets_at", "resets_at_raw"}


def fixture(name):
    with open(os.path.join(FIX, name)) as fh:
        return json.load(fh)


# -- parse_reset ------------------------------------------------------------- #

def test_parse_reset_iso_with_offset():
    ts = parse_reset("2026-06-19T11:00:00+00:00")
    assert ts == 1781866800.0


def test_parse_reset_z_suffix():
    assert parse_reset("2026-06-19T11:00:00Z") == parse_reset("2026-06-19T11:00:00+00:00")


def test_parse_reset_bare_date_is_midnight_utc():
    assert parse_reset("2026-07-01") == 1782864000.0


def test_parse_reset_garbage():
    assert parse_reset("not a date") is None
    assert parse_reset("") is None
    assert parse_reset(None) is None


# -- normalize_quota ---------------------------------------------------------- #

def test_quota_fixture_contract():
    quotas = normalize_quota(fixture("usage.json"))
    assert [q["provider"] for q in quotas] == ["Claude", "Codex", "Copilot"]
    for q in quotas:
        assert set(q) == {"provider", "plan", "account", "metrics"}
        for m in q["metrics"]:
            assert set(m) == METRIC_KEYS
    codex = quotas[1]
    assert codex["account"] == "user@example.com"
    assert codex["plan"] == "Pro"


def test_quota_fixture_values():
    quotas = normalize_quota(fixture("usage.json"))
    claude_weekly = quotas[0]["metrics"][1]
    assert claude_weekly["label"] == "Weekly"
    assert claude_weekly["remaining_percent"] == 95.0
    assert claude_weekly["resets_at"] is not None  # full ISO parses
    copilot_chat = quotas[2]["metrics"][0]
    assert copilot_chat["remaining_label"] == "194/200 left"
    assert copilot_chat["resets_at"] == parse_reset("2026-07-01")  # bare date parses
    premium = quotas[2]["metrics"][2]
    assert premium["remaining_percent"] == 0.0


def test_quota_weird_blocks_degrade_independently():
    quotas = normalize_quota(fixture("usage_weird.json"))
    # null block + providerless block dropped; the rest survive
    assert [q["provider"] for q in quotas] == ["NewVendor", "Broken"]
    nv = quotas[0]
    # string percents coerce, the label-less metric is dropped, bad date -> None
    assert len(nv["metrics"]) == 1
    assert nv["metrics"][0]["remaining_percent"] == 88.0
    assert nv["metrics"][0]["resets_at"] is None
    assert nv["metrics"][0]["resets_at_raw"] == "not a date"
    assert quotas[1]["metrics"] == []


def test_quota_empty_and_garbage():
    assert normalize_quota(fixture("empty.json")) == []
    assert normalize_quota(None) == []
    assert normalize_quota({"not": "a list"}) == []


# -- normalize_hourly / monthly / graph ---------------------------------------- #

def test_hourly_fixture_contract():
    buckets = normalize_hourly(fixture("hourly.json"))
    assert len(buckets) == 4
    assert buckets == sorted(buckets, key=lambda b: b["bucket"])
    for b in buckets:
        assert set(b) == BUCKET_KEYS
        assert set(b["totals"]) == TOTAL_KEYS
        assert b["by_client"] == [] and b["by_model"] == []   # hourly has no split
        assert b["totals"]["total"] == sum(
            b["totals"][k] for k in ("input", "output", "cache_read",
                                     "cache_write", "reasoning"))
    assert buckets[0]["bucket"] == "2026-06-12 00:00"
    assert "codex" in buckets[0]["clients"]


def test_monthly_fixture_contract():
    buckets = normalize_monthly(fixture("monthly.json"))
    assert len(buckets) == 4
    for b in buckets:
        assert set(b) == BUCKET_KEYS
        assert set(b["totals"]) == TOTAL_KEYS
        assert b["clients"] == []   # tokscale monthly has no client list
    assert buckets[0]["bucket"] == "2025-09"
    assert buckets[0]["totals"]["messages"] == 2518


def test_graph_fixture_contract_and_aggregation():
    buckets = normalize_graph(fixture("graph.json"))
    assert len(buckets) == 3
    for b in buckets:
        assert set(b) == BUCKET_KEYS
        assert set(b["totals"]) == TOTAL_KEYS
        assert b["by_client"], "daily buckets must carry per-client slices"
        assert b["by_model"], "daily buckets must carry per-model slices"
        # by_client and by_model aggregate the same entries -> identical sums
        assert sum(r["cost"] for r in b["by_client"]) == pytest.approx(
            sum(r["cost"] for r in b["by_model"]))
        assert sum(r["total"] for r in b["by_client"]) == \
            sum(r["total"] for r in b["by_model"])
        assert b["by_client"] == sorted(b["by_client"], key=lambda r: -r["cost"])
        assert b["clients"] == sorted(b["clients"])
    assert buckets[0]["bucket"] == "2026-06-09"


def test_graph_garbage():
    assert normalize_graph(None) == []
    assert normalize_graph({}) == []
    assert normalize_graph({"contributions": "nope"}) == []


# -- build_daily_summary -------------------------------------------------------- #

def _day(d, cost, by_model=None, by_client=None):
    totals = {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0,
              "reasoning": 0, "total": 3, "cost": cost, "messages": 5}
    return {"bucket": d, "totals": totals, "by_client": by_client or [],
            "by_model": by_model or [], "clients": [], "models": []}


def test_daily_summary_delta_math():
    from datetime import date
    models = [{"model": "m%d" % i, "cost": float(10 - i), "total": 1, "messages": 1}
              for i in range(5)]
    buckets = [_day("2026-06-11", 10.0), _day("2026-06-12", 15.0, by_model=models)]
    s = build_daily_summary(buckets, date(2026, 6, 12))
    assert s["totals"]["cost"] == 15.0
    assert s["yesterday"]["cost"] == 10.0
    assert s["cost_delta_pct"] == 50.0
    assert [m["model"] for m in s["top_models"]] == ["m0", "m1", "m2"]   # top 3 only


def test_daily_summary_zero_yesterday_has_null_delta():
    from datetime import date
    buckets = [_day("2026-06-11", 0.0), _day("2026-06-12", 5.0)]
    s = build_daily_summary(buckets, date(2026, 6, 12))
    assert s["cost_delta_pct"] is None


def test_daily_summary_missing_today():
    from datetime import date
    assert build_daily_summary([_day("2026-06-10", 1.0)], date(2026, 6, 12)) is None
    assert build_daily_summary([], date(2026, 6, 12)) is None


def test_daily_summary_no_yesterday():
    from datetime import date
    s = build_daily_summary([_day("2026-06-12", 5.0)], date(2026, 6, 12))
    assert s["yesterday"] is None and s["cost_delta_pct"] is None


# -- collect_quota_alerts --------------------------------------------------------- #

def _quotas(pct, provider="Claude", label="Weekly"):
    return [{"provider": provider, "plan": None, "account": None, "metrics": [{
        "label": label, "used_percent": 100 - pct, "remaining_percent": pct,
        "remaining_label": None, "resets_at": None, "resets_at_raw": None}]}]


def test_alert_fires_on_crossing():
    alerted = {}
    got = collect_quota_alerts(_quotas(35.0), _quotas(15.0), alerted, 1000.0, threshold=20.0)
    assert len(got) == 1
    assert got[0]["provider"] == "Claude" and got[0]["remaining_percent"] == 15.0
    assert alerted["Claude|Weekly"] == 1000.0


def test_no_alert_when_already_low_at_startup():
    # first observation below threshold (vmux restart) must not alert
    assert collect_quota_alerts([], _quotas(10.0), {}, 1000.0, threshold=20.0) == []


def test_no_alert_when_staying_low():
    assert collect_quota_alerts(_quotas(15.0), _quotas(12.0), {}, 1000.0, threshold=20.0) == []


def test_rearm_after_quota_reset():
    alerted = {}
    assert len(collect_quota_alerts(_quotas(30.0), _quotas(10.0), alerted, 1000.0,
                                    threshold=20.0)) == 1
    # quota resets (climbs back above threshold) -> alerted entry cleared
    assert collect_quota_alerts(_quotas(10.0), _quotas(90.0), alerted, 2000.0,
                                threshold=20.0) == []
    assert alerted == {}
    # next exhaustion alerts again
    assert len(collect_quota_alerts(_quotas(90.0), _quotas(5.0), alerted, 3000.0,
                                    threshold=20.0)) == 1


def test_alert_cooldown():
    alerted = {"Claude|Weekly": 1000.0}
    got = collect_quota_alerts(_quotas(30.0), _quotas(10.0), alerted, 1100.0,
                               threshold=20.0, cooldown=3600.0)
    assert got == []  # crossed again but still inside cooldown


def test_threshold_zero_disables():
    assert collect_quota_alerts(_quotas(50.0), _quotas(0.0), {}, 1000.0, threshold=0.0) == []


def test_vanished_metric_cleans_alerted():
    alerted = {"Gone|Weekly": 1000.0}
    collect_quota_alerts([], _quotas(50.0), alerted, 2000.0, threshold=20.0)
    assert "Gone|Weekly" not in alerted


# -- fmt_reset ----------------------------------------------------------------- #

def test_fmt_reset():
    assert fmt_reset(1000.0 + 3 * 3600 + 12 * 60, "raw", now=1000.0) == "resets in 3h 12m"
    assert fmt_reset(1000.0 + 3 * 86400, "raw", now=1000.0) == "resets in 3d"
    assert fmt_reset(None, "2026-07-01") == "resets 2026-07-01"
    assert fmt_reset(None, None) == ""
    assert fmt_reset(500.0, None, now=1000.0) == ""   # already past, no raw fallback


# -- UsageCollector ---------------------------------------------------------------- #

def _collector(**cfg_kw):
    # default the command to a binary that exists everywhere so payload
    # availability doesn't depend on tokscale being installed on the test host
    cfg_kw.setdefault("usage_enabled", True)
    cfg_kw.setdefault("usage_command", sys.executable)
    return UsageCollector(Config(**cfg_kw))


def test_disabled_payload():
    c = _collector(usage_enabled=False)
    p = c.usage_payload()
    assert p["available"] is False and p["reason"] == "disabled"
    assert p["quotas"] == [] and p["today"] is None
    h = c.history_payload("daily")
    assert h["available"] is False and h["buckets"] == []


def test_not_installed_payload_and_zero_spawns(monkeypatch):
    c = _collector(usage_command="definitely-not-a-real-binary-xyz")
    p = c.usage_payload()
    assert p["available"] is False and p["reason"] == "not_installed"
    assert "tokscale not found" in p["detail"] or "install" in p["detail"]

    def boom(*a, **kw):
        raise AssertionError("no subprocess may be spawned when binary is missing")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    asyncio.run(c.refresh("all"))   # must hit the FileNotFoundError path, not boom
    assert c.slots["quota"]["error"]
    assert c.info()["installed"] is False


def test_collector_success_then_failure_keeps_last_good():
    c = _collector()
    raw = {"usage": fixture("usage.json"),
           "hourly": fixture("hourly.json"),
           "monthly": fixture("monthly.json"),
           "graph": fixture("graph.json")}
    calls = {"fail": False}

    async def fake_exec(args, timeout):
        if calls["fail"]:
            raise RuntimeError("tokscale exploded")
        return raw[args[0]]

    c._exec = fake_exec
    asyncio.run(c.refresh("all"))
    p = c.usage_payload()
    assert p["available"] is True and p["stale"] is False
    assert [q["provider"] for q in p["quotas"]] == ["Claude", "Codex", "Copilot"]
    for period, n in (("hourly", 4), ("daily", 3), ("monthly", 4)):
        h = c.history_payload(period)
        assert h["available"] is True and len(h["buckets"]) == n
        if period != "daily":
            assert all(b["by_client"] == [] for b in h["buckets"])

    # now every exec fails: last-good data keeps serving, flagged stale
    calls["fail"] = True
    c.slots["quota"]["fetched_at"] = 0   # age it so refresh re-runs
    c.slots["daily"]["fetched_at"] = 0
    asyncio.run(c.refresh("all"))
    p2 = c.usage_payload()
    assert p2["available"] is True and p2["stale"] is True
    assert [q["provider"] for q in p2["quotas"]] == ["Claude", "Codex", "Copilot"]


def test_collector_error_before_any_data():
    c = _collector()

    async def fake_exec(args, timeout):
        raise TimeoutError("tokscale usage timed out after 30s")

    c._exec = fake_exec
    asyncio.run(c.refresh("all"))
    p = c.usage_payload()
    assert p["available"] is False and p["reason"] == "timeout"


def test_history_days_clamp_and_bad_period():
    c = _collector()
    c.slots["daily"].update(
        data=[_day("2026-06-%02d" % d, 1.0) for d in range(1, 13)], fetched_at=1.0)
    assert len(c.history_payload("daily", days=5)["buckets"]) == 5
    assert len(c.history_payload("daily", days=999)["buckets"]) == 12   # clamp to all
    assert len(c.history_payload("daily", days=0)["buckets"]) == 12     # falsy -> all
    with pytest.raises(ValueError):
        c.history_payload("weekly")


def test_quota_alert_fired_through_push():
    sent = []

    class FakePush:
        def fire_message(self, title, body, *, thread, extra):
            sent.append((title, body, thread, extra))

    cfg = Config(usage_alert_threshold=20.0, usage_command=sys.executable)
    c = UsageCollector(cfg, push=FakePush())
    low = json.loads(json.dumps(fixture("usage.json")))
    low[0]["metrics"][1].update(used_percent=85.0, remaining_percent=15.0)

    feed = {"current": fixture("usage.json")}

    async def fake_exec(args, timeout):
        assert args == ["usage", "--json"]
        return feed["current"]

    c._exec = fake_exec
    asyncio.run(c.refresh_quota())          # baseline: everything healthy
    assert sent == []
    feed["current"] = low
    asyncio.run(c.refresh_quota())          # Claude Weekly drops 95% -> 15%
    assert len(sent) == 1
    title, body, thread, extra = sent[0]
    assert title == "vmux"
    assert body == "Usage needs your attention."
    assert thread == "vmux-usage"
    assert extra == {"type": "quota"}

    encoded = json.dumps(sent[0])
    for private_detail in ("Claude", "Weekly", "15", "95", "reset"):
        assert private_detail not in encoded


def test_refresh_lock_coalesces():
    c = _collector()
    calls = []

    async def fake_exec(args, timeout):
        calls.append(args[0])
        await asyncio.sleep(0)
        return fixture("usage.json") if args[0] == "usage" else {"entries": []}

    c._exec = fake_exec

    async def both():
        await asyncio.gather(c.refresh("quota"), c.refresh("quota"))

    asyncio.run(both())
    assert calls.count("usage") == 1   # second caller saw fresh data and skipped


def test_info_shape():
    c = _collector()
    info = c.info()
    assert set(info) == {"enabled", "installed", "resolved_path",
                         "quota_age", "reports_age", "last_error"}
    assert info["enabled"] is True
    assert info["quota_age"] is None   # nothing fetched yet


# -- endpoints (skipped unless httpx is installed, e.g. via the push extra) ------ #

def test_usage_endpoints():
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from vmux.server import create_app

    cfg = Config(token="sekrit", usage_enabled=False,
                 usage_command="definitely-not-a-real-binary-xyz")
    app = create_app(cfg)
    client = TestClient(app)   # no lifespan: poll/usage loops stay off

    assert client.get("/api/usage").status_code == 401
    auth = {"Authorization": "Bearer sekrit"}
    body = client.get("/api/usage", headers=auth).json()
    assert body["available"] is False and body["reason"] == "disabled"
    assert client.get("/api/usage/history", headers=auth,
                      params={"period": "weekly"}).status_code == 400
    ok = client.get("/api/usage/history", headers=auth,
                    params={"period": "monthly"}).json()
    assert ok["period"] == "monthly" and ok["buckets"] == []
    assert client.post("/api/usage/refresh", headers=auth,
                       json={"scope": "bogus"}).status_code == 400
    refreshed = client.post("/api/usage/refresh", headers=auth,
                            json={"scope": "quota"}).json()
    assert refreshed["available"] is False
    cfg_payload = client.get("/api/config", headers=auth).json()
    assert cfg_payload["_info"]["usage"]["enabled"] is False

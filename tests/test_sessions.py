"""Tests for connected-session tracking + kill on the Hub."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vmux.config import Config
from vmux.poller import Hub


class FakeWS:
    def __init__(self):
        self.closed = False

    async def close(self, code=None):
        self.closed = True


def test_session_tracking():
    h = Hub(Config())
    h.add_client("abc", FakeWS(), "1.2.3.4", "iPhone", 1000.0)
    h.add_client("def", FakeWS(), "5.6.7.8", "Mac", 1001.0)
    s = h.sessions()
    assert {x["id"] for x in s} == {"abc", "def"}
    assert any(x["ip"] == "1.2.3.4" and x["ua"] == "iPhone" for x in s)
    assert all("age" in x for x in s)
    h.remove_client("abc")
    assert {x["id"] for x in h.sessions()} == {"def"}


def test_kill_client():
    h = Hub(Config())
    ws = FakeWS()
    h.add_client("x", ws, "ip", "ua", 1.0)
    assert asyncio.run(h.kill_client("x")) is True
    assert ws.closed is True
    assert h.sessions() == []
    assert asyncio.run(h.kill_client("missing")) is False

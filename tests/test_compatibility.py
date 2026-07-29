"""Compatibility metadata is additive, stable, and read-only over HTTP."""

import asyncio
from importlib.metadata import version

import pytest

from vmux.compatibility import MINIMUM_IOS_VERSION, PROTOCOL_VERSION, compatibility_info
from vmux.config import Config
from vmux.server import create_app


@pytest.fixture
def app():
    return create_app(Config())


def endpoint(app, path, method):
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    )


def test_compatibility_policy_has_expected_wire_shape():
    expected = {
        "protocol_version": 1,
        "minimum_ios_version": "1.0.0",
    }

    assert PROTOCOL_VERSION == 1
    assert MINIMUM_IOS_VERSION == "1.0.0"
    assert compatibility_info() == expected


def test_get_config_advertises_version_and_compatibility(app):
    body = endpoint(app, "/api/config", "GET")()
    info = body["_info"]

    assert info["version"] == version("vmux-agent")
    assert info["compatibility"] == compatibility_info()


def test_patch_config_cannot_override_read_only_compatibility(app):
    forged = {
        "protocol_version": 999,
        "minimum_ios_version": "999.0.0",
    }

    body = asyncio.run(endpoint(app, "/api/config", "PATCH")({
        "poll_interval": 1.25,
        "compatibility": forged,
        "_info": {"version": "forged", "compatibility": forged},
    }))

    assert body["poll_interval"] == 1.25
    assert body["_info"]["version"] == version("vmux-agent")
    assert body["_info"]["compatibility"] == compatibility_info()
    assert "compatibility" not in Config().editable_dict()


def test_compatibility_payload_is_fresh():
    first = compatibility_info()
    first["protocol_version"] = 999

    assert compatibility_info()["protocol_version"] == PROTOCOL_VERSION

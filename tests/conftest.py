"""Repository-wide pytest options shared by optional browser suites."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("vmux web")
    group.addoption(
        "--update-web-baselines",
        action="store_true",
        help="replace checked-in vmux web screenshot baselines",
    )

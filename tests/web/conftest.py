"""Shared server and browser fixtures for vmux web tests."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
import uvicorn

from .fixture_app import create_fixture_app

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = Path(__file__).resolve().parent / "baselines"
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"


@dataclass
class FixtureServer:
    url: str

    def reset(self) -> None:
        httpx = pytest.importorskip("httpx")
        response = httpx.post(f"{self.url}/__test__/reset", timeout=5)
        response.raise_for_status()

    def scenario(self, name: str) -> None:
        httpx = pytest.importorskip("httpx")
        response = httpx.post(
            f"{self.url}/__test__/scenario",
            json={"name": name},
            timeout=5,
        )
        response.raise_for_status()

    def action_requests(self) -> list[dict[str, Any]]:
        httpx = pytest.importorskip("httpx")
        response = httpx.get(f"{self.url}/__test__/requests", timeout=5)
        response.raise_for_status()
        return response.json()["requests"]

    def set_panes(self, panes: list[dict[str, Any]]) -> None:
        httpx = pytest.importorskip("httpx")
        response = httpx.post(f"{self.url}/__test__/panes", json={"panes": panes}, timeout=5)
        response.raise_for_status()

@pytest.fixture(scope="session")
def fixture_server() -> Iterator[FixtureServer]:
    httpx = pytest.importorskip("httpx")
    app = create_fixture_app()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    host, port = listener.getsockname()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        lifespan="off",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="vmux-web-fixture",
        daemon=True,
    )
    thread.start()
    url = f"http://{host}:{port}"
    deadline = time.monotonic() + 10
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/__test__/health", timeout=0.5)
            if response.status_code == 200:
                break
        except Exception as exc:  # pragma: no cover - only exercised on startup failure
            last_error = exc
        time.sleep(0.05)
    else:  # pragma: no cover - diagnostic path
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail(f"fixture server did not start: {last_error}")

    yield FixtureServer(url)
    server.should_exit = True
    thread.join(timeout=5)
    listener.close()


@pytest.fixture(autouse=True)
def reset_fixture_state(fixture_server: FixtureServer) -> Iterator[None]:
    fixture_server.reset()
    yield


@pytest.fixture(scope="session")
def playwright_runtime():
    sync_api = pytest.importorskip(
        "playwright.sync_api",
        reason="install the web-test dependency group to run browser tests",
    )
    with sync_api.sync_playwright() as runtime:
        yield runtime


def _browser_names() -> list[str]:
    raw = os.environ.get("VMUX_WEB_BROWSERS", "chromium,webkit")
    names = [name.strip().lower() for name in raw.split(",") if name.strip()]
    unsupported = set(names) - {"chromium", "webkit"}
    if unsupported:
        raise pytest.UsageError(f"unsupported VMUX_WEB_BROWSERS value: {sorted(unsupported)}")
    return names or ["chromium", "webkit"]


@dataclass
class BrowserRuntime:
    name: str
    browser: Any


def _launch_browser(playwright_runtime: Any, name: str, pytestconfig: pytest.Config) -> Any:
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        return getattr(playwright_runtime, name).launch(headless=True)
    except sync_api.Error as exc:
        install = f"uv run --group web-test playwright install {name}"
        message = f"Playwright {name} is unavailable; run `{install}`. ({exc})"
        missing_binary = "Executable doesn't exist" in str(exc) or "playwright install" in str(exc)
        if missing_binary and not os.environ.get("CI"):
            pytest.skip(message)
        pytest.fail(message)


@pytest.fixture(scope="session", params=_browser_names(), ids=lambda name: name)
def browser_runtime(request: pytest.FixtureRequest, playwright_runtime: Any, pytestconfig: pytest.Config):
    name = str(request.param)
    browser = _launch_browser(playwright_runtime, name, pytestconfig)
    yield BrowserRuntime(name=name, browser=browser)
    browser.close()


@pytest.fixture(scope="session")
def chromium_runtime(playwright_runtime: Any, pytestconfig: pytest.Config):
    browser = _launch_browser(playwright_runtime, "chromium", pytestconfig)
    yield BrowserRuntime(name="chromium", browser=browser)
    browser.close()


DEFAULT_PREFS = {
    "version": 2,
    "theme": "light",
    "glass": True,
    "ambient": False,
    "notify": False,
    "sound": False,
    "alertErrors": False,
    "defaultFilter": "queue",
    "sort": "status",
    "terminalWrap": False,
}


def _init_script(prefs: dict[str, Any]) -> str:
    serialized = json.dumps(prefs, sort_keys=True).replace("<", "\\u003c")
    return f"""
        (() => {{
          const realNow = Date.now.bind(Date);
          const started = realNow();
          Date.now = () => 1784044800000 + (realNow() - started);
          localStorage.setItem("vmux_prefs", {json.dumps(serialized)});
        }})();
    """


@pytest.fixture
def page_factory() -> Callable[..., Any]:
    contexts = []
    page_errors: list[str] = []

    def make_page(
        runtime: BrowserRuntime,
        *,
        viewport: tuple[int, int] = (1280, 800),
        prefs: dict[str, Any] | None = None,
        touch: bool = False,
        service_workers: str = "block",
    ):
        context = runtime.browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            color_scheme="light",
            reduced_motion="reduce",
            locale="en-US",
            timezone_id="UTC",
            device_scale_factor=1,
            has_touch=touch,
            service_workers=service_workers,
        )
        contexts.append(context)
        chosen = {**DEFAULT_PREFS, **(prefs or {})}
        context.add_init_script(_init_script(chosen))
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(error.stack or str(error)))
        return page

    yield make_page
    for context in reversed(contexts):
        context.close()
    if page_errors:
        pytest.fail("uncaught browser errors:\n" + "\n".join(page_errors))

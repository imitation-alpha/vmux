"""Pinned-Chromium visual regression checks for vmux responsive layouts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from .conftest import ARTIFACT_DIR, BASELINE_DIR, BrowserRuntime, FixtureServer
from .test_functional import open_app, wait_for_connection

PIXEL_DIFFERENCE_LIMIT = 0.005
ANTIALIAS_CHANNEL_TOLERANCE = 24
UPDATE_COMMAND = (
    "uv run --group web-test pytest tests/web/test_visual.py "
    "--update-web-baselines"
)


@dataclass(frozen=True)
class VisualCase:
    name: str
    width: int
    height: int
    shell: str
    touch: bool = False


CASES = [
    VisualCase("compact-390x844", 390, 844, ".compact-shell"),
    VisualCase("medium-touch-1024x768", 1024, 768, ".medium-shell", touch=True),
    VisualCase("wide-1440x960", 1440, 960, ".wide-shell"),
    VisualCase("compact-reflow-320x568", 320, 568, ".compact-shell"),
]


def compare_images(actual_path: Path, baseline_path: Path, diff_path: Path) -> tuple[int, int, float]:
    image_module = pytest.importorskip("PIL.Image")
    image_chops = pytest.importorskip("PIL.ImageChops")
    actual = image_module.open(actual_path).convert("RGBA")
    baseline = image_module.open(baseline_path).convert("RGBA")
    if actual.size != baseline.size:
        pytest.fail(
            f"visual size changed for {baseline_path.name}: "
            f"expected {baseline.size}, got {actual.size}"
        )

    raw_diff = image_chops.difference(actual, baseline)
    changed = sum(
        1
        for pixel in raw_diff.get_flattened_data()
        if max(pixel[0], pixel[1], pixel[2]) > ANTIALIAS_CHANNEL_TOLERANCE
    )
    total = actual.width * actual.height
    ratio = changed / total
    if ratio > PIXEL_DIFFERENCE_LIMIT:
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        # Brighten the diff so small layout changes remain obvious in CI artifacts.
        raw_diff.convert("RGB").point(lambda value: min(255, value * 4)).save(diff_path)
    return changed, total, ratio


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_responsive_visual_baseline(
    case: VisualCase,
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
    pytestconfig: pytest.Config,
) -> None:
    page = page_factory(
        chromium_runtime,
        viewport=(case.width, case.height),
        touch=case.touch,
        service_workers="allow",
    )
    open_app(page, fixture_server)
    wait_for_connection(page)
    page.locator(case.shell).wait_for()
    page.wait_for_function(
        """() => [...document.querySelectorAll('button')].some((button) =>
          button.textContent.includes('Stats') &&
          ['2'].includes((button.querySelector('.tab-count, b')?.textContent || '').trim()))"""
    )
    page.evaluate(
        "navigator.serviceWorker ? navigator.serviceWorker.ready : Promise.resolve()"
    )
    page.add_style_tag(
        content=(
            "*,*::before,*::after{animation:none!important;transition:none!important;"
            "caret-color:transparent!important}"
        )
    )
    page.evaluate("document.fonts && document.fonts.ready")
    page.locator("svg use").first.wait_for(state="attached")

    if case.width == 320:
        reflow = page.evaluate(
            "({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
        )
        assert reflow["scroll"] <= reflow["client"]

    baseline_path = BASELINE_DIR / f"{case.name}.png"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    actual_path = ARTIFACT_DIR / f"{case.name}-actual.png"
    page.screenshot(path=actual_path, full_page=False, animations="disabled")

    # The option is registered in tests/conftest.py; the defensive default
    # also keeps this helper safe when imported outside normal pytest startup.
    if pytestconfig.getoption("--update-web-baselines", default=False):
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_bytes(actual_path.read_bytes())
        return
    if not baseline_path.is_file():
        pytest.fail(f"missing {baseline_path}; review and create it with `{UPDATE_COMMAND}`")

    diff_path = ARTIFACT_DIR / f"{case.name}-diff.png"
    changed, total, ratio = compare_images(actual_path, baseline_path, diff_path)
    assert ratio <= PIXEL_DIFFERENCE_LIMIT, (
        f"{case.name} changed by {ratio:.3%} ({changed}/{total} pixels), above the "
        f"{PIXEL_DIFFERENCE_LIMIT:.1%} limit with channel tolerance "
        f"{ANTIALIAS_CHANNEL_TOLERANCE}; inspect {actual_path} and {diff_path}, then "
        f"update only after review with `{UPDATE_COMMAND}`"
    )

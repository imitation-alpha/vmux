# vmux browser tests

The suite serves the real no-build web assets from a deterministic FastAPI
fixture and exercises the same-origin REST and WebSocket paths in Chromium and
WebKit.

```bash
uv sync --locked --group dev --group web-test
uv run playwright install chromium webkit
uv run pytest tests/web/test_functional.py
uv run pytest tests/web/test_visual.py
```

Visual comparisons use Playwright 1.61.0's pinned Chromium, Pillow 12.3.0,
a per-channel anti-aliasing tolerance of 24, and fail when more than 0.5% of
pixels differ. Baselines are platform-specific; CI and reviewed baseline
updates run on macOS 26 arm64.

Update screenshots only after reviewing the current UI at every target size:

```bash
uv run --group web-test pytest tests/web/test_visual.py --update-web-baselines
```

If a Playwright browser is not installed, local runs skip that engine with the
install command in the reason. CI sets `CI=1`, turning a missing browser into a
hard failure. Failure screenshots and amplified diffs are written to
`tests/web/artifacts/` and uploaded by the browser job.

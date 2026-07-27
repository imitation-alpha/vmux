"""Security and lifecycle coverage for private image uploads."""

from __future__ import annotations

import asyncio
import os
import re
import stat
import time
from pathlib import Path

import pytest
from starlette.requests import ClientDisconnect

from vmux.config import Config
from vmux.images import (
    ImageStorageUnavailable,
    ImageStore,
    UnsupportedImage,
    UploadQuotaExceeded,
)
from vmux.server import create_app

SAMPLES = {
    "image/png": b"\x89PNG\r\n\x1a\n" + b"png-payload",
    "image/jpeg": b"\xff\xd8\xff\xe0" + b"jpeg-payload",
    "image/webp": b"RIFF\x08\x00\x00\x00WEBP" + b"webp-payload",
    "image/gif": b"GIF89a" + b"gif-payload",
}


async def chunks(payload: bytes, width: int = 3):
    for offset in range(0, len(payload), width):
        await asyncio.sleep(0)
        yield payload[offset : offset + width]


def store_image(store: ImageStore, payload: bytes, mime_type: str, content_length=None):
    async def run():
        return await store.store(
            chunks(payload),
            mime_type,
            content_length=content_length,
        )

    return asyncio.run(run())


@pytest.mark.parametrize("mime_type", list(SAMPLES))
def test_supported_signatures_stream_to_private_opaque_files(tmp_path, mime_type):
    root = tmp_path / "directory with spaces" / "uploads"
    store = ImageStore(root)
    result = store_image(store, SAMPLES[mime_type], mime_type)
    path = Path(result.path)

    assert result.mime_type == mime_type
    assert result.size == len(SAMPLES[mime_type])
    assert path.is_absolute() and path.parent == root.resolve()
    assert path.read_bytes() == SAMPLES[mime_type]
    assert re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|webp|gif)", path.name)
    assert result.id not in path.name
    assert result.terminal_text.startswith("'") and result.terminal_text.endswith("'")
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(root.glob("*.part")) and not list(root.glob(".*.part"))


@pytest.mark.parametrize(
    ("payload", "mime_type"),
    [
        (b"", "image/png"),
        (b"not an image", "image/png"),
        (b"\x89PNG\r", "image/png"),
        (SAMPLES["image/jpeg"], "image/png"),
        (SAMPLES["image/png"], "application/octet-stream"),
        (SAMPLES["image/png"], "image/heic"),
    ],
)
def test_empty_malformed_spoofed_and_unsupported_content_is_rejected(tmp_path, payload, mime_type):
    store = ImageStore(tmp_path / "uploads")
    with pytest.raises(UnsupportedImage):
        store_image(store, payload, mime_type)
    if store.root.exists():
        assert list(store.root.iterdir()) == []


def test_stream_limits_quota_reclamation_and_expiry(tmp_path):
    now = [10_000.0]
    root = tmp_path / "uploads"
    store = ImageStore(
        root,
        max_image_bytes=32,
        max_upload_bytes=30,
        retention_seconds=60,
        clock=lambda: now[0],
    )

    first = store_image(store, SAMPLES["image/png"], "image/png")
    with pytest.raises(UploadQuotaExceeded):
        store_image(store, SAMPLES["image/png"], "image/png", content_length=None)
    assert len(list(root.iterdir())) == 1

    os.utime(first.path, (now[0] - 61, now[0] - 61))
    second = store_image(store, SAMPLES["image/png"], "image/png", content_length=None)
    assert not Path(first.path).exists()
    assert Path(second.path).exists()
    assert second.expires_at == int(now[0] + 60)

    oversized = SAMPLES["image/png"] + (b"x" * 40)
    from vmux.images import ImageTooLarge

    size_store = ImageStore(
        tmp_path / "size-uploads",
        max_image_bytes=32,
        max_upload_bytes=100,
    )
    with pytest.raises(ImageTooLarge):
        store_image(size_store, oversized, "image/png", content_length=None)
    assert not list(size_store.root.glob(".*.part"))


def test_interrupted_stream_removes_the_partial_file(tmp_path):
    root = tmp_path / "uploads"
    store = ImageStore(root)

    async def interrupted():
        yield SAMPLES["image/png"][:8]
        raise ClientDisconnect

    async def run():
        with pytest.raises(ClientDisconnect):
            await store.store(interrupted(), "image/png")

    asyncio.run(run())
    assert root.exists() and list(root.iterdir()) == []


def test_upload_root_symlink_is_rejected(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    root = tmp_path / "uploads"
    try:
        root.symlink_to(target, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - platforms without symlink support
        pytest.skip(str(exc))
    store = ImageStore(root)
    with pytest.raises(ImageStorageUnavailable):
        store_image(store, SAMPLES["image/png"], "image/png")
    assert list(target.iterdir()) == []


def test_failed_atomic_finalization_removes_the_private_file(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    store = ImageStore(root)

    def fail_replace(source, destination):
        raise OSError("fixture rename failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(ImageStorageUnavailable):
        store_image(store, SAMPLES["image/png"], "image/png")
    assert root.exists() and list(root.iterdir()) == []


def test_endpoint_authenticates_before_processing_and_never_serves_uploads(tmp_path):
    httpx = pytest.importorskip("httpx")
    assert httpx
    from fastapi.testclient import TestClient

    root = tmp_path / "uploads"
    store = ImageStore(root)
    app = create_app(Config(token="secret"), image_store=store)
    client = TestClient(app)

    denied = client.post(
        "/api/images",
        content=SAMPLES["image/png"],
        headers={"Content-Type": "image/png"},
    )
    assert denied.status_code == 401
    assert not root.exists()

    response = client.post(
        "/api/images",
        content=SAMPLES["image/png"],
        headers={"Authorization": "Bearer secret", "Content-Type": "image/png"},
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert set(response.json()) == {
        "id", "path", "terminal_text", "mime_type", "size", "expires_at"
    }
    uploaded = Path(response.json()["path"])
    assert uploaded.exists()
    assert client.get(f"/{uploaded.name}", headers={"Accept": "image/png"}).status_code == 404


def test_endpoint_has_bounded_statuses_for_size_type_and_quota(tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    headers = {"Authorization": "Bearer secret", "Content-Type": "image/png"}
    size_app = create_app(
        Config(token="secret"),
        image_store=ImageStore(tmp_path / "size", max_image_bytes=8),
    )
    too_large = TestClient(size_app).post("/api/images", content=SAMPLES["image/png"], headers=headers)
    assert too_large.status_code == 413
    assert too_large.json() == {"detail": "image exceeds the 20 MiB limit"}

    type_app = create_app(Config(token="secret"), image_store=ImageStore(tmp_path / "type"))
    unsupported = TestClient(type_app).post("/api/images", content=b"not-image", headers=headers)
    assert unsupported.status_code == 415
    assert "PNG, JPEG, WebP, and GIF" in unsupported.json()["detail"]

    quota_app = create_app(
        Config(token="secret"),
        image_store=ImageStore(tmp_path / "quota", max_image_bytes=100, max_upload_bytes=8),
    )
    quota = TestClient(quota_app).post("/api/images", content=SAMPLES["image/png"], headers=headers)
    assert quota.status_code == 507
    assert quota.json() == {"detail": "temporary image upload quota exceeded"}
    assert str(tmp_path) not in quota.text


def test_lifespan_cleans_at_startup_and_on_the_hourly_schedule(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    class CountingStore:
        def __init__(self):
            self.cleanups = 0

        async def cleanup_expired(self):
            self.cleanups += 1
            return 0

    images = CountingStore()
    app = create_app(Config(), image_store=images)

    async def idle_hub():
        await asyncio.Event().wait()

    monkeypatch.setattr(app.state.hub, "run", idle_hub)
    monkeypatch.setattr("vmux.server.CLEANUP_INTERVAL_SECONDS", 0.01)
    with TestClient(app):
        deadline = time.monotonic() + 1
        while images.cleanups < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert images.cleanups >= 2

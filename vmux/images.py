"""Private, short-lived image uploads shared by vmux clients.

Uploads are intentionally filesystem paths rather than served resources.  The
tmux process and vmux run on the same host, so a client can insert the returned
shell-safe path through the existing literal-text controls without making the
image available over HTTP.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterable, Callable, Optional

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
RETENTION_SECONDS = 24 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 60 * 60

SUPPORTED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class ImageUploadError(Exception):
    """Base class for errors safe to translate to bounded HTTP responses."""


class ImageTooLarge(ImageUploadError):
    pass


class UnsupportedImage(ImageUploadError):
    pass


class UploadQuotaExceeded(ImageUploadError):
    pass


class ImageStorageUnavailable(ImageUploadError):
    pass


@dataclass(frozen=True)
class StoredImage:
    id: str
    path: str
    terminal_text: str
    mime_type: str
    size: int
    expires_at: int

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "terminal_text": self.terminal_text,
            "mime_type": self.mime_type,
            "size": self.size,
            "expires_at": self.expires_at,
        }


def normalized_mime_type(value: Optional[str]) -> str:
    """Return a canonical media type without trusting parameters or casing."""

    return (value or "").split(";", 1)[0].strip().lower()


def detect_image_type(header: bytes) -> Optional[str]:
    """Identify one of the supported formats from its required file signature."""

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


class ImageStore:
    """Stream, validate, expire, and quota private image files."""

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        max_image_bytes: int = MAX_IMAGE_BYTES,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        retention_seconds: int = RETENTION_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = (root or (Path.home() / ".vmux" / "uploads")).expanduser().absolute()
        self.max_image_bytes = int(max_image_bytes)
        self.max_upload_bytes = int(max_upload_bytes)
        self.retention_seconds = int(retention_seconds)
        self.clock = clock
        self._lock = asyncio.Lock()

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_stat = self.root.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise OSError("upload root is not a directory")
        # mkdir respects umask and an existing directory may have broader
        # permissions, so always converge on the documented private mode.
        os.chmod(self.root, 0o700, follow_symlinks=False)

    def _regular_files(self):
        with os.scandir(self.root) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    yield entry

    def _cleanup_expired_unlocked(self, now: float) -> int:
        self._ensure_root()
        cutoff = now - self.retention_seconds
        removed = 0
        for entry in list(self._regular_files()):
            try:
                if entry.stat(follow_symlinks=False).st_mtime <= cutoff:
                    os.unlink(entry.path)
                    removed += 1
            except FileNotFoundError:
                continue
        return removed

    def _usage_unlocked(self) -> int:
        total = 0
        for entry in self._regular_files():
            try:
                total += entry.stat(follow_symlinks=False).st_size
            except FileNotFoundError:
                continue
        return total

    async def cleanup_expired(self) -> int:
        """Remove files at or beyond the retention boundary."""

        async with self._lock:
            try:
                return self._cleanup_expired_unlocked(self.clock())
            except OSError as exc:
                raise ImageStorageUnavailable from exc

    async def store(
        self,
        chunks: AsyncIterable[bytes],
        declared_mime_type: Optional[str],
        *,
        content_length: Optional[int] = None,
    ) -> StoredImage:
        """Stream one authenticated request into a validated atomic file."""

        declared = normalized_mime_type(declared_mime_type)
        if declared not in SUPPORTED_IMAGE_TYPES:
            raise UnsupportedImage
        if content_length is not None:
            if content_length > self.max_image_bytes:
                raise ImageTooLarge
            if content_length < 0:
                content_length = None

        async with self._lock:
            temporary: Optional[Path] = None
            descriptor: Optional[int] = None
            try:
                now = self.clock()
                self._cleanup_expired_unlocked(now)
                used = self._usage_unlocked()
                if content_length is not None and used + content_length > self.max_upload_bytes:
                    raise UploadQuotaExceeded

                image_id = str(uuid.uuid4())
                temporary = self.root / f".{image_id}.part"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(temporary, flags, 0o600)

                header = bytearray()
                size = 0
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = None
                    for_signature = 16
                    async for chunk in chunks:
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > self.max_image_bytes:
                            raise ImageTooLarge
                        if used + size > self.max_upload_bytes:
                            raise UploadQuotaExceeded
                        if len(header) < for_signature:
                            header.extend(chunk[: for_signature - len(header)])
                        output.write(chunk)
                    if size == 0:
                        raise UnsupportedImage

                    detected = detect_image_type(bytes(header))
                    if detected is None or detected != declared:
                        raise UnsupportedImage
                    output.flush()
                    os.fsync(output.fileno())

                # Apply final metadata while the name is still private.  The
                # rename is the final fallible step so an error never leaves a
                # published file behind after returning a storage failure.
                temporary.chmod(0o600)
                os.utime(temporary, (now, now), follow_symlinks=False)
                final_path = self.root / f"{image_id.replace('-', '')}{SUPPORTED_IMAGE_TYPES[detected]}"
                os.replace(temporary, final_path)
                temporary = None
                absolute_path = str(final_path)
                return StoredImage(
                    id=image_id,
                    path=absolute_path,
                    terminal_text=shlex.quote(absolute_path),
                    mime_type=detected,
                    size=size,
                    expires_at=int(now + self.retention_seconds),
                )
            except ImageUploadError:
                raise
            except OSError as exc:
                raise ImageStorageUnavailable from exc
            finally:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

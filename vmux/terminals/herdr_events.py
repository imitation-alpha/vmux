"""Optional wake-only Herdr event subscriber.

Frames from this socket never become vmux state and can never trigger input.
They only ask the normal snapshot poller to reconcile sooner.
"""

from __future__ import annotations

import json
import os
import random
import socket
import stat
import threading
import uuid
from typing import Callable, Sequence

_MAX_FRAME = 256 * 1024


class HerdrEventSubscriber:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self._ids: tuple[str, ...] = ()
        self._wake: Callable[[], None] = lambda: None
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.active = False
        self.last_error: str | None = None

    @staticmethod
    def validate_socket(path: str) -> bool:
        if not path or not os.path.isabs(path):
            return False
        try:
            info = os.stat(path)
        except OSError:
            return False
        return bool(
            stat.S_ISSOCK(info.st_mode)
            and info.st_uid == os.getuid()
            and bool(info.st_mode & stat.S_IWUSR)
            and not (stat.S_IMODE(info.st_mode) & 0o077)
        )

    def update(self, pane_ids: Sequence[str], wake: Callable[[], None]) -> None:
        ids = tuple(sorted({value for value in pane_ids if isinstance(value, str) and value}))
        with self._lock:
            changed = ids != self._ids
            self._ids = ids
            self._wake = wake
            if changed:
                self._changed.set()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="vmux-herdr-events", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._changed.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.active = False

    def _run(self) -> None:
        delay = 0.25
        while not self._stop.is_set():
            with self._lock:
                pane_ids = self._ids
                # This stream is being built for exactly this captured set.
                # A later update sets the flag again and forces reconnect.
                self._changed.clear()
            if not pane_ids:
                self.active = False
                self._changed.wait(1.0)
                continue
            if not self.validate_socket(self.socket_path):
                self.active = False
                self.last_error = "socket_invalid"
                self._stop.wait(min(10.0, delay + random.random() * delay))
                delay = min(10.0, delay * 2)
                continue
            try:
                self._stream(pane_ids)
                delay = 0.25
            except (OSError, ValueError, json.JSONDecodeError):
                self.active = False
                self.last_error = "stream_error"
                self._stop.wait(min(10.0, delay + random.random() * delay))
                delay = min(10.0, delay * 2)

    def _stream(self, pane_ids: tuple[str, ...]) -> None:
        request_id = "vmux-" + uuid.uuid4().hex
        request = {
            "id": request_id,
            "method": "events.subscribe",
            "params": {
                "subscriptions": [
                    {"type": "pane.agent_status_changed", "pane_id": pane_id}
                    for pane_id in pane_ids
                ]
            },
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5.0)
            client.connect(self.socket_path)
            client.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            buffer = b""
            while b"\n" not in buffer:
                chunk = client.recv(65536)
                if not chunk:
                    raise OSError("event stream closed before acknowledgement")
                buffer += chunk
                if len(buffer) > _MAX_FRAME:
                    raise ValueError("bad acknowledgement")
            line, buffer = buffer.split(b"\n", 1)
            ack = json.loads(line)
            if (
                ack.get("id") != request_id
                or not isinstance(ack.get("result"), dict)
                or ack["result"].get("type") != "subscription_started"
            ):
                raise ValueError("bad acknowledgement")
            self.active = True
            self.last_error = None
            client.settimeout(1.0)
            while not self._stop.is_set() and not self._changed.is_set():
                if b"\n" not in buffer:
                    try:
                        chunk = client.recv(65536)
                    except (TimeoutError, socket.timeout):
                        continue
                    if not chunk:
                        raise OSError("event stream closed")
                    buffer += chunk
                    if len(buffer) > _MAX_FRAME:
                        raise ValueError("event frame too large")
                    continue
                line, buffer = buffer.split(b"\n", 1)
                if len(line) > _MAX_FRAME:
                    raise ValueError("event frame too large")
                envelope = json.loads(line)
                if envelope.get("event") != "pane.agent_status_changed":
                    continue
                data = envelope.get("data")
                if not isinstance(data, dict) or data.get("pane_id") not in pane_ids:
                    continue
                with self._lock:
                    wake = self._wake
                wake()
            self._changed.clear()
            self.active = False

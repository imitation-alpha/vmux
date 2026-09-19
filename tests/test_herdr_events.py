"""Wake-only Herdr socket event reader."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid

from vmux.terminals.herdr_events import HerdrEventSubscriber


def short_socket_path(label):
    return f"/tmp/vmux-{label}-{uuid.uuid4().hex[:8]}.sock"


def unix_server(path, *, bad_ack=False):
    ready = threading.Event()
    request = {}

    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            os.chmod(path, 0o600)
            listener.listen(1)
            ready.set()
            client, _ = listener.accept()
            with client:
                file = client.makefile("rwb", buffering=0)
                request.update(json.loads(file.readline()))
                ack = {
                    "id": "wrong" if bad_ack else request["id"],
                    "result": {"type": "subscription_started"},
                }
                encoded = json.dumps(ack).encode() + b"\n"
                client.sendall(encoded[:5])
                client.sendall(encoded[5:])
                if not bad_ack:
                    event = {
                        "event": "pane.agent_status_changed",
                        "data": {"pane_id": "w1:p1", "workspace_id": "w1", "agent_status": "blocked"},
                    }
                    client.sendall(json.dumps(event).encode() + b"\n")
                time.sleep(0.1)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(2)
    return thread, request


def test_event_subscriber_validates_ack_and_only_wakes():
    path = short_socket_path("event")
    server, request = unix_server(path)
    woke = threading.Event()
    subscriber = HerdrEventSubscriber(str(path))
    assert subscriber.validate_socket(str(path)) is True

    subscriber.update(["w1:p1"], woke.set)

    assert woke.wait(3)
    subscriber.close()
    server.join(timeout=2)
    assert request["method"] == "events.subscribe"
    assert request["params"]["subscriptions"] == [
        {"type": "pane.agent_status_changed", "pane_id": "w1:p1"}
    ]
    os.unlink(path)


def test_bad_ack_never_wakes_and_insecure_socket_is_rejected():
    path = short_socket_path("bad")
    server, _ = unix_server(path, bad_ack=True)
    woke = threading.Event()
    subscriber = HerdrEventSubscriber(str(path))
    subscriber.update(["w1:p1"], woke.set)
    server.join(timeout=2)
    time.sleep(0.2)
    subscriber.close()
    assert woke.is_set() is False
    assert subscriber.active is False

    os.unlink(path)
    other = short_socket_path("writable")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(other))
        os.chmod(other, 0o620)
        assert HerdrEventSubscriber.validate_socket(str(other)) is False
    finally:
        sock.close()
        os.unlink(other)

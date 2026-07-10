from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from conftest import fixture_frame
from groop.daemon import FrameBroker, serve_unix_socket
from groop.model import Frame


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


def _request(socket_path: Path, payload: dict[str, object]) -> list[dict[str, object]]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(socket_path))
        client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        client.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def test_daemon_socket_serves_current_and_stream_frames(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(100.0), _frame_at(105.0), _frame_at(110.0)], history_size=10)
    socket_path = tmp_path / "groop.sock"
    server = serve_unix_socket(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # current() returns the latest frame produced by the background
        # producer — all three frames have been consumed by now.
        current = _request(socket_path, {"op": "current"})
        assert current[0]["type"] == "frame"
        assert current[0]["frame"]["ts"] == 110.0
        assert current[1] == {"type": "end", "count": 1, "next_cursor": 2}

        # stream with cursor=None returns the tail of history.
        stream = _request(socket_path, {"op": "stream", "limit": 10})
        timestamps = [item["frame"]["ts"] for item in stream if item["type"] == "frame"]
        assert timestamps == [100.0, 105.0, 110.0]
        assert stream[-1] == {
            "type": "end",
            "count": 3,
            "gap": False,
            "oldest_seq": 0,
            "latest_seq": 2,
            "next_cursor": 2,
        }
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_socket_rejects_unknown_and_does_not_offer_file_or_command_ops(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(100.0)])
    socket_path = tmp_path / "groop.sock"
    server = serve_unix_socket(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _request(socket_path, {"op": "read_file", "path": "/etc/shadow"}) == [{"type": "error", "error": "unsupported operation"}]
        assert _request(socket_path, {"op": "exec", "argv": ["id"]}) == [{"type": "error", "error": "unsupported operation"}]
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_socket_permissions_are_group_read_write(tmp_path: Path) -> None:
    server = serve_unix_socket(tmp_path / "groop.sock", FrameBroker([_frame_at(100.0)]), mode=0o660)
    try:
        assert (tmp_path / "groop.sock").stat().st_mode & 0o777 == 0o660
    finally:
        server.server_close()

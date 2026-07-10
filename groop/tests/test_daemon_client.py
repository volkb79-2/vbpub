from __future__ import annotations

import json
import socketserver
import threading
from pathlib import Path

import pytest

from conftest import fixture_frame
from groop.daemon import FrameBroker, serve_unix_socket
from groop.daemon.client import DaemonClient, DaemonProtocolError, DaemonResponseError
from groop.model import Frame, frame_to_jsonable


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


def _serve_lines(socket_path: Path, lines: list[bytes]) -> socketserver.UnixStreamServer:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.rfile.readline(1024 * 1024)
            for line in lines:
                self.wfile.write(line if line.endswith(b"\n") else line + b"\n")

    server = socketserver.UnixStreamServer(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_daemon_client_current_and_stream_parsing(tmp_path: Path) -> None:
    frames = [_frame_at(100.0), _frame_at(105.0), _frame_at(110.0)]
    socket_path = tmp_path / "groop.sock"
    server = serve_unix_socket(socket_path, FrameBroker(frames, history_size=10))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = DaemonClient(socket_path)
        # current() returns the latest frame from the background producer
        assert frame_to_jsonable(client.current_frame()) == frame_to_jsonable(frames[2])
        # stream without cursor returns the tail of history
        assert [frame_to_jsonable(frame) for frame in client.stream_frames(limit=10)] == [
            frame_to_jsonable(frames[0]),
            frame_to_jsonable(frames[1]),
            frame_to_jsonable(frames[2]),
        ]
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_client_reports_error_response(tmp_path: Path) -> None:
    socket_path = tmp_path / "groop.sock"
    server = _serve_lines(socket_path, [json.dumps({"type": "error", "error": "denied"}).encode("utf-8")])
    try:
        with pytest.raises(DaemonResponseError, match="denied"):
            DaemonClient(socket_path).current_frame()
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_client_rejects_malformed_json_and_missing_end(tmp_path: Path) -> None:
    malformed = tmp_path / "bad-json.sock"
    malformed_server = _serve_lines(malformed, [b"not-json"])
    missing_end = tmp_path / "missing-end.sock"
    frame = _frame_at(100.0)
    missing_end_server = _serve_lines(
        missing_end,
        [json.dumps({"type": "frame", "frame": frame_to_jsonable(frame)}).encode("utf-8")],
    )
    try:
        with pytest.raises(DaemonProtocolError, match="malformed JSON"):
            DaemonClient(malformed).current_frame()
        with pytest.raises(DaemonProtocolError, match="without an end response"):
            DaemonClient(missing_end).current_frame()
    finally:
        malformed_server.shutdown()
        malformed_server.server_close()
        missing_end_server.shutdown()
        missing_end_server.server_close()

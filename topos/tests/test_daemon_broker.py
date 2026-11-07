from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon import FrameBroker, serve_unix_socket
from topos.daemon.broker import BrokerUnixServer
from topos.model import Frame


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
    socket_path = tmp_path / "topos.sock"
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
        end = dict(stream[-1])
        assert isinstance(end.pop("epoch"), str)  # per-start daemon identity (B-04)
        assert end == {
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
    socket_path = tmp_path / "topos.sock"
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
    server = serve_unix_socket(tmp_path / "topos.sock", FrameBroker([_frame_at(100.0)]), mode=0o660)
    try:
        assert (tmp_path / "topos.sock").stat().st_mode & 0o777 == 0o660
    finally:
        server.server_close()


def test_failed_unix_bind_leaves_the_existing_socket_node_untouched(tmp_path: Path) -> None:
    socket_path = tmp_path / "owned.sock"
    winner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    winner.bind(str(socket_path))
    winner.listen()
    before = socket_path.lstat()
    try:
        with pytest.raises(OSError):
            BrokerUnixServer(socket_path, FrameBroker([]), owned_path=socket_path)
        after = socket_path.lstat()
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        assert socket_path.is_socket()
    finally:
        winner.close()
        socket_path.unlink(missing_ok=True)


def test_server_close_preserves_a_replacement_socket_node(tmp_path: Path) -> None:
    socket_path = tmp_path / "owned.sock"
    server = BrokerUnixServer(socket_path, FrameBroker([]), owned_path=socket_path)
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        socket_path.unlink()
        replacement.bind(str(socket_path))
        replacement.listen()
        replacement_node = socket_path.lstat()

        server.server_close()

        after = socket_path.lstat()
        assert (after.st_dev, after.st_ino) == (replacement_node.st_dev, replacement_node.st_ino)
        assert socket_path.is_socket()
    finally:
        server.server_close()
        replacement.close()
        socket_path.unlink(missing_ok=True)


def test_server_without_owned_path_never_unlinks_its_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "unowned.sock"
    server = BrokerUnixServer(socket_path, FrameBroker([]))
    try:
        assert server.owned_path is None
        server.server_close()
        assert socket_path.is_socket()
    finally:
        socket_path.unlink(missing_ok=True)


def test_unreadable_node_after_bind_closes_the_server_and_claims_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "owned.sock"
    original_lstat = Path.lstat

    def lstat(path: Path, *args, **kwargs):
        if path == socket_path:
            raise PermissionError("lstat denied")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(PermissionError, match="lstat denied"):
        BrokerUnixServer(socket_path, FrameBroker([]), owned_path=socket_path)
    monkeypatch.undo()
    # Ownership was never recorded, so the bound node is left in place.
    assert socket_path.is_socket()
    socket_path.unlink()


def test_server_close_tolerates_an_already_removed_owned_node(tmp_path: Path) -> None:
    socket_path = tmp_path / "owned.sock"
    server = BrokerUnixServer(socket_path, FrameBroker([]), owned_path=socket_path)
    socket_path.unlink()
    server.server_close()
    assert not socket_path.exists()

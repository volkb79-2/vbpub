"""Dedicated observer socket is read-only and bound to its kernel peer."""

from __future__ import annotations

import errno
import json
import socket
import stat
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import serve


def _request(verb="resource-limits", **args):
    return (json.dumps({"verb": verb, "args": args, "contract": 1}) + "\n").encode()


def _response(conn):
    chunks = bytearray()
    while b"\n" not in chunks:
        chunk = conn.recv(4096)
        if not chunk:
            break
        chunks.extend(chunk)
    return json.loads(bytes(chunks).split(b"\n", 1)[0])


def test_observer_dispatch_accepts_only_peer_bound_resource_query(tmp_path, monkeypatch):
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        cgroup_root=str(tmp_path / "cgroup"),
        proc_root=str(tmp_path / "proc"),
        gates_slice_name="dev-gates.slice",
    )
    observed = {}

    def observe(**kwargs):
        observed.update(kwargs)
        return {"cgroup_version": 2, "thread_id": 777, "cgroups": []}

    monkeypatch.setattr(serve.resource_observer_mod, "observe_peer_cgroup", observe)
    payload = json.dumps({
        "verb": "resource-limits", "args": {"thread_id": 777}, "contract": 1,
    }).encode()
    response = server._observer_dispatch(payload, (1234, 1000, 1000))
    assert response == {
        "ok": True, "contract": 1, "cgroup_version": 2,
        "thread_id": 777, "cgroups": [],
    }
    assert observed == {
        "peer_pid": 1234,
        "peer_uid": 1000,
        "thread_id": 777,
        "proc_root": str(tmp_path / "proc"),
        "cgroup_root": str(tmp_path / "cgroup"),
        "mountinfo_path": str(tmp_path / "proc" / "self" / "mountinfo"),
        "gates_slice_name": "dev-gates.slice",
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"not json", "request was not valid JSON"),
        (b"[]", "request must be a JSON object"),
        (_request("start"), "accepts only 'resource-limits'"),
        (_request("stop", thread_id=42), "accepts only 'resource-limits'"),
        (_request("resource-limits", thread_id=42, path="/dev.slice"), "requires exactly one"),
        (_request("resource-limits", thread_id=True), "positive integer"),
        (_request("resource-limits", thread_id=0), "positive integer"),
    ],
)
def test_observer_dispatch_refuses_bad_or_mutating_requests(tmp_path, payload, expected):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    response = server._observer_dispatch(payload, (1234, 1000, 1000))
    assert response["ok"] is False
    assert expected in response["error"]["message"]


def test_observer_dispatch_requires_peer_credentials(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    response = server._observer_dispatch(_request(thread_id=42).rstrip(b"\n"), None)
    assert response["error"]["code"] == "peer-refused"


def test_observer_dispatch_maps_identity_refusal_to_structured_error(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))

    def refuse(**_kwargs):
        raise serve.resource_observer_mod.ResourceObserverError("identity unavailable")

    monkeypatch.setattr(serve.resource_observer_mod, "observe_peer_cgroup", refuse)
    response = server._observer_dispatch(_request(thread_id=42).rstrip(b"\n"), (1, 1, 1))
    assert response == {
        "ok": False, "contract": 1,
        "error": {"code": "observer-refused", "message": "identity unavailable"},
    }


def test_dedicated_socket_serves_observer_but_control_socket_does_not(tmp_path, monkeypatch):
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(tmp_path / "resource-observer.sock"),
    )
    monkeypatch.setattr(
        serve.resource_observer_mod,
        "observe_peer_cgroup",
        lambda **kwargs: {
            "cgroup_version": 2,
            "thread_id": kwargs["thread_id"],
            "cgroups": [],
        },
    )
    thread = threading.Thread(target=server._accept_loop, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not Path(server.observer_socket_path).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stat.S_ISSOCK(Path(server.socket_path).lstat().st_mode)
    assert stat.S_ISSOCK(Path(server.observer_socket_path).lstat().st_mode)

    observer_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    observer_client.settimeout(2)
    observer_client.connect(server.observer_socket_path)
    observer_client.sendall(_request(thread_id=threading.get_native_id()))
    assert _response(observer_client)["ok"] is True
    observer_client.close()

    denied = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    denied.settimeout(2)
    denied.connect(server.observer_socket_path)
    denied.sendall(_request("start"))
    response = _response(denied)
    denied.close()
    assert response["error"]["code"] == "bad-argument"
    assert "accepts only" in response["error"]["message"]

    control_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    control_client.settimeout(2)
    control_client.connect(server.socket_path)
    control_client.sendall(_request(thread_id=threading.get_native_id()))
    response = _response(control_client)
    control_client.close()
    assert response["error"]["code"] == "bad-argument"
    assert "unknown verb" in response["error"]["message"]
    assert server._sessions == {}

    server.request_shutdown()
    thread.join(timeout=4)
    assert not thread.is_alive()
    assert not Path(server.socket_path).exists()
    assert not Path(server.observer_socket_path).exists()


def test_bind_observer_refuses_regular_file_without_replacing_it(tmp_path):
    path = tmp_path / "resource-observer.sock"
    path.write_text("preserve me", encoding="ascii")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )
    with pytest.raises(OSError) as exc_info:
        server._bind_observer_socket()
    assert exc_info.value.errno == errno.EEXIST
    assert path.read_text(encoding="ascii") == "preserve me"


def test_bind_observer_refuses_a_live_existing_socket(tmp_path):
    path = tmp_path / "resource-observer.sock"
    owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    owner.bind(str(path))
    owner.listen(1)
    second = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "other-ctl.sock"),
        observer_socket_path=str(path),
    )
    with pytest.raises(OSError) as exc_info:
        second._bind_observer_socket()
    assert exc_info.value.errno == errno.EADDRINUSE
    assert stat.S_ISSOCK(path.lstat().st_mode)
    owner.close()


def test_observer_bind_refuses_existing_socket_with_unexpected_probe_error(
    tmp_path, monkeypatch
):
    path = tmp_path / "resource-observer.sock"
    owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    owner.bind(str(path))
    owner.listen(1)

    class Probe:
        closed = False

        def settimeout(self, _value):
            pass

        def connect(self, _path):
            raise PermissionError(errno.EACCES, "denied")

        def close(self):
            self.closed = True

    probe = Probe()
    monkeypatch.setattr(serve.socket, "socket", lambda *_args, **_kwargs: probe)
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )
    try:
        with pytest.raises(OSError) as exc_info:
            server._bind_observer_socket()
        assert exc_info.value.errno == errno.EADDRINUSE
        assert probe.closed
        assert stat.S_ISSOCK(path.lstat().st_mode)
    finally:
        owner.close()


def test_bind_observer_replaces_only_a_stale_socket(tmp_path):
    path = tmp_path / "resource-observer.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )
    server._bind_observer_socket()
    try:
        assert stat.S_ISSOCK(path.lstat().st_mode)
    finally:
        server._close_socket()


def test_observer_socket_path_derivation_and_control_path_separation(tmp_path):
    default = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    assert default.observer_socket_path == serve.DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH

    control = str(tmp_path / "control.sock")
    derived = serve.SessionServer(
        sessions_dir=str(tmp_path / "derived-sessions"), socket_path=control
    )
    assert derived.observer_socket_path == str(tmp_path / "resource-observer.sock")

    with pytest.raises(ValueError, match="must be distinct"):
        serve.SessionServer(
            sessions_dir=str(tmp_path / "same-sessions"),
            socket_path=control,
            observer_socket_path=control,
        )


def test_control_socket_stays_available_if_observer_path_is_occupied(tmp_path, monkeypatch):
    observer_path = tmp_path / "resource-observer.sock"
    observer_path.write_text("preserve", encoding="ascii")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(observer_path),
    )
    messages = []
    monkeypatch.setattr(server, "_assert_socket_permissions", lambda _directory: None)
    monkeypatch.setattr(server, "_log", messages.append)

    server._bind()
    try:
        assert server._sock is not None
        assert server._observer_sock is None
        assert observer_path.read_text(encoding="ascii") == "preserve"
        assert any("resource observer socket unavailable" in message for message in messages)
    finally:
        server._close_socket()


def test_observer_bind_propagates_setup_failure_and_closes_new_socket(tmp_path, monkeypatch):
    path = tmp_path / "resource-observer.sock"
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )

    def fail_permissions(*_args, **_kwargs):
        raise PermissionError("cannot secure socket")

    monkeypatch.setattr(server, "_assert_one_socket_permissions", fail_permissions)
    with pytest.raises(PermissionError, match="cannot secure socket"):
        server._bind_observer_socket()
    assert server._observer_sock is not None
    with pytest.raises(OSError):
        server._observer_sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
    server._close_socket()
    assert not path.exists()


def test_close_socket_survives_observer_path_unlink_failure(tmp_path, monkeypatch):
    path = tmp_path / "resource-observer.sock"
    path.write_text("owned stale path", encoding="ascii")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )

    class OwnedSocket:
        closed = False

        def close(self):
            self.closed = True

    owned = OwnedSocket()
    server._observer_sock = owned
    unlink = serve.os.unlink

    def deny_observer_unlink(candidate, *args, **kwargs):
        if candidate == str(path):
            raise PermissionError("unlink denied")
        return unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(serve.os, "unlink", deny_observer_unlink)
    server._close_socket()
    assert owned.closed
    assert path.exists()
    monkeypatch.undo()
    path.unlink()


def test_close_socket_preserves_an_unowned_observer_path(tmp_path):
    path = tmp_path / "resource-observer.sock"
    path.write_text("belongs to another listener", encoding="ascii")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )

    server._close_socket()

    assert path.read_text(encoding="ascii") == "belongs to another listener"


def test_observer_socket_permission_failures_are_logged_and_fail_closed(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    messages = []
    monkeypatch.setattr(server, "_log", messages.append)
    chmod_calls = []

    def denied_chmod(path, _mode):
        chmod_calls.append(path)
        raise PermissionError("chmod denied")

    monkeypatch.setattr(serve.os, "chmod", denied_chmod)
    monkeypatch.setattr(serve.os, "stat", lambda _path: SimpleNamespace(st_gid=0))
    monkeypatch.setattr(serve.os, "chown", lambda *_args: (_ for _ in ()).throw(PermissionError("chown denied")))

    server._assert_one_socket_permissions("observer-dir", "observer.sock", warn_root_only=True)

    assert chmod_calls == ["observer-dir", "observer.sock", "observer.sock"]
    assert any("could not chmod observer-dir" in message for message in messages)
    assert any("could not chmod observer.sock" in message for message in messages)
    assert any("could not set observer.sock owner" in message for message in messages)
    assert any("root-only until host-setup" in message for message in messages)


def test_observer_socket_permission_check_returns_if_directory_stat_fails(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    messages = []
    monkeypatch.setattr(server, "_log", messages.append)
    monkeypatch.setattr(serve.os, "chmod", lambda *_args: None)
    monkeypatch.setattr(serve.os, "stat", lambda _path: (_ for _ in ()).throw(PermissionError("stat denied")))
    monkeypatch.setattr(serve.os, "chown", lambda *_args: pytest.fail("chown after failed directory stat"))

    server._assert_one_socket_permissions("observer-dir", "observer.sock", warn_root_only=False)
    assert any("could not stat observer-dir" in message for message in messages)


@pytest.mark.parametrize("failure", [OSError("getsockopt"), struct.error("short credentials")])
def test_peer_credentials_refuse_unreadable_kernel_identity(tmp_path, failure):
    class Connection:
        def getsockopt(self, *_args):
            if isinstance(failure, OSError):
                raise failure
            return b"short"

    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    assert server._peer_credentials(Connection()) is None


def test_observer_accept_loop_handles_absent_socket_timeout_and_close(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    server._observer_accept_loop()

    class TimeoutOnce:
        calls = 0

        def accept(self):
            self.calls += 1
            server._stopping = True
            raise socket.timeout()

    server._observer_sock = TimeoutOnce()
    server._stopping = False
    server._observer_accept_loop()
    assert server._observer_sock.calls == 1

    class Closed:
        def accept(self):
            raise OSError("closed")

    server._observer_sock = Closed()
    server._stopping = False
    server._observer_accept_loop()


def test_accept_loop_logs_if_observer_thread_does_not_stop(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    messages = []
    thread_args = {}
    monkeypatch.setattr(server, "_log", messages.append)

    class MainSocket:
        def accept(self):
            server._stopping = True
            raise OSError("closed")

    class ObserverSocket:
        pass

    class StuckThread:
        def __init__(self, **kwargs):
            thread_args.update(kwargs)

        def start(self):
            pass

        def join(self, timeout):
            assert timeout == 6.0

        def is_alive(self):
            return True

    def bind():
        server._sock = MainSocket()
        server._observer_sock = ObserverSocket()

    monkeypatch.setattr(server, "_bind", bind)
    monkeypatch.setattr(server, "_stop_all_sessions", lambda **_kwargs: None)
    monkeypatch.setattr(server, "_close_socket", lambda: None)
    monkeypatch.setattr(server.damon_pool, "close", lambda: True)
    monkeypatch.setattr(serve.threading, "Thread", StuckThread)
    server._stopping = False

    server._accept_loop()

    assert server._observer_thread is None
    assert thread_args["daemon"] is True
    assert thread_args["name"] == "cgprofile-resource-observer"
    assert any("accept thread did not stop" in message for message in messages)


def test_accept_loop_continues_without_optional_observer_socket(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))

    class MainSocket:
        def accept(self):
            server._stopping = True
            raise OSError("closed")

    def bind_without_observer():
        server._sock = MainSocket()
        server._observer_sock = None

    monkeypatch.setattr(server, "_bind", bind_without_observer)
    monkeypatch.setattr(server, "_stop_all_sessions", lambda **_kwargs: None)
    monkeypatch.setattr(server, "_close_socket", lambda: None)
    monkeypatch.setattr(server.damon_pool, "close", lambda: True)
    server._stopping = False

    server._accept_loop()

    assert server._observer_thread is None


class _FakeConnection:
    def __init__(self, chunks=(), *, recv_error=None, send_error=None):
        self.chunks = list(chunks)
        self.recv_error = recv_error
        self.send_error = send_error
        self.sent = []
        self.closed = False
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, _size):
        if self.recv_error is not None:
            raise self.recv_error
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, payload):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(payload)

    def close(self):
        self.closed = True


def _handle_fake(server, conn, monkeypatch, *, peer=(1234, 1000, 1000), allowed=True):
    monkeypatch.setattr(server, "_peer_credentials", lambda _conn: peer)
    monkeypatch.setattr(server, "_peer_allowed", lambda _uid: allowed)
    server._handle_observer_connection(conn)


def test_observer_connection_refuses_peer_and_handles_eof_and_timeout(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    refused = _FakeConnection()
    _handle_fake(server, refused, monkeypatch, peer=(1, 2000, 2000), allowed=False)
    assert json.loads(refused.sent[0]) ["error"]["code"] == "peer-refused"
    assert refused.closed

    no_credentials = _FakeConnection([_request(thread_id=42)])
    _handle_fake(server, no_credentials, monkeypatch, peer=None)
    response = json.loads(no_credentials.sent[0])
    assert response["error"]["code"] == "peer-refused"
    assert "SO_PEERCRED is required" in response["error"]["message"]

    eof = _FakeConnection()
    _handle_fake(server, eof, monkeypatch)
    assert eof.sent == [] and eof.closed

    ticks = iter((10.0, 16.0))
    monkeypatch.setattr(serve.time, "monotonic", lambda: next(ticks))
    timeout = _FakeConnection()
    _handle_fake(server, timeout, monkeypatch)
    assert timeout.sent == [] and timeout.closed


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        ([b"{}\nextra\n"], "exactly one request line"),
        ([b"x" * 4097], "exceeds 4096 bytes"),
    ],
)
def test_observer_connection_rejects_extra_lines_and_oversize_payloads(
    tmp_path, monkeypatch, chunks, expected
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    conn = _FakeConnection(chunks)
    _handle_fake(server, conn, monkeypatch)
    assert expected in json.loads(conn.sent[0])["error"]["message"]
    assert conn.closed


def test_observer_request_line_accepts_limit_and_refuses_one_byte_over(
    tmp_path, monkeypatch
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    dispatched = []
    monkeypatch.setattr(
        server, "_observer_dispatch",
        lambda payload, _peer: (dispatched.append(payload) or {"ok": True, "contract": 1}),
    )
    prefix = b'{"verb":"resource-limits","args":{"thread_id":1,"padding":"'
    suffix = b'"},"contract":1}\n'

    def payload_of_size(size):
        padding = size - len(prefix) - len(suffix)
        assert padding >= 0
        return prefix + b"x" * padding + suffix

    exact_payload = payload_of_size(4096)
    exact = _FakeConnection([exact_payload])
    _handle_fake(server, exact, monkeypatch)
    assert json.loads(exact.sent[0])["ok"] is True
    assert dispatched == [exact_payload[:-1]]

    oversized = _FakeConnection([payload_of_size(4097)])
    _handle_fake(server, oversized, monkeypatch)
    response = json.loads(oversized.sent[0])
    assert response["error"]["code"] == "bad-argument"
    assert "exceeds 4096 bytes" in response["error"]["message"]
    assert dispatched == [exact_payload[:-1]]

    unterminated = _FakeConnection([b"x" * 4097])
    _handle_fake(server, unterminated, monkeypatch)
    response = json.loads(unterminated.sent[0])
    assert response["error"]["code"] == "bad-argument"
    assert "exceeds 4096 bytes" in response["error"]["message"]
    assert dispatched == [exact_payload[:-1]]


def test_observer_request_received_exactly_at_deadline_is_not_dispatched(
    tmp_path, monkeypatch
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    monkeypatch.setattr(
        server, "_observer_dispatch",
        lambda *_args: {"ok": True, "contract": 1},
    )
    ticks = iter((10.0, 15.0))
    monkeypatch.setattr(serve.time, "monotonic", lambda: next(ticks))
    conn = _FakeConnection([_request(thread_id=42)])

    _handle_fake(server, conn, monkeypatch)

    assert conn.sent == []
    assert conn.closed


def test_observer_connection_handles_newline_at_offset_zero(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    conn = _FakeConnection([b"\nextra"])

    _handle_fake(server, conn, monkeypatch)

    response = json.loads(conn.sent[0])
    assert response["error"]["code"] == "bad-argument"
    assert "exactly one request line" in response["error"]["message"]


def test_observer_connection_keeps_accept_loop_alive_on_errors(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    messages = []
    monkeypatch.setattr(server, "_log", messages.append)

    broken_read = _FakeConnection(recv_error=OSError("client vanished"))
    _handle_fake(server, broken_read, monkeypatch)
    assert broken_read.closed and broken_read.sent == []

    monkeypatch.setattr(server, "_observer_dispatch", lambda *_args: (_ for _ in ()).throw(RuntimeError("bug")))
    broken_dispatch = _FakeConnection([b"{}\n"])
    _handle_fake(server, broken_dispatch, monkeypatch)
    assert json.loads(broken_dispatch.sent[0])["error"]["code"] == "daemon-error"
    assert any("unhandled resource observer error: RuntimeError: bug" in message for message in messages)

    broken_send = _FakeConnection([b"{}\n"], send_error=OSError("client vanished"))
    _handle_fake(server, broken_send, monkeypatch)
    assert broken_send.closed

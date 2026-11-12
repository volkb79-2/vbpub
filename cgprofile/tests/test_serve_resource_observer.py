"""Dedicated observer socket is read-only and bound to its kernel peer."""

from __future__ import annotations

import errno
import json
import os
import socket
import stat
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgprofile import serve


def _request(verb="resource-limits", *, contract=2, **args):
    return (json.dumps({"verb": verb, "args": args, "contract": contract}) + "\n").encode()


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
        "verb": "resource-limits", "args": {"thread_id": 777}, "contract": 2,
    }).encode()
    response = server._observer_dispatch(payload, (1234, 1000, 1000))
    assert response == {
        "ok": True, "contract": 2, "cgroup_version": 2,
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
        "contract": 2,
    }


def test_observer_dispatch_accepts_contract_2_without_changing_control_contract(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    monkeypatch.setattr(
        serve.resource_observer_mod,
        "observe_peer_cgroup",
        lambda **kwargs: {
            "cgroup_version": 2,
            "thread_id": kwargs["thread_id"],
            "pids_events_scope": "hierarchical",
            "memory_events_scope": "local",
            "cgroups": [],
        },
    )

    response = server._observer_dispatch(
        _request(thread_id=42, contract=2), (1, 1, 1)
    )

    assert response == {
        "ok": True,
        "contract": 2,
        "cgroup_version": 2,
        "thread_id": 42,
        "pids_events_scope": "hierarchical",
        "memory_events_scope": "local",
        "cgroups": [],
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


def test_contract_2_observer_errors_keep_the_requested_contract(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))

    response = server._observer_dispatch(
        _request(thread_id=42, contract=2), None
    )

    assert response["contract"] == 2
    assert response["error"]["code"] == "peer-refused"


def test_contract_2_structural_errors_echo_valid_contract(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    request = json.dumps({
        "verb": "resource-limits",
        "args": {"thread_id": 42},
        "contract": 2,
        "extra": True,
    }).encode("utf-8")

    response = server._observer_dispatch(request, None)

    assert response["contract"] == 2
    assert response["error"]["code"] == "bad-argument"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_request(thread_id=42, contract=1), "supports contracts 2 and 3 only"),
        (_request(thread_id=42, contract=4), "supports contracts 2 and 3 only"),
        (
            json.dumps({"verb": None, "args": {}, "contract": 2}).encode(),
            "request 'verb' must be a string",
        ),
        (
            json.dumps({"verb": "resource-limits", "args": [], "contract": 2}).encode(),
            "request 'args' must be a JSON object",
        ),
    ],
)
def test_observer_dispatch_rejects_invalid_contract_verb_and_args(
    tmp_path, payload, message
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))

    response = server._observer_dispatch(payload, (1234, 1000, 1000))

    assert response["ok"] is False
    assert response["contract"] == 2
    assert message in response["error"]["message"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"not json", 2),
        (b"[]", 2),
        (b'{"contract": false}', 2),
        (b'{"contract": 1}', 2),
        (b'{"contract": 2}', 2),
    ],
)
def test_requested_observer_contract_uses_safe_default_for_invalid_envelopes(
    payload, expected
):
    assert serve.SessionServer._requested_observer_contract(payload) == expected


def test_observer_socket_echoes_valid_contract_for_unhandled_dispatch_error(
    tmp_path, monkeypatch
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    monkeypatch.setattr(server, "_peer_credentials", lambda _conn: (1234, 1000, 1000))
    monkeypatch.setattr(server, "_peer_allowed", lambda _uid: True)

    def fail(**_kwargs):
        raise RuntimeError("synthetic observer crash")

    monkeypatch.setattr(serve.resource_observer_mod, "observe_peer_cgroup", fail)
    client, daemon = socket.socketpair()
    client.sendall(_request(thread_id=42, contract=2))

    server._handle_observer_connection(daemon)
    response = _response(client)
    client.close()

    assert response == {
        "ok": False,
        "contract": 2,
        "error": {"code": "daemon-error", "message": "resource observer failed closed"},
    }


def test_observer_socket_uses_current_observer_contract_for_peer_refusal_before_read(
    tmp_path, monkeypatch
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    monkeypatch.setattr(server, "_peer_credentials", lambda _conn: (1234, 1000, 1000))
    monkeypatch.setattr(server, "_peer_allowed", lambda _uid: False)
    client, daemon = socket.socketpair()

    server._handle_observer_connection(daemon)
    response = _response(client)
    client.close()

    assert response["contract"] == 2
    assert response["error"]["code"] == "peer-refused"


def test_observer_dispatch_maps_identity_refusal_to_structured_error(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))

    def refuse(**_kwargs):
        raise serve.resource_observer_mod.ResourceObserverError("identity unavailable")

    monkeypatch.setattr(serve.resource_observer_mod, "observe_peer_cgroup", refuse)
    response = server._observer_dispatch(_request(thread_id=42).rstrip(b"\n"), (1, 1, 1))
    assert response == {
        "ok": False, "contract": 2,
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
    control_client.sendall(
        _request(thread_id=threading.get_native_id(), contract=serve.CONTRACT_VERSION)
    )
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


def test_observer_bind_refuses_a_stale_socket_path_replaced_during_probe(
    tmp_path, monkeypatch,
):
    path = tmp_path / "resource-observer.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    real_lstat = serve.os.lstat
    path_reads = 0

    def replaced_lstat(candidate):
        nonlocal path_reads
        result = real_lstat(candidate)
        if str(candidate) == str(path):
            path_reads += 1
            if path_reads == 2:
                return SimpleNamespace(
                    st_mode=result.st_mode,
                    st_dev=result.st_dev,
                    st_ino=result.st_ino + 1,
                )
        return result

    monkeypatch.setattr(serve.os, "lstat", replaced_lstat)
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )

    with pytest.raises(OSError, match="path changed while checking stale entry"):
        server._bind_observer_socket()

    assert path_reads == 2
    assert stat.S_ISSOCK(real_lstat(path).st_mode)


def test_observer_bind_refuses_a_non_socket_bound_at_the_requested_path(
    tmp_path, monkeypatch,
):
    path = tmp_path / "resource-observer.sock"
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )

    class BoundRegularFile:
        closed = False

        def bind(self, candidate):
            Path(candidate).write_text("raced replacement", encoding="ascii")

        def listen(self, _backlog):
            pass

        def settimeout(self, _timeout):
            pass

        def close(self):
            self.closed = True

    fake_socket = BoundRegularFile()
    monkeypatch.setattr(serve.socket, "socket", lambda *_args, **_kwargs: fake_socket)

    with pytest.raises(OSError, match="bound resource observer path is not a socket"):
        server._bind_observer_socket()

    assert fake_socket.closed
    assert path.read_text(encoding="ascii") == "raced replacement"


def test_observer_socket_path_derivation_and_control_path_separation(tmp_path):
    default = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    assert default.observer_socket_path == serve.DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH

    control = str(tmp_path / "control.sock")
    derived = serve.SessionServer(
        sessions_dir=str(tmp_path / "derived-sessions"), socket_path=control
    )
    assert derived.observer_socket_path == serve.DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH

    with pytest.raises(ValueError, match="must be distinct"):
        serve.SessionServer(
            sessions_dir=str(tmp_path / "same-sessions"),
            socket_path=control,
            observer_socket_path=control,
        )


def test_observer_bind_refuses_to_invent_a_missing_directory(tmp_path):
    observer_dir = tmp_path / "observer"
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(observer_dir / "resource-observer.sock"),
    )
    with pytest.raises(OSError, match="provisioned by host setup"):
        server._bind_observer_socket()
    assert not observer_dir.exists()


def test_observer_bind_refuses_a_non_directory_parent(tmp_path):
    observer_dir = tmp_path / "observer"
    observer_dir.write_text("not a directory", encoding="ascii")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(observer_dir / "resource-observer.sock"),
    )

    with pytest.raises(OSError, match="parent must be an existing directory"):
        server._bind_observer_socket()

    assert observer_dir.read_text(encoding="ascii") == "not a directory"


def test_observer_bind_does_not_change_host_directory_permissions(tmp_path):
    observer_dir = tmp_path / "observer"
    observer_dir.mkdir(mode=0o755)
    observer_dir.chmod(0o755)
    socket_path = observer_dir / "cgprofile.sock"
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(socket_path),
    )
    before = observer_dir.stat()
    server._bind_observer_socket()
    try:
        after = observer_dir.stat()
        socket_stat = socket_path.stat()
        assert stat.S_IMODE(after.st_mode) == 0o755
        assert after.st_uid == before.st_uid
        assert after.st_gid == before.st_gid
        assert stat.S_IMODE(socket_stat.st_mode) == 0o660
        assert socket_stat.st_uid == os.getuid()
    finally:
        server._close_socket()


def test_default_observer_directory_accepts_0755_without_changing_it(
    tmp_path, monkeypatch,
):
    observer_dir = tmp_path / "observer"
    observer_dir.mkdir(mode=0o755)
    observer_dir.chmod(0o755)
    socket_path = observer_dir / "cgprofile.sock"
    monkeypatch.setattr(serve, "DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH", str(socket_path))
    real_lstat = serve.os.lstat
    directory_stat = real_lstat(observer_dir)
    chowns = []

    def host_metadata(path):
        if str(path) == str(observer_dir):
            return SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0,
                st_dev=directory_stat.st_dev, st_ino=directory_stat.st_ino,
            )
        if str(path) == str(socket_path) and real_lstat(path):
            socket_stat = real_lstat(path)
            return SimpleNamespace(
                st_mode=stat.S_IFSOCK | 0o660, st_uid=os.getuid(), st_gid=os.getgid(),
                st_dev=socket_stat.st_dev, st_ino=socket_stat.st_ino,
            )
        return real_lstat(path)

    monkeypatch.setattr(serve.os, "lstat", host_metadata)
    monkeypatch.setattr(serve.os, "chown", lambda *args: chowns.append(args))
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
    )
    server._bind_observer_socket()
    try:
        assert server._observer_sock is not None
        assert stat.S_IMODE(observer_dir.stat().st_mode) == 0o755
        assert chowns == []
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o660
    finally:
        server._close_socket()


def test_default_observer_directory_refuses_group_or_world_write(
    tmp_path, monkeypatch,
):
    observer_dir = tmp_path / "observer"
    observer_dir.mkdir()
    socket_path = observer_dir / "cgprofile.sock"
    monkeypatch.setattr(serve, "DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH", str(socket_path))
    real_lstat = serve.os.lstat
    monkeypatch.setattr(
        serve.os, "lstat",
        lambda path: SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o771, st_uid=0, st_gid=1234
        ) if str(path) == str(observer_dir) else real_lstat(path),
    )
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
    )
    with pytest.raises(OSError, match="not writable by group or other users"):
        server._bind_observer_socket()
    assert not socket_path.exists()


def test_control_socket_stays_available_if_observer_path_is_occupied(tmp_path, monkeypatch):
    observer_path = tmp_path / "resource-observer.sock"
    observer_path.write_text("preserve", encoding="ascii")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(observer_path),
    )
    messages = []
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
        raise PermissionError("cannot set self-bound socket mode")

    monkeypatch.setattr(server, "_apply_self_bind_socket_mode", fail_permissions)
    with pytest.raises(PermissionError, match="cannot set self-bound socket mode"):
        server._bind_observer_socket()
    assert server._observer_sock is None
    assert server.handle_version({})["resource_observer"]["listening"] is False
    assert not path.exists()


def test_close_socket_survives_observer_path_unlink_failure(tmp_path, monkeypatch):
    path = tmp_path / "resource-observer.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        identity = os.lstat(path)
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
    server._observer_socket_identity = (identity.st_dev, identity.st_ino)
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


def test_unlink_observer_socket_returns_if_its_identity_cannot_be_read(
    tmp_path, monkeypatch,
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    monkeypatch.setattr(
        serve.os, "lstat",
        lambda _path: (_ for _ in ()).throw(PermissionError("stat denied")),
    )

    server._unlink_observer_socket_if_owned((10, 20))


@pytest.mark.parametrize("entry_kind", ["regular-file", "replaced-socket"])
def test_unlink_observer_socket_preserves_non_socket_and_replaced_entries(
    tmp_path, entry_kind,
):
    path = tmp_path / "resource-observer.sock"
    owner = None
    if entry_kind == "regular-file":
        path.write_text("preserve", encoding="ascii")
        current = path.lstat()
    else:
        owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        owner.bind(str(path))
        current = path.lstat()
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(tmp_path / "ctl.sock"),
        observer_socket_path=str(path),
    )

    try:
        server._unlink_observer_socket_if_owned((current.st_dev, current.st_ino + 1))
        assert path.exists()
        if entry_kind == "regular-file":
            assert path.read_text(encoding="ascii") == "preserve"
    finally:
        if owner is not None:
            owner.close()


def test_self_bind_mode_failure_is_logged_and_does_not_change_parent(
    tmp_path, monkeypatch,
):
    observer_dir = tmp_path / "observer"
    observer_dir.mkdir()
    socket_path = observer_dir / "cgprofile.sock"
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        observer_socket_path=str(socket_path),
    )
    messages = []
    monkeypatch.setattr(server, "_log", messages.append)
    monkeypatch.setattr(serve.os, "chmod", lambda *_args: (_ for _ in ()).throw(PermissionError("denied")))
    server._apply_self_bind_socket_mode(str(socket_path), "observer")
    assert any("could not chmod self-bound observer socket" in line for line in messages)


def test_self_bind_mode_uses_the_configured_octal_value(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    monkeypatch.setattr(server.settings, "get", lambda key: {"mode": "0600"})
    calls = []
    monkeypatch.setattr(serve.os, "chmod", lambda path, mode: calls.append((path, mode)))
    server._apply_self_bind_socket_mode("/tmp/observer.sock", "observer")
    assert calls == [("/tmp/observer.sock", 0o600)]


def test_activated_close_never_unlinks_systemd_socket_nodes(tmp_path, monkeypatch):
    class Listener:
        def __init__(self):
            self.closed = False

        def settimeout(self, value):
            self.timeout = value

        def close(self):
            self.closed = True

    control, observer = Listener(), Listener()
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        activated_sockets={"control": control, "observer": observer},
    )
    server._bind()
    monkeypatch.setattr(serve.os, "unlink", lambda *_args: pytest.fail("activated paths are systemd-owned"))
    server._close_socket()
    assert control.closed and observer.closed


def test_self_bind_mode_falls_back_to_the_default_for_unusable_settings(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    calls = []
    monkeypatch.setattr(serve.os, "chmod", lambda path, mode: calls.append(mode))
    for settings in ({"mode": "not-octal"}, {}, None):
        monkeypatch.setattr(server.settings, "get", lambda key, _settings=settings: _settings)
        server._apply_self_bind_socket_mode("/tmp/x.sock", "control")
    assert calls == [serve.SOCKET_MODE] * 3


def _activated_server_with_real_nodes(tmp_path):
    """An activated server whose descriptors are real bound sockets, with the
    node identities recorded as a self-bound server would record them: the
    unlink guards must hold on their own, not because identities were absent."""
    listeners = {}
    paths = {}
    for level in ("control", "observer"):
        directory = tmp_path / level
        directory.mkdir()
        paths[level] = str(directory / "cgprofile.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(paths[level])
        listener.listen(1)
        listeners[level] = listener
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=paths["control"],
        observer_socket_path=paths["observer"],
        activated_sockets=listeners,
    )
    server._bind()
    control = os.lstat(paths["control"])
    observer = os.lstat(paths["observer"])
    server._sock_identity = (control.st_dev, control.st_ino)
    server._observer_socket_identity = (observer.st_dev, observer.st_ino)
    return server, paths


def test_activated_close_leaves_both_real_socket_nodes_in_place(tmp_path):
    server, paths = _activated_server_with_real_nodes(tmp_path)
    assert server.socket_mode == "activated"
    server._close_socket()
    assert server._sock is None and server._observer_sock is None
    for path in paths.values():
        assert stat.S_ISSOCK(os.lstat(path).st_mode), f"{path} is systemd-owned and must survive close"


def test_activated_observer_loop_exit_leaves_the_real_node_in_place(tmp_path):
    server, paths = _activated_server_with_real_nodes(tmp_path)
    server._stopping = True
    server._observer_accept_loop()
    assert server._observer_sock is None
    assert stat.S_ISSOCK(os.lstat(paths["observer"]).st_mode)
    server._close_socket()


def test_self_bound_close_does_remove_the_nodes_it_created(tmp_path):
    control_dir = tmp_path / "control"
    observer_dir = tmp_path / "observer"
    control_dir.mkdir()
    observer_dir.mkdir()
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        socket_path=str(control_dir / "cgprofile.sock"),
        observer_socket_path=str(observer_dir / "cgprofile.sock"),
    )
    server._bind()
    assert os.path.exists(server.socket_path) and os.path.exists(server.observer_socket_path)
    server._close_socket()
    assert not os.path.exists(server.socket_path)
    assert not os.path.exists(server.observer_socket_path)


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
        closed = False

        def accept(self):
            self.calls += 1
            server._stopping = True
            raise socket.timeout()

        def close(self):
            self.closed = True

    server._observer_sock = TimeoutOnce()
    server._stopping = False
    server._observer_accept_loop()
    assert server._observer_sock is None
    assert server.handle_version({})["resource_observer"]["listening"] is False

    class Closed:
        def accept(self):
            server._stopping = True
            raise OSError("closed")

        def close(self):
            pass

    server._observer_sock = Closed()
    server._stopping = False
    server._observer_accept_loop()
    assert server._observer_sock is None
    assert server.handle_version({})["resource_observer"]["listening"] is False


def test_observer_accept_loop_logs_unexpected_accept_error(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    messages = []
    monkeypatch.setattr(server, "_log", messages.append)

    class UnexpectedError:
        closed = False

        def accept(self):
            raise OSError("unexpected accept failure")

        def close(self):
            self.closed = True

    observer = UnexpectedError()
    server._observer_sock = observer
    server._stopping = False

    server._observer_accept_loop()

    assert server._observer_sock is None
    assert observer.closed
    assert any("accept loop stopped unexpectedly" in message for message in messages)


def test_observer_accept_loop_preserves_replacement_socket_and_swallows_close_error(
    tmp_path, monkeypatch,
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    replacement = object()

    class ReplacedSocket:
        close_attempted = False

        def accept(self):
            server._observer_sock = replacement
            server._observer_socket_identity = (30, 40)
            server._stopping = True
            raise socket.timeout()

        def close(self):
            self.close_attempted = True
            raise OSError("close raced with shutdown")

    old = ReplacedSocket()
    server._observer_sock = old
    server._observer_socket_identity = (10, 20)
    monkeypatch.setattr(
        server, "_unlink_observer_socket_if_owned",
        lambda _identity: pytest.fail("replacement socket must not be unlinked"),
    )

    server._observer_accept_loop()

    assert old.close_attempted
    assert server._observer_sock is replacement
    assert server._observer_socket_identity == (30, 40)


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
        lambda payload, _peer: (dispatched.append(payload) or {"ok": True, "contract": 2}),
    )
    prefix = b'{"verb":"resource-limits","args":{"thread_id":1,"padding":"'
    suffix = b'"},"contract":2}\n'

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


def test_observer_incomplete_under_limit_request_is_ignored_on_eof(
    tmp_path, monkeypatch
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    conn = _FakeConnection([b"x" * 4096, b""])

    _handle_fake(server, conn, monkeypatch)

    assert conn.sent == []
    assert conn.closed


def test_observer_request_received_exactly_at_deadline_is_not_dispatched(
    tmp_path, monkeypatch
):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    monkeypatch.setattr(
        server, "_observer_dispatch",
        lambda *_args: {"ok": True, "contract": 2},
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

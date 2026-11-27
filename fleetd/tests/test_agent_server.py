import errno
import hashlib
import os
import select
import socket
import stat
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleetd.admission import AdmissionError, ExitCode, InspectorAnswer
from fleetd.agent_verbs import AgentReply
from fleetd.agent_protocol import FrameError, read_frame, write_frame
from fleetd.agent_server import AgentServer, open_agent_socket, serve_connection, serve_forced_command
from fleetd import agent_server
from fleetd import agent_protocol
from test_agent_verbs import _lease_service, _service

THREAD_WATCHDOG_S = 60


def _round_trip(server, path):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(path))
        write_frame(client, {"verb": "probe"})
        client.shutdown(socket.SHUT_WR)
        server.serve_once()
        return read_frame(client, None)
    finally:
        client.close()


def _assert_error_response(document, exit_code, reason_contains=None):
    assert document["schema"] == "fleet-host-agent/1"
    assert isinstance(document["agent_version"], str)
    assert document["error"]["exit_code"] == exit_code
    assert isinstance(document["error"]["reason"], str)
    assert set(document["error"]) == {"exit_code", "reason"}
    assert "exit_code" not in document
    assert "reason" not in document
    if reason_contains is not None:
        assert reason_contains in document["error"]["reason"]


def test_socket_server_serves_probe_restarts_on_same_path_and_keeps_directory(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    socket_dir = tmp_path / "runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "agent.sock"

    server = AgentServer.on_path(socket_path, service, service.policy)
    first = _round_trip(server, socket_path)
    server.close()
    assert socket_dir.is_dir()

    restarted = AgentServer.on_path(socket_path, service, service.policy)
    second = _round_trip(restarted, socket_path)
    restarted.close()

    assert first["schema"] == "fleet-host-agent/1"
    assert second["schema"] == "fleet-host-agent/1"
    assert socket_dir.is_dir()


def test_inherited_listener_accepts_the_systemd_style_descriptor(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    socket_dir = tmp_path / "runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "agent.sock"
    owner = AgentServer.on_path(socket_path, service, service.policy)
    inherited = AgentServer.from_fd(owner.listener.fileno(), service, service.policy)

    reply = _round_trip(inherited, socket_path)

    inherited.close()
    owner.close()
    assert reply["agent_version"] == "1.0.0"


def test_held_idle_connection_does_not_block_another_client(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    socket_dir = tmp_path / "runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "agent.sock"
    server = AgentServer.on_path(socket_path, service, service.policy)
    serving_done = threading.Event()

    def serve():
        server.serve_forever()
        serving_done.set()

    serving = threading.Thread(target=serve)
    serving.start()

    idle_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    active_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        idle_client.connect(str(socket_path))
        active_client.connect(str(socket_path))
        write_frame(active_client, {"verb": "probe"})
        active_client.shutdown(socket.SHUT_WR)
        reply = read_frame(active_client, None)
        assert reply["schema"] == "fleet-host-agent/1"
    finally:
        idle_client.close()
        active_client.close()
        server.close()
        assert serving_done.wait(3), "server did not stop before the failure watchdog"
        serving.join(timeout=3)
        assert not serving.is_alive(), "server worker remained alive after close"


def test_close_does_not_reuse_worker_owned_descriptor_during_response_write(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    server = AgentServer.on_path(tmp_path / "fd-reuse.sock", service, service.policy)
    client, worker_stream = socket.socketpair()
    assert server._register_ordinary(worker_stream)
    write_parked = threading.Event()
    resume_write = threading.Event()
    captured = []
    real_select = select.select

    def park_response_write(readers, writers, errors, timeout=None):
        if writers:
            captured.extend(writers)
            write_parked.set()
            if not resume_write.wait(THREAD_WATCHDOG_S):
                raise AssertionError("response writer was not released by the failure watchdog")
            return [], writers, []
        return real_select(readers, writers, errors, timeout)

    monkeypatch.setattr(agent_protocol.select, "select", park_response_write)
    worker = threading.Thread(target=server._serve_connection, args=(worker_stream,))
    replacement_pairs = []
    replacement_peer = None
    try:
        worker.start()
        write_frame(client, {"verb": "probe"})
        client.shutdown(socket.SHUT_WR)
        assert write_parked.wait(THREAD_WATCHDOG_S), "worker did not park after capturing its response descriptor"
        worker_fd = captured[0]

        server.close()
        assert stat.S_ISSOCK(os.fstat(worker_fd).st_mode), "close released the worker-owned descriptor"
        for _attempt in range(16):
            left, right = socket.socketpair()
            replacement_pairs.append((left, right))
            if left.fileno() == worker_fd:
                replacement_peer = right
                break
            if right.fileno() == worker_fd:
                replacement_peer = left
                break
        assert replacement_peer is None, "a live worker's descriptor number was reused"

        resume_write.set()
        worker.join(timeout=THREAD_WATCHDOG_S)
        assert not worker.is_alive(), "connection worker remained parked after its write was released"
        with pytest.raises(OSError):
            os.fstat(worker_fd)
    finally:
        resume_write.set()
        if worker.ident is not None:
            worker.join(timeout=THREAD_WATCHDOG_S)
        client.close()
        for left, right in replacement_pairs:
            left.close()
            right.close()
        server.close()


def test_lease_reconciliation_uses_injected_interval_without_wall_clock_wait(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    socket_dir = tmp_path / "runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "agent.sock"
    now = [0.0]
    select_calls = [0]
    reconciled = threading.Event()
    server = None

    def select_waiter(readers, writers, errors, timeout):
        select_calls[0] += 1
        if select_calls[0] == 1:
            assert timeout == service.policy.lease_reconcile_interval_s
            now[0] += timeout
            return [], [], []
        return select.select(readers, writers, errors, 0)

    server = AgentServer(
        open_agent_socket(socket_path), service, service.policy,
        monotonic=lambda: now[0], select_waiter=select_waiter,
    )
    original_reconcile = service.reconcile_leases

    def reconcile_and_close():
        result = original_reconcile()
        reconciled.set()
        server.close()
        return result

    service.reconcile_leases = reconcile_and_close
    serving = threading.Thread(target=server.serve_forever)
    serving.start()

    assert reconciled.wait(3), "periodic reconciliation did not run before the failure watchdog"
    serving.join(timeout=3)
    assert not serving.is_alive(), "server did not exit after its wake event"
    assert select_calls[0] == 2


def test_connection_limit_refuses_excess_and_close_unparks_ordinary_worker(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    service.policy = replace(service.policy, max_connections=1)
    socket_dir = tmp_path / "runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "agent.sock"
    server = AgentServer.on_path(socket_path, service, service.policy)
    serving_done = threading.Event()

    def serve():
        server.serve_forever()
        serving_done.set()

    serving = threading.Thread(target=serve)
    serving.start()
    parked = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    excess = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    parked.connect(str(socket_path))
    excess.connect(str(socket_path))
    refusal = read_frame(excess, 3)
    _assert_error_response(refusal, ExitCode.IDENTITY_OR_TRANSPORT, "connection limit")
    assert len(server._ordinary_connections) == 1
    workers = tuple(server._client_threads)
    assert len(workers) == 1

    server.close()
    assert serving_done.wait(3), "server did not stop after its wake event"
    serving.join(timeout=3)
    assert not serving.is_alive(), "server worker remained alive after close"
    for worker in workers:
        worker.join(timeout=3)
        assert not worker.is_alive(), "parked connection worker remained alive after close"
    assert not server._ordinary_connections
    parked.close()
    excess.close()


def test_granted_lease_leaves_connection_budget_and_survives_server_close(tmp_path):
    lease_id = "controller-run-1"
    service, _identity, _facts = _lease_service(
        tmp_path,
        lambda _target: InspectorAnswer.NO_SUCH_OBJECT,
        peer_credentials=(os.geteuid(), os.getegid()),
    )
    service.policy = replace(service.policy, max_connections=1)
    socket_dir = tmp_path / "runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "agent.sock"
    server = AgentServer.on_path(socket_path, service, service.policy)
    serving_done = threading.Event()

    def serve():
        server.serve_forever()
        serving_done.set()

    serving = threading.Thread(target=serve)
    serving.start()
    lease_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lease_client.connect(str(socket_path))
    write_frame(lease_client, {
        "verb": "lease", "lease_id": lease_id, "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    })
    grant = read_frame(lease_client, 3)
    assert grant["event"] == "grant"
    lease_workers = tuple(server._client_threads)
    assert len(lease_workers) == 1

    probe_client.connect(str(socket_path))
    write_frame(probe_client, {"verb": "probe"})
    probe_client.shutdown(socket.SHUT_WR)
    assert read_frame(probe_client, 3)["schema"] == "fleet-host-agent/1"
    probe_workers = tuple(worker for worker in server._client_threads if worker not in lease_workers)

    server.close()
    assert serving_done.wait(3), "server did not stop after its wake event"
    serving.join(timeout=3)
    assert not serving.is_alive(), "server worker remained alive after close"
    write_frame(lease_client, {
        "event": "bind", "lease_id": lease_id, "container_id": "c" * 64,
    })
    bound = read_frame(lease_client, 3)
    assert bound["event"] == "bound"
    assert bound["container_id"] == "c" * 64

    lease_client.close()
    probe_client.close()
    for worker in lease_workers + probe_workers:
        worker.join(timeout=3)
        assert not worker.is_alive(), "held lease worker did not exit after client EOF"


def test_nonreading_client_stalls_response_write_only_until_idle_deadline(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    policy = replace(service.policy, idle_connection_s=10)
    service.policy = policy
    service.handle = lambda _request, _identity, *, body_stream=None: AgentReply(  # type: ignore[method-assign]
        service._envelope(ok=True), body=b"x" * (1024 * 1024),
    )
    server_socket, client_socket = socket.socketpair()
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
    write_frame(client_socket, {"verb": "probe"})
    client_socket.shutdown(socket.SHUT_WR)
    real_select = select.select

    def select_waiter(readers, writers, errors, timeout):
        if writers:
            return [], [], []
        return real_select(readers, writers, errors, timeout)

    monkeypatch.setattr("fleetd.agent_protocol.select.select", select_waiter)
    finished = threading.Event()
    results = []

    def serve():
        try:
            results.append(serve_connection(server_socket, server_socket, service, policy, identity))
        finally:
            server_socket.close()
            finished.set()

    worker = threading.Thread(target=serve)
    worker.start()
    assert finished.wait(3), "stalled response write exceeded the failure watchdog"
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert results == [ExitCode.IDENTITY_OR_TRANSPORT]
    client_socket.close()


def test_server_returns_protocol_exit_codes_on_the_wire(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    policy = service.policy

    client_socket, server_socket = socket.socketpair()
    write_frame(client_socket, {"verb": "probe", "extra": True})
    client_socket.shutdown(socket.SHUT_WR)
    result = serve_connection(server_socket, server_socket, service, policy, identity)
    response = read_frame(client_socket, None)
    assert result == ExitCode.REFUSAL
    _assert_error_response(response, ExitCode.REFUSAL)
    server_socket.close()
    client_socket.close()

    client_socket, server_socket = socket.socketpair()
    client_socket.sendall(b"\x00\x00\x00\x01{")
    client_socket.shutdown(socket.SHUT_WR)
    result = serve_connection(server_socket, server_socket, service, policy, identity)
    response = read_frame(client_socket, None)
    assert result == ExitCode.IDENTITY_OR_TRANSPORT
    _assert_error_response(response, ExitCode.IDENTITY_OR_TRANSPORT)
    server_socket.close()
    client_socket.close()


def test_lease_stream_errors_use_the_nested_error_envelope(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    client_socket, server_socket = socket.socketpair()
    write_frame(client_socket, {
        "verb": "lease", "lease_id": "controller-lease-1", "container_name": "rg-test",
        "footprint": "small", "planned_cores": 1.0, "priority": "gate",
    })

    result = serve_connection(server_socket, server_socket, service, service.policy, identity)
    response = read_frame(client_socket, None)

    assert result == ExitCode.REFUSAL
    _assert_error_response(response, ExitCode.REFUSAL, "injected object inspector")
    server_socket.close()
    client_socket.close()


def test_server_rejects_data_after_one_ordinary_request(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    client_socket, server_socket = socket.socketpair()
    write_frame(client_socket, {"verb": "probe"})
    write_frame(client_socket, {"verb": "probe"})
    client_socket.shutdown(socket.SHUT_WR)

    result = serve_connection(server_socket, server_socket, service, service.policy, identity)
    response = read_frame(client_socket, None)

    assert result == ExitCode.IDENTITY_OR_TRANSPORT
    _assert_error_response(response, ExitCode.IDENTITY_OR_TRANSPORT)
    server_socket.close()
    client_socket.close()


def test_server_reads_put_body_before_accepting_request_eof(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    client_socket, server_socket = socket.socketpair()
    content = b"candidate bytes"
    write_frame(client_socket, {
        "verb": "put", "name": "candidates", "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    })
    client_socket.sendall(content)
    client_socket.shutdown(socket.SHUT_WR)

    result = serve_connection(server_socket, server_socket, service, service.policy, identity)
    response = read_frame(client_socket, None)

    assert result == ExitCode.PASS
    assert response["name"] == "candidates"
    assert response["bytes"] == len(content)
    server_socket.close()
    client_socket.close()


def test_forced_command_uses_framed_stdin_stdout_and_transport_group(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    try:
        write_frame(input_write, {"verb": "probe"})
        os.close(input_write)
        input_write = -1

        code = serve_forced_command(input_read, output_write, service, service.policy, "worker")
        os.close(output_write)
        output_write = -1
        reply = read_frame(output_read, None)

        assert code == ExitCode.PASS
        assert reply["schema"] == "fleet-host-agent/1"
    finally:
        os.close(input_read)
        os.close(output_read)
        if input_write >= 0:
            os.close(input_write)
        if output_write >= 0:
            os.close(output_write)


@pytest.mark.parametrize("parent_kind", ["missing", "file", "symlink"])
def test_socket_open_refuses_missing_non_directory_or_symlink_parent(tmp_path, parent_kind):
    if parent_kind == "missing":
        parent = tmp_path / "missing"
    elif parent_kind == "file":
        parent = tmp_path / "parent-file"
        parent.write_text("not a directory", encoding="utf-8")
    else:
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        parent = tmp_path / "parent-link"
        parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(AdmissionError, match="existing real directory") as raised:
        open_agent_socket(parent / "agent.sock")
    assert raised.value.exit_code == ExitCode.RUNNER_FAILURE


def test_socket_open_refuses_a_symlink_in_an_ancestor_component(tmp_path):
    real_parent = tmp_path / "real-parent"
    (real_parent / "runtime").mkdir(parents=True)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(AdmissionError, match="must not be a symlink") as raised:
        open_agent_socket(linked_parent / "runtime" / "agent.sock")
    assert raised.value.exit_code == ExitCode.RUNNER_FAILURE


def test_socket_open_rejects_non_socket_path_and_keeps_an_active_listener(tmp_path):
    regular_file = tmp_path / "agent.sock"
    regular_file.write_text("owned file", encoding="utf-8")
    with pytest.raises(AdmissionError, match="exists and is not a socket"):
        open_agent_socket(regular_file)

    active_path = tmp_path / "active.sock"
    active = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    active.bind(str(active_path))
    active.listen(1)
    try:
        with pytest.raises(AdmissionError, match="already has a listener") as raised:
            open_agent_socket(active_path)
        assert raised.value.exit_code == ExitCode.RUNNER_FAILURE
    finally:
        active.close()
        active_path.unlink()


def test_stale_socket_restart_rejects_unexpected_probe_error_and_changed_inode(tmp_path, monkeypatch):
    path = tmp_path / "stale.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()

    def denied_connect(_socket, _path):
        raise PermissionError(errno.EPERM, "probe denied")

    monkeypatch.setattr(socket.socket, "connect", denied_connect)
    with pytest.raises(AdmissionError, match="cannot inspect existing agent socket"):
        agent_server._remove_stale_socket(path)

    monkeypatch.undo()
    real_lstat = Path.lstat
    calls = [0]

    def changed_inode(target):
        result = real_lstat(target)
        if target == path:
            calls[0] += 1
            if calls[0] == 2:
                return SimpleNamespace(st_mode=result.st_mode, st_dev=result.st_dev, st_ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(Path, "lstat", changed_inode)
    with pytest.raises(AdmissionError, match="changed during restart") as changed:
        agent_server._remove_stale_socket(path)
    assert changed.value.exit_code == ExitCode.RUNNER_FAILURE
    path.unlink()


@pytest.mark.parametrize("unlink_failure", ["vanished", "denied"])
def test_stale_socket_unlink_races_are_handled_without_removing_other_paths(tmp_path, monkeypatch, unlink_failure):
    path = tmp_path / "stale.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    real_unlink = Path.unlink

    def unlink_race(target, *args, **kwargs):
        if target == path:
            if unlink_failure == "vanished":
                real_unlink(target, *args, **kwargs)
                raise FileNotFoundError("socket disappeared")
            raise PermissionError(errno.EACCES, "unlink denied")
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_race)
    if unlink_failure == "denied":
        with pytest.raises(AdmissionError, match="cannot remove stale agent socket") as raised:
            agent_server._remove_stale_socket(path)
        assert raised.value.exit_code == ExitCode.RUNNER_FAILURE
        monkeypatch.undo()
        real_unlink(path)
        return

    listener = open_agent_socket(path)
    assert listener.getsockname() == str(path)
    listener.close()
    monkeypatch.undo()
    real_unlink(path)


def test_open_agent_socket_rejects_relative_paths_and_closes_after_bind_failure(tmp_path, monkeypatch):
    with pytest.raises(AdmissionError, match="absolute file path") as relative:
        open_agent_socket(Path("relative.sock"))
    assert relative.value.exit_code == ExitCode.RUNNER_FAILURE

    class FailedListener:
        closed = False

        def bind(self, _path):
            raise OSError("bind denied")

        def close(self):
            self.closed = True

    listener = FailedListener()
    monkeypatch.setattr(agent_server.socket, "socket", lambda *_args: listener)
    with pytest.raises(AdmissionError, match="cannot bind fleetd agent socket") as raised:
        open_agent_socket(tmp_path / "agent.sock")
    assert raised.value.exit_code == ExitCode.RUNNER_FAILURE
    assert listener.closed is True


def test_inherited_listener_refuses_bad_descriptors_and_closes_invalid_duplicates(monkeypatch):
    with pytest.raises(AdmissionError, match="non-negative integer") as negative:
        agent_server.inherited_listener(-1)
    assert negative.value.exit_code == ExitCode.RUNNER_FAILURE

    monkeypatch.setattr(
        agent_server.socket, "fromfd",
        lambda *_args: (_ for _ in ()).throw(OSError("bad inherited fd")),
    )
    with pytest.raises(AdmissionError, match="invalid inherited agent listener fd") as bad_fd:
        agent_server.inherited_listener(987654)
    assert bad_fd.value.exit_code == ExitCode.RUNNER_FAILURE


@pytest.mark.parametrize("socktype", [socket.SOCK_DGRAM, socket.SOCK_STREAM])
def test_inherited_listener_closes_wrong_type_or_non_listening_descriptor(socktype):
    original = socket.socket(socket.AF_UNIX, socktype)
    try:
        expected = "AF_UNIX stream socket" if socktype == socket.SOCK_DGRAM else "not listening"
        with pytest.raises(AdmissionError, match=expected) as raised:
            agent_server.inherited_listener(original.fileno())
        assert raised.value.exit_code == ExitCode.RUNNER_FAILURE
    finally:
        original.close()


def test_inherited_listener_closes_its_duplicate_when_type_validation_fails(monkeypatch):
    duplicate = SimpleNamespace(family=socket.AF_UNIX, closed=False)

    def getsockopt(_level, option):
        if option == socket.SO_TYPE:
            return socket.SOCK_DGRAM
        raise AssertionError("type validation must stop before listening validation")

    duplicate.getsockopt = getsockopt
    duplicate.close = lambda: setattr(duplicate, "closed", True)
    monkeypatch.setattr(agent_server.socket, "fromfd", lambda *_args: duplicate)
    with pytest.raises(AdmissionError, match="AF_UNIX stream socket"):
        agent_server.inherited_listener(55)
    assert duplicate.closed is True


def test_serve_once_handles_wake_limit_and_peer_authentication_refusal(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    server = AgentServer.on_path(tmp_path / "wake.sock", service, service.policy)
    server._select_waiter = lambda *_args: ([server._wake_read], [], [])
    server.serve_once()
    assert server.listener.fileno() >= 0
    server.close()

    limited_root = tmp_path / "limited"
    limited_root.mkdir()
    service, _identity, _checkout = _service(
        limited_root, peer_credentials=(os.geteuid(), os.getegid()),
    )
    limited_policy = replace(service.policy, max_connections=0)
    client_stream, server_stream = socket.socketpair()

    class Listener:
        def accept(self):
            return server_stream, None

        def close(self):
            return None

    limited = AgentServer(Listener(), service, limited_policy)
    limited._select_waiter = lambda *_args: ([limited.listener], [], [])
    limited.serve_once()
    refusal = read_frame(client_stream, 1)
    _assert_error_response(refusal, ExitCode.IDENTITY_OR_TRANSPORT, "connection limit")
    client_stream.close()
    limited.close()

    identity_root = tmp_path / "identity"
    identity_root.mkdir()
    service, _identity, _checkout = _service(
        identity_root, peer_credentials=(os.geteuid(), os.getegid()),
    )
    client_stream, server_stream = socket.socketpair()

    class AcceptedListener:
        def accept(self):
            return server_stream, None

        def close(self):
            return None

    identity_server = AgentServer(AcceptedListener(), service, service.policy)
    identity_server._select_waiter = lambda *_args: ([identity_server.listener], [], [])
    monkeypatch.setattr(
        agent_server.AgentPolicy, "resolve_socket_peer",
        lambda *_args: (_ for _ in ()).throw(AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "unknown peer")),
    )
    identity_server.serve_once()
    refusal = read_frame(client_stream, 1)
    _assert_error_response(refusal, ExitCode.IDENTITY_OR_TRANSPORT, "unknown peer")
    client_stream.close()
    identity_server.close()


def test_close_wakes_active_serve_once_and_waiter_closes_watched_descriptors(tmp_path):
    service, _identity, _checkout = _service(tmp_path)
    waiting = threading.Event()
    woke = threading.Event()
    leave_wait = threading.Event()
    completed = threading.Event()
    real_select = select.select
    server = None

    def parked_select(readers, writers, errors, timeout=None):
        if server._wake_read in readers:
            waiting.set()
            readable = real_select(readers, writers, errors, timeout)
            woke.set()
            if not leave_wait.wait(THREAD_WATCHDOG_S):
                raise AssertionError("serve_once was not released by the failure watchdog")
            return readable
        return real_select(readers, writers, errors, timeout)

    server = AgentServer(
        open_agent_socket(tmp_path / "serve-once-close.sock"),
        service, service.policy, select_waiter=parked_select,
    )
    watched = (server.listener.fileno(), server._wake_read.fileno(), server._wake_write.fileno())

    def serve_one():
        server.serve_once()
        completed.set()

    waiter = threading.Thread(target=serve_one)
    try:
        waiter.start()
        assert waiting.wait(THREAD_WATCHDOG_S), "serve_once did not enter its listener wait"
        with pytest.raises(AdmissionError) as second_once:
            server.serve_once()
        assert second_once.value.exit_code == ExitCode.RUNNER_FAILURE
        with pytest.raises(AdmissionError) as concurrent_forever:
            server.serve_forever()
        assert concurrent_forever.value.exit_code == ExitCode.RUNNER_FAILURE

        server.close()
        assert woke.wait(THREAD_WATCHDOG_S), "close did not wake the active serve_once"
        assert not completed.is_set(), "serve_once left its wait before the test released it"
        assert all(stat.S_ISSOCK(os.fstat(fd).st_mode) for fd in watched)

        leave_wait.set()
        assert completed.wait(THREAD_WATCHDOG_S), "serve_once did not finish after leaving the wait"
        waiter.join(timeout=THREAD_WATCHDOG_S)
        assert not waiter.is_alive(), "serve_once thread remained alive after the failure watchdog"
        for fd in watched:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        leave_wait.set()
        if waiter.ident is not None:
            waiter.join(timeout=THREAD_WATCHDOG_S)
        server.close()


def test_serve_forever_returns_when_closed_rejects_duplicate_and_skips_early_reconcile(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    closed = AgentServer.on_path(tmp_path / "closed.sock", service, service.policy)
    closed.close()
    closed.serve_forever()
    closed.serve_once()
    assert closed.listener.fileno() == -1

    duplicate = AgentServer.on_path(tmp_path / "duplicate.sock", service, service.policy)
    duplicate._serving = True
    with pytest.raises(AdmissionError, match="already serving") as raised:
        duplicate.serve_forever()
    assert raised.value.exit_code == ExitCode.RUNNER_FAILURE
    duplicate._serving = False
    duplicate.close()

    calls = [0]
    reconciliations = []
    early = AgentServer.on_path(tmp_path / "early.sock", service, service.policy)

    def select_waiter(readers, _writers, _errors, _timeout):
        calls[0] += 1
        if calls[0] == 1:
            return [], [], []
        return [early._wake_read], [], []

    early._select_waiter = select_waiter
    early._monotonic = lambda: 0.0
    service.reconcile_leases = lambda: reconciliations.append("called")
    early.serve_forever()
    assert calls[0] == 2
    assert reconciliations == []


def test_serve_forever_reports_worker_start_failure_and_releases_connection(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    client_stream, server_stream = socket.socketpair()
    accept_calls = [0]

    class Listener:
        def accept(self):
            accept_calls[0] += 1
            return server_stream, None

        def close(self):
            return None

    server = AgentServer(Listener(), service, service.policy)

    class FailedThread:
        def __init__(self, **_kwargs):
            return None

        def start(self):
            raise RuntimeError("thread resource unavailable")

    monkeypatch.setattr(agent_server.threading, "Thread", FailedThread)

    def select_waiter(readers, _writers, _errors, _timeout):
        if accept_calls[0] == 0:
            return [server.listener], [], []
        return [server._wake_read], [], []

    server._select_waiter = select_waiter
    server.serve_forever()
    refusal = read_frame(client_stream, 1)
    _assert_error_response(refusal, ExitCode.RUNNER_FAILURE, "cannot start")
    assert server._ordinary_connections == set()
    assert server._client_threads == set()
    client_stream.close()


def test_serve_forever_closes_connection_if_server_closes_before_worker_start(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    client_stream, server_stream = socket.socketpair()
    accepted = [False]

    class Listener:
        def accept(self):
            accepted[0] = True
            return server_stream, None

        def close(self):
            return None

    server = AgentServer(Listener(), service, service.policy)

    class ClosingThread:
        def __init__(self, **_kwargs):
            server._closed = True

        def start(self):
            raise AssertionError("closed server must not start the connection worker")

    monkeypatch.setattr(agent_server.threading, "Thread", ClosingThread)
    server._select_waiter = lambda *_args: (
        ([server.listener] if not accepted[0] else [server._wake_read]), [], [],
    )
    server.serve_forever()
    assert accepted[0] is True
    assert client_stream.recv(1) == b""
    assert server._ordinary_connections == set()
    assert server._client_threads == set()
    client_stream.close()


def test_server_worker_releases_connection_after_peer_refusal_and_transport_oserror(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    client_stream, server_stream = socket.socketpair()
    worker = AgentServer.on_path(tmp_path / "unused.sock", service, service.policy)
    worker._ordinary_connections.add(server_stream)
    worker._client_threads.add(threading.current_thread())
    monkeypatch.setattr(
        agent_server.AgentPolicy, "resolve_socket_peer",
        lambda *_args: (_ for _ in ()).throw(AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "unknown peer")),
    )
    worker._serve_connection(server_stream)
    refusal = read_frame(client_stream, 1)
    _assert_error_response(refusal, ExitCode.IDENTITY_OR_TRANSPORT, "unknown peer")
    assert worker._ordinary_connections == set()
    assert worker._client_threads == set()
    client_stream.close()
    worker.close()

    client_stream, server_stream = socket.socketpair()
    worker = AgentServer.on_path(tmp_path / "transport.sock", service, service.policy)
    worker._ordinary_connections.add(server_stream)
    worker._client_threads.add(threading.current_thread())
    monkeypatch.setattr(agent_server.AgentPolicy, "resolve_socket_peer", lambda *_args: _identity)
    monkeypatch.setattr(
        agent_server, "serve_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("peer transport closed")),
    )
    worker._serve_connection(server_stream)
    assert client_stream.recv(1) == b""
    assert worker._ordinary_connections == set()
    assert worker._client_threads == set()
    client_stream.close()
    worker.close()


def test_server_close_contains_wake_and_connection_close_oserrors(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    server = AgentServer.on_path(tmp_path / "close.sock", service, service.policy)

    class BrokenConnection:
        def __init__(self):
            self.calls = []

        def shutdown(self, _how):
            self.calls.append("shutdown")
            raise OSError("already gone")

        def close(self):
            self.calls.append("close")
            raise OSError("already closed")

    connection = BrokenConnection()
    server._ordinary_connections.add(connection)
    server.close()
    server.close()
    assert connection.calls == ["shutdown"]

    wake_server = AgentServer.on_path(tmp_path / "wake-error.sock", service, service.policy)
    wake_read = wake_server._wake_read
    wake_write = wake_server._wake_write
    wake_server._wake_write = SimpleNamespace(
        send=lambda _value: (_ for _ in ()).throw(OSError("wake fd closed")),
    )
    wake_server._serving = True
    wake_server.close()
    assert wake_server._closed is True
    wake_read.close()
    wake_write.close()
    wake_server.listener.close()


def test_server_error_writer_and_catchall_return_bounded_wire_documents(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    monkeypatch.setattr(
        agent_server, "write_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FrameError("write refused")),
    )
    assert agent_server._write_error(object(), service, ExitCode.REFUSAL, "test") is None

    monkeypatch.undo()
    client_stream, server_stream = socket.socketpair()
    write_frame(client_stream, {"verb": "probe"})
    client_stream.shutdown(socket.SHUT_WR)
    monkeypatch.setattr(agent_server, "require_end_of_input", lambda *_args: None)
    service.handle = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider exploded"))

    result = serve_connection(server_stream, server_stream, service, service.policy, identity)
    reply = read_frame(client_stream, 1)
    assert result == ExitCode.RUNNER_FAILURE
    _assert_error_response(reply, ExitCode.RUNNER_FAILURE, "unexpected agent failure: provider exploded")
    client_stream.close()
    server_stream.close()


def test_forced_command_refuses_unknown_transport_group_on_wire(tmp_path):
    service, _identity, _checkout = _service(tmp_path)
    client_stream, server_stream = socket.socketpair()
    result = serve_forced_command(client_stream, server_stream, service, service.policy, "unknown")
    refusal = read_frame(client_stream, 1)
    assert result == ExitCode.IDENTITY_OR_TRANSPORT
    _assert_error_response(refusal, ExitCode.IDENTITY_OR_TRANSPORT, "unknown forced-command group")
    client_stream.close()
    server_stream.close()

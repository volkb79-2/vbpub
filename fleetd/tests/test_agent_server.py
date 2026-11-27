import os
import select
import socket
import threading

from fleetd.admission import ExitCode
from fleetd.agent_protocol import read_frame, write_frame
from fleetd.agent_server import AgentServer, open_agent_socket, serve_forced_command
from test_agent_verbs import _service


def _round_trip(server, path):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(path))
        write_frame(client, {"verb": "probe"})
        server.serve_once()
        return read_frame(client, None)
    finally:
        client.close()


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
    serving = threading.Thread(target=server.serve_forever)
    serving.start()

    idle_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    active_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        idle_client.connect(str(socket_path))
        active_client.connect(str(socket_path))
        write_frame(active_client, {"verb": "probe"})
        reply = read_frame(active_client, None)
        assert reply["schema"] == "fleet-host-agent/1"
    finally:
        idle_client.close()
        active_client.close()
        server.close()
        serving.join()


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

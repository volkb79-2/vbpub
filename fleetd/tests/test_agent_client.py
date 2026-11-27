import os
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleetd.admission import AdmissionError, ExitCode
from fleetd.agent_client import (
    AgentClient,
    AgentLeaseSession,
    _raise_remote_error,
    _read_body,
    _validate_agent_response,
    require_compatible_agent,
)
from fleetd.agent_protocol import MAX_FETCH_BYTES, FrameError, read_frame, write_frame
from fleetd.agent_server import AgentServer
from test_agent_verbs import _service


def _probe(version, capabilities=None):
    return {
        "schema": "fleet-host-agent/1",
        "agent_version": version,
        "capabilities": capabilities or {"facts": ["verb"]},
    }


def test_major_mismatch_names_both_versions_and_refuses_before_the_request():
    with pytest.raises(AdmissionError, match="client 1.2.3, host 2.0.0") as raised:
        require_compatible_agent("1.2.3", {"verb": "submit"}, _probe("2.0.0"))

    assert raised.value.exit_code == ExitCode.REFUSAL


def test_minor_or_patch_mismatch_requires_advertised_verb_and_every_field():
    request = {"verb": "facts"}
    require_compatible_agent("1.2.4", request, _probe("1.2.3", {"facts": ["verb"]}))

    with pytest.raises(AdmissionError, match="does not advertise 'submit' fields: resources") as raised:
        require_compatible_agent(
            "1.2.4", {"verb": "submit", "resources": {}}, _probe("1.2.3", {"submit": ["verb"]}),
        )
    assert raised.value.exit_code == ExitCode.REFUSAL


def test_probe_rejects_a_different_agent_schema_even_when_versions_match():
    probe = _probe("1.2.3")
    probe["schema"] = "fleet-host-agent/2"
    with pytest.raises(AdmissionError, match="schema mismatch.*1.2.3"):
        require_compatible_agent("1.2.3", {"verb": "facts"}, probe)


def test_local_client_probes_before_request_and_uses_the_socket_service(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    policy = service.policy
    socket_path = tmp_path / "agent.sock"
    server = AgentServer.on_path(socket_path, service, policy)
    failures = []
    finished = threading.Event()

    def serve_probe_and_request():
        try:
            server.serve_once()
            server.serve_once()
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=serve_probe_and_request)
    worker.start()
    reply = AgentClient(socket_path, "1.0.0").request({"verb": "facts"})
    assert finished.wait(3), "local client server did not finish before the failure watchdog"
    worker.join(timeout=3)
    assert not worker.is_alive(), "local client server worker remained alive after completion"
    server.close()

    assert reply.document["schema"] == "fleet-host-agent/1"
    assert reply.document["facts"]["schema"] == "host-facts/2"
    assert failures == []


def test_major_version_refusal_does_not_send_the_job_request(tmp_path):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    server = AgentServer.on_path(tmp_path / "agent.sock", service, service.policy)
    failures = []
    finished = threading.Event()

    def serve_probe():
        try:
            server.serve_once()
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=serve_probe)
    worker.start()
    with pytest.raises(AdmissionError, match="client 2.0.0, host 1.0.0") as raised:
        AgentClient(tmp_path / "agent.sock", "2.0.0").request({"verb": "submit"})
    assert finished.wait(3), "version-probe server did not finish before the failure watchdog"
    worker.join(timeout=3)
    assert not worker.is_alive(), "version-probe server worker remained alive after completion"
    server.close()

    assert raised.value.exit_code == ExitCode.REFUSAL
    assert list(service.paths.jobs_dir.iterdir()) == []
    assert failures == []


@pytest.mark.parametrize("existing_socket", [False, True], ids=["missing", "refusing"])
def test_local_client_reports_missing_or_refusing_socket_as_transport_3(tmp_path, existing_socket):
    socket_path = tmp_path / "agent.sock"
    if existing_socket:
        refusal = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        refusal.bind(str(socket_path))
        refusal.close()

    with pytest.raises(AdmissionError, match="cannot connect to fleetd agent socket") as raised:
        AgentClient(socket_path, "1.0.0").connect()

    assert raised.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT


def test_lease_client_keeps_connection_and_validates_each_agent_reply(monkeypatch):
    client_stream, server_stream = socket.socketpair()
    client = AgentClient(Path("/unused"), "1.0.0")
    monkeypatch.setattr(client, "_check_version", lambda _request: "1.0.0")
    monkeypatch.setattr(client, "connect", lambda: client_stream)
    failures = []
    finished = threading.Event()

    def serve_lease():
        try:
            request = read_frame(server_stream, None)
            assert request["verb"] == "lease"
            write_frame(server_stream, {
                "schema": "fleet-host-agent/1", "agent_version": "1.0.0",
                "event": "grant", "lease_id": request["lease_id"],
            })
            bind = read_frame(server_stream, None)
            assert bind["event"] == "bind"
            write_frame(server_stream, {
                "schema": "fleet-host-agent/1", "agent_version": "1.0.0",
                "event": "bound", "lease_id": bind["lease_id"],
                "container_id": bind["container_id"],
            })
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=serve_lease)
    worker.start()
    session = client.open_lease({"verb": "lease", "lease_id": "controller-lease-1"})
    try:
        assert session.first_reply["event"] == "grant"
        bound = session.bind("a" * 64)
        assert bound["event"] == "bound"
    finally:
        session.close()
        server_stream.close()
    assert finished.wait(3), "lease client server did not finish before the failure watchdog"
    worker.join(timeout=3)
    assert not worker.is_alive(), "lease client server worker remained alive after completion"

    assert failures == []


def test_client_refuses_an_oversized_response_body_before_reading_it():
    client, server = socket.socketpair()
    try:
        with pytest.raises(FrameError, match="response body exceeds"):
            _read_body(client, MAX_FETCH_BYTES + 1)
        server.setblocking(False)
        with pytest.raises(BlockingIOError):
            server.recv(1)
    finally:
        client.close()
        server.close()


def test_version_and_capability_refusals_cover_malformed_metadata():
    with pytest.raises(AdmissionError, match="client version is not a SemVer") as client_version:
        require_compatible_agent("1", {"verb": "facts"}, _probe("1.0.0"))
    assert client_version.value.exit_code == ExitCode.REFUSAL

    with pytest.raises(AdmissionError, match="host agent version is not a SemVer") as host_version:
        require_compatible_agent("1.0.0", {"verb": "facts"}, _probe("invalid"))
    assert host_version.value.exit_code == ExitCode.REFUSAL

    missing_probe = _probe("1.0.0")
    missing_probe["capabilities"] = []
    with pytest.raises(AdmissionError, match="does not advertise verb 'facts'") as missing:
        require_compatible_agent("1.1.0", {"verb": "facts"}, missing_probe)
    assert missing.value.exit_code == ExitCode.REFUSAL

    with pytest.raises(AdmissionError, match="does not advertise verb 'facts'") as invalid_fields:
        require_compatible_agent("1.1.0", {"verb": "facts"}, _probe("1.0.0", {"facts": ["verb", 1]}))
    assert invalid_fields.value.exit_code == ExitCode.REFUSAL


def test_agent_response_validation_and_remote_exit_documents_are_closed():
    with pytest.raises(AdmissionError, match="response schema mismatch") as schema:
        _validate_agent_response({"schema": "other", "agent_version": "1.0.0"})
    assert schema.value.exit_code == ExitCode.REFUSAL

    with pytest.raises(AdmissionError, match="host agent response version is not a SemVer") as malformed:
        _validate_agent_response({"schema": "fleet-host-agent/1", "agent_version": "v1"})
    assert malformed.value.exit_code == ExitCode.REFUSAL

    with pytest.raises(AdmissionError, match="version changed after probe") as changed:
        _validate_agent_response(_probe("1.0.1"), expected_version="1.0.0")
    assert changed.value.exit_code == ExitCode.REFUSAL

    assert _validate_agent_response(_probe("1.0.0"), expected_version="1.0.0") == "1.0.0"
    assert _raise_remote_error({"state": "running"}) is None
    with pytest.raises(AdmissionError, match="malformed error response") as malformed_error:
        _raise_remote_error({"exit_code": "2", "reason": "bad"})
    assert malformed_error.value.exit_code == ExitCode.RUNNER_FAILURE
    with pytest.raises(AdmissionError, match="remote refusal") as remote_error:
        _raise_remote_error({"exit_code": 2, "reason": "remote refusal"})
    assert remote_error.value.exit_code == ExitCode.REFUSAL


def test_read_body_validates_length_and_detects_truncation():
    client, server = socket.socketpair()
    try:
        for length in (-1, True, "1"):
            with pytest.raises(FrameError, match="non-negative integer"):
                _read_body(client, length)
        assert _read_body(client, 0) == b""
        server.sendall(b"body")
        assert _read_body(client, 4) == b"body"
        server.shutdown(socket.SHUT_WR)
        with pytest.raises(FrameError, match="body was truncated"):
            _read_body(client, 1)
    finally:
        client.close()
        server.close()


def test_put_client_half_closes_and_reads_the_exact_response_body():
    client_stream, server_stream = socket.socketpair()
    client = AgentClient(Path("/unused"), "1.0.0")
    client._check_version = lambda _request: "1.0.0"
    client.connect = lambda: client_stream
    failures = []
    finished = threading.Event()

    def serve_put():
        try:
            request = read_frame(server_stream, None)
            body = bytearray()
            while True:
                chunk = server_stream.recv(64)
                if not chunk:
                    break
                body.extend(chunk)
            assert request == {"verb": "put", "name": "candidates"}
            assert bytes(body) == b"candidate-data"
            write_frame(server_stream, {
                "schema": "fleet-host-agent/1", "agent_version": "1.0.0", "length": 6,
            })
            server_stream.sendall(b"stored")
        except BaseException as exc:
            failures.append(exc)
        finally:
            server_stream.close()
            finished.set()

    worker = threading.Thread(target=serve_put)
    worker.start()
    reply = client.request({"verb": "put", "name": "candidates"}, body=b"candidate-data")
    assert reply.body == b"stored"
    assert finished.wait(3), "put client server did not finish before the failure watchdog"
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert failures == []


def test_client_request_refuses_lease_and_mismatched_put_bodies():
    client = AgentClient(Path("/unused"), "1.0.0")
    client._check_version = lambda _request: "1.0.0"
    with pytest.raises(AdmissionError, match="use open_lease") as lease:
        client.request({"verb": "lease", "lease_id": "lease-1"})
    assert lease.value.exit_code == ExitCode.REFUSAL

    with pytest.raises(AdmissionError, match="put requires exactly one streamed body") as missing:
        client.request({"verb": "put"})
    assert missing.value.exit_code == ExitCode.REFUSAL
    with pytest.raises(AdmissionError, match="put requires exactly one streamed body") as extra:
        client.request({"verb": "facts"}, body=b"not a put")
    assert extra.value.exit_code == ExitCode.REFUSAL


def test_probe_request_skips_recursive_compatibility_probe(monkeypatch):
    client = AgentClient(Path("/unused"), "1.0.0")
    seen = []
    monkeypatch.setattr(client, "_exchange", lambda request, body=None, *, expected_version=None: (
        seen.append((request, body, expected_version)) or SimpleNamespace(document={"verb": "probe"}, body=b"")
    ))
    reply = client.request({"verb": "probe"})
    assert reply.document == {"verb": "probe"}
    assert seen == [({"verb": "probe"}, None, None)]


def test_exchange_converts_frame_errors_and_always_closes_the_stream(monkeypatch):
    class Stream:
        closed = False

        def close(self):
            self.closed = True

    stream = Stream()
    client = AgentClient(Path("/unused"), "1.0.0")
    client.connect = lambda: stream
    monkeypatch.setattr("fleetd.agent_client.write_frame", lambda *_args: (_ for _ in ()).throw(FrameError("write failed")))
    with pytest.raises(AdmissionError, match="write failed") as error:
        client._exchange({"verb": "probe"})
    assert error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    assert stream.closed is True


def test_lease_client_closes_on_invalid_request_frame_errors_and_remote_refusal(monkeypatch):
    client = AgentClient(Path("/unused"), "1.0.0")
    with pytest.raises(AdmissionError, match="requires a lease request") as invalid_request:
        client.open_lease({"verb": "facts", "lease_id": "lease-1"})
    assert invalid_request.value.exit_code == ExitCode.REFUSAL

    class Stream:
        closed = False

        def close(self):
            self.closed = True

    stream = Stream()
    client._check_version = lambda _request: "1.0.0"
    client.connect = lambda: stream
    monkeypatch.setattr("fleetd.agent_client.write_frame", lambda *_args: None)
    monkeypatch.setattr("fleetd.agent_client.read_frame", lambda *_args: (_ for _ in ()).throw(FrameError("bad grant frame")))
    with pytest.raises(AdmissionError, match="bad grant frame") as frame_error:
        client.open_lease({"verb": "lease", "lease_id": "lease-1"})
    assert frame_error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    assert stream.closed is True

    stream = Stream()
    client.connect = lambda: stream
    monkeypatch.setattr("fleetd.agent_client.read_frame", lambda *_args: {
        "schema": "fleet-host-agent/1", "agent_version": "1.0.0",
        "exit_code": 2, "reason": "lease refused",
    })
    with pytest.raises(AdmissionError, match="lease refused") as refusal:
        client.open_lease({"verb": "lease", "lease_id": "lease-1"})
    assert refusal.value.exit_code == ExitCode.REFUSAL
    assert stream.closed is True


def test_lease_session_bind_maps_disconnect_to_transport_exit_three():
    client_stream, server_stream = socket.socketpair()
    server_stream.close()
    session = AgentLeaseSession(client_stream, {}, "lease-1", "1.0.0")
    with pytest.raises(AdmissionError) as disconnected:
        session.bind("a" * 64)
    assert disconnected.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    session.close()

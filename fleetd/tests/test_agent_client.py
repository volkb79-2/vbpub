import os
import socket
import threading
from pathlib import Path

import pytest

from fleetd.admission import AdmissionError, ExitCode
from fleetd.agent_client import AgentClient, require_compatible_agent
from fleetd.agent_protocol import read_frame, write_frame
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

    def serve_probe_and_request():
        try:
            server.serve_once()
            server.serve_once()
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=serve_probe_and_request)
    worker.start()
    reply = AgentClient(socket_path, "1.0.0").request({"verb": "facts"})
    worker.join()
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

    def serve_probe():
        try:
            server.serve_once()
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=serve_probe)
    worker.start()
    with pytest.raises(AdmissionError, match="client 2.0.0, host 1.0.0") as raised:
        AgentClient(tmp_path / "agent.sock", "2.0.0").request({"verb": "submit"})
    worker.join()
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
    worker.join()

    assert failures == []

import hashlib
import json
import os
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleetd.admission import AdmissionError, ExitCode
from fleetd.admission import InspectorAnswer
from fleetd.agent_verbs import AgentVerbService
from fleetd.agent_protocol import read_frame, write_frame
from fleetd.contracts import validate_host_facts
from fleetd.facts import collect_host_facts
from test_agent_protocol import _policy, _submit


FACTS_ROOT = Path(__file__).parent / "fixtures" / "facts-root"


def _service(tmp_path, *, extra_argv='[["--verbose"]]'):
    policy, checkout = _policy(tmp_path, extra_argv=extra_argv)
    facts = validate_host_facts(collect_host_facts(
        FACTS_ROOT,
        disk_statvfs_reader=lambda _path: SimpleNamespace(
            f_frsize=4096, f_bsize=4096, f_blocks=1_000_000, f_bavail=750_000,
        ),
    ))
    service = AgentVerbService(tmp_path / "state", policy, lambda: facts, agent_version="1.0.0")
    return service, policy.resolve_peer(1000, 100), checkout


def _put(service, identity, name, content):
    client, server = socket.socketpair()
    try:
        client.sendall(content)
        client.shutdown(socket.SHUT_WR)
        reply = service.handle({
            "verb": "put", "name": name, "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }, identity, body_stream=server)
        return reply.document
    finally:
        client.close()
        server.close()


def _job(service, identity, request=None):
    job_id = "controller-run-1"
    job_dir = service.paths.jobs_dir / job_id
    job_dir.mkdir()
    record = {
        "group": identity.group,
        "request": {"project": ".", "lane": "r0-r1"} if request is None else request,
    }
    (job_dir / "request.json").write_text(json.dumps(record), encoding="utf-8")
    return job_dir


def _body(reply):
    try:
        if reply.body_fd is None:
            return reply.body
        chunks = []
        remaining = reply.body_length
        while remaining:
            chunk = os.read(reply.body_fd, remaining)
            assert chunk
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        reply.close()


def test_probe_facts_and_have_return_host_owned_data(tmp_path):
    service, identity, checkout = _service(tmp_path)
    (checkout / ".git").write_text("gitdir: ../.git/worktrees/test\n", encoding="utf-8")

    probe = service.handle({"verb": "probe"}, identity)
    facts = service.handle({"verb": "facts"}, identity)
    have = service.handle({"verb": "have", "repo": "vbpub"}, identity)

    assert probe.document["schema"] == "fleet-host-agent/1"
    assert "submit" in probe.document["capabilities"]
    assert facts.document["schema"] == "host-facts/2"
    assert facts.document["policy"]["max_upload_bytes"]["v"] == 1073741824
    assert have.document["refs"] == ["a" * 40]


def test_submit_without_executor_refuses_before_admission_or_durable_write(tmp_path):
    service, identity, _checkout = _service(tmp_path)

    with pytest.raises(AdmissionError, match="executor unavailable") as error:
        service.handle(_submit(), identity)

    assert error.value.exit_code == ExitCode.REFUSAL
    assert list(service.paths.jobs_dir.iterdir()) == []
    assert list(service.admission.book.admitted_dir.iterdir()) == []
    assert list(service.admission.book.waiting_dir.iterdir()) == []


def test_lease_grant_bind_ack_and_reconnect_hold_the_admission_handle(tmp_path):
    policy, _checkout = _policy(tmp_path)
    raw_facts = collect_host_facts(
        FACTS_ROOT,
        disk_statvfs_reader=lambda _path: SimpleNamespace(
            f_frsize=4096, f_bsize=4096, f_blocks=1_000_000, f_bavail=750_000,
        ),
    )
    raw_facts["policy"]["priority_classes_by_principal"] = {
        "v": {"controller-devcontainer": ["gate"]},
    }
    raw_facts["policy"]["admission"] = {
        "v": {"queue_path": "/run/fleetd/queue", "wait_s": 30, "settle_s": 1},
    }
    facts = validate_host_facts(raw_facts)
    lease_id = "controller-run-1"
    container_id = "c" * 64
    service = AgentVerbService(
        tmp_path / "state", policy, lambda: facts, agent_version="1.0.0",
        legacy_jobs_provider=lambda: (),
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
        container_inspector=lambda inspected_id: {
            "id": inspected_id, "name": "rg-test",
            "labels": {"fleetd.lease": lease_id},
        },
    )
    identity = policy.resolve_peer(1000, 100)
    initial_request = {
        "verb": "lease", "lease_id": lease_id, "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    errors = []

    def serve(request, server):
        try:
            service.serve_lease(request, identity, server)
        except BaseException as exc:
            errors.append(exc)
            try:
                write_frame(server, {"event": "test-error", "reason": str(exc)})
            except Exception:
                pass
        finally:
            try:
                server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            server.close()

    client, server = socket.socketpair()
    first = threading.Thread(target=serve, args=(initial_request, server))
    first.start()
    grant = read_frame(client, None)
    assert grant["event"] == "grant", grant
    write_frame(client, {"event": "bind", "lease_id": lease_id, "container_id": container_id})
    bound = read_frame(client, None)
    assert bound["event"] == "bound"
    assert bound["container_id"] == container_id
    client.close()
    first.join()
    assert errors == []

    client, server = socket.socketpair()
    reconnect = threading.Thread(target=serve, args=(
        {"verb": "lease", "lease_id": lease_id, "container_id": container_id}, server,
    ))
    reconnect.start()
    resumed = read_frame(client, None)
    assert resumed["event"] == "bound"
    assert resumed["container_id"] == container_id
    client.close()
    reconnect.join()
    assert errors == []
    assert service.admission.book.status(lease_id, identity.group)["container_id"] == container_id


@pytest.mark.parametrize(
    ("content", "declared_bytes", "declared_sha", "message"),
    [
        (b"abc", 4, hashlib.sha256(b"abcd").hexdigest(), "truncated"),
        (b"abcdX", 4, hashlib.sha256(b"abcd").hexdigest(), "beyond its declared length"),
        (b"abcd", 4, hashlib.sha256(b"nope").hexdigest(), "does not match"),
    ],
)
def test_put_rejects_truncation_extra_bytes_and_wrong_digest_without_publishing(
    tmp_path, content, declared_bytes, declared_sha, message,
):
    service, identity, _checkout = _service(tmp_path)
    client, server = socket.socketpair()
    try:
        client.sendall(content)
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(AdmissionError, match=message):
            service.handle({
                "verb": "put", "name": "candidates", "sha256": declared_sha,
                "bytes": declared_bytes,
            }, identity, body_stream=server)
    finally:
        client.close()
        server.close()

    assert list(service.paths.incoming_dir.iterdir()) == []
    assert not (service.paths.uploads_dir / identity.group / "candidates").exists()


def test_put_creates_host_owned_content_addressed_candidate_file(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    content = b"candidate table\n"

    result = _put(service, identity, "candidates", content)

    path = Path(result["path"])
    assert path == service.paths.uploads_dir / identity.group / "candidates" / hashlib.sha256(content).hexdigest()
    assert path.read_bytes() == content
    assert service.paths.candidate_paths(identity.group) == frozenset({str(path.resolve())})


def test_put_refuses_a_symlinked_group_directory_before_writing_outside_state(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (service.paths.uploads_dir / identity.group).symlink_to(outside, target_is_directory=True)

    with pytest.raises(AdmissionError, match="symlink"):
        _put(service, identity, "candidates", b"candidate table")

    assert list(outside.iterdir()) == []


def test_fetch_checks_job_ownership_and_returns_only_the_requested_byte_range(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    content = b"0123456789"
    (job_dir / "job.log").write_bytes(content)

    reply = service.handle({
        "verb": "fetch", "job_id": "controller-run-1", "artifact": "job.log",
        "offset": 3, "length": 4,
    }, identity)

    assert reply.document["sha256"] == hashlib.sha256(content).hexdigest()
    assert reply.document["next_offset"] == 7
    assert reply.document["length"] == 4
    assert _body(reply) == b"3456"


def test_fetch_refuses_a_symlink_that_escapes_the_job_directory(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    outside = tmp_path / "outside.log"
    outside.write_text("private", encoding="utf-8")
    (job_dir / "job.log").symlink_to(outside)

    with pytest.raises(AdmissionError, match="escapes its job directory"):
        service.handle({
            "verb": "fetch", "job_id": "controller-run-1", "artifact": "job.log",
            "offset": 0, "length": 10,
        }, identity)


def test_assay_progress_uses_resolved_lane_and_streams_from_byte_offset(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    checkout = job_dir / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        '[lanes."r0-r1"]\nkind = "assay"\nassay_lane = "contract-assay"\n', encoding="utf-8",
    )
    progress = checkout / ".assay" / "progress-contract-assay.jsonl"
    progress.parent.mkdir()
    first = json.dumps({"event": "candidate", "candidate_id": "a" * 64}).encode() + b"\n"
    second = json.dumps({"event": "candidate", "candidate_id": "b" * 64}).encode() + b"\n"
    progress.write_bytes(first + second)

    reply = service.handle({
        "verb": "progress", "job_id": "controller-run-1", "offset": len(first),
    }, identity)

    assert reply.document["done"] == 2
    assert reply.document["offset"] == len(first)
    assert reply.document["unit"] == "assay_candidates"
    assert _body(reply) == second


def test_command_progress_uses_lane_log_byte_offset(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    checkout = job_dir / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        '[lanes."r0-r1"]\nkind = "command"\n', encoding="utf-8",
    )
    (job_dir / "lane.log").write_bytes(b"one\ntwo\n")

    reply = service.handle({
        "verb": "progress", "job_id": "controller-run-1", "offset": 4,
    }, identity)

    assert reply.document["done"] is None
    assert reply.document["unit"] == "log_bytes"
    assert _body(reply) == b"two\n"


def test_unknown_group_upload_directory_cannot_authorize_candidates(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (service.paths.uploads_dir / identity.group).symlink_to(outside, target_is_directory=True)

    with pytest.raises(AdmissionError, match="symlink"):
        service.paths.candidate_paths(identity.group)


def test_submit_candidate_allowlist_accepts_only_file_created_for_authenticated_group(tmp_path):
    service, identity, _checkout = _service(
        tmp_path, extra_argv='[["--only-candidates", "<host-candidates>"]]'
    )
    result = _put(service, identity, "candidates", b"candidate table")
    request = _submit()
    request["extra_argv"] = ["--only-candidates", result["path"]]

    with pytest.raises(AdmissionError, match="executor unavailable"):
        service.handle(request, identity)

    request["extra_argv"][1] = "/tmp/client-owned-candidates"
    with pytest.raises(AdmissionError, match="host-created candidates file"):
        service.handle(request, identity)

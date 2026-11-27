import hashlib
import json
import errno
import multiprocessing
import os
import io
import socket
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleetd.admission import AdmissionError, ExitCode
from fleetd.admission import InspectorAnswer
from fleetd.agent_verbs import AgentVerbService, MAX_ASSAY_PROGRESS_COUNT_BYTES
from fleetd import agent_verbs
from fleetd.agent_protocol import MAX_FETCH_BYTES, FrameError, read_frame, write_frame
from fleetd.contracts import HostFacts, validate_host_facts
from fleetd.facts import collect_host_facts
from test_agent_protocol import _policy, _submit


FACTS_ROOT = Path(__file__).parent / "fixtures" / "facts-root"


def _service(tmp_path, *, extra_argv='[["--verbose"]]', peer_credentials=None):
    identities = ""
    if peer_credentials is not None:
        uid, gid = peer_credentials
        identities = f'''[[identities]]
transport = "peer"
principal = "controller-devcontainer"
group = "controller"
uid = {uid}
gid = {gid}

[[identities]]
transport = "forced-command"
principal = "worker"
group = "worker"
'''
    policy, checkout = _policy(tmp_path, identities=identities, extra_argv=extra_argv)
    facts = validate_host_facts(collect_host_facts(
        FACTS_ROOT,
        disk_statvfs_reader=lambda _path: SimpleNamespace(
            f_frsize=4096, f_bsize=4096, f_blocks=1_000_000, f_bavail=750_000,
        ),
    ))
    service = AgentVerbService(tmp_path / "state", policy, lambda: facts, agent_version="1.0.0")
    peer_uid, peer_gid = (1000, 100) if peer_credentials is None else peer_credentials
    return service, policy.resolve_peer(peer_uid, peer_gid), checkout


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


def _direct_put(service, identity, name, content):
    client, server = socket.socketpair()
    try:
        client.sendall(content)
        client.shutdown(socket.SHUT_WR)
        reply = service._put({
            "name": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
        }, identity, server)
        return reply.document
    finally:
        client.close()
        server.close()


def _upload_policy(service, *, store_bytes=None, floors=None):
    document = service.facts_provider().to_dict()
    document["policy"]["max_upload_store_bytes"] = (
        {"v": store_bytes} if store_bytes is not None else {
            "v": None, "reason": "not-configured: test upload store limit",
        }
    )
    if floors is not None:
        document["policy"]["disk_floors"] = {"v": floors}
    facts = validate_host_facts(document)
    service.facts_provider = lambda: facts


def _upload_reservation_record(token, *, expected=1, remaining=1, temporary=None):
    return {
        "schema": "fleet-upload-reservation/1",
        "expected": expected,
        "remaining": remaining,
        "temporary": f"upload-part-{token}" if temporary is None else temporary,
    }


class _ObservedSocket:
    def __init__(self, stream, observed):
        self.stream = stream
        self.observed = observed

    def fileno(self):
        self.observed.set()
        return self.stream.fileno()


def _upload_process(service, identity, name, content, stream, observed, result):
    try:
        reply = service._put({
            "name": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
        }, identity, _ObservedSocket(stream, observed))
        result.send(("ok", reply.document))
    except AdmissionError as exc:
        result.send(("error", exc.exit_code, exc.reason))
    finally:
        stream.close()
        result.close()


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


def _lease_service(tmp_path, inspector, lease_id="controller-run-1", peer_credentials=None):
    identities = ""
    if peer_credentials is not None:
        uid, gid = peer_credentials
        identities = f'''[[identities]]
transport = "peer"
principal = "controller-devcontainer"
group = "controller"
uid = {uid}
gid = {gid}

[[identities]]
transport = "forced-command"
principal = "worker"
group = "worker"
'''
    policy, _checkout = _policy(tmp_path, identities=identities)
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
    service = AgentVerbService(
        tmp_path / "state", policy, lambda: facts, agent_version="1.0.0",
        legacy_jobs_provider=lambda: (),
        inspector=inspector,
        container_inspector=lambda inspected_id: {
            "id": inspected_id, "name": "rg-test",
            "labels": {"fleetd.lease": lease_id},
        },
    )
    uid, gid = (1000, 100) if peer_credentials is None else peer_credentials
    return service, policy.resolve_peer(uid, gid), facts


def _lease_worker(service, request, identity, server, outcomes, finished, on_lease_held=None):
    try:
        service.serve_lease(request, identity, server, on_lease_held=on_lease_held)
    except BaseException as exc:
        outcomes.append(exc)
    finally:
        server.close()
        finished.set()


def _assert_worker_finished(thread, finished):
    assert finished.wait(3), "lease worker did not finish before the failure watchdog"
    thread.join(timeout=3)
    assert not thread.is_alive(), "lease worker remained alive after its completion event"


def test_probe_facts_and_have_return_host_owned_data(tmp_path):
    service, identity, checkout = _service(tmp_path)
    assert service.reconcile_leases() == ()
    (checkout / ".git").write_text("gitdir: ../.git/worktrees/test\n", encoding="utf-8")

    probe = service.handle({"verb": "probe"}, identity)
    facts = service.handle({"verb": "facts"}, identity)
    have = service.handle({"verb": "have", "repo": "vbpub"}, identity)

    assert probe.document["schema"] == "fleet-host-agent/1"
    assert "error" not in probe.document
    assert "submit" in probe.document["capabilities"]
    assert facts.document["schema"] == "fleet-host-agent/1"
    assert "error" not in facts.document
    assert facts.document["facts"]["schema"] == "host-facts/2"
    assert facts.document["facts"]["policy"]["max_upload_bytes"]["v"] == 1073741824
    assert have.document["refs"] == ["a" * 40]
    assert "error" not in have.document


def test_success_envelope_refuses_the_reserved_error_field(tmp_path):
    service, _identity, _checkout = _service(tmp_path)

    with pytest.raises(ValueError, match="success response cannot contain the reserved error field"):
        service._envelope(error={"exit_code": ExitCode.REFUSAL, "reason": "refused"})


def test_submit_without_executor_refuses_before_admission_or_durable_write(tmp_path):
    service, identity, _checkout = _service(tmp_path)

    with pytest.raises(AdmissionError, match="executor unavailable") as error:
        service.handle(_submit(), identity)

    assert error.value.exit_code == ExitCode.REFUSAL
    assert list(service.paths.jobs_dir.iterdir()) == []
    assert list(service.admission.book.admitted_dir.iterdir()) == []
    assert list(service.admission.book.waiting_dir.iterdir()) == []


def test_lease_grant_bind_ack_and_reconnect_hold_the_admission_handle(tmp_path):
    lease_id = "controller-run-1"
    container_id = "c" * 64
    inspections = []
    service, identity, facts = _lease_service(
        tmp_path, lambda target: inspections.append(target) or InspectorAnswer.NO_SUCH_OBJECT,
    )
    assert "lease" in service.handle({"verb": "probe"}, identity).document["capabilities"]
    initial_request = {
        "verb": "lease", "lease_id": lease_id, "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    errors = []
    first_done = threading.Event()
    first_held = threading.Event()

    client, server = socket.socketpair()
    first = threading.Thread(target=_lease_worker, args=(
        service, initial_request, identity, server, errors, first_done, first_held.set,
    ))
    first.start()
    grant = read_frame(client, 3)
    assert grant["event"] == "grant", grant
    assert first_held.is_set()
    write_frame(client, {"event": "bind", "lease_id": lease_id, "container_id": container_id})
    bound = read_frame(client, 3)
    assert bound["event"] == "bound"
    assert bound["container_id"] == container_id
    client.close()
    _assert_worker_finished(first, first_done)
    assert errors == []
    assert service.admission.book.status(lease_id, identity.group)["released"] is False
    assert inspections and set(inspections) == {"rg-test"}

    reconnect_done = threading.Event()
    reconnect_held = threading.Event()

    client, server = socket.socketpair()
    reconnect = threading.Thread(target=_lease_worker, args=(
        service,
        {"verb": "lease", "lease_id": lease_id, "container_id": container_id},
        identity, server, errors, reconnect_done, reconnect_held.set,
    ))
    reconnect.start()
    resumed = read_frame(client, 3)
    assert resumed["event"] == "bound"
    assert resumed["container_id"] == container_id
    assert reconnect_held.is_set()
    client.close()
    _assert_worker_finished(reconnect, reconnect_done)
    assert errors == []
    assert service.admission.book.status(lease_id, identity.group)["container_id"] == container_id

    restart_inspections = []
    restarted = AgentVerbService(
        tmp_path / "state", service.policy, lambda: facts, agent_version="1.0.0",
        legacy_jobs_provider=lambda: (),
        inspector=lambda target: restart_inspections.append(target) or InspectorAnswer.NO_SUCH_OBJECT,
        container_inspector=lambda inspected_id: {
            "id": inspected_id, "name": "rg-test", "labels": {"fleetd.lease": lease_id},
        },
    )
    assert restart_inspections == [container_id]
    assert restarted.admission.book.status(lease_id, identity.group)["released"] is True


def test_prebind_disconnect_tombstones_then_reconciles_exact_name(tmp_path):
    lease_id = "controller-run-1"
    container_id = "c" * 64
    inspections = []

    client_disconnected = threading.Event()
    failed_tombstone_inspection = threading.Event()

    def inspect(target):
        inspections.append(target)
        if client_disconnected.is_set() and not failed_tombstone_inspection.is_set():
            failed_tombstone_inspection.set()
            raise RuntimeError("inspector unavailable")
        return InspectorAnswer.NO_SUCH_OBJECT

    service, identity, _facts = _lease_service(tmp_path, inspect)
    request = {
        "verb": "lease", "lease_id": lease_id, "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    outcomes = []
    finished = threading.Event()
    client, server = socket.socketpair()
    worker = threading.Thread(target=_lease_worker, args=(service, request, identity, server, outcomes, finished))
    worker.start()
    grant = read_frame(client, 3)
    assert grant["event"] == "grant"
    client_disconnected.set()
    client.close()
    _assert_worker_finished(worker, finished)

    record = service.admission.book.status(lease_id, identity.group)
    assert isinstance(outcomes[0], FrameError)
    assert record["state"] == "tombstone"
    assert record["released"] is False
    assert failed_tombstone_inspection.is_set()
    assert inspections and set(inspections) == {"rg-test"}

    reconcile = service.admission.book.reconcile_lease
    service.admission.book.reconcile_lease = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("record reconciliation failed"),
    )
    assert service.reconcile_leases() == ()
    assert service.admission.book.status(lease_id, identity.group)["released"] is False
    service.admission.book.reconcile_lease = reconcile

    service.reconcile_leases()
    assert service.admission.book.status(lease_id, identity.group)["released"] is True
    assert inspections and set(inspections) == {"rg-test"}
    with pytest.raises(AdmissionError, match="revoked lease") as error:
        service.admission.reserve_lease(lease_id, identity, container_id=container_id)
    assert error.value.exit_code == ExitCode.REFUSAL
    with pytest.raises(AdmissionError, match="revoked lease") as duplicate_error:
        service.admission.reserve_lease(
            lease_id, identity, container_name="rg-test", footprint="tiny",
            planned_cores=1.0, priority="gate",
        )
    assert duplicate_error.value.exit_code == ExitCode.REFUSAL


@pytest.mark.parametrize("cleanup_stage", ["tombstone", "inspect"])
def test_prebind_cleanup_failures_keep_the_lease_charge(tmp_path, cleanup_stage):
    service, identity, _facts = _lease_service(
        tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT,
    )
    lease_id = "controller-run-1"
    request = {
        "verb": "lease", "lease_id": lease_id, "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    if cleanup_stage == "tombstone":
        service.admission.revoke_lease = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("cannot persist tombstone"),
        )
    else:
        service.admission.reconcile_lease = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("inspection transport failed"),
        )
    outcomes = []
    finished = threading.Event()
    client, server = socket.socketpair()
    worker = threading.Thread(target=_lease_worker, args=(service, request, identity, server, outcomes, finished))
    worker.start()
    grant = read_frame(client, 3)
    assert grant["event"] == "grant"
    client.close()
    _assert_worker_finished(worker, finished)

    record = service.admission.book.status(lease_id, identity.group)
    assert isinstance(outcomes[0], FrameError)
    assert record["released"] is False
    assert record["state"] == ("pending" if cleanup_stage == "tombstone" else "tombstone")


def test_pregrant_disconnect_withdraws_connection_bound_waiter(tmp_path):
    service, identity, _facts = _lease_service(tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT)
    request = {
        "verb": "lease", "lease_id": "controller-run-1", "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }

    def waiting_reservation(_request, *, waiter, **_kwargs):
        waiter(0.0)
        raise AssertionError("disconnected waiter must be rejected before grant")

    service.admission.book.reserve_lease = waiting_reservation
    outcomes = []
    finished = threading.Event()
    client, server = socket.socketpair()
    client.close()
    worker = threading.Thread(target=_lease_worker, args=(service, request, identity, server, outcomes, finished))
    worker.start()
    _assert_worker_finished(worker, finished)

    assert isinstance(outcomes[0], AdmissionError)
    assert outcomes[0].exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    assert list(service.admission.book.leases_dir.iterdir()) == []


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


def test_put_store_limit_allows_equality_refuses_plus_one_and_charges_reuploads(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=5, floors={"checkout": 0})

    first = _put(service, identity, "candidates", b"four")
    second = _put(service, identity, "candidates", b"5")
    assert Path(first["path"]).read_bytes() == b"four"
    assert Path(second["path"]).read_bytes() == b"5"

    with pytest.raises(AdmissionError, match="max_upload_store_bytes") as plus_one:
        _put(service, identity, "candidates", b"x")
    assert plus_one.value.exit_code == ExitCode.REFUSAL
    with pytest.raises(AdmissionError, match="max_upload_store_bytes") as reupload:
        _put(service, identity, "candidates", b"four")
    assert reupload.value.exit_code == ExitCode.REFUSAL


def test_concurrent_puts_reserve_across_processes_before_reading_their_bodies(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=4, floors={"checkout": 0})
    context = multiprocessing.get_context("fork")
    first_content = b"abc"
    second_content = b"xy"
    first_client, first_server = socket.socketpair()
    first_observed = context.Event()
    first_receive, first_send = context.Pipe(duplex=False)
    first = context.Process(
        target=_upload_process,
    args=(service, identity, "candidates", first_content, first_server, first_observed, first_send),
    )
    second_client = second_server = None
    second_receive = second_send = None
    second_observed = None
    second = None
    try:
        first.start()
        first_server.close()
        first_send.close()
        assert first_observed.wait(3), "first put did not reserve before reading its body"

        second_client, second_server = socket.socketpair()
        second_observed = context.Event()
        second_receive, second_send = context.Pipe(duplex=False)
        second = context.Process(
            target=_upload_process,
            args=(service, identity, "candidates", second_content, second_server, second_observed, second_send),
        )
        second.start()
        second_server.close()
        second_send.close()
        assert second_receive.poll(3), "second put did not return while the first body was parked"
        second_reply = second_receive.recv()
        assert second_reply[0:2] == ("error", ExitCode.REFUSAL)
        assert "max_upload_store_bytes" in second_reply[2]
        assert first.is_alive()
        assert any(entry.name.startswith("upload-part-") for entry in service.paths.incoming_dir.iterdir())

        first_client.sendall(first_content)
        first_client.shutdown(socket.SHUT_WR)
        assert first_receive.poll(3), "first put did not finish after its body was supplied"
        first_reply = first_receive.recv()
        assert first_reply[0] == "ok"
        assert Path(first_reply[1]["path"]).read_bytes() == first_content
    finally:
        for client, content in ((first_client, first_content), (second_client, second_content)):
            if client is not None:
                try:
                    client.sendall(content)
                    client.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                client.close()
        first.join(3)
        if second is not None:
            second.join(3)
        assert not first.is_alive(), "first upload worker did not exit"
        if second is not None:
            assert not second.is_alive(), "second upload worker did not exit"
        first_receive.close()
        if second_receive is not None:
            second_receive.close()


def test_put_disk_floor_reservation_subtracts_live_remaining_bytes(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(tmp_path)
    monkeypatch.setattr(
        agent_verbs.os, "statvfs",
        lambda _path: SimpleNamespace(f_bavail=10, f_frsize=1),
    )
    reservation = service._reserve_upload(3, 100, 6)
    try:
        with pytest.raises(AdmissionError, match="breach configured disk floor") as floor:
            service._reserve_upload(2, 100, 6)
        assert floor.value.exit_code == ExitCode.REFUSAL
        assert Path(reservation.temporary_name).exists()
    finally:
        service._discard_upload_reservation(reservation)
        service._release_upload_reservation(reservation)


def test_put_counts_regular_files_across_groups_without_following_symlinks(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=3, floors={"checkout": 0})
    other_group = service.paths.uploads_dir / "another-group" / "artifact"
    other_group.mkdir(parents=True)
    (other_group / "stored-object").write_bytes(b"12")
    outside = tmp_path / "outside-large-object"
    outside.write_bytes(b"not counted" * 100)
    (other_group / "external-link").symlink_to(outside)

    reply = _put(service, identity, "candidates", b"3")

    assert Path(reply["path"]).read_bytes() == b"3"
    assert outside.read_bytes() == b"not counted" * 100


def test_put_refuses_unreadable_stored_regular_files(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    stored = service.paths.uploads_dir / "other" / "artifact" / "object"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"stored")
    real_open = os.open

    def refuse_stored(path, flags, *args, **kwargs):
        if Path(path) == stored and flags & os.O_ACCMODE == os.O_RDONLY:
            raise PermissionError("stored object unreadable")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "open", refuse_stored)
    with pytest.raises(AdmissionError, match="cannot read stored upload") as unreadable:
        _put(service, identity, "candidates", b"new")
    assert unreadable.value.exit_code == ExitCode.RUNNER_FAILURE
    assert stored.read_bytes() == b"stored"


def test_put_refuses_below_disk_floor_and_when_store_limit_is_unknown(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 10, "docker": 20})
    monkeypatch.setattr(
        agent_verbs.os, "statvfs",
        lambda _path: SimpleNamespace(f_bavail=19, f_frsize=1),
    )
    with pytest.raises(AdmissionError, match="breach configured disk floor") as floor:
        _put(service, identity, "candidates", b"a")
    assert floor.value.exit_code == ExitCode.REFUSAL

    _upload_policy(service, store_bytes=None, floors={"checkout": 0})
    with pytest.raises(AdmissionError, match="max_upload_store_bytes unavailable") as unknown:
        _put(service, identity, "candidates", b"a")
    assert unknown.value.exit_code == ExitCode.REFUSAL


@pytest.mark.parametrize(
    ("field", "wrapper", "reason"),
    [
        ("max_upload_bytes", {"v": None, "reason": "per-object cap missing"}, "max_upload_bytes unavailable"),
        ("disk_floors", {"v": None, "reason": "disk floors missing"}, "disk_floors unavailable"),
        ("disk_floors", {"v": {}}, "no configured floors"),
    ],
)
def test_put_refuses_when_each_required_upload_fact_is_unavailable(tmp_path, field, wrapper, reason):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    policy = service.facts_provider().to_dict()["policy"]
    policy[field] = wrapper
    service._facts = lambda: {"policy": policy}

    with pytest.raises(AdmissionError, match=reason) as unavailable:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert unavailable.value.exit_code == ExitCode.REFUSAL


def test_upload_store_lock_refuses_symlink_private_mode_and_lock_errors(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    lock_path = service.paths.state_dir / ".upload-store.lock"
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"untouched")
    lock_path.symlink_to(outside)
    with pytest.raises(AdmissionError, match="cannot lock upload store") as linked:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert linked.value.exit_code == ExitCode.RUNNER_FAILURE
    assert outside.read_bytes() == b"untouched"

    lock_path.unlink()
    lock_path.write_bytes(b"lock")
    lock_path.chmod(0o644)
    with pytest.raises(AdmissionError, match="not a private regular file") as public:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert public.value.exit_code == ExitCode.REFUSAL

    lock_path.unlink()
    real_open = os.open
    opened_fds = []

    def capture_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if Path(path) == lock_path:
            opened_fds.append(fd)
        return fd

    real_flock = agent_verbs.fcntl.flock

    def fail_exclusive(fd, operation):
        if operation == agent_verbs.fcntl.LOCK_EX:
            raise OSError("upload store lock unavailable")
        return real_flock(fd, operation)

    monkeypatch.setattr(agent_verbs.os, "open", capture_open)
    monkeypatch.setattr(agent_verbs.fcntl, "flock", fail_exclusive)
    with pytest.raises(AdmissionError, match="cannot lock upload store accounting") as failed:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert failed.value.exit_code == ExitCode.RUNNER_FAILURE
    assert len(opened_fds) == 1
    with pytest.raises(OSError):
        os.fstat(opened_fds[0])


def test_put_recovers_dead_partial_and_restart_counts_committed_objects(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=4, floors={"checkout": 0})
    committed = _put(service, identity, "candidates", b"four")
    restarted = AgentVerbService(
        tmp_path / "state", service.policy, service.facts_provider, agent_version="1.0.0",
    )
    restarted_identity = identity
    _upload_policy(restarted, store_bytes=4, floors={"checkout": 0})
    with pytest.raises(AdmissionError, match="max_upload_store_bytes") as stored_limit:
        _put(restarted, restarted_identity, "candidates", b"x")
    assert stored_limit.value.exit_code == ExitCode.REFUSAL
    assert Path(committed["path"]).read_bytes() == b"four"

    _upload_policy(restarted, store_bytes=6, floors={"checkout": 0})
    reservation = restarted._reserve_upload(2, 6, 0)
    os.write(reservation.temporary_fd, b"p")
    restarted._update_upload_remaining(reservation, 1)
    partial = Path(reservation.temporary_name)
    record = reservation.path
    os.close(reservation.temporary_fd)
    reservation.temporary_fd = -1
    restarted._release_upload_reservation(reservation)

    fresh = _put(restarted, restarted_identity, "candidates", b"ok")
    assert Path(fresh["path"]).read_bytes() == b"ok"
    assert not partial.exists()
    assert not record.exists()


@pytest.mark.parametrize(
    "record_bytes",
    [
        b"x" * 4097,
        b"{not json",
        json.dumps([]).encode(),
        json.dumps({"schema": "bad"}).encode(),
        json.dumps({**_upload_reservation_record("token"), "expected": True}).encode(),
        json.dumps({**_upload_reservation_record("token"), "expected": -1, "remaining": 0}).encode(),
        json.dumps({**_upload_reservation_record("token"), "remaining": True}).encode(),
        json.dumps({**_upload_reservation_record("token"), "remaining": 2}).encode(),
        json.dumps({**_upload_reservation_record("token"), "temporary": "other"}).encode(),
        json.dumps({"schema": "fleet-upload-reservation/1", "expected": 1, "remaining": 1}).encode(),
    ],
)
def test_put_refuses_malformed_or_oversized_reservation_records(tmp_path, record_bytes):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    reservation = service.paths.incoming_dir / ".upload-reservation-token.json"
    partial = service.paths.incoming_dir / "upload-part-token"
    reservation.write_bytes(record_bytes)
    partial.write_bytes(b"partial")

    with pytest.raises(AdmissionError) as invalid:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert invalid.value.exit_code == ExitCode.RUNNER_FAILURE
    assert reservation.exists()
    assert partial.read_bytes() == b"partial"


def test_put_recovery_rejects_invalid_reservation_names_and_symlink_partials(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    malformed_name = service.paths.incoming_dir / ".upload-reservation-!.json"
    malformed_name.write_text("{}", encoding="utf-8")
    with pytest.raises(AdmissionError, match="filename is malformed") as name_error:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert name_error.value.exit_code == ExitCode.RUNNER_FAILURE
    malformed_name.unlink()

    reservation = service.paths.incoming_dir / ".upload-reservation-token.json"
    partial = service.paths.incoming_dir / "upload-part-token"
    outside = tmp_path / "outside-partial"
    outside.write_bytes(b"preserve")
    reservation.write_text(json.dumps(_upload_reservation_record("token")), encoding="utf-8")
    partial.symlink_to(outside)
    with pytest.raises(AdmissionError, match="not a regular file") as partial_error:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert partial_error.value.exit_code == ExitCode.RUNNER_FAILURE
    assert outside.read_bytes() == b"preserve"


def test_put_reaps_orphan_partials_and_maps_reservation_scan_failures(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    orphan = service.paths.incoming_dir / "upload-part-orphan"
    orphan.write_bytes(b"dead before reservation")
    reply = _put(service, identity, "candidates", b"new")
    assert Path(reply["path"]).read_bytes() == b"new"
    assert not orphan.exists()

    real_scandir = os.scandir
    incoming = service.paths.incoming_dir

    def fail_scan(path):
        if Path(path) == incoming:
            raise PermissionError("reservation directory unreadable")
        return real_scandir(path)

    monkeypatch.setattr(agent_verbs.os, "scandir", fail_scan)
    with pytest.raises(AdmissionError, match="cannot inspect upload reservations") as scan_error:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert scan_error.value.exit_code == ExitCode.RUNNER_FAILURE


def test_put_recovery_maps_disappearing_reservation_and_flock_errors(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    reservation = service.paths.incoming_dir / ".upload-reservation-token.json"
    reservation.write_text(json.dumps(_upload_reservation_record("token")), encoding="utf-8")
    real_open = os.open

    def disappear(path, flags, *args, **kwargs):
        if Path(path) == reservation:
            reservation.unlink()
            raise FileNotFoundError("reservation disappeared")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "open", disappear)
    with pytest.raises(AdmissionError, match="changed during recovery") as vanished:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert vanished.value.exit_code == ExitCode.RUNNER_FAILURE
    monkeypatch.undo()

    reservation.write_text(json.dumps(_upload_reservation_record("token")), encoding="utf-8")
    real_open = os.open

    def fail_reservation_open(path, flags, *args, **kwargs):
        if Path(path) == reservation:
            raise PermissionError("reservation cannot be opened")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "open", fail_reservation_open)
    with pytest.raises(AdmissionError, match="cannot recover upload reservation") as open_error:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert open_error.value.exit_code == ExitCode.RUNNER_FAILURE
    monkeypatch.undo()

    reservation.write_text(json.dumps(_upload_reservation_record("token")), encoding="utf-8")
    real_unlink = os.unlink

    def disappear_during_recovery(path, *args, **kwargs):
        if Path(path) == reservation:
            real_unlink(path, *args, **kwargs)
            raise FileNotFoundError("reservation disappeared during recovery")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "unlink", disappear_during_recovery)
    with pytest.raises(AdmissionError, match="changed during recovery") as unlink_error:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert unlink_error.value.exit_code == ExitCode.RUNNER_FAILURE
    monkeypatch.undo()

    reservation.write_text(json.dumps(_upload_reservation_record("token")), encoding="utf-8")
    real_flock = agent_verbs.fcntl.flock

    def fail_reservation_flock(fd, operation):
        if operation == agent_verbs.fcntl.LOCK_EX | agent_verbs.fcntl.LOCK_NB:
            raise OSError("reservation flock failed")
        return real_flock(fd, operation)

    monkeypatch.setattr(agent_verbs.fcntl, "flock", fail_reservation_flock)
    with pytest.raises(AdmissionError, match="cannot recover upload reservation") as flock_error:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert flock_error.value.exit_code == ExitCode.RUNNER_FAILURE


@pytest.mark.parametrize("failure", ["scan", "entry-stat"])
def test_put_stored_upload_scan_errors_refuse_without_reading_body(tmp_path, monkeypatch, failure):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    uploads = service.paths.uploads_dir
    real_scandir = os.scandir
    if failure == "scan":
        def failing_scandir(path):
            if Path(path) == uploads:
                raise PermissionError("stored uploads cannot be scanned")
            return real_scandir(path)
        expected = "cannot inspect stored uploads"
    else:
        def failing_scandir(path):
            if Path(path) == uploads:
                entry = SimpleNamespace(name="unreadable-entry", path=str(uploads / "unreadable-entry"))
                entry.stat = lambda **_kwargs: (_ for _ in ()).throw(OSError("entry stat failed"))
                return [entry]
            return real_scandir(path)
        expected = "cannot inspect stored upload"
    monkeypatch.setattr(agent_verbs.os, "scandir", failing_scandir)

    with pytest.raises(AdmissionError, match=expected) as unreadable:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert unreadable.value.exit_code == ExitCode.RUNNER_FAILURE


@pytest.mark.parametrize("failure", ["changed-type", "fstat"])
def test_put_stored_file_descriptor_validation_refuses_races(tmp_path, monkeypatch, failure):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    stored = service.paths.uploads_dir / "other" / "artifact" / "object"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"stored")
    real_open = os.open
    opened = set()

    def capture_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if Path(path) == stored:
            opened.add(fd)
        return fd

    real_fstat = os.fstat

    def invalid_fstat(fd):
        if fd in opened:
            if failure == "fstat":
                raise OSError("stored upload fstat failed")
            return SimpleNamespace(st_mode=agent_verbs.stat.S_IFDIR, st_size=0)
        return real_fstat(fd)

    monkeypatch.setattr(agent_verbs.os, "open", capture_open)
    monkeypatch.setattr(agent_verbs.os, "fstat", invalid_fstat)
    reason = "cannot inspect stored upload" if failure == "fstat" else "changed type"
    with pytest.raises(AdmissionError, match=reason) as invalid:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert invalid.value.exit_code == ExitCode.RUNNER_FAILURE
    assert stored.read_bytes() == b"stored"


@pytest.mark.parametrize("failure", ["statvfs", "invalid-values"])
def test_put_free_space_query_failures_are_closed_refusals(tmp_path, monkeypatch, failure):
    service, identity, _checkout = _service(tmp_path)
    if failure == "statvfs":
        monkeypatch.setattr(
            agent_verbs.os, "statvfs",
            lambda _path: (_ for _ in ()).throw(OSError("statvfs failed")),
        )
        reason = "cannot inspect upload filesystem free space"
    else:
        monkeypatch.setattr(
            agent_verbs.os, "statvfs",
            lambda _path: SimpleNamespace(f_bavail=True, f_frsize=1),
        )
        reason = "invalid available-byte facts"
    with pytest.raises(AdmissionError, match=reason) as unavailable:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert unavailable.value.exit_code == ExitCode.RUNNER_FAILURE


def test_put_rejects_a_symlinked_incoming_directory(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    incoming = service.paths.incoming_dir
    moved = tmp_path / "moved-incoming"
    incoming.rename(moved)
    incoming.symlink_to(moved, target_is_directory=True)

    with pytest.raises(AdmissionError, match="incoming directory is not a real directory") as unsafe:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert unsafe.value.exit_code == ExitCode.REFUSAL
    assert list(moved.iterdir()) == []


def test_put_recovers_orphan_scan_errors_after_reservation_scan(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    incoming = service.paths.incoming_dir
    real_scandir = os.scandir
    calls = [0]

    def fail_orphan_scan(path):
        if Path(path) == incoming:
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("orphan scan failed")
        return real_scandir(path)

    monkeypatch.setattr(agent_verbs.os, "scandir", fail_orphan_scan)
    with pytest.raises(AdmissionError, match="cannot recover orphaned upload partials") as scan_error:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert scan_error.value.exit_code == ExitCode.RUNNER_FAILURE


def test_put_reservation_write_zero_progress_and_fsync_cleanup(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    monkeypatch.setattr(agent_verbs.os, "write", lambda *_args: 0)
    with pytest.raises(AdmissionError, match="cannot store put body") as no_progress:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert no_progress.value.exit_code == ExitCode.RUNNER_FAILURE
    assert list(service.paths.incoming_dir.iterdir()) == []

    monkeypatch.undo()
    monkeypatch.setattr(
        agent_verbs.os, "fsync",
        lambda *_args: (_ for _ in ()).throw(OSError("reservation fsync failed")),
    )
    with pytest.raises(AdmissionError, match="cannot store put body") as sync_error:
        service._put({"bytes": 1, "name": "candidates", "sha256": "a" * 64}, identity, object())
    assert sync_error.value.exit_code == ExitCode.RUNNER_FAILURE
    assert list(service.paths.incoming_dir.iterdir()) == []


def test_put_ignores_non_regular_upload_entries_without_following_them(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=4, floors={"checkout": 0})
    uploads = service.paths.uploads_dir
    real_scandir = os.scandir

    def scan_with_fifo(path):
        if Path(path) == uploads:
            fifo = SimpleNamespace(name="pipe", path=str(uploads / "pipe"))
            fifo.stat = lambda *, follow_symlinks: SimpleNamespace(st_mode=agent_verbs.stat.S_IFIFO)
            return [fifo]
        return real_scandir(path)

    monkeypatch.setattr(agent_verbs.os, "scandir", scan_with_fifo)
    reply = _put(service, identity, "candidates", b"data")

    assert Path(reply["path"]).read_bytes() == b"data"


def test_put_refuses_upload_root_replaced_with_symlink_after_body(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    content = b"body then root swap"
    client, server = socket.socketpair()
    moved = tmp_path / "moved-upload-root"
    original_stream = service._stream_upload

    def swap_root_after_body(*args):
        original_stream(*args)
        service.paths.uploads_dir.rename(moved)
        service.paths.uploads_dir.symlink_to(moved, target_is_directory=True)

    monkeypatch.setattr(service, "_stream_upload", swap_root_after_body)
    try:
        client.sendall(content)
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(AdmissionError, match="agent upload directory is not a real directory") as swapped:
            service._put({
                "name": "candidates", "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
            }, identity, server)
        assert swapped.value.exit_code == ExitCode.REFUSAL
        assert list(moved.iterdir()) == []
        assert list(service.paths.incoming_dir.iterdir()) == []
    finally:
        client.close()
        server.close()
        service.paths.uploads_dir.unlink()
        moved.rename(service.paths.uploads_dir)


def test_put_cleans_descriptor_and_reservation_when_fdopen_fails(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=10, floors={"checkout": 0})
    descriptors = []

    def fail_fdopen(fd, *_args, **_kwargs):
        descriptors.append(fd)
        raise OSError("fdopen failed")

    monkeypatch.setattr(agent_verbs.os, "fdopen", fail_fdopen)
    with pytest.raises(AdmissionError, match="cannot store put body") as failed:
        service._put({
            "name": "candidates", "sha256": hashlib.sha256(b"x").hexdigest(), "bytes": 1,
        }, identity, object())

    assert failed.value.exit_code == ExitCode.RUNNER_FAILURE
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert list(service.paths.incoming_dir.iterdir()) == []


def test_put_reservation_setup_tolerates_partial_cleanup_races(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=10, floors={"checkout": 0})
    real_unlink = os.unlink
    removed = []

    def unlink_then_report_missing(path, *args, **kwargs):
        candidate = Path(path)
        if candidate.parent == service.paths.incoming_dir and (
            candidate.name.startswith("upload-part-")
            or candidate.name.startswith(".upload-reservation-")
        ):
            real_unlink(path, *args, **kwargs)
            removed.append(candidate.name)
            raise FileNotFoundError(candidate)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "unlink", unlink_then_report_missing)
    monkeypatch.setattr(
        service, "_fsync_directory",
        lambda _path: (_ for _ in ()).throw(OSError("reservation directory sync failed")),
    )
    with pytest.raises(AdmissionError, match="cannot store put body") as failed:
        service._put({
            "name": "candidates", "sha256": hashlib.sha256(b"").hexdigest(), "bytes": 0,
        }, identity, object())

    assert failed.value.exit_code == ExitCode.RUNNER_FAILURE
    assert any(name.startswith("upload-part-") for name in removed)
    assert any(name.startswith(".upload-reservation-") for name in removed)
    assert list(service.paths.incoming_dir.iterdir()) == []


def test_put_discard_and_release_are_idempotent_when_reservation_was_removed(tmp_path):
    service, _identity, _checkout = _service(tmp_path)
    reservation = service._reserve_upload(1, 10, 0)
    fd = reservation.fd
    reservation.path.unlink()

    service._discard_upload_reservation(reservation)
    service._release_upload_reservation(reservation)
    service._release_upload_reservation(reservation)

    assert not Path(reservation.temporary_name).exists()
    assert not reservation.path.exists()
    assert reservation.fd == -1
    with pytest.raises(OSError):
        os.fstat(fd)


def test_put_refuses_a_symlinked_group_directory_before_writing_outside_state(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (service.paths.uploads_dir / identity.group).symlink_to(outside, target_is_directory=True)

    with pytest.raises(AdmissionError, match="symlink"):
        _put(service, identity, "candidates", b"candidate table")

    assert list(outside.iterdir()) == []

    # Exercise the second check inside _put after the public candidate scan rejects it.
    with pytest.raises(AdmissionError, match="put group directory is a symlink") as internal_check:
        _direct_put(service, identity, "candidates", b"candidate table")
    assert internal_check.value.exit_code == ExitCode.REFUSAL
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


def test_progress_refuses_symlinked_lane_config_and_progress_components(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    checkout = job_dir / "checkout"
    checkout.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "run-gate.toml").write_text('[lanes."r0-r1"]\nkind = "command"\n', encoding="utf-8")
    (checkout / "run-gate.toml").symlink_to(outside / "run-gate.toml")

    with pytest.raises(AdmissionError, match="lane declaration path is unsafe") as config_error:
        service.handle({"verb": "progress", "job_id": "controller-run-1", "offset": 0}, identity)
    assert config_error.value.exit_code == ExitCode.REFUSAL

    (checkout / "run-gate.toml").unlink()
    (checkout / "run-gate.toml").write_text('[lanes."r0-r1"]\nkind = "command"\n', encoding="utf-8")
    (job_dir / "lane.log").symlink_to(outside / "run-gate.toml")
    with pytest.raises(AdmissionError, match="progress path .* is unsafe") as progress_error:
        service.handle({"verb": "progress", "job_id": "controller-run-1", "offset": 0}, identity)
    assert progress_error.value.exit_code == ExitCode.REFUSAL


def test_assay_progress_refuses_symlinked_project_path_component(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity, {"project": "project", "lane": "r0-r1"})
    checkout = job_dir / "checkout"
    checkout.mkdir()
    outside = tmp_path / "outside-project"
    outside.mkdir()
    (outside / "run-gate.toml").write_text('[lanes."r0-r1"]\nkind = "assay"\n', encoding="utf-8")
    (checkout / "project").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AdmissionError, match="progress path is unsafe") as raised:
        service.handle({"verb": "progress", "job_id": "controller-run-1", "offset": 0}, identity)
    assert raised.value.exit_code == ExitCode.REFUSAL


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


@pytest.mark.parametrize(
    ("size_delta", "expected_done"),
    [(-1, 1), (0, 1), (1, None)],
)
def test_assay_progress_done_count_is_bounded_at_eight_mib(tmp_path, size_delta, expected_done):
    assert MAX_ASSAY_PROGRESS_COUNT_BYTES == 8 * 1024 * 1024
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    checkout = job_dir / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        '[lanes."r0-r1"]\nkind = "assay"\nassay_lane = "contract-assay"\n', encoding="utf-8",
    )
    progress = checkout / ".assay" / "progress-contract-assay.jsonl"
    progress.parent.mkdir()
    candidate = json.dumps({"event": "candidate", "candidate_id": "counted"}).encode() + b"\n"
    progress.write_bytes(candidate)
    size = MAX_ASSAY_PROGRESS_COUNT_BYTES + size_delta
    with progress.open("r+b") as stream:
        stream.truncate(size)

    reply = service.handle({
        "verb": "progress", "job_id": "controller-run-1", "offset": size,
    }, identity)
    try:
        assert reply.document["done"] == expected_done
        if size_delta == 1:
            assert isinstance(reply.document["done_reason"], str)
            assert reply.document["done_reason"]
            assert "8 MiB" in reply.document["done_reason"] or "8388608" in reply.document["done_reason"]
        else:
            assert "done_reason" not in reply.document
        assert reply.document["offset"] == size
        assert reply.document["next_offset"] == size
        assert reply.document["length"] == 0
    finally:
        reply.close()


def test_assay_progress_count_stops_at_opened_size_when_file_grows(tmp_path, monkeypatch):
    progress = tmp_path / "progress.jsonl"
    candidate = json.dumps({"event": "candidate", "candidate_id": "before"}).encode() + b"\n"
    progress.write_bytes(candidate)
    with progress.open("r+b") as stream:
        stream.truncate(MAX_ASSAY_PROGRESS_COUNT_BYTES)
    opened_size = progress.stat().st_size
    fd = os.open(progress, os.O_RDONLY)
    original_read = os.read
    total_read = 0
    grew = False
    later = json.dumps({"event": "candidate", "candidate_id": "after"}).encode() + b"\n"

    def grow_then_read(read_fd, count):
        nonlocal grew, total_read
        if not grew:
            with progress.open("ab") as stream:
                stream.write(later)
            grew = True
        chunk = original_read(read_fd, count)
        total_read += len(chunk)
        return chunk

    monkeypatch.setattr(agent_verbs.os, "read", grow_then_read)
    try:
        assert AgentVerbService._assay_done_count(fd, opened_size) == 1
        assert grew
        assert total_read == MAX_ASSAY_PROGRESS_COUNT_BYTES
        assert progress.stat().st_size > opened_size
    finally:
        os.close(fd)


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


def test_progress_caps_each_range_and_reports_the_next_offset(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    checkout = job_dir / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        '[lanes."r0-r1"]\nkind = "command"\n', encoding="utf-8",
    )
    progress = job_dir / "lane.log"
    with progress.open("wb") as stream:
        stream.truncate(MAX_FETCH_BYTES + 19)

    reply = service.handle({
        "verb": "progress", "job_id": "controller-run-1", "offset": 7,
    }, identity)
    try:
        assert reply.document["offset"] == 7
        assert reply.document["length"] == MAX_FETCH_BYTES
        assert reply.document["next_offset"] == 7 + MAX_FETCH_BYTES
        assert reply.body_length == MAX_FETCH_BYTES
    finally:
        reply.close()


def test_missing_command_progress_returns_an_empty_range(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    checkout = job_dir / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        '[lanes."r0-r1"]\nkind = "command"\n', encoding="utf-8",
    )

    reply = service.handle({
        "verb": "progress", "job_id": "controller-run-1", "offset": 9,
    }, identity)

    assert reply.document["offset"] == 9
    assert reply.document["next_offset"] == 9
    assert reply.document["length"] == 0
    assert reply.document["unit"] == "log_bytes"


def test_terminal_status_uses_only_durable_record_and_cancel_refuses(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    terminal = service.admission.book._terminal_path("controller-run-1")
    terminal.write_text(json.dumps({
        "schema": "fleet-admission-terminal/1", "job_id": "controller-run-1",
        "group": identity.group, "digest": "a" * 64, "state": "completed", "completed_at": 42,
    }), encoding="utf-8")

    status = service.handle({"verb": "status", "job_id": "controller-run-1"}, identity)
    assert status.document["state"] == "completed"
    assert status.document["completed_at"] == 42
    assert "verdict" not in status.document
    assert "exit_code" not in status.document

    with pytest.raises(AdmissionError, match="finished job .* cannot be cancelled") as raised:
        service.handle({"verb": "cancel", "job_id": "controller-run-1", "reason": "done"}, identity)
    assert raised.value.exit_code == ExitCode.REFUSAL


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


def test_agent_paths_refuse_symlinks_unprivate_modes_and_creation_failures(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    linked_root = tmp_path / "linked-state"
    linked_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AdmissionError, match="must not be a symlink") as root_link:
        agent_verbs.AgentPaths.initialize(linked_root)
    assert root_link.value.exit_code == ExitCode.REFUSAL

    shared_root = tmp_path / "shared-state"
    shared_root.mkdir(mode=0o755)
    with pytest.raises(AdmissionError, match="accessible by other users") as root_mode:
        agent_verbs.AgentPaths.initialize(shared_root)
    assert root_mode.value.exit_code == ExitCode.REFUSAL

    failed_root = tmp_path / "failed-state"
    real_mkdir = Path.mkdir

    def fail_jobs(path, *args, **kwargs):
        if path == failed_root / "jobs":
            raise OSError("jobs creation refused")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_jobs)
    with pytest.raises(AdmissionError, match="cannot initialize agent state directory") as create_error:
        agent_verbs.AgentPaths.initialize(failed_root)
    assert create_error.value.exit_code == ExitCode.RUNNER_FAILURE
    monkeypatch.setattr(Path, "mkdir", real_mkdir)

    missing_jobs = tmp_path / "missing-jobs"

    def skip_jobs(path, *args, **kwargs):
        if path == missing_jobs / "jobs":
            return None
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", skip_jobs)
    with pytest.raises(AdmissionError, match="not a real directory") as not_directory:
        agent_verbs.AgentPaths.initialize(missing_jobs)
    assert not_directory.value.exit_code == ExitCode.REFUSAL

    shared_child = tmp_path / "shared-child"
    shared_child.mkdir(mode=0o700)
    (shared_child / "jobs").mkdir(mode=0o755)
    with pytest.raises(AdmissionError, match="accessible by other users") as child_mode:
        agent_verbs.AgentPaths.initialize(shared_child)
    assert child_mode.value.exit_code == ExitCode.REFUSAL

    unreadable = tmp_path / "unreadable-state"
    target = unreadable / "uploads"
    real_stat = Path.stat
    real_is_dir = Path.is_dir

    def path_stat_error(path, *args, **kwargs):
        if path == target:
            raise OSError("upload directory stat failed")
        return real_stat(path, *args, **kwargs)

    def target_is_directory(path):
        if path == target:
            return True
        return real_is_dir(path)

    monkeypatch.setattr(Path, "stat", path_stat_error)
    monkeypatch.setattr(Path, "is_dir", target_is_directory)
    with pytest.raises(AdmissionError, match="cannot inspect agent state path") as stat_error:
        agent_verbs.AgentPaths.initialize(unreadable)
    assert stat_error.value.exit_code == ExitCode.RUNNER_FAILURE


def test_service_refuses_bad_versions_and_advertises_lease_only_with_all_providers(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    with pytest.raises(AdmissionError, match="must be SemVer") as version_error:
        AgentVerbService(
            tmp_path / "bad-version", service.policy, service.facts_provider,
            agent_version="version-one",
        )
    assert version_error.value.exit_code == ExitCode.REFUSAL

    service.admission.inspector = object()
    assert "lease" not in service.handle({"verb": "probe"}, identity).document["capabilities"]
    service.admission.legacy_jobs_provider = lambda: ()
    assert "lease" not in service.handle({"verb": "probe"}, identity).document["capabilities"]
    service.admission.container_inspector = lambda _container_id: None
    assert "lease" in service.handle({"verb": "probe"}, identity).document["capabilities"]


def test_agent_facts_upload_limits_and_unavailable_verbs_have_closed_replies(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    default_probe = service.handle({"verb": "probe"}, identity).document
    assert "lease" not in default_probe["capabilities"]
    with pytest.raises(AdmissionError, match="put request has no streamed body") as no_body:
        service.handle({"verb": "put", "name": "candidates", "sha256": "a" * 64, "bytes": 1}, identity)
    assert no_body.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT

    service.facts_provider = lambda: {}
    with pytest.raises(AdmissionError, match="host facts unavailable") as invalid_facts:
        service.handle({"verb": "facts"}, identity)
    assert invalid_facts.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    service.facts_provider = lambda: HostFacts({"schema": "host-facts/1"})
    with pytest.raises(AdmissionError, match="requires host-facts/2") as old_facts:
        service.handle({"verb": "facts"}, identity)
    assert old_facts.value.exit_code == ExitCode.REFUSAL

    service._facts = lambda: {"policy": {"max_upload_bytes": {"v": None, "reason": "not configured"}}}
    with pytest.raises(AdmissionError, match="max_upload_bytes unavailable") as unknown_limit:
        service._max_upload_bytes()
    assert unknown_limit.value.exit_code == ExitCode.REFUSAL

    with pytest.raises(AdmissionError, match="held-connection protocol handler") as ordinary_lease:
        service.handle({
            "verb": "lease", "lease_id": "controller-run-1", "container_name": "runner",
            "footprint": "tiny", "planned_cores": 1, "priority": "gate",
        }, identity)
    assert ordinary_lease.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT


def test_agent_dispatch_returns_admission_documents_for_submit_status_and_cancel(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    service.executor = object()
    service.admission.submit = lambda _request, _identity, _executor: {
        "job_id": "controller-run-1", "state": "queued",
    }
    submitted = service.handle(_submit(), identity)
    assert submitted.document == {
        "schema": "fleet-host-agent/1", "agent_version": "1.0.0",
        "job_id": "controller-run-1", "state": "queued",
    }

    service.admission.status = lambda job_id, _identity, _executor: {"job_id": job_id, "state": "running"}
    status = service.handle({"verb": "status", "job_id": "controller-run-1"}, identity)
    assert status.document["state"] == "running"

    service.admission.cancel = lambda job_id, reason, _identity, _executor: {
        "job_id": job_id, "state": "stopping", "reason": reason,
    }
    cancelled = service.handle({
        "verb": "cancel", "job_id": "controller-run-1", "reason": "operator request",
    }, identity)
    assert cancelled.document["reason"] == "operator request"


def test_agent_dispatch_refuses_a_validated_verb_without_a_stage_handler(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    monkeypatch.setattr(agent_verbs, "validate_request", lambda *_args, **_kwargs: {"verb": "future-stage"})
    with pytest.raises(AdmissionError, match="is not available in this package stage") as unavailable:
        service.handle({"verb": "future-stage"}, identity)
    assert unavailable.value.exit_code == ExitCode.REFUSAL


def test_candidate_paths_returns_only_real_commit_named_files_and_maps_io_errors(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    assert service.paths.candidate_paths(identity.group) == frozenset()
    group = service.paths.uploads_dir / identity.group
    group.mkdir(mode=0o700)
    assert service.paths.candidate_paths(identity.group) == frozenset()

    directory = group / "candidates"
    directory.mkdir(mode=0o700)
    good = directory / ("a" * 40)
    good.write_bytes(b"host-created")
    (directory / "not-a-commit").write_bytes(b"ignored")
    (directory / ("b" * 40)).symlink_to(good)
    (directory / ("c" * 40)).mkdir()
    assert service.paths.candidate_paths(identity.group) == frozenset({str(good.resolve())})

    real_iterdir = Path.iterdir

    def fail_candidate_listing(path):
        if path == directory:
            raise OSError("candidate listing unavailable")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_candidate_listing)
    with pytest.raises(AdmissionError, match="cannot inspect host-created candidates") as listing_error:
        service.paths.candidate_paths(identity.group)
    assert listing_error.value.exit_code == ExitCode.RUNNER_FAILURE
    monkeypatch.setattr(Path, "iterdir", real_iterdir)

    uploads = service.paths.uploads_dir
    displaced = tmp_path / "displaced-uploads"
    uploads.rename(displaced)
    uploads.symlink_to(displaced, target_is_directory=True)
    with pytest.raises(AdmissionError, match="upload directory is not a real directory") as uploads_link:
        service.paths.candidate_paths(identity.group)
    assert uploads_link.value.exit_code == ExitCode.REFUSAL


def test_have_filters_untrusted_checkouts_and_maps_root_errors(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    root = service.policy.repo_roots["vbpub"]
    valid = root / ("e" * 40)
    valid.mkdir()
    (valid / ".git").write_text("gitdir: valid", encoding="utf-8")
    no_marker = root / ("b" * 40)
    no_marker.mkdir()
    marker_link = root / ("c" * 40)
    marker_link.mkdir()
    (marker_link / ".git").symlink_to(valid / ".git")
    wrong_name = root / "not-a-commit"
    wrong_name.mkdir()
    symlink = root / ("d" * 40)
    symlink.symlink_to(valid, target_is_directory=True)
    assert service.handle({"verb": "have", "repo": "vbpub"}, identity).document["refs"] == ["e" * 40]

    root.rename(tmp_path / "moved-checkouts")
    root.symlink_to(tmp_path / "moved-checkouts", target_is_directory=True)
    with pytest.raises(AdmissionError, match="checkout root .* unavailable") as unavailable:
        service.handle({"verb": "have", "repo": "vbpub"}, identity)
    assert unavailable.value.exit_code == ExitCode.REFUSAL


def test_have_maps_listing_errors_and_skips_unresolvable_checkouts(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    root = service.policy.repo_roots["vbpub"]
    valid = root / ("e" * 40)
    valid.mkdir()
    (valid / ".git").write_text("gitdir: valid", encoding="utf-8")
    bad_resolution = root / ("b" * 40)
    bad_resolution.mkdir()
    (bad_resolution / ".git").write_text("gitdir: invalid", encoding="utf-8")
    real_resolve = Path.resolve

    def fail_one_checkout(path, *args, **kwargs):
        if path == bad_resolution:
            raise RuntimeError("checkout moved while probing")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_one_checkout)
    reply = service.handle({"verb": "have", "repo": "vbpub"}, identity)
    assert reply.document["refs"] == ["e" * 40]

    monkeypatch.setattr(Path, "resolve", real_resolve)
    real_iterdir = Path.iterdir

    def fail_listing(path):
        if path == root:
            raise OSError("repo checkout listing failed")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_listing)
    with pytest.raises(AdmissionError, match="cannot read repo checkout root") as listing_error:
        service.handle({"verb": "have", "repo": "vbpub"}, identity)
    assert listing_error.value.exit_code == ExitCode.RUNNER_FAILURE


def test_stream_upload_maps_descriptor_wait_read_and_idle_errors(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(tmp_path)
    target = io.BytesIO()
    with pytest.raises(AdmissionError, match="no file descriptor") as no_fd:
        service._stream_upload(object(), 1, hashlib.sha256(b"x").hexdigest(), target)
    assert no_fd.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT

    invalid_stream = SimpleNamespace(fileno=lambda: -1)
    with pytest.raises(AdmissionError, match="invalid file descriptor") as invalid_fd:
        service._stream_upload(invalid_stream, 1, hashlib.sha256(b"x").hexdigest(), target)
    assert invalid_fd.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT

    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: (_ for _ in ()).throw(ValueError("bad fd")))
        with pytest.raises(AdmissionError, match="cannot wait for put body") as wait_error:
            service._stream_upload(read_fd, 1, hashlib.sha256(b"x").hexdigest(), target)
        assert wait_error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT

        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: ([read_fd], [], []))
        monkeypatch.setattr(agent_verbs.os, "read", lambda *_args: (_ for _ in ()).throw(OSError("read failed")))
        with pytest.raises(AdmissionError, match="cannot read put body") as read_error:
            service._stream_upload(read_fd, 1, hashlib.sha256(b"x").hexdigest(), target)
        assert read_error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_stream_upload_uses_idle_deadline_when_body_or_end_never_arrives(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(tmp_path)
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: ([], [], []))
        with pytest.raises(AdmissionError, match="idle connection deadline expired") as no_body:
            service._stream_upload(read_fd, 1, hashlib.sha256(b"x").hexdigest(), io.BytesIO())
        assert no_body.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT

        os.write(write_fd, b"x")
        calls = 0

        def readable_then_parked(*_args):
            nonlocal calls
            calls += 1
            return ([read_fd], [], []) if calls == 1 else ([], [], [])

        monkeypatch.setattr(agent_verbs.select, "select", readable_then_parked)
        with pytest.raises(AdmissionError, match="did not half-close") as no_eof:
            service._stream_upload(read_fd, 1, hashlib.sha256(b"x").hexdigest(), io.BytesIO())
        assert no_eof.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    finally:
        os.close(read_fd)
        os.close(write_fd)

    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"x")
        calls = 0

        def fail_end_check(*_args):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ([read_fd], [], [])
            raise OSError("end check failed")

        monkeypatch.setattr(agent_verbs.select, "select", fail_end_check)
        with pytest.raises(AdmissionError, match="cannot check put body end") as end_error:
            service._stream_upload(read_fd, 1, hashlib.sha256(b"x").hexdigest(), io.BytesIO())
        assert end_error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_owned_job_refuses_missing_symlink_oversized_invalid_and_other_group_records(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    with pytest.raises(AdmissionError, match="unknown job") as missing:
        service._owned_job("missing", identity)
    assert missing.value.exit_code == ExitCode.REFUSAL

    outside = tmp_path / "outside-job"
    outside.mkdir()
    (service.paths.jobs_dir / "linked-job").symlink_to(outside, target_is_directory=True)
    with pytest.raises(AdmissionError, match="unknown job") as job_link:
        service._owned_job("linked-job", identity)
    assert job_link.value.exit_code == ExitCode.REFUSAL

    job_dir = _job(service, identity)
    request_file = job_dir / "request.json"
    saved = request_file.read_bytes()
    request_file.unlink()
    request_file.symlink_to(outside / "absent.json")
    with pytest.raises(AdmissionError, match="request record is a symlink") as request_link:
        service._owned_job("controller-run-1", identity)
    assert request_link.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    request_file.unlink()
    with pytest.raises(AdmissionError, match="cannot read job .* request record") as missing_record:
        service._owned_job("controller-run-1", identity)
    assert missing_record.value.exit_code == ExitCode.QUEUED_WITHDRAWN
    request_file.write_bytes(b"x" * 65537)
    with pytest.raises(AdmissionError, match="exceeds 64 KiB") as oversized:
        service._owned_job("controller-run-1", identity)
    assert oversized.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    request_file.write_bytes(b"\xff")
    with pytest.raises(AdmissionError, match="invalid JSON") as invalid_json:
        service._owned_job("controller-run-1", identity)
    assert invalid_json.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    request_file.write_text(json.dumps({"group": "worker", "request": {}}), encoding="utf-8")
    with pytest.raises(AdmissionError, match="does not belong") as wrong_group:
        service._owned_job("controller-run-1", identity)
    assert wrong_group.value.exit_code == ExitCode.REFUSAL

    request_file.write_text(json.dumps({"group": identity.group, "request": []}), encoding="utf-8")
    with pytest.raises(AdmissionError, match="does not belong") as invalid_request:
        service._owned_job("controller-run-1", identity)
    assert invalid_request.value.exit_code == ExitCode.REFUSAL

    request_file.write_bytes(saved)
    assert service._owned_job("controller-run-1", identity)[1]["lane"] == "r0-r1"


def test_owned_job_maps_request_file_open_failures(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    request_file = job_dir / "request.json"
    real_open = os.open

    def deny_request(path, flags, *args, **kwargs):
        if path == request_file:
            raise OSError("request record open refused")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "open", deny_request)
    with pytest.raises(AdmissionError, match="cannot read job .* request record") as error:
        service._owned_job("controller-run-1", identity)
    assert error.value.exit_code == ExitCode.QUEUED_WITHDRAWN


def test_safe_file_constrains_missing_open_and_nonregular_files(tmp_path, monkeypatch):
    root = tmp_path / "job"
    root.mkdir()
    missing = root / "missing.log"
    with pytest.raises(AdmissionError, match="is missing") as absent:
        AgentVerbService._safe_file(root, missing)
    assert absent.value.exit_code == ExitCode.REFUSAL
    assert AgentVerbService._safe_file(root, missing, missing_ok=True) is None

    subdir = root / "directory"
    subdir.mkdir()
    real_open = os.open
    opened = []

    def capture_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(agent_verbs.os, "open", capture_open)
    with pytest.raises(AdmissionError, match="is not a regular file") as not_regular:
        AgentVerbService._safe_file(root, subdir)
    assert not_regular.value.exit_code == ExitCode.REFUSAL
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])

    monkeypatch.setattr(agent_verbs.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("read denied")))
    with pytest.raises(AdmissionError, match="cannot open requested file") as denied:
        AgentVerbService._safe_file(root, subdir)
    assert denied.value.exit_code == ExitCode.RUNNER_FAILURE


def test_safe_file_handles_missing_open_race_and_escaping_paths(tmp_path, monkeypatch):
    root = tmp_path / "job"
    root.mkdir()
    artifact = root / "artifact"
    artifact.write_text("data", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_text("secret", encoding="utf-8")
    symlink = root / "escape"
    symlink.symlink_to(outside)
    with pytest.raises(AdmissionError, match="escapes its job directory") as escape:
        AgentVerbService._safe_file(root, symlink)
    assert escape.value.exit_code == ExitCode.REFUSAL

    real_open = os.open

    def missing_after_resolve(path, flags, *args, **kwargs):
        if path == artifact.resolve():
            raise FileNotFoundError("vanished after resolution")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "open", missing_after_resolve)
    assert AgentVerbService._safe_file(root, artifact, missing_ok=True) is None
    with pytest.raises(AdmissionError, match="cannot open requested file") as vanished:
        AgentVerbService._safe_file(root, artifact)
    assert vanished.value.exit_code == ExitCode.RUNNER_FAILURE


def test_fetch_refuses_offsets_and_reports_files_that_change_during_reads(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    job_dir = _job(service, identity)
    artifact = job_dir / "job.log"
    artifact.write_bytes(b"content")
    with pytest.raises(AdmissionError, match="exceeds artifact size") as offset_error:
        service.handle({
            "verb": "fetch", "job_id": "controller-run-1", "artifact": "job.log",
            "offset": 99, "length": 1,
        }, identity)
    assert offset_error.value.exit_code == ExitCode.REFUSAL

    real_read = os.read
    reads = 0

    def vanish_during_hash(fd, size):
        nonlocal reads
        if fd != service.paths.jobs_dir:
            reads += 1
            if reads == 1:
                return b""
        return real_read(fd, size)

    monkeypatch.setattr(agent_verbs.os, "read", vanish_during_hash)
    with pytest.raises(AdmissionError, match="changed while hashing") as changed:
        service.handle({
            "verb": "fetch", "job_id": "controller-run-1", "artifact": "job.log",
            "offset": 0, "length": 1,
        }, identity)
    assert changed.value.exit_code == ExitCode.RUNNER_FAILURE

    reads = 0

    def vanish_during_range(fd, size):
        nonlocal reads
        reads += 1
        if reads == 1:
            return b"content"
        if reads == 2:
            return b""
        return real_read(fd, size)

    monkeypatch.setattr(agent_verbs.os, "read", vanish_during_range)
    with pytest.raises(AdmissionError, match="changed while reading its range") as range_changed:
        service.handle({
            "verb": "fetch", "job_id": "controller-run-1", "artifact": "job.log",
            "offset": 0, "length": 1,
        }, identity)
    assert range_changed.value.exit_code == ExitCode.RUNNER_FAILURE


def test_hash_file_requires_regular_non_symlink_files_and_closes_on_read_errors(tmp_path, monkeypatch):
    regular = tmp_path / "artifact"
    regular.write_bytes(b"hash me")
    assert AgentVerbService._hash_file(regular) == (7, hashlib.sha256(b"hash me").hexdigest())

    link = tmp_path / "link"
    link.symlink_to(regular)
    with pytest.raises(AdmissionError, match="must not be a symlink") as symlink_error:
        AgentVerbService._hash_file(link)
    assert symlink_error.value.exit_code == ExitCode.REFUSAL

    directory = tmp_path / "directory"
    directory.mkdir()
    real_open = os.open
    captured = []

    def return_directory_for_file(path, flags, *args, **kwargs):
        fd = real_open(directory if path == regular else path, flags, *args, **kwargs)
        captured.append(fd)
        return fd

    monkeypatch.setattr(agent_verbs.os, "open", return_directory_for_file)
    with pytest.raises(AdmissionError, match="is not a regular file") as not_regular:
        AgentVerbService._hash_file(regular)
    assert not_regular.value.exit_code == ExitCode.REFUSAL
    with pytest.raises(OSError):
        os.fstat(captured[0])

    monkeypatch.setattr(agent_verbs.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open failed")))
    with pytest.raises(AdmissionError, match="cannot open agent file") as open_error:
        AgentVerbService._hash_file(regular)
    assert open_error.value.exit_code == ExitCode.RUNNER_FAILURE

    monkeypatch.setattr(agent_verbs.os, "open", real_open)
    read_fd = real_open(regular, os.O_RDONLY)
    monkeypatch.setattr(agent_verbs.os, "open", lambda *_args, **_kwargs: read_fd)
    monkeypatch.setattr(agent_verbs.os, "read", lambda *_args: (_ for _ in ()).throw(OSError("read failed")))
    with pytest.raises(OSError, match="read failed"):
        AgentVerbService._hash_file(regular)
    with pytest.raises(OSError):
        os.fstat(read_fd)


def test_fsync_directory_closes_its_descriptor_when_sync_fails(tmp_path, monkeypatch):
    real_open = os.open
    captured = []

    def capture_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        captured.append(fd)
        return fd

    monkeypatch.setattr(agent_verbs.os, "open", capture_open)
    monkeypatch.setattr(agent_verbs.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("sync failed")))
    with pytest.raises(OSError, match="sync failed"):
        AgentVerbService._fsync_directory(tmp_path)
    assert len(captured) == 1
    with pytest.raises(OSError):
        os.fstat(captured[0])


def test_lane_kind_uses_root_declaration_and_rejects_invalid_or_missing_lanes(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.root.toml").write_text(
        '[lanes."r0-r1"]\nkind = "sequence"\n', encoding="utf-8",
    )
    checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert AgentVerbService._lane_kind(checkout_fd, "project", "r0-r1") == "sequence"
        (checkout / "run-gate.root.toml").write_text(
            '[lanes."r0-r1"]\nkind = "unknown"\n', encoding="utf-8",
        )
        with pytest.raises(AdmissionError, match="invalid kind") as invalid_kind:
            AgentVerbService._lane_kind(checkout_fd, "project", "r0-r1")
        assert invalid_kind.value.exit_code == ExitCode.QUEUED_WITHDRAWN

        (checkout / "run-gate.root.toml").write_text("[lanes\n", encoding="utf-8")
        with pytest.raises(AdmissionError, match="cannot read Run-Gate lane declaration") as malformed:
            AgentVerbService._lane_kind(checkout_fd, "project", "r0-r1")
        assert malformed.value.exit_code == ExitCode.QUEUED_WITHDRAWN

        (checkout / "run-gate.root.toml").unlink()
        with pytest.raises(AdmissionError, match="is not declared") as missing:
            AgentVerbService._lane_kind(checkout_fd, "project", "r0-r1")
        assert missing.value.exit_code == ExitCode.QUEUED_WITHDRAWN

        (checkout / "run-gate.root.toml").write_text(
            '[lanes]\n"r0-r1" = "not a table"\n', encoding="utf-8",
        )
        with pytest.raises(AdmissionError, match="is not declared") as malformed_declaration:
            AgentVerbService._lane_kind(checkout_fd, "project", "r0-r1")
        assert malformed_declaration.value.exit_code == ExitCode.QUEUED_WITHDRAWN
    finally:
        os.close(checkout_fd)


def test_progress_helpers_ignore_bad_assay_events_and_map_file_inspection_errors(tmp_path, monkeypatch):
    progress = tmp_path / "progress.jsonl"
    progress.write_bytes(
        b'{"event":"candidate","candidate_id":"same"}\n'
        b'{"event":"candidate","candidate_id":"same"}\n'
        b'{"event":"candidate","candidate_id":2}\n'
        b"not-json\n\xff\n"
        b'{"event":"candidate","candidate_id":"trailing"}',
    )
    fd = os.open(progress, os.O_RDONLY)
    try:
        assert AgentVerbService._assay_done_count(fd, progress.stat().st_size) == 1
    finally:
        os.close(fd)

    real_open = os.open
    captured = []

    def open_progress(_root_fd, _relative):
        fd = real_open(progress, os.O_RDONLY)
        captured.append(fd)
        return fd

    monkeypatch.setattr(agent_verbs, "open_beneath", open_progress)
    monkeypatch.setattr(agent_verbs.os, "fstat", lambda _fd: (_ for _ in ()).throw(OSError("stat failed")))
    with pytest.raises(AdmissionError, match="cannot inspect progress file") as stat_error:
        AgentVerbService._progress_file(10, "progress.jsonl")
    assert stat_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN
    monkeypatch.undo()
    with pytest.raises(OSError):
        os.fstat(captured[0])

    early_fd = os.open(progress, os.O_RDONLY)
    monkeypatch.setattr(agent_verbs.os, "read", lambda *_args: b"")
    try:
        assert AgentVerbService._assay_done_count(early_fd, progress.stat().st_size) == 0
    finally:
        os.close(early_fd)


def test_progress_refuses_invalid_job_metadata_and_maps_checkout_io_errors(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    invalid_project = _job(service, identity, {"project": "../outside", "lane": "r0-r1"})
    invalid_project.mkdir(exist_ok=True)
    (invalid_project / "checkout").mkdir()
    (invalid_project / "checkout" / "run-gate.toml").write_text(
        '[lanes."r0-r1"]\nkind = "command"\n', encoding="utf-8",
    )
    with pytest.raises(AdmissionError, match="project") as unsafe_project:
        service.handle({"verb": "progress", "job_id": "controller-run-1", "offset": 0}, identity)
    assert unsafe_project.value.exit_code == ExitCode.REFUSAL

    invalid_lane = {"project": ".", "lane": 4}
    request_file = invalid_project / "request.json"
    request_file.write_text(json.dumps({"group": identity.group, "request": invalid_lane}), encoding="utf-8")
    with pytest.raises(AdmissionError, match="lane is invalid") as bad_lane:
        service.handle({"verb": "progress", "job_id": "controller-run-1", "offset": 0}, identity)
    assert bad_lane.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    request_file.write_text(json.dumps({"group": identity.group, "request": {"project": ".", "lane": "r0-r1"}}), encoding="utf-8")
    checkout = invalid_project / "checkout"
    (checkout / "run-gate.toml").unlink()
    checkout.rmdir()
    with pytest.raises(AdmissionError, match="checkout is unavailable") as missing_checkout:
        service.handle({"verb": "progress", "job_id": "controller-run-1", "offset": 0}, identity)
    assert missing_checkout.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    checkout.mkdir()
    (checkout / "run-gate.toml").write_text('[lanes."r0-r1"]\nkind = "command"\n', encoding="utf-8")
    real_open = os.open

    def fail_jobs_open(path, flags, *args, **kwargs):
        if path == service.paths.jobs_dir:
            raise OSError(errno.ELOOP, "looped progress tree")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "open", fail_jobs_open)
    with pytest.raises(AdmissionError, match="progress path is unsafe") as loop:
        service.handle({"verb": "progress", "job_id": "controller-run-1", "offset": 0}, identity)
    assert loop.value.exit_code == ExitCode.REFUSAL

    def fail_jobs_io(path, flags, *args, **kwargs):
        if path == service.paths.jobs_dir:
            raise OSError(errno.EIO, "progress tree unreadable")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "open", fail_jobs_io)
    with pytest.raises(AdmissionError, match="cannot open job .* progress tree") as io_error:
        service.handle({"verb": "progress", "job_id": "controller-run-1", "offset": 0}, identity)
    assert io_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN


def test_write_agent_reply_writes_exact_frames_and_closes_streamed_files(tmp_path):
    body_path = tmp_path / "body"
    body_path.write_bytes(b"streamed-body")
    body_fd = os.open(body_path, os.O_RDONLY)
    document = {"schema": "reply/1", "length": 13}
    reply = agent_verbs.AgentReply(document, body_fd=body_fd, body_length=13)
    client, server = socket.socketpair()
    try:
        agent_verbs.write_agent_reply(server, reply, idle_timeout_s=3)
        expected_body = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        header = b""
        while len(header) < 4:
            chunk = client.recv(4 - len(header))
            assert chunk
            header += chunk
        assert struct.unpack(">I", header)[0] == len(expected_body)
        framed_body = b""
        while len(framed_body) < len(expected_body) + 13:
            chunk = client.recv(len(expected_body) + 13 - len(framed_body))
            assert chunk
            framed_body += chunk
        assert framed_body == expected_body + b"streamed-body"
        assert reply.body_fd is None
        with pytest.raises(OSError):
            os.fstat(body_fd)
    finally:
        client.close()
        server.close()


def test_write_agent_reply_appends_in_memory_body_after_the_frame():
    document = {"result": "ok"}
    reply = agent_verbs.AgentReply(document, body=b"range-bytes")
    client, server = socket.socketpair()
    try:
        agent_verbs.write_agent_reply(server, reply)
        assert read_frame(client, 2) == document
        assert client.recv(len(b"range-bytes")) == b"range-bytes"
    finally:
        client.close()
        server.close()


def test_write_agent_reply_closes_on_invalid_stream_and_truncated_body(tmp_path, monkeypatch):
    reply = agent_verbs.AgentReply({"answer": True}, body=b"body")
    monkeypatch.setattr(agent_verbs, "write_frame", lambda *_args, **_kwargs: None)
    with pytest.raises(FrameError, match="invalid file descriptor"):
        agent_verbs.write_agent_reply(SimpleNamespace(fileno=lambda: -1), reply)
    assert reply.body_fd is None

    body_path = tmp_path / "short-body"
    body_path.write_bytes(b"x")
    body_fd = os.open(body_path, os.O_RDONLY)
    truncated = agent_verbs.AgentReply({"length": 2}, body_fd=body_fd, body_length=2)
    client, server = socket.socketpair()
    try:
        monkeypatch.setattr(agent_verbs, "write_frame", lambda stream, document, _timeout=None: write_frame(stream, document))
        with pytest.raises(FrameError, match="streamed response body was truncated"):
            agent_verbs.write_agent_reply(server, truncated)
        assert read_frame(client, 2) == {"length": 2}
        assert client.recv(1) == b"x"
        with pytest.raises(OSError):
            os.fstat(body_fd)
    finally:
        client.close()
        server.close()


def test_streamed_reply_write_errors_are_reported_as_frame_errors(monkeypatch):
    monkeypatch.setattr(agent_verbs, "_write_all", lambda *_args: (_ for _ in ()).throw(FrameError("blocked")))
    with pytest.raises(FrameError, match="cannot write streamed response: blocked"):
        agent_verbs._write_bytes(1, b"body", None)


def test_lease_waiter_and_disconnect_helpers_map_transport_states(monkeypatch):
    with pytest.raises(AdmissionError, match="local socket connection") as wrong_stream:
        agent_verbs._lease_queue_waiter(object())
    assert wrong_stream.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT

    client, server = socket.socketpair()
    try:
        waiter = agent_verbs._lease_queue_waiter(server)
        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: ([], [], []))
        waiter(0)
        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: ([server], [], []))
        waiter(0)  # A readiness race with no queued bytes is harmless.
        client.sendall(b"unexpected")
        with pytest.raises(AdmissionError, match="unexpected client data") as queued_data:
            waiter(0)
        assert queued_data.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    finally:
        client.close()
        server.close()

    client, server = socket.socketpair()
    try:
        waiter = agent_verbs._lease_queue_waiter(server)
        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: ([server], [], []))
        client.close()
        with pytest.raises(AdmissionError, match="disconnected before its grant") as disconnected:
            waiter(0)
        assert disconnected.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    finally:
        server.close()

    client, server = socket.socketpair()
    try:
        waiter = agent_verbs._lease_queue_waiter(server)
        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: (_ for _ in ()).throw(OSError("select failed")))
        with pytest.raises(AdmissionError, match="connection failed while queued") as select_error:
            waiter(0)
        assert select_error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    finally:
        client.close()
        server.close()


def test_held_lease_disconnect_waits_for_eof_and_rejects_invalid_streams(monkeypatch):
    with pytest.raises(FrameError, match="no file descriptor"):
        agent_verbs._wait_for_lease_disconnect(object())
    with pytest.raises(FrameError, match="invalid file descriptor"):
        agent_verbs._wait_for_lease_disconnect(SimpleNamespace(fileno=lambda: -1))

    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    selections = iter(([], [read_fd]))
    monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: (next(selections), [], []))
    try:
        assert agent_verbs._wait_for_lease_disconnect(read_fd) is None
    finally:
        os.close(read_fd)

    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"still connected")
        os.close(write_fd)
        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: ([read_fd], [], []))
        with pytest.raises(AdmissionError, match="unexpected data after lease bind") as extra:
            agent_verbs._wait_for_lease_disconnect(read_fd)
        assert extra.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    finally:
        os.close(read_fd)

    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr(agent_verbs.select, "select", lambda *_args: (_ for _ in ()).throw(OSError("select failed")))
        with pytest.raises(AdmissionError, match="held lease connection failed") as failed:
            agent_verbs._wait_for_lease_disconnect(read_fd)
        assert failed.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_lease_reconnect_without_callback_holds_charge_until_eof(tmp_path):
    lease_id = "controller-run-1"
    container_id = "c" * 64
    service, identity, _facts = _lease_service(
        tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT, lease_id=lease_id,
    )
    initial = service.admission.reserve_lease(
        lease_id, identity, container_name="rg-test", footprint="tiny",
        planned_cores=1, priority="gate",
    )
    assert initial.handle is not None
    initial.handle.close()

    outcomes = []
    finished = threading.Event()
    client, server = socket.socketpair()
    worker = threading.Thread(target=_lease_worker, args=(
        service, {"verb": "lease", "lease_id": lease_id, "container_id": container_id},
        identity, server, outcomes, finished,
    ))
    worker.start()
    bound = read_frame(client, 3)
    assert bound["event"] == "bound"
    assert bound["container_id"] == container_id
    assert service.admission.book.status(lease_id, identity.group)["released"] is False
    client.close()
    _assert_worker_finished(worker, finished)
    assert outcomes == []
    assert service.admission.book.status(lease_id, identity.group)["released"] is False


def test_lease_rejects_invalid_bind_frame_then_tombstones_and_reconciles(tmp_path):
    service, identity, _facts = _lease_service(
        tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT,
    )
    request = {
        "verb": "lease", "lease_id": "controller-run-1", "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    outcomes = []
    finished = threading.Event()
    client, server = socket.socketpair()
    worker = threading.Thread(target=_lease_worker, args=(service, request, identity, server, outcomes, finished))
    worker.start()
    grant = read_frame(client, 3)
    assert grant["event"] == "grant"
    write_frame(client, {"event": "wrong-event", "lease_id": request["lease_id"], "container_id": "c" * 64})
    _assert_worker_finished(worker, finished)
    client.close()

    assert len(outcomes) == 1
    assert isinstance(outcomes[0], AdmissionError)
    assert outcomes[0].exit_code == ExitCode.REFUSAL
    record = service.admission.book.status(request["lease_id"], identity.group)
    assert record["state"] == "tombstone"
    assert record["released"] is True


def test_lease_reservation_failure_leaves_no_charge_to_cleanup(tmp_path):
    service, identity, _facts = _lease_service(
        tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT,
    )
    service.admission.reserve_lease = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AdmissionError(ExitCode.QUEUED_WITHDRAWN, "lease admission timed out"),
    )
    request = {
        "verb": "lease", "lease_id": "controller-run-1", "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    client, server = socket.socketpair()
    try:
        with pytest.raises(AdmissionError, match="lease admission timed out") as error:
            service.serve_lease(request, identity, server)
        assert error.value.exit_code == ExitCode.QUEUED_WITHDRAWN
        assert list(service.admission.book.leases_dir.iterdir()) == []
    finally:
        client.close()
        server.close()


def test_lease_cleanup_closes_handle_when_tombstone_write_fails(tmp_path):
    service, identity, _facts = _lease_service(
        tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT,
    )

    class Handle:
        closed = False

        def close(self):
            self.closed = True

    handle = Handle()
    service.admission.reserve_lease = lambda *_args, **_kwargs: SimpleNamespace(
        record={"container_name": "rg-test"}, handle=handle,
    )
    service.admission.revoke_lease = lambda *_args: (_ for _ in ()).throw(OSError("tombstone write failed"))
    reconciliations = []
    service.admission.reconcile_lease = lambda *_args: reconciliations.append("called")
    request = {
        "verb": "lease", "lease_id": "controller-run-1", "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    outcomes = []
    finished = threading.Event()
    client, server = socket.socketpair()
    worker = threading.Thread(target=_lease_worker, args=(service, request, identity, server, outcomes, finished))
    worker.start()
    assert read_frame(client, 3)["event"] == "grant"
    client.close()
    _assert_worker_finished(worker, finished)
    assert isinstance(outcomes[0], FrameError)
    assert handle.closed is True
    assert reconciliations == []


def test_lease_tombstone_failure_skips_reconciliation_after_close(tmp_path, monkeypatch):
    service, identity, _facts = _lease_service(
        tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT,
    )

    class Handle:
        closed = False

        def close(self):
            self.closed = True

    handle = Handle()
    service.admission.reserve_lease = lambda *_args, **_kwargs: SimpleNamespace(
        record={"container_name": "rg-test"}, handle=handle,
    )
    service.admission.revoke_lease = lambda *_args: (_ for _ in ()).throw(OSError("tombstone write failed"))
    reconciliations = []
    service.admission.reconcile_lease = lambda *_args: reconciliations.append("called")
    monkeypatch.setattr(
        agent_verbs, "_wait_for_lease_disconnect",
        lambda _stream: (_ for _ in ()).throw(FrameError("injected disconnect")),
    )
    request = {
        "verb": "lease", "lease_id": "controller-run-1", "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    outcomes = []
    finished = threading.Event()
    client, server = socket.socketpair()
    worker = threading.Thread(target=_lease_worker, args=(service, request, identity, server, outcomes, finished))
    worker.start()
    try:
        assert read_frame(client, 3)["event"] == "grant"
        write_frame(client, {"event": "wrong-event", "lease_id": request["lease_id"], "container_id": "c" * 64})
        _assert_worker_finished(worker, finished)
        assert isinstance(outcomes[0], AdmissionError)
        assert outcomes[0].exit_code == ExitCode.REFUSAL
        assert handle.closed is True
        assert reconciliations == []
    finally:
        client.close()


def test_pending_lease_tombstone_failure_does_not_reconcile(tmp_path, monkeypatch):
    service, identity, _facts = _lease_service(
        tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT,
    )

    class Handle:
        closed = False

        def close(self):
            self.closed = True

    handle = Handle()
    revocations = []
    reconciliations = []

    def fail_tombstone(lease_id, _identity):
        revocations.append(lease_id)
        raise OSError("tombstone storage unavailable")

    service.admission.reserve_lease = lambda *_args, **_kwargs: SimpleNamespace(
        record={"container_name": "rg-test"}, handle=handle,
    )
    service.admission.revoke_lease = fail_tombstone
    service.admission.reconcile_lease = lambda *args: reconciliations.append(args)
    monkeypatch.setattr(service, "_write_lease_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent_verbs, "read_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FrameError("bind transport failed")),
    )
    request = {
        "verb": "lease", "lease_id": "controller-run-1", "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }

    client, server = socket.socketpair()
    try:
        with pytest.raises(FrameError, match="bind transport failed"):
            service.serve_lease(request, identity, server)
    finally:
        client.close()
        server.close()

    assert revocations == ["controller-run-1"]
    assert handle.closed is True
    assert reconciliations == []


def test_lease_cleanup_skips_when_grant_has_no_handle(tmp_path):
    service, identity, _facts = _lease_service(
        tmp_path, lambda _target: InspectorAnswer.NO_SUCH_OBJECT,
    )
    service.admission.reserve_lease = lambda *_args, **_kwargs: SimpleNamespace(
        record={"container_name": "rg-test"}, handle=None,
    )
    bound_calls = []
    service.admission.bind_lease = lambda *args: bound_calls.append(args)
    request = {
        "verb": "lease", "lease_id": "controller-run-1", "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    outcomes = []
    finished = threading.Event()
    client, server = socket.socketpair()
    worker = threading.Thread(target=_lease_worker, args=(service, request, identity, server, outcomes, finished))
    worker.start()
    assert read_frame(client, 3)["event"] == "grant"
    container_id = "c" * 64
    write_frame(client, {"event": "bind", "lease_id": request["lease_id"], "container_id": container_id})
    assert read_frame(client, 3)["event"] == "bound"
    client.close()
    _assert_worker_finished(worker, finished)
    assert outcomes == []
    assert bound_calls[0][1] == container_id


def test_put_caps_uploads_and_refuses_wrong_existing_content_address(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    maximum = service._max_upload_bytes()
    with pytest.raises(AdmissionError, match="exceeds policy.max_upload_bytes") as too_large:
        service._put({
            "bytes": maximum + 1, "name": "candidates", "sha256": "a" * 64,
        }, identity, object())
    assert too_large.value.exit_code == ExitCode.REFUSAL

    content = b"same candidate"
    first = _put(service, identity, "candidates", content)
    second = _put(service, identity, "candidates", content)
    assert first["path"] == second["path"]
    destination = Path(first["path"])
    destination.write_bytes(b"tampered content")
    client, server = socket.socketpair()
    try:
        client.sendall(content)
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(AdmissionError, match="does not match its content address") as mismatch:
            service.handle({
                "verb": "put", "name": "candidates", "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }, identity, body_stream=server)
        assert mismatch.value.exit_code == ExitCode.REFUSAL
    finally:
        client.close()
        server.close()
    assert destination.read_bytes() == b"tampered content"


def test_put_maps_creation_and_fsync_errors_without_leaving_temporary_files(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    request = {"bytes": 0, "name": "candidates", "sha256": hashlib.sha256(b"").hexdigest()}
    monkeypatch.setattr(agent_verbs.tempfile, "mkstemp", lambda **_kwargs: (_ for _ in ()).throw(OSError("temp unavailable")))
    with pytest.raises(AdmissionError, match="cannot store put body") as temp_error:
        service._put(request, identity, object())
    assert temp_error.value.exit_code == ExitCode.RUNNER_FAILURE
    assert list(service.paths.incoming_dir.iterdir()) == []

    monkeypatch.undo()
    content = b"directory sync"
    destination_dir = service.paths.uploads_dir / identity.group / "candidates"
    real_fsync_directory = service._fsync_directory

    def fail_destination_sync(path):
        if path == destination_dir:
            raise OSError("directory fsync failed")
        real_fsync_directory(path)

    monkeypatch.setattr(
        service, "_fsync_directory",
        fail_destination_sync,
    )
    client, server = socket.socketpair()
    try:
        client.sendall(content)
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(AdmissionError, match="cannot store put body") as sync_error:
            service.handle({
                "verb": "put", "name": "candidates", "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }, identity, body_stream=server)
        assert sync_error.value.exit_code == ExitCode.RUNNER_FAILURE
    finally:
        client.close()
        server.close()
    destination = service.paths.uploads_dir / identity.group / "candidates" / hashlib.sha256(content).hexdigest()
    assert destination.read_bytes() == content
    assert list(service.paths.incoming_dir.iterdir()) == []


def test_put_refuses_upload_root_and_destination_symlinks_and_non_directory_results(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    content = b"symlink check"
    uploads = service.paths.uploads_dir
    moved = tmp_path / "moved-uploads"
    uploads.rename(moved)
    uploads.symlink_to(moved, target_is_directory=True)
    with pytest.raises(AdmissionError, match="agent upload directory is not a real directory") as root_link:
        _direct_put(service, identity, "candidates", content)
    assert root_link.value.exit_code == ExitCode.REFUSAL
    assert list(service.paths.incoming_dir.iterdir()) == []

    uploads.unlink()
    uploads.mkdir(mode=0o700)
    group = uploads / identity.group
    outside = tmp_path / "outside-destination"
    outside.mkdir()
    group.mkdir(mode=0o700)
    (group / "candidates").symlink_to(outside, target_is_directory=True)
    with pytest.raises(AdmissionError, match="destination directory is a symlink") as destination_link:
        _put(service, identity, "candidates", content)
    assert destination_link.value.exit_code == ExitCode.REFUSAL
    assert list(outside.iterdir()) == []

    (group / "candidates").unlink()
    real_mkdir = Path.mkdir
    destination_dir = group / "candidates"

    def skip_destination_directory(path, *args, **kwargs):
        if path == destination_dir:
            return None
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", skip_destination_directory)
    with pytest.raises(AdmissionError, match="destination is not a real directory") as missing_destination:
        _put(service, identity, "candidates", content)
    assert missing_destination.value.exit_code == ExitCode.REFUSAL


def test_put_refuses_non_directory_group_and_content_symlink_paths(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    real_mkdir = Path.mkdir
    group_dir = service.paths.uploads_dir / identity.group

    def skip_group_directory(path, *args, **kwargs):
        if path == group_dir:
            return None
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", skip_group_directory)
    with pytest.raises(AdmissionError, match="put group path is not a real directory") as group_error:
        _direct_put(service, identity, "candidates", b"group path")
    assert group_error.value.exit_code == ExitCode.REFUSAL
    assert list(service.paths.incoming_dir.iterdir()) == []
    monkeypatch.setattr(Path, "mkdir", real_mkdir)

    group_dir.mkdir(mode=0o700)
    destination_dir = group_dir / "candidates"
    destination_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside-content"
    outside.write_bytes(b"untouched")
    content = b"content link"
    digest = hashlib.sha256(content).hexdigest()
    (destination_dir / digest).symlink_to(outside)
    with pytest.raises(AdmissionError, match="put destination is a symlink") as content_link:
        _direct_put(service, identity, "candidates", content)
    assert content_link.value.exit_code == ExitCode.REFUSAL
    assert outside.read_bytes() == b"untouched"


def test_put_closes_temporary_descriptor_when_setup_fails(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    real_mkstemp = agent_verbs.tempfile.mkstemp
    descriptors = []

    def capture_temp(**kwargs):
        fd, name = real_mkstemp(**kwargs)
        descriptors.append((fd, name))
        return fd, name

    monkeypatch.setattr(agent_verbs.tempfile, "mkstemp", capture_temp)
    monkeypatch.setattr(agent_verbs.os, "fchmod", lambda *_args: (_ for _ in ()).throw(OSError("chmod failed")))
    with pytest.raises(AdmissionError, match="cannot store put body") as setup_error:
        service._put({"bytes": 0, "name": "candidates", "sha256": hashlib.sha256(b"").hexdigest()}, identity, object())
    assert setup_error.value.exit_code == ExitCode.RUNNER_FAILURE
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0][0])
    assert not Path(descriptors[0][1]).exists()

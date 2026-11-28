from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
from contextlib import contextmanager

import pytest

from fleetd.admission import AdmissionError
from fleetd import agent_verbs
from test_agent_protocol import _submit
from test_agent_verbs import _put, _service, _upload_policy


def _enable_collection(service):
    service.policy = replace(service.policy, upload_collection_enabled=True)


@pytest.mark.parametrize("enabled", [None, False])
def test_collection_is_off_by_default_or_explicit_false(tmp_path, enabled):
    service, identity, _checkout = _service(tmp_path)
    if enabled is not None:
        service.policy = replace(service.policy, upload_collection_enabled=enabled)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"old")
    _upload_policy(service, store_bytes=5, floors={"checkout": 0})

    with pytest.raises(AdmissionError, match="max_upload_store_bytes"):
        _put(service, identity, "candidates", b"new")

    assert Path(old["path"]).read_bytes() == b"old"


def test_put_that_cannot_reclaim_enough_bytes_deletes_nothing(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    _enable_collection(service)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"old")
    _upload_policy(service, store_bytes=5, floors={"checkout": 0})

    with pytest.raises(AdmissionError, match="max_upload_store_bytes"):
        _put(service, identity, "candidates", b"new-content")

    assert Path(old["path"]).read_bytes() == b"old"


def test_disk_floor_refusal_does_not_collect(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _enable_collection(service)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"old")
    _upload_policy(service, store_bytes=1, floors={"checkout": 10})
    monkeypatch.setattr(service, "_upload_free_bytes", lambda: 10)

    with pytest.raises(AdmissionError, match="disk floor"):
        service._reserve_upload(1, 1, 10)

    assert Path(old["path"]).read_bytes() == b"old"


def test_reupload_of_stored_object_does_not_collect(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _enable_collection(service)
    _upload_policy(service, store_bytes=3, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"abc")
    deleted = []
    monkeypatch.setattr(service, "_collection_hook", lambda item: deleted.append(item.key))

    with pytest.raises(AdmissionError, match="max_upload_store_bytes"):
        _put(service, identity, "candidates", b"abc")

    assert Path(old["path"]).read_bytes() == b"abc"
    assert deleted == []


def test_absent_jobs_and_uploads_are_empty_on_fresh_state(tmp_path):
    service, _identity, _checkout = _service(tmp_path)
    service.paths.jobs_dir.rmdir()
    service.paths.uploads_dir.rmdir()

    assert service._referenced_upload_objects_locked() == {}
    assert service._upload_store_objects() == ()
    assert service._stored_upload_bytes() == 0


def test_unknown_incoming_entry_stops_collection(tmp_path):
    service, _identity, _checkout = _service(tmp_path)
    _enable_collection(service)
    (service.paths.incoming_dir / "unexpected").write_text("unknown")

    with pytest.raises(AdmissionError, match="unknown incoming upload artifact"):
        service._recover_upload_reservations_locked(strict=True)


def test_submit_refuses_a_candidate_collected_before_submit(tmp_path):
    service, identity, _checkout = _service(
        tmp_path, extra_argv='[["--only-candidates", "<host-candidates>"]]'
    )
    service.executor = object()
    _enable_collection(service)
    result = _put(service, identity, "candidates", b"candidate")
    request = _submit()
    request["extra_argv"] = ["--only-candidates", result["path"]]
    with service._upload_store_lock():
        assert service._collect_upload_objects_locked(1, 1) == len(b"candidate")

    with pytest.raises(AdmissionError, match="missing upload object candidates/"):
        service.handle(request, identity)


def test_submit_and_have_enter_store_lock(tmp_path, monkeypatch):
    service, identity, checkout = _service(tmp_path)
    (checkout / ".git").write_text("gitdir: ../.git/worktrees/test\n", encoding="utf-8")
    entered = []
    original = service._upload_store_lock

    @contextmanager
    def observed_lock():
        with original():
            entered.append(True)
            yield

    monkeypatch.setattr(service, "_upload_store_lock", observed_lock)
    assert service.handle({"verb": "have", "repo": "vbpub"}, identity).document["refs"]
    assert entered

    entered.clear()
    service.executor = object()
    service.admission.submit = lambda *_args: {"job_id": "job", "state": "queued"}
    service.handle(_submit(), identity)
    assert entered


def test_collector_classifies_every_submit_request_field(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    classified = agent_verbs._UPLOAD_REFERENCE_REQUEST_FIELDS | agent_verbs._NON_UPLOAD_REQUEST_FIELDS
    assert classified == frozenset(agent_verbs._CAPABILITIES["submit"])
    request = _submit()
    with pytest.raises(AdmissionError, match="unknown request field set"):
        service._request_upload_digests(request | {"new_upload_reference": "unknown"}, identity.group, "job")


def test_durable_request_inventory_retains_seed_dev_and_candidates(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    seed_digest = "a" * 64
    tracked_digest = "b" * 64
    untracked_digest = "c" * 64
    candidate_digest = "d" * 64
    request = _submit()
    request["stores"]["seed"] = {"path": "assay-store", "sha256": seed_digest, "bytes": 4}
    request["dev"] = {
        "tracked": {"sha256": tracked_digest, "bytes": 1},
        "untracked": {"sha256": untracked_digest, "bytes": 1},
        "verb_argv": ["status"], "tty": False, "worktree_key": "test-key",
    }
    request["kind"] = "dev"
    request["extra_argv"] = ["--only-candidates", str(service.paths.candidate_root(identity.group) / candidate_digest)]
    expected = {seed_digest, tracked_digest, untracked_digest, candidate_digest}

    assert service._request_upload_digests(request, identity.group, "job") == expected
    job_dir = service.paths.jobs_dir / "job"
    job_dir.mkdir()
    (job_dir / "request.json").write_text(json.dumps({
        "schema": "fleet-host-job-request/1", "group": identity.group,
        "digest": "e" * 64, "request": request,
    }), encoding="utf-8")
    assert service._referenced_upload_objects_locked() == {identity.group: frozenset(expected)}


def test_seed_checksum_sidecar_is_retained_with_referenced_archive(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    archive = _put(service, identity, "assay-seed-1.tgz", b"archive")
    checksum = _put(service, identity, "assay-seed-1.tgz.sha256", b"checksum")
    unused = _put(service, identity, "candidates", b"unused")
    objects = service._upload_store_objects()
    references = {identity.group: frozenset({archive["sha256"]})}

    sidecars = agent_verbs._referenced_seed_sidecars(references, objects)
    assert (identity.group, "assay-seed-1.tgz.sha256") in sidecars
    archive_object = next(item for item in objects if item.name == "assay-seed-1.tgz")
    assert agent_verbs._is_referenced_upload_object(archive_object, references, sidecars)
    request = _submit()
    request["stores"]["seed"] = {"path": "assay-store", "sha256": archive["sha256"], "bytes": 7}
    job_dir = service.paths.jobs_dir / "seed-job"
    job_dir.mkdir()
    (job_dir / "request.json").write_text(json.dumps({
        "schema": "fleet-host-job-request/1", "group": identity.group,
        "digest": "f" * 64, "request": request,
    }), encoding="utf-8")
    with service._upload_store_lock():
        assert service._collect_upload_objects_locked(len(b"unused"), len(b"unused")) >= len(b"unused")

    assert Path(archive["path"]).read_bytes() == b"archive"
    assert Path(checksum["path"]).read_bytes() == b"checksum"
    assert not Path(unused["path"]).exists()


def test_submit_refuses_missing_dev_and_seed_inputs_by_name(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    dev_request = _submit()
    dev_request["dev"] = {
        "tracked": {"sha256": "a" * 64, "bytes": 1},
        "untracked": {"sha256": "b" * 64, "bytes": 1},
        "verb_argv": ["status"], "tty": False, "worktree_key": "test-key",
    }
    dev_request["kind"] = "dev"
    with pytest.raises(AdmissionError, match="missing upload object dev.tracked.tar/"):
        service._require_submit_uploads(dev_request, identity.group)
    seed_request = _submit()
    seed_request["stores"]["seed"] = {"path": "assay-store", "sha256": "c" * 64, "bytes": 1}
    with pytest.raises(AdmissionError, match=r"missing upload object assay-seed-\*\.tgz/"):
        service._require_submit_uploads(seed_request, identity.group)


def test_submit_requires_seed_sidecar_and_accepts_complete_seed_pair(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    archive = _put(service, identity, "assay-seed-1.tgz", b"archive")
    request = _submit()
    request["stores"]["seed"] = {"path": "assay-store", "sha256": archive["sha256"], "bytes": 7}
    with pytest.raises(AdmissionError, match=r"missing upload object assay-seed-1.tgz\.sha256"):
        service._require_submit_uploads(request, identity.group)
    sidecar = _put(service, identity, "assay-seed-1.tgz.sha256", b"checksum")

    service._require_submit_uploads(request, identity.group)

    assert Path(archive["path"]).read_bytes() == b"archive"
    assert Path(sidecar["path"]).read_bytes() == b"checksum"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("{", "is unreadable"),
        ("[]", "unknown shape"),
        (json.dumps({"schema": "fleet-host-job-request/1", "group": "../bad", "digest": "a" * 64, "request": _submit()}), "unknown shape"),
        (json.dumps({"schema": "fleet-host-job-request/1", "group": "controller", "digest": "bad", "request": _submit()}), "unknown shape"),
    ],
)
def test_malformed_job_records_stop_reference_scan_without_deleting(tmp_path, raw, reason):
    service, identity, _checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    job_dir = service.paths.jobs_dir / "job"
    job_dir.mkdir()
    (job_dir / "request.json").write_text(raw, encoding="utf-8")

    with pytest.raises(AdmissionError, match=reason):
        service._referenced_upload_objects_locked()

    assert Path(old["path"]).read_bytes() == b"preserved"


def test_collection_record_readers_refuse_bad_names_types_and_sizes(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(tmp_path)
    state_fd = service._state_directory_fd()
    with pytest.raises(AdmissionError, match="is unreadable"):
        agent_verbs._upload_directory(state_fd, "missing", "test directory").__enter__()
    try:
        with pytest.raises(AdmissionError, match="cannot be listed"):
            monkeypatch.setattr(agent_verbs.os, "scandir", lambda _fd: (_ for _ in ()).throw(OSError("scan failed")))
            agent_verbs._upload_directory_entries(state_fd, "test listing")
    finally:
        os.close(state_fd)

    record_dir = service.paths.jobs_dir / "record"
    record_dir.mkdir()
    (record_dir / "child").mkdir()
    (record_dir / "request.json").write_text("{}", encoding="utf-8")
    record_fd = os.open(record_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(AdmissionError, match="not a regular file"):
            agent_verbs._read_upload_record(record_fd, "child", "test record")
        monkeypatch.setattr(agent_verbs, "_MAX_UPLOAD_RECORD_BYTES", 1)
        with pytest.raises(AdmissionError, match="exceeds 1 bytes"):
            agent_verbs._read_upload_record(record_fd, "request.json", "test record")
    finally:
        os.close(record_fd)


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ("group", "not an upload group directory"),
        ("name", "not a known upload name"),
        ("digest", "not a content address"),
        ("object_type", "unreadable"),
    ],
)
def test_odd_store_entries_refuse_without_deleting(tmp_path, entry, reason):
    service, identity, _checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    uploads = service.paths.uploads_dir
    if entry == "group":
        (uploads / "bad group").mkdir()
    else:
        name_dir = uploads / identity.group / ("unknown" if entry == "name" else "candidates")
        name_dir.mkdir(parents=True, exist_ok=True)
        if entry == "digest":
            (name_dir / "not-a-digest").write_bytes(b"unexpected")
        elif entry == "object_type":
            (name_dir / ("f" * 64)).mkdir()

    with pytest.raises(AdmissionError, match=reason):
        service._upload_store_objects()

    assert Path(old["path"]).read_bytes() == b"preserved"


def test_upload_directory_helpers_refuse_open_read_and_entry_limit_errors(tmp_path, monkeypatch):
    service, identity, checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    state_fd = service._state_directory_fd()
    try:
        (service.paths.state_dir / "linked").symlink_to(checkout, target_is_directory=True)
        with pytest.raises(AdmissionError, match="is unreadable"):
            agent_verbs._open_optional_upload_directory(state_fd, "linked", "test optional directory")
        (service.paths.state_dir / "not-directory").write_text("file", encoding="utf-8")
        with pytest.raises(AdmissionError, match="is unreadable"):
            agent_verbs._upload_directory(state_fd, "not-directory", "test directory").__enter__()
        monkeypatch.setattr(agent_verbs, "_MAX_UPLOAD_RECORDS", 0)
        with pytest.raises(AdmissionError, match="entry limit"):
            agent_verbs._upload_directory_entries(state_fd, "test listing")
    finally:
        os.close(state_fd)

    record_fd = os.open(service.paths.jobs_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(AdmissionError, match="is unreadable"):
            agent_verbs._read_upload_record(record_fd, "missing", "test record")
        (service.paths.jobs_dir / "read-me").write_text("data", encoding="utf-8")
        with pytest.raises(AdmissionError, match="is unreadable"):
            monkeypatch.setattr(agent_verbs.os, "read", lambda *_args: (_ for _ in ()).throw(OSError("read denied")))
            agent_verbs._read_upload_record(record_fd, "read-me", "test record")
    finally:
        os.close(record_fd)

    assert Path(old["path"]).read_bytes() == b"preserved"


def test_state_directory_open_failure_refuses_collection_without_changes(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    real_open = os.open

    def denied(path, flags, *args, **kwargs):
        if Path(path) == service.paths.state_dir:
            raise OSError("state directory denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(agent_verbs.os, "open", denied)
    with pytest.raises(AdmissionError, match="cannot inspect the agent state directory"):
        service._state_directory_fd()

    assert Path(old["path"]).read_bytes() == b"preserved"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda r: r.update(stores={"collect": None, "seed": None, "unknown": True}), "unknown stores shape"),
        (lambda r: r["stores"].update(seed={"sha256": "a" * 64, "bytes": 1, "extra": 1}), "unknown seed descriptor shape"),
        (lambda r: r["stores"].update(seed={"path": "store", "sha256": "bad", "bytes": 1}), "invalid stores.seed digest"),
        (lambda r: r.update(dev={"unknown": True}), "unknown dev shape"),
        (lambda r: r.update(dev={
            "tracked": {"sha256": "a" * 64, "bytes": 1, "extra": 1},
            "untracked": {"sha256": "b" * 64, "bytes": 1}, "verb_argv": ["status"],
            "tty": False, "worktree_key": "test-key",
        }, kind="dev"), "unknown dev.tracked descriptor shape"),
        (lambda r: r.update(dev={
            "tracked": {"sha256": "bad", "bytes": 1},
            "untracked": {"sha256": "b" * 64, "bytes": 1}, "verb_argv": ["status"],
            "tty": False, "worktree_key": "test-key",
        }, kind="dev"), "invalid dev.tracked digest"),
        (lambda r: r.update(extra_argv="bad"), "unknown extra_argv shape"),
        (lambda r: r.update(extra_argv=["--only-candidates", "not-a-digest"]), "invalid candidates reference"),
        (lambda r: r.update(extra_argv=["--only-candidates", "/tmp/other/" + "a" * 64]), "outside the authenticated group"),
    ],
)
def test_malformed_durable_references_refuse_without_store_changes(tmp_path, change, reason):
    service, identity, _checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    request = _submit()
    change(request)

    with pytest.raises(AdmissionError, match=reason):
        service._request_upload_digests(request, identity.group, "job")

    assert Path(old["path"]).read_bytes() == b"preserved"


def test_replay_streaming_during_collection_is_published_again(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _enable_collection(service)
    _upload_policy(service, store_bytes=7, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"abc")
    replay_digest = old["sha256"]
    replay_started = threading.Event()
    allow_replay = threading.Event()
    deleted = threading.Event()
    allow_collection = threading.Event()
    real_stream = service._stream_upload

    def pause_replay(stream, expected, digest, target):
        if digest == replay_digest:
            replay_started.set()
            assert allow_replay.wait(60)
        return real_stream(stream, expected, digest, target)

    def pause_collection(_item):
        deleted.set()
        assert allow_collection.wait(60)

    monkeypatch.setattr(service, "_stream_upload", pause_replay)
    monkeypatch.setattr(service, "_collection_hook", pause_collection)
    errors = []

    def put(content):
        try:
            _put(service, identity, "candidates", content)
        except BaseException as exc:
            errors.append(exc)

    replay = threading.Thread(target=put, args=(b"abc",))
    collector = threading.Thread(target=put, args=(b"1234",))
    replay.start()
    assert replay_started.wait(60)
    collector.start()
    assert deleted.wait(60)
    allow_replay.set()
    allow_collection.set()
    replay.join(60)
    collector.join(60)

    assert not replay.is_alive()
    assert not collector.is_alive()
    assert errors == []
    assert Path(old["path"]).read_bytes() == b"abc"
    assert (service.paths.uploads_dir / identity.group / "candidates" / hashlib.sha256(b"1234").hexdigest()).read_bytes() == b"1234"

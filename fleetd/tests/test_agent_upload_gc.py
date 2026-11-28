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


def test_bundle_is_retained_until_job_has_terminal_book_record(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _enable_collection(service)
    bundle = _put(service, identity, "in.bundle", b"bundle")
    job_dir = service.paths.jobs_dir / "bundle-job"
    job_dir.mkdir()
    request = _submit()
    (job_dir / "request.json").write_text(json.dumps({
        "schema": "fleet-host-job-request/1", "group": identity.group,
        "digest": "e" * 64, "request": request,
    }), encoding="utf-8")
    monkeypatch.setattr(service.admission.book, "read_record", lambda _job_id: {
        "schema": "fleet-admission-waiting/1", "job_id": "bundle-job", "group": identity.group,
    })
    with service._upload_store_lock():
        assert service._collect_upload_objects_locked(len(b"bundle"), len(b"bundle")) == 0
    assert Path(bundle["path"]).read_bytes() == b"bundle"

    monkeypatch.setattr(service.admission.book, "read_record", lambda _job_id: {
        "schema": "fleet-admission-terminal/1", "job_id": "bundle-job", "group": identity.group,
    })
    with service._upload_store_lock():
        assert service._collect_upload_objects_locked(len(b"bundle"), len(b"bundle")) == len(b"bundle")
    assert not Path(bundle["path"]).exists()


def test_refused_submit_leaves_collectable_empty_job_directory(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _enable_collection(service)
    service.executor = object()
    old = _put(service, identity, "candidates", b"old-content-long")
    monkeypatch.setattr(
        service.admission.book, "submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AdmissionError(2, "capacity refused")),
    )
    with pytest.raises(AdmissionError, match="capacity refused"):
        service.handle(_submit(), identity)
    refused_job_dir = service.paths.jobs_dir / "controller-run-1"
    assert sorted(path.name for path in refused_job_dir.iterdir()) == ["submit.lock"]
    assert service.admission.book.read_record("controller-run-1") is None

    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    _upload_policy(service, store_bytes=5, floors={"checkout": 0})
    new = _put(service, identity, "candidates", b"new!!")
    assert not Path(old["path"]).exists()
    assert Path(new["path"]).read_bytes() == b"new!!"


def test_upload_store_path_that_is_a_regular_file_refuses_accounting(tmp_path):
    service, _identity, _checkout = _service(tmp_path)
    service.paths.uploads_dir.rmdir()
    service.paths.uploads_dir.write_text("preserve", encoding="utf-8")

    with pytest.raises(AdmissionError, match="agent upload directory is not a real directory"):
        service._stored_upload_bytes()

    assert service.paths.uploads_dir.read_text(encoding="utf-8") == "preserve"


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


def test_submit_accepts_existing_candidate_and_names_relative_candidate_refusal(tmp_path):
    service, identity, _checkout = _service(
        tmp_path, extra_argv='[["--only-candidates", "<host-candidates>"]]'
    )
    result = _put(service, identity, "candidates", b"candidate")
    request = _submit()
    request["extra_argv"] = ["--only-candidates", result["path"]]
    service._require_submit_uploads(request, identity.group)
    request["extra_argv"][1] = "a" * 64

    with pytest.raises(AdmissionError, match="outside the authenticated group"):
        service._request_upload_digests(request, identity.group, "job")

    assert Path(result["path"]).read_bytes() == b"candidate"


def test_submit_inventory_is_locked_but_have_is_not(tmp_path, monkeypatch):
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
    assert entered == []

    service.executor = object()
    _enable_collection(service)
    def prepared_submit(*_args, **kwargs):
        if kwargs.get("prepare_request"):
            kwargs["prepare_request"]("a" * 64)
        return {"job_id": "job", "state": "queued"}

    service.admission.submit = prepared_submit
    request = _submit()
    request["extra_argv"] = ["--verbose"]
    service.handle(request, identity)
    assert entered


def test_blocked_admission_does_not_block_unrelated_put_or_have(tmp_path, monkeypatch):
    service, identity, checkout = _service(tmp_path)
    _enable_collection(service)
    service.executor = object()
    (checkout / ".git").write_text("gitdir: ../.git/worktrees/test\n", encoding="utf-8")
    admission_entered = threading.Event()
    release_admission = threading.Event()
    submit_errors = []

    def blocked_submit(*_args, **_kwargs):
        admission_entered.set()
        assert release_admission.wait(10)
        raise AdmissionError(2, "test admission refusal")

    monkeypatch.setattr(service.admission.book, "submit", blocked_submit)

    def submit_job():
        try:
            service.handle(_submit(), identity)
        except AdmissionError as exc:
            submit_errors.append(exc.reason)

    submit_thread = threading.Thread(target=submit_job)
    submit_thread.start()
    assert admission_entered.wait(10)

    put_done = threading.Event()
    have_done = threading.Event()
    put_thread = threading.Thread(target=lambda: (_put(service, identity, "candidates", b"parallel"), put_done.set()))
    have_thread = threading.Thread(
        target=lambda: (service.handle({"verb": "have", "repo": "vbpub"}, identity), have_done.set()),
    )
    put_thread.start()
    have_thread.start()
    assert put_done.wait(10)
    assert have_done.wait(10)
    release_admission.set()
    submit_thread.join()
    put_thread.join()
    have_thread.join()
    assert submit_errors == ["test admission refusal"]


def test_collector_classifies_every_submit_request_field(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    classified = agent_verbs._UPLOAD_REFERENCE_REQUEST_FIELDS | agent_verbs._NON_UPLOAD_REQUEST_FIELDS
    assert classified == frozenset(agent_verbs._CAPABILITIES["submit"])
    request = _submit()
    with pytest.raises(AdmissionError, match="unknown request field set"):
        service._request_upload_digests(request | {"new_upload_reference": "unknown"}, identity.group, "job")


def test_durable_request_inventory_retains_seed_dev_and_candidates(tmp_path, monkeypatch):
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
    monkeypatch.setattr(service.admission.book, "read_record", lambda _job_id: {
        "schema": "fleet-admission-terminal/1", "job_id": "job", "group": identity.group,
    })
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


def test_upload_object_stat_failure_refuses_collection_without_deleting(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    name_fd = os.open(Path(old["path"]).parent, os.O_RDONLY | os.O_DIRECTORY)
    opened = []
    real_fstat = os.fstat

    def open_object(parent_fd, name, *, directory=False):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        opened.append(fd)
        return fd

    def fail_object_stat(fd):
        if fd in opened:
            raise OSError("object stat denied")
        return real_fstat(fd)

    monkeypatch.setattr(agent_verbs, "open_beneath", open_object)
    monkeypatch.setattr(agent_verbs.os, "fstat", fail_object_stat)
    try:
        with pytest.raises(AdmissionError, match="object stat denied"):
            service._upload_name_objects(identity.group, "candidates", name_fd)
    finally:
        os.close(name_fd)

    assert Path(old["path"]).read_bytes() == b"preserved"


def test_collection_stops_after_enough_bytes_without_deleting_later_objects(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    oldest = _put(service, identity, "candidates", b"old")
    later = _put(service, identity, "in.bundle", b"later")
    os.utime(oldest["path"], (1, 1))
    os.utime(later["path"], (2, 2))

    with service._upload_store_lock():
        assert service._collect_upload_objects_locked(3, 0) == 3

    assert not Path(oldest["path"]).exists()
    assert Path(later["path"]).read_bytes() == b"later"


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
    ("field", "value", "reason"),
    [
        ("stores", None, "unknown stores shape"),
        ("dev", "bad", "unknown dev shape"),
        ("extra_argv", "bad", "unknown extra_argv shape"),
    ],
)
def test_submit_upload_inventory_rejects_unknown_shapes_without_changes(tmp_path, field, value, reason):
    service, identity, _checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    request = _submit()
    request[field] = value

    with pytest.raises(AdmissionError, match=reason):
        service._require_submit_uploads(request, identity.group)

    assert Path(old["path"]).read_bytes() == b"preserved"


def test_submit_with_non_array_argv_refuses_before_creating_state(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    request = _submit()
    request["extra_argv"] = "not-an-array"

    with pytest.raises(AdmissionError, match="extra_argv must be an argv array"):
        service.handle(request, identity)

    assert list(service.paths.jobs_dir.iterdir()) == []
    assert list(service.paths.incoming_dir.iterdir()) == []


def test_collection_noop_and_record_failure_leave_objects_untouched(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    assert service._collect_upload_objects_locked(0, 0) == 0
    job_dir = service.paths.jobs_dir / "job"
    job_dir.mkdir()
    (job_dir / "request.json").write_text("{", encoding="utf-8")

    with pytest.raises(AdmissionError, match="nothing was collected.*unreadable"):
        service._collect_upload_objects_locked(1, 0)

    assert Path(old["path"]).read_bytes() == b"preserved"


@pytest.mark.parametrize("failure", ["admission", "directory", "oserror"])
def test_unlink_failure_refuses_and_preserves_object(tmp_path, monkeypatch, failure):
    service, identity, _checkout = _service(tmp_path)
    old = _put(service, identity, "candidates", b"preserved")
    item = service._upload_store_objects()[0]
    if failure == "admission":
        monkeypatch.setattr(
            service, "_state_directory_fd",
            lambda: (_ for _ in ()).throw(AdmissionError(2, "state denied")),
        )
        reason = "after a partial collection.*state denied"
    elif failure == "directory":
        monkeypatch.setattr(
            agent_verbs, "open_beneath",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("directory vanished")),
        )
        reason = "after a partial collection.*unreadable"
    else:
        monkeypatch.setattr(
            agent_verbs.os, "unlink",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unlink denied")),
        )
        reason = "cannot delete store object.*unlink denied"

    with pytest.raises(AdmissionError, match=reason):
        service._unlink_upload_object(item)

    assert Path(old["path"]).read_bytes() == b"preserved"


def test_existing_replay_commits_through_existing_object_path(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"same")

    replay = _put(service, identity, "candidates", b"same")

    assert replay["path"] == old["path"]
    assert Path(old["path"]).read_bytes() == b"same"


@pytest.mark.parametrize("collision", ["symlink", "content"])
def test_put_refuses_commit_path_collision_without_publishing_replay(tmp_path, monkeypatch, collision):
    service, identity, _checkout = _service(tmp_path)
    content = b"body"
    digest = hashlib.sha256(content).hexdigest()
    destination = service.paths.uploads_dir / identity.group / "candidates" / digest
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    real_stream = service._stream_upload

    def inject_collision(stream, expected, requested_digest, target):
        real_stream(stream, expected, requested_digest, target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if collision == "symlink":
            destination.symlink_to(outside)
        else:
            destination.write_bytes(b"wrong")

    monkeypatch.setattr(service, "_stream_upload", inject_collision)
    reason = "destination is a symlink" if collision == "symlink" else "does not match its content address"
    with pytest.raises(AdmissionError, match=reason):
        _put(service, identity, "candidates", content)

    assert destination.is_symlink() if collision == "symlink" else destination.read_bytes() == b"wrong"
    assert list(service.paths.incoming_dir.iterdir()) == []


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

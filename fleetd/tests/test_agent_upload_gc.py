from dataclasses import replace
import hashlib
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

from dataclasses import replace
from pathlib import Path

import pytest

from fleetd.admission import AdmissionError
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

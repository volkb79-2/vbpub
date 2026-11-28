from pathlib import Path

import pytest

from fleetd.admission import AdmissionError
from test_agent_verbs import _put, _service, _upload_policy


def test_put_that_cannot_reclaim_enough_bytes_deletes_nothing(tmp_path):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"old")
    _upload_policy(service, store_bytes=5, floors={"checkout": 0})

    with pytest.raises(AdmissionError, match="max_upload_store_bytes"):
        _put(service, identity, "candidates", b"new-content")

    assert Path(old["path"]).read_bytes() == b"old"


def test_disk_floor_refusal_does_not_collect(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=100, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"old")
    _upload_policy(service, store_bytes=1, floors={"checkout": 10})
    monkeypatch.setattr(service, "_upload_free_bytes", lambda: 10)

    with pytest.raises(AdmissionError, match="disk floor"):
        service._reserve_upload(1, 1, 10)

    assert Path(old["path"]).read_bytes() == b"old"


def test_reupload_of_stored_object_does_not_collect(tmp_path, monkeypatch):
    service, identity, _checkout = _service(tmp_path)
    _upload_policy(service, store_bytes=3, floors={"checkout": 0})
    old = _put(service, identity, "candidates", b"abc")
    deleted = []
    monkeypatch.setattr(service, "_collection_hook", lambda item: deleted.append(item.key))

    result = _put(service, identity, "candidates", b"abc")

    assert result["path"] == old["path"]
    assert Path(old["path"]).read_bytes() == b"abc"
    assert deleted == []

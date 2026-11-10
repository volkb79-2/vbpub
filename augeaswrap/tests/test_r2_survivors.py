"""Behavioral regressions for the v0.2.0 R2 survivor review."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from augeaswrap import Change, Plan, PostAction, Problem, Request, Result, _fs


def test_public_records_are_frozen():
    request = Request("writer")
    change = Change("file", "/etc/example", "change", {}, {})
    records = (
        (Problem("problem", "message"), "code", "changed"),
        (change, "kind", "other"),
        (PostAction("reload", ("systemctl", "daemon-reload"), 1), "name", "other"),
        (request, "writer", "other"),
        (Plan(request, "/", (change,)), "root", "/other"),
        (Result(None), "txn", "other"),
    )
    for record, field_name, replacement in records:
        with pytest.raises(FrozenInstanceError):
            setattr(record, field_name, replacement)


def test_change_resource_is_excluded_from_equality_and_repr():
    common = ("file", "/etc/example", "change", {}, {})
    left = Change(*common, resource=object())
    right = Change(*common, resource=object())

    assert left == right
    assert "resource=" not in repr(left)


def test_plan_observation_is_excluded_from_equality_and_repr():
    request = Request("writer")
    left = Plan(request, "/", (), observed={"fingerprint": "old"})
    right = Plan(request, "/", (), observed={"fingerprint": "new"})

    assert left == right
    assert "observed=" not in repr(left)


def test_post_action_rejects_an_empty_argv_tuple():
    with pytest.raises(ValueError, match="post-action argv is required"):
        PostAction("reload", (), 1)


@pytest.mark.parametrize(
    "result",
    (
        Result(None, changed=("/etc/example",)),
        Result(None, actions=("reload",)),
        Result(None, pending=("restart",)),
    ),
)
def test_result_has_changes_when_any_result_collection_is_nonempty(result):
    assert result.has_changes is True


def test_problem_keeps_its_string_and_problem_equality_contract():
    problem = Problem("bad", "invalid request", path="/etc/example")

    assert str(problem) == "invalid request"
    assert problem == "invalid request"
    assert problem == Problem("bad", "invalid request", path="/etc/example")


def test_lock_does_not_unlock_a_descriptor_before_acquisition(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    lock_path = root / "etc" / "writer.lock"
    lock_path.write_bytes(b"")
    operations = []

    def record_flock(descriptor, operation):
        operations.append(operation)

    def failed_fstat(_descriptor):
        raise OSError("fstat failed before flock")

    monkeypatch.setattr(_fs.fcntl, "flock", record_flock)
    with pytest.raises(OSError, match="fstat failed"):
        with _fs.lock(
            root,
            lock_path,
            exclusive=False,
            create=False,
            fstat_fn=failed_fstat,
        ):
            pytest.fail("lock yielded after fstat failed")

    assert operations == []


def test_lock_releases_a_successfully_acquired_descriptor(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    lock_path = root / "etc" / "writer.lock"
    lock_path.write_bytes(b"")
    operations = []

    def record_flock(descriptor, operation):
        operations.append(operation)

    monkeypatch.setattr(_fs.fcntl, "flock", record_flock)
    with _fs.lock(root, lock_path, exclusive=False, create=False) as held:
        assert held is True

    assert operations == [_fs.fcntl.LOCK_SH, _fs.fcntl.LOCK_UN]

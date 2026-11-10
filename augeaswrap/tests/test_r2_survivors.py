"""Behavioral regressions for the v0.2.0 R2 survivor review."""

from __future__ import annotations

import hashlib
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import Change, Plan, PostAction, Problem, Request, Result, _fs, kinds
from augeaswrap.check import Check, PathMode
from augeaswrap._resource_files import StagedFile, commit_file, restore_file, stage_file
from augeaswrap import augtool
from augeaswrap.formats.fstab import mount_item, swap_item
from augeaswrap.shadow import Assignment, Finding, LayerFile, TargetClass, effective_files, analyze


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
        (StagedFile("/tmp/stage", {"exists": False}), "path", "/tmp/other"),
        (augtool.AugtoolOutput("out", "err"), "stdout", "other"),
        (kinds.RevertHooks(snapshot=lambda change, observed: {}, restore=lambda address, record, actions: None), "snapshot", None),
        (
            kinds.KindSpec(kind="example", parse=lambda raw: None, to_toml=lambda resource: {}, revert=None),
            "kind",
            "other",
        ),
        (TargetClass("test", ("/etc/test.d",), "last"), "name", "other"),
        (LayerFile("/etc/test.d/example.conf", b"x", 0), "rank", 1),
        (Assignment("/etc/test.d/example.conf", "key", "value", (0, 0)), "value", "other"),
        (Finding("shadow.later", "/usr/lib/example.conf", "key", "message", "error"), "severity", "note"),
        (PathMode("/etc/example", 0, 0, "0644", "must-match"), "path", "/etc/changed"),
        (Check("present", exec=("/bin/true",), expect_exit=0, timeout=1), "state", "absent"),
    )
    for record, field_name, replacement in records:
        with pytest.raises(FrozenInstanceError):
            setattr(record, field_name, replacement)


def test_kind_registry_records_are_keyword_only():
    with pytest.raises(TypeError):
        kinds.RevertHooks(lambda change, observed: {}, lambda address, record, actions: None)
    with pytest.raises(TypeError):
        kinds.KindSpec("example", lambda raw: None, lambda resource: {}, None)


@pytest.mark.parametrize("priority", [-1, 32767])
def test_swap_priority_includes_both_documented_boundaries(priority):
    assert swap_item("UUID=swap", priority)["opt"] == ["sw", f"pri={priority}"]


def test_mount_dump_includes_one_as_a_valid_boundary():
    assert mount_item("UUID=data", "/data", "ext4", ["defaults"], "1", "2")["dump"] == "1"


def test_shadow_observation_rejects_noncanonical_base64_whitespace():
    with pytest.raises(ValueError):
        effective_files(
            {"files": {"/etc/test.d/example.conf": "Y Q=="}, "links": {}},
            TargetClass("test", ("/etc/test.d",), "last"),
        )


@pytest.mark.parametrize("merge", ["last", "first"])
def test_authoritative_equal_order_different_values_are_not_shadowed(merge):
    target = "/etc/test.d/target.conf"
    other = "/usr/lib/test.d/other.conf"
    assignments = (
        Assignment(target, "key", "ours", (0, 0)),
        Assignment(other, "key", "theirs", (0, 0)),
    )

    assert analyze(
        target_file=target,
        assignments=assignments,
        target_keys=("key",),
        intent="authoritative",
        merge=merge,
        accept_shadow=(),
    ) == ()


@pytest.mark.parametrize("merge", ["last", "first"])
def test_shadow_ties_have_stable_later_code(merge):
    target = "/etc/test.d/target.conf"
    other = "/usr/lib/test.d/other.conf"
    finding = analyze(
        target_file=target,
        assignments=(
            Assignment(target, "key", "same", (0, 0)),
            Assignment(other, "key", "same", (0, 0)),
        ),
        target_keys=("key",),
        intent="authoritative",
        merge=merge,
        accept_shadow=(),
    )[0]

    assert finding.code == ("shadow.earlier" if merge == "last" else "shadow.later")


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


def test_stage_file_creates_missing_stage_parents_and_can_replace_a_stage(tmp_path):
    stage_dir = tmp_path / "not-created-yet"
    ctx = SimpleNamespace(root=tmp_path / "root", stage_dir=stage_dir)
    first = {"exists": True, "data_b64": "b2xk", "mode": 0o600}
    second = {"exists": True, "data_b64": "bmV3", "mode": 0o600}

    staged = stage_file(ctx, "file:/etc/example", "/etc/example", first)
    assert Path(staged.path).read_bytes() == b"old"
    staged = stage_file(ctx, "file:/etc/example", "/etc/example", second)

    assert Path(staged.path).read_bytes() == b"new"


def test_stage_file_rejects_non_base64_image_bytes(tmp_path):
    ctx = SimpleNamespace(root=tmp_path / "root", stage_dir=tmp_path / "stages")
    image = {"exists": True, "data_b64": "?", "mode": 0o600}

    with pytest.raises(ValueError):
        stage_file(ctx, "file:/etc/example", "/etc/example", image)


def test_commit_file_rejects_non_base64_preimage_bytes_before_writing(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    expected = {
        "exists": True,
        "data_b64": "?",
        "mode": 0o600,
        "uid": os.geteuid(),
        "gid": os.getegid(),
    }

    with pytest.raises(ValueError):
        commit_file(SimpleNamespace(root=root), "/etc/example", StagedFile("unused", expected))

    assert not (root / "etc" / "example").exists()


def test_restore_file_refuses_relative_and_noncanonical_preimage_paths():
    with pytest.raises(ValueError, match="path is invalid"):
        restore_file("file", "file:etc/example", {"path": "etc/example", "exists": False}, ())


def test_restore_file_rejects_boolean_ids_and_accepts_the_maximum_mode():
    with pytest.raises(ValueError, match="non-negative integers"):
        restore_file(
            "file",
            "file:/etc/example",
            {
                "path": "/etc/example",
                "exists": True,
                "mode": True,
                "uid": 0,
                "gid": 0,
                "data_b64": "YQ==",
                "sha256": hashlib.sha256(b"a").hexdigest(),
            },
            (),
        )

    restored = restore_file(
        "file",
        "file:/etc/example",
        {
            "path": "/etc/example",
            "exists": True,
            "mode": 0o7777,
            "uid": 0,
            "gid": 0,
            "data_b64": "YQ==",
            "sha256": hashlib.sha256(b"a").hexdigest(),
        },
        (),
    )
    assert restored.image["mode"] == 0o7777


def test_restore_file_rejects_non_base64_preimage_bytes():
    with pytest.raises(ValueError):
        restore_file(
            "file",
            "file:/etc/example",
            {
                "path": "/etc/example",
                "exists": True,
                "mode": 0o600,
                "uid": 0,
                "gid": 0,
                "data_b64": "?",
                "sha256": hashlib.sha256(b"").hexdigest(),
            },
            (),
        )


def test_augtool_error_parser_requires_a_real_augeas_error_node():
    ordinary_tree_value = '/augeas/files/etc/fstab/1/spec = "UUID=data"'

    assert augtool._error_text(ordinary_tree_value, "", 0) is None


def test_augtool_private_root_creates_nested_and_root_level_target_parents(tmp_path, monkeypatch):
    def make_lens_dir(_source, destination):
        destination.mkdir(mode=0o700)

    def check_private_target(argv, *, timeout):
        root = Path(argv[1].removeprefix("--root="))
        assert (root / "nested/deep/fstab").exists() or (root / "fstab").exists()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(augtool, "_copy_tree", make_lens_dir)
    monkeypatch.setattr(augtool.shadow, "run_command", check_private_target)

    nested = augtool.run(path="/nested/deep/fstab", lens="Fstab", source=b"", script="load\n")
    root_level = augtool.run(path="/fstab", lens="Fstab", source=b"", script="load\n")

    assert nested.saved is None and root_level.saved is None

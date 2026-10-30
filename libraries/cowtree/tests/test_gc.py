from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import socket
from pathlib import Path

import cowtree.gc as gc_module
import pytest
from cowtree import SpaceEstimate, open_run
from cowtree.backends import CopyBackend
from cowtree.detect import Area, Capabilities
from cowtree.errors import BackendFailed, PathRefused
from cowtree.gc import (
    _allocated_bytes,
    _manifest,
    dead_flock_runs,
    gc,
    make_shared,
    survey,
)
from cowtree.space import pending_delete_bytes

from .fakes import StatResultProxy


def _abandon(run):
    fcntl.flock(run._lock_fd, fcntl.LOCK_UN)
    os.close(run._lock_fd)
    run._closed = True


def _leave_run_in_process(base, tool, connection, area_root, filesystem, create_tree, payload_bytes, remove_trees, trees_target):
    from .fakes import FakeOps

    if area_root is None:
        environ = {"COWTREE_MODE": "off"}
        ops = None
    else:
        environ = {"MDT_COW_ROOT": area_root}
        ops = FakeOps(filesystem=filesystem)
    run = open_run(
        tool,
        estimate=SpaceEstimate(0, 0, 0),
        environ=environ,
        ops=ops,
        plain_dir=base if area_root is None else None,
    )
    if create_tree:
        tree = run.make_tree("tree")
        if payload_bytes:
            (tree / "payload").write_bytes(b"x" * payload_bytes)
    trees_path = Path(run.path) / "trees"
    if remove_trees:
        trees_path.rmdir()
    elif trees_target is not None:
        trees_path.rmdir()
        trees_path.symlink_to(trees_target, target_is_directory=True)
    connection.send(run.path)
    connection.close()


def _dead_run(base, tool, *, area_root=None, filesystem="xfs", create_tree=False, payload_bytes=0, remove_trees=False, trees_target=None):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_leave_run_in_process,
        args=(
            str(base), tool, child, None if area_root is None else str(area_root), filesystem,
            create_tree, payload_bytes, remove_trees,
            None if trees_target is None else str(trees_target),
        ),
    )
    process.start()
    child.close()
    if not parent.poll(20):
        process.terminate()
        process.join()
        raise AssertionError("spawned owner did not report its run directory")
    path = Path(parent.recv())
    parent.close()
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join()
    assert process.exitcode == 0
    return path


def _area(root, *, backend=None):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private = root / ".cowtree"
    runs = private / "runs"
    shared = root / "shared"
    for path in (private, runs, shared):
        path.mkdir(mode=0o700, exist_ok=True)
    return Area(
        str(root),
        Capabilities("copy", "ext4", False, False, True, False, False),
        backend or CopyBackend(),
        object(),
        str(private),
        str(runs),
        str(shared),
    )


def test_gc_deletes_only_lease_free_flock_runs(tmp_path):
    live = open_run("live", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    live.make_tree("tree")
    results = gc(tmp_path)
    assert [(item.name, item.status) for item in results] == [(Path(live.path).name, "lease-held")]
    live.close()

    dead_path = _dead_run(tmp_path, "dead", create_tree=True)
    results = gc(tmp_path)
    assert [(item.name, item.status) for item in results] == [(dead_path.name, "deleted")]
    assert not dead_path.exists()


def test_gc_retains_unlocked_run_while_manifest_owner_is_live(tmp_path):
    run = open_run("unlocked-live", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    _abandon(run)
    result = gc(tmp_path)
    assert result[0].status == "owner-live"
    assert path.exists()


def test_owner_status_uses_boot_pid_namespace_and_pid_starttime(monkeypatch):
    import cowtree.gc as gc_module

    owner = {
        "boot_id": "boot-current",
        "hostname": socket.gethostname(),
        "pid": 42,
        "pid_starttime": 100,
        "pidns": 77,
        "uid": os.geteuid(),
    }
    original_read_text = Path.read_text

    def read_text(path, *args, **kwargs):
        if str(path) == "/proc/sys/kernel/random/boot_id":
            return "boot-current\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    original_stat = os.stat

    def namespace_stat(path, *args, **kwargs):
        if str(path) == "/proc/self/ns/pid":
            return type("Stat", (), {"st_ino": 77})()
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(gc_module.os, "stat", namespace_stat)
    monkeypatch.setattr(gc_module, "_pid_starttime", lambda _pid: 100)
    assert gc_module._owner_status(owner) == "live"

    monkeypatch.setattr(gc_module, "_pid_starttime", lambda _pid: 101)
    assert gc_module._owner_status(owner) == "dead"
    monkeypatch.setattr(
        gc_module,
        "_pid_starttime",
        lambda _pid: (_ for _ in ()).throw(FileNotFoundError("process exited")),
    )
    assert gc_module._owner_status(owner) == "dead"
    monkeypatch.setattr(
        gc_module,
        "_pid_starttime",
        lambda _pid: (_ for _ in ()).throw(PermissionError("proc denied")),
    )
    assert gc_module._owner_status(owner) == "unknown"

    assert gc_module._owner_status({**owner, "boot_id": "previous-boot"}) == "dead"
    assert gc_module._owner_status({**owner, "pidns": 78}) == "unknown"
    assert gc_module._owner_status({**owner, "uid": owner["uid"] + 1}) == "unknown"
    assert gc_module._owner_status({**owner, "hostname": "other-host"}) == "unknown"
    assert gc_module._owner_status({**owner, "hostname": "other-host", "boot_id": "other-boot"}) == "unknown"

    def denied_namespace(path, *args, **kwargs):
        if str(path) == "/proc/self/ns/pid":
            raise PermissionError("namespace denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(gc_module.os, "stat", denied_namespace)
    assert gc_module._owner_status(owner) == "unknown"

    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boot unavailable")))
    assert gc_module._owner_status(owner) == "unknown"

    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "\n")
    assert gc_module._owner_status(owner) == "unknown"

    monkeypatch.setattr(gc_module.socket, "gethostname", lambda: (_ for _ in ()).throw(OSError("hostname denied")))
    assert gc_module._owner_status(owner) == "unknown"


def test_gc_retains_run_when_owner_namespace_cannot_be_compared(tmp_path):
    run = open_run("unknown-owner", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    _abandon(run)
    manifest_path = path / "owner.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["owner"]["pidns"] += 1
    manifest_path.write_text(json.dumps(manifest))

    result = gc(tmp_path)
    assert result[0].status == "owner-identity-unknown"
    assert path.exists()


def test_gc_never_deletes_external_or_manifestless(tmp_path):
    external = open_run(
        "external",
        estimate=SpaceEstimate(0, 0, 0),
        holder="external",
        external_ref="vm:42",
        environ={},
        plain_dir=tmp_path,
    )
    external_path = external.path
    _abandon(external)
    manifestless = tmp_path / "old-orphan"
    manifestless.mkdir()
    results = gc(tmp_path)
    states = {item.name: item.status for item in results}
    assert states[Path(external_path).name] == "external"
    assert states[manifestless.name] == "manifestless-or-invalid"
    assert Path(external_path).exists() and manifestless.exists()


@pytest.mark.parametrize("payload", ["{", "{}", json.dumps({"format": "wrong"})])
def test_gc_retains_invalid_manifests(tmp_path, payload):
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "owner.json").write_text(payload)
    result = gc(tmp_path)
    assert result[0].status == "manifestless-or-invalid"
    assert directory.exists()


def test_manifest_reader_rejects_owner_json_larger_than_limit(tmp_path):
    run_path = _dead_run(tmp_path, "oversized-manifest")
    manifest_path = run_path / "owner.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" " * (64 * 1024))

    assert _manifest(str(run_path)) is None


def test_manifest_validator_rejects_malformed_identity_and_estimate_fields(tmp_path):
    run = open_run("manifest-shapes", estimate=SpaceEstimate(1, 2, 3), environ={}, plain_dir=tmp_path)
    path = Path(run.path) / "owner.json"
    valid = json.loads(path.read_text())
    assert _manifest(run.path) == valid

    def altered(section, key, value):
        document = json.loads(json.dumps(valid))
        target = document if section is None else document[section]
        target[key] = value
        return document

    missing_external_ref = json.loads(json.dumps(valid))
    del missing_external_ref["external_ref"]
    missing_estimate_key = altered("estimate", "ingest_bytes", None)
    del missing_estimate_key["estimate"]["ingest_bytes"]
    missing_owner_key = json.loads(json.dumps(valid))
    del missing_owner_key["owner"]["pidns"]
    missing_required_key = json.loads(json.dumps(valid))
    del missing_required_key["tool"]
    invalid_documents = [
        [],
        altered(None, "format", "wrong"),
        missing_required_key,
        missing_external_ref,
        altered(None, "holder", "unknown"),
        altered(None, "run_id", 3),
        altered(None, "run_id", ""),
        altered(None, "tool", None),
        altered(None, "labels", []),
        altered(None, "estimate", []),
        altered(None, "backend", []),
        altered(None, "api", True),
        altered(None, "api", 2),
        altered(None, "created_utc", 3),
        altered(None, "holder", "external") | {"external_ref": None},
        altered(None, "holder", "external") | {"external_ref": 5},
        altered(None, "holder", "external") | {"external_ref": ""},
        altered(None, "external_ref", "vm:1"),
        missing_estimate_key,
        altered("estimate", "ingest_bytes", True),
        altered("estimate", "per_clone_bytes", 1.5),
        altered("estimate", "max_live_clones", -1),
        altered(None, "owner", []),
        missing_owner_key,
        altered("owner", "uid", True),
        altered("owner", "pid", -1),
        altered("owner", "pid", 0),
        altered("owner", "pid_starttime", "1"),
        altered("owner", "pid_starttime", 0),
        altered("owner", "boot_id", ""),
        altered("owner", "boot_id", 4),
        altered("owner", "hostname", ""),
        altered("owner", "hostname", 4),
    ]
    for document in invalid_documents:
        path.write_text(json.dumps(document))
        assert _manifest(run.path) is None
    path.write_text(json.dumps(valid))
    run.close()


def test_gc_retains_nonregular_owner_manifest_without_blocking(tmp_path):
    directory = tmp_path / "fifo-manifest"
    directory.mkdir()
    os.mkfifo(directory / "owner.json")
    result = gc(tmp_path)
    assert result[0].status == "manifestless-or-invalid"
    assert directory.exists()


def test_gc_retains_symlinked_owner_manifest(tmp_path):
    directory = tmp_path / "linked-manifest"
    directory.mkdir()
    target = tmp_path / "manifest-target"
    target.write_text("{}")
    (directory / "owner.json").symlink_to(target)
    result = gc(tmp_path)
    assert result[0].status == "manifestless-or-invalid"


def test_gc_retains_non_utf8_owner_manifest(tmp_path):
    directory = tmp_path / "non-utf8-manifest"
    directory.mkdir()
    (directory / "owner.json").write_bytes(b"\xff")
    result = gc(tmp_path)
    assert result[0].status == "manifestless-or-invalid"


def test_gc_missing_scan_root_is_empty(tmp_path):
    assert gc(tmp_path / "absent") == []


def test_gc_rejects_non_directory_scan_root_and_reports_entry_inspection_error(tmp_path, monkeypatch):
    regular = tmp_path / "regular"
    regular.write_text("not a directory")
    with pytest.raises(PathRefused, match="real directory"):
        gc(regular)

    run = open_run("inspect-error", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    _abandon(run)
    real_lstat = gc_module.os.lstat

    def fail_run_lstat(value, *args, **kwargs):
        if os.fspath(value) == str(path):
            raise PermissionError(13, "entry inspection denied")
        return real_lstat(value, *args, **kwargs)

    monkeypatch.setattr(gc_module.os, "lstat", fail_run_lstat)
    result = gc(tmp_path)
    assert {item.name: item.status for item in result}[path.name] == "inspect-error"
    monkeypatch.setattr(gc_module.os, "lstat", real_lstat)
    import shutil
    shutil.rmtree(path)


def test_allocated_bytes_accounts_for_files_symlinks_and_nested_directories(tmp_path):
    root = tmp_path / "run"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "payload").write_bytes(b"x" * 4096)
    (nested / "more").write_text("payload")
    (root / "link").symlink_to(nested)
    allocated, device = _allocated_bytes(str(root))
    assert allocated > 0 and device == os.lstat(root).st_dev


def test_allocated_bytes_accounts_for_a_regular_file(tmp_path):
    path = tmp_path / "payload"
    path.write_bytes(b"allocated")
    info = os.lstat(path)
    assert _allocated_bytes(str(path)) == (info.st_blocks * 512, info.st_dev)


def test_dead_flock_inventory_filters_holder_and_lease_outcomes(tmp_path, monkeypatch):
    external = open_run(
        "external-inventory", estimate=SpaceEstimate(0, 0, 0), holder="external",
        external_ref="vm:inventory", environ={}, plain_dir=tmp_path,
    )
    external_path = Path(external.path)
    _abandon(external)
    assert dead_flock_runs(tmp_path) == []
    import shutil
    shutil.rmtree(external_path)

    managed = open_run("managed-inventory", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    managed_path = Path(managed.path)
    _abandon(managed)
    monkeypatch.setattr(gc_module, "_owner_status", lambda _owner: "dead")
    assert dead_flock_runs(tmp_path) == [managed_path.name]
    real_flock = gc_module.fcntl.flock

    def lock_error(_fd, operation):
        if operation & fcntl.LOCK_NB:
            raise OSError(5, "inventory lock error")
        return real_flock(_fd, operation)

    monkeypatch.setattr(gc_module.fcntl, "flock", lock_error)
    assert dead_flock_runs(tmp_path) == []
    monkeypatch.setattr(gc_module.fcntl, "flock", real_flock)
    shutil.rmtree(managed_path)


def test_gc_and_dead_inventory_handle_lock_inspection_and_scan_failures(tmp_path, monkeypatch):
    assert dead_flock_runs(tmp_path / "missing") == []
    regular = tmp_path / "regular"
    regular.write_text("not a directory")
    assert dead_flock_runs(regular) == []
    link = tmp_path / "linked"
    link.symlink_to(regular)
    assert dead_flock_runs(link) == []

    run = open_run("fstat-lock", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    _abandon(run)
    real_fstat = gc_module.os.fstat

    def fail_lock_fstat(fd):
        target = os.readlink(f"/proc/self/fd/{fd}")
        if target.endswith("/owner.lock"):
            raise PermissionError(13, "lock fstat denied")
        return real_fstat(fd)

    monkeypatch.setattr(gc_module.os, "fstat", fail_lock_fstat)
    assert {item.name: item.status for item in gc(tmp_path)}[path.name] == "lock-unavailable"
    assert dead_flock_runs(tmp_path) == []

    def nonregular_lock_fstat(fd):
        target = os.readlink(f"/proc/self/fd/{fd}")
        if target.endswith("/owner.lock"):
            return StatResultProxy(real_fstat(fd), st_mode=0o040700)
        return real_fstat(fd)

    monkeypatch.setattr(gc_module.os, "fstat", nonregular_lock_fstat)
    assert dead_flock_runs(tmp_path) == []
    monkeypatch.setattr(gc_module.os, "fstat", real_fstat)

    real_lstat = gc_module.os.lstat

    def fail_run_lstat(value, *args, **kwargs):
        if os.fspath(value) == str(path):
            raise PermissionError(13, "run stat denied")
        return real_lstat(value, *args, **kwargs)

    monkeypatch.setattr(gc_module.os, "lstat", fail_run_lstat)
    assert dead_flock_runs(tmp_path) == []
    monkeypatch.setattr(gc_module.os, "lstat", real_lstat)
    import shutil
    shutil.rmtree(path)


def test_gc_retains_missing_or_invalid_lock_and_non_directory(tmp_path):
    missing_lock = open_run("missing-lock", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(missing_lock.path)
    fcntl.flock(missing_lock._lock_fd, fcntl.LOCK_UN)
    os.close(missing_lock._lock_fd)
    missing_lock._closed = True
    (path / "owner.lock").unlink()
    (tmp_path / "symlink-run").symlink_to(path, target_is_directory=True)
    assert dead_flock_runs(tmp_path) == []
    results = {item.name: item.status for item in gc(tmp_path)}
    assert results[path.name] == "lock-unavailable"
    assert results["symlink-run"] == "not-directory"
    assert path.exists()


def test_gc_retains_nonregular_lock(tmp_path):
    run = open_run("invalid-lock", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    _abandon(run)
    (path / "owner.lock").unlink()
    (path / "owner.lock").mkdir()
    result = gc(tmp_path)
    assert result[0].status == "invalid-lock"
    assert path.exists()


def test_gc_checks_the_opened_lock_is_regular(tmp_path, monkeypatch):
    run = open_run("invalid-open-lock", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    _abandon(run)
    real_fstat = gc_module.os.fstat

    def nonregular_lock(fd):
        target = os.readlink(f"/proc/self/fd/{fd}")
        if target.endswith("/owner.lock"):
            return StatResultProxy(real_fstat(fd), st_mode=0o040700)
        return real_fstat(fd)

    monkeypatch.setattr(gc_module.os, "fstat", nonregular_lock)
    result = {item.name: item.status for item in gc(tmp_path)}
    assert result[path.name] == "invalid-lock"
    assert path.exists()


def test_dead_flock_inventory_swallows_lock_close_error(tmp_path, monkeypatch):
    run = open_run("inventory-close-error", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    _abandon(run)
    lock_path = path / "owner.lock"
    real_open = gc_module.os.open
    real_fstat = gc_module.os.fstat
    real_close = gc_module.os.close
    descriptors = []

    def track_open(value, *args, **kwargs):
        descriptor = real_open(value, *args, **kwargs)
        if os.fspath(value) == str(lock_path):
            descriptors.append(descriptor)
        return descriptor

    def fail_lock_fstat(descriptor):
        if descriptor in descriptors:
            raise PermissionError(13, "inventory lock fstat denied")
        return real_fstat(descriptor)

    def fail_lock_close(descriptor):
        if descriptor in descriptors:
            raise OSError(5, "inventory lock close denied")
        return real_close(descriptor)

    monkeypatch.setattr(gc_module.os, "open", track_open)
    monkeypatch.setattr(gc_module.os, "fstat", fail_lock_fstat)
    monkeypatch.setattr(gc_module.os, "close", fail_lock_close)
    assert dead_flock_runs(tmp_path) == []
    assert len(descriptors) == 1
    monkeypatch.setattr(gc_module.os, "close", real_close)
    real_close(descriptors[0])


def test_gc_reports_lock_and_delete_errors(tmp_path, monkeypatch):
    import cowtree.gc as gc_module

    locked = open_run("lock-error", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    real_flock = gc_module.fcntl.flock

    def fail_flock(fd, operation):
        if operation & fcntl.LOCK_NB:
            raise OSError(5, "lock manager failed")
        return real_flock(fd, operation)

    monkeypatch.setattr(gc_module.fcntl, "flock", fail_flock)
    assert gc(tmp_path)[0].status == "lock-error"
    monkeypatch.setattr(gc_module.fcntl, "flock", real_flock)
    locked.close()

    path = _dead_run(tmp_path, "delete-error")

    def fail_delete(_path):
        raise OSError(5, "delete failed")

    monkeypatch.setattr(gc_module.shutil, "rmtree", fail_delete)
    result = gc(tmp_path)
    assert {item.name: item.status for item in result}[path.name] == "delete-failed"
    assert path.exists()


def test_gc_rejects_symlink_scan_root(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PathRefused, match="real directory"):
        gc(link)


def test_gc_records_btrfs_pending_delete_on_scan_device(tmp_path, monkeypatch):
    import cowtree.gc as gc_module

    root = tmp_path / "area"
    root.mkdir()
    run_path = _dead_run(root, "pending-gc", area_root=root, filesystem="btrfs", create_tree=True, payload_bytes=8192)
    device = os.stat(root).st_dev
    before = pending_delete_bytes(device)
    monkeypatch.setattr(gc_module, "_allocated_bytes", lambda _path: (8192, device + 1000))
    result = gc(run_path.parent)
    assert result[0].status == "deleted"
    assert pending_delete_bytes(device) >= before + 8192
    assert not run_path.exists()


def test_gc_deletes_btrfs_subvolume_trees_with_backend(tmp_path, monkeypatch):
    from .fakes import FakeOps

    root = tmp_path / "area"
    root.mkdir()
    ops = FakeOps(filesystem="btrfs")
    run_path = _dead_run(root, "btrfs-gc", area_root=root, filesystem="btrfs", create_tree=True)
    tree = run_path / "trees" / "tree"
    ops.destroy_calls.clear()

    real_stat = os.stat
    target = os.path.abspath(tree)

    def subvolume_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if isinstance(path, (str, bytes, os.PathLike)) and os.path.abspath(os.fspath(path)) == target:
            values = list(result)
            values[1] = 256
            return os.stat_result(values)
        return result

    monkeypatch.setattr(os, "stat", subvolume_stat)
    result = gc(run_path.parent, ops=ops)
    assert result[0].status == "deleted"
    assert ops.destroy_calls == ["tree"]
    assert not run_path.exists()


def test_gc_removes_btrfs_run_interrupted_before_trees_directory(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    run_path = _dead_run(root, "btrfs-before-trees", area_root=root, filesystem="btrfs", remove_trees=True)

    result = gc(run_path.parent)
    assert result[0].status == "deleted"
    assert not run_path.exists()


def test_gc_retains_btrfs_run_with_symlinked_trees_path(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    sentinel = tmp_path / "external-trees"
    sentinel.mkdir()
    (sentinel / "keep").write_text("retain")
    run_path = _dead_run(
        root,
        "btrfs-symlink-trees",
        area_root=root,
        filesystem="btrfs",
        trees_target=sentinel,
    )

    result = gc(run_path.parent)
    assert result[0].status == "delete-failed"
    assert run_path.exists()
    assert (sentinel / "keep").read_text() == "retain"


def test_survey_lists_managed_runs_and_available_bytes(tmp_path):
    area = _area(tmp_path / "area")
    managed = open_run(
        "survey-managed", estimate=SpaceEstimate(0, 0, 0), environ={"COWTREE_MODE": "off"}, plain_dir=area.runs_dir
    )
    external = open_run(
        "survey-external",
        estimate=SpaceEstimate(0, 0, 0),
        holder="external",
        external_ref="vm:survey",
        environ={"COWTREE_MODE": "off"},
        plain_dir=area.runs_dir,
    )
    (Path(area.runs_dir) / "bad-entry").mkdir()
    result = survey(area)
    assert result["backend"] == "copy" and result["fs_type"] == "ext4"
    assert {row["state"] for row in result["runs"]} == {"managed", "external", "unknown"}
    assert result["available_bytes"] > 0
    managed.close()
    external.close()


def test_make_shared_serializes_and_never_overwrites(tmp_path):
    area = _area(tmp_path / "area")
    logs = []
    area = Area(area.root, area.caps, CopyBackend(log=logs.append), area.ops, area.private_dir, area.runs_dir, area.shared_dir)
    shared = make_shared(area, "template-cache", compression="zstd:3")
    assert Path(shared).is_dir()
    assert logs == ["cowtree: copy backend ignores per-tree compression 'zstd:3'"]
    with pytest.raises(PathRefused, match="already exists"):
        make_shared(area, "template-cache")
    with pytest.raises(PathRefused, match="name must"):
        make_shared(area, "../escape")
    with pytest.raises(PathRefused, match="compression"):
        make_shared(area, "bad-compression", compression="zstd:99")


def test_shared_lock_and_backend_failures_are_reported(tmp_path, monkeypatch):
    area = _area(tmp_path / "area")
    lock = Path(area.private_dir) / "shared.lock"
    lock.touch()
    real_fstat = gc_module.os.fstat

    def directory_stat(fd):
        return StatResultProxy(real_fstat(fd), st_mode=0o040700)

    monkeypatch.setattr(gc_module.os, "fstat", directory_stat)
    with pytest.raises(PathRefused, match="regular file"):
        make_shared(area, "cache")
    monkeypatch.setattr(gc_module.os, "fstat", real_fstat)

    lock.unlink()
    target = tmp_path / "shared-lock-target"
    target.touch()
    lock.symlink_to(target)
    with pytest.raises(BackendFailed, match="shared-lock"):
        make_shared(area, "cache")
    lock.unlink()

    real_flock = gc_module.fcntl.flock

    def fail_lock(fd, operation):
        if operation == fcntl.LOCK_EX:
            raise OSError(5, "shared flock failed")
        return real_flock(fd, operation)

    monkeypatch.setattr(gc_module.fcntl, "flock", fail_lock)
    with pytest.raises(BackendFailed, match="shared-lock"):
        make_shared(area, "cache")


def test_shared_creation_wraps_oserror_and_preserves_typed_failures(tmp_path, monkeypatch):
    area = _area(tmp_path / "area")

    def fail_oserror(_path, compression=None):
        raise OSError(5, "create failed")

    monkeypatch.setattr(area.backend, "make_tree", fail_oserror)
    with pytest.raises(BackendFailed, match="make-shared"):
        make_shared(area, "cache")

    def fail_typed(_path, compression=None):
        raise PathRefused("typed refusal")

    monkeypatch.setattr(area.backend, "make_tree", fail_typed)
    with pytest.raises(PathRefused, match="typed refusal"):
        make_shared(area, "cache")


def test_survey_skips_non_directory_entries_and_absent_runs(tmp_path, monkeypatch):
    area = _area(tmp_path / "area")
    (Path(area.runs_dir) / "plain-file").write_text("not a run")
    (Path(area.runs_dir) / "linked-run").symlink_to(Path(area.root), target_is_directory=True)
    report = survey(area)
    assert {item["name"]: item["state"] for item in report["runs"]} == {
        "linked-run": "unknown", "plain-file": "unknown"
    }
    monkeypatch.setattr(gc_module.os.path, "isdir", lambda _path: False)
    assert survey(area)["runs"] == []

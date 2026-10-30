from __future__ import annotations

import errno
import fcntl
import json
import multiprocessing
import os
import shutil
import stat
import threading
from pathlib import Path

import cowtree.run as run_module
import pytest
from cowtree import SpaceEstimate, open_run
from cowtree.backends import CopyBackend
from cowtree.detect import Area, Capabilities
from cowtree.errors import (
    BackendFailed,
    LeaseHeld,
    PathRefused,
    RootInvalid,
    SpaceRefused,
)

from .fakes import FakeOps

ESTIMATE = SpaceEstimate(16_000, 2_000, 1)


def _process_run(base, queue):
    run = open_run("proc", estimate=SpaceEstimate(0, 0, 0), environ={"COWTREE_MODE": "off"}, plain_dir=base)
    queue.put(run.run_id)
    run.close()


def _leave_run_for_gc(root, queue):
    run = open_run(
        "dead-owner",
        estimate=SpaceEstimate(0, 0, 0),
        environ={"MDT_COW_ROOT": str(root)},
        ops=FakeOps(filesystem="xfs"),
    )
    queue.put(run.path)


def test_plain_run_manifest_before_trees_and_close(tmp_path, monkeypatch):
    messages = []
    original_write_manifest = run_module._write_manifest
    manifest_was_written_before_trees = []

    def observe_write_manifest(run_dir, document, *, create):
        original_write_manifest(run_dir, document, create=create)
        manifest_was_written_before_trees.append(not (Path(run_dir) / "trees").exists())

    monkeypatch.setattr(run_module, "_write_manifest", observe_write_manifest)
    run = open_run(
        "test-tool",
        estimate=ESTIMATE,
        labels={"purpose": "unit"},
        environ={},
        plain_dir=tmp_path,
        log=messages.append,
    )
    manifest_path = Path(run.path) / "owner.json"
    assert manifest_was_written_before_trees == [True]
    assert (Path(run.path) / "trees").is_dir()
    document = json.loads(manifest_path.read_text())
    assert document["format"] == "cowtree-run/1"
    assert document["tool"] == "test-tool" and document["labels"] == {"purpose": "unit"}
    assert document["estimate"] == ESTIMATE.as_dict()
    assert set(document["owner"]) == {"uid", "pid", "pid_starttime", "boot_id", "pidns", "hostname"}
    with pytest.raises(PathRefused, match="already exists"):
        run_module._write_manifest(run.path, document, create=True)
    tree = run.make_tree("tree")
    assert manifest_path.is_file() and tree.is_dir()
    (tree / "keep").write_text("original")
    with pytest.raises(PathRefused, match="already exists"):
        run.make_tree("tree")
    assert (tree / "keep").read_text() == "original"
    run.close()
    assert not Path(run.path).exists()
    with pytest.raises(PathRefused, match="already closed"):
        run.make_tree("late")
    run.close()


def test_context_manager_closes_after_success(tmp_path):
    with open_run("context", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path) as run:
        path = Path(run.path)
        run.make_tree("tree")
    assert not path.exists()


def test_write_all_retries_partial_writes_and_rejects_zero(monkeypatch):
    writes = []

    def partial_write(_fd, view):
        chunk = bytes(view[:2])
        writes.append(chunk)
        return len(chunk)

    monkeypatch.setattr(run_module.os, "write", partial_write)
    run_module._write_all(1, b"abcde")
    assert writes == [b"ab", b"cd", b"e"]
    monkeypatch.setattr(run_module.os, "write", lambda _fd, _view: 0)
    with pytest.raises(OSError, match="short write"):
        run_module._write_all(1, b"x")


def test_unset_root_plain_logs_once_for_open_run(tmp_path):
    from cowtree.detect import _reset_info_for_tests

    _reset_info_for_tests()
    lines = []
    for _ in range(2):
        run = open_run("plain", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path, log=lines.append)
        run.close()
    assert lines == ["cowtree: MDT_COW_ROOT unset; using plain directories"]


def test_empty_set_root_refuses_without_plain_run_allocation(tmp_path):
    with pytest.raises(RootInvalid):
        open_run("invalid-root", estimate=SpaceEstimate(0, 0, 0), environ={"MDT_COW_ROOT": ""}, plain_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_run_ids_unique_threads_and_processes(tmp_path):
    ids = []

    def thread_run():
        run = open_run("thread", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
        ids.append(run.run_id)
        run.close()

    threads = [threading.Thread(target=thread_run) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_process_run, args=(str(tmp_path), queue)) for _ in range(2)]
    for process in processes:
        process.start()
    process_ids = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    combined = ids + process_ids
    assert len(combined) == 8 and len(set(combined)) == 8


def test_lease_held_until_close(tmp_path):
    run = open_run("lease", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    lock_path = Path(run.path) / "owner.lock"
    other = os.open(lock_path, os.O_RDWR)
    with pytest.raises(BlockingIOError):
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    run.close()
    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(other, fcntl.LOCK_UN)
    os.close(other)


def test_open_run_reports_dead_flock_runs_without_reaping(tmp_path):
    from cowtree.detect import _reset_info_for_tests

    _reset_info_for_tests()
    root = tmp_path / "area"
    root.mkdir()
    env = {"MDT_COW_ROOT": str(root)}
    ops = FakeOps(filesystem="xfs")
    unlocked_live = open_run("unlocked-live", estimate=SpaceEstimate(0, 0, 0), environ=env, ops=ops)
    fcntl.flock(unlocked_live._lock_fd, fcntl.LOCK_UN)
    os.close(unlocked_live._lock_fd)
    unlocked_live._closed = True
    messages = []
    observer = open_run("observer", estimate=SpaceEstimate(0, 0, 0), environ=env, ops=ops, log=messages.append)
    assert not any("dead flock-owned runs" in line for line in messages)
    observer.close()

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_leave_run_for_gc, args=(root, queue))
    process.start()
    dead_path = Path(queue.get(timeout=20))
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join()
    assert process.exitcode == 0
    lines = []
    live = open_run("next-owner", estimate=SpaceEstimate(0, 0, 0), environ=env, ops=ops, log=lines.append)
    assert any("dead flock-owned runs exist" in line for line in lines)
    assert dead_path.is_dir()
    live.close()


def test_new_run_lock_collision_raises_lease_held(tmp_path, monkeypatch):
    monkeypatch.setattr(run_module.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(BlockingIOError()))
    with pytest.raises(LeaseHeld):
        open_run("lock-collision", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_owner_identity_and_pid_starttime_parser():
    fields = ["S"] + ["0"] * 19
    fields[19] = "1234"
    assert run_module._pid_starttime.__module__ == "cowtree.run"
    from pathlib import Path
    original = Path.read_text

    def fake_read_text(path, *args, **kwargs):
        if str(path) == "/proc/123/stat":
            return "123 (worker ) with paren) " + " ".join(fields)
        return original(path, *args, **kwargs)

    # Use the parser against a synthetic comm field containing closing parentheses.
    from unittest.mock import patch
    with patch.object(run_module.Path, "read_text", fake_read_text):
        assert run_module._pid_starttime(123) == 1234
        identity = run_module._owner_identity()
    assert identity["uid"] == os.geteuid() and identity["pid_starttime"] >= 0


def test_pid_starttime_malformed_stat_refuses(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "malformed")
    with pytest.raises(OSError, match="malformed"):
        run_module._pid_starttime(1)

    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "1 (worker) S short")
    with pytest.raises(OSError, match="starttime"):
        run_module._pid_starttime(1)


def test_owner_identity_failure_cleans_new_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "_owner_identity", lambda: (_ for _ in ()).throw(BackendFailed("owner", 5)))
    with pytest.raises(BackendFailed, match="owner"):
        open_run("bad-owner", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_manifest_write_failure_cleans_new_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "_write_all", lambda *_args: (_ for _ in ()).throw(OSError(5, "disk error")))
    with pytest.raises(BackendFailed, match="write-manifest"):
        open_run("bad-manifest", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_ingest_clone_symlink_mode_mtime_and_file_metadata(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    payload = source / "payload"
    payload.write_text("hello")
    os.chmod(payload, 0o640)
    os.utime(payload, ns=(1_600_000_000_000_000_000, 1_600_000_001_000_000_000))
    outside = tmp_path / "outside-secret"
    outside.write_text("do not follow")
    link = source / "absolute-link"
    link.symlink_to(outside)
    os.utime(link, ns=(1_600_000_002_000_000_000, 1_600_000_003_000_000_000), follow_symlinks=False)
    run = open_run("identity", estimate=ESTIMATE, environ={}, plain_dir=tmp_path)
    imported = run.ingest_tree(source, "template")
    run.seal(imported)
    cloned = run.clone_tree(imported, "clone")
    cloned_file = cloned / "payload"
    cloned_link = cloned / "absolute-link"
    assert cloned_file.read_text() == "hello"
    assert stat.S_IMODE(cloned_file.stat().st_mode) == 0o640
    assert cloned_file.stat().st_mtime_ns == payload.stat().st_mtime_ns
    assert cloned_link.is_symlink() and os.readlink(cloned_link) == str(outside)
    assert stat.S_IMODE(os.lstat(cloned_link).st_mode) == stat.S_IMODE(os.lstat(link).st_mode)
    assert os.lstat(cloned_link).st_mtime_ns == os.lstat(link).st_mtime_ns
    run.close()


def test_special_files_refused_and_partial_tree_removed(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    os.mkfifo(source / "pipe")
    run = open_run("special", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="special file"):
        run.ingest_tree(source, "bad")
    assert not (Path(run.path) / "trees" / "bad").exists()
    run.close()


def test_ingest_tree_rejects_a_source_that_contains_its_destination(tmp_path):
    run = open_run("recursive-ingest", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    trees = Path(run.path) / "trees"
    with pytest.raises(PathRefused, match="inside its source"):
        run.ingest_tree(trees, "recursive")
    assert not (trees / "recursive").exists()
    run.close()


def test_backend_failure_does_not_delete_a_competing_destination(tmp_path, monkeypatch):
    run = open_run("create-race", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    destination = Path(run.path) / "trees" / "raced"

    def other_writer_then_backend_error(_source, path):
        Path(path).write_text("other writer")
        raise BackendFailed("stat-ingest-file", 2, str(path), "source disappeared")

    monkeypatch.setattr(run._backend, "ingest_file", other_writer_then_backend_error)
    with pytest.raises(BackendFailed, match="source disappeared"):
        run.ingest_file(tmp_path / "missing-source", "raced")
    assert destination.read_text() == "other writer"
    destination.unlink()
    run.close()


def test_run_does_not_adopt_a_path_replaced_after_backend_creation(tmp_path, monkeypatch):
    run = open_run("replace-after-create", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    destination = Path(run.path) / "trees" / "raced-file"
    created_by_backend = destination.with_name("backend-created-file")
    original_ingest = run._backend.ingest_file

    def create_then_replace(source, path):
        original_ingest(source, path)
        os.rename(path, created_by_backend)
        Path(path).write_text("other writer")

    monkeypatch.setattr(run._backend, "ingest_file", create_then_replace)
    source = tmp_path / "incoming"
    source.write_text("cowtree")
    with pytest.raises(PathRefused, match="identity changed during creation"):
        run.ingest_file(source, "raced-file")

    assert destination.read_text() == "other writer"
    created_by_backend.unlink()
    destination.unlink()
    run.close()


def test_nested_clone_file_checks_its_receipt_before_adopting_parent(tmp_path, monkeypatch):
    run = open_run("nested-receipt", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source_path = tmp_path / "input"
    source_path.write_text("source")
    source = run.ingest_file(source_path, "source")
    trees = Path(run.path) / "trees"
    unowned_parent = trees / "unowned"
    unowned_parent.mkdir()
    destination = unowned_parent / "candidate"
    created_by_backend = unowned_parent / "created-by-backend"
    original_clone = run._backend.clone_file

    def clone_then_replace(source_arg, destination_arg):
        original_clone(source_arg, destination_arg)
        os.rename(destination_arg, created_by_backend)
        Path(destination_arg).write_text("other writer")

    monkeypatch.setattr(run._backend, "clone_file", clone_then_replace)
    with pytest.raises(PathRefused, match="identity changed during creation"):
        run.clone_file(source, destination)

    assert destination.read_text() == "other writer"
    created_by_backend.unlink()
    destination.unlink()
    unowned_parent.rmdir()
    run.close()


def test_clone_source_outside_run_refused(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    run = open_run("paths", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="inside this run"):
        run.clone_tree(external, "clone")
    with pytest.raises(PathRefused, match="inside this run"):
        run.clone_tree(Path(run.path) / "trees", "clone")
    with pytest.raises(PathRefused, match="name must match"):
        run.make_tree("../escape")
    run.close()


def test_ingest_file_clone_file_and_delete_sealed_tree(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.write_bytes(b"file")
    run = open_run("files", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    ingested = run.ingest_file(incoming, "source")
    tree = run.make_tree("tree")
    nested = tree / "child"
    nested.write_text("content")
    run.clone_file(ingested, nested.parent / "copy")
    run.clone_file(ingested, Path(run.path) / "trees" / "top-level-copy")
    assert (tree / "copy").read_bytes() == b"file"
    assert (Path(run.path) / "trees" / "top-level-copy").read_bytes() == b"file"
    run.seal(tree)
    assert stat.S_IMODE(os.lstat(tree).st_mode) & 0o222 == 0
    run.delete_tree(tree)
    assert not tree.exists()
    with pytest.raises(PathRefused, match="not owned"):
        run.delete_tree(tmp_path / "incoming")
    run.close()


def test_clone_file_adopts_nested_destination_tree_for_cleanup(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.write_bytes(b"file")
    run = open_run("adopt-file-tree", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source = run.ingest_file(incoming, "source")
    tree = Path(run.path) / "trees" / "caller-tree"
    tree.mkdir()

    run.clone_file(source, tree / "copy")
    assert (tree / "copy").read_bytes() == b"file"
    run.close()
    assert not Path(run.path).exists()


def test_clone_file_rejects_outside_and_symlink_sources(tmp_path):
    source = tmp_path / "external"
    source.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(source)
    run = open_run("clone-file", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="inside this run"):
        run.clone_file(source, Path(run.path) / "trees" / "copy")
    ingested = run.ingest_file(source, "source")
    tree = run.make_tree("destination")
    occupied = tree / "occupied"
    occupied.write_text("preserve")
    with pytest.raises(PathRefused, match="already exists"):
        run.clone_file(ingested, occupied)
    assert occupied.read_text() == "preserve"
    with pytest.raises(PathRefused, match="inside this run"):
        run.clone_file(link, Path(run.path) / "trees" / "other")
    with pytest.raises(PathRefused, match="parent"):
        run.clone_file(ingested, Path(run.path) / "trees" / "missing" / "escape")
    with pytest.raises(PathRefused, match="inside this run"):
        run.clone_file(ingested, tmp_path / "outside-destination")
    run.close()


def test_clone_file_rejects_symlinked_destination_ancestor(tmp_path):
    run = open_run("clone-file-link-parent", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source_tree = run.make_tree("source-tree")
    nested = source_tree / "nested"
    nested.mkdir()
    source_file = nested / "source"
    source_file.write_text("payload")
    destination_tree = run.make_tree("destination-tree")
    (destination_tree / "alias").symlink_to(source_tree, target_is_directory=True)

    destination = destination_tree / "alias" / "nested" / "copy"
    with pytest.raises(PathRefused, match="symlink or non-directory"):
        run.clone_file(source_file, destination)
    assert not (nested / "copy").exists()
    run.close()


@pytest.mark.parametrize("replacement", ["symlink", "directory", "missing"])
def test_run_refuses_replaced_trees_directory_before_writing(tmp_path, replacement):
    run = open_run("replaced-trees", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source = tmp_path / "incoming"
    source.write_text("payload")
    outside = tmp_path / "outside"
    outside.mkdir()
    trees = Path(run.path) / "trees"
    saved_trees = Path(run.path) / "trees.saved"
    trees.rename(saved_trees)
    if replacement == "symlink":
        trees.symlink_to(outside, target_is_directory=True)
        expected_error = "real directory"
    elif replacement == "directory":
        trees.mkdir()
        expected_error = "identity changed"
    else:
        expected_error = "unavailable"

    try:
        with pytest.raises(PathRefused, match=expected_error):
            run.ingest_file(source, "escaped")
        if replacement != "missing":
            assert list((outside if replacement == "symlink" else trees).iterdir()) == []
    finally:
        if replacement == "symlink":
            trees.unlink()
        elif replacement == "directory":
            trees.rmdir()
        saved_trees.rename(trees)
    run.close()


def test_run_refuses_replaced_run_directory(tmp_path):
    run = open_run("replaced-run", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    run_dir = Path(run.path)
    saved_run = tmp_path / "run.saved"
    run_dir.rename(saved_run)
    run_dir.mkdir()
    (run_dir / "trees").mkdir()

    try:
        with pytest.raises(PathRefused, match="identity changed"):
            run.make_tree("escaped")
        assert list((run_dir / "trees").iterdir()) == []
    finally:
        (run_dir / "trees").rmdir()
        run_dir.rmdir()
        saved_run.rename(run_dir)
    run.close()


def test_close_refuses_replaced_directory_without_deleting_replacement(tmp_path):
    run = open_run("close-replaced-run", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    run.make_tree("tree")
    run_dir = Path(run.path)
    saved_run = tmp_path / "run.saved"
    run_dir.rename(saved_run)
    run_dir.mkdir()
    replacement_trees = run_dir / "trees"
    replacement_trees.mkdir()
    sentinel = replacement_trees / "keep"
    sentinel.write_text("unowned replacement")

    with pytest.raises(PathRefused, match="identity changed"):
        run.close()
    assert sentinel.read_text() == "unowned replacement"
    run_dir.rename(tmp_path / "replacement-run")
    saved_run.rename(run_dir)
    shutil.rmtree(run_dir)


def test_owned_tree_replacement_is_refused(tmp_path):
    run = open_run("replace-owned-tree", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    tree = run.make_tree("tree")
    saved_tree = tmp_path / "tree.saved"
    tree.rename(saved_tree)
    tree.mkdir()

    with pytest.raises(PathRefused, match="run-owned path identity changed"):
        run.delete_tree(tree)
    tree.rmdir()
    saved_tree.rename(tree)
    run.close()


def test_close_refuses_replaced_owned_tree_without_deleting_replacement(tmp_path):
    run = open_run("close-replaced-tree", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    tree = run.make_tree("tree")
    saved_tree = tmp_path / "tree.saved"
    tree.rename(saved_tree)
    tree.mkdir()
    sentinel = tree / "keep"
    sentinel.write_text("unowned replacement")

    with pytest.raises(PathRefused, match="run-owned path identity changed"):
        run.close()
    assert sentinel.read_text() == "unowned replacement"
    tree.rename(tmp_path / "replacement-tree")
    saved_tree.rename(tree)
    shutil.rmtree(run.path)


def test_clone_file_refuses_disappeared_destination_component(tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    incoming.write_text("payload")
    run = open_run("disappearing-component", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source = run.ingest_file(incoming, "source")
    tree = run.make_tree("tree")
    nested = tree / "nested"
    nested.mkdir()
    original_lstat = os.lstat

    def disappearing(path, *args, **kwargs):
        if os.fspath(path) == str(nested):
            raise FileNotFoundError("component disappeared")
        return original_lstat(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(run_module.os, "lstat", disappearing)
        with pytest.raises(PathRefused, match="destination path is unavailable"):
            run.clone_file(source, nested / "copy")
    run.close()


def test_reserve_rechecks_before_growth(tmp_path, monkeypatch):
    run = open_run("reserve", estimate=SpaceEstimate(10, 10, 1), environ={}, plain_dir=tmp_path)
    monkeypatch.setattr(run_module, "available_bytes", lambda _path: 1)
    with pytest.raises(SpaceRefused) as caught:
        run.reserve(SpaceEstimate(0, 10, 1))
    assert caught.value.needed_bytes == 40
    assert caught.value.available_bytes == 1
    monkeypatch.setattr(run_module, "available_bytes", lambda _path: 10_000)
    run.reserve(SpaceEstimate(0, 10, 1))
    document = json.loads((Path(run.path) / "owner.json").read_text())
    assert document["reserved_bytes"] == 40
    run.close()


def test_reserve_commits_memory_only_after_manifest_write_succeeds(tmp_path, monkeypatch):
    run = open_run("reserve-manifest-cap", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    monkeypatch.setattr(run_module, "available_bytes", lambda _path: 10**9)
    run.reserve(SpaceEstimate(9, 0, 0))

    manifest_at_cap = {**run._manifest, "labels": {"padding": ""}}
    padding_bytes = run_module._MAX_OWNER_MANIFEST_BYTES - len(run_module._json_bytes(manifest_at_cap))
    run._manifest["labels"] = {"padding": "x" * padding_bytes}
    run_module._write_manifest(run.path, run._manifest, create=False)
    owner_path = Path(run.path) / "owner.json"
    assert len(owner_path.read_bytes()) == run_module._MAX_OWNER_MANIFEST_BYTES

    with pytest.raises(PathRefused, match="owner manifest exceeds 64 KiB"):
        run.reserve(SpaceEstimate(1, 0, 0))
    assert run._reserved_bytes == 9
    assert run._manifest["reserved_bytes"] == 9
    assert json.loads(owner_path.read_text())["reserved_bytes"] == 9

    run.reserve(SpaceEstimate(0, 0, 0))
    assert json.loads(owner_path.read_text())["reserved_bytes"] == 9
    run.close()


def test_pending_delete_allowance_refuses_before_run_allocation(tmp_path, monkeypatch):
    from cowtree import run as run_module_import

    root = tmp_path / "area"
    root.mkdir()
    (root / ".cowtree").mkdir(mode=0o700)
    (root / ".cowtree" / "runs").mkdir(mode=0o700)
    (root / "shared").mkdir(mode=0o700)
    area = Area(
        str(root),
        Capabilities("btrfs", "btrfs", True, True, True, True, True),
        CopyBackend(),
        FakeOps(),
        str(root / ".cowtree"),
        str(root / ".cowtree" / "runs"),
        str(root / "shared"),
    )
    monkeypatch.setattr(run_module_import, "detect", lambda **_kwargs: area)
    monkeypatch.setattr(run_module_import, "pending_delete_bytes", lambda _dev: 10**30)
    with pytest.raises(SpaceRefused):
        run_module_import.open_run("pending", estimate=SpaceEstimate(0, 0, 0), environ={})
    assert list(Path(area.runs_dir).iterdir()) == []


def test_btrfs_pending_delete_uses_the_admission_filesystem(tmp_path, monkeypatch):
    import cowtree.gc as gc_module
    from cowtree.space import filesystem_identity, pending_delete_bytes

    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    runs = private / "runs"
    shared = root / "shared"
    for path in (private, runs, shared):
        path.mkdir(mode=0o700)
    area = Area(
        str(root),
        Capabilities("btrfs", "btrfs", True, True, True, True, True),
        CopyBackend(),
        FakeOps(),
        str(private),
        str(runs),
        str(shared),
    )
    monkeypatch.setattr(run_module, "detect", lambda **_kwargs: area)
    root_filesystem = filesystem_identity(str(root), backend="btrfs", ops=area.ops)
    run_filesystem = filesystem_identity(str(runs), backend="btrfs", ops=area.ops)
    assert root_filesystem == run_filesystem
    root_pending_before = pending_delete_bytes(root_filesystem)
    monkeypatch.setattr(gc_module, "_allocated_bytes", lambda _path: (8192, 999_999))
    run = open_run("pending-device", estimate=SpaceEstimate(0, 0, 0), environ={})

    assert run._pending_filesystem == run_filesystem
    run.reserve(SpaceEstimate(0, 0, 0))
    before = pending_delete_bytes(run_filesystem)
    run.delete_tree(run.make_tree("deleted"))
    assert pending_delete_bytes(run_filesystem) >= before + 8192

    before = pending_delete_bytes(run_filesystem)
    run.make_tree("closed")
    run.close()
    assert pending_delete_bytes(run_filesystem) >= before + 8192
    assert pending_delete_bytes(root_filesystem) >= root_pending_before + 16_384


def test_open_run_validates_holder_labels_and_plain_dir(tmp_path):
    with pytest.raises(PathRefused, match="external_ref"):
        open_run("external", estimate=SpaceEstimate(0, 0, 0), holder="external", environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="only valid"):
        open_run("ordinary", estimate=SpaceEstimate(0, 0, 0), external_ref="x", environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="holder"):
        open_run("ordinary", estimate=SpaceEstimate(0, 0, 0), holder="other", environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="serializable"):
        open_run("ordinary", estimate=SpaceEstimate(0, 0, 0), labels={"bad": object()}, environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="serializable"):
        open_run("ordinary", estimate=SpaceEstimate(0, 0, 0), labels={"bad": float("nan")}, environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="filename prefix"):
        open_run("ordinary", estimate=SpaceEstimate(0, 0, 0), plain_prefix="../bad", environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="name must"):
        open_run("../bad", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    with pytest.raises(RootInvalid, match="real directory"):
        open_run("ordinary", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path / "missing")


def test_open_run_refuses_owner_manifest_larger_than_gc_read_limit(tmp_path):
    with pytest.raises(PathRefused, match="64 KiB"):
        open_run(
            "large-labels",
            estimate=SpaceEstimate(0, 0, 0),
            labels={"payload": "x" * (64 * 1024)},
            environ={},
            plain_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_external_manifest_and_close(tmp_path):
    run = open_run(
        "vmctl",
        estimate=SpaceEstimate(0, 0, 0),
        holder="external",
        external_ref="vm-run:one",
        environ={},
        plain_dir=tmp_path,
    )
    document = json.loads((Path(run.path) / "owner.json").read_text())
    assert document["holder"] == "external" and document["external_ref"] == "vm-run:one"
    run.close()


def test_close_raise_vs_best_effort(tmp_path, monkeypatch):
    normal = open_run("normal", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)

    def fail(_path):
        raise OSError(5, "cannot delete")

    monkeypatch.setattr(normal._backend, "delete_tree", fail)
    normal.make_tree("tree")
    with pytest.raises(BackendFailed, match="close"):
        normal.close()
    assert Path(normal.path).is_dir()

    best_effort = open_run("exception", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    best_effort.make_tree("tree")
    monkeypatch.setattr(best_effort._backend, "delete_tree", fail)
    assert best_effort.__exit__(RuntimeError, RuntimeError("boom"), None) is False
    assert Path(best_effort.path).is_dir()

    typed = open_run("typed-close", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    typed.make_tree("tree")
    monkeypatch.setattr(typed._backend, "delete_tree", lambda _path: (_ for _ in ()).throw(PathRefused("unsafe")))
    with pytest.raises(PathRefused, match="unsafe"):
        typed.close()


def test_ingest_cleanup_error_preserves_original_backend_failure(tmp_path, monkeypatch):
    run = open_run("failed-ingest", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "f").write_text("x")
    original_ingest = run._backend.ingest_tree

    def partial_then_fail(src, dst):
        original_ingest(src, dst)
        raise BackendFailed("ingest", 5, dst)

    monkeypatch.setattr(run._backend, "ingest_tree", partial_then_fail)
    with pytest.raises(BackendFailed, match="ingest"):
        run.ingest_tree(source, "bad")
    assert not (Path(run.path) / "trees" / "bad").exists()
    run.close()


def test_failed_run_adoption_preserves_unowned_child_inside_created_tree(tmp_path, monkeypatch):
    run = open_run("preserve-child", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "owned-file").write_text("cowtree")
    destination = Path(run.path) / "trees" / "partial"
    foreign = destination / "foreign"
    original_ingest = run._backend.ingest_tree

    def copy_then_foreign_child(src, dst):
        original_ingest(src, dst)
        foreign.write_text("other writer")
        raise BackendFailed("post-create", 5, dst)

    monkeypatch.setattr(run._backend, "ingest_tree", copy_then_foreign_child)
    with pytest.raises(BackendFailed, match="post-create"):
        run.ingest_tree(source, "partial")

    assert foreign.read_text() == "other writer"
    assert not (destination / "owned-file").exists()
    foreign.unlink()
    destination.rmdir()
    run.close()


def test_partial_cleanup_failure_is_logged_without_masking_original(tmp_path, monkeypatch):
    messages = []
    run = open_run("cleanup-log", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path, log=messages.append)
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("payload")
    original_ingest = run._backend.ingest_tree
    original_delete = run._backend.delete_tree
    original_delete_empty = run._backend._delete_empty_created_tree

    def partial_then_fail(src, dst):
        original_ingest(src, dst)
        raise BackendFailed("primary-ingest", 5, dst)

    def cleanup_fails(_path):
        raise OSError(5, "cleanup unavailable")

    monkeypatch.setattr(run._backend, "ingest_tree", partial_then_fail)
    monkeypatch.setattr(run._backend, "_delete_empty_created_tree", cleanup_fails)
    with pytest.raises(BackendFailed, match="primary-ingest"):
        run.ingest_tree(source, "partial")
    assert any("cleanup of partial path" in message for message in messages)
    monkeypatch.setattr(run._backend, "delete_tree", original_delete)
    monkeypatch.setattr(run._backend, "_delete_empty_created_tree", original_delete_empty)
    original_delete(Path(run.path) / "trees" / "partial")
    run.close()


def test_close_keeps_first_error_across_multiple_tree_delete_failures(tmp_path, monkeypatch):
    run = open_run("multiple-delete-errors", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    run.make_tree("first")
    run.make_tree("second")

    def fail_delete(path):
        raise OSError(errno.EIO, f"cannot delete {path}")

    monkeypatch.setattr(run._backend, "delete_tree", fail_delete)
    with pytest.raises(BackendFailed, match="cannot delete"):
        run.close()
    assert Path(run.path).is_dir()
    shutil.rmtree(run.path)


def test_close_handles_missing_manifest_and_owner_lock_unlink_error(tmp_path, monkeypatch):
    run = open_run("missing-manifest-close", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    (path / "owner.json").unlink()
    real_unlink = run_module.os.unlink

    def fail_lock_unlink(value, *args, **kwargs):
        if os.fspath(value) == str(path / "owner.lock"):
            raise PermissionError(errno.EACCES, "owner lock unlink denied")
        return real_unlink(value, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "unlink", fail_lock_unlink)
    with pytest.raises(BackendFailed, match="owner lock unlink denied"):
        run.close()
    assert path.is_dir() and (path / "owner.lock").exists()
    monkeypatch.setattr(run_module.os, "unlink", real_unlink)
    shutil.rmtree(path)


def test_close_retains_first_unlink_error_when_unlock_and_close_also_fail(tmp_path, monkeypatch):
    run = open_run("multiple-close-errors", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    lock_fd = run._lock_fd
    real_unlink = run_module.os.unlink
    real_flock = run_module.fcntl.flock
    real_close = run_module.os.close

    def fail_owner_unlink(value, *args, **kwargs):
        if os.path.basename(os.fspath(value)) in {"owner.json", "owner.lock"}:
            raise PermissionError(errno.EACCES, f"unlink denied for {value}")
        return real_unlink(value, *args, **kwargs)

    def fail_unlock(fd, operation):
        if fd == lock_fd and operation == fcntl.LOCK_UN:
            raise OSError(errno.EIO, "unlock failed")
        return real_flock(fd, operation)

    def fail_close(fd):
        if fd == lock_fd:
            raise OSError(errno.EIO, "close failed")
        return real_close(fd)

    monkeypatch.setattr(run_module.os, "unlink", fail_owner_unlink)
    monkeypatch.setattr(run_module.fcntl, "flock", fail_unlock)
    monkeypatch.setattr(run_module.os, "close", fail_close)
    with pytest.raises(BackendFailed, match="unlink denied"):
        run.close()
    assert path.is_dir()
    monkeypatch.setattr(run_module.os, "close", real_close)
    real_close(lock_fd)
    monkeypatch.setattr(run_module.os, "unlink", real_unlink)
    shutil.rmtree(path)


def test_close_reports_run_directory_removal_error(tmp_path, monkeypatch):
    run = open_run("run-dir-close-error", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    real_rmdir = run_module.os.rmdir

    def fail_run_rmdir(value, *args, **kwargs):
        if os.fspath(value) == str(path):
            raise PermissionError(errno.EACCES, "run directory removal denied")
        return real_rmdir(value, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "rmdir", fail_run_rmdir)
    with pytest.raises(BackendFailed, match="run directory removal denied"):
        run.close()
    assert path.is_dir()
    monkeypatch.setattr(run_module.os, "rmdir", real_rmdir)
    path.rmdir()


def test_close_reports_unlock_error_without_an_earlier_failure(tmp_path, monkeypatch):
    run = open_run("unlock-close-error", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    lock_fd = run._lock_fd
    real_flock = run_module.fcntl.flock

    def fail_unlock(fd, operation):
        if fd == lock_fd and operation == fcntl.LOCK_UN:
            raise OSError(errno.EIO, "unlock failed")
        return real_flock(fd, operation)

    monkeypatch.setattr(run_module.fcntl, "flock", fail_unlock)
    with pytest.raises(BackendFailed, match="unlock failed"):
        run.close()
    assert not path.exists()


def test_close_reports_lock_descriptor_close_error(tmp_path, monkeypatch):
    run = open_run("lock-close-error", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    lock_fd = run._lock_fd
    real_close = run_module.os.close

    def fail_lock_close(fd):
        if fd == lock_fd:
            raise OSError(errno.EIO, "descriptor close failed")
        return real_close(fd)

    monkeypatch.setattr(run_module.os, "close", fail_lock_close)
    with pytest.raises(BackendFailed, match="descriptor close failed"):
        run.close()
    assert not path.exists()
    monkeypatch.setattr(run_module.os, "close", real_close)
    real_close(lock_fd)

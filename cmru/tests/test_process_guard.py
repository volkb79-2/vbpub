"""The test process guard must expose leaks, even when it can clean them up."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from cmru.process_guard import (
    DescendantGuard,
    DescendantLeakError,
    ProcessRecord,
    ProcessTreeUnavailable,
    descendants,
)


def _sleeping_child() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _fake_proc_root(tmp_path: Path) -> tuple[Path, int, Path]:
    root_pid = os.getpid()
    proc_root = tmp_path / "proc"
    children_path = proc_root / str(root_pid) / "task" / str(root_pid) / "children"
    children_path.parent.mkdir(parents=True)
    children_path.write_text("", encoding="ascii")
    return proc_root, root_pid, children_path


def _write_stat(proc_root: Path, pid: int, *, ppid: int = 1, start_time: int = 10) -> None:
    fields = ["S", str(ppid), *(["0"] * 17), str(start_time)]
    stat_path = proc_root / str(pid) / "stat"
    stat_path.parent.mkdir(parents=True, exist_ok=True)
    stat_path.write_text(f"{pid} (probe child) {' '.join(fields)}\n", encoding="ascii")


def _write_child(proc_root: Path, pid: int, *, ppid: int = 1, start_time: int = 10) -> None:
    _write_stat(proc_root, pid, ppid=ppid, start_time=start_time)
    children_path = proc_root / str(pid) / "task" / str(pid) / "children"
    children_path.parent.mkdir(parents=True)
    children_path.write_text("", encoding="ascii")


def test_guard_fails_after_killing_a_new_descendant():
    guard = DescendantGuard(root_pid=os.getpid(), grace_period=0.5)
    child = _sleeping_child()
    try:
        with pytest.raises(DescendantLeakError, match="new process descendant") as raised:
            guard.check()
        assert str(child.pid) in str(raised.value)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_guard_reports_a_descendant_that_survives_its_kill_attempt(monkeypatch):
    guard = DescendantGuard(
        root_pid=os.getpid(), grace_period=0.03, poll_interval=0.005,
    )
    child = _sleeping_child()
    monkeypatch.setattr(guard, "_kill", lambda _pid: None)
    try:
        with pytest.raises(DescendantLeakError, match="survivor") as raised:
            guard.check()
        assert str(child.pid) in str(raised.value)
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_descendants_include_children_started_by_worker_threads():
    guard = DescendantGuard(root_pid=os.getpid())
    spawned: queue.Queue[subprocess.Popen] = queue.Queue()
    release_worker = threading.Event()

    def start_child_from_worker():
        child = _sleeping_child()
        spawned.put(child)
        release_worker.wait(timeout=5)
        if child.poll() is None:
            child.terminate()
        child.wait()

    worker = threading.Thread(target=start_child_from_worker)
    worker.start()
    child = spawned.get(timeout=5)
    try:
        assert child.pid in guard.snapshot()
    finally:
        release_worker.set()
        worker.join(timeout=5)
        if worker.is_alive():
            child.kill()
            worker.join(timeout=5)
        assert not worker.is_alive()


def test_guard_fails_loudly_when_proc_children_are_unavailable(tmp_path):
    with pytest.raises(ProcessTreeUnavailable, match="children"):
        DescendantGuard(root_pid=os.getpid(), proc_root=tmp_path)


def test_proc_tree_api_is_importable_and_explicitly_unsupported_off_linux(monkeypatch):
    from cmru import process_guard

    monkeypatch.setattr(process_guard.platform, "system", lambda: "Darwin")
    with pytest.raises(ProcessTreeUnavailable, match="Linux"):
        descendants(os.getpid())


def test_test_session_git_environment_disables_automatic_maintenance():
    count = int(os.environ["GIT_CONFIG_COUNT"])
    effective = {
        os.environ[f"GIT_CONFIG_KEY_{index}"]: os.environ[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(count)
    }
    assert effective["maintenance.auto"] == "false"
    assert effective["maintenance.autoDetach"] == "false"
    assert effective["gc.auto"] == "0"
    assert effective["gc.autoDetach"] == "false"


def test_process_tree_refuses_missing_or_malformed_children_files(tmp_path):
    proc_root, root_pid, children_path = _fake_proc_root(tmp_path)
    children_path.unlink()
    with pytest.raises(ProcessTreeUnavailable, match="children support is unavailable"):
        descendants(root_pid, proc_root=proc_root)

    children_path.write_text("child-id", encoding="ascii")
    with pytest.raises(ProcessTreeUnavailable, match="malformed.*children"):
        descendants(root_pid, proc_root=proc_root)


def test_process_tree_wraps_proc_permission_errors(monkeypatch, tmp_path):
    from cmru import process_guard

    proc_root, root_pid, children_path = _fake_proc_root(tmp_path)
    original_read_text = Path.read_text

    def denied(path, *args, **kwargs):
        if path == children_path:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(ProcessTreeUnavailable, match="cannot read Linux /proc children"):
        process_guard.descendants(root_pid, proc_root=proc_root)


def test_process_tree_ignores_a_child_that_exits_before_its_stat_is_read(tmp_path):
    proc_root, root_pid, children_path = _fake_proc_root(tmp_path)
    children_path.write_text("99999999\n", encoding="ascii")
    assert descendants(root_pid, proc_root=proc_root) == {}


def test_task_enumeration_reports_malformed_or_unavailable_proc_entries(tmp_path):
    from cmru import process_guard

    proc_root, root_pid, _children_path = _fake_proc_root(tmp_path)
    assert process_guard._task_ids(root_pid + 100000, proc_root, root_pid=root_pid) == []

    task_dir = proc_root / str(root_pid) / "task"
    malformed = task_dir / "not-a-thread-id"
    malformed.mkdir()
    with pytest.raises(ProcessTreeUnavailable, match="malformed task entry"):
        process_guard._task_ids(root_pid, proc_root, root_pid=root_pid)


def test_task_enumeration_wraps_permission_errors(monkeypatch, tmp_path):
    from cmru import process_guard

    proc_root, root_pid, _children_path = _fake_proc_root(tmp_path)
    original_iterdir = Path.iterdir

    def denied(path):
        if path == proc_root / str(root_pid) / "task":
            raise PermissionError("denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(ProcessTreeUnavailable, match="cannot enumerate Linux /proc task"):
        process_guard._task_ids(root_pid, proc_root, root_pid=root_pid)


def test_task_enumeration_refuses_an_existing_empty_task_directory(tmp_path):
    from cmru import process_guard

    proc_root, root_pid, children_path = _fake_proc_root(tmp_path)
    children_path.unlink()
    children_path.parent.rmdir()
    with pytest.raises(ProcessTreeUnavailable, match="no readable children files"):
        process_guard._task_ids(root_pid, proc_root, root_pid=root_pid)


@pytest.mark.parametrize("stat_text, message", [
    ("not a stat record", "malformed Linux process stat"),
    ("123 (bad) S not-a-ppid 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0", "malformed Linux process identity"),
])
def test_process_record_rejects_malformed_stat_data(tmp_path, stat_text, message):

    proc_root, root_pid, children_path = _fake_proc_root(tmp_path)
    child_pid = root_pid + 100000
    children_path.write_text(f"{child_pid}\n", encoding="ascii")
    stat_path = proc_root / str(child_pid) / "stat"
    stat_path.parent.mkdir(parents=True)
    stat_path.write_text(stat_text, encoding="ascii")
    with pytest.raises(ProcessTreeUnavailable, match=message):
        descendants(root_pid, proc_root=proc_root)


def test_process_record_wraps_permission_errors(monkeypatch, tmp_path):
    from cmru import process_guard

    proc_root, root_pid, children_path = _fake_proc_root(tmp_path)
    child_pid = root_pid + 100001
    children_path.write_text(f"{child_pid}\n", encoding="ascii")
    _write_child(proc_root, child_pid)
    original_read_text = Path.read_text

    def denied(path, *args, **kwargs):
        if path == proc_root / str(child_pid) / "stat":
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(ProcessTreeUnavailable, match="cannot read Linux process stat"):
        process_guard.descendants(root_pid, proc_root=proc_root)


def test_process_tree_walks_each_thread_and_deduplicates_children(tmp_path):
    proc_root, root_pid, children_path = _fake_proc_root(tmp_path)
    child_pid = root_pid + 100002
    _write_child(proc_root, child_pid)
    children_path.write_text(f"{child_pid}\n", encoding="ascii")
    other_tid = root_pid + 1
    other_children = proc_root / str(root_pid) / "task" / str(other_tid) / "children"
    other_children.parent.mkdir(parents=True)
    other_children.write_text(f"{child_pid}\n", encoding="ascii")

    records = descendants(root_pid, proc_root=proc_root)

    assert list(records) == [child_pid]
    assert records[child_pid].start_time == 10


def test_process_tree_wraps_proc_read_errors_and_ignores_gone_task_directories(
    monkeypatch, tmp_path,
):
    from cmru import process_guard

    proc_root, root_pid, children_path = _fake_proc_root(tmp_path)
    children_path.write_text("", encoding="ascii")
    original_iterdir = Path.iterdir

    def disappeared(path):
        if path == proc_root / str(root_pid + 100003) / "task":
            raise FileNotFoundError(path)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", disappeared)
    assert process_guard._task_ids(root_pid + 100003, proc_root, root_pid=root_pid) == []


def test_guard_identity_change_and_missing_process_handles(monkeypatch):
    from cmru import process_guard

    guard = DescendantGuard(root_pid=os.getpid())
    pid = os.getpid() + 100004
    guard.baseline = {pid: ProcessRecord(pid, os.getpid(), "S", "old", 1)}
    changed = ProcessRecord(pid, os.getpid(), "S", "new", 2)
    assert guard._new_descendants({pid: changed}) == {pid: changed}
    monkeypatch.setattr(process_guard.os, "kill", lambda *_args: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(process_guard.os, "waitpid", lambda *_args: (_ for _ in ()).throw(ChildProcessError()))
    guard._kill(pid)
    guard._reap(pid)


def test_prctl_failures_are_reported_without_import_time_resolution(monkeypatch):
    import errno

    from cmru import process_guard

    class NoPrctl:
        pass

    monkeypatch.setattr(process_guard.ctypes, "CDLL", lambda *_args, **_kwargs: NoPrctl())
    with pytest.raises(ProcessTreeUnavailable, match="does not expose prctl"):
        process_guard._prctl(process_guard._PR_SET_CHILD_SUBREAPER, 1)

    class FailedPrctl:
        @staticmethod
        def prctl(*_args):
            return -1

    monkeypatch.setattr(process_guard.ctypes, "CDLL", lambda *_args, **_kwargs: FailedPrctl())
    monkeypatch.setattr(process_guard.ctypes, "get_errno", lambda: errno.EPERM)
    with pytest.raises(OSError) as raised:
        process_guard._prctl(process_guard._PR_SET_CHILD_SUBREAPER, 1)
    assert raised.value.errno == errno.EPERM

    monkeypatch.setattr(process_guard.ctypes, "get_errno", lambda: 0)
    with pytest.raises(OSError) as fallback:
        process_guard._prctl(process_guard._PR_SET_CHILD_SUBREAPER, 1)
    assert fallback.value.errno == errno.EIO

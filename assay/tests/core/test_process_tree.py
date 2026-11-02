"""Native mutation process-tree isolation must close every command window."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from assay import process_tree
from assay.process_tree import (
    ProcessTreeDescendant,
    ProcessTreeError,
    ProcessTreeEvidence,
    ProcessTreeProcessRunner,
    ProcessTreeSpawnError,
    ProcessTreeWindow,
)


def _pid_is_gone(pid: int) -> bool:
    return not Path(f"/proc/{pid}").exists()


@pytest.mark.parametrize("value", [True, 0, -1])
def test_process_tree_evidence_rejects_invalid_signal(value) -> None:
    with pytest.raises(ValueError, match="signal must be a positive integer"):
        ProcessTreeEvidence(
            leader_status="signaled",
            group_signalled_before_reap=False,
            outliving_descendants=0,
            descendants_signalled=0,
            signal=value,
            clean=True,
        )


def test_process_tree_evidence_rejects_a_non_string_leader_status() -> None:
    with pytest.raises(ValueError, match="leader status"):
        ProcessTreeEvidence(
            leader_status=[],  # type: ignore[arg-type]
            group_signalled_before_reap=False,
            outliving_descendants=0,
            descendants_signalled=0,
            clean=True,
        )


def test_process_tree_evidence_rejects_more_signalled_than_outliving_descendants():
    with pytest.raises(ValueError, match="cannot exceed outliving_descendants"):
        ProcessTreeEvidence(
            leader_status="exited",
            group_signalled_before_reap=False,
            outliving_descendants=1,
            descendants_signalled=2,
            clean=True,
        )


def test_normal_leader_exit_kills_and_reaps_detached_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "detached.pid"
    descendant = (
        "import os,time\n"
        "os.setsid()\n"
        f"temporary={str(pid_file) + '.tmp'!r}\n"
        f"destination={str(pid_file)!r}\n"
        "with open(temporary, 'w') as stream:\n"
        " stream.write(str(os.getpid()))\n"
        "os.replace(temporary, destination)\n"
        "time.sleep(60)\n"
    )
    parent = (
        "import os,subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL)\n"
        "deadline=time.monotonic()+30\n"
        f"while not os.path.exists({str(pid_file)!r}) and time.monotonic()<deadline:\n"
        " time.sleep(0.01)\n"
        f"if not os.path.isfile({str(pid_file)!r}): raise SystemExit('child did not publish pid')\n"
        "print('leader done')\n"
    )
    waited_pids: list[int] = []
    original_waitpid = os.waitpid

    def wait_for_specific_child(pid: int, options: int):
        assert pid > 0, "process-tree cleanup must never reap an unspecified child"
        waited_pids.append(pid)
        return original_waitpid(pid, options)

    monkeypatch.setattr(os, "waitpid", wait_for_specific_child)
    result = ProcessTreeProcessRunner()(
        [sys.executable, "-c", parent],
        env=dict(os.environ),
        cwd=tmp_path,
        timeout=35,
    )

    assert result.returncode == 0
    evidence = ProcessTreeEvidence.from_dict(result.process_tree_evidence)
    assert evidence.clean is True
    assert evidence.leader_status == "exited"
    assert evidence.outliving_descendants >= 1
    assert evidence.descendants_signalled >= 1
    assert waited_pids
    pid = int(pid_file.read_text())
    descendant = next(item for item in evidence.descendants if item.pid == pid)
    assert descendant.ppid is not None and descendant.ppid > 0
    assert descendant.comm
    assert _pid_is_gone(pid)


def test_vanished_descendant_keeps_pid_and_explicitly_omits_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = 87654321

    def vanished(self):
        assert str(self) == f"/proc/{pid}/stat"
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(Path, "read_bytes", vanished)
    identity = process_tree._process_identity(pid)

    assert identity.to_dict() == {"pid": pid, "ppid": None, "comm": None}


@pytest.mark.parametrize(
    "stat_bytes",
    [b"truncated", b"123 (worker) S malformed"],
    ids=["missing-fields", "malformed-ppid"],
)
def test_malformed_descendant_stat_keeps_pid_and_omits_identity(
    monkeypatch: pytest.MonkeyPatch, stat_bytes: bytes
) -> None:
    pid = 87654321
    monkeypatch.setattr(Path, "read_bytes", lambda self: stat_bytes)

    identity = process_tree._process_identity(pid)

    assert identity.to_dict() == {"pid": pid, "ppid": None, "comm": None}


@pytest.mark.parametrize(
    ("name", "expected_comm"),
    [("wörker", "wörker"), ("", None)],
    ids=["non_ascii", "empty_name"],
)
def test_real_descendant_with_nonstandard_name_is_cleaned_and_recorded(
    tmp_path: Path, name: str, expected_comm: str | None
) -> None:
    pid_file = tmp_path / "named-child.pid"
    name_bytes = name.encode("utf-8")
    child = (
        "import ctypes,os,time\n"
        "os.setsid()\n"
        "libc=ctypes.CDLL(None)\n"
        "libc.prctl.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong]\n"
        f"assert libc.prctl(15,{name_bytes!r},0,0,0)==0\n"
        f"temporary={str(pid_file) + '.tmp'!r}\n"
        f"destination={str(pid_file)!r}\n"
        "with open(temporary,'w') as stream:\n"
        " stream.write(str(os.getpid()))\n"
        "os.replace(temporary,destination)\n"
        "time.sleep(60)\n"
    )
    parent = (
        "import os,subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "deadline=time.monotonic()+30\n"
        f"while not os.path.exists({str(pid_file)!r}) and time.monotonic()<deadline:\n"
        " time.sleep(0.01)\n"
        f"if not os.path.isfile({str(pid_file)!r}): raise SystemExit('child did not publish pid')\n"
    )
    try:
        result = ProcessTreeProcessRunner()(
            [sys.executable, "-c", parent],
            env=dict(os.environ),
            cwd=tmp_path,
            timeout=40,
        )
    finally:
        # Keep a failed regression test from leaking its deliberately long-
        # lived child into the rest of the test process.
        if pid_file.is_file():
            child_pid = int(pid_file.read_text())
            if not _pid_is_gone(child_pid):
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(child_pid, 0)
                except ChildProcessError:
                    pass

    evidence = ProcessTreeEvidence.from_dict(result.process_tree_evidence)
    child_pid = int(pid_file.read_text())
    child_identity = next(
        item for item in evidence.descendants if item.pid == child_pid
    )
    assert result.returncode == 0
    assert evidence.clean is True
    assert evidence.outliving_descendants >= 1
    assert evidence.descendants_signalled >= 1
    assert child_identity.comm == expected_comm
    if expected_comm is None:
        assert child_identity.ppid is None
    else:
        assert child_identity.ppid is not None and child_identity.ppid > 0
    assert _pid_is_gone(child_pid)


def test_signaled_leader_requires_its_terminating_signal() -> None:
    with pytest.raises(ValueError, match="signaled leader requires a signal"):
        ProcessTreeEvidence(
            leader_status="signaled",
            group_signalled_before_reap=False,
            outliving_descendants=0,
            descendants_signalled=0,
            signal=None,
            clean=True,
        )


def test_timeout_kills_detached_descendant_and_keeps_timeout_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_file = tmp_path / "timed-out.pid"
    child = (
        "import os,time; os.setsid(); "
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent = (
        "import os,subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL)\n"
        "deadline=time.monotonic()+5\n"
        f"while not os.path.exists({str(pid_file)!r}) and time.monotonic()<deadline:\n"
        " time.sleep(0.01)\n"
        "time.sleep(60)\n"
    )
    def ready_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        deadline = __import__("time").monotonic() + 5
        while not pid_file.exists() and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.01)
        assert pid_file.is_file(), "detached child did not publish readiness"
        return proc

    runner = ProcessTreeProcessRunner()
    real_popen = subprocess.Popen
    # The timeout starts only after Popen returns. Make child readiness part of
    # process creation so host scheduling cannot race the timeout assertion.
    monkeypatch.setattr(subprocess, "Popen", ready_popen)

    with pytest.raises(subprocess.TimeoutExpired) as captured:
        runner(
            [sys.executable, "-c", parent],
            env=dict(os.environ),
            cwd=tmp_path,
            timeout=2,
        )

    raw = captured.value.process_tree_evidence
    evidence = ProcessTreeEvidence.from_dict(raw)
    assert evidence.clean is True
    assert evidence.leader_status == "timeout-killed"
    assert pid_file.is_file()
    assert _pid_is_gone(int(pid_file.read_text()))
    assert getattr(captured.value, "assay_stdout_dropped_bytes") == 0


def test_waitid_distinguishes_a_live_child_from_kernel_echild() -> None:
    assert process_tree._has_any_children() is False


def test_kernel_sigign_mask_detects_ignored_sigchld_in_isolated_process() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src"
    script = (
        "import signal; signal.signal(signal.SIGCHLD, signal.SIG_IGN); "
        "from assay.process_tree import _sigchld_is_ignored; "
        "assert _sigchld_is_ignored()"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_cli_restores_waitable_sigchld_before_subprocesses() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src"
    script = "\n".join((
        "import io, signal, subprocess, sys",
        "signal.signal(signal.SIGCHLD, signal.SIG_IGN)",
        "from assay.cli import main",
        "try:",
        "    main(['--help'], stdout=io.StringIO(), stderr=io.StringIO())",
        "except SystemExit:",
        "    pass",
        "assert signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL",
        "child = subprocess.run([sys.executable, '-c', 'raise SystemExit(7)'])",
        "assert child.returncode == 7, child.returncode",
    ))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_window_refuses_ignored_sigchld_before_enabling_subreaper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subreaper_changes: list[bool] = []
    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: False)
    monkeypatch.setattr(process_tree, "_set_subreaper", subreaper_changes.append)
    monkeypatch.setattr(process_tree, "_sigchld_is_ignored", lambda: True)

    with pytest.raises(ProcessTreeError, match="SIGCHLD to be waitable"):
        ProcessTreeWindow().__enter__()
    assert subreaper_changes == []
    assert process_tree._WINDOW_LOCK.acquire(blocking=False)
    process_tree._WINDOW_LOCK.release()


def test_failed_cleanup_blocks_later_windows_and_preserves_subreaper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_tree, "_UNPROVEN_CLEANUP", False)
    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: True)
    monkeypatch.setattr(process_tree, "_sigchld_is_ignored", lambda: False)
    window = ProcessTreeWindow()
    window.__enter__()

    def fail_cleanup(*, force: bool = False):
        del force
        raise ProcessTreeError("cleanup not proven")

    monkeypatch.setattr(window, "finish", fail_cleanup)
    with pytest.raises(ProcessTreeError, match="cleanup not proven"):
        window.__exit__(None, None, None)
    assert process_tree._UNPROVEN_CLEANUP is True
    with pytest.raises(ProcessTreeError, match="earlier native R2 window"):
        ProcessTreeWindow().__enter__()
    pid = os.fork()
    if pid == 0:
        time.sleep(30)
        os._exit(0)
    try:
        assert process_tree._has_any_children() is True
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    assert process_tree._has_any_children() is False


@pytest.mark.parametrize("prior_subreaper", [False, True])
def test_window_refuses_to_start_when_caller_has_an_existing_child(
    monkeypatch: pytest.MonkeyPatch, prior_subreaper: bool,
) -> None:
    subreaper_changes: list[bool] = []
    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: prior_subreaper)
    monkeypatch.setattr(process_tree, "_set_subreaper", subreaper_changes.append)
    monkeypatch.setattr(process_tree, "_has_any_children", lambda: True)

    with pytest.raises(ProcessTreeError, match="already has child processes"):
        ProcessTreeWindow().__enter__()
    assert subreaper_changes == ([] if prior_subreaper else [True, False])


def test_procfs_blindness_cannot_certify_a_live_descendant_as_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_file = tmp_path / "adopted.pid"
    child = (
        "import os,time; os.setsid(); "
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent = (
        "import os,subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL)\n"
        "deadline=time.monotonic()+5\n"
        f"while not os.path.exists({str(pid_file)!r}) and time.monotonic()<deadline:\n"
        " time.sleep(0.01)\n"
    )
    window = ProcessTreeWindow(cleanup_seconds=0.1)
    with window:
        proc = window.spawn(
            [sys.executable, "-c", parent],
            cwd=tmp_path,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        proc.wait(timeout=5)
        child_pid = int(pid_file.read_text())
        assert not _pid_is_gone(child_pid)
        with monkeypatch.context() as patch:
            patch.setattr(process_tree, "_direct_child_pids", lambda: [])
            with pytest.raises(ProcessTreeError, match="did not become empty"):
                window.finish()
        evidence = window.finish()
        assert evidence.outliving_descendants >= 1
        assert evidence.descendants_signalled >= 1
        assert _pid_is_gone(child_pid)


def test_spawn_failure_is_candidate_level_and_carries_empty_tree_evidence(
    tmp_path: Path,
) -> None:
    with pytest.raises(ProcessTreeSpawnError) as captured:
        ProcessTreeProcessRunner()(
            [str(tmp_path / "does-not-exist")],
            env=dict(os.environ),
            cwd=tmp_path,
            timeout=1,
        )
    evidence = ProcessTreeEvidence.from_dict(
        captured.value.process_tree_evidence
    )
    assert evidence.leader_status == "not-started"
    assert evidence.outliving_descendants == 0


def test_spawn_error_wrapper_preserves_both_unstructured_and_structured_oserrors() -> None:
    class ErrorWithoutFilename2(OSError):
        def __getattribute__(self, name):
            if name == "filename2":
                raise AttributeError(name)
            return super().__getattribute__(name)

    unstructured = ProcessTreeSpawnError.from_oserror(
        ErrorWithoutFilename2("popen failed")
    )
    assert str(unstructured) == "popen failed"
    no_filename2 = ProcessTreeSpawnError.from_oserror(
        ErrorWithoutFilename2(errno.EPERM, "denied")
    )
    assert no_filename2.errno == errno.EPERM
    original = FileNotFoundError(errno.ENOENT, "not found", "missing-command")
    structured = ProcessTreeSpawnError.from_oserror(original)
    assert structured.errno == errno.ENOENT
    assert structured.filename == "missing-command"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("leader_status", "killed", "invalid process-tree leader status"),
        ("group_signalled_before_reap", 1, "must be a boolean"),
        ("outliving_descendants", -1, "non-negative integer"),
        ("descendants_signalled", True, "non-negative integer"),
        ("clean", 1, "must be a boolean"),
        ("protocol", "assay-process-tree/0", "unsupported process-tree evidence protocol"),
        ("clean", False, "must prove a clean window"),
    ],
)
def test_process_tree_evidence_rejects_invalid_fields(field, value, message):
    values = {
        "leader_status": "exited",
        "group_signalled_before_reap": False,
        "outliving_descendants": 0,
        "descendants_signalled": 0,
        "signal": None,
        "descendants": (),
        "clean": True,
    }
    values[field] = value
    with pytest.raises(ValueError, match=message):
        ProcessTreeEvidence(**values)


def test_process_tree_evidence_requires_the_exact_serialized_field_set():
    with pytest.raises(ValueError, match="invalid field set"):
        ProcessTreeEvidence.from_dict({})
    assert ProcessTreeEvidence.not_started().leader_status == "not-started"


@pytest.mark.parametrize(
    "value",
    [None, True, "1", pytest.param(10**10000, id="integer-overflow")],
)
def test_process_tree_cleanup_bound_rejects_nonfinite_or_non_numeric_values(value):
    with pytest.raises(ValueError, match="cleanup_seconds must be positive"):
        ProcessTreeWindow(cleanup_seconds=value)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"pid": True, "ppid": 1, "comm": "child"}, "pid must be a positive integer"),
        ({"pid": 1, "ppid": 1, "comm": None}, "identity must be complete or omitted"),
        ({"pid": 1, "ppid": True, "comm": "child"}, "ppid must be a positive integer"),
        ({"pid": 1, "ppid": 0, "comm": "child"}, "ppid must be a positive integer"),
        ({"pid": 1, "ppid": 1, "comm": ""}, "comm must be a non-empty string"),
        ({"pid": 1, "ppid": 1, "comm": 7}, "comm must be a non-empty string"),
    ],
)
def test_process_tree_descendant_refuses_incomplete_or_invalid_identity(values, message):
    with pytest.raises(ValueError, match=message):
        ProcessTreeDescendant(**values)


def test_process_tree_descendant_wire_shape_is_exact_and_round_trips():
    with pytest.raises(ValueError, match="invalid field set"):
        ProcessTreeDescendant.from_dict({"pid": 7, "ppid": 1, "comm": "child", "extra": 1})
    identity = ProcessTreeDescendant.from_dict({"pid": 7, "ppid": 1, "comm": "child"})
    assert identity.to_dict() == {"pid": 7, "ppid": 1, "comm": "child"}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"leader_status": "exited", "signal": 9}, "signal requires a signaled leader"),
        (
            {"leader_status": "signaled", "signal": 9, "descendants": []},
            "descendants must be a tuple",
        ),
        ({"descendants": (ProcessTreeDescendant(pid=1, ppid=1, comm="child"),) * 10_001,
          "outliving_descendants": 10_001}, "exceed 10000"),
        ({"descendants": (ProcessTreeDescendant(pid=1, ppid=1, comm="child"),) * 2,
          "outliving_descendants": 2}, "duplicate pid"),
        ({"descendants": (ProcessTreeDescendant(pid=2, ppid=1, comm="child"),
                           ProcessTreeDescendant(pid=1, ppid=1, comm="child")),
          "outliving_descendants": 2}, "sorted by pid"),
        ({"descendants": (ProcessTreeDescendant(pid=1, ppid=1, comm="child"),)},
         "must match outliving_descendants"),
    ],
)
def test_process_tree_evidence_rejects_untrustworthy_descendant_rows(changes, message):
    values = {
        "leader_status": "exited",
        "group_signalled_before_reap": False,
        "outliving_descendants": 0,
        "descendants_signalled": 0,
        "signal": None,
        "descendants": (),
        "clean": True,
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        ProcessTreeEvidence(**values)


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (None, PermissionError("hidden")),
        (b"malformed", None),
        (b"(child) S", None),
        (b"(child) S nope 0", None),
        (b"(child) S 0 0", None),
    ],
)
def test_process_identity_omits_unavailable_or_malformed_procfs(
    monkeypatch: pytest.MonkeyPatch, raw: bytes | None, error: OSError | None
):
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error) if error else raw,
    )
    identity = process_tree._process_identity(42)
    assert identity.to_dict() == {"pid": 42, "ppid": None, "comm": None}


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_process_tree_window_requires_a_finite_positive_cleanup_bound(seconds):
    with pytest.raises(ValueError, match="cleanup_seconds must be positive"):
        ProcessTreeWindow(cleanup_seconds=seconds)


class _ScandirEntries:
    def __init__(self, names: list[str]):
        self.entries = [SimpleNamespace(name=name, path=f"/proc/{name}") for name in names]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.entries)


def test_ppid_fallback_reads_proc_stat_and_uses_the_parent_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process_tree.os, "scandir", lambda _path: _ScandirEntries(["not-a-pid", "41"])
    )
    monkeypatch.setattr(
        process_tree.Path,
        "read_text",
        lambda _path, **_kwargs: "41 (name with ) paren) S 7 0 0",
    )
    assert process_tree._ppid_children_from_proc(7) == [41]


def test_ppid_fallback_ignores_processes_with_a_different_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_tree.os, "scandir", lambda _path: _ScandirEntries(["41"]))
    monkeypatch.setattr(
        process_tree.Path, "read_text",
        lambda _path, **_kwargs: "41 (child) S 8 0 0",
    )
    assert process_tree._ppid_children_from_proc(7) == []


@pytest.mark.parametrize(
    ("stat_text", "read_error", "message"),
    [
        (None, FileNotFoundError(), None),
        (None, ProcessLookupError(), None),
        (None, PermissionError("hidden"), "cannot inspect procfs process"),
        ("malformed", None, "malformed procfs stat"),
        ("41 (name) S", None, "truncated procfs stat"),
        ("41 (name) S not-a-pid", None, "malformed procfs pid fields"),
    ],
)
def test_ppid_fallback_refuses_incomplete_or_malformed_procfs(
    monkeypatch: pytest.MonkeyPatch, stat_text, read_error, message,
) -> None:
    monkeypatch.setattr(process_tree.os, "scandir", lambda _path: _ScandirEntries(["41"]))

    def read_stat(_path, **_kwargs):
        if read_error is not None:
            raise read_error
        return stat_text

    monkeypatch.setattr(process_tree.Path, "read_text", read_stat)
    if message is None:
        assert process_tree._ppid_children_from_proc(7) == []
    else:
        with pytest.raises(ProcessTreeError, match=message):
            process_tree._ppid_children_from_proc(7)


def test_ppid_fallback_refuses_when_procfs_itself_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process_tree.os, "scandir", lambda _path: (_ for _ in ()).throw(PermissionError("hidden"))
    )
    with pytest.raises(ProcessTreeError, match="cannot scan procfs"):
        process_tree._ppid_children_from_proc(7)


@pytest.mark.parametrize("failure", ["process-lookup", "permission"])
def test_direct_child_fallback_handles_missing_task_files_and_uses_ppid_scan(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    task = Path(f"/proc/{os.getpid()}/task/{os.getpid()}")
    monkeypatch.setattr(Path, "iterdir", lambda _path: [task])

    def read_children(_path, **_kwargs):
        if failure == "process-lookup":
            raise ProcessLookupError("task disappeared")
        raise PermissionError("task hidden")

    monkeypatch.setattr(Path, "read_text", read_children)
    monkeypatch.setattr(process_tree, "_ppid_children_from_proc", lambda _pid: [7])
    assert process_tree._direct_child_pids() == [7]


def test_direct_child_list_is_sorted_and_malformed_values_are_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = Path(f"/proc/{os.getpid()}/task/{os.getpid()}")
    monkeypatch.setattr(Path, "iterdir", lambda _path: [task])
    monkeypatch.setattr(Path, "read_text", lambda _path, **_kwargs: "9 3 9")
    monkeypatch.setattr(
        process_tree, "_ppid_children_from_proc",
        lambda _pid: pytest.fail("a non-empty children file should be authoritative"),
    )
    assert process_tree._direct_child_pids() == [3, 9]

    monkeypatch.setattr(Path, "read_text", lambda _path, **_kwargs: "not-a-pid")
    with pytest.raises(ProcessTreeError, match="malformed procfs children list"):
        process_tree._direct_child_pids()


def test_direct_child_enumeration_falls_back_when_task_listing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Path, "iterdir",
        lambda _path: (_ for _ in ()).throw(PermissionError("task list hidden")),
    )
    monkeypatch.setattr(process_tree, "_ppid_children_from_proc", lambda _pid: [11])
    assert process_tree._direct_child_pids() == [11]


@pytest.mark.parametrize("failure", ["get", "set"])
def test_subreaper_prctl_failures_are_lane_fatal(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    class FailedLibc:
        @staticmethod
        def prctl(*_args):
            return -1

    monkeypatch.setattr(process_tree.ctypes, "CDLL", lambda *_a, **_kw: FailedLibc())
    monkeypatch.setattr(process_tree.ctypes, "get_errno", lambda: errno.EPERM)
    if failure == "get":
        with pytest.raises(ProcessTreeError, match="cannot inspect child-subreaper"):
            process_tree._subreaper_state()
    else:
        with pytest.raises(ProcessTreeError, match="cannot enable child-subreaper"):
            process_tree._set_subreaper(True)


@pytest.mark.parametrize("failure", ["missing", "permission"])
def test_process_group_kill_reports_lookup_and_other_os_errors(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    def killpg(_pgid, _sig):
        if failure == "missing":
            raise ProcessLookupError("gone")
        raise PermissionError("denied")

    monkeypatch.setattr(process_tree.os, "killpg", killpg)
    if failure == "missing":
        assert process_tree._kill_process_group(22) is False
    else:
        with pytest.raises(ProcessTreeError, match="cannot signal candidate process group"):
            process_tree._kill_process_group(22)


def test_window_requires_linux_and_restores_subreaper_after_enable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_tree.sys, "platform", "win32")
    with pytest.raises(ProcessTreeError, match="requires Linux"):
        ProcessTreeWindow().__enter__()

    monkeypatch.setattr(process_tree.sys, "platform", "linux")
    subreaper_changes: list[bool] = []

    def set_subreaper(enabled: bool) -> None:
        subreaper_changes.append(enabled)
        raise ProcessTreeError("cannot enable" if enabled else "restore failed")

    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: False)
    monkeypatch.setattr(process_tree, "_set_subreaper", set_subreaper)
    with pytest.raises(ProcessTreeError, match="cannot enable"):
        ProcessTreeWindow().__enter__()
    assert subreaper_changes == [True, False]


def test_unentered_window_exit_reports_inactive_cleanup() -> None:
    with pytest.raises(ProcessTreeError, match="not active"):
        ProcessTreeWindow().__exit__(None, None, None)


def test_window_does_not_reset_a_subreaper_it_did_not_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[bool] = []
    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: True)
    monkeypatch.setattr(process_tree, "_set_subreaper", changes.append)
    monkeypatch.setattr(process_tree, "_has_any_children", lambda: False)
    with ProcessTreeWindow() as window:
        evidence = window.finish()
    assert evidence.leader_status == "not-started"
    assert changes == []


def test_window_spawn_and_attach_refuse_invalid_lifecycle_states() -> None:
    window = ProcessTreeWindow()
    with pytest.raises(ProcessTreeError, match="not ready to spawn"):
        window.spawn(["true"], start_new_session=True)
    with pytest.raises(ProcessTreeError, match="not ready to attach"):
        window.attach(SimpleNamespace())
    with pytest.raises(ProcessTreeError, match="not active"):
        window.finish()

    with window:
        with pytest.raises(ProcessTreeError, match="new session"):
            window.spawn(["true"], start_new_session=False)
        process = SimpleNamespace(pid=123, returncode=0)
        window.attach(process)
        assert window._proc is process


def test_window_finish_requires_a_dead_leader_unless_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveProcess:
        pid = 123
        returncode = None

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

    process = LiveProcess()
    monkeypatch.setattr(process_tree, "_kill_process_group", lambda _pgid: True)
    window = ProcessTreeWindow()
    with pytest.raises(ProcessTreeError, match="live leader"):
        with window:
            window.attach(process)
            window.finish()
    assert process.returncode == 0
    assert window.evidence is not None
    assert window.evidence.leader_status == "exited"
    assert window.finish() is window.evidence


def test_process_window_spawn_propagates_popen_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process_tree, "_subreaper_state", lambda: False
    )
    monkeypatch.setattr(process_tree, "_set_subreaper", lambda _enabled: None)
    monkeypatch.setattr(process_tree, "_has_any_children", lambda: False)
    monkeypatch.setattr(
        process_tree.subprocess, "Popen",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("spawn denied")),
    )
    with ProcessTreeWindow() as window:
        with pytest.raises(OSError, match="spawn denied"):
            window.spawn(
                ["candidate"], cwd=tmp_path, env=dict(os.environ),
                start_new_session=True,
            )


def test_signalled_leader_is_preserved_in_process_tree_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SignalledProcess:
        pid = 123
        returncode = -signal.SIGSEGV

    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: False)
    monkeypatch.setattr(process_tree, "_set_subreaper", lambda _enabled: None)
    monkeypatch.setattr(process_tree, "_has_any_children", lambda: False)
    window = ProcessTreeWindow()
    with window:
        window.attach(SignalledProcess())
        evidence = window.finish()
    assert evidence.leader_status == "signaled"


@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_forced_leader_cleanup_failure_stays_lane_fatal(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    class LiveProcess:
        pid = 123
        returncode = None

        def wait(self, timeout=None):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(["candidate"], timeout)
            raise OSError("wait failed")

    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: False)
    monkeypatch.setattr(process_tree, "_set_subreaper", lambda _enabled: None)
    monkeypatch.setattr(process_tree, "_has_any_children", lambda: False)
    monkeypatch.setattr(process_tree, "_kill_process_group", lambda _pgid: True)
    window = ProcessTreeWindow(cleanup_seconds=0.01)
    window.__enter__()
    window.attach(LiveProcess())
    message = "did not exit during cleanup" if failure == "timeout" else "cannot reap candidate leader"
    with pytest.raises(ProcessTreeError, match=message):
        window.finish(force=True)
    if failure == "timeout":
        with pytest.raises(ProcessTreeError, match="while handling"):
            window.__exit__(RuntimeError, RuntimeError("outer failure"), None)
    else:
        with pytest.raises(ProcessTreeError):
            window.__exit__(None, None, None)


@pytest.mark.parametrize("failure", ["signal", "reap"])
def test_adopted_descendant_cleanup_os_errors_are_lane_fatal(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: False)
    monkeypatch.setattr(process_tree, "_set_subreaper", lambda _enabled: None)
    child_queries = 0

    def has_children():
        nonlocal child_queries
        child_queries += 1
        return child_queries > 1

    monkeypatch.setattr(process_tree, "_has_any_children", has_children)
    monkeypatch.setattr(process_tree, "_direct_child_pids", lambda: [123])
    if failure == "signal":
        monkeypatch.setattr(
            process_tree.os, "kill",
            lambda *_a: (_ for _ in ()).throw(PermissionError("signal denied")),
        )
        message = "cannot signal adopted candidate descendant"
    else:
        monkeypatch.setattr(process_tree.os, "kill", lambda *_a: None)
        monkeypatch.setattr(
            process_tree.os, "waitpid",
            lambda *_a: (_ for _ in ()).throw(OSError("waitpid denied")),
        )
        message = "cannot reap candidate descendants"
    window = ProcessTreeWindow(cleanup_seconds=0.01)
    window.__enter__()
    with pytest.raises(ProcessTreeError, match=message):
        window.finish()
    with pytest.raises(ProcessTreeError):
        window.__exit__(None, None, None)


def test_child_already_reaped_by_another_waiter_is_never_waited_by_any_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_queries = 0

    def has_children():
        nonlocal child_queries
        child_queries += 1
        return child_queries == 2

    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: False)
    monkeypatch.setattr(process_tree, "_set_subreaper", lambda _enabled: None)
    monkeypatch.setattr(process_tree, "_has_any_children", has_children)
    monkeypatch.setattr(process_tree, "_direct_child_pids", lambda: [123])
    monkeypatch.setattr(
        process_tree.os, "kill",
        lambda *_a: (_ for _ in ()).throw(ProcessLookupError("already gone")),
    )
    monkeypatch.setattr(
        process_tree.os, "waitpid",
        lambda pid, _options: (_ for _ in ()).throw(ChildProcessError()),
    )
    window = ProcessTreeWindow(cleanup_seconds=0.01)
    with window:
        evidence = window.finish()
    assert evidence.clean is True


def test_process_runner_turns_post_spawn_oserror_into_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process_tree, "_read_tail",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("tail read failed")),
    )
    with pytest.raises(ProcessTreeError, match="failed after starting the command"):
        ProcessTreeProcessRunner()(
            [sys.executable, "-c", "pass"],
            env=dict(os.environ),
            cwd=tmp_path,
            timeout=2,
        )


def test_read_tail_redacts_secret_that_crosses_retained_boundary(tmp_path: Path) -> None:
    output = tmp_path / "output.bin"
    output.write_bytes(b"xxSECRETzz")
    with output.open("rb") as stream:
        tail, dropped = process_tree._read_tail(
            stream, limit=5, sensitive_values=("SECRET",)
        )
    assert dropped == 5
    assert tail == "***zz"


def test_process_runner_propagates_process_tree_proof_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    finish = ProcessTreeWindow.finish

    def finish_then_fail(window, **kwargs):
        finish(window, **kwargs)
        raise ProcessTreeError("tree proof unavailable")

    monkeypatch.setattr(ProcessTreeWindow, "finish", finish_then_fail)
    with pytest.raises(ProcessTreeError, match="tree proof unavailable"):
        ProcessTreeProcessRunner()(
            [sys.executable, "-c", "pass"],
            env=dict(os.environ),
            cwd=tmp_path,
            timeout=2,
        )


def test_subreaper_restore_failure_is_reported_after_successful_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[bool] = []

    def set_subreaper(enabled: bool) -> None:
        changes.append(enabled)
        if not enabled:
            raise OSError("restore denied")

    monkeypatch.setattr(process_tree, "_subreaper_state", lambda: False)
    monkeypatch.setattr(process_tree, "_set_subreaper", set_subreaper)
    monkeypatch.setattr(process_tree, "_has_any_children", lambda: False)
    window = ProcessTreeWindow()
    with pytest.raises(ProcessTreeError, match="cleanup failed"):
        with window:
            window.finish()
    assert changes == [True, False]

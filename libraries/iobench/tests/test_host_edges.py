"""Mock-only closure for the remaining RealHost and supervisor adapter branches."""
from __future__ import annotations

import errno
import importlib.util
import io
import os
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from iobench import host as host_mod
from iobench.host import HostError, RealHost, TargetRef

MIB = 1 << 20
DEV = "/dev/vda7"
R1002_SIZE = 34_359_738_368
R1002_PARTUUID = "5a8943ed-0071-4c45-95b9-62b5f47058ef"


class _ExitException(Exception):
    pass


class _Closeable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _file_ref(path: str, *, size: int = 4 * MIB, inode: int = 77) -> TargetRef:
    return TargetRef("file", path, size, fs_uuid="fs-uuid", inode=inode, fs_type="ext4")


def _device_ref(*, partuuid: str | None = R1002_PARTUUID) -> TargetRef:
    return TargetRef("device", DEV, R1002_SIZE, devno="254:7", partuuid=partuuid)


def _sysfs_open(size_bytes: int) -> object:
    return lambda path, *args, **kwargs: (
        io.StringIO(str(size_bytes // 512) + "\n")
        if str(path).startswith("/sys/dev/block/")
        else open(path, *args, **kwargs)
    )


def test_host_import_falls_back_without_prctl(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError(errno.ENOSYS, "prctl unavailable")

    monkeypatch.setattr(host_mod.ctypes, "CDLL", unavailable)
    spec = importlib.util.spec_from_file_location("iobench._host_without_prctl", host_mod.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    assert module._PRCTL is None


def test_wipefs_without_no_act_and_short_systemctl_argv_are_refused():
    with pytest.raises(HostError, match="wipefs without -n"):
        host_mod.check_argv(["wipefs", "-p", DEV], bound=True)
    assert host_mod.is_allowed_argv(["systemctl"]) is False


def test_parent_death_installation_and_reap_timeout(monkeypatch):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(host_mod.signal, "pthread_sigmask", lambda _how, _signals: None)
    monkeypatch.setattr(host_mod, "_PRCTL", lambda option, signum: calls.append((option, signum)) or 0)
    monkeypatch.setattr(host_mod.os, "getppid", lambda: 77)

    host_mod._arm_parent_death(77)
    assert calls == [(host_mod.PR_SET_PDEATHSIG, host_mod.signal.SIGTERM)]

    monkeypatch.setattr(host_mod.os, "getppid", lambda: 88)
    monkeypatch.setattr(host_mod.os, "_exit", lambda code: (_ for _ in ()).throw(_ExitException(code)))
    with pytest.raises(_ExitException, match="1"):
        host_mod._arm_parent_death(77)

    class Proc:
        stdout = None
        stderr = _Closeable()

        @staticmethod
        def communicate(*, timeout):
            raise subprocess.TimeoutExpired(["true"], timeout)

    host_mod._reap(Proc())
    assert Proc.stderr.closed is True


def test_proc_handle_unavailable_prctl_is_refused(monkeypatch):
    monkeypatch.setattr(host_mod, "_PRCTL", None)
    with pytest.raises(HostError, match="PR_SET_PDEATHSIG.*unavailable"):
        host_mod._ProcHandle(["fio", "--version"])


def test_proc_handle_poll_sentinel_and_close_boundaries(monkeypatch):
    class IdlePopen:
        pid = 4242

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        @staticmethod
        def poll():
            return None

        @staticmethod
        def send_signal(_signum):
            return None

        @staticmethod
        def wait(timeout=None):
            return 0

    monkeypatch.setattr(host_mod, "_PRCTL", object())
    monkeypatch.setattr(host_mod.subprocess, "Popen", IdlePopen)
    handle = host_mod._ProcHandle(["true"])
    assert handle.pid == 4242
    assert handle.poll() is None
    handle.kill()
    assert handle._out.closed and handle._err.closed

    class LookupRacePopen(IdlePopen):
        @staticmethod
        def send_signal(_signum):
            raise ProcessLookupError

    monkeypatch.setattr(host_mod.subprocess, "Popen", LookupRacePopen)
    raced = host_mod._ProcHandle(["true"])
    raced.kill()
    assert raced._out.closed and raced._err.closed

    class SentinelPopen(IdlePopen):
        @staticmethod
        def poll():
            return host_mod.SUPERVISOR_UNREAPED_STATUS

        @staticmethod
        def wait(timeout=None):
            return host_mod.SUPERVISOR_UNREAPED_STATUS

    monkeypatch.setattr(host_mod.subprocess, "Popen", SentinelPopen)
    sentinel = host_mod._ProcHandle(["true"])
    sentinel._err.write(
        f"noise\n{host_mod.SUPERVISOR_UNREAPED_PREFIX}not-a-pid\n"
    )
    sentinel._err.flush()
    assert sentinel.poll() == host_mod.SUPERVISOR_UNREAPED_STATUS
    assert sentinel.unreaped_pid == sentinel.pid
    sentinel.kill()
    assert sentinel._inspect_stderr(host_mod.SUPERVISOR_UNREAPED_STATUS) == ""


def test_realhost_small_adapters_spawn_and_scratch(monkeypatch, tmp_path):
    host = RealHost()
    assert host.allow_scratch(str(tmp_path)) is True
    assert host.allow_scratch(str(tmp_path)) is False
    host.revoke_scratch(str(tmp_path))
    assert host.scratch_roots == []

    monkeypatch.setattr(host_mod.time, "time", lambda: 123.5)
    monkeypatch.setattr(host_mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(host_mod.secrets, "token_hex", lambda _bytes: "beef")
    monkeypatch.setenv("IOBENCH_TEST_VALUE", "present")
    assert host.now() == 123.5
    host.sleep(1)
    assert host.token() == "beef"
    assert host.getenv("IOBENCH_TEST_VALUE") == "present"

    text = tmp_path / "text.txt"
    text.write_text("hello", encoding="utf-8")
    assert host.read_text(str(text)) == "hello"
    assert host.listdir(str(tmp_path)) == ["text.txt"]

    monkeypatch.setattr(host_mod.os, "statvfs", lambda _path: SimpleNamespace(f_blocks=5, f_frsize=512, f_bavail=3))
    assert host.statfs(str(tmp_path)) == (2560, 1536)

    link = tmp_path / "link"
    link.symlink_to(text)
    assert host.readlink(str(link)) == str(text)

    marker = object()
    monkeypatch.setattr(host_mod, "check_argv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_mod, "_ProcHandle", lambda *_args, **_kwargs: marker)
    assert host.spawn(["fio", "--version"]) is marker


@pytest.mark.parametrize(("case", "result"), (
    ("missing", None),
    ("regular", None),
    ("block", "254:7"),
))
def test_realhost_devno_classifies_missing_regular_and_block(monkeypatch, case, result):
    host = RealHost()
    if case == "missing":
        monkeypatch.setattr(host_mod.os, "stat", lambda _path: (_ for _ in ()).throw(FileNotFoundError()))
    elif case == "regular":
        monkeypatch.setattr(host_mod.os, "stat", lambda _path: SimpleNamespace(st_mode=stat.S_IFREG))
    else:
        monkeypatch.setattr(host_mod.os, "stat", lambda _path: SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(254, 7)))
    assert host.devno(DEV) == result


def test_realhost_rmdir_probe_and_sysfs_edges(monkeypatch, tmp_path):
    host = RealHost()
    empty = tmp_path / "empty"
    empty.mkdir()
    host.rmdir(str(empty))
    assert not empty.exists()

    monkeypatch.setattr(host_mod.os, "rmdir", lambda _path: (_ for _ in ()).throw(OSError(errno.ENOTEMPTY, "not empty")))
    host.rmdir(str(tmp_path / "not-empty"))

    opened: list[tuple[object, int, int]] = []
    closed: list[int] = []
    removed: list[object] = []
    monkeypatch.setattr(host_mod.os, "open", lambda path, flags, mode: opened.append((path, flags, mode)) or 71)
    monkeypatch.setattr(host_mod.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(host_mod.os, "unlink", lambda path: removed.append(path))
    assert host.probe_odirect(str(tmp_path)) is True
    assert opened and flags_have(opened[0][1], (os.O_WRONLY, os.O_CREAT, os.O_EXCL, os.O_NOFOLLOW))
    assert closed == [71] and len(removed) == 1

    monkeypatch.setattr(host_mod.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EACCES, "direct io unavailable")))
    assert host.probe_odirect(str(tmp_path)) is False

    sysfs = tmp_path / "sysfs-value"
    host.write_sysfs(str(sysfs), "123")
    assert sysfs.read_text(encoding="utf-8") == "123"
    with pytest.raises(HostError, match="io.max is never written"):
        host.write_sysfs("/sys/fs/cgroup/io.max", "1")


def flags_have(flags: int, values: tuple[int, ...]) -> bool:
    return all(flags & value for value in values)


def test_realhost_verify_live_file_identity_and_blkid_edges(monkeypatch):
    host = RealHost()
    file_target = _file_ref("/bench/target.img", inode=77)
    monkeypatch.setattr(host_mod.os, "stat", lambda _path: SimpleNamespace(st_ino=78, st_size=file_target.size_bytes))
    with pytest.raises(HostError, match="file target identity changed"):
        host._verify_live(file_target)

    device_target = _device_ref()
    monkeypatch.setattr(host_mod.os, "stat", lambda _path: SimpleNamespace(st_rdev=os.makedev(254, 7)))
    monkeypatch.setattr("builtins.open", _sysfs_open(device_target.size_bytes))
    monkeypatch.setattr(host_mod.subprocess, "run",
                            lambda *_args, **_kwargs: SimpleNamespace(returncode=4, stdout="", stderr="probe failed"))
    with pytest.raises(HostError, match="blkid failed"):
        host._verify_live(device_target)

    monkeypatch.setattr(host_mod.subprocess, "run",
                            lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    host._verify_live(_device_ref(partuuid=None))


def test_realhost_file_marker_error_close_and_read(monkeypatch, tmp_path):
    host = RealHost()
    target = _file_ref(str(tmp_path / "target.img"))
    monkeypatch.setattr(host, "_verify_live", lambda _target: None)
    monkeypatch.setattr(host_mod.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EACCES, "denied")))
    with pytest.raises(HostError, match="cannot be opened for its marker"):
        host.write_marker(target, "marker")

    closed: list[int] = []
    writes: list[tuple[int, int]] = []
    monkeypatch.setattr(host_mod.os, "open", lambda *_args, **_kwargs: 71)
    monkeypatch.setattr(host_mod.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(host_mod.os, "pwritev", lambda fd, _buf, offset: writes.append((fd, offset)))
    monkeypatch.setattr(host_mod.os, "fsync", lambda _fd: None)
    host.write_marker(target, "marker")
    assert writes == [(71, target.size_bytes - host_mod.TAIL_BYTES)] and closed == [71]

    marker = tmp_path / "marker.bin"
    size = host_mod.TAIL_BYTES + 4096
    with marker.open("wb") as fh:
        fh.seek(size - host_mod.TAIL_BYTES)
        fh.write(b"read-marker\0ignored")
    assert host.read_marker(TargetRef("file", str(marker), size, fs_type="ext4")) == "read-marker"


def test_realhost_write_atomic_skips_a_temp_path_that_already_disappeared(monkeypatch, tmp_path):
    host = RealHost()
    target = tmp_path / "atomic.json"
    temp = tmp_path / "atomic.tmp"

    def mkstemp(*, dir, prefix):
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return fd, str(temp)

    monkeypatch.setattr(host_mod.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(host_mod.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "replace failed")))
    real_exists = os.path.exists
    monkeypatch.setattr(host_mod.os.path, "exists", lambda path: False if path == str(temp) else real_exists(path))
    with pytest.raises(OSError, match="replace failed"):
        host.write_atomic(str(target), "{}")
    temp.unlink(missing_ok=True)


@pytest.mark.parametrize("case", ("open-error", "enxio", "backwards", "hole-error"))
def test_realhost_written_bytes_edge_errors(monkeypatch, case):
    host = RealHost()
    target = _file_ref("/bench/target.img")
    monkeypatch.setattr(host, "_verify_live", lambda _target: None)

    if case == "open-error":
        monkeypatch.setattr(host_mod.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EIO, "open failed")))
        with pytest.raises(HostError, match="cannot open"):
            host.written_bytes(target, 0, MIB)
        return

    fd = 71
    closed: list[int] = []
    monkeypatch.setattr(host_mod.os, "open", lambda *_args, **_kwargs: fd)
    monkeypatch.setattr(host_mod.os, "close", lambda candidate: closed.append(candidate))
    monkeypatch.setattr(host_mod.os, "fstat", lambda _fd: SimpleNamespace(
        st_mode=stat.S_IFREG, st_ino=target.inode, st_size=target.size_bytes))

    if case == "enxio":
        monkeypatch.setattr(host_mod.os, "lseek",
                                lambda *_args: (_ for _ in ()).throw(OSError(errno.ENXIO, "no data")))
        assert host.written_bytes(target, 0, MIB) == 0
    elif case == "backwards":
        monkeypatch.setattr(host_mod.os, "lseek", lambda _fd, _offset, _whence: -1)
        with pytest.raises(HostError, match="SEEK_DATA moved backwards"):
            host.written_bytes(target, 0, MIB)
    else:
        def hole_error(_fd, offset, whence):
            if whence == os.SEEK_DATA:
                return offset
            raise OSError(errno.EIO, "hole failed")
        monkeypatch.setattr(host_mod.os, "lseek", hole_error)
        with pytest.raises(HostError, match="SEEK_HOLE failed"):
            host.written_bytes(target, 0, MIB)
    assert closed == [fd]


def test_realhost_release_without_claim_and_uninspectable_handle(monkeypatch):
    host = RealHost()
    assert host.release_claim() is None

    class BadHandle:
        @staticmethod
        def poll():
            raise RuntimeError("cannot inspect")

    host._claim_fd = 71
    host._claim_target = _device_ref()
    host._raw_handles = [BadHandle()]
    assert host.release_claim() == (
        "raw_claim_release_failed: a raw writer handle could not be inspected (RuntimeError: cannot inspect)"
    )
    assert host._claim_fd == 71


def test_realhost_lock_and_unlock_paths(monkeypatch, tmp_path):
    host = RealHost()
    host.unlock()

    lock_path = tmp_path / "locks" / "run.lock"
    assert host.lock(str(lock_path)) is True
    host.unlock()
    assert host._lock_fd is None

    monkeypatch.setattr(host_mod.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(OSError(errno.EWOULDBLOCK, "busy")))
    assert host.lock(str(lock_path)) is False
    assert host._lock_fd is None

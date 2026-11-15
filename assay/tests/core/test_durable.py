"""Durable writes use the kernel fsync syscall, outside os.fsync patching."""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from assay import _durable, cli, mutation, output


def test_durable_fsync_raw_syscall(tmp_path, monkeypatch):
    target = tmp_path / "durable"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, b"durable bytes\n")

        real_fsync = os.fsync

        def forbidden(fd):
            if fd == descriptor:
                raise AssertionError("durable writes must not call os.fsync")
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", forbidden)
        _durable.fsync(descriptor)
    finally:
        os.close(descriptor)


def test_fsync_preserves_errno_from_the_syscall(monkeypatch):
    def fail(_number, _descriptor):
        ctypes.set_errno(errno.EIO)
        return -1

    monkeypatch.setattr(_durable, "_syscall", fail)
    with pytest.raises(OSError) as caught:
        _durable.fsync(19)

    assert caught.value.errno == errno.EIO


def test_fsync_refuses_unknown_linux_syscall_architecture():
    with pytest.raises(OSError, match="unsupported Linux syscall architecture"):
        _durable._fsync_syscall_number("unsupported-test-architecture")


@pytest.mark.parametrize(("machine", "number"), [("x86_64", 74), ("aarch64", 82)])
def test_fsync_syscall_numbers_match_supported_architectures(machine, number):
    assert _durable._fsync_syscall_number(machine) == number


@pytest.mark.parametrize("pointer_bytes", [4])
def test_a_32_bit_userland_never_uses_the_64_bit_syscall_table(pointer_bytes):
    # i386 syscall 74 is sethostname and Darwin's is mprotect: a kernel-wide
    # machine name alone must not select the x86_64 number.
    with pytest.raises(OSError, match="32-bit userland"):
        _durable._fsync_syscall_number("x86_64", pointer_bytes=pointer_bytes)


def test_non_linux_platforms_use_os_fsync_without_proc_or_raw_syscall(monkeypatch):
    calls = []
    real_fsync = os.fsync
    monkeypatch.setattr(_durable, "_is_linux", lambda: False)
    monkeypatch.setattr(
        _durable, "_syscall", lambda *_args: pytest.fail("no Linux syscall off Linux")
    )
    monkeypatch.setattr(
        _durable,
        "_preload_shim_mapped",
        lambda: pytest.fail("no /proc mapping check off Linux"),
    )
    monkeypatch.setattr(
        os,
        "fsync",
        lambda fd: calls.append(fd) if fd == 19 else real_fsync(fd),
    )

    _durable.fsync(19)

    assert calls == [19]


def test_unknown_architecture_without_interposer_uses_os_fsync(monkeypatch):
    calls = []
    real_fsync = os.fsync
    monkeypatch.setattr(_durable.platform, "machine", lambda: "unknown-test-architecture")
    monkeypatch.setattr(_durable, "_preload_shim_mapped", lambda: False)
    monkeypatch.setattr(
        os,
        "fsync",
        lambda fd: calls.append(fd) if fd == 19 else real_fsync(fd),
    )

    _durable.fsync(19)

    assert calls == [19]


def test_unknown_architecture_with_interposer_refuses_os_fsync(monkeypatch):
    monkeypatch.setattr(_durable.platform, "machine", lambda: "unknown-test-architecture")
    monkeypatch.setattr(_durable, "_preload_shim_mapped", lambda: True)
    real_fsync = os.fsync
    monkeypatch.setattr(
        os,
        "fsync",
        lambda fd: pytest.fail("interposed fsync must be refused")
        if fd == 19
        else real_fsync(fd),
    )

    with pytest.raises(OSError, match="unsupported Linux syscall architecture"):
        _durable.fsync(19)


def test_shim_mapping_detector_distinguishes_a_real_mapping():
    assert _durable._shim_is_mapped("7f00 r-xp 0 /usr/lib/libeatmydata.so\n")
    assert not _durable._shim_is_mapped("7f00 r-xp 0 /usr/lib/libc.so.6\n")


@pytest.mark.parametrize(
    ("mappings", "expected"),
    [
        ("7f00 r-xp 0 /usr/lib/libeatmydata.so\n", True),
        ("7f00 r-xp 0 /usr/lib/libc.so.6\n", False),
    ],
)
def test_preload_mapping_reads_process_maps(monkeypatch, mappings, expected):
    monkeypatch.setattr(
        _durable.Path,
        "read_text",
        lambda _self, *, encoding=None: mappings,
    )

    assert _durable._preload_shim_mapped() is expected


def test_preload_mapping_read_failure_refuses_unknown_arch_fallback(monkeypatch):
    def unreadable(self, *, encoding=None):
        raise OSError(errno.EACCES, "maps denied")

    monkeypatch.setattr(_durable.Path, "read_text", unreadable)
    with pytest.raises(OSError, match="cannot read process mappings"):
        _durable._preload_shim_mapped()


def test_state_and_verdict_writes_use_durable(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(_durable, "fsync", calls.append)

    mutation._write_mutation_state_record(
        tmp_path / "mutation-state", {"candidate_id": "a" * 64}
    )
    destination = tmp_path / "verdict.json"
    reserved = output.reserve_verdict_output(str(destination), stdout=io.StringIO())
    try:
        reserved.emit("{}\n")
    finally:
        reserved.close()
    cli._ensure_pilot_state(
        tmp_path / "pilot-state",
        lane="package",
        selection_sha256="b" * 64,
        lock_held=True,
    )

    assert len(calls) == 5  # mutation state, verdict, and pilot state file+directory


def test_not_intercepted_under_preload_shim():
    library = ctypes.util.find_library("eatmydata")
    if library is None:
        pytest.skip("libeatmydata is not installed in this environment")
    project_root = Path(__file__).resolve().parents[2]
    source_root = project_root / "src"
    code = (
        "import errno; from assay import _durable; "
        "\ntry: _durable.fsync(-1)"
        "\nexcept OSError as exc:"
        "\n    assert exc.errno == errno.EBADF, exc"
        "\nelse: raise SystemExit('raw syscall was intercepted')"
    )
    environment = dict(os.environ)
    environment["LD_PRELOAD"] = library
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), environment.get("PYTHONPATH", "")) if part
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr

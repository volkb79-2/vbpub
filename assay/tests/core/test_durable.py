"""Durable writes use the kernel fsync syscall, outside os.fsync patching."""

from __future__ import annotations

import ctypes
import errno
import os

import pytest

from assay import _durable


def test_fsync_calls_the_kernel_even_when_os_fsync_is_replaced(tmp_path, monkeypatch):
    target = tmp_path / "durable"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, b"durable bytes\n")

        def forbidden(_fd):
            raise AssertionError("durable writes must not call os.fsync")

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

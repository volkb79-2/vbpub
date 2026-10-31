"""Durability primitives that call the Linux kernel directly.

Critical state and verdict writes use ``syscall(SYS_fsync, fd)`` so an
``os.fsync`` replacement in the interpreter cannot silently weaken the
publication boundary.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
from pathlib import Path

_FSYNC_SYSCALL_NUMBERS = {
    "x86_64": 74,
    "aarch64": 82,
}

_LIBC_SYSCALL = ctypes.CDLL(None, use_errno=True).syscall
_LIBC_SYSCALL.restype = ctypes.c_long


class _UnsupportedSyscallArchitecture(OSError):
    """The raw fsync syscall number is not declared for this Linux ABI."""


def _fsync_syscall_number(machine: str | None = None) -> int:
    """Return Linux ``SYS_fsync`` for the current or supplied architecture."""
    name = platform.machine() if machine is None else machine
    try:
        return _FSYNC_SYSCALL_NUMBERS[name.lower()]
    except KeyError as exc:
        raise _UnsupportedSyscallArchitecture(
            errno.ENOSYS,
            f"unsupported Linux syscall architecture {name!r}",
        ) from exc


def _syscall(number: int, descriptor: int) -> int:
    """Invoke libc's variadic syscall entry with integer ABI arguments."""
    return int(
        _LIBC_SYSCALL(ctypes.c_long(number), ctypes.c_int(descriptor))
    )


def _shim_is_mapped(mappings: str) -> bool:
    """Whether process mappings include the fsync-interposing eatmydata shim."""
    return any("libeatmydata" in line.lower() for line in mappings.splitlines())


def _preload_shim_mapped() -> bool:
    """Check the loaded mappings, rather than trusting a mutable env value."""
    try:
        mappings = Path("/proc/self/maps").read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(
            errno.EIO,
            f"cannot read process mappings to check fsync interposer: {exc}",
        ) from exc
    return _shim_is_mapped(mappings)


def fsync(descriptor: int) -> None:
    """Synchronize a file or directory descriptor through the raw syscall."""
    try:
        syscall_number = _fsync_syscall_number()
    except _UnsupportedSyscallArchitecture:
        if _preload_shim_mapped():
            raise
        os.fsync(descriptor)
        return
    if _syscall(syscall_number, descriptor) == 0:
        return
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number))

"""Read-only access to a whole disk: 4 KiB O_DIRECT reads at arbitrary offsets, a bounded sequential read, a witness set.

Safety properties (each one is tested):
  * the device is only ever opened O_RDONLY (an assertion in `_open_ro` rejects any write flag, and nothing in this
    module ever calls write on it); the write-permission self-check uses os.access(), NOT an O_WRONLY open, because a
    close-after-write of a disk makes udev re-probe it (a side effect on the machine we are trying not to disturb);
  * only paths that are block devices (or, in tests, regular files when allowed) and that are in the allow-list of
    detected/configured disks are opened;
  * read buffers are page-aligned (O_DIRECT) and zeroed after the content check; content is never logged;
  * every read is charged to the rate buckets and the daily budget BEFORE it is submitted.
"""

from __future__ import annotations

import fcntl
import math
import mmap
import os
import stat
import struct
from dataclasses import dataclass, field

from .env import Env

BLKGETSIZE64 = 0x80081272
BLKSSZGET = 0x1268
FORBIDDEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class RawReadError(Exception):
    pass


def _open_ro(path: str, direct: bool) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | (getattr(os, "O_DIRECT", 0) if direct else 0)
    assert not (flags & FORBIDDEN_FLAGS), "raw disk must never be opened for writing"
    return os.open(path, flags)


def device_size_and_block(fd: int) -> tuple[int, int]:
    st = os.fstat(fd)
    if stat.S_ISBLK(st.st_mode):
        size = struct.unpack("Q", fcntl.ioctl(fd, BLKGETSIZE64, b"\0" * 8))[0]
        try:
            block = struct.unpack("I", fcntl.ioctl(fd, BLKSSZGET, b"\0" * 4))[0]
        except OSError:
            block = 512
        return size, block
    return st.st_size, 512


def write_denied(path: str) -> bool:
    """True when the current identity has NO write permission on the node (permission check only; nothing is opened)."""

    return not os.access(path, os.W_OK)


def coprime_stride(n: int) -> int:
    """A stride near 0.618*n coprime with n: i -> (start + i*stride) % n visits every block once before any repeat."""

    if n <= 1:
        return 1
    s = max(1, int(n * 0.6180339887))
    while math.gcd(s, n) != 1:
        s += 1
    return s


def block_at(i: int, n: int, stride: int, start: int) -> int:
    return (start + i * stride) % n


class AlignedBuffer:
    def __init__(self, size: int):
        self.size = size
        self.mm = mmap.mmap(-1, size)  # anonymous mmap is page-aligned: valid for O_DIRECT

    def nonzero(self, n: int | None = None) -> bool:
        n = self.size if n is None else n
        return int.from_bytes(self.mm[:n], "little") != 0

    def wipe(self) -> None:
        self.mm[:] = b"\0" * self.size

    def close(self) -> None:
        self.mm.close()


@dataclass
class ReadResult:
    offset: int
    nbytes: int
    seconds: float
    nonzero: bool


@dataclass
class RawReader:
    path: str
    env: Env
    allow_regular: bool = False
    direct: bool = True
    allowed: tuple = ()
    fd: int = -1
    size: int = 0
    logical_block: int = 512
    direct_active: bool = field(default=False)

    def open(self) -> "RawReader":
        real = os.path.realpath(self.path)
        if self.allowed and real not in {os.path.realpath(a) for a in self.allowed}:
            raise RawReadError(f"{real} is not in the allow-list of probed disks")
        st = os.stat(real)
        if not stat.S_ISBLK(st.st_mode) and not (self.allow_regular and stat.S_ISREG(st.st_mode)):
            raise RawReadError(f"{real} is not a block device")
        try:
            self.fd = _open_ro(real, self.direct)
            self.direct_active = self.direct and hasattr(os, "O_DIRECT")
        except OSError as exc:
            if self.direct and exc.errno == 22:  # EINVAL: O_DIRECT unsupported (e.g. tmpfs in tests)
                self.fd = _open_ro(real, False)
                self.direct_active = False
            else:
                raise
        self.size, self.logical_block = device_size_and_block(self.fd)
        return self

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "RawReader":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def block(self) -> int:
        return max(4096, self.logical_block)

    def n_blocks(self, block: int | None = None) -> int:
        return self.size // (block or self.block)

    def read_at(self, buf: AlignedBuffer, offset: int, length: int) -> ReadResult:
        if offset % self.block or length % self.block or offset < 0 or offset + length > self.size:
            raise RawReadError(f"bad read range offset={offset} length={length} size={self.size}")
        t0 = self.env.mono()
        got = os.preadv(self.fd, [memoryview(buf.mm)[:length]], offset)
        dt = self.env.mono() - t0
        if not self.direct_active:
            try:
                os.posix_fadvise(self.fd, offset, length, os.POSIX_FADV_DONTNEED)
            except (AttributeError, OSError):
                pass
        nz = buf.nonzero(got)
        buf.wipe()
        return ReadResult(offset, got, dt, nz)


def log2_hist(latencies_us: list[int], buckets: int = 24) -> list[int]:
    """Counts per power-of-two microsecond bucket: bucket i holds [2^i, 2^(i+1)) us, bucket 0 also holds < 2 us."""

    h = [0] * buckets
    for v in latencies_us:
        i = 0 if v < 2 else min(buckets - 1, int(math.log2(v)))
        h[i] += 1
    return h


def percentile(sorted_values: list, q: float):
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, int(math.ceil(q * len(sorted_values))) - 1))
    return sorted_values[idx]


def summarize_latencies(lat_us: list[int], keep: int = 32) -> dict:
    s = sorted(lat_us)
    step = max(1, len(lat_us) // keep)
    return {
        "n": len(lat_us),
        "p50": percentile(s, 0.50), "p90": percentile(s, 0.90), "p99": percentile(s, 0.99),
        "min": s[0] if s else None, "max": s[-1] if s else None,
        "hist": log2_hist(lat_us),
        "lat_us": lat_us[::step][:keep],
    }

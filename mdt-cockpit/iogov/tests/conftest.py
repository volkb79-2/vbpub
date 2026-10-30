"""Fixtures: fake clock/env, a fake sysfs+procfs+cgroup tree, and a regular file standing in for a raw disk.

Nothing here touches a real device: the "disk" is a temp file, sysfs/procfs/cgroup are temp trees.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # mdt-cockpit/ so `import iogov` works from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))

from iogov.config import MiB, Config, from_mapping  # noqa: E402
from iogov.env import Env  # noqa: E402


class FakeClock:
    """Deterministic time. `mono()` advances `step` per call, so every measured IO lasts `step` seconds (set it high for slow IO)."""

    def __init__(self, start: float = 1_790_000_000.0):
        self.t = start
        self.step = 1e-4
        self.slept: list[float] = []
        self.on_sleep = None

    def wall(self) -> float:
        return self.t

    def mono(self) -> float:
        self.t += self.step
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s
        if self.on_sleep:
            self.on_sleep(s)


def make_env(seed: int = 1, clock: FakeClock | None = None) -> tuple[Env, FakeClock]:
    clock = clock or FakeClock()
    rng = random.Random(seed)

    def urandom(n: int) -> bytes:
        return bytes((b % 255) + 1 for b in rng.randbytes(n))  # never a zero byte

    return Env(wall=clock.wall, mono=clock.mono, sleep=clock.sleep, rng=rng, urandom=urandom), clock


@pytest.fixture
def env():
    e, c = make_env()
    e.clock = c
    return e


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


_CACHE: dict[int, bytes] = {}


def _disk_bytes(size: int) -> bytes:
    if size not in _CACHE:
        rng = random.Random(7)
        _CACHE[size] = bytes((b % 255) + 1 for b in rng.randbytes(size))
    return _CACHE[size]


class World:
    """A temp universe: fake /sys, /proc, cgroup tree, a fake disk file, state and scratch dirs."""

    def __init__(self, root: Path):
        self.root = root
        self.sys = root / "sys"
        self.proc = root / "proc"
        self.cg = root / "cg"
        self.dev = root / "dev"
        self.state = root / "state"
        self.scratch = root / "scratch"
        self.disk = self.dev / "vda"
        self.set_pressure(0.0, 0.0)
        write_file(self.proc / "loadavg", "0.10 0.10 0.10 1/100 123\n")
        for sub, nr in (("queue/nr_requests", "16"), ("queue/max_sectors_kb", "64"), ("queue/scheduler", "none [mq-deadline] kyber\n"),
                        ("stat", self._stat(0, 0)), ("inflight", "       0        0\n"), ("size", "0\n")):
            write_file(self.sys / "class/block/vda" / sub, nr if "\n" in nr else nr + "\n")
        self.make_disk(16 * MiB)
        for name in ("dev.slice", "game.slice"):
            write_file(self.cg / name / "io.stat", "8:0 rbytes=0 wbytes=0 rios=0 wios=0 dbytes=0 dios=0\n")
            write_file(self.cg / name / "io.pressure", "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        write_file(self.cg / "io.cost.model", "8:0 ctrl=user model=linear rbps=1 rseqiops=1\n")

    @staticmethod
    def _stat(wios: int, wsectors: int) -> str:
        return f"0 0 0 0 {wios} 0 {wsectors} {wios} 0 0 0 0 0 0 0 0 0\n"

    def set_pressure(self, some: float, full: float) -> None:
        write_file(self.proc / "pressure/io",
                   f"some avg10={some:.2f} avg60=0.00 avg300=0.00 total=0\nfull avg10={full:.2f} avg60=0.00 avg300=0.00 total=0\n")

    def set_inflight(self, n: int) -> None:
        write_file(self.sys / "class/block/vda/inflight", f"{n} 0\n")

    def set_cgroup_pressure(self, name: str, some: float) -> None:
        write_file(self.cg / name / "io.pressure",
                   f"some avg10={some:.2f} avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")

    def make_disk(self, size: int, zero_ranges=()) -> None:
        self.dev.mkdir(parents=True, exist_ok=True)
        data = bytearray(_disk_bytes(size))
        for a, b in zero_ranges:
            data[a:b] = b"\0" * (b - a)
        self.disk.write_bytes(bytes(data))
        write_file(self.sys / "class/block/vda/size", f"{size // 512}\n")

    def config(self, **over) -> Config:
        base = dict(
            state_dir=str(self.state), scratch_dir=str(self.scratch), kill_switch_file=str(self.root / "etc/disabled"),
            sysfs_root=str(self.sys), procfs_root=str(self.proc), cgroup_root=str(self.cg), dev_root=str(self.dev),
            docker_containers_dir=str(self.root / "docker"),
            devices=[str(self.disk)], governed_cgroups=[str(self.cg / "dev.slice")], protected_cgroups=[str(self.cg / "game.slice")],
            allow_regular_files_as_devices=True, scratch_fs_allowed=[],
            region_default=8 * MiB, region_min=1 * MiB, region_hard_cap=16 * MiB, region_disk_fraction=1.0,
            free_reserve_bytes=0, free_reserve_fraction=0.0,
            probe_bytes_cap=2 * MiB, escalation_bytes_cap=4 * MiB, sync_every_bytes=512 * 1024, block_bytes=64 * 1024,
            sync4k_samples=32, seq_read_bytes=1 * MiB, seq_read_block=128 * 1024,
            read_samples_per_tick=6, read_max_attempts=12, witness_reads=2, quiet_probe_s=0.5,
            write_bps_max=float(1024 * MiB), read_bps_max=float(1024 * MiB), read_iops_max=200.0, sync4k_iops_max=500.0,
            loadavg_per_cpu_max=1000.0,
        )
        base.update(over)
        return from_mapping(base)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)

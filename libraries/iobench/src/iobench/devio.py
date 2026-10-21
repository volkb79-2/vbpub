"""Typed builders for every device-touching action (IOB-ARC-05) and fio parsing.

Nothing here executes anything.  A builder returns an argv; ``is_allowed_argv``
is the allowlist the tests hold every ``Host.run``/``spawn`` argv against.  There
is deliberately no builder for ``fstrim``, ``mkfs*``, ``wipefs`` without ``-n``,
``mount``, ``umount``, ``dd of=``, ``systemctl set-property`` or ``daemon-reload``.
"""
from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, replace
from typing import Any, Sequence

from .errors import Refusal
from .host import TAIL_BYTES, Host, RunResult

PERCENTILES = (50.0, 90.0, 95.0, 99.0, 99.9)
HIST_BINS = 1856
_RW = ("read", "write", "randread", "randwrite")


@dataclass(frozen=True)
class FioJob:
    name: str
    filename: str
    rw: str
    bs: int
    iodepth: int
    runtime_s: int
    offset: int = 0
    size: int = 0
    io_size: int | None = None
    direct: bool = True
    log_avg_ms: int = 2000
    metric: str = "iops"  # "iops" | "bw": which fio log carries the rate series
    hist: bool = False
    rate_iops: int | None = None
    fsync: int | None = None
    numjobs: int = 1
    prefix: str = ""

    @property
    def writes(self) -> bool:
        return self.rw in ("write", "randwrite")

    @property
    def random(self) -> bool:
        return self.rw.startswith("rand")

    @property
    def ddir(self) -> str:
        return "write" if self.writes else "read"

    def with_(self, **kw: Any) -> "FioJob":
        return replace(self, **kw)

    def argv(self) -> list[str]:
        if self.rw not in _RW:
            raise ValueError(f"unsupported rw {self.rw!r}")
        a = [
            "fio", f"--name={self.name}", f"--filename={self.filename}", f"--rw={self.rw}",
            f"--bs={self.bs}", f"--iodepth={self.iodepth}", "--ioengine=libaio",
            f"--direct={1 if self.direct else 0}", "--time_based", f"--runtime={self.runtime_s}",
            f"--offset={self.offset}", f"--size={self.size}", f"--numjobs={self.numjobs}",
            "--group_reporting", "--output-format=json",
            "--percentile_list=" + ":".join(str(p) for p in PERCENTILES),
            f"--log_avg_msec={self.log_avg_ms}", f"--write_{self.metric}_log={self.prefix}",
        ]
        if not self.writes:
            a.append("--readonly")
        if self.io_size is not None:
            a.append(f"--io_size={self.io_size}")
        if self.hist:
            a += [f"--write_hist_log={self.prefix}", "--log_hist_msec=1000", "--log_hist_coarseness=0"]
        if self.rate_iops is not None:
            a.append(f"--rate_iops={self.rate_iops}")
        if self.fsync is not None:
            a.append(f"--fsync={self.fsync}")
        return a

    @property
    def log_path(self) -> str:
        return f"{self.prefix}_{self.metric}.1.log"

    @property
    def hist_path(self) -> str:
        return f"{self.prefix}_clat_hist.1.log"

    @property
    def end(self) -> int:
        return self.offset + self.size


def parse_job(argv: Sequence[str]) -> FioJob:
    """Inverse of :meth:`FioJob.argv`; used by the confinement checks and FakeFio."""
    if posixpath.basename(argv[0]) != "fio":
        raise ValueError("not a fio argv")
    opts: dict[str, str] = {}
    for token in argv[1:]:
        key, _, value = token[2:].partition("=")
        opts[key] = value
    metric = "bw" if "write_bw_log" in opts else "iops"
    return FioJob(
        name=opts["name"], filename=opts["filename"], rw=opts["rw"], bs=int(opts["bs"]),
        iodepth=int(opts["iodepth"]), runtime_s=int(opts["runtime"]), offset=int(opts["offset"]),
        size=int(opts["size"]), io_size=int(opts["io_size"]) if "io_size" in opts else None,
        direct=opts["direct"] == "1", log_avg_ms=int(opts["log_avg_msec"]), metric=metric,
        hist="write_hist_log" in opts, rate_iops=int(opts["rate_iops"]) if "rate_iops" in opts else None,
        fsync=int(opts["fsync"]) if "fsync" in opts else None, numjobs=int(opts["numjobs"]),
        prefix=opts[f"write_{metric}_log"],
    )


# -- other typed builders ---------------------------------------------------
def fio_version_argv() -> list[str]:
    return ["fio", "--version"]


def blkdiscard_argv(device: str, offset: int, length: int) -> list[str]:
    return ["blkdiscard", "-o", str(offset), "-l", str(length), device]


def blkid_argv(device: str) -> list[str]:
    return ["blkid", "-p", "-o", "export", device]


def wipefs_check_argv(device: str) -> list[str]:
    return ["wipefs", "-n", "-p", device]


def swapon_show_argv() -> list[str]:
    return ["swapon", "--show=NAME,TYPE,SIZE", "--noheadings", "--raw"]


def fallocate_argv(path: str, size: int) -> list[str]:
    return ["fallocate", "-l", str(size), path]


def punch_hole_argv(path: str, offset: int, length: int) -> list[str]:
    return ["fallocate", "-p", "-o", str(offset), "-l", str(length), path]


def docker_ps_argv() -> list[str]:
    return ["docker", "ps", "--format", "{{.Names}}"]


def unit_active_argv(unit: str) -> list[str]:
    return ["systemctl", "is-active", unit]


def unit_loadstate_argv(unit: str) -> list[str]:
    return ["systemctl", "show", "-p", "LoadState", "--value", unit]


def probe(host: Host, name: str, argv: Sequence[str], ok: Sequence[int] = (0,)) -> RunResult:
    """Run a read-only probe; a return code outside ``ok`` is a refusal naming the probe (never "nothing found")."""
    res = host.run(argv)
    if res.returncode not in ok:
        raise Refusal("probe_failed", f"the {name} probe failed (exit {res.returncode}: {res.stderr.strip() or 'no message'}); "
                      "its silence cannot be read as 'nothing found'", f"fix {name} (run as root, binary on PATH) and rerun")
    return res


def probe_unit_argv(unit: str, slice_name: str, runtime_max_s: int, script: str, args: Sequence[str]) -> list[str]:
    """Transient unit for the sync-latency probe: no IO limits, bounded lifetime."""
    return ["systemd-run", f"--unit={unit}", f"--slice={slice_name}", "--collect",
            "-p", f"RuntimeMaxSec={runtime_max_s}", "python3", "-c", script, *args]


_ALLOWED = {
    "fio": lambda a: True,
    "blkdiscard": lambda a: a[1] == "-o" and a[3] == "-l" and len(a) == 6,
    "blkid": lambda a: a[1:4] == ["-p", "-o", "export"],
    "wipefs": lambda a: a[1] == "-n",
    "swapon": lambda a: a[1].startswith("--show"),
    "fallocate": lambda a: a[1] in ("-l", "-p"),
    "docker": lambda a: a[1] == "ps",
    "systemctl": lambda a: a[1] == "is-active" or a[1:3] == ["show", "-p"],
    "systemd-run": lambda a: any(t.startswith("RuntimeMaxSec=") for t in a),
}


def is_allowed_argv(argv: Sequence[str]) -> bool:
    check = _ALLOWED.get(posixpath.basename(argv[0]))
    return bool(check and check(list(argv)))


# -- fio output ---------------------------------------------------------------
@dataclass(frozen=True)
class FioStats:
    ddir: str
    iops: float
    bw_bps: float
    io_bytes: int
    runtime_s: float
    clat_mean_us: float
    pct_us: dict[str, float]


@dataclass(frozen=True)
class FioResult:
    job: FioJob
    returncode: int
    stats: FioStats | None
    series: list[tuple[float, float]]
    hist: list[tuple[float, list[int]]] | None
    started: float
    error: str = ""


def parse_fio_json(text: str, ddir: str) -> FioStats:
    doc = json.loads(text)
    job = doc["jobs"][0][ddir]
    clat = job["clat_ns"]
    return FioStats(
        ddir=ddir, iops=float(job["iops"]), bw_bps=float(job["bw_bytes"]), io_bytes=int(job["io_bytes"]),
        runtime_s=job["runtime"] / 1000.0, clat_mean_us=float(clat["mean"]) / 1000.0,
        pct_us={k: v / 1000.0 for k, v in clat["percentile"].items()},
    )


def parse_rate_log(text: str, metric: str) -> list[tuple[float, float]]:
    """fio avg log lines ``msec, value, ddir, bs, offset`` -> ``(t_s, rate)``.

    The bw log is in KiB/s; the series is always returned in bytes/s or IOPS."""
    scale = 1024.0 if metric == "bw" else 1.0
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        out.append((int(parts[0]) / 1000.0, float(parts[1]) * scale))
    return out


def parse_hist_log(text: str) -> list[tuple[float, list[int]]]:
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        out.append((int(parts[0]) / 1000.0, [int(p) for p in parts[3:]]))
    return out


def bin_value_ns(idx: int) -> float:
    """Latency of a fio histogram bin (fio's ``plat_idx_to_val``, 6 plat bits)."""
    if idx < 128:
        return float(idx)
    error_bits = (idx >> 6) - 1
    base = 1 << (error_bits + 6)
    return base + ((idx % 64) + 0.5) * (1 << error_bits)


def bin_value_us(idx: int) -> float:
    return bin_value_ns(idx) / 1000.0


def collect(host: Host, job: FioJob, run: RunResult, started: float) -> FioResult:
    """Parse a finished fio run into a :class:`FioResult` (stats, series, histogram)."""
    if run.returncode != 0:
        return FioResult(job, run.returncode, None, [], None, started, run.stderr.strip())
    stats = parse_fio_json(run.stdout, job.ddir)
    series = parse_rate_log(host.read_text(job.log_path), job.metric)
    hist = parse_hist_log(host.read_text(job.hist_path)) if job.hist else None
    return FioResult(job, 0, stats, series, hist, started)


def tail_start(size: int) -> int:
    return size - TAIL_BYTES

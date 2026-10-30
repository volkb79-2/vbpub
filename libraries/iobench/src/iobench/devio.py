"""Typed builders for every device-touching action (IOB-ARC-05) and fio parsing.

Nothing here executes anything.  A builder returns an argv; ``is_allowed_argv``
is the allowlist the tests hold every ``Host.run``/``spawn`` argv against.  There
is deliberately no builder for ``fstrim``, ``mkfs*``, ``wipefs`` without ``-n``,
``mount``, ``umount``, ``dd of=``, ``systemctl set-property`` or ``daemon-reload``.
"""
from __future__ import annotations

import json
import math
import posixpath
from dataclasses import dataclass, replace
from typing import Any, Sequence

from .errors import Refusal
from .series import IntegrityError
from .host import TAIL_BYTES, Host, HostError, RunResult, TargetRef, is_allowed_argv  # noqa: F401  (is_allowed_argv re-exported)

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
    exclusive: bool = False  # device targets: open with O_EXCL so the kernel refuses a mounted, swap-active or claimed device (TOCTOU, review A-S2)

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
        if self.exclusive:
            a.append("--exclusive_open=1")
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
        prefix=opts[f"write_{metric}_log"], exclusive=opts.get("exclusive_open") == "1",
    )


# -- other typed builders ---------------------------------------------------
def fio_version_argv() -> list[str]:
    return ["fio", "--version"]


def fio_option_help_argv(option: str) -> list[str]:
    return ["fio", f"--cmdhelp={option}"]


def require_fio_option(host: Host, option: str) -> None:
    """Fail closed: refuse unless the installed fio documents ``option`` (return code checked AND the option named in the answer).

    An unknown option makes fio abort the job at its first open; dropping the option silently would lose the protection it gives."""
    res = probe(host, f"fio --cmdhelp={option}", fio_option_help_argv(option))
    documented = any(line.strip().startswith(option) for line in res.stdout.splitlines()) and "Bad option" not in res.stdout
    if not documented:
        raise Refusal("fio_option_unsupported", f"the installed fio does not document the option {option!r}; iobench needs it to open a device exclusively",
                      "install a newer fio (>= 3.30) that supports it; iobench never runs a device job without it")


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
    try:
        res = host.run(argv)
    except OSError as exc:  # the binary is missing or not executable: the same silence, the same answer (never a traceback)
        raise Refusal("probe_failed", f"the {name} probe could not run ({exc.strerror or exc}); its absence cannot be read as 'nothing found'",
                      f"install {posixpath.basename(argv[0])} (binary on PATH, run as root) and rerun") from exc
    if res.returncode not in ok:
        raise Refusal("probe_failed", f"the {name} probe failed (exit {res.returncode}: {res.stderr.strip() or 'no message'}); "
                      "its silence cannot be read as 'nothing found'", f"fix {name} (run as root, binary on PATH) and rerun")
    return res


def unit_state_argv(unit: str) -> list[str]:
    return ["systemctl", "show", "-p", "ActiveState", "--value", unit]


def unit_stop_argv(unit: str) -> list[str]:
    return ["systemctl", "stop", unit]


def probe_unit_argv(unit: str, slice_name: str, runtime_max_s: int, script: str, args: Sequence[str]) -> list[str]:
    """Transient unit for the sync-latency probe: no IO limits, bounded lifetime.

    ``--wait`` makes systemd-run return only when the unit has finished (its exit status is the unit's): the probe script writes its
    output file at the very end, so without it the caller would read the file before it exists (review A N2)."""
    return ["systemd-run", "--wait", f"--unit={unit}", f"--slice={slice_name}", "--collect",
            "-p", f"RuntimeMaxSec={runtime_max_s}", "python3", "-c", script, *args]


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
        t, v = int(parts[0]) / 1000.0, float(parts[1]) * scale
        if not (math.isfinite(t) and math.isfinite(v)):
            raise IntegrityError(f"IOB-PLA-14: non-finite value in the fio log line {line!r}")
        out.append((t, v))
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


# -- runtime bindings (review A-S1) ---------------------------------------------------------------------
def assert_writer_bound(argv: Sequence[str], target: TargetRef, offset: int, length: int) -> FioJob:
    """The argv a Host is about to spawn writes exactly the extent it was authorised for, on the authorised target, outside the tail."""
    job = parse_job(argv)
    if job.filename != target.path or job.offset != offset or job.size != length:
        raise HostError("writer extent differs from the extent it was authorised for")
    if target.kind == "device" and not job.exclusive:  # the kernel's O_EXCL refusal (mounted / swap / claimed) must be armed for every device writer
        raise HostError(f"writer on device {target.path} without --exclusive_open=1")
    if length <= 0 or offset < 0 or job.end > target.size_bytes - TAIL_BYTES:
        raise HostError(f"writer extent [{offset}, {offset + length}) overlaps the reserved tail or the end of {target.path}")
    return job


def assert_discard_bound(argv: Sequence[str], target: TargetRef, offset: int, length: int) -> None:
    """The discard / hole-punch argv is exactly what the typed builder produces for the authorised extent."""
    build = blkdiscard_argv if target.kind == "device" else punch_hole_argv
    if list(argv) != build(target.path, offset, length):
        raise HostError("discard argv differs from the extent it was authorised for")

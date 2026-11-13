"""Bounded, resumable host driver for the iobench ladder error study.

This file deliberately uses only the Python standard library.  It is run on the
study host as root; the repository contains no SSH client or host launcher.
"""
from __future__ import annotations

import argparse
import datetime as dt
import errno
import json
import math
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
JOB_TEMPLATE = HERE / "job-template.fio"
STATE_NAME = "ibx-study-state.json"
SCHEMA = 2
STATE_SCHEMA = 3
RUN_MANIFEST_SCHEMA = 2
DEFAULT_LIVENESS_PROBE_INTERVAL_S = 10.0
LIVENESS_ROOT_DIRECTORY = "/"
LIVENESS_SSH_HOST = "127.0.0.1"
LIVENESS_SSH_PORT = 22
LIVENESS_COCKPIT_UNIT = "mdt-cockpit-runner.service"
LIVENESS_COCKPIT_CONTAINER = "mdt-cockpit-runner"
LIVENESS_ACTIVE_UNIT_STATES = {"active", "activating", "deactivating", "reloading"}
GIB = 1024**3
GB = 10**9
WRITE_CAP_GB = 500
WRITE_CAP = WRITE_CAP_GB * GB
ACTIVE_CAP_S = 3 * 60 * 60
ELAPSED_CAP_S = 12 * 60 * 60
FIO_DRAIN_RESERVE_S = 60


class StudyError(RuntimeError):
    """A deliberate refusal or an invalid study record."""


@dataclass(frozen=True)
class Job:
    name: str
    rw: str
    bs: int
    qd: int
    runtime_s: int
    value: str
    write_cap: int = 0
    rate_fraction: float | None = None
    extent_bytes: int | None = None
    discard_before: bool = False
    wait_before_s: int = 0
    read_written_extent: bool = False

    @property
    def writes(self) -> bool:
        return self.rw in ("write", "randwrite")


@dataclass(frozen=True)
class TargetIdentity:
    """Identity facts that bind a raw run and its resume state to one partition."""

    size_bytes: int
    partuuid: str

    def as_json(self) -> dict[str, Any]:
        return {"size_bytes": self.size_bytes, "partuuid": self.partuuid}


@dataclass(frozen=True)
class TargetValidation:
    """Target identity plus the root-disk facts needed to interpret a study run."""

    identity: TargetIdentity | None
    system_disk: dict[str, Any] | None = None


def system_disk_acknowledgement(hostname: str, disk: str, root_devices: Iterable[str],
                                partuuid: str) -> str:
    """Format a per-run drift-check token from facts read from the running host."""
    roots = sorted(set(root_devices))
    if (not isinstance(hostname, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", hostname) or
            not isinstance(disk, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", disk) or not roots or
            any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
                for name in roots) or not isinstance(partuuid, str) or
            not re.fullmatch(r"[A-Fa-f0-9-]+", partuuid)):
        raise StudyError("cannot format system-disk acknowledgement from malformed host facts")
    return f"{hostname}:{disk}:{','.join(roots)}:{partuuid}"


LIVENESS_PROBE_SCRIPT = r'''
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

limit = float(sys.argv[1])
root_directory = sys.argv[2]
ssh_host = sys.argv[3]
ssh_port = int(sys.argv[4])
cockpit_unit = sys.argv[5]
cockpit_container = sys.argv[6]
components = {}
try:
    started = time.monotonic()
    fd, path = tempfile.mkstemp(prefix=".ibx-liveness-", dir=root_directory)
    try:
        os.write(fd, b"ibx host liveness probe\n")
        os.fsync(fd)
    finally:
        os.close(fd)
        os.unlink(path)
    components["root_fsync_seconds"] = time.monotonic() - started

    started = time.monotonic()
    with socket.create_connection((ssh_host, ssh_port), timeout=limit) as connection:
        connection.settimeout(limit)
        if not connection.recv(255).startswith(b"SSH-2.0-"):
            raise RuntimeError("sshd did not return an SSH protocol banner")
    components["sshd_seconds"] = time.monotonic() - started

    started = time.monotonic()
    result = subprocess.run(
        ["systemctl", "show", cockpit_unit, "--property=ActiveState", "--value"],
        text=True, capture_output=True, check=False, timeout=limit,
    )
    if result.returncode or result.stdout.strip() != "active":
        raise RuntimeError(f"{cockpit_unit} is not active")
    result = subprocess.run(
        ["docker", "exec", cockpit_container, "true"],
        text=True, capture_output=True, check=False, timeout=limit,
    )
    if result.returncode:
        raise RuntimeError(f"{cockpit_container} container did not respond to exec")
    components["cockpit_seconds"] = time.monotonic() - started
    print(json.dumps({"responsive": True, "components": components}))
except Exception as exc:
    print(json.dumps({"responsive": False, "components": components,
                      "error": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(1)
'''


def validate_liveness_probe(probe: Any) -> dict[str, Any]:
    if not isinstance(probe, Mapping):
        raise StudyError("host-liveness probe data is incomplete or malformed")
    if not isinstance(probe.get("responsive"), bool):
        raise StudyError("host-liveness probe data is incomplete or malformed")
    latency = probe.get("latency_seconds")
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        raise StudyError("host-liveness probe data is incomplete or malformed")
    if not math.isfinite(latency) or latency < 0:
        raise StudyError("host-liveness probe data is incomplete or malformed")
    components = probe.get("components")
    if not isinstance(components, dict):
        raise StudyError("host-liveness probe data is incomplete or malformed")
    return {"responsive": probe["responsive"], "latency_seconds": float(latency),
            "components": components, "error": probe.get("error")}


RUNG = {
    "veryquick": {"runtime": 210, "write_gb": 15, "burst_s": 20, "burst_gb": 8,
                  "fill_gb": 4, "seq_read_s": 12, "rand_s": 12, "seq4k_s": 0,
                  "seq128k_s": 12, "lat_s": 10, "canary": 0},
    "quick": {"runtime": 600, "write_gb": 26, "burst_s": 30, "burst_gb": 12,
              "fill_gb": 5, "seq_read_s": 30, "rand_s": 30, "seq4k_s": 30,
              "seq128k_s": 20, "lat_s": 20, "canary": 1},
    "standard": {"runtime": 2400, "write_gb": 100, "burst_s": 600, "burst_gb": 64,
                 "fill_gb": 8, "seq_read_s": 90, "rand_s": 90, "seq4k_s": 90,
                 "seq128k_s": 60, "lat_s": 60, "canary": 3},
}

CELL_SETS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "raw-t0": ("raw", ("standard", "quick", "veryquick"), "t0"),
    "raw-t3h": ("raw", ("veryquick", "quick"), "t3h"),
    "raw-t6h": ("raw", ("quick", "veryquick", "standard"), "t6h"),
    "raw-t9h": ("raw", ("veryquick", "quick"), "t9h"),
    "raw-t12h": ("raw", ("quick", "veryquick"), "t12h"),
    "raw-t12h2": ("raw", ("quick", "veryquick"), "t12h2"),
    "file-ext4": ("ext4-file", ("quick", "veryquick"), "t9h"),
    "file-btrfs": ("btrfs-file", ("quick", "veryquick"), "t9h"),
    "file-ext4-standard": ("ext4-file", ("standard",), "t9h"),
    "file-btrfs-standard": ("btrfs-file", ("standard",), "t9h"),
    "part-c": ("raw", ("part-c",), "t1h"),
    "part-c-repeat": ("raw", ("part-c",), "t4h"),
}

ADAPTIVE_REPEAT_CELL_SETS = ("part-c-repeat", "raw-t12h", "raw-t12h2")
FILE_STANDARD_CELL_SETS = ("file-ext4-standard", "file-btrfs-standard")
REQUIRED_CELL_SETS_AFTER_OPTIONAL = {
    "file-ext4-standard": ("file-btrfs",),
}


def rung_jobs(rung: str, *, part_c: bool = False) -> list[Job]:
    """Return section 5's nested cells, with write caps later bounded to rung total."""
    if part_c:
        rows = [
            Job("c-pass1-seq", "write", 1024**2, 32, 600, "c_seq_bps", 8 * GIB,
                extent_bytes=8 * GIB, discard_before=True),
            Job("c-pass1-rand", "randwrite", 4096, 32, 30, "c_rand_iops", 256 * 1024**2),
            Job("c-pass2-seq", "write", 1024**2, 32, 600, "c_seq_bps", 8 * GIB, extent_bytes=8 * GIB),
            Job("c-pass2-rand", "randwrite", 4096, 32, 30, "c_rand_iops", 256 * 1024**2),
            Job("c-pass3-seq", "write", 1024**2, 32, 600, "c_seq_bps", 8 * GIB, extent_bytes=8 * GIB),
            Job("c-pass3-rand", "randwrite", 4096, 32, 30, "c_rand_iops", 256 * 1024**2),
            Job("c-pass4-seq", "write", 1024**2, 32, 600, "c_seq_bps", 8 * GIB, extent_bytes=8 * GIB),
            Job("c-pass4-rand", "randwrite", 4096, 32, 30, "c_rand_iops", 256 * 1024**2),
            Job("c-idle-probe", "randwrite", 4096, 32, 30, "idle_iops", 256 * 1024**2, wait_before_s=600),
            Job("c-idle-seq", "write", 1024**2, 32, 600, "c_seq_bps", 4 * GIB,
                extent_bytes=4 * GIB),
            Job("c-recover0-rand", "randwrite", 4096, 32, 30, "c_recovery_iops", 256 * 1024**2, discard_before=True),
            Job("c-recover0-seq", "write", 1024**2, 32, 120, "c_recovery_bps", 4 * GIB,
                extent_bytes=4 * GIB),
            Job("c-recover10-rand", "randwrite", 4096, 32, 30, "c_recovery_iops", 256 * 1024**2, wait_before_s=600),
            Job("c-recover10-seq", "write", 1024**2, 32, 120, "c_recovery_bps", 4 * GIB,
                extent_bytes=4 * GIB),
        ]
        return bound_write_caps(rows, 50 * GB)
    if rung not in RUNG:
        raise StudyError(f"unknown rung {rung!r}")
    spec = RUNG[rung]
    rows: list[Job] = [
        Job("seq1m-write", "write", 1024**2, 32, spec["burst_s"], "wbps", spec["burst_gb"] * GB),
        Job("fill", "write", 1024**2, 32, 10, "fill", spec["fill_gb"] * GB),
    ]
    if rung == "standard":
        for qd in (1, 8, 32, 128):
            rows.append(Job(f"rate-read-q{qd}", "randread", 4096, qd, 30, f"rate_read_q{qd}",
                            read_written_extent=True))
            rows.append(Job(f"rate-write-q{qd}", "randwrite", 4096, qd, 30, f"rate_write_q{qd}"))
    else:
        for qd in (1, 8, 32, 128):
            rows.append(Job(f"rate-read-q{qd}", "randread", 4096, qd, 5, f"rate_read_q{qd}",
                            read_written_extent=True))
        for qd in (1, 32):
            rows.append(Job(f"rate-write-q{qd}", "randwrite", 4096, qd, 5, f"rate_write_q{qd}"))
    rows.append(Job("seq1m-read", "read", 1024**2, 32, spec["seq_read_s"], "rbps",
                    read_written_extent=True))
    if spec["seq4k_s"]:
        rows.extend((
            Job("seq4k-read", "read", 4096, 32, spec["seq4k_s"], "rseqiops",
                read_written_extent=True),
            Job("seq4k-write", "write", 4096, 32, spec["seq4k_s"], "wseqiops"),
        ))
    rows.extend((
        Job("rand4k-read", "randread", 4096, 32, spec["rand_s"], "rrandiops",
            read_written_extent=True),
        Job("rand4k-write", "randwrite", 4096, 32, spec["rand_s"], "wrandiops"),
        Job("seq128k-write", "write", 128 * 1024, 32, spec["seq128k_s"], "w128kbps"),
        Job("latency-q1-read", "randread", 4096, 1, spec["lat_s"], "rlat",
            read_written_extent=True),
        Job("latency-q1-write", "randwrite", 4096, 1, spec["lat_s"], "wlat"),
    ))
    if rung == "standard":
        rows.insert(2, Job("anchor-start-randread", "randread", 4096, 32, 10, "anchor_start",
                           read_written_extent=True))
        for direction, rw, value in (("read", "randread", "latency_under_load_read"),
                                     ("write", "randwrite", "latency_under_load_write")):
            for pct in (0.3, 0.6, 0.9):
                rows.append(Job(f"load-{direction}-{int(pct * 100)}", rw, 4096, 32, 20,
                                f"latency_under_load_{direction}_{int(pct * 100)}",
                                rate_fraction=pct,
                                read_written_extent=direction == "read"))
        rows.append(Job("rand4k-write-observation", "randwrite", 4096, 32, 240,
                        "wrandiops_observation"))
    if spec["canary"]:
        passes = 1 if rung == "quick" else 3
        extent = (2 if rung == "quick" else 4) * GIB
        rows.append(Job("fresh4k-write", "randwrite", 4096, 1, 5, "canary_iops",
                        extent_bytes=extent, discard_before=True))
        for index in range(1, passes + 1):
            rows.append(Job(f"rewrite-pass-{index}", "write", 1024**2, 32,
                            45 if passes == 1 else 40, "canary_bps", extent,
                            extent_bytes=extent))
        rows.append(Job("discard-recovery4k-write", "randwrite", 4096, 1, 5, "discard_recovery_iops",
                        extent_bytes=extent, discard_before=True))
    if rung == "standard":
        rows.append(Job("anchor-end-randread", "randread", 4096, 32, 10, "anchor_end",
                        read_written_extent=True))
    return bound_write_caps(rows, spec["write_gb"] * GB)


def bound_write_caps(jobs: list[Job], total: int) -> list[Job]:
    """Give every write job an io_size and prove their planned sum fits the rung cap."""
    writers = [i for i, job in enumerate(jobs) if job.writes]
    if not writers:
        return jobs
    explicit = sum(jobs[i].write_cap for i in writers)
    if explicit > total:
        left = total
        out = list(jobs)
        for i in writers:
            cap = min(jobs[i].write_cap, left)
            out[i] = Job(**{**asdict(jobs[i]), "write_cap": cap})
            left -= cap
        return out
    missing = [i for i in writers if jobs[i].write_cap == 0]
    remaining = total - explicit
    total_weight = sum(jobs[i].runtime_s for i in missing)
    if missing and total_weight <= 0:
        raise StudyError("cannot allocate a write budget across zero-duration cells")
    extra = remaining
    out = list(jobs)
    for i in writers:
        cap = jobs[i].write_cap
        if cap == 0:
            cap = (remaining * jobs[i].runtime_s // total_weight) if i != missing[-1] else extra
            extra -= cap
        out[i] = Job(**{**asdict(jobs[i]), "write_cap": cap})
    return out


def base_schedule_write_cap() -> int:
    """Derive the base schedule's maximum, including conditional t0+6h standard."""
    cell_sets = ("raw-t0", "raw-t3h", "raw-t6h", "raw-t9h", "file-ext4", "file-btrfs", "part-c")
    total = 0
    for cell_set in cell_sets:
        _, rungs, _ = CELL_SETS[cell_set]
        for rung in rungs:
            jobs = rung_jobs("quick" if rung == "part-c" else rung, part_c=rung == "part-c")
            total += sum(job.write_cap for job in jobs if job.writes)
    return total


def cell_set_write_cap(cell_set: str) -> int:
    """Derive a complete cell-set's maximum writes from its actual fio job caps."""
    try:
        _, rungs, _ = CELL_SETS[cell_set]
    except KeyError as exc:
        raise StudyError(f"unknown cell-set {cell_set!r}") from exc
    total = 0
    for rung in rungs:
        jobs = rung_jobs("quick" if rung == "part-c" else rung, part_c=rung == "part-c")
        total += sum(job.write_cap for job in jobs if job.writes)
    return total


def pending_cell_set_write_cap(cell_set: str, *, out: Path,
                               state: Mapping[str, Any]) -> int:
    """Return the write cap still unpaid by missing result cells in a cell-set."""
    kind, rungs, slot = CELL_SETS[cell_set]
    pending = 0
    for rung in rungs:
        special = rung == "part-c"
        for job in rung_jobs("quick" if special else rung, part_c=special):
            key = cell_key(kind, slot, rung, job)
            filename = re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json"
            if key not in state["cells"] and not (out / "cells" / filename).exists():
                pending += job.write_cap
    return pending


def required_remainder_write_cap(optional_cell_set: str, *, out: Path,
                                 state: Mapping[str, Any]) -> int:
    """Return the unpaid write caps of required cell-sets following an optional set."""
    return sum(
        pending_cell_set_write_cap(cell_set, out=out, state=state)
        for cell_set in REQUIRED_CELL_SETS_AFTER_OPTIONAL.get(optional_cell_set, ())
    )


def adaptive_repeat_candidate_write_cap() -> int:
    """Return the total cap for every optional repeat, whether schedulable or not."""
    return sum(cell_set_write_cap(cell_set) for cell_set in ADAPTIVE_REPEAT_CELL_SETS)


def schedule_adaptive_repeats() -> tuple[str, ...]:
    """Keep only complete optional repeats that fit after the maximum base schedule."""
    base_cap = base_schedule_write_cap()
    if base_cap > WRITE_CAP:
        raise StudyError(f"maximum base schedule requires {base_cap} bytes; "
                         f"the write cap is {WRITE_CAP} bytes")
    remaining = WRITE_CAP - base_cap
    scheduled = []
    for cell_set in ADAPTIVE_REPEAT_CELL_SETS:
        needed = cell_set_write_cap(cell_set)
        if needed <= remaining:
            scheduled.append(cell_set)
            remaining -= needed
    return tuple(scheduled)


BASE_SCHEDULE_WRITE_CAP = base_schedule_write_cap()
SCHEDULED_ADAPTIVE_REPEAT_CELL_SETS = schedule_adaptive_repeats()
DROPPED_ADAPTIVE_REPEAT_CELL_SETS = tuple(
    cell_set for cell_set in ADAPTIVE_REPEAT_CELL_SETS
    if cell_set not in SCHEDULED_ADAPTIVE_REPEAT_CELL_SETS
)
ADAPTIVE_RESERVE_WRITE_CAP = sum(
    cell_set_write_cap(cell_set) for cell_set in SCHEDULED_ADAPTIVE_REPEAT_CELL_SETS
)


def adaptive_reserve_write_cap() -> int:
    """Return the complete write cap reserved for the repeats that fit this study."""
    return ADAPTIVE_RESERVE_WRITE_CAP


def file_standard_write_cap() -> int:
    """Return the maximum writer volume for one optional file-standard set."""
    return cell_set_write_cap(FILE_STANDARD_CELL_SETS[0])


def render_job(job: Job, *, target: str, offset: int, size: int, log_prefix: str,
               rate_iops: int | None = None) -> str:
    """Render the repository job template; bounded writers never combine time_based and io_size."""
    values = {
        "NAME": job.name, "FILENAME": target, "OFFSET": str(offset), "SIZE": str(size),
        "RW": job.rw, "BS": str(job.bs), "IODEPTH": str(job.qd),
        "RUNTIME": str(job.runtime_s), "LOG_PREFIX": log_prefix,
    }
    template = JOB_TEMPLATE.read_text(encoding="utf-8")
    text = template
    for key, value in values.items():
        if "\n" in value or "\r" in value or "=" in value:
            raise StudyError(f"unsafe fio template value for {key}")
        if key in ("FILENAME", "LOG_PREFIX") and not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
            raise StudyError(f"unsafe path for fio {key.lower()}")
        text = text.replace("{{" + key + "}}", value)
    lines = []
    for line in text.splitlines():
        if line == "time_based={{TIME_BASED}}":
            if job.writes:
                continue
            lines.append("time_based=1")
        elif line == "io_size={{IO_SIZE}}":
            if job.writes:
                lines.append(f"io_size={job.write_cap}")
        elif line == "rate_iops={{RATE_IOPS}}":
            if rate_iops:
                lines.append(f"rate_iops={rate_iops}")
        else:
            lines.append(line)
    rendered = "\n".join(lines) + "\n"
    if job.writes and ("time_based=1" in rendered or f"io_size={job.write_cap}" not in rendered):
        raise StudyError("fio writer is not volume bounded")
    return rendered


def parse_psi(text: str) -> dict[str, dict[str, float]]:
    parsed: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float] = {}
        try:
            for item in fields[1:]:
                key, raw = item.split("=", 1)
                values[key] = float(raw)
        except (ValueError, TypeError):
            continue
        if "avg10" in values and math.isfinite(values["avg10"]):
            parsed[fields[0]] = values
    return parsed


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z_()]+):\s+(\d+)\s+kB", line.strip())
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    if "MemAvailable" not in values:
        raise StudyError("MemAvailable is missing from /proc/meminfo")
    return values


def parse_diskstats(text: str) -> dict[str, dict[str, int]]:
    """Parse Linux diskstats, preserving sectors, discard, flush and timing counters."""
    output: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        try:
            nums = [int(value) for value in fields[:2]] + [int(value) for value in fields[3:]]
        except ValueError:
            continue
        row = {"major": nums[0], "minor": nums[1]}
        values = nums[2:]
        names = ("reads_completed", "reads_merged", "sectors_read", "read_ms", "writes_completed",
                 "writes_merged", "sectors_written", "write_ms", "in_flight", "io_ms", "weighted_io_ms")
        row.update({key: values[i] for i, key in enumerate(names) if i < len(values)})
        if len(values) >= 15:
            row.update({"discards_completed": values[11], "discards_merged": values[12],
                        "sectors_discarded": values[13], "discard_ms": values[14]})
        if len(values) >= 17:
            row.update({"flushes_completed": values[15], "flush_ms": values[16]})
        output[fields[2]] = row
    return output


def parse_fio_log(text: str, *, bandwidth: bool) -> list[dict[str, float]]:
    """Read fio's one-second CSV log; bandwidth values are KiB/s unless log_units=1."""
    samples = []
    for line in text.splitlines():
        fields = line.split(",")
        if len(fields) < 2:
            continue
        try:
            timestamp = float(fields[0])
            value = float(fields[1])
        except ValueError:
            continue
        samples.append({"time_ms": timestamp, "value": value * (1024 if bandwidth else 1)})
    return samples


def summarize_series(series: Mapping[str, list[dict[str, float]]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for kind, samples in series.items():
        values = [point["value"] for point in samples]
        tail = values[max(0, math.floor(len(values) * 0.75)):]
        early = [point["value"] for point in samples if point["time_ms"] <= 10_000]
        result[kind] = {"samples": len(values), "early_10s": median(early),
                        "steady_tail": median(tail), "raw": samples}
    return result


def monitor_sample(device: str, *, proc: Path = Path("/proc")) -> dict[str, Any]:
    disk = parse_diskstats((proc / "diskstats").read_text(encoding="utf-8"))
    io_psi = parse_psi((proc / "pressure/io").read_text(encoding="utf-8"))
    mem_psi = parse_psi((proc / "pressure/memory").read_text(encoding="utf-8"))
    meminfo = parse_meminfo((proc / "meminfo").read_text(encoding="utf-8"))
    if device not in disk:
        raise StudyError(f"diskstats has no row for {device!r}")
    required = ("discards_completed", "sectors_discarded", "flushes_completed", "flush_ms")
    if any(name not in disk[device] for name in required):
        raise StudyError(f"diskstats for {device!r} lacks discard/flush counters")
    return {"time": dt.datetime.now(dt.UTC).isoformat(), "device": device,
            "diskstats": disk[device], "psi": {"io": io_psi, "memory": mem_psi},
            "meminfo": meminfo}


def cgroup_path(text: str) -> str:
    for line in text.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            path = fields[2]
            if path.startswith("/") and ".." not in Path(path).parts:
                return path
    raise StudyError("cannot resolve the unified cgroup of the benchmark process")


def _io_max_active(text: str) -> bool:
    controls = {"rbps", "wbps", "riops", "wiops"}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if not re.fullmatch(r"\d+:\d+", fields[0]):
            raise StudyError("malformed io.max line")
        if len(fields) < 2:
            raise StudyError("malformed io.max line")
        for value in fields[1:]:
            if "=" not in value:
                raise StudyError("malformed io.max setting")
            name, amount = value.split("=", 1)
            if name not in controls or not (amount == "max" or amount.isdigit()):
                raise StudyError("unknown or malformed io.max control")
            if amount != "max":
                return True
    return False


def io_control_snapshot(*, cgroup_root: Path = Path("/sys/fs/cgroup"),
                        cgroup_text: str | None = None) -> dict[str, Any]:
    """Read the full benchmark cgroup chain and root io.cost state before and after a run."""
    if cgroup_text is None:
        cgroup_text = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    rel = cgroup_path(cgroup_text).lstrip("/")
    dirs = [cgroup_root]
    current = cgroup_root
    for part in rel.split("/") if rel else ():
        current = current / part
        dirs.append(current)
    snapshot: dict[str, Any] = {"cgroups": [], "io_cost": {}}
    for directory in dirs:
        files: dict[str, str] = {}
        for name in ("io.max", "io.weight", "io.latency"):
            path = directory / name
            if not path.is_file():
                raise StudyError(f"cannot read required cgroup IO control {path}")
            files[name] = path.read_text(encoding="utf-8").strip()
        if _io_max_active(files["io.max"]):
            raise StudyError(f"active io.max limit at {directory}")
        saw_default_weight = False
        for line in files["io.weight"].splitlines():
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "default" and len(fields) == 2:
                weights = fields[1:]
                saw_default_weight = True
            elif re.fullmatch(r"\d+:\d+", fields[0]) and len(fields) == 2:
                weights = fields[1:]
            else:
                raise StudyError(f"malformed io.weight at {directory}")
            if any(not value.isdigit() or int(value) != 100 for value in weights):
                raise StudyError(f"non-default io.weight at {directory}")
        if not saw_default_weight:
            raise StudyError(f"io.weight has no explicit default 100 at {directory}")
        if files["io.latency"]:
            raise StudyError(f"active io.latency setting at {directory}")
        snapshot["cgroups"].append({"path": str(directory), "files": files})
    for name in ("io.cost.qos", "io.cost.model"):
        path = cgroup_root / name
        if not path.is_file():
            raise StudyError(f"cannot assert io.cost state: {path} is missing")
        snapshot["io_cost"][name] = path.read_text(encoding="utf-8").strip()
    qos = snapshot["io_cost"]["io.cost.qos"]
    enables = re.findall(r"(?:^|\s)enable=([^\s]+)", qos)
    if "1" in enables:
        raise StudyError("io.cost qos is enabled")
    if enables != ["0"]:
        raise StudyError("io.cost qos must explicitly report enable=0")
    if snapshot["io_cost"]["io.cost.model"]:
        raise StudyError("io.cost model is configured")
    return snapshot


def adaptive_repeat_action(effect: float, noise: float, completed_runs: int) -> str:
    """Section 6.6 rule. ``noise`` already includes the 5%/10% floor."""
    if noise <= 0 or completed_runs < 1:
        raise StudyError("adaptive repeat needs positive noise and at least one completed run")
    magnitude = abs(effect)
    if magnitude >= 2 * noise:
        return "proven"
    if magnitude < noise:
        return "repeat-once" if completed_runs == 1 else "no-effect"
    if completed_runs < 3:
        return "repeat"
    return "small"


def needs_extra_repeat(values: list[float], reference: float, *, latency: bool = False) -> bool:
    """True when the newest scheduled repeat moved e by more than 20 percent."""
    if len(values) < 4 or reference == 0:
        return False
    before = max(abs(value / reference - 1) for value in values[:-1])
    after = max(abs(value / reference - 1) for value in values)
    floor = 0.10 if latency else 0.05
    return abs(after - before) / max(before, floor) > 0.20 + 1e-12


def noise_band(values: Iterable[float], *, latency: bool = False) -> float:
    nums = list(values)
    spread = 0.0 if len(nums) < 2 or min(nums) == 0 else max(nums) / min(nums) - 1
    return max(spread, 0.10 if latency else 0.05)


def parse_fio_values(payload: Mapping[str, Any], job: Job,
                     series: Mapping[str, list[dict[str, float]]] | None = None) -> tuple[float, int]:
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != 1 or not isinstance(jobs[0], dict):
        raise StudyError("fio JSON must contain exactly one job")
    stats = jobs[0].get("write" if job.writes else "read")
    if not isinstance(stats, dict):
        raise StudyError("fio JSON is missing the expected direction statistics")
    bytes_done = stats.get("io_bytes")
    if isinstance(bytes_done, bool) or not isinstance(bytes_done, int) or bytes_done < 0:
        raise StudyError("fio JSON has malformed io_bytes")
    if job.writes and bytes_done > job.write_cap:
        raise StudyError("fio wrote more than the cell's io_size cap")
    if (job.value in ("rlat", "wlat", "latency_under_load_read", "latency_under_load_write")
            or job.value.startswith("latency_under_load_")):
        percentiles = stats.get("clat_ns", {}).get("percentile", {})
        if job.value.startswith("latency_under_load"):
            wanted = next((percentiles[name] for name in ("99.000000", "95.000000")
                           if name in percentiles), None)
        else:
            wanted = next((percentiles[name] for name in ("95.000000", "99.000000")
                           if name in percentiles), None)
        if wanted is None:
            raise StudyError("fio JSON has no clat_ns percentile for latency cell")
        value = float(wanted)
        if not math.isfinite(value) or value < 0:
            raise StudyError("fio JSON has malformed latency percentile")
        return value, bytes_done
    if job.value in ("rbps", "wbps", "w128kbps", "burst_64gb", "canary_bps", "c_seq_bps",
                     "c_recovery_bps"):
        logged = summarize_series(series or {}).get("bw", {}).get("steady_tail")
        value = float(logged if logged is not None else stats.get("bw_bytes", 0))
        if not math.isfinite(value) or value < 0:
            raise StudyError("fio JSON has malformed bandwidth")
        return value, bytes_done
    logged = summarize_series(series or {}).get("iops", {}).get("steady_tail")
    value = float(logged if logged is not None else stats.get("iops", 0))
    if not math.isfinite(value) or value < 0:
        raise StudyError("fio JSON has malformed IOPS")
    return value, bytes_done


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")


def empty_state(started: str, target_kind: str, target: str, device: str,
                target_identity: TargetIdentity | None = None) -> dict[str, Any]:
    target_record: dict[str, Any] = {"target": target, "device": device}
    if target_kind == "raw":
        if target_identity is None:
            raise StudyError("raw target state requires size and PARTUUID identity")
        target_record.update(target_identity.as_json())
    return {"schema": STATE_SCHEMA, "started_utc": started, "write_bytes": 0, "active_seconds": 0.0,
            "targets": {target_kind: target_record}, "cells": {},
            "write_reservations": {}, "active_reservations": {},
            "run_manifest": {"schema": RUN_MANIFEST_SCHEMA, "stop_policy": None,
                             "target_safety": {}, "target_safety_attempts": {}}}


def load_state(path: Path, target_kind: str, target: str, device: str,
               target_identity: TargetIdentity | None = None) -> dict[str, Any]:
    if not path.exists():
        return empty_state(dt.datetime.now(dt.UTC).isoformat(), target_kind, target, device, target_identity)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudyError(f"study state is unreadable: {exc}") from exc
    if (not isinstance(state, dict) or isinstance(state.get("schema"), bool) or
            state.get("schema") != STATE_SCHEMA):
        raise StudyError("unsupported or malformed study state")
    required = ("started_utc", "write_bytes", "active_seconds", "targets", "cells", "run_manifest")
    if any(key not in state for key in required):
        raise StudyError("study state is missing required fields")
    if (not isinstance(state["targets"], dict) or not isinstance(state["cells"], dict) or
            any(not isinstance(key, str) or not isinstance(value, str) or not value
                for key, value in state["cells"].items()) or
            isinstance(state["write_bytes"], bool) or not isinstance(state["write_bytes"], int) or
            state["write_bytes"] < 0 or isinstance(state["active_seconds"], bool) or
            not isinstance(state["active_seconds"], (int, float)) or
            not math.isfinite(state["active_seconds"]) or state["active_seconds"] < 0 or
            state["write_bytes"] > WRITE_CAP or state["active_seconds"] > ACTIVE_CAP_S):
        raise StudyError("study state fields are malformed")
    manifest = state["run_manifest"]
    if (not isinstance(manifest, dict) or manifest.get("schema") != RUN_MANIFEST_SCHEMA or
            isinstance(manifest.get("schema"), bool) or
            not isinstance(manifest.get("target_safety"), dict) or
            not isinstance(manifest.get("target_safety_attempts"), dict) or
            manifest.get("stop_policy") is not None and not isinstance(manifest.get("stop_policy"), dict)):
        raise StudyError("study run manifest is malformed")
    expected_target: dict[str, Any] = {"target": target, "device": device}
    if target_kind == "raw":
        if target_identity is None:
            raise StudyError("raw target resume requires current size and PARTUUID identity")
        expected_target.update(target_identity.as_json())
    reservations = state.setdefault("write_reservations", {})
    if (not isinstance(reservations, dict) or any(
            not isinstance(key, str) or not isinstance(caps, list) or not caps or any(
                isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0 for cap in caps)
            for key, caps in reservations.items())):
        raise StudyError("study write reservations are malformed")
    if sum(sum(caps) for caps in reservations.values()) > state["write_bytes"]:
        raise StudyError("study write reservations exceed the write meter")
    active_reservations = state.setdefault("active_reservations", {})
    if (not isinstance(active_reservations, dict) or any(
            not isinstance(key, str) or not isinstance(values, list) or not values or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or value <= 0 for value in values)
            for key, values in active_reservations.items())):
        raise StudyError("study active-time reservations are malformed")
    if sum(sum(values) for values in active_reservations.values()) > state["active_seconds"]:
        raise StudyError("study active-time reservations exceed the active-time meter")
    for key, value in state["targets"].items():
        valid_shape = (set(value) == {"target", "device", "size_bytes", "partuuid"}
                       if isinstance(value, dict) and key == "raw" else
                       isinstance(value, dict) and set(value) == {"target", "device"})
        if (key not in ("raw", "ext4-file", "btrfs-file") or not valid_shape or
                not isinstance(value["target"], str) or not value["target"] or
                not isinstance(value["device"], str) or not value["device"]):
            raise StudyError("study target identity is malformed")
        if key == "raw" and (isinstance(value["size_bytes"], bool) or
                not isinstance(value["size_bytes"], int) or value["size_bytes"] <= 0 or
                not isinstance(value["partuuid"], str) or not value["partuuid"]):
            raise StudyError("study raw target size/PARTUUID identity is malformed")
        if value["device"] != device:
            raise StudyError("study targets do not share the requested backing device")
        if key == target_kind and value != expected_target:
            raise StudyError(f"target identity changed for {target_kind}")
    state["targets"][target_kind] = expected_target
    return state


def reserve_write(state: dict[str, Any], key: str, cap: int) -> None:
    """Book a full fio cap before launch so an interrupted attempt cannot evade the meter."""
    reservations = state.setdefault("write_reservations", {})
    if (not isinstance(reservations, dict) or isinstance(cap, bool) or
            not isinstance(cap, int) or cap <= 0):
        raise StudyError("study write reservations are malformed")
    attempts = reservations.setdefault(key, [])
    if not isinstance(attempts, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in attempts):
        raise StudyError("study write reservations are malformed")
    attempts.append(cap)
    state["write_bytes"] += cap


def settle_write(state: dict[str, Any], key: str, actual: int) -> None:
    """Replace the latest attempt's conservative reservation with its recorded fio bytes."""
    reservations = state.setdefault("write_reservations", {})
    if isinstance(actual, bool) or not isinstance(actual, int) or actual < 0:
        raise StudyError("recorded fio writes are malformed")
    attempts = reservations.get(key) if isinstance(reservations, dict) else None
    if attempts is None:
        state["write_bytes"] += actual
        return
    if not isinstance(attempts, list) or not attempts:
        raise StudyError("study write reservations are malformed")
    reserved = attempts.pop()
    if actual > reserved:
        raise StudyError("recorded fio writes exceed the reserved cell cap")
    state["write_bytes"] -= reserved - actual
    if not attempts:
        del reservations[key]


def reserve_active(state: dict[str, Any], key: str, seconds: float) -> None:
    """Book a cell's maximum active allowance before starting an effect."""
    reservations = state.setdefault("active_reservations", {})
    if (not isinstance(reservations, dict) or isinstance(seconds, bool) or
            not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0):
        raise StudyError("study active-time reservations are malformed")
    attempts = reservations.setdefault(key, [])
    if not isinstance(attempts, list) or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value <= 0 for value in attempts):
        raise StudyError("study active-time reservations are malformed")
    attempts.append(seconds)
    state["active_seconds"] += seconds


def settle_active(state: dict[str, Any], key: str, actual: float) -> None:
    """Replace the latest active-time allowance with measured duration."""
    reservations = state.setdefault("active_reservations", {})
    if (not isinstance(reservations, dict) or isinstance(actual, bool) or
            not isinstance(actual, (int, float)) or not math.isfinite(actual) or actual < 0):
        raise StudyError("study active-time reservations are malformed")
    attempts = reservations.get(key)
    if attempts is None:
        state["active_seconds"] += actual
        return
    if not isinstance(attempts, list) or not attempts:
        raise StudyError("study active-time reservations are malformed")
    reserved = attempts.pop()
    if actual > reserved:
        raise StudyError("recorded cell duration exceeds the reserved active-time allowance")
    state["active_seconds"] -= reserved - actual
    if not attempts:
        del reservations[key]


def state_elapsed(state: Mapping[str, Any], now: dt.datetime | None = None) -> float:
    try:
        started = dt.datetime.fromisoformat(str(state["started_utc"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise StudyError("study state timestamp is malformed") from exc
    if started.tzinfo is None:
        raise StudyError("study state timestamp must include a timezone")
    current = now or dt.datetime.now(dt.UTC)
    elapsed = (current - started).total_seconds()
    if elapsed < 0:
        raise StudyError("study state timestamp is in the future")
    return elapsed


def cell_key(target_kind: str, slot: str, rung: str, job: Job) -> str:
    return f"{target_kind}|{slot}|{rung}|{job.name}"


def parse_run_cellset(name: str, target_kind: str | None) -> tuple[str, tuple[str, ...], str]:
    try:
        expected_kind, rungs, slot = CELL_SETS[name]
    except KeyError as exc:
        raise StudyError(f"unknown cell-set {name!r}") from exc
    if target_kind is not None and target_kind != expected_kind:
        raise StudyError(f"cell-set {name!r} requires target type {expected_kind}")
    return expected_kind, rungs, slot


def plan_document(state_path: Path | None = None) -> dict[str, Any]:
    base_cap = base_schedule_write_cap()
    reserve_cap = adaptive_reserve_write_cap()
    meter = {"base_schedule_bytes": base_cap, "adaptive_reserve_bytes": reserve_cap,
             "adaptive_candidate_bytes": adaptive_repeat_candidate_write_cap(),
             "adaptive_repeat_sets_scheduled": list(SCHEDULED_ADAPTIVE_REPEAT_CELL_SETS),
             "adaptive_repeat_sets_dropped": list(DROPPED_ADAPTIVE_REPEAT_CELL_SETS),
             "write_cap_bytes": WRITE_CAP,
             "used_bytes": 0, "remaining_bytes": WRITE_CAP}
    time = {"active_io_cap_seconds": ACTIVE_CAP_S, "active_seconds_used": 0.0,
            "elapsed_cap_seconds": ELAPSED_CAP_S, "elapsed_seconds_used": 0.0,
            "scheduled_window_hours": 9, "optional_repeat_deadline_hours": 12}
    run_manifest = None
    if state_path is not None and state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudyError(f"study state is unreadable: {exc}") from exc
        if (not isinstance(state, dict) or isinstance(state.get("schema"), bool) or
                state.get("schema") != STATE_SCHEMA or isinstance(state.get("write_bytes"), bool) or
                not isinstance(state.get("write_bytes"), int) or state["write_bytes"] < 0 or
                isinstance(state.get("active_seconds"), bool) or
                not isinstance(state.get("active_seconds"), (int, float)) or
                not math.isfinite(state["active_seconds"]) or state["active_seconds"] < 0):
            raise StudyError("study state cannot be used for the plan meter")
        run_manifest = state.get("run_manifest")
        if (not isinstance(run_manifest, dict) or
                run_manifest.get("schema") != RUN_MANIFEST_SCHEMA or
                isinstance(run_manifest.get("schema"), bool) or
                not isinstance(run_manifest.get("target_safety"), dict) or
                not isinstance(run_manifest.get("target_safety_attempts"), dict) or
                run_manifest.get("stop_policy") is not None and
                not isinstance(run_manifest.get("stop_policy"), dict)):
            raise StudyError("study run manifest is malformed for the plan")
        meter["used_bytes"] = state["write_bytes"]
        meter["remaining_bytes"] = max(0, WRITE_CAP - state["write_bytes"])
        time["active_seconds_used"] = state["active_seconds"]
        time["elapsed_seconds_used"] = state_elapsed(state)
    return {"schedule": [
        {"slot": "t0", "sets": ["raw-t0"], "detail": "standard, quick, veryquick"},
        {"slot": "t0+1h", "sets": ["part-c"], "detail": "rewrite/fstrim study on region C"},
        {"slot": "t0+3h", "sets": ["raw-t3h"], "detail": "veryquick, quick"},
        {"slot": "t0+4h if positive", "sets": ["part-c-repeat"],
         "detail": ("not scheduled under the 500 GB cap; a complete repeat does not fit "
                    "after the maximum base schedule")},
        {"slot": "t0+6h", "sets": ["raw-t6h"],
         "detail": "quick and veryquick always; standard only when the 6.2 boundary or moving-target rule needs it"},
        {"slot": "t0+9h", "sets": ["raw-t9h", "file-ext4", "file-btrfs"],
         "detail": "raw quick/veryquick, then file quick/veryquick"},
        {"slot": "by t0+12h", "sets": ["raw-t12h", "raw-t12h2"],
         "detail": ("not scheduled under the 500 GB cap; neither complete quick/veryquick "
                    "set fits after the maximum base schedule")},
    ], "rungs": {name: {"runtime_cap_s": row["runtime"], "writes_gb": row["write_gb"]}
                for name, row in RUNG.items()},
        "limits": {"active_seconds": ACTIVE_CAP_S, "elapsed_seconds": ELAPSED_CAP_S,
                   "write_bytes": WRITE_CAP, "minimum_free_bytes": "required per host policy",
                   "io_psi": "advisory; recorded but does not stop the study",
                   "memory_full_psi_threshold": "required per host policy",
                   "minimum_memavailable_bytes": "required per host policy",
                   "max_liveness_probe_latency_seconds": "required per host policy",
                   "liveness_probe_consecutive_samples": "required per host policy",
                   "liveness_probe_interval_seconds": DEFAULT_LIVENESS_PROBE_INTERVAL_S},
        "risk_note": ("A raw partition on the running system disk requires both "
                      "the operator's explicit --allow-system-disk-partition consent and an exact "
                      "per-run --system-disk-ack drift check; results on that disk include system "
                      "and sibling-swap activity."),
        "run_manifest": run_manifest,
        "meter": meter, "time": time}


def print_plan(as_json: bool = False, state_path: Path | None = None) -> None:
    document = plan_document(state_path)
    if as_json:
        print(json.dumps(document, indent=2, sort_keys=True))
        return
    print("IBX study schedule (relative to t0; run each cell-set at its scheduled slot)")
    for row in document["schedule"]:
        print(f"{row['slot']}: {', '.join(row['sets'])} — {row['detail']}")
    print("Time: scheduled window 9 h; elapsed cap 12 h; active I/O cap 3 h")
    print(f"Limits: 3 h active I/O; 12 h elapsed; {WRITE_CAP / GB:.2f} GB writes")
    print("I/O PSI is advisory. Stop thresholds for memory PSI full, MemAvailable, free space, and host liveness come from host policy.")
    print(document["risk_note"])
    manifest = document.get("run_manifest")
    safety = manifest.get("target_safety") if isinstance(manifest, Mapping) else None
    if isinstance(safety, Mapping) and isinstance(safety.get("raw"), Mapping):
        raw = safety["raw"]
        roots = ",".join(raw.get("root_fs_devices", [])) or "unknown"
        mounts = ",".join(raw.get("system_mounts_on_disk", [])) or "none"
        swaps = ",".join(raw.get("sibling_swap_devices", [])) or "none"
        print(f"Recorded raw target facts: host={raw.get('hostname', 'unknown')}; "
              f"disk={raw.get('target_disk', 'unknown')}; "
              f"root filesystem device(s)={roots}; "
              f"system mount(s)={mounts}; sibling swap device(s)={swaps}; "
              f"PARTUUID={raw.get('target_partuuid', 'unknown')}; "
              f"system-disk drift-check token={raw.get('system_disk_acknowledgement') or 'not required'}")
    elif isinstance(manifest, Mapping):
        print("Recorded raw target system-disk facts: not recorded yet.")
    meter = document["meter"]
    active_time = document["time"]
    print(f"Meter: {meter['used_bytes'] / GB:.1f} GB used; "
          f"base schedule cap {meter['base_schedule_bytes'] / GB:.2f} GB; "
          f"adaptive reserve {meter['adaptive_reserve_bytes'] / GB:.2f} GB; "
          f"{meter['remaining_bytes'] / GB:.1f} GB remaining")
    print(f"Time meter: {active_time['active_seconds_used'] / 60:.1f} min active; "
          f"{active_time['elapsed_seconds_used'] / 3600:.2f} h elapsed")


def stop_reason(sample: Mapping[str, Any], *, min_memavailable: int, min_free: int) -> str | None:
    mem_full = sample.get("psi", {}).get("memory", {}).get("full", {}).get("avg10")
    available = sample.get("meminfo", {}).get("MemAvailable")
    free = sample.get("free_bytes")
    if mem_full is None or available is None or free is None:
        return "monitor data incomplete"
    if (isinstance(mem_full, bool) or not isinstance(mem_full, (int, float)) or
            not math.isfinite(mem_full) or mem_full < 0 or mem_full > 100):
        return "monitor memory PSI data is malformed"
    if (isinstance(available, bool) or isinstance(free, bool) or
            not isinstance(available, int) or not isinstance(free, int) or available < 0 or free < 0):
        return "monitor memory or free-space data is malformed"
    if available < min_memavailable:
        return "MemAvailable fell below policy floor"
    if free < min_free:
        return "free space fell below required floor"
    return None


def parse_target_confirmation(text: str | None, target: str) -> TargetIdentity:
    """Parse the operator's exact raw-partition acknowledgement."""
    if not isinstance(text, str):
        raise StudyError("raw runs require --confirm-target PATH:SIZE_BYTES:PARTUUID")
    fields = text.rsplit(":", 2)
    if (len(fields) != 3 or fields[0] != target or not fields[1].isdigit() or
            int(fields[1]) <= 0 or not fields[2] or not re.fullmatch(r"[A-Fa-f0-9-]+", fields[2])):
        raise StudyError("--confirm-target must exactly match PATH:SIZE_BYTES:PARTUUID for the raw partition")
    return TargetIdentity(int(fields[1]), fields[2])



def _mountpoint_is_system(mountpoint: str) -> bool:
    return mountpoint == "/" or any(
        mountpoint == root or mountpoint.startswith(root.rstrip("/") + "/")
        for root in ("/usr", "/var", "/etc", "/boot", "/srv/cow")
    )


class LocalHost:
    """Effect boundary. Tests replace this with FakeHost/FakeFio."""

    def __init__(self, proc: Path = Path("/proc"), sys: Path = Path("/sys")) -> None:
        self.proc = proc
        self.sys = sys
        self._liveness_probe_sequence = 0
        self._liveness_probe_stalled = False
        self._liveness_probe_stalled_unit: str | None = None

    def is_root(self) -> bool:
        return os.geteuid() == 0

    def io_control_snapshot(self) -> dict[str, Any]:
        return io_control_snapshot()

    def assert_quiet_window(self) -> None:
        for unit in ("fstrim.timer", "apt-daily.timer", "apt-daily-upgrade.timer",
                     "mdt-dev-governance-reconcile.timer"):
            result = subprocess.run(["systemctl", "show", unit, "--property=LoadState",
                                    "--property=ActiveState", "--value"], text=True,
                                   capture_output=True, check=False)
            values = result.stdout.splitlines()
            if result.returncode or len(values) != 2:
                raise StudyError(f"cannot verify quiet-window timer state for {unit}")
            load, active = values
            if load == "loaded" and active not in ("inactive", "dead"):
                raise StudyError(f"quiet-window timer {unit} is active")
            if load not in ("loaded", "not-found"):
                raise StudyError(f"quiet-window timer {unit} state is unknown")

    def _sys_device(self, devno: str) -> Path:
        try:
            return (self.sys / "dev/block" / devno).resolve(strict=True)
        except OSError as exc:
            raise StudyError(f"cannot resolve sysfs block device {devno}: {exc}") from exc

    @staticmethod
    def _read_devno(path: Path) -> str:
        try:
            devno = (path / "dev").read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise StudyError(f"cannot read sysfs device number at {path}: {exc}") from exc
        if not re.fullmatch(r"\d+:\d+", devno):
            raise StudyError(f"sysfs device number at {path} is malformed")
        return devno

    def _holders(self, path: Path) -> set[str]:
        holder_dir = path / "holders"
        try:
            names = list(holder_dir.iterdir())
        except OSError as exc:
            raise StudyError(f"cannot inspect sysfs holders for {path.name}: {exc}") from exc
        out = set()
        for name in names:
            try:
                out.add(self._read_devno((self.sys / "class/block" / name.name).resolve(strict=True)))
            except OSError as exc:
                raise StudyError(f"cannot resolve holder {name.name} for {path.name}: {exc}") from exc
        return out

    @staticmethod
    def _assert_unheld(target: str) -> None:
        try:
            fd = os.open(target, os.O_RDONLY | os.O_EXCL | os.O_CLOEXEC | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno == errno.EBUSY:
                raise StudyError("raw target has an active exclusive holder") from exc
            raise StudyError(f"cannot verify raw target is free of exclusive holders: {exc}") from exc
        try:
            os.close(fd)
        except OSError as exc:
            raise StudyError(f"cannot release raw target exclusive probe: {exc}") from exc

    def _holder_closure(self, roots: Iterable[Path]) -> set[str]:
        pending = list(roots)
        visited: set[Path] = set()
        devnos: set[str] = set()
        while pending:
            path = pending.pop().resolve(strict=True)
            if path in visited:
                continue
            visited.add(path)
            devnos.add(self._read_devno(path))
            for holder in (path / "holders").iterdir():
                next_path = (self.sys / "class/block" / holder.name).resolve(strict=True)
                if next_path not in visited:
                    pending.append(next_path)
        return devnos

    @staticmethod
    def _mount_rows(path: Path) -> list[dict[str, str]]:
        try:
            contents = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StudyError(f"cannot read mount table {path}: {exc}") from exc
        rows = []
        unescape = lambda value: re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)
        for line in contents.splitlines():
            left, marker, right = line.partition(" - ")
            fields, after = left.split(), right.split()
            if not marker or len(fields) < 6 or len(after) < 2 or not re.fullmatch(r"\d+:\d+", fields[2]):
                raise StudyError(f"mount table {path} is malformed")
            rows.append({"devno": fields[2], "mountpoint": unescape(fields[4]),
                         "fstype": after[0], "source": unescape(after[1])})
        return rows

    def _findmnt_facts(self, target: str) -> tuple[str, str, str]:
        result = subprocess.run(["findmnt", "--noheadings", "--raw", "--output", "FSTYPE,UUID,MAJ:MIN",
                                 "--target", target], text=True, capture_output=True, check=False)
        fields = result.stdout.split()
        if result.returncode or len(fields) != 3:
            raise StudyError(f"cannot establish filesystem identity for {target}")
        fstype, fs_uuid, devno = fields
        if not re.fullmatch(r"\d+:\d+", devno):
            raise StudyError(f"filesystem device number for {target} is malformed")
        return fstype, fs_uuid, devno

    def _btrfs_members(self, fs_uuid: str, target: str) -> set[str]:
        if not re.fullmatch(r"[A-Fa-f0-9-]+", fs_uuid):
            raise StudyError(f"cannot establish btrfs UUID for {target}")
        device_dir = self.sys / "fs/btrfs" / fs_uuid / "devices"
        try:
            names = list(device_dir.iterdir())
        except OSError as exc:
            raise StudyError(f"cannot resolve btrfs backing members for {target}: {exc}") from exc
        members = set()
        for name in names:
            try:
                members.add(self._read_devno(name.resolve(strict=True)))
            except OSError as exc:
                raise StudyError(f"cannot resolve btrfs member {name.name} for {target}: {exc}") from exc
        if not members:
            raise StudyError(f"btrfs backing members are missing for {target}")
        return members

    def _mount_devnos(self, row: Mapping[str, str]) -> set[str]:
        if row["fstype"] == "btrfs":
            fstype, fs_uuid, _ = self._findmnt_facts(row["mountpoint"])
            if fstype != "btrfs":
                raise StudyError(f"filesystem identity changed while inspecting {row['mountpoint']}")
            return self._btrfs_members(fs_uuid, row["mountpoint"])
        return {row["devno"]}

    def _swap_devnos(self) -> set[str]:
        try:
            lines = (self.proc / "swaps").read_text(encoding="utf-8").splitlines()[1:]
        except OSError as exc:
            raise StudyError(f"cannot establish active swap devices: {exc}") from exc
        out: set[str] = set()
        for line in lines:
            fields = line.split()
            if not fields:
                continue
            try:
                info = Path(fields[0]).stat()
            except OSError as exc:
                raise StudyError(f"cannot inspect active swap target {fields[0]}: {exc}") from exc
            dev = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
            out.add(f"{os.major(dev)}:{os.minor(dev)}")
            if not stat.S_ISBLK(info.st_mode):
                fstype, fs_uuid, _ = self._findmnt_facts(fields[0])
                if fstype == "btrfs":
                    out.update(self._btrfs_members(fs_uuid, fields[0]))
        return out

    def validate_target(self, target: str, kind: str, offset: int, size: int, device: str,
                        confirmation: TargetIdentity | None = None, *,
                        allow_system_disk_partition: bool = False,
                        system_disk_ack: str | None = None,
                        derive_system_disk_ack: bool = False) -> TargetValidation:
        path = Path(target)
        try:
            info = path.lstat()
        except OSError as exc:
            raise StudyError(f"target cannot be inspected: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise StudyError("target must not be a symlink")
        if kind == "raw" and not stat.S_ISBLK(info.st_mode):
            raise StudyError("raw study target must be a block device")
        if kind == "raw":
            result = subprocess.run(["blockdev", "--getsize64", target], text=True,
                                    capture_output=True, check=False)
            if result.returncode or not result.stdout.strip().isdigit():
                raise StudyError("cannot establish raw target size")
            target_size = int(result.stdout.strip())
            if target_size < offset + size:
                raise StudyError("raw target is smaller than the requested extent")
            if confirmation is None:
                raise StudyError("raw runs require --confirm-target PATH:SIZE_BYTES:PARTUUID")
            if target_size != confirmation.size_bytes:
                raise StudyError("raw target size does not match --confirm-target")
            target_stat = path.stat()
            target_devno = f"{os.major(target_stat.st_rdev)}:{os.minor(target_stat.st_rdev)}"
            target_sys = self._sys_device(target_devno)
            if not (target_sys / "partition").is_file():
                raise StudyError("raw target must be a dedicated partition; whole disks are refused")
            disk_sys = target_sys.parent
            try:
                partition_uuid = subprocess.run(["blkid", "-s", "PARTUUID", "-o", "value", target],
                                                text=True, capture_output=True, check=False)
            except OSError as exc:
                raise StudyError(f"cannot establish raw target PARTUUID: {exc}") from exc
            actual_partuuid = partition_uuid.stdout.strip()
            if partition_uuid.returncode or not actual_partuuid:
                raise StudyError("cannot establish raw target PARTUUID")
            if actual_partuuid != confirmation.partuuid:
                raise StudyError("raw target PARTUUID does not match --confirm-target")
            expected_path = self.sys / "class/block" / device / "dev"
            try:
                expected = expected_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise StudyError(f"cannot map diskstats device {device}: {exc}") from exc
            if target_devno != expected:
                raise StudyError(f"target backing device {target_devno} does not match monitored device {device} ({expected})")
            try:
                siblings = [child for child in disk_sys.iterdir() if (child / "partition").is_file()]
            except OSError as exc:
                raise StudyError(f"cannot inspect partitions of raw target disk: {exc}") from exc
            disk_partitions = {self._read_devno(child) for child in siblings}
            same_disk = {self._read_devno(disk_sys), *disk_partitions}
            same_disk.update(self._holder_closure([disk_sys, *siblings]))
            if self._holders(target_sys):
                raise StudyError("raw target partition has active holders")
            self_mounts = self._mount_rows(self.proc / "self/mountinfo")
            init_mounts = self._mount_rows(self.proc / "1/mountinfo")
            resolved_self = [(row, self._mount_devnos(row)) for row in self_mounts]
            resolved_init = [(row, self._mount_devnos(row)) for row in init_mounts]
            root_rows = [(row, devnos) for row, devnos in resolved_self if row["mountpoint"] == "/"]
            if len(root_rows) != 1 or not any((self.sys / "dev/block" / devno).exists()
                                               for _, devnos in root_rows for devno in devnos):
                raise StudyError("cannot establish the running system's root block device")
            root_devices = sorted({self._sys_device(devno).name for devno in root_rows[0][1]})
            touching_self = {(row["mountpoint"], tuple(sorted(devnos))) for row, devnos in resolved_self
                             if devnos & same_disk}
            touching_init = {(row["mountpoint"], tuple(sorted(devnos))) for row, devnos in resolved_init
                             if devnos & same_disk}
            if touching_self != touching_init:
                raise StudyError("cannot establish raw target mount state across PID 1 and current namespaces")
            target_related = self._holder_closure([target_sys])
            # The whole-disk device and its holders cover the target extent; the
            # system-disk opt-in relaxes sharing with SIBLING partitions only.
            whole_disk_related = self._holder_closure([disk_sys])
            system_mounts = sorted(row["mountpoint"] for row, devnos in resolved_self
                                   if _mountpoint_is_system(row["mountpoint"]) and devnos & same_disk)
            system_disk_shared = bool(system_mounts)
            hostname = socket.gethostname()
            expected_ack = system_disk_acknowledgement(hostname, disk_sys.name, root_devices,
                                                       actual_partuuid)
            for row, devnos in resolved_self:
                if devnos & target_related:
                    raise StudyError("raw target partition is mounted")
            if system_disk_shared:
                if (not allow_system_disk_partition or
                        any(devnos & whole_disk_related for row, devnos in resolved_self
                            if _mountpoint_is_system(row["mountpoint"]))):
                    raise StudyError("raw target is on the root disk or contains a running system filesystem")
                if not derive_system_disk_ack and system_disk_ack != expected_ack:
                    raise StudyError("system-disk acknowledgement does not match current host facts; "
                                     f"expected {expected_ack}")
            elif (allow_system_disk_partition or system_disk_ack is not None) and not derive_system_disk_ack:
                raise StudyError("system-disk acknowledgement applies only to a target on the running system disk")
            swaps = self._swap_devnos()
            if (swaps & target_related or swaps & whole_disk_related or
                    (swaps & same_disk and not system_disk_shared)):
                raise StudyError("raw target partition or its disk is swap-active")
            self._assert_unheld(target)
            system_disk = {
                "hostname": hostname,
                "target_disk": disk_sys.name,
                "root_fs_devices": root_devices,
                "system_mounts_on_disk": system_mounts,
                "sibling_swap_devices": sorted(self._sys_device(devno).name
                                                for devno in swaps & same_disk
                                                if devno not in target_related),
                "target_partuuid": actual_partuuid,
                "root_disk_shared": system_disk_shared,
                "system_disk_acknowledgement": expected_ack if system_disk_shared and derive_system_disk_ack
                else system_disk_ack if system_disk_shared else None,
                "acknowledgement_format": "<hostname>:<disk-kname>:<root-fs-device-kname[,root-fs-device-kname...]>:<target-PARTUUID>",
            }
            return TargetValidation(TargetIdentity(target_size, actual_partuuid), system_disk)
        if kind != "raw" and (not stat.S_ISREG(info.st_mode) or info.st_size != offset + size):
            raise StudyError("file study target must be a regular file exactly covering the requested extent")
        dev_path = self.sys / "class/block" / device / "dev"
        try:
            expected = dev_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise StudyError(f"cannot map diskstats device {device}: {exc}") from exc
        if not re.fullmatch(r"\d+:\d+", expected):
            raise StudyError(f"sysfs device number for {device} is malformed")
        fstype, fs_uuid, devno = self._findmnt_facts(target)
        expected_fstype = "ext4" if kind == "ext4-file" else "btrfs"
        if fstype != expected_fstype:
            raise StudyError(f"file target must be on {expected_fstype} and map to one device")
        if fstype == "btrfs":
            members = self._btrfs_members(fs_uuid, target)
            if len(members) != 1:
                raise StudyError("file target must be on single-device btrfs")
            observed = next(iter(members))
        else:
            observed = devno
        if observed != expected:
            raise StudyError(f"target backing device {observed} does not match monitored device {device} ({expected})")
        return TargetValidation(None)

    def trim_state(self, device: str) -> dict[str, Any]:
        path = self.sys / "class/block" / device / "queue/discard_max_bytes"
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise StudyError(f"cannot read discard support for {device}: {exc}") from exc
        if not raw.isdigit():
            raise StudyError(f"discard support for {device} is malformed")
        maximum = int(raw)
        return {"supported": maximum > 0, "discard_max_bytes": maximum}

    def discard(self, target: str, kind: str, offset: int, size: int,
                timeout: float | None = None, sample: Callable[[], None] | None = None) -> float:
        argv = (["blkdiscard", "-o", str(offset), "-l", str(size), target] if kind == "raw" else
                ["fallocate", "-p", "-o", str(offset), "-l", str(size), target])
        if timeout is not None and timeout <= 0:
            raise StudyError("bounded discard exceeded the remaining active-I/O allowance")
        process = subprocess.Popen(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True)
        start = self.now()
        try:
            while process.poll() is None:
                if sample is not None:
                    sample()
                remaining = None if timeout is None else timeout - (self.now() - start)
                if remaining is not None and remaining <= 0:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=30)
                    raise StudyError("bounded discard exceeded the remaining active-I/O allowance")
                self.sleep(1.0 if remaining is None else min(1.0, remaining))
            _, stderr = process.communicate()
        except BaseException:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=30)
            raise
        if process.returncode:
            raise StudyError(f"bounded discard failed: {stderr.strip()}")
        return self.now() - start

    def wait_with_samples(self, seconds: int, sample: Callable[[], None]) -> None:
        end = self.now() + seconds
        while self.now() < end:
            sample()
            self.sleep(min(1.0, end - self.now()))

    def current_container_id(self) -> str:
        text = (self.proc / "self/cgroup").read_text(encoding="utf-8")
        matches = re.findall(r"docker-([0-9a-f]{64})\.scope", text)
        if len(set(matches)) != 1:
            raise StudyError("fsync mode requires one Docker cgroup identity from the host wrapper")
        return matches[0]

    def start_fsync(self, out: Path) -> tuple[str, Path]:
        parent = os.environ.get("CGROUP_PARENT_DEV_BACKGROUND")
        if not parent:
            raise StudyError("fsync mode requires CGROUP_PARENT_DEV_BACKGROUND from host policy")
        state = subprocess.run(["systemctl", "show", parent, "--property=LoadState", "--value"],
                               text=True, capture_output=True, check=False)
        if state.returncode or state.stdout.strip() != "loaded":
            raise StudyError("fsync collector cgroup parent is not a loaded systemd slice")
        script = REPO / "mdt-cockpit/scripts/iomon-bpf/run.sh"
        if not script.is_file():
            raise StudyError(f"iomon-bpf wrapper is missing: {script}")
        name = f"iomon-bpf-ibx-{os.getpid()}"
        bpf_out = out / "fsync-bpf"
        subprocess.run([str(script), "--hours", "1", "--name", name, "--out", str(bpf_out),
                        self.current_container_id()], check=True)
        deadline = self.now() + 30
        while self.now() < deadline:
            probe = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", "--", name],
                                   text=True, capture_output=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip() == "true":
                return name, bpf_out
            self.sleep(1)
        subprocess.run(["docker", "stop", "-t", "10", name], text=True,
                       capture_output=True, check=False)
        raise StudyError("iomon-bpf did not start within 30 seconds")

    def stop_fsync(self, collector: tuple[str, Path]) -> None:
        name, bpf_out = collector
        (bpf_out / "STOP-BPF").touch()
        stopped = subprocess.run(["docker", "stop", "-t", "60", name], text=True,
                                 capture_output=True, check=False)
        if stopped.returncode:
            raise StudyError(f"iomon-bpf stop failed for owned collector {name}: {stopped.stderr.strip()}")

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def liveness_probe(self, max_latency_s: float) -> dict[str, Any]:
        if self._liveness_probe_stalled:
            try:
                status = subprocess.run(["systemctl", "is-active", self._liveness_probe_stalled_unit],
                                        text=True, capture_output=True, check=False,
                                        timeout=min(max_latency_s, 1.0))
            except (OSError, subprocess.TimeoutExpired):
                status = None
            state = status.stdout.strip() if status is not None else ""
            # is-active returns 4 for a missing unit, which --collect removes after exit.
            confirmed_stopped = (status is not None and
                                 ((status.returncode != 0 and state in {"inactive", "failed"}) or
                                  (status.returncode == 4 and state == "unknown")))
            if not confirmed_stopped:
                return {"responsive": False, "latency_seconds": max_latency_s, "components": {},
                        "error": "previous probe has not been confirmed stopped; no overlapping host probe was launched"}
            self._liveness_probe_stalled = False
            self._liveness_probe_stalled_unit = None
        self._liveness_probe_sequence += 1
        unit = f"ibx-liveness-{os.getpid()}-{self._liveness_probe_sequence}.service"
        started = time.monotonic()
        try:
            result = subprocess.run(["systemd-run", "--quiet", "--wait", "--pipe", "--collect",
                                     f"--unit={unit}", "--slice=system.slice",
                                     f"--property=RuntimeMaxSec={max_latency_s}",
                                     sys.executable, "-c", LIVENESS_PROBE_SCRIPT,
                                     str(max_latency_s), LIVENESS_ROOT_DIRECTORY,
                                     LIVENESS_SSH_HOST, str(LIVENESS_SSH_PORT),
                                     LIVENESS_COCKPIT_UNIT, LIVENESS_COCKPIT_CONTAINER],
                                    text=True, capture_output=True,
                                    check=False, timeout=max_latency_s + 1)
        except subprocess.TimeoutExpired:
            self._liveness_probe_stalled = True
            self._liveness_probe_stalled_unit = unit
            return {"responsive": False, "latency_seconds": max(max_latency_s,
                    time.monotonic() - started), "components": {}, "error": "probe timed out"}
        except OSError as exc:
            return {"responsive": False, "latency_seconds": time.monotonic() - started,
                    "components": {}, "error": f"probe could not start: {exc}"}
        latency = time.monotonic() - started
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None
        if (not isinstance(payload, dict) or not isinstance(payload.get("responsive"), bool) or
                not isinstance(payload.get("components"), dict)):
            return {"responsive": False, "latency_seconds": latency, "components": {},
                    "error": "probe returned malformed output"}
        return {"responsive": result.returncode == 0 and payload["responsive"],
                "latency_seconds": latency, "components": payload["components"],
                "error": payload.get("error")}

    def sample(self, device: str, out: Path, target: str | None = None) -> dict[str, Any]:
        row = monitor_sample(device, proc=self.proc)
        output_stat = os.statvfs(out)
        output_free = output_stat.f_bavail * output_stat.f_frsize
        row["output_free_bytes"] = output_free
        row["free_bytes"] = output_free
        if target is not None:
            target_stat = os.statvfs(target)
            target_free = target_stat.f_bavail * target_stat.f_frsize
            row["target_free_bytes"] = target_free
            row["free_bytes"] = min(output_free, target_free)
        return row

    def read_fio_logs(self, prefix: Path) -> dict[str, list[dict[str, float]]]:
        result: dict[str, list[dict[str, float]]] = {"bw": [], "iops": []}
        for kind, is_bandwidth in (("bw", True), ("iops", False)):
            path = Path(f"{prefix}_{kind}.1.log")
            if path.is_file():
                result[kind] = parse_fio_log(path.read_text(encoding="utf-8"), bandwidth=is_bandwidth)
        return result

    def execute_fio(self, job_path: Path, runtime_s: int,
                    sample: Callable[[], None]) -> tuple[dict[str, Any], float]:
        proc = subprocess.Popen(["fio", str(job_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, close_fds=True)
        start = self.now()
        try:
            while proc.poll() is None:
                sample()
                remaining = runtime_s - (self.now() - start)
                if remaining <= 0:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        proc.send_signal(signal.SIGTERM)
                        proc.wait(timeout=30)
                    break
                self.sleep(min(1, remaining))
            stdout, stderr = proc.communicate()
        except BaseException:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=30)
            raise
        duration = self.now() - start
        if proc.returncode != 0:
            raise StudyError(f"fio failed with exit {proc.returncode}: {stderr.strip()}")
        try:
            return json.loads(stdout), duration
        except json.JSONDecodeError as exc:
            raise StudyError(f"fio returned invalid JSON: {exc}") from exc


def derive_system_disk_ack(target: str, device: str, confirm_target: str,
                           host: Any | None = None) -> str:
    """Derive the opt-in from current host and target facts after all raw guards pass."""
    host = host or LocalHost()
    if not host.is_root():
        raise StudyError("system-disk acknowledgement derivation requires root on the study host")
    confirmation = parse_target_confirmation(confirm_target, target)
    validation = host.validate_target(target, "raw", 0, 1, device, confirmation=confirmation,
                                      allow_system_disk_partition=True,
                                      derive_system_disk_ack=True)
    if (validation.identity != confirmation or validation.system_disk is None or
            validation.system_disk.get("root_disk_shared") is not True):
        raise StudyError("raw target does not share the running system disk")
    acknowledgement = validation.system_disk.get("system_disk_acknowledgement")
    if not isinstance(acknowledgement, str) or not acknowledgement:
        raise StudyError("cannot derive system-disk acknowledgement from current host facts")
    return acknowledgement


def run_study(cell_set: str, *, target: str, device: str, out: Path, target_kind: str | None,
              offset: int, size: int, mem_psi_max: float, min_memavailable: int, min_free: int,
              max_probe_latency_s: float, probe_consecutive_samples: int,
              probe_interval_s: float = DEFAULT_LIVENESS_PROBE_INTERVAL_S,
              host: Any | None = None, fsync_cell: str | None = None,
              quiet_window_confirmed: bool = False, confirm_target: str | None = None,
              allow_system_disk_partition: bool = False, system_disk_ack: str | None = None,
              dry_run: bool = False) -> dict[str, Any]:
    host = host or LocalHost()
    if not host.is_root():
        raise StudyError("run requires root on the study host")
    if not quiet_window_confirmed:
        raise StudyError("run requires --confirm-quiet-window")
    if not dry_run:
        host.assert_quiet_window()
    kind, rungs, slot = parse_run_cellset(cell_set, target_kind)
    if cell_set == "raw-t6h" and not raw_standard_needed(out):
        rungs = tuple(rung for rung in rungs if rung != "standard")
    if cell_set in ("file-ext4-standard", "file-btrfs-standard") and not file_standard_needed(out, kind):
        return {"cell_set": cell_set, "skipped": True,
                "reason": f"{kind} standard is not needed; quick results are not borderline"}
    if cell_set in DROPPED_ADAPTIVE_REPEAT_CELL_SETS:
        return {"cell_set": cell_set, "skipped": True,
                "reason": (f"not scheduled under the {WRITE_CAP_GB} GB write cap; "
                           "the complete base schedule leaves too little reserve")}
    if not target.startswith("/") or not re.fullmatch(r"[A-Za-z0-9_.-]+", device):
        raise StudyError("target must be an absolute path and device a sysfs block name")
    if offset < 0 or size <= 0 or offset + size > (32 * GIB if kind == "raw" else 24 * GIB):
        raise StudyError("target extent is outside the study's 24 GiB/32 GiB geometry")
    expected_extent = ((24 * GIB, 8 * GIB) if cell_set in ("part-c", "part-c-repeat") else
                       (0, 24 * GIB))
    if (offset, size) != expected_extent:
        raise StudyError("cell-set target extent does not match its assigned study region")
    if not math.isfinite(mem_psi_max) or mem_psi_max <= 0 or mem_psi_max > 100:
        raise StudyError("memory PSI full limit must be a percentage in (0,100]")
    if (not math.isfinite(max_probe_latency_s) or max_probe_latency_s <= 0 or
            max_probe_latency_s > 60):
        raise StudyError("maximum host-liveness probe latency must be finite and in (0,60] seconds")
    if (isinstance(probe_consecutive_samples, bool) or
            not isinstance(probe_consecutive_samples, int) or probe_consecutive_samples <= 0):
        raise StudyError("host-liveness consecutive-sample count must be a positive integer")
    if (isinstance(probe_interval_s, bool) or not isinstance(probe_interval_s, (int, float)) or
            not math.isfinite(probe_interval_s) or probe_interval_s <= 0 or
            probe_interval_s > 60):
        raise StudyError("host-liveness probe interval must be finite and in (0,60] seconds")
    if min_memavailable <= 0 or min_free <= 0:
        raise StudyError("memory and free-space floors must be positive")
    if not out.is_absolute():
        raise StudyError("--out must be an absolute path")
    confirmation = parse_target_confirmation(confirm_target, target) if kind == "raw" else None
    if kind != "raw" and confirm_target is not None:
        raise StudyError("--confirm-target applies only to raw partition runs")
    if allow_system_disk_partition != (system_disk_ack is not None):
        raise StudyError("--allow-system-disk-partition and --system-disk-ack must be supplied together")
    if kind != "raw" and allow_system_disk_partition:
        raise StudyError("--allow-system-disk-partition applies only to raw partition runs")
    validation = host.validate_target(target, kind, offset, size, device, confirmation=confirmation,
                                      allow_system_disk_partition=allow_system_disk_partition,
                                      system_disk_ack=system_disk_ack)
    target_identity = validation.identity
    if kind == "raw" and target_identity != confirmation:
        raise StudyError("validated raw target identity does not match --confirm-target")
    before = host.io_control_snapshot()
    state_path = out / STATE_NAME
    state = load_state(state_path, kind, target, device, target_identity)
    stop_policy = {
        "io_psi": "advisory; recorded, no stop threshold",
        "memory_psi_full_avg10_max_percent": mem_psi_max,
        "min_memavailable_bytes": min_memavailable,
        "min_free_bytes": min_free,
        "max_probe_latency_seconds": max_probe_latency_s,
        "probe_consecutive_samples": probe_consecutive_samples,
        "probe_interval_seconds": probe_interval_s,
    }
    manifest = state["run_manifest"]
    existing_policy = manifest.get("stop_policy")
    if existing_policy is not None and existing_policy != stop_policy:
        recorded_policy = json.dumps(existing_policy, sort_keys=True, separators=(",", ":"))
        raise StudyError("study stop policy changed since the previous attempt; resume is refused to preserve "
                         f"the existing byte and time meters. Recorded stop policy: {recorded_policy}. "
                         "Resume only with those values; if they are no longer acceptable, keep this state "
                         "and obtain an operator decision. A new --out directory resets those meters")
    manifest["stop_policy"] = stop_policy
    target_safety = validation.system_disk
    safety_history = manifest["target_safety"]
    previous_safety = safety_history.get(kind)
    if previous_safety is not None:
        stable_keys = ("hostname", "target_disk", "root_fs_devices", "target_partuuid",
                       "root_disk_shared", "system_disk_acknowledgement")
        if (not isinstance(target_safety, Mapping) or not isinstance(previous_safety, Mapping) or
                any(previous_safety.get(key) != target_safety.get(key) for key in stable_keys)):
            raise StudyError("stable system-disk target identity changed since the previous attempt")
    if target_safety is not None:
        safety_history[kind] = target_safety
        attempts_by_kind = manifest.setdefault("target_safety_attempts", {})
        if not isinstance(attempts_by_kind, dict):
            raise StudyError("system-disk target-safety history is malformed")
        attempts = attempts_by_kind.setdefault(kind, [])
        if not isinstance(attempts, list):
            raise StudyError("system-disk target-safety history is malformed")
        attempts.append({"recorded_utc": dt.datetime.now(dt.UTC).isoformat(),
                         "facts": target_safety})
    snapshots = state.setdefault("io_control_runs", {})
    if not isinstance(snapshots, dict):
        raise StudyError("study IO-control snapshot history is malformed")
    previous = snapshots.get(cell_set)
    previous_snapshot = None
    if previous is not None and not isinstance(previous, dict):
        raise StudyError("study IO-control snapshot history is malformed")
    if isinstance(previous, dict):
        if not isinstance(previous.get("before"), dict):
            raise StudyError("study IO-control snapshot history is malformed")
        previous_snapshot = previous.get("after", previous.get("before"))
        if not isinstance(previous_snapshot, dict):
            raise StudyError("study IO-control snapshot history is malformed")
    if previous_snapshot is not None and previous_snapshot != before:
        raise StudyError("IO-control snapshot changed since the previous attempt at this cell-set")
    snapshots[cell_set] = {"before": before}
    if state_elapsed(state) > ELAPSED_CAP_S:
        raise StudyError("12 hour elapsed study limit reached")
    if state["active_seconds"] >= ACTIVE_CAP_S or state["write_bytes"] >= WRITE_CAP:
        raise StudyError("study active-time or write budget already exhausted")
    if cell_set in ("raw-t12h", "raw-t12h2"):
        required = 4 if cell_set == "raw-t12h" else 5
        decisions = [adaptive_raw_repeat(out, rung, required) for rung in rungs]
        if any(not enough for enough, _ in decisions):
            raise StudyError(f"{cell_set} needs {required} completed quick/veryquick runs first")
        if not any(needed for _, needed in decisions):
            return {"cell_set": cell_set, "skipped": True,
                    "reason": "the latest e moved by no more than 20 percent"}
    if cell_set == "part-c-repeat":
        assessment = part_c_assessment(out)
        if assessment.get("status") == "incomplete":
            raise StudyError("part-c-repeat requires the first rewrite/fstrim study to be complete")
        if not assessment.get("confirmed"):
            return {"cell_set": cell_set, "skipped": True,
                    "reason": "the first rewrite/fstrim study did not confirm degradation"}
    destination = out / "cells"
    results: list[dict[str, Any]] = []
    bad_memory_psi = 0
    bad_liveness = 0
    last_liveness_probe_at: float | None = None
    current_metric: dict[str, float] = {}

    def take_liveness_probe(*, preflight: bool = False) -> tuple[dict[str, Any], bool]:
        nonlocal bad_liveness, last_liveness_probe_at
        probe = validate_liveness_probe(host.liveness_probe(max_probe_latency_s))
        last_liveness_probe_at = host.now()
        failed = (not probe["responsive"] or probe["latency_seconds"] > max_probe_latency_s)
        bad_liveness = bad_liveness + 1 if failed else 0
        record = {**probe, "sampled": True, "limit_seconds": max_probe_latency_s,
                  "consecutive_samples_required": probe_consecutive_samples,
                  "probe_interval_seconds": probe_interval_s}
        if preflight:
            manifest["liveness_preflight"] = {
                "recorded_utc": dt.datetime.now(dt.UTC).isoformat(), "probe": record}
        return record, failed

    def remember(job: Job, record: Mapping[str, Any]) -> None:
        current_metric[job.value] = float(record["value"])
        if job.name == "fill":
            current_metric["written_extent_bytes"] = float(record["write_bytes"])
        if job.name == "seq1m-write" and record.get("estimates", {}).get("bw", {}).get("early_10s") is not None:
            current_metric["wbps_r_est"] = float(record["estimates"]["bw"]["early_10s"])
        if job.name == "rate-read-q32":
            current_metric["read_iops_ceiling"] = float(record["value"])
        elif job.name == "rate-write-q32":
            current_metric["write_iops_ceiling"] = float(record["value"])

    def verify_record_identity(record: Mapping[str, Any], filename: str) -> None:
        expected = target_identity.as_json() if target_identity else None
        if record.get("target_identity") != expected:
            raise StudyError(f"cell result target identity changed: {filename}")

    all_planned = {(rung, job.name) for rung in rungs for job in
                   rung_jobs("quick" if rung == "part-c" else rung, part_c=rung == "part-c")}
    if fsync_cell is not None and fsync_cell not in {f"{rung}:{name}" for rung, name in all_planned}:
        raise StudyError("--fsync-cell must be written as RUNG:CELL and name a cell in this set")
    pending_cap = 0
    pending_work = False
    for rung in rungs:
        special = rung == "part-c"
        planned_jobs = rung_jobs("quick" if special else rung, part_c=special)
        planned_slot = slot
        trim_key = cell_key(kind, planned_slot, rung, Job("trim-state", "read", 4096, 1, 0, "trim_state"))
        trim_name = re.sub(r"[^A-Za-z0-9_.-]", "_", trim_key) + ".json"
        if trim_key not in state["cells"] and not (out / "cells" / trim_name).exists():
            pending_work = True
        for job in planned_jobs:
            key = cell_key(kind, planned_slot, rung, job)
            filename = re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json"
            if key not in state["cells"] and not (out / "cells" / filename).exists():
                pending_cap += job.write_cap
                pending_work = True
    required_cap = required_remainder_write_cap(cell_set, out=out, state=state)
    if int(state["write_bytes"]) + pending_cap + required_cap > WRITE_CAP:
        if cell_set in FILE_STANDARD_CELL_SETS:
            return {"cell_set": cell_set, "skipped": True,
                    "reason": (f"the full file-standard cap and {required_cap} bytes required "
                               f"by cell-sets still ahead do not fit in the remaining "
                               f"{WRITE_CAP_GB} GB write budget")}
        raise StudyError("the remaining write budget cannot complete this cell-set")
    rendered_jobs: dict[tuple[str, str], str] = {}
    planned_jobs: dict[tuple[str, str], Job] = {}
    for rung in rungs:
        special = rung == "part-c"
        for job in rung_jobs("quick" if special else rung, part_c=special):
            planned_jobs[(rung, job.name)] = job
            filename = re.sub(r"[^A-Za-z0-9_.-]", "_",
                              cell_key(kind, slot, rung, job)) + ".json"
            extent = job.extent_bytes or (RUNG[rung]["fill_gb"] * GIB
                                          if job.read_written_extent else size)
            rendered_jobs[(rung, job.name)] = render_job(
                job, target=target, offset=offset, size=extent,
                log_prefix=str(out / "fio-logs" / filename[:-5]),
                rate_iops=1 if job.rate_fraction is not None else None)
    if dry_run:
        if out.exists() and not out.is_dir():
            raise StudyError("--out exists and is not a directory")
        sample_out = out
        while not sample_out.is_dir():
            parent = sample_out.parent
            if parent == sample_out:
                raise StudyError("cannot find an existing output filesystem for --dry-run")
            sample_out = parent
        bad_memory_psi = 0
        for _ in range(3):
            sample = host.sample(device, sample_out, target if kind != "raw" else None)
            reason = stop_reason(sample, min_memavailable=min_memavailable, min_free=min_free)
            if reason:
                raise StudyError(reason)
            memory_full = sample["psi"]["memory"]["full"]["avg10"]
            bad_memory_psi = bad_memory_psi + 1 if memory_full > mem_psi_max else 0
            if bad_memory_psi >= 3:
                raise StudyError("memory PSI full exceeded threshold for three consecutive samples")
        preflight_probe, probe_failed = take_liveness_probe(preflight=True)
        if probe_failed:
            raise StudyError("initial host-liveness probe failed or exceeded its latency limit")
        return {"cell_set": cell_set, "dry_run": True, "jobs_rendered": len(rendered_jobs),
                "write_bytes_used": state["write_bytes"], "write_cap_bytes": WRITE_CAP,
                "target_identity": target_identity.as_json() if target_identity else None,
                "host_liveness_preflight": preflight_probe, "run_manifest": manifest}
    out.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    (out / "fio-logs").mkdir(parents=True, exist_ok=True)
    write_json(state_path, state)
    if pending_work:
        first_rung = rungs[0]
        first_special = first_rung == "part-c"
        first_job = rung_jobs("quick" if first_special else first_rung,
                              part_c=first_special)[0]
        first_key = cell_key(kind, slot, first_rung, first_job)
        first_filename = re.sub(r"[^A-Za-z0-9_.-]", "_", first_key) + ".json"
        first_job_path = out / "jobs" / (first_filename[:-5] + ".fio")
        first_job_path.parent.mkdir(parents=True, exist_ok=True)
        first_job_path.write_text(rendered_jobs[(first_rung, first_job.name)], encoding="utf-8")
    monitor_total = 0

    def monitor_host(*, preflight: bool = False) -> None:
        nonlocal monitor_total, bad_memory_psi, bad_liveness
        row = host.sample(device, out, target if kind != "raw" else None)
        reason = stop_reason(row, min_memavailable=min_memavailable, min_free=min_free)
        if reason:
            row["host_liveness"] = None
            row["sample_no"] = monitor_total
            append_jsonl(out / "monitor.jsonl", row)
            monitor_total += 1
            raise StudyError(reason)
        probe_failed = False
        if (preflight or last_liveness_probe_at is None or
                host.now() - last_liveness_probe_at + 1e-9 >= probe_interval_s):
            probe_record, probe_failed = take_liveness_probe(preflight=preflight)
            row["host_liveness"] = probe_record
        else:
            row["host_liveness"] = {
                "sampled": False,
                "seconds_since_last_probe": max(0.0, host.now() - last_liveness_probe_at),
                "probe_interval_seconds": probe_interval_s,
            }
        row["sample_no"] = monitor_total
        append_jsonl(out / "monitor.jsonl", row)
        monitor_total += 1
        if preflight:
            write_json(state_path, state)
            if probe_failed:
                raise StudyError("initial host-liveness probe failed or exceeded its latency limit")
        mem_full = row["psi"]["memory"]["full"]["avg10"]
        bad_memory_psi = bad_memory_psi + 1 if mem_full > mem_psi_max else 0
        if bad_memory_psi >= 3:
            raise StudyError("memory PSI full exceeded threshold for three consecutive samples")
        if not preflight and probe_failed and bad_liveness >= probe_consecutive_samples:
            raise StudyError("host-liveness probe exceeded its latency limit or failed for "
                             f"{probe_consecutive_samples} consecutive samples")
    try:
        if pending_work:
            monitor_host(preflight=True)
        for rung in rungs:
            special = rung == "part-c"
            jobs = rung_jobs("quick" if special else rung, part_c=special)
            slot_for_cell = slot
            trim_job = Job("trim-state", "read", 4096, 1, 0, "trim_state")
            trim_key = cell_key(kind, slot_for_cell, rung, trim_job)
            trim_file = destination / (re.sub(r"[^A-Za-z0-9_.-]", "_", trim_key) + ".json")
            if trim_key not in state["cells"]:
                capability = host.trim_state(device)
                trim_record = {"schema": SCHEMA, "cell_key": trim_key, "cell_set": cell_set,
                               "target_type": kind, "target": target, "device": device,
                               "slot": slot_for_cell, "rung": rung, "cell": "trim-state",
                               "number": "trim_state", "value": int(bool(capability["supported"])),
                               "target_identity": target_identity.as_json() if target_identity else None,
                               "run_manifest": manifest,
                               "trim": capability, "write_bytes": 0, "duration_s": 0,
                               "recorded_utc": dt.datetime.now(dt.UTC).isoformat()}
                write_json(trim_file, trim_record)
                state["cells"][trim_key] = trim_file.name
                write_json(state_path, state)
                results.append(trim_record)
            elif trim_file.is_file():
                results.append(json.loads(trim_file.read_text(encoding="utf-8")))
            else:
                raise StudyError(f"state points to missing trim result {trim_file.name}")
            pending = [job for job in jobs
                       if cell_key(kind, slot_for_cell, rung, job) not in state["cells"]]
            completed_before = [job for job in jobs if cell_key(kind, slot_for_cell, rung, job) in state["cells"] or
                                (destination / (re.sub(r"[^A-Za-z0-9_.-]", "_",
                                                       cell_key(kind, slot_for_cell, rung, job)) + ".json")).exists()]
            if kind == "raw" and pending and not completed_before and not special:
                timeout = ACTIVE_CAP_S - float(state["active_seconds"]) - FIO_DRAIN_RESERVE_S
                if timeout <= 0:
                    raise StudyError("3 hour active-I/O cap would be exceeded by discard")
                discard_key = f"{trim_key}|discard"
                reserve_active(state, discard_key, timeout)
                write_json(state_path, state)
                discard_duration = host.discard(target, kind, offset, size, timeout=timeout,
                                                sample=monitor_host)
                settle_active(state, discard_key, discard_duration)
                write_json(state_path, state)
            for job in jobs:
                key = cell_key(kind, slot_for_cell, rung, job)
                filename = re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json"
                result_path = destination / filename
                existing = state["cells"].get(key)
                if existing:
                    if existing != result_path.name or not result_path.is_file():
                        raise StudyError(f"state points to missing result {result_path.name}")
                    recovered = json.loads(result_path.read_text(encoding="utf-8"))
                    if recovered.get("cell_key") != key:
                        raise StudyError(f"state result does not match planned cell: {result_path.name}")
                    verify_record_identity(recovered, result_path.name)
                    results.append(recovered)
                    remember(job, recovered)
                    continue
                if result_path.exists():
                    recovered = json.loads(result_path.read_text(encoding="utf-8"))
                    if recovered.get("cell_key") != key:
                        raise StudyError(f"orphan result does not match planned cell: {result_path.name}")
                    verify_record_identity(recovered, result_path.name)
                    recovered_bytes = recovered.get("write_bytes", 0)
                    if (isinstance(recovered_bytes, bool) or not isinstance(recovered_bytes, int) or
                            recovered_bytes < 0 or recovered_bytes > job.write_cap):
                        raise StudyError(f"orphan result has malformed write bytes: {result_path.name}")
                    recovered_duration = recovered.get("duration_s", 0)
                    if (isinstance(recovered_duration, bool) or
                            not isinstance(recovered_duration, (int, float)) or
                            not math.isfinite(recovered_duration) or recovered_duration < 0):
                        raise StudyError(f"orphan result has malformed duration: {result_path.name}")
                    state["cells"][key] = result_path.name
                    settle_write(state, key, recovered_bytes)
                    settle_active(state, key, float(recovered_duration))
                    write_json(state_path, state)
                    results.append(recovered)
                    remember(job, recovered)
                    continue
                rate_iops = None
                if job.rate_fraction is not None:
                    base = current_metric.get("read_iops_ceiling" if "read" in job.value else "write_iops_ceiling")
                    if base is None:
                        raise StudyError("latency-under-load cell lacks its preceding QD32 rate")
                    rate_iops = max(1, math.floor(base * job.rate_fraction))
                if job.name == "fill" and current_metric.get("wbps_r_est") is not None:
                    fill_cap = max(1, min(job.write_cap, math.ceil(current_metric["wbps_r_est"] * 10)))
                    job = Job(**{**asdict(job), "write_cap": fill_cap})
                cap = job.write_cap
                if job.writes and cap <= 0:
                    raise StudyError(f"write cell {job.name} has no io_size")
                if job.read_written_extent:
                    read_extent = int(current_metric.get("written_extent_bytes", 0))
                    if read_extent <= 0:
                        raise StudyError(f"read cell {job.name} has no recorded written extent")
                else:
                    read_extent = job.extent_bytes or size
                elapsed_left = ELAPSED_CAP_S - state_elapsed(state)
                active_left = ACTIVE_CAP_S - float(state["active_seconds"])
                if elapsed_left <= FIO_DRAIN_RESERVE_S:
                    raise StudyError("12 hour elapsed study limit reached")
                if active_left <= FIO_DRAIN_RESERVE_S:
                    raise StudyError("3 hour active-I/O cap would be exceeded")
                if job.discard_before:
                    discard_timeout = active_left - FIO_DRAIN_RESERVE_S
                    discard_key = f"{key}|discard"
                    reserve_active(state, discard_key, discard_timeout)
                    write_json(state_path, state)
                    discard_seconds = host.discard(target, kind, offset, job.extent_bytes or size,
                                                   timeout=discard_timeout, sample=monitor_host)
                    settle_active(state, discard_key, discard_seconds)
                    active_left = ACTIVE_CAP_S - float(state["active_seconds"])
                    if active_left <= FIO_DRAIN_RESERVE_S:
                        raise StudyError("3 hour active-I/O cap would be exceeded")
                    write_json(state_path, state)
                job_path = out / "jobs" / (filename[:-5] + ".fio")
                log_prefix = out / "fio-logs" / filename[:-5]
                prepared_key = (rung, job.name)
                text = rendered_jobs[prepared_key]
                prepared_job = planned_jobs[prepared_key]
                prepared_extent = (prepared_job.extent_bytes or
                                   (RUNG[rung]["fill_gb"] * GIB
                                    if prepared_job.read_written_extent else size))
                if (job != prepared_job or rate_iops is not None or
                        (job.read_written_extent and read_extent != prepared_extent)):
                    text = render_job(job, target=target, offset=offset, size=read_extent,
                                      log_prefix=str(log_prefix), rate_iops=rate_iops)
                job_path.parent.mkdir(parents=True, exist_ok=True)
                job_path.write_text(text, encoding="utf-8")
                samples = 0

                collector: Any | None = None

                def sample_once() -> None:
                    nonlocal samples
                    samples += 1
                    monitor_host()

                if job.wait_before_s:
                    if state_elapsed(state) + job.wait_before_s + FIO_DRAIN_RESERVE_S > ELAPSED_CAP_S:
                        raise StudyError("12 hour elapsed study limit reached before required idle wait")
                    host.wait_with_samples(job.wait_before_s, sample_once)
                    if state_elapsed(state) > ELAPSED_CAP_S:
                        raise StudyError("12 hour elapsed study limit reached")
                elapsed_left = ELAPSED_CAP_S - state_elapsed(state)
                active_left = ACTIVE_CAP_S - float(state["active_seconds"])
                runtime_limit = min(job.runtime_s,
                                    math.floor(elapsed_left - FIO_DRAIN_RESERVE_S),
                                    math.floor(active_left - FIO_DRAIN_RESERVE_S))
                if runtime_limit <= 0:
                    raise StudyError("3 hour active-I/O or 12 hour elapsed study limit reached")
                collector_info: dict[str, Any] | None = None
                if fsync_cell == f"{rung}:{job.name}":
                    collector = host.start_fsync(out)
                    collector_info = {"name": collector[0], "output": str(collector[1])}
                try:
                    if job.writes:
                        reserve_write(state, key, cap)
                    reserve_active(state, key, runtime_limit + FIO_DRAIN_RESERVE_S)
                    write_json(state_path, state)
                    started_utc = dt.datetime.now(dt.UTC).isoformat()
                    fio_json, duration = host.execute_fio(job_path, runtime_limit, sample_once)
                finally:
                    if collector is not None:
                        host.stop_fsync(collector)
                completed_utc = dt.datetime.now(dt.UTC).isoformat()
                if duration < 0 or duration > runtime_limit + FIO_DRAIN_RESERVE_S:
                    raise StudyError("fio duration is outside the bounded cell allowance")
                fio_series = host.read_fio_logs(log_prefix)
                estimates = summarize_series(fio_series)
                value, actual_bytes = parse_fio_values(fio_json, job, fio_series)
                write_bytes = actual_bytes if job.writes else 0
                record = {"schema": SCHEMA, "cell_key": key, "cell_set": cell_set, "target_type": kind,
                          "target": target, "device": device, "slot": slot_for_cell, "rung": rung,
                          "target_identity": target_identity.as_json() if target_identity else None,
                          "run_manifest": manifest,
                          "cell": job.name, "number": job.value, "value": value,
                          "started_utc": started_utc, "completed_utc": completed_utc,
                          "io_bytes": actual_bytes, "write_bytes": write_bytes,
                          "read_extent_bytes": read_extent if job.read_written_extent else None,
                          "io_size": job.write_cap if job.writes else None, "duration_s": duration,
                          "runtime_limit_s": runtime_limit,
                          "meter": {"write_bytes_before": state["write_bytes"],
                                    "write_cap_bytes": WRITE_CAP,
                                    "active_seconds_before": state["active_seconds"]},
                          "provenance": {"kind": "live-fio", "driver": "ibx-study"},
                          "samples": samples, "fio": fio_json, "fio_series": fio_series,
                          "estimates": estimates, "fsync": collector_info,
                          "discard_before": job.discard_before,
                          "burst_seen": bool(job.name == "seq1m-write" and rung == "standard" and
                                              actual_bytes >= 40 * GB and duration <= 600)}
                write_json(result_path, record)
                state["cells"][key] = result_path.name
                settle_write(state, key, write_bytes)
                settle_active(state, key, duration)
                write_json(state_path, state)
                remember(job, record)
                results.append(record)
    finally:
        after = host.io_control_snapshot()
        snapshots[cell_set]["after"] = after
        write_json(state_path, state)
        if before != after:
            raise StudyError("IO-control snapshot changed during the study cell-set")
    return {"cell_set": cell_set, "results": len(results), "write_bytes": state["write_bytes"],
            "active_seconds": state["active_seconds"], "state": str(state_path),
            "run_manifest": manifest}


TOLERANCES: dict[str, dict[str, Any]] = {
    "rbps": {"label": "rbps (1 MiB sequential read)", "T": "±50%", "low": -0.50, "high": 0.50,
             "C": 0.50, "C_text": "±25% per week; about ±50% in 2 weeks",
             "visible": ("veryquick", "quick", "standard"),
             "why": "io.cost is inert with QoS off; it is the expectation baseline"},
    "wbps": {"label": "wbps (1 MiB sequential write)", "T": "±50%", "low": -0.50, "high": 0.50,
             "C": 0.50, "C_text": "±25% per week; about ±50% in 2 weeks",
             "visible": ("veryquick", "quick", "standard"),
             "why": "io.cost is inert with QoS off; it is the expectation baseline"},
    "rrandiops": {"label": "rrandiops", "T": "±50%", "low": -0.50, "high": 0.50,
                  "C": 0.50, "C_text": "±25% per week; about ±50% in 2 weeks",
                  "visible": ("veryquick", "quick", "standard"),
                  "why": "QoS off makes the model inert; vrate spans about 2x when QoS is on"},
    "read_write_bps_ratio": {"label": "read/write bandwidth ratio", "T": "±30%", "low": -0.30,
                              "high": 0.30, "C": 0.50,
                              "C_text": "±25% per week; about ±50% in 2 weeks",
                              "visible": ("veryquick", "quick", "standard"),
                              "why": "io.cost weights depend on read/write fairness"},
    "read_write_rand_iops_ratio": {"label": "read/write random IOPS ratio", "T": "±30%",
                                    "low": -0.30, "high": 0.30, "C": 0.50,
                                    "C_text": "±25% per week; about ±50% in 2 weeks",
                                    "visible": ("veryquick", "quick", "standard"),
                                    "why": "io.cost weights depend on read/write fairness"},
    "read_write_seq_iops_ratio": {"label": "read/write sequential IOPS ratio", "T": "±30%",
                                   "low": -0.30, "high": 0.30, "C": 0.50,
                                   "C_text": "±25% per week; about ±50% in 2 weeks",
                                   "visible": ("quick", "standard"),
                                   "why": "io.cost weights depend on read/write fairness"},
    "read_rand_seq_iops_ratio": {"label": "read random/sequential IOPS ratio", "T": "±30%",
                                 "low": -0.30, "high": 0.30, "C": 0.50,
                                 "C_text": "±25% per week; about ±50% in 2 weeks",
                                 "visible": ("quick", "standard"),
                                 "why": "io.cost weights depend on random/sequential fairness"},
    "write_rand_seq_iops_ratio": {"label": "write random/sequential IOPS ratio", "T": "±30%",
                                  "low": -0.30, "high": 0.30, "C": 0.50,
                                  "C_text": "±25% per week; about ±50% in 2 weeks",
                                  "visible": ("quick", "standard"),
                                  "why": "io.cost weights depend on random/sequential fairness"},
    "wrandiops": {"label": "wrandiops", "T": "±50%", "low": -0.50, "high": 0.50,
                  "C": 0.50, "C_text": "±25% per week; about ±50% in 2 weeks",
                  "visible": ("veryquick", "quick", "standard"),
                  "why": "QoS off makes the model inert; vrate spans about 2x when QoS is on"},
    "rseqiops": {"label": "rseqiops", "T": "±50%", "low": -0.50, "high": 0.50,
                 "C": 0.50, "C_text": "±25% per week; about ±50% in 2 weeks",
                 "visible": ("quick", "standard"),
                 "why": "sequential io.cost input; veryquick uses a coarse random-IOPS proxy"},
    "wseqiops": {"label": "wseqiops", "T": "±50%", "low": -0.50, "high": 0.50,
                 "C": 0.50, "C_text": "±25% per week; about ±50% in 2 weeks",
                 "visible": ("quick", "standard"),
                 "why": "sequential io.cost input; veryquick uses a coarse random-IOPS proxy"},
    "rlat": {"label": "rlat (QD1 p95)", "T": "x2", "low": -0.50, "high": 1.0,
             "C": 1.0, "C_text": "x2 bounded QoS band",
             "visible": ("veryquick", "quick", "standard"), "why": "iogovmon walks the bounded QoS knob"},
    "wlat": {"label": "wlat (QD1 p95)", "T": "x2", "low": -0.50, "high": 1.0,
             "C": 1.0, "C_text": "x2 bounded QoS band",
             "visible": ("veryquick", "quick", "standard"), "why": "iogovmon walks the bounded QoS knob"},
    "write_iops_cap": {"label": "write IOPS ceiling", "T": "-30/+20%", "low": -0.30, "high": 0.20,
                        "C": 0.0, "C_text": "none",
                        "visible": ("veryquick", "quick", "standard"), "why": "feeds dev.slice wiops; mdt owns io.max"},
    "read_iops_cap": {"label": "read IOPS ceiling (QD128)", "T": "±30%", "low": -0.30, "high": 0.30,
                       "C": None, "C_text": "expectation re-baselined on re-bench; no bounded C",
                       "visible": ("veryquick", "quick", "standard"),
                       "why": "request-cap expectation is re-baselined by re-bench; the design gives no numeric correction bound"},
    "read_bps_cap": {"label": "read bandwidth ceiling", "T": "-30/+20%", "low": -0.30, "high": 0.20,
                     "C": 0.0, "C_text": "none",
                     "visible": ("veryquick", "quick", "standard"), "why": "feeds dev.slice rbps; mdt owns io.max"},
    "write_bps_cap": {"label": "write bandwidth ceiling", "T": "-30/+20%", "low": -0.30, "high": 0.20,
                      "C": 0.0, "C_text": "none",
                      "visible": ("veryquick", "quick", "standard"),
                      "why": "if e exceeds +20%, caps may not bind; use standard or lower the 0.70 factor"},
    "dirty_bytes": {"label": "writeback dirty_bytes basis (wbps × 1 s)", "T": "x2", "low": -0.50, "high": 1.0,
                    "C": 0.50, "C_text": "±50% within rev 9 bounds",
                    "visible": ("veryquick", "quick", "standard"),
                    "why": "unclamped 1 s basis; effective host-specific floor and ceiling are not supplied by rev 4.1"},
    "latency_under_load_read_30": {"label": "latency under load read at 30%", "T": "x2", "low": -0.50, "high": 1.0,
                                    "C": 1.0, "C_text": "x2 bounded QoS band", "visible": ("veryquick", "quick", "standard"),
                                    "why": "QoS headroom; quick/veryquick use the QD32 p99 proxy; standard measures load directly"},
    "latency_under_load_read_60": {"label": "latency under load read at 60%", "T": "x2", "low": -0.50, "high": 1.0,
                                    "C": 1.0, "C_text": "x2 bounded QoS band", "visible": ("veryquick", "quick", "standard"),
                                    "why": "QoS headroom; quick/veryquick use the QD32 p99 proxy; standard measures load directly"},
    "latency_under_load_read_90": {"label": "latency under load read at 90%", "T": "x2", "low": -0.50, "high": 1.0,
                                    "C": 1.0, "C_text": "x2 bounded QoS band", "visible": ("veryquick", "quick", "standard"),
                                    "why": "QoS headroom; quick/veryquick use the QD32 p99 proxy; standard measures load directly"},
    "latency_under_load_write_30": {"label": "latency under load write at 30%", "T": "x2", "low": -0.50, "high": 1.0,
                                     "C": 1.0, "C_text": "x2 bounded QoS band", "visible": ("veryquick", "quick", "standard"),
                                     "why": "QoS headroom; quick/veryquick use the QD32 p99 proxy; standard measures load directly"},
    "latency_under_load_write_60": {"label": "latency under load write at 60%", "T": "x2", "low": -0.50, "high": 1.0,
                                     "C": 1.0, "C_text": "x2 bounded QoS band", "visible": ("veryquick", "quick", "standard"),
                                     "why": "QoS headroom; quick/veryquick use the QD32 p99 proxy; standard measures load directly"},
    "latency_under_load_write_90": {"label": "latency under load write at 90%", "T": "x2", "low": -0.50, "high": 1.0,
                                     "C": 1.0, "C_text": "x2 bounded QoS band", "visible": ("veryquick", "quick", "standard"),
                                     "why": "QoS headroom; quick/veryquick use the QD32 p99 proxy; standard measures load directly"},
    "burst_64gb": {"label": "burst / sustained rate", "T": "≥40 GB visible", "low": 0, "high": 0,
                    "C": None, "C_text": "knee / capability shift",
                    "visible": ("standard",), "why": "only standard can prove a burst of at least 40 GB"},
    "rewrite_ratio": {"label": "rewrite_ratio", "T": "±30%", "low": -0.30, "high": 0.30,
                      "C": None, "C_text": "provider-degraded only after confirmation; no bounded C",
                      "visible": ("quick", "standard"),
                      "why": "provider-degraded is enabled only after confirmation; the design gives no numeric correction bound"},
    "discard_recovery": {"label": "discard recovery ratio", "T": "±30%", "low": -0.30, "high": 0.30,
                         "C": None, "C_text": "none", "visible": ("quick", "standard"),
                         "why": "canary recovery ratio; compare each rung with the raw-standard reference"},
    "trim_state": {"label": "trim state / discard support", "T": "yes/no", "low": 0, "high": 0,
                   "C": None, "C_text": "none",
                   "visible": ("veryquick", "quick", "standard"), "why": "host fact; not inferred from rate cells"},
}


def median(values: Iterable[float]) -> float | None:
    numbers = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not numbers:
        return None
    middle = len(numbers) // 2
    return numbers[middle] if len(numbers) % 2 else (numbers[middle - 1] + numbers[middle]) / 2


def spread(values: Iterable[float]) -> float | None:
    numbers = [float(value) for value in values if math.isfinite(float(value))]
    if not numbers or min(numbers) == 0:
        return None
    return max(numbers) / min(numbers) - 1


def verdict(result: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    error = result.get("e")
    if error is None:
        return "—"
    low, high = float(spec["low"]), float(spec["high"])
    minimum, maximum = result.get("min_deviation"), result.get("max_deviation")
    if minimum is not None and maximum is not None and minimum >= low and maximum <= high:
        return "✓"
    correction = spec.get("C")
    if (correction is not None and minimum is not None and maximum is not None and
            minimum >= low - float(correction) and maximum <= high + float(correction)):
        return "⚠"
    return "✗"


def _reference_value(row: Mapping[str, Any], raw_standard: list[dict[str, Any]], number: str) -> float | None:
    candidates = [item for item in raw_standard if item["number"] == number]
    if not candidates:
        return None
    observed_hour = _slot_hours(str(row["slot"]))
    closest = min(candidates, key=lambda item: (abs(_slot_hours(item["slot"]) - observed_hour),
                                                _slot_hours(item["slot"])))
    selected_hour = _slot_hours(closest["slot"])
    return median(float(item["value"]) for item in candidates
                  if _slot_hours(item["slot"]) == selected_hour)


def _row_error(rows: list[dict[str, Any]], raw_standard: list[dict[str, Any]],
               number: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    values = [float(row["value"]) for row in rows]
    run_values = [{"slot": row["slot"], "value": value,
                   "x_ref": _reference_value(row, raw_standard, number)}
                  for row, value in zip(rows, values)]
    if not values:
        return {"e": None, "bias": None, "spread": None, "min_deviation": None,
                "max_deviation": None, "verdict": "—", "run_values": []}
    if number == "trim_state":
        references = [_reference_value(row, raw_standard, number) for row in rows]
        deviations = [0.0 if reference is not None and value == reference else 1.0
                      for row, value, reference in zip(rows, values, references)]
        error = {"e": max(deviations), "bias": median(deviations), "spread": None,
                 "min_deviation": min(deviations), "max_deviation": max(deviations),
                 "value": bool(values[0]), "run_values": run_values}
        error["verdict"] = "✓" if error["e"] == 0 else "✗"
        return error
    if number == "burst_64gb":
        visible = min(values) >= 1
        return {"e": None, "bias": None, "spread": spread(values), "min_deviation": None,
                "max_deviation": None, "visible": visible, "run_values": run_values,
                "verdict": "✓" if visible else "✗"}
    deviations = []
    for row, value in zip(rows, values):
        reference = _reference_value(row, raw_standard, number)
        if reference is None or reference == 0:
            continue
        deviations.append(value / reference - 1)
    if not deviations:
        return {"e": None, "bias": None, "spread": spread(values), "min_deviation": None,
                "max_deviation": None, "verdict": "—", "run_values": run_values}
    result = {"e": max(abs(value) for value in deviations), "bias": median(deviations),
              "spread": spread(values), "min_deviation": min(deviations),
              "max_deviation": max(deviations), "run_values": run_values}
    result["verdict"] = verdict(result, spec)
    return result


def load_results(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((root / "cells").glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudyError(f"invalid cell result {path.name}: {exc}") from exc
        required = ("target_type", "slot", "rung", "number", "value", "cell")
        if not isinstance(row, dict) or any(key not in row for key in required):
            raise StudyError(f"cell result {path.name} is missing required fields")
        if any(not isinstance(row[key], str) or not row[key] for key in required if key != "value"):
            raise StudyError(f"cell result {path.name} has malformed identity fields")
        value = row["value"]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value)):
            raise StudyError(f"cell result {path.name} has a malformed measurement")
        rows.append(row)
    original = list(rows)
    for row in original:
        if row["rung"] in ("quick", "veryquick") and row["cell"] in ("rate-read-q32", "rate-write-q32"):
            fio_doc = row.get("fio")
            fio_jobs = fio_doc.get("jobs") if isinstance(fio_doc, dict) else None
            direction = "read" if row["cell"] == "rate-read-q32" else "write"
            stats = fio_jobs[0].get(direction) if isinstance(fio_jobs, list) and len(fio_jobs) == 1 else None
            percentiles = stats.get("clat_ns", {}).get("percentile", {}) if isinstance(stats, dict) else {}
            raw_p99 = percentiles.get("99.000000") if isinstance(percentiles, dict) else None
            if (isinstance(raw_p99, bool) or not isinstance(raw_p99, (int, float)) or
                    not math.isfinite(raw_p99) or raw_p99 < 0):
                raise StudyError(f"QD32 proxy cell {row['cell']} is missing a valid fio p99 percentile")
            for pct in (30, 60, 90):
                proxy_number = f"latency_under_load_{direction}_{pct}"
                rows.append({**row, "number": proxy_number, "value": float(raw_p99),
                             "cell": f"derived:{proxy_number}:qd32-p99-proxy", "write_bytes": 0,
                             "latency_proxy": "QD32 p99"})
        if row["number"] == "rate_write_q32":
            rows.append({**row, "number": "write_iops_cap"})
        if row["number"] == "rate_read_q128":
            rows.append({**row, "number": "read_iops_cap"})
        if row["number"] == "rbps":
            rows.append({**row, "number": "read_bps_cap"})
        if row["number"] == "wbps":
            rows.append({**row, "number": "write_bps_cap"})
            rows.append({**row, "number": "dirty_bytes"})
            if row["rung"] == "standard":
                rows.append({**row, "number": "burst_64gb",
                             "value": int(bool(row.get("burst_seen")) and
                                           float(row.get("duration_s", float("inf"))) <= 600)})
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in original:
        groups.setdefault((row["target_type"], row["slot"], row["rung"]), []).append(row)
    for group in groups.values():
        cells = {row["cell"]: row for row in group}
        ratios = (
            ("read_write_bps_ratio", "seq1m-read", "seq1m-write"),
            ("read_write_rand_iops_ratio", "rand4k-read", "rand4k-write"),
            ("read_write_seq_iops_ratio", "seq4k-read", "seq4k-write"),
            ("read_rand_seq_iops_ratio", "rand4k-read", "seq4k-read"),
            ("write_rand_seq_iops_ratio", "rand4k-write", "seq4k-write"),
        )
        for number, numerator_cell, denominator_cell in ratios:
            numerator = cells.get(numerator_cell)
            denominator = cells.get(denominator_cell)
            if numerator is not None and denominator is not None and denominator["value"] != 0:
                rows.append({**numerator, "number": number,
                             "value": float(numerator["value"]) / float(denominator["value"]),
                             "cell": f"derived:{number}", "write_bytes": 0})
        if "seq1m-write" in cells:
            rewrites = [row["value"] for row in group
                        if row["cell"].startswith("rewrite-pass-") and row["number"] == "canary_bps"]
            if rewrites and cells["seq1m-write"]["value"]:
                rows.append({**cells["seq1m-write"], "number": "rewrite_ratio",
                             "value": median(rewrites) / float(cells["seq1m-write"]["value"]),
                             "write_bytes": 0})
            fresh = cells.get("fresh4k-write")
            recovery = cells.get("discard-recovery4k-write")
            if fresh and recovery and fresh["value"]:
                rows.append({**fresh, "number": "discard_recovery",
                             "value": float(recovery["value"]) / float(fresh["value"]), "write_bytes": 0})
    return rows


def load_run_manifest(root: Path) -> dict[str, Any] | None:
    path = root / STATE_NAME
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudyError(f"study state is unreadable: {exc}") from exc
    manifest = state.get("run_manifest") if isinstance(state, dict) else None
    if (not isinstance(state, dict) or isinstance(state.get("schema"), bool) or
            state.get("schema") != STATE_SCHEMA or not isinstance(manifest, dict) or
            isinstance(manifest.get("schema"), bool) or
            manifest.get("schema") != RUN_MANIFEST_SCHEMA or
            not isinstance(manifest.get("target_safety"), dict) or
            not isinstance(manifest.get("target_safety_attempts"), dict) or
            not isinstance(manifest.get("stop_policy"), dict)):
        raise StudyError("study state run manifest is missing or malformed")
    return manifest


def raw_standard_needed(root: Path) -> bool:
    """Apply the 6.2 t0+6h standard rule to quick/veryquick evidence."""
    rows = load_results(root)
    standards = [row for row in rows if row["target_type"] == "raw" and
                 row["rung"] == "standard" and row["slot"] == "t0"]
    if not standards:
        return True
    observed = [row for row in rows if row["target_type"] == "raw" and
                row["rung"] in ("quick", "veryquick") and row["slot"] in ("t0", "t3h")]
    for rung in ("quick", "veryquick"):
        expected = {job.name for job in rung_jobs(rung)} | {"trim-state"}
        if any(not {"t0", "t3h"}.issubset({row["slot"] for row in observed
                                                if row["rung"] == rung and row["cell"] == cell})
               for cell in expected):
            return True
    for row in observed:
        spec = TOLERANCES.get(row["number"])
        if spec is None or row["number"] in ("trim_state", "burst_64gb"):
            continue
        reference = _reference_value(row, standards, row["number"])
        if reference in (None, 0):
            return True
        deviation = float(row["value"]) / reference - 1
        for boundary in (float(spec["low"]), float(spec["high"])):
            if boundary == 0:
                continue
            near = sorted((boundary / 1.5, boundary * 1.5))
            if near[0] <= deviation <= near[1]:
                return True
    by_metric: dict[tuple[str, str], list[float]] = {}
    for row in observed:
        if row["number"] in TOLERANCES and row["number"] not in ("trim_state", "burst_64gb"):
            by_metric.setdefault((row["rung"], row["number"]), []).append(float(row["value"]))
    for (_, number), values in by_metric.items():
        movement = spread(values)
        spec = TOLERANCES[number]
        boundary = max(abs(float(spec["low"])), float(spec["high"]))
        if movement is not None and movement > boundary:
            return True
    return False


def _slot_hours(slot: str) -> float:
    return {"t0": 0, "t1h": 1, "t3h": 3, "t6h": 6, "t9h": 9, "t12h": 11,
            "t12h2": 11.5, "t4h": 4}.get(slot, 0)


def adaptive_raw_repeat(root: Path, rung: str, required: int) -> tuple[bool, bool]:
    """Return (enough prior observations, repeat needed) for the optional t+12h slots."""
    rows = [row for row in load_results(root) if row["target_type"] == "raw"]
    standard = [row for row in rows if row["rung"] == "standard"]
    observed = [row for row in rows if row["rung"] == rung]
    standard_slots = {row["slot"] for row in standard}
    if "t0" not in standard_slots or (raw_standard_needed(root) and "t6h" not in standard_slots):
        return False, False
    expected_slots = {"t0", "t3h", "t6h", "t9h"}
    if required == 5:
        expected_slots.add("t12h")
    expected_cells = {job.name for job in rung_jobs(rung)} | {"trim-state"}
    if any(not expected_slots.issubset({row["slot"] for row in observed if row["cell"] == cell})
           for cell in expected_cells):
        return False, False
    numbers = {row["number"] for row in observed if row["number"] in TOLERANCES}
    saw_data = False
    for number in numbers:
        selected = [row for row in sorted(observed, key=lambda item: _slot_hours(item["slot"]))
                    if row["number"] == number]
        saw_data = True
        errors = []
        for row in selected[:required]:
            reference = _reference_value(row, standard, number)
            if reference not in (None, 0):
                errors.append(1 + abs(float(row["value"]) / reference - 1))
        if len(errors) >= required and needs_extra_repeat(errors, 1,
                                                           latency="lat" in number):
            return True, True
    return saw_data, False


def part_c_assessment(root: Path) -> dict[str, Any]:
    """Apply the section 6.3 rewrite, idle, and discard recovery criteria."""
    rows = [row for row in load_results(root)
            if row["target_type"] == "raw" and row["rung"] == "part-c"]
    cells = {row["cell"]: row for row in rows}
    needed = ("c-pass1-seq", "c-pass4-seq", "c-pass1-rand", "c-idle-probe", "c-idle-seq",
              "c-recover10-rand", "c-recover10-seq")
    if any(name not in cells for name in needed):
        return {"status": "incomplete", "confirmed": False}
    quick_rates = [float(row["value"]) for row in load_results(root)
                   if row["target_type"] == "raw" and row["rung"] == "quick"
                   and row["number"] == "wbps"]
    raw_band = noise_band(quick_rates) if quick_rates else 0.05
    pass1 = float(cells["c-pass1-seq"]["value"])
    pass4 = float(cells["c-pass4-seq"]["value"])
    fresh_iops = float(cells["c-pass1-rand"]["value"])
    idle_iops = float(cells["c-idle-probe"]["value"])
    idle_bps = float(cells["c-idle-seq"]["value"])
    recovered_iops = float(cells["c-recover10-rand"]["value"])
    recovered_bps = float(cells["c-recover10-seq"]["value"])
    rewrite_ratio = pass4 / pass1 if pass1 else None
    recovery_ratio = recovered_iops / fresh_iops if fresh_iops else None
    idle_ratio = idle_iops / fresh_iops if fresh_iops else None
    recovery_bps_ratio = recovered_bps / pass1 if pass1 else None
    idle_bps_ratio = idle_bps / pass1 if pass1 else None
    rewrite = bool(rewrite_ratio is not None and rewrite_ratio < 0.8 - raw_band)
    idle_low = bool(idle_ratio is not None and idle_ratio < 0.9 and
                    idle_bps_ratio is not None and idle_bps_ratio < 0.9)
    recovered = bool(recovery_ratio is not None and recovery_ratio >= 0.9 and
                     recovery_bps_ratio is not None and recovery_bps_ratio >= 0.9)
    confirmed = rewrite and idle_low and recovered
    return {"status": "confirmed" if confirmed else "not reproduced", "confirmed": confirmed,
            "rewrite_ratio": rewrite_ratio, "raw_quick_spread": spread(quick_rates),
            "rewrite_threshold": 0.8 - raw_band, "rewrite_below_threshold": rewrite,
            "idle_ratio": idle_ratio, "idle_bps_ratio": idle_bps_ratio, "idle_stays_low": idle_low,
            "recovery_ratio": recovery_ratio, "recovery_bps_ratio": recovery_bps_ratio,
            "recovered_by_10m": recovered}


def file_standard_needed(root: Path, target_kind: str) -> bool:
    """Only authorize the optional file standard when a quick result is borderline."""
    rows = load_results(root)
    for rung in ("quick", "veryquick"):
        expected_cells = {"trim-state"} | {job.name for job in rung_jobs(rung)}
        for cell in expected_cells:
            if not any(row["target_type"] == target_kind and row["slot"] == "t9h" and
                       row["rung"] == rung and row["cell"] == cell for row in rows):
                raise StudyError(f"{target_kind} quick and veryquick results must complete before standard")
    report = analysis(root)
    table = report["tables"].get(target_kind, {})
    seen = False
    for number, row in table.items():
        if number in ("trim_state", "discard_recovery", "burst_64gb"):
            continue
        quick = row["cells"]["quick"]
        error = quick.get("e")
        if error is None:
            continue
        seen = True
        tolerance = max(abs(float(row["T_low"])), float(row["T_high"]))
        correction = row["C"]
        upper = tolerance + (float(correction) if correction is not None else 0)
        if tolerance / 1.5 <= float(error) <= upper:
            return True
    if not seen:
        raise StudyError(f"{target_kind} quick results are required before its optional standard run")
    return False


def analysis(root: Path) -> dict[str, Any]:
    rows = load_results(root)
    if not rows:
        raise StudyError("no recorded cell results to analyze")
    manifest = load_run_manifest(root)
    targets = sorted({row["target_type"] for row in rows} | {"raw", "ext4-file", "btrfs-file"})
    numbers = sorted(TOLERANCES)
    report: dict[str, Any] = {
        "tables": {}, "moving_target": {}, "part_c": part_c_assessment(root),
        "study_decisions": {
            "D-IBX-001": {
                "status": "study decision",
                "file_standard_cutoff": "T/1.5 <= e <= T+C",
                "reason": "rev 4.1 does not give a numeric cutoff for optional file-standard runs",
            },
            "D-IBX-002": {
                "status": "study decision",
                "root_disk_exception": ("operator consent via --allow-system-disk-partition, bound to "
                                         "current host and target facts by a per-run drift-check token"),
                "reason": ("operator-requested controller policy dated 2026-11-13; "
                           "system-disk and sibling-swap activity remains part of the measurement context"),
            },
        },
        "run_manifest": manifest,
    }
    raw_standard = [row for row in rows if row["target_type"] == "raw" and row["rung"] == "standard"]
    for number in numbers:
        vals = [float(row["value"]) for row in raw_standard if row["number"] == number]
        report["moving_target"][number] = spread(vals)
    for target in targets:
        table: dict[str, Any] = {}
        target_rows = [row for row in rows if row["target_type"] == target]
        for number, spec in TOLERANCES.items():
            cells: dict[str, Any] = {}
            for rung in ("veryquick", "quick", "standard"):
                if rung not in spec["visible"]:
                    cells[rung] = {"e": None, "bias": None, "spread": None, "verdict": "—"}
                    continue
                observed = [row for row in target_rows if row["number"] == number and row["rung"] == rung]
                result = _row_error(observed, raw_standard, number, spec)
                if result["e"] is not None and number not in ("trim_state", "discard_recovery", "burst_64gb"):
                    control_values = [float(row["value"]) for row in raw_standard
                                      if row["number"] == number]
                    noise = noise_band(control_values, latency="lat" in number)
                    count = len(observed)
                    acceptable = max(abs(float(spec["low"])), float(spec["high"]))
                    correction = spec.get("C")
                    if ((correction is not None and result["e"] > acceptable + float(correction)) or
                            (count >= 4 and result["e"] <= acceptable / 2)):
                        result["repeat_action"] = "verdict-fixed"
                    else:
                        result["repeat_action"] = adaptive_repeat_action(result["e"], noise, count)
                    if target == "raw" and rung == "standard":
                        result["is_reference"] = True
                cells[rung] = result
            file_bias: dict[str, float | None] = {}
            file_band: dict[str, float | None] = {}
            if target != "raw":
                for rung in ("veryquick", "quick", "standard"):
                    file_rows = [row for row in target_rows if row["number"] == number and row["rung"] == rung]
                    raw_rows = [row for row in rows if row["target_type"] == "raw" and
                                row["number"] == number and row["rung"] == rung]
                    biases = []
                    for file_row in file_rows:
                        same_slot = [row for row in raw_rows if row["slot"] == file_row["slot"]]
                        raw_value = median(float(row["value"]) for row in same_slot)
                        if raw_value:
                            biases.append(float(file_row["value"]) / raw_value - 1)
                    file_bias[rung] = median(biases)
                    file_band[rung] = spread(float(row["value"]) for row in raw_rows)
            table[number] = {"label": spec["label"], "T": spec["T"], "C": spec["C"],
                             "C_text": spec.get("C_text", "none"),
                             "T_low": spec["low"], "T_high": spec["high"],
                             "cells": cells, "V": report["moving_target"].get(number),
                             "file_bias_vs_raw": file_bias, "file_bias_raw_spread": file_band,
                             "reason": spec["why"]}
        report["tables"][target] = table
    return report


def render_analysis(result: Mapping[str, Any]) -> str:
    lines: list[str] = []
    manifest = result.get("run_manifest")
    if isinstance(manifest, Mapping):
        lines.extend(("## Run context", ""))
        policy = manifest.get("stop_policy", {})
        if isinstance(policy, Mapping):
            lines.append("Stop policy: memory PSI full avg10 above "
                         f"{policy.get('memory_psi_full_avg10_max_percent', 'unknown')}% for three "
                         "consecutive samples, MemAvailable and free-space floors, and a host-liveness "
                         f"probe above {policy.get('max_probe_latency_seconds', 'unknown')} s for "
                         f"{policy.get('probe_consecutive_samples', 'unknown')} consecutive samples "
                         f"at {policy.get('probe_interval_seconds', 'unknown')} s intervals. "
                         "I/O PSI is recorded as advisory.")
        safety = manifest.get("target_safety", {})
        if isinstance(safety, Mapping) and isinstance(safety.get("raw"), Mapping):
            raw = safety["raw"]
            roots = ", ".join(raw.get("root_fs_devices", [])) or "unknown"
            swaps = ", ".join(raw.get("sibling_swap_devices", [])) or "none"
            mounts = ", ".join(raw.get("system_mounts_on_disk", [])) or "none"
            if raw.get("root_disk_shared") is True:
                lines.append("Raw target risk: **acknowledged system-disk run**. The benchmark shares "
                             "the running system disk; results include system filesystem and sibling-swap "
                             "activity and are not a quiet-disk study.")
            else:
                lines.append("Raw target risk: target disk was separate from the running system disk; "
                             "no system-disk acknowledgement was required.")
            lines.extend((f"Host: `{raw.get('hostname', 'unknown')}`; target disk: "
                          f"`{raw.get('target_disk', 'unknown')}`; root filesystem device(s): `{roots}`; "
                          f"system mount(s) on target disk: `{mounts}`; sibling swap device(s): `{swaps}`.",
                          f"System-disk drift-check token: `{raw.get('system_disk_acknowledgement') or 'not required'}`.", ""))
        else:
            lines.extend(("Raw target system-disk facts were not recorded in this manifest.", ""))
    for target, table in result["tables"].items():
        lines.extend((f"## {target}", "", "| Number | T | C | veryquick | quick (default) | standard | V | Reason line |",
                      "|---|---:|---:|---:|---:|---:|---:|---|"))
        rung_use: dict[str, dict[str, list[str]]] = {rung: {"usable": [], "correct": [], "no": []}
                                                    for rung in ("veryquick", "quick", "standard")}
        for key, row in table.items():
            formatted = {}
            for rung, value in row["cells"].items():
                err = value["e"]
                if key == "burst_64gb":
                    detail = ("≥40 GB visible " if value.get("visible") else "not visible ") + value["verdict"]
                    if value["verdict"] == "—":
                        detail = "—"
                elif key == "trim_state":
                    detail = ("discard supported " if value.get("value") else "discard unsupported ") + value["verdict"]
                elif value.get("is_reference"):
                    spread_value = value.get("spread")
                    spread_text = "—" if spread_value is None else f"{spread_value:.1%}"
                    detail = f"ref ({value['bias']:+.1%}/{spread_text})"
                else:
                    spread_value = value.get("spread")
                    spread_text = "—" if spread_value is None else f"{spread_value:.1%}"
                    bias_value = value.get("bias")
                    bias_text = "—" if bias_value is None else f"{bias_value:+.1%}"
                    detail = "—" if err is None else f"{err:.1%} ({bias_text}/{spread_text}) {value['verdict']}"
                formatted[rung] = detail
                bucket = {"✓": "usable", "⚠": "correct", "✗": "no"}.get(value["verdict"])
                if bucket:
                    rung_use[rung][bucket].append(row["label"])
            v = row["V"]
            v_text = "—" if v is None else f"{v:.1%}"
            reason = row["reason"]
            if v is not None and v > max(abs(float(row["T_low"])), float(row["T_high"])):
                reason += "; moving target dominates: no rung is accurate; rely on iogovmon and conservative factors"
            if isinstance(row["file_bias_vs_raw"], dict):
                bias_text = []
                for rung, bias in row["file_bias_vs_raw"].items():
                    if bias is not None:
                        band = row["file_bias_raw_spread"][rung]
                        bias_text.append(f"{rung} {bias:+.1%} vs raw at same slot (raw band {band:.1%})"
                                         if band is not None else f"{rung} {bias:+.1%} vs raw at same slot")
                if bias_text:
                    reason += "; file bias: " + ", ".join(bias_text)
            lines.append(f"| `{key}` | {row['T']} | {row['C_text']} | "
                         f"{formatted['veryquick']} | {formatted['quick']} | {formatted['standard']} | {v_text} | {reason} |")
        for rung, groups in rung_use.items():
            lines.append("")
            lines.append(f"**{rung}: usable for:** {', '.join(groups['usable']) or '—'}; "
                         f"**iogovmon must correct:** {', '.join(groups['correct']) or '—'}; "
                         f"**not usable for:** {', '.join(groups['no']) or '—'}.")
        lines.append("")
    part_c = result.get("part_c", {})
    lines.extend(("## (c) rewrite/fstrim", "", f"Status: **{part_c.get('status', 'incomplete')}**."))
    if part_c.get("rewrite_ratio") is not None:
        quick_spread = part_c.get("raw_quick_spread")
        quick_spread_text = "—" if quick_spread is None else f"{quick_spread:.1%}"
        idle_ratio = part_c.get("idle_ratio")
        idle_text = "—" if idle_ratio is None else f"{idle_ratio:.1%}"
        recovery_ratio = part_c.get("recovery_ratio")
        recovery_text = "—" if recovery_ratio is None else f"{recovery_ratio:.1%}"
        lines.append(f"Pass-4/pass-1: {part_c['rewrite_ratio']:.1%} "
                     f"(threshold below {part_c['rewrite_threshold']:.1%}; "
                     f"raw quick spread {quick_spread_text}).")
        lines.append(f"Idle probe: {idle_text} of pass 1; "
                     f"discard recovery at +10m: {recovery_text} of pass 1.")
    decisions = result.get("study_decisions", {})
    if decisions:
        lines.extend(("", "## Study decisions"))
        for decision_id, facts in decisions.items():
            if decision_id == "D-IBX-001":
                lines.append(f"- **{decision_id}** ({facts['status']}): file-standard runs are selected when "
                             f"`{facts['file_standard_cutoff']}`; {facts['reason']}.")
            else:
                lines.append(f"- **{decision_id}** ({facts['status']}): "
                             f"{facts['root_disk_exception']}; {facts['reason']}.")
    return "\n".join(lines).rstrip() + "\n"


def parse_size(text: str) -> int:
    match = re.fullmatch(r"([0-9]+)(B|KB|MB|GB|KiB|MiB|GiB)?", text, re.IGNORECASE)
    if not match:
        raise argparse.ArgumentTypeError("size must be a non-negative integer with optional B/KB/MB/GB/KiB/MiB/GiB")
    amount = int(match.group(1))
    unit = (match.group(2) or "B").lower()
    factor = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3,
              "kib": 1024, "mib": 1024**2, "gib": 1024**3}[unit]
    return amount * factor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="print the bounded study schedule and empty meter")
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--out", type=Path, help="read the existing study state for live meter values")
    ack = sub.add_parser("system-disk-ack", help="derive the current system-disk drift-check token")
    ack.add_argument("--target", required=True)
    ack.add_argument("--device", required=True, help="block-device name used by diskstats")
    ack.add_argument("--confirm-target", required=True,
                     help="raw target identity: PATH:SIZE_BYTES:PARTUUID")
    run = sub.add_parser("run", help="run one scheduled cell-set; safe to resume")
    run.add_argument("cell_set", choices=tuple(CELL_SETS))
    run.add_argument("--target", required=True)
    run.add_argument("--device", required=True, help="block-device name used by diskstats")
    run.add_argument("--confirm-target", help="raw target acknowledgement: PATH:SIZE_BYTES:PARTUUID")
    run.add_argument("--target-type", choices=("raw", "ext4-file", "btrfs-file"))
    run.add_argument("--offset", type=parse_size, required=True)
    run.add_argument("--size", type=parse_size, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--max-memory-psi", type=float, required=True,
                     help="study-host policy, memory PSI full avg10 percent")
    run.add_argument("--min-memavailable", type=parse_size, required=True, help="study-host policy floor")
    run.add_argument("--min-free", type=parse_size, required=True,
                     help="study-host policy floor for free space on output and target filesystems")
    run.add_argument("--max-probe-latency-seconds", type=float, required=True,
                     help="study-host policy limit for the root-fsync, sshd and cockpit probe")
    run.add_argument("--probe-consecutive-samples", type=int, required=True,
                     help="study-host policy count of consecutive slow or failed liveness probes")
    run.add_argument("--probe-interval-seconds", type=float,
                     default=DEFAULT_LIVENESS_PROBE_INTERVAL_S,
                     help="seconds between independent liveness probes (default: 10)")
    run.add_argument("--allow-system-disk-partition", action="store_true",
                     help="operator consent to allow a raw partition sharing the running system disk")
    run.add_argument("--system-disk-ack",
                     help="per-run drift-check token: HOSTNAME:DISK:ROOT-DEVICE[,ROOT-DEVICE...]:PARTUUID")
    run.add_argument("--confirm-quiet-window", action="store_true", required=True,
                     help="confirm scheduled idle window; the driver checks required timers")
    run.add_argument("--dry-run", action="store_true",
                     help="validate the target, schedule and every fio job without writing files or target data")
    run.add_argument("--fsync-cell", help="wrap exactly this cell with mdt-cockpit/scripts/iomon-bpf/run.sh")
    analyze_parser = sub.add_parser("analyze", help="compute error, bias, spread and rung tables")
    analyze_parser.add_argument("results", type=Path)
    analyze_parser.add_argument("--output", type=Path)
    analyze_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            print_plan(args.json, args.out / STATE_NAME if args.out else None)
            return 0
        if args.command == "system-disk-ack":
            print(derive_system_disk_ack(args.target, args.device, args.confirm_target))
            return 0
        if args.command == "analyze":
            result = analysis(args.results)
            content = json.dumps(result, indent=2, sort_keys=True) + "\n" if args.json else render_analysis(result)
            if args.output:
                args.output.write_text(content, encoding="utf-8")
            else:
                print(content, end="")
            return 0
        result = run_study(args.cell_set, target=args.target, device=args.device, out=args.out,
                           target_kind=args.target_type, offset=args.offset, size=args.size,
                           mem_psi_max=args.max_memory_psi,
                           min_memavailable=args.min_memavailable, min_free=args.min_free,
                           max_probe_latency_s=args.max_probe_latency_seconds,
                           probe_consecutive_samples=args.probe_consecutive_samples,
                           probe_interval_s=args.probe_interval_seconds,
                           fsync_cell=args.fsync_cell, quiet_window_confirmed=args.confirm_quiet_window,
                           confirm_target=args.confirm_target,
                           allow_system_disk_partition=args.allow_system_disk_partition,
                           system_disk_ack=args.system_disk_ack, dry_run=args.dry_run)
        print(json.dumps(result, sort_keys=True))
        return 0
    except StudyError as exc:
        print(f"ibx-study: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

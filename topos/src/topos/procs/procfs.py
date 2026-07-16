"""Low-level typed ``/proc`` readers for the P90 process sampler.

Every reader returns ``(value | None, MetricSource)``, the same typed-state
convention ``topos.collect.cgroup`` already uses: a vanished process
(``ProcessLookupError``/``FileNotFoundError`` racing exit) or hidepid/
permission loss is reported as ``unavail_kernel``/``unavail_perm``, never as a
zero value (Required contract 1 / oracle O6). Nothing here aggregates or
derives rates — that is the sampler's job once it has two samples.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from topos.model import MetricSource

CLK_TCK: float = float(os.sysconf("SC_CLK_TCK")) if hasattr(os, "sysconf") else 100.0
PAGE_SIZE: int = int(os.sysconf("SC_PAGE_SIZE")) if hasattr(os, "sysconf") else 4096

_CPU_LINE_RE = re.compile(r"^cpu\d+ ")


def _unavail_for(exc: OSError) -> MetricSource:
    return "unavail_perm" if isinstance(exc, PermissionError) else "unavail_kernel"


def discover_pids(proc_root: Path) -> list[int]:
    """List every visible PID directory under ``proc_root``, sorted ascending."""
    try:
        names = os.listdir(proc_root)
    except OSError:
        return []
    return sorted(int(name) for name in names if name.isdigit())


@dataclass(frozen=True)
class StatFields:
    pid: int
    comm: str
    state: str
    ppid: int
    utime: int
    stime: int
    minflt: int
    majflt: int
    num_threads: int
    starttime: int
    blkio_ticks: int | None


def _stat_field(rest: list[str], field_number: int) -> str:
    # rest[0] is field 3 (state); pid (1) and comm (2) are consumed separately.
    return rest[field_number - 3]


def read_stat(pid_dir: Path) -> tuple[StatFields | None, MetricSource]:
    try:
        text = (pid_dir / "stat").read_text()
    except OSError as exc:
        return None, _unavail_for(exc)
    open_paren = text.find("(")
    close_paren = text.rfind(")")
    if open_paren == -1 or close_paren == -1 or close_paren < open_paren:
        return None, "unavail_kernel"
    pid_part = text[:open_paren].strip()
    comm = text[open_paren + 1 : close_paren]
    rest = text[close_paren + 1 :].split()
    try:
        pid = int(pid_part)
        state = _stat_field(rest, 3)
        ppid = int(_stat_field(rest, 4))
        minflt = int(_stat_field(rest, 10))
        majflt = int(_stat_field(rest, 12))
        utime = int(_stat_field(rest, 14))
        stime = int(_stat_field(rest, 15))
        num_threads = int(_stat_field(rest, 20))
        starttime = int(_stat_field(rest, 22))
    except (IndexError, ValueError):
        return None, "unavail_kernel"
    blkio_ticks: int | None = None
    try:
        blkio_ticks = int(_stat_field(rest, 42))
    except (IndexError, ValueError):
        blkio_ticks = None
    return (
        StatFields(
            pid=pid,
            comm=comm,
            state=state,
            ppid=ppid,
            utime=utime,
            stime=stime,
            minflt=minflt,
            majflt=majflt,
            num_threads=num_threads,
            starttime=starttime,
            blkio_ticks=blkio_ticks,
        ),
        "exact",
    )


@dataclass(frozen=True)
class IoFields:
    read_bytes: int | None
    write_bytes: int | None
    cancelled_write_bytes: int | None


def read_io(pid_dir: Path) -> tuple[IoFields | None, MetricSource]:
    try:
        text = (pid_dir / "io").read_text()
    except OSError as exc:
        return None, _unavail_for(exc)
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        try:
            values[key.strip()] = int(rest.strip())
        except ValueError:
            continue
    return (
        IoFields(
            read_bytes=values.get("read_bytes"),
            write_bytes=values.get("write_bytes"),
            cancelled_write_bytes=values.get("cancelled_write_bytes"),
        ),
        "exact",
    )


@dataclass(frozen=True)
class StatusFields:
    uid: int | None
    vm_rss: int | None
    vm_size: int | None
    vm_swap: int | None
    threads: int | None
    voluntary_ctxt_switches: int | None
    nonvoluntary_ctxt_switches: int | None


def read_status(pid_dir: Path) -> tuple[StatusFields | None, MetricSource]:
    try:
        text = (pid_dir / "status").read_text()
    except OSError as exc:
        return None, _unavail_for(exc)
    kv: dict[str, str] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        kv[key.strip()] = rest.strip()

    def _kb(name: str) -> int | None:
        raw = kv.get(name)
        if raw is None:
            return None
        parts = raw.split()
        if not parts:
            return None
        try:
            return int(parts[0]) * 1024
        except ValueError:
            return None

    def _int(name: str) -> int | None:
        raw = kv.get(name)
        if raw is None:
            return None
        try:
            return int(raw.split()[0])
        except (ValueError, IndexError):
            return None

    uid_line = kv.get("Uid")
    uid = None
    if uid_line:
        try:
            uid = int(uid_line.split()[0])
        except (ValueError, IndexError):
            uid = None

    return (
        StatusFields(
            uid=uid,
            vm_rss=_kb("VmRSS"),
            vm_size=_kb("VmSize"),
            vm_swap=_kb("VmSwap"),
            threads=_int("Threads"),
            voluntary_ctxt_switches=_int("voluntary_ctxt_switches"),
            nonvoluntary_ctxt_switches=_int("nonvoluntary_ctxt_switches"),
        ),
        "exact",
    )


def read_cmdline(pid_dir: Path) -> tuple[str | None, MetricSource]:
    try:
        raw = (pid_dir / "cmdline").read_bytes()
    except OSError as exc:
        return None, _unavail_for(exc)
    text = raw.decode("utf-8", errors="replace")
    parts = [p for p in text.split("\0") if p]
    return (" ".join(parts) if parts else None), "exact"


def read_cgroup_path(pid_dir: Path) -> tuple[str | None, MetricSource]:
    """Return the process's unified (v2) cgroup path relative to the mount root.

    The root cgroup itself is reported as ``""`` to match the existing
    ``Entity.key`` convention (``cgroup_root`` when ``entity_key == ""``).
    """
    try:
        text = (pid_dir / "cgroup").read_text()
    except OSError as exc:
        return None, _unavail_for(exc)
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            return parts[2].lstrip("/"), "exact"
    return None, "unavail_kernel"


def read_boot_time(proc_root: Path) -> float | None:
    try:
        text = (proc_root / "stat").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("btime "):
            try:
                return float(line.split()[1])
            except (ValueError, IndexError):
                return None
    return None


def count_logical_cpus(proc_root: Path) -> int:
    try:
        text = (proc_root / "stat").read_text()
    except OSError:
        return 1
    count = sum(1 for line in text.splitlines() if _CPU_LINE_RE.match(line))
    return max(1, count)

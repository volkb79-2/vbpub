"""Read time-aligned resource pressure for native mutation liveness.

The liveness watchdog must not turn a host or cgroup scheduling/resource stall
into a functional ``hung`` result.  This module derives the candidate's
visible cgroup from procfs and reads cumulative PSI/cpu-stat counters there
and from the visible host procfs.  Missing, malformed, reset, or namespace-
mismatched observations are *unknown*, never a synthetic zero-pressure fact.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .guards import is_int_at_least


RESOURCE_SNAPSHOT_SCHEMA_VERSION = 1
_PRESSURE_NAMES = ("cpu", "memory", "io")
_PRESSURE_ROWS = {
    "cpu": ("some",),
    "memory": ("some", "full"),
    "io": ("some", "full"),
}
_CPU_STAT_KEYS = ("nr_throttled", "throttled_usec")
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


class ResourceSnapshotError(ValueError):
    """A resource observation is incomplete and cannot support a hang claim."""


def _decode_mount_field(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _read_cgroup_path(pid: int, proc_root: Path) -> PurePosixPath:
    try:
        lines = (proc_root / str(pid) / "cgroup").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ResourceSnapshotError("candidate-cgroup-unreadable") from exc
    matches = [line[3:] for line in lines if line.startswith("0::")]
    if len(matches) != 1:
        raise ResourceSnapshotError("candidate-cgroup-v2-identity-unavailable")
    path = PurePosixPath(matches[0])
    if not path.is_absolute() or ".." in path.parts:
        raise ResourceSnapshotError("candidate-cgroup-path-invalid")
    return path


def _read_cgroup2_mount(proc_root: Path) -> tuple[PurePosixPath, Path]:
    try:
        lines = (proc_root / "self" / "mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResourceSnapshotError("cgroup-mountinfo-unreadable") from exc
    for line in lines:
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        left = before.split()
        right = after.split()
        if len(left) >= 5 and right and right[0] == "cgroup2":
            mount_root = PurePosixPath(_decode_mount_field(left[3]))
            mountpoint = Path(_decode_mount_field(left[4]))
            if mount_root.is_absolute() and mountpoint.is_absolute():
                return mount_root, mountpoint
    raise ResourceSnapshotError("cgroup-v2-mount-unavailable")


def _candidate_cgroup_dir(pid: int, proc_root: Path) -> tuple[Path, str]:
    group_path = _read_cgroup_path(pid, proc_root)
    mount_root, mountpoint = _read_cgroup2_mount(proc_root)
    # In a private cgroup namespace, procfs reports '/' for the namespace
    # root and the mounted cgroup filesystem is rooted at that same group.
    if group_path == PurePosixPath("/"):
        relative = PurePosixPath(".")
    else:
        try:
            relative = group_path.relative_to(mount_root)
        except ValueError as exc:
            raise ResourceSnapshotError("candidate-cgroup-outside-visible-mount") from exc
    if ".." in relative.parts:
        raise ResourceSnapshotError("candidate-cgroup-path-invalid")
    identity = hashlib.sha256(group_path.as_posix().encode("utf-8")).hexdigest()[:16]
    return mountpoint.joinpath(*relative.parts), identity


def _read_psi(path: Path, name: str) -> dict[str, int]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ResourceSnapshotError(f"{name}-psi-unreadable") from exc
    totals: dict[str, int] = {}
    for line in lines:
        fields = line.split()
        if not fields or fields[0] not in _PRESSURE_ROWS[name]:
            continue
        total_fields = [field[6:] for field in fields[1:] if field.startswith("total=")]
        if len(total_fields) != 1 or not total_fields[0].isdigit():
            raise ResourceSnapshotError(f"{name}-psi-malformed")
        totals[fields[0]] = int(total_fields[0])
    if set(totals) != set(_PRESSURE_ROWS[name]):
        raise ResourceSnapshotError(f"{name}-psi-incomplete")
    return totals


def _read_cpu_stat(path: Path) -> dict[str, int]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ResourceSnapshotError("candidate-cpu-stat-unreadable") from exc
    values: dict[str, int] = {}
    for line in lines:
        fields = line.split()
        if len(fields) == 2 and fields[0] in _CPU_STAT_KEYS and fields[1].isdigit():
            values[fields[0]] = int(fields[1])
    if set(values) != set(_CPU_STAT_KEYS):
        raise ResourceSnapshotError("candidate-cpu-stat-incomplete")
    return values


def read_liveness_resources(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    """Return a complete snapshot, or an explicit unavailable observation.

    PSI counters are cumulative microseconds.  The caller compares adjacent
    snapshots; this function never interprets a missing source as zero.
    ``proc_root`` is injectable for deterministic namespace-resolution tests.
    """
    if not is_int_at_least(pid, 1):
        return {
            "schema_version": RESOURCE_SNAPSHOT_SCHEMA_VERSION,
            "status": "unavailable",
            "reason": "candidate-pid-invalid",
        }
    try:
        cgroup_dir, cgroup_identity = _candidate_cgroup_dir(pid, proc_root)
        host_psi = {
            name: _read_psi(proc_root / "pressure" / name, name)
            for name in _PRESSURE_NAMES
        }
        cgroup_psi = {
            name: _read_psi(cgroup_dir / f"{name}.pressure", name)
            for name in _PRESSURE_NAMES
        }
        cgroup_cpu = _read_cpu_stat(cgroup_dir / "cpu.stat")
    except ResourceSnapshotError as exc:
        return {
            "schema_version": RESOURCE_SNAPSHOT_SCHEMA_VERSION,
            "status": "unavailable",
            "reason": str(exc),
        }
    return {
        "schema_version": RESOURCE_SNAPSHOT_SCHEMA_VERSION,
        "status": "available",
        "cgroup_identity": cgroup_identity,
        "host_psi": host_psi,
        "cgroup_psi": cgroup_psi,
        "cgroup_cpu": cgroup_cpu,
    }


def _counter_delta(old: Any, new: Any) -> int | None:
    """``new - old`` for two monotonic counters, or ``None`` when unusable.

    ``None`` when either value is a bool, a non-int or negative, or when the
    counter went backwards (``new < old``): the caller reads that as
    ``"unknown"``. A zero delta is a valid result (an idle counter).
    """
    if (
        isinstance(old, bool)
        or isinstance(new, bool)
        or not isinstance(old, int)
        or not isinstance(new, int)
        or old < 0
        or new < 0
        or new < old
    ):
        return None
    return new - old


def compare_resource_snapshots(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> tuple[str, dict[str, int]]:
    """Classify one interval as ``clear``, ``stalled``, or ``unknown``.

    Counter resets, schema changes, incomplete snapshots, or cgroup identity
    changes are unknown.  Positive deltas are returned by source so callers
    can retain the exact observation that paused liveness time.
    """
    if previous is None:
        return "unknown", {}
    if (
        previous.get("schema_version") != RESOURCE_SNAPSHOT_SCHEMA_VERSION
        or current.get("schema_version") != RESOURCE_SNAPSHOT_SCHEMA_VERSION
        or previous.get("status") != "available"
        or current.get("status") != "available"
        or previous.get("cgroup_identity") != current.get("cgroup_identity")
    ):
        return "unknown", {}

    deltas: dict[str, int] = {}
    for scope in ("host_psi", "cgroup_psi"):
        old_scope = previous.get(scope)
        new_scope = current.get(scope)
        if not isinstance(old_scope, Mapping) or not isinstance(new_scope, Mapping):
            return "unknown", {}
        for pressure_name in _PRESSURE_NAMES:
            old_pressure = old_scope.get(pressure_name)
            new_pressure = new_scope.get(pressure_name)
            if not isinstance(old_pressure, Mapping) or not isinstance(new_pressure, Mapping):
                return "unknown", {}
            for row_name in _PRESSURE_ROWS[pressure_name]:
                delta = _counter_delta(old_pressure.get(row_name), new_pressure.get(row_name))
                if delta is None:
                    return "unknown", {}
                if delta:
                    deltas[f"{scope}.{pressure_name}.{row_name}_us"] = delta

    old_cpu = previous.get("cgroup_cpu")
    new_cpu = current.get("cgroup_cpu")
    if not isinstance(old_cpu, Mapping) or not isinstance(new_cpu, Mapping):
        return "unknown", {}
    for key in _CPU_STAT_KEYS:
        delta = _counter_delta(old_cpu.get(key), new_cpu.get(key))
        if delta is None:
            return "unknown", {}
        if delta:
            deltas[f"cgroup_cpu.{key}"] = delta
    return ("stalled" if deltas else "clear"), deltas

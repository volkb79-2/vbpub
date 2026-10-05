"""Read and validate cgroup v2 resource-limit event counters.

R2 uses these counters to distinguish a test failure caused by a mutant from a
test process that could not run because its lane exhausted the process or
memory limit. The counters are read from the current process's cgroup, using
the kernel's own cgroup and mount records rather than assuming a host path.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .records import record


class ResourceLimitObservationError(RuntimeError):
    """The current cgroup's resource-limit counters could not be observed."""


@record
class ResourceLimitCounters:
    pids_max: int
    memory_oom_kill: int
    memory_oom_group_kill: int

    def __post_init__(self) -> None:
        for name in ("pids_max", "memory_oom_kill", "memory_oom_group_kill"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@record
class CounterDelta:
    before: int
    after: int
    delta: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.before, self.after, self.delta)
        ):
            raise ValueError("resource counter values must be non-negative integers")
        if self.after < self.before:
            raise ValueError("resource counters must not decrease during a candidate")
        if self.delta != self.after - self.before:
            raise ValueError("resource counter delta must equal after minus before")

    @classmethod
    def between(cls, before: int, after: int) -> CounterDelta:
        return cls(before=before, after=after, delta=after - before)

    def to_dict(self) -> dict[str, int]:
        return {"before": self.before, "after": self.after, "delta": self.delta}


@record
class ResourceLimitEvidence:
    pids_events_max: CounterDelta
    memory_events_oom_kill: CounterDelta
    memory_events_oom_group_kill: CounterDelta

    def __post_init__(self) -> None:
        for name in (
            "pids_events_max",
            "memory_events_oom_kill",
            "memory_events_oom_group_kill",
        ):
            if not isinstance(getattr(self, name), CounterDelta):
                raise ValueError(f"{name} must be a CounterDelta")

    @property
    def limit_hit(self) -> bool:
        return any(
            getattr(self, name).delta > 0
            for name in (
                "pids_events_max",
                "memory_events_oom_kill",
                "memory_events_oom_group_kill",
            )
        )

    @classmethod
    def between(
        cls, before: ResourceLimitCounters, after: ResourceLimitCounters
    ) -> ResourceLimitEvidence:
        return cls(
            pids_events_max=CounterDelta.between(before.pids_max, after.pids_max),
            memory_events_oom_kill=CounterDelta.between(
                before.memory_oom_kill, after.memory_oom_kill
            ),
            memory_events_oom_group_kill=CounterDelta.between(
                before.memory_oom_group_kill, after.memory_oom_group_kill
            ),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ResourceLimitEvidence:
        if set(raw) != {"cgroup_version", "pids_events", "memory_events"}:
            raise ValueError("resource-limit evidence has unknown or missing fields")
        if raw["cgroup_version"] != 2 or type(raw["cgroup_version"]) is not int:
            raise ValueError("resource-limit evidence requires cgroup_version 2")
        pids = raw["pids_events"]
        memory = raw["memory_events"]
        if not isinstance(pids, Mapping) or set(pids) != {"max"}:
            raise ValueError("pids_events must contain exactly max")
        if not isinstance(memory, Mapping) or set(memory) != {
            "oom_kill",
            "oom_group_kill",
        }:
            raise ValueError("memory_events must contain exactly oom_kill and oom_group_kill")
        return cls(
            pids_events_max=_counter_delta_from_dict(pids["max"]),
            memory_events_oom_kill=_counter_delta_from_dict(memory["oom_kill"]),
            memory_events_oom_group_kill=_counter_delta_from_dict(
                memory["oom_group_kill"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cgroup_version": 2,
            "pids_events": {"max": self.pids_events_max.to_dict()},
            "memory_events": {
                "oom_kill": self.memory_events_oom_kill.to_dict(),
                "oom_group_kill": self.memory_events_oom_group_kill.to_dict(),
            },
        }


def _counter_delta_from_dict(raw: Any) -> CounterDelta:
    if not isinstance(raw, Mapping) or set(raw) != {"before", "after", "delta"}:
        raise ValueError("resource counter must contain exactly before, after, and delta")
    return CounterDelta(before=raw["before"], after=raw["after"], delta=raw["delta"])


def _unescape_mountinfo(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _current_cgroup_directory(
    *, cgroup_text: str, mountinfo_text: str
) -> Path:
    unified_path: str | None = None
    for line in cgroup_text.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            unified_path = fields[2]
            break
    if unified_path is None or not unified_path.startswith("/"):
        raise ResourceLimitObservationError(
            "/proc/self/cgroup has no absolute unified cgroup v2 path"
        )

    cgroup_path = PurePosixPath(unified_path)
    for line in mountinfo_text.splitlines():
        try:
            left, right = line.split(" - ", 1)
            mount_fields = left.split()
            filesystem_fields = right.split()
            if filesystem_fields[0] != "cgroup2":
                continue
            mount_root = PurePosixPath(_unescape_mountinfo(mount_fields[3]))
            mount_point = Path(_unescape_mountinfo(mount_fields[4]))
        except (IndexError, ValueError) as exc:
            raise ResourceLimitObservationError("malformed cgroup mount record") from exc

        if cgroup_path == PurePosixPath("/") or cgroup_path == mount_root:
            return mount_point
        try:
            relative = cgroup_path.relative_to(mount_root)
        except ValueError:
            continue
        return mount_point.joinpath(*relative.parts)

    raise ResourceLimitObservationError(
        "the current process has no visible cgroup v2 mount"
    )


def _read_event_file(path: Path, required: frozenset[str]) -> dict[str, int]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ResourceLimitObservationError(f"cannot read {path.name}: {exc}") from exc
    values: dict[str, int] = {}
    for line in lines:
        fields = line.split()
        if len(fields) != 2 or fields[0] in values:
            raise ResourceLimitObservationError(f"malformed event line in {path.name}")
        try:
            value = int(fields[1], 10)
        except ValueError as exc:
            raise ResourceLimitObservationError(f"malformed counter in {path.name}") from exc
        if value < 0:
            raise ResourceLimitObservationError(f"negative counter in {path.name}")
        values[fields[0]] = value
    missing = required - values.keys()
    if missing:
        raise ResourceLimitObservationError(
            f"{path.name} is missing required counters {sorted(missing)}"
        )
    return values


def read_current_cgroup_counters(
    *,
    cgroup_file: Path = Path("/proc/self/cgroup"),
    mountinfo_file: Path = Path("/proc/self/mountinfo"),
) -> ResourceLimitCounters:
    """Read exact process-limit and OOM counters for this process's cgroup."""
    try:
        cgroup_text = cgroup_file.read_text(encoding="utf-8")
        mountinfo_text = mountinfo_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResourceLimitObservationError(
            f"cannot inspect current cgroup identity: {exc}"
        ) from exc
    directory = _current_cgroup_directory(
        cgroup_text=cgroup_text, mountinfo_text=mountinfo_text
    )
    pids = _read_event_file(directory / "pids.events", frozenset({"max"}))
    memory = _read_event_file(
        directory / "memory.events", frozenset({"oom_kill", "oom_group_kill"})
    )
    return ResourceLimitCounters(
        pids_max=pids["max"],
        memory_oom_kill=memory["oom_kill"],
        memory_oom_group_kill=memory["oom_group_kill"],
    )

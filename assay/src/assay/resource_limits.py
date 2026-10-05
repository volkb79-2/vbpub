"""Read and validate cgroup v2 resource-limit event counters.

R2 uses these counters to distinguish a test failure caused by a mutant from a
test process that could not run because its lane exhausted the process or
memory limit. The counters are read from the current process's cgroup and every
visible ancestor, using the kernel's own cgroup and mount records rather than
assuming a host path. Matching cgroup2 mounts must be read-only, preventing a
candidate from creating or removing child cgroups. Assay also verifies that
candidate credentials cannot write `cgroup.procs` on the candidate cgroup or
any ancestor: `CLONE_INTO_CGROUP` checks inode permissions without honoring a
read-only mount. If the cgroup namespace hides ancestors, observation fails
closed because an ancestor limit can reject work without incrementing a child's
local event counter.
"""

from __future__ import annotations

import errno
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .records import record


LimitSignatureEntry = tuple[
    str, bool, int | None, bool, int | None, bool, bool
]


class ResourceLimitObservationError(RuntimeError):
    """Required cgroup resource-limit counters or ancestors are not visible."""


@record
class ResourceLimitCounters:
    pids_max: int
    memory_oom: int
    memory_max: int
    memory_oom_kill: int
    memory_oom_group_kill: int
    # This is an in-process pre/post binding, not verdict evidence. It makes a
    # changed cgroup path, limit/event interface availability, or configured
    # limit during one candidate an infrastructure error instead of hiding a
    # counter from the aggregate merely because it became unlimited.
    limit_signature: tuple[LimitSignatureEntry, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "pids_max",
            "memory_oom",
            "memory_max",
            "memory_oom_kill",
            "memory_oom_group_kill",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if type(self.limit_signature) is not tuple:
            raise ValueError("limit_signature must be a tuple")
        for entry in self.limit_signature:
            if (
                type(entry) is not tuple
                or len(entry) != 7
                or not isinstance(entry[0], str)
                or type(entry[1]) is not bool
                or (entry[2] is not None and (type(entry[2]) is not int or entry[2] < 0))
                or type(entry[3]) is not bool
                or (entry[4] is not None and (type(entry[4]) is not int or entry[4] < 0))
                or type(entry[5]) is not bool
                or type(entry[6]) is not bool
                or (not entry[1] and entry[2] is not None)
                or (not entry[3] and entry[4] is not None)
                or (entry[1] and not entry[5])
                or (entry[3] and not entry[6])
            ):
                raise ValueError("limit_signature entries are malformed")


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
    memory_events_max: CounterDelta
    memory_events_oom: CounterDelta
    memory_events_oom_kill: CounterDelta
    memory_events_oom_group_kill: CounterDelta

    def __post_init__(self) -> None:
        for name in (
            "pids_events_max",
            "memory_events_max",
            "memory_events_oom",
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
                "memory_events_max",
                "memory_events_oom",
                "memory_events_oom_kill",
                "memory_events_oom_group_kill",
            )
        )

    @classmethod
    def between(
        cls, before: ResourceLimitCounters, after: ResourceLimitCounters
    ) -> ResourceLimitEvidence:
        if before.limit_signature != after.limit_signature:
            raise ResourceLimitObservationError(
                "cgroup paths, controller files, or resource limits changed "
                "during a candidate command"
            )
        return cls(
            pids_events_max=CounterDelta.between(before.pids_max, after.pids_max),
            memory_events_max=CounterDelta.between(before.memory_max, after.memory_max),
            memory_events_oom=CounterDelta.between(
                before.memory_oom, after.memory_oom
            ),
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
            "max",
            "oom",
            "oom_kill",
            "oom_group_kill",
        }:
            raise ValueError(
                "memory_events must contain exactly max, oom, oom_kill, and "
                "oom_group_kill"
            )
        return cls(
            pids_events_max=_counter_delta_from_dict(pids["max"]),
            memory_events_max=_counter_delta_from_dict(memory["max"]),
            memory_events_oom=_counter_delta_from_dict(memory["oom"]),
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
                "max": self.memory_events_max.to_dict(),
                "oom": self.memory_events_oom.to_dict(),
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


def _path_is_present(path: Path) -> bool:
    """Distinguish an absent cgroup interface from an unreadable one."""
    try:
        path.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ResourceLimitObservationError(
            f"cannot inspect {path.name}: {exc}"
        ) from exc
    return True


def _candidate_capabilities_are_unprivileged() -> None:
    """Refuse capabilities that can bypass cgroup control-file permissions."""
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ResourceLimitObservationError(
            "cannot inspect candidate process capabilities"
        ) from exc
    capabilities: dict[str, int] = {}
    for line in status.splitlines():
        name, separator, value = line.partition(":")
        if separator and name in {"CapEff", "CapPrm"}:
            try:
                capabilities[name] = int(value.strip(), 16)
            except ValueError as exc:
                raise ResourceLimitObservationError(
                    f"malformed {name} in /proc/self/status"
                ) from exc
    if set(capabilities) != {"CapEff", "CapPrm"}:
        raise ResourceLimitObservationError(
            "/proc/self/status is missing candidate capability sets"
        )
    may_change_cgroup_access = sum(1 << bit for bit in (1, 3, 6, 7, 21))
    if any(value & may_change_cgroup_access for value in capabilities.values()):
        raise ResourceLimitObservationError(
            "candidate capabilities can bypass or change cgroup control permissions"
        )


def _mode_grants_write(path: Path) -> bool:
    """Check cgroupfs DAC write bits without the mount's read-only overlay."""
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ResourceLimitObservationError(
            f"cannot inspect permissions on {path.name}: {exc}"
        ) from exc
    try:
        os.getxattr(path, "system.posix_acl_access")
    except OSError as exc:
        if exc.errno not in {
            errno.ENODATA,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
            errno.ENOSYS,
        }:
            raise ResourceLimitObservationError(
                f"cannot inspect access ACL on {path.name}: {exc}"
            ) from exc
    else:
        raise ResourceLimitObservationError(
            f"cannot establish candidate write access through the ACL on {path.name}"
        )

    if os.geteuid() == metadata.st_uid:
        write_bit = stat.S_IWUSR
    elif metadata.st_gid in {os.getegid(), *os.getgroups()}:
        write_bit = stat.S_IWGRP
    else:
        write_bit = stat.S_IWOTH
    return bool(metadata.st_mode & write_bit)


def _require_unwritable_cgroup_procs(
    directories: tuple[Path, ...], hierarchy_root: Path
) -> None:
    """A candidate must not be able to migrate to an unsampled cgroup.

    CLONE_INTO_CGROUP checks the cgroup.procs inode's DAC permission directly;
    a read-only mount alone does not block it. The common ancestor's
    cgroup.procs must be writable for any migration, so checking this path at
    the candidate and every ancestor proves that candidate credentials cannot
    move into an existing descendant, sibling, or ancestor cgroup.
    """
    _candidate_capabilities_are_unprivileged()
    for directory in (*directories, hierarchy_root):
        path = directory / "cgroup.procs"
        if not _path_is_present(path):
            raise ResourceLimitObservationError(
                "cgroup.procs is unavailable on a visible cgroup ancestor"
            )
        if _mode_grants_write(path):
            raise ResourceLimitObservationError(
                "candidate credentials can write an ancestor cgroup.procs and "
                "could migrate into an unsampled cgroup"
            )


def _visible_cgroup_directories(
    *, cgroup_text: str, mountinfo_text: str
) -> tuple[Path, ...]:
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
    matching_mounts: list[tuple[Path, PurePosixPath]] = []
    visible_mount_points: list[Path] = []
    for line in mountinfo_text.splitlines():
        try:
            left, right = line.split(" - ", 1)
            mount_fields = left.split()
            filesystem_fields = right.split()
            mount_point = Path(_unescape_mountinfo(mount_fields[4]))
            visible_mount_points.append(mount_point)
            if filesystem_fields[0] != "cgroup2":
                continue
            mount_root = PurePosixPath(_unescape_mountinfo(mount_fields[3]))
            mount_options = set(mount_fields[5].split(","))
        except (IndexError, ValueError) as exc:
            raise ResourceLimitObservationError("malformed cgroup mount record") from exc

        if cgroup_path.is_relative_to(mount_root) or mount_root.is_relative_to(
            cgroup_path
        ):
            matching_mounts.append((mount_point, mount_root))
            if "ro" not in mount_options:
                raise ResourceLimitObservationError(
                    "a cgroup2 mount exposes the candidate hierarchy writable"
                )

    if not matching_mounts:
        raise ResourceLimitObservationError(
            "the current process has no visible cgroup v2 mount"
        )
    full_hierarchy_mounts = [
        (point, root)
        for point, root in matching_mounts
        if root == PurePosixPath("/")
    ]
    if not full_hierarchy_mounts:
        raise ResourceLimitObservationError(
            "the visible cgroup2 mount omits ancestors above its mount root"
        )

    mount_point, _mount_root = full_hierarchy_mounts[0]
    hierarchy_root = mount_point
    relative_path = cgroup_path.relative_to(PurePosixPath("/"))
    current_directory = mount_point.joinpath(*relative_path.parts)
    if cgroup_path == PurePosixPath("/"):
        raise ResourceLimitObservationError(
            "the cgroup namespace hides the process's parent cgroups"
        )
    if _path_is_present(mount_point / "pids.max") or _path_is_present(
        mount_point / "memory.max"
    ):
        raise ResourceLimitObservationError(
            "the visible cgroup2 mount root is a non-root cgroup and may hide ancestors"
        )

    directories: list[Path] = []
    directory = current_directory
    while directory != hierarchy_root:
        directories.append(directory)
        parent = directory.parent
        if (
            parent == directory
            or (parent != hierarchy_root and hierarchy_root not in parent.parents)
        ):
            raise ResourceLimitObservationError(
                "the process cgroup is outside the visible cgroup2 hierarchy"
            )
        directory = parent
    visible_directories = tuple(directories)
    if visible_mount_points.count(hierarchy_root) != 1:
        raise ResourceLimitObservationError(
            "multiple mounts cover the visible cgroup hierarchy root"
        )
    target_paths = [*visible_directories, hierarchy_root]
    for directory in (*visible_directories, hierarchy_root):
        target_paths.extend(
            directory / name
            for name in (
                "cgroup.procs",
                "pids.max",
                "pids.events",
                "memory.max",
                "memory.events",
            )
        )
    for visible_mount in visible_mount_points:
        if visible_mount == hierarchy_root or not visible_mount.is_relative_to(
            hierarchy_root
        ):
            continue
        if any(path.is_relative_to(visible_mount) for path in target_paths):
            raise ResourceLimitObservationError(
                "a visible mount shadows a cgroup path or resource counter"
            )
    return visible_directories



def _read_event_file_if_present(
    path: Path, required: frozenset[str]
) -> dict[str, int] | None:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except FileNotFoundError:
        return None
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


def _read_event_file(path: Path, required: frozenset[str]) -> dict[str, int]:
    values = _read_event_file_if_present(path, required)
    if values is None:
        raise ResourceLimitObservationError(f"{path.name} is unavailable")
    return values


def _read_limit_file(path: Path) -> tuple[bool, int | None]:
    """Read a cgroup limit, returning (interface present, limit or max)."""
    try:
        fields = path.read_text(encoding="ascii").split()
    except FileNotFoundError:
        return False, None
    except (OSError, UnicodeError) as exc:
        raise ResourceLimitObservationError(f"cannot read {path.name}: {exc}") from exc
    if fields == ["max"]:
        return True, None
    if len(fields) != 1:
        raise ResourceLimitObservationError(f"malformed limit in {path.name}")
    try:
        value = int(fields[0], 10)
    except ValueError as exc:
        raise ResourceLimitObservationError(f"malformed limit in {path.name}") from exc
    if value < 0:
        raise ResourceLimitObservationError(f"negative limit in {path.name}")
    return True, value


def read_current_cgroup_counters(
    *,
    cgroup_file: Path = Path("/proc/self/cgroup"),
    mountinfo_file: Path = Path("/proc/self/mountinfo"),
) -> ResourceLimitCounters:
    """Read exact process-limit and OOM counters across visible ancestors.

    The cgroup2 mounts exposing the current hierarchy must be read-only, and
    `cgroup.procs` permissions at the candidate and ancestors must deny writes.
    The second check blocks `CLONE_INTO_CGROUP` into an existing child, which
    does not honor the mount's read-only flag.
    The candidate's PID and memory event counters and every visible ancestor's
    available event counters are sampled. This includes unlimited active
    ancestors, because local-event configurations can attribute a refusal to
    the nearest active ancestor rather than the finite enforcing ancestor. If
    a controller is inactive, the nearest active visible ancestors are still
    sampled. A shared ancestor can conservatively attribute sibling activity.
    Limit and path signatures are bound across each candidate's before/after
    samples; any change makes the observation unusable.
    """
    try:
        cgroup_text = cgroup_file.read_text(encoding="utf-8")
        mountinfo_text = mountinfo_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResourceLimitObservationError(
            f"cannot inspect current cgroup identity: {exc}"
        ) from exc
    directories = _visible_cgroup_directories(
        cgroup_text=cgroup_text, mountinfo_text=mountinfo_text
    )
    _require_unwritable_cgroup_procs(directories, directories[-1].parent)
    pids_max = 0
    memory_max = 0
    memory_oom = 0
    memory_oom_kill = 0
    memory_oom_group_kill = 0
    pids_samples: dict[int, dict[str, int]] = {}
    memory_samples: dict[int, dict[str, int]] = {}
    limits: list[tuple[bool, int | None, bool, int | None]] = []
    limit_signature: list[LimitSignatureEntry] = []
    for directory in directories:
        pids_limit_present, pids_limit = _read_limit_file(directory / "pids.max")
        memory_limit_present, memory_limit = _read_limit_file(directory / "memory.max")
        limits.append(
            (
                pids_limit_present,
                pids_limit,
                memory_limit_present,
                memory_limit,
            )
        )
    for index, directory in enumerate(directories):
        (
            pids_limit_present,
            _pids_limit,
            memory_limit_present,
            _memory_limit,
        ) = limits[index]
        pids_events = _read_event_file_if_present(
            directory / "pids.events", frozenset({"max"})
        )
        if pids_limit_present and pids_events is None:
            raise ResourceLimitObservationError(
                f"{directory.name} pids.max is present but pids.events is unavailable"
            )
        if pids_events is not None:
            pids_samples[index] = pids_events

        memory_events = _read_event_file_if_present(
            directory / "memory.events",
            frozenset({"max", "oom", "oom_kill", "oom_group_kill"}),
        )
        if memory_limit_present and memory_events is None:
            raise ResourceLimitObservationError(
                f"{directory.name} memory.max is present but memory.events is unavailable"
            )
        if memory_events is not None:
            memory_samples[index] = memory_events
        limit_signature.append(
            (
                str(directory),
                *limits[index],
                pids_events is not None,
                memory_events is not None,
            )
        )

    if not pids_samples:
        raise ResourceLimitObservationError(
            "no visible pids.events counter is available for native R2"
        )
    if not memory_samples:
        raise ResourceLimitObservationError(
            "no visible memory.events counter is available for native R2"
        )

    for counters in pids_samples.values():
        pids_max += counters["max"]
    for counters in memory_samples.values():
        memory_max += counters["max"]
        memory_oom += counters["oom"]
        memory_oom_kill += counters["oom_kill"]
        memory_oom_group_kill += counters["oom_group_kill"]
    return ResourceLimitCounters(
        pids_max=pids_max,
        memory_max=memory_max,
        memory_oom=memory_oom,
        memory_oom_kill=memory_oom_kill,
        memory_oom_group_kill=memory_oom_group_kill,
        limit_signature=tuple(limit_signature),
    )

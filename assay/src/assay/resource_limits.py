"""Read and validate cgroup v2 resource-limit event counters.

R2 uses these counters to distinguish a test failure caused by a mutant from a
test process that could not run because its lane exhausted the process or
memory limit. The counters are read from the calling thread's cgroup and every
visible ancestor, using the kernel's own cgroup and mount records rather than
assuming a host path. Matching cgroup2 mounts must be read-only, preventing a
candidate from creating or removing child cgroups. Assay also verifies that
candidate credentials cannot write `cgroup.procs` on the candidate cgroup or
any ancestor: `CLONE_INTO_CGROUP` checks inode permissions without honoring a
read-only mount. If the local cgroup namespace hides ancestors, observation
fails closed by default because an ancestor limit can reject work without
incrementing a child's local event counter. Native R2 may consult an
explicitly configured, peer-bound read-only observer only for that specific
hidden-ancestor case; other local-reader failures never fall through remotely.
"""

from __future__ import annotations

import errno
import json
import os
import re
import socket
import stat
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .records import record


LimitSignatureEntry = tuple[
    str,
    int,
    bool,
    bool,
    bool,
    bool,
    bool,
    bool,
    bool,
    bool,
]
LimitValueEntry = tuple[int | None, int | None]

_LIMIT_SIGNATURE_FIELDS = (
    "path",
    "mount_id",
    "pids.max.present",
    "memory.max.present",
    "pids.events.present",
    "memory.events.present",
    "pids.events.local.present",
    "memory.events.local.present",
    "pids_localevents",
    "memory_localevents",
)

_RESOURCE_OBSERVER_REQUEST_TIMEOUT_SECONDS = 5.0
_RESOURCE_OBSERVER_PREFLIGHT_JOIN_GRACE_SECONDS = 1.0
_RESOURCE_OBSERVER_RESTART_RETRY_SECONDS = 30.0
_RESOURCE_OBSERVER_RETRY_INTERVAL_SECONDS = 0.2


def _limit_signature_change_detail(
    before: ResourceLimitCounters, after: ResourceLimitCounters
) -> str:
    changes: list[str] = []
    for index, (old, new) in enumerate(
        zip(before.limit_signature, after.limit_signature)
    ):
        changes.extend(
            f"cgroup[{index}].{name} changed: {old_value} -> {new_value}"
            for name, old_value, new_value in zip(_LIMIT_SIGNATURE_FIELDS, old, new)
            if old_value != new_value
        )
    for index in range(len(after.limit_signature), len(before.limit_signature)):
        old = before.limit_signature[index]
        changes.append(
            f"cgroup[{index}] removed: path={old[0]}, mount_id={old[1]}"
        )
    for index in range(len(before.limit_signature), len(after.limit_signature)):
        new = after.limit_signature[index]
        changes.append(
            f"cgroup[{index}] added: path={new[0]}, mount_id={new[1]}"
        )
    return "; ".join(changes) or "signature changed without a field-level difference"


def _limit_value_changes(
    before: ResourceLimitCounters, after: ResourceLimitCounters
) -> tuple[str, ...]:
    return tuple(
        f"cgroup[{index}].{name} changed: {_format_limit_value(old_value)} -> "
        f"{_format_limit_value(new_value)}"
        for index, (old, new) in enumerate(
            zip(before.limit_values, after.limit_values, strict=True)
        )
        for name, old_value, new_value in (
            ("pids.max", old[0], new[0]),
            ("memory.max", old[1], new[1]),
        )
        if old_value != new_value
    )


def _format_limit_value(value: int | None) -> str:
    return "max" if value is None else str(value)


class ResourceLimitObservationError(RuntimeError):
    """Required cgroup resource-limit counters or ancestors are not visible."""


class ResourceLimitAncestorsHiddenError(ResourceLimitObservationError):
    """The local cgroup view omits ancestors, but a configured observer may see them."""


@record
class ResourceObservationCapability:
    """Whether this process can currently observe the B145 counter hierarchy."""

    available: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.available) is not bool:
            raise ValueError("resource observation capability available must be a bool")
        if self.available and self.reason is not None:
            raise ValueError("available resource observation cannot carry a refusal reason")
        if not self.available and (not isinstance(self.reason, str) or not self.reason):
            raise ValueError("unavailable resource observation requires a reason")


@record
class ResourceLimitCounters:
    pids_max: int
    memory_oom: int
    memory_max: int
    memory_oom_kill: int
    memory_oom_group_kill: int
    # This is an in-process pre/post identity binding, not verdict evidence.
    # Configured pids.max and memory.max values live separately below because
    # changing a value does not invalidate monotonic event counters.
    limit_signature: tuple[LimitSignatureEntry, ...] = ()
    limit_values: tuple[LimitValueEntry, ...] = ()
    # Internal per-cgroup snapshots used only to attribute events. Each row is
    # path, regular pids/memory events, then effective local pids/memory events.
    # They are never serialized into mutation verdicts.
    path_event_counters: tuple[
        tuple[
            str,
            int | None, int | None, int | None, int | None, int | None,
            int | None, int | None, int | None, int | None, int | None,
        ], ...
    ] = ()

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
                or len(entry) != 10
                or not isinstance(entry[0], str)
                or type(entry[1]) is not int
                or entry[1] <= 0
                or any(type(value) is not bool for value in entry[2:])
                or (entry[2] and (not entry[4] or (not entry[6] and not entry[8])))
                or (entry[3] and (not entry[5] or (not entry[7] and not entry[9])))
            ):
                raise ValueError("limit_signature entries are malformed")
        if type(self.limit_values) is not tuple or len(self.limit_values) != len(
            self.limit_signature
        ):
            raise ValueError("limit_values must match the limit_signature entries")
        for identity, values in zip(
            self.limit_signature, self.limit_values, strict=True
        ):
            if (
                type(values) is not tuple
                or len(values) != 2
                or any(
                    value is not None and (type(value) is not int or value < 0)
                    for value in values
                )
                or (not identity[2] and values[0] is not None)
                or (not identity[3] and values[1] is not None)
            ):
                raise ValueError("limit_values entries are malformed")
        if type(self.path_event_counters) is not tuple:
            raise ValueError("path_event_counters must be a tuple")
        seen_paths: set[str] = set()
        for entry in self.path_event_counters:
            if (
                type(entry) is not tuple
                or len(entry) != 11
                or not isinstance(entry[0], str)
                or not entry[0].startswith("/")
                or str(PurePosixPath(entry[0])) != entry[0]
                or entry[0] in seen_paths
                or any(
                    value is not None and (type(value) is not int or value < 0)
                    for value in entry[1:]
                )
            ):
                raise ValueError("path_event_counters entries are malformed")
            seen_paths.add(entry[0])


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
        # memory.events:max is retained as telemetry: the kernel may satisfy
        # the allocation through reclaim without entering OOM or changing the
        # candidate's correctness result.
        return any(
            getattr(self, name).delta > 0
            for name in (
                "pids_events_max",
                "memory_events_oom",
                "memory_events_oom_kill",
                "memory_events_oom_group_kill",
            )
        )

    @classmethod
    def between(
        cls, before: ResourceLimitCounters, after: ResourceLimitCounters
    ) -> tuple[ResourceLimitEvidence, tuple[str, ...]]:
        """Compare event counters across one stable cgroup identity.

        Configured limit changes are returned separately as advisory details;
        they do not invalidate the event-counter window.
        """
        if before.limit_signature != after.limit_signature:
            raise ResourceLimitObservationError(
                "cgroup resource-limit observation changed during a candidate "
                f"command: {_limit_signature_change_detail(before, after)}"
            )
        pids, memory_max, memory_oom, memory_oom_kill, memory_oom_group_kill = (
            _candidate_counter_deltas(before, after)
        )
        return (
            cls(
                pids_events_max=pids,
                memory_events_max=memory_max,
                memory_events_oom=memory_oom,
                memory_events_oom_kill=memory_oom_kill,
                memory_events_oom_group_kill=memory_oom_group_kill,
            ),
            _limit_value_changes(before, after),
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


def _candidate_counter_deltas(
    before: ResourceLimitCounters, after: ResourceLimitCounters
) -> tuple[CounterDelta, CounterDelta, CounterDelta, CounterDelta, CounterDelta]:
    """Use candidate subtree events and ancestor-local events for attribution."""
    if not before.path_event_counters and not after.path_event_counters:
        # Compatibility for in-memory fixtures and pre-collection callers.
        # Both production readers below always attach per-path samples.
        return (
            CounterDelta.between(before.pids_max, after.pids_max),
            CounterDelta.between(before.memory_max, after.memory_max),
            CounterDelta.between(before.memory_oom, after.memory_oom),
            CounterDelta.between(before.memory_oom_kill, after.memory_oom_kill),
            CounterDelta.between(
                before.memory_oom_group_kill, after.memory_oom_group_kill
            ),
        )
    if (
        not before.path_event_counters
        or not after.path_event_counters
        or len(before.path_event_counters) != len(after.path_event_counters)
        or any(
            old[0] != new[0]
            for old, new in zip(
                before.path_event_counters, after.path_event_counters, strict=True
            )
        )
    ):
        raise ResourceLimitObservationError(
            "cgroup ancestry samples changed during a candidate command"
        )

    per_path: list[tuple[CounterDelta | None, ...]] = []
    for old, new in zip(
        before.path_event_counters, after.path_event_counters, strict=True
    ):
        deltas: list[CounterDelta | None] = []
        for old_value, new_value in zip(old[1:], new[1:], strict=True):
            if old_value is None or new_value is None:
                if old_value is not new_value:
                    raise ResourceLimitObservationError(
                        "cgroup event-counter availability changed during a candidate command"
                    )
                deltas.append(None)
            else:
                deltas.append(CounterDelta.between(old_value, new_value))
        per_path.append(tuple(deltas))

    candidate_deltas: list[CounterDelta] = []
    for metric_index in range(5):
        # Layout: five hierarchical counters followed by five local counters.
        # The candidate's ordinary events file covers its cgroup subtree unless
        # the mount explicitly enables localevents; in that mode it still
        # covers the candidate cgroup itself. Candidate descendants cannot be
        # introduced by the measured process when that local mode is active.
        regular_leaf_delta = per_path[0][metric_index]
        local_leaf_delta = per_path[0][metric_index + 5]
        if regular_leaf_delta is None:
            leaf_delta = local_leaf_delta
        elif local_leaf_delta is not None and regular_leaf_delta.delta == 0:
            # The two files are read sequentially. A candidate event can land
            # between reads, so either positive candidate-scope delta counts.
            leaf_delta = local_leaf_delta if local_leaf_delta.delta > 0 else regular_leaf_delta
        else:
            leaf_delta = regular_leaf_delta
        leaf_value = 0 if leaf_delta is None else leaf_delta.delta
        for ancestor in per_path[1:]:
            ancestor_delta = ancestor[metric_index + 5]
            if (
                metric_index != 1  # memory.events:max is telemetry, not a failure
                and ancestor_delta is not None
                and ancestor_delta.delta > 0
                and leaf_value == 0
            ):
                raise ResourceLimitObservationError(
                    "a shared ancestor resource event is not attributable to the "
                    "candidate cgroup; mutation outcomes are withheld"
                )
        if leaf_delta is None:
            candidate_deltas.append(CounterDelta(before=0, after=0, delta=0))
        else:
            candidate_deltas.append(leaf_delta)
    return tuple(candidate_deltas)  # type: ignore[return-value]


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


def _mount_id_for_fd(fd: int) -> int:
    """Read the mount ID of an already-open path from its proc fdinfo."""
    try:
        lines = Path(f"/proc/self/fdinfo/{fd}").read_text(
            encoding="ascii"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise ResourceLimitObservationError(
            "cannot inspect the opened cgroup path's mount identity"
        ) from exc
    values = [
        line.split()[1]
        for line in lines
        if len(line.split()) == 2 and line.split()[0] == "mnt_id:"
    ]
    if len(values) != 1:
        raise ResourceLimitObservationError(
            "the opened cgroup path has no unique mount identity"
        )
    try:
        mount_id = int(values[0], 10)
    except ValueError as exc:
        raise ResourceLimitObservationError(
            "the opened cgroup path has a malformed mount identity"
        ) from exc
    if mount_id <= 0:
        raise ResourceLimitObservationError(
            "the opened cgroup path has an invalid mount identity"
        )
    return mount_id


def _mount_id_for_path(path: Path) -> int:
    """Resolve a path with O_PATH and return the mount ID of that opened path."""
    path_only = getattr(os, "O_PATH", None)
    if path_only is None:
        raise ResourceLimitObservationError(
            "the platform cannot establish cgroup path mount identity"
        )
    try:
        fd = os.open(path, path_only | os.O_CLOEXEC)
    except OSError as exc:
        raise ResourceLimitObservationError(
            f"cannot open {path.name} to establish cgroup mount identity: {exc}"
        ) from exc
    try:
        return _mount_id_for_fd(fd)
    finally:
        os.close(fd)


def _require_mount_id(fd: int, expected_mount_id: int, name: str) -> None:
    actual_mount_id = _mount_id_for_fd(fd)
    if actual_mount_id != expected_mount_id:
        raise ResourceLimitObservationError(
            f"{name} resolves through a different mount ID {actual_mount_id}, "
            "not the "
            f"selected cgroup2 mount ID {expected_mount_id}; an overmount "
            "may shadow the sampled hierarchy"
        )


def _read_control_text(path: Path, *, expected_mount_id: int) -> str | None:
    """Read a control file and bind its bytes to the selected mount ID."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        _require_path_mount_id(path.parent, expected_mount_id)
        return None
    except OSError as exc:
        raise ResourceLimitObservationError(f"cannot read {path.name}: {exc}") from exc
    try:
        _require_mount_id(fd, expected_mount_id, path.name)
        try:
            with os.fdopen(fd, "r", encoding="ascii", closefd=False) as stream:
                return stream.read()
        except (OSError, UnicodeError) as exc:
            raise ResourceLimitObservationError(
                f"cannot read {path.name}: {exc}"
            ) from exc
    finally:
        os.close(fd)


def _require_path_mount_id(path: Path, expected_mount_id: int) -> None:
    # The public reader established O_PATH support while opening the selected
    # hierarchy root; this helper only checks paths beneath that same root.
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError as exc:
        raise ResourceLimitObservationError(
            f"cannot open {path.name} to establish cgroup mount identity: {exc}"
        ) from exc
    try:
        _require_mount_id(fd, expected_mount_id, path.name)
    finally:
        os.close(fd)


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
        status = Path("/proc/thread-self/status").read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ResourceLimitObservationError(
            "cannot inspect candidate thread capabilities"
        ) from exc
    capabilities: dict[str, int] = {}
    for line in status.splitlines():
        name, separator, value = line.partition(":")
        if separator and name in {"CapEff", "CapPrm"}:
            try:
                capabilities[name] = int(value.strip(), 16)
            except ValueError as exc:
                raise ResourceLimitObservationError(
                    f"malformed {name} in /proc/thread-self/status"
                ) from exc
    if set(capabilities) != {"CapEff", "CapPrm"}:
        raise ResourceLimitObservationError(
            "/proc/thread-self/status is missing candidate capability sets"
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
    directories: tuple[Path, ...], hierarchy_root: Path, mount_id: int
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
        _require_path_mount_id(path, mount_id)
        if _mode_grants_write(path):
            raise ResourceLimitObservationError(
                "candidate credentials can write an ancestor cgroup.procs and "
                "could migrate into an unsampled cgroup"
            )


def _visible_cgroup_directories(
    *, cgroup_text: str, mountinfo_text: str
) -> tuple[tuple[Path, ...], int, bool, bool]:
    unified_path: str | None = None
    for line in cgroup_text.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            unified_path = fields[2]
            break
    if unified_path is None or not unified_path.startswith("/"):
        raise ResourceLimitObservationError(
            "the calling thread cgroup record has no absolute unified cgroup v2 path"
        )

    cgroup_path = PurePosixPath(unified_path)
    if ".." in cgroup_path.parts:
        raise ResourceLimitObservationError(
            "the calling thread cgroup path escapes its hierarchy"
        )
    matching_mounts: list[tuple[int, Path, PurePosixPath, bool, bool]] = []
    visible_mount_points: list[Path] = []
    for line in mountinfo_text.splitlines():
        try:
            left, right = line.split(" - ", 1)
            mount_fields = left.split()
            filesystem_fields = right.split()
            mount_id = int(mount_fields[0], 10)
            mount_point = Path(_unescape_mountinfo(mount_fields[4]))
            visible_mount_points.append(mount_point)
            if filesystem_fields[0] != "cgroup2":
                continue
            if len(filesystem_fields) < 3:
                raise ValueError("cgroup2 mount omits superblock options")
            mount_root = PurePosixPath(_unescape_mountinfo(mount_fields[3]))
            mount_options = set(mount_fields[5].split(","))
            super_options = set(filesystem_fields[2].split(","))
        except (IndexError, ValueError) as exc:
            raise ResourceLimitObservationError("malformed cgroup mount record") from exc

        if mount_id <= 0:
            raise ResourceLimitObservationError("invalid cgroup mount ID")
        if cgroup_path.is_relative_to(mount_root) or mount_root.is_relative_to(
            cgroup_path
        ):
            matching_mounts.append((
                mount_id,
                mount_point,
                mount_root,
                "pids_localevents" in super_options,
                "memory_localevents" in super_options,
            ))
            if "ro" not in mount_options:
                raise ResourceLimitObservationError(
                    "a cgroup2 mount exposes the candidate hierarchy writable"
                )

    if not matching_mounts:
        raise ResourceLimitObservationError(
            "the current process has no visible cgroup v2 mount"
        )
    full_hierarchy_mounts = [
        (mount_id, point, root, pids_localevents, memory_localevents)
        for mount_id, point, root, pids_localevents, memory_localevents in matching_mounts
        if root == PurePosixPath("/")
    ]
    if not full_hierarchy_mounts:
        raise ResourceLimitAncestorsHiddenError(
            "the visible cgroup2 mount omits ancestors above its mount root"
        )

    (
        selected_mount_id,
        mount_point,
        _mount_root,
        pids_localevents,
        memory_localevents,
    ) = full_hierarchy_mounts[0]
    hierarchy_root = mount_point
    opened_mount_id = _mount_id_for_path(hierarchy_root)
    if opened_mount_id != selected_mount_id:
        raise ResourceLimitObservationError(
            "the opened cgroup hierarchy resolves through mount ID "
            f"{opened_mount_id}, not selected cgroup2 mount ID {selected_mount_id}; "
            "an overmount may shadow the hierarchy"
        )
    relative_path = cgroup_path.relative_to(PurePosixPath("/"))
    current_directory = mount_point.joinpath(*relative_path.parts)
    if cgroup_path == PurePosixPath("/"):
        raise ResourceLimitAncestorsHiddenError(
            "the cgroup namespace hides the process's parent cgroups"
        )
    if _path_is_present(mount_point / "pids.max") or _path_is_present(
        mount_point / "memory.max"
    ):
        raise ResourceLimitAncestorsHiddenError(
            "the visible cgroup2 mount root is a non-root cgroup and may hide ancestors"
        )

    directories: list[Path] = []
    directory = current_directory
    while directory != hierarchy_root:
        directories.append(directory)
        directory = directory.parent
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
                "pids.events.local",
                "memory.max",
                "memory.events",
                "memory.events.local",
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
    return visible_directories, selected_mount_id, pids_localevents, memory_localevents


def _require_no_candidate_subgroups(candidate_directory: Path) -> None:
    """Localevents can cover the candidate cgroup only when it is a leaf."""
    try:
        with os.scandir(candidate_directory) as entries:
            has_child = any(entry.is_dir(follow_symlinks=False) for entry in entries)
    except OSError as exc:
        raise ResourceLimitObservationError(
            "cannot prove the localevents candidate cgroup has no child cgroups"
        ) from exc
    if has_child:
        raise ResourceLimitObservationError(
            "localevents expose only local events, but the candidate cgroup has child cgroups"
        )


def _read_event_file_if_present(
    path: Path, required: frozenset[str], *, expected_mount_id: int
) -> dict[str, int] | None:
    text = _read_control_text(path, expected_mount_id=expected_mount_id)
    if text is None:
        return None
    values: dict[str, int] = {}
    for line in text.splitlines():
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


def _effective_local_events(
    regular: dict[str, int] | None,
    local: dict[str, int] | None,
    *,
    regular_is_local: bool,
    required: frozenset[str],
    controller: str,
) -> dict[str, int] | None:
    """Return local event evidence, using localevents as a proven equivalent."""
    if regular is None:
        if local is not None:
            raise ResourceLimitObservationError(
                f"{controller}.events.local is present without its regular event file"
            )
        return None
    if local is None:
        if regular_is_local:
            return {key: regular[key] for key in required}
        raise ResourceLimitObservationError(
            f"{controller}.events.local is unavailable"
        )
    if regular_is_local:
        # The two interfaces have the same scope under localevents. Taking the
        # maximum preserves a concurrent increment between their sequential reads.
        return {key: max(regular[key], local[key]) for key in required}
    return {key: local[key] for key in required}


def _read_limit_file(
    path: Path, *, expected_mount_id: int
) -> tuple[bool, int | None]:
    """Read a cgroup limit, returning (interface present, limit or max)."""
    text = _read_control_text(path, expected_mount_id=expected_mount_id)
    if text is None:
        return False, None
    fields = text.split()
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
    cgroup_file: Path = Path("/proc/thread-self/cgroup"),
    mountinfo_file: Path = Path("/proc/self/mountinfo"),
) -> ResourceLimitCounters:
    """Read exact process-limit and OOM counters for the calling thread.

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
    The calling thread's `/proc/thread-self/cgroup` identity is used because
    threaded cgroup subtrees can place it separately from the process leader.
    The opened hierarchy and each sampled file must resolve through the mount
    ID selected from mountinfo. Limit, mount, and path signatures are bound
    across each candidate's before/after samples; any change makes the
    observation unusable.
    """
    try:
        cgroup_text = cgroup_file.read_text(encoding="utf-8")
        mountinfo_text = mountinfo_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResourceLimitObservationError(
            f"cannot inspect calling thread cgroup identity: {exc}"
        ) from exc
    directories, mount_id, pids_localevents, memory_localevents = _visible_cgroup_directories(
        cgroup_text=cgroup_text, mountinfo_text=mountinfo_text
    )
    _require_unwritable_cgroup_procs(
        directories, directories[-1].parent, mount_id
    )
    pids_max = 0
    memory_max = 0
    memory_oom = 0
    memory_oom_kill = 0
    memory_oom_group_kill = 0
    pids_samples: dict[int, dict[str, int]] = {}
    pids_local_samples: dict[int, dict[str, int]] = {}
    memory_samples: dict[int, dict[str, int]] = {}
    memory_local_samples: dict[int, dict[str, int]] = {}
    limits: list[tuple[bool, int | None, bool, int | None]] = []
    limit_signature: list[LimitSignatureEntry] = []
    limit_values: list[LimitValueEntry] = []
    for directory in directories:
        pids_limit_present, pids_limit = _read_limit_file(
            directory / "pids.max", expected_mount_id=mount_id
        )
        memory_limit_present, memory_limit = _read_limit_file(
            directory / "memory.max", expected_mount_id=mount_id
        )
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
            directory / "pids.events",
            frozenset({"max"}),
            expected_mount_id=mount_id,
        )
        if pids_limit_present and pids_events is None:
            raise ResourceLimitObservationError(
                f"{directory.name} pids.max is present but pids.events is unavailable"
            )
        if pids_events is not None:
            pids_samples[index] = pids_events
        pids_events_local = _read_event_file_if_present(
            directory / "pids.events.local",
            frozenset({"max"}),
            expected_mount_id=mount_id,
        )
        pids_local_effective = _effective_local_events(
            pids_events,
            pids_events_local,
            regular_is_local=pids_localevents,
            required=frozenset({"max"}),
            controller="pids",
        )
        if pids_local_effective is not None:
            pids_local_samples[index] = pids_local_effective

        memory_events = _read_event_file_if_present(
            directory / "memory.events",
            frozenset({"max", "oom", "oom_kill", "oom_group_kill"}),
            expected_mount_id=mount_id,
        )
        if memory_limit_present and memory_events is None:
            raise ResourceLimitObservationError(
                f"{directory.name} memory.max is present but memory.events is unavailable"
            )
        if memory_events is not None:
            memory_samples[index] = memory_events
        if index == 0 and (
            (pids_localevents and pids_events is not None)
            or (memory_localevents and memory_events is not None)
        ):
            _require_no_candidate_subgroups(directory)
        memory_events_local = _read_event_file_if_present(
            directory / "memory.events.local",
            frozenset({"max", "oom", "oom_kill", "oom_group_kill"}),
            expected_mount_id=mount_id,
        )
        memory_local_effective = _effective_local_events(
            memory_events,
            memory_events_local,
            regular_is_local=memory_localevents,
            required=frozenset({"max", "oom", "oom_kill", "oom_group_kill"}),
            controller="memory",
        )
        if memory_local_effective is not None:
            memory_local_samples[index] = memory_local_effective
        limit_signature.append(
            (
                str(directory),
                mount_id,
                limits[index][0],
                limits[index][2],
                pids_events is not None,
                memory_events is not None,
                pids_events_local is not None,
                memory_events_local is not None,
                pids_localevents,
                memory_localevents,
            )
        )
        limit_values.append((limits[index][1], limits[index][3]))

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
    path_event_counters = tuple(
        (
            str(directory),
            pids_samples.get(index, {}).get("max"),
            memory_samples.get(index, {}).get("max"),
            memory_samples.get(index, {}).get("oom"),
            memory_samples.get(index, {}).get("oom_kill"),
            memory_samples.get(index, {}).get("oom_group_kill"),
            pids_local_samples.get(index, {}).get("max"),
            memory_local_samples.get(index, {}).get("max"),
            memory_local_samples.get(index, {}).get("oom"),
            memory_local_samples.get(index, {}).get("oom_kill"),
            memory_local_samples.get(index, {}).get("oom_group_kill"),
        )
        for index, directory in enumerate(directories)
    )
    return ResourceLimitCounters(
        pids_max=pids_max,
        memory_max=memory_max,
        memory_oom=memory_oom,
        memory_oom_kill=memory_oom_kill,
        memory_oom_group_kill=memory_oom_group_kill,
        limit_signature=tuple(limit_signature),
        limit_values=tuple(limit_values),
        path_event_counters=path_event_counters,
    )


def read_candidate_resource_counters(
    *, observer_socket_path: str | None = None,
) -> ResourceLimitCounters:
    """Use the local hierarchy, consulting cgprofile only for hidden ancestors."""
    try:
        return read_current_cgroup_counters()
    except ResourceLimitAncestorsHiddenError:
        if observer_socket_path is None:
            raise
        if not isinstance(observer_socket_path, str) or not observer_socket_path:
            raise ResourceLimitObservationError(
                "ASSAY_RESOURCE_OBSERVER_SOCKET must be a non-empty absolute socket path"
            ) from None
        if not os.path.isabs(observer_socket_path):
            raise ResourceLimitObservationError(
                "ASSAY_RESOURCE_OBSERVER_SOCKET must be an absolute socket path"
            ) from None
        return read_resource_observer_counters(observer_socket_path)


def read_resource_observer_counters(socket_path: str) -> ResourceLimitCounters:
    """Fetch one authenticated thread sample from cgprofile's observer-only socket."""
    if not isinstance(socket_path, str) or not socket_path:
        raise ResourceLimitObservationError(
            "configured resource observer socket path must be non-empty"
        )
    if not os.path.isabs(socket_path):
        raise ResourceLimitObservationError(
            "configured resource observer socket path must be absolute"
        )
    path = Path(socket_path)
    thread_id = threading.get_native_id()
    request = json.dumps(
        {"verb": "resource-limits", "args": {"thread_id": thread_id}, "contract": 2},
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    retry_deadline = time.monotonic() + _RESOURCE_OBSERVER_RESTART_RETRY_SECONDS
    response: bytearray | None = None
    while response is None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED) \
                    or time.monotonic() >= retry_deadline:
                raise ResourceLimitObservationError(
                    f"cannot inspect configured resource observer socket: {exc}"
                ) from exc
            time.sleep(min(
                _RESOURCE_OBSERVER_RETRY_INTERVAL_SECONDS,
                max(0.0, retry_deadline - time.monotonic()),
            ))
            continue
        if not stat.S_ISSOCK(metadata.st_mode):
            raise ResourceLimitObservationError(
                "configured resource observer endpoint is not a Unix socket"
            )

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        candidate_response = bytearray()
        request_deadline = time.monotonic() + _RESOURCE_OBSERVER_REQUEST_TIMEOUT_SECONDS

        def set_remaining_timeout() -> None:
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                raise ResourceLimitObservationError(
                    "resource observer socket request timed out"
                )
            client.settimeout(remaining)

        try:
            set_remaining_timeout()
            client.connect(socket_path)
            set_remaining_timeout()
            client.sendall(request)
            while True:
                set_remaining_timeout()
                chunk = client.recv(65536)
                if not chunk:
                    break
                candidate_response.extend(chunk)
                if len(candidate_response) > 1024 * 1024:
                    raise ResourceLimitObservationError(
                        "resource observer response exceeds 1 MiB"
                    )
        except ResourceLimitObservationError:
            raise
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED) \
                    or time.monotonic() >= retry_deadline:
                raise ResourceLimitObservationError(
                    f"resource observer socket request failed: {exc}"
                ) from exc
            time.sleep(min(
                _RESOURCE_OBSERVER_RETRY_INTERVAL_SECONDS,
                max(0.0, retry_deadline - time.monotonic()),
            ))
        else:
            response = candidate_response
        finally:
            client.close()
    assert response is not None
    if not response.endswith(b"\n") or response.count(b"\n") != 1:
        raise ResourceLimitObservationError(
            "resource observer response is not exactly one JSON line"
        )
    try:
        document = json.loads(response[:-1].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ResourceLimitObservationError(
            "resource observer response cannot be parsed as JSON"
        ) from exc
    return _resource_counters_from_observer(document, thread_id)


def _resource_counters_from_observer(
    document: Any, expected_thread_id: int
) -> ResourceLimitCounters:
    if not isinstance(document, Mapping):
        raise ResourceLimitObservationError("resource observer response is not an object")
    if document.get("ok") is not True:
        error = document.get("error")
        if (
            document.get("ok") is False
            and type(document.get("contract")) is int
            and document.get("contract") in {1, 2}
            and isinstance(error, Mapping)
            and isinstance(error.get("message"), str)
        ):
            raise ResourceLimitObservationError(
                f"resource observer refused the query: {error['message']}"
            )
        raise ResourceLimitObservationError("resource observer returned a malformed error")
    if type(document.get("contract")) is not int or document["contract"] != 2:
        raise ResourceLimitObservationError(
            "resource observer contract version 2 is required for local event counters"
        )
    if set(document) != {
        "ok", "contract", "cgroup_version", "thread_id", "gates_slice",
        "pids_events_scope", "memory_events_scope", "cgroups",
    }:
        raise ResourceLimitObservationError(
            "resource observer response has unknown or missing fields"
        )
    if not isinstance(document["pids_events_scope"], str) or document[
        "pids_events_scope"
    ] not in {"local", "hierarchical"}:
        raise ResourceLimitObservationError("resource observer pids event scope is malformed")
    if not isinstance(document["memory_events_scope"], str) or document[
        "memory_events_scope"
    ] not in {"local", "hierarchical"}:
        raise ResourceLimitObservationError("resource observer memory event scope is malformed")
    pids_localevents = document["pids_events_scope"] == "local"
    memory_localevents = document["memory_events_scope"] == "local"
    if type(document["cgroup_version"]) is not int or document["cgroup_version"] != 2:
        raise ResourceLimitObservationError("resource observer did not report cgroup v2")
    if type(document["thread_id"]) is not int or document["thread_id"] != expected_thread_id:
        raise ResourceLimitObservationError("resource observer thread identity does not match")
    gates_path = _canonical_observer_path(document["gates_slice"], "gates_slice")
    raw_cgroups = document["cgroups"]
    if not isinstance(raw_cgroups, list) or not raw_cgroups:
        raise ResourceLimitObservationError("resource observer cgroups must be a non-empty list")

    signatures: list[LimitSignatureEntry] = []
    limit_values: list[LimitValueEntry] = []
    path_counters: list[
        tuple[
            str,
            int | None, int | None, int | None, int | None, int | None,
            int | None, int | None, int | None, int | None, int | None,
        ]
    ] = []
    pids_total = 0
    memory_totals = [0, 0, 0, 0]
    pids_available = False
    memory_available = False
    previous_path: PurePosixPath | None = None
    mount_id: int | None = None
    for raw in raw_cgroups:
        if not isinstance(raw, Mapping) or set(raw) != {
            "path", "mount_id", "pids_max_present", "pids_max",
            "memory_max_present", "memory_max", "pids_events", "memory_events",
            "pids_events_local", "memory_events_local",
        }:
            raise ResourceLimitObservationError("resource observer cgroup entry is malformed")
        path_value = _canonical_observer_path(raw["path"], "cgroup path")
        if previous_path is not None and path_value != previous_path.parent:
            raise ResourceLimitObservationError("resource observer cgroups are not ordered leaf-to-root")
        previous_path = path_value
        if type(raw["mount_id"]) is not int or raw["mount_id"] <= 0:
            raise ResourceLimitObservationError("resource observer mount ID is invalid")
        if mount_id is None:
            mount_id = raw["mount_id"]
        elif mount_id != raw["mount_id"]:
            raise ResourceLimitObservationError("resource observer cgroups span multiple mounts")

        pids_present, pids_limit = _observer_limit(
            raw["pids_max_present"], raw["pids_max"], "pids.max"
        )
        memory_present, memory_limit = _observer_limit(
            raw["memory_max_present"], raw["memory_max"], "memory.max"
        )
        pids_events = _observer_events(raw["pids_events"], {"max"}, "pids.events")
        memory_events = _observer_events(
            raw["memory_events"],
            {"max", "oom", "oom_kill", "oom_group_kill"},
            "memory.events",
        )
        pids_events_local = _observer_events(
            raw["pids_events_local"], {"max"}, "pids.events.local"
        )
        memory_events_local = _observer_events(
            raw["memory_events_local"],
            {"max", "oom", "oom_kill", "oom_group_kill"},
            "memory.events.local",
        )
        if pids_present and pids_events is None:
            raise ResourceLimitObservationError("finite or unlimited pids.max lacks pids.events")
        if memory_present and memory_events is None:
            raise ResourceLimitObservationError("finite or unlimited memory.max lacks memory.events")
        pids_local_effective = _effective_local_events(
            pids_events,
            pids_events_local,
            regular_is_local=pids_localevents,
            required=frozenset({"max"}),
            controller="pids",
        )
        memory_local_effective = _effective_local_events(
            memory_events,
            memory_events_local,
            regular_is_local=memory_localevents,
            required=frozenset({"max", "oom", "oom_kill", "oom_group_kill"}),
            controller="memory",
        )
        signatures.append(
            (
                str(path_value),
                raw["mount_id"],
                pids_present,
                memory_present,
                pids_events is not None,
                memory_events is not None,
                pids_events_local is not None,
                memory_events_local is not None,
                pids_localevents,
                memory_localevents,
            )
        )
        limit_values.append((pids_limit, memory_limit))
        path_counters.append((
            str(path_value),
            None if pids_events is None else pids_events["max"],
            None if memory_events is None else memory_events["max"],
            None if memory_events is None else memory_events["oom"],
            None if memory_events is None else memory_events["oom_kill"],
            None if memory_events is None else memory_events["oom_group_kill"],
            None if pids_local_effective is None else pids_local_effective["max"],
            None if memory_local_effective is None else memory_local_effective["max"],
            None if memory_local_effective is None else memory_local_effective["oom"],
            None if memory_local_effective is None else memory_local_effective["oom_kill"],
            None if memory_local_effective is None else memory_local_effective["oom_group_kill"],
        ))
        if pids_events is not None:
            pids_available = True
            pids_total += pids_events["max"]
        if memory_events is not None:
            memory_available = True
            for index, key in enumerate(("max", "oom", "oom_kill", "oom_group_kill")):
                memory_totals[index] += memory_events[key]

    if previous_path != PurePosixPath("/"):
        raise ResourceLimitObservationError("resource observer ancestry does not reach the hierarchy root")
    candidate_path = PurePosixPath(path_counters[0][0])
    if candidate_path == gates_path or not candidate_path.is_relative_to(gates_path):
        raise ResourceLimitObservationError("resource observer candidate is outside its gates slice")
    if not pids_available:
        raise ResourceLimitObservationError("resource observer has no pids.events counters")
    if not memory_available:
        raise ResourceLimitObservationError("resource observer has no memory.events counters")
    return ResourceLimitCounters(
        pids_max=pids_total,
        memory_max=memory_totals[0],
        memory_oom=memory_totals[1],
        memory_oom_kill=memory_totals[2],
        memory_oom_group_kill=memory_totals[3],
        limit_signature=tuple(signatures),
        limit_values=tuple(limit_values),
        path_event_counters=tuple(path_counters),
    )


def _canonical_observer_path(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ResourceLimitObservationError(f"resource observer {label} is not absolute")
    path = PurePosixPath(value)
    if str(path) != value or ".." in path.parts or "." in path.parts:
        raise ResourceLimitObservationError(f"resource observer {label} is not canonical")
    return path


def _observer_limit(present: Any, value: Any, name: str) -> tuple[bool, int | None]:
    if type(present) is not bool:
        raise ResourceLimitObservationError(f"resource observer {name} presence is malformed")
    if value is not None and (type(value) is not int or value < 0):
        raise ResourceLimitObservationError(f"resource observer {name} value is malformed")
    if not present and value is not None:
        raise ResourceLimitObservationError(f"resource observer absent {name} has a value")
    return present, value


def _observer_events(
    raw: Any, required: set[str], name: str
) -> dict[str, int] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise ResourceLimitObservationError(f"resource observer {name} fields are malformed")
    if any(type(value) is not int or value < 0 for value in raw.values()):
        raise ResourceLimitObservationError(f"resource observer {name} counters are malformed")
    return {key: raw[key] for key in required}


def inspect_current_cgroup_observation(
    *, observer_socket_path: str | None = None
) -> ResourceObservationCapability:
    """Probe the full B145 observation contract without classifying a candidate.

    Planning and pre-R0 refusals use this read-only report. Candidate attempts
    still take their own before/after samples, because a successful probe cannot
    certify that visibility remains stable for the later execution. It uses the
    same local-first/hidden-ancestor-only observer fallback as candidate reads.
    Read it on a fresh thread: R2 samples `/proc/thread-self/cgroup` from executor
    workers, and threaded cgroup v2 placements can differ from the CLI's main
    thread.
    """
    error: BaseException | None = None

    def probe() -> None:
        nonlocal error
        try:
            read_candidate_resource_counters(
                observer_socket_path=observer_socket_path
            )
        except BaseException as exc:
            error = exc

    try:
        worker = threading.Thread(
            target=probe, name="assay-cgroup-observation-preflight", daemon=True
        )
        worker.start()
    except (OSError, RuntimeError) as exc:
        return ResourceObservationCapability(
            available=False,
            reason=f"cannot start cgroup observation probe thread: {exc}",
        )
    worker.join(
        timeout=(
            _RESOURCE_OBSERVER_REQUEST_TIMEOUT_SECONDS
            + _RESOURCE_OBSERVER_PREFLIGHT_JOIN_GRACE_SECONDS
        )
    )
    if worker.is_alive():
        return ResourceObservationCapability(
            available=False,
            reason="resource observation preflight did not complete before its deadline",
        )
    if isinstance(error, ResourceLimitObservationError):
        return ResourceObservationCapability(available=False, reason=str(error))
    if error is not None:
        raise error
    return ResourceObservationCapability(available=True)

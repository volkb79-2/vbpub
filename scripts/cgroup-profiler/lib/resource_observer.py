"""Read-only, lane-scoped cgroup-v2 observation for native Assay R2.

The caller supplies only its native thread ID.  The daemon binds that ID to
the process authenticated by SO_PEERCRED, proves all of that process's live
threads share one cgroup, and derives the cgroup path from host procfs.  It
never accepts a caller-selected PID or path and returns no process list.
"""

from __future__ import annotations

import errno
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from . import targets


class ResourceObserverError(RuntimeError):
    """The peer identity or its complete cgroup ancestry is unprovable."""


_PIDS_EVENTS = frozenset({"max"})
_MEMORY_EVENTS = frozenset({"max", "oom", "oom_kill", "oom_group_kill"})
_CAPS_THAT_CAN_BYPASS_CGROUP_DAC = sum(1 << bit for bit in (0, 1, 3, 6, 7, 21))
def observe_peer_cgroup(
    *,
    peer_pid: int | None,
    peer_uid: int | None,
    thread_id: object,
    proc_root: str,
    cgroup_root: str,
    mountinfo_path: str,
    gates_slice_name: str,
) -> dict[str, Any]:
    """Return counters for the authenticated peer's cgroup and ancestors.

    The gates slice path is a daemon policy fact. The request cannot select a
    different cgroup, process, slice, or filesystem path.
    """
    if peer_pid is None or type(peer_pid) is not int or peer_pid <= 0:
        raise ResourceObserverError("SO_PEERCRED did not provide a host-visible peer PID")
    if type(thread_id) is not int or thread_id <= 0:
        raise ResourceObserverError("thread_id must be a positive native TID")
    if peer_uid is None or type(peer_uid) is not int or peer_uid < 0:
        raise ResourceObserverError("SO_PEERCRED did not provide a valid peer UID")

    peer = _resolve_peer(peer_pid, peer_uid, thread_id, proc_root)
    gates_path = PurePosixPath(targets.slice_to_path(gates_slice_name))
    root = Path(cgroup_root)
    if not root.is_absolute():
        raise ResourceObserverError("configured cgroup root is not absolute")
    root = Path(os.path.normpath(str(root)))
    mount_id, namespace_parent_levels = _cgroup_mount(root, Path(mountinfo_path))
    leaf_path = _host_cgroup_path(peer["cgroup"], namespace_parent_levels)
    if leaf_path == gates_path or not leaf_path.is_relative_to(gates_path):
        raise ResourceObserverError("peer cgroup is not a descendant of the configured gates slice")
    for task in peer["tasks"]:
        task["cgroup"] = str(_host_cgroup_path(
            task["cgroup"], namespace_parent_levels
        ))

    entries: list[dict[str, Any]] = []
    current = leaf_path
    while True:
        directory = root.joinpath(*current.parts[1:])
        entries.append(_read_cgroup_entry(current, directory, mount_id, peer["tasks"]))
        if current == PurePosixPath("/"):
            break
        current = current.parent

    after = _resolve_peer(peer_pid, peer_uid, thread_id, proc_root)
    if after["cgroup"] != peer["cgroup"] or after["start_ticks"] != peer["start_ticks"]:
        raise ResourceObserverError("peer process or cgroup identity changed during observation")

    return {
        "cgroup_version": 2,
        "thread_id": thread_id,
        "gates_slice": str(gates_path),
        "cgroups": entries,
    }


def _resolve_peer(peer_pid: int, peer_uid: int, thread_id: int, proc_root: str) -> dict[str, Any]:
    peer_dir = Path(proc_root) / str(peer_pid)
    start_ticks = _proc_start_ticks(peer_dir / "stat")
    task_root = peer_dir / "task"
    try:
        tasks = sorted(
            (entry for entry in task_root.iterdir() if entry.name.isdecimal()),
            key=lambda entry: int(entry.name),
        )
    except OSError as exc:
        raise ResourceObserverError("cannot enumerate authenticated peer threads") from exc
    if not tasks:
        raise ResourceObserverError("authenticated peer has no visible live threads")

    selected: list[dict[str, Any]] = []
    task_facts: list[dict[str, Any]] = []
    for task_dir in tasks:
        try:
            facts = _task_facts(task_dir)
        except FileNotFoundError:
            raise ResourceObserverError("peer thread disappeared during identity verification") from None
        if facts["native_tid"] == thread_id:
            selected.append(facts)
        task_facts.append(facts)
    if len(selected) != 1:
        raise ResourceObserverError("native TID is not a unique thread of the authenticated peer")
    cgroups = {facts["cgroup"] for facts in task_facts}
    if len(cgroups) != 1:
        raise ResourceObserverError("peer threads span multiple cgroups; attribution is ambiguous")
    if _proc_start_ticks(peer_dir / "stat") != start_ticks:
        raise ResourceObserverError("authenticated peer PID changed during identity verification")

    # The SO_PEERCRED UID identifies the connecting peer.  Reject a proc view
    # whose effective UID no longer agrees with that connection credential.
    if selected[0]["euid"] != peer_uid:
        raise ResourceObserverError("peer UID changed after the socket connection was established")
    if any(facts["euid"] != peer_uid for facts in task_facts):
        raise ResourceObserverError("peer threads do not share the socket peer UID")
    credentials = {
        (facts["fsuid"], facts["fsgid"], facts["groups"]) for facts in task_facts
    }
    if len(credentials) != 1:
        raise ResourceObserverError("peer threads have different filesystem credentials")
    for facts in task_facts:
        if facts["cap_eff"] & _CAPS_THAT_CAN_BYPASS_CGROUP_DAC or facts["cap_prm"] & _CAPS_THAT_CAN_BYPASS_CGROUP_DAC:
            raise ResourceObserverError("peer thread capabilities can bypass cgroup control permissions")
    return {
        "cgroup": selected[0]["cgroup"],
        "start_ticks": start_ticks,
        "tasks": task_facts,
    }


def _proc_start_ticks(path: Path) -> int:
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ResourceObserverError("cannot read authenticated peer process identity") from exc
    closing = text.rfind(")")
    if closing <= 0:
        raise ResourceObserverError("peer process stat record is malformed")
    fields = text[closing + 1 :].split()
    if len(fields) <= 19 or not fields[19].isdecimal():
        raise ResourceObserverError("peer process start-time fact is malformed")
    return int(fields[19], 10)


def _task_facts(task_dir: Path) -> dict[str, Any]:
    status = _read_status(task_dir / "status")
    native_ids = status.get("NSpid")
    if not isinstance(native_ids, tuple) or not native_ids:
        raise ResourceObserverError("peer task has no valid NSpid mapping")
    cgroup_file = task_dir / "cgroup"
    try:
        cgroup_text = cgroup_file.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ResourceObserverError("cannot read peer thread cgroup identity") from exc
    cgroup = targets._unified_cgroup_path(cgroup_text)
    if not isinstance(cgroup, str) or not cgroup.startswith("/"):
        raise ResourceObserverError("peer thread has no absolute unified cgroup path")
    parsed_cgroup = PurePosixPath(cgroup)
    if str(parsed_cgroup) != cgroup:
        raise ResourceObserverError("peer thread cgroup path is not canonical")
    parts = parsed_cgroup.parts
    seen_non_parent = False
    for part in parts[1:]:
        if part == ".." and seen_non_parent:
            raise ResourceObserverError("peer thread cgroup path is not canonical")
        if part != "..":
            seen_non_parent = True
    host_tid = int(task_dir.name, 10)
    if native_ids[0] != host_tid:
        raise ResourceObserverError("peer task NSpid mapping does not match its host task ID")
    return {
        "host_tid": host_tid,
        "native_tid": native_ids[-1],
        "cgroup": str(parsed_cgroup),
        "euid": status["Uid"][1],
        "fsuid": status["Uid"][3],
        "fsgid": status["Gid"][3],
        "groups": status["Groups"],
        "cap_eff": status["CapEff"],
        "cap_prm": status["CapPrm"],
    }


def _read_status(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ResourceObserverError("cannot read peer thread status") from exc
    raw: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key in {"NSpid", "Uid", "Gid", "Groups", "CapEff", "CapPrm"}:
            if key in raw:
                raise ResourceObserverError(f"peer thread status repeats {key}")
            raw[key] = value.strip()
    required = {"NSpid", "Uid", "Gid", "Groups", "CapEff", "CapPrm"}
    if set(raw) != required:
        raise ResourceObserverError("peer thread status omits required identity or capability facts")
    try:
        values = {
            "NSpid": tuple(int(value, 10) for value in raw["NSpid"].split()),
            "Uid": tuple(int(value, 10) for value in raw["Uid"].split()),
            "Gid": tuple(int(value, 10) for value in raw["Gid"].split()),
            "Groups": tuple(int(value, 10) for value in raw["Groups"].split()),
            "CapEff": int(raw["CapEff"], 16),
            "CapPrm": int(raw["CapPrm"], 16),
        }
    except ValueError as exc:
        raise ResourceObserverError("peer thread status contains malformed numeric facts") from exc
    if (
        not values["NSpid"]
        or len(values["Uid"]) != 4
        or len(values["Gid"]) != 4
        or any(value <= 0 for value in values["NSpid"])
        or any(value < 0 for key, group in values.items() if key in {"NSpid", "Uid", "Gid", "Groups"} for value in group)
        or values["CapEff"] < 0
        or values["CapPrm"] < 0
    ):
        raise ResourceObserverError("peer thread status contains out-of-range identity facts")
    return values


def _cgroup_mount(cgroup_root: Path, mountinfo_path: Path) -> tuple[int, int]:
    try:
        lines = mountinfo_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ResourceObserverError("cannot inspect daemon cgroup mount identity") from exc
    matches: list[tuple[int, int]] = []
    nested_mounts: list[str] = []
    for line in lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            raise ResourceObserverError("daemon mountinfo contains a malformed record")
        fields = left.split()
        filesystem = right.split()
        if len(fields) < 5 or not filesystem:
            raise ResourceObserverError("daemon mountinfo contains a truncated record")
        mountpoint = _unescape_mountinfo(fields[4])
        normalized_mountpoint = os.path.normpath(mountpoint)
        normalized_root = os.path.normpath(str(cgroup_root))
        if normalized_mountpoint != normalized_root and os.path.commonpath(
            (normalized_root, normalized_mountpoint)
        ) == normalized_root:
            nested_mounts.append(normalized_mountpoint)
        if filesystem[0] != "cgroup2" or normalized_mountpoint != normalized_root:
            continue
        mount_root = _unescape_mountinfo(fields[3])
        parsed_mount_root = PurePosixPath(mount_root)
        root_parts = parsed_mount_root.parts
        parent_levels = len(root_parts) - 1
        if (
            root_parts[0] != "/"
            or str(parsed_mount_root) != mount_root
            or any(part != ".." for part in root_parts[1:])
        ):
            raise ResourceObserverError("daemon cgroup2 mount does not expose the hierarchy root")
        try:
            mount_id = int(fields[0], 10)
        except ValueError as exc:
            raise ResourceObserverError("daemon cgroup mount ID is malformed") from exc
        if mount_id <= 0:
            raise ResourceObserverError("daemon cgroup mount ID is invalid")
        matches.append((mount_id, parent_levels))
    if nested_mounts:
        raise ResourceObserverError(
            "nested mount(s) can shadow cgroup ancestry: "
            + ", ".join(sorted(nested_mounts))
        )
    if len(matches) != 1:
        raise ResourceObserverError("daemon has no unique full-root cgroup2 mount at its configured root")
    return matches[0]


def _host_cgroup_path(cgroup: str, namespace_parent_levels: int) -> PurePosixPath:
    """Map proc's peer cgroup path through the daemon's verified full-root mount.

    A private cgroup namespace reports an outside peer with leading parent
    components. The mountinfo root gives the exact count needed to reach the
    hierarchy root; both facts must agree. Paths inside the daemon's private
    subtree or with a mismatched mapping are refused.
    """
    path = PurePosixPath(cgroup)
    if path.parts[0] != "/" or str(path) != cgroup:
        raise ResourceObserverError("peer thread cgroup path is not canonical")
    parts = path.parts[1:]
    if namespace_parent_levels == 0:
        if any(part in {".", ".."} for part in parts):
            raise ResourceObserverError("peer thread cgroup path is not canonical")
        return path
    prefix = ("..",) * namespace_parent_levels
    if parts[:namespace_parent_levels] != prefix:
        raise ResourceObserverError(
            "peer thread cgroup path does not match the daemon's full-root namespace mapping"
        )
    remaining = parts[namespace_parent_levels:]
    if any(part in {".", ".."} for part in remaining):
        raise ResourceObserverError("peer thread cgroup path is not canonical")
    return PurePosixPath("/", *remaining)


def _unescape_mountinfo(value: str) -> str:
    return re.sub(
        r"\\(040|011|012|134)",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _read_cgroup_entry(
    cgroup_path: PurePosixPath,
    directory: Path,
    mount_id: int,
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        metadata = directory.lstat()
    except OSError as exc:
        raise ResourceObserverError(f"cannot inspect cgroup ancestor {cgroup_path}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ResourceObserverError(f"cgroup ancestor {cgroup_path} is not a directory")

    pids_limit_present, pids_max = _read_limit(directory / "pids.max")
    memory_limit_present, memory_max = _read_limit(directory / "memory.max")
    pids_events = _read_events(directory / "pids.events", _PIDS_EVENTS)
    memory_events = _read_events(directory / "memory.events", _MEMORY_EVENTS)
    if pids_limit_present and pids_events is None:
        raise ResourceObserverError(f"{cgroup_path} pids.max is present but pids.events is unavailable")
    if memory_limit_present and memory_events is None:
        raise ResourceObserverError(f"{cgroup_path} memory.max is present but memory.events is unavailable")

    procs = directory / "cgroup.procs"
    try:
        procs_stat = procs.stat()
    except OSError as exc:
        raise ResourceObserverError(f"cgroup.procs is unavailable at {cgroup_path}") from exc
    if _has_access_acl(procs):
        raise ResourceObserverError(f"cannot prove cgroup.procs DAC permissions at {cgroup_path}")
    mode = stat.S_IMODE(procs_stat.st_mode)
    for task in tasks:
        if _mode_grants_write(
            mode, procs_stat.st_uid, procs_stat.st_gid,
            task["fsuid"], task["fsgid"], task["groups"],
        ):
            raise ResourceObserverError(
                f"peer credentials can write cgroup.procs at {cgroup_path} and migrate into an unsampled cgroup"
            )

    return {
        "path": str(cgroup_path),
        "mount_id": mount_id,
        "pids_max_present": pids_limit_present,
        "pids_max": pids_max,
        "memory_max_present": memory_limit_present,
        "memory_max": memory_max,
        "pids_events": pids_events,
        "memory_events": memory_events,
    }


def _read_limit(path: Path) -> tuple[bool, int | None]:
    try:
        text = path.read_text(encoding="ascii").split()
    except FileNotFoundError:
        return False, None
    except (OSError, UnicodeError) as exc:
        raise ResourceObserverError(f"cannot read {path.name}") from exc
    if text == ["max"]:
        return True, None
    if len(text) != 1 or not text[0].isdecimal():
        raise ResourceObserverError(f"{path.name} is malformed")
    return True, int(text[0], 10)


def _read_events(path: Path, required: frozenset[str]) -> dict[str, int] | None:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise ResourceObserverError(f"cannot read {path.name}") from exc
    values: dict[str, int] = {}
    for line in lines:
        fields = line.split()
        if len(fields) != 2 or fields[0] in values:
            raise ResourceObserverError(f"{path.name} contains a malformed event record")
        try:
            value = int(fields[1], 10)
        except ValueError as exc:
            raise ResourceObserverError(f"{path.name} contains a malformed event value") from exc
        if value < 0:
            raise ResourceObserverError(f"{path.name} contains a negative event value")
        values[fields[0]] = value
    if not required.issubset(values):
        raise ResourceObserverError(f"{path.name} omits required event counters")
    return {name: values[name] for name in required}


def _has_access_acl(path: Path) -> bool:
    try:
        os.getxattr(path, "system.posix_acl_access")
    except OSError as exc:
        if exc.errno in {
            errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS,
        }:
            return False
        raise ResourceObserverError(f"cannot inspect ACL on {path.name}") from exc
    return True


def _mode_grants_write(
    mode: int,
    owner_uid: int,
    owner_gid: int,
    fsuid: int,
    fsgid: int,
    groups: tuple[int, ...],
) -> bool:
    if fsuid == owner_uid:
        return bool(mode & stat.S_IWUSR)
    if owner_gid in {fsgid, *groups}:
        return bool(mode & stat.S_IWGRP)
    return bool(mode & stat.S_IWOTH)

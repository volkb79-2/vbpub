"""Lane placement — RG55-INTERFACE-CONTRACT.md §8.3, design D-20/D-25/D-31.

An *ephemeral* lane is a container, so docker already made it a cgroup of
its own and capped it at create time. An **exec-mode** lane (``docker exec``
into a long-lived devcontainer) and a **bare-host** lane have neither: their
cgroup is shared with the IDE, the agents and the caller, so ``memory.peak``,
``memory.pressure`` and ``io.stat`` are somebody else's numbers and the
lane's declared ``resources.memory`` is advisory. D-31's answer is that the
private-namespace daemon asks host systemd to create a transient delegated
scope beneath the verified gates slice, then creates its own
``rg-<token>`` leaf beneath that scope. Host systemd's
``AttachProcessesToUnit`` D-Bus method moves host PIDs into the leaf and back;
the daemon uses the host PID namespace, but does not write into the
systemd-owned slice. Before any move, a write-ahead journal records each
PID's start-time identity and exact original cgroup/unit so a later daemon
can restore only processes it can still prove it owns.

**Why a delegated scope between the gates slice and profiler leaf** (D-31):
systemd owns slice cgroups and does not support delegating them as a writable
subtree. The transient scope is the supported delegation boundary; systemd
owns the scope root, while cgprofile owns only the leaf beneath it. The scope
is a direct child of the bounded gates slice, so its workload remains inside
that slice's capacity ceiling. Enabling controllers at the delegated scope
root requires moving its processes into the leaf first, due to cgroup v2's
no-internal-process rule. Moving a PID out of its original container scope
preserves its PID and mount namespaces (pid-1's death still kills it); only
its cgroup accounting moves.

**The write boundary is a whitelist, never a relaxation** (D-15 extended by
D-25, plus RW-35(a)'s single exception). :class:`CgroupWriteGuard` is the
third half of the ``WRITABLE_ROOTS`` boundary whose other halves are
:func:`lib.damon._write_nr_kdamonds` (the DAMON admin sysfs root) and
:meth:`lib.serve.SessionServer._guard_path` (the sessions directory). It
admits exactly:

* ``<delegated scope>/cgroup.subtree_control`` — ``+memory``/``+cpu``/``+pids``
  ONLY (RW-35(a): the one non-leaf write, because a hand-created leaf cannot
  take ``memory.high`` unless its parent delegates the controller; a ``-``
  value is refused);
* ``<delegated scope>/rg-*/{cgroup.procs, memory.high, memory.max, cpu.weight,
  cgroup.kill}`` — the leaf this daemon made;
* ``cgroup.kill`` on one exact container-ID cgroup beneath the verified,
  bounded gates slice (never a parent, sibling, or path chosen by a label);
* ``mkdir``/``rmdir`` of ``<delegated scope>/rg-*``;
* the original cgroup's ``cgroup.procs`` only for move-back at ``stop``;
  production PID transfers use the host manager so systemd remains the owner
  of scope lifecycle and exact-unit attachment.

Every other cgroup path — a production tier, the gates slice's own
``memory.max``, another session's leaf — raises :class:`HostWriteError`
before the ``open()``. Every admitted write is also an ``events.jsonl`` row
(D-25) through the ``on_write`` sink the server passes in.

Everything here works against ``cgroup_root`` as a parameter, so the whole
module is exercised on a fake cgroupfs in a tmp directory — the same seam
every other reader in this package takes.
"""

from __future__ import annotations

import errno
import math
import os
import posixpath
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from . import access, proc_stat, util


def _systemd_attach_process(
    unit_name: str,
    subcgroup: str,
    pid: int,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Ask host systemd to move one host PID into ``subcgroup``.

    The daemon is deliberately deployed in the host PID namespace so DAMON
    can resolve host ``pid_target`` values. Placement still uses the systemd
    manager as the owner of the delegated scope and exact-unit attachment;
    this keeps the systemd-owned boundary explicit and does not grant the
    daemon a Docker socket. ``unit_name`` is either the verified gates slice
    (move into the lane leaf) or the unit owning the original scope (move a
    survivor back).
    """
    # systemd requires an absolute subcgroup path within the named unit.
    # A bare rg-token leaf name is refused by the live host bus, leaving
    # every healthy private-PID placement unplaced. Callers derive a
    # relative suffix from a verified cgroup path; check it at this
    # boundary before adding the required leading slash.
    if not isinstance(subcgroup, str) or (
        subcgroup
        and (
            subcgroup.startswith("/")
            or any(part in ("", ".", "..") for part in subcgroup.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in subcgroup)
        )
    ):
        return False
    absolute_subcgroup = "/" + subcgroup
    busctl = os.environ.get("CGPROFILE_BUSCTL", "busctl")
    try:
        completed = run(
            [
                busctl,
                "--system",
                "call",
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager",
                "AttachProcessesToUnit",
                "ssau",
                unit_name,
                absolute_subcgroup,
                "1",
                str(pid),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _systemd_unit_path(unit_name: str) -> Optional[str]:
    """Resolve a unit through systemd instead of guessing its cgroup path."""
    busctl = os.environ.get("CGPROFILE_BUSCTL", "busctl")
    try:
        completed = subprocess.run(
            [
                busctl, "--system", "call", "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
                "GetUnit", "s", unit_name,
            ],
            check=False, capture_output=True, text=True, timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        fields = shlex.split(completed.stdout)
    except ValueError:
        return None
    if len(fields) != 2 or fields[0] != "o":
        return None
    return fields[1]


def _systemd_unit_is_absent(
    unit_name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Optional[bool]:
    """Return true only for systemd's explicit exact-unit absence response.

    A failed GetUnit can also mean that the manager or bus is unavailable, so
    a nonzero status is not evidence that the unit retired. Keep that case
    distinct from the exact, manager-authored absence response.
    """
    busctl = os.environ.get("CGPROFILE_BUSCTL", "busctl")
    try:
        completed = run(
            [
                busctl, "--system", "call", "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
                "GetUnit", "s", unit_name,
            ],
            check=False, capture_output=True, text=True, timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode == 0:
        return False
    # `busctl call` on the deployed host renders NoSuchUnit as only
    # "Call failed: Unit <name> not loaded."; other versions include the
    # D-Bus error name. Require either complete line for this exact unit,
    # never a substring or a generic failed GetUnit.
    message = f"Unit {unit_name} not loaded."
    error = (completed.stderr or "").strip()
    if error in (
        f"Call failed: {message}",
        f"Call failed: org.freedesktop.systemd1.NoSuchUnit: {message}",
    ):
        return True
    return None


def _systemd_property(unit_path: str, interface: str, name: str) -> Optional[str]:
    """Read one string property from the host manager's authoritative unit."""
    busctl = os.environ.get("CGPROFILE_BUSCTL", "busctl")
    try:
        completed = subprocess.run(
            [
                busctl, "--system", "get-property", "org.freedesktop.systemd1",
                unit_path, interface, name,
            ],
            check=False, capture_output=True, text=True, timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        fields = shlex.split(completed.stdout)
    except ValueError:
        return None
    if len(fields) != 2 or fields[0] != "s":
        return None
    return fields[1]


def _systemd_bool_property(unit_path: str, interface: str, name: str) -> Optional[bool]:
    busctl = os.environ.get("CGPROFILE_BUSCTL", "busctl")
    try:
        completed = subprocess.run(
            [
                busctl, "--system", "get-property", "org.freedesktop.systemd1",
                unit_path, interface, name,
            ],
            check=False, capture_output=True, text=True, timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        fields = shlex.split(completed.stdout)
    except ValueError:
        return None
    if len(fields) != 2 or fields[0] != "b" or fields[1] not in ("true", "false"):
        return None
    return fields[1] == "true"


def _systemd_string_array_property(
    unit_path: str, interface: str, name: str,
) -> Optional[List[str]]:
    busctl = os.environ.get("CGPROFILE_BUSCTL", "busctl")
    try:
        completed = subprocess.run(
            [
                busctl, "--system", "get-property", "org.freedesktop.systemd1",
                unit_path, interface, name,
            ],
            check=False, capture_output=True, text=True, timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        fields = shlex.split(completed.stdout)
        if len(fields) < 2 or fields[0] != "as":
            return None
        count = int(fields[1])
        values = fields[2:]
    except (ValueError, TypeError):
        return None
    return values if count == len(values) else None


def _systemd_create_scope(
    unit_name: str,
    slice_unit: str,
    pids: Sequence[int],
    controllers: Sequence[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Optional[str]:
    """Create one delegated scope with the initial PIDs and return its path.

    ``StartTransientUnit``'s scope ``PIDs`` property is the initial ownership
    transfer. The returned path is read back from systemd; it is never
    reconstructed from the requested name.
    """
    if not pids or any(isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 for pid in pids):
        return None
    if not unit_name.endswith(".scope") or not slice_unit.endswith(".slice"):
        return None
    if not controllers or any(
        not isinstance(item, str) or not re.fullmatch(r"[a-z0-9_]+", item)
        for item in controllers
    ):
        return None
    properties: List[str] = [
        "Description", "s", "cgprofile lane placement",
        "Slice", "s", slice_unit,
        "PIDs", "au", str(len(pids)), *(str(pid) for pid in pids),
        "Delegate", "b", "true",
        "DelegateControllers", "as", str(len(controllers)), *controllers,
    ]
    argv = [
        os.environ.get("CGPROFILE_BUSCTL", "busctl"), "--system", "call",
        "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager", "StartTransientUnit",
        "ssa(sv)a(sa(sv))", unit_name, "fail", "5", *properties, "0",
    ]
    try:
        result = run(argv, check=False, capture_output=True, text=True, timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    # The job may be queued when StartTransientUnit returns. Poll its
    # authoritative properties briefly; an absent/unverified result is a
    # placement refusal, never permission to infer the path from the unit.
    for _ in range(40):
        path = _systemd_unit_path(unit_name)
        if path is not None:
            load_state = _systemd_property(
                path, "org.freedesktop.systemd1.Unit", "LoadState"
            )
            actual_cgroup = _systemd_property(
                path, "org.freedesktop.systemd1.Scope", "ControlGroup"
            )
            actual_slice = _systemd_property(
                path, "org.freedesktop.systemd1.Scope", "Slice"
            )
            delegated = _systemd_bool_property(
                path, "org.freedesktop.systemd1.Scope", "Delegate"
            )
            actual_controllers = _systemd_string_array_property(
                path, "org.freedesktop.systemd1.Scope", "DelegateControllers"
            )
            if (
                load_state == "loaded" and actual_cgroup and actual_slice == slice_unit
                and delegated is True and actual_controllers is not None
                and set(controllers).issubset(actual_controllers)
            ):
                return actual_cgroup
        time.sleep(0.05)
    return None


def _systemd_stop_unit(
    unit_name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Retire only the exact empty transient scope created for this session."""
    busctl = os.environ.get("CGPROFILE_BUSCTL", "busctl")
    try:
        result = run(
            [
                busctl, "--system", "call", "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
                "StopUnit", "ss", unit_name, "fail",
            ],
            check=False, capture_output=True, text=True, timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    # StopUnit returns when the job is accepted, not necessarily when the
    # unit has reached its terminal state. Never report successful retirement
    # based only on an accepted request.
    for _ in range(40):
        path = _systemd_unit_path(unit_name)
        if path is None:
            return _systemd_unit_is_absent(unit_name, run=run) is True
        active = _systemd_property(
            path, "org.freedesktop.systemd1.Unit", "ActiveState"
        )
        substate = _systemd_property(
            path, "org.freedesktop.systemd1.Unit", "SubState"
        )
        if active == "inactive" and substate == "dead":
            return True
        time.sleep(0.05)
    return False


def _systemd_destination(cgroup: str) -> Optional[tuple[str, str]]:
    """Map an absolute cgroup path to its nearest systemd unit and subgroup.

    The systemd bridge is deliberately scoped to a real unit in the already
    resolved cgroup path. Refuse relative or traversal-shaped paths and paths
    with no unit boundary rather than asking systemd to attach to a broader
    ancestor such as ``-.slice``.
    """
    if not isinstance(cgroup, str) or not cgroup.startswith("/"):
        return None
    parts = cgroup[1:].split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    for index in range(len(parts) - 1, -1, -1):
        if parts[index].endswith((".scope", ".service", ".slice")):
            return parts[index], "/".join(parts[index + 1:])
    return None


def _systemd_unit_cgroup_matches(unit_name: str, expected_cgroup: str) -> bool:
    """Verify a path-derived unit name against systemd's own ControlGroup."""
    unit_path = _systemd_unit_path(unit_name)
    if unit_path is None:
        return False
    load_state = _systemd_property(
        unit_path, "org.freedesktop.systemd1.Unit", "LoadState"
    )
    suffix = unit_name.rsplit(".", 1)[-1]
    if suffix not in {"scope", "service", "slice"}:
        return False
    actual = _systemd_property(
        unit_path, f"org.freedesktop.systemd1.{suffix.title()}", "ControlGroup"
    )
    return (
        load_state == "loaded"
        and actual is not None
        and posixpath.normpath(actual) == posixpath.normpath(expected_cgroup)
    )


def _process_start_time_ticks(proc_root: str, pid: int) -> Optional[str]:
    """Return Linux /proc stat field 22, without being confused by comm's ')'s."""
    text = util.read_text(os.path.join(proc_root, str(pid), "stat"))
    if not text:
        return None
    parsed = proc_stat.split_after_comm(text)
    if parsed is None:
        return None
    close, fields = parsed
    if close <= 0:
        return None
    # The suffix starts at stat field 3 (state), so field 22 is index 19.
    if len(fields) <= 19 or not fields[19].isdigit():
        return None
    return fields[19]


def _process_parent_pid(proc_root: str, pid: int) -> Optional[int]:
    """Return ``/proc/<pid>/stat`` field 4, robust to ``comm`` contents."""
    text = util.read_text(os.path.join(proc_root, str(pid), "stat"))
    if not text:
        return None
    parsed = proc_stat.split_after_comm(text)
    if parsed is None:
        return None
    close, fields = parsed
    if close <= 0:
        return None
    if len(fields) < 2 or not fields[1].isdigit():
        return None
    return int(fields[1])


def _host_pid_of_self(proc_root: str) -> Optional[int]:
    """Read this process's host PID from the first value in ``NSpid``."""
    text = util.read_text(os.path.join(proc_root, "self", "status"))
    if not text:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if key != "NSpid":
            continue
        if not sep:
            continue
        values = value.split()
        if values and values[0].isdigit() and int(values[0]) > 0:
            return int(values[0])
    return None


def _within_cgroup(path: str, parent: str) -> bool:
    """Whether normalized absolute ``path`` is ``parent`` or below it."""
    if not isinstance(path, str) or not isinstance(parent, str):
        return False
    if not path.startswith("/"):
        return False
    if not parent.startswith("/"):
        return False
    normalized_path = posixpath.normpath(path)
    normalized_parent = posixpath.normpath(parent)
    return (
        normalized_path == normalized_parent
        or normalized_parent == "/"
        or normalized_path.startswith(normalized_parent.rstrip("/") + "/")
    )

#: §8.3: the leaf's name is ``rg-`` plus the lane's token.
LEAF_PREFIX = "rg-"
#: The only files the daemon may write inside a leaf (§8.3/D-25).
LEAF_FILES = ("cgroup.procs", "memory.high", "memory.max", "cpu.weight")
SUBTREE_CONTROL = "cgroup.subtree_control"
PROCS = "cgroup.procs"
#: RW-35(a): the controllers the gates slice must delegate, and the exact
#: value written when it does not (``+`` only — never ``-``).
REQUIRED_CONTROLLERS = ("memory", "cpu", "pids")
SUBTREE_CONTROL_VALUE = " ".join(f"+{name}" for name in REQUIRED_CONTROLLERS)

#: §8.8 refusal codes. Every one of them is a `placement.error` on a session
#: that STARTED — placement never fails `start` (§8.3).
REFUSED_NO_TOKEN = "place-refused:no-token"
REFUSED_NO_GATES_SLICE = "place-refused:no-gates-slice"
REFUSED_OVER_SLICE = "place-refused:over-slice"
REFUSED_PARENT_NOT_GATES_SLICE = "place-refused:parent-not-gates-slice"
REFUSED_NO_HOST_PROC = "place-refused:no-host-proc-view"
REFUSED_IDENTITY_UNAVAILABLE = "place-refused:identity-unavailable"
REFUSED_STATE_UNAVAILABLE = "place-refused:recovery-state-unavailable"

#: `stop`'s `rmdir` retry budget: 3 attempts over 3 s (§8.3).
RMDIR_ATTEMPTS = 3
RMDIR_RETRY_SECONDS = 1.0

#: §8.3's `--cpu-weight <1..10000>` (the kernel's own range for `cpu.weight`).
CPU_WEIGHT_MIN = 1
CPU_WEIGHT_MAX = 10000


def write_failed(relative_path: str) -> str:
    """§8.8's ``place-refused:write-failed:<file>``.

    ``<file>`` is the path RELATIVE TO THE CGROUP ROOT rather than a bare
    basename: three different files in a placement are called
    ``cgroup.procs`` (the leaf's, the origin scope's, and — for a reader
    chasing the error — any ancestor's), so a basename alone would not tell
    an operator which write the kernel refused.
    """
    return f"place-refused:write-failed:{relative_path}"


class HostWriteError(RuntimeError):
    """A cgroup write outside the D-25 whitelist — see the module docstring.

    Deliberately NOT caught by the placement paths below: a refusal here
    means this module tried to write somewhere it must never write, which is
    a defect in this file, not a host condition to be reported as
    ``place-refused``. It propagates to the session thread's own error path.
    """


class CapsError(ValueError):
    """An unparsable ``--memory-high``/``--memory-max``/``--cpu-weight``.

    The server turns this into the contract's `bad-argument` (exit 2, no
    session), exactly the way :class:`lib.liveness.PolicyError` becomes
    `bad-policy`: a value the CLIENT typed wrong is a request error, while
    `place-refused:*` is reserved for what the HOST turned out to be (no
    gates slice, a cap over the slice's own ceiling, a write the kernel
    rejected) — those never fail `start`.
    """


def bounded_slice_capacity(cgroup_root: str, gates_cgroup: str) -> bool:
    """Require finite, positive memory and CPU ceilings on the gates slice."""
    gates_abs = abs_path(cgroup_root, gates_cgroup)
    memory_max = util.read_int(os.path.join(gates_abs, "memory.max"))
    if memory_max is None or memory_max <= 0:
        return False
    try:
        cpu_max = util.read_text(os.path.join(gates_abs, "cpu.max"))
        if not isinstance(cpu_max, str):
            return False
        # Unpacking enforces the two-field shape and int() rejects the
        # kernel's unbounded "max" quota token. Keep one parser as the
        # authority for both conditions rather than a redundant precheck.
        quota, period = map(int, cpu_max.split())
    except (OSError, ValueError):
        return False
    return quota > 0 and period > 0


_CONTAINER_SCOPE_PREFIXES = ("docker", "crio", "containerd", "libpod")


def container_cgroup_matches_id(cgroup: str, container_id: str) -> bool:
    """Recognize only the two supported runtime cgroup leaf spellings."""
    if not util.is_container_id(container_id):
        return False
    if not isinstance(cgroup, str) or not cgroup.startswith("/"):
        return False
    parts = cgroup.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts[1:]):
        return False
    name = parts[-1]
    return name == container_id or any(
        name == f"{prefix}-{container_id}.scope"
        for prefix in _CONTAINER_SCOPE_PREFIXES
    )


@dataclass(frozen=True)
class PlacementRequest:
    """The caps §8.3's four options ask for. ``None`` = not requested."""

    memory_high: Optional[int] = None
    memory_max: Optional[int] = None
    cpu_weight: Optional[int] = None

    def cap_files(self) -> List[str]:
        """The leaf files this request writes, in a stable order."""
        files = []
        if self.memory_high is not None:
            files.append("memory.high")
        if self.memory_max is not None:
            files.append("memory.max")
        if self.cpu_weight is not None:
            files.append("cpu.weight")
        return files

    def value_for(self, name: str) -> int:
        return {
            "memory.high": self.memory_high,
            "memory.max": self.memory_max,
            "cpu.weight": self.cpu_weight,
        }[name]


def _parse_bytes(raw: Any, *, option: str) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise CapsError(f"{option} must be a number of bytes, got {raw!r}")
    if isinstance(raw, float) and (not math.isfinite(raw) or not raw.is_integer()):
        raise CapsError(f"{option} must be a finite whole number of bytes, got {raw!r}")
    value = int(raw)
    if value < 0:
        raise CapsError(f"{option} must be >= 0, got {value}")
    return value


def parse_request(args: Dict[str, Any]) -> Optional[PlacementRequest]:
    """§8.3's four `start` options → a request, or ``None`` for "not asked".

    "All optional, all ignored without ``--place``" is literal: the caps are
    not even validated when ``place`` is false, because a consumer that sends
    a cap it knows the daemon will ignore has made no error.
    """
    place = args.get("place")
    if place is None or place is False:
        return None
    if place is not True:
        raise CapsError(f"--place must be a boolean, got {place!r}")
    weight = args.get("cpu_weight")
    if weight is not None:
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise CapsError(f"--cpu-weight must be a number, got {weight!r}")
        if isinstance(weight, float):
            if not math.isfinite(weight) or not weight.is_integer():
                raise CapsError(
                    f"--cpu-weight must be a finite whole number, got {weight!r}"
                )
        weight = int(weight)
        if not CPU_WEIGHT_MIN <= weight <= CPU_WEIGHT_MAX:
            raise CapsError(
                f"--cpu-weight must be in [{CPU_WEIGHT_MIN}, {CPU_WEIGHT_MAX}], got {weight}"
            )
    return PlacementRequest(
        memory_high=_parse_bytes(args.get("memory_high"), option="--memory-high"),
        memory_max=_parse_bytes(args.get("memory_max"), option="--memory-max"),
        cpu_weight=weight,
    )


class CgroupWriteGuard:
    """The D-15/D-25 cgroup write whitelist — see the module docstring.

    Bound to one session: the gates slice it may delegate controllers on and
    the origin scope it may move pids back to. Every path is compared by
    ``os.path.realpath`` so a symlink planted under the gates slice cannot
    smuggle a write out of it.
    """

    def __init__(
        self, *, cgroup_root: str, gates_cgroup: str, origin_cgroup: Optional[str],
        leaf_name: Optional[str], scope_cgroup: Optional[str] = None,
        container_cgroup: Optional[str] = None,
        container_id: Optional[str] = None,
    ) -> None:
        self.cgroup_root = cgroup_root
        self.gates_cgroup = gates_cgroup
        self.gates_abs = abs_path(cgroup_root, gates_cgroup)
        self.origin_abs = abs_path(cgroup_root, origin_cgroup) if origin_cgroup else None
        self.origin_abss: Set[str] = (
            {os.path.realpath(self.origin_abs)} if self.origin_abs is not None else set()
        )
        self.scope_cgroup = scope_cgroup
        self.scope_abs = abs_path(cgroup_root, scope_cgroup) if scope_cgroup else None
        self.leaf_name = leaf_name
        self.container_kill_abs: Optional[str] = None
        if container_cgroup is None or container_id is None:
            return
        gates_path = posixpath.normpath(gates_cgroup)
        target_path = posixpath.normpath(container_cgroup)
        if not gates_path.startswith("/"):
            return
        if not target_path.startswith(gates_path.rstrip("/") + "/"):
            return
        if target_path != container_cgroup:
            return
        if not container_cgroup_matches_id(container_cgroup, container_id):
            return
        target_abs = abs_path(cgroup_root, target_path)
        target_real = os.path.realpath(target_abs)
        if (
            util.realpath_is_within(target_abs, self.gates_abs, allow_root=False)
            and container_cgroup_matches_id(target_real, container_id)
        ):
            self.container_kill_abs = target_real

    # -- predicates --------------------------------------------------------

    def is_leaf(self, path: str) -> bool:
        """Whether ``path`` is this session's leaf under its delegated scope."""
        if self.leaf_name is None or self.scope_abs is None:
            return False
        real = os.path.realpath(path)
        return real == os.path.join(os.path.realpath(self.scope_abs), self.leaf_name)

    def allow_origin(self, cgroup: str) -> None:
        """Admit one identity-verified original cgroup for exact restoration."""
        self.origin_abss.add(os.path.realpath(abs_path(self.cgroup_root, cgroup)))

    # -- the three checks --------------------------------------------------

    def check_write(self, path: str, value: str) -> None:
        real = os.path.realpath(path)
        parent, name = os.path.split(real)
        if (
            self.scope_abs is not None
            and parent == os.path.realpath(self.scope_abs)
            and name == SUBTREE_CONTROL
        ):
            self._check_subtree_control_value(path, value)
            return
        if name == "cgroup.kill":
            if value != "1":
                raise HostWriteError(
                    f"refusing a non-'1' cgroup.kill value {value!r}: {path!r}"
                )
            if (
                self.container_kill_abs is not None
                and parent == self.container_kill_abs
            ):
                return
            if self.is_leaf(parent):
                return
        if name in LEAF_FILES and self.is_leaf(parent):
            return
        if name == PROCS and parent in self.origin_abss:
            return
        raise HostWriteError(
            f"refusing to write outside the D-25 cgroup whitelist "
            f"(gates slice {self.gates_cgroup!r}): {path!r}"
        )

    def check_mkdir(self, path: str) -> None:
        if not self.is_leaf(path):
            raise HostWriteError(
                f"refusing to create a cgroup that is not this session's "
                f"{LEAF_PREFIX}* child of a delegated scope: {path!r}"
            )

    def check_rmdir(self, path: str) -> None:
        if not self.is_leaf(path):
            raise HostWriteError(
                f"refusing to remove a cgroup that is not this session's "
                f"{LEAF_PREFIX}* child of a delegated scope: {path!r}"
            )

    @staticmethod
    def _check_subtree_control_value(path: str, value: str) -> None:
        """Allow only enabling required controllers on this exact scope root."""
        tokens = value.split()
        if not tokens:
            raise HostWriteError(f"refusing an empty {SUBTREE_CONTROL} write: {path!r}")
        for token in tokens:
            if not token.startswith("+"):
                raise HostWriteError(
                    f"refusing a non-'+' {SUBTREE_CONTROL} value {token!r}: {path!r}"
                )
            if token[1:] not in REQUIRED_CONTROLLERS:
                raise HostWriteError(
                    f"refusing to delegate controller {token[1:]!r} (RW-35a allows only "
                    f"{list(REQUIRED_CONTROLLERS)}): {path!r}"
                )


class TargetContainerKill:
    """One-shot kill authority for the exact container cgroup under gates.

    This writes only that target's ``cgroup.kill``. It never moves the target
    out of Docker's cgroup, so the container's existing limits remain intact.
    The caller separately verifies that ``gates_cgroup`` is an authored,
    loaded, bounded systemd slice before accepting a successful write.
    """

    def __init__(
        self, *, cgroup_root: str, gates_cgroup: str, container_cgroup: str,
        container_id: str, slice_unit_verifier: Optional[Callable[[str, str], bool]] = None,
        on_write: Optional[Callable[[str, str], None]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cgroup_root = cgroup_root
        self.gates_cgroup = gates_cgroup
        self.container_cgroup = container_cgroup
        self.container_id = container_id
        self._slice_unit_verifier = slice_unit_verifier or access.verify_systemd_slice
        self._on_write = on_write
        self._log = log
        self.guard = CgroupWriteGuard(
            cgroup_root=cgroup_root, gates_cgroup=gates_cgroup,
            origin_cgroup=None, leaf_name=None,
            container_cgroup=container_cgroup, container_id=container_id,
        )
        self.refusal_reason: Optional[str] = None

    @property
    def target_abs(self) -> Optional[str]:
        return self.guard.container_kill_abs

    @property
    def gates_unit(self) -> str:
        return os.path.basename(self.gates_cgroup.rstrip("/"))

    def kill(self) -> bool:
        target = self.target_abs
        if target is None:
            self.refusal_reason = "target-not-an-exact-gates-container"
            return False
        try:
            gates_verified = self._slice_unit_verifier(self.gates_unit, self.gates_cgroup)
            gates_bounded = bounded_slice_capacity(self.cgroup_root, self.gates_cgroup)
        except Exception as exc:  # fail closed on an unavailable host fact
            gates_verified = gates_bounded = False
            if self._log is not None:
                self._log(f"placement: gates-slice proof failed for container kill: {exc}")
        if not gates_verified or not gates_bounded:
            self.refusal_reason = "gates-slice-unverified-or-unbounded"
            return False
        if not os.path.isdir(target):
            self.refusal_reason = "target-cgroup-missing"
            return False
        if not _cgroup_is_populated(target):
            self.refusal_reason = "target-cgroup-empty-or-unreadable"
            return False

        kill_path = os.path.join(target, "cgroup.kill")
        try:
            self.guard.check_write(kill_path, "1")
            with open(kill_path, "w", encoding="utf-8") as stream:
                stream.write("1")
        except (HostWriteError, OSError) as exc:
            self.refusal_reason = "target-cgroup-kill-write-failed"
            if self._log is not None:
                self._log(f"placement: exact container cgroup.kill refused: {exc}")
            return False
        if self._on_write is not None:
            relative = os.path.relpath(kill_path, self.cgroup_root)
            try:
                self._on_write(relative, "1")
            except Exception as exc:  # enforcement already succeeded; preserve that fact
                if self._log is not None:
                    self._log(
                        "placement: exact container cgroup.kill succeeded but its "
                        f"event row could not be recorded: {exc}"
                    )
        return True

def abs_path(cgroup_root: str, cgroup: str) -> str:
    return os.path.join(cgroup_root, cgroup.lstrip("/"))


class LanePlacement:
    """One lane's leaf: create, cap, migrate, read, kill, release.

    Constructed once per placed session and mutated only by the server
    (``start`` on the caller's thread, later migrations and leaf readings on
    the session's own sampler thread, ``release`` under the registry lock at
    ``stop``) — the server serializes those the same way it serializes the
    liveness tracker.
    """

    def __init__(
        self,
        *,
        cgroup_root: str,
        gates_cgroup: str,
        token: Optional[str],
        origin_cgroup: str,
        request: PlacementRequest,
        on_write: Optional[Callable[[str, str], None]] = None,
        log: Optional[Callable[[str], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
        rmdir: Callable[[str], None] = os.rmdir,
        mkdir: Callable[[str], None] = os.mkdir,
        proc_root: Optional[str] = None,
        systemd_attach: Optional[Callable[[str, str, int], bool]] = None,
        pid_cgroup: Optional[Callable[[int], Optional[str]]] = None,
        pid_exists: Optional[Callable[[int], bool]] = None,
        pid_start_time: Optional[Callable[[int], Optional[str]]] = None,
        pid_parent: Optional[Callable[[int], Optional[int]]] = None,
        host_pid: Optional[Callable[[], Optional[int]]] = None,
        host_proc_view: Optional[Callable[[str], bool]] = None,
        pids_in_cgroup: Optional[Callable[[str, str, str], List[int]]] = None,
        unit_cgroup_verifier: Optional[Callable[[str, str], bool]] = None,
        state_write: Optional[Callable[[Dict[str, Any]], None]] = None,
        scope_create: Optional[Callable[[str, str, Sequence[int], Sequence[str]], Optional[str]]] = None,
        scope_stop: Optional[Callable[[str], bool]] = None,
        slice_unit_verifier: Optional[Callable[[str, str], bool]] = None,
        unit_absence_verifier: Optional[Callable[[str], Optional[bool]]] = None,
    ) -> None:
        self.cgroup_root = cgroup_root
        self.gates_cgroup = gates_cgroup
        self.token = token
        self.origin_cgroup = origin_cgroup
        self.request = request
        self._on_write = on_write
        self._log = log
        self._sleep = sleep
        self.proc_root = proc_root or os.environ.get("CGPROFILE_PROC_ROOT", "/proc")
        self._systemd_attach = systemd_attach or _systemd_attach_process
        self._pid_cgroup = pid_cgroup or self._default_pid_cgroup
        self._pid_exists = pid_exists or self._default_pid_exists
        self._pid_start_time = pid_start_time or (
            lambda pid: _process_start_time_ticks(self.proc_root, pid)
        )
        self._pid_parent = pid_parent or (
            lambda pid: _process_parent_pid(self.proc_root, pid)
        )
        self._host_pid = host_pid or (lambda: _host_pid_of_self(self.proc_root))
        self._host_proc_view = host_proc_view or access.have_host_proc_view
        self._pids_in_cgroup = pids_in_cgroup or self._default_pids_in_cgroup
        self._unit_cgroup_verifier = unit_cgroup_verifier or _systemd_unit_cgroup_matches
        self._state_write = state_write
        self._scope_create = scope_create or _systemd_create_scope
        self._scope_stop = scope_stop or _systemd_stop_unit
        self._slice_unit_verifier = slice_unit_verifier or access.verify_systemd_slice
        self._unit_absence_verifier = unit_absence_verifier or _systemd_unit_is_absent
        # A cgroup directory is kernfs: `rmdir` on it succeeds even though it
        # "contains" the controller's interface files, which the kernel
        # created and no process may unlink. A fake cgroupfs in a tmp
        # directory is a real filesystem, where the same `os.rmdir` is
        # ENOTEMPTY — so removal is the ONE primitive here that behaves
        # differently on the test seam every other reader in this package
        # already takes, and it is injectable for exactly that reason. The
        # guard still runs on the real path either way (`_rmdir` checks
        # before it calls this).
        self._rmdir_fn = rmdir
        self._mkdir_fn = mkdir
        self.guard = CgroupWriteGuard(
            cgroup_root=cgroup_root, gates_cgroup=gates_cgroup,
            origin_cgroup=origin_cgroup, leaf_name=self.leaf_name,
        )
        self.scope_unit: Optional[str] = None
        self.scope_cgroup: Optional[str] = None
        self.leaf_cgroup: Optional[str] = None
        self.leaf_created = False
        self.successfully_placed = False
        self.applied: Dict[str, Optional[int]] = {}
        self.error: Optional[str] = None
        self.moved: Set[int] = set()
        self.pid_records: Dict[int, Dict[str, Any]] = {}
        self._journal: Optional[Dict[str, Any]] = None
        self.released = False

    def _default_pid_cgroup(self, pid: int) -> Optional[str]:
        # Imported lazily: targets imports the access layer, while placement
        # is itself wired into serve's target-resolution path.
        from . import targets

        return targets.cgroup_of_pid(pid, self.cgroup_root, self.proc_root)

    def _default_pid_exists(self, pid: int) -> bool:
        return os.path.exists(os.path.join(self.proc_root, str(pid)))

    def _default_pids_in_cgroup(self, cgroup: str, root: str, proc_root: str) -> List[int]:
        from . import targets

        return targets.pids_in_cgroup(cgroup, root, proc_root)

    def _persist(self, *, state: Optional[str] = None) -> bool:
        if self._journal is None or self._state_write is None:
            return True
        if state is not None:
            self._journal["state"] = state
        self._journal["pids"] = {
            str(pid): record for pid, record in sorted(self.pid_records.items())
        }
        self._journal["leaf_created"] = self.leaf_created
        self._journal["was_placed"] = self.successfully_placed
        try:
            self._state_write(self._journal)
        except Exception as exc:  # noqa: BLE001 - persistence is a safety boundary
            if self._log is not None:
                self._log(f"placement: could not persist recovery journal: {exc}")
            return False
        return True

    def _ancestor_record(self, pid: int) -> Optional[Dict[str, Any]]:
        """Find the nearest still-identical journaled ancestor of ``pid``."""
        parent = self._pid_parent(pid)
        visited: Set[int] = {pid}
        for _ in range(4096):
            if parent is None:
                return None
            if parent <= 0 or parent in visited:
                return None
            visited.add(parent)
            record = self.pid_records.get(parent)
            if record is not None and self._same_process(parent, record):
                return record
            parent = self._pid_parent(parent)
        return None

    def _capture_pid(self, pid: int) -> Optional[Dict[str, Any]]:
        """Capture stable process identity and a safe restoration destination.

        A process still in the selected origin tree gets its own exact
        original cgroup/unit recorded. A child already born inside our
        systemd scope/leaf inherits that destination from its nearest
        identity-verified journaled ancestor. Anything else is ambiguous and
        is refused rather than moved on the strength of a numeric PID alone.
        """
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            self.error = REFUSED_IDENTITY_UNAVAILABLE
            return None
        if not self._host_proc_view(self.proc_root):
            self.error = REFUSED_NO_HOST_PROC
            return None
        daemon_pid = self._host_pid()
        if daemon_pid is None:
            self.error = REFUSED_IDENTITY_UNAVAILABLE
            return None
        if pid == daemon_pid:
            self.error = REFUSED_IDENTITY_UNAVAILABLE
            return None
        if not self._pid_exists(pid):
            return None
        start_time = self._pid_start_time(pid)
        cgroup = self._pid_cgroup(pid)
        if start_time is None or cgroup is None:
            self.error = REFUSED_IDENTITY_UNAVAILABLE
            return None
        normalized_cgroup = posixpath.normpath(cgroup)
        if _within_cgroup(normalized_cgroup, self.origin_cgroup):
            destination = _systemd_destination(normalized_cgroup)
            if destination is None:
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                return None
            unit_name, subcgroup = destination
            parts = normalized_cgroup.strip("/").split("/")
            suffix_parts = subcgroup.split("/") if subcgroup else []
            unit_parts = parts[: len(parts) - len(suffix_parts)] if suffix_parts else parts
            unit_cgroup = "/" + "/".join(unit_parts)
            if not self._unit_cgroup_verifier(unit_name, unit_cgroup):
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                return None
            origin = {
                "origin_cgroup": normalized_cgroup,
                "origin_unit": unit_name,
                "origin_unit_cgroup": unit_cgroup,
                "origin_subcgroup": subcgroup,
            }
        elif (
            self.scope_cgroup is not None
            and _within_cgroup(normalized_cgroup, self.scope_cgroup)
        ):
            parent = self._ancestor_record(pid)
            if parent is None:
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                return None
            origin = {
                key: parent[key]
                for key in (
                    "origin_cgroup", "origin_unit", "origin_unit_cgroup",
                    "origin_subcgroup",
                )
            }
        else:
            self.error = REFUSED_IDENTITY_UNAVAILABLE
            return None
        # Re-read both identity and membership after resolving the owning unit;
        # a recycled PID or a process that escaped during inspection is not
        # admitted to the transaction.
        if (
            not self._pid_exists(pid)
            or self._pid_start_time(pid) != start_time
            or posixpath.normpath(self._pid_cgroup(pid) or "") != normalized_cgroup
        ):
            if not self._pid_exists(pid):
                return None
            self.error = REFUSED_IDENTITY_UNAVAILABLE
            return None
        self.guard.allow_origin(origin["origin_cgroup"])
        return {
            "pid": pid,
            "start_time_ticks": start_time,
            "current_cgroup": normalized_cgroup,
            **origin,
            "state": "prepared",
        }

    @property
    def gates_unit(self) -> str:
        """The systemd unit owning ``gates_cgroup`` (e.g. dev-gates.slice)."""
        return os.path.basename(self.gates_cgroup.rstrip("/"))

    @property
    def scope_name(self) -> str:
        """A collision-resistant per-session transient scope name."""
        return f"rg-profile-{self.token}.scope"

    # -- geometry ---------------------------------------------------------

    @property
    def leaf_name(self) -> str:
        return f"{LEAF_PREFIX}{self.token}"

    @property
    def leaf_abs(self) -> Optional[str]:
        if self.leaf_cgroup is None or not self.leaf_created:
            return None
        return abs_path(self.cgroup_root, self.leaf_cgroup)

    @property
    def placed(self) -> bool:
        """Whether a leaf exists and holds this lane (§8.4's `throttled` and
        the `cgroup.kill` enforcement path both key on this)."""
        return self.leaf_cgroup is not None and self.leaf_created and not self.released

    def _relative(self, abs_target: str) -> str:
        return os.path.relpath(abs_target, self.cgroup_root)

    def _set_scope(self, cgroup: str) -> None:
        self.scope_cgroup = cgroup
        self.guard.scope_cgroup = cgroup
        self.guard.scope_abs = abs_path(self.cgroup_root, cgroup)

    # -- guarded primitives ------------------------------------------------

    def _write(self, abs_target: str, value: str) -> None:
        """The ONLY way this module touches a cgroup file. Guard first, then
        the write, then D-25's `events.jsonl` row."""
        self.guard.check_write(abs_target, value)
        with open(abs_target, "w", encoding="utf-8") as fh:
            fh.write(value)
        self._record_write(abs_target, value)

    def _record_write(self, abs_target: str, value: str) -> None:
        """Record a successful cgroup mutation, including a systemd-mediated one."""
        if self._on_write is not None:
            self._on_write(self._relative(abs_target), value)

    def _mkdir(self, abs_target: str) -> None:
        self.guard.check_mkdir(abs_target)
        self._mkdir_fn(abs_target)
        if self._on_write is not None:
            self._on_write(self._relative(abs_target), "mkdir")

    def _rmdir(self, abs_target: str) -> None:
        self.guard.check_rmdir(abs_target)
        self._rmdir_fn(abs_target)
        if self._on_write is not None:
            self._on_write(self._relative(abs_target), "rmdir")

    # -- §8.3: apply ------------------------------------------------------

    def apply(self, pids: Sequence[int]) -> None:
        """Journal identities, create the delegated scope, then place/cap it.

        A refusal is recorded on the session and never aborts profiling. Once
        systemd may have moved a PID, the journal and exact unit identity stay
        available for ordinary cleanup or restart recovery.
        """
        if not self.token:
            self.error = REFUSED_NO_TOKEN
            return
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,64}", self.token):
            self.error = REFUSED_NO_TOKEN
            return
        gates_abs = abs_path(self.cgroup_root, self.gates_cgroup)
        if (
            not os.path.isdir(gates_abs)
            or not self._slice_unit_verifier(self.gates_unit, self.gates_cgroup)
            or not bounded_slice_capacity(self.cgroup_root, self.gates_cgroup)
        ):
            self.error = REFUSED_NO_GATES_SLICE
            return
        slice_max = util.read_int(os.path.join(gates_abs, "memory.max"))
        if (
            self.request.memory_max is not None
            and slice_max is not None
            and self.request.memory_max > slice_max
        ):
            self.error = REFUSED_OVER_SLICE
            return
        if not self._host_proc_view(self.proc_root):
            self.error = REFUSED_NO_HOST_PROC
            return
        if self._state_write is None:
            self.error = REFUSED_STATE_UNAVAILABLE
            return
        if not pids:
            self.error = "place-refused:no-target-pids"
            return

        for pid in pids:
            if pid in self.pid_records:
                continue
            identity = self._capture_pid(pid)
            if identity is not None:
                self.pid_records[pid] = identity
            elif self.error is not None:
                return
        if not self.pid_records:
            self.error = "place-refused:no-target-pids"
            return

        self._journal = {
            "schema": 1,
            "token": self.token,
            "state": "prepared",
            "scope_unit": self.scope_name,
            "gates_unit": self.gates_unit,
            "gates_cgroup": posixpath.normpath(self.gates_cgroup),
            "scope_cgroup": None,
            "leaf_cgroup": None,
            "pids": {},
        }
        if not self._persist(state="creating-scope"):
            self.error = REFUSED_STATE_UNAVAILABLE
            return
        self.scope_unit = self.scope_name

        try:
            scope_cgroup = self._scope_create(
                self.scope_name, self.gates_unit, tuple(sorted(self.pid_records)),
                REQUIRED_CONTROLLERS,
            )
        except Exception as exc:  # a host-side systemd failure is a refusal
            if self._log is not None:
                self._log(f"placement: systemd scope creation failed: {exc}")
            scope_cgroup = None
        if scope_cgroup is None:
            self.error = "place-refused:scope-unavailable"
            # Keep the write-ahead record: a timed-out manager call can have
            # created the exact transient unit even when its reply was lost.
            self._persist(state="scope-create-uncertain")
            return
        normalized_gates = posixpath.normpath(self.gates_cgroup)
        normalized_scope = posixpath.normpath(scope_cgroup)
        self.scope_cgroup = normalized_scope
        self._journal["scope_cgroup"] = normalized_scope
        if not self._persist(state="scope-created"):
            self.error = REFUSED_STATE_UNAVAILABLE
            return
        if not normalized_gates.startswith("/"):
            self.error = REFUSED_PARENT_NOT_GATES_SLICE
            self._persist(state="recovery-required")
            return
        if not normalized_scope.startswith("/"):
            self.error = REFUSED_PARENT_NOT_GATES_SLICE
            self._persist(state="recovery-required")
            return
        if posixpath.dirname(normalized_scope) != normalized_gates:
            self.error = REFUSED_PARENT_NOT_GATES_SLICE
            self._persist(state="recovery-required")
            return
        if posixpath.basename(normalized_scope) != self.scope_name:
            self.error = REFUSED_PARENT_NOT_GATES_SLICE
            self._persist(state="recovery-required")
            return
        self._set_scope(normalized_scope)
        scope_abs = abs_path(self.cgroup_root, normalized_scope)
        if not os.path.isdir(scope_abs):
            self.error = REFUSED_PARENT_NOT_GATES_SLICE
            self._persist(state="recovery-required")
            return
        if os.path.realpath(scope_abs) != os.path.abspath(scope_abs):
            self.error = REFUSED_PARENT_NOT_GATES_SLICE
            self._persist(state="recovery-required")
            return
        if not self._unit_cgroup_verifier(self.scope_name, normalized_scope):
            self.error = REFUSED_PARENT_NOT_GATES_SLICE
            self._persist(state="recovery-required")
            return

        # StartTransientUnit(PIDs=...) transfers each initial PID into this
        # exact scope. Verify identity and membership before the second move.
        for pid, record in list(self.pid_records.items()):
            if not self._same_process(pid, record):
                if not self._pid_exists(pid):
                    record["state"] = "exited"
                    continue
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                self._persist(state="recovery-required")
                return
            if posixpath.normpath(self._pid_cgroup(pid) or "") != normalized_scope:
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                self._persist(state="recovery-required")
                return
            record["state"] = "scope"
        if not self._persist(state="scope-verified"):
            self.error = REFUSED_STATE_UNAVAILABLE
            return

        leaf_abs = os.path.join(scope_abs, self.leaf_name)
        self.leaf_cgroup = os.path.join("/", self._relative(leaf_abs))
        self._journal["leaf_cgroup"] = self.leaf_cgroup
        if not self._persist(state="creating-leaf"):
            self.error = REFUSED_STATE_UNAVAILABLE
            return
        scope_procs = os.path.join(scope_abs, PROCS)
        if os.path.lexists(leaf_abs):
            self.error = (
                write_failed(self._relative(leaf_abs))
                if self.guard.is_leaf(leaf_abs)
                else REFUSED_PARENT_NOT_GATES_SLICE
            )
            self.leaf_cgroup = None
            self._restore_and_retire()
            return
        try:
            self._mkdir(leaf_abs)
        except OSError as exc:
            self.error = write_failed(self._failed_relative(exc, leaf_abs))
            self.leaf_cgroup = None
            self._restore_and_retire()
            return
        self.leaf_created = True
        if not self._persist(state="leaf-created"):
            self.error = REFUSED_STATE_UNAVAILABLE
            self.release()
            return

        self.migrate(tuple(self.pid_records))
        if self.error is None and self._cgroup_has_processes(normalized_scope) is not False:
            # Unknown is not empty: the no-internal-process precondition must
            # be positively established before controller delegation.
            self.error = write_failed(self._relative(scope_procs))
        if self.error is not None:
            self.release()
            return
        try:
            self._delegate_controllers(scope_abs)
        except OSError as exc:
            self.error = write_failed(self._failed_relative(exc, os.path.join(scope_abs, SUBTREE_CONTROL)))
            self.release()
            return

        if not self._persist(state="controllers-enabled"):
            self.error = REFUSED_STATE_UNAVAILABLE
            self.release()
            return

        for name in self.request.cap_files():
            target = os.path.join(leaf_abs, name)
            try:
                self._write(target, str(self.request.value_for(name)))
            except OSError as exc:
                self.error = write_failed(self._failed_relative(exc, target))
                self.release()
                return
            # §8.3: `applied` holds what the kernel reports AFTER the write,
            # never the number that was asked for — `memory.high` rounds to a
            # page multiple and `memory.max` can be refused silently by an
            # ancestor, so echoing the request would be a claim, not a
            # reading (the S13.3.2 discipline).
            applied = util.read_int(target)
            if applied is None:
                self.error = write_failed(self._relative(target))
                self.release()
                return
            self.applied[name] = applied
        if not self._verify_leaf_membership():
            self.release()
            return
        self.successfully_placed = True
        if not self._persist(state="placed"):
            self.successfully_placed = False
            self.error = REFUSED_STATE_UNAVAILABLE
            self.release()

    def _verify_leaf_membership(self) -> bool:
        """Require an exact, identity-verified snapshot before placement success."""
        if self.leaf_cgroup is None:
            self.error = REFUSED_IDENTITY_UNAVAILABLE
            return False
        members = self._owned_cgroup_pids(self.leaf_cgroup)
        if members is None:
            self.error = write_failed(
                self._relative(os.path.join(abs_path(self.cgroup_root, self.leaf_cgroup), PROCS))
            )
            return False
        actual = set(members)
        for pid in members:
            record = self.pid_records.get(pid)
            if record is None:
                record = self._capture_pid(pid)
                if record is None:
                    if self.error is None:
                        self.error = REFUSED_IDENTITY_UNAVAILABLE
                    return False
                self.pid_records[pid] = record
            if not self._same_process(pid, record):
                if not self._pid_exists(pid):
                    record["state"] = "exited"
                    continue
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                return False
            if posixpath.normpath(self._pid_cgroup(pid) or "") != posixpath.normpath(
                self.leaf_cgroup
            ):
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                return False
            record["state"] = "leaf"
        expected: Set[int] = set()
        for pid, record in self.pid_records.items():
            if not self._same_process(pid, record):
                if not self._pid_exists(pid):
                    record["state"] = "exited"
                    continue
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                return False
            current = self._pid_cgroup(pid)
            if current is None:
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                return False
            if posixpath.normpath(current) == posixpath.normpath(self.leaf_cgroup):
                expected.add(pid)
                record["state"] = "leaf"
            else:
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                return False
        if expected != actual:
            self.error = REFUSED_IDENTITY_UNAVAILABLE
            return False
        if not self._persist(state="placement-verified"):
            self.error = REFUSED_STATE_UNAVAILABLE
            return False
        return True

    def _same_process(self, pid: int, record: Dict[str, Any]) -> bool:
        return (
            self._pid_exists(pid)
            and self._pid_start_time(pid) == record.get("start_time_ticks")
        )

    def _cgroup_has_processes(self, cgroup: str) -> Optional[bool]:
        abs_cgroup = abs_path(self.cgroup_root, cgroup)
        raw = util.read_lines(os.path.join(abs_cgroup, PROCS))
        if raw is None:
            return None
        try:
            visible = self._pids_in_cgroup(cgroup, self.cgroup_root, self.proc_root)
        except Exception:  # noqa: BLE001 - unreadable membership is unknown
            return None
        entries = [line.strip() for line in raw if line.strip()]
        # A cgroupfs view can render tasks invisible to the writer's PID
        # namespace as zero. If the explicit proc resolver cannot account for
        # every visible entry, that is unknown, never proof of emptiness.
        if len(entries) != len(visible):
            return None
        return bool(visible)

    def _delegate_controllers(self, scope_abs: str) -> None:
        """Enable required controllers only after the delegated scope is empty."""
        control_path = os.path.join(scope_abs, SUBTREE_CONTROL)
        enabled = (util.read_text(control_path) or "").split()
        if all(name in enabled for name in REQUIRED_CONTROLLERS):
            return
        self._write(control_path, SUBTREE_CONTROL_VALUE)

    def _failed_relative(self, exc: OSError, fallback: str) -> str:
        target = exc.filename if isinstance(exc.filename, str) else fallback
        return self._relative(target)

    def _abandon(self, leaf_abs: str) -> None:
        """A cap write failed: the leaf is useless, so take it back out
        rather than leave an empty capless cgroup on the host forever."""
        self.leaf_cgroup = None
        self.applied = {}
        try:
            self._rmdir(leaf_abs)
        except OSError:
            pass  # reported through `error`, which is already set

    # -- §8.3: migration ---------------------------------------------------

    def migrate(self, pids: Sequence[int]) -> int:
        """Move newly discovered, identity-verified processes into the leaf."""
        leaf_abs = self.leaf_abs
        if leaf_abs is None or self.released or self.error is not None:
            return 0
        procs = os.path.join(leaf_abs, PROCS)
        moved = 0
        for pid in pids:
            record = self.pid_records.get(pid)
            if record is None:
                record = self._capture_pid(pid)
                if record is None:
                    if self.error is not None:
                        break
                    continue
                self.pid_records[pid] = record
                if not self._persist(state="pid-prepared"):
                    self.error = REFUSED_STATE_UNAVAILABLE
                    break
            if record.get("state") == "exited":
                continue
            if not self._same_process(pid, record):
                if not self._pid_exists(pid):
                    record["state"] = "exited"
                    if not self._persist(state="placing"):
                        self.error = REFUSED_STATE_UNAVAILABLE
                    continue
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                break
            current = posixpath.normpath(self._pid_cgroup(pid) or "")
            if current == posixpath.normpath(self.leaf_cgroup or ""):
                record["state"] = "leaf"
                self.moved.add(pid)
                continue
            if not (
                _within_cgroup(current, record["origin_cgroup"])
                or (
                    self.scope_cgroup is not None
                    and _within_cgroup(current, self.scope_cgroup)
                )
            ):
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                break
            if self.scope_unit is None or self.scope_cgroup is None:
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                break
            try:
                self.guard.check_write(procs, str(pid))
            except HostWriteError:
                self.error = REFUSED_PARENT_NOT_GATES_SLICE
                break
            record["state"] = "moving-to-leaf"
            if not self._persist(state="moving-to-leaf"):
                self.error = REFUSED_STATE_UNAVAILABLE
                break
            try:
                attached = self._systemd_attach(self.scope_unit, self.leaf_name, pid)
            except Exception as exc:  # noqa: BLE001 - manager failure is a refusal
                attached = False
                if self._log is not None:
                    self._log(f"placement: systemd attach of pid {pid} failed: {exc}")
            if not attached:
                if not self._pid_exists(pid):
                    record["state"] = "exited"
                    if not self._persist(state="placing"):
                        self.error = REFUSED_STATE_UNAVAILABLE
                    continue
                self.error = write_failed(self._relative(procs))
                self._persist(state="recovery-required")
                if self._log is not None:
                    self._log(f"placement: systemd could not attach pid {pid} to {self.leaf_cgroup}")
                break
            if not self._same_process(pid, record):
                if not self._pid_exists(pid):
                    record["state"] = "exited"
                    if not self._persist(state="placing"):
                        self.error = REFUSED_STATE_UNAVAILABLE
                    continue
                record["state"] = "identity-mismatch"
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                self._persist(state="recovery-required")
                break
            if posixpath.normpath(self._pid_cgroup(pid) or "") != posixpath.normpath(
                self.leaf_cgroup
            ):
                record["state"] = "membership-mismatch"
                self.error = REFUSED_IDENTITY_UNAVAILABLE
                self._persist(state="recovery-required")
                break
            self._record_write(procs, str(pid))
            record["state"] = "leaf"
            if not self._persist(state="placing"):
                self.error = REFUSED_STATE_UNAVAILABLE
                break
            self.moved.add(pid)
            moved += 1
        if self.error is not None:
            self._persist(state="recovery-required")
        return moved

    # -- §8.4: what the leaf says -----------------------------------------

    def leaf_readings(self) -> Dict[str, Any]:
        """The two §8.4 readings only a leaf can give: its own memory PSI and
        whether `memory.high` is actually applied there. Together they are
        what makes `throttled` reachable at all."""
        leaf_abs = self.leaf_abs
        if leaf_abs is None:
            return {"psi_full_avg10": None, "memory_high_applied": False}
        psi = util.read_pressure(os.path.join(leaf_abs, "memory.pressure")).get("full_avg10")
        return {
            "psi_full_avg10": psi,
            "memory_high_applied": util.read_int(os.path.join(leaf_abs, "memory.high")) is not None,
        }

    def kill(self) -> bool:
        """§8.4's `--on-stall kill` for a PLACED session: one write of ``1``
        to the leaf's ``cgroup.kill``, which the kernel applies to every pid
        in the leaf atomically — it cannot miss a pid that forked between the
        resolver's walk and the signal, which a pid loop can. An empty or
        unreadable leaf cannot certify that the lane was killed."""
        leaf_abs = self.leaf_abs
        if leaf_abs is None:  # pragma: no cover - callers check `placed`
            return False
        if self.error is not None:
            if self._log is not None:
                self._log(
                    f"placement: cgroup.kill on {self.leaf_cgroup} refused: "
                    f"placement is incomplete ({self.error})"
                )
            return False
        if not _read_pids(os.path.join(leaf_abs, PROCS)):
            if self._log is not None:
                self._log(f"placement: cgroup.kill on {self.leaf_cgroup} refused: no visible pids")
            return False
        try:
            self._write(os.path.join(leaf_abs, "cgroup.kill"), "1")
        except OSError as exc:
            if self._log is not None:
                self._log(f"placement: cgroup.kill on {self.leaf_cgroup} failed: {exc}")
            return False
        return True

    # -- §8.3: release -----------------------------------------------------

    def _owned_cgroup_pids(self, cgroup: Optional[str]) -> Optional[List[int]]:
        """Resolve every direct member of an owned cgroup, or return unknown."""
        if cgroup is None:
            return []
        abs_cgroup = abs_path(self.cgroup_root, cgroup)
        raw = util.read_lines(os.path.join(abs_cgroup, PROCS))
        if raw is None:
            return None
        entries = [line.strip() for line in raw if line.strip()]
        try:
            resolved = sorted(set(self._pids_in_cgroup(
                cgroup, self.cgroup_root, self.proc_root
            )))
        except Exception:  # noqa: BLE001 - membership is a safety proof
            return None
        if len(entries) != len(resolved):
            return None
        if any(pid <= 0 for pid in resolved):
            return None
        return resolved

    def _cleanup_failure(self, cgroup: Optional[str]) -> None:
        target = os.path.join(
            abs_path(self.cgroup_root, cgroup or self.scope_cgroup or self.gates_cgroup),
            PROCS,
        )
        # Preserve a specific safety refusal (for example a PID identity
        # mismatch) as the public diagnosis while still recording cleanup
        # failure and retaining the exact recovery subtree.
        if self.error is None:
            self.error = write_failed(self._relative(target))
        self._persist(state="recovery-required")
        self._log_unrestored_survivor(target)

    def _accept_retirement_during_restore(self) -> bool:
        """Systemd may remove the empty scope during the last PID move back."""
        if self.scope_cgroup is None or not self._scope_retired_after_restore(
            abs_path(self.cgroup_root, self.scope_cgroup)
        ):
            return False
        if not self._persist(state="restoring"):
            self.error = REFUSED_STATE_UNAVAILABLE
            return False
        return True

    def _restore_owned_processes(self) -> bool:
        """Restore only journaled, same-start-time processes in our exact tree."""
        if self.scope_cgroup is None or self.scope_unit is None:
            return False
        if not self._persist(state="restoring"):
            self.error = REFUSED_STATE_UNAVAILABLE
            return False
        sources = [self.leaf_cgroup if self.leaf_created else None, self.scope_cgroup]
        for attempt in range(RMDIR_ATTEMPTS):
            for source_cgroup in sources:
                if source_cgroup is None:
                    continue
                pids = self._owned_cgroup_pids(source_cgroup)
                if pids is None:
                    if self._accept_retirement_during_restore():
                        return True
                    self._cleanup_failure(source_cgroup)
                    return False
                for pid in pids:
                    record = self.pid_records.get(pid)
                    if record is None:
                        record = self._capture_pid(pid)
                        if record is None:
                            self._cleanup_failure(source_cgroup)
                            return False
                        self.pid_records[pid] = record
                    if not self._same_process(pid, record):
                        if not self._pid_exists(pid):
                            record["state"] = "exited"
                            if not self._persist(state="restoring"):
                                self.error = REFUSED_STATE_UNAVAILABLE
                                return False
                            continue
                        self.error = REFUSED_IDENTITY_UNAVAILABLE
                        self._cleanup_failure(source_cgroup)
                        return False
                    current = posixpath.normpath(self._pid_cgroup(pid) or "")
                    origin = posixpath.normpath(record["origin_cgroup"])
                    if current == origin:
                        record["state"] = "restored"
                        if not self._persist(state="restoring"):
                            self.error = REFUSED_STATE_UNAVAILABLE
                            return False
                        continue
                    if current != posixpath.normpath(source_cgroup):
                        self.error = REFUSED_IDENTITY_UNAVAILABLE
                        self._cleanup_failure(source_cgroup)
                        return False
                    destination = record.get("origin_unit"), record.get("origin_subcgroup")
                    if (
                        not isinstance(destination[0], str)
                        or not isinstance(destination[1], str)
                        or not self._unit_cgroup_verifier(
                            destination[0], record.get("origin_unit_cgroup", "")
                        )
                    ):
                        self.error = REFUSED_IDENTITY_UNAVAILABLE
                        self._cleanup_failure(source_cgroup)
                        return False
                    record["state"] = "restoring"
                    if not self._persist(state="restoring"):
                        self.error = REFUSED_STATE_UNAVAILABLE
                        return False
                    try:
                        attached = self._systemd_attach(
                            destination[0], destination[1], pid
                        )
                    except Exception as exc:  # noqa: BLE001
                        attached = False
                        if self._log is not None:
                            self._log(f"placement: systemd restore of pid {pid} failed: {exc}")
                    if not attached:
                        if not self._pid_exists(pid):
                            record["state"] = "exited"
                            if not self._persist(state="restoring"):
                                self.error = REFUSED_STATE_UNAVAILABLE
                                return False
                            continue
                        self._cleanup_failure(source_cgroup)
                        return False
                    if not self._same_process(pid, record):
                        if not self._pid_exists(pid):
                            record["state"] = "exited"
                            if not self._persist(state="restoring"):
                                self.error = REFUSED_STATE_UNAVAILABLE
                                return False
                            continue
                        self.error = REFUSED_IDENTITY_UNAVAILABLE
                        self._cleanup_failure(source_cgroup)
                        return False
                    if posixpath.normpath(self._pid_cgroup(pid) or "") != origin:
                        self._cleanup_failure(source_cgroup)
                        return False
                    record["state"] = "restored"
                    self._record_write(
                        os.path.join(abs_path(self.cgroup_root, origin), PROCS),
                        str(pid),
                    )
                    if not self._persist(state="restoring"):
                        self.error = REFUSED_STATE_UNAVAILABLE
                        return False
            remaining = [self._owned_cgroup_pids(path) for path in sources if path is not None]
            if any(pids is None for pids in remaining):
                if self._accept_retirement_during_restore():
                    return True
                self._cleanup_failure(self.scope_cgroup)
                return False
            if not any(remaining):
                return True
            if attempt < RMDIR_ATTEMPTS - 1:
                self._sleep(RMDIR_RETRY_SECONDS)
        self._cleanup_failure(self.scope_cgroup)
        return False

    def release(self) -> None:
        """Restore identity-proved members, remove the leaf, then retire scope."""
        if self.released or self.scope_cgroup is None or self.scope_unit is None:
            return
        if not self._restore_owned_processes():
            return
        if self.leaf_cgroup is not None and self.leaf_created:
            leaf_abs = abs_path(self.cgroup_root, self.leaf_cgroup)
            if os.path.lexists(leaf_abs) and not self.guard.is_leaf(leaf_abs):
                self._cleanup_failure(self.scope_cgroup)
                return
            if os.path.lexists(leaf_abs):
                last_error: Optional[OSError] = None
                for attempt in range(RMDIR_ATTEMPTS):
                    try:
                        self._rmdir(leaf_abs)
                        last_error = None
                        break
                    except OSError as exc:
                        last_error = exc
                        if attempt < RMDIR_ATTEMPTS - 1:
                            self._sleep(RMDIR_RETRY_SECONDS)
                if last_error is not None:
                    self.error = write_failed(self._failed_relative(last_error, leaf_abs))
                    self._persist(state="recovery-required")
                    if self._log is not None:
                        self._log(f"placement: could not remove leaf {self.leaf_cgroup}: {last_error}")
                    return
            self.leaf_created = False
            if not self._persist(state="leaf-removed"):
                self.error = REFUSED_STATE_UNAVAILABLE
                return
        scope_abs = abs_path(self.cgroup_root, self.scope_cgroup)
        scope_members = self._cgroup_has_processes(self.scope_cgroup)
        if scope_members is None and self._scope_retired_after_restore(scope_abs):
            if not self._persist(state="complete"):
                self.error = REFUSED_STATE_UNAVAILABLE
                return
            self.released = True
            return
        if scope_members is not False:
            self._cleanup_failure(self.scope_cgroup)
            return
        try:
            children = [
                name for name in os.listdir(scope_abs)
                if os.path.isdir(os.path.join(scope_abs, name))
            ]
        except OSError:
            children = ["<unreadable>"]
        if children or not self._unit_cgroup_verifier(self.scope_unit, self.scope_cgroup):
            self._cleanup_failure(self.scope_cgroup)
            return
        if not self._scope_stop(self.scope_unit):
            self._cleanup_failure(self.scope_cgroup)
            if self._log is not None:
                self._log(f"placement: systemd would not retire empty scope {self.scope_unit}")
            return
        if not self._persist(state="complete"):
            self.error = REFUSED_STATE_UNAVAILABLE
            return
        self.released = True

    def _scope_retired_after_restore(self, scope_abs: str) -> bool:
        """Accept systemd auto-retirement only with independent proof of cleanup."""
        # A lane leaf is always an exact child of this scope, so a missing
        # scope path also proves its leaf path cannot still exist beneath it.
        if os.path.lexists(scope_abs) or self._unit_absence_verifier(
            self.scope_unit or ""
        ) is not True:
            return False
        for pid, record in self.pid_records.items():
            if not self._pid_exists(pid):
                record["state"] = "exited"
                continue
            start_time = self._pid_start_time(pid)
            if start_time is None:
                return False
            if start_time != record.get("start_time_ticks"):
                record["state"] = "exited"
                continue
            current = self._pid_cgroup(pid)
            origin = record.get("origin_cgroup")
            if (
                not isinstance(origin, str)
                or current is None
                or posixpath.normpath(current) != posixpath.normpath(origin)
            ):
                return False
            record["state"] = "restored"
        return True

    def _restore_and_retire(self) -> None:
        """Best-effort cleanup after a failed placement, preserving its journal."""
        self.release()

    def _log_unrestored_survivor(self, leaf_procs: str) -> None:
        if self._log is not None:
            self._log(
                f"placement: could not safely restore all survivors from "
                f"{self._relative(leaf_procs)}; refusing to remove its leaf"
            )

    # -- §8.3: the block ---------------------------------------------------

    def block(self) -> Dict[str, Any]:
        """§8.3's `placement` object, as `start`/`status`/`stop`/every watch
        reading/the Summary carry it."""
        return {
            "requested": True,
            "leaf": (
                self.leaf_cgroup
                if self.leaf_created or self.successfully_placed else None
            ),
            "applied": dict(self.applied),
            "pids_moved": len(self.moved),
            "error": self.error,
        }


def recover_journal(
    journal: Any,
    *,
    cgroup_root: str,
    gates_cgroup: str,
    proc_root: str,
    state_write: Callable[[Dict[str, Any]], None],
    log: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Restore a previous daemon's placement only from its durable journal.

    Return ``None`` after verified cleanup, or a concise failure string while
    leaving all inconclusive cgroups and journal evidence intact. Paths and
    unit identities are checked against systemd before any process is moved.
    """
    if not isinstance(journal, dict) or journal.get("schema") != 1:
        return REFUSED_STATE_UNAVAILABLE
    token = journal.get("token")
    gates_unit = os.path.basename(gates_cgroup.rstrip("/"))
    expected_scope = f"rg-profile-{token}.scope" if isinstance(token, str) else None
    persisted_scope = journal.get("scope_cgroup")
    persisted_leaf = journal.get("leaf_cgroup")
    if (
        not isinstance(token, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{8,64}", token)
        or journal.get("scope_unit") != expected_scope
        or journal.get("gates_unit") != gates_unit
        or not isinstance(journal.get("gates_cgroup"), str)
        or posixpath.normpath(journal.get("gates_cgroup", ""))
        != posixpath.normpath(gates_cgroup)
        or (persisted_scope is not None and not isinstance(persisted_scope, str))
        or (persisted_leaf is not None and not isinstance(persisted_leaf, str))
    ):
        return REFUSED_STATE_UNAVAILABLE
    normalized_gates = posixpath.normpath(gates_cgroup)
    if persisted_scope is not None and (
        not persisted_scope.startswith("/")
        or posixpath.normpath(persisted_scope) != persisted_scope
        or posixpath.dirname(persisted_scope) != normalized_gates
        or posixpath.basename(persisted_scope) != expected_scope
    ):
        return REFUSED_STATE_UNAVAILABLE
    if persisted_leaf is not None and (
        persisted_scope is None
        or persisted_leaf
        != posixpath.join(persisted_scope, f"{LEAF_PREFIX}{token}")
    ):
        return REFUSED_STATE_UNAVAILABLE
    raw_records = journal.get("pids")
    if not isinstance(raw_records, dict) or not raw_records:
        return REFUSED_STATE_UNAVAILABLE
    records: Dict[int, Dict[str, Any]] = {}
    for key, value in raw_records.items():
        if not isinstance(key, str) or not key.isdigit() or not isinstance(value, dict):
            return REFUSED_STATE_UNAVAILABLE
        pid = int(key)
        if value.get("pid") != pid:
            return REFUSED_STATE_UNAVAILABLE
        origin = value.get("origin_cgroup")
        unit = value.get("origin_unit")
        unit_cgroup = value.get("origin_unit_cgroup")
        subcgroup = value.get("origin_subcgroup")
        start_time = value.get("start_time_ticks")
        if (
            not isinstance(origin, str) or not origin.startswith("/")
            or not isinstance(unit, str) or not isinstance(unit_cgroup, str)
            or not isinstance(subcgroup, str)
            or not isinstance(start_time, str) or not start_time.isdigit()
            or _systemd_destination(origin) != (unit, subcgroup)
        ):
            return REFUSED_STATE_UNAVAILABLE
        records[pid] = dict(value)

    if not access.have_host_proc_view(proc_root):
        return REFUSED_NO_HOST_PROC
    scope_unit = expected_scope
    assert scope_unit is not None
    unit_path = _systemd_unit_path(scope_unit)
    if unit_path is None:
        # The crash may have happened after journaling but before systemd
        # accepted StartTransientUnit. That is clean only if no owned scope
        # directory remains and every surviving PID is still at its recorded
        # origin. A failed GetUnit is not proof of absence: require the exact
        # manager-authored NoSuchUnit response and check both owned paths.
        if _systemd_unit_is_absent(scope_unit) is not True:
            return REFUSED_STATE_UNAVAILABLE
        if isinstance(persisted_scope, str) and os.path.lexists(
            abs_path(cgroup_root, persisted_scope)
        ):
            return REFUSED_STATE_UNAVAILABLE
        if isinstance(persisted_leaf, str) and os.path.lexists(
            abs_path(cgroup_root, persisted_leaf)
        ):
            return REFUSED_STATE_UNAVAILABLE
        from . import targets as target_mod

        for pid, record in records.items():
            if not os.path.exists(os.path.join(proc_root, str(pid))):
                record["state"] = "exited"
                continue
            start_time = _process_start_time_ticks(proc_root, pid)
            if start_time is None:
                return REFUSED_IDENTITY_UNAVAILABLE
            if start_time != record["start_time_ticks"]:
                # The original identity is gone; never inspect or move the
                # unrelated process that reused its numeric PID.
                record["state"] = "exited"
                continue
            current = target_mod.cgroup_of_pid(pid, cgroup_root, proc_root)
            if current is None or posixpath.normpath(current) != posixpath.normpath(
                record["origin_cgroup"]
            ):
                return REFUSED_IDENTITY_UNAVAILABLE
            record["state"] = "restored"
        journal["state"] = "complete"
        journal["pids"] = {str(pid): record for pid, record in sorted(records.items())}
        try:
            state_write(journal)
        except Exception:  # noqa: BLE001 - durable recovery state is required
            return REFUSED_STATE_UNAVAILABLE
        return None

    load_state = _systemd_property(unit_path, "org.freedesktop.systemd1.Unit", "LoadState")
    actual_scope = _systemd_property(
        unit_path, "org.freedesktop.systemd1.Scope", "ControlGroup"
    )
    actual_slice = _systemd_property(
        unit_path, "org.freedesktop.systemd1.Scope", "Slice"
    )
    delegated = _systemd_bool_property(
        unit_path, "org.freedesktop.systemd1.Scope", "Delegate"
    )
    controllers = _systemd_string_array_property(
        unit_path, "org.freedesktop.systemd1.Scope", "DelegateControllers"
    )
    if (
        load_state != "loaded" or not isinstance(actual_scope, str)
        or not actual_scope.startswith("/") or actual_slice != gates_unit
        or delegated is not True or controllers is None
        or not set(REQUIRED_CONTROLLERS).issubset(controllers)
        or posixpath.dirname(posixpath.normpath(actual_scope))
        != posixpath.normpath(gates_cgroup)
        or posixpath.basename(posixpath.normpath(actual_scope)) != scope_unit
        or (
            persisted_scope is not None
            and posixpath.normpath(persisted_scope) != posixpath.normpath(actual_scope)
        )
    ):
        return REFUSED_STATE_UNAVAILABLE
    if not _systemd_unit_cgroup_matches(scope_unit, actual_scope):
        return REFUSED_STATE_UNAVAILABLE

    scope_abs = abs_path(cgroup_root, actual_scope)
    if not os.path.isdir(scope_abs):
        return REFUSED_STATE_UNAVAILABLE
    first_origin = next(iter(records.values()))["origin_cgroup"]
    recovery = LanePlacement(
        cgroup_root=cgroup_root,
        gates_cgroup=gates_cgroup,
        token=token,
        origin_cgroup=first_origin,
        request=PlacementRequest(),
        proc_root=proc_root,
        state_write=state_write,
        log=log,
    )
    recovery.scope_unit = scope_unit
    recovery.scope_cgroup = posixpath.normpath(actual_scope)
    recovery._set_scope(recovery.scope_cgroup)
    recovery.pid_records = records
    recovery._journal = journal
    recovery.successfully_placed = journal.get("was_placed") is True
    expected_leaf = posixpath.join(recovery.scope_cgroup, recovery.leaf_name)
    # `persisted_leaf`, when present, was already required above to equal
    # `persisted_scope/<token>`. The verified systemd scope is also required
    # to normalize to that exact persisted scope, so this is the only leaf
    # path recovery may consider; rechecking the same implication here would
    # add an unreachable refusal branch.
    leaf_created = journal.get("leaf_created") is True
    expected_leaf_abs = abs_path(cgroup_root, expected_leaf)
    if leaf_created:
        if os.path.isdir(expected_leaf_abs):
            recovery.leaf_cgroup = expected_leaf
            recovery.leaf_created = True
        elif os.path.lexists(expected_leaf_abs):
            return REFUSED_STATE_UNAVAILABLE
        else:
            # A crash can happen after the empty leaf was removed but before
            # the journal records `leaf_created = false`. With no leaf left
            # to enumerate, prove each original PID is gone, already at its
            # recorded origin, or still in this exact scope for release below.
            for pid, record in records.items():
                if not recovery._pid_exists(pid):
                    record["state"] = "exited"
                    continue
                start_time = recovery._pid_start_time(pid)
                if start_time is None:
                    return REFUSED_IDENTITY_UNAVAILABLE
                if start_time != record["start_time_ticks"]:
                    record["state"] = "exited"
                    continue
                current = recovery._pid_cgroup(pid)
                if current is None:
                    return REFUSED_IDENTITY_UNAVAILABLE
                normalized_current = posixpath.normpath(current)
                if normalized_current == posixpath.normpath(record["origin_cgroup"]):
                    record["state"] = "restored"
                elif normalized_current == recovery.scope_cgroup:
                    record["state"] = "scope"
                else:
                    return REFUSED_IDENTITY_UNAVAILABLE
    # A leaf path that appeared before the write-ahead record said cgprofile
    # created it is never adopted or removed by recovery.
    for record in records.values():
        recovery.guard.allow_origin(record["origin_cgroup"])
    recovery.release()
    if not recovery.released:
        return recovery.error or REFUSED_STATE_UNAVAILABLE
    return None


def _read_pids(path: str) -> List[int]:
    lines = util.read_lines(path)
    if not lines:
        return []
    pids = []
    for line in lines:
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def _cgroup_is_populated(path: str) -> bool:
    """Require positive kernel evidence before claiming a cgroup kill.

    `cgroup.events`'s `populated` field covers descendant cgroups too, unlike
    `cgroup.procs` on the target alone. Missing, malformed, or empty evidence
    is a refusal; a successful write to an already-empty cgroup is not proof
    that the requested lane was killed.
    """
    lines = util.read_lines(os.path.join(path, "cgroup.events"))
    if lines is None:
        return False
    values = [
        parts[1]
        for line in lines
        if len(parts := line.split()) == 2 and parts[0] == "populated"
    ]
    return values == ["1"]

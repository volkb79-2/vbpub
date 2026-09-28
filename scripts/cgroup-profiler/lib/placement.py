"""Lane placement — RG55-INTERFACE-CONTRACT.md §8.3, design D-20/D-25, RW-35(a).

An *ephemeral* lane is a container, so docker already made it a cgroup of
its own and capped it at create time. An **exec-mode** lane (``docker exec``
into a long-lived devcontainer) and a **bare-host** lane have neither: their
cgroup is shared with the IDE, the agents and the caller, so ``memory.peak``,
``memory.pressure`` and ``io.stat`` are somebody else's numbers and the
lane's declared ``resources.memory`` is advisory. D-20's answer is that the
private-namespace daemon creates a leaf ``<gates slice>/rg-<token>/``, moves
the lane's pids into it, applies the caps and reads them back. A direct
``cgroup.procs`` write is used when the PID is visible; when the host PID is
not addressable from the daemon's private PID namespace, host systemd's
``AttachProcessesToUnit`` D-Bus method performs the exact move into the
already-created subcgroup. The daemon never joins a host namespace.

**Why a sibling leaf under the gates slice and never a child of the
container's own scope** (D-20): enabling a controller inside the container's
cgroup requires writing ``cgroup.subtree_control`` there, after which cgroup
v2's "no internal processes" rule forbids that scope from holding processes
itself — and every later ``docker exec`` into the devcontainer would fail
with ``EBUSY``. Moving a pid OUT of a container's scope is safe: it keeps
the container's pid and mount namespaces (pid-1's death still kills it),
only its accounting moves.

**The write boundary is a whitelist, never a relaxation** (D-15 extended by
D-25, plus RW-35(a)'s single exception). :class:`CgroupWriteGuard` is the
third half of the ``WRITABLE_ROOTS`` boundary whose other halves are
:func:`lib.damon._write_nr_kdamonds` (the DAMON admin sysfs root) and
:meth:`lib.serve.SessionServer._guard_path` (the sessions directory). It
admits exactly:

* ``<gates slice>/cgroup.subtree_control`` — ``+memory``/``+cpu``/``+pids``
  ONLY (RW-35(a): the one non-leaf write, because a hand-created leaf cannot
  take ``memory.high`` unless its parent delegates the controller; a ``-``
  value would DISABLE a controller for every other child of the gates slice
  and is refused);
* ``<gates slice>/rg-*/{cgroup.procs, memory.high, memory.max, cpu.weight,
  cgroup.kill}`` — the leaf this daemon made;
* ``mkdir``/``rmdir`` of ``<gates slice>/rg-*``;
* the ORIGINAL scope's ``cgroup.procs``, for the move-back at ``stop`` and
  nothing else (a pid this daemon moved out is a pid it must be able to put
  back — the alternative is a lane's survivors accounted to a leaf that no
  longer exists).

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
import os
import posixpath
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from . import util


def _systemd_attach_process(
    unit_name: str,
    subcgroup: str,
    pid: int,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Ask host systemd to move one host PID into ``subcgroup``.

    A daemon with private PID and cgroup namespaces cannot write a host PID
    into ``cgroup.procs``: the kernel translates that PID through the writer's
    namespace and returns ``ESRCH``. The systemd manager is already the host
    cgroup authority; its D-Bus method performs the same move in the host PID
    namespace without relaxing D-15. The daemon receives no Docker socket and
    never joins a host namespace. ``unit_name`` is either the verified gates
    slice (move into the lane leaf) or the unit owning the original scope
    (move a survivor back).
    """
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
                subcgroup,
                "1",
                str(pid),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except OSError:
        return False
    return completed.returncode == 0


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

#: §8.3: the leaf's name is ``rg-`` plus the lane's token.
LEAF_PREFIX = "rg-"
#: The only files the daemon may write inside a leaf (§8.3/D-25).
LEAF_FILES = ("cgroup.procs", "memory.high", "memory.max", "cpu.weight", "cgroup.kill")
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
        leaf_name: str,
    ) -> None:
        self.cgroup_root = cgroup_root
        self.gates_cgroup = gates_cgroup
        self.gates_abs = abs_path(cgroup_root, gates_cgroup)
        self.origin_abs = abs_path(cgroup_root, origin_cgroup) if origin_cgroup else None
        self.leaf_name = leaf_name

    # -- predicates --------------------------------------------------------

    def is_leaf(self, path: str) -> bool:
        """Whether ``path`` is a direct ``rg-*`` child of the gates slice."""
        real = os.path.realpath(path)
        return real == os.path.join(os.path.realpath(self.gates_abs), self.leaf_name)

    # -- the three checks --------------------------------------------------

    def check_write(self, path: str, value: str) -> None:
        real = os.path.realpath(path)
        parent, name = os.path.split(real)
        if parent == os.path.realpath(self.gates_abs) and name == SUBTREE_CONTROL:
            self._check_subtree_control_value(path, value)
            return
        if name in LEAF_FILES and self.is_leaf(parent):
            return
        if (
            self.origin_abs is not None
            and name == PROCS
            and parent == os.path.realpath(self.origin_abs)
        ):
            return
        raise HostWriteError(
            f"refusing to write outside the D-25 cgroup whitelist "
            f"(gates slice {self.gates_cgroup!r}): {path!r}"
        )

    def check_mkdir(self, path: str) -> None:
        if not self.is_leaf(path):
            raise HostWriteError(
                f"refusing to create a cgroup that is not an {LEAF_PREFIX}* leaf of "
                f"{self.gates_cgroup!r}: {path!r}"
            )

    def check_rmdir(self, path: str) -> None:
        if not self.is_leaf(path):
            raise HostWriteError(
                f"refusing to remove a cgroup that is not an {LEAF_PREFIX}* leaf of "
                f"{self.gates_cgroup!r}: {path!r}"
            )

    @staticmethod
    def _check_subtree_control_value(path: str, value: str) -> None:
        """RW-35(a): ``+`` values only, and only the three named controllers.

        A ``-memory`` here would revoke the controller from every OTHER child
        of the gates slice — every other lane's leaf, live, mid-run — which
        is precisely the "one non-leaf write" being narrow rather than a
        general permission to write the parent.
        """
        tokens = value.split()
        if not tokens:
            raise HostWriteError(f"refusing an empty {SUBTREE_CONTROL} write: {path!r}")
        for token in tokens:
            if not token.startswith("+"):
                raise HostWriteError(
                    f"refusing a non-'+' {SUBTREE_CONTROL} value {token!r} (RW-35a: the one "
                    f"non-leaf write may only ENABLE a controller): {path!r}"
                )
            if token[1:] not in REQUIRED_CONTROLLERS:
                raise HostWriteError(
                    f"refusing to delegate controller {token[1:]!r} (RW-35a allows only "
                    f"{list(REQUIRED_CONTROLLERS)}): {path!r}"
                )


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
        proc_root: Optional[str] = None,
        systemd_attach: Optional[Callable[[str, str, int], bool]] = None,
        pid_cgroup: Optional[Callable[[int], Optional[str]]] = None,
        pid_exists: Optional[Callable[[int], bool]] = None,
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
        self.guard = CgroupWriteGuard(
            cgroup_root=cgroup_root, gates_cgroup=gates_cgroup,
            origin_cgroup=origin_cgroup, leaf_name=self.leaf_name,
        )
        self.leaf_cgroup: Optional[str] = None
        self.applied: Dict[str, Optional[int]] = {}
        self.error: Optional[str] = None
        self.moved: Set[int] = set()
        self.released = False

    def _default_pid_cgroup(self, pid: int) -> Optional[str]:
        # Imported lazily: targets imports the access layer, while placement
        # is itself wired into serve's target-resolution path.
        from . import targets

        return targets.cgroup_of_pid(pid, self.cgroup_root, self.proc_root)

    def _default_pid_exists(self, pid: int) -> bool:
        return os.path.exists(os.path.join(self.proc_root, str(pid)))

    @property
    def gates_unit(self) -> str:
        """The systemd unit owning ``gates_cgroup`` (e.g. dev-gates.slice)."""
        return os.path.basename(self.gates_cgroup.rstrip("/"))

    # -- geometry ---------------------------------------------------------

    @property
    def leaf_name(self) -> str:
        return f"{LEAF_PREFIX}{self.token}"

    @property
    def leaf_abs(self) -> Optional[str]:
        if self.leaf_cgroup is None:
            return None
        return abs_path(self.cgroup_root, self.leaf_cgroup)

    @property
    def placed(self) -> bool:
        """Whether a leaf exists and holds this lane (§8.4's `throttled` and
        the `cgroup.kill` enforcement path both key on this)."""
        return self.leaf_cgroup is not None and not self.released

    def _relative(self, abs_target: str) -> str:
        return os.path.relpath(abs_target, self.cgroup_root)

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
        os.mkdir(abs_target)
        if self._on_write is not None:
            self._on_write(self._relative(abs_target), "mkdir")

    def _rmdir(self, abs_target: str) -> None:
        self.guard.check_rmdir(abs_target)
        self._rmdir_fn(abs_target)
        if self._on_write is not None:
            self._on_write(self._relative(abs_target), "rmdir")

    # -- §8.3: apply ------------------------------------------------------

    def apply(self, pids: Sequence[int]) -> None:
        """Create the leaf, delegate the controllers if the slice has not,
        write the caps, read them back, migrate ``pids``.

        Never raises for a host condition: every refusal lands in
        :attr:`error` with :attr:`leaf_cgroup` back to ``None``, because
        §8.3's "placement never fails `start`" is the whole point — a lane
        that cannot be placed still has to be profiled.
        """
        if not self.token:
            self.error = REFUSED_NO_TOKEN
            return
        gates_abs = abs_path(self.cgroup_root, self.gates_cgroup)
        if not os.path.isdir(gates_abs):
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

        leaf_abs = os.path.join(gates_abs, self.leaf_name)
        if os.path.lexists(leaf_abs) and not self.guard.is_leaf(leaf_abs):
            # A pre-existing `rg-<token>` that does not RESOLVE to a child of
            # the gates slice (a symlink planted under it) would make every
            # cap below land on somebody else's cgroup. D-25's
            # `parent-not-gates-slice`, and the reason every guard comparison
            # is on `realpath`.
            self.error = REFUSED_PARENT_NOT_GATES_SLICE
            return
        if os.path.lexists(leaf_abs):
            # An orphan or another session's leaf is not ours to cap, move
            # processes into, or remove.  The mkdir below also refuses a
            # leaf appearing between this check and creation.
            self.error = write_failed(self._relative(leaf_abs))
            return
        try:
            self._delegate_controllers(gates_abs)
            self._mkdir(leaf_abs)
        except OSError as exc:
            self.error = write_failed(self._failed_relative(exc, leaf_abs))
            return
        self.leaf_cgroup = os.path.join("/", self._relative(leaf_abs))

        for name in self.request.cap_files():
            target = os.path.join(leaf_abs, name)
            try:
                self._write(target, str(self.request.value_for(name)))
            except OSError as exc:
                self.error = write_failed(self._failed_relative(exc, target))
                self._abandon(leaf_abs)
                return
            # §8.3: `applied` holds what the kernel reports AFTER the write,
            # never the number that was asked for — `memory.high` rounds to a
            # page multiple and `memory.max` can be refused silently by an
            # ancestor, so echoing the request would be a claim, not a
            # reading (the S13.3.2 discipline).
            self.applied[name] = util.read_int(target)

        self.migrate(pids)
        if self.error is not None and not self.moved:
            # A leaf with no successfully migrated PID is not a placement;
            # abandon it rather than expose caps and an empty kill target as
            # if the lane had been contained.
            self._abandon(leaf_abs)

    def _delegate_controllers(self, gates_abs: str) -> None:
        """RW-35(a)'s single non-leaf write, and only when it is needed."""
        control_path = os.path.join(gates_abs, SUBTREE_CONTROL)
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
        """Move every not-yet-moved pid into the leaf. Returns how many moved.

        Called at `start` AND from every discovery tick (§8.3: "also pids
        found later") — the token resolver keeps finding descendants for as
        long as the lane forks, and a pid left in the devcontainer's scope is
        a pid whose memory is not the lane's.

        A pid that vanished between the resolver's walk and this write is not
        an error (the resolver's own ESRCH discipline, :mod:`lib.subtree`) —
        it contributes nothing and is not retried.
        """
        leaf_abs = self.leaf_abs
        if leaf_abs is None or self.released:
            return 0
        procs = os.path.join(leaf_abs, PROCS)
        moved = 0
        enforcement_failed = False
        for pid in pids:
            if pid in self.moved:
                continue
            try:
                self._write(procs, str(pid))
            except OSError as exc:
                # A private PID namespace makes a host-visible PID
                # unaddressable from this writer and the kernel reports
                # ESRCH. Ask host systemd to perform the same move in its
                # host PID namespace, then verify through the explicit host
                # proc view. Other failures retain the existing vanished-pid
                # tolerance; they cannot silently certify placement.
                if exc.errno != errno.ESRCH:
                    continue
                from . import access

                if not access.have_host_proc_view(self.proc_root):
                    enforcement_failed = True
                    if self._log is not None:
                        self._log(
                            f"placement: cannot safely attach pid {pid} through systemd "
                            "without a verified host-proc view"
                        )
                    continue
                if not self._pid_exists(pid):
                    continue
                if not self._systemd_attach(self.gates_unit, self.leaf_name, pid):
                    enforcement_failed = True
                    if self._log is not None:
                        self._log(
                            f"placement: systemd could not attach pid {pid} "
                            f"to {self.gates_unit}/{self.leaf_name}"
                        )
                    continue
                if self._pid_cgroup(pid) != self.leaf_cgroup:
                    enforcement_failed = True
                    if self._log is not None:
                        self._log(
                            f"placement: systemd attach of pid {pid} was not "
                            f"visible in {self.leaf_cgroup}"
                        )
                    continue
                self._record_write(procs, str(pid))
            self.moved.add(pid)
            moved += 1
        if enforcement_failed and self.error is None:
            self.error = write_failed(self._relative(procs))
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

    def release(self) -> None:
        """`stop`: move survivors back to the original scope, then `rmdir` the
        leaf (3 attempts over 3 s). A leaf that will not go is REPORTED
        (`place-refused:write-failed:<file>`) and `stop` still succeeds — a
        cleanup failure must not cost the caller its Summary."""
        leaf_abs = self.leaf_abs
        if leaf_abs is None or self.released:
            return
        if not self._move_survivors_back(leaf_abs):
            if self.error is None:
                origin_procs = os.path.join(
                    abs_path(self.cgroup_root, self.origin_cgroup), PROCS
                )
                self.error = write_failed(self._relative(origin_procs))
            return
        self.released = True
        for attempt in range(RMDIR_ATTEMPTS):
            try:
                self._rmdir(leaf_abs)
                return
            except OSError as exc:
                last = exc
            if attempt < RMDIR_ATTEMPTS - 1:
                self._sleep(RMDIR_RETRY_SECONDS)
        self.error = write_failed(self._relative(leaf_abs))
        if self._log is not None:
            self._log(f"placement: could not remove leaf {self.leaf_cgroup}: {last}")

    def _move_survivors_back(self, leaf_abs: str) -> bool:
        """Whatever is still in the leaf goes back to the scope it came from.

        Read the leaf membership at stop rather than relying on :attr:`moved`:
        the lane may have forked since the last discovery tick. In a private
        PID namespace, enumerate host-visible survivors through the configured
        host-proc view because ``cgroup.procs`` renders them as PID 0. An
        unresolvable survivor or failed move leaves the leaf intact and is
        surfaced in ``placement.error``; it is never silently treated as an
        exited process.
        """
        leaf_procs = os.path.join(leaf_abs, PROCS)
        visible = _read_pids(leaf_procs)
        if not visible:
            return True
        from . import access, targets

        host_proc_view = access.have_host_proc_view(self.proc_root)
        if host_proc_view:
            survivors = targets.pids_in_cgroup(
                self.leaf_cgroup or "", self.cgroup_root, self.proc_root
            )
            if not survivors:
                # The file was nonempty, so an empty host scan is not proof
                # that the PIDs exited: it can also mean the host-proc/cgroup
                # namespace mapping was unavailable.
                if not _read_pids(leaf_procs):
                    return True
                self._log_unrestored_survivor(leaf_procs)
                return False
        else:
            # Positive local PIDs can still be written directly. A zero
            # placeholder means there is at least one host task we cannot
            # identify or restore without the explicit broader proc view.
            survivors = [pid for pid in visible if pid > 0]
            if any(pid <= 0 for pid in visible):
                self._log_unrestored_survivor(leaf_procs)
                return False

        origin_procs = os.path.join(abs_path(self.cgroup_root, self.origin_cgroup), PROCS)
        destination = _systemd_destination(self.origin_cgroup) if host_proc_view else None
        failed = False
        for pid in survivors:
            if host_proc_view:
                if not self._pid_exists(pid):
                    continue
                current = self._pid_cgroup(pid)
                if current is None:
                    if not self._pid_exists(pid):
                        continue
                    failed = True
                    continue
                if posixpath.normpath(current) != posixpath.normpath(self.leaf_cgroup or ""):
                    # A concurrent actor already moved it out of this leaf;
                    # never move it again based only on a stale proc scan.
                    continue
            try:
                self._write(origin_procs, str(pid))
            except OSError as exc:
                if exc.errno == errno.ESRCH and not self._pid_exists(pid):
                    continue  # exited between the membership read and write
                if not host_proc_view or exc.errno != errno.ESRCH or destination is None:
                    failed = True
                    continue
                unit_name, subcgroup = destination
                # Check the exact D-25 path before crossing the system-bus
                # boundary; only this session's original scope is admitted.
                self.guard.check_write(origin_procs, str(pid))
                if not self._systemd_attach(unit_name, subcgroup, pid):
                    failed = True
                    if self._log is not None:
                        self._log(
                            f"placement: systemd could not restore pid {pid} "
                            f"to {unit_name}/{subcgroup}"
                        )
                    continue
                actual = self._pid_cgroup(pid)
                if posixpath.normpath(actual or "") != posixpath.normpath(self.origin_cgroup):
                    if actual is None and not self._pid_exists(pid):
                        continue
                    failed = True
                    if self._log is not None:
                        self._log(
                            f"placement: systemd restore of pid {pid} was not "
                            f"visible in {self.origin_cgroup}"
                        )
                    continue
                self._record_write(origin_procs, str(pid))
            else:
                if host_proc_view:
                    actual = self._pid_cgroup(pid)
                    if actual is not None and posixpath.normpath(actual) != posixpath.normpath(
                        self.origin_cgroup
                    ):
                        failed = True
                    elif actual is None and self._pid_exists(pid):
                        failed = True

        if failed:
            self.error = write_failed(self._relative(origin_procs))
            self._log_unrestored_survivor(leaf_procs)
        return not failed

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
            "leaf": self.leaf_cgroup,
            "applied": dict(self.applied),
            "pids_moved": len(self.moved),
            "error": self.error,
        }


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

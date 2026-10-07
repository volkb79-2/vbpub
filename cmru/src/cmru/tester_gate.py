"""Run a release gate in the dedicated ``tester-unified`` container.

The developer checkout is a cockpit, not release evidence.  This small wrapper
maps the current worktree through the cockpit's bind mount to the host path
Docker can see, then runs an explicit command in the tester image.  It has no
project policy: ``cmru.toml`` declares the command each project considers its
meaningful gate.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Sequence

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)
from cmru import exit_codes
from cmru.cli_support import cmru_registry


def _unescape_mountinfo(value: str) -> str:
    """Decode the octal escapes used by Linux mountinfo paths."""
    for escaped, character in ((r"\040", " "), (r"\011", "\t"), (r"\012", "\n"), (r"\134", "\\")):
        value = value.replace(escaped, character)
    return value


def _physical_path(path: Path, mountinfo: str | None = None) -> Path:
    """Map a cockpit path to the host path seen by Docker, if bind-mounted."""
    resolved = path.resolve()
    if mountinfo is None:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    best: tuple[Path, Path] | None = None
    for line in mountinfo.splitlines():
        fields = line.split(" - ", 1)[0].split()
        if len(fields) < 5:
            continue
        source, destination = (Path(_unescape_mountinfo(fields[index])) for index in (3, 4))
        if destination == Path("/"):
            continue
        if destination == resolved or destination in resolved.parents:
            # ``>=``: on equal-length mount points the LAST mountinfo entry is
            # the visible one (an earlier entry at the same point is shadowed).
            if best is None or len(destination.parts) >= len(best[1].parts):
                best = (source, destination)
    if best is None:
        return resolved
    source, destination = best
    return source / resolved.relative_to(destination)


def _git_common_dir(repo_root: Path) -> Path | None:
    """Resolve the shared ``.git`` directory for ``repo_root``, if it lives
    OUTSIDE ``repo_root`` — i.e. ``repo_root`` is a linked worktree (exactly
    what every cmru release transaction runs gates from: an isolated
    ``git worktree add`` checkout, never the raw developer checkout).

    A linked worktree's ``.git`` is a FILE containing ``gitdir: <absolute
    path>`` pointing at the real repo's object database elsewhere on disk.
    Mounting only the worktree subtree (as :func:`build_docker_command`
    otherwise would) leaves that absolute path unresolvable inside the gate
    container — ``fatal: not a git repository`` for anything needing git
    history (e.g. a test diffing its source against an old commit), even
    though the worktree's checked-out files themselves are all present.

    Returns ``None`` for an ordinary (non-worktree) checkout, where the
    mounted tree already contains everything git needs.
    """
    from cmru.transaction import _common_git_dir, _shared_worktree

    shared = _shared_worktree()
    try:
        common = _common_git_dir(repo_root)
    except shared.WorkspaceError:
        return None
    if common == (repo_root / ".git").resolve():
        return None
    return common


def _resolve_worktree_context(invocation_root: Path, relative_cwd: str) -> tuple[Path, str]:
    """Return the repository root and container-relative target directory.

    Commands in a consumer's ``cmru.toml`` run with that consumer as their
    process cwd.  Mounting that cwd as ``/worktree`` loses its siblings and,
    crucially, the repository's ordinary ``.git`` directory.  Tests that use
    repository history then see ``/`` as their parent and fail despite the
    host checkout being complete.

    Derive the actual Git worktree root rather than treating the caller's cwd
    as an authoritative substitute.  ``relative_cwd`` remains relative to the
    caller, preserving the public CLI contract while the container receives
    the full checkout.
    """
    relative = Path(relative_cwd)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("--cwd must be a relative path inside the current worktree")

    from cmru.transaction import _shared_worktree

    shared = _shared_worktree()
    try:
        repo_root, _common = shared.discover_git_root(invocation_root)
    except shared.WorkspaceError as exc:
        raise SystemExit(
            "tester-gate: refusing to launch — the caller is not inside a Git worktree; "
            "the gate must mount the complete repository, not an inferred subtree."
        ) from exc
    target = (invocation_root / relative).resolve()
    try:
        container_relative = target.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("--cwd must resolve inside the current Git worktree") from exc
    return repo_root, str(container_relative) or "."


_DIND_READY_TIMEOUT = 30.0

# Flat, per-container safe bounds for the tester workload (host dev-tier cgroup
# governance rollout — nyxloom/docs/plan-resource-governance.md + the mdt
# host-setup companion). They are not a fraction of the slice's aggregate cap.
# The optional DinD sidecar has its own required limits (BG-07), separate from
# the workload's so the two envelopes are not doubled or silently shared.
#
_SLICE_PROBE_IMAGE_ENV = "CMRU_TESTER_CGROUP_PROBE_IMAGE"
_CPUS_ENV = "CMRU_TESTER_CPUS"
_PIDS_LIMIT_ENV = "CMRU_TESTER_PIDS_LIMIT"
_DIND_IMAGE_ENV = "CMRU_TESTER_DIND_IMAGE"
_DIND_MEMORY_ENV = "CMRU_TESTER_DIND_MEMORY"
_DIND_CPUS_ENV = "CMRU_TESTER_DIND_CPUS"
_DIND_PIDS_LIMIT_ENV = "CMRU_TESTER_DIND_PIDS_LIMIT"
#: Required with ``--enable-docker`` only; shared with ``cmru standards``.
DIND_TESTER_ENV = (
    _DIND_IMAGE_ENV, _DIND_MEMORY_ENV, _DIND_CPUS_ENV, _DIND_PIDS_LIMIT_ENV,
)
_CGROUP_PARENT_ENV = "CMRU_TESTER_CGROUP_PARENT"
_PIDS_LIMIT_PATTERN = re.compile(r"[1-9][0-9]*\Z")
_DIGEST_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}\Z")
_IMAGE_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*\Z")
#: In-container location of the worktree mount; the events file lives under it.
_WORKTREE_MOUNT = "/worktree"
_EVENTS_DIR = ".cmru"
# Runs INSIDE the gate container: execute the gate command, then copy this
# container's own cgroup counters into a cmru-owned file on the mounted
# worktree (``--rm`` deletes the cgroup at exit, so nothing outside can read
# them afterwards). The command's own exit status is preserved.
_EVENTS_WRAPPER = (
    'events=$1; shift; "$@"; rc=$?; '
    'for f in pids.events memory.events; do '
    'sed "s|^|$f |" "/sys/fs/cgroup/$f"; '
    'done > "$events" 2>/dev/null; exit "$rc"'
)
_CPU_LIMIT_PATTERN = re.compile(r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_MIN_CPU_LIMIT = Decimal("0.00001")
_CPU_LIMIT_ERROR = (
    "CPU limit must be a finite decimal Docker can enforce "
    "(minimum 0.00001 CPUs)"
)


def _docker_run_argv(cgroup_parent: str, *arguments: str) -> list[str]:
    """Start every helper and workload container inside the declared tier."""
    parent = cgroup_parent.strip()
    if not parent:
        raise ValueError("tester-gate Docker containers require a cgroup parent")
    return ["docker", "run", f"--cgroup-parent={parent}", *arguments]


def _container_name(kind: str) -> str:
    """Unique, exact container name (``cmru-<kind>-<uuid8>``) so cleanup never
    needs a filter."""
    return f"cmru-{kind}-{uuid.uuid4().hex[:8]}"


def _remove_container(name: str) -> None:
    """Best-effort ``docker stop`` then ``docker rm -f`` by EXACT name."""
    for argv in (["docker", "stop", "-t", "5", name], ["docker", "rm", "-f", name]):
        try:
            subprocess.run(argv, capture_output=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


@contextmanager
def _terminate_as_exit() -> Iterator[None]:
    """Turn SIGTERM/SIGHUP into ``SystemExit`` so ``finally`` cleanup runs
    (same pattern as ``tools/run_release_gate.py``)."""
    previous: dict[int, object] = {}

    def _handler(signum, _frame) -> None:
        raise SystemExit(128 + signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, _handler)
    except ValueError:  # not the main thread: nothing can be installed
        previous.clear()
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def validate_image_reference(value: str, label: str) -> str:
    """Refuse an image reference docker could parse as an option (BG-13)."""
    candidate = (value or "").strip()
    if not _IMAGE_REF_PATTERN.fullmatch(candidate):
        raise SystemExit(
            f"tester-gate: invalid {label} {value!r}: an image reference must start "
            "with a letter or digit and contain only [A-Za-z0-9._/:@-]"
        )
    return candidate


def require_digest_pinned(value: str, label: str) -> str:
    """Images run privileged / with host PID must be ``@sha256:`` pinned (BG-06)."""
    # Shape first: the shipped templates carry a `<digest>` placeholder, which
    # the generic character check would only call "invalid".
    if "<" in (value or "") or ">" in (value or ""):
        raise SystemExit(
            f"tester-gate: {label} {value!r} is the template placeholder; replace it with "
            "the real pinned reference of a locally present image: "
            "docker image inspect --format '{{index .RepoDigests 0}}' <image>"
        )
    candidate = validate_image_reference(value, label)
    if not _DIGEST_PATTERN.search(candidate):
        raise SystemExit(
            f"tester-gate: {label} {candidate!r} is not digest-pinned. It runs "
            "privileged and is started with --pull=never; pin it as "
            "<repo>@sha256:<64 hex> (docker image inspect --format "
            "'{{index .RepoDigests 0}}' <image>)."
        )
    return candidate


def _positive_pids_limit(value: str, label: str = "pids limit") -> str:
    candidate = str(value).strip()
    if not _PIDS_LIMIT_PATTERN.fullmatch(candidate):
        raise SystemExit(
            f"tester-gate: {label} {value!r} must be a positive integer "
            "(unlimited / 0 / -1 are refused)"
        )
    return candidate

# The orchestration-injected environment every tester-gate step depends on
# (KI-17). These are normally supplied by ``cmru.orchestration.toml [env]`` and
# reach the step through ``cmru release`` -- they are NOT usually set in the
# project's own ``cmru.toml [env]``. ``cmru standards`` validates this same set
# against a project's declared config (it imports this tuple, keeping one source
# of truth); :func:`_missing_orchestration_env` validates it at runtime so a
# step copied out of ``cmru.toml`` and run by hand fails ONCE, naming every
# missing variable, instead of one container spin-up at a time.
REQUIRED_TESTER_ENV = (
    "CMRU_TESTER_UNIFIED_IMAGE",
    "CMRU_TESTER_MEMORY",
    "CMRU_TESTER_MEMORY_SWAP",
    _CPUS_ENV,
    _PIDS_LIMIT_ENV,
    _SLICE_PROBE_IMAGE_ENV,
    _CGROUP_PARENT_ENV,
)


def _run_probe(
    cgroup_parent: str, probe_image: str, *probe_command: str,
) -> subprocess.CompletedProcess:
    """Run one privileged host probe under a unique exact name.

    ``--pull=never``: the probe image is privileged with host PID, so it must
    already be present locally (digest-pinned) and is never pulled implicitly.
    ``timeout`` only kills the docker CLI, so a probe that did not finish
    normally (timeout, signal) has its container removed by exact name.
    """
    name = _container_name("probe")
    finished = False
    try:
        result = subprocess.run(
            _docker_run_argv(
                cgroup_parent, "--rm", "--name", name, "--pull=never",
                "--privileged", "--pid=host", probe_image, *probe_command,
            ),
            capture_output=True, text=True, timeout=30, check=False,
        )
        finished = True
        return result
    finally:
        if not finished:
            _remove_container(name)


def check_slice_unit(
    slice_name: str, probe_image: str, cgroup_parent: str,
) -> tuple[bool | None, str]:
    """Probe whether a systemd slice UNIT is genuinely installed on the DOCKER
    HOST (not this process's own host/mount namespace).

    Mirrors ``ciu/src/ciu/governance.py:check_slice_unit`` — duplicated, not
    imported: ``cmru`` is deliberately dependency-free (``cmru/pyproject.toml``
    declares zero deps), so it cannot import ``ciu`` for one small helper.

    This runs from a cockpit (devcontainer) that has no systemd of its own and
    no view of the host's — private cgroup/mount namespaces, no host cgroupfs
    bind (see vbpub's devcontainer notes). An earlier version of this check
    shelled out to a *local* ``systemctl``, which on any host running the
    standard container ``systemctl`` shim (checks for ``/run/systemd/system``,
    prints a banner, exits 0 either way) silently misreports "not installed"
    for every slice, always — it never actually reached the host. Running
    from inside the eventual dedicated devcontainer doesn't fix this either:
    that container has no host systemd visibility by design either.

    So instead this reaches the real host systemd through a throwaway
    ``--privileged --pid=host`` probe container and ``nsenter -t 1`` into
    PID 1's namespaces (proven live against this host's dbus/systemd) —
    mirroring how ``shared-ramdisk-depot-manager/tools/cgroup-parent.sh``
    solves the same reachability problem via a ``--cgroupns=host`` cgroupfs
    read instead. A pure cgroupfs read was tried first here and rejected: a
    slice that is real but simply hasn't been instantiated yet this boot
    (no scope ever placed under it) has NO cgroup directory at all, which is
    indistinguishable from "never installed" by directory presence alone —
    ``dev-interactive.slice`` on this host is exactly this case (loaded,
    correctly configured, ``Active: inactive`` because nothing has used it
    yet). ``LoadState`` alone is not enough either: systemd auto-vivifies
    ``.slice`` units for ANY name, so ``systemctl show totally-typo.slice``
    also reports ``LoadState=loaded`` — verified live. ``FragmentPath`` is the
    one property that distinguishes a real, configured unit (backed by an
    on-disk unit file) from a name Docker fail-opened into an unlimited
    transient slice (no on-disk file, so ``FragmentPath`` is empty) — this is
    also what host-setup/CGROUP-NOTES.md's own verification cheat sheet uses.

    Returns ``(exists, note)``:

    - ``exists is None`` — no ``docker`` on this host at all; nothing here
      can launch a gate container regardless of slice governance, so the
      caller should warn and let the launch attempt fail on its own terms.
    - ``exists is True`` — the slice is a real, configured unit
      (``LoadState=loaded`` and a non-empty ``FragmentPath``).
    - ``exists is False`` — the slice is missing, unknown, or transient
      (fail-open) — or the host could not be probed at all. Any uncertainty
      here fails closed; a typo'd cgroup-parent must never sail through.
    """
    if shutil.which("docker") is None:
        return None, (
            "no docker on this host — a gate container cannot be launched here "
            "regardless of slice governance; skipping the slice-existence preflight"
        )

    try:
        result = _run_probe(
            cgroup_parent, probe_image,
            "nsenter", "-t", "1", "-m", "-u", "-n", "-i", "-p",
            "systemctl", "show", slice_name,
            "--property=LoadState,FragmentPath", "--no-pager",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not probe the Docker host for {slice_name!r} ({exc})"

    properties = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    load_state = properties.get("LoadState", "")
    fragment_path = properties.get("FragmentPath", "")

    if load_state == "loaded" and fragment_path:
        return True, f"{slice_name}: LoadState=loaded, FragmentPath={fragment_path}"
    if load_state == "loaded":
        return False, (
            f"{slice_name}: LoadState=loaded but FragmentPath is empty — this is a "
            "TRANSIENT slice, the fail-open signature Docker leaves behind for a typo'd "
            "or never-installed name (systemd auto-vivifies .slice units for any name, "
            "hands this one an unlimited cgroup, and the container starts normally). "
            "Install the tier (modern-debian-tools-python-debug/host-setup/install.sh) "
            "or pass a slice that exists."
        )
    if load_state:
        return False, f"{slice_name}: LoadState={load_state} — the unit is not installed on this host"
    return False, (
        f"could not determine {slice_name}'s LoadState on the Docker host "
        f"(probe stderr: {result.stderr.strip()[:300] or 'empty'})"
    )


def _probe_io_support(
    probe_image: str, cgroup_parent: str,
) -> tuple[bool | None, str]:
    """Whether the DOCKER HOST's cgroup hierarchy supports per-device blkio
    throttling — required before passing any ``--device-*-bps/iops`` flag.

    Same reachability constraints as :func:`check_slice_unit` (a cockpit has
    no host view; the privileged nsenter probe is the honest channel).
    Supported means: cgroup v2 hierarchy (``stat -fc %T /sys/fs/cgroup`` ==
    ``cgroup2fs``) with the ``io`` controller present in the root's
    ``cgroup.controllers``. Docker translates the device flags onto that
    controller; on a v1 host, or v2 without ``io`` delegated to the root,
    container creation would fail anyway — this surfaces it as a named
    refusal instead.

    Returns ``(supported | None, note)``: ``None`` = no docker here at all
    (caller warns and lets the launch fail on its own terms); ``False`` =
    probed and unsupported (refuse); ``True`` = go.
    """
    if shutil.which("docker") is None:
        return None, (
            "no docker on this host — skipping the IO-controller preflight"
        )
    try:
        result = _run_probe(
            cgroup_parent, probe_image,
            "sh", "-c",
            "stat -fc %T /sys/fs/cgroup; cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not probe the Docker host's IO support ({exc})"
    if result.returncode != 0:
        # Partial/failed probe (e.g. cat failed): indeterminate, fail closed
        # with the REAL cause rather than misreading partial output.
        return False, (
            "IO-support probe failed on the Docker host "
            f"(rc={result.returncode}; stderr: "
            f"{result.stderr.strip()[:300] or 'empty'})"
        )
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    fstype = lines[0].strip() if lines else ""
    controllers = lines[1].split() if len(lines) > 1 else []
    if fstype == "cgroup2fs" and "io" in controllers:
        return True, "cgroup v2 with the io controller — per-device caps available"
    if fstype == "cgroup2fs":
        return False, (
            "cgroup v2 but the io controller is not delegated to the root "
            f"(controllers: {' '.join(controllers) or '(none)'}) — per-device "
            "blkio caps would fail at container create"
        )
    if fstype == "tmpfs" or fstype.startswith("cgroup"):
        # tmpfs root == hybrid/v1 mount shape: name it, don't fold it into
        # 'could not determine' (review: status folding).
        return False, (
            "hybrid/v1 cgroup hierarchy detected — per-device blkio caps need "
            "cgroup v2 with the io controller"
        )
    return False, (
        f"could not determine the Docker host's cgroup hierarchy "
        f"(fstype={fstype!r}; stderr: {result.stderr.strip()[:300] or 'empty'})"
    )


def resolve_cgroup_parent(explicit: str | None) -> str:
    """Resolve the gate container's ``--cgroup-parent`` from DECLARED config
    only — no ambient reads, no hardcoded default (estate rule).

    Order: ``--cgroup-parent`` (explicit CLI) > ``CMRU_TESTER_CGROUP_PARENT``
    (normally declared once in ``cmru.orchestration.toml [env]`` as a
    ``${CGROUP_PARENT_DEV_GATES}`` reference). An EMPTY or unset value is a
    configuration error: gate containers must never fall through to Docker's
    unconfined default. Per-project override works through the ordinary env
    merge or the explicit CLI flag.

    Whatever non-empty value resolves is verified against the HOST systemd by
    :func:`check_slice_unit` before any container launches.
    """
    if explicit is not None and explicit.strip():
        return explicit.strip()
    configured = (os.environ.get("CMRU_TESTER_CGROUP_PARENT") or "").strip()
    if configured:
        return configured
    raise SystemExit(
        "tester-gate: cgroup_parent is required; set "
        "$CMRU_TESTER_CGROUP_PARENT (normally from cmru.orchestration.toml "
        "[env]) or pass --cgroup-parent"
    )


def resolve_memory(explicit: str | None) -> str:
    """Resolve the gate container's ``--memory`` limit — no hardcoded fallback.

    Order: ``--memory`` (explicit) > ``CMRU_TESTER_MEMORY`` (normally supplied by
    ``cmru.orchestration.toml [env]`` and inherited through ``cmru release`` --
    where the estate's actual default lives -- not the project's own
    ``cmru.toml [env]``). Unresolvable is a hard error, never a silent unbounded
    launch.
    """
    if explicit:
        return explicit
    resolved = os.environ.get("CMRU_TESTER_MEMORY")
    if not resolved:
        raise SystemExit(
            "tester-gate: no memory limit resolvable — pass --memory explicitly, or set "
            "CMRU_TESTER_MEMORY (normally supplied by cmru.orchestration.toml [env] and "
            "inherited through `cmru release`, not usually the project's own cmru.toml [env]). "
            "Refusing to launch an unbounded container next to production."
        )
    return resolved


def resolve_memory_swap(explicit: str | None) -> str:
    """Resolve the gate container's ``--memory-swap`` limit — no hardcoded fallback.

    Order: ``--memory-swap`` (explicit) > ``CMRU_TESTER_MEMORY_SWAP`` (normally
    supplied by ``cmru.orchestration.toml [env]`` and inherited through
    ``cmru release``, not the project's own ``cmru.toml [env]``). Unresolvable is
    a hard error, never a silent unbounded launch. Docker's own flag semantics:
    this is the COMBINED mem+swap total, not swap alone.
    """
    if explicit:
        return explicit
    resolved = os.environ.get("CMRU_TESTER_MEMORY_SWAP")
    if not resolved:
        raise SystemExit(
            "tester-gate: no memory-swap limit resolvable — pass --memory-swap explicitly, or "
            "set CMRU_TESTER_MEMORY_SWAP (normally supplied by cmru.orchestration.toml [env] and "
            "inherited through `cmru release`, not usually the project's own cmru.toml [env]). "
            "Refusing to launch an unbounded container next to production."
        )
    return resolved


def _resolve_required(explicit: str | None, env_name: str, label: str) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    configured = (os.environ.get(env_name) or "").strip()
    if configured:
        return configured
    raise SystemExit(
        f"tester-gate: no {label} resolvable — pass the matching CLI option, or set "
        f"{env_name} (normally supplied by cmru.orchestration.toml [env] and inherited "
        "through `cmru release`, not usually set in the project's own cmru.toml [env])."
    )


def _positive_cpu_limit(value: str) -> str:
    """Require a CPU value Docker can turn into a nonzero limit."""
    candidate = str(value).strip()
    if not _CPU_LIMIT_PATTERN.fullmatch(candidate):
        raise argparse.ArgumentTypeError(_CPU_LIMIT_ERROR)
    try:
        parsed = Decimal(candidate)
        as_float = float(parsed)
        nano_cpus = as_float * 1_000_000_000
        representable = (
            math.isfinite(as_float)
            and math.isfinite(nano_cpus)
            and parsed >= _MIN_CPU_LIMIT
            and 1 <= nano_cpus <= (2**63 - 1)
        )
    except (InvalidOperation, OverflowError, ValueError):
        representable = False
        parsed = Decimal(0)
    if not representable or parsed <= 0:
        raise argparse.ArgumentTypeError(_CPU_LIMIT_ERROR)
    return candidate


def resolve_cpus(explicit: str | None) -> str:
    """Resolve the per-container CPU ceiling without a hidden source default."""
    value = _resolve_required(explicit, _CPUS_ENV, "CPU limit")
    try:
        return _positive_cpu_limit(value)
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(f"tester-gate: {exc}") from exc


def resolve_image(explicit: str | None) -> str:
    """Resolve the gate workload image (validated, not required to be pinned)."""
    return validate_image_reference(
        _resolve_required(explicit, "CMRU_TESTER_UNIFIED_IMAGE", "tester image"),
        "tester image",
    )


def resolve_cgroup_probe_image(explicit: str | None) -> str:
    """Resolve the host-systemd probe image. It runs ``--privileged
    --pid=host`` so it must be digest-pinned (BG-06)."""
    return require_digest_pinned(
        _resolve_required(explicit, _SLICE_PROBE_IMAGE_ENV, "cgroup probe image"),
        "cgroup probe image",
    )


def resolve_dind_image(explicit: str | None) -> str:
    """Resolve the nested-Docker image only for an explicit Docker-enabled gate.
    It runs ``--privileged`` so it must be digest-pinned (BG-06)."""
    return require_digest_pinned(
        _resolve_required(explicit, _DIND_IMAGE_ENV, "nested Docker image"),
        "nested Docker image",
    )


def resolve_pids_limit(explicit: str | None) -> str:
    """Resolve the gate container's ``--pids-limit``: required, no default.

    Without it the ceiling is systemd's DefaultTasksMax, a host-wide default
    shared with the production game server (2026-10-05 zombie incident)."""
    return _positive_pids_limit(
        _resolve_required(explicit, _PIDS_LIMIT_ENV, "pids limit"), "pids limit",
    )


def resolve_dind_memory(explicit: str | None) -> str:
    return _resolve_required(explicit, _DIND_MEMORY_ENV, "nested Docker memory limit")


def resolve_dind_cpus(explicit: str | None) -> str:
    value = _resolve_required(explicit, _DIND_CPUS_ENV, "nested Docker CPU limit")
    try:
        return _positive_cpu_limit(value)
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(f"tester-gate: {exc}") from exc


def resolve_dind_pids_limit(explicit: str | None) -> str:
    return _positive_pids_limit(
        _resolve_required(explicit, _DIND_PIDS_LIMIT_ENV, "nested Docker pids limit"),
        "nested Docker pids limit",
    )


_DIND_PROBE_TIMEOUT = 10.0


def _dind_ready(name: str) -> bool:
    try:
        probe = subprocess.run(
            ["docker", "exec", name, "docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=_DIND_PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False
    return probe.returncode == 0 and bool(probe.stdout.strip())


def _dind_start_argv(
    image: str, name: str, cgroup_parent: str, *, memory: str, cpus: str, pids_limit: str,
) -> list[str]:
    return _docker_run_argv(
        cgroup_parent, "-d", "--rm", "--init", "--privileged", "--pull=never",
        "--name", name, "--memory", memory, "--cpus", cpus,
        "--pids-limit", pids_limit, "-e", "DOCKER_TLS_CERTDIR=", image,
    )


@contextmanager
def dind_sidecar(
    image: str, *, cgroup_parent: str, memory: str, cpus: str, pids_limit: str,
    ready_timeout: float = _DIND_READY_TIMEOUT,
) -> Iterator[str]:
    """Start an ephemeral, fully isolated nested Docker daemon; yield its
    container name once ready. Always torn down, even on failure.

    Chosen over host-socket passthrough (the alternative, simpler approach):
    the gate container never touches the HOST's real Docker daemon — anyone
    with socket access can run arbitrary privileged containers, i.e.
    root-equivalent host access, which a sandboxed test gate shouldn't have.
    Everything a `--enable-docker` gate step does instead lives inside this
    disposable nested daemon and disappears when the sidecar stops. Needs
    `--privileged` (the nested dockerd manages its own cgroups/namespaces).
    """
    name = f"cmru-tester-dind-{uuid.uuid4().hex[:12]}"
    try:
        subprocess.run(
            _dind_start_argv(
                image, name, cgroup_parent,
                memory=memory, cpus=cpus, pids_limit=pids_limit,
            ),
            check=True, capture_output=True, text=True,
        )
        deadline = time.monotonic() + ready_timeout
        while not _dind_ready(name):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"nested Docker daemon ({name}) did not become ready within {ready_timeout}s"
                )
            time.sleep(0.5)
        yield name
    finally:
        _remove_container(name)


def build_docker_command(
    repo_root: Path,
    relative_cwd: str,
    command: Sequence[str],
    *,
    image: str,
    cgroup_parent: str,
    cgroup_parent_dev_background: str = "",
    cgroup_parent_dev_gates: str = "",
    sidecar_name: str | None = None,
    memory: str,
    memory_swap: str,
    cpus: str,
    pids_limit: str,
    container_name: str | None = None,
    events_file: str | None = None,
    device_read_iops: str = "",
    device_write_iops: str = "",
    device_read_bps: str = "",
    device_write_bps: str = "",
) -> list[str]:
    """Build the Docker argv without a shell or an ambient working-tree path.

    ``sidecar_name`` (a running container name from :func:`dind_sidecar`)
    attaches the gate container to that sidecar's network namespace and points
    its Docker CLI at the sidecar's nested daemon — a deliberate, per-invocation
    opt-in, never a default: only a project step that actually needs Docker
    (currently: MDT's OCI-layout push tests,
    modern-debian-tools-python-debug/scripts/test_oci_layout_push.py) should
    request it. Every other project's gate is unaffected.

    ``memory``/``memory_swap``/``cpus``/``pids_limit`` are required (see
    :func:`resolve_memory`, :func:`resolve_memory_swap`, :func:`resolve_cpus`
    and :func:`resolve_pids_limit` — no hardcoded fallback here, matching
    ``cgroup_parent``'s own no-implicit-default rule). The gate runs a command
    cmru does not control (git auto-maintenance, test subprocesses), so it
    always gets ``--init`` (a reaper as PID 1) and a pids ceiling.

    ``container_name`` gives the container an exact name so the caller can stop
    and remove it on termination. ``events_file`` (a path relative to the
    worktree) wraps the command so that, after it exits, the container copies
    its own ``pids.events``/``memory.events`` there; see
    :func:`gate_exit_code`.
    ``cpus`` is a flat
    per-container safe bound, always applied (host dev-tier cgroup
    governance) — genuine per-container guarantees, distinct from and
    complementary to whatever aggregate tier ``cgroup_parent`` places this
    container under (a slice's own limits bound the WHOLE tier combined, not
    any one container in it). The four ``device_*`` values are optional
    per-container blkio caps in Docker's own ``path:rate`` syntax (e.g.
    ``"/dev/vda:1000"``) — empty (the default) means "rely on the
    ``dev.slice`` tier's own aggregate IOPS/bandwidth ceiling instead of a
    per-container one."

    ``cgroup_parent_dev_background`` and ``cgroup_parent_dev_gates``, when
    given, are forwarded into the spawned container as their correspondingly
    named variables. Docker never passes host/caller env into a container on
    its own. The gate placement is the gates tier; the background value is
    retained separately for tests that intentionally start a long-running
    application stack.

    When ``repo_root`` is a linked worktree (see :func:`_git_common_dir`),
    the shared ``.git`` directory is bind-mounted read-only at the SAME
    absolute path it has outside the container — matching, byte-for-byte,
    the absolute ``gitdir:`` path already written into the worktree's own
    ``.git`` file — so git operations needing history (not just the
    checked-out working tree) resolve correctly inside the gate container.
    """
    relative = Path(relative_cwd)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("--cwd must be a relative path inside the current worktree")
    if not command:
        raise ValueError("tester-gate requires a command after '--'")
    try:
        cpus = _positive_cpu_limit(cpus)
    except argparse.ArgumentTypeError as exc:
        raise ValueError(str(exc)) from exc
    image = validate_image_reference(image, "tester image")
    pids_limit = _positive_pids_limit(pids_limit)
    host_root = _physical_path(repo_root)
    argv = _docker_run_argv(
        cgroup_parent,
        "--rm",
        "--init",
        *(["--name", container_name] if container_name else []),
        "--mount", f"type=bind,src={host_root},dst={_WORKTREE_MOUNT}",
        "--workdir", str(Path(_WORKTREE_MOUNT) / relative),
        "--memory", memory,
        "--memory-swap", memory_swap,
        "--cpus", cpus,
        "--pids-limit", pids_limit,
    )
    common_dir = _git_common_dir(repo_root)
    if common_dir is not None:
        host_common_dir = _physical_path(common_dir)
        argv += ["--mount", f"type=bind,src={host_common_dir},dst={common_dir},readonly"]
    if device_read_iops:
        argv += ["--device-read-iops", device_read_iops]
    if device_write_iops:
        argv += ["--device-write-iops", device_write_iops]
    if device_read_bps:
        argv += ["--device-read-bps", device_read_bps]
    if device_write_bps:
        argv += ["--device-write-bps", device_write_bps]
    if cgroup_parent_dev_background:
        argv += ["-e", f"CGROUP_PARENT_DEV_BACKGROUND={cgroup_parent_dev_background}"]
    if cgroup_parent_dev_gates:
        argv += ["-e", f"CGROUP_PARENT_DEV_GATES={cgroup_parent_dev_gates}"]
    if sidecar_name:
        argv += ["--network", f"container:{sidecar_name}", "-e", "DOCKER_HOST=tcp://localhost:2375"]
    if events_file:
        return [
            *argv, image, "sh", "-c", _EVENTS_WRAPPER, "cmru-events-wrapper",
            f"{_WORKTREE_MOUNT}/{events_file}", *command,
        ]
    return [*argv, image, *command]


#: Counters that make a gate run an INFRASTRUCTURE failure (exit 3) even when
#: the command itself exited 0: ``(events file, key, human meaning)``.
_EVENT_COUNTERS = (
    ("pids.events", "max", "the pids limit was hit (fork refused, possible zombie/process flood)"),
    ("memory.events", "oom_kill", "the kernel OOM-killed a process in the gate's cgroup"),
)
EXIT_INFRASTRUCTURE = 3


def read_events_problems(events_path: Path) -> list[str]:
    """Problems found in the in-container cgroup events file; empty means clean.

    A missing, unreadable or incomplete file is itself a problem: without it
    a limit hit cannot be ruled out (a missing file proves nothing)."""
    try:
        text = events_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [
            f"the cgroup events file {events_path} is missing or unreadable ({exc}); the container "
            "user (the uid baked into the image) must be able to write .cmru/ in the mounted "
            "worktree, and a host/image uid mismatch is the usual cause"
        ]
    values: dict[tuple[str, str], int] = {}
    problems = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) == 3 and parts[2].isdigit():
            values[(parts[0], parts[1])] = int(parts[2])
        else:
            problems.append(f"malformed line {line!r} in {events_path}")
    for events, key, meaning in _EVENT_COUNTERS:
        if (events, key) not in values:
            problems.append(f"{events} has no '{key}' counter in {events_path}")
        elif values[(events, key)] > 0:
            problems.append(f"{events} {key}={values[(events, key)]}: {meaning}")
    return problems


def gate_exit_code(command_returncode: int, events_path: Path) -> int:
    """cmru's exit status for one gate run: the command's own code, unless the
    cgroup counters show an infrastructure fault (then :data:`EXIT_INFRASTRUCTURE`,
    even if the command exited 0)."""
    problems = read_events_problems(events_path)
    if not problems:
        return command_returncode
    print(
        f"tester-gate: INFRASTRUCTURE failure (command exit status {command_returncode}): "
        + "; ".join(problems),
        file=sys.stderr,
    )
    return EXIT_INFRASTRUCTURE


def _missing_orchestration_env(args: argparse.Namespace) -> list[str]:
    """Every required tester-gate variable that resolves empty from BOTH its
    explicit CLI flag and the environment (KI-17), in declared order.

    Mirrors each resolver's ``explicit > env`` precedence exactly, so the
    single up-front report matches what would otherwise fail later -- one
    resolver, one container spin-up, at a time. ``CMRU_TESTER_DIND_IMAGE`` is
    required only when ``--enable-docker`` is set, matching where
    :func:`resolve_dind_image` is actually reached.
    """
    explicit_for = {
        "CMRU_TESTER_UNIFIED_IMAGE": args.image,
        "CMRU_TESTER_MEMORY": args.memory,
        "CMRU_TESTER_MEMORY_SWAP": args.memory_swap,
        _CPUS_ENV: args.cpus,
        _PIDS_LIMIT_ENV: args.pids_limit,
        _SLICE_PROBE_IMAGE_ENV: args.cgroup_probe_image,
        _CGROUP_PARENT_ENV: args.cgroup_parent,
    }
    missing = [
        name
        for name in REQUIRED_TESTER_ENV
        if not (explicit_for[name] or os.environ.get(name) or "").strip()
    ]
    if args.enable_docker:
        dind_explicit = {
            _DIND_IMAGE_ENV: args.dind_image,
            _DIND_MEMORY_ENV: args.dind_memory,
            _DIND_CPUS_ENV: args.dind_cpus,
            _DIND_PIDS_LIMIT_ENV: args.dind_pids_limit,
        }
        missing.extend(
            name for name in DIND_TESTER_ENV
            if not (dind_explicit[name] or os.environ.get(name) or "").strip()
        )
    return missing


def tester_gate_cli():
    registry = cmru_registry(
        "cmru tester-gate",
        "Run one command in tester-unified for this worktree.",
        single_command=True,
        no_args_action=True,
    )
    # One env-fallback rule (CLI-17): EVERY default is None at parse time and is
    # resolved at run time as explicit flag > environment variable; the help
    # names the variable as "(default: $VAR)". Nothing reads os.environ while
    # the parser is being built.
    option_data = (
        (("--cwd",), "relative directory in the current worktree", "DIR", {"required": True}),
        (("--image",), "tester container image (default: $CMRU_TESTER_UNIFIED_IMAGE)", "IMG", {"default": None}),
        (("--cgroup-parent",), "explicit host gates slice, verified before launch (default: $CMRU_TESTER_CGROUP_PARENT)", "SLICE", {"default": None}),
        (("--forward-background-slice",), "slice forwarded into the container as $CGROUP_PARENT_DEV_BACKGROUND (default: $CMRU_TESTER_CGROUP_FORWARD_VAR)", "SLICE", {"default": None}),
        (("--forward-gates-slice",), "slice forwarded into the container as $CGROUP_PARENT_DEV_GATES (default: $CMRU_TESTER_CGROUP_FORWARD_GATES_VAR)", "SLICE", {"default": None}),
        (("--memory",), "Docker memory cap (default: $CMRU_TESTER_MEMORY)", "MEMORY", {"default": None}),
        (("--memory-swap",), "Docker combined memory-plus-swap total (default: $CMRU_TESTER_MEMORY_SWAP)", "MEMORY", {"default": None}),
        (("--cpus",), "CPU ceiling >= 0.00001 (default: $CMRU_TESTER_CPUS)", "N", {"default": None, "type": _positive_cpu_limit}),
        (("--pids-limit",), "container process ceiling, a positive integer (default: $CMRU_TESTER_PIDS_LIMIT)", "N", {"default": None}),
        (("--cgroup-probe-image",), "digest-pinned host-systemd probe image (default: $CMRU_TESTER_CGROUP_PROBE_IMAGE)", "IMG", {"default": None}),
        (("--dind-image",), "digest-pinned nested Docker daemon image; with --enable-docker (default: $CMRU_TESTER_DIND_IMAGE)", "IMG", {"default": None}),
        (("--dind-memory",), "nested Docker memory cap; with --enable-docker (default: $CMRU_TESTER_DIND_MEMORY)", "MEMORY", {"default": None}),
        (("--dind-cpus",), "nested Docker CPU ceiling; with --enable-docker (default: $CMRU_TESTER_DIND_CPUS)", "N", {"default": None}),
        (("--dind-pids-limit",), "nested Docker process ceiling; with --enable-docker (default: $CMRU_TESTER_DIND_PIDS_LIMIT)", "N", {"default": None}),
        (("--device-read-iops",), "per-container read IOPS cap, Docker path:rate (default: $CMRU_TESTER_DEVICE_READ_IOPS, none when unset)", "DEV:RATE", {"default": None}),
        (("--device-write-iops",), "per-container write IOPS cap, Docker path:rate (default: $CMRU_TESTER_DEVICE_WRITE_IOPS, none when unset)", "DEV:RATE", {"default": None}),
        (("--device-read-bps",), "per-container read bandwidth cap, Docker path:rate (default: $CMRU_TESTER_DEVICE_READ_BPS, none when unset)", "DEV:RATE", {"default": None}),
        (("--device-write-bps",), "per-container write bandwidth cap, Docker path:rate (default: $CMRU_TESTER_DEVICE_WRITE_BPS, none when unset)", "DEV:RATE", {"default": None}),
        (("--enable-docker",), "give this step an isolated nested Docker daemon", None, {"action": "store_true", "default": False}),
    )
    registry.register(VerbSpec(
        "tester-gate",
        description="Run the supplied command inside tester-unified with declared host limits.",
        group=VerbGroup.MODIFICATION.value,
        mutating=True,
        dry_run=True,
        include_confirmation=False,
        arguments=(ArgumentSpec(
            "command", "command to execute in the gate container", metavar="COMMAND",
            parser_kwargs={"nargs": argparse.REMAINDER, "default": []},
        ),),
        options=tuple(
            OptionSpec(flags, description, metavar=metavar, parser_kwargs=kwargs)
            for flags, description, metavar, kwargs in option_data
        ),
        include_json=False,
        include_progress=False,
        handler=_run_tester_gate,
    ))
    return registry.build()


def main(argv: Sequence[str] | None = None) -> int:
    return tester_gate_cli().run(argv=argv)


def _run_tester_gate(args, _runtime) -> int:
    # SIGTERM/SIGHUP become SystemExit so every container started below is
    # stopped and removed by its exact name (BG-02).
    with _terminate_as_exit():
        return _run_tester_gate_body(args)


def _run_tester_gate_body(args) -> int:
    dry_run = args.dry_run
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]

    missing = _missing_orchestration_env(args)
    if missing:
        # A prerequisite (declared configuration) is missing: exit 3 (CLI-16).
        raise CliFailure(
            "tester-gate: missing required configuration: " + ", ".join(missing) + ".\n"
            "These values are normally supplied by cmru.orchestration.toml [env] and reach\n"
            "this step through `cmru release`; they are NOT usually set in the project's own\n"
            "cmru.toml [env]. If you copied this step out of cmru.toml to run it by hand,\n"
            "export every variable listed above (or pass its matching CLI flag) first.",
            exit_code=exit_codes.PREREQ_MISSING,
        )

    # Guaranteed non-empty by the preflight above; resolvers below stay as
    # defense-in-depth for direct callers.
    image = resolve_image(args.image)

    cgroup_parent = resolve_cgroup_parent(args.cgroup_parent)
    probe_image = resolve_cgroup_probe_image(args.cgroup_probe_image)
    memory = resolve_memory(args.memory)
    memory_swap = resolve_memory_swap(args.memory_swap)
    cpus = resolve_cpus(args.cpus)
    pids_limit = resolve_pids_limit(args.pids_limit)
    dind: dict[str, str] = {}
    if args.enable_docker:
        # Resolved (and refused if unpinned/invalid) BEFORE any container starts.
        dind = dict(
            image=resolve_dind_image(args.dind_image),
            memory=resolve_dind_memory(args.dind_memory),
            cpus=resolve_dind_cpus(args.dind_cpus),
            pids_limit=resolve_dind_pids_limit(args.dind_pids_limit),
        )
    if dry_run:
        print(
            "[DRY RUN] Host gates-slice verification skipped; it starts a temporary "
            "privileged container and will run during an actual launch."
        )
    else:
        exists, note = check_slice_unit(cgroup_parent, probe_image, cgroup_parent)
        if exists is False:
            raise SystemExit(f"tester-gate: refusing to launch — {note}")
        if exists is None:
            print(f"[WARN] tester-gate: {note}", file=sys.stderr)

    # Explicit flag (even an empty one) > environment variable > no cap.
    device_caps = [
        (explicit if explicit is not None else os.environ.get(env_name, "")).strip()
        for explicit, env_name in (
            (args.device_read_iops, "CMRU_TESTER_DEVICE_READ_IOPS"),
            (args.device_write_iops, "CMRU_TESTER_DEVICE_WRITE_IOPS"),
            (args.device_read_bps, "CMRU_TESTER_DEVICE_READ_BPS"),
            (args.device_write_bps, "CMRU_TESTER_DEVICE_WRITE_BPS"),
        )
    ]
    args.device_read_iops, args.device_write_iops = device_caps[0], device_caps[1]
    args.device_read_bps, args.device_write_bps = device_caps[2], device_caps[3]
    if any(device_caps) and not dry_run:
        io_ok, io_note = _probe_io_support(probe_image, cgroup_parent)
        if io_ok is False:
            raise SystemExit(
                "tester-gate: refusing to launch — device IO caps requested but "
                f"unavailable on this host: {io_note}"
            )
        if io_ok is None:
            print(f"[WARN] tester-gate: {io_note}", file=sys.stderr)
    elif any(device_caps):
        print(
            "[DRY RUN] Host IO capability probe skipped; it starts a temporary "
            "privileged container and will run during an actual launch."
        )

    forward_var = (
        args.forward_background_slice
        if args.forward_background_slice is not None
        else os.environ.get("CMRU_TESTER_CGROUP_FORWARD_VAR", "")
    )
    forward_gates_var = (
        args.forward_gates_slice
        if args.forward_gates_slice is not None
        else os.environ.get("CMRU_TESTER_CGROUP_FORWARD_GATES_VAR", "")
    )
    build_kwargs = dict(
        image=image,
        cgroup_parent=cgroup_parent or "",
        cgroup_parent_dev_background=(forward_var or "").strip(),
        cgroup_parent_dev_gates=(forward_gates_var or "").strip(),
        memory=memory,
        memory_swap=memory_swap,
        cpus=cpus,
        pids_limit=pids_limit,
        device_read_iops=args.device_read_iops,
        device_write_iops=args.device_write_iops,
        device_read_bps=args.device_read_bps,
        device_write_bps=args.device_write_bps,
    )

    repo_root, container_cwd = _resolve_worktree_context(Path.cwd(), args.cwd)
    gate_name = _container_name("tester")
    events_rel = f"{_EVENTS_DIR}/tester-gate-events-{uuid.uuid4().hex}.txt"
    build_kwargs.update(container_name=gate_name, events_file=events_rel)
    if dry_run:
        if args.enable_docker:
            dind_name = "cmru-dry-run-dind-sidecar"
            print(
                "[DRY RUN] "
                + shlex.join(_dind_start_argv(
                    dind["image"], dind_name, cgroup_parent,
                    memory=dind["memory"], cpus=dind["cpus"], pids_limit=dind["pids_limit"],
                ))
            )
            docker_argv = build_docker_command(
                repo_root, container_cwd, command,
                sidecar_name=dind_name, **build_kwargs,
            )
        else:
            docker_argv = build_docker_command(
                repo_root, container_cwd, command, **build_kwargs,
            )
        print("[DRY RUN] " + shlex.join(docker_argv))
        return 0

    events_path = repo_root / events_rel
    events_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if args.enable_docker:
            with dind_sidecar(cgroup_parent=cgroup_parent, **dind) as sidecar:
                docker_argv = build_docker_command(
                    repo_root, container_cwd, command, sidecar_name=sidecar, **build_kwargs,
                )
                code = gate_exit_code(subprocess.run(docker_argv, check=False).returncode, events_path)
        else:
            docker_argv = build_docker_command(repo_root, container_cwd, command, **build_kwargs)
            code = gate_exit_code(subprocess.run(docker_argv, check=False).returncode, events_path)
    finally:
        _remove_container(gate_name)
        events_path.unlink(missing_ok=True)
        try:
            events_path.parent.rmdir()  # only if cmru's own directory is now empty
        except OSError:
            pass
    raise SystemExit(code)

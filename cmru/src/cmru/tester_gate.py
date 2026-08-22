"""Run a release gate in the dedicated ``tester-unified`` container.

The developer checkout is a cockpit, not release evidence.  This small wrapper
maps the current worktree through the cockpit's bind mount to the host path
Docker can see, then runs an explicit command in the tester image.  It has no
project policy: ``cmru.toml`` declares the command each project considers its
meaningful gate.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence


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
            if best is None or len(destination.parts) > len(best[1].parts):
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
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (repo_root / common).resolve()
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

    result = subprocess.run(
        ["git", "-C", str(invocation_root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(
            "tester-gate: refusing to launch — the caller is not inside a Git worktree; "
            "the gate must mount the complete repository, not an inferred subtree."
        )
    repo_root = Path(result.stdout.strip()).resolve()
    target = (invocation_root / relative).resolve()
    try:
        container_relative = target.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("--cwd must resolve inside the current Git worktree") from exc
    return repo_root, str(container_relative) or "."


_DIND_READY_TIMEOUT = 30.0

# Flat, per-container safe bounds (host dev-tier cgroup governance rollout —
# nyxloom/docs/plan-resource-governance.md + the mdt host-setup companion).
# Deliberately NOT a fraction of dev.slice's own aggregate budget: every gate
# container gets its own independent ceiling regardless of how many run
# concurrently, on top of (not instead of) the tier's own aggregate cap.
#
_SLICE_PROBE_IMAGE_ENV = "CMRU_TESTER_CGROUP_PROBE_IMAGE"
_CPUS_ENV = "CMRU_TESTER_CPUS"
_DIND_IMAGE_ENV = "CMRU_TESTER_DIND_IMAGE"

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
    _SLICE_PROBE_IMAGE_ENV,
)


def check_slice_unit(slice_name: str, probe_image: str) -> tuple[bool | None, str]:
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
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--privileged", "--pid=host", probe_image,
                "nsenter", "-t", "1", "-m", "-u", "-n", "-i", "-p",
                "systemctl", "show", slice_name,
                "--property=LoadState,FragmentPath", "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
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


def resolve_cgroup_parent(explicit: str | None) -> str:
    """Resolve the gate container's cgroup-parent, per the estate's
    no-hardcoded-fallbacks rule: an unresolvable value is an error, never a
    silent "launch with no --cgroup-parent" degrade.

    Order: ``--cgroup-parent`` (explicit) > ``CMRU_TESTER_CGROUP_PARENT``
    (per-project override, e.g. cmru.toml env) > ``CGROUP_PARENT_DEV_BACKGROUND``
    (ambient, injected by devcontainer.json's ``containerEnv`` — see AGENTS.md)
    > ``CMRU_TESTER_CGROUP_PARENT_FALLBACK`` (an operator-DECLARED literal
    default, normally set once in ``cmru.orchestration.toml [env]`` for hosts
    that run gates outside a devcontainer and therefore have no ambient var).

    The fallback tier is not a code-level hardcoded default: it exists only
    when the operator declares it, and whatever value resolves — fallback
    included — is verified against the HOST systemd by :func:`check_slice_unit`
    before any container launches (a typo'd or fail-open transient slice is
    refused there). On a devcontainer host the ambient var wins by precedence,
    so the fallback never fires; on a bare host it supplies the estate's tier
    explicitly instead of refusing.
    """
    if explicit:
        return explicit
    resolved = os.environ.get("CMRU_TESTER_CGROUP_PARENT") or os.environ.get(
        "CGROUP_PARENT_DEV_BACKGROUND"
    )
    if not resolved:
        declared_fallback = (
            os.environ.get("CMRU_TESTER_CGROUP_PARENT_FALLBACK") or ""
        ).strip()
        if declared_fallback:
            print(
                f"[INFO] tester-gate: no --cgroup-parent, "
                f"CMRU_TESTER_CGROUP_PARENT, or CGROUP_PARENT_DEV_BACKGROUND — "
                f"using the declared fallback {declared_fallback!r} (verified "
                "against the host systemd below)",
                file=sys.stderr,
            )
            return declared_fallback
    if not resolved:
        raise SystemExit(
            "tester-gate: no cgroup_parent resolvable — pass --cgroup-parent explicitly, "
            "or set CMRU_TESTER_CGROUP_PARENT (per-project override), "
            "CGROUP_PARENT_DEV_BACKGROUND (ambient, from devcontainer.json), or "
            "CMRU_TESTER_CGROUP_PARENT_FALLBACK (declared literal default, e.g. in "
            "cmru.orchestration.toml [env]). "
            "Refusing to launch an ungoverned container next to production."
        )
    return resolved


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


def resolve_cpus(explicit: str | None) -> str:
    """Resolve the per-container CPU ceiling without a hidden source default."""
    return _resolve_required(explicit, _CPUS_ENV, "CPU limit")


def resolve_cgroup_probe_image(explicit: str | None) -> str:
    """Resolve the host-systemd probe image as an explicit, pinned input."""
    return _resolve_required(explicit, _SLICE_PROBE_IMAGE_ENV, "cgroup probe image")


def resolve_dind_image(explicit: str | None) -> str:
    """Resolve the nested-Docker image only for an explicit Docker-enabled gate."""
    return _resolve_required(explicit, _DIND_IMAGE_ENV, "nested Docker image")


def _dind_ready(name: str) -> bool:
    probe = subprocess.run(
        ["docker", "exec", name, "docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True, text=True,
    )
    return probe.returncode == 0 and bool(probe.stdout.strip())


@contextmanager
def dind_sidecar(image: str, ready_timeout: float = _DIND_READY_TIMEOUT) -> Iterator[str]:
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
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--privileged", "--name", name,
         "-e", "DOCKER_TLS_CERTDIR=", image],
        check=True, capture_output=True, text=True,
    )
    try:
        deadline = time.monotonic() + ready_timeout
        while not _dind_ready(name):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"nested Docker daemon ({name}) did not become ready within {ready_timeout}s"
                )
            time.sleep(0.5)
        yield name
    finally:
        subprocess.run(["docker", "stop", "-t", "5", name], capture_output=True)


def build_docker_command(
    repo_root: Path,
    relative_cwd: str,
    command: Sequence[str],
    *,
    image: str,
    cgroup_parent: str = "",
    cgroup_parent_dev_background: str = "",
    sidecar_name: str | None = None,
    memory: str,
    memory_swap: str,
    cpus: str,
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

    ``memory``/``memory_swap``/``cpus`` are required (see :func:`resolve_memory`,
    :func:`resolve_memory_swap`, and :func:`resolve_cpus` — no hardcoded
    fallback here, matching ``cgroup_parent``'s own no-implicit-default rule).
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

    ``cgroup_parent_dev_background``, when given, is forwarded into the
    spawned container as ``$CGROUP_PARENT_DEV_BACKGROUND`` — Docker never
    passes host/caller env into a container on its own, and a project's own
    in-process governance code (e.g. ciu's own S15.2 resolver, exercised by
    its own test suite) needs this ambient var visible *inside* the
    container, independent of the ``--cgroup-parent`` placement of the
    container itself.

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
    host_root = _physical_path(repo_root)
    argv = [
        "docker", "run", "--rm",
        "--mount", f"type=bind,src={host_root},dst=/worktree",
        "--workdir", str(Path("/worktree") / relative),
        "--memory", memory,
        "--memory-swap", memory_swap,
        "--cpus", cpus,
    ]
    common_dir = _git_common_dir(repo_root)
    if common_dir is not None:
        host_common_dir = _physical_path(common_dir)
        argv += ["--mount", f"type=bind,src={host_common_dir},dst={common_dir},readonly"]
    if cgroup_parent:
        argv.append(f"--cgroup-parent={cgroup_parent}")
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
    if sidecar_name:
        argv += ["--network", f"container:{sidecar_name}", "-e", "DOCKER_HOST=tcp://localhost:2375"]
    return [*argv, image, *command]


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
        _SLICE_PROBE_IMAGE_ENV: args.cgroup_probe_image,
    }
    missing = [
        name
        for name in REQUIRED_TESTER_ENV
        if not (explicit_for[name] or os.environ.get(name) or "").strip()
    ]
    if args.enable_docker and not (args.dind_image or os.environ.get(_DIND_IMAGE_ENV) or "").strip():
        missing.append(_DIND_IMAGE_ENV)
    return missing


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a command in tester-unified for this worktree")
    parser.add_argument("--cwd", required=True, help="relative directory in the current worktree")
    parser.add_argument(
        "--image",
        default=None,
        help="Required container image; otherwise read explicitly from $CMRU_TESTER_UNIFIED_IMAGE",
    )
    parser.add_argument(
        "--cgroup-parent", default=None,
        help="Defaults to $CMRU_TESTER_CGROUP_PARENT, then $CGROUP_PARENT_DEV_BACKGROUND "
             "(ambient, from devcontainer.json) — see AGENTS.md. No implicit fallback: "
             "unresolvable is a hard error, never an ungoverned launch.",
    )
    parser.add_argument(
        "--memory", default=os.environ.get("CMRU_TESTER_MEMORY"),
        help="Defaults to $CMRU_TESTER_MEMORY (normally from cmru.orchestration.toml [env], "
             "inherited through `cmru release`). No implicit fallback: unresolvable is a hard "
             "error, never an unbounded launch.",
    )
    parser.add_argument(
        "--memory-swap", default=os.environ.get("CMRU_TESTER_MEMORY_SWAP"),
        help="Defaults to $CMRU_TESTER_MEMORY_SWAP (normally from cmru.orchestration.toml "
             "[env], inherited through `cmru release`); Docker's combined mem+swap total, "
             "not swap alone. No implicit fallback.",
    )
    parser.add_argument(
        "--cpus", default=None,
        help="Required CPU ceiling; otherwise read explicitly from $CMRU_TESTER_CPUS",
    )
    parser.add_argument(
        "--cgroup-probe-image", default=None,
        help=(
            "Required host-systemd probe image; otherwise read explicitly from "
            "$CMRU_TESTER_CGROUP_PROBE_IMAGE"
        ),
    )
    parser.add_argument(
        "--dind-image", default=None,
        help=(
            "Required only with --enable-docker; otherwise read explicitly from "
            "$CMRU_TESTER_DIND_IMAGE"
        ),
    )
    parser.add_argument("--device-read-iops", default=os.environ.get("CMRU_TESTER_DEVICE_READ_IOPS", ""))
    parser.add_argument("--device-write-iops", default=os.environ.get("CMRU_TESTER_DEVICE_WRITE_IOPS", ""))
    parser.add_argument("--device-read-bps", default=os.environ.get("CMRU_TESTER_DEVICE_READ_BPS", ""))
    parser.add_argument("--device-write-bps", default=os.environ.get("CMRU_TESTER_DEVICE_WRITE_BPS", ""))
    parser.add_argument(
        "--enable-docker", action="store_true",
        help="Give this gate step an isolated, nested Docker daemon (docker:dind "
            "sidecar) — only pass this for a step that actually needs it.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]

    missing = _missing_orchestration_env(args)
    if missing:
        raise SystemExit(
            "tester-gate: missing required configuration: " + ", ".join(missing) + ".\n"
            "These values are normally supplied by cmru.orchestration.toml [env] and reach\n"
            "this step through `cmru release`; they are NOT usually set in the project's own\n"
            "cmru.toml [env]. If you copied this step out of cmru.toml to run it by hand,\n"
            "export every variable listed above (or pass its matching CLI flag) first."
        )

    # Guaranteed non-empty by the preflight above; resolvers below stay as
    # defense-in-depth for direct callers.
    image = (args.image or os.environ.get("CMRU_TESTER_UNIFIED_IMAGE") or "").strip()

    cgroup_parent = resolve_cgroup_parent(args.cgroup_parent)
    probe_image = resolve_cgroup_probe_image(args.cgroup_probe_image)
    exists, note = check_slice_unit(cgroup_parent, probe_image)
    if exists is False:
        raise SystemExit(f"tester-gate: refusing to launch — {note}")
    if exists is None:
        print(f"[WARN] tester-gate: {note}", file=sys.stderr)

    memory = resolve_memory(args.memory)
    memory_swap = resolve_memory_swap(args.memory_swap)
    cpus = resolve_cpus(args.cpus)

    build_kwargs = dict(
        image=image,
        cgroup_parent=cgroup_parent,
        cgroup_parent_dev_background=os.environ.get("CGROUP_PARENT_DEV_BACKGROUND", ""),
        memory=memory,
        memory_swap=memory_swap,
        cpus=cpus,
        device_read_iops=args.device_read_iops,
        device_write_iops=args.device_write_iops,
        device_read_bps=args.device_read_bps,
        device_write_bps=args.device_write_bps,
    )

    if args.enable_docker:
        dind_image = resolve_dind_image(args.dind_image)
        repo_root, container_cwd = _resolve_worktree_context(Path.cwd(), args.cwd)
        with dind_sidecar(dind_image) as sidecar:
            docker_argv = build_docker_command(
                repo_root, container_cwd, command, sidecar_name=sidecar, **build_kwargs,
            )
            raise SystemExit(subprocess.run(docker_argv, check=False).returncode)

    repo_root, container_cwd = _resolve_worktree_context(Path.cwd(), args.cwd)
    docker_argv = build_docker_command(repo_root, container_cwd, command, **build_kwargs)
    raise SystemExit(subprocess.run(docker_argv, check=False).returncode)

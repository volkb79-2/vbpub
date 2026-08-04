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


_DIND_IMAGE = "docker:dind"
_DIND_READY_TIMEOUT = 30.0

# Flat, per-container safe bounds (host dev-tier cgroup governance rollout —
# nyxloom/docs/plan-resource-governance.md + the mdt host-setup companion).
# Deliberately NOT a fraction of dev.slice's own aggregate budget: every gate
# container gets its own independent ceiling regardless of how many run
# concurrently, on top of (not instead of) the tier's own aggregate cap.
#
# No hardcoded memory/memory-swap default here, deliberately (mirrors
# resolve_cgroup_parent's no-hardcoded-fallbacks rule): the operative default
# lives in cmru.toml's [env] (CMRU_TESTER_MEMORY / CMRU_TESTER_MEMORY_SWAP),
# not in this module — see resolve_memory()/resolve_memory_swap().
_DEFAULT_CPUS = "1.5"


def check_slice_unit(slice_name: str) -> tuple[bool | None, str]:
    """Probe whether a systemd slice UNIT is loaded on this host.

    Mirrors ``ciu/src/ciu/governance.py:check_slice_unit`` — duplicated, not
    imported: ``cmru`` is deliberately dependency-free (``cmru/pyproject.toml``
    declares zero deps), so it cannot import ``ciu`` for one small helper.

    Returns ``(exists, note)``:

    - ``exists is None`` — no ``systemctl`` on this host (not systemd); the
      caller must SKIP the check, not fail it.
    - ``exists is True`` — the unit is installed and known to systemd.
    - ``exists is False`` — systemd is present but the slice is NOT loaded.
      Docker would silently auto-create it transiently with NO limits — this
      is the fail-closed case a typo'd cgroup-parent must not sail through.
    """
    if shutil.which("systemctl") is None:
        return None, (
            "no systemctl on this host — not a systemd host, so governed cgroup "
            "slices cannot be honored here regardless; skipping the slice-existence preflight"
        )
    try:
        result = subprocess.run(
            ["systemctl", "show", slice_name, "--property=LoadState"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"systemctl probe failed ({exc}) — skipping the slice-existence preflight"

    output = (result.stdout or "").strip()
    load_state = output.split("=", 1)[1] if "=" in output else ""
    if load_state == "loaded":
        return True, f"{slice_name}: LoadState=loaded"
    return False, (
        f"{slice_name}: LoadState={load_state or '(unknown — systemctl returned no LoadState)'} "
        "— the unit is not installed on this host"
    )


def resolve_cgroup_parent(explicit: str | None) -> str:
    """Resolve the gate container's cgroup-parent, per the estate's
    no-hardcoded-fallbacks rule: an unresolvable value is an error, never a
    silent "launch with no --cgroup-parent" degrade.

    Order: ``--cgroup-parent`` (explicit) > ``CMRU_TESTER_CGROUP_PARENT``
    (per-project override, e.g. cmru.toml env) > ``CGROUP_PARENT_DEV_BACKGROUND``
    (ambient, injected by devcontainer.json's ``containerEnv`` — see AGENTS.md).
    """
    if explicit:
        return explicit
    resolved = os.environ.get("CMRU_TESTER_CGROUP_PARENT") or os.environ.get("CGROUP_PARENT_DEV_BACKGROUND")
    if not resolved:
        raise SystemExit(
            "tester-gate: no cgroup_parent resolvable — pass --cgroup-parent explicitly, "
            "or set CMRU_TESTER_CGROUP_PARENT (per-project override) or "
            "CGROUP_PARENT_DEV_BACKGROUND (ambient, from devcontainer.json) in the environment. "
            "Refusing to launch an ungoverned container next to production."
        )
    return resolved


def resolve_memory(explicit: str | None) -> str:
    """Resolve the gate container's ``--memory`` limit — no hardcoded fallback.

    Order: ``--memory`` (explicit) > ``CMRU_TESTER_MEMORY`` (normally set in
    cmru.toml's ``[env]``, the one place the estate's actual default lives).
    Unresolvable is a hard error, never a silent unbounded launch.
    """
    if explicit:
        return explicit
    resolved = os.environ.get("CMRU_TESTER_MEMORY")
    if not resolved:
        raise SystemExit(
            "tester-gate: no memory limit resolvable — pass --memory explicitly, "
            "or set CMRU_TESTER_MEMORY (e.g. in cmru.toml's [env]) in the environment. "
            "Refusing to launch an unbounded container next to production."
        )
    return resolved


def resolve_memory_swap(explicit: str | None) -> str:
    """Resolve the gate container's ``--memory-swap`` limit — no hardcoded fallback.

    Order: ``--memory-swap`` (explicit) > ``CMRU_TESTER_MEMORY_SWAP`` (normally
    set in cmru.toml's ``[env]``). Unresolvable is a hard error, never a silent
    unbounded launch. Docker's own flag semantics: this is the COMBINED
    mem+swap total, not swap alone.
    """
    if explicit:
        return explicit
    resolved = os.environ.get("CMRU_TESTER_MEMORY_SWAP")
    if not resolved:
        raise SystemExit(
            "tester-gate: no memory-swap limit resolvable — pass --memory-swap explicitly, "
            "or set CMRU_TESTER_MEMORY_SWAP (e.g. in cmru.toml's [env]) in the environment. "
            "Refusing to launch an unbounded container next to production."
        )
    return resolved


def _dind_ready(name: str) -> bool:
    probe = subprocess.run(
        ["docker", "exec", name, "docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True, text=True,
    )
    return probe.returncode == 0 and bool(probe.stdout.strip())


@contextmanager
def dind_sidecar(image: str = _DIND_IMAGE, ready_timeout: float = _DIND_READY_TIMEOUT) -> Iterator[str]:
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
    image: str = "tester-unified:local",
    cgroup_parent: str = "",
    cgroup_parent_dev_background: str = "",
    sidecar_name: str | None = None,
    memory: str,
    memory_swap: str,
    cpus: str = _DEFAULT_CPUS,
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

    ``memory``/``memory_swap`` are required (see :func:`resolve_memory` /
    :func:`resolve_memory_swap` — no hardcoded fallback here, matching
    ``cgroup_parent``'s own no-implicit-default rule). ``cpus`` is a flat
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


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a command in tester-unified for this worktree")
    parser.add_argument("--cwd", required=True, help="relative directory in the current worktree")
    parser.add_argument("--image", default=os.environ.get("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:local"))
    parser.add_argument(
        "--cgroup-parent", default=None,
        help="Defaults to $CMRU_TESTER_CGROUP_PARENT, then $CGROUP_PARENT_DEV_BACKGROUND "
             "(ambient, from devcontainer.json) — see AGENTS.md. No implicit fallback: "
             "unresolvable is a hard error, never an ungoverned launch.",
    )
    parser.add_argument(
        "--memory", default=os.environ.get("CMRU_TESTER_MEMORY"),
        help="Defaults to $CMRU_TESTER_MEMORY (normally set in cmru.toml's [env]). "
             "No implicit fallback: unresolvable is a hard error, never an unbounded launch.",
    )
    parser.add_argument(
        "--memory-swap", default=os.environ.get("CMRU_TESTER_MEMORY_SWAP"),
        help="Defaults to $CMRU_TESTER_MEMORY_SWAP (normally set in cmru.toml's [env]); "
             "Docker's combined mem+swap total, not swap alone. No implicit fallback.",
    )
    parser.add_argument("--cpus", default=os.environ.get("CMRU_TESTER_CPUS", _DEFAULT_CPUS))
    parser.add_argument("--device-read-iops", default=os.environ.get("CMRU_TESTER_DEVICE_READ_IOPS", ""))
    parser.add_argument("--device-write-iops", default=os.environ.get("CMRU_TESTER_DEVICE_WRITE_IOPS", ""))
    parser.add_argument("--device-read-bps", default=os.environ.get("CMRU_TESTER_DEVICE_READ_BPS", ""))
    parser.add_argument("--device-write-bps", default=os.environ.get("CMRU_TESTER_DEVICE_WRITE_BPS", ""))
    parser.add_argument(
        "--enable-docker", action="store_true",
        default=os.environ.get("CMRU_TESTER_ENABLE_DOCKER", "").strip().lower() in {"1", "true", "yes"},
        help="Give this gate step an isolated, nested Docker daemon (docker:dind "
             "sidecar) — only pass this for a step that actually needs it.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]

    cgroup_parent = resolve_cgroup_parent(args.cgroup_parent)
    exists, note = check_slice_unit(cgroup_parent)
    if exists is False:
        raise SystemExit(f"tester-gate: refusing to launch — {note}")
    if exists is None:
        print(f"[WARN] tester-gate: {note}", file=sys.stderr)

    memory = resolve_memory(args.memory)
    memory_swap = resolve_memory_swap(args.memory_swap)

    build_kwargs = dict(
        image=args.image,
        cgroup_parent=cgroup_parent,
        cgroup_parent_dev_background=os.environ.get("CGROUP_PARENT_DEV_BACKGROUND", ""),
        memory=memory,
        memory_swap=memory_swap,
        cpus=args.cpus,
        device_read_iops=args.device_read_iops,
        device_write_iops=args.device_write_iops,
        device_read_bps=args.device_read_bps,
        device_write_bps=args.device_write_bps,
    )

    if args.enable_docker:
        with dind_sidecar() as sidecar:
            docker_argv = build_docker_command(
                Path.cwd(), args.cwd, command, sidecar_name=sidecar, **build_kwargs,
            )
            raise SystemExit(subprocess.run(docker_argv, check=False).returncode)

    docker_argv = build_docker_command(Path.cwd(), args.cwd, command, **build_kwargs)
    raise SystemExit(subprocess.run(docker_argv, check=False).returncode)

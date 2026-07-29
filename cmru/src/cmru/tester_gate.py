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
import subprocess
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


_DIND_IMAGE = "docker:dind"
_DIND_READY_TIMEOUT = 30.0


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
    sidecar_name: str | None = None,
) -> list[str]:
    """Build the Docker argv without a shell or an ambient working-tree path.

    ``sidecar_name`` (a running container name from :func:`dind_sidecar`)
    attaches the gate container to that sidecar's network namespace and points
    its Docker CLI at the sidecar's nested daemon — a deliberate, per-invocation
    opt-in, never a default: only a project step that actually needs Docker
    (currently: MDT's OCI-layout push tests,
    modern-debian-tools-python-debug/scripts/test_oci_layout_push.py) should
    request it. Every other project's gate is unaffected.
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
    ]
    if cgroup_parent:
        argv.append(f"--cgroup-parent={cgroup_parent}")
    if sidecar_name:
        argv += ["--network", f"container:{sidecar_name}", "-e", "DOCKER_HOST=tcp://localhost:2375"]
    return [*argv, image, *command]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a command in tester-unified for this worktree")
    parser.add_argument("--cwd", required=True, help="relative directory in the current worktree")
    parser.add_argument("--image", default=os.environ.get("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:local"))
    parser.add_argument("--cgroup-parent", default=os.environ.get("CMRU_TESTER_CGROUP_PARENT", ""))
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

    if args.enable_docker:
        with dind_sidecar() as sidecar:
            docker_argv = build_docker_command(
                Path.cwd(), args.cwd, command,
                image=args.image, cgroup_parent=args.cgroup_parent, sidecar_name=sidecar,
            )
            raise SystemExit(subprocess.run(docker_argv, check=False).returncode)

    docker_argv = build_docker_command(
        Path.cwd(), args.cwd, command, image=args.image, cgroup_parent=args.cgroup_parent,
    )
    raise SystemExit(subprocess.run(docker_argv, check=False).returncode)

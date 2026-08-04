"""Privilege model: can this process see the host cgroup tree, or must it
re-exec itself inside a privileged helper container?

Two modes, auto-detected:

**direct** — the process already sees the host's cgroup v2 root (running as
root on the host, or in a container started with ``--cgroupns=host
--privileged``).  Everything is read straight from ``/sys/fs/cgroup`` and
``/proc``.

**helper** — the common devcontainer case.  ``/sys/fs/cgroup`` is a read-only,
cgroup-namespaced view containing only our own subtree, so the host's slices
are simply not present.  Rather than shuttle individual file reads across a
container boundary (which cannot sustain a 250 ms cadence), the profiler
**re-executes its whole self** inside a privileged helper container that has
``--cgroupns=host --pid=host`` and streams its output back over a shared
directory.  Sampling code is then identical in both modes.

The helper needs an image with python3 and the repo bind-mounted.  Both are
resolved from the *host's* view of paths: a bind source given to the Docker
daemon is a host path, never a path inside this container, so the workspace
mount has to be translated (``/workspaces/vbpub`` → ``/home/vb/…/vbpub``)
before it can be passed on.  That translation is derived from our own
container's mount table, not configured.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CGROUP_ROOT = "/sys/fs/cgroup"

# Root-level cgroups that only exist in the host's own view. If none of these
# are visible we are looking at a namespaced subtree, whatever the mount says.
_HOST_MARKERS = ("init.scope", "system.slice", "user.slice")


class AccessError(RuntimeError):
    """Raised when neither direct nor helper access can be established."""


@dataclass
class HostPathMap:
    """Container-path → host-path translation for bind mounts.

    Ordered longest-destination-first so the most specific mount wins.
    """

    entries: List[Tuple[str, str]] = field(default_factory=list)

    def to_host(self, path: str) -> Optional[str]:
        real = os.path.realpath(path)
        for dest, source in self.entries:
            if real == dest:
                return source
            if real.startswith(dest.rstrip("/") + "/"):
                return os.path.join(source, os.path.relpath(real, dest))
        return None


def have_host_cgroup_view(root: str = CGROUP_ROOT) -> bool:
    """True when ``root`` is the host's cgroup v2 root rather than a subtree."""
    if not os.path.isdir(root):
        return False
    if not os.path.exists(os.path.join(root, "cgroup.controllers")):
        return False
    return any(os.path.exists(os.path.join(root, marker)) for marker in _HOST_MARKERS)


def cgroup_root_is_writable(root: str = CGROUP_ROOT) -> bool:
    """True when we could actually set a limit (needed for ``--cap-*``).

    Tested by attempting a probe directory rather than by checking the mount
    flags: ``/sys/fs/cgroup`` reports ``rw`` inside an unprivileged container
    while every write is still denied.
    """
    probe = os.path.join(root, ".cgprofile-probe")
    try:
        os.mkdir(probe)
    except OSError:
        return False
    try:
        os.rmdir(probe)
    except OSError:
        pass
    return True


def in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    return "docker" in text or "containerd" in text


# ── docker plumbing ─────────────────────────────────────────────────────────

def docker_bin() -> Optional[str]:
    return shutil.which("docker")


def _docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    binary = docker_bin()
    if not binary:
        raise AccessError("docker CLI not found — cannot start a helper container")
    return subprocess.run(
        [binary, *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def self_container_id() -> Optional[str]:
    """Best-effort identity of the container we are running in.

    Docker sets the container hostname to the short id unless a name was
    assigned, and either form is accepted by ``docker inspect``, so the
    hostname is tried first and only then the cgroup path (which is empty
    under a private cgroup namespace).
    """
    host = socket.gethostname()
    if host:
        result = _docker("inspect", host, "--format", "{{.Id}}")
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    for token in text.replace("/", " ").replace("-", " ").replace(".", " ").split():
        if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
            return token
    return None


def self_inspect() -> Optional[dict]:
    cid = self_container_id()
    if not cid:
        return None
    result = _docker("inspect", cid)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data[0] if data else None


def host_path_map() -> HostPathMap:
    """Build the container→host bind-mount translation for this container."""
    info = self_inspect()
    entries: List[Tuple[str, str]] = []
    if info:
        for mount in info.get("Mounts", []):
            if mount.get("Type") != "bind":
                continue
            dest = mount.get("Destination")
            source = mount.get("Source")
            if dest and source:
                entries.append((os.path.normpath(dest), os.path.normpath(source)))
    entries.sort(key=lambda pair: len(pair[0]), reverse=True)
    return HostPathMap(entries)


def self_image() -> Optional[str]:
    """The image this container runs, the natural helper image.

    Using our own image guarantees the helper's python matches the code it is
    about to execute, and guarantees the image is present locally — a helper
    that has to pull is a helper that fails on an offline host.
    """
    info = self_inspect()
    if not info:
        return None
    return (info.get("Config") or {}).get("Image")


def image_exists(image: str) -> bool:
    return _docker("image", "inspect", image).returncode == 0


def resolve_helper_image(explicit: Optional[str] = None) -> str:
    """Pick the helper image: explicit > env > our own image > known fallbacks.

    Deliberately never pulls. A profiler that reaches for the network on a
    RAM-tight host during a gate run is doing the opposite of its job.
    """
    if explicit:
        if not image_exists(explicit):
            raise AccessError(f"helper image not present locally: {explicit}")
        return explicit
    env = os.environ.get("CGPROFILE_HELPER_IMAGE")
    if env:
        if not image_exists(env):
            raise AccessError(
                f"CGPROFILE_HELPER_IMAGE={env} is not present locally "
                "(this tool never pulls — load or build it first)"
            )
        return env
    own = self_image()
    if own and image_exists(own):
        return own
    for candidate in ("vbpub-escape:local", "debian:trixie-slim", "python:3-slim"):
        if image_exists(candidate):
            return candidate
    raise AccessError(
        "no helper image found. Set CGPROFILE_HELPER_IMAGE to a locally "
        "present image that has python3."
    )


@dataclass
class HelperSpec:
    """Everything needed to launch the helper, resolved from this container."""

    image: str
    repo_host_path: str
    repo_mount_path: str
    out_host_path: str
    out_mount_path: str
    cgroup_parent: str

    def docker_args(self, name: Optional[str] = None) -> List[str]:
        args = [
            "run",
            "--rm",
            "-i",
            "--privileged",
            "--user",
            "0:0",
            # The whole point: the host's cgroup tree, its PID namespace (so
            # /proc/<pid> of a monitored container resolves), and its network
            # (so nothing new is plumbed).
            "--cgroupns=host",
            "--pid=host",
            # The collector reads files and writes to a bind mount; it has no
            # business on the network. Denying it also skips veth setup, which
            # is the slowest part of starting the helper.
            "--network=none",
            # The observer must not land inside the observed. This host's
            # daemon.json sets `cgroup-parent: dev-background.slice`, so a
            # helper that named no parent would be placed in the very tier a
            # gate run profiles — contaminating the measurement with the
            # profiler's own 4 Hz sampling and charging it to the gate tier's
            # memory budget. Placement is never left to the daemon default.
            f"--cgroup-parent={self.cgroup_parent}",
            # Never let the observer become part of what is being observed:
            # placement is inherited from whatever the caller is in otherwise.
            "-v",
            f"{self.repo_host_path}:{self.repo_mount_path}:ro",
            "-v",
            f"{self.out_host_path}:{self.out_mount_path}:rw",
            "-e",
            "CGPROFILE_IN_HELPER=1",
            "-e",
            "PYTHONUNBUFFERED=1",
        ]
        if name:
            args.extend(["--name", name])
        args.append(self.image)
        return args


def resolve_helper_cgroup_parent(explicit: Optional[str] = None) -> str:
    """Where the helper container itself is placed.

    Order: explicit > ``$CGPROFILE_HELPER_CGROUP_PARENT`` >
    ``$CGROUP_PARENT_DEV_INTERACTIVE``. There is deliberately **no fallback to
    the daemon default**: on this estate that default is the background tier,
    which is exactly where gate runs live, so falling through would put the
    profiler inside its own measurement. A profiler that silently perturbs what
    it measures is worse than one that refuses to start.

    The interactive tier is the right home because this is a cockpit tool —
    short-lived, driven by a person waiting on it — not background work.
    """
    if explicit:
        return explicit
    for var in ("CGPROFILE_HELPER_CGROUP_PARENT", "CGROUP_PARENT_DEV_INTERACTIVE"):
        value = os.environ.get(var)
        if value:
            return value
    raise AccessError(
        "cannot place the helper container: set CGPROFILE_HELPER_CGROUP_PARENT "
        "(or $CGROUP_PARENT_DEV_INTERACTIVE, normally injected by "
        "devcontainer.json).\n"
        "  This is not a fallback we can guess: Docker's daemon default on this "
        "host is the background tier, which is where gate runs are profiled — "
        "placing the collector there would make it part of its own measurement."
    )


def build_helper_spec(
    repo_dir: str,
    out_dir: str,
    image: Optional[str] = None,
    cgroup_parent: Optional[str] = None,
) -> HelperSpec:
    """Resolve image + host paths for the helper, or explain why we cannot."""
    mapping = host_path_map()
    repo_dir = os.path.realpath(repo_dir)
    out_dir = os.path.realpath(out_dir)

    repo_host = mapping.to_host(repo_dir)
    if repo_host is None:
        raise AccessError(
            f"cannot translate {repo_dir} to a host path — the profiler code "
            "must live under a bind mount for the helper container to see it. "
            "Run with --mode direct on the host instead."
        )
    out_host = mapping.to_host(out_dir)
    if out_host is None:
        raise AccessError(
            f"cannot translate the output directory {out_dir} to a host path. "
            "Choose an --out-dir under a bind-mounted path (for example one "
            "inside the workspace), or run with --mode direct on the host."
        )
    return HelperSpec(
        image=resolve_helper_image(image),
        repo_host_path=repo_host,
        repo_mount_path=repo_dir,
        out_host_path=out_host,
        out_mount_path=out_dir,
        cgroup_parent=resolve_helper_cgroup_parent(cgroup_parent),
    )


def describe_access(root: str = CGROUP_ROOT) -> Dict[str, object]:
    """A doctor-style summary of what this process can and cannot reach."""
    direct = have_host_cgroup_view(root)
    return {
        "in_helper": os.environ.get("CGPROFILE_IN_HELPER") == "1",
        "in_container": in_container(),
        "host_cgroup_view": direct,
        "cgroup_writable": cgroup_root_is_writable(root) if direct else False,
        "damon_sysfs": os.path.isdir("/sys/kernel/mm/damon/admin/kdamonds"),
        "damon_writable": os.access(
            "/sys/kernel/mm/damon/admin/kdamonds/nr_kdamonds", os.W_OK
        ),
        "docker": docker_bin(),
        "uid": os.getuid(),
    }


def choose_mode(requested: str, root: str = CGROUP_ROOT) -> str:
    """Resolve ``auto`` into ``direct`` or ``helper``; validate an explicit one."""
    if requested not in ("auto", "direct", "helper"):
        raise AccessError(f"unknown access mode: {requested}")
    if requested == "direct":
        if not have_host_cgroup_view(root):
            raise AccessError(
                "--mode direct requested but this process cannot see the host "
                "cgroup tree (only its own namespaced subtree). Drop to --mode "
                "auto, or run as root on the host."
            )
        return "direct"
    if requested == "helper":
        return "helper"
    return "direct" if have_host_cgroup_view(root) else "helper"

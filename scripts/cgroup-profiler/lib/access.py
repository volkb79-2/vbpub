"""Privilege model: can this process see the host cgroup tree, or must it
re-exec itself inside a privileged helper container?

Two modes, auto-detected:

**direct** — the process has an explicit view of the host's cgroup v2 root
(running on the host, or with the host cgroup tree bind-mounted at
``/sys/fs/cgroup``). Host process files are read through
``CGPROFILE_PROC_ROOT`` when configured; the process's own identity still
comes from its private ``/proc``.

**helper** — the common devcontainer case. Rather than shuttle individual file
reads across a container boundary (which cannot sustain a 250 ms cadence), the
profiler **re-executes its whole self** inside a privileged helper container
with private PID/cgroup namespaces and explicit host ``/proc`` and cgroup-v2
bind mounts. Host proc files are mounted read-only at ``/hostproc`` and
sampling code reads them through ``CGPROFILE_PROC_ROOT``.

The helper needs an image with python3 and the repo bind-mounted.  Both are
resolved from the *host's* view of paths: a bind source given to the Docker
daemon is a host path, never a path inside this container, so the workspace
mount has to be translated (``/workspaces/vbpub`` → ``/home/vb/…/vbpub``)
before it can be passed on. That translation is derived from our own
container's mount table, not configured. Before starting the helper, a
separate bounded probe uses the local ``tester-unified:local`` image (or
``CGPROFILE_PLACEMENT_PROBE_IMAGE``) to verify both the requested and trusted
interactive systemd slices through the host manager; absent evidence refuses
launch rather than trusting Docker's transient-slice fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CGROUP_ROOT = "/sys/fs/cgroup"


def _configured_proc_root() -> str:
    value = os.environ.get("CGPROFILE_PROC_ROOT")
    if value is None:
        return "/proc"
    root = value.strip()
    if not root:
        raise RuntimeError("CGPROFILE_PROC_ROOT must not be empty")
    if not os.path.isabs(root):
        raise RuntimeError(f"CGPROFILE_PROC_ROOT must be absolute, got {value!r}")
    return os.path.normpath(root)


PROC_ROOT = _configured_proc_root()

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


def have_host_proc_view(proc_root: str = PROC_ROOT) -> bool:
    """Require *proc_root* to expose a broader PID namespace than this one.

    A bind-mounted procfs still reports cgroup paths relative to the reader's
    cgroup namespace, so PID 1's ``/proc/<pid>/cgroup`` is not a sound host
    view check. Its PID-namespace identity is: in a container, host PID 1 is
    in a different namespace from this container's PID 1; on the host, both
    paths name the same namespace.
    """
    try:
        selected_pid_ns = os.stat(os.path.join(proc_root, "1", "ns", "pid")).st_ino
        local_pid_ns = os.stat("/proc/1/ns/pid").st_ino
    except OSError:
        return False
    if in_container():
        return selected_pid_ns != local_pid_ns
    return selected_pid_ns == local_pid_ns


def same_cgroup_namespace(pid: int, proc_root: str = PROC_ROOT) -> bool:
    """Whether ``pid`` and this process interpret cgroup paths from one root.

    Helper-mode PID targets can be translated through the caller container's
    Docker ID only when both paths share that container's cgroup namespace.
    An inode mismatch is evidence that the target's relative path has a
    different root; absent namespace metadata is indeterminate and refuses
    that translation.
    """
    try:
        target = os.stat(os.path.join(proc_root, str(pid), "ns", "cgroup")).st_ino
        current = os.stat("/proc/self/ns/cgroup").st_ino
    except OSError:
        return False
    return target == current


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
        # This process's own cgroup, not PID 1's: the configured host-proc
        # bind may show the host's init at /hostproc/1.
        with open("/proc/self/cgroup", "r", encoding="utf-8") as fh:
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


_PLACEMENT_PROBE = r"""
import pathlib
import shutil
import subprocess
import sys
import time

systemctl = shutil.which("systemctl")
if systemctl is None:
    print("placement verifier image must provide systemctl", file=sys.stderr)
    raise SystemExit(2)

def show(unit, prop):
    result = subprocess.run(
        [systemctl, "show", unit, "--property=" + prop, "--value"],
        capture_output=True, text=True, check=False, timeout=5,
    )
    if result.returncode:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout.strip()

for unit in dict.fromkeys(sys.argv[1:]):
    state = show(unit, "LoadState")
    fragment = show(unit, "FragmentPath")
    control_group = show(unit, "ControlGroup")
    if state != "loaded" or not fragment:
        print(
            f"unit={unit} LoadState={state} FragmentPath={fragment}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if fragment == "/run/systemd/transient" or fragment.startswith(
        "/run/systemd/transient/"
    ):
        print(f"refusing transient unit fragment: {fragment}", file=sys.stderr)
        raise SystemExit(1)
    if not control_group.startswith("/") or any(
        part in ("", ".", "..") for part in control_group.split("/")[1:]
    ):
        print(
            f"unit={unit} has invalid ControlGroup={control_group}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    path = pathlib.Path("/hostcg") / control_group.lstrip("/")
    if not path.is_dir():
        print(f"cgroup path missing: {path}", file=sys.stderr)
        raise SystemExit(1)
    print(
        f"VERIFIED_SLICE={unit} LoadState={state} "
        f"FragmentPath={fragment} ControlGroup={control_group}"
    )

# Keep the detached container alive long enough for its create-time and
# immediate post-launch CPU caps to both be inspected.
time.sleep(1)
"""


def _placement_docker(*args: str, timeout: int) -> subprocess.CompletedProcess:
    try:
        return _docker(*args, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AccessError(
            f"placement probe Docker command {args[0]!r} failed: {exc}"
        ) from exc


def _remove_placement_probe(name: str) -> None:
    try:
        _docker("rm", "--force", name, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _cap_and_remove_placement_probe(name: str) -> None:
    try:
        _docker("update", "--cpus=3", name, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    _remove_placement_probe(name)


def verify_helper_cgroup_parent(parent: str) -> None:
    """Refuse helper placement unless systemd confirms the slice is loaded.

    The probe runs under the cockpit's injected interactive slice, after
    proving that value matches this container's actual Docker placement. It
    can therefore ask the host system manager over a read-only DBus mount
    without joining any host namespace. The helper itself is not launched
    until both the trusted probe tier and requested target tier are verified.
    """
    if (
        not parent or parent != parent.strip() or "/" in parent
        or not parent.endswith(".slice")
    ):
        raise AccessError(
            f"helper cgroup parent must be a non-empty slice name, got {parent!r}"
        )

    probe_parent = os.environ.get("CGROUP_PARENT_DEV_INTERACTIVE")
    if (
        not probe_parent or probe_parent != probe_parent.strip()
        or "/" in probe_parent or not probe_parent.endswith(".slice")
    ):
        raise AccessError(
            "cannot verify helper placement: CGROUP_PARENT_DEV_INTERACTIVE is missing "
            "or is not a simple slice name; refusing to start an unplaced probe"
        )
    info = self_inspect()
    actual_parent = ((info or {}).get("HostConfig") or {}).get("CgroupParent")
    if not actual_parent:
        raise AccessError(
            "cannot verify helper placement: Docker did not report this "
            "container's cgroup parent"
        )
    if actual_parent != probe_parent:
        raise AccessError(
            f"trusted probe parent {probe_parent!r} differs from this container's "
            f"actual Docker parent {actual_parent!r}"
        )

    probe_image = os.environ.get("CGPROFILE_PLACEMENT_PROBE_IMAGE")
    if probe_image is None:
        probe_image = "tester-unified:local"
    if not probe_image or probe_image != probe_image.strip():
        raise AccessError(
            "CGPROFILE_PLACEMENT_PROBE_IMAGE must be a non-empty local image name"
        )
    if not image_exists(probe_image):
        raise AccessError(
            f"placement verifier image {probe_image!r} is not present locally; "
            "build tester-unified:local or set CGPROFILE_PLACEMENT_PROBE_IMAGE"
        )

    name = f"cgprofile-placement-probe-{os.getpid()}-{time.time_ns()}"
    print(
        f"cgprofile: placement probe container={name} parent={probe_parent}",
        file=sys.stderr, flush=True,
    )
    try:
        started = _docker(
            "run", "-d", "--name", name,
            f"--cgroup-parent={probe_parent}", "--cgroupns=private",
            "--network=none", "--cpus=3", "--memory=256m", "--memory-swap=256m",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user=1003:1003",
            "--mount=type=bind,source=/sys/fs/cgroup,target=/hostcg,readonly",
            "--mount=type=bind,source=/run/systemd/system,target=/run/systemd/system,readonly",
            "--mount=type=bind,source=/run/dbus/system_bus_socket,target=/tmp/host-system-bus,readonly",
            "-e", "DBUS_SYSTEM_BUS_ADDRESS=unix:path=/tmp/host-system-bus",
            "--entrypoint=python3", probe_image, "-c", _PLACEMENT_PROBE,
            probe_parent, parent, timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        _cap_and_remove_placement_probe(name)
        raise AccessError(
            f"starting placement probe {name} timed out: {exc}"
        ) from exc
    except OSError as exc:
        raise AccessError(f"could not start placement probe {name}: {exc}") from exc
    if started.returncode != 0:
        # Docker may have created the named container even if the client
        # returned an error (for example, a lost response after create).
        # Names include a per-call nanosecond nonce, so cleanup is scoped to
        # this exact attempted probe and cannot match an operator container.
        _cap_and_remove_placement_probe(name)
        detail = (started.stderr or started.stdout).strip()
        raise AccessError(
            f"could not start placement probe {name}: "
            f"{detail or started.returncode}"
        )

    try:
        # This is intentionally the first action after Docker confirms the
        # detached container exists; the create-time cap is repeated live.
        updated = _placement_docker("update", "--cpus=3", name, timeout=5)
        if updated.returncode != 0:
            raise AccessError(
                f"could not apply the 3-CPU cap to placement probe {name}"
            )
        if not started.stdout.strip():
            raise AccessError(
                f"Docker started placement probe {name} without returning "
                "its container ID"
            )
        inspected = _placement_docker(
            "inspect", name, "--format",
            "{{.HostConfig.NanoCpus}} {{.HostConfig.CgroupParent}}",
            timeout=5,
        )
        if inspected.returncode != 0:
            raise AccessError(f"could not inspect placement probe {name}")
        cap = inspected.stdout.strip().split()
        if cap != ["3000000000", probe_parent]:
            raise AccessError(
                f"placement probe {name} has unsafe placement/cap: "
                f"{inspected.stdout.strip()!r}"
            )

        waited = _placement_docker("wait", name, timeout=15)
        logs = _placement_docker("logs", name, timeout=5)
        if waited.returncode != 0 or waited.stdout.strip() != "0":
            detail = (logs.stderr or logs.stdout).strip()
            raise AccessError(
                "host systemd rejected helper slice placement: "
                f"{detail or waited.stdout.strip()}"
            )
        if logs.returncode != 0:
            raise AccessError(
                f"could not read placement probe {name} logs"
            )
        verified = set()
        for line in logs.stdout.splitlines():
            if line.startswith("VERIFIED_SLICE="):
                verified.add(line.split(" ", 1)[0].partition("=")[2])
        if not {probe_parent, parent}.issubset(verified):
            raise AccessError(
                f"placement probe {name} returned no complete systemd evidence for "
                f"{probe_parent!r} and {parent!r}"
            )
        print(
            "cgprofile: verified host placement slices:\n"
            f"{logs.stdout.strip()}",
            file=sys.stderr,
        )
    finally:
        _remove_placement_probe(name)


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
            "--cgroupns=private",
            # Namespaces stay private. These explicit bind mounts provide the
            # host observations needed by the collector without sharing host
            # PID or cgroup namespaces; the cgroup view is read-only.
            "--cpus=3",
            "--mount=type=bind,source=/proc,target=/hostproc,readonly",
            "--mount=type=bind,source=/sys/fs/cgroup,target=/sys/fs/cgroup,readonly",
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
            "CGPROFILE_PROC_ROOT=/hostproc",
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
    helper_image = resolve_helper_image(image)
    helper_parent = resolve_helper_cgroup_parent(cgroup_parent)
    verify_helper_cgroup_parent(helper_parent)
    return HelperSpec(
        image=helper_image,
        repo_host_path=repo_host,
        repo_mount_path=repo_dir,
        out_host_path=out_host,
        out_mount_path=out_dir,
        cgroup_parent=helper_parent,
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

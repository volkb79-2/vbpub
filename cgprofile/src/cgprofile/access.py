"""Host-view detection: can this process see the host cgroup tree and proc?

The process has an explicit view of the host's cgroup v2 root when it runs on
the host or with the host cgroup tree bind-mounted at ``/sys/fs/cgroup``. Host
process files are read through ``CGPROFILE_PROC_ROOT`` when configured; the
process's own identity comes from local ``/proc``, whose PID namespace is
determined by deployment (host for the managed daemon).

The 1.x helper-container mode (re-executing the profiler inside a privileged
helper container) was removed with cgprofile 2.0 package 2: ``cgprofilectl`` talks to
the daemon, or runs the collector directly where the host view exists
(``cgprofile.direct``).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from typing import Callable, Dict, List, Optional

CGROUP_ROOT = "/sys/fs/cgroup"
_ASCII_CONTROL_CHARACTERS = frozenset(chr(value) for value in range(32)) | {chr(127)}


def _has_whitespace_or_control(value: str) -> bool:
    return any(
        char.isspace() or char in _ASCII_CONTROL_CHARACTERS
        for char in value
    )


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
PID_NAMESPACE_MODE_ENV = "CGPROFILE_PID_NAMESPACE_MODE"
PID_NAMESPACE_MODES = frozenset(("private", "host"))

# Root-level cgroups that only exist in the host's own view. If none of these
# are visible we are looking at a namespaced subtree, whatever the mount says.
_HOST_MARKERS = ("init.scope", "system.slice", "user.slice")


class AccessError(RuntimeError):
    """Raised when the host view a caller requires cannot be established."""


def have_host_cgroup_view(root: str = CGROUP_ROOT) -> bool:
    """True when ``root`` is the host's cgroup v2 root rather than a subtree."""
    if not os.path.isdir(root):
        return False
    if not os.path.exists(os.path.join(root, "cgroup.controllers")):
        return False
    return any(os.path.exists(os.path.join(root, marker)) for marker in _HOST_MARKERS)


def have_host_proc_view(proc_root: str = PROC_ROOT) -> bool:
    """Require the configured proc view to match this process's PID namespace.

    A bind-mounted procfs still reports cgroup paths relative to the reader's
    cgroup namespace, so PID 1's ``/proc/<pid>/cgroup`` is not a sound host
    view check. This PID-namespace comparison is a consistency guard, not an
    independent proof that the selected procfs is the host's. The managed
    daemon's CIU Compose service supplies that deployment fact with
    ``pid: "host"`` and an explicit host-``/proc`` bind. The daemon explicitly
    uses ``CGPROFILE_PID_NAMESPACE_MODE=host`` so DAMON sysfs can resolve host
    ``pid_target`` values; the one-shot helper defaults to ``private`` and
    needs a broader proc view.
    Unknown modes fail closed.
    """
    try:
        selected_pid_ns = os.stat(os.path.join(proc_root, "1", "ns", "pid")).st_ino
        local_pid_ns = os.stat("/proc/1/ns/pid").st_ino
    except OSError:
        return False
    mode = os.environ.get(PID_NAMESPACE_MODE_ENV, "private")
    if mode not in PID_NAMESPACE_MODES:
        return False
    if mode == "host":
        return selected_pid_ns == local_pid_ns
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
    target = namespace_inode(pid, "cgroup", proc_root)
    current = local_namespace_inode("cgroup")
    return target is not None and target == current


def namespace_inode(pid: int, name: str, proc_root: str = PROC_ROOT) -> Optional[int]:
    """Return a process namespace's kernel inode from the selected proc view.

    Only PID and cgroup namespaces participate in target identity. Missing or
    unreadable proc metadata stays indeterminate so callers can refuse a
    translation instead of inferring an identity from a numeric PID.
    """
    if name not in ("pid", "cgroup") or pid <= 0:
        return None
    try:
        return os.stat(os.path.join(proc_root, str(pid), "ns", name)).st_ino
    except OSError:
        return None


def local_namespace_inode(name: str) -> Optional[int]:
    """Return this process's PID or cgroup namespace inode, when readable."""
    if name not in ("pid", "cgroup"):
        return None
    try:
        return os.stat(os.path.join("/proc/self/ns", name)).st_ino
    except OSError:
        return None


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


def verify_systemd_slice(
    unit_name: str,
    expected_cgroup: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Verify a named slice is loaded from an authored unit at its expected path.

    A cgroup directory alone is insufficient: systemd/Docker can create an
    unlimited transient slice for an unknown parent. This read-only check is
    deliberately fail-closed and never stats ``FragmentPath`` in this
    container's filesystem namespace; systemd's loaded-unit properties are
    authoritative for that host path.
    """
    if not isinstance(unit_name, str):
        return False
    if not unit_name.endswith(".slice"):
        return False
    if unit_name.startswith("-"):
        return False
    if unit_name != unit_name.strip() or "/" in unit_name:
        return False
    if _has_whitespace_or_control(unit_name):
        return False
    if not isinstance(expected_cgroup, str):
        return False
    if not expected_cgroup.startswith("/"):
        return False
    if _has_whitespace_or_control(expected_cgroup):
        return False
    if any(part in ("", ".", "..") for part in expected_cgroup.split("/")[1:]):
        return False
    # Query the host manager over its explicit system-bus socket.  busctl's
    # exact typed replies let this check verify manager-owned facts without
    # relying on local unit-file or cgroup-path views.
    busctl = shutil.which("busctl")
    if not busctl:
        return False

    def query(argv: List[str], signature: str) -> Optional[str]:
        try:
            result = run(
                [busctl, "--system", *argv],
                capture_output=True, text=True, check=False, timeout=5,
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        stdout = getattr(result, "stdout", None)
        if getattr(result, "returncode", 1) != 0:
            return None
        if not isinstance(stdout, str):
            return None
        try:
            fields = shlex.split(stdout)
        except ValueError:
            return None
        if len(fields) != 2:
            return None
        if fields[0] != signature:
            return None
        return fields[1]

    service = "org.freedesktop.systemd1"
    manager_path = "/org/freedesktop/systemd1"
    manager_interface = "org.freedesktop.systemd1.Manager"
    unit_path = query(
        ["call", service, manager_path, manager_interface, "GetUnit", "s", unit_name],
        "o",
    )
    if unit_path is None or not unit_path.startswith(manager_path + "/unit/"):
        return False
    properties: Dict[str, Optional[str]] = {}
    for interface, name in (
        ("org.freedesktop.systemd1.Unit", "LoadState"),
        ("org.freedesktop.systemd1.Unit", "FragmentPath"),
        ("org.freedesktop.systemd1.Slice", "ControlGroup"),
    ):
        properties[name] = query(
            ["get-property", service, unit_path, interface, name], "s"
        )

    fragment = properties["FragmentPath"]
    authored_fragment_roots = (
        "/etc/systemd/system",
        "/run/systemd/system",
        "/usr/local/lib/systemd/system",
        "/usr/lib/systemd/system",
        "/lib/systemd/system",
    )
    if (
        properties["LoadState"] != "loaded"
        or fragment is None
        or not any(
            fragment.startswith(root + os.sep) for root in authored_fragment_roots
        )
        or properties["ControlGroup"] != expected_cgroup
    ):
        return False
    return True


# ── docker plumbing ─────────────────────────────────────────────────────────

def docker_bin() -> Optional[str]:
    return shutil.which("docker")


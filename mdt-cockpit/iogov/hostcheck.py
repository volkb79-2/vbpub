"""`iogov preflight`: every host tool, package and kernel facility the install steps and the units rely on, checked BEFORE anything is installed.

Read-only: it looks files up (PATH search, sysfs/procfs/cgroup reads) and executes nothing. A missing item is reported with the Debian package
that provides it, and the command exits non-zero, so an operator learns about a missing `acl` before step 3 of the README instead of in the middle of it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from . import safeio
from .devices import detect

MIN_PYTHON = (3, 11)  # tomllib

# (name, executable, Debian package, what needs it). Tools are looked up in $PATH plus the sbin directories (a non-root PATH often lacks them);
# an explicit `path` argument (tests) replaces that search.
TOOLS = (
    ("setfacl", "setfacl", "acl", "udev rule: read-only ACL on the one disk node (README step 3)"),
    ("udevadm", "udevadm", "udev", "ID_PATH lookup, `udevadm test` / `trigger` (step 3)"),
    ("systemctl", "systemctl", "systemd", "daemon-reload, timers (steps 4-5)"),
    ("systemd-analyze", "systemd-analyze", "systemd", "`systemd-analyze verify` of the rendered units"),
    ("useradd", "useradd", "passwd", "the service user (step 1)"),
    ("install", "install", "coreutils", "directories with owner and mode (steps 1-2)"),
    ("dd", "dd", "coreutils", "the O_DIRECT read check as the service user (step 3)"),
)
SBIN = ("/usr/local/sbin", "/usr/sbin", "/sbin", "/usr/local/bin", "/usr/bin", "/bin")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    package: str | None = None  # Debian package to install when not ok
    required: bool = True
    purpose: str = ""


def find_executable(name: str, path: str | None = None) -> str | None:
    dirs = path.split(os.pathsep) if path is not None else os.environ.get("PATH", "").split(os.pathsep) + list(SBIN)
    for d in dirs:
        if not d:
            continue
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def run_checks(cfg, path: str | None = None, py_version: tuple = tuple(sys.version_info[:3])) -> list[Check]:
    out: list[Check] = []
    ok = tuple(py_version[:2]) >= MIN_PYTHON
    out.append(Check("python3", ok, ".".join(map(str, py_version[:3])) + ("" if ok else f" (need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})"),
                     None if ok else "python3", purpose="stdlib tomllib; the package needs nothing else"))
    for name, exe, package, purpose in TOOLS:
        found = find_executable(exe, path)
        out.append(Check(name, found is not None, found or "not found in PATH or the sbin directories", None if found else package,
                         purpose=purpose))
    controllers = safeio.read_text(os.path.join(cfg.cgroup_root, "cgroup.controllers"))
    if controllers is None:
        out.append(Check("cgroup v2", False, f"{cfg.cgroup_root}/cgroup.controllers is not readable (cgroup v1 or not mounted?)",
                         purpose="harm observations read io.stat / io.pressure"))
    else:
        has_io = "io" in controllers.split()
        out.append(Check("cgroup v2 io controller", has_io, "controllers: " + controllers.strip() if has_io else
                         "the io controller is not enabled at the root: " + controllers.strip(), purpose="io.stat / io.pressure of the cgroups"))
    psi = safeio.read_text(os.path.join(cfg.procfs_root, "pressure", "io"))
    out.append(Check("PSI (pressure/io)", psi is not None, f"{cfg.procfs_root}/pressure/io " + ("readable" if psi is not None else
                     "missing: kernel without CONFIG_PSI or booted with psi=0"), purpose="load gates and distress episodes"))
    out.append(Check("sysfs block", os.path.isdir(os.path.join(cfg.sysfs_root, "class", "block")), f"{cfg.sysfs_root}/class/block",
                     purpose="device detection and block statistics"))
    if cfg.containers:
        found = find_executable("docker", path)
        out.append(Check("docker", found is not None, found or "not found", None if found else "docker.io", required=False,
                         purpose="only to find the container cgroup paths by hand (`docker inspect`); the service itself does not use it"))
    if cfg.devices or cfg.data_paths:
        disks, notes = detect(cfg)
        detail = ", ".join(f"{d.kname} ({d.node})" for d in disks) if disks else "no disk resolved"
        out.append(Check("disks", bool(disks), detail + ("; " + " | ".join(notes) if notes else "")))
    else:
        out.append(Check("disks", True, "skipped: neither `devices` nor `data_paths` is configured yet (run `iogov devices` after editing the config)",
                         required=False))
    return out


def render(checks: list[Check]) -> tuple[str, int]:
    lines = ["iogov preflight (read-only: nothing is installed, created or executed)", ""]
    for c in checks:
        tag = "ok     " if c.ok else ("MISSING" if c.required else "absent ")
        lines.append(f"{tag} {c.name}: {c.detail}" + (f"   [apt: {c.package}]" if c.package and not c.ok else ""))
        if not c.ok and c.purpose:
            lines.append(f"        needed for: {c.purpose}")
    bad = [c for c in checks if not c.ok and c.required]
    packages = sorted({c.package for c in bad if c.package})
    lines.append("")
    if bad:
        lines.append(f"preflight FAILED: {len(bad)} required item(s) missing: " + ", ".join(c.name for c in bad))
        if packages:
            lines.append("Debian/Ubuntu packages to install first:  apt-get install " + " ".join(packages))
        return "\n".join(lines), 1
    lines.append("preflight OK")
    return "\n".join(lines), 0

#!/usr/bin/env python3
"""Run up to three bounded TrueNAS QEMU/TCG guests in the host background slice.

The VM runs in its own Docker container so TCG CPU use and guest memory do not
consume the interactive devcontainer's cgroup. The launcher refuses to start
unless it can verify the configured systemd slice and available memory budget
through a running host cgroup inspector.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import math
import os
import posixpath
import re
import selectors
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import termios
import tempfile
import time
import tty
from pathlib import Path, PurePosixPath
from typing import Any


VM_CPUS = 2.0
VM_NANO_CPUS = 2_000_000_000
VM_MEMORY_BYTES = 3_758_096_384  # 3.5 GiB, including QEMU and guest overhead.
VM_GUEST_MEMORY_MIB = 3072
DEFAULT_MAX_CONCURRENT_GUESTS = 3
MAX_CONCURRENT_GUESTS = 3
VM_LABEL = "org.vbpub.truenas.tcg-vm"
VM_LABEL_VALUE = "true"
STATE_LABEL = "org.vbpub.truenas.state-dir-sha256"
DEFAULT_SSH_PORT = 2222
DEFAULT_HTTPS_PORT = 8443
START_TIMEOUT_SECONDS = 20
STOP_TIMEOUT_SECONDS = 120
QMP_SOCKET_PATH_MAX_BYTES = 107
MAX_MEMORY_PSI_SOME_AVG10 = 10.0
MAX_MEMORY_PSI_FULL_AVG10 = 1.0
MAX_EXTRA_DISKS = 8


class RunnerError(RuntimeError):
    """A fail-closed VM runner preflight or lifecycle error."""


@contextlib.contextmanager
def qmp_socket_address(socket_path: Path):
    """Yield a usable local pathname, aliasing long bind-mount paths briefly."""
    if len(os.fsencode(socket_path)) <= QMP_SOCKET_PATH_MAX_BYTES:
        yield socket_path
        return

    try:
        temporary_dir = tempfile.TemporaryDirectory(prefix="tn-qmp-", dir="/tmp")
    except OSError as exc:
        raise RunnerError(f"cannot create a short QMP socket alias under /tmp: {exc}") from exc
    with temporary_dir:
        alias = Path(temporary_dir.name) / "qmp.sock"
        if len(os.fsencode(alias)) > QMP_SOCKET_PATH_MAX_BYTES:
            raise RunnerError(f"temporary QMP socket alias is unexpectedly long: {alias}")
        try:
            os.symlink(socket_path, alias)
        except OSError as exc:
            raise RunnerError(f"cannot create a short QMP socket alias for {socket_path}: {exc}") from exc
        yield alias


def prepare_unix_socket(socket_path: Path, purpose: str) -> None:
    """Remove only a stale Unix socket and refuse to replace a live or other path."""
    try:
        mode = socket_path.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RunnerError(f"cannot inspect {purpose} socket path {socket_path}: {exc}") from exc
    if not stat.S_ISSOCK(mode):
        raise RunnerError(f"refusing to replace non-socket {purpose} path {socket_path}")
    try:
        with qmp_socket_address(socket_path) as address:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.5)
                client.connect(str(address))
    except OSError as exc:
        if exc.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
            raise RunnerError(
                f"cannot determine whether {purpose} socket is active at {socket_path}: {exc}"
            ) from exc
    else:
        raise RunnerError(f"{purpose} socket already accepts connections: {socket_path}")
    try:
        socket_path.unlink()
    except OSError as exc:
        raise RunnerError(f"cannot remove stale {purpose} socket {socket_path}: {exc}") from exc


def unix_socket_client_path(state_dir: Path, socket_name: str, alias_prefix: str) -> Path:
    """Return a short host path that reaches a socket in the state directory."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.sock", socket_name):
        raise RunnerError(f"invalid Unix socket basename {socket_name!r}")
    resolved_state = state_dir.resolve(strict=True)
    socket_path = resolved_state / socket_name
    if len(os.fsencode(socket_path)) <= QMP_SOCKET_PATH_MAX_BYTES:
        return socket_path
    digest = hashlib.sha256(os.fsencode(str(resolved_state))).hexdigest()[:16]
    alias = Path(tempfile.gettempdir()) / f"truenas-{alias_prefix}-{digest}.sock"
    try:
        alias_mode = alias.lstat().st_mode
    except FileNotFoundError:
        alias_mode = None
    except OSError as exc:
        raise RunnerError(f"cannot inspect {alias_prefix} socket alias {alias}: {exc}") from exc
    if alias_mode is not None:
        if not stat.S_ISLNK(alias_mode) or os.readlink(alias) != str(socket_path):
            raise RunnerError(f"{alias_prefix} socket alias path is occupied by another object: {alias}")
        return alias
    try:
        os.symlink(socket_path, alias)
    except OSError as exc:
        raise RunnerError(f"cannot create short {alias_prefix} socket alias {alias}: {exc}") from exc
    return alias


def prepare_vnc_socket(socket_path: Path) -> None:
    """Remove only a stale VNC socket and refuse to replace a live or other path."""
    prepare_unix_socket(socket_path, "VNC")


def prepare_serial_socket(socket_path: Path) -> None:
    """Remove only a stale serial socket and refuse to replace a live or other path."""
    prepare_unix_socket(socket_path, "serial")


def vnc_socket_client_path(state_dir: Path) -> Path:
    """Return a short host path to the VNC socket in the bind-mounted state directory."""
    return unix_socket_client_path(state_dir, "vnc.sock", "vnc")


def serial_socket_client_path(state_dir: Path) -> Path:
    """Return a short host path to the serial socket in the bind-mounted state directory."""
    return unix_socket_client_path(state_dir, "serial.sock", "serial")


def parse_cpu_max(value: str) -> float | None:
    """Return the cgroup CPU quota in cores, or None for an unlimited quota."""
    parts = value.split()
    if len(parts) != 2:
        raise ValueError(f"malformed cpu.max value: {value!r}")
    if parts[0] == "max":
        return None
    quota, period = (int(part) for part in parts)
    if quota <= 0 or period <= 0:
        raise ValueError(f"invalid cpu.max value: {value!r}")
    return quota / period


def parse_memory_limit(value: str) -> int | None:
    """Return a cgroup memory limit in bytes, or None when it is unlimited."""
    value = value.strip()
    if value == "max":
        return None
    limit = int(value)
    if limit <= 0:
        raise ValueError(f"invalid memory limit: {value!r}")
    return limit


def parse_memory_psi(raw: str) -> tuple[float, float]:
    """Return memory PSI some/full avg10 percentages from cgroup v2 pressure."""
    values: dict[str, float] = {}
    for line in raw.splitlines():
        parts = line.split()
        if not parts or parts[0] not in {"some", "full"}:
            continue
        fields = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        if "avg10" not in fields:
            continue
        try:
            average = float(fields["avg10"])
        except ValueError as exc:
            raise ValueError(f"malformed memory PSI avg10 value: {fields['avg10']!r}") from exc
        if not math.isfinite(average) or average < 0:
            raise ValueError(f"invalid memory PSI avg10 value: {fields['avg10']!r}")
        values[parts[0]] = average
    if set(values) != {"some", "full"}:
        raise ValueError(f"memory PSI is missing some/full avg10 values: {raw!r}")
    return values["some"], values["full"]


def positive_finite_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def validate_slice_name(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]+\.slice", value):
        raise RunnerError(f"invalid cgroup slice name {value!r}; expected a systemd .slice unit")


def validate_max_concurrent_guests(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CONCURRENT_GUESTS:
        raise RunnerError(
            f"max concurrent guests must be between 1 and {MAX_CONCURRENT_GUESTS}"
        )


def validate_active_guest_count(active_names: list[str], max_concurrent_guests: int) -> None:
    validate_max_concurrent_guests(max_concurrent_guests)
    if len(active_names) >= max_concurrent_guests:
        active = ", ".join(active_names) or "none reported"
        raise RunnerError(
            f"max concurrent guest limit {max_concurrent_guests} reached; active runner(s): {active}"
        )


def vm_start_lock_path() -> Path:
    project = Path(__file__).resolve().parent
    result = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RunnerError(f"cannot resolve the shared Git directory for VM admission: {result.stderr.strip()}")
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = project / common_dir
    try:
        common_dir = common_dir.resolve(strict=True)
    except OSError as exc:
        raise RunnerError(f"cannot resolve the shared Git directory for VM admission: {exc}") from exc
    if not common_dir.is_dir() or not os.access(common_dir, os.W_OK | os.X_OK):
        raise RunnerError(f"shared Git directory is not writable for VM admission: {common_dir}")
    return common_dir / "truenas-tcg-vm-admission.lock"


@contextlib.contextmanager
def vm_start_admission_lock():
    """Serialize admission checks and container creation across this repository's worktrees."""
    lock_path = vm_start_lock_path()
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RunnerError(f"cannot open VM admission lock {lock_path}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RunnerError(f"VM admission lock is not a regular file: {lock_path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunnerError("another tcg_vm.py start is in progress; retry after it finishes") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def mount_source_for_path(
    namespace_path: Path,
    mounts: list[dict[str, Any]],
    *,
    require_writable: bool,
) -> str:
    """Map a local container path through Docker's authoritative mount table."""
    target = PurePosixPath(str(namespace_path))
    matches: list[tuple[int, PurePosixPath, str, bool]] = []
    for mount in mounts:
        destination = PurePosixPath(str(mount.get("Destination", "")))
        source = str(mount.get("Source", ""))
        if not destination.is_absolute() or not source.startswith("/"):
            continue
        try:
            relative = target.relative_to(destination)
        except ValueError:
            continue
        if require_writable and not mount.get("RW", False):
            continue
        matches.append((len(destination.parts), destination, source, bool(mount.get("RW", False))))
    if not matches:
        access = "writable " if require_writable else ""
        raise RunnerError(f"no {access}Docker mount contains {namespace_path}; refusing a namespace-wrong bind")
    _, destination, source, _ = max(matches, key=lambda entry: entry[0])
    relative = target.relative_to(destination)
    host_path = PurePosixPath(source) / relative
    return str(host_path)


def validate_memory_headroom(
    levels: list[dict[str, int | float | None]],
    *,
    requested_cpus: float = VM_CPUS,
    max_memory_psi_full_avg10: float = MAX_MEMORY_PSI_FULL_AVG10,
) -> None:
    """Use live PSI and hard limits for admission; memory.high and VM caps may overcommit."""
    if not levels:
        raise RunnerError("host cgroup inspector returned no resource levels")
    target = levels[0]
    if target.get("memory_max") is None:
        raise RunnerError("selected background slice has no finite memory.max limit")
    available_cpus: list[float] = []
    for level in levels:
        raw_current = level.get("memory_current")
        if raw_current is None and level.get("memory_max") is not None:
            raise RunnerError(f"cannot read memory.current at finite cgroup {level['path']}")
        current = int(raw_current or 0)
        limit = level.get("memory_max")
        if limit is not None and current >= int(limit):
            raise RunnerError(
                f"memory.current {current} has reached hard memory.max {limit} at {level['path']}"
            )
        if limit is not None:
            some = level.get("memory_psi_some_avg10")
            full = level.get("memory_psi_full_avg10")
            if some is None or full is None:
                raise RunnerError(f"cannot read memory PSI at finite cgroup {level['path']}")
            if float(some) >= MAX_MEMORY_PSI_SOME_AVG10:
                raise RunnerError(
                    f"memory PSI some avg10 {some:g}% at {level['path']} meets or exceeds "
                    f"the {MAX_MEMORY_PSI_SOME_AVG10:g}% admission limit"
                )
            if float(full) >= max_memory_psi_full_avg10:
                raise RunnerError(
                    f"memory PSI full avg10 {full:g}% at {level['path']} meets or exceeds "
                    f"the {max_memory_psi_full_avg10:g}% admission limit"
                )
        cpu_limit = level.get("cpu_max")
        if cpu_limit is not None:
            available_cpus.append(float(cpu_limit))
    if available_cpus and min(available_cpus) < requested_cpus:
        raise RunnerError(
            f"VM requires {requested_cpus:g} CPUs but the tightest cgroup quota is {min(available_cpus):g}"
        )


def docker_run(argv: list[str], *, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"command failed to run ({shlex.join(argv)}): {exc}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RunnerError(f"command failed ({result.returncode}): {shlex.join(argv)}\n{detail}")
    return result


def docker_inspect(name: str) -> dict[str, Any]:
    result = docker_run(["docker", "inspect", name, "--format", "{{json .}}"], check=False)
    if result.returncode:
        raise RunnerError(f"Docker container {name!r} is not available: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"Docker returned malformed inspect data for {name!r}") from exc


def inspect_current_container() -> tuple[str, dict[str, Any]]:
    hostname = Path("/etc/hostname").read_text().strip()
    ids = docker_run(["docker", "ps", "-q", "--no-trunc"]).stdout.splitlines()
    matches: list[tuple[str, dict[str, Any]]] = []
    for container_id in ids:
        item = docker_inspect(container_id)
        if item.get("Config", {}).get("Hostname") == hostname:
            matches.append((container_id, item))
    if len(matches) != 1:
        raise RunnerError(
            f"expected one Docker container for cockpit hostname {hostname!r}; found {len(matches)}"
        )
    return matches[0]


def resolve_docker_path(path: Path, mounts: list[dict[str, Any]], *, writable: bool) -> str:
    resolved = path.resolve(strict=True)
    return mount_source_for_path(resolved, mounts, require_writable=writable)


def find_cgroup_inspector() -> str:
    ids = docker_run(["docker", "ps", "-q", "--no-trunc"]).stdout.splitlines()
    matches: list[str] = []
    for container_id in ids:
        inspected = docker_inspect(container_id)
        mounts = inspected.get("Mounts", [])
        has_cgroupfs = any(
            mount.get("Source") == "/sys/fs/cgroup"
            and mount.get("Destination") == "/sys/fs/cgroup"
            for mount in mounts
        )
        has_host_bus = any(
            mount.get("Source") == "/run/dbus/system_bus_socket"
            and mount.get("Destination") == "/run/dbus/system_bus_socket"
            for mount in mounts
        )
        if has_cgroupfs and has_host_bus:
            matches.append(container_id)
    if len(matches) != 1:
        raise RunnerError(
            "expected one running Docker container with host /sys/fs/cgroup and system D-Bus mounts; "
            f"found {len(matches)}. Start the host cgroup inspector before launching a VM."
        )
    return matches[0]


def inspector_exec(inspector: str, argv: list[str]) -> str:
    return docker_run(["docker", "exec", inspector, *argv]).stdout.strip()


def busctl_value(inspector: str, path: str, interface: str, prop: str) -> str:
    raw = inspector_exec(
        inspector,
        [
            "busctl",
            "--address=unix:path=/run/dbus/system_bus_socket",
            "get-property",
            "org.freedesktop.systemd1",
            path,
            interface,
            prop,
        ],
    )
    match = re.fullmatch(r'[stbxoay]\s+(?:"([^"]*)"|([0-9]+|true|false))', raw)
    if not match:
        raise RunnerError(f"could not parse host systemd property {interface}.{prop}: {raw!r}")
    return match.group(1) if match.group(1) is not None else match.group(2)


def systemd_slice_path(inspector: str, slice_name: str) -> str:
    validate_slice_name(slice_name)
    output = inspector_exec(
        inspector,
        [
            "busctl",
            "--address=unix:path=/run/dbus/system_bus_socket",
            "call",
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            "GetUnit",
            "s",
            slice_name,
        ],
    )
    match = re.fullmatch(r'o\s+"([^"]+)"', output)
    if not match:
        raise RunnerError(f"systemd did not return an object path for {slice_name}: {output!r}")
    object_path = match.group(1)
    load_state = busctl_value(inspector, object_path, "org.freedesktop.systemd1.Unit", "LoadState")
    if load_state != "loaded":
        raise RunnerError(f"systemd slice {slice_name} has LoadState={load_state!r}; refusing auto-created slices")
    control_group = busctl_value(inspector, object_path, "org.freedesktop.systemd1.Slice", "ControlGroup")
    if not control_group.startswith("/") or ".." in PurePosixPath(control_group).parts:
        raise RunnerError(f"systemd returned an unsafe cgroup path for {slice_name}: {control_group!r}")
    return control_group


def cgroup_file(inspector: str, control_group: str, name: str) -> str | None:
    path = posixpath.normpath(posixpath.join("/sys/fs/cgroup", control_group.lstrip("/"), name))
    result = docker_run(["docker", "exec", inspector, "cat", path], check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    if "No such file or directory" in result.stderr:
        return None
    raise RunnerError(f"cannot read host cgroup file {path}: {result.stderr.strip()}")


def cgroup_levels(inspector: str, control_group: str) -> list[dict[str, int | float | None]]:
    levels: list[dict[str, int | float | None]] = []
    current_path = control_group
    while True:
        cpu_raw = cgroup_file(inspector, current_path, "cpu.max")
        high_raw = cgroup_file(inspector, current_path, "memory.high")
        max_raw = cgroup_file(inspector, current_path, "memory.max")
        current_raw = cgroup_file(inspector, current_path, "memory.current")
        psi_raw = cgroup_file(inspector, current_path, "memory.pressure")
        psi_some, psi_full = parse_memory_psi(psi_raw) if psi_raw is not None else (None, None)
        levels.append(
            {
                "path": current_path,
                "cpu_max": parse_cpu_max(cpu_raw) if cpu_raw is not None else None,
                "memory_high": parse_memory_limit(high_raw) if high_raw is not None else None,
                "memory_max": parse_memory_limit(max_raw) if max_raw is not None else None,
                "memory_current": int(current_raw) if current_raw is not None else None,
                "memory_psi_some_avg10": psi_some,
                "memory_psi_full_avg10": psi_full,
            }
        )
        if current_path == "/":
            break
        current_path = posixpath.dirname(current_path.rstrip("/")) or "/"
    return levels


def cgroup_preflight(
    slice_name: str,
    *,
    max_memory_psi_full_avg10: float = MAX_MEMORY_PSI_FULL_AVG10,
) -> tuple[str, list[dict[str, int | float | None]], str]:
    inspector = find_cgroup_inspector()
    control_group = systemd_slice_path(inspector, slice_name)
    levels = cgroup_levels(inspector, control_group)
    validate_memory_headroom(levels, max_memory_psi_full_avg10=max_memory_psi_full_avg10)
    return inspector, levels, control_group


def host_pid_cgroup(inspector: str, pid: int) -> str:
    raw = inspector_exec(inspector, ["cat", f"/hostproc/{pid}/cgroup"])
    return parse_host_cgroup(raw)


def parse_host_cgroup(raw: str) -> str:
    unified = [line.split(":", 2)[2] for line in raw.splitlines() if line.startswith("0::")]
    if len(unified) != 1:
        raise RunnerError(f"could not determine a unique unified host cgroup: {raw!r}")
    normalized = posixpath.normpath(unified[0])
    if not normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
        raise RunnerError(f"host process reported an invalid cgroup path: {unified[0]!r}")
    return normalized


def verify_running_container(
    name: str,
    state_dir_hash: str,
    network: str,
    cgroup_parent: str,
    inspector: str,
    expected_cgroup_path: str,
) -> dict[str, Any]:
    item = docker_inspect(name)
    config = item.get("Config", {})
    labels = config.get("Labels") or {}
    if labels.get(VM_LABEL) != VM_LABEL_VALUE or labels.get(STATE_LABEL) != state_dir_hash:
        raise RunnerError(f"container {name!r} does not carry this TrueNAS runner's identity labels")
    host_config = item.get("HostConfig", {})
    if host_config.get("CgroupParent", "").split("/")[-1] != cgroup_parent:
        raise RunnerError(f"container {name!r} has unexpected CgroupParent {host_config.get('CgroupParent')!r}")
    if host_config.get("NanoCpus") != VM_NANO_CPUS:
        raise RunnerError(f"container {name!r} has unexpected CPU cap {host_config.get('NanoCpus')!r}")
    if host_config.get("Memory") != VM_MEMORY_BYTES or host_config.get("MemorySwap") != VM_MEMORY_BYTES:
        raise RunnerError("Docker did not apply the requested 3.5 GiB memory and no-swap limits")
    if network not in (item.get("NetworkSettings", {}).get("Networks") or {}):
        raise RunnerError(f"container {name!r} is not attached to required network {network!r}")
    if not item.get("State", {}).get("Running"):
        raise RunnerError(f"container {name!r} exited during startup; inspect with `docker logs {name}`")
    pid = int(item["State"]["Pid"])
    actual_cgroup = host_pid_cgroup(inspector, pid)
    if actual_cgroup != expected_cgroup_path and not actual_cgroup.startswith(expected_cgroup_path.rstrip("/") + "/"):
        raise RunnerError(
            f"container host PID {pid} is in {actual_cgroup!r}, outside selected slice {expected_cgroup_path!r}"
        )
    leaf = cgroup_levels(inspector, actual_cgroup)[0]
    if leaf.get("memory_max") != VM_MEMORY_BYTES or leaf.get("cpu_max") is None or abs(float(leaf["cpu_max"]) - VM_CPUS) > 0.01:
        raise RunnerError(f"host cgroup read-back does not match requested caps: {leaf}")
    return {
        "container": name,
        "host_pid": pid,
        "host_cgroup": actual_cgroup,
        "cpu_cores": float(leaf["cpu_max"]),
        "memory_max_bytes": int(leaf["memory_max"]),
        "memory_swap_bytes": host_config.get("MemorySwap"),
        "guest_memory_mib": VM_GUEST_MEMORY_MIB,
        "network": network,
        "cgroup_parent": cgroup_parent,
    }


def qmp_command(
    socket_path: Path,
    command: str,
    arguments: dict[str, Any] | None = None,
    timeout: float = 3.0,
) -> Any:
    with qmp_socket_address(socket_path) as address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(address))
            stream = client.makefile("rwb", buffering=0)
            greeting = json.loads(stream.readline())
            if "QMP" not in greeting:
                raise RunnerError(f"invalid QMP greeting: {greeting!r}")
            stream.write(b'{"execute":"qmp_capabilities"}\r\n')
            while True:
                response = json.loads(stream.readline())
                if "return" in response:
                    break
                if "error" in response:
                    raise RunnerError(f"QMP capabilities failed: {response['error']!r}")
            request: dict[str, Any] = {"execute": command}
            if arguments is not None:
                request["arguments"] = arguments
            stream.write((json.dumps(request) + "\r\n").encode())
            while True:
                response = json.loads(stream.readline())
                if "return" in response:
                    return response["return"]
                if "error" in response:
                    raise RunnerError(f"QMP {command} failed: {response['error']!r}")


def qmp_human_monitor_command(socket_path: Path, command_line: str, timeout: float = 3.0) -> str:
    """Run one QEMU HMP command through QMP and return its text response."""
    result = qmp_command(
        socket_path,
        "human-monitor-command",
        {"command-line": command_line},
        timeout,
    )
    if not isinstance(result, str):
        raise RunnerError(f"QMP human-monitor-command returned an unexpected result: {result!r}")
    return result


def validate_console_guest(name: str, state_dir: Path) -> Path:
    validate_name(name)
    resolved_state = state_dir.resolve(strict=True)
    item = docker_inspect(name)
    labels = item.get("Config", {}).get("Labels") or {}
    if labels.get(VM_LABEL) != VM_LABEL_VALUE:
        raise RunnerError(f"container {name!r} is not a managed TrueNAS TCG VM")
    if labels.get(STATE_LABEL) != state_identity(resolved_state):
        raise RunnerError(f"container {name!r} does not belong to state directory {resolved_state}")
    if not item.get("State", {}).get("Running"):
        raise RunnerError(f"container {name!r} is not running")
    return resolved_state


def cmd_console_screendump(args: argparse.Namespace) -> int:
    state_dir = validate_console_guest(args.name, Path(args.state_dir))
    filename = args.filename
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", filename) or filename in {".", ".."}:
        raise RunnerError("screendump filename must be a simple filename within the VM state directory")
    output = state_dir / filename
    if output.exists() or output.is_symlink():
        raise RunnerError(f"refusing to overwrite existing screendump {output}")
    result = qmp_human_monitor_command(state_dir / "qmp.sock", f"screendump /state/{filename}")
    if not output.is_file():
        raise RunnerError(f"QEMU did not create the requested screendump {output}: {result.strip()}")
    print(f"[screendump] file={output} response={result.strip() or 'ok'}", flush=True)
    return 0


def cmd_console_sendkey(args: argparse.Namespace) -> int:
    state_dir = validate_console_guest(args.name, Path(args.state_dir))
    if len(args.key) > 96 or not re.fullmatch(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*", args.key):
        raise RunnerError("key must be a QEMU sendkey name or hyphen-separated key combination")
    result = qmp_human_monitor_command(state_dir / "qmp.sock", f"sendkey {args.key}")
    print(f"[sendkey] key={args.key} response={result.strip() or 'ok'}", flush=True)
    return 0


def relay_serial_console(client: socket.socket) -> None:
    """Relay an interactive terminal to QEMU's bidirectional serial chardev."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RunnerError("serial console needs an interactive terminal on stdin and stdout")

    input_fd = sys.stdin.fileno()
    output_fd = sys.stdout.fileno()
    original_terminal = termios.tcgetattr(input_fd)
    selector = selectors.DefaultSelector()

    try:
        tty.setraw(input_fd)
        selector.register(input_fd, selectors.EVENT_READ, "terminal")
        selector.register(client, selectors.EVENT_READ, "serial")
        while True:
            for key, _events in selector.select():
                if key.data == "terminal":
                    data = os.read(input_fd, 4096)
                    if not data:
                        return
                    escape = data.find(b"\x1d")
                    if escape >= 0:
                        if escape:
                            client.sendall(data[:escape])
                        return
                    client.sendall(data)
                else:
                    data = client.recv(4096)
                    if not data:
                        return
                    view = memoryview(data)
                    while view:
                        written = os.write(output_fd, view)
                        view = view[written:]
    except KeyboardInterrupt:
        return
    finally:
        selector.close()
        termios.tcsetattr(input_fd, termios.TCSADRAIN, original_terminal)


def cmd_console_serial(args: argparse.Namespace) -> int:
    state_dir = validate_console_guest(args.name, Path(args.state_dir))
    socket_path = state_dir / "serial.sock"
    try:
        mode = socket_path.lstat().st_mode
    except OSError as exc:
        raise RunnerError(
            f"serial socket is unavailable at {socket_path}; start or resume with --serial-console"
        ) from exc
    if not stat.S_ISSOCK(mode):
        raise RunnerError(f"refusing to connect to non-socket serial path {socket_path}")

    client_path = serial_socket_client_path(state_dir)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(client_path))
    except OSError as exc:
        client.close()
        raise RunnerError(f"cannot connect to guest serial socket {client_path}: {exc}") from exc

    print(
        f"[serial] connected to {client_path}; press Ctrl-] to disconnect",
        file=sys.stderr,
        flush=True,
    )
    with client:
        relay_serial_console(client)
    return 0


def cleanup_failed_start(name: str, state_dir: Path, timeout: int = STOP_TIMEOUT_SECONDS) -> str:
    """Request graceful shutdown after a failed post-start check; never force-kill."""
    inspected = docker_run(["docker", "inspect", name, "--format", "{{json .}}"], check=False)
    if inspected.returncode:
        return "container is already absent"
    try:
        item = json.loads(inspected.stdout)
    except json.JSONDecodeError:
        return "container remains for inspection; Docker inspect output was malformed"
    if not item.get("State", {}).get("Running"):
        docker_run(["docker", "rm", name], check=False)
        return "stopped container removed; state files preserved"
    socket_path = state_dir / "qmp.sock"
    if not socket_path.exists():
        return f"container {name} remains running because no QMP socket is available"
    try:
        qmp_command(socket_path, "system_powerdown")
    except (OSError, RunnerError, ValueError) as exc:
        return f"container {name} remains running; graceful QMP shutdown failed: {exc}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inspected = docker_run(["docker", "inspect", name, "--format", "{{json .State}}"], check=False)
        if inspected.returncode == 0:
            try:
                if not json.loads(inspected.stdout).get("Running"):
                    docker_run(["docker", "rm", name], check=False)
                    return "guest shut down gracefully; stopped container removed; state files preserved"
            except json.JSONDecodeError:
                pass
        time.sleep(1)
    return f"container {name} remains running after {timeout}s; it was not force-stopped"


def validate_name(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise RunnerError(f"invalid Docker VM name {value!r}")


def validate_port(value: int, label: str) -> None:
    if not 1 <= value <= 65535:
        raise RunnerError(f"{label} must be between 1 and 65535")


def parse_extra_disk_size(value: str) -> int:
    """Parse a positive byte count or binary K/M/G/T size for a sparse test disk."""
    match = re.fullmatch(r"([1-9][0-9]*)([KMGT]?)", value.upper())
    if match is None:
        raise RunnerError(
            f"invalid extra disk size {value!r}; use a positive byte count or K/M/G/T suffix"
        )
    multiplier = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[match.group(2)]
    size = int(match.group(1)) * multiplier
    if size > (2**63 - 1):
        raise RunnerError(f"extra disk size {value!r} exceeds the supported signed 64-bit byte range")
    return size


def extra_disk_sizes_bytes(values: list[str]) -> list[int]:
    if len(values) > MAX_EXTRA_DISKS:
        raise RunnerError(f"at most {MAX_EXTRA_DISKS} extra disks can be attached to one guest")
    return [parse_extra_disk_size(value) for value in values]


def inspect_network(name: str) -> None:
    docker_run(["docker", "network", "inspect", name])


def image_exists(name: str) -> None:
    docker_run(["docker", "image", "inspect", name])


def image_id(name: str) -> str:
    return docker_run(["docker", "image", "inspect", name, "--format", "{{.Id}}"]).stdout.strip()


def container_labels(name: str) -> dict[str, str]:
    item = docker_inspect(name)
    return item.get("Config", {}).get("Labels") or {}


def state_identity(path: Path) -> str:
    return hashlib.sha256(str(path.resolve(strict=True)).encode()).hexdigest()


def runner_manifest(
    *,
    name: str,
    base_disk: Path,
    firmware_code: Path,
    firmware_vars: Path,
    image_id: str,
    network: str,
    cgroup_parent: str,
    ssh_port: int,
    https_port: int,
    readonly_media: Path | None = None,
    extra_disk_sizes: list[int] | None = None,
) -> dict[str, Any]:
    stat = base_disk.stat()
    manifest = {
        "schema": 1,
        "name": name,
        "base_disk": str(base_disk),
        "base_identity": {
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
        "firmware_code": file_identity(firmware_code),
        "firmware_vars_template": file_identity(firmware_vars),
        "image_id": image_id,
        "network": network,
        "cgroup_parent": cgroup_parent,
        "ssh_port": ssh_port,
        "https_port": https_port,
        "cpu_nano_cpus": VM_NANO_CPUS,
        "memory_bytes": VM_MEMORY_BYTES,
        "memory_swap_bytes": VM_MEMORY_BYTES,
        "guest_memory_mib": VM_GUEST_MEMORY_MIB,
    }
    if readonly_media is not None:
        manifest["readonly_media"] = file_identity(readonly_media)
    if extra_disk_sizes:
        manifest["extra_disk_sizes_bytes"] = list(extra_disk_sizes)
    return manifest


def file_identity(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def check_runner_manifest(
    path: Path,
    expected: dict[str, Any],
    *,
    allow_readonly_media_addition: bool = False,
) -> None:
    try:
        actual = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"cannot read runner identity file {path}: {exc}") from exc
    if actual == expected:
        return

    comparable_actual = dict(actual)
    comparable_expected = dict(expected)
    if (
        allow_readonly_media_addition
        and "readonly_media" in comparable_expected
        and "readonly_media" not in comparable_actual
    ):
        comparable_expected.pop("readonly_media")

    firmware_device_changed = False
    for field in ("firmware_code", "firmware_vars_template"):
        actual_identity = comparable_actual.get(field)
        expected_identity = comparable_expected.get(field)
        if not isinstance(actual_identity, dict) or not isinstance(expected_identity, dict):
            continue
        if set(actual_identity) != set(expected_identity):
            continue
        if type(actual_identity.get("device")) is not int or type(expected_identity.get("device")) is not int:
            continue
        actual_without_device = {
            key: value for key, value in actual_identity.items() if key != "device"
        }
        expected_without_device = {
            key: value for key, value in expected_identity.items() if key != "device"
        }
        if actual_without_device != expected_without_device:
            continue
        firmware_device_changed |= actual_identity["device"] != expected_identity["device"]
        normalized_identity = dict(actual_identity)
        normalized_identity["device"] = expected_identity["device"]
        comparable_actual[field] = normalized_identity

    if comparable_actual == comparable_expected:
        if firmware_device_changed:
            print(
                "[resume] OVMF source device number changed; path, inode, size, "
                "mtime, and SHA-256 still match",
                flush=True,
            )
        return
    raise RunnerError(
        f"resume identity differs from the existing guest in {path}; "
        "keep its original base image, QEMU image, network, slice, and ports"
    )


def write_runner_manifest(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def rebind_runner_readonly_media(
    manifest_path: Path,
    *,
    name: str,
    readonly_media: Path,
) -> tuple[str, str]:
    """Change a stopped guest's CD-ROM path only when its ISO bytes are identical."""
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"cannot read runner identity file {manifest_path}: {exc}") from exc
    if manifest.get("schema") != 1 or manifest.get("name") != name:
        raise RunnerError(f"runner identity in {manifest_path} does not belong to {name!r}")
    previous = manifest.get("readonly_media")
    if not isinstance(previous, dict) or not previous.get("path"):
        raise RunnerError(f"runner identity in {manifest_path} has no read-only media to rebind")

    old_path = Path(previous["path"])
    try:
        old_path = old_path.resolve(strict=True)
    except OSError as exc:
        raise RunnerError(f"recorded read-only media is unavailable at {old_path}: {exc}") from exc
    try:
        old_stat = old_path.stat()
    except OSError as exc:
        raise RunnerError(f"cannot inspect recorded read-only media {old_path}: {exc}") from exc
    for field, value in {
        "path": str(old_path),
        "device": old_stat.st_dev,
        "inode": old_stat.st_ino,
        "size": old_stat.st_size,
        "mtime_ns": old_stat.st_mtime_ns,
    }.items():
        if previous.get(field) != value:
            raise RunnerError(f"recorded read-only media identity changed at {old_path} ({field})")

    new_path = readonly_media.resolve(strict=True)
    if not new_path.is_file():
        raise RunnerError(f"read-only media is not a regular file: {new_path}")
    new_identity = file_identity(new_path)
    shares_inode = (old_stat.st_dev, old_stat.st_ino) == (
        new_identity["device"], new_identity["inode"]
    )
    if not shares_inode and file_identity(old_path) != previous:
        raise RunnerError(f"recorded read-only media contents changed at {old_path}")
    if previous.get("size") != new_identity["size"] or previous.get("sha256") != new_identity["sha256"]:
        raise RunnerError(
            "new read-only media does not contain the exact same bytes as the recorded ISO"
        )

    old_value = previous["path"]
    manifest["readonly_media"] = new_identity
    write_runner_manifest(manifest_path, manifest)
    return old_value, str(new_path)


def host_command_script() -> str:
    return r'''set -eu
command -v qemu-system-x86_64 >/dev/null
command -v qemu-img >/dev/null
test -s /state/OVMF_CODE_4M.fd
test -s /state/OVMF_VARS_4M.fd
if [ "$VM_MODE" = "create" ]; then
  test ! -e /state/disk-overlay.qcow2
  qemu-img create -q -f qcow2 -F qcow2 -b /base/disk.qcow2 /state/disk-overlay.qcow2
else
  test -s /state/disk-overlay.qcow2
  test -s /state/OVMF_VARS_4M.fd
fi
set --
extra_sizes=${VM_EXTRA_DISK_SIZES_BYTES:-}
if [ -n "$extra_sizes" ]; then
  old_ifs=$IFS
  IFS=,
  index=0
  for size in $extra_sizes; do
    case "$size" in
      ''|*[!0-9]*) echo "invalid extra disk size in VM_EXTRA_DISK_SIZES_BYTES" >&2; exit 2 ;;
    esac
    index=$((index + 1))
    if [ "$index" -gt 8 ]; then
      echo "too many extra disks in VM_EXTRA_DISK_SIZES_BYTES" >&2
      exit 2
    fi
    disk="/state/data-disk-${index}.qcow2"
    if [ "$VM_MODE" = "create" ]; then
      test ! -e "$disk"
      qemu-img create -q -f qcow2 -o preallocation=off "$disk" "$size"
    else
      test -s "$disk"
      info=$(qemu-img info --output=json "$disk" | tr -d '[:space:]')
      printf '%s' "$info" | grep -Fq "\"virtual-size\":$size"
    fi
    # OVMF boot entries in the base image name the primary boot disk's PCI
    # device path. Give test disks fixed higher slots so they cannot take that
    # path and strand the guest in the EFI shell when sparse disks are added.
    pci_addr=$(printf '0x%x' "$((7 + index))")
    set -- "$@" \
      -drive "file=$disk,if=none,id=truenasdata${index},format=qcow2,cache=writeback" \
      -device "virtio-blk-pci,drive=truenasdata${index},serial=TNEXTRA$(printf '%03d' "$index"),addr=$pci_addr"
  done
  IFS=$old_ifs
fi
if [ -n "${VM_READONLY_MEDIA:-}" ]; then
  test -s "$VM_READONLY_MEDIA"
  set -- "$@" -cdrom "$VM_READONLY_MEDIA"
fi
if [ "${VM_VNC_CONSOLE:-0}" = "1" ]; then
  set -- "$@" -vnc unix:/state/vnc.sock
else
  set -- "$@" -display none
fi
if [ "${VM_SERIAL_CONSOLE:-0}" = "1" ]; then
  set -- "$@" -chardev socket,id=serial0,path=/state/serial.sock,server=on,wait=off,logfile=/state/serial.log,logappend=on -serial chardev:serial0
else
  set -- "$@" -serial file:/state/serial.log
fi
umask 077
exec qemu-system-x86_64 \
  -name "$VM_NAME" \
  -machine q35,accel=tcg \
  -cpu max,-vmx,-svm \
  -smp 2 \
  -m 3072 \
  -rtc base=utc,clock=host \
  -drive if=pflash,format=raw,readonly=on,file=/state/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=/state/OVMF_VARS_4M.fd \
  -drive file=/state/disk-overlay.qcow2,if=virtio,format=qcow2,cache=writeback \
  -netdev "user,id=net0,hostfwd=tcp:0.0.0.0:${VM_SSH_PORT}-:22,hostfwd=tcp:0.0.0.0:${VM_HTTPS_PORT}-:443" \
  -device virtio-net-pci,netdev=net0 \
  -boot order=c,menu=on \
  -monitor none \
  "$@" \
  -qmp unix:/state/qmp.sock,server=on,wait=off
'''


def print_preflight(
    slice_name: str,
    levels: list[dict[str, int | float | None]],
    control_group: str,
    *,
    max_memory_psi_full_avg10: float = MAX_MEMORY_PSI_FULL_AVG10,
) -> None:
    target = levels[0]
    target_current = target["memory_current"]
    target_projected = int(target_current) + VM_MEMORY_BYTES if target_current is not None else None
    target_high = target["memory_high"]
    target_max = target["memory_max"]
    print(
        f"[preflight] slice={slice_name} cgroup={control_group} "
        f"cpu-quota={target['cpu_max']} current-memory={target['memory_current']} "
        f"new-guest-cap={VM_MEMORY_BYTES} projected-memory={target_projected} "
        f"high-advisory={target_high} cap-exceeds-high-advisory="
        f"{None if target_projected is None or target_high is None else target_projected > int(target_high)} "
        f"max={target_max} cap-exceeds-hard-max="
        f"{None if target_projected is None or target_max is None else target_projected > int(target_max)} "
        f"full-avg10-limit={max_memory_psi_full_avg10:g}% "
        f"psi-some-avg10={target['memory_psi_some_avg10']} "
        f"psi-full-avg10={target['memory_psi_full_avg10']}",
        flush=True,
    )
    for level in levels:
        current = int(level["memory_current"] or 0)
        limit = level["memory_max"]
        high = level["memory_high"]
        projected = current + VM_MEMORY_BYTES
        print(
            f"[memory-admission] cgroup={level['path']} current={current} "
            f"projected-memory={projected} high-advisory={high} cap-exceeds-high-advisory="
            f"{None if high is None else projected > int(high)} max={limit} cap-exceeds-hard-max="
            f"{None if limit is None else projected > int(limit)} "
            f"psi-some-avg10={level['memory_psi_some_avg10']} "
            f"psi-full-avg10={level['memory_psi_full_avg10']}",
            flush=True,
        )


def cmd_start(args: argparse.Namespace) -> int:
    with vm_start_admission_lock():
        return _cmd_start(args)


def _cmd_start(args: argparse.Namespace) -> int:
    validate_name(args.name)
    validate_max_concurrent_guests(args.max_concurrent_guests)
    validate_port(args.ssh_port, "SSH port")
    validate_port(args.https_port, "HTTPS port")
    if args.ssh_port == args.https_port:
        raise RunnerError("SSH and HTTPS forwarding ports must differ")

    slice_name = args.cgroup_parent or os.environ.get("CGROUP_PARENT_DEV_BACKGROUND", "")
    if not slice_name:
        raise RunnerError("CGROUP_PARENT_DEV_BACKGROUND is unset; refusing an unplaced VM")
    validate_slice_name(slice_name)
    network = args.network or os.environ.get("DOCKER_NETWORK_INTERNAL", "")
    if not network:
        raise RunnerError("DOCKER_NETWORK_INTERNAL is unset; pass --network explicitly")
    image = args.image or os.environ.get("TRUENAS_TCG_IMAGE", "")
    if not image:
        raise RunnerError("pass --image or set TRUENAS_TCG_IMAGE; no image fallback is selected")

    base_disk = Path(args.base_disk).resolve(strict=True)
    if not base_disk.is_file():
        raise RunnerError(f"base disk is not a regular file: {base_disk}")
    firmware_code = Path(args.ovmf_code).resolve(strict=True)
    firmware_vars = Path(args.ovmf_vars).resolve(strict=True)
    if not firmware_code.is_file() or not firmware_vars.is_file():
        raise RunnerError("OVMF code and variable template must both be regular files")
    readonly_media = Path(args.readonly_media).resolve(strict=True) if args.readonly_media else None
    if readonly_media is not None and not readonly_media.is_file():
        raise RunnerError(f"read-only media is not a regular file: {readonly_media}")
    disk_sizes_bytes = extra_disk_sizes_bytes(args.data_disk_size)
    state_dir = Path(args.state_dir).resolve(strict=True)
    if not state_dir.is_dir():
        raise RunnerError(f"state directory is not a directory: {state_dir}")
    if not os.access(state_dir, os.W_OK | os.X_OK):
        raise RunnerError(f"state directory is not writable: {state_dir}")
    with qmp_socket_address(state_dir / "qmp.sock"):
        pass
    state_hash = state_identity(state_dir)

    inspect_network(network)
    selected_image_id = image_id(image)
    _, current = inspect_current_container()
    mounts = current.get("Mounts", [])
    host_base = resolve_docker_path(base_disk, mounts, writable=False)
    host_state = resolve_docker_path(state_dir, mounts, writable=True)
    if "," in host_base or "," in host_state:
        raise RunnerError("Docker bind paths containing commas are unsupported; choose a state/base path without commas")
    host_media = resolve_docker_path(readonly_media, mounts, writable=False) if readonly_media else None
    if host_media is not None and "," in host_media:
        raise RunnerError(
            "Docker bind paths containing commas are unsupported; choose a media path without commas"
        )
    expected_manifest = runner_manifest(
        name=args.name,
        base_disk=base_disk,
        firmware_code=firmware_code,
        firmware_vars=firmware_vars,
        image_id=selected_image_id,
        network=network,
        cgroup_parent=slice_name,
        ssh_port=args.ssh_port,
        https_port=args.https_port,
        readonly_media=readonly_media,
        extra_disk_sizes=disk_sizes_bytes,
    )
    manifest_path = state_dir / "runner.json"
    if args.resume:
        check_runner_manifest(
            manifest_path,
            expected_manifest,
            allow_readonly_media_addition=readonly_media is not None,
        )
    elif any(state_dir.iterdir()):
        raise RunnerError(f"state directory is not empty; use a fresh directory or --resume: {state_dir}")

    inspector, levels, control_group = cgroup_preflight(
        slice_name,
        max_memory_psi_full_avg10=args.memory_psi_full_avg10_limit,
    )
    print_preflight(
        slice_name,
        levels,
        control_group,
        max_memory_psi_full_avg10=args.memory_psi_full_avg10_limit,
    )

    current_ids = docker_run(["docker", "ps", "-q", "--filter", f"label={VM_LABEL}={VM_LABEL_VALUE}"]).stdout.splitlines()
    active = [docker_inspect(item).get("Name", "").lstrip("/") for item in current_ids]
    validate_active_guest_count(active, args.max_concurrent_guests)
    print(
        f"[admission] max-concurrent-guests={args.max_concurrent_guests} active={len(active)} "
        "memory-check=current-usage-plus-next-guest-cap",
        flush=True,
    )
    existing = docker_run(["docker", "inspect", args.name], check=False)
    if existing.returncode == 0:
        labels = container_labels(args.name)
        if labels.get(VM_LABEL) != VM_LABEL_VALUE or labels.get(STATE_LABEL) != state_hash:
            raise RunnerError(f"container name {args.name!r} is owned by another workload")
        item = docker_inspect(args.name)
        if item.get("State", {}).get("Running"):
            raise RunnerError(f"container {args.name!r} is already running")
        if not args.resume:
            raise RunnerError(f"container {args.name!r} is stopped; pass --resume to reuse its COW disk")
        docker_run(["docker", "rm", args.name])
    # An explicit --resume can also recover state retained after startup
    # verification removed a mis-placed container.

    vnc_socket_path: Path | None = None
    if args.vnc_console:
        prepare_vnc_socket(state_dir / "vnc.sock")
        vnc_socket_path = vnc_socket_client_path(state_dir)

    serial_socket_path: Path | None = None
    if args.serial_console:
        prepare_serial_socket(state_dir / "serial.sock")
        serial_socket_path = serial_socket_client_path(state_dir)

    overlay = state_dir / "disk-overlay.qcow2"
    staged_firmware_code = state_dir / "OVMF_CODE_4M.fd"
    varstore = state_dir / "OVMF_VARS_4M.fd"
    if args.resume:
        if not overlay.is_file() or not staged_firmware_code.is_file() or not varstore.is_file():
            raise RunnerError(
                "--resume requires disk-overlay.qcow2, OVMF_CODE_4M.fd, and OVMF_VARS_4M.fd in the state directory"
            )
        if file_identity(staged_firmware_code)["sha256"] != expected_manifest["firmware_code"]["sha256"]:
            raise RunnerError("staged OVMF code differs from the source firmware recorded for this guest")
    else:
        os.chmod(state_dir, 0o700)
        shutil.copyfile(firmware_code, staged_firmware_code)
        shutil.copyfile(firmware_vars, varstore)
        os.chmod(staged_firmware_code, 0o600)
        os.chmod(varstore, 0o600)
        write_runner_manifest(manifest_path, expected_manifest)
    if args.resume and readonly_media is not None:
        stored_manifest = json.loads(manifest_path.read_text())
        if "readonly_media" not in stored_manifest:
            write_runner_manifest(manifest_path, expected_manifest)
    print(f"[source] base={base_disk} state={state_dir}", flush=True)
    print(f"[container] host base mount={host_base} host state mount={host_state}", flush=True)
    print(
        f"[limits] cgroup-parent={slice_name} cpus={VM_CPUS:g} memory={VM_MEMORY_BYTES} "
        f"memory-swap={VM_MEMORY_BYTES} guest-RAM={VM_GUEST_MEMORY_MIB}MiB network={network}",
        flush=True,
    )
    if disk_sizes_bytes:
        print(
            f"[extra-disks] sparse-qcow2-sizes-bytes={disk_sizes_bytes}",
            flush=True,
        )

    create_argv = [
        "docker", "run", "--detach", "--init", "--name", args.name,
        "--cgroup-parent", slice_name,
        "--cpus", f"{VM_CPUS:g}",
        "--memory", str(VM_MEMORY_BYTES),
        "--memory-swap", str(VM_MEMORY_BYTES),
        "--pids-limit", "128",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--network", network,
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--label", f"{VM_LABEL}={VM_LABEL_VALUE}",
        "--label", f"{STATE_LABEL}={state_hash}",
        "--mount", f"type=bind,src={host_base},dst=/base/disk.qcow2,readonly",
        "--mount", f"type=bind,src={host_state},dst=/state",
        "--env", f"VM_NAME={args.name}",
        "--env", f"VM_SSH_PORT={args.ssh_port}",
        "--env", f"VM_HTTPS_PORT={args.https_port}",
        "--env", f"VM_MODE={'resume' if args.resume else 'create'}",
        "--env", f"VM_VNC_CONSOLE={'1' if args.vnc_console else '0'}",
        "--env", f"VM_SERIAL_CONSOLE={'1' if args.serial_console else '0'}",
        "--env", "VM_EXTRA_DISK_SIZES_BYTES=" + ",".join(map(str, disk_sizes_bytes)),
    ]
    if readonly_media is not None:
        assert host_media is not None
        create_argv.extend([
            "--mount", f"type=bind,src={host_media},dst=/media/truenas-readonly.iso,readonly",
            "--env", "VM_READONLY_MEDIA=/media/truenas-readonly.iso",
        ])
    create_argv.extend([
        "--entrypoint", "/bin/sh",
        image,
        "-eu", "-c", host_command_script(),
    ])
    docker_run(create_argv)
    try:
        verified: dict[str, Any] | None = None
        for _ in range(START_TIMEOUT_SECONDS * 4):
            item = docker_inspect(args.name)
            if not item.get("State", {}).get("Running"):
                log = docker_run(["docker", "logs", "--tail", "80", args.name], check=False).stdout
                raise RunnerError(f"QEMU container exited during startup:\n{log}")
            qmp_ready = (state_dir / "qmp.sock").exists()
            vnc_ready = not args.vnc_console or (state_dir / "vnc.sock").exists()
            serial_ready = not args.serial_console or (state_dir / "serial.sock").exists()
            if qmp_ready and vnc_ready and serial_ready:
                verified = verify_running_container(
                    args.name, state_hash, network, slice_name, inspector, control_group
                )
                break
            time.sleep(0.25)
        if verified is None:
            missing = "QMP socket"
            if (state_dir / "qmp.sock").exists():
                missing = "VNC Unix socket" if args.vnc_console and not (state_dir / "vnc.sock").exists() else "serial Unix socket"
            raise RunnerError(f"QEMU did not create its {missing} within {START_TIMEOUT_SECONDS}s")
        vm_status = qmp_command(state_dir / "qmp.sock", "query-status")
    except (RunnerError, OSError, ValueError) as exc:
        cleanup = cleanup_failed_start(args.name, state_dir)
        raise RunnerError(f"{exc}; startup cleanup: {cleanup}") from exc
    result = {**verified, "qmp_status": vm_status, "state_dir": str(state_dir), "overlay": str(overlay)}
    if vnc_socket_path is not None:
        result["vnc_socket"] = str(vnc_socket_path)
        print(f"[console] VNC listens on Unix socket only: {vnc_socket_path}", flush=True)
    if serial_socket_path is not None:
        result["serial_socket"] = str(serial_socket_path)
        print(f"[console] serial listens on Unix socket only: {serial_socket_path}", flush=True)
    print("[started] " + json.dumps(result, sort_keys=True), flush=True)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    validate_name(args.name)
    item = docker_inspect(args.name)
    labels = item.get("Config", {}).get("Labels") or {}
    if labels.get(VM_LABEL) != VM_LABEL_VALUE:
        raise RunnerError(f"container {args.name!r} is not a managed TrueNAS TCG VM")
    state = item.get("State", {})
    result: dict[str, Any] = {
        "container": args.name,
        "running": bool(state.get("Running")),
        "status": state.get("Status"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled"),
        "cgroup_parent": item.get("HostConfig", {}).get("CgroupParent"),
        "cpu_nano_cpus": item.get("HostConfig", {}).get("NanoCpus"),
        "memory_bytes": item.get("HostConfig", {}).get("Memory"),
        "memory_swap_bytes": item.get("HostConfig", {}).get("MemorySwap"),
    }
    if state.get("Running"):
        state_dir = Path(args.state_dir).resolve(strict=True)
        if labels.get(STATE_LABEL) != state_identity(state_dir):
            raise RunnerError(f"state directory does not match container {args.name!r}")
        result["qmp_status"] = qmp_command(state_dir / "qmp.sock", "query-status")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    validate_name(args.name)
    item = docker_inspect(args.name)
    labels = item.get("Config", {}).get("Labels") or {}
    if labels.get(VM_LABEL) != VM_LABEL_VALUE:
        raise RunnerError(f"container {args.name!r} is not a managed TrueNAS TCG VM")
    if not item.get("State", {}).get("Running"):
        print(f"[stopped] {args.name} is already {item.get('State', {}).get('Status')}")
        return 0
    state_dir = Path(args.state_dir).resolve(strict=True)
    if labels.get(STATE_LABEL) != state_identity(state_dir):
        raise RunnerError(f"state directory does not match container {args.name!r}")
    qmp_command(state_dir / "qmp.sock", "system_powerdown")
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        current = docker_inspect(args.name)
        if not current.get("State", {}).get("Running"):
            print(f"[stopped] {args.name} shut down cleanly; state remains at {state_dir}")
            return 0
        time.sleep(1)
    raise RunnerError(
        f"guest did not stop within {STOP_TIMEOUT_SECONDS}s; it remains running. "
        "The runner did not force-stop it; inspect the serial log and QMP state before intervening."
    )


def cmd_terminate_paused(args: argparse.Namespace) -> int:
    """Stop a QEMU process that is already paused, preserving its state."""
    validate_name(args.name)
    item = docker_inspect(args.name)
    labels = item.get("Config", {}).get("Labels") or {}
    if labels.get(VM_LABEL) != VM_LABEL_VALUE:
        raise RunnerError(f"container {args.name!r} is not a managed TrueNAS TCG VM")
    if not item.get("State", {}).get("Running"):
        print(f"[aborted] {args.name} is already {item.get('State', {}).get('Status')}")
        return 0

    state_dir = Path(args.state_dir).resolve(strict=True)
    if labels.get(STATE_LABEL) != state_identity(state_dir):
        raise RunnerError(f"state directory does not match container {args.name!r}")
    status = qmp_command(state_dir / "qmp.sock", "query-status")
    if not isinstance(status, dict) or status.get("running") is not False:
        raise RunnerError(
            "refusing termination: this action is only for a managed guest QEMU reports as paused; "
            f"observed {status!r}"
        )

    # A paused guest cannot process ACPI shutdown. The operator must choose
    # this explicit lifecycle verb; keep the COW files and require a separate
    # `destroy` for container removal.
    docker_run(["docker", "kill", args.name])
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        current = docker_inspect(args.name)
        if not current.get("State", {}).get("Running"):
            print(
                f"[terminated] {args.name} QEMU process stopped without guest shutdown; "
                f"state remains at {state_dir}",
                flush=True,
            )
            return 0
        time.sleep(1)
    raise RunnerError(f"QEMU container {args.name!r} remains running after termination request")


def cmd_destroy(args: argparse.Namespace) -> int:
    validate_name(args.name)
    item = docker_inspect(args.name)
    labels = item.get("Config", {}).get("Labels") or {}
    if labels.get(VM_LABEL) != VM_LABEL_VALUE:
        raise RunnerError(f"container {args.name!r} is not a managed TrueNAS TCG VM")
    if item.get("State", {}).get("Running"):
        raise RunnerError("refusing to destroy a running guest; use stop first")
    docker_run(["docker", "rm", args.name])
    print(f"[destroyed] removed stopped container {args.name}; COW disk and firmware state were preserved")
    return 0


def cmd_rebind_media(args: argparse.Namespace) -> int:
    validate_name(args.name)
    state_dir = Path(args.state_dir).resolve(strict=True)
    if not state_dir.is_dir():
        raise RunnerError(f"state directory is not a directory: {state_dir}")
    container_names = docker_run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if args.name in container_names.stdout.splitlines():
        item = docker_inspect(args.name)
        labels = item.get("Config", {}).get("Labels") or {}
        if labels.get(VM_LABEL) != VM_LABEL_VALUE:
            raise RunnerError(f"container {args.name!r} is not a managed TrueNAS TCG VM")
        if labels.get(STATE_LABEL) == state_identity(state_dir):
            raise RunnerError(
                f"container {args.name!r} still references this state directory; "
                "stop the guest and destroy its stopped container before rebinding media"
            )
    old_path, new_path = rebind_runner_readonly_media(
        state_dir / "runner.json",
        name=args.name,
        readonly_media=Path(args.readonly_media),
    )
    print(f"[media-rebound] {old_path} -> {new_path}; ISO bytes and guest disk state are unchanged")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="start a bounded local TrueNAS TCG guest")
    start.add_argument("--name", required=True)
    start.add_argument("--base-disk", required=True, help="local qcow2/raw disk image mounted read-only")
    start.add_argument("--ovmf-code", required=True, help="local OVMF_CODE_4M.fd source file")
    start.add_argument("--ovmf-vars", required=True, help="local OVMF_VARS_4M.fd template file")
    start.add_argument("--state-dir", required=True, help="existing empty or resumed writable state directory")
    start.add_argument("--image", help="QEMU image; defaults to TRUENAS_TCG_IMAGE")
    start.add_argument("--network", help="Docker network; defaults to DOCKER_NETWORK_INTERNAL")
    start.add_argument("--cgroup-parent", help="systemd slice; defaults to CGROUP_PARENT_DEV_BACKGROUND")
    start.add_argument("--ssh-port", type=int, default=DEFAULT_SSH_PORT)
    start.add_argument("--https-port", type=int, default=DEFAULT_HTTPS_PORT)
    start.add_argument(
        "--max-concurrent-guests",
        type=int,
        choices=range(1, MAX_CONCURRENT_GUESTS + 1),
        default=DEFAULT_MAX_CONCURRENT_GUESTS,
        help=f"repository-wide cap for running managed guests (default: {DEFAULT_MAX_CONCURRENT_GUESTS}; maximum: {MAX_CONCURRENT_GUESTS})",
    )
    start.add_argument(
        "--memory-psi-full-avg10-limit",
        type=positive_finite_float,
        default=MAX_MEMORY_PSI_FULL_AVG10,
        help=f"maximum full memory PSI avg10 percentage for admission (default: {MAX_MEMORY_PSI_FULL_AVG10:g})",
    )
    start.add_argument(
        "--readonly-media",
        help="optional ISO attached as a read-only CD-ROM",
    )
    start.add_argument(
        "--data-disk-size",
        action="append",
        default=[],
        metavar="SIZE",
        help=(
            "attach one additional sparse QCOW2 data disk; repeat for more disks "
            f"(up to {MAX_EXTRA_DISKS}; examples: 512M, 2G)"
        ),
    )
    start.add_argument(
        "--vnc-console",
        action="store_true",
        help="enable a local VNC Unix socket at <state-dir>/vnc.sock; headless is the default",
    )
    start.add_argument(
        "--serial-console",
        action="store_true",
        help="enable an interactive serial Unix socket at <state-dir>/serial.sock and log serial output",
    )
    start.add_argument("--resume", action="store_true", help="reuse an exited runner's existing overlay and OVMF vars")
    start.set_defaults(func=cmd_start)
    for verb in ("status", "stop"):
        sub = commands.add_parser(verb)
        sub.add_argument("--name", required=True)
        sub.add_argument("--state-dir", required=True)
        sub.set_defaults(func=cmd_status if verb == "status" else cmd_stop)
    terminate_paused = commands.add_parser(
        "terminate-paused",
        help="terminate a managed guest QEMU reports as paused; preserves disk state",
    )
    terminate_paused.add_argument("--name", required=True)
    terminate_paused.add_argument("--state-dir", required=True)
    terminate_paused.set_defaults(func=cmd_terminate_paused)
    destroy = commands.add_parser("destroy", help="remove a stopped container but preserve its disk state")
    destroy.add_argument("--name", required=True)
    destroy.set_defaults(func=cmd_destroy)
    rebind = commands.add_parser(
        "rebind-media",
        help="update a stopped guest's read-only ISO path after verifying identical contents",
    )
    rebind.add_argument("--name", required=True)
    rebind.add_argument("--state-dir", required=True)
    rebind.add_argument("--readonly-media", required=True)
    rebind.set_defaults(func=cmd_rebind_media)
    console = commands.add_parser("console", help="interact with a running guest through its QMP socket")
    console_commands = console.add_subparsers(dest="console_action", required=True)
    screendump = console_commands.add_parser("screendump", help="save the current VGA frame as a PPM image")
    screendump.add_argument("--name", required=True)
    screendump.add_argument("--state-dir", required=True)
    screendump.add_argument("--filename", default="screendump.ppm")
    screendump.set_defaults(func=cmd_console_screendump)
    sendkey = console_commands.add_parser("sendkey", help="send a QEMU key or key combination to the guest")
    sendkey.add_argument("--name", required=True)
    sendkey.add_argument("--state-dir", required=True)
    sendkey.add_argument("key")
    sendkey.set_defaults(func=cmd_console_sendkey)
    serial = console_commands.add_parser("serial", help="connect interactively to a guest's serial console")
    serial.add_argument("--name", required=True)
    serial.add_argument("--state-dir", required=True)
    serial.set_defaults(func=cmd_console_serial)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, RunnerError) as exc:
        print(f"tcg_vm.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

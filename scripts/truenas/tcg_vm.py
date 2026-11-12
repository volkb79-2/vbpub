#!/usr/bin/env python3
"""Run up to three bounded TrueNAS QEMU/TCG guests in the host background slice.

The VM runs in its own Docker container so TCG CPU use and guest memory do not
consume the interactive devcontainer's cgroup. The launcher refuses to start
unless it can verify the configured systemd slice and available memory budget
through a running host cgroup inspector.
"""

from __future__ import annotations

import argparse
import re
import sys
import time  # noqa: F401  (tests patch tcg_vm.time)
from pathlib import Path
from typing import Any

# The launcher logic lives in the sibling project scripts/vm-lab. This file is
# the TrueNAS profile front: it defines the TrueNAS VmProfile and keeps the
# names other files import from here.
_VM_LAB_SOURCE = str(Path(__file__).resolve().parent.parent / "vm-lab")
if _VM_LAB_SOURCE not in sys.path:
    sys.path.insert(0, _VM_LAB_SOURCE)

import vm_lab  # noqa: E402
from vm_lab import (  # noqa: E402,F401  (re-exported for importers of this module)
    MAX_EXTRA_DISKS,
    MAX_MEMORY_PSI_FULL_AVG10,
    MAX_MEMORY_PSI_SOME_AVG10,
    QMP_SOCKET_PATH_MAX_BYTES,
    DOCKER_START_TIMEOUT_SECONDS,
    START_TIMEOUT_SECONDS,
    STOP_TIMEOUT_SECONDS,
    PortForward,
    RunnerError,
    VmProfile,
    busctl_value,
    cgroup_file,
    cgroup_levels,
    check_runner_manifest,
    cleanup_failed_start,
    container_labels,
    docker_inspect,
    docker_run,
    extra_disk_sizes_bytes,
    file_identity,
    find_cgroup_inspector,
    host_pid_cgroup,
    image_exists,
    image_id,
    inspect_current_container,
    inspect_network,
    inspector_exec,
    mount_source_for_path,
    parse_cpu_max,
    parse_extra_disk_size,
    parse_host_cgroup,
    parse_memory_limit,
    parse_memory_psi,
    positive_finite_float,
    prepare_serial_socket,
    prepare_unix_socket,
    prepare_vnc_socket,
    qmp_command,
    qmp_human_monitor_command,
    qmp_socket_address,
    rebind_runner_readonly_media,
    relay_serial_console,
    resolve_docker_path,
    state_identity,
    systemd_slice_path,
    validate_name,
    validate_port,
    validate_slice_name,
    vm_start_lock_path,
    write_runner_manifest,
)


PROFILE = VmProfile(
    program="tcg_vm.py",
    product="TrueNAS",
    description=__doc__ or "",
    start_help="start a bounded local TrueNAS TCG guest",
    vm_label="org.vbpub.truenas.tcg-vm",
    state_label="org.vbpub.truenas.state-dir-sha256",
    label_value="true",
    socket_alias_prefix="truenas",
    image_env="TRUENAS_TCG_IMAGE",
    extra_disk_drive_prefix="truenasdata",
    extra_disk_serial_prefix="TNEXTRA",
    readonly_media_path="/media/truenas-readonly.iso",
    max_concurrent_guests=3,
    default_max_concurrent_guests=3,
    guest_memory_mib=3072,
    cpus=2,
    forwards=(
        PortForward(name="ssh", host_port=2222, guest_port=22),
        PortForward(name="https", host_port=8443, guest_port=443),
    ),
)

VM_CPUS = float(PROFILE.cpus)
VM_NANO_CPUS = PROFILE.nano_cpus
VM_MEMORY_BYTES = PROFILE.container_memory_bytes  # 3.5 GiB, including QEMU and guest overhead.
VM_GUEST_MEMORY_MIB = PROFILE.guest_memory_mib
DEFAULT_MAX_CONCURRENT_GUESTS = PROFILE.default_max_concurrent_guests
MAX_CONCURRENT_GUESTS = PROFILE.max_concurrent_guests
VM_LABEL = PROFILE.vm_label
VM_LABEL_VALUE = PROFILE.label_value
STATE_LABEL = PROFILE.state_label
DEFAULT_SSH_PORT = PROFILE.forwards[0].host_port
DEFAULT_HTTPS_PORT = PROFILE.forwards[1].host_port
# Estate layout (AGENTS.md "Directory conventions"): VM state lives in
# <project>/artifacts/vm-lab/<vm>/, derived from this file's location and never
# from an environment variable. An explicit --state-dir still wins.
VM_LAB_DIR = Path(__file__).resolve().parent / "artifacts" / "vm-lab"
_STATE_DIR_DEFAULTS = (
    "artifacts/vm-lab/<name> beside this script, created",
    "artifacts/vm-lab/<name>",
)


def default_state_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise RunnerError(f"guest name cannot name a vm-lab directory: {name!r}")
    return VM_LAB_DIR / name


def resolve_state_dir_argument(args: argparse.Namespace) -> None:
    """Fill a missing --state-dir with artifacts/vm-lab/<name>; only `start` creates it."""
    if getattr(args, "state_dir", "") is not None or not hasattr(args, "name"):
        return
    state_dir = default_state_dir(args.name)
    if args.command == "start":
        state_dir.mkdir(parents=True, exist_ok=True)
    args.state_dir = str(state_dir)


# Profile-bound forms of the generic launcher functions. Each keeps the
# signature this module has always had.

def validate_max_concurrent_guests(value: int) -> None:
    vm_lab.validate_max_concurrent_guests(value, PROFILE.max_concurrent_guests)


def validate_active_guest_count(active_names: list[str], max_concurrent_guests: int) -> None:
    vm_lab.validate_active_guest_count(
        active_names, max_concurrent_guests, PROFILE.max_concurrent_guests
    )


def vm_start_admission_lock():
    return vm_lab.vm_start_admission_lock(PROFILE.program)


def validate_memory_headroom(
    levels: list[dict[str, int | float | None]],
    *,
    requested_cpus: float = VM_CPUS,
    max_memory_psi_full_avg10: float = MAX_MEMORY_PSI_FULL_AVG10,
    allow_memory_psi_pressure: bool = False,
) -> None:
    vm_lab.validate_memory_headroom(
        levels,
        requested_cpus=requested_cpus,
        max_memory_psi_full_avg10=max_memory_psi_full_avg10,
        allow_memory_psi_pressure=allow_memory_psi_pressure,
    )


def cgroup_preflight(
    slice_name: str,
    *,
    max_memory_psi_full_avg10: float = MAX_MEMORY_PSI_FULL_AVG10,
) -> tuple[str, list[dict[str, int | float | None]], str]:
    return vm_lab.cgroup_preflight(
        slice_name,
        requested_cpus=VM_CPUS,
        max_memory_psi_full_avg10=max_memory_psi_full_avg10,
    )


def print_preflight(
    slice_name: str,
    levels: list[dict[str, int | float | None]],
    control_group: str,
    *,
    max_memory_psi_full_avg10: float = MAX_MEMORY_PSI_FULL_AVG10,
) -> None:
    vm_lab.print_preflight(
        slice_name,
        levels,
        control_group,
        memory_bytes=VM_MEMORY_BYTES,
        max_memory_psi_full_avg10=max_memory_psi_full_avg10,
    )


def verify_running_container(
    name: str,
    state_dir_hash: str,
    network: str,
    cgroup_parent: str,
    inspector: str,
    expected_cgroup_path: str,
) -> dict[str, Any]:
    return vm_lab.verify_running_container(
        PROFILE, name, state_dir_hash, network, cgroup_parent, inspector, expected_cgroup_path
    )


def vnc_socket_client_path(state_dir: Path) -> Path:
    """Return a short host path to the VNC socket in the bind-mounted state directory."""
    return vm_lab.vnc_socket_client_path(PROFILE, state_dir)


def serial_socket_client_path(state_dir: Path) -> Path:
    """Return a short host path to the serial socket in the bind-mounted state directory."""
    return vm_lab.serial_socket_client_path(PROFILE, state_dir)


def validate_console_guest(name: str, state_dir: Path) -> Path:
    return vm_lab.validate_console_guest(PROFILE, name, state_dir)


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
    return vm_lab.runner_manifest(
        PROFILE,
        name=name,
        base_disk=base_disk,
        firmware_code=firmware_code,
        firmware_vars=firmware_vars,
        image_id=image_id,
        network=network,
        cgroup_parent=cgroup_parent,
        host_ports={"ssh": ssh_port, "https": https_port},
        readonly_media=readonly_media,
        extra_disk_sizes=extra_disk_sizes,
    )


def host_command_script() -> str:
    return vm_lab.host_command_script(PROFILE)


def cmd_start(args: argparse.Namespace) -> int:
    return vm_lab.cmd_start(PROFILE, args)


def cmd_status(args: argparse.Namespace) -> int:
    return vm_lab.cmd_status(PROFILE, args)


def cmd_stop(args: argparse.Namespace) -> int:
    return vm_lab.cmd_stop(PROFILE, args)


def cmd_terminate_paused(args: argparse.Namespace) -> int:
    return vm_lab.cmd_terminate_paused(PROFILE, args)


def cmd_destroy(args: argparse.Namespace) -> int:
    return vm_lab.cmd_destroy(PROFILE, args)


def cmd_rebind_media(args: argparse.Namespace) -> int:
    return vm_lab.cmd_rebind_media(PROFILE, args)


def cmd_console_screendump(args: argparse.Namespace) -> int:
    return vm_lab.cmd_console_screendump(PROFILE, args)


def cmd_console_sendkey(args: argparse.Namespace) -> int:
    return vm_lab.cmd_console_sendkey(PROFILE, args)


def cmd_console_reset(args: argparse.Namespace) -> int:
    return vm_lab.cmd_console_reset(PROFILE, args)


def cmd_console_serial(args: argparse.Namespace) -> int:
    return vm_lab.cmd_console_serial(PROFILE, args)


def build_parser() -> argparse.ArgumentParser:
    return vm_lab.build_parser(PROFILE, state_dir_defaults=_STATE_DIR_DEFAULTS)


def main(argv: list[str] | None = None) -> int:
    return vm_lab.main(
        PROFILE,
        argv,
        parser=build_parser(),
        prepare_args=resolve_state_dir_argument,
    )


if __name__ == "__main__":
    raise SystemExit(main())

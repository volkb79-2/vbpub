#!/usr/bin/python3
"""Run the version-pinned, fail-closed TrueNAS unattended installation."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


RUNTIME_DIR = Path("/usr/local/lib/truenas-vanilla-iso")
PROFILE_PATH = RUNTIME_DIR / "profile.json"
sys.path.insert(0, str(RUNTIME_DIR))

from safety import UNSAFE_MESSAGE, UnsafeHardwareConfiguration, select_target_disks  # noqa: E402
from safety import parse_zdb_label_probe  # noqa: E402


def _read_json_command(argv: list[str]) -> dict[str, Any]:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE) from exc
    if not isinstance(value, dict):
        raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)
    return value


def _installer_media_source() -> str:
    result = subprocess.run(
        ["findmnt", "--noheadings", "--raw", "--mountpoint", "/cdrom", "--output", "SOURCE"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)
    source = result.stdout.strip().splitlines()
    if len(source) != 1 or not source[0]:
        raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)
    return source[0]


def _scan_zfs_pool_labels(lsblk_document: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Read ZFS labels from every reported block node without importing pools."""
    blockdevices = lsblk_document.get("blockdevices")
    if not isinstance(blockdevices, list):
        raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)

    result: dict[str, tuple[str, ...]] = {}

    def visit(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)
            name = node.get("name")
            kind = node.get("type")
            path = node.get("path")
            if not isinstance(name, str) or not name or not isinstance(kind, str):
                raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)
            if kind not in {"rom", "loop"}:
                if not isinstance(path, str) or not path.startswith("/dev/"):
                    raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)
                try:
                    probe = subprocess.run(
                        ["zdb", "-l", path],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                except OSError as exc:
                    raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE) from exc
                result[name] = parse_zdb_label_probe(
                    probe.stdout,
                    probe.stderr,
                    probe.returncode,
                )
            children = node.get("children") or []
            if not isinstance(children, list):
                raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)
            visit(children)

    visit(blockdevices)
    return result


def _load_profile() -> dict[str, Any]:
    try:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read unattended profile: {exc}") from exc
    if (
        not isinstance(profile, dict)
        or profile.get("schema_version") != 1
        or profile.get("flavor") not in {"unattended", "unattended-relaxed"}
    ):
        raise RuntimeError("unattended ISO profile has an unknown schema or flavor")
    return profile


async def _install(profile: dict[str, Any]) -> None:
    from truenas_installer.disks import list_disks
    from truenas_installer.install import InstallError, install
    from truenas_installer.serial import serial_sql

    try:
        inventory = _read_json_command(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--output",
                "NAME,PATH,TYPE,SIZE,MODEL,TRAN,RM,RO,PKNAME,MOUNTPOINTS",
            ]
        )
        source = _installer_media_source()
        zfs_pool_names_by_node = _scan_zfs_pool_labels(inventory)
        installer_disks = await list_disks()
        targets = select_target_disks(
            inventory,
            installer_disks,
            source,
            zfs_pool_names_by_node,
        )
    except UnsafeHardwareConfiguration:
        print(UNSAFE_MESSAGE, flush=True)
        raise
    except Exception as exc:
        print(UNSAFE_MESSAGE, flush=True)
        raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE) from exc

    size_gib = profile.get("boot_pool_size_gib")
    if not isinstance(size_gib, int) or isinstance(size_gib, bool) or not 16 <= size_gib <= 1024:
        raise RuntimeError("unattended profile has an invalid boot-pool partition size")
    username = profile.get("administrator_username")
    password = profile.get("fixed_password")
    set_pmbr = profile.get("set_pmbr")
    post_install_action = profile.get("post_install_action", "reboot")
    if username != "truenas_admin" or not isinstance(password, str) or not password or not isinstance(set_pmbr, bool):
        raise RuntimeError("unattended profile has invalid authentication or partitioning defaults")
    if post_install_action not in {"reboot", "poweroff"}:
        raise RuntimeError("unattended profile has an invalid post-install action")

    names = ", ".join(disk.name for disk in targets)
    topology = "single-disk boot-pool" if len(targets) == 1 else "mirrored boot-pool"
    print("UNATTENDED INSTALL: target disk data will be erased without confirmation.", flush=True)
    print(f"Target disk(s): {names}; layout: {topology}; boot-pool partition: {size_gib} GiB.", flush=True)
    print("Initial administrator: truenas_admin / truenas. Change this password immediately after first boot.", flush=True)

    def progress(_fraction: float, message: str) -> None:
        print(message, flush=True)

    try:
        await install(
            targets,
            [],
            set_pmbr,
            {"username": username, "password": password},
            None,
            await serial_sql(),
            progress,
        )
    except InstallError as exc:
        raise RuntimeError(f"TrueNAS installer failed: {getattr(exc, 'message', str(exc))}") from exc

    adapter_id = profile.get("relaxation_adapter")
    if profile["flavor"] == "unattended-relaxed":
        if not isinstance(adapter_id, str) or not adapter_id:
            raise RuntimeError("combined ISO profile is missing its pinned relaxation adapter")
        from relaxation import apply_relaxation

        def adapter_progress(message: str) -> None:
            progress(1.0, message)

        apply_relaxation(adapter_id, str(profile.get("release_version") or ""), adapter_progress)
    elif adapter_id is not None:
        raise RuntimeError("unattended ISO unexpectedly requests a relaxation adapter")

    next_step = "powering off" if post_install_action == "poweroff" else "rebooting"
    print(f"TrueNAS installation is complete; {next_step} now.", flush=True)
    action = await asyncio.create_subprocess_exec("systemctl", post_install_action)
    await action.communicate()
    if action.returncode:
        raise RuntimeError(f"systemctl {post_install_action} exited with status {action.returncode}")


def main() -> int:
    try:
        profile = _load_profile()
        asyncio.run(_install(profile))
    except UnsafeHardwareConfiguration:
        return 20
    except Exception as exc:
        print(f"unattended installation stopped: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

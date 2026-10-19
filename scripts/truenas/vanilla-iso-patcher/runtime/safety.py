"""Fail-closed disk selection policy for the unattended installer."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any


UNSAFE_MESSAGE = "unsupported unsafe disk hardware configuration"
MIN_TARGET_BYTES = 16 * 1024**3 + 512 * 1024**2 + 2 * 1024**2
ZFS_POOL_NAME_RE = re.compile(r"^\s*name:\s*'([^']+)'\s*$", re.MULTILINE)
ZDB_EMPTY_LABELS = frozenset(f"failed to unpack label {index}" for index in range(4))


class UnsafeHardwareConfiguration(RuntimeError):
    """The discovered storage does not meet the unattended wipe policy."""


def _fail() -> None:
    raise UnsafeHardwareConfiguration(UNSAFE_MESSAGE)


def _walk_nodes(nodes: list[dict[str, Any]], parent: str | None = None):
    for node in nodes:
        name = str(node.get("name") or "")
        if not name:
            _fail()
        current_parent = str(node.get("pkname") or parent or "") or None
        yield name, node, current_parent
        children = node.get("children") or []
        if not isinstance(children, list):
            _fail()
        yield from _walk_nodes(children, name if node.get("type") == "disk" else current_parent)


def _mountpoints(node: dict[str, Any]) -> list[str]:
    values = node.get("mountpoints")
    if values is None:
        value = node.get("mountpoint")
        values = [] if value is None else [value]
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        _fail()
    result = []
    for value in values:
        if value:
            result.append(str(value))
    return result


def _root_disk_name(
    source: str,
    nodes: dict[str, tuple[dict[str, Any], str | None]],
) -> str | None:
    source = source.split("[", 1)[0]
    if not source.startswith("/dev/"):
        return None
    resolved = os.path.realpath(source)
    name = Path(resolved).name
    if name not in nodes:
        _fail()
    node, parent = nodes[name]
    kind = node.get("type")
    if kind == "rom":
        return None
    if kind == "loop":
        _fail()
    if kind == "disk":
        return name
    seen = set()
    while parent:
        if parent in seen or parent not in nodes:
            _fail()
        seen.add(parent)
        parent_node, next_parent = nodes[parent]
        if parent_node.get("type") == "disk":
            return parent
        parent = next_parent
    _fail()
    return None


def _normalize_model(value: Any) -> str:
    if value is None:
        return ""
    normalized = re.sub(r"\s+", " ", str(value)).strip().casefold()
    return "" if normalized in {"", "unknown model"} else normalized


def _flag(value: Any) -> bool:
    return value is True or value == 1 or value == "1"


def parse_zdb_label_probe(stdout: str, stderr: str, returncode: int) -> tuple[str, ...]:
    """Classify read-only ``zdb -l`` output for one block device.

    A successful label read yields one or more pool names. A blank/non-ZFS
    device must produce the four known unpack failures. Any other result is
    indeterminate and blocks unattended disk erasure.
    """
    output = f"{stdout}\n{stderr}"
    names = tuple(sorted(set(ZFS_POOL_NAME_RE.findall(output))))
    if names and returncode == 0:
        return names

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) == 4 and frozenset(lines) == ZDB_EMPTY_LABELS and returncode in {0, 1, 2}:
        return ()
    _fail()


def select_target_disks(
    lsblk_document: dict[str, Any],
    installer_disks: list[Any],
    installer_media_source: str,
    zfs_pool_names_by_node: dict[str, tuple[str, ...] | list[str]],
) -> list[Any]:
    """Return the sole disk or matching pair that the installer will wipe.

    The disk list is cross-checked against both the installer API and a full
    ``lsblk`` inventory. Mounted non-installer disks, read-only/removable disks,
    mismatched pairs, ZFS pools other than ``boot-pool``, and every count other
    than one or two fail closed. Every target disk and block-device descendant
    must have a determinate ``zdb -l`` result. A single disk may have an
    unknown model when the installer and kernel inventories still agree on its
    name and exact size. A mirror needs known matching model, size, and
    transport metadata.
    """
    blockdevices = lsblk_document.get("blockdevices")
    if not isinstance(blockdevices, list) or not installer_media_source:
        _fail()

    flattened = list(_walk_nodes(blockdevices))
    nodes = {name: (node, parent) for name, node, parent in flattened}
    cdrom_disk_names = set()
    for name, node, parent in flattened:
        if "/cdrom" in _mountpoints(node):
            if node.get("type") == "disk":
                cdrom_disk_names.add(name)
            elif parent and nodes.get(parent, ({}, None))[0].get("type") == "disk":
                cdrom_disk_names.add(parent)
            elif node.get("type") == "rom":
                # Optical media is a standalone non-target device in lsblk;
                # its mountpoint has no parent disk to exclude from targets.
                continue
            else:
                _fail()

    if installer_media_source.startswith("/dev/"):
        media_name = _root_disk_name(installer_media_source, nodes)
        if media_name:
            cdrom_disk_names.add(media_name)
        elif Path(os.path.realpath(installer_media_source)).name not in nodes:
            _fail()
    elif not cdrom_disk_names and ":" not in installer_media_source and not installer_media_source.startswith("//"):
        _fail()

    media_names = cdrom_disk_names

    physical_disks = [
        (name, node)
        for name, node, _parent in flattened
        if node.get("type") == "disk" and name not in media_names
    ]
    if len(physical_disks) not in {1, 2}:
        _fail()
    if len(physical_disks) == 2 and any(
        not _normalize_model(node.get("model")) for _name, node in physical_disks
    ):
        _fail()

    mountpoints_by_disk: dict[str, list[str]] = {name: [] for name, _node in physical_disks}
    for name, node, parent in flattened:
        current = name
        seen: set[str] = set()
        disk_name: str | None = None
        while current and current not in seen:
            seen.add(current)
            current_node, current_parent = nodes.get(current, ({}, None))
            if current_node.get("type") == "disk":
                disk_name = current
                break
            current = current_parent or ""
        if disk_name in mountpoints_by_disk:
            mountpoints_by_disk[disk_name].extend(_mountpoints(node))

    for name, node in physical_disks:
        if _flag(node.get("rm")) or _flag(node.get("ro")):
            _fail()
        try:
            size = int(node.get("size"))
        except (TypeError, ValueError):
            _fail()
        if size < MIN_TARGET_BYTES:
            _fail()
        _normalize_model(node.get("model"))
        for mountpoint in mountpoints_by_disk[name]:
            if mountpoint != "/cdrom" and not mountpoint.startswith("/cdrom/"):
                _fail()

    target_names = {name for name, _node in physical_disks}

    # A disk can contain ZFS labels on the whole device, one or more
    # partitions, or a descendant block mapping. Check every node below each
    # target before passing the disks to the installer, which wipes them.
    disk_for_node: dict[str, str] = {}
    for name, node, parent in flattened:
        current = name
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            current_node, current_parent = nodes.get(current, ({}, None))
            if current_node.get("type") == "disk":
                disk_for_node[name] = current
                break
            current = current_parent or ""

    for name, node, _parent in flattened:
        if disk_for_node.get(name) not in target_names or node.get("type") in {"rom", "loop"}:
            continue
        pool_names = zfs_pool_names_by_node.get(name)
        if not isinstance(pool_names, (tuple, list)) or any(
            not isinstance(pool_name, str) or not pool_name for pool_name in pool_names
        ):
            _fail()
        if any(pool_name != "boot-pool" for pool_name in pool_names):
            _fail()

    api_by_name = {str(disk.name): disk for disk in installer_disks}
    if len(api_by_name) != len(installer_disks) or set(api_by_name) != target_names:
        _fail()

    targets = [api_by_name[name] for name, _node in physical_disks]
    inventory_by_name = {name: node for name, node in physical_disks}
    for disk in targets:
        node = inventory_by_name[str(disk.name)]
        if (
            int(disk.size) != int(node.get("size"))
            or _normalize_model(disk.model) != _normalize_model(node.get("model"))
            or _flag(disk.removable)
        ):
            _fail()

    if len(targets) == 2:
        first = inventory_by_name[str(targets[0].name)]
        second = inventory_by_name[str(targets[1].name)]
        first_transport = str(first.get("tran") or "").strip().casefold()
        second_transport = str(second.get("tran") or "").strip().casefold()
        first_model = _normalize_model(first.get("model"))
        second_model = _normalize_model(second.get("model"))
        if (
            not first_transport
            or not second_transport
            or int(first.get("size")) != int(second.get("size"))
            or not first_model
            or first_model != second_model
            or first_transport != second_transport
        ):
            _fail()

    return sorted(targets, key=lambda disk: str(disk.name))

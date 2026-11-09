"""Pure conversion of mount and swap intents into Augeas ``records`` ops."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _field(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"fstab {label} must be a non-empty string")
    if any(char.isspace() or char in {"#", "\x00"} for char in value):
        raise ValueError(f"fstab {label} must not contain whitespace, newline, or #")
    return value


def _number(value: object, label: str, *, maximum: int | None = None) -> str:
    text = _field(value, label)
    if not text.isdecimal():
        raise ValueError(f"fstab {label} must be a non-negative decimal integer")
    parsed = int(text, 10)
    if maximum is not None and parsed > maximum:
        raise ValueError(f"fstab {label} must be at most {maximum}")
    return text


def mount_item(
    source: str,
    mountpoint: str,
    filesystem: str,
    options: Sequence[str],
    dump: str,
    passno: str,
) -> dict[str, Any]:
    """Build one ordered Fstab record, keeping each mount option as ``opt``."""

    device = _field(source, "device")
    target = _field(mountpoint, "mountpoint")
    if not target.startswith("/"):
        raise ValueError("fstab mountpoint must be an absolute path")
    fs = _field(filesystem, "filesystem")
    if not isinstance(options, (tuple, list)) or not options:
        raise ValueError("fstab options must be a non-empty sequence")
    normalized_options = [_field(item, "option") for item in options]
    return {
        "spec": device,
        "file": target,
        "vfstype": fs,
        "opt": normalized_options,
        "dump": _number(dump, "dump", maximum=1),
        "passno": _number(passno, "passno"),
    }


def mount_ops(items: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Return one non-exclusive ``records`` op per mountpoint; reject repeats."""

    if not isinstance(items, (tuple, list)):
        raise ValueError("fstab mount items must be a sequence")
    seen: set[str] = set()
    operations: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("fstab mount items must be mappings from mount_item")
        mountpoint = item.get("file")
        if not isinstance(mountpoint, str) or not mountpoint:
            raise ValueError("fstab mount item has no mountpoint")
        if mountpoint in seen:
            raise ValueError(f"duplicate fstab mountpoint {mountpoint!r}")
        seen.add(mountpoint)
        operations.append(
            {
                "records": "*",
                "key": "file",
                "exclusive": False,
                "items": [dict(item)],
            }
        )
    return tuple(operations)


def swap_item(device: str, priority: int, *, discard: bool = False) -> dict[str, Any]:
    """Build one swap record with explicit ``sw`` and ``pri=`` options."""

    source = _field(device, "device")
    if isinstance(priority, bool) or not isinstance(priority, int) or not -1 <= priority <= 32767:
        raise ValueError("fstab swap priority must be an integer from -1 through 32767")
    if type(discard) is not bool:
        raise ValueError("fstab discard must be a boolean")
    options = ["sw", f"pri={priority}"]
    if discard:
        options.append("discard")
    return {
        "spec": source,
        "file": "none",
        "vfstype": "swap",
        "opt": options,
        "dump": "0",
        "passno": "0",
    }


def swap_op(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build the exclusive swap selector operation (empty items means remove all)."""

    if not isinstance(items, (tuple, list)):
        raise ValueError("fstab swap items must be a sequence")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or item.get("vfstype") != "swap" or item.get("file") != "none":
            raise ValueError("fstab swap items must come from swap_item")
        device = item.get("spec")
        device = _field(device, "device")
        if device in seen:
            raise ValueError(f"duplicate fstab swap device {device!r}")
        seen.add(device)
        normalized.append(dict(item))
    return {
        "records": "*[vfstype='swap']",
        "key": "spec",
        "exclusive": True,
        "items": normalized,
    }


__all__ = ["mount_item", "mount_ops", "swap_item", "swap_op"]

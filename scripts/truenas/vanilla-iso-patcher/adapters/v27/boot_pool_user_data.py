"""Component-aware policy for an opt-in boot-pool user-data namespace.

This module is copied into TrueNAS middleware by the release-specific patch
adapters. Keep it free of middleware imports so its path rules can be tested
on the controller before a patch is applied.
"""

from __future__ import annotations

import posixpath
from collections.abc import Collection, Mapping
from typing import Any


USER_DATASET_COMPONENT = "user-data"
CONTAINER_DS_NAME = ".truenas_containers"
PROTECTED_COMPONENTS = frozenset({
    ".system",
    CONTAINER_DS_NAME,
    "ROOT",
    "grub",
    "ix-applications",
    "ix-apps",
})


def _dataset_components(path: str) -> list[str]:
    """Return dataset components, or an empty list for malformed snapshot paths."""
    dataset, separator, snapshot = path.partition("@")
    if separator and (not snapshot or "/" in snapshot or "@" in snapshot):
        return []
    return dataset.split("/")


def statfs_field(result: Any, name: str) -> Any:
    """Read statfs output across releases returning mappings or typed models.

    v28's internal middleware calls return a Pydantic ``FilesystemStatfsData``
    model; older middleware paths may return mappings. Missing fields fail
    closed as ``None`` so a caller's source/destination comparison rejects it.
    """
    if isinstance(result, Mapping):
        return result.get(name)
    return getattr(result, name, None)


def user_data_namespace_root(path: str, boot_pool_names: Collection[str]) -> str | None:
    """Return the namespace root for a path beneath a recognized boot pool."""
    components = _dataset_components(path)
    if not components:
        return None
    if (
        len(components) >= 2
        and components[0] in boot_pool_names
        and components[1] == USER_DATASET_COMPONENT
        and all(component not in ("", ".", "..") for component in components)
    ):
        return f"{components[0]}/{USER_DATASET_COMPONENT}"
    return None


def is_user_data_namespace_path(
    path: str,
    boot_pool_names: Collection[str],
    release_internal_components: Collection[str] = (),
) -> bool:
    """Return true only for the opted-in namespace and ordinary descendants.

    System-managed names are denied at any depth. The caller supplies the
    release's existing internal dataset components so newly introduced TrueNAS
    service datasets remain protected without weakening that release's policy.
    """
    root = user_data_namespace_root(path, boot_pool_names)
    if root is None:
        return False

    components = _dataset_components(path)
    if not components:
        return False
    protected = PROTECTED_COMPONENTS.union(release_internal_components)
    return not any(component in protected for component in components[2:])


def is_user_data_namespace_root(path: str, boot_pool_names: Collection[str]) -> bool:
    """Return true for the namespace dataset itself, excluding its snapshots."""
    root = user_data_namespace_root(path, boot_pool_names)
    components = _dataset_components(path)
    return root is not None and bool(components) and components == root.split("/")


def user_data_root_mount_config_is_valid(
    path: str,
    properties: Mapping[str, Any],
    boot_pool_names: Collection[str],
) -> bool:
    """Require the exact bounded root properties before mounting user storage."""
    if not is_user_data_namespace_root(path, boot_pool_names):
        return False

    quota = (properties.get("quota") or {}).get("value")
    mountpoint = (properties.get("mountpoint") or {}).get("value")
    canmount = (properties.get("canmount") or {}).get("value")
    return (
        type(quota) is int
        and quota > 0
        and mountpoint == f"/mnt/{path}"
        and canmount == "on"
    )


def user_data_mountpoint_is_within_root(mountpoint: str, namespace_root: str) -> bool:
    """Allow automatic mounts only below the opted-in namespace root.

    ``none`` and ``legacy`` deliberately opt a descendant out of automatic
    mounting. All filesystem mountpoints must otherwise be absolute and stay
    within the namespace after path normalization.
    """
    if mountpoint in ("none", "legacy"):
        return True
    if not isinstance(mountpoint, str) or not mountpoint.startswith("/"):
        return False

    root = posixpath.normpath(f"/mnt/{namespace_root}")
    candidate = posixpath.normpath(mountpoint)
    return candidate == root or candidate.startswith(f"{root}/")


def container_mountpoint_property(
    pool: str,
    boot_pool_names: Collection[str],
    container_dataset_name: str = CONTAINER_DS_NAME,
) -> str:
    """Return a writable ZFS mountpoint property for a pool's container data.

    Ordinary data pools are imported with ``/mnt`` as their altroot. The boot
    pool has no such altroot, so its container storage must name the writable
    ``/mnt`` path explicitly.
    """
    prefix = f"/mnt/{pool}/{USER_DATASET_COMPONENT}" if pool in boot_pool_names else ""
    return f"{prefix}/{container_dataset_name}/{pool}"


def container_mountpoint_real_path(
    pool: str,
    boot_pool_names: Collection[str],
    container_dataset_name: str = CONTAINER_DS_NAME,
) -> str:
    """Return the host-visible path after the pool's optional ``/mnt`` altroot."""
    property_path = container_mountpoint_property(
        pool, boot_pool_names, container_dataset_name
    )
    return property_path if property_path.startswith("/mnt/") else f"/mnt{property_path}"


def container_instance_real_path(
    pool: str,
    container_name: str,
    boot_pool_names: Collection[str],
    container_dataset_name: str = CONTAINER_DS_NAME,
) -> str:
    """Return the host-visible rootfs path for one managed container."""
    return (
        f"{container_mountpoint_real_path(pool, boot_pool_names, container_dataset_name)}"
        f"/containers/{container_name}"
    )

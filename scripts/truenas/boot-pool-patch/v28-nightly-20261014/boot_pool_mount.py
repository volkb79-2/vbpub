"""Release-specific boot-pool user-data mount hook for TrueNAS 28."""

from __future__ import annotations

import errno
import os

from middlewared.api.current import ZFSResourceQuery
from middlewared.plugins.boot_pool_user_data import (
    USER_DATASET_COMPONENT, is_user_data_namespace_path,
    statfs_field, user_data_mountpoint_is_within_root,
    user_data_root_mount_config_is_valid, user_data_namespace_root,
)
from middlewared.plugins.container.utils import CONTAINER_DS_NAME
from middlewared.plugins.zfs.utils import INTERNAL_PATHS
from middlewared.service import CallError
from middlewared.utils.boot.pool import BOOT_POOL_NAME_VALID


def mount_boot_pool_user_data(service):
    """Mount only the bounded, opted-in boot-pool namespace and safe descendants."""
    # Use the release's internal-name list as well as the shared namespace
    # policy. The container manager's own hidden subtree is the one
    # exception: it is stored below user-data and must be mounted at boot.
    for boot_pool in BOOT_POOL_NAME_VALID:
        root = f'{boot_pool}/{USER_DATASET_COMPONENT}'
        root_mountpoint = f'/mnt/{root}'
        try:
            roots = service.call_sync2(
                service.s.zfs.resource.list_impl,
                ZFSResourceQuery(paths=[root], properties=['quota', 'mountpoint', 'canmount']),
            )
        except Exception:
            service.logger.warning('Failed querying boot-pool user-data root %r', root, exc_info=True)
            continue

        if not roots:
            continue
        root_properties = roots[0].get('properties') or {}
        if not user_data_root_mount_config_is_valid(root, root_properties, BOOT_POOL_NAME_VALID):
            service.logger.warning('Skipping boot-pool user-data root %r with unsafe properties', root)
            continue

        # Never let a ZFS mount follow a symlink out of the fixed mount path.
        if os.path.realpath(root_mountpoint) != root_mountpoint:
            service.logger.error('Skipping boot-pool user-data root %r: mountpoint resolves through a symlink', root)
            continue

        try:
            mounted = service.middleware.call_sync('filesystem.statfs', root_mountpoint)
        except CallError as e:
            if e.errno == errno.ENOENT:
                mounted = None
            else:
                service.logger.warning('Failed checking boot-pool user-data mount %r', root_mountpoint, exc_info=True)
                continue
        except Exception:
            service.logger.warning('Failed checking boot-pool user-data mount %r', root_mountpoint, exc_info=True)
            continue

        root_is_mounted = bool(
            mounted and statfs_field(mounted, 'source') == root
            and statfs_field(mounted, 'dest') == root_mountpoint
        )
        if not root_is_mounted:
            if mounted:
                parent_source = statfs_field(mounted, 'source') or ''
                parent_is_expected = (
                    statfs_field(mounted, 'dest') == '/mnt'
                    and parent_source.startswith(f'{boot_pool}/ROOT/')
                    and parent_source.endswith('/mnt')
                )
            else:
                try:
                    parent = service.middleware.call_sync('filesystem.statfs', '/mnt')
                except Exception:
                    service.logger.warning('Cannot verify /mnt before mounting %r', root, exc_info=True)
                    continue
                parent_source = statfs_field(parent, 'source') or ''
                parent_is_expected = (
                    statfs_field(parent, 'dest') == '/mnt'
                    and parent_source.startswith(f'{boot_pool}/ROOT/')
                    and parent_source.endswith('/mnt')
                )
            if not parent_is_expected:
                service.logger.error(
                    'Skipping boot-pool user-data root %r: expected the boot root filesystem at /mnt, got %r',
                    root, mounted,
                )
                continue
            try:
                service.call_sync2(service.s.zfs.resource.mount, root)
            except Exception:
                service.logger.warning('Failed mounting boot-pool user-data root %r', root, exc_info=True)
                continue

            try:
                mounted = service.middleware.call_sync('filesystem.statfs', root_mountpoint)
            except Exception:
                service.logger.warning('Could not verify boot-pool user-data mount %r', root_mountpoint, exc_info=True)
                continue
            if statfs_field(mounted, 'source') != root or statfs_field(mounted, 'dest') != root_mountpoint:
                service.logger.error('Boot-pool user-data root %r did not mount at its configured path', root)
                continue
            service.logger.info('Mounted boot-pool user-data root %r', root)

        try:
            descendants = service.call_sync2(
                service.s.zfs.resource.list_impl,
                ZFSResourceQuery(
                    paths=[root], properties=['mountpoint', 'canmount'], get_children=True,
                ),
            )
        except Exception:
            service.logger.warning('Failed querying descendants of boot-pool user-data root %r', root, exc_info=True)
            continue

        container_root = f'{root}/{CONTAINER_DS_NAME}'
        for dataset in sorted(descendants, key=lambda row: (row.get('name', '').count('/'), row.get('name', ''))):
            name = dataset.get('name')
            if not name or name == root or dataset.get('type') != 'FILESYSTEM':
                continue
            is_managed_container_dataset = name == container_root or name.startswith(f'{container_root}/')
            if not is_managed_container_dataset and not is_user_data_namespace_path(
                name, BOOT_POOL_NAME_VALID, INTERNAL_PATHS
            ):
                continue

            properties = dataset.get('properties') or {}
            canmount = (properties.get('canmount') or {}).get('value')
            mountpoint = (properties.get('mountpoint') or {}).get('value')
            if canmount != 'on' or not user_data_mountpoint_is_within_root(mountpoint, root):
                continue
            if mountpoint in ('none', 'legacy'):
                continue

            mountpoint = os.path.normpath(mountpoint)
            try:
                resolved_mountpoint = os.path.realpath(mountpoint)
                if os.path.commonpath((resolved_mountpoint, root_mountpoint)) != root_mountpoint:
                    service.logger.error('Skipping %r: mountpoint %r escapes its user-data root', name, mountpoint)
                    continue
            except (OSError, ValueError):
                service.logger.warning('Skipping %r: cannot resolve mountpoint %r', name, mountpoint, exc_info=True)
                continue

            try:
                current = service.middleware.call_sync('filesystem.statfs', mountpoint)
            except CallError as e:
                if e.errno == errno.ENOENT:
                    current = None
                else:
                    service.logger.warning('Failed checking mountpoint %r for %r', mountpoint, name, exc_info=True)
                    continue
            except Exception:
                service.logger.warning('Failed checking mountpoint %r for %r', mountpoint, name, exc_info=True)
                continue

            if current and statfs_field(current, 'source') == name and statfs_field(current, 'dest') == mountpoint:
                continue
            if current:
                current_source = statfs_field(current, 'source') or ''
                current_dest = statfs_field(current, 'dest') or ''
                try:
                    current_is_user_data_parent = (
                        user_data_namespace_root(current_source, BOOT_POOL_NAME_VALID) == root
                        and os.path.commonpath((current_dest, root_mountpoint)) == root_mountpoint
                        and (name.startswith(f'{current_source}/') or current_source == root)
                    )
                except ValueError:
                    current_is_user_data_parent = False
                if not current_is_user_data_parent:
                    service.logger.error('Skipping %r: mountpoint %r is occupied by %r', name, mountpoint, current)
                    continue

            try:
                service.call_sync2(service.s.zfs.resource.mount, name)
                mounted = service.middleware.call_sync('filesystem.statfs', mountpoint)
            except Exception:
                service.logger.warning('Failed mounting boot-pool user-data dataset %r', name, exc_info=True)
                continue
            if statfs_field(mounted, 'source') == name and statfs_field(mounted, 'dest') == mountpoint:
                service.logger.info('Mounted boot-pool user-data dataset %r', name)
            else:
                service.logger.error('Boot-pool user-data dataset %r did not mount at %r', name, mountpoint)

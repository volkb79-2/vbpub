"""Adapters translating a host's policy into plain cowtree ``Settings``.

The library proper reads no environment and knows no host. This module is the single place where the vbpub
host contract (mdt-cockpit host-setup) is spelled out; a third party writes its own adapter (or builds
``Settings`` directly). Placing a populate helper in a cgroup slice (e.g. ``CGROUP_PARENT_DEV_CACHE``) is the
caller's job: cowtree only ever sees the resulting directories.
"""
from __future__ import annotations

import os

from .provision import Settings

MDT_CACHE_ROOT = "MDT_TREE_CACHE_ROOT"
MDT_UPPER_ROOT = "MDT_TREE_UPPER_ROOT"
MDT_DOCKER_SOCKET = "MDT_TREE_DOCKER_SOCKET"


def mdt_settings(env: dict[str, str] | None = None, *, level: str = "auto", tmpfs_dir: str | None = None,
                 clone_dir: str | None = None, copy_dir: str | None = None) -> Settings:
    """Settings from the mdt-cockpit host-policy keys (unset keys leave level A unconfigured)."""
    source = os.environ if env is None else env
    return Settings(
        level=level,
        shared_root=source.get(MDT_CACHE_ROOT) or None,
        upper_root=source.get(MDT_UPPER_ROOT) or None,
        docker_socket=source.get(MDT_DOCKER_SOCKET) or "/var/run/docker.sock",
        tmpfs_dir=tmpfs_dir,
        clone_dir=clone_dir,
        copy_dir=copy_dir,
    )

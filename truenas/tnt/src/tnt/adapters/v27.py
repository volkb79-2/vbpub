"""API generation 27 (v27.0.0-RC.1 measured on the hosted test VM; method names from https://api.truenas.com/v27.0)."""
from __future__ import annotations

from ..policy import DESTRUCTIVE_METHODS
from .base import Backend


class V27(Backend):
    generation = "v27"
    verified = "reference"  # names read from the v27.0 reference; the disk.details params are UNVERIFIED (BACKLOG TNT-01)
    destructive = DESTRUCTIVE_METHODS  # methods an action may only contain as a DESTRUCTIVE action
    manifest = {
        "disks": ("disk.query", "disk.details"),
        "pools": ("pool.query", "pool.create", "pool.import_find", "pool.import_pool", "pool.update", "pool.get_disks"),
        "datasets": ("pool.dataset.query", "pool.dataset.create", "pool.dataset.details"),
        "boot": ("boot.get_state", "boot.environment.query"),
        "containers": ("container.query", "container.start", "container.stop"),  # no virt.* namespace in v27
        "vms": ("vm.query", "vm.start", "vm.stop"),
        "apps": ("app.query",),
        "shares": ("sharing.smb.query", "sharing.nfs.query"),
        "system": ("system.info", "system.version", "core.get_methods"),
    }

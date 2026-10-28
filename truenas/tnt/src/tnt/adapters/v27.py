"""API generation 27 (v27.0.0-RC.1 measured on the hosted test VM; method names from https://api.truenas.com/v27.0)."""
from __future__ import annotations

from ..policy import DESTRUCTIVE_METHODS
from .base import Backend, MethodSpec


class V27(Backend):
    generation = "v27"
    verified = "reference"  # names read from the v27.0 reference; the disk.details params are UNVERIFIED (BACKLOG TNT-01)
    destructive = DESTRUCTIVE_METHODS  # methods an action may only contain as a DESTRUCTIVE action
    manifest = {
        "disks": {
            "disk.query": MethodSpec("read"), "disk.details": MethodSpec("read"),
            "disk.wipe": MethodSpec("destructive"),
        },
        "pools": {
            "pool.query": MethodSpec("read"), "pool.get_disks": MethodSpec("read"),
            "pool.import_find": MethodSpec("read"), "pool.scrub.query": MethodSpec("read"),
            "pool.scrub.get_instance": MethodSpec("read"), "pool.scrub.create": MethodSpec("write"),
            "pool.scrub.update": MethodSpec("write"), "pool.scrub.run": MethodSpec("write"),
            "pool.scrub.delete": MethodSpec("destructive"), "pool.create": MethodSpec("write"),
            "pool.import_pool": MethodSpec("destructive"), "pool.update": MethodSpec("write"),
            "pool.export": MethodSpec("destructive"), "pool.delete": MethodSpec("destructive"),
            "pool.expand": MethodSpec("write"), "pool.attach": MethodSpec("destructive"),
            "pool.detach": MethodSpec("destructive"), "pool.replace": MethodSpec("destructive"),
            "pool.remove": MethodSpec("destructive"), "pool.offline": MethodSpec("destructive"),
            "pool.online": MethodSpec("write"), "pool.upgrade": MethodSpec("write"),
        },
        "datasets": {
            "pool.dataset.query": MethodSpec("read"), "pool.dataset.details": MethodSpec("read"),
            "pool.dataset.get_instance": MethodSpec("read"), "pool.dataset.create": MethodSpec("write"),
            "pool.dataset.update": MethodSpec("write"), "pool.dataset.delete": MethodSpec("destructive"),
        },
        "snapshots": {
            "pool.snapshot.query": MethodSpec("read"), "pool.snapshot.get_instance": MethodSpec("read"),
            "pool.snapshot.create": MethodSpec("write"), "pool.snapshot.clone": MethodSpec("write"),
            "pool.snapshot.hold": MethodSpec("write"), "pool.snapshot.release": MethodSpec("write"),
            "pool.snapshot.rollback": MethodSpec("destructive"), "pool.snapshot.delete": MethodSpec("destructive"),
        },
        "boot": {
            "boot.get_state": MethodSpec("read"), "boot.environment.query": MethodSpec("read"),
        },
        "containers": {
            "container.query": MethodSpec("read"), "container.get_instance": MethodSpec("read"),
            "container.pool_choices": MethodSpec("read"), "container.create": MethodSpec("write"),
            "container.update": MethodSpec("write"), "container.start": MethodSpec("write"),
            "container.stop": MethodSpec("destructive"), "container.delete": MethodSpec("destructive"),
        },
        "vms": {
            "vm.query": MethodSpec("read"), "vm.get_instance": MethodSpec("read"),
            "vm.status": MethodSpec("read"), "vm.get_display_devices": MethodSpec("read"),
            "vm.get_display_web_uri": MethodSpec("read"), "vm.get_console": MethodSpec("read"),
            "vm.create": MethodSpec("write"), "vm.update": MethodSpec("write"),
            "vm.start": MethodSpec("write"), "vm.restart": MethodSpec("write"),
            "vm.suspend": MethodSpec("write"), "vm.resume": MethodSpec("write"),
            "vm.stop": MethodSpec("destructive"), "vm.poweroff": MethodSpec("destructive"),
            "vm.delete": MethodSpec("destructive"), "vm.device.query": MethodSpec("read"),
            "vm.device.create": MethodSpec("write"), "vm.device.update": MethodSpec("write"),
            "vm.device.delete": MethodSpec("destructive"),
        },
        "users": {
            "user.query": MethodSpec("read"), "user.get_instance": MethodSpec("read"),
            "user.shell_choices": MethodSpec("read"), "group.query": MethodSpec("read"),
            "user.create": MethodSpec("write"), "user.update": MethodSpec("write"),
            "user.delete": MethodSpec("destructive"), "user.set_password": MethodSpec("destructive"),
        },
        "apps": {"app.query": MethodSpec("read")},
        "shares": {"sharing.smb.query": MethodSpec("read"), "sharing.nfs.query": MethodSpec("read")},
        "system": {"system.info": MethodSpec("read"), "system.version": MethodSpec("read"),
                   "core.get_methods": MethodSpec("read")},
        # These names are known to be absent from the v27 method index. They remain classified so an accidental
        # action cannot treat them as harmless, but supported=False keeps them out of the callable allow-list.
        "policy-only": {
            "app.uninstall": MethodSpec("destructive", supported=False),
            "pool.dataset.destroy_snapshots": MethodSpec("destructive", supported=False),
            "filesystem.rmdir": MethodSpec("destructive", supported=False),
            "filesystem.unlink": MethodSpec("destructive", supported=False),
            "filesystem.remove": MethodSpec("destructive", supported=False),
            "container.restart": MethodSpec("destructive", supported=False),
        },
    }

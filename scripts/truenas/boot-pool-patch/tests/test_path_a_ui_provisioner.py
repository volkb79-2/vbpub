from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "acceptance" / "path_a_ui_provisioner.py"
SPEC = importlib.util.spec_from_file_location("path_a_ui_provisioner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
provisioner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provisioner)


class FakeTarget:
    def __init__(self, *, health="ONLINE", free=str(8 * 1024**3), root="absent", mountpoint="absent"):
        self.health = health
        self.free = free
        self.root = root
        self.mountpoint = mountpoint
        self.commands: list[list[str]] = []
        self.created = False

    def run(self, argv: list[str], *, timeout: int = 30) -> str:
        self.commands.append(argv)
        if argv[:5] == ["zpool", "list", "-H", "-o", "health"]:
            return self.health
        if argv[:5] == ["zpool", "list", "-H", "-p", "-o"]:
            return self.free
        if argv[:2] == ["zfs", "create"]:
            self.created = True
            return ""
        if argv[:2] == ["zfs", "get"]:
            prop = argv[-2]
            return {
                "quota": str(provisioner.ROOT_QUOTA_BYTES),
                "mountpoint": provisioner.ROOT_MOUNTPOINT,
                "canmount": "on",
            }[prop]
        if argv[0] == "findmnt":
            return provisioner.ROOT_DATASET
        if argv[:2] == ["zfs", "list"]:
            return provisioner.ROOT_DATASET
        if argv[0] == "find":
            return ""
        raise AssertionError(f"unexpected command: {argv!r}")

    def shell(self, command: str, *, timeout: int = 30) -> str:
        if "zfs list" in command:
            return self.root
        if "test -e" in command:
            return self.mountpoint
        raise AssertionError(f"unexpected shell command: {command!r}")


def test_provisions_only_empty_bounded_root_and_verifies_mount() -> None:
    target = FakeTarget()

    result = provisioner.provision_path_a_ui_root(target)

    assert result == {
        "dataset": "boot-pool/user-data",
        "quota_bytes": 2 * 1024**3,
        "mountpoint": "/mnt/boot-pool/user-data",
        "canmount": "on",
        "mount_source": "boot-pool/user-data",
    }
    create = next(command for command in target.commands if command[:2] == ["zfs", "create"])
    assert create == [
        "zfs", "create", "-o", f"quota={2 * 1024**3}",
        "-o", "mountpoint=/mnt/boot-pool/user-data", "-o", "canmount=on",
        "boot-pool/user-data",
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"health": "DEGRADED"}, "must be ONLINE"),
        ({"free": str(2 * 1024**3 - 1)}, "at least"),
        ({"free": "unknown"}, "at least"),
        ({"root": "present"}, "existing boot-pool/user-data"),
        ({"mountpoint": "present"}, "existing /mnt/boot-pool/user-data"),
    ],
)
def test_refuses_unhealthy_or_ambiguous_storage(kwargs: dict, message: str) -> None:
    target = FakeTarget(**kwargs)

    with pytest.raises(provisioner.PathARootProvisionError, match=message):
        provisioner.provision_path_a_ui_root(target)

    assert not target.created

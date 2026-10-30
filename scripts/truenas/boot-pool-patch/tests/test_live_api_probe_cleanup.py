from __future__ import annotations

import sys
from pathlib import Path

import pytest

PATCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATCH_ROOT / "acceptance"))
import live_api_probe as probe  # noqa: E402
from remote import RemoteError  # noqa: E402


PARTITION = "/dev/vda4"
PARTUUID = "11111111-2222-3333-4444-555555555555"
POOL_GUID = "12345"
DEVICE = f"/dev/disk/by-partuuid/{PARTUUID}"


@pytest.mark.parametrize(
    ("ok", "text", "expected"),
    [
        (False, "[ENOENT] ALL: Pool 0 does not exist", True),
        (False, "[ENOENT] pool.upgrade: pool with database id 0 does not exist", True),
        (True, "Pool 0 does not exist", False),
        (False, "[ENOENT] pool with database id 7 does not exist", False),
        (False, "permission denied", False),
    ],
)
def test_synthetic_pool_zero_refusal_matches_only_missing_pool_zero(ok, text, expected) -> None:
    assert probe.synthetic_row_refused(ok, text) is expected


def test_path_a_refusal_aggregate_requires_every_expected_mutation_result() -> None:
    assert probe.all_mutations_refused([True] * 10, 10)
    assert not probe.all_mutations_refused([True] * 9, 10)
    assert not probe.all_mutations_refused([True] * 9 + [False], 10)


class FakeTarget:
    def __init__(self):
        self.imported = True
        self.registered = True
        self.labels = True
        self.guid = POOL_GUID
        self.partuuid = PARTUUID
        self.parent = "vda"
        self.mountpoint = ""
        self.fail_findmnt = False
        self.pool_leaf = PARTITION
        self.export_pool_leaf = None
        self.fail_export = False
        self.fail_labelclear = False
        self.commands = []

    def run(self, argv, *, timeout=45, check=True):
        argv = tuple(argv)
        self.commands.append(argv)
        if argv[:2] == ("readlink", "-f"):
            path = argv[-1]
            if path in {DEVICE, f"/dev/disk/by-partuuid/{self.partuuid}"}:
                return PARTITION
            if path.startswith("/dev/disk/by-partuuid/"):
                return "/dev/vda5"
            return path if path.startswith("/dev/") else PARTITION
        if argv[:2] == ("lsblk", "-dnro"):
            field = argv[2]
            return {
                "PARTUUID": self.partuuid,
                "PARTLABEL": probe.MARKER,
                "PARTTYPE": probe.ZFS_TYPE_GUID,
                "PARTN": "4",
                "FSTYPE": "zfs_member" if self.labels else "",
                "MOUNTPOINT": self.mountpoint,
                "PKNAME": self.parent,
            }[field]
        if argv[:2] == ("bash", "-lc"):
            if self.fail_findmnt:
                raise RemoteError("findmnt failed")
            if self.mountpoint:
                raise RemoteError("findmnt found a mount")
            return ""
        if argv[:3] == ("zpool", "status", "-P"):
            if argv[-1:] == ("boot-pool",):
                return "pool: boot-pool\n  /dev/vda3 ONLINE\n"
            if argv[-1:] == (probe.POOL,):
                if not self.imported:
                    raise RemoteError("pool is exported")
                return f"pool: {probe.POOL}\n  {self.pool_leaf} ONLINE\n"
            return (
                f"pool: boot-pool\n  /dev/vda3 ONLINE\n"
                + (f"pool: {probe.POOL}\n  {self.pool_leaf} ONLINE\n" if self.imported else "")
            )
        if argv == ("zpool", "list", "-H", "-o", "name"):
            return "boot-pool\n" + (f"{probe.POOL}\n" if self.imported else "")
        if argv == ("zpool", "get", "-H", "-o", "value", "guid", probe.POOL):
            return self.guid
        if argv == ("zpool", "import", "-d", DEVICE):
            if not self.labels:
                return "no pools available to import\n"
            if self.imported:
                return "no pools available to import\n"
            return (
                f"  pool: {probe.POOL}\n    id: {self.guid}\n state: ONLINE\nconfig:\n\n"
                f"        NAME        STATE     READ WRITE CKSUM\n"
                f"        {probe.POOL} ONLINE       0     0     0\n"
                f"          {self.export_pool_leaf or self.pool_leaf} ONLINE       0     0     0\n"
            )
        if argv[:4] == ("midclt", "call", "-j", "pool.export"):
            if self.fail_export:
                raise RemoteError("pool.export failed")
            self.imported = False
            self.registered = False
            return ""
        if argv == ("zpool", "labelclear", "-f", DEVICE):
            if self.fail_labelclear:
                raise RemoteError("labelclear failed")
            self.labels = False
            return ""
        raise AssertionError(f"unexpected remote command: {argv!r}")

    def query(self, method, *args, timeout=90):
        assert method == "pool.query"
        return (
            [{"id": 7, "name": probe.POOL, "guid": POOL_GUID}]
            if self.registered else []
        )

    def call(self, method, *args, timeout=90):
        assert method == "disk.details"
        if self.labels:
            return []
        return [{"name": "vda4", "identifier": "{partuuid}" + PARTUUID}]


def cleanup(target: FakeTarget) -> None:
    probe.cleanup(target, PARTITION, PARTUUID, POOL_GUID, "/dev/vda")


def test_cleanup_exports_and_clears_only_the_captured_partuuid_pool() -> None:
    target = FakeTarget()

    cleanup(target)

    assert target.imported is False
    assert target.registered is False
    assert target.labels is False
    assert ("zpool", "labelclear", "-f", DEVICE) in target.commands
    assert not any(
        command[:2] == ("zpool", "labelclear") and command[-1] != DEVICE
        for command in target.commands
    )
    assert target.commands.count(("lsblk", "-dnro", "PARTUUID", DEVICE)) >= 2


def test_cleanup_resolves_partuuid_leaf_tokens_from_zpool_import() -> None:
    target = FakeTarget()
    target.export_pool_leaf = PARTUUID

    cleanup(target)

    assert ("readlink", "-f", DEVICE) in target.commands
    assert ("zpool", "labelclear", "-f", DEVICE) in target.commands


def test_cleanup_refuses_a_different_partuuid_leaf_token() -> None:
    target = FakeTarget()
    target.export_pool_leaf = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    with pytest.raises(RemoteError, match="not a single-leaf pool"):
        cleanup(target)

    assert not any(command[:2] == ("zpool", "labelclear") for command in target.commands)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("guid", "67890", "GUID changed"),
        ("partuuid", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "PARTUUID"),
        ("mountpoint", "/mnt/foreign", "mounted"),
        ("parent", "vdb", "boot-pool disk"),
        ("pool_leaf", "/dev/vda5", "solely"),
    ],
)
def test_cleanup_refuses_identity_or_target_state_drift(field, value, message) -> None:
    target = FakeTarget()
    setattr(target, field, value)

    with pytest.raises(RemoteError, match=message):
        cleanup(target)

    assert not any(command[:2] == ("zpool", "labelclear") for command in target.commands)


def test_cleanup_propagates_middleware_export_failure() -> None:
    target = FakeTarget()
    target.fail_export = True

    with pytest.raises(RemoteError, match="pool.export failed"):
        cleanup(target)

    assert not any(command[:2] == ("zpool", "labelclear") for command in target.commands)


def test_cleanup_propagates_mount_check_failure() -> None:
    target = FakeTarget()
    target.fail_findmnt = True

    with pytest.raises(RemoteError, match="findmnt failed"):
        cleanup(target)

    assert not any(command[:2] == ("zpool", "labelclear") for command in target.commands)


def test_cleanup_propagates_labelclear_failure() -> None:
    target = FakeTarget()
    target.fail_labelclear = True

    with pytest.raises(RemoteError, match="labelclear failed"):
        cleanup(target)


def test_cleanup_refuses_an_exported_pool_with_a_foreign_guid() -> None:
    target = FakeTarget()
    target.imported = False
    target.registered = False
    target.guid = "67890"

    with pytest.raises(RemoteError, match="GUID 12345"):
        cleanup(target)

    assert not any(command[:2] == ("zpool", "labelclear") for command in target.commands)


def test_cleanup_is_idempotent_after_labels_are_already_clear() -> None:
    target = FakeTarget()

    cleanup(target)
    cleanup(target)

    assert target.labels is False
    assert target.commands.count(("zpool", "labelclear", "-f", DEVICE)) == 1

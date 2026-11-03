from __future__ import annotations

import sys
from pathlib import Path

import pytest

PATCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATCH_ROOT / "shared"))
import reclaim_path_b as reclaim  # noqa: E402

PARTUUID = "11111111-2222-3333-4444-555555555555"
POOL = "chosen-data-pool"
GUID = "12345"
DEVICE = f"/dev/disk/by-partuuid/{PARTUUID}"


class FakeSystem:
    def __init__(self, *, imported=False, registered=False):
        self.imported = imported
        self.registered = registered
        self.labels = True
        self.partuuid = PARTUUID
        self.pool_guid = GUID
        self.pool_leaf = "/dev/vda4"
        self.export_pool_leaf = None
        self.parent = "vda"
        self.mountpoint = ""
        self.fail_export = False
        self.fail_labelclear = False
        self.commands = []

    def __call__(self, argv):
        argv = tuple(argv)
        self.commands.append(argv)
        if argv[:2] == ("readlink", "-f"):
            path = argv[-1]
            if path == "/dev/vda":
                return reclaim.CommandResult(0, "/dev/vda")
            if path.startswith("/dev/disk/by-partuuid/"):
                token = path.rsplit("/", 1)[-1]
                return reclaim.CommandResult(0, "/dev/vda4" if token == self.partuuid else "/dev/vda5")
            return reclaim.CommandResult(0, path if path.startswith("/dev/vda") else "/dev/vda4")
        if argv[:2] == ("lsblk", "-dnro"):
            field = argv[2]
            value = {
                "PARTUUID": self.partuuid,
                "PARTLABEL": reclaim.MARKER,
                "PARTTYPE": reclaim.ZFS_TYPE_GUID,
                "PARTN": "4",
                "FSTYPE": "zfs_member" if self.labels else "",
                "MOUNTPOINT": self.mountpoint,
                "PKNAME": "vda" if argv[-1] == "/dev/vda3" else self.parent,
            }[field]
            return reclaim.CommandResult(0, value)
        if argv[0] == "findmnt":
            return reclaim.CommandResult(0 if self.mountpoint else 1, self.mountpoint)
        if argv[:3] == ("zpool", "status", "-P"):
            if argv[-1:] == ("boot-pool",):
                return reclaim.CommandResult(0, "pool: boot-pool\n  /dev/vda3 ONLINE\n")
            if argv[-1:] == (POOL,):
                if not self.imported:
                    return reclaim.CommandResult(1, "", "pool is exported")
                return reclaim.CommandResult(0, f"pool: {POOL}\n  {self.pool_leaf} ONLINE\n")
            return reclaim.CommandResult(
                0,
                "pool: boot-pool\n  /dev/vda3 ONLINE\n"
                + (f"pool: {POOL}\n  {self.pool_leaf} ONLINE\n" if self.imported else ""),
            )
        if argv == ("zpool", "list", "-H", "-o", "name"):
            return reclaim.CommandResult(0, "boot-pool\n" + (f"{POOL}\n" if self.imported else ""))
        if argv == ("zpool", "get", "-H", "-o", "value", "guid", POOL):
            return reclaim.CommandResult(0, self.pool_guid)
        if argv == ("zpool", "import", "-d", DEVICE):
            if not self.labels or self.imported:
                return reclaim.CommandResult(0, "no pools available to import\n")
            return reclaim.CommandResult(
                0,
                f"  pool: {POOL}\n    id: {self.pool_guid}\n state: ONLINE\nconfig:\n\n"
                f"        NAME        STATE     READ WRITE CKSUM\n"
                f"        {POOL} ONLINE       0     0     0\n"
                f"          {self.export_pool_leaf or self.pool_leaf} ONLINE       0     0     0\n",
            )
        if argv[:4] == ("midclt", "call", "pool.query", "[[\"name\",\"=\",\"chosen-data-pool\"]]"):
            rows = ([{"id": 7, "name": POOL, "guid": self.pool_guid}] if self.registered else [])
            return reclaim.CommandResult(0, __import__("json").dumps(rows))
        if argv[:4] == ("midclt", "call", "-j", "pool.export"):
            if self.fail_export:
                return reclaim.CommandResult(1, "", "pool.export failed")
            self.imported = False
            self.registered = False
            return reclaim.CommandResult(0)
        if argv[:3] == ("midclt", "call", "disk.details"):
            rows = (
                [{"name": "vda4", "identifier": "{partuuid}" + PARTUUID}]
                if not self.labels else []
            )
            return reclaim.CommandResult(0, __import__("json").dumps(rows))
        if argv == ("zpool", "labelclear", "-f", DEVICE):
            if self.fail_labelclear:
                return reclaim.CommandResult(1, "", "labelclear failed")
            self.labels = False
            return reclaim.CommandResult(0)
        return reclaim.CommandResult(127, "", f"unexpected command: {argv!r}")


def run(system: FakeSystem) -> str:
    return reclaim.PathBReclaimer(system).reclaim(POOL, PARTUUID, GUID)


def test_reclaim_resumes_when_middleware_export_already_completed() -> None:
    system = FakeSystem(imported=False, registered=False)

    result = run(system)

    assert result == "reclaimed"
    assert not system.labels
    assert ("zpool", "labelclear", "-f", DEVICE) in system.commands


def test_reclaim_resolves_partuuid_leaf_tokens_from_zpool_import() -> None:
    system = FakeSystem(imported=False, registered=False)
    system.export_pool_leaf = PARTUUID

    result = run(system)

    assert result == "reclaimed"
    assert ("readlink", "-f", DEVICE) in system.commands
    assert ("zpool", "labelclear", "-f", DEVICE) in system.commands


def test_reclaim_refuses_a_different_partuuid_leaf_token() -> None:
    system = FakeSystem(imported=False, registered=False)
    system.export_pool_leaf = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    with pytest.raises(reclaim.ReclaimError, match="not a single-leaf pool"):
        run(system)

    assert not any(command[:2] == ("zpool", "labelclear") for command in system.commands)


def test_reclaim_exports_registered_pool_through_middleware_before_clearing() -> None:
    system = FakeSystem(imported=True, registered=True)

    result = run(system)

    assert result == "reclaimed"
    assert not system.imported and not system.registered and not system.labels
    export_index = system.commands.index((
        "midclt", "call", "-j", "pool.export", "7", '{"destroy":false}',
    ))
    labelclear_index = system.commands.index(("zpool", "labelclear", "-f", DEVICE))
    assert export_index < labelclear_index


def test_reclaim_is_idempotent_when_the_pool_signature_is_already_clear() -> None:
    system = FakeSystem(imported=False, registered=False)

    assert run(system) == "reclaimed"
    assert run(system) == "already_clear"

    assert system.commands.count(("zpool", "labelclear", "-f", DEVICE)) == 1


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("pool_guid", "67890", "GUID 12345"),
        ("partuuid", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "PARTUUID"),
        ("mountpoint", "/mnt/other", "mounted"),
        ("parent", "vdb", "boot-pool leaf"),
        ("pool_leaf", "/dev/vda5", "single-leaf"),
    ],
)
def test_reclaim_refuses_changed_identity_or_storage_state(field, value, message) -> None:
    system = FakeSystem(imported=False, registered=False)
    setattr(system, field, value)

    with pytest.raises(reclaim.ReclaimError, match=message):
        run(system)

    assert not any(command[:2] == ("zpool", "labelclear") for command in system.commands)


def test_reclaim_propagates_export_and_labelclear_failures() -> None:
    system = FakeSystem(imported=True, registered=True)
    system.fail_export = True
    with pytest.raises(reclaim.ReclaimError, match="pool.export"):
        run(system)
    assert not any(command[:2] == ("zpool", "labelclear") for command in system.commands)

    system = FakeSystem(imported=False, registered=False)
    system.fail_labelclear = True
    with pytest.raises(reclaim.ReclaimError, match="labelclear failed"):
        run(system)

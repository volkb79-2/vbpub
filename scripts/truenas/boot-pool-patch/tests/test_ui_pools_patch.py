"""Contract tests for v27/ui-pools.patch and the boot_disk_partition helper."""

from __future__ import annotations

import importlib.util
import asyncio
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import types

import pytest

PATCH_ROOT = Path(__file__).resolve().parents[1]
PATCH = PATCH_ROOT / "v27" / "ui-pools.patch"
TARGETS = [
    "middlewared/plugins/pool_/pool.py",
    "middlewared/plugins/pool_/export.py",
    "middlewared/plugins/disk_/availability.py",
]
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def split_files(text: str) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("--- a/"):
            current = line[6:]
            files[current] = []
        elif current is not None:
            files[current].append(line)
    return files


def test_hunk_headers_match_their_bodies() -> None:
    files = split_files(PATCH.read_text())
    assert sorted(files) == sorted(TARGETS)
    for name, lines in files.items():
        hunks, i = 0, 0
        while i < len(lines):
            m = HUNK.match(lines[i])
            if not m:
                i += 1
                continue
            hunks += 1
            old_n = int(m.group(2) or 1)
            new_n = int(m.group(4) or 1)
            old = new = 0
            i += 1
            while i < len(lines) and (old < old_n or new < new_n):
                line = lines[i]
                if line.startswith("\\"):
                    i += 1
                    continue
                tag = line[:1]
                if tag == " " or line == "":
                    old += 1; new += 1
                elif tag == "-":
                    old += 1
                elif tag == "+":
                    new += 1
                else:
                    break
                i += 1
            assert (old, new) == (old_n, new_n), (name, lines[i - 1])
        assert hunks >= 1, name


def test_targets_apply_script_and_bundle_know_the_patch() -> None:
    targets = (PATCH_ROOT / "v27" / "targets.sha256").read_text()
    for target in TARGETS:
        assert re.search(rf"^[0-9a-f]{{64}}  {re.escape(target)}$", targets, re.M), target
    apply_script = (PATCH_ROOT / "acceptance" / "apply_v27_patch_remote.sh").read_text()
    assert 'patch --dry-run --batch --fuzz=0 -p1 < "$patch_dir/ui-pools.patch"' in apply_script
    assert 'patch --batch --fuzz=0 -p1 < "$patch_dir/ui-pools.patch"' in apply_script
    assert apply_script.index("--dry-run --batch --fuzz=0 -p1 < \"$patch_dir/ui-pools.patch\"") < \
        apply_script.index("systemctl stop middlewared.service")
    assert '"$bundle_root/shared/boot_disk_partition.py" middlewared/plugins/' in apply_script
    for compiled in ("middlewared/plugins/boot_disk_partition.py", "middlewared/plugins/disk_/availability.py",
                     "middlewared/plugins/pool_/pool.py", "middlewared/plugins/pool_/export.py"):
        assert compiled in apply_script
    controller = (PATCH_ROOT / "acceptance" / "live_v27_patch.py").read_text()
    assert '"v27" / "ui-pools.patch"' in controller
    assert '"shared" / "boot_disk_partition.py"' in controller


def test_patch_keeps_the_synthetic_row_safe() -> None:
    text = PATCH.read_text()
    assert "info | {'id': 0, 'guid': guid, 'all_sed': False}" in text  # id/guid win over info
    assert "if app is not None and" in text  # internal callers never see the row
    assert "asyncio.wait_for(proc.communicate(), timeout=10)" in text
    assert "boot_disk_partition.topology_match" in text
    assert "boot_disk_partition.pseudo_disks" in text


def test_patch_rejects_unsupported_partition_pool_options_and_destroy() -> None:
    text = PATCH.read_text()
    helper = (PATCH_ROOT / "shared" / "boot_disk_partition.py").read_text()
    for option in (
        "encryption", "encryption_options", "deduplication", "dedup_table_quota",
        "dedup_table_quota_value", "checksum", "allow_duplicate_serials", "all_sed",
        "force_topology",
    ):
        assert option in helper
    assert "reserved boot-disk partitions" in text
    assert "pool_uses_reserved_partition" in text
    assert any("destroy" in line for line in split_files(text)["middlewared/plugins/pool_/export.py"])


def test_patch_is_anchored_and_fails_on_drift(tmp_path: Path) -> None:
    patch_bin = shutil.which("patch")
    if patch_bin is None:
        pytest.skip("patch is not installed")
    # Each hunk is checked on its own pre-image (its context and removed lines).
    only = tmp_path / "x"
    only.mkdir()
    counter = 0
    for name, lines in split_files(PATCH.read_text()).items():
        starts = [i for i, line in enumerate(lines) if HUNK.match(line)]
        starts.append(len(lines))
        for a, b in zip(starts, starts[1:]):
            hunk = lines[a:b]
            pre = [ln[1:] for ln in hunk[1:] if ln[:1] in " -"]
            m = HUNK.match(hunk[0])
            padded = ["pad"] * (int(m.group(1)) - 1) + pre
            counter += 1
            sub = tmp_path / f"h{counter}"
            sub.mkdir()
            f = sub / "file.py"
            f.write_text("\n".join(padded) + "\n")
            one = sub / "one.patch"
            one.write_text(f"--- a/file.py\n+++ b/file.py\n" + "\n".join(hunk) + "\n")
            ok = subprocess.run([patch_bin, "--dry-run", "--batch", "--fuzz=0", "-p1", "-i", str(one)],
                                cwd=sub, capture_output=True, text=True)
            assert ok.returncode == 0, ok.stdout + ok.stderr
            # Drift: change an anchoring context/removed line; the hunk must be refused.
            drifted = "\n".join(padded).replace(pre[len(pre) // 2], pre[len(pre) // 2] + "  # drift", 1)
            f.write_text(drifted + "\n")
            bad = subprocess.run([patch_bin, "--dry-run", "--batch", "--fuzz=0", "-p1", "-i", str(one)],
                                 cwd=sub, capture_output=True, text=True)
            assert bad.returncode != 0, "hunk applied silently to drifted source"


def load_helper():
    service = types.ModuleType("middlewared.service")
    service.CallError = type("CallError", (Exception,), {})
    service.Service = type("Service", (), {})
    service.private = lambda fn: fn
    pkg = types.ModuleType("middlewared")
    sys.modules.setdefault("middlewared", pkg)
    sys.modules["middlewared.service"] = service
    plugins = types.ModuleType("middlewared.plugins")
    pool_plugins = types.ModuleType("middlewared.plugins.pool_")
    pool_utils = types.ModuleType("middlewared.plugins.pool_.utils")
    pool_utils.ZPOOL_CACHE_FILE = "/data/zfs/zpool.cache"
    sys.modules.setdefault("middlewared.plugins", plugins)
    sys.modules.setdefault("middlewared.plugins.pool_", pool_plugins)
    sys.modules["middlewared.plugins.pool_.utils"] = pool_utils
    spec = importlib.util.spec_from_file_location("boot_disk_partition", PATCH_ROOT / "shared" / "boot_disk_partition.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ZFS = "6a898cc3-1dd2-11b2-99a6-080020736631"
UUID = "11111111-2222-3333-4444-555555555555"


def part(path: str, number: int, label: str | None, **extra) -> dict:
    return {"path": path, "name": path, "pkname": "/dev/vda", "partlabel": label, "parttype": ZFS,
            "partuuid": UUID, "partn": number, "size": 1 << 30, "fstype": None, "mountpoint": None,
            "type": "part"} | extra


def test_only_the_reserved_unused_partition_is_ever_offered() -> None:
    helper = load_helper()
    tree = {"blockdevices": [{"path": "/dev/vda", "type": "disk", "children": [
        part("/dev/vda3", 3, None),                      # boot pool partition
        part("/dev/vda4", 4, "boot-disk-data"),          # reserved: offered
        part("/dev/vda5", 5, "boot-disk-data", fstype="zfs_member"),
        part("/dev/vda6", 6, "boot-disk-data", mountpoint="/mnt/x"),
        part("/dev/vda7", 7, "other"),
        part("/dev/vda8", 8, "boot-disk-data", parttype="0fc63daf-8483-4772-8e79-3d69d8477de4"),
        part("/dev/vda9", 9, "boot-disk-data", partuuid="bad"),
        part("/dev/vda2", 2, "boot-disk-data"),          # number below 4
    ]}]}
    offered = helper.reserved_partitions_from_lsblk(tree, set(), {"/dev/vda3"})
    assert [row["name"] for row in offered] == ["vda4"]
    assert helper.reserved_partitions_from_lsblk(tree, {"/dev/vda4"}, {"/dev/vda3"}) == []


def test_reserved_partition_must_share_the_boot_pool_parent_disk() -> None:
    helper = load_helper()
    tree = {"blockdevices": [
        {"path": "/dev/vda", "type": "disk", "children": [part("/dev/vda3", 3, None),
            part("/dev/vda4", 4, "boot-disk-data")]},
        {"path": "/dev/vdb", "type": "disk", "children": [
            part("/dev/vdb4", 4, "boot-disk-data", pkname="/dev/vdb"),
        ]},
    ]}
    offered = helper.reserved_partitions_from_lsblk(tree, set(), {"/dev/vda3"})
    assert [row["path"] for row in offered] == ["/dev/vda4"]


def test_reserved_create_option_detector_names_every_unsupported_nondefault() -> None:
    helper = load_helper()
    options = [
        ({"encryption": True}, "encryption"),
        ({"encryption_options": {"generate_key": True}}, "encryption_options"),
        ({"deduplication": "ON"}, "deduplication"),
        ({"dedup_table_quota": "CUSTOM"}, "dedup_table_quota"),
        ({"dedup_table_quota_value": 1048576}, "dedup_table_quota_value"),
        ({"checksum": "SHA256"}, "checksum"),
        ({"allow_duplicate_serials": True}, "allow_duplicate_serials"),
        ({"all_sed": True}, "all_sed"),
        ({"force_topology": True}, "force_topology"),
    ]
    for payload, expected in options:
        assert helper.unsupported_create_options(payload) == [expected]
    assert helper.unsupported_create_options({
        "encryption": False,
        "encryption_options": {"generate_key": False, "pbkdf2iters": 1300000,
                               "passphrase": None, "key": None},
        "deduplication": None,
        "dedup_table_quota": "AUTO",
        "dedup_table_quota_value": None,
        "checksum": None,
        "allow_duplicate_serials": False,
        "all_sed": False,
        "force_topology": False,
    }) == []


def test_reserved_returns_no_candidates_when_inventory_commands_fail() -> None:
    helper = load_helper()

    class Logger:
        def __init__(self):
            self.warnings = 0

        def warning(self, *args, **kwargs):
            self.warnings += 1

    class Broken(helper.BootDiskPartitionService):
        def __init__(self, error):
            self.error = error
            self.logger = Logger()

        async def _run(self, *argv, timeout=15):
            raise self.error

    for error in (FileNotFoundError("lsblk"), asyncio.TimeoutError()):
        service = Broken(error)
        assert asyncio.run(service.reserved()) == []
        assert service.logger.warnings == 1


def test_destroy_guard_matches_boot_disk_partition_without_relying_on_its_label() -> None:
    helper = load_helper()
    tree = {"blockdevices": [{"path": "/dev/vda", "type": "disk", "children": [
        part("/dev/vda3", 3, None),
        part("/dev/vda4", 4, "changed-label"),
    ]}, {"path": "/dev/vdb", "type": "disk", "children": [
        part("/dev/vdb4", 4, "boot-disk-data", pkname="/dev/vdb"),
    ]}]}

    class Service(helper.BootDiskPartitionService):
        async def _read_lsblk(self):
            return tree

        async def _read_boot_pool_leaf_paths(self):
            return {"/dev/vda3"}

    service = Service()
    on_boot_disk = {"data": [{"type": "DISK", "path": "/dev/vda4", "device": "vda4"}]}
    on_other_disk = {"data": [{"type": "DISK", "path": "/dev/vdb4", "device": "vdb4"}]}
    assert asyncio.run(service.pool_uses_reserved_partition(on_boot_disk))
    assert not asyncio.run(service.pool_uses_reserved_partition(on_other_disk))


class _MiddlewareJob:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def wait(self, raise_error=False):
        if self.error:
            raise RuntimeError(self.error)
        return self.result


class _CreatePoolMiddleware:
    def __init__(self, scenario):
        self.scenario = scenario

    async def call(self, method, *args):
        if method == "pool.import_find":
            if self.scenario == "find-zero":
                return _MiddlewareJob([])
            if self.scenario == "find-two":
                return _MiddlewareJob([
                    {"name": "probe", "guid": "12345"},
                    {"name": "probe", "guid": "67890"},
                ])
            return _MiddlewareJob([{"name": "probe", "guid": "12345"}])
        if method == "pool.import_pool":
            error = "import_pool failed" if self.scenario in {"import-fails", "cleanup-fails"} else None
            return _MiddlewareJob(True, error)
        raise AssertionError(f"unexpected middleware call: {method} {args!r}")


class _CreatePoolHarness:
    def __new__(cls, helper, scenario):
        class Harness(helper.BootDiskPartitionService):
            def __init__(self):
                self.scenario = scenario
                self.commands = []
                self.verify_count = 0
                self.import_scan_count = 0
                self.status_calls = 0
                self.middleware = _CreatePoolMiddleware(scenario)

            async def reserved(self):
                return [{"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                         "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}]

            async def _verify_partition_identity(self, expected):
                self.verify_count += 1
                if self.scenario == "toctou" and self.verify_count == 1:
                    raise helper.CallError("partition PARTUUID changed")
                return expected

            async def _run(self, *argv, timeout=15):
                argv = tuple(argv)
                self.commands.append(argv)
                if argv == ("zpool", "list", "-H", "-o", "name"):
                    pools = "boot-pool\nprobe\n" if self.scenario == "name-imported" else "boot-pool\n"
                    return 0, pools, ""
                if argv == ("zpool", "import"):
                    if self.scenario == "name-exported":
                        return 0, "  pool: probe\n    id: 12345\n state: ONLINE\n", ""
                    return 0, "", ""
                if argv[:2] == ("zpool", "create"):
                    return 0, "", ""
                if argv[:2] == ("zpool", "status") and argv[-1:] == ("probe",):
                    self.status_calls += 1
                    if getattr(self, "exported", False):
                        return 1, "", "pool is exported"
                    return 0, "pool: probe\n  /dev/vda4 ONLINE\n", ""
                if argv == ("zpool", "export", "probe"):
                    if self.scenario == "export-fails":
                        return 1, "", "export failed"
                    self.exported = True
                    return 0, "", ""
                if argv[:2] == ("zpool", "import") and "-d" in argv:
                    self.import_scan_count += 1
                    if getattr(self, "exported", False):
                        return 0, "  pool: probe\n    id: 12345\n state: ONLINE\n", ""
                    return 0, "", ""
                if argv[:4] == ("zpool", "import", "-f", "-N"):
                    self.exported = False
                    return 0, "", ""
                if argv == ("zpool", "destroy", "-f", "probe"):
                    if self.scenario == "cleanup-fails":
                        return 1, "", "destroy failed"
                    self.destroyed = True
                    return 0, "", ""
                if argv[:2] == ("zpool", "status"):
                    return 0, "pool: boot-pool\n  /dev/vda3 ONLINE\n", ""
                if argv[:2] == ("zpool", "labelclear"):
                    return 0, "", ""
                raise AssertionError(f"unexpected command: {argv!r}")

        return Harness()


def test_create_pool_uses_pinned_identity_stock_properties_and_no_force_create() -> None:
    helper = load_helper()
    partition = {"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                 "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}
    service = _CreatePoolHarness(helper, "success")
    asyncio.run(service.create_pool("probe", partition))
    create = next(command for command in service.commands if command[:2] == ("zpool", "create"))
    assert "-f" not in create
    assert create[-2:] == ("probe", f"/dev/disk/by-partuuid/{UUID}")
    assert create == (
        "zpool", "create", "-o", "feature@lz4_compress=enabled", "-o", "altroot=/mnt",
        "-o", "cachefile=/data/zfs/zpool.cache", "-o", "failmode=continue", "-o",
        "autoexpand=on", "-o", "ashift=12", "-O", "atime=off", "-O", "acltype=posix",
        "-O", "aclinherit=discard", "-O", "aclmode=discard", "-O", "compression=lz4",
        "-O", "xattr=sa", "-O", "mountpoint=/probe", "probe",
        f"/dev/disk/by-partuuid/{UUID}",
    )
    assert service.verify_count >= 2


@pytest.mark.parametrize("scenario", ["name-imported", "name-exported"])
def test_create_pool_refuses_an_existing_imported_or_exported_pool_name(scenario) -> None:
    helper = load_helper()
    partition = {"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                 "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}
    service = _CreatePoolHarness(helper, scenario)
    with pytest.raises(helper.CallError, match="already exists"):
        asyncio.run(service.create_pool("probe", partition))
    assert service.commands == [
        ("zpool", "list", "-H", "-o", "name"),
        ("zpool", "import"),
    ]
    assert service.verify_count == 0


@pytest.mark.parametrize(
    ("scenario", "expected_error", "expected_commands"),
    [
        ("toctou", "changed", [
            ("zpool", "list", "-H", "-o", "name"), ("zpool", "import"),
        ]),
        ("export-fails", "export failed", [
            ("zpool", "list", "-H", "-o", "name"), ("zpool", "import"),
            ("zpool", "create", "probe", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"), ("zpool", "export", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "destroy", "-f", "probe"),
            ("zpool", "status", "-P", "-L"),
            ("zpool", "import", "-d", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "labelclear", "-f", f"/dev/disk/by-partuuid/{UUID}"),
        ]),
        ("find-zero", "found 0", [
            ("zpool", "list", "-H", "-o", "name"), ("zpool", "import"),
            ("zpool", "create", "probe", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"), ("zpool", "export", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "import", "-d", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "import", "-f", "-N", "12345"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "destroy", "-f", "probe"), ("zpool", "status", "-P", "-L"),
            ("zpool", "import", "-d", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "labelclear", "-f", f"/dev/disk/by-partuuid/{UUID}"),
        ]),
        ("find-two", "found 2", [
            ("zpool", "list", "-H", "-o", "name"), ("zpool", "import"),
            ("zpool", "create", "probe", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"), ("zpool", "export", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "import", "-d", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "import", "-f", "-N", "12345"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "destroy", "-f", "probe"), ("zpool", "status", "-P", "-L"),
            ("zpool", "import", "-d", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "labelclear", "-f", f"/dev/disk/by-partuuid/{UUID}"),
        ]),
        ("import-fails", "import_pool failed", [
            ("zpool", "list", "-H", "-o", "name"), ("zpool", "import"),
            ("zpool", "create", "probe", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"), ("zpool", "export", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "import", "-d", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "import", "-f", "-N", "12345"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "destroy", "-f", "probe"), ("zpool", "status", "-P", "-L"),
            ("zpool", "import", "-d", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "labelclear", "-f", f"/dev/disk/by-partuuid/{UUID}"),
        ]),
        ("cleanup-fails", "cleanup incomplete", [
            ("zpool", "list", "-H", "-o", "name"), ("zpool", "import"),
            ("zpool", "create", "probe", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"), ("zpool", "export", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "import", "-d", f"/dev/disk/by-partuuid/{UUID}"),
            ("zpool", "import", "-f", "-N", "12345"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "status", "-P", "-L", "probe"),
            ("zpool", "destroy", "-f", "probe"),
        ]),
    ],
)
def test_create_pool_failure_rollbacks_are_partition_scoped(scenario, expected_error, expected_commands) -> None:
    helper = load_helper()
    partition = {"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                 "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}
    service = _CreatePoolHarness(helper, scenario)
    with pytest.raises(helper.CallError, match=expected_error):
        asyncio.run(service.create_pool("probe", partition))
    compact = [
        command[:2] + command[-2:] if command[:2] == ("zpool", "create") else command
        for command in service.commands
    ]
    assert compact == expected_commands
    if scenario == "toctou":
        assert service.verify_count == 1
        assert not any(command[:2] == ("zpool", "create") for command in service.commands)
    if scenario == "cleanup-fails":
        assert not any(command[:2] == ("zpool", "labelclear") for command in service.commands)


def test_topology_match_refuses_anything_but_a_single_stripe_partition() -> None:
    helper = load_helper()
    reserved = [{"name": "vda4", "path": "/dev/vda4"}]
    ok = helper.match_topology({"data": [{"type": "STRIPE", "disks": ["vda4"]}]}, reserved)
    assert ok == (reserved[0], None)
    assert helper.match_topology({"data": [{"type": "STRIPE", "disks": ["sdb"]}]}, reserved) == (None, None)
    for topology in (
        {"data": [{"type": "MIRROR", "disks": ["vda4", "sdb"]}]},
        {"data": [{"type": "STRIPE", "disks": ["vda4"]}], "cache": [{"type": "STRIPE", "disks": ["sdb"]}]},
        {"data": [{"type": "MIRROR", "disks": ["vda4"]}]},
        {"data": [{"type": "STRIPE", "disks": ["vda4"]}], "spares": ["vda4"]},
    ):
        partition, error = helper.match_topology(topology, reserved)
        assert partition is None and error, topology

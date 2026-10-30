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


def load_helper(helper_path: Path | None = None):
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
    spec = importlib.util.spec_from_file_location(
        "boot_disk_partition", helper_path or PATCH_ROOT / "shared" / "boot_disk_partition.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v28_partition_helper_uses_only_the_recorded_20261027_schema() -> None:
    helper_path = PATCH_ROOT / "v28-nightly-20261027" / "boot_disk_partition.py"
    schema_path = PATCH_ROOT / "v28-nightly-20261027" / "pool_create_schema_v28.json"
    schema = json.loads(schema_path.read_text())
    helper = load_helper(helper_path)
    assert schema["release"] == "28.0.0-MASTER+20261027-175229"
    assert set(schema["fields"]) == helper.ALLOWED_CREATE_FIELDS | helper.REFUSED_CREATE_FIELDS
    assert schema["fields"]["encryption_options"]["nested_defaults"]["key"] == ""
    assert helper.unsupported_create_options({"name": "data", "topology": {"data": []}}) == []
    assert helper.unsupported_create_options({
        "name": "data", "topology": {"data": []}, "encryption": True,
    }) == ["encryption"]
    assert helper.unsupported_create_options({
        "name": "data", "topology": {"data": []}, "future_option": True,
    }) == ["future_option"]

    class SecretValue:
        def __init__(self, value):
            self.value = value

        def get_secret_value(self):
            return self.value

    middleware_defaults = schema["fields"]["encryption_options"]["nested_defaults"] | {
        "key": SecretValue(None),
        "passphrase": SecretValue(None),
    }
    assert helper.unsupported_create_options({
        "name": "data", "topology": {"data": []},
        "encryption_options": middleware_defaults,
    }) == []
    nondefault = middleware_defaults | {"passphrase": SecretValue("user supplied")}
    assert helper.unsupported_create_options({"encryption_options": nondefault}) == [
        "encryption_options",
    ]


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


def test_reserved_create_option_detector_refuses_each_nondefault_schema_field() -> None:
    helper = load_helper()
    schema = json.loads((PATCH_ROOT / "shared" / "pool_create_schema_v27.json").read_text())
    defaults = {}
    for name in helper.REFUSED_CREATE_FIELDS:
        field = schema["fields"][name]
        default = field.get("default", field.get("nested_defaults"))
        defaults[name] = default
        if isinstance(default, bool):
            changed = not default
        elif isinstance(default, dict):
            changed = default | {"generate_key": not default["generate_key"]}
        elif default is None:
            changed = "nondefault"
        elif isinstance(default, str):
            changed = default + "-nondefault"
        else:
            changed = default + 1
        assert helper.unsupported_create_options({name: changed}) == [name]

    assert helper.unsupported_create_options(defaults) == []
    assert helper.unsupported_create_options({"all_sed": 0}) == ["all_sed"]
    assert helper.unsupported_create_options({"future_pool_option": False}) == [
        "future_pool_option",
    ]

    class SecretValue:
        def __init__(self, value):
            self.value = value

        def get_secret_value(self):
            return self.value

    encryption_options = defaults["encryption_options"] | {
        "key": SecretValue(None),
        "passphrase": SecretValue(None),
    }
    assert helper.unsupported_create_options({"encryption_options": encryption_options}) == []


def test_recorded_pool_create_schema_matches_allowlisted_and_defaulted_fields() -> None:
    helper = load_helper()
    schema_path = PATCH_ROOT / "shared" / "pool_create_schema_v27.json"
    schema = json.loads(schema_path.read_text())
    fields = schema["fields"]

    assert schema["method"] == "pool.create"
    assert schema["release"] == "27.0.0-RC.1"
    assert set(fields) == helper.ALLOWED_CREATE_FIELDS | helper.REFUSED_CREATE_FIELDS
    assert helper.ALLOWED_CREATE_FIELDS == {"name", "topology"}

    for name in helper.REFUSED_CREATE_FIELDS:
        default = fields[name].get("default", fields[name].get("nested_defaults"))
        assert "default" in fields[name] or "nested_defaults" in fields[name], name
        assert helper.unsupported_create_options({name: default}) == []

    assert helper.unsupported_create_options({"future_pool_option": False}) == [
        "future_pool_option",
    ]


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
    def __init__(self, scenario, on_partial_import=None, on_partial_export=None):
        self.scenario = scenario
        self.calls = []
        self.stale_pool_record = False
        self.partial_pool_exported = False
        self.on_partial_import = on_partial_import
        self.on_partial_export = on_partial_export

    async def call(self, method, *args):
        self.calls.append((method, args))
        if method == "pool.import_find":
            if self.scenario in {"find-zero", "rollback-scan-timeout", "rollback-scan-wrong-name"}:
                return _MiddlewareJob([])
            if self.scenario == "find-two":
                return _MiddlewareJob([
                    {"name": "probe", "guid": "12345"},
                    {"name": "probe", "guid": "67890"},
                ])
            if self.scenario == "find-wrong-name":
                return _MiddlewareJob([{"name": "some-other-pool", "guid": "12345"}])
            return _MiddlewareJob([{"name": "probe", "guid": "12345"}])
        if method == "pool.import_pool":
            error = "import_pool failed" if self.scenario in {
                "import-fails", "cleanup-fails", "partial-registration",
            } else None
            if self.on_partial_import is not None:
                self.on_partial_import()
            if self.scenario == "partial-registration":
                self.stale_pool_record = True
            return _MiddlewareJob(True, error)
        if method == "pool.query":
            return ([{"id": 7, "name": "probe", "guid": "12345"}]
                    if self.stale_pool_record else [])
        if method == "pool.export":
            assert self.scenario == "partial-registration"
            pool_id, options = args
            assert pool_id == 7 and options == {"destroy": False}
            self.stale_pool_record = False
            self.partial_pool_exported = True
            if self.on_partial_export is not None:
                self.on_partial_export()
            return _MiddlewareJob(True)
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
                self.timeouts = []
                self.created = False
                self.guid_reads = 0
                self.exported = False
                self.middleware = _CreatePoolMiddleware(
                    scenario,
                    on_partial_import=lambda: setattr(self, "exported", False),
                    on_partial_export=lambda: setattr(self, "exported", True),
                )

            async def reserved(self):
                return [{"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                         "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}]

            async def _verify_partition_identity(self, expected):
                self.verify_count += 1
                if self.scenario == "toctou" and self.verify_count == 1:
                    raise helper.CallError("partition PARTUUID changed")
                if self.scenario == "identity-drift-after-create" and self.verify_count >= 2:
                    raise helper.CallError("partition PARTUUID changed after zpool create")
                return expected

            async def _run(self, *argv, timeout=15):
                argv = tuple(argv)
                self.commands.append(argv)
                self.timeouts.append((argv, timeout))
                if argv == ("zpool", "list", "-H", "-o", "name"):
                    pools = "boot-pool\nprobe\n" if self.scenario == "name-imported" else "boot-pool\n"
                    return 0, pools, ""
                if argv == ("zpool", "import"):
                    if self.scenario == "initial-scan-timeout":
                        raise asyncio.TimeoutError()
                    if self.scenario == "name-exported":
                        return 0, "  pool: probe\n    id: 12345\n state: ONLINE\n", ""
                    return 0, "", ""
                if argv == ("zpool", "get", "-H", "-o", "value", "guid", "probe"):
                    self.guid_reads += 1
                    if scenario == "export-fails-foreign-guid" and self.guid_reads >= 3:
                        return 0, "67890\n", ""
                    return (0, "12345\n", "") if self.created else (1, "", "pool missing")
                if argv[:2] == ("zpool", "create"):
                    if self.scenario == "create-fails":
                        return 1, "", "synthetic create failure"
                    if self.scenario == "create-timeout":
                        raise asyncio.TimeoutError()
                    self.created = True
                    return 0, "", ""
                if argv[:2] == ("zpool", "status") and argv[-1:] == ("probe",):
                    self.status_calls += 1
                    if not self.created:
                        return 1, "", "pool does not exist"
                    if getattr(self, "exported", False):
                        return 1, "", "pool is exported"
                    if self.scenario == "wrong-leaf":
                        return 0, "pool: probe\n  /dev/vda5 ONLINE\n", ""
                    return 0, "pool: probe\n  /dev/vda4 ONLINE\n", ""
                if argv == ("zpool", "export", "probe"):
                    if self.scenario in {"export-fails", "export-fails-foreign-guid"}:
                        return 1, "", "export failed"
                    self.exported = True
                    return 0, "", ""
                if argv[:2] == ("zpool", "import") and "-d" in argv:
                    self.import_scan_count += 1
                    if self.scenario == "rollback-scan-timeout":
                        raise asyncio.TimeoutError()
                    if getattr(self, "exported", False):
                        if self.scenario == "rollback-scan-wrong-name":
                            return 0, "  pool: other-pool\n    id: 98765\n state: ONLINE\n", ""
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
                if argv[:3] == ("findmnt", "-rn", "-S"):
                    return 1, "", ""
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


def expected_create_rollback_trace(path: str, *, destroy_fails: bool = False) -> list[tuple[str, ...]]:
    device = f"/dev/disk/by-partuuid/{UUID}"
    create = ("zpool", "create", "probe", device)
    guid = ("zpool", "get", "-H", "-o", "value", "guid", "probe")
    pool_status = ("zpool", "status", "-P", "-L", "probe")
    base = [
        ("zpool", "list", "-H", "-o", "name"), ("zpool", "import"), create,
        guid, guid, pool_status, pool_status, ("zpool", "export", "probe"),
    ]
    if path == "imported":
        rollback = [pool_status, guid, pool_status, guid, pool_status]
    else:
        rollback = [
            pool_status, ("zpool", "import", "-d", device),
            ("zpool", "import", "-f", "-N", "12345"),
            guid, pool_status, guid, pool_status,
        ]
    rollback.append(("zpool", "destroy", "-f", "probe"))
    if not destroy_fails:
        rollback.extend([
            ("zpool", "status", "-P", "-L"), ("zpool", "import", "-d", device),
            ("findmnt", "-rn", "-S", device), ("zpool", "labelclear", "-f", device),
        ])
    return base + rollback


@pytest.mark.parametrize(
    ("scenario", "expected_error", "rollback_path", "destroy_fails"),
    [
        ("toctou", "changed", None, False),
        ("export-fails", "export failed", "imported", False),
        ("find-zero", "one exact name/GUID match", "exported", False),
        ("find-two", "one exact name/GUID match", "exported", False),
        ("import-fails", "import_pool failed", "imported", False),
        ("cleanup-fails", "cleanup incomplete", "imported", True),
    ],
)
def test_create_pool_failure_rollbacks_are_partition_scoped(
    scenario, expected_error, rollback_path, destroy_fails,
) -> None:
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
    if rollback_path is None:
        assert compact == [("zpool", "list", "-H", "-o", "name"), ("zpool", "import")]
    else:
        assert compact == expected_create_rollback_trace(
            rollback_path, destroy_fails=destroy_fails,
        )
    if scenario == "toctou":
        assert service.verify_count == 1
        assert not any(command[:2] == ("zpool", "create") for command in service.commands)
    if scenario == "cleanup-fails":
        assert not any(command[:2] == ("zpool", "labelclear") for command in service.commands)


@pytest.mark.parametrize(
    ("scenario", "expected_error"),
    [
        ("create-fails", "synthetic create failure"),
        ("create-timeout", "timed out after 15s"),
        ("wrong-leaf", "cleanup incomplete"),
        ("identity-drift-after-create", "cleanup incomplete"),
        ("find-wrong-name", "one exact name/GUID match"),
        ("initial-scan-timeout", "timed out after 15s scanning exported pools"),
        ("rollback-scan-timeout", "timed out after 15s scanning exported rollback pools"),
        ("rollback-scan-wrong-name", "only captured pool GUID 12345"),
    ],
)
def test_create_pool_additional_rollback_failures_are_closed(scenario, expected_error) -> None:
    helper = load_helper()
    partition = {"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                 "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}
    service = _CreatePoolHarness(helper, scenario)

    with pytest.raises(helper.CallError, match=re.escape(expected_error)):
        asyncio.run(service.create_pool("probe", partition))

    create_commands = [command for command in service.commands if command[:2] == ("zpool", "create")]
    assert bool(create_commands) is (scenario != "initial-scan-timeout")
    if scenario in {"wrong-leaf", "identity-drift-after-create", "rollback-scan-timeout",
                    "rollback-scan-wrong-name"}:
        assert not any(command[:2] == ("zpool", "destroy") for command in service.commands)
        assert not any(command[:2] == ("zpool", "labelclear") for command in service.commands)
    if scenario in {"create-timeout", "initial-scan-timeout", "rollback-scan-timeout"}:
        assert any(timeout == 15 for _, timeout in service.timeouts)


@pytest.mark.parametrize("scenario", ["create-fails", "create-timeout"])
def test_ambiguous_create_failure_stops_for_manual_recovery(scenario) -> None:
    helper = load_helper()
    partition = {"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                 "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}
    service = _CreatePoolHarness(helper, scenario)

    with pytest.raises(helper.CallError, match="manual recovery"):
        asyncio.run(service.create_pool("probe", partition))

    assert any(command[:2] == ("zpool", "create") for command in service.commands)
    assert not any(command[:2] in {("zpool", "destroy"), ("zpool", "labelclear")}
                   for command in service.commands)
    assert not any(command[:2] == ("zpool", "import") and "-d" in command
                   for command in service.commands)


def test_rollback_refuses_to_destroy_a_pool_after_its_guid_changes() -> None:
    helper = load_helper()
    partition = {"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                 "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}
    service = _CreatePoolHarness(helper, "export-fails-foreign-guid")

    with pytest.raises(helper.CallError, match="GUID"):
        asyncio.run(service.create_pool("probe", partition))

    assert service.guid_reads >= 2
    assert not any(command[:2] in {("zpool", "destroy"), ("zpool", "labelclear")}
                   for command in service.commands)


def test_partial_import_registration_uses_middleware_export_and_preserves_pool() -> None:
    helper = load_helper()
    partition = {"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                 "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}
    service = _CreatePoolHarness(helper, "partial-registration")

    with pytest.raises(helper.CallError, match="left exported"):
        asyncio.run(service.create_pool("probe", partition))

    assert not any(command[:2] == ("zpool", "destroy") for command in service.commands)
    assert not any(command[:2] == ("zpool", "labelclear") for command in service.commands)
    assert service.middleware.partial_pool_exported
    assert not service.middleware.stale_pool_record
    assert service.exported
    assert ("pool.export", (7, {"destroy": False})) in service.middleware.calls
    assert any(method == "pool.query" for method, _ in service.middleware.calls)


@pytest.mark.parametrize(
    ("mountpoint", "parent_path", "message"),
    [("/mnt/data", "/dev/vda", "mounted"), (None, "/dev/vdb", "boot-pool disk")],
)
def test_labelclear_target_rechecks_mount_and_boot_disk_parent(mountpoint, parent_path, message) -> None:
    helper = load_helper()
    partition = {"name": "vda4", "path": "/dev/vda4", "parent": "vda",
                 "parent_path": "/dev/vda", "size": 1 << 30, "partuuid": UUID}
    row = partition | {"parent_path": parent_path, "mountpoint": mountpoint, "fstype": "zfs_member"}

    class Service(helper.BootDiskPartitionService):
        async def _verify_partition_identity(self, expected):
            return row

        async def _run(self, *argv, timeout=15):
            assert argv == ("findmnt", "-rn", "-S", f"/dev/disk/by-partuuid/{UUID}")
            return 1, "", ""

    with pytest.raises(helper.CallError, match=message):
        asyncio.run(Service()._verify_labelclear_target(
            partition, f"/dev/disk/by-partuuid/{UUID}",
        ))


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

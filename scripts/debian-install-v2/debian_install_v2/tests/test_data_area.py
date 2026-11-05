"""Hermetic tests for the flexible data area (data_area = none | partition | lvm)."""

from __future__ import annotations

import json
import shlex

import pytest

from debian_install_v2 import layout_policy as lp
from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.config import Config, ConfigError, load_config, validate_config
from debian_install_v2.customscript import build_customscript_bundle
from debian_install_v2.disk_layout import GIB
from debian_install_v2.installer import Installer, InstallerError

from .test_disk_policy import make_planner

S = lp.SECTORS_PER_GIB
LVM_CFG = {
    "vg": "vg_data",
    "pv_size_gb": 200,
    "lvs": [
        {"name": "cow", "size_gb": 120, "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
        {"name": "spare", "size_gb": 40, "fs": "none"},
    ],
}


def _set(installer: Installer, **values) -> None:
    for key, value in values.items():
        object.__setattr__(installer.config, key, value)


# ---------------------------------------------------------------- validation

def test_default_partition_is_the_512_gib_layout():
    area = lp.parse_data_area("partition", None, None)
    assert (area.fs, area.mount, area.label) == ("btrfs", "/srv/cow", "MDT_COW")
    rec = lp.recommended_layout(512, 16)
    assert (rec.root_gib, rec.cow_gib, rec.swap_gib) == (160, 168, 16)
    assert lp.data_area_size_gib(area, 512) == rec.cow_gib == 168
    disk = 512 * S
    swap_start = disk - 2048 - 16 * S
    plan = lp.plan_data_area(area, disk_sectors=disk, root_end=2048 + 160 * S, first_swap_start=swap_start)
    assert plan.size == 168 * S
    assert plan.start + plan.size <= swap_start
    assert plan.reserve_sectors > 150 * S  # the reserve stays between root and data


@pytest.mark.parametrize(
    ("mode", "partition", "lvm", "message"),
    [
        ("zfs", None, None, "none, partition or lvm"),
        ("partition", {"fs": "zfs"}, None, "fs must be one of"),
        ("partition", {"fs": "ntfs"}, None, "fs must be one of"),
        ("partition", {"mount": "srv/cow"}, None, "absolute path"),
        ("partition", {"mount": "/srv/cow/"}, None, "trailing slash"),
        ("partition", {"mount": "/"}, None, "system tree"),
        ("partition", {"mount": "/boot/data"}, None, "system tree"),
        ("partition", {"mount": "/srv/../etc"}, None, "dot segments"),
        ("partition", {"label": "THIS_LABEL_IS_TOO_LONG"}, None, "label"),
        ("partition", {"label": "OTHER"}, None, "label must be MDT_COW"),
        ("partition", {"mount": "/srv/tree-cache"}, None, "system tree"),
        ("partition", {"mount": "/srv/tree-upper/x"}, None, "system tree"),
        ("lvm", None, {"vg": "v", "lvs": [{"name": "a", "size_gb": 1, "fs": "ext4", "mount": "/srv/tree-cache"}]}, "system tree"),
        ("partition", {"size_gb": 0}, None, "integer GiB"),
        ("partition", {"size_gb": 100, "size_fraction": "1/3"}, None, "not both"),
        ("partition", {"size_fraction": "3/2"}, None, "fraction"),
        ("partition", {"bogus": 1}, None, "unknown key"),
        ("lvm", None, None, "requires data_lvm"),
        ("lvm", None, {"vg": "v", "lvs": []}, "non-empty list"),
        ("lvm", None, {"vg": "bad name", "lvs": [{"name": "a", "size_gb": 1, "fs": "none"}]}, "volume group"),
        ("lvm", None, {"vg": "v", "lvs": [{"name": "a", "size_gb": 1, "fs": "zfs"}]}, "zfs is not managed"),
        ("lvm", None, {"vg": "v", "lvs": [{"name": "a", "size_gb": 1, "fs": "ext4"}]}, "needs a mount point"),
        ("lvm", None, {"vg": "v", "lvs": [{"name": "a", "size_gb": 1, "fs": "none", "mount": "/x"}]}, "takes no mount"),
        (
            "lvm", None,
            {"vg": "v", "lvs": [
                {"name": "a", "size_gb": 1, "fs": "ext4", "mount": "/srv/a"},
                {"name": "a", "size_gb": 1, "fs": "ext4", "mount": "/srv/b"},
            ]},
            "used twice",
        ),
        (
            "lvm", None,
            {"vg": "v", "lvs": [
                {"name": "a", "size_gb": 1, "fs": "ext4", "mount": "/srv/a"},
                {"name": "b", "size_gb": 1, "fs": "xfs", "mount": "/srv/a"},
            ]},
            "mount point collision",
        ),
        (
            "lvm", None,
            {"vg": "v", "lvs": [
                {"name": "a", "size_gb": 1, "fs": "ext4", "mount": "/srv"},
                {"name": "b", "size_gb": 1, "fs": "xfs", "mount": "/srv/b"},
            ]},
            "mount point collision",
        ),
    ],
)
def test_parse_refusals(mode, partition, lvm, message):
    with pytest.raises(lp.LayoutPolicyError, match=message):
        lp.parse_data_area(mode, partition, lvm)


def test_lvm_sizes_must_fit_the_pv_and_leave_free_vg_space():
    disk = 512 * S
    swap_start = disk - 2048 - 16 * S
    kwargs = {"disk_sectors": disk, "root_end": 2048 + 160 * S, "first_swap_start": swap_start}
    ok = lp.parse_data_area("lvm", None, LVM_CFG)
    plan = lp.plan_data_area(ok, **kwargs)
    assert plan.size == 200 * S
    assert plan.lv_free_gib == 200 - lp.LVM_METADATA_GIB - 160
    too_big = {**LVM_CFG, "lvs": [{**LVM_CFG["lvs"][0], "size_gb": 200}]}
    with pytest.raises(lp.LayoutPolicyError, match="exceed the physical volume"):
        lp.plan_data_area(lp.parse_data_area("lvm", None, too_big), **kwargs)
    exact = {**LVM_CFG, "lvs": [{**LVM_CFG["lvs"][0], "size_gb": 200 - lp.LVM_METADATA_GIB}]}
    with pytest.raises(lp.LayoutPolicyError, match="no free VG space"):
        lp.plan_data_area(lp.parse_data_area("lvm", None, exact), **kwargs)


def test_data_area_must_fit_between_root_and_swap():
    disk = 512 * S
    area = lp.parse_data_area("partition", {"size_gb": 400}, None)
    with pytest.raises(lp.LayoutPolicyError, match="does not fit"):
        lp.plan_data_area(area, disk_sectors=disk, root_end=2048 + 160 * S, first_swap_start=disk - 2048 - 16 * S)


def test_config_prereqs_swap_at_end_and_capped_root():
    base = {"data_area": "partition"}
    with pytest.raises(ConfigError, match="capped root"):
        load_config(raw_json=json.dumps(base))
    with pytest.raises(ConfigError, match="swap_policy=on"):
        load_config(raw_json=json.dumps({**base, "disk_layout": "dev-host", "swap_policy": "untouched"}))
    with pytest.raises(ConfigError, match="swap_policy=on"):
        load_config(raw_json=json.dumps({"data_area": "lvm", "data_lvm": LVM_CFG, "root_max_size_gb": 100, "swap_policy": "off"}))
    with pytest.raises(ConfigError, match="root_resize_policy=resize"):
        load_config(raw_json=json.dumps({**base, "root_max_size_gb": 100, "root_resize_policy": "grow-only"}))
    ok = load_config(raw_json=json.dumps({**base, "disk_layout": "dev-host"}))
    assert ok.data_area == "partition"
    assert load_config(raw_json="{}").data_area == "none"


# ---------------------------------------------------------------- packages

def test_package_set_follows_the_choice():
    assert lp.data_area_packages(lp.parse_data_area("none", None, None)) == []
    assert lp.data_area_packages(lp.parse_data_area("partition", None, None)) == ["btrfs-progs"]
    assert lp.data_area_packages(lp.parse_data_area("partition", {"fs": "xfs"}, None)) == ["xfsprogs"]
    assert lp.data_area_packages(lp.parse_data_area("partition", {"fs": "ext4"}, None)) == ["util-linux"]
    assert lp.data_area_packages(lp.parse_data_area("lvm", None, LVM_CFG)) == ["lvm2", "btrfs-progs"]


# ---------------------------------------------------------------- planner

def _planner(tmp_path, mode="partition", partition=None, lvm=None):
    installer = make_planner(
        tmp_path, disk_gib=512, root_gib=500, swap_disk_total_gb=16, swap_partition_count=4,
    )
    _set(installer, disk_layout="dev-host", data_area=mode, data_partition=partition or {}, data_lvm=lvm or {})
    return installer


def test_sfdisk_plan_places_root_reserve_data_swap_in_that_order(tmp_path):
    installer = _planner(tmp_path)
    swap, root = installer._plan_swap_partitions()
    assert root * 512 == 160 * GIB
    plan = installer._write_sfdisk_plan(swap, root)
    entries = installer._parse_partition_entries(plan)
    data = [e for e in entries.values() if e.get("name") == "MDT_COW"]
    assert len(data) == 1
    start, size = int(data[0]["start"]), int(data[0]["size"])
    assert size == 168 * S
    assert data[0]["type"].upper() == lp.LINUX_FS_TYPE_GUID
    root_entry = entries[3]
    assert int(root_entry["start"]) + int(root_entry["size"]) < start  # reserve in between
    assert start + size <= swap[0][0]
    assert swap[0][0] - (start + size) < 4096  # data sits directly in front of swap
    numbers = sorted(entries)
    assert len(numbers) == len(set(numbers)) and 3 in numbers
    disk_sectors = 512 * S
    assert disk_sectors - (swap[-1][0] + swap[-1][1]) <= lp.SWAP_END_SLACK_SECTORS  # swap stays last
    installer._validate_plan_geometry(installer._parse_partition_entries(
        installer._current_partition_dump(2048, 500 * S)), entries, root)


def test_sfdisk_plan_lvm_uses_pv_type_and_label(tmp_path):
    installer = _planner(tmp_path, "lvm", lvm=LVM_CFG)
    swap, root = installer._plan_swap_partitions()
    plan = installer._write_sfdisk_plan(swap, root)
    pv = [e for e in installer._parse_partition_entries(plan).values() if e.get("name") == lp.LVM_PV_PARTLABEL]
    assert len(pv) == 1 and int(pv[0]["size"]) == 200 * S
    assert pv[0]["type"].upper() == lp.LINUX_LVM_TYPE_GUID


def test_planner_refuses_unfitting_data_area_and_writes_nothing(tmp_path):
    installer = _planner(tmp_path, partition={"size_gb": 400})
    with pytest.raises(InstallerError, match="does not fit"):
        installer._plan_swap_partitions()
    installer = _planner(tmp_path, "lvm", lvm={**LVM_CFG, "lvs": [{**LVM_CFG["lvs"][0], "size_gb": 300}]})
    with pytest.raises(InstallerError, match="exceed the physical volume"):
        installer._plan_swap_partitions()


def test_planner_refuses_data_area_without_managed_swap(tmp_path):
    installer = make_planner(tmp_path, disk_gib=512, root_gib=500, swap_policy="off", root_max_size_gb=100)
    _set(installer, data_area="partition")
    with pytest.raises(InstallerError, match="need managed tail swap"):
        installer._plan_swap_partitions()


def test_existing_installer_data_partition_is_not_a_foreign_partition(tmp_path):
    installer = _planner(tmp_path)
    swap, root = installer._plan_swap_partitions()
    plan = installer._write_sfdisk_plan(swap, root)
    entries = installer._parse_partition_entries(plan)
    entries[3]["size"] = str(root)
    installer._live_partition_entries = lambda *_a: entries  # second run: the table already holds data + swap
    installer._disk_facts = lambda: (512 * S, 2048, root)
    swap2, root2 = installer._plan_swap_partitions()
    assert (swap2, root2) == (swap, root)
    # without data_area configured the same table is a foreign partition after root and is refused
    _set(installer, data_area="none")
    with pytest.raises(InstallerError, match="non-swap partition"):
        installer._plan_swap_partitions()


# ---------------------------------------------------------------- stage two commands (dry-run, allowlist checked)

def _argvs(installer):
    return [list(action.argv) for action in installer.actions.planned]


def _stage_two(tmp_path, mode, partition=None, lvm=None):
    installer = _planner(tmp_path, mode, partition, lvm)
    installer._read_fstab_lines = lambda: ["UUID=abc / ext4 defaults 0 1"]
    installer._apt_get = lambda *a, **k: None
    installer._configure_data_area()
    return installer


def test_partition_commands_pass_the_allowlist(tmp_path):
    installer = _stage_two(tmp_path, "partition")
    argvs = _argvs(installer)
    assert ["/usr/sbin/mkfs.btrfs", "-L", "MDT_COW", "/dev/vda8"] in argvs or any(
        a[0].endswith("mkfs.btrfs") and a[-2:] != [] and a[1:3] == ["-L", "MDT_COW"] for a in argvs
    )
    assert any(a[0].endswith("mount") and a[1] == "/srv/cow" for a in argvs)
    written = installer.actions.dry_run_writes["/etc/fstab"]
    assert "LABEL=MDT_COW /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in written  # dry run: no UUID yet
    assert "UUID=abc / ext4" in written


def test_lvm_commands_pass_the_allowlist_and_leave_spare_unformatted(tmp_path):
    installer = _stage_two(tmp_path, "lvm", lvm=LVM_CFG)
    argvs = _argvs(installer)
    names = [a[0].rsplit("/", 1)[-1] for a in argvs]
    for tool in ("pvcreate", "vgcreate", "lvcreate", "mkfs.btrfs"):
        assert tool in names
    assert sum(1 for n in names if n == "lvcreate") == 2
    assert names.count("mkfs.btrfs") == 1 and "mkfs.ext4" not in names  # spare LV: no mkfs
    assert ["/usr/sbin/lvcreate", "-y", "-n", "cow", "-L", "120G", "vg_data"] in argvs
    written = installer.actions.dry_run_writes["/etc/fstab"]
    assert "/dev/vg_data/cow /srv/cow btrfs" in written and "spare" not in written


def test_new_commands_are_allowlisted_strictly():
    actions = HostActions(dry_run=True)
    actions.run(["/usr/sbin/lvcreate", "-y", "-n", "x", "-L", "1G", "vg"])
    with pytest.raises(ActionError):
        actions.run(["/usr/sbin/lvcreate", "--force", "-n", "x", "vg"])
    with pytest.raises(ActionError):
        actions.run(["/usr/sbin/mkfs.btrfs", "--rootdir", "/", "/dev/x"])
    with pytest.raises(ActionError):
        actions.run(["/usr/sbin/vgcreate", "-f", "vg", "/dev/x"])


class FakeActions(HostActions):
    """Non-dry-run runner with scripted blkid/vgs/lvs/findmnt answers; records every command."""

    def __init__(self, signatures=None, vg_pvs="", lvs="", blkid_errors=None, label_devices=(), uuids=None):
        super().__init__(dry_run=False)
        self.blkid_errors = dict(blkid_errors or {})
        self.label_devices = list(label_devices)
        self.uuids = dict(uuids or {})
        self.signatures = dict(signatures or {})
        self.vg_pvs, self.lvs = vg_pvs, lvs
        self.calls: list[list[str]] = []
        self.files: dict[str, str] = {}

    def run(self, argv, description="", dangerous=False, input=None, timeout=None):
        self._validate(list(argv))
        self.calls.append(list(argv))
        tool = argv[0].rsplit("/", 1)[-1]
        if tool == "blkid":
            device = argv[-1]
            if device in self.blkid_errors:
                raise ActionError(f"action failed ({self.blkid_errors[device]}): blkid")
            if "-t" in argv:
                return "".join(f"{d}\n" for d in self.label_devices)
            if "UUID" in argv and device in self.uuids:
                return self.uuids[device] + "\n"
            if "TYPE" in argv and self.signatures.get(device):
                return self.signatures[device] + "\n"
            raise ActionError("action failed (2): blkid")
        if tool == "vgs":
            if not self.vg_pvs:
                raise ActionError("action failed (5): vgs")
            return self.vg_pvs
        if tool == "lvs":
            return self.lvs
        if tool == "findmnt":
            raise ActionError("action failed (1): not mounted")
        return ""

    def write_file(self, path, content, mode=0o644):
        self.files[path] = content


def _real_stage_two(tmp_path, actions, mode, partition=None, lvm=None):
    installer = _planner(tmp_path, mode, partition, lvm)
    swap, root = installer._plan_swap_partitions()
    entries = installer._parse_partition_entries(installer._write_sfdisk_plan(swap, root))
    entries[3]["size"] = str(root)
    installer.actions = actions
    installer._live_partition_entries = lambda *_a: entries
    installer._disk_facts = lambda: (512 * S, 2048, root)
    installer._read_fstab_lines = lambda: []
    installer._apt_get = lambda *a, **k: None
    installer._mark_step = lambda *a, **k: None
    installer._plan_swap_partitions = lambda **_k: (swap, root)
    return installer


def test_blank_partition_is_formatted_once_and_existing_same_fs_is_kept(tmp_path):
    actions = FakeActions()
    _real_stage_two(tmp_path, actions, "partition")._configure_data_area()
    assert any(c[0].endswith("mkfs.btrfs") for c in actions.calls)
    actions = FakeActions(signatures={"/dev/vda8": "btrfs"})
    _real_stage_two(tmp_path, actions, "partition")._configure_data_area()  # rerun: idempotent
    assert not any(c[0].rsplit("/", 1)[-1].startswith("mkfs") for c in actions.calls)


def test_foreign_signature_is_refused_never_overwritten(tmp_path):
    actions = FakeActions(signatures={"/dev/vda8": "ext4"})
    with pytest.raises(InstallerError, match="already holds a ext4 signature"):
        _real_stage_two(tmp_path, actions, "partition")._configure_data_area()
    assert not any(c[0].rsplit("/", 1)[-1].startswith("mkfs") for c in actions.calls)
    actions = FakeActions(signatures={"/dev/vda8": "crypto_LUKS"})
    with pytest.raises(InstallerError, match="LVM physical volume"):
        _real_stage_two(tmp_path, actions, "lvm", lvm=LVM_CFG)._configure_data_area()
    assert not any(c[0].rsplit("/", 1)[-1] == "pvcreate" for c in actions.calls)


def test_lvm_rerun_creates_only_what_is_missing(tmp_path):
    actions = FakeActions(
        signatures={"/dev/vda8": "LVM2_member", "/dev/vg_data/cow": "btrfs"},
        vg_pvs="/dev/vda8", lvs="cow",
    )
    _real_stage_two(tmp_path, actions, "lvm", lvm=LVM_CFG)._configure_data_area()
    tools = [c[0].rsplit("/", 1)[-1] for c in actions.calls]
    assert "pvcreate" not in tools and "vgcreate" not in tools and "mkfs.btrfs" not in tools
    assert tools.count("lvcreate") == 1  # only the missing spare LV
    actions = FakeActions(signatures={"/dev/vda8": "LVM2_member"}, vg_pvs="/dev/vdb1")
    with pytest.raises(InstallerError, match="already exists on other physical volumes"):
        _real_stage_two(tmp_path, actions, "lvm", lvm=LVM_CFG)._configure_data_area()


def test_blkid_failure_is_not_blank_fail_closed(tmp_path):
    actions = FakeActions(blkid_errors={"/dev/vda8": 4})  # probe error, not "no signature"
    with pytest.raises(InstallerError, match="cannot determine whether /dev/vda8 is blank"):
        _real_stage_two(tmp_path, actions, "partition")._configure_data_area()
    assert not any(c[0].rsplit("/", 1)[-1].startswith("mkfs") for c in actions.calls)
    actions = FakeActions(blkid_errors={"/dev/vda8": 4})
    with pytest.raises(InstallerError, match="cannot determine whether"):
        _real_stage_two(tmp_path, actions, "lvm", lvm=LVM_CFG)._configure_data_area()
    assert not any(c[0].rsplit("/", 1)[-1] == "pvcreate" for c in actions.calls)


def test_fstab_uses_uuid_and_ambiguous_label_is_refused(tmp_path):
    actions = FakeActions(uuids={"/dev/vda8": "1234-abcd"}, label_devices=["/dev/vda8"])
    _real_stage_two(tmp_path, actions, "partition")._configure_data_area()
    assert "UUID=1234-abcd /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in actions.files["/etc/fstab"]
    actions = FakeActions(uuids={"/dev/vda8": "1234-abcd"}, label_devices=["/dev/vda8", "/dev/vdb1"])
    with pytest.raises(InstallerError, match="also on /dev/vdb1"):
        _real_stage_two(tmp_path, actions, "partition")._configure_data_area()
    assert "/etc/fstab" not in actions.files


def test_rerun_with_changed_swap_count_cannot_disturb_a_populated_data_partition(tmp_path):
    old = _planner(tmp_path)
    swap, root = old._plan_swap_partitions()
    current = old._parse_partition_entries(old._write_sfdisk_plan(swap, root))
    new = make_planner(tmp_path, disk_gib=512, root_gib=500, swap_disk_total_gb=16, swap_partition_count=2)
    _set(new, disk_layout="dev-host", data_area="partition", data_partition={}, data_lvm={})
    swap2, root2 = new._plan_swap_partitions()
    planned = new._parse_partition_entries(new._write_sfdisk_plan(swap2, root2))
    data_number = next(n for n, e in current.items() if e.get("name") == "MDT_COW")
    # populated: refused with a clear error
    new.actions = FakeActions(signatures={f"/dev/vda{data_number}": "btrfs"})
    with pytest.raises(InstallerError, match="refusing to delete or resize data-area partition"):
        new._refuse_to_disturb_data_partitions(current, planned)
    # unreadable probe counts as in use
    new.actions = FakeActions(blkid_errors={f"/dev/vda{data_number}": 1})
    with pytest.raises(InstallerError, match="cannot determine"):
        new._refuse_to_disturb_data_partitions(current, planned)
    # provably blank (blkid exit 2): may be re-planned
    new.actions = FakeActions()
    new._refuse_to_disturb_data_partitions(current, planned)
    # unchanged layout: never probed, never refused
    new.actions = FakeActions(signatures={f"/dev/vda{data_number}": "btrfs"})
    new._refuse_to_disturb_data_partitions(current, current)


# ---------------------------------------------------------------- customscript

def test_customscript_carries_the_data_area_config():
    config = Config(
        disk_layout="dev-host", data_area="lvm", data_lvm=LVM_CFG,
        telegram_bot_token="", telegram_chat_id="",
    )
    bundle = build_customscript_bundle(config)
    assert bundle["config"]["data_area"] == "lvm"
    token = next(
        part for part in shlex.split(bundle["customScript"]) if part.startswith("VBPUB_CONFIG_EXTRA_JSON=")
    )
    embedded = json.loads(token.split("=", 1)[1])
    assert embedded["data_lvm"]["vg"] == "vg_data"
    assert embedded["data_lvm"]["lvs"][1]["fs"] == "none"
    # the host side loads exactly what the producer embedded
    assert load_config(raw_json=json.dumps(embedded)).data_lvm == LVM_CFG


def test_customscript_refuses_an_invalid_data_area():
    config = Config(disk_layout="dev-host", data_area="lvm", data_lvm={"vg": "v", "lvs": []},
                    telegram_bot_token="", telegram_chat_id="")
    with pytest.raises(ConfigError, match="non-empty list"):
        build_customscript_bundle(config)
    with pytest.raises(ConfigError):
        validate_config(Config(data_area="partition", telegram_bot_token="", telegram_chat_id=""))

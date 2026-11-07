"""IOB-R2-01 (group B): oracles for ``iobench.guard`` (evidence selection, violation set, marker text, identity rules, sizes)."""
import dataclasses
import json

import pytest

from iobench import guard
from iobench.errors import Refusal
from iobench.host import RunResult
from iobench.testing import GIB, R1002_SIZE, standard_host

DEV = "/dev/vda7"
CREATED = "2026-10-21T03:00:00Z"
ROOT_UUID = "11111111-1111-1111-1111-111111111111"


def conditions(host, path=DEV):
    return [v.condition for v in guard.violations(host, guard.read_device(host, path))]


# -- immutable value records ----------------------------------------------------------------------------------------
def test_the_guard_records_are_immutable():
    host = standard_host()
    facts = guard.read_device(host, DEV)
    fs = guard.fs_info(host, "/bench")
    records = [facts.chain, facts, guard.Violation("c", "m"), guard.Target("device", DEV, 1), fs,
               guard.check_workdir(host, "/bench/w", planned_volume=GIB, installer=False, confirm_quiet_window=False, target_disk="vda")]
    for record in records:
        field = dataclasses.fields(record)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, field, None)


# -- mount evidence -------------------------------------------------------------------------------------------------
def test_a_mount_entry_touches_a_device_by_its_number_or_by_its_source_name_alone():
    host = standard_host()
    by_number = guard.MountEntry("254:7", "/a", "ext4", "/dev/other", "rw")
    by_name = guard.MountEntry("0:99", "/b", "ext4", "/dev/vda7", "rw")
    neither = guard.MountEntry("0:98", "/c", "ext4", "/dev/vdz", "rw")
    touching = guard._touching(host, [by_number, by_name, neither], {"254:7"}, {"/dev/vda7"})
    assert {e.mountpoint for e in touching} == {"/a", "/b"}
    assert {e.devno for e in touching} == {"254:7"}


def test_a_mount_entry_touches_a_device_by_its_resolved_devno_alone():
    from test_tgt import btrfs_file_host

    host, _ = btrfs_file_host()
    # 0:52 is not a sysfs block number, but this source resolves to 254:8.
    resolved_only = guard.MountEntry("0:52", "/resolved-only", "btrfs", "/dev/vda8", "rw")
    touching = guard._touching(host, [resolved_only], {"254:8"}, set())
    assert {entry.mountpoint for entry in touching} == {"/resolved-only"}
    assert {entry.devno for entry in touching} == {"254:8"}


# -- the violation set -----------------------------------------------------------------------------------------------
def test_an_unmounted_holder_is_reported_as_active_and_a_mounted_one_only_as_a_holder():
    host = standard_host()
    host.add_holder(DEV, "dm-0", 253, 0, GIB)
    assert conditions(host) == ["holder_active"]
    host.add_mount("253:0", "/srv", "/dev/dm-0")
    assert conditions(host) == ["holder"]


def test_an_unmounted_holder_is_reported_beside_other_violations():
    host = standard_host()
    host.add_holder(DEV, "dm-0", 253, 0, GIB)
    host.add_mount("254:7", "/x", "/dev/vda7")
    assert conditions(host) == ["mounted", "holder_active"]


def test_a_signature_seen_only_by_blkid_is_a_violation_naming_it():
    host = standard_host()
    host.devices[DEV].signatures.append("ext4")
    host.probe_failures["wipefs"] = RunResult(0, "", "")
    found = guard.violations(host, guard.read_device(host, DEV))
    assert [v.condition for v in found] == ["signature"] and found[0].overridable
    assert "TYPE=ext4" in found[0].message


def test_a_signature_seen_only_by_wipefs_is_a_violation_with_a_generic_name():
    host = standard_host()
    host.devices[DEV].signatures.append("ext4")
    host.probe_failures["blkid"] = RunResult(2, "", "")
    found = guard.violations(host, guard.read_device(host, DEV))
    assert [v.condition for v in found] == ["signature"]
    assert "a wipefs signature" in found[0].message


# -- marker text ----------------------------------------------------------------------------------------------------------
def test_the_marker_text_is_canonical_json_with_sorted_keys():
    text = guard.marker_text(10, "pu", "ser", CREATED, inode=3)
    assert text == json.dumps(json.loads(text), sort_keys=True)
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_only_a_json_object_with_the_magic_is_a_marker():
    good = guard.marker_text(10, None, None, CREATED)
    assert guard.parse_marker(good)["size_bytes"] == 10
    assert guard.parse_marker(json.dumps({"magic": "something-else"})) is None
    assert guard.parse_marker(json.dumps([guard.MARKER_MAGIC])) is None
    assert guard.parse_marker("not json") is None


# -- identity ---------------------------------------------------------------------------------------------------------
def test_a_device_with_a_serial_but_no_partuuid_is_never_accepted_on_an_unset_partuuid_expectation():
    host = standard_host()
    host.add_disk("vdb", 8, 10 * GIB, serial="SER-1")
    host.add_partition("vdb", "vdb1", 8, 1, 4 * GIB, None)
    args = dict(own=True, expect_size=4 * GIB, expect_partuuid=None, created=CREATED)
    with pytest.raises(Refusal) as err:
        guard.init_device(host, "/dev/vdb1", expect_serial="WRONG", **args)
    assert err.value.condition == "expect_identity_mismatch"
    assert guard.init_device(host, "/dev/vdb1", expect_serial="SER-1", **args).serial == "SER-1"


# -- the root filesystem ----------------------------------------------------------------------------------------------
def _fs(devno, source):
    return guard.FsInfo("/x", source, "ext4", "rw", devno, None, 10, 5)


def test_a_filesystem_is_the_root_filesystem_by_device_number_or_by_backing_source():
    host = standard_host()  # root: 254:3 on /dev/vda3
    assert guard.on_root_fs(host, _fs("254:3", "/dev/other")) is True  # the same device number
    assert guard.on_root_fs(host, _fs("0:44", "/dev/vda3")) is True  # a subvolume or bind mount: another number, the same source
    assert guard.on_root_fs(host, _fs("0:44", "/dev/other")) is False
    assert guard.on_root_fs(host, _fs("0:44", "tmpfs")) is False
    host.mounts = [m for m in host.mounts if m[2] != "/"]
    assert guard.on_root_fs(host, _fs("254:3", "/dev/vda3")) is False


def test_a_btrfs_work_filesystem_uses_the_same_resolved_devno_as_root(monkeypatch):
    from test_tgt import btrfs_file_host

    host, _ = btrfs_file_host()
    host.mounts = [
        (ns, "0:52", "/", "/dev/root-alias", "btrfs", opts) if mp == "/"
        else (ns, "0:53", mp, "/dev/vda8", "btrfs", opts) if mp == "/bench"
        else (ns, devno, mp, source, fstype, opts)
        for ns, devno, mp, source, fstype, opts in host.mounts
    ]
    real_devno = host.devno
    monkeypatch.setattr(host, "devno", lambda path: "254:8" if path in ("/dev/root-alias", "/dev/vda8") else real_devno(path))
    fs = guard.fs_info(host, "/bench")
    assert fs.devno == "254:8" and fs.raw_devno == "0:53"
    assert guard.on_root_fs(host, fs) is True


def _root_host(free):
    host = standard_host()
    host.set_fs("/", 40 * GIB, free)
    host.devices["/dev/vda3"].fs_uuid = ROOT_UUID
    host.makedirs("/var/lib/iobench")
    return host


def _root_init(host, size):
    return guard.init_file(host, "/var/lib/iobench/t.img", size=size, own=True, expect_fs_uuid=ROOT_UUID, confirm_quiet_window=True,
                           allow_large_root_target=False, created=CREATED)


def test_a_root_file_target_of_exactly_ten_percent_of_the_free_space_is_allowed_and_one_byte_more_is_not():
    free = 20 * GIB
    assert _root_init(_root_host(free), free // 10).size_bytes == free // 10
    with pytest.raises(Refusal) as err:
        _root_init(_root_host(free), free // 10 + (1 << 20))
    assert err.value.condition == "root_target_too_large"


# -- the work dir -------------------------------------------------------------------------------------------------------
def _workdir(free, planned=GIB):
    host = standard_host(workdir_free=free)
    return guard.check_workdir(host, "/bench/w", planned_volume=planned, installer=False, confirm_quiet_window=False, target_disk="vda")


def test_a_work_dir_with_exactly_the_required_free_space_is_accepted_and_one_byte_less_is_refused():
    assert _workdir(4 * GIB).path == "/bench/w"  # the 4 GiB floor
    with pytest.raises(Refusal) as err:
        _workdir(4 * GIB - 1)
    assert err.value.condition == "workdir_free_space"
    assert _workdir(6 * GIB, planned=2 * GIB).fs.free == 6 * GIB  # 3 x the planned volume
    with pytest.raises(Refusal):
        _workdir(6 * GIB - 1, planned=2 * GIB)

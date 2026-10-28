"""Device resolution on a fake sysfs/procfs/dev tree: partitions, LVM, dm-crypt, md, btrfs (anonymous st_dev, via mountinfo and
/sys/fs/btrfs), unknown filesystems (fail with the facts seen), and explicit `devices` that are symlinks or do not exist."""

from __future__ import annotations

import os
from pathlib import Path

from conftest import write_file

from iogov.config import from_mapping
from iogov.devices import detect, resolve_path
from iogov.runner import build, plan_text


class FakeSys:
    def __init__(self, root: Path):
        self.root = root
        self.sys = root / "sys"
        self.proc = root / "proc"
        self.dev = root / "dev"
        self.data = root / "data"
        self.data.mkdir(parents=True)
        (self.sys / "class/block").mkdir(parents=True)
        (self.sys / "dev/block").mkdir(parents=True)
        self.dev.mkdir()
        self._n = 0

    def _num(self) -> str:
        self._n += 1
        return f"250:{self._n}"

    def _blk(self, path: str, size: int = 2048) -> Path:
        d = self.sys / "devices" / path
        write_file(d / "size", f"{size}\n")
        name = d.name
        rel = os.path.relpath(d, self.sys / "class/block")
        os.symlink(rel, self.sys / "class/block" / name)
        os.symlink(f"../../class/block/{name}", self.sys / "dev/block" / self._num())
        return d

    def disk(self, name: str) -> None:
        self._blk(name)
        write_file(self.dev / name, "")

    def part(self, disk: str, name: str) -> None:
        write_file(self._blk(f"{disk}/{name}") / "partition", "1\n")

    def mapped(self, name: str, slaves: list[str], dmname: str | None = None, kind: str = "dm") -> None:
        d = self._blk(f"virtual/block/{name}")
        (d / "slaves").mkdir()
        for s in slaves:
            os.symlink(f"../../../../../class/block/{s}", d / "slaves" / s)
        if dmname:
            write_file(d / "dm/name", dmname + "\n")

    def btrfs(self, uuid: str, members: list[str]) -> None:
        for m in members:
            d = self.sys / "fs/btrfs" / uuid / "devices"
            d.mkdir(parents=True, exist_ok=True)
            os.symlink(f"../../../../class/block/{m}", d / m)

    def anon_fs(self, rows: str) -> None:
        write_file(self.proc / "self/mountinfo", rows)

    def link_stdev(self, kname: str) -> None:
        st = os.stat(self.data)
        os.symlink(f"../../class/block/{kname}", self.sys / "dev/block" / f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}")

    def mount_line(self, fstype: str, source: str) -> str:
        return f"36 25 0:48 /sub {self.data} rw,relatime shared:1 - {fstype} {source} rw\n"

    def resolve(self):
        return resolve_path(str(self.sys), str(self.data), str(self.proc), str(self.dev))

    def cfg(self, **kw):
        return from_mapping({"sysfs_root": str(self.sys), "procfs_root": str(self.proc), "dev_root": str(self.dev), **kw})


def fs(tmp_path) -> FakeSys:
    return FakeSys(tmp_path / "fake")


def test_plain_partition(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    f.part("vda", "vda1")
    f.link_stdev("vda1")
    assert f.resolve()[0] == {"vda"}


def test_nvme_partition_naming(tmp_path):
    f = fs(tmp_path)
    f.disk("nvme0n1")
    f.part("nvme0n1", "nvme0n1p2")
    f.link_stdev("nvme0n1p2")
    assert f.resolve()[0] == {"nvme0n1"}


def test_lvm_over_a_partition_of_one_disk(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    f.part("vda", "vda5")
    f.mapped("dm-0", ["vda5"], "vg-root")
    f.link_stdev("dm-0")
    assert f.resolve()[0] == {"vda"}


def test_lvm_over_multiple_pvs(tmp_path):
    f = fs(tmp_path)
    for d in ("vda", "vdb"):
        f.disk(d)
    f.part("vda", "vda5")
    f.mapped("dm-0", ["vda5", "vdb"], "vg-data")
    f.link_stdev("dm-0")
    assert f.resolve()[0] == {"vda", "vdb"}


def test_dm_crypt_on_lvm_on_disk(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    f.part("vda", "vda3")
    f.mapped("dm-0", ["vda3"], "vg-lv")
    f.mapped("dm-1", ["dm-0"], "cryptdata")
    f.link_stdev("dm-1")
    assert f.resolve()[0] == {"vda"}


def test_md_raid1(tmp_path):
    f = fs(tmp_path)
    for d in ("sda", "sdb"):
        f.disk(d)
    f.part("sda", "sda1")
    f.part("sdb", "sdb1")
    f.mapped("md0", ["sda1", "sdb1"], kind="md")
    f.link_stdev("md0")
    assert f.resolve()[0] == {"sda", "sdb"}


def test_btrfs_single_device_on_lvm_the_host_case(tmp_path):
    """st_dev is anonymous (no /sys/dev/block entry): mountinfo gives /dev/mapper/vg-games, /sys/fs/btrfs lists its member dm-0."""
    f = fs(tmp_path)
    f.disk("vda")
    f.part("vda", "vda5")
    f.mapped("dm-0", ["vda5"], "vg-games")
    f.mapped("dm-1", ["vda5"], "vg-root")
    f.btrfs("11111111-2222-3333-4444-555555555555", ["dm-0"])
    f.anon_fs("20 1 254:1 / / rw - ext4 /dev/mapper/vg-root rw\n" + f.mount_line("btrfs", "/dev/mapper/vg-games"))
    found, seen = f.resolve()
    assert found == {"vda"}
    assert any("btrfs" in s for s in seen)
    cfg = f.cfg(data_paths=[str(f.data)])
    disks, notes = detect(cfg)
    assert [d.kname for d in disks] == ["vda"] and notes == []


def test_btrfs_multi_device_uses_every_member(tmp_path):
    f = fs(tmp_path)
    for d in ("vdb", "vdc"):
        f.disk(d)
    f.btrfs("aaaa", ["vdb", "vdc"])
    f.anon_fs(f.mount_line("btrfs", "/dev/vdb"))  # mountinfo names only one member
    assert f.resolve()[0] == {"vdb", "vdc"}


def test_btrfs_member_on_lvm_over_two_disks_plus_a_plain_disk(tmp_path):
    f = fs(tmp_path)
    for d in ("vda", "vdb", "vdc"):
        f.disk(d)
    f.mapped("dm-0", ["vda", "vdb"], "vg-x")
    f.btrfs("bbbb", ["dm-0", "vdc"])
    f.anon_fs(f.mount_line("btrfs", "/dev/mapper/vg-x"))
    assert f.resolve()[0] == {"vda", "vdb", "vdc"}


def test_btrfs_subvolume_mount_with_a_stacked_longer_mountpoint_wins(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    f.disk("vdb")
    f.btrfs("cccc", ["vda"])
    f.btrfs("dddd", ["vdb"])
    outer = f"36 25 0:48 / {f.data.parent} rw - btrfs /dev/vda rw\n"
    f.anon_fs(outer + f.mount_line("btrfs", "/dev/vdb"))
    assert f.resolve()[0] == {"vdb"}


def test_mount_point_with_an_escaped_space(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    f.btrfs("eeee", ["vda"])
    spaced = f.data.parent / "my data"
    spaced.mkdir()
    f.data = spaced
    f.anon_fs(f"36 25 0:48 / {str(spaced).replace(' ', chr(92) + '040')} rw - btrfs /dev/vda rw\n")
    assert f.resolve()[0] == {"vda"}


def test_unknown_filesystem_fails_with_what_was_seen(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    f.anon_fs(f.mount_line("zfs", "tank/games"))
    found, seen = f.resolve()
    assert found == set()
    text = " | ".join(seen)
    assert "st_dev" in text and "fstype=zfs" in text and "tank/games" in text
    disks, notes = detect(f.cfg(data_paths=[str(f.data)]))
    assert disks == [] and len(notes) == 1
    assert "not guessed" in notes[0] and "fstype=zfs" in notes[0] and "devices = [" in notes[0]


def test_no_mount_entry_and_btrfs_source_without_sysfs_members_fail(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    assert f.resolve()[0] == set()  # no mountinfo at all
    f.anon_fs(f.mount_line("btrfs", "/dev/vda"))
    found, seen = f.resolve()  # no /sys/fs/btrfs entry: never fall back to "the source is the disk"
    assert found == set() and any("none found" in s for s in seen)


def test_slave_without_a_sysfs_entry_is_unresolved(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    d = f.mapped("dm-0", [], "vg-broken")
    f.link_stdev("dm-0")
    assert f.resolve()[0] == set()  # a dm device with no slaves is not a disk


def test_ordinary_fs_with_a_mount_source_but_anonymous_st_dev(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    f.part("vda", "vda2")
    write_file(f.dev / "vda2", "")
    f.anon_fs(f.mount_line("ext4", "/dev/vda2"))
    assert f.resolve()[0] == {"vda"}


# --- explicit devices -----------------------------------------------------------------------------------------------


def test_explicit_by_id_symlink_is_resolved_before_use(tmp_path, env):
    f = fs(tmp_path)
    f.disk("vda")
    (f.dev / "disk/by-path").mkdir(parents=True)
    os.symlink("../../vda", f.dev / "disk/by-path/pci-0000:00:10.0")
    node = str(f.dev / "disk/by-path/pci-0000:00:10.0")
    cfg = f.cfg(devices=[node], state_dir=str(tmp_path / "st"), scratch_dir=str(tmp_path / "sc"), allow_regular_files_as_devices=True)
    disks, notes = detect(cfg)
    assert [(d.kname, d.node) for d in disks] == [("vda", node)] and notes == []
    text = plan_text(build(cfg, env))
    assert "size 1048576 B" in text and "size ? B" not in text


def test_explicit_dangling_or_wrong_directory_names_the_problem(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    (f.dev / "disk/by-path").mkdir(parents=True)
    os.symlink("../../vda", f.dev / "disk/by-path/pci-0000:00:10.0")
    wrong = str(f.dev / "disk/by-id/pci-0000:00:10.0")  # the observed host mistake: a by-path name given as by-id
    disks, notes = detect(f.cfg(devices=[wrong]))
    assert disks == [] and len(notes) == 1
    assert "does not exist" in notes[0] and "by-path" in notes[0] and "use " in notes[0]
    (f.dev / "disk/by-id").mkdir()
    os.symlink("../../gone", f.dev / "disk/by-id/ata-x")
    disks, notes = detect(f.cfg(devices=[str(f.dev / "disk/by-id/ata-x")]))
    assert disks == [] and "dangling symlink" in notes[0]


def test_explicit_partition_is_replaced_by_its_whole_disk(tmp_path):
    f = fs(tmp_path)
    f.disk("vda")
    f.part("vda", "vda1")
    write_file(f.dev / "vda1", "")
    disks, notes = detect(f.cfg(devices=[str(f.dev / "vda1")]))
    assert [d.kname for d in disks] == ["vda"] and any("not a whole disk" in n for n in notes)
    assert disks[0].node == str(f.dev / "vda")

"""VM-guest proof of the `partitioning` plan: real loop disk, real sfdisk/partx/resize2fs/mkswap/pvcreate/lvcreate/mkfs/mount.

Runs only in the disposable QEMU/KVM guest of the `r1-vm-real-commit` lane (REAL_VM_SWAP_TEST_SKIP): LVM, loop devices and
swap are host-global state and must never be touched from a container or the controller.

Each test drives the REAL stage-two steps in stage two's own order (`_apply_known_swap_shape` -> `_grow_root_to_plan` ->
`_activate_swap_partitions` -> `_configure_data_area`) against the REAL planner on a 6 GiB sparse image that starts like the
output of stage one: ESP, /boot and a small root, nothing else. Only the apt step and the guest's own /etc/fstab are replaced.
Every command goes through the production HostActions allowlist (RedirectFstab is a REAL HostActions).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from debian_install_v2 import layout_policy as lp
from debian_install_v2.actions import HostActions
from debian_install_v2 import installer as installer_module

from .test_disk_policy import make_planner
from .test_inuse_partition_editor_r1 import REAL_VM_SWAP_TEST_SKIP

VG = "vgdiv2vm"
MOUNT = "/srv/div2-vm-cow"
LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
SWAP_GUID = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
MIB = 2048  # sectors per MiB
GIB_S = lp.SECTORS_PER_GIB
ROOT_START = 40960
ROOT_OLD = 200 * MIB
DISK_SECTORS = 6 * GIB_S


def sh(*argv: str, input: str | None = None, check: bool = True) -> str:
    done = subprocess.run(argv, check=check, text=True, capture_output=True, input=input)
    return done.stdout.strip()


class RedirectFstab(HostActions):
    """Real commands, but the guest's own /etc/fstab is never rewritten."""

    def __init__(self, fstab_copy: str):
        super().__init__(dry_run=False)
        self.fstab_copy = fstab_copy

    def write_file(self, path, content, mode=0o644):
        super().write_file(self.fstab_copy if path == "/etc/fstab" else path, content, mode)

    def run(self, argv, *args, **kwargs):
        # `mount <dir>` resolves the entry from the fstab; point it at the redirected copy.
        # (`-T` is not on the installer's mount allowlist and must not be added for a test, so the
        # allowlist check of the real `mount <dir>` argv runs and the command itself is redirected.)
        argv = list(argv)
        if len(argv) == 2 and argv[0] == "/usr/bin/mount":
            self._validate(argv)
            return sh("mount", "-T", self.fstab_copy, argv[1])
        return super().run(argv, *args, **kwargs)


def _dump_sizes(loop: str) -> dict[int, tuple[int, int]]:
    out: dict[int, tuple[int, int]] = {}
    for line in sh("sfdisk", "--dump", loop).splitlines():
        m = re.match(r"\S*?(\d+)\s*:\s*start=\s*(\d+),\s*size=\s*(\d+)", line)
        if m:
            out[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return out


class Guest:
    """A stage-one-shaped loop disk plus an installer wired to the real commands."""

    def __init__(self, tmp_path, monkeypatch, partitioning):
        self.tmp_path = tmp_path
        self.img = tmp_path / "disk.img"
        sh("truncate", "-s", f"{DISK_SECTORS * 512}", str(self.img))
        rows = (
            (1, 'start=2048, size=8192, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="esp"'),
            (2, f'start=10240, size=30720, type={LINUX}, name="boot"'),
            (3, f'start={ROOT_START}, size={ROOT_OLD}, type={LINUX}, name="root"'),
        )
        script = "label: gpt\nunit: sectors\n\n" + "".join(f"/dev/img{n} : {line}\n" for n, line in rows)
        sh("sfdisk", "--force", str(self.img), input=script)
        self.loop = sh("losetup", "--find", "--show", "--partscan", str(self.img))
        sh("udevadm", "settle")
        self.root = f"{self.loop}p3"
        self.root_mnt = tmp_path / "rootmnt"
        self.root_mnt.mkdir()
        self.fstab = tmp_path / "fstab"
        self.fstab.write_text("", encoding="utf-8")
        sh("mkfs.ext4", "-q", self.root)
        sh("mount", self.root, str(self.root_mnt))

        installer = make_planner(
            tmp_path, disk_gib=6, root_gib=1, root_start=ROOT_START, swap_policy="on", swap_disk_total_gb=1,
            swap_partition_count=4, preserve_root_size_gb=1,
            partitioning={**partitioning, "iobench_raw_gb": False},
        )
        installer.actions = RedirectFstab(str(self.fstab))  # REAL HostActions: production allowlist applies
        installer.root_disk = os.path.basename(self.loop)
        installer.root_partition_path = self.root
        installer.root_number = 3
        # the REAL planner and table readers (make_planner replaced them with constants)
        installer._disk_facts = type(installer)._disk_facts.__get__(installer)
        installer._live_partition_entries = type(installer)._live_partition_entries.__get__(installer)
        installer._root_filesystem_facts = lambda: (4096, 1000)  # resize2fs -P on a mounted fs is not needed here
        installer._read_fstab_lines = lambda: self.fstab.read_text("utf-8").splitlines()
        installer._packages = lambda *_a: None  # tools are installed in the guest image (run-vm-tests.sh)
        self.steps: list[tuple[str, str]] = []
        installer._mark_step = lambda name, status, detail="": self.steps.append((name, status))
        installer.state = type("S", (), {"load": staticmethod(lambda: {"steps": {}})})()
        real_which = shutil.which  # captured BEFORE the patch (installer_module.shutil is the shutil module)
        monkeypatch.setattr(installer_module.shutil, "which", lambda n: real_which(n) or f"/usr/bin/{n}")
        self.installer = installer
        # the plan, computed BEFORE anything is written (a pure function of config + disk + root start)
        self.swaps, self.root_target = installer._plan_swap_partitions()
        installer._resolve_swap_plan = lambda **_k: (self.swaps, self.root_target, False)

    def stage_two(self, *, data: bool = True) -> None:
        inst = self.installer
        inst._apply_known_swap_shape()    # ONE crash-safe table write: data + swaps (root still at its old size)
        inst._grow_root_to_plan()         # sfdisk -N to the exact planned end, partx -u, resize2fs
        inst._activate_swap_partitions()  # blkid, mkswap -L, swapon -p, fstab
        inst._configure_data_area()       # pvcreate/vgcreate/lvcreate or mkfs, fstab UUID lines, mount

    def table(self) -> dict[int, tuple[int, int]]:
        return _dump_sizes(self.loop)

    def close(self, mounts=(), vg=None) -> None:
        for mount in mounts:
            subprocess.run(["umount", mount], check=False, capture_output=True)
        for part in sorted(self.table_numbers(), reverse=True):
            subprocess.run(["swapoff", f"{self.loop}p{part}"], check=False, capture_output=True)
        subprocess.run(["umount", str(self.root_mnt)], check=False, capture_output=True)
        if vg:
            subprocess.run(["lvremove", "-f", "-y", vg], check=False, capture_output=True)
            subprocess.run(["vgremove", "-f", "-y", vg], check=False, capture_output=True)
            subprocess.run(["pvremove", "-f", "-y", f"{self.loop}p4"], check=False, capture_output=True)
        subprocess.run(["losetup", "--detach", self.loop], check=False)

    def table_numbers(self) -> list[int]:
        try:
            return list(self.table())
        except subprocess.CalledProcessError:
            return []


def _assert_numbers_follow_disk_position_and_nothing_is_lost(table, loop):
    """Ascending numbers == ascending starts; every partition touches the next; swap ends at the disk end."""
    after_root = sorted(n for n in table if n >= 3)
    starts = [table[n][0] for n in after_root]
    assert starts == sorted(starts), f"numbers do not follow disk position: {table}"
    for first, second in zip(after_root, after_root[1:]):
        assert table[first][0] + table[first][1] == table[second][0], f"gap between partitions {first} and {second}: {table}"
    last = after_root[-1]
    assert table[last][0] + table[last][1] > DISK_SECTORS - 4096, table
    assert all(table[n][0] % MIB == 0 for n in after_root[1:])


def _assert_swaps_active(loop, numbers):
    active = sh("swapon", "--show=NAME", "--noheadings").split()
    for offset, number in enumerate(numbers, start=1):
        part = f"{loop}p{number}"
        assert part in active, (part, active)
        assert sh("blkid", "-s", "TYPE", "-o", "value", part) == "swap"
        assert sh("blkid", "-s", "LABEL", "-o", "value", part) == f"vbpub-swap{offset}"


def _assert_root_grown(guest, table):
    assert table[3] == (ROOT_START, GIB_S), table
    assert int(sh("blockdev", "--getsz", guest.root)) == GIB_S
    fs_bytes = int(sh("sh", "-c", f"dumpe2fs -h {guest.root} 2>/dev/null | "
                                  "awk '/^Block count:/ {c=$3} /^Block size:/ {s=$3} END {print c*s}'"))
    assert abs(fs_bytes - GIB_S * 512) <= 4 * 1048576, fs_bytes


@REAL_VM_SWAP_TEST_SKIP
def test_real_partition_data_area_on_loop_disk(tmp_path, monkeypatch):
    """root 1 GiB + a 1 GiB ext4 partition + a '*' btrfs MDT_COW partition: numbers 3, 4, 5, then swaps 6-9."""
    plan = {"root": 1, "data": {"kind": "partitions", "volumes": [
        {"size": 1, "fs": "ext4", "mount": MOUNT + "-a", "label": "DATA_A"},
        {"fs": "btrfs", "mount": MOUNT, "label": lp.COW_LABEL},
    ]}}
    guest = Guest(tmp_path, monkeypatch, plan)
    mounts = [MOUNT, MOUNT + "-a"]
    try:
        guest.stage_two()
        assert guest.steps[-1] == ("data_area", "success"), guest.steps
        table = guest.table()
        assert sorted(table) == [1, 2, 3, 4, 5, 6, 7, 8, 9], table  # data 4-5 BEFORE the swaps 6-9 (the v1001 bug)
        _assert_numbers_follow_disk_position_and_nothing_is_lost(table, guest.loop)
        _assert_root_grown(guest, table)
        assert table[4][1] == GIB_S and table[4][0] == ROOT_START + GIB_S  # data starts exactly at the root end
        assert table[5][1] > 2 * GIB_S  # '*' took the whole remainder, no hole before the swaps
        assert [table[n][1] for n in (6, 7, 8, 9)] == [256 * MIB] * 4
        _assert_swaps_active(guest.loop, (6, 7, 8, 9))

        # GPT names + types written by the one table transaction
        assert sh("blkid", "-s", "PARTLABEL", "-o", "value", f"{guest.loop}p4") == "DATA_A"
        assert sh("blkid", "-s", "PARTLABEL", "-o", "value", f"{guest.loop}p5") == lp.COW_LABEL
        assert sh("lsblk", "-no", "PARTTYPE", f"{guest.loop}p4").upper() == LINUX
        # filesystems, labels, mounts via fstab
        assert sh("blkid", "-s", "TYPE", "-o", "value", f"{guest.loop}p4") == "ext4"
        assert sh("blkid", "-s", "LABEL", "-o", "value", f"{guest.loop}p4") == "DATA_A"
        cow = f"{guest.loop}p5"
        assert sh("blkid", "-s", "TYPE", "-o", "value", cow) == "btrfs"
        assert sh("blkid", "-s", "LABEL", "-o", "value", cow) == lp.COW_LABEL
        assert sh("findmnt", "-n", "-o", "FSTYPE", MOUNT) == "btrfs"
        assert sh("findmnt", "-n", "-o", "FSTYPE", MOUNT + "-a") == "ext4"
        fstab = guest.fstab.read_text("utf-8")
        uuid = sh("blkid", "-s", "UUID", "-o", "value", cow)
        assert f"UUID={uuid} {MOUNT} btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in fstab
        assert len(re.findall(r"^PARTUUID=\S+ none swap sw,pri=", fstab, re.M)) == 4, fstab
        rows = sh("lsblk", "-P", "-o", "PATH,FSTYPE,LABEL", guest.loop).splitlines()
        assert len([r for r in rows if 'FSTYPE="btrfs"' in r and f'LABEL="{lp.COW_LABEL}"' in r]) == 1  # carve detection

        # a plain rerun converges: the table is recognised as done, nothing grows, reformats or errors
        guest.steps.clear()
        assert guest.installer._swap_partitions_already_planned_in_table() is True
        guest.installer._grow_root_to_plan()
        guest.installer._configure_data_area()
        assert sh("blkid", "-s", "UUID", "-o", "value", cow) == uuid
        assert guest.steps[-1] == ("data_area", "success"), guest.steps
        assert guest.table() == table
    finally:
        guest.close(mounts)


@REAL_VM_SWAP_TEST_SKIP
def test_real_lvm_data_area_on_loop_disk(tmp_path, monkeypatch):
    """root 1 GiB + a '*' LVM PV (partition 4) holding a 1 GiB btrfs LV and a '*' xfs LV (created last, 100%FREE)."""
    mount_rest = MOUNT + "-rest"
    plan = {"root": 1, "data": {"kind": "lvm", "vg": VG, "volumes": [
        {"name": "rest", "fs": "xfs", "mount": mount_rest, "label": "REST"},  # the '*' LV is listed FIRST on purpose
        {"name": "cow", "size": 1, "fs": "btrfs", "mount": MOUNT, "label": lp.COW_LABEL},
    ]}}
    guest = Guest(tmp_path, monkeypatch, plan)
    try:
        guest.stage_two()
        assert guest.steps[-1] == ("data_area", "success"), guest.steps
        table = guest.table()
        assert sorted(table) == [1, 2, 3, 4, 5, 6, 7, 8], table  # PV 4 before the swaps 5-8
        _assert_numbers_follow_disk_position_and_nothing_is_lost(table, guest.loop)
        _assert_root_grown(guest, table)
        assert sh("blkid", "-s", "PARTLABEL", "-o", "value", f"{guest.loop}p4") == lp.LVM_PV_PARTLABEL
        assert sh("blkid", "-s", "TYPE", "-o", "value", f"{guest.loop}p4") == "LVM2_member"
        assert sh("lsblk", "-no", "PARTTYPE", f"{guest.loop}p4").upper() == lp.LINUX_LVM_TYPE_GUID
        _assert_swaps_active(guest.loop, (5, 6, 7, 8))

        sizes = {
            line.split()[0]: float(line.split()[1])
            for line in sh("lvs", "--noheadings", "--units", "g", "--nosuffix", "-o", "lv_name,lv_size", VG).splitlines()
        }
        assert abs(sizes["cow"] - 1.0) < 0.01, sizes
        pv_gib = table[4][1] / GIB_S
        assert abs(sizes["rest"] + sizes["cow"] - pv_gib) < 0.05, (sizes, pv_gib)  # the '*' LV took everything left
        free = float(sh("vgs", "--noheadings", "--units", "g", "--nosuffix", "-o", "vg_free", VG))
        assert free < 0.01, free  # vg_free_gib defaults to 0: the whole VG is used
        cow, rest = f"/dev/{VG}/cow", f"/dev/{VG}/rest"
        assert sh("blkid", "-s", "TYPE", "-o", "value", cow) == "btrfs"
        assert sh("blkid", "-s", "LABEL", "-o", "value", cow) == lp.COW_LABEL
        assert sh("blkid", "-s", "TYPE", "-o", "value", rest) == "xfs"
        assert sh("findmnt", "-n", "-o", "FSTYPE", MOUNT) == "btrfs"
        assert sh("findmnt", "-n", "-o", "FSTYPE", mount_rest) == "xfs"
        fstab = guest.fstab.read_text("utf-8")
        assert f"{cow} {MOUNT} btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in fstab
        rows = sh("lsblk", "-P", "-o", "PATH,PARTLABEL,FSTYPE,LABEL", guest.loop).splitlines()
        cows = [r for r in rows if 'FSTYPE="btrfs"' in r and f'LABEL="{lp.COW_LABEL}"' in r]
        assert len(cows) == 1 and f"/dev/mapper/{VG}-cow" in cows[0]

        # rerun converges: nothing is created or formatted again
        uuid_before = sh("blkid", "-s", "UUID", "-o", "value", cow)
        guest.steps.clear()
        guest.installer._configure_data_area()
        assert sh("blkid", "-s", "UUID", "-o", "value", cow) == uuid_before
        assert guest.steps[-1] == ("data_area", "success")
    finally:
        guest.close([MOUNT, mount_rest], vg=VG)


@REAL_VM_SWAP_TEST_SKIP
def test_real_dev_host_layout_stage_two_with_root_growth_swap_and_cow(tmp_path, monkeypatch):
    """No data area: root 1 GiB, `reserve_gb: "*"` keeps the rest free on purpose (the ONLY sanctioned hole), swaps 4-7 at the end.

    Exercises root growth with free space behind root (sfdisk -N must not swallow the reserve) and that the plan
    reports the reserve instead of silently leaving it.
    """
    guest = Guest(tmp_path, monkeypatch, {"root": 1, "reserve_gb": "*"})
    try:
        guest.stage_two()
        table = guest.table()
        assert sorted(table) == [1, 2, 3, 4, 5, 6, 7], table
        _assert_root_grown(guest, table)
        assert table[4][0] > table[3][0] + table[3][1] + 3 * GIB_S, "the explicit reserve behind root was swallowed"
        assert table[7][0] + table[7][1] > DISK_SECTORS - 4096
        assert [table[n][1] for n in (4, 5, 6, 7)] == [256 * MIB] * 4
        assert [table[n][0] for n in (4, 5, 6, 7)] == sorted(table[n][0] for n in (4, 5, 6, 7))
        _assert_swaps_active(guest.loop, (4, 5, 6, 7))
        assert guest.steps[-1] == ("data_area", "not_needed"), guest.steps
        guest.steps.clear()
        assert guest.installer._swap_partitions_already_planned_in_table() is True
        guest.installer._grow_root_to_plan()
        assert guest.table() == table
    finally:
        guest.close()

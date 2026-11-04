"""VM-guest proof of the LVM data area: real loop disk, real pvcreate/vgcreate/lvcreate/mkfs.btrfs/mount.

Runs only in the disposable QEMU/KVM guest of the `r1-vm-real-commit` lane (REAL_VM_SWAP_TEST_SKIP): LVM and loop
devices are host-global state and must never be touched from a container or the controller.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from debian_install_v2 import layout_policy as lp
from debian_install_v2.actions import HostActions
from debian_install_v2.installer import InstallerError

from .test_disk_policy import make_planner
from .test_inuse_partition_editor_r1 import REAL_VM_SWAP_TEST_SKIP

VG = "vgdiv2vm"
MOUNT = "/srv/div2-vm-cow"
LVM_CFG = {
    "vg": VG,
    "pv_size_gb": 4,
    "lvs": [
        {"name": "cow", "size_gb": 1, "fs": "btrfs", "mount": MOUNT, "label": "MDT_COW"},
        {"name": "spare", "size_gb": 1, "fs": "none"},
    ],
}


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


@REAL_VM_SWAP_TEST_SKIP
def test_real_lvm_data_area_on_loop_disk(tmp_path, monkeypatch):
    img = tmp_path / "data-area.img"
    sh("truncate", "-s", "6G", str(img))
    plan = (
        "label: gpt\nunit: sectors\n\n"
        'start=2048, size=409600, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="root"\n'
        f'start=1024000, size={4 * lp.SECTORS_PER_GIB}, type={lp.LINUX_LVM_TYPE_GUID}, name="{lp.LVM_PV_PARTLABEL}"\n'
        f'start={1024000 + 4 * lp.SECTORS_PER_GIB + 2048}, size=524288, '
        "type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F, name=\"swap\"\n"
    )
    sh("sfdisk", "--force", str(img), input=plan)
    loop = sh("losetup", "--find", "--show", "--partscan", str(img))
    sh("udevadm", "settle")
    fstab_copy = tmp_path / "fstab"
    fstab_copy.write_text("", encoding="utf-8")
    try:
        installer = make_planner(tmp_path, disk_gib=6, root_gib=1, swap_policy="on", swap_disk_total_gb=1)
        object.__setattr__(installer.config, "disk_layout", "dev-host")
        object.__setattr__(installer.config, "data_area", "lvm")
        object.__setattr__(installer.config, "data_lvm", LVM_CFG)
        installer.actions = RedirectFstab(str(fstab_copy))
        installer.root_disk = os.path.basename(loop)
        installer.root_partition_path = f"{loop}p1"
        installer.root_number = 1
        installer._disk_facts = lambda: (6 * lp.SECTORS_PER_GIB, 2048, 409600)
        installer._live_partition_entries = type(installer)._live_partition_entries.__get__(installer)
        installer._plan_swap_partitions = lambda **_k: ([(1024000 + 4 * lp.SECTORS_PER_GIB + 2048, 524288)], 409600)
        installer._data_area_layout = lambda *_a: (2, 1024000, 4 * lp.SECTORS_PER_GIB)
        installer._read_fstab_lines = lambda: []
        steps: list[tuple[str, str]] = []
        installer._mark_step = lambda name, status, detail="": steps.append((name, status))
        if shutil.which("pvcreate") and shutil.which("mkfs.btrfs"):
            installer._packages = lambda *_a: None  # tools already in the guest image

        installer._configure_data_area()

        assert steps[-1] == ("data_area", "success"), steps
        assert sh("lvs", "--noheadings", "-o", "lv_name", VG).split() == ["cow", "spare"]
        cow = f"/dev/{VG}/cow"
        assert sh("blkid", "-s", "TYPE", "-o", "value", cow) == "btrfs"
        assert sh("blkid", "-s", "LABEL", "-o", "value", cow) == "MDT_COW"
        assert sh("blkid", "-s", "TYPE", "-o", "value", f"/dev/{VG}/spare", check=False) == ""  # left unformatted
        assert sh("findmnt", "-n", "-o", "FSTYPE", MOUNT) == "btrfs"
        assert f"{cow} {MOUNT} btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in fstab_copy.read_text("utf-8")
        free = float(sh("vgs", "--noheadings", "--units", "g", "--nosuffix", "-o", "vg_free", VG))
        assert free >= 1.5  # free VG space is left for later lvextend / zfs / snapshots

        # the post-install carve (scripts/netcup/runner/cow-carve.sh cow_select_device) keys on exactly this lsblk row
        rows = sh("lsblk", "-P", "-o", "PATH,PARTLABEL,FSTYPE,LABEL", loop).splitlines()
        cows = [
            row for row in rows
            if re.search(r'FSTYPE="btrfs"', row) and re.search(r'LABEL="MDT_COW"', row)
        ]
        assert len(cows) == 1 and f"/dev/mapper/{VG}-cow" in cows[0]

        # rerun converges: nothing is formatted or created again
        uuid_before = sh("blkid", "-s", "UUID", "-o", "value", cow)
        steps.clear()
        installer._configure_data_area()
        assert sh("blkid", "-s", "UUID", "-o", "value", cow) == uuid_before
        assert steps[-1] == ("data_area", "success")

        # a foreign signature is refused, never overwritten
        sh("umount", MOUNT)
        sh("lvchange", "-an", f"{VG}/cow")
        sh("lvchange", "-ay", f"{VG}/cow")
        sh("wipefs", "-a", f"/dev/{VG}/spare")
        sh("mkfs.ext4", "-q", f"/dev/{VG}/spare")
        spare_cfg = {**LVM_CFG, "lvs": [LVM_CFG["lvs"][0], {"name": "spare", "size_gb": 1, "fs": "xfs", "mount": "/srv/div2-vm-spare"}]}
        object.__setattr__(installer.config, "data_lvm", spare_cfg)
        try:
            installer._configure_data_area()
        except InstallerError as exc:
            assert "already holds a ext4 signature" in str(exc)
        else:
            raise AssertionError("a foreign ext4 signature must be refused")
    finally:
        subprocess.run(["umount", MOUNT], check=False, capture_output=True)
        subprocess.run(["lvremove", "-f", "-y", VG], check=False, capture_output=True)
        subprocess.run(["vgremove", "-f", "-y", VG], check=False, capture_output=True)
        subprocess.run(["pvremove", "-f", "-y", f"{loop}p2"], check=False, capture_output=True)
        subprocess.run(["losetup", "--detach", loop], check=False)

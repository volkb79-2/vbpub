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
from debian_install_v2 import installer as installer_module
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

    def run(self, argv, *args, **kwargs):
        # `mount <dir>` resolves the entry from the fstab; point it at the redirected copy.
        # (`-T` is not on the installer's mount allowlist and must not be added for a test, so the
        # allowlist check of the real `mount <dir>` argv runs and the command itself is redirected.)
        argv = list(argv)
        if len(argv) == 2 and argv[0] == "/usr/bin/mount":
            self._validate(argv)
            return sh("mount", "-T", self.fstab_copy, argv[1])
        return super().run(argv, *args, **kwargs)


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


@REAL_VM_SWAP_TEST_SKIP
def test_real_partition_data_area_on_loop_disk(tmp_path):
    """data_area=partition: real mkfs.btrfs labelled MDT_COW on the data partition, mounted via fstab, detected by the carve."""
    img = tmp_path / "data-partition.img"
    sh("truncate", "-s", "6G", str(img))
    data_start = 1024000
    data_size = 4 * lp.SECTORS_PER_GIB
    plan = (
        "label: gpt\nunit: sectors\n\n"
        'start=2048, size=409600, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="root"\n'
        f'start={data_start}, size={data_size}, type={lp.LINUX_FS_TYPE_GUID}, name="{lp.COW_LABEL}"\n'
        f'start={data_start + data_size + 2048}, size=524288, '
        'type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F, name="swap"\n'
    )
    sh("sfdisk", "--force", str(img), input=plan)
    loop = sh("losetup", "--find", "--show", "--partscan", str(img))
    sh("udevadm", "settle")
    fstab_copy = tmp_path / "fstab"
    fstab_copy.write_text("", encoding="utf-8")
    try:
        installer = make_planner(tmp_path, disk_gib=6, root_gib=1, swap_policy="on", swap_disk_total_gb=1)
        object.__setattr__(installer.config, "disk_layout", "dev-host")
        object.__setattr__(installer.config, "data_area", "partition")
        object.__setattr__(installer.config, "data_partition", {"fs": "btrfs", "mount": MOUNT, "label": lp.COW_LABEL})
        installer.actions = RedirectFstab(str(fstab_copy))
        installer.root_disk = os.path.basename(loop)
        installer.root_partition_path = f"{loop}p1"
        installer.root_number = 1
        installer._disk_facts = lambda: (6 * lp.SECTORS_PER_GIB, 2048, 409600)
        installer._live_partition_entries = type(installer)._live_partition_entries.__get__(installer)
        installer._plan_swap_partitions = lambda **_k: ([(data_start + data_size + 2048, 524288)], 409600)
        installer._data_area_layout = lambda *_a: (2, data_start, data_size)
        installer._read_fstab_lines = lambda: []
        steps: list[tuple[str, str]] = []
        installer._mark_step = lambda name, status, detail="": steps.append((name, status))
        if shutil.which("mkfs.btrfs"):
            installer._packages = lambda *_a: None  # tools already in the guest image

        installer._configure_data_area()

        assert steps[-1] == ("data_area", "success"), steps
        data = f"{loop}p2"
        assert sh("blkid", "-s", "TYPE", "-o", "value", data) == "btrfs"
        assert sh("blkid", "-s", "LABEL", "-o", "value", data) == lp.COW_LABEL
        assert sh("findmnt", "-n", "-o", "FSTYPE", MOUNT) == "btrfs"
        data_uuid = sh("blkid", "-s", "UUID", "-o", "value", data)
        assert f"UUID={data_uuid} {MOUNT} btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in fstab_copy.read_text("utf-8")
        rows = sh("lsblk", "-P", "-o", "PATH,PARTLABEL,FSTYPE,LABEL", loop).splitlines()
        cows = [row for row in rows if 'FSTYPE="btrfs"' in row and f'LABEL="{lp.COW_LABEL}"' in row]
        assert len(cows) == 1 and f"{loop}p2" in cows[0]

        uuid_before = sh("blkid", "-s", "UUID", "-o", "value", data)
        steps.clear()
        installer._configure_data_area()  # rerun converges: no second mkfs
        assert sh("blkid", "-s", "UUID", "-o", "value", data) == uuid_before
        assert steps[-1] == ("data_area", "success")
    finally:
        subprocess.run(["umount", MOUNT], check=False, capture_output=True)
        subprocess.run(["losetup", "--detach", loop], check=False)


# --- full dev-host layout through the real stage-two steps (extends the data-area scenario above) ---
# Why here, not a new file/lane: same loop-disk + RedirectFstab setup; this adds the gap/growth branch and tail swaps.
LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
SWAP = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
MIB = 2048  # sectors per MiB

ROOT_START = 40960
ROOT_OLD = 200 * MIB
ROOT_TARGET = 800 * MIB
DATA_START = 1_802_240  # leaves ~100 MiB reserve behind the grown root, like the 6.75 GiB reserve on 512 GiB
DATA_SIZE = 512 * MIB
SWAP_SIZE = 64 * MIB
DISK_SECTORS = 3 * 1024 * MIB  # 3 GiB sparse image, image larger than the root image so a gap exists
SWAP_STARTS = [DISK_SECTORS - 2048 - (4 - i) * SWAP_SIZE for i in range(4)]


def _dump_sizes(loop: str) -> dict[int, tuple[int, int]]:
    out: dict[int, tuple[int, int]] = {}
    for line in sh("sfdisk", "--dump", loop).splitlines():
        m = re.match(r"\S*?(\d+)\s*:\s*start=\s*(\d+),\s*size=\s*(\d+)", line)
        if m:
            out[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return out



@REAL_VM_SWAP_TEST_SKIP
def test_real_dev_host_layout_stage_two_with_root_growth_swap_and_cow(tmp_path, monkeypatch):
    img = tmp_path / "dev-host.img"
    sh("truncate", "-s", f"{DISK_SECTORS * 512}", str(img))
    # explicit partition numbers, exactly like stage one writes them (data = 8, swaps 4-7)
    rows = [
        (1, 'start=2048, size=8192, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="esp"'),
        (2, f'start=10240, size=30720, type={LINUX}, name="boot"'),
        (3, f'start={ROOT_START}, size={ROOT_OLD}, type={LINUX}, name="root"'),
        (8, f'start={DATA_START}, size={DATA_SIZE}, type={lp.LINUX_FS_TYPE_GUID}, name="{lp.COW_LABEL}"'),
    ] + [
        (number, f'start={start}, size={SWAP_SIZE}, type={SWAP}, name="swap{number}"')
        for number, start in zip((4, 5, 6, 7), SWAP_STARTS)
    ]
    script = "label: gpt\nunit: sectors\n\n" + "".join(f"/dev/img{n} : {line}\n" for n, line in rows)
    sh("sfdisk", "--force", str(img), input=script)
    loop = sh("losetup", "--find", "--show", "--partscan", str(img))
    sh("udevadm", "settle")
    root = f"{loop}p3"
    root_mnt = tmp_path / "rootmnt"
    root_mnt.mkdir()
    fstab_copy = tmp_path / "fstab"
    fstab_copy.write_text("", encoding="utf-8")
    try:
        sh("mkfs.ext4", "-q", root)
        sh("mount", root, str(root_mnt))

        installer = make_planner(tmp_path, disk_gib=3, root_gib=1, root_start=ROOT_START,
                                 swap_policy="on", swap_disk_total_gb=1)
        for key, value in {
            "disk_layout": "dev-host", "data_area": "partition",
            "data_partition": {"fs": "btrfs", "mount": MOUNT, "label": lp.COW_LABEL},
        }.items():
            object.__setattr__(installer.config, key, value)
        installer.actions = RedirectFstab(str(fstab_copy))  # REAL HostActions: production allowlist applies
        installer.root_disk = os.path.basename(loop)
        installer.root_partition_path = root
        installer.root_number = 3
        installer._disk_facts = type(installer)._disk_facts.__get__(installer)
        installer._live_partition_entries = type(installer)._live_partition_entries.__get__(installer)
        swaps = [(start, SWAP_SIZE) for start in SWAP_STARTS]
        installer._resolve_swap_plan = lambda **_k: ([], ROOT_TARGET, False)
        installer._plan_swap_partitions = lambda **_k: (swaps, ROOT_TARGET)
        installer._data_area_layout = lambda *_a: (8, DATA_START, DATA_SIZE)
        installer._root_filesystem_facts = lambda: (4096, 1000)
        installer._read_fstab_lines = lambda: fstab_copy.read_text("utf-8").splitlines()
        installer._packages = lambda *_a: None  # tools are baked into the guest image
        steps: list[tuple[str, str]] = []
        installer._mark_step = lambda name, status, detail="": steps.append((name, status))
        installer.state = type("S", (), {"load": staticmethod(lambda: {"steps": {}})})()
        monkeypatch.setattr(installer_module.shutil, "which", lambda _n: "/usr/bin/growpart")
        assert installer.actions.dry_run is False

        # --- the three real stage-two steps, in stage two's own order --------------------------------------
        installer._grow_root_to_plan()          # gap branch: sfdisk --no-reread --no-tell-kernel -N 3, partx -u, resize2fs
        installer._activate_swap_partitions()   # blkid, mkswap -L, swapon -p, fstab
        installer._configure_data_area()        # blkid, mkfs.btrfs -L MDT_COW, fstab UUID line, mount /srv/cow

        assert ("root_resize", "success") in steps and ("swap_partitions", "success") in steps, steps
        assert steps[-1] == ("data_area", "success"), steps

        # root: table == kernel == filesystem == plan, and the reserve gap behind it is untouched
        table = _dump_sizes(loop)
        assert table[3] == (ROOT_START, ROOT_TARGET), table
        assert ROOT_START + ROOT_TARGET < table[8][0], "root growth swallowed the reserve in front of the data area"
        assert int(sh("blockdev", "--getsz", root)) == ROOT_TARGET
        fs_bytes = int(sh("sh", "-c", f"dumpe2fs -h {root} 2>/dev/null | "
                                      "awk '/^Block count:/ {c=$3} /^Block size:/ {s=$3} END {print c*s}'"))
        assert abs(fs_bytes - ROOT_TARGET * 512) <= 4 * 1048576, fs_bytes

        # swap: four partitions at the END of the disk, formatted, labelled, active
        assert [table[n][0] for n in (4, 5, 6, 7)] == SWAP_STARTS
        assert table[7][0] + table[7][1] > DISK_SECTORS - 4096
        assert max(table, key=lambda n: table[n][0]) == 7
        active = sh("swapon", "--show=NAME", "--noheadings").split()
        for offset, number in enumerate((4, 5, 6, 7), start=1):
            part = f"{loop}p{number}"
            assert part in active, (part, active)
            assert sh("blkid", "-s", "TYPE", "-o", "value", part) == "swap"
            assert sh("blkid", "-s", "LABEL", "-o", "value", part) == f"vbpub-swap{offset}"

        # data area: btrfs MDT_COW on p8, mounted at the carve's expected mount point, via fstab
        data = f"{loop}p8"
        assert sh("blkid", "-s", "TYPE", "-o", "value", data) == "btrfs"
        assert sh("blkid", "-s", "LABEL", "-o", "value", data) == lp.COW_LABEL
        assert sh("findmnt", "-n", "-o", "FSTYPE", MOUNT) == "btrfs"
        assert sh("findmnt", "-n", "-o", "SOURCE", MOUNT).startswith(data)
        fstab = fstab_copy.read_text("utf-8")
        uuid = sh("blkid", "-s", "UUID", "-o", "value", data)
        assert f"UUID={uuid} {MOUNT} btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in fstab
        assert len(re.findall(r"^PARTUUID=\S+ none swap sw,pri=", fstab, re.M)) == 4, fstab
        rows = sh("lsblk", "-P", "-o", "PATH,FSTYPE,LABEL", loop).splitlines()
        assert len([r for r in rows if 'FSTYPE="btrfs"' in r and f'LABEL="{lp.COW_LABEL}"' in r]) == 1

        # a plain rerun converges: nothing grows, reformats or errors
        uuid_before = uuid
        steps.clear()
        installer._grow_root_to_plan()
        installer._configure_data_area()
        assert sh("blkid", "-s", "UUID", "-o", "value", data) == uuid_before
        assert steps[-1] == ("data_area", "success"), steps
    finally:
        subprocess.run(["umount", MOUNT], check=False, capture_output=True)
        for number in (4, 5, 6, 7):
            subprocess.run(["swapoff", f"{loop}p{number}"], check=False, capture_output=True)
        subprocess.run(["umount", str(root_mnt)], check=False, capture_output=True)
        subprocess.run(["losetup", "--detach", loop], check=False)

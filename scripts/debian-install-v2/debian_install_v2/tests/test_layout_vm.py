"""VM-guest proof of the whole dev-host layout stage two, with the PRODUCTION HostActions allowlist.

Runs only in the disposable QEMU guest of the `r1-vm-real-commit` lane (REAL_VM_SWAP_TEST_SKIP).

Why (live finding 2026-11-05, v1001 reinstall): the gap branch of `_grow_root_to_plan` (sfdisk -N, partx -u,
resize2fs) never executed in any lane, because dry-run skips it and the unit fakes bypassed the allowlist. This
test builds the scaled-down shape stage one writes on a Netcup host and runs the real stage-two steps through the
real `HostActions.run` (only /etc/fstab is redirected to a scratch copy):

    p1 ESP, p2 boot, p3 small ext4 root (mounted), [reserve gap], p8 MDT_COW data partition, p4-p7 tail swaps

expected end state: root grown to the plan through the gap branch (fs == partition == plan, gap kept), four
formatted+active swaps at the END of the disk, a btrfs filesystem labelled MDT_COW on p8 mounted via fstab.
"""

from __future__ import annotations

import os
import re
import subprocess

from debian_install_v2 import layout_policy as lp
from debian_install_v2 import installer as installer_module

from .test_data_area_vm import MOUNT, RedirectFstab, sh
from .test_disk_policy import make_planner
from .test_inuse_partition_editor_r1 import REAL_VM_SWAP_TEST_SKIP

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

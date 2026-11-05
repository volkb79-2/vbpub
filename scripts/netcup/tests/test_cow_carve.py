"""Hermetic tests for scripts/netcup/runner/cow-carve.sh (the cow carve decisions of provision-runner.sh).

The functions read sample `parted -m ... print free` / `lsblk -P` output on stdin; no device, parted or root needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "runner" / "cow-carve.sh"
PROVISION = LIB.with_name("provision-runner.sh")


def bash(snippet: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"set -Eeuo pipefail; source {LIB}; {snippet}"],
        input=stdin, text=True, capture_output=True, check=False,
    )


# 512 GiB disk = 524288 MiB; dev-host layout: ESP, /boot, root 160 GiB, gap, 4 x 4 GiB swap at the end.
NORMAL_GAP = """BYT;
/dev/vda:524288MiB:virtblk:512:512:gpt:Virtio Block Device:;
1:0.02MiB:1.00MiB:0.98MiB:free;
1:1.00MiB:244MiB:243MiB:fat32:EFI:boot, esp;
2:244MiB:1221MiB:977MiB:ext4:boot:;
3:1221MiB:165061MiB:163840MiB:ext4:root:;
1:165061MiB:507904MiB:342843MiB:free;
4:507904MiB:511999MiB:4095MiB:linux-swap(v1):swap1:swap;
5:512000MiB:516095MiB:4095MiB:linux-swap(v1):swap2:swap;
6:516096MiB:520191MiB:4095MiB:linux-swap(v1):swap3:swap;
7:520192MiB:524287MiB:4095MiB:linux-swap(v1):swap4:swap;
1:524287MiB:524288MiB:1.00MiB:free;
"""

TAIL_SLIVER_ONLY = """BYT;
/dev/vda:524288MiB:virtblk:512:512:gpt:Virtio Block Device:;
1:0.02MiB:1.00MiB:0.98MiB:free;
1:1.00MiB:244MiB:243MiB:fat32:EFI:boot, esp;
2:244MiB:1221MiB:977MiB:ext4:boot:;
3:1221MiB:524287MiB:523066MiB:ext4:root:;
1:524287MiB:524288MiB:1.00MiB:free;
"""

TWO_FREE_ROWS = """BYT;
/dev/vda:524288MiB:virtblk:512:512:gpt:Virtio Block Device:;
1:0.02MiB:1.00MiB:0.98MiB:free;
1:1.00MiB:244MiB:243MiB:fat32:EFI:boot, esp;
2:244MiB:1221MiB:977MiB:ext4:boot:;
3:1221MiB:101221MiB:100000MiB:ext4:root:;
1:101221MiB:131221MiB:30000MiB:free;
4:131221MiB:231221MiB:100000MiB:ext4:data:;
1:231221MiB:507904MiB:276683MiB:free;
5:507904MiB:524287MiB:16383MiB:linux-swap(v1):swap:swap;
1:524287MiB:524288MiB:1.00MiB:free;
"""

COW_GIB_168 = 168 * 1024


def test_normal_gap_is_selected_and_cow_sits_at_its_end():
    res = bash(f"row=$(cow_pick_free_row {COW_GIB_168}); echo $row; cow_plan_range ${{row%%:*}} ${{row##*:}} {COW_GIB_168}", NORMAL_GAP)
    assert res.returncode == 0, res.stderr
    row, cow = res.stdout.split()
    assert row == "165061:507904"
    assert cow == f"{507904 - COW_GIB_168}:507904"


def test_tail_sliver_only_is_refused():
    res = bash("cow_pick_free_row 20480", TAIL_SLIVER_ONLY)
    assert res.returncode == 2
    assert "below the required" in res.stderr
    assert res.stdout == ""


def test_no_free_row_at_all_is_refused():
    res = bash("cow_pick_free_row 1024", "BYT;\n/dev/vda:1MiB:x:512:512:gpt::;\n1:1.00MiB:2.00MiB:1.00MiB:ext4:root:;\n")
    assert res.returncode == 1


def test_two_free_rows_pick_the_largest_and_never_the_slivers():
    res = bash("cow_pick_free_row 20480", TWO_FREE_ROWS)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "231221:507904"


def test_gap_smaller_than_the_requested_cow_is_refused_not_clipped():
    res = bash(f"cow_pick_free_row {400 * 1024}", NORMAL_GAP)
    assert res.returncode == 2


def test_row_boundaries_round_inwards():
    res = bash("cow_pick_free_row 1", "1:100.30MiB:900.90MiB:800MiB:free;\n")
    assert res.stdout.strip() == "101:900"


LSBLK_FRESH = """PATH="/dev/vda" PARTLABEL="" FSTYPE="" LABEL=""
PATH="/dev/vda1" PARTLABEL="" FSTYPE="vfat" LABEL=""
PATH="/dev/vda3" PARTLABEL="" FSTYPE="ext4" LABEL="root"
PATH="/dev/vda4" PARTLABEL="" FSTYPE="swap" LABEL=""
"""

LSBLK_EXISTING_PARTLABEL = LSBLK_FRESH + 'PATH="/dev/vda5" PARTLABEL="MDT_COW" FSTYPE="" LABEL=""\n'

# renumbered: the cow is NOT vda8 (the old fixed COW_DEVICE), a swap holds that number
LSBLK_RENUMBERED = (
    LSBLK_FRESH
    + 'PATH="/dev/vda6" PARTLABEL="MDT_COW" FSTYPE="btrfs" LABEL="MDT_COW"\n'
    + 'PATH="/dev/vda8" PARTLABEL="" FSTYPE="swap" LABEL=""\n'
)

LSBLK_BTRFS_LABEL_ONLY = LSBLK_FRESH + 'PATH="/dev/vda7" PARTLABEL="" FSTYPE="btrfs" LABEL="MDT_COW"\n'

LSBLK_TWO_COWS = LSBLK_RENUMBERED + 'PATH="/dev/vda9" PARTLABEL="MDT_COW" FSTYPE="" LABEL=""\n'

LSBLK_FOREIGN_BTRFS = LSBLK_FRESH + 'PATH="/dev/vda7" PARTLABEL="" FSTYPE="btrfs" LABEL="other"\n'


@pytest.mark.parametrize(
    ("lsblk", "device"),
    [
        (LSBLK_EXISTING_PARTLABEL, "/dev/vda5"),
        (LSBLK_RENUMBERED, "/dev/vda6"),
        (LSBLK_BTRFS_LABEL_ONLY, "/dev/vda7"),
    ],
)
def test_existing_cow_is_found_by_label_not_by_number(lsblk, device):
    res = bash("cow_select_device", lsblk)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == device


@pytest.mark.parametrize("lsblk", [LSBLK_FRESH, LSBLK_FOREIGN_BTRFS])
def test_no_cow_means_carve(lsblk):
    assert bash("cow_select_device", lsblk).returncode == 1


def test_two_cow_candidates_are_never_resolved_by_guessing():
    res = bash("cow_select_device", LSBLK_TWO_COWS)
    assert res.returncode == 3
    assert "several cow candidates" in res.stderr


# installer-created data area (debian-install-v2 data_area=lvm): the PV partition carries PARTLABEL=MDT_LVM_PV, the LVs are
# lsblk children of it (PATH=/dev/mapper/<vg>-<lv>); only the LV with filesystem LABEL=MDT_COW is the cow
LSBLK_LVM_COW = (
    LSBLK_FRESH
    + 'PATH="/dev/vda8" PARTLABEL="MDT_LVM_PV" FSTYPE="LVM2_member" LABEL=""\n'
    + 'PATH="/dev/mapper/vg_data-cow" PARTLABEL="" FSTYPE="btrfs" LABEL="MDT_COW"\n'
    + 'PATH="/dev/mapper/vg_data-spare" PARTLABEL="" FSTYPE="" LABEL=""\n'
)
LSBLK_LVM_NO_COW = (
    LSBLK_FRESH
    + 'PATH="/dev/vda8" PARTLABEL="MDT_LVM_PV" FSTYPE="LVM2_member" LABEL=""\n'
    + 'PATH="/dev/mapper/vg_data-spare" PARTLABEL="" FSTYPE="" LABEL=""\n'
)
LSBLK_EXT4_COW_PARTITION = LSBLK_FRESH + 'PATH="/dev/vda8" PARTLABEL="MDT_COW" FSTYPE="ext4" LABEL="MDT_COW"\n'
LSBLK_LVM_AND_PARTITION_COW = LSBLK_LVM_COW + 'PATH="/dev/vda9" PARTLABEL="MDT_COW" FSTYPE="btrfs" LABEL="MDT_COW"\n'


def test_installer_created_cow_lv_is_detected_by_label_and_nothing_is_carved():
    res = bash("cow_select_device", LSBLK_LVM_COW)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "/dev/mapper/vg_data-cow"


def test_lvm_without_a_labelled_cow_lv_still_means_carve_from_the_reserve():
    # the PV partition and an unlabelled spare LV are never mistaken for a cow
    assert bash("cow_select_device", LSBLK_LVM_NO_COW).returncode == 1


def test_installer_created_ext4_cow_is_found_so_no_second_cow_is_carved():
    res = bash("cow_select_device", LSBLK_EXT4_COW_PARTITION)
    assert res.returncode == 0 and res.stdout.strip() == "/dev/vda8"


def test_lv_cow_plus_partition_cow_is_ambiguous():
    assert bash("cow_select_device", LSBLK_LVM_AND_PARTITION_COW).returncode == 3


@pytest.mark.parametrize(
    ("existing", "ok"),
    [
        ("UUID=u1 /srv/cow btrfs noatime,user_subvol_rm_allowed 0 0", True),  # what provision-runner.sh writes
        ("LABEL=MDT_COW /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail 0 0", True),  # installer, partition
        ("/dev/vg_data/cow /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail 0 0", True),  # installer, LV
        ("UUID=u1 /srv/cow btrfs defaults,nofail 0 0", True),
        ("UUID=other /srv/cow btrfs defaults 0 0", False),
        ("LABEL=MDT_COW /srv/cow ext4 defaults 0 2", False),
        ("/dev/sdz9 /srv/cow btrfs defaults 0 0", False),
    ],
)
def test_existing_fstab_line_for_the_cow_mount(existing, ok):
    expected = "UUID=u1 /srv/cow btrfs noatime,user_subvol_rm_allowed 0 0"
    res = bash(f'cow_fstab_entry_ok "{existing}" "{expected}" u1 /dev/vg_data/cow')
    assert (res.returncode == 0) is ok


def test_provision_script_skips_carving_for_an_installer_cow_and_checks_its_mount_point():
    text = PROVISION.read_text(encoding="utf-8")
    assert "cow_fstab_entry_ok" in text
    assert "make data_area mount and COW_MOUNT agree" in text


def test_provision_script_wires_the_helpers_and_keeps_the_guards():
    text = PROVISION.read_text(encoding="utf-8")
    assert 'source "$here/cow-carve.sh"' in text
    assert "cow_pick_free_row" in text and "cow_select_device" in text
    assert 'mkpart "$COW_LABEL" btrfs' in text  # PARTLABEL is written at creation time
    assert "COW_WIPE_STALE" in text
    assert "mkpart primary" not in text
    subprocess.run(["bash", "-n", str(PROVISION)], check=True)
    subprocess.run(["bash", "-n", str(LIB)], check=True)

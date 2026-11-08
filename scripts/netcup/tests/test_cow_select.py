"""Hermetic checks for PARTLABEL-based runner CoW discovery."""

from __future__ import annotations

import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "runner" / "cow-select.sh"
PROVISION = LIB.with_name("provision-runner.sh")
ENV_EXAMPLE = LIB.with_name("runner.env.example")


def bash(snippet: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"set -Eeuo pipefail; source {LIB}; {snippet}"],
        input=stdin, text=True, capture_output=True, check=False,
    )


def test_planned_partition_is_found_by_partlabel_after_renumbering():
    lsblk = (
        'PATH="/dev/vda" PARTLABEL=""\n'
        'PATH="/dev/vda3" PARTLABEL="root"\n'
        'PATH="/dev/vda5" PARTLABEL="iobench-raw"\n'
        'PATH="/dev/vda6" PARTLABEL="MDT_COW"\n'
        'PATH="/dev/vda7" PARTLABEL="vbpub-swap1"\n'
    )
    result = bash("cow_select_device", lsblk)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/dev/vda6"


def test_filesystem_label_without_partlabel_is_not_a_candidate():
    result = bash('cow_select_device', 'PATH="/dev/vda7" PARTLABEL="" LABEL="MDT_COW"\n')
    assert result.returncode == 1


def test_ambiguous_partlabels_refuse_to_choose():
    lsblk = 'PATH="/dev/vda6" PARTLABEL="MDT_COW"\nPATH="/dev/vda8" PARTLABEL="MDT_COW"\n'
    result = bash("cow_select_device", lsblk)
    assert result.returncode == 3
    assert "several PARTLABEL=MDT_COW partitions" in result.stderr


def test_existing_fstab_line_for_the_planned_partition():
    expected = "UUID=u1 /srv/cow btrfs noatime,user_subvol_rm_allowed 0 0"
    assert bash(f'cow_fstab_entry_ok "{expected}" "{expected}" u1 /dev/vda6').returncode == 0
    installed = "LABEL=MDT_COW /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail 0 0"
    assert bash(f'cow_fstab_entry_ok "{installed}" "{expected}" u1 /dev/vda6').returncode == 0
    foreign = "UUID=other /srv/cow btrfs defaults 0 0"
    assert bash(f'cow_fstab_entry_ok "{foreign}" "{expected}" u1 /dev/vda6').returncode != 0


def test_runner_requires_the_planned_partition_and_has_no_partition_creation_path():
    text = PROVISION.read_text(encoding="utf-8")
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert 'source "$here/cow-select.sh"' in text
    assert 'lsblk -P -o PATH,PARTLABEL "$COW_PARENT_DISK"' in text
    assert "the install plan has no PARTLABEL=MDT_COW partition" in text
    assert "COW_DEVICE" not in text and "COW_DEVICE" not in env
    assert "parted" not in text and "mkpart" not in text and "cow_pick_free_row" not in text
    assert "COW_SIZE_GIB" not in env and "COW_WIPE_STALE" not in env
    subprocess.run(["bash", "-n", str(PROVISION)], check=True)
    subprocess.run(["bash", "-n", str(LIB)], check=True)

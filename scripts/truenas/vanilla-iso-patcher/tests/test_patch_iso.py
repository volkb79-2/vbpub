from __future__ import annotations

import importlib.util
import hashlib
from pathlib import Path
import sys

import pytest


PATCHER_PATH = Path(__file__).parents[1] / "patch_iso.py"
SPEC = importlib.util.spec_from_file_location("truenas_vanilla_iso_patcher", PATCHER_PATH)
assert SPEC is not None and SPEC.loader is not None
patcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = patcher
SPEC.loader.exec_module(patcher)


INSTALLER = '''
import asyncio

BOOT_POOL = "boot-pool"

async def format_disk(disk, set_pmbr, callback):
    await run(["sgdisk", "-n1:0:+1024K", "-t1:EF02", disk.device])
    await run(["sgdisk", "-n2:0:+524288K", "-t2:EF00", disk.device])
    await run(["sgdisk", "-n3:0:0", "-t3:BF01", disk.device])
'''


def test_patches_only_partition_3_and_leaves_valid_python():
    patched = patcher.patch_installer_source(INSTALLER, 24)

    assert '"-n3:0:+24G"' in patched
    assert '"-n1:0:+1024K"' in patched
    assert '"-n2:0:+524288K"' in patched
    assert '"-n3:0:0"' not in patched
    compile(patched, "install.py", "exec")
    assert INSTALLER.count('"-n3:0:0"') == 1


@pytest.mark.parametrize("size", [0, 15, 1025, -16])
def test_rejects_boot_pool_sizes_outside_documented_range(size):
    with pytest.raises(patcher.PatcherError, match="boot-pool size"):
        patcher.patch_installer_source(INSTALLER, size)


def test_refuses_when_upstream_installer_shape_changes():
    changed = INSTALLER.replace('"-n3:0:0"', '"-n3:0:+16G"')
    with pytest.raises(patcher.PatcherError, match="partition 3"):
        patcher.patch_installer_source(changed, 24)


def test_refuses_duplicate_partition3_allocation_calls():
    duplicated = INSTALLER + '''\nasync def other(disk):
    await run(["sgdisk", "-n3:0:0", "-t3:BF01", disk.device])
'''
    with pytest.raises(patcher.PatcherError, match="found 2"):
        patcher.patch_installer_source(duplicated, 24)


def test_patch_iso_requires_a_well_formed_expected_sha256(tmp_path):
    source = tmp_path / "vanilla.iso"
    source.write_bytes(b"vanilla iso fixture")
    target = tmp_path / "patched.iso"

    with pytest.raises(patcher.PatcherError, match="expected input SHA-256"):
        patcher.patch_iso(source, target, 16, "not-a-checksum")


def test_patch_iso_refuses_checksum_mismatch_before_extracting(tmp_path, monkeypatch):
    source = tmp_path / "vanilla.iso"
    source.write_bytes(b"vanilla iso fixture")
    target = tmp_path / "patched.iso"
    monkeypatch.setattr(patcher.os, "geteuid", lambda: 0)

    def should_not_extract(*_args, **_kwargs):
        raise AssertionError("input hash must be checked before extraction")

    monkeypatch.setattr(patcher, "_extract_iso_file", should_not_extract)
    expected = hashlib.sha256(b"different ISO").hexdigest()
    with pytest.raises(patcher.PatcherError, match="input ISO SHA-256 mismatch"):
        patcher.patch_iso(source, target, 16, expected)
    assert not target.exists()


def test_parse_single_iso_extent_requires_one_2048_byte_file_record():
    report = "File data lba:  0 , 40063 , 118620 , 242933760 , '/live/filesystem.squashfs'\n"

    extent = patcher.parse_single_iso_extent(report, "/live/filesystem.squashfs")

    assert extent.byte_offset == 40063 * 2048
    assert extent.allocated_bytes == 118620 * 2048
    assert extent.file_bytes == 242933760


@pytest.mark.parametrize(
    "report",
    [
        "",
        "File data lba: 0 , 12 , 4 , 8193 , '/live/filesystem.squashfs'\n",
        """File data lba: 0 , 12 , 4 , 8192 , '/live/filesystem.squashfs'
File data lba: 1 , 16 , 4 , 8192 , '/live/filesystem.squashfs'
""",
    ],
)
def test_parse_single_iso_extent_rejects_missing_split_or_invalid_records(report):
    with pytest.raises(patcher.PatcherError, match="extent"):
        patcher.parse_single_iso_extent(report, "/live/filesystem.squashfs")


def test_replace_extent_keeps_file_length_and_zeroes_old_tail(tmp_path):
    iso = tmp_path / "base.iso"
    replacement = tmp_path / "filesystem.squashfs"
    iso.write_bytes(b"A" * 8192)
    replacement.write_bytes(b"new-squashfs")
    extent = patcher.IsoExtent(byte_offset=2048, allocated_bytes=4096, file_bytes=4096)

    patcher._replace_extent(iso, extent, replacement)

    result = iso.read_bytes()
    assert len(result) == 8192
    assert result[:2048] == b"A" * 2048
    assert result[2048:2060] == b"new-squashfs"
    assert result[2060:6144] == bytes(4084)
    assert result[6144:] == b"A" * 2048


def test_replace_extent_refuses_a_replacement_larger_than_the_iso_file_record(tmp_path):
    iso = tmp_path / "base.iso"
    replacement = tmp_path / "filesystem.squashfs"
    iso.write_bytes(b"A" * 4096)
    replacement.write_bytes(b"B" * 2049)

    with pytest.raises(patcher.PatcherError, match="larger than its existing ISO file record"):
        patcher._replace_extent(
            iso,
            patcher.IsoExtent(byte_offset=0, allocated_bytes=2048, file_bytes=2048),
            replacement,
        )

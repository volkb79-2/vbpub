"""R0 — fast unit coverage for inuse_partition_editor.py.

Loaded by path via importlib (matching how installer.py and the other
sibling test modules load it) rather than a plain package import, so this
file stays runnable standalone. All tests here fake subprocess.run (no real
sfdisk/blockdev calls) and never touch the filesystem outside of pytest's
tmp_path. Real-sfdisk contract tests live in test_inuse_partition_editor_r1.py.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "inuse_partition_editor.py"


def load_module():
    spec = importlib.util.spec_from_file_location("inuse_partition_editor", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return load_module()


# A real, padded `sfdisk --dump` capture (dos label, primary root [1],
# extended container [2], one logical [5] inside it) — matches the exact
# format sfdisk emits, including the whitespace padding after `=`.
DOS_DUMP = """label: dos
label-id: 0x1f4966f8
device: /dev/vda
unit: sectors
sector-size: 512

/dev/vda1 : start=        2048, size=    16777216, type=83
/dev/vda2 : start=    16779264, size=    24176640, type=5
/dev/vda5 : start=    16781312, size=     8388608, type=83
"""
DOS_DISK_SECTORS = 41943040

GPT_DUMP = """label: gpt
label-id: 762277D0-569D-4A6A-9413-660D12E9034D
device: /dev/vda
unit: sectors
first-lba: 2048
last-lba: 10485726
sector-size: 512

/dev/vda1 : start=        2048, size=     2097152, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=9CDEFB93-4E2F-453B-84BF-14FE5DCAF55E, name="EFI System Partition"
/dev/vda2 : start=     2099200, size=     6000000, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid=2BF725DE-4A5D-4505-90E5-C45956069416, name="root"
"""


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def fake_run_factory(dump=DOS_DUMP, sectors=DOS_DISK_SECTORS, recorder=None):
    def fake_run(cmd, *, input=None, text=None, capture_output=None):
        if recorder is not None:
            recorder.append((list(cmd), input))
        if cmd[:2] == ["sfdisk", "-d"]:
            return FakeCompleted(stdout=dump)
        if cmd[:2] == ["blockdev", "--getsz"]:
            return FakeCompleted(stdout=f"{sectors}\n")
        return FakeCompleted()
    return fake_run


def make_table(mod, monkeypatch, dump=DOS_DUMP, sectors=DOS_DISK_SECTORS, recorder=None):
    monkeypatch.setattr(mod.subprocess, "run", fake_run_factory(dump, sectors, recorder))
    return mod.Table("/dev/vda")


# --- parse_size ---


def test_parse_size_bare_sectors(mod):
    assert mod.parse_size("2048", 512) == 2048


@pytest.mark.parametrize("suffix,expected_bytes", [
    ("k", 1024), ("m", 1024**2), ("g", 1024**3), ("t", 1024**4),
    ("ki", 1024), ("mi", 1024**2), ("gi", 1024**3), ("ti", 1024**4),
])
def test_parse_size_units(mod, suffix, expected_bytes):
    assert mod.parse_size(f"3{suffix}", 512) == (3 * expected_bytes) // 512


def test_parse_size_units_case_insensitive(mod):
    assert mod.parse_size("1G", 512) == mod.parse_size("1g", 512)


@pytest.mark.parametrize("word", ["fill", "rest", "max", "FILL"])
def test_parse_size_fill_words_mean_none(mod, word):
    assert mod.parse_size(word, 512) is None


def test_parse_size_rejects_garbage(mod):
    with pytest.raises(SystemExit, match="bad size"):
        mod.parse_size("not-a-size", 512)


# --- align helpers ---


def test_align_up_rounds_up_to_boundary(mod):
    assert mod.align_up(2049, 2048) == 4096
    assert mod.align_up(2048, 2048) == 2048


def test_align_down_rounds_down_to_boundary(mod):
    assert mod.align_down(4095, 2048) == 2048
    assert mod.align_down(4096, 2048) == 4096


# --- Table construction / parsing ---


def test_table_parses_real_padded_dump(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    assert t.sector == 512
    assert t.label_type == "dos"
    assert t.disk_sectors == DOS_DISK_SECTORS
    nums = {p["num"]: p for p in t.parts}
    assert set(nums) == {1, 2, 5}
    assert nums[1] == {"dev": "/dev/vda1", "num": 1, "start": 2048, "size": 16777216, "type": "83"}
    assert nums[5]["start"] == 16781312 and nums[5]["size"] == 8388608


def test_table_identifies_extended_partition(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    assert t.extended["num"] == 2


def test_table_parses_real_gpt_dump(mod, monkeypatch):
    t = make_table(mod, monkeypatch, dump=GPT_DUMP, sectors=10485760)
    assert t.label_type == "gpt"
    assert t.is_gpt is True
    assert t.first_lba == 2048
    assert t.last_lba == 10485726
    nums = {p["num"]: p for p in t.parts}
    # GPT type is a full GUID — must not be truncated at the first hyphen
    assert nums[1]["type"] == "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
    assert nums[2]["type"] == "0FC63DAF-8483-4772-8E79-3D69D8477DE4"


def test_table_gpt_has_no_extended_partition_concept(mod, monkeypatch):
    t = make_table(mod, monkeypatch, dump=GPT_DUMP, sectors=10485760)
    assert t.extended is None


def test_table_refuses_unsupported_disklabel(mod, monkeypatch):
    sun_dump = "label: sun\nunit: sectors\nsector-size: 512\n"
    with pytest.raises(SystemExit, match="unsupported disklabel 'sun'"):
        make_table(mod, monkeypatch, dump=sun_dump, sectors=10485760)


def test_table_accepts_injected_raw_and_disk_sectors_without_shelling_out(mod, monkeypatch):
    # installer.py's own preflight/manifest/rollback wraps this construction
    # path around a dump it already fetched through HostActions (allowlist +
    # dry-run gated) — Table must not shell out itself when given raw=/
    # disk_sectors=, or that gate is bypassed entirely.
    def explode(*a, **kw):
        raise AssertionError("Table shelled out despite raw=/disk_sectors= being provided")
    monkeypatch.setattr(mod.subprocess, "run", explode)
    t = mod.Table("/dev/vda", raw=DOS_DUMP, disk_sectors=DOS_DISK_SECTORS)
    assert t.disk_sectors == DOS_DISK_SECTORS
    assert {p["num"] for p in t.parts} == {1, 2, 5}


def test_table_skips_lines_with_no_digit_suffixed_device_name(mod):
    dump = "label: gpt\ndevice: /dev/vda\n\n/dev/vdax : start=9, size=9, type=def\n"
    t = mod.Table("/dev/vda", raw=dump, disk_sectors=10485760)
    assert t.parts == []


def test_table_refuses_lines_missing_start_or_size(mod):
    # Fail loud, not silent (adversarial review finding, reverting an
    # earlier draft of this fix): a real sfdisk --dump always emits both for
    # an existing partition, so silently excluding a malformed one from
    # self.parts would make free_regions() report its real on-disk sectors
    # as free -- a --force write could then overlap it undetected.
    dump = "label: gpt\ndevice: /dev/vda\n\n/dev/vda3 : type=linux\n"
    with pytest.raises(SystemExit, match="missing start=/size="):
        mod.Table("/dev/vda", raw=dump, disk_sectors=10485760)


# --- free_regions ---


def test_free_regions_reports_primary_tail_and_logical_gaps(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    regs = {kind: (rs, re_) for rs, re_, kind in t.free_regions()}
    # tail of the disk, after the extended container
    assert regs["primary"] == (40955904, 41943039)
    # logical free space is reported separately and does NOT appear as 'primary'
    assert "logical" in regs


def test_free_regions_default_align_matches_real_disk_shape(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    logical = [(rs, re_) for rs, re_, kind in t.free_regions() if kind == "logical"]
    # the small EBR-alignment gap right after the extended container's start,
    # plus the large trailing gap after the one logical partition
    assert (16779264, 16781311) in logical
    assert (25169920, 40955903) in logical


def test_free_regions_honors_custom_align_for_disk_start(mod, monkeypatch):
    # An empty table (no existing partitions) isolates the align parameter's
    # effect on the leading edge of the primary free region.
    empty_dump = "label: dos\nunit: sectors\nsector-size: 512\n"
    t = make_table(mod, monkeypatch, dump=empty_dump, sectors=DOS_DISK_SECTORS)
    default_start = min(rs for rs, _, kind in t.free_regions() if kind == "primary")
    aligned_start = min(rs for rs, _, kind in t.free_regions(align=4096) if kind == "primary")
    assert default_start == 2048
    assert aligned_start == 4096


# --- GPT support ---


def test_gpt_free_regions_bounded_by_first_and_last_lba_not_disk_sectors(mod, monkeypatch):
    t = make_table(mod, monkeypatch, dump=GPT_DUMP, sectors=10485760)
    regs = {kind: (rs, re_) for rs, re_, kind in t.free_regions()}
    assert regs["primary"] == (8099200, 10485726)  # last_lba, NOT disk_sectors-1 (10485759)
    assert "logical" not in regs


def test_gpt_next_num_is_unbounded_past_four(mod, monkeypatch):
    t = make_table(mod, monkeypatch, dump=GPT_DUMP, sectors=10485760)
    p1 = t.add(1000000, t.default_type("swap"), "auto", 2048, 2048, label="s1")
    p2 = t.add(1000000, t.default_type("swap"), "auto", 2048, 2048, label="s2")
    assert (p1["num"], p2["num"]) == (3, 4)


def test_gpt_placement_logical_is_refused(mod, monkeypatch):
    t = make_table(mod, monkeypatch, dump=GPT_DUMP, sectors=10485760)
    with pytest.raises(SystemExit, match="GPT has no extended/logical partitions"):
        t.add(1000000, t.default_type("swap"), "logical", 2048, 2048)


def test_gpt_default_type_is_the_canonical_guid(mod, monkeypatch):
    t = make_table(mod, monkeypatch, dump=GPT_DUMP, sectors=10485760)
    assert t.default_type("linux") == mod.GPT_TYPE_LINUX
    assert t.default_type("swap") == mod.GPT_TYPE_SWAP
    dos = make_table(mod, monkeypatch)
    assert dos.default_type("linux") == "83"
    assert dos.default_type("swap") == "82"


def test_gpt_to_dump_emits_partition_name_from_label(mod, monkeypatch):
    t = make_table(mod, monkeypatch, dump=GPT_DUMP, sectors=10485760)
    t.add(1000000, t.default_type("swap"), "auto", 2048, 2048, label="gswap1")
    assert 'name="gswap1"' in t.to_dump()


def test_mbr_to_dump_never_emits_a_name_attribute(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    t.add(2048, "82", "primary", 2048, 2048, label="gswap1")
    assert "name=" not in t.to_dump()


# --- Table.add ---


def test_add_primary_picks_first_free_slot_skipping_existing(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    p = t.add(2048, "83", "primary", 2048, 2048)
    assert p["num"] == 3  # 1 and 2 are taken, extended's slot is 2
    assert p["dev"] == "/dev/vda3"


def test_add_logical_reserves_ebr_gap_and_aligns(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    p = t.add(None, "82", "logical", 2048, 2048, label="gswap1")
    assert p["num"] == 6  # next free logical num after existing 5
    assert p["start"] == mod.align_up(25169920 + 2048, 2048)
    assert p["label"] == "gswap1"


def test_add_auto_placement_prefers_logical_when_extended_exists(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    p = t.add(2048, "82", "auto", 2048, 2048)
    assert p["num"] >= 5


def test_add_refuses_when_region_too_small(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    with pytest.raises(SystemExit, match="won't fit"):
        t.add(10**12, "83", "primary", 2048, 2048)


def test_add_refuses_when_no_free_region_of_kind(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    t.add(None, "83", "primary", 2048, 2048)  # fills the only free primary region
    with pytest.raises(SystemExit, match="no free primary region available"):
        t.add(2048, "83", "primary", 2048, 2048)


def test_next_num_exhausts_primary_slots(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    t.add(2048, "83", "primary", 2048, 2048)  # takes slot 3
    t.add(2048, "83", "primary", 2048, 2048)  # takes slot 4
    with pytest.raises(SystemExit, match="no free primary slot"):
        t.next_num(logical=False)


def test_part_dev_inserts_p_separator_only_for_digit_suffixed_disks(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    assert t.part_dev(1) == "/dev/vda1"
    t.disk = "/dev/loop7"
    assert t.part_dev(1) == "/dev/loop7p1"


# --- to_dump ---


def test_to_dump_appends_added_partitions(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    t.add(2048, "83", "primary", 2048, 2048)
    dump = t.to_dump()
    assert dump.startswith(DOS_DUMP.rstrip("\n") + "\n")
    assert "/dev/vda3 : start=" in dump


# --- Table.write safety gates ---


def test_write_dry_run_returns_false_and_touches_nothing(mod, monkeypatch, tmp_path):
    calls = []
    t = make_table(mod, monkeypatch, recorder=calls)
    t.add(2048, "83", "primary", 2048, 2048)
    calls.clear()
    assert t.write(commit=False) is False
    assert calls == []  # no sfdisk/partx invocation happened


def test_write_commit_refuses_without_root(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit, match="must be root"):
        t.write(commit=True)


# --- main()/argparse-level validation (no real sfdisk needed) ---


def test_main_add_swap_rejects_zero_count(mod, monkeypatch):
    make_table_env(mod, monkeypatch)
    with pytest.raises(SystemExit, match="--count must be >= 1"):
        run_main(mod, ["--disk", "/dev/vda", "add-swap", "--count", "0", "--labels", "x"])


def test_main_add_swap_rejects_duplicate_labels(mod, monkeypatch):
    make_table_env(mod, monkeypatch)
    with pytest.raises(SystemExit, match="must be unique"):
        run_main(mod, ["--disk", "/dev/vda", "add-swap", "--count", "2", "--labels", "dup,dup"])


def test_main_add_swap_rejects_empty_label(mod, monkeypatch):
    make_table_env(mod, monkeypatch)
    with pytest.raises(SystemExit, match="must not be empty"):
        run_main(mod, ["--disk", "/dev/vda", "add-swap", "--count", "2", "--labels", "a,"])


def test_main_add_swap_strips_label_whitespace(mod, monkeypatch, capsys):
    make_table_env(mod, monkeypatch)
    run_main(mod, ["--disk", "/dev/vda", "add-swap", "--count", "2", "--size", "fill",
                   "--labels", " gswap1 , gswap2 "])
    out = capsys.readouterr().out
    assert "label= gswap1" not in out
    assert "label=gswap1" in out and "label=gswap2" in out


def test_main_add_fstab_without_label_is_refused_before_any_write(mod, monkeypatch):
    calls = []
    make_table_env(mod, monkeypatch, recorder=calls)
    with pytest.raises(SystemExit, match="--fstab requires --label"):
        run_main(mod, ["--disk", "/dev/vda", "add", "--size", "1G", "--fstab"])
    # reading the table (sfdisk -d / blockdev --getsz) is harmless and fine;
    # the guarantee is that no *mutating* sfdisk call was ever attempted.
    assert not any("--force" in cmd for cmd, _ in calls)


# --- restore device-mismatch guard ---


def test_restore_refuses_backup_from_a_different_device(mod, tmp_path):
    backup = tmp_path / "vda.sfdisk"
    backup.write_text(DOS_DUMP)
    old_argv = sys.argv
    sys.argv = ["inuse-partition-editor.py", "--disk", "/dev/vdb", "restore", "--in", str(backup)]
    try:
        with pytest.raises(SystemExit, match="backup was captured from '/dev/vda', not '/dev/vdb'"):
            mod.main()
    finally:
        sys.argv = old_argv


def test_restore_force_flag_overrides_the_mismatch_guard(mod, tmp_path, capsys):
    backup = tmp_path / "vda.sfdisk"
    backup.write_text(DOS_DUMP)
    old_argv = sys.argv
    sys.argv = ["inuse-partition-editor.py", "--disk", "/dev/vdb", "restore",
                "--in", str(backup), "--force-mismatched-device"]
    try:
        mod.main()  # dry-run: returns normally, no SystemExit
    finally:
        sys.argv = old_argv
    assert "WARNING" in capsys.readouterr().out


def test_restore_same_device_needs_no_override(mod, tmp_path):
    backup = tmp_path / "vda.sfdisk"
    backup.write_text(DOS_DUMP)
    old_argv = sys.argv
    sys.argv = ["inuse-partition-editor.py", "--disk", "/dev/vda", "restore", "--in", str(backup)]
    try:
        mod.main()  # must not raise
    finally:
        sys.argv = old_argv


def make_table_env(mod, monkeypatch, dump=DOS_DUMP, sectors=DOS_DISK_SECTORS, recorder=None):
    monkeypatch.setattr(mod.subprocess, "run", fake_run_factory(dump, sectors, recorder))


def run_main(mod, argv):
    old_argv = sys.argv
    sys.argv = ["inuse-partition-editor.py", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old_argv


def test_module_invocation_help_shows_the_real_docs():
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--help"],
        cwd=MODULE_PATH.parent, capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "add-swap" in result.stdout
    # regression: --help used to be bare argparse output with none of this
    assert "CURRENTLY IN USE" in result.stdout
    assert "GPT" in result.stdout
    assert "Examples:" in result.stdout

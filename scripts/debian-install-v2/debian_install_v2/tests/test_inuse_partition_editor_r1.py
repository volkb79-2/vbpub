"""R1 — adversarial edge cases and real-sfdisk contract tests for
inuse_partition_editor.py.

These prove the module's parsing/planning against ACTUAL `sfdisk --dump`
output (not a hand-typed fixture) and lock in the regressions found during
review: GPT support (type-GUID parsing, first-lba/last-lba bounds, the
loop/NVMe device-naming fix), the --count/--labels validation, the
timestamped+checksummed backup, and the fill-size EBR-gap waste fix.

sfdisk/blockdev/partx all work against a plain regular file — no loop
device, no root, no VM needed for anything except the real-commit
contract tests, which are skipped unless running as root with losetup
available. That combination is exactly what the privileged systemd
container at scripts/debian-install-v2/testing/ provides — see its README.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "inuse_partition_editor.py"
SFDISK_TOOLS = ("sfdisk", "blockdev", "partx")

pytestmark = pytest.mark.skipif(
    any(shutil.which(t) is None for t in SFDISK_TOOLS),
    reason="sfdisk/blockdev/partx not available in this environment",
)


def load_module():
    spec = importlib.util.spec_from_file_location("inuse_partition_editor_r1", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return load_module()


def run_cli(*args):
    """Invoke the real script as a subprocess against a real image file."""
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), *args],
        cwd=MODULE_PATH.parent, capture_output=True, text=True,
    )
    return result


def make_dos_image(path):
    """dos label: primary root [1], extended container [2], one logical [5]
    inside it — the standard fresh-VPS shape this tool targets."""
    subprocess.run(["truncate", "-s", "20G", str(path)], check=True)
    plan = (
        "label: dos\nunit: sectors\n\n"
        "start=2048, size=16777216, type=83\n"
        "start=16779264, size=24176640, type=5\n"
        "start=16781312, size=8388608, type=83\n"
    )
    subprocess.run(["sfdisk", "--force", str(path)], input=plan, text=True,
                    check=True, capture_output=True)


def make_gpt_image(path):
    subprocess.run(["truncate", "-s", "5G", str(path)], check=True)
    plan = (
        "label: gpt\nunit: sectors\n\n"
        'start=2048, size=2097152, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="EFI System Partition"\n'
        'start=2099200, size=6000000, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="root"\n'
    )
    subprocess.run(["sfdisk", "--force", str(path)], input=plan, text=True,
                    check=True, capture_output=True)


@pytest.fixture()
def dos_image(tmp_path_factory):
    # Must live under /dev/ — Table's device-line regex requires a
    # `/dev/...` prefixed path, matching real usage (--disk /dev/vda).
    d = Path("/dev/shm") / f"pe-r1-{os.getpid()}-{id(tmp_path_factory)}"
    d.mkdir(exist_ok=True)
    img = d / "disk.img"
    make_dos_image(img)
    yield img
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def gpt_image(tmp_path_factory):
    d = Path("/dev/shm") / f"pe-r1-gpt-{os.getpid()}-{id(tmp_path_factory)}"
    d.mkdir(exist_ok=True)
    img = d / "disk.img"
    make_gpt_image(img)
    yield img
    shutil.rmtree(d, ignore_errors=True)


# --- real-sfdisk contract: parsing ---


def test_real_dump_list_matches_actual_partitions(dos_image):
    result = run_cli("--disk", str(dos_image), "list")
    assert result.returncode == 0
    assert f"{dos_image}1" in result.stdout
    assert f"{dos_image}2" in result.stdout
    assert f"{dos_image}5" in result.stdout
    assert "8.0 GiB" in result.stdout  # root
    assert "4.0 GiB" in result.stdout  # logical


def test_real_dump_free_reports_gaps_not_the_whole_disk(dos_image):
    result = run_cli("--disk", str(dos_image), "free")
    assert result.returncode == 0
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    # the whole-disk-as-free bug would report a single ~20 GiB primary region
    assert not any("20.0 GiB" in l and "primary" in l for l in lines)
    assert any("logical" in l for l in lines)
    assert any("primary" in l for l in lines)


def test_real_add_swap_dry_run_does_not_collide_with_existing_partition_numbers(dos_image):
    before = subprocess.run(["sfdisk", "--dump", str(dos_image)],
                             capture_output=True, text=True, check=True).stdout
    result = run_cli("--disk", str(dos_image), "add-swap", "--count", "2",
                      "--size", "fill", "--labels", "gswap1,gswap2")
    assert result.returncode == 0
    assert f"{dos_image}1" not in [l.split(":")[0].strip() for l in result.stdout.splitlines() if "NEW" in l]
    assert f"{dos_image}6" in result.stdout
    assert f"{dos_image}7" in result.stdout
    assert "DRY-RUN" in result.stdout
    # a dry run must never mutate the on-disk table
    after = subprocess.run(["sfdisk", "--dump", str(dos_image)],
                            capture_output=True, text=True, check=True).stdout
    assert before == after


def test_real_align_flag_changes_leading_boundary(tmp_path_factory):
    # An unpartitioned dos-labeled disk isolates align's effect: the whole
    # disk is one free primary region starting at `align`.
    d = Path("/dev/shm") / f"pe-r1-blank-{os.getpid()}"
    d.mkdir(exist_ok=True)
    img = d / "disk.img"
    try:
        subprocess.run(["truncate", "-s", "5G", str(img)], check=True)
        subprocess.run(["sfdisk", "--force", str(img)], input="label: dos\n",
                        text=True, check=True, capture_output=True)
        default = run_cli("--disk", str(img), "free")
        aligned = run_cli("--disk", str(img), "--align", "1000000", "free")
        assert default.returncode == 0 and aligned.returncode == 0
        assert "2048" in default.stdout
        assert "1000000" in aligned.stdout
        assert default.stdout != aligned.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- real-sfdisk contract: GPT is fully supported, not just refused ---


def test_real_gpt_list_shows_full_type_guids_not_truncated_hex(gpt_image):
    result = run_cli("--disk", str(gpt_image), "list")
    assert result.returncode == 0
    assert "C12A7328-F81F-11D2-BA4B-00A0C93EC93B" in result.stdout  # EFI, not "C12A7328"
    assert "0FC63DAF-8483-4772-8E79-3D69D8477DE4" in result.stdout  # root


def test_real_gpt_free_is_bounded_by_backup_header_reservation(gpt_image):
    # last-lba is BELOW disk_sectors-1 (GPT reserves a backup header+array at
    # the tail) — free_regions must respect that or a later --commit would
    # ask sfdisk for space that doesn't exist.
    disk_sectors = int(subprocess.run(["blockdev", "--getsz", str(gpt_image)],
                                       capture_output=True, text=True, check=True).stdout)
    result = run_cli("--disk", str(gpt_image), "free")
    assert result.returncode == 0
    hi = int(re.search(r'\.\.\s*(\d+)\]', result.stdout).group(1))
    assert hi < disk_sectors - 1


def test_real_gpt_add_swap_plan_is_accepted_by_real_sfdisk(gpt_image):
    """The generated plan text must be syntactically valid GPT sfdisk input —
    not just "didn't crash". Feed it back into real sfdisk directly (no
    --commit / no root needed: this bypasses the tool's own root gate on
    purpose, to isolate "is the plan syntax correct" from "is this host
    root").
    """
    plan = run_cli("--disk", str(gpt_image), "add-swap", "--count", "2",
                    "--size", "fill", "--labels", "gswap1,gswap2").stdout
    assert "type=82" not in plan and "type=83" not in plan  # bare MBR codes are invalid on GPT
    assert plan.count("0657FD6D-A4AB-43C4-84E5-0933C84B4F4F") == 2  # the real swap GUID, twice
    lines = plan.splitlines()
    start = lines.index("== table to write ==") + 1
    end = next(i for i, l in enumerate(lines) if l.startswith("DRY-RUN"))
    apply_result = subprocess.run(
        ["sfdisk", "--force", "--no-reread", str(gpt_image)],
        input="\n".join(lines[start:end]) + "\n", text=True, capture_output=True,
    )
    assert apply_result.returncode == 0, apply_result.stderr
    after = subprocess.run(["sfdisk", "--dump", str(gpt_image)],
                            capture_output=True, text=True, check=True).stdout
    assert after.count("0657FD6D-A4AB-43C4-84E5-0933C84B4F4F") == 2
    assert 'name="gswap1"' in after and 'name="gswap2"' in after


def test_gpt_add_swap_fill_does_not_waste_ebr_gap_space(gpt_image):
    """Regression: the fill-size math used to reserve `--gap` sectors per
    partition unconditionally, even though GPT (and MBR --placement primary)
    partitions need no EBR gap. That wasted ~1 MiB per swap partition for no
    reason. After the fix, only alignment rounding (< one --align unit)
    should be left unused.
    """
    align, count = 2048, 2
    free_out = run_cli("--disk", str(gpt_image), "free").stdout
    rs, re_ = (int(x) for x in re.search(r'\[\s*(\d+)\s*\.\.\s*(\d+)\]', free_out).groups())
    free_total = re_ - rs + 1
    plan = run_cli("--disk", str(gpt_image), "add-swap", "--count", str(count),
                    "--size", "fill", "--labels", "gswap1,gswap2").stdout
    sizes = [int(m.group(1)) for m in re.finditer(r'NEW \S+: start=\d+ size=(\d+)', plan)]
    assert len(sizes) == count
    # with the bug (unconditional gap reservation) the shortfall would exceed
    # count*gap == 4096 sectors on top of alignment rounding
    assert sum(sizes) >= free_total - count * align


def test_real_unsupported_disklabel_is_refused_not_mis_parsed():
    d = Path("/dev/shm") / f"ipe-r1-unsup-{os.getpid()}"
    d.mkdir(exist_ok=True)
    img = d / "disk.img"
    try:
        subprocess.run(["truncate", "-s", "1G", str(img)], check=True)
        subprocess.run(["sfdisk", "--force", str(img)], input="label: sun\n",
                        text=True, capture_output=True)  # sun may not be buildable; guarded below
        dumped = subprocess.run(["sfdisk", "--dump", str(img)],
                                 capture_output=True, text=True)
        if dumped.returncode != 0 or "label: sun" not in dumped.stdout:
            pytest.skip("this sfdisk build cannot create a sun disklabel to test against")
        result = run_cli("--disk", str(img), "list")
        assert result.returncode != 0
        assert "unsupported disklabel 'sun'" in (result.stdout + result.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- adversarial: exhaustion / sizing edge cases (fake run, fast) ---


DOS_DUMP = """label: dos
unit: sectors
sector-size: 512

/dev/vda1 : start=        2048, size=    16777216, type=83
/dev/vda2 : start=    16779264, size=    24176640, type=5
/dev/vda5 : start=    16781312, size=     8388608, type=83
"""


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def fake_run(cmd, *, input=None, text=None, capture_output=None):
    if cmd[:2] == ["sfdisk", "-d"]:
        return FakeCompleted(stdout=DOS_DUMP)
    if cmd[:2] == ["blockdev", "--getsz"]:
        return FakeCompleted(stdout="41943040\n")
    return FakeCompleted()


def make_table(mod, monkeypatch):
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return mod.Table("/dev/vda")


def test_add_swap_zero_free_space_is_a_clean_refusal_not_a_crash(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    t.add(None, "83", "primary", 2048, 2048)  # consume the only primary gap
    with pytest.raises(SystemExit, match="no free primary region available"):
        t.add(1, "83", "primary", 2048, 2048)


def test_add_swap_fill_math_never_divides_by_zero_via_cli(mod, monkeypatch, capsys):
    # regression for the --count 0 ZeroDivisionError
    make_table(mod, monkeypatch)
    old_argv = sys.argv
    sys.argv = ["inuse-partition-editor.py", "--disk", "/dev/vda", "add-swap",
                "--count", "0", "--labels", "x"]
    try:
        with pytest.raises(SystemExit) as exc:
            mod.main()
    finally:
        sys.argv = old_argv
    assert "ZeroDivisionError" not in str(exc.value)
    assert "--count must be >= 1" in str(exc.value)


def test_backup_filename_is_timestamped_and_checksummed(mod, monkeypatch, tmp_path):
    """Regression for the fixed-filename backup clobber: write() must not
    reuse the same backup path across commits, and must print a checksum.
    Root and the real sfdisk/partx calls are faked; self.added is left
    empty so the device-materialization wait loop never runs.
    """
    written = {}  # path -> io.StringIO buffer (the real code never calls .close())

    def fake_open(path, mode="r"):
        import io
        if mode == "w":
            written[path] = io.StringIO()
            return written[path]
        raise FileNotFoundError(path)

    t = make_table(mod, monkeypatch)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    monkeypatch.setattr(mod, "open", fake_open, raising=False)
    monkeypatch.setattr(mod, "time", __import__("time"))

    import io as _io
    printed = _io.StringIO()
    monkeypatch.setattr(sys, "stdout", printed)
    try:
        assert t.write(commit=True) is True
    finally:
        monkeypatch.setattr(sys, "stdout", sys.__stdout__)

    backup_paths = list(written)
    assert len(backup_paths) == 1
    path = backup_paths[0]
    assert path.startswith("/root/parttable-vda-")
    assert path.endswith(".backup.sfdisk")
    stamp = path[len("/root/parttable-vda-"):-len(".backup.sfdisk")]
    assert stamp.isdigit()
    assert "sha256=" in printed.getvalue()


def test_part_dev_naming_matches_loop_device_convention(mod, monkeypatch):
    t = make_table(mod, monkeypatch)
    t.disk = "/dev/loop7"
    p = t.add(2048, "83", "primary", 2048, 2048)
    assert p["dev"] == "/dev/loop7p3"


# --- root+loop-device full commit contract (see docker/systemd note) ---


@pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("losetup") is None,
    reason="needs root + losetup for a real --commit contract test; "
           "run inside the privileged systemd container described in the README",
)
def test_real_commit_via_loop_device_materializes_partition_nodes(tmp_path):
    img = tmp_path / "commit.img"
    make_dos_image(img)
    loop = subprocess.run(["losetup", "--find", "--show", "--partscan", str(img)],
                           capture_output=True, text=True, check=True).stdout.strip()
    try:
        result = run_cli("--disk", loop, "add-swap", "--count", "1",
                          "--size", "fill", "--labels", "gswap1", "--commit")
        assert result.returncode == 0
        assert os.path.exists(f"{loop}p6")
    finally:
        subprocess.run(["losetup", "--detach", loop], check=False)


@pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("losetup") is None,
    reason="needs root + losetup for a real --commit contract test; "
           "run inside the privileged systemd container described in the README",
)
def test_real_gpt_commit_via_loop_device_materializes_partition_nodes(tmp_path):
    img = tmp_path / "commit-gpt.img"
    make_gpt_image(img)
    loop = subprocess.run(["losetup", "--find", "--show", "--partscan", str(img)],
                           capture_output=True, text=True, check=True).stdout.strip()
    try:
        result = run_cli("--disk", loop, "add-swap", "--count", "1",
                          "--size", "fill", "--labels", "gswap1", "--commit")
        assert result.returncode == 0
        assert os.path.exists(f"{loop}p3")
    finally:
        subprocess.run(["losetup", "--detach", loop], check=False)

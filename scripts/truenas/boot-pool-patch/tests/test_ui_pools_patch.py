"""Contract tests for v27/ui-pools.patch and the boot_disk_partition helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys
import types

import pytest

PATCH_ROOT = Path(__file__).resolve().parents[1]
PATCH = PATCH_ROOT / "v27" / "ui-pools.patch"
TARGETS = ["middlewared/plugins/pool_/pool.py", "middlewared/plugins/disk_/availability.py"]
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def split_files(text: str) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("--- a/"):
            current = line[6:]
            files[current] = []
        elif current is not None:
            files[current].append(line)
    return files


def test_hunk_headers_match_their_bodies() -> None:
    files = split_files(PATCH.read_text())
    assert sorted(files) == sorted(TARGETS)
    for name, lines in files.items():
        hunks, i = 0, 0
        while i < len(lines):
            m = HUNK.match(lines[i])
            if not m:
                i += 1
                continue
            hunks += 1
            old_n = int(m.group(2) or 1)
            new_n = int(m.group(4) or 1)
            old = new = 0
            i += 1
            while i < len(lines) and (old < old_n or new < new_n):
                line = lines[i]
                if line.startswith("\\"):
                    i += 1
                    continue
                tag = line[:1]
                if tag == " " or line == "":
                    old += 1; new += 1
                elif tag == "-":
                    old += 1
                elif tag == "+":
                    new += 1
                else:
                    break
                i += 1
            assert (old, new) == (old_n, new_n), (name, lines[i - 1])
        assert hunks >= 1, name


def test_targets_apply_script_and_bundle_know_the_patch() -> None:
    targets = (PATCH_ROOT / "v27" / "targets.sha256").read_text()
    for target in TARGETS:
        assert re.search(rf"^[0-9a-f]{{64}}  {re.escape(target)}$", targets, re.M), target
    apply_script = (PATCH_ROOT / "acceptance" / "apply_v27_patch_remote.sh").read_text()
    assert 'patch --dry-run --batch --fuzz=0 -p1 < "$patch_dir/ui-pools.patch"' in apply_script
    assert 'patch --batch --fuzz=0 -p1 < "$patch_dir/ui-pools.patch"' in apply_script
    assert apply_script.index("--dry-run --batch --fuzz=0 -p1 < \"$patch_dir/ui-pools.patch\"") < \
        apply_script.index("systemctl stop middlewared.service")
    assert '"$bundle_root/shared/boot_disk_partition.py" middlewared/plugins/' in apply_script
    for compiled in ("middlewared/plugins/boot_disk_partition.py", "middlewared/plugins/disk_/availability.py",
                     "middlewared/plugins/pool_/pool.py"):
        assert compiled in apply_script
    controller = (PATCH_ROOT / "acceptance" / "live_v27_patch.py").read_text()
    assert '"v27" / "ui-pools.patch"' in controller
    assert '"shared" / "boot_disk_partition.py"' in controller


def test_patch_keeps_the_synthetic_row_safe() -> None:
    text = PATCH.read_text()
    assert "info | {'id': 0, 'guid': guid, 'all_sed': False}" in text  # id/guid win over info
    assert "if app is not None and" in text  # internal callers never see the row
    assert "asyncio.wait_for(proc.communicate(), timeout=10)" in text
    assert "boot_disk_partition.topology_match" in text
    assert "boot_disk_partition.pseudo_disks" in text


def test_patch_is_anchored_and_fails_on_drift(tmp_path: Path) -> None:
    patch_bin = shutil.which("patch")
    if patch_bin is None:
        pytest.skip("patch is not installed")
    # Each hunk is checked on its own pre-image (its context and removed lines).
    only = tmp_path / "x"
    only.mkdir()
    counter = 0
    for name, lines in split_files(PATCH.read_text()).items():
        starts = [i for i, line in enumerate(lines) if HUNK.match(line)]
        starts.append(len(lines))
        for a, b in zip(starts, starts[1:]):
            hunk = lines[a:b]
            pre = [ln[1:] for ln in hunk[1:] if ln[:1] in " -"]
            m = HUNK.match(hunk[0])
            padded = ["pad"] * (int(m.group(1)) - 1) + pre
            counter += 1
            sub = tmp_path / f"h{counter}"
            sub.mkdir()
            f = sub / "file.py"
            f.write_text("\n".join(padded) + "\n")
            one = sub / "one.patch"
            one.write_text(f"--- a/file.py\n+++ b/file.py\n" + "\n".join(hunk) + "\n")
            ok = subprocess.run([patch_bin, "--dry-run", "--batch", "--fuzz=0", "-p1", "-i", str(one)],
                                cwd=sub, capture_output=True, text=True)
            assert ok.returncode == 0, ok.stdout + ok.stderr
            # Drift: change an anchoring context/removed line; the hunk must be refused.
            drifted = "\n".join(padded).replace(pre[len(pre) // 2], pre[len(pre) // 2] + "  # drift", 1)
            f.write_text(drifted + "\n")
            bad = subprocess.run([patch_bin, "--dry-run", "--batch", "--fuzz=0", "-p1", "-i", str(one)],
                                 cwd=sub, capture_output=True, text=True)
            assert bad.returncode != 0, "hunk applied silently to drifted source"


def load_helper():
    service = types.ModuleType("middlewared.service")
    service.CallError = type("CallError", (Exception,), {})
    service.Service = type("Service", (), {})
    service.private = lambda fn: fn
    pkg = types.ModuleType("middlewared")
    sys.modules.setdefault("middlewared", pkg)
    sys.modules["middlewared.service"] = service
    spec = importlib.util.spec_from_file_location("boot_disk_partition", PATCH_ROOT / "shared" / "boot_disk_partition.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ZFS = "6a898cc3-1dd2-11b2-99a6-080020736631"
UUID = "11111111-2222-3333-4444-555555555555"


def part(path: str, number: int, label: str | None, **extra) -> dict:
    return {"path": path, "name": path, "pkname": "/dev/vda", "partlabel": label, "parttype": ZFS,
            "partuuid": UUID, "partn": number, "size": 1 << 30, "fstype": None, "mountpoint": None,
            "type": "part"} | extra


def test_only_the_reserved_unused_partition_is_ever_offered() -> None:
    helper = load_helper()
    tree = {"blockdevices": [{"path": "/dev/vda", "type": "disk", "children": [
        part("/dev/vda3", 3, None),                      # boot pool partition
        part("/dev/vda4", 4, "boot-disk-data"),          # reserved: offered
        part("/dev/vda5", 5, "boot-disk-data", fstype="zfs_member"),
        part("/dev/vda6", 6, "boot-disk-data", mountpoint="/mnt/x"),
        part("/dev/vda7", 7, "other"),
        part("/dev/vda8", 8, "boot-disk-data", parttype="0fc63daf-8483-4772-8e79-3d69d8477de4"),
        part("/dev/vda9", 9, "boot-disk-data", partuuid="bad"),
        part("/dev/vda2", 2, "boot-disk-data"),          # number below 4
    ]}]}
    offered = helper.reserved_partitions_from_lsblk(tree, set())
    assert [row["name"] for row in offered] == ["vda4"]
    assert helper.reserved_partitions_from_lsblk(tree, {"/dev/vda4"}) == []


def test_topology_match_refuses_anything_but_a_single_stripe_partition() -> None:
    helper = load_helper()
    reserved = [{"name": "vda4", "path": "/dev/vda4"}]
    ok = helper.match_topology({"data": [{"type": "STRIPE", "disks": ["vda4"]}]}, reserved)
    assert ok == (reserved[0], None)
    assert helper.match_topology({"data": [{"type": "STRIPE", "disks": ["sdb"]}]}, reserved) == (None, None)
    for topology in (
        {"data": [{"type": "MIRROR", "disks": ["vda4", "sdb"]}]},
        {"data": [{"type": "STRIPE", "disks": ["vda4"]}], "cache": [{"type": "STRIPE", "disks": ["sdb"]}]},
        {"data": [{"type": "MIRROR", "disks": ["vda4"]}]},
        {"data": [{"type": "STRIPE", "disks": ["vda4"]}], "spares": ["vda4"]},
    ):
        partition, error = helper.match_topology(topology, reserved)
        assert partition is None and error, topology

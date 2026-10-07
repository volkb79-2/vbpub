"""io.cost benchmark step (IO-BENCHMARK-DESIGN.md): throwaway partition lifecycle.

Everything runs against SimDisk, an in-memory sfdisk/partx/mkfs/mount/umount
simulator behind the real HostActions allowlist. Nothing here touches a real
device, loop device, swap or fio.
"""
from __future__ import annotations

from dataclasses import replace
import json
import re
import stat
from pathlib import Path

import pytest

from debian_install_v2 import installer as installer_module
from debian_install_v2.actions import (
    ActionError, ActionTimeout, ActionUnreapable, HostActions, PlannedAction,
)
from debian_install_v2.config import Config
from debian_install_v2.inuse_partition_editor import ATTR_RE
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.state import StateStore

GIB = 1024 ** 3
SECTORS_PER_GIB = GIB // 512
ROOT_START = 2_500_608
ROOT_SIZE = 8 * SECTORS_PER_GIB
LINUX = "0fc63daf-8483-4772-8e79-3d69d8477de4"

TOOL_OUTPUT = (
    "fio: warning, something chatty on stderr (merged into stdout by HostActions)\n"
    "254:0 rbps=1234567890 rseqiops=11000 rrandiops=9000 "
    "wbps=987654321 wseqiops=8000 wrandiops=7000\n"
)


class SimDisk(HostActions):
    """sfdisk/partx/mkfs/mount/umount/tool simulator (non-dry-run HostActions)."""

    def __init__(self, disk_gib: int = 100, tool_output: str = TOOL_OUTPUT) -> None:
        super().__init__(dry_run=False)
        self.disk_bytes = disk_gib * GIB
        self.table: dict[int, dict[str, str]] = {
            3: {"start": str(ROOT_START), "size": str(ROOT_SIZE), "type": LINUX.upper(), "uuid": "ROOT-UUID"}
        }
        self.kernel: set[int] = {3}
        self.mounts: dict[str, str] = {"/dev/vda3": "/"}
        self.inputs: list[tuple[tuple[str, ...], str | None]] = []
        self.tool_output = tool_output
        self.fail: dict[str, Exception | str] = {}  # command name -> error to raise
        self.restore_is_noop = False
        self.restore_mutator = None  # called with the SimDisk after every restore write
        self.flaky: dict[tuple[str, str], int] = {}  # (command, argv[1]) -> failures left before success
        self.partial_write_fails = False  # forward benchmark write lands, then sfdisk errors
        self.label_id = "SIM-LABEL-ID"
        self.timeouts: list[tuple[str, float | None]] = []
        self.sysfs: dict[str, str] = {}
        self.tool_changes_sysfs = True
        self.mkfs_devices: list[str] = []
        self._uuid = 0
        self.allowed_root = "/nonexistent-set-by-make"

    # -- helpers -----------------------------------------------------------
    def dump(self) -> str:
        lines = ["label: gpt", f"label-id: {self.label_id}", "device: /dev/vda", "unit: sectors", "sector-size: 512", ""]
        for number in sorted(self.table):
            attrs = self.table[number]
            extra = f', name="{attrs["name"]}"' if "name" in attrs else ""
            lines.append(
                f"/dev/vda{number} : start={int(attrs['start']):>12}, size={int(attrs['size']):>12}, "
                f"type={attrs['type']}, uuid={attrs['uuid']}{extra}"
            )
        return "\n".join(lines) + "\n"

    def load_dump(self, text: str) -> None:
        table: dict[int, dict[str, str]] = {}
        label = re.search(r"^label-id:\s*(\S+)", text, re.M)
        if label:
            self.label_id = label.group(1)
        for line in text.splitlines():
            match = re.match(r"^/dev/vda(\d+)\s*:(.*)$", line)
            if not match:
                continue
            attrs = {k: v.strip('"') for k, v in ATTR_RE.findall(match.group(2))}
            attrs["type"] = attrs["type"].upper()
            if "uuid" not in attrs:
                self._uuid += 1
                attrs["uuid"] = f"SIM-UUID-{self._uuid}"
            table[int(match.group(1))] = attrs
        self.table = table

    def tags(self) -> list[str]:
        """Collapse planned actions into the lifecycle events the tests order."""
        out: list[str] = []
        for action in self.planned:
            argv = action.argv
            name = Path(argv[0]).name
            if name == "sfdisk" and "--dump" in argv:
                out.append("dump")
            elif name == "sfdisk" and "--force" in argv:
                # forward writes carry no description (it defaults to the argv); restores name their purpose
                out.append("write" if action.description.startswith("/usr/sbin/sfdisk") else "restore")
            elif name == "partx":
                out.append("partx" + argv[1])
            elif name in {"mkfs.ext4", "mount", "umount", "iocost_coef_gen.py", "mkswap", "swapon", "dd", "tee"}:
                out.append(name)
        return out

    # -- HostActions overrides ---------------------------------------------
    def run(self, argv, description="", dangerous=False, input=None, timeout=None):
        self._validate(list(argv))
        self.timeouts.append((Path(argv[0]).name, timeout))
        self.planned.append(PlannedAction(tuple(argv), description or " ".join(argv), dangerous))
        self.inputs.append((tuple(argv), input))
        name = Path(argv[0]).name
        if name == "iocost_coef_gen.py" and self.tool_changes_sysfs:
            # what the real generator does to the whole disk for the run
            for path in self.sysfs:
                self.sysfs[path] = "none" if path.endswith("scheduler") else "1"
        if name in self.fail:
            err = self.fail[name]
            if isinstance(err, Exception):
                raise err
            raise ActionError(err)
        if name == "findmnt" and "-n" in argv:
            return "/dev/vda3\n"
        if name == "findmnt":
            return "".join(f"{dev} {target} ext4\n" for dev, target in self.mounts.items())
        if name == "blockdev":
            return str(self.disk_bytes)
        if name == "sfdisk" and "--dump" in argv:
            return self.dump()
        if name == "sfdisk" and "--force" in argv:
            forward = not description
            if forward or not self.restore_is_noop:
                self.load_dump(input or "")
            if not forward and self.restore_mutator:
                self.restore_mutator(self)
            if forward and self.partial_write_fails and "vbpub-iobench" in (input or ""):
                raise ActionError("sfdisk: write failed midway")
            return ""
        if (name, argv[1] if len(argv) > 1 else "") in self.flaky and self.flaky[(name, argv[1])] > 0:
            self.flaky[(name, argv[1])] -= 1
            raise ActionError(f"{name} {argv[1]}: device or resource busy")
        if name == "tee":
            self.sysfs[argv[1]] = (input or "").strip()
            return ""
        if name == "partx":
            first, _, last = argv[argv.index("--nr") + 1].partition(":") if "--nr" in argv else ("", "", "")
            if argv[1] == "-a":
                self.kernel |= {n for n in range(int(first), int(last) + 1) if n in self.table}
            elif argv[1] == "-d":
                self.kernel -= set(range(int(first), int(last) + 1))
            return ""
        if name == "mkfs.ext4":
            self.mkfs_devices.append(argv[-1])
            return ""
        if name == "mount":
            self.mounts[argv[-2]] = argv[-1]
            return ""
        if name == "umount":
            self.mounts.pop(argv[-1], None)
            return ""
        if name == "iocost_coef_gen.py":
            return self.tool_output
        return ""

    def write_file(self, path: str, content: str, mode: int = 0o644) -> None:
        # Safety net: a stage2 step that forgot to be stubbed must never write
        # outside the test's tmp dir (these tests may run as root).
        assert path.startswith(self.allowed_root), f"test would write outside tmp: {path}"
        super().write_file(path, content, mode)

    def exists(self, path: str) -> bool:
        return int(path.removeprefix("/dev/vda")) in self.kernel


def make(tmp_path: Path, *, disk_gib: int = 100, **overrides) -> tuple[Installer, SimDisk]:
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        telegram_bot_token="",
        telegram_chat_id="",
        auto_reboot_after_stage1=False,
        swap_disk_total_gb=32,
        swap_file_count=8,
        run_io_benchmark=True,
        io_benchmark_duration_s=5,
        run_ksm=False,
        run_oomd_config=False,
        run_fstrim=False,
        run_auto_reboot=False,
        **overrides,
    )
    disk = SimDisk(disk_gib)
    disk.allowed_root = str(tmp_path)
    installer = Installer(config, disk)
    sys_block = tmp_path / "sys-block"
    queue = sys_block / "vda" / "queue"
    queue.mkdir(parents=True)
    (queue / "scheduler").write_text("[mq-deadline] none kyber\n")
    (queue / "nomerges").write_text("0\n")
    installer._SYS_BLOCK = sys_block
    disk.sysfs = {str(queue / "scheduler"): "mq-deadline", str(queue / "nomerges"): "0"}
    disk.planned.clear()  # drop the root-discovery probe made by the constructor
    disk.inputs.clear()
    StateStore(config.state_dir).save_new(StateStore.new(config))
    for hook in ("_configure_zswap", "_configure_iocost", "_configure_cgroup2_flags", "_activate_swap_partitions", "_health_gate_swap_devices"):
        setattr(installer, hook, lambda *a, **k: None)
    return installer, disk


def step(installer: Installer, name: str = "io_benchmark") -> dict:
    return StateStore(installer.config.state_dir).load()["steps"].get(name, {})


def index_after(tags: list[str], tag: str, after: int) -> int:
    return tags.index(tag, after + 1)


def assert_lifecycle_order(tags: list[str]) -> None:
    create = tags.index("write")
    verify1 = index_after(tags, "dump", create)
    mkfs = index_after(tags, "mkfs.ext4", verify1)
    mount = index_after(tags, "mount", mkfs)
    tool = index_after(tags, "iocost_coef_gen.py", mount)
    umount = index_after(tags, "umount", tool)
    delete = index_after(tags, "restore", umount)
    verify2 = index_after(tags, "dump", delete)
    swap = index_after(tags, "write", verify2)
    assert create < verify1 < mkfs < mount < tool < umount < delete < verify2 < swap
    # the swap shape's own write is the SECOND `write`: nothing else writes between
    assert tags.count("write") == 2


def bench_lines(disk: SimDisk) -> list[int]:
    return [n for n, a in disk.table.items() if a.get("name") == "vbpub-iobench"]


# --- happy path ---------------------------------------------------------------

def test_stage2_lifecycle_order_and_final_layout(tmp_path):
    installer, disk = make(tmp_path)
    installer._stage2()
    assert_lifecycle_order(disk.tags())
    assert bench_lines(disk) == []
    assert set(disk.table) == {3, *range(4, 12)}  # root + 8 swap partitions, no benchmark partition
    assert 12 not in disk.kernel
    assert disk.mounts == {"/dev/vda3": "/"}
    assert disk.mkfs_devices == ["/dev/vda12"]
    assert step(installer)["status"] == "success"
    assert "rbps 1177 MiB/s" in step(installer)["detail"]


def test_results_persisted_0600_with_metadata(tmp_path):
    installer, disk = make(tmp_path)
    installer._stage2()
    path = Path(installer.config.state_dir) / "io-benchmark.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    record = json.loads(path.read_text())
    assert record["results"] == {
        "rbps": 1234567890, "rseqiops": 11000, "rrandiops": 9000,
        "wbps": 987654321, "wseqiops": 8000, "wrandiops": 7000,
    }
    assert record["partition"] == "/dev/vda12" and record["device"] == "/dev/vda"
    assert record["duration_s"] == 5 and record["partition_size_gib"] == 32.0
    assert record["devno"] == "254:0"
    assert re.fullmatch(r"[0-9a-f]{64}", record["tool_sha256"])
    assert set(record["tool_header"]) == {"source-sha256", "patch-series-sha256"}
    assert record["timestamp"]


def test_tool_invocation_arguments(tmp_path):
    installer, disk = make(tmp_path)
    installer._stage2()
    argv = next(a.argv for a in disk.planned if Path(a.argv[0]).name == "iocost_coef_gen.py")
    assert argv[0].endswith("tools/iocost_coef_gen.py")
    assert argv[argv.index("--duration") + 1] == "5"
    assert "--quiet" in argv and argv[argv.index("--testfile") + 1].endswith("/iocost-coef-fio.testfile")
    assert float(argv[argv.index("--testfile-size-gb") + 1]) == 16.0  # min(16, 75% of 32 GiB)
    assert ("apt-get" in {Path(a.argv[0]).name for a in disk.planned})
    apt = [a.argv for a in disk.planned if a.argv[-2:] == ("fio", "pv")]
    assert apt


def test_does_not_touch_iocost_model_or_qos(tmp_path):
    installer, disk = make(tmp_path)
    installer._stage2()
    assert not any("io.cost" in " ".join(a.argv) and "cgroup" in " ".join(a.argv) for a in disk.planned)
    assert not any(a.argv[0] == "/usr/bin/tee" and "io.cost" in a.argv[1] for a in disk.planned)


def test_disabled_does_nothing(tmp_path):
    installer, disk = make(tmp_path)
    installer.config = replace(installer.config, run_io_benchmark=False)
    installer._run_io_benchmark(swap_written=False)
    assert disk.planned == [] and step(installer) == {}


def test_terminal_marker_skips_rerun(tmp_path):
    installer, disk = make(tmp_path)
    installer._mark_step("io_benchmark", "success", "earlier")
    installer._run_io_benchmark(swap_written=False)
    assert disk.planned == []


# --- sizing ---------------------------------------------------------------------

def test_too_small_free_space_skips_cleanly_and_swap_still_applied(tmp_path):
    installer, disk = make(tmp_path, disk_gib=44)
    installer._stage2()
    state = step(installer)
    assert state["status"] == "skipped" and "under 2 GiB" in state["detail"]
    tags = disk.tags()
    assert "mkfs.ext4" not in tags and "umount" not in tags and tags.count("write") == 1  # swap only
    assert not (Path(installer.config.state_dir) / "io-benchmark.json").exists()


def test_size_is_capped_by_swap_requirement(tmp_path):
    installer, disk = make(tmp_path, disk_gib=50)
    partitions, _ = installer._plan_swap_partitions()
    swap_end = partitions[-1][0] + partitions[-1][1]
    installer._run_io_benchmark(swap_written=False)
    create_input = next(i for argv, i in disk.inputs if "--no-reread" in argv)
    entry = installer._parse_partition_entries(create_input)[12]
    start, size = int(entry["start"]), int(entry["size"])
    assert size < 32 * SECTORS_PER_GIB  # the 32 GiB max did NOT win
    assert start >= swap_end + SECTORS_PER_GIB  # never inside swap's region (+1 GiB margin)
    assert start + size <= 50 * SECTORS_PER_GIB - 2048
    assert start % 2048 == 0 and size % 2048 == 0
    assert entry["name"] == "vbpub-iobench" and entry["type"].lower() == LINUX


def test_size_capped_by_config_max(tmp_path):
    installer, disk = make(tmp_path, io_benchmark_max_size_gb=4)
    installer._run_io_benchmark(swap_written=False)
    create_input = next(i for argv, i in disk.inputs if "--no-reread" in argv)
    assert int(installer._parse_partition_entries(create_input)[12]["size"]) == 4 * SECTORS_PER_GIB


def test_plan_benchmark_partition_boundary(tmp_path):
    installer, _ = make(tmp_path)
    disk_sectors = 100 * SECTORS_PER_GIB
    # exactly 2 GiB available beyond swap + 1 GiB margin -> allowed; one aligned unit less -> None
    end_aligned = (disk_sectors - 2048) // 2048 * 2048
    swap_end = end_aligned - 3 * SECTORS_PER_GIB
    assert installer._plan_benchmark_partition(disk_sectors, swap_end) == (end_aligned - 2 * SECTORS_PER_GIB, 2 * SECTORS_PER_GIB)
    assert installer._plan_benchmark_partition(disk_sectors, swap_end + 2048) is None


def test_case_b_swap_already_written_uses_tail_after_existing_partitions(tmp_path):
    installer, disk = make(tmp_path, disk_gib=60)
    partitions, _ = installer._plan_swap_partitions()
    for index, (start, size) in enumerate(partitions, start=4):
        disk.table[index] = {"start": str(start), "size": str(size), "type": "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F", "uuid": f"S{index}"}
        disk.kernel.add(index)
    installer._run_io_benchmark(swap_written=True)
    assert step(installer)["status"] == "success"
    assert set(disk.table) == {3, *range(4, 12)}  # swap untouched, benchmark partition gone
    assert disk.table[11]["start"] == str(partitions[7][0])


# --- resume idempotency ----------------------------------------------------------

def test_leftover_partition_removed_before_swap(tmp_path):
    installer, disk = make(tmp_path)
    disk.table[12] = {"start": str(80 * SECTORS_PER_GIB), "size": str(10 * SECTORS_PER_GIB), "type": LINUX.upper(),
                      "uuid": "LEFT", "name": "vbpub-iobench"}
    disk.kernel.add(12)
    disk.mounts["/dev/vda12"] = "/tmp/vbpub-iobench-old"
    installer._mark_step("io_benchmark", "started", "")
    installer._stage2()
    tags = disk.tags()
    first_swap_write = tags.index("write")
    # the leftover is unmounted and deleted (restore) before ANY partition-table write of ours
    assert tags.index("umount") < tags.index("restore") < first_swap_write
    assert bench_lines(disk) == [] and 12 not in disk.kernel
    assert set(disk.table) == {3, *range(4, 12)}
    assert disk.mounts == {"/dev/vda3": "/"}
    # leftover derived restore dump must not mention the benchmark partition
    restore_input = [i for argv, i in disk.inputs if "--force" in argv][0]
    assert "vda12" not in restore_input and "vda3" in restore_input


def test_foreign_partition_at_benchmark_number_is_not_touched(tmp_path):
    installer, disk = make(tmp_path)
    disk.table[12] = {"start": str(80 * SECTORS_PER_GIB), "size": str(SECTORS_PER_GIB), "type": LINUX.upper(), "uuid": "X", "name": "mine"}
    installer._run_io_benchmark(swap_written=False)
    assert step(installer)["status"] == "skipped"
    assert 12 in disk.table and "restore" not in disk.tags() and "write" not in disk.tags()


# --- failure semantics -----------------------------------------------------------

def test_benchmark_failure_cleans_up_and_install_continues(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["iocost_coef_gen.py"] = "fio exploded"
    installer._stage2()  # must NOT raise
    tags = disk.tags()
    assert "umount" in tags and "restore" in tags
    assert tags.index("restore") < len(tags) - 1 - tags[::-1].index("write")  # delete precedes swap write
    assert bench_lines(disk) == [] and set(disk.table) == {3, *range(4, 12)}
    state = step(installer)
    assert state["status"] == "warned" and "fio exploded" in state["detail"]
    assert StateStore(installer.config.state_dir).load()["phase"] == "done"
    assert not (Path(installer.config.state_dir) / "io-benchmark.json").exists()


def test_unparseable_tool_output_is_advisory_failure(tmp_path):
    installer, disk = make(tmp_path)
    disk.tool_output = "no result line here\n"
    installer._stage2()
    assert step(installer)["status"] == "warned" and bench_lines(disk) == []


def test_mkfs_failure_still_deletes_partition(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["mkfs.ext4"] = "mkfs boom"
    installer._stage2()
    assert step(installer)["status"] == "warned"
    assert "umount" not in disk.tags()  # never mounted, so nothing to unmount
    assert bench_lines(disk) == [] and 12 not in disk.kernel


def test_create_readback_mismatch_is_advisory_and_restores(tmp_path):
    installer, disk = make(tmp_path)
    real_load = disk.load_dump

    def corrupt(text: str) -> None:
        real_load(text)
        if 12 in disk.table:  # only the forward write carries the benchmark partition
            disk.table[12]["start"] = str(int(disk.table[12]["start"]) + 2048)

    disk.load_dump = corrupt  # type: ignore[method-assign]
    installer._run_io_benchmark(swap_written=False)
    assert step(installer)["status"] == "warned" and "verification failed" in step(installer)["detail"]
    assert bench_lines(disk) == [] and set(disk.table) == {3}


def test_umount_failure_stops_before_swap(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["umount"] = "target is busy"
    with pytest.raises(InstallerError, match="cleanup failed"):
        installer._stage2()
    assert disk.tags().count("write") == 1  # only the benchmark partition was ever written
    assert "restore" not in disk.tags()  # never tried to delete a mounted partition
    assert step(installer)["status"] == "failed"
    assert StateStore(installer.config.state_dir).load().get("phase") != "done"


def test_cleanup_that_does_not_restore_layout_stops_before_swap(tmp_path):
    installer, disk = make(tmp_path)
    disk.restore_is_noop = True  # sfdisk --force restore silently does nothing
    with pytest.raises(InstallerError, match="cleanup failed.*differs"):
        installer._stage2()
    assert disk.tags().count("write") == 1
    assert step(installer)["status"] == "failed"


def test_cleanup_node_still_present_stops(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    monkeypatch.setattr(installer_module.time, "sleep", lambda s: None)
    disk.exists = lambda path: True  # type: ignore[method-assign]
    with pytest.raises(InstallerError, match="still exists"):
        installer._run_io_benchmark(swap_written=False)


def test_failed_marker_is_not_terminal_so_resume_rechecks(tmp_path):
    installer, disk = make(tmp_path)
    installer._mark_step("io_benchmark", "failed", "cleanup failed earlier")
    disk.table[12] = {"start": str(80 * SECTORS_PER_GIB), "size": str(10 * SECTORS_PER_GIB), "type": LINUX.upper(),
                      "uuid": "LEFT", "name": "vbpub-iobench"}
    disk.kernel.add(12)
    installer._run_io_benchmark(swap_written=False)
    assert bench_lines(disk) == [] and step(installer)["status"] == "success"


# --- dry-run -----------------------------------------------------------------------

def test_dry_run_records_full_ordered_sequence(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"), telegram_bot_token="",
        telegram_chat_id="", auto_reboot_after_stage1=False, run_io_benchmark=True,
    )
    StateStore(config.state_dir).save_new(StateStore.new(config))
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions, inspect_host=False)
    installer.root_disk, installer.root_partition_path, installer.root_number = "vda", "/dev/vda3", 3
    installer._run_io_benchmark(swap_written=False)
    installer._apply_known_swap_shape()

    sim = SimDisk()
    sim.planned = actions.planned
    tags = sim.tags()
    # dry-run records the benchmark's own write; _apply_known_swap_shape()'s dry-run records none
    assert tags.count("write") == 1 and tags.count("restore") == 1
    create = tags.index("write")
    verify1 = index_after(tags, "dump", create)
    assert create < verify1 < tags.index("mkfs.ext4") < tags.index("mount") < tags.index("iocost_coef_gen.py")
    assert tags.index("iocost_coef_gen.py") < tags.index("umount") < tags.index("restore")
    assert index_after(tags, "dump", tags.index("restore")) > tags.index("restore")
    assert not Path(config.state_dir, "io-benchmark.json").exists()
    assert not Path("/tmp/vbpub-iobench-dry-run").exists()


# --- parsing / tool verification ------------------------------------------------------

def test_parse_result_takes_final_matching_line():
    devno, results = Installer._parse_iocost_result(TOOL_OUTPUT)
    assert devno == "254:0" and results["wrandiops"] == 7000 and results["rbps"] == 1234567890


@pytest.mark.parametrize("output", [
    "",
    "254:0 rbps=1 rseqiops=2 rrandiops=3 wbps=4 wseqiops=5\n",
    "254:0 rbps=1 rseqiops=2 rrandiops=3 wbps=4 wseqiops=5 wrandiops=x\n",
    "254:0 rbps=1 rseqiops=2 rrandiops=3 wbps=4 wseqiops=5 bogus=6\n",
    "rbps=1 rseqiops=2 rrandiops=3 wbps=4 wseqiops=5 wrandiops=6\n",
])
def test_parse_result_rejects_malformed(output):
    with pytest.raises(InstallerError):
        Installer._parse_iocost_result(output)


def test_shipped_generator_verifies_against_vendor(tmp_path):
    installer, _ = make(tmp_path)
    digest, header = installer._verify_iocost_generator(installer._iocost_tool_path())
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert header["source-sha256"] == "7be1ffde0780b867271ca700abd3a80a5a1c965421dcb4f4e54661f8352f80ee"


def test_generator_verification_rejects_bad_artifacts(tmp_path):
    import hashlib

    installer, _ = make(tmp_path)
    real = installer._iocost_tool_path().read_bytes()
    root = tmp_path / "tree"
    (root / "tools").mkdir(parents=True)
    vendor = root / "debian_install_v2" / "vendor"
    vendor.mkdir(parents=True)
    for source in (installer._iocost_tool_path().parents[1] / "debian_install_v2" / "vendor").iterdir():
        if source.suffix in {".py", ".patch"}:
            (vendor / source.name).write_bytes(source.read_bytes())
    tool = root / "tools" / "iocost_coef_gen.py"
    digest = tool.with_name(tool.name + ".sha256")

    def install(data: bytes, *, sha: bool = True) -> None:
        """Write the tool plus (by default) a digest file that matches it."""
        tool.write_bytes(data)
        if sha:
            digest.write_text(f"{hashlib.sha256(data).hexdigest()}  iocost_coef_gen.py\n")

    with pytest.raises(InstallerError, match="not found"):
        installer._verify_iocost_generator(tool)
    install(real)
    installer._verify_iocost_generator(tool)  # the copy verifies
    install(real.replace(b"source-sha256: 7be1", b"source-sha256: 0000", 1))
    with pytest.raises(InstallerError, match="stale"):
        installer._verify_iocost_generator(tool)
    install(b"#!/usr/bin/env python3\nprint('not generated')\n")
    with pytest.raises(InstallerError, match="header missing"):
        installer._verify_iocost_generator(tool)
    install(real)
    (vendor / "0001-testdev-resolve-partition-to-parent-for-sysfs.patch").write_bytes(b"changed")
    with pytest.raises(InstallerError, match="stale"):
        installer._verify_iocost_generator(tool)


def test_tampered_tool_body_with_valid_header_is_refused(tmp_path):
    """The header hashes only vouch for the header text; the committed sha256 covers the BODY."""
    installer, _ = make(tmp_path)
    real = installer._iocost_tool_path().read_bytes()
    root = tmp_path / "tree"
    (root / "tools").mkdir(parents=True)
    tool = root / "tools" / "iocost_coef_gen.py"
    vendor_src = installer._iocost_tool_path().parents[1] / "debian_install_v2" / "vendor"
    (root / "debian_install_v2").mkdir()
    import shutil
    shutil.copytree(vendor_src, root / "debian_install_v2" / "vendor")
    shutil.copy(installer._iocost_tool_path().with_name("iocost_coef_gen.py.sha256"), tool.with_name("iocost_coef_gen.py.sha256"))
    tool.write_bytes(real)
    installer._verify_iocost_generator(tool)
    tool.write_bytes(real + b"\nimport os; os.system('id')\n")  # header untouched, body tampered
    with pytest.raises(InstallerError, match="tool integrity check failed"):
        installer._verify_iocost_generator(tool)
    tool.write_bytes(real)
    tool.with_name("iocost_coef_gen.py.sha256").unlink()
    with pytest.raises(InstallerError, match="tool integrity check failed"):
        installer._verify_iocost_generator(tool)


def test_committed_digest_matches_shipped_tool():
    import hashlib

    tool = Path(installer_module.__file__).resolve().parents[1] / "tools" / "iocost_coef_gen.py"
    committed = tool.with_name(tool.name + ".sha256").read_text().split()[0]
    assert committed == hashlib.sha256(tool.read_bytes()).hexdigest()


def test_integrity_failure_marks_warned_skips_benchmark_and_continues(tmp_path):
    installer, disk = make(tmp_path)
    real = installer._iocost_tool_path()
    bad = tmp_path / "tools"
    bad.mkdir()
    (bad / "iocost_coef_gen.py").write_bytes(real.read_bytes() + b"# tampered\n")
    (bad / "iocost_coef_gen.py.sha256").write_text(real.with_name("iocost_coef_gen.py.sha256").read_text())
    # the verifier resolves the vendor dir relative to the tool's parent's parent
    import shutil
    shutil.copytree(real.parents[1] / "debian_install_v2" / "vendor", tmp_path / "debian_install_v2" / "vendor")
    installer._iocost_tool_path = lambda: bad / "iocost_coef_gen.py"  # type: ignore[method-assign]
    installer._stage2()  # must NOT raise
    state = step(installer)
    assert state["status"] == "warned" and "tool integrity check failed" in state["detail"]
    assert disk.tags().count("write") == 1 and "mkfs.ext4" not in disk.tags()  # swap only, no benchmark partition


def test_bad_tool_artifact_is_advisory_and_never_partitions(tmp_path):
    installer, disk = make(tmp_path)
    installer._iocost_tool_path = lambda: tmp_path / "missing.py"  # type: ignore[method-assign]
    installer._run_io_benchmark(swap_written=False)
    assert step(installer)["status"] == "warned"
    assert "write" not in disk.tags()  # failed before any partition was created


# --- plan validation / allowlist --------------------------------------------------------

def test_benchmark_plan_validation_refuses_unsafe_plans(tmp_path):
    installer, _ = make(tmp_path)
    cur = {3: {"start": "2500608", "size": "100", "type": LINUX}}
    good = {**cur, 12: {"start": "5000000", "size": "2048", "type": LINUX}}
    installer._validate_benchmark_plan(cur, good, 12, 5000000, 2048, 0)
    with pytest.raises(InstallerError, match="exactly one"):
        installer._validate_benchmark_plan(cur, cur, 12, 5000000, 2048, 0)
    with pytest.raises(InstallerError, match="modify"):
        installer._validate_benchmark_plan(cur, {**good, 3: {"start": "2500608", "size": "99", "type": LINUX}}, 12, 5000000, 2048, 0)
    with pytest.raises(InstallerError, match="overlap"):
        installer._validate_benchmark_plan(cur, good, 12, 2500609, 2048, 0)
    with pytest.raises(InstallerError, match="swap shape needs"):
        installer._validate_benchmark_plan(cur, good, 12, 5000000, 2048, 5000000)


@pytest.mark.parametrize("argv", [
    ["/usr/bin/umount", "-f", "/dev/vda12"],
    ["/usr/bin/umount", "-l", "/dev/vda12"],
    ["/usr/bin/mount", "--bind", "/", "/mnt"],
    ["/usr/sbin/mkfs.ext4", "-E", "x", "/dev/vda12"],
    ["/opt/x/iocost_coef_gen.py", "--testdev", "/dev/vda"],
])
def test_allowlist_refuses_unexpected_options(argv):
    with pytest.raises(ActionError):
        HostActions._validate(argv)


# =============================================================================
# LT-IOB review fix round 1
# =============================================================================

def settle_sleeps(monkeypatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(installer_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def sysfs_original(disk: SimDisk) -> dict[str, str]:
    return {path: ("mq-deadline" if path.endswith("scheduler") else "0") for path in disk.sysfs}


# --- blocker 1: timeouts and the scheduler -------------------------------------------

def test_commands_carry_timeouts_and_tool_timeout_formula(tmp_path):
    installer, disk = make(tmp_path)
    installer._run_io_benchmark(swap_written=False)
    timeouts = {}
    for name, value in disk.timeouts:
        timeouts.setdefault(name, value)
    # 6 x duration(5) x 3 + testfile_gb(16) x 30 + 120
    assert timeouts["iocost_coef_gen.py"] == 6 * 5 * 3 + 16 * 30 + 120 == 690
    for name in ("sync", "umount", "mkfs.ext4"):
        assert 120 <= timeouts[name] <= 300, name
    assert Installer._iocost_tool_timeout(60, 16.0) == 6 * 60 * 3 + 16 * 30 + 120


def test_timeout_kwarg_is_only_forwarded_when_supplied(tmp_path):
    seen: list[dict] = []

    class Narrow(HostActions):
        def run(self, argv, description="", dangerous=False, input=None):  # no timeout parameter
            seen.append({"input": input})
            return "x"

    config = Config(state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l"), telegram_bot_token="", telegram_chat_id="")
    installer = Installer(config, Narrow(dry_run=True), inspect_host=False)
    assert installer._run(["/usr/bin/sync"], "no timeout") == "x"
    with pytest.raises(TypeError):
        installer._run(["/usr/bin/sync"], "with timeout", timeout=5)


def test_hung_tool_cleans_up_restores_scheduler_and_install_continues(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["iocost_coef_gen.py"] = ActionTimeout("action timed out after 690s and was killed")
    installer._stage2()  # must NOT raise
    tags = disk.tags()
    assert "umount" in tags and "restore" in tags and "dd" in tags
    assert bench_lines(disk) == [] and set(disk.table) == {3, *range(4, 12)}
    state = step(installer)
    assert state["status"] == "warned" and "timed out" in state["detail"]
    assert StateStore(installer.config.state_dir).load()["phase"] == "done"
    # the killed tool never ran its atexit; the installer put scheduler/nomerges back
    assert disk.sysfs == sysfs_original(disk)


def test_scheduler_restored_with_tee_after_success_too(tmp_path):
    installer, disk = make(tmp_path)
    installer._run_io_benchmark(swap_written=False)
    assert step(installer)["status"] == "success"
    assert disk.sysfs == sysfs_original(disk)
    tee_calls = [(argv, i) for argv, i in disk.inputs if Path(argv[0]).name == "tee"]
    assert sorted(i.strip() for _, i in tee_calls) == ["0", "mq-deadline"]
    planned = [Path(a.argv[0]).name + ":" + (a.argv[1] if Path(a.argv[0]).name == "tee" else "") for a in disk.planned]
    tool_at = planned.index("iocost_coef_gen.py:")
    assert all(i > tool_at for i, p in enumerate(planned) if p.startswith("tee:") and p[4:] in disk.sysfs)


def test_scheduler_snapshot_taken_before_the_tool_runs(tmp_path):
    installer, disk = make(tmp_path)
    queue = tmp_path / "sys-block" / "vda" / "queue"
    original = disk.run

    def spy(argv, *a, **k):
        if Path(argv[0]).name == "iocost_coef_gen.py":
            assert (queue / "scheduler").read_text().startswith("[mq-deadline]")  # untouched yet
        return original(argv, *a, **k)

    disk.run = spy  # type: ignore[method-assign]
    installer._run_io_benchmark(swap_written=False)
    assert disk.sysfs == sysfs_original(disk)


@pytest.mark.parametrize("command", ["umount", "sync"])
def test_hung_umount_or_sync_stops_the_install(tmp_path, command):
    installer, disk = make(tmp_path)
    disk.fail[command] = ActionTimeout(f"{command} timed out")
    with pytest.raises(InstallerError, match="cleanup failed"):
        installer._stage2()
    assert disk.tags().count("write") == 1 and "restore" not in disk.tags()
    assert step(installer)["status"] == "failed"
    assert StateStore(installer.config.state_dir).load().get("phase") != "done"
    assert disk.sysfs == sysfs_original(disk)  # the tool had already finished and restored


def test_unreapable_tool_child_stops_the_install(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["iocost_coef_gen.py"] = ActionUnreapable("child could not be reaped after SIGKILL")
    with pytest.raises(InstallerError, match="cleanup failed.*reaped"):
        installer._stage2()
    tags = disk.tags()
    assert tags.count("write") == 1  # no swap shape
    # a child we cannot reap may still hold the mount: nothing is unmounted/zeroed/deleted
    assert "umount" not in tags and "dd" not in tags and "restore" not in tags
    assert step(installer)["status"] == "failed"
    assert StateStore(installer.config.state_dir).load().get("phase") != "done"
    assert disk.sysfs == sysfs_original(disk)  # but the scheduler is still put back


def test_unreapable_umount_stops_the_install(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["umount"] = ActionUnreapable("umount stuck in D state")
    with pytest.raises(InstallerError, match="cleanup failed"):
        installer._stage2()
    assert disk.tags().count("write") == 1 and step(installer)["status"] == "failed"


# --- HostActions.run(timeout=) itself (real, harmless child processes) -----------------

def _alive(pid: int) -> bool:
    try:
        import os
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_bounded_run_returns_output_and_exit_status():
    import shutil
    sysctl = shutil.which("sysctl")
    if not sysctl:
        pytest.skip("sysctl not available")
    out = HostActions().run([sysctl, "-n", "kernel.ostype"], timeout=30)
    assert out.strip() == "Linux"
    with pytest.raises(ActionError, match="action failed"):
        HostActions().run([sysctl, "-n", "no.such.key.at.all"], timeout=30)


def test_bounded_run_kills_the_whole_process_group_on_timeout(tmp_path):
    import time as real_time
    pidfile = tmp_path / "grandchild.pid"
    script = f'sleep 300 & echo $! > {pidfile}; wait'
    with pytest.raises(ActionTimeout, match="timed out"):
        HostActions._run_bounded(["/bin/sh", "-c", script], None, 0.5, "hang", term_grace=2.0, reap_wait=2.0)
    grandchild = int(pidfile.read_text())
    real_time.sleep(0.2)
    assert not _alive(grandchild)  # orphaned grandchild (think fio) died with the group


def test_bounded_run_escalates_to_sigkill_for_a_term_ignoring_child(tmp_path):
    import time as real_time
    pidfile = tmp_path / "pid"
    script = f'trap "" TERM; echo $$ > {pidfile}; while :; do sleep 1; done'
    started = real_time.monotonic()
    with pytest.raises(ActionTimeout):
        HostActions._run_bounded(["/bin/sh", "-c", script], None, 0.5, "stubborn", term_grace=0.5, reap_wait=5.0)
    assert real_time.monotonic() - started < 15
    real_time.sleep(0.2)
    assert not _alive(int(pidfile.read_text()))


def test_bounded_run_unreapable_child_raises_unreapable(monkeypatch):
    import subprocess as sp
    from debian_install_v2 import actions as actions_module

    signals: list[int] = []

    class Stuck:
        pid = 424242
        stdout = stdin = None

        def communicate(self, input=None, timeout=None):
            raise sp.TimeoutExpired("x", timeout)

        def wait(self, timeout=None):
            raise sp.TimeoutExpired("x", timeout)  # D-state: never reaped

    monkeypatch.setattr(actions_module.subprocess, "Popen", lambda *a, **k: Stuck())
    monkeypatch.setattr(actions_module.os, "killpg", lambda pid, sig: signals.append(int(sig)))
    with pytest.raises(ActionUnreapable, match="could not be reaped"):
        HostActions._run_bounded(["/bin/true"], None, 0.1, "stuck", term_grace=0.01, reap_wait=0.01)
    assert signals == [15, 9]  # SIGTERM to the group, then SIGKILL; no hang


def test_bounded_run_uses_a_new_session(monkeypatch):
    import subprocess as sp
    from debian_install_v2 import actions as actions_module
    seen = {}

    class Done:
        returncode = 0

        def communicate(self, input=None, timeout=None):
            return "ok", None

    def fake_popen(*a, **k):
        seen.update(k)
        return Done()

    monkeypatch.setattr(actions_module.subprocess, "Popen", fake_popen)
    assert HostActions._run_bounded(["/bin/true"], None, 5, "x") == (0, "ok")
    assert seen["start_new_session"] is True


# --- blocker 2: Case B resume wedge ----------------------------------------------------

def put_swap_partitions(installer: Installer, disk: SimDisk, count: int | None = None, shift: int = 0) -> list[tuple[int, int]]:
    partitions, _ = installer._plan_swap_partitions()
    for index, (start, size) in enumerate(partitions[:count], start=4):
        disk.table[index] = {"start": str(start + shift), "size": str(size),
                             "type": "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F", "uuid": f"S{index}"}
        disk.kernel.add(index)
    return partitions


def test_resume_with_swap_partitions_already_written_does_not_wedge(tmp_path):
    """Reviewer repro: a re-run found 'fresh-install contract violated: existing partition 4 follows root'."""
    installer, disk = make(tmp_path)
    installer.config = replace(installer.config, run_io_benchmark=False)
    put_swap_partitions(installer, disk)
    installer._stage2()  # must not raise
    assert disk.tags().count("write") == 0  # nothing rewritten
    assert set(disk.table) == {3, *range(4, 12)}
    assert step(installer, "partitions")["status"] == "success"
    assert StateStore(installer.config.state_dir).load()["phase"] == "done"


def test_resume_after_benchmark_cleanup_stop_continues_to_swap_activation(tmp_path):
    """Case B shape: swap already on disk, benchmark step terminal; the second stage2 pass completes."""
    installer, disk = make(tmp_path, disk_gib=100)
    put_swap_partitions(installer, disk)
    installer._mark_step("io_benchmark", "warned", "earlier")
    installer._stage2()
    assert disk.tags().count("write") == 0 and "mkfs.ext4" not in disk.tags()
    assert StateStore(installer.config.state_dir).load()["phase"] == "done"


def test_partial_swap_presence_still_refuses(tmp_path):
    installer, disk = make(tmp_path)
    installer.config = replace(installer.config, run_io_benchmark=False)
    put_swap_partitions(installer, disk, count=3)
    with pytest.raises(InstallerError, match="existing partition"):
        installer._stage2()


def test_mismatching_swap_presence_still_refuses(tmp_path):
    installer, disk = make(tmp_path)
    installer.config = replace(installer.config, run_io_benchmark=False)
    put_swap_partitions(installer, disk, shift=2048)
    with pytest.raises(InstallerError, match="existing partition"):
        installer._stage2()
    wrong_type = make(tmp_path / "b")
    installer2, disk2 = wrong_type
    installer2.config = replace(installer2.config, run_io_benchmark=False)
    put_swap_partitions(installer2, disk2)
    disk2.table[6]["type"] = LINUX.upper()
    with pytest.raises(InstallerError, match="existing partition"):
        installer2._stage2()


def test_swap_written_flag_decides_the_requirement(tmp_path):
    """Discriminating: with swap_written=True the requirement is the existing partitions' END."""
    installer, disk = make(tmp_path, disk_gib=60)
    partitions, _ = installer._plan_swap_partitions()
    # swap on disk sits LATER than the plan would put it (a hook layout): it leaves < 2 GiB tail
    tail_shift = 60 * SECTORS_PER_GIB - 2048 - (partitions[-1][0] + partitions[-1][1]) - SECTORS_PER_GIB
    put_swap_partitions(installer, disk, shift=tail_shift)
    installer._run_io_benchmark(swap_written=True)
    assert step(installer)["status"] == "skipped" and disk.tags().count("write") == 0
    # the same table, swap_written=False: the plan-derived requirement leaves room, so it runs
    installer2, disk2 = make(tmp_path / "b", disk_gib=60)
    installer2._mark_step("io_benchmark", "started", "")
    installer2._run_io_benchmark(swap_written=False)
    assert step(installer2)["status"] == "success"


# --- blocker 3: escaping ---------------------------------------------------------------

def test_failure_notification_is_html_escaped(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["iocost_coef_gen.py"] = "fio: bad <thing> & more"
    sent: list[tuple[str, dict]] = []
    installer._notify = lambda message, **kw: sent.append((message, kw))  # type: ignore[method-assign]
    installer._run_io_benchmark(swap_written=False)
    message, kw = sent[-1]
    assert "&lt;thing&gt;" in message and "<thing>" not in message and "<code>" in message
    assert "<thing>" in kw["event"]  # the Mattermost text stays plain


# --- required-before-live: cleanup robustness ------------------------------------------

def test_cleanup_settles_before_restore_and_restore_uses_no_reread(tmp_path):
    installer, disk = make(tmp_path)
    installer._run_io_benchmark(swap_written=False)
    argvs = [a.argv for a in disk.planned]
    restore = next(i for i, a in enumerate(disk.planned) if a.description.startswith("delete throwaway"))
    assert argvs[restore - 1] == ("/usr/bin/udevadm", "settle")
    assert "--no-reread" in argvs[restore]


def test_cleanup_partx_retries_then_succeeds(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    sleeps = settle_sleeps(monkeypatch)
    disk.flaky[("partx", "-d")] = 2
    disk.flaky[("partx", "-u")] = 1
    installer._run_io_benchmark(swap_written=False)
    assert step(installer)["status"] == "success" and bench_lines(disk) == []
    assert disk.tags().count("partx-d") == 3 and disk.tags().count("partx-u") == 2
    assert sleeps.count(1.0) == 3


def test_cleanup_partx_gives_up_after_five_attempts(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    settle_sleeps(monkeypatch)
    disk.flaky[("partx", "-d")] = 99
    with pytest.raises(InstallerError, match="cleanup failed"):
        installer._stage2()
    assert disk.tags().count("partx-d") == 5
    assert disk.tags().count("write") == 1  # no swap shape


def test_stale_signature_zeroed_before_partition_is_deleted(tmp_path):
    installer, disk = make(tmp_path)
    installer._run_io_benchmark(swap_written=False)
    tags = disk.tags()
    assert tags.index("umount") < tags.index("dd") < tags.index("restore")
    dd = next(a.argv for a in disk.planned if Path(a.argv[0]).name == "dd")
    assert dd[1:] == ("if=/dev/zero", "of=/dev/vda12", "bs=1M", "count=1", "conv=fsync")


def test_leftover_without_a_device_node_skips_the_zeroing(tmp_path):
    installer, disk = make(tmp_path)
    disk.table[12] = {"start": str(80 * SECTORS_PER_GIB), "size": str(10 * SECTORS_PER_GIB), "type": LINUX.upper(),
                      "uuid": "LEFT", "name": "vbpub-iobench"}  # in the table, never registered with the kernel
    installer._mark_step("io_benchmark", "started", "")
    installer._run_io_benchmark(swap_written=False)
    assert step(installer)["status"] == "success"
    assert disk.tags().count("dd") == 1  # only the benchmark's own, none for the node-less leftover


# --- readback strictness ------------------------------------------------------------------

def _set_uuid(disk: SimDisk) -> None:
    disk.table[3]["uuid"] = "CHANGED-UUID"


def _set_name(disk: SimDisk) -> None:
    disk.table[3]["name"] = "renamed"


def _set_label(disk: SimDisk) -> None:
    disk.label_id = "OTHER-LABEL"


@pytest.mark.parametrize("mutator", [_set_uuid, _set_name, _set_label])
def test_cleanup_readback_compares_uuid_name_and_label_id(tmp_path, mutator):
    installer, disk = make(tmp_path)
    disk.restore_mutator = mutator
    with pytest.raises(InstallerError, match="cleanup failed.*identity"):
        installer._stage2()
    assert disk.tags().count("write") == 1 and step(installer)["status"] == "failed"


# --- required tests that close surviving mutants -----------------------------------------------

def test_apt_failure_is_advisory(tmp_path):
    installer, disk = make(tmp_path)
    real_run = disk.run

    def run(argv, *a, **k):
        if Path(argv[0]).name == "apt-get" and "fio" in argv:
            raise ActionError("E: unable to locate package fio")
        return real_run(argv, *a, **k)

    disk.run = run  # type: ignore[method-assign]
    installer._stage2()  # must NOT raise
    state = step(installer)
    assert state["status"] == "warned" and "fio" in state["detail"]
    assert disk.tags().count("write") == 1 and "mkfs.ext4" not in disk.tags()  # no partition was created
    assert StateStore(installer.config.state_dir).load()["phase"] == "done"


def test_partial_write_failure_still_triggers_cleanup(tmp_path):
    """The forward sfdisk write lands and THEN errors: `created` must already be set."""
    installer, disk = make(tmp_path)
    disk.partial_write_fails = True
    installer._stage2()  # advisory failure, then swap applied
    state = step(installer)
    assert state["status"] == "warned" and "failed midway" in state["detail"]
    assert bench_lines(disk) == [] and 12 not in disk.kernel
    assert set(disk.table) == {3, *range(4, 12)}
    assert "restore" in disk.tags()


def test_success_notification_is_sent_with_the_values(tmp_path):
    installer, disk = make(tmp_path)
    sent: list[tuple[str, dict]] = []
    installer._notify = lambda message, **kw: sent.append((message, kw))  # type: ignore[method-assign]
    installer._run_io_benchmark(swap_written=False)
    assert len(sent) == 1
    message, kw = sent[0]
    assert "rbps 1177 MiB/s" in message and "wbps 942 MiB/s" in message and "rrandiops 9000" in message
    assert kw["status"] == "ok" and "wrandiops 7000" in kw["event"]

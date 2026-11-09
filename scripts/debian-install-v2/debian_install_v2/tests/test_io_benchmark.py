"""Install-time iobench uses only the planned, raw iobench-raw partition."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from debian_install_v2 import installer as installer_module
from debian_install_v2.actions import ActionError, HostActions, PlannedAction
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.layout_policy import IOBENCH_PARTLABEL, ZFS_TYPE_GUID
from debian_install_v2.state import StateStore

GIB = 1024 ** 3
SECTORS_PER_GIB = GIB // 512
ROOT_START = 2_500_608
ROOT_SIZE = 8 * SECTORS_PER_GIB
LINUX = "0fc63daf-8483-4772-8e79-3d69d8477de4"
PARTUUID = "12345678-1234-1234-1234-123456789012"
RESULTS = {"rbps": 1234567890, "rseqiops": 11000, "rrandiops": 9000,
           "wbps": 987654321, "wseqiops": 8000, "wrandiops": 7000}


class SimDisk(HostActions):
    """Small HostActions simulator; it never opens a block device or executes iobench."""

    def __init__(self, disk_gib: int = 100) -> None:
        super().__init__(dry_run=False)
        self.disk_bytes = disk_gib * GIB
        self.table: dict[int, dict[str, str]] = {
            3: {"start": str(ROOT_START), "size": str(ROOT_SIZE), "type": LINUX.upper(),
                "uuid": "ROOT-UUID", "name": "root"}
        }
        self.fail: dict[str, Exception | str] = {}
        self.timeouts: list[tuple[str, float | None]] = []
        self.fstab_content = ""
        self.files: dict[str, bytes] = {}
        self.findmnt_output = "/dev/vda3 / ext4\n"
        self.swap_output = ""

    def dump(self) -> str:
        lines = ["label: gpt", "device: /dev/vda", "unit: sectors", "sector-size: 512", ""]
        for number, attrs in sorted(self.table.items()):
            name = f', name="{attrs["name"]}"' if attrs.get("name") else ""
            lines.append(
                f"/dev/vda{number} : start={attrs['start']}, size={attrs['size']}, "
                f"type={attrs['type']}, uuid={attrs.get('uuid', 'SIM-UUID')}{name}"
            )
        return "\n".join(lines) + "\n"

    def load_entries(self, entries: dict[int, dict[str, str]]) -> None:
        self.table = {
            number: {**entry, "uuid": entry.get("uuid", f"SIM-UUID-{number}")}
            for number, entry in entries.items()
        }

    def run(self, argv, description="", dangerous=False, input=None, timeout=None):
        self._validate(list(argv))
        self.timeouts.append((Path(argv[0]).name, timeout))
        self.planned.append(PlannedAction(tuple(argv), description or " ".join(argv), dangerous))
        name = Path(argv[0]).name
        if name in self.fail:
            error = self.fail[name]
            if isinstance(error, Exception):
                raise error
            raise ActionError(error)
        if name == "blockdev":
            return str(self.disk_bytes)
        if name == "sfdisk" and "--dump" in argv:
            return self.dump()
        if name == "findmnt" and "SOURCE,TARGET,FSTYPE" in argv:
            return self.findmnt_output
        if name == "swapon" and "--show=NAME,TYPE,SIZE,PRIO" in argv:
            return self.swap_output
        if name == "blkid" and "PARTUUID" in argv:
            return PARTUUID
        if name == "iobench-install-runner":
            state_dir = Path(argv[argv.index("--state-dir") + 1])
            output = state_dir.parent / "io-benchmark.json"
            output.write_text(json.dumps({"results": RESULTS}), encoding="utf-8")
        return ""

    def write_file(self, path: str, content: str, mode: int = 0o644) -> None:
        self.files[path] = content.encode("utf-8")
        self.planned.append(PlannedAction(("/usr/bin/tee", path), f"write {path}", True))
        if path == "/etc/fstab":
            self.fstab_content = content
        # This simulator records all file writes. In particular, a stage-two
        # test must never send a write to the cockpit's /etc or /var paths.


def make(tmp_path: Path, *, disk_gib: int = 100, **overrides) -> tuple[Installer, SimDisk]:
    partitioning = overrides.pop("partitioning", {})
    values = dict(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        telegram_bot_token="",
        telegram_chat_id="",
        auto_reboot_after_stage1=False,
        swap_disk_total_gb=32,
        swap_partition_count=8,
        preserve_root_size_gb=2,
        partitioning=partitioning,
        run_io_benchmark=True,
        run_ksm=False,
        run_oomd_config=False,
        run_fstrim=False,
        run_auto_reboot=False,
    )
    values.update(overrides)
    config = Config(**values)
    disk = SimDisk(disk_gib)
    installer = Installer(config, disk, inspect_host=False)
    installer.root_disk = "vda"
    installer.root_partition_path = "/dev/vda3"
    installer.root_number = 3
    swaps, target_root = installer._plan_swap_partitions()
    planned = installer._parse_partition_entries(installer._write_sfdisk_plan(swaps, target_root))
    disk.load_entries(planned)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer, disk


def step(installer: Installer, name: str = "io_benchmark") -> dict:
    return StateStore(installer.config.state_dir).load()["steps"].get(name, {})


def test_install_benchmark_wipes_and_runs_against_the_planned_raw_partition(tmp_path):
    installer, disk = make(tmp_path)
    raw_number = next(number for number, entry in disk.table.items() if entry.get("name") == IOBENCH_PARTLABEL)
    raw_device = f"/dev/vda{raw_number}"
    installer._run_io_benchmark()

    actions = disk.planned
    wipe = next(i for i, action in enumerate(actions) if Path(action.argv[0]).name == "wipefs")
    run = next(i for i, action in enumerate(actions) if Path(action.argv[0]).name == "iobench-install-runner")
    assert wipe < run
    assert actions[wipe].argv == ("/usr/sbin/wipefs", "-a", raw_device)
    assert actions[run].argv[2] == raw_device
    assert actions[run].argv[4] == str(8 * GIB)
    assert not any("--force" in action.argv or "-N" in action.argv for action in actions)
    assert disk.table[raw_number]["name"] == IOBENCH_PARTLABEL
    assert not any(
        Path(action.argv[0]).name in {"mkfs.ext4", "mkfs.btrfs", "mkfs.xfs", "mount", "umount"}
        and raw_device in action.argv
        for action in actions
    )
    assert step(installer)["status"] == "success"
    assert ("iobench-install-runner", installer_module.IOBENCH_OUTER_TIMEOUT_S) in disk.timeouts


def test_benchmark_off_does_not_wipe_or_run_the_raw_partition(tmp_path):
    installer, disk = make(tmp_path, run_io_benchmark=False)
    installer._run_io_benchmark()
    assert not any(Path(action.argv[0]).name in {"wipefs", "iobench-install-runner"} for action in disk.planned)
    assert step(installer) == {}


def test_benchmark_failure_is_advisory_and_keeps_the_reserved_partition(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["iobench-install-runner"] = "simulated iobench refusal"
    sent: list[str] = []
    installer._notify = lambda message, **kwargs: sent.append(kwargs.get("event", message))  # type: ignore[method-assign]
    raw_number = next(number for number, entry in disk.table.items() if entry.get("name") == IOBENCH_PARTLABEL)

    installer._run_io_benchmark()

    assert step(installer)["status"] == "warned"
    assert disk.table[raw_number]["name"] == IOBENCH_PARTLABEL
    assert len([item for item in sent if "iobench failed (advisory)" in item.lower()]) == 1


def test_failed_benchmark_keeps_full_stderr_and_names_the_log(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["iobench-install-runner"] = ActionError(
        "action failed (1): run iobench veryquick on /dev/vda5\n"
        "Refusal: fio_option_unsupported\n"
        "detail: the installed fio does not document 'exclusive_open'"
    )
    sent: list[str] = []
    installer._notify = lambda message, **kwargs: sent.append(kwargs.get("event", message))  # type: ignore[method-assign]

    installer._run_io_benchmark()

    log = Path(installer.config.state_dir) / "io-benchmark.log"
    assert disk.files[str(log)].decode("utf-8") == (
        "action failed (1): run iobench veryquick on /dev/vda5\n"
        "Refusal: fio_option_unsupported\n"
        "detail: the installed fio does not document 'exclusive_open'\n"
    )
    detail = step(installer)["detail"]
    assert str(log) in detail
    assert sent == [detail]


def test_benchmark_refuses_when_the_planned_partlabel_geometry_disagrees(tmp_path):
    installer, disk = make(tmp_path)
    raw_number = next(number for number, entry in disk.table.items() if entry.get("name") == IOBENCH_PARTLABEL)
    disk.table[raw_number]["name"] = "FOREIGN"
    try:
        installer._raw_iobench_partition()
    except Exception as exc:
        assert "PARTLABEL=iobench-raw" in str(exc)
    else:
        raise AssertionError("raw benchmark partition with a foreign PARTLABEL was accepted")


def test_benchmark_refuses_when_raw_partition_has_the_wrong_gpt_type(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    planned = installer._plan_swap_partitions()
    monkeypatch.setattr(installer, "_plan_swap_partitions", lambda: planned)
    raw_number = next(number for number, entry in disk.table.items() if entry.get("name") == IOBENCH_PARTLABEL)
    disk.table[raw_number]["type"] = ZFS_TYPE_GUID

    with pytest.raises(InstallerError, match="does not match PARTLABEL=iobench-raw"):
        installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "wipefs" for action in disk.planned)


def test_benchmark_checks_raw_partlabel_at_the_planned_partition_number(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    planned = installer._plan_swap_partitions()
    monkeypatch.setattr(installer, "_plan_swap_partitions", lambda: planned)
    raw_number = next(number for number, entry in disk.table.items() if entry.get("name") == IOBENCH_PARTLABEL)
    planned_entry = dict(disk.table[raw_number])
    disk.table[raw_number]["name"] = "FOREIGN"
    disk.table[raw_number + 20] = {**planned_entry, "name": IOBENCH_PARTLABEL}

    with pytest.raises(InstallerError, match="does not match PARTLABEL=iobench-raw"):
        installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "wipefs" for action in disk.planned)


@pytest.mark.parametrize("geometry_field", ["start", "size"])
def test_benchmark_geometry_mismatch_stops_before_wipefs(tmp_path, geometry_field):
    installer, disk = make(tmp_path)
    raw_number = next(number for number, entry in disk.table.items() if entry.get("name") == IOBENCH_PARTLABEL)
    disk.table[raw_number][geometry_field] = str(int(disk.table[raw_number][geometry_field]) + 2048)

    with pytest.raises(InstallerError, match="does not match PARTLABEL=iobench-raw"):
        installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "wipefs" for action in disk.planned)


def test_duplicate_iobench_partlabels_stop_before_wipefs(tmp_path):
    installer, disk = make(tmp_path)
    raw_number = next(number for number, entry in disk.table.items() if entry.get("name") == IOBENCH_PARTLABEL)
    raw = disk.table[raw_number]
    disk.table[raw_number + 20] = {
        "start": str(int(raw["start"]) + int(raw["size"]) + 2048),
        "size": "2048", "type": LINUX.upper(), "name": IOBENCH_PARTLABEL,
    }

    with pytest.raises(InstallerError, match="expected exactly one disk partition with PARTLABEL=iobench-raw"):
        installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "wipefs" for action in disk.planned)


@pytest.mark.parametrize("guard", ["mounted", "active-swap"])
def test_mounted_or_active_swap_raw_partition_is_never_wiped(tmp_path, guard):
    installer, disk = make(tmp_path)
    raw_number = next(number for number, entry in disk.table.items() if entry.get("name") == IOBENCH_PARTLABEL)
    raw_device = f"/dev/vda{raw_number}"
    if guard == "mounted":
        disk.findmnt_output += f"{raw_device} /mnt/raw ext4\n"
    else:
        disk.swap_output = f"{raw_device} partition 8G -2\n"

    installer._run_io_benchmark()

    assert step(installer)["status"] == "warned"
    assert not any(Path(action.argv[0]).name == "wipefs" for action in disk.planned)


def test_stage_two_benchmark_result_is_consumed_by_iocost_after_the_table_write(tmp_path, monkeypatch):
    installer, disk = make(
        tmp_path,
        iocost_enabled=True,
        partitioning={"root": 10, "data": {"kind": "partitions", "volumes": [
            {"size": "*", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
        ]}},
    )
    disk.mkdir = lambda path: disk.planned.append(PlannedAction(("/usr/bin/mkdir", "-p", path), "fake mkdir"))
    disk.exists = lambda _path: False
    events: list[str] = []
    monkeypatch.setattr(installer, "_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(installer, "_verify_and_apply_root_shrink", lambda: False)
    monkeypatch.setattr(installer, "_swap_partitions_already_planned_in_table", lambda: False)
    monkeypatch.setattr(installer, "_disable_active_swap", lambda: None)
    monkeypatch.setattr(installer, "_configure_zswap", lambda: None)
    monkeypatch.setattr(installer, "_configure_cgroup2_flags", lambda: None)
    monkeypatch.setattr(installer, "_configure_ksm", lambda: None)
    monkeypatch.setattr(installer, "_configure_oomd", lambda: None)
    monkeypatch.setattr(installer, "_configure_fstrim", lambda: None)
    monkeypatch.setattr(installer, "_configure_auto_reboot", lambda: None)
    monkeypatch.setattr(installer, "_apply_known_swap_shape", lambda: events.append("table-write"))
    monkeypatch.setattr(installer, "_ensure_disk_transaction_manifest", lambda: None)
    run_benchmark = installer._run_io_benchmark
    configure_iocost = installer._configure_iocost
    monkeypatch.setattr(installer, "_run_io_benchmark", lambda: (events.append("benchmark"), run_benchmark())[1])
    monkeypatch.setattr(installer, "_configure_iocost", lambda: (events.append("iocost"), configure_iocost())[1])
    monkeypatch.setattr(installer, "_grow_root_to_plan", lambda: events.append("root-grow"))
    monkeypatch.setattr(installer, "_activate_swap_partitions", lambda: events.append("swap-active"))
    monkeypatch.setattr(installer, "_configure_data_area", lambda: events.append("data-area-format"))
    monkeypatch.setattr(installer, "_health_gate_swap_devices", lambda: None)
    monkeypatch.setattr(installer, "_run_cpu_benchmark", lambda: None)
    monkeypatch.setattr(installer, "_release_apt_timers", lambda: None)
    monkeypatch.setattr(installer, "_cleanup_cloud_init_cache", lambda: None)

    installer._stage2()

    assert events.index("table-write") < events.index("benchmark") < events.index("iocost")
    assert events.index("iocost") < events.index("root-grow") < events.index("swap-active")
    assert events.index("benchmark") < events.index("data-area-format")
    iocost = step(installer, "iocost")
    assert iocost["status"] == "success"
    assert f"rbps={RESULTS['rbps']}" in iocost["detail"]
    assert f"wbps={RESULTS['wbps']}" in iocost["detail"]


def test_removed_temporary_tail_and_generator_commands_are_not_allowed():
    assert "extra_tail_sectors" not in installer_module.Installer._plan_swap_partitions.__code__.co_varnames
    assert not hasattr(installer_module.Installer, "_iocost_tool_path")
    assert not hasattr(installer_module.Installer, "_verify_iocost_generator")


def test_installer_iobench_runner_is_directly_executable():
    runner = Path(__file__).resolve().parents[2] / "tools" / "iobench-install-runner"
    assert runner.stat().st_mode & 0o111

"""Install-time iobench temporarily borrows the planned tail swap span."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from debian_install_v2 import installer as installer_module
from debian_install_v2.actions import ActionError, ActionUnreapable, HostActions, PlannedAction
from debian_install_v2.config import (
    IO_BENCHMARK_OUTER_TIMEOUT_S,
    Config,
)
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.layout_policy import LINUX_FS_TYPE_GUID, SWAP_SPAN_PARTLABEL, ZFS_TYPE_GUID
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
        self.signatures: dict[str, str] = {}
        self.partition_tables: dict[str, str] = {}
        self.extent_probe_output = ""
        self.extent_probe_error: Exception | None = None
        self.partuuid_output = PARTUUID
        self.events: list[str] = []
        self.table_writes: list[dict[int, dict[str, str]]] = []
        self.crash_after_table_write: int | None = None
        self.runner_signature_snapshots: list[tuple[str, str]] = []
        # What the fake runner records; a test may drop or invalidate a key.
        self.iobench_results: dict[str, object] | None = None

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

    def _load_sfdisk_plan(self, text: str) -> None:
        previous = self.table
        entries: dict[int, dict[str, str]] = {}
        for line in text.splitlines():
            match = re.match(r"/dev/vda(\d+)\s*:", line)
            if not match:
                continue
            number = int(match.group(1))
            attrs = {
                key: value.strip('"')
                for key, value in installer_module.inuse_partition_editor.ATTR_RE.findall(line.partition(":")[2])
            }
            attrs["uuid"] = previous.get(number, {}).get("uuid", f"SIM-UUID-{number}")
            entries[number] = attrs
        self.table = entries
        self.table_writes.append({number: dict(entry) for number, entry in entries.items()})

    def run(self, argv, description="", dangerous=False, input=None, timeout=None):
        self._validate(list(argv))
        self.timeouts.append((Path(argv[0]).name, timeout))
        self.planned.append(PlannedAction(tuple(argv), description or " ".join(argv), dangerous))
        name = Path(argv[0]).name
        self.events.append(name)
        if self.dry_run:
            if name == "sfdisk" and "--dump" in argv:
                return self.dump()
            return ""
        if name in self.fail:
            error = self.fail[name]
            if isinstance(error, Exception):
                raise error
            raise ActionError(error)
        if name == "blockdev":
            return str(self.disk_bytes)
        if name == "sfdisk" and "--dump" in argv:
            return self.dump()
        if name == "sfdisk" and "--force" in argv:
            self._load_sfdisk_plan(input or "")
            return ""
        if name == "udevadm" and self.crash_after_table_write == len(self.table_writes):
            self.crash_after_table_write = None
            raise SystemExit("simulated process crash after partition-table write")
        if name == "findmnt" and "SOURCE,TARGET,FSTYPE" in argv:
            return self.findmnt_output
        if name == "swapon" and "--show=NAME,TYPE,SIZE,PRIO" in argv:
            return self.swap_output
        if name == "blkid" and "-O" in argv:
            if self.extent_probe_error is not None:
                raise self.extent_probe_error
            return self.extent_probe_output
        if name == "blkid" and "PARTUUID" in argv:
            return self.partuuid_output
        if name == "blkid" and "PTTYPE" in argv:
            return self.partition_tables.get(argv[-1], "")
        if name == "blkid" and "TYPE" in argv:
            return self.signatures.get(argv[-1], "")
        if name == "wipefs":
            self.signatures.pop(argv[-1], None)
            return ""
        if name == "iobench-install-runner":
            target = argv[argv.index("--target") + 1]
            signature = self.signatures.get(target, "")
            self.runner_signature_snapshots.append((target, signature))
            if signature:
                raise ActionError(f"fake runner refuses target with signature {signature}")
            # The runner records exactly what its calibration produced; a test
            # may seed fewer coefficients than the six the step needs.
            results = RESULTS if self.iobench_results is None else self.iobench_results
            output = Path(argv[argv.index("--output") + 1])
            output.write_text(json.dumps({"results": results}), encoding="utf-8")
        return ""

    def exists(self, path: str) -> bool:
        return path.startswith("/dev/vda")

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
    installer._preflight_disk_transaction = lambda: {"holders": [], "mounts": []}  # type: ignore[method-assign]
    swaps, target_root = installer._plan_swap_partitions()
    planned = installer._parse_partition_entries(installer._write_sfdisk_plan(swaps, target_root))
    disk.load_entries(planned)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer, disk


def step(installer: Installer, name: str = "io_benchmark") -> dict:
    return StateStore(installer.config.state_dir).load()["steps"].get(name, {})


def make_span(installer: Installer, disk: SimDisk) -> int:
    partitions, target_root = installer._plan_swap_partitions()
    text = installer._write_sfdisk_plan(partitions, target_root, tail_swap_span=True)
    entries = installer._parse_partition_entries(text)
    disk.load_entries(entries)
    return next(number for number, entry in entries.items() if entry.get("name") == SWAP_SPAN_PARTLABEL)


def install_pre_root_swap(installer: Installer, disk: SimDisk, *, size_gib: int = 1) -> None:
    disk.load_entries({
        2: {"start": "2048", "size": str(size_gib * SECTORS_PER_GIB),
            "type": installer_module.SWAP_TYPE_GUID, "name": ""},
        3: {"start": str(ROOT_START), "size": str(ROOT_SIZE), "type": LINUX.upper(), "name": "root"},
    })
    partitions, target_root = installer._plan_swap_partitions()
    text = installer._write_sfdisk_plan(partitions, target_root)
    disk.load_entries(installer._parse_partition_entries(text))


def set_benchmark_step(installer: Installer, status: str, *, span_identity: bool = False) -> None:
    detail = "previous terminal benchmark result"
    if span_identity:
        _number, start, size, _device, partuuid = installer._swap_span_partition()
        detail = f"PARTUUID={partuuid} start={start} size={size} sectors profile=quick"
    installer._mark_step("io_benchmark", status, detail)


def test_install_benchmark_uses_the_exact_temporary_tail_swap_span(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    span_device = f"/dev/vda{span_number}"
    installer._run_io_benchmark()

    actions = disk.planned
    run = next(i for i, action in enumerate(actions) if Path(action.argv[0]).name == "iobench-install-runner")
    assert actions[run].argv[4] == span_device
    assert actions[run].argv[6] == str(32 * GIB)
    assert not any("--force" in action.argv or "-N" in action.argv for action in actions)
    assert disk.table[span_number]["name"] == SWAP_SPAN_PARTLABEL
    assert disk.table[span_number]["type"].lower() == LINUX_FS_TYPE_GUID.lower()
    assert not any(
        Path(action.argv[0]).name in {"mkfs.ext4", "mkfs.btrfs", "mkfs.xfs", "mount", "umount"}
        and span_device in action.argv
        for action in actions
    )
    assert step(installer)["status"] == "success"
    assert f"PARTUUID={PARTUUID}" in step(installer)["detail"]
    assert "start=" in step(installer)["detail"] and "size=" in step(installer)["detail"]
    assert ("iobench-install-runner", IO_BENCHMARK_OUTER_TIMEOUT_S["quick"]) in disk.timeouts


def assert_extent_recovery(message: str, disk: SimDisk) -> None:
    """The recovery text names the probed extent and erases only through a loop device bound to it."""
    probe = next(
        action.argv for action in disk.planned
        if Path(action.argv[0]).name == "blkid" and "-O" in action.argv
    )
    offset, size = probe[probe.index("-O") + 1], probe[probe.index("-S") + 1]
    assert f"losetup --find --show --offset {offset} --sizelimit {size} /dev/vda`" in message
    assert "never run `wipefs -a` on /dev/vda:" in message
    assert re.findall(r"`wipefs -a ([^`\s]+)`", message) == ["/dev/loopN"]
    assert "`losetup -d /dev/loopN`" in message
    assert not re.search(r"/dev/vda\d", message)


@pytest.mark.parametrize(
    ("probe_output", "probe_error"),
    [
        ("", ActionError("action failed (2): blank extent")),
        ("TYPE=swap\n", None),
    ],
)
def test_fresh_span_probe_accepts_blank_or_swap_extent_before_table_write(
    tmp_path, probe_output, probe_error,
):
    installer, disk = make(tmp_path)
    disk.load_entries({3: disk.table[3]})
    disk.extent_probe_output = probe_output
    disk.extent_probe_error = probe_error

    installer._prepare_swap_span_benchmark()

    probe = next(
        i for i, action in enumerate(disk.planned)
        if Path(action.argv[0]).name == "blkid" and "-O" in action.argv
    )
    write = next(
        i for i, action in enumerate(disk.planned)
        if Path(action.argv[0]).name == "sfdisk" and "--force" in action.argv
    )
    assert probe < write


@pytest.mark.parametrize("signature", ["TYPE=crypto_LUKS\n", "PTTYPE=gpt\n"])
def test_foreign_extent_signature_is_refused_before_first_table_write(
    tmp_path, signature,
):
    installer, disk = make(tmp_path)
    disk.load_entries({3: disk.table[3]})
    disk.extent_probe_output = signature

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_foreign_signature") as raised:
        installer._prepare_swap_span_benchmark()

    assert_extent_recovery(str(raised.value), disk)
    assert not disk.table_writes
    assert not any(
        Path(action.argv[0]).name == "sfdisk" and "--force" in action.argv
        for action in disk.planned
    )


@pytest.mark.parametrize("failure", ["1): device read error", "8): ambivalent signatures"])
def test_extent_probe_failure_refuses_before_table_write(tmp_path, failure):
    installer, disk = make(tmp_path)
    disk.load_entries({3: disk.table[3]})
    disk.extent_probe_error = ActionError(f"action failed ({failure}")

    with pytest.raises(InstallerError, match="cannot probe planned swap-span extent") as raised:
        installer._prepare_swap_span_benchmark()

    assert_extent_recovery(str(raised.value), disk)
    assert not disk.table_writes


def test_extent_probe_is_skipped_in_dry_run(tmp_path):
    installer, disk = make(tmp_path)
    installer.actions.dry_run = True

    installer._probe_swap_span_extent(2048, 4096)

    assert not any(
        Path(action.argv[0]).name == "blkid" and "-O" in action.argv
        for action in disk.planned
    )


def test_fresh_benchmark_can_replace_an_inactive_unusual_tail_swap_layout(tmp_path):
    installer, disk = make(tmp_path)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    _number, start, size = installer._planned_tail_swap_span(partitions, before_root)
    disk.load_entries({
        3: dict(disk.table[3]),
        30: {
            "start": str(start), "size": str(size),
            "type": installer_module.SWAP_TYPE_GUID, "name": "provider-swap",
        },
    })

    installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 2
    assert not any(number == 30 for number in disk.table)
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())


def test_resume_does_not_replace_an_unplanned_tail_swap_layout(tmp_path):
    installer, disk = make(tmp_path)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    _number, start, size = installer._planned_tail_swap_span(partitions, before_root)
    disk.load_entries({
        3: dict(disk.table[3]),
        30: {
            "start": str(start), "size": str(size),
            "type": installer_module.SWAP_TYPE_GUID, "name": "provider-swap",
        },
    })
    installer._mark_step("io_benchmark", "started", "resuming")

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


@pytest.mark.parametrize("guard", ["mounted", "foreign-signature"])
def test_unusual_tail_swap_in_use_or_foreign_signed_is_not_replaced(tmp_path, guard):
    installer, disk = make(tmp_path)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    _number, start, size = installer._planned_tail_swap_span(partitions, before_root)
    disk.load_entries({
        3: dict(disk.table[3]),
        30: {
            "start": str(start), "size": str(size),
            "type": installer_module.SWAP_TYPE_GUID, "name": "provider-swap",
        },
    })
    if guard == "mounted":
        disk.findmnt_output += "/dev/vda30 /mnt/provider ext4\n"
    else:
        disk.signatures["/dev/vda30"] = "ext4"

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_(partition_in_use|foreign_signature)"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


def test_benchmark_disabled_does_not_rewrite_for_gpt_names_alone(tmp_path):
    installer, disk = make(tmp_path, run_io_benchmark=False)
    for number, entry in disk.table.items():
        if installer._is_swap_partition(entry):
            entry["name"] = f"provider-name-{number}"

    installer._apply_known_swap_shape()

    assert not disk.table_writes


def test_final_benchmark_step_is_not_run_twice(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)
    set_benchmark_step(installer, "success", span_identity=True)

    installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned)


def test_schema_one_ext4_benchmark_refusal_reaches_root_aware_validation(tmp_path):
    from debian_install_v2.installer import HostIdentityRefusal

    valid, _disk = make(tmp_path / "valid", schema_version=1, run_io_benchmark=True,
                        swap_policy="on", swap_partition_count=1)
    valid.root_fs = "ext4"
    valid._validate_root_config()

    invalid_config = Config(
        state_dir=str(tmp_path / "invalid" / "state"),
        log_dir=str(tmp_path / "invalid" / "logs"),
        schema_version=1,
        run_io_benchmark=True,
        swap_policy="off",
        swap_partition_count=1,
    )
    invalid = Installer(invalid_config, SimDisk(), inspect_host=False)
    invalid.root_disk = "vda"
    invalid.root_partition_path = "/dev/vda3"
    invalid.root_number = 3
    invalid.root_fs = "ext4"
    with pytest.raises(HostIdentityRefusal, match="io_benchmark_requires_tail_swap_span"):
        invalid._validate_root_config()


@pytest.mark.parametrize(
    ("from_root_end", "refusal"),
    [
        (2048, "io_benchmark_swap_span_state_mismatch: non-swap partition"),
        (-4096, "io_benchmark_swap_span_geometry_mismatch: partition.*overlap"),
    ],
)
def test_benchmark_plan_refuses_an_unknown_partition_after_root_without_writing(
    tmp_path, from_root_end, refusal,
):
    installer, disk = make(tmp_path)
    root = dict(disk.table[3])
    root_end = int(root["start"]) + int(root["size"])
    disk.load_entries({
        3: root,
        100: {"start": str(root_end + from_root_end), "size": "2048", "type": LINUX, "name": "foreign"},
    })

    with pytest.raises(InstallerError, match=refusal):
        installer._plan_swap_partitions()

    assert not disk.table_writes


def test_benchmark_off_does_not_wipe_or_run_a_span(tmp_path):
    installer, disk = make(tmp_path, run_io_benchmark=False)
    installer._run_io_benchmark()
    assert not any(Path(action.argv[0]).name in {"wipefs", "iobench-install-runner"} for action in disk.planned)
    assert step(installer) == {}


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ("sw,pri=10", "sw,pri=10,nofail,x-systemd.device-timeout=5s"),
        ("sw,nofail,pri=10", "sw,nofail,pri=10,x-systemd.device-timeout=5s"),
        ("sw,x-systemd.device-timeout=5s,pri=10", "sw,x-systemd.device-timeout=5s,pri=10,nofail"),
        ("sw,nofail,x-systemd.device-timeout=5s,pri=10", "sw,nofail,x-systemd.device-timeout=5s,pri=10"),
    ],
)
def test_swap_fstab_rows_always_include_both_boot_guards_once(options, expected):
    line = f"PARTUUID={PARTUUID} none swap {options} 0 0"

    assert Installer._swap_fstab_entry(line) == f"PARTUUID={PARTUUID} none swap {expected} 0 0"


@pytest.mark.parametrize("line", ["", "# swap note", "UUID=root / ext4 defaults 0 1", "UUID=short none swap"])
def test_swap_fstab_normalizer_leaves_non_rows_and_incomplete_rows_alone(line):
    assert Installer._swap_fstab_entry(line) == line


def test_persist_fstab_writes_guarded_swap_rows(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    monkeypatch.setattr(
        installer,
        "_read_fstab_lines",
        lambda: ["UUID=root / ext4 defaults 0 1", "PARTUUID=old none swap sw 0 0"],
    )

    installer._persist_fstab([f"PARTUUID={PARTUUID} none swap sw,pri=10 0 0"])

    assert disk.fstab_content.splitlines() == [
        "UUID=root / ext4 defaults 0 1",
        f"PARTUUID={PARTUUID} none swap sw,pri=10,nofail,x-systemd.device-timeout=5s 0 0",
    ]


def test_benchmark_failure_is_advisory_and_leaves_cleanup_to_stage_two(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    disk.fail["iobench-install-runner"] = "simulated iobench refusal"
    sent: list[str] = []
    installer._notify = lambda message, **kwargs: sent.append(kwargs.get("event", message))  # type: ignore[method-assign]

    installer._run_io_benchmark()

    assert step(installer)["status"] == "warned"
    assert disk.table[span_number]["name"] == SWAP_SPAN_PARTLABEL
    names = [Path(action.argv[0]).name for action in disk.planned]
    assert names.index("wipefs") < names.index("iobench-install-runner")
    assert "dd" not in names
    # The generic advisory line names the rung the install asked for.
    assert step(installer)["detail"].startswith(f"PARTLABEL={SWAP_SPAN_PARTLABEL} ")
    assert "iobench quick failed (advisory)" in step(installer)["detail"]
    assert len([item for item in sent if "iobench quick failed (advisory)" in item.lower()]) == 1


def test_failed_benchmark_keeps_full_stderr_and_names_the_log(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)
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
    span_number = make_span(installer, disk)
    disk.table[span_number]["name"] = "FOREIGN"
    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._run_io_benchmark()
    assert not disk.table_writes


def test_benchmark_refuses_when_span_has_the_wrong_gpt_type(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    disk.table[span_number]["type"] = ZFS_TYPE_GUID

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_identity_mismatch"):
        installer._run_io_benchmark()

    assert not disk.table_writes


def test_span_identity_is_rechecked_after_fio_package_setup(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)

    def change_span(*_args):
        disk.table[span_number]["start"] = str(int(disk.table[span_number]["start"]) + 2048)

    installer._packages = change_span
    with pytest.raises(InstallerError, match="io_benchmark_swap_span_identity_changed"):
        installer._run_io_benchmark()

    assert not any(
        Path(action.argv[0]).name in {"wipefs", "iobench-install-runner"}
        for action in disk.planned
    )


def test_signature_appearing_during_fio_setup_stops_before_wipe(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)

    def sign_span(*_args):
        disk.signatures[f"/dev/vda{span_number}"] = "ext4"

    installer._packages = sign_span
    with pytest.raises(InstallerError, match="io_benchmark_swap_span_foreign_signature"):
        installer._run_io_benchmark()

    assert not any(
        Path(action.argv[0]).name in {"wipefs", "iobench-install-runner"}
        for action in disk.planned
    )


def test_benchmark_requires_the_span_at_the_planned_partition_number(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    planned_entry = dict(disk.table[span_number])
    disk.table[span_number]["name"] = "FOREIGN"
    disk.table[span_number + 20] = {**planned_entry, "name": SWAP_SPAN_PARTLABEL}

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_(identity|geometry|state)_mismatch"):
        installer._run_io_benchmark()

    assert not disk.table_writes


def test_benchmark_refuses_a_span_without_a_partuuid(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)
    disk.partuuid_output = ""

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_identity_mismatch.*no PARTUUID"):
        installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned)
    assert not disk.table_writes


def test_benchmark_refuses_a_resolved_device_outside_the_planned_span(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    make_span(installer, disk)
    monkeypatch.setattr(
        installer,
        "_swap_span_partition",
        lambda: (99, 1, 2, "/dev/vda99", PARTUUID),
    )

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch"):
        installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned)
    assert not disk.table_writes


@pytest.mark.parametrize("geometry_field", ["start", "size"])
def test_benchmark_geometry_mismatch_stops_before_runner(tmp_path, geometry_field):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    disk.table[span_number][geometry_field] = str(int(disk.table[span_number][geometry_field]) + 2048)

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_identity_mismatch"):
        installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned)


def test_duplicate_swap_span_labels_stop_before_runner(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    span = disk.table[span_number]
    disk.table[span_number + 20] = {
        "start": str(int(span["start"]) + int(span["size"]) + 2048),
        "size": "2048", "type": LINUX.upper(), "name": SWAP_SPAN_PARTLABEL,
    }

    with pytest.raises(InstallerError, match="expected exactly one partition with PARTLABEL=vbpub-swap-span"):
        installer._run_io_benchmark()

    assert not any(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned)


def test_duplicate_span_rows_are_refused_before_resume_conversion(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    span = disk.table[span_number]
    disk.table[span_number + 20] = {
        "start": str(int(span["start"]) + int(span["size"]) + 2048),
        "size": "2048", "type": LINUX, "name": SWAP_SPAN_PARTLABEL,
    }

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


def test_resume_refuses_a_partial_tail_swap_shape(tmp_path):
    installer, disk = make(tmp_path)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    last_tail_number = installer._tail_swap_numbers(len(partitions), before_root)[-1]
    disk.table.pop(last_tail_number)

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


def test_resume_refuses_data_area_without_its_tail_swaps(tmp_path):
    installer, disk = make(
        tmp_path,
        partitioning={"data": {"kind": "partitions", "volumes": [
            {"size": 4, "fs": "ext4", "mount": "/data", "label": "DATA"},
        ]}},
    )
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    for number in installer._tail_swap_numbers(len(partitions), before_root):
        disk.table.pop(number)

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


@pytest.mark.parametrize("guard", ["mounted", "active-swap", "foreign-signature"])
def test_mounted_active_or_foreign_signature_swap_is_refused_before_conversion(tmp_path, guard):
    installer, disk = make(tmp_path)
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    swap_number = installer._tail_swap_numbers(len(partitions), before_root)[0]
    swap_device = f"/dev/vda{swap_number}"
    if guard == "mounted":
        disk.findmnt_output += f"{swap_device} /mnt/swap ext4\n"
    elif guard == "active-swap":
        disk.swap_output = f"{swap_device} partition 4G -2\n"
    else:
        disk.signatures[swap_device] = "ext4"

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_(partition_in_use|foreign_signature)"):
        installer._apply_known_swap_shape(tail_swap_span=True)

    assert not disk.table_writes
    assert not any(Path(action.argv[0]).name == "sfdisk" and "--force" in action.argv for action in disk.planned)


def test_fresh_case_a_benchmarks_a_span_then_wipes_and_writes_regular_swaps(tmp_path):
    installer, disk = make(tmp_path)
    disk.load_entries({3: disk.table[3]})

    installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 2
    assert next(entry for entry in disk.table_writes[0].values()
                if entry.get("name") == SWAP_SPAN_PARTLABEL)["type"].lower() == LINUX_FS_TYPE_GUID.lower()
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    assert len([entry for entry in disk.table.values() if entry.get("type", "").lower() == installer_module.SWAP_TYPE_GUID]) == 8
    table_writes = [i for i, action in enumerate(disk.planned)
                    if Path(action.argv[0]).name == "sfdisk" and "--force" in action.argv]
    runner = next(i for i, action in enumerate(disk.planned)
                  if Path(action.argv[0]).name == "iobench-install-runner")
    wipes = [i for i, action in enumerate(disk.planned) if Path(action.argv[0]).name == "wipefs"]
    assert len(table_writes) == 2 and len(wipes) == 2
    assert table_writes[0] < wipes[0] < runner < wipes[1] < table_writes[1]
    assert not any(Path(action.argv[0]).name == "dd" for action in disk.planned)


def test_existing_swap_signatures_are_allowed_and_erased_by_the_span_cycle(tmp_path):
    installer, disk = make(tmp_path)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    first_tail_swap = installer._tail_swap_numbers(len(partitions), before_root)[0]
    disk.signatures[f"/dev/vda{first_tail_swap}"] = "swap"

    installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 2
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    assert f"/dev/vda{first_tail_swap}" not in disk.signatures
    assert disk.runner_signature_snapshots == [(f"/dev/vda{first_tail_swap}", "")]


def test_fake_runner_refuses_to_start_when_a_signature_remains(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    installer._run_io_benchmark()
    runner = next(
        action.argv for action in disk.planned
        if Path(action.argv[0]).name == "iobench-install-runner"
    )
    disk.signatures[f"/dev/vda{span_number}"] = "swap"

    with pytest.raises(ActionError, match="fake runner refuses target with signature swap"):
        disk.run(list(runner))


def test_case_b_root_shrink_table_is_resumed_as_planned_swaps_then_span(tmp_path):
    installer, disk = make(tmp_path)
    disk.load_entries({3: {
        "start": str(ROOT_START), "size": str(90 * SECTORS_PER_GIB),
        "type": LINUX.upper(), "name": "root",
    }})
    partitions, target_root = installer._plan_swap_partitions(validate_shrink_safety=False)
    disk.load_entries(installer._parse_partition_entries(
        installer._write_sfdisk_plan(partitions, target_root)
    ))
    assert int(disk.table[3]["size"]) < 90 * SECTORS_PER_GIB
    assert any(entry.get("type", "").lower() == installer_module.SWAP_TYPE_GUID
               for entry in disk.table.values())
    installer._mark_step("root_shrink", "success", "simulated Case B initramfs hook wrote the table")

    installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 2
    assert any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table_writes[0].values())
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    assert step(installer)["status"] == "success"


@pytest.mark.parametrize(
    ("shape", "status", "expected_writes", "expected_runs"),
    [
        ("swaps", None, 2, 1),
        ("span", None, 1, 1),
        ("span", "success", 1, 0),
        ("span", "warned", 1, 0),
        ("span", "skipped", 1, 0),
        ("swaps", "success", 0, 0),
    ],
)
def test_resume_table_and_step_status_choose_only_the_required_writes(
    tmp_path, shape, status, expected_writes, expected_runs,
):
    installer, disk = make(tmp_path)
    if shape == "span":
        make_span(installer, disk)
    if status:
        set_benchmark_step(installer, status, span_identity=(shape == "span"))

    installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == expected_writes
    assert sum(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned) == expected_runs
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())


def test_unrecognized_resume_shape_is_refused_without_table_write(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    monkeypatch.setattr(
        installer,
        "_validate_swap_span_table_replacement",
        lambda *_args, **_kwargs: ("unknown", (4, 100, 200)),
    )

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


@pytest.mark.parametrize("crash_after", [1, 2])
def test_resume_recovers_a_crash_after_either_table_write(tmp_path, crash_after):
    installer, disk = make(tmp_path)
    disk.crash_after_table_write = crash_after

    with pytest.raises(SystemExit, match="simulated process crash"):
        installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == crash_after
    installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 2
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    assert step(installer)["status"] == "success"


def test_stage2_resume_with_live_span_checks_layout_disables_swap_and_finishes(tmp_path):
    installer, disk = make(
        tmp_path,
        run_zswap_config=False,
        run_cgroup2_config=False,
        run_ksm=False,
        run_oomd_config=False,
        run_fstrim=False,
        run_auto_reboot=False,
    )
    make_span(installer, disk)
    set_benchmark_step(installer, "started", span_identity=True)
    installer._read_fstab_lines = lambda: []
    checked_layouts = []
    check_layout = installer._swap_partitions_already_planned_in_table

    def observe_layout():
        had_span = any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
        result = check_layout()
        checked_layouts.append((had_span, result))
        return result

    installer._swap_partitions_already_planned_in_table = observe_layout
    for name in (
        "_configure_control_plane_memory_min",
        "_configure_iocost",
        "_grow_root_to_plan",
        "_configure_root_cap_boot_guards",
        "_activate_swap_partitions",
        "_health_gate_swap_devices",
        "_verify_control_plane_memory_min",
        "_run_cpu_benchmark",
        "_release_apt_timers",
        "_cleanup_cloud_init_cache",
    ):
        setattr(installer, name, lambda *_args, **_kwargs: None)

    installer._stage2()

    assert checked_layouts == [(True, False)]
    assert installer.state.load()["steps"]["swap_backing_cleanup"]["status"] == "success"
    assert len([action for action in disk.planned if Path(action.argv[0]).name == "iobench-install-runner"]) == 1
    assert len([table for table in disk.table_writes
                if any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in table.values())]) == 0
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    assert installer.state.load()["status"] == "success"


def test_resume_after_sfdisk_then_partx_failure_uses_live_span_without_rewriting_it(tmp_path):
    installer, disk = make(tmp_path)
    retry = installer._run_with_retries
    failed = {"once": False}

    def fail_first_partx(argv, description, *args, **kwargs):
        if Path(argv[0]).name == "partx" and not failed["once"]:
            failed["once"] = True
            raise ActionError("simulated partx failure after sfdisk")
        return retry(argv, description, *args, **kwargs)

    installer._run_with_retries = fail_first_partx
    with pytest.raises(ActionError, match="simulated partx failure"):
        installer._prepare_swap_span_benchmark()

    span_writes_after_crash = [
        table for table in disk.table_writes
        if any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in table.values())
    ]
    assert len(span_writes_after_crash) == 1
    assert not any(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned)

    installer._prepare_swap_span_benchmark()

    span_writes_after_resume = [
        table for table in disk.table_writes
        if any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in table.values())
    ]
    assert len(span_writes_after_resume) == 1
    assert len(disk.table_writes) == 2
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())


def test_final_benchmark_without_a_matching_table_shape_is_refused(tmp_path):
    installer, disk = make(tmp_path)
    disk.load_entries({3: disk.table[3]})
    set_benchmark_step(installer, "success")

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_resume_state"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


def test_advisory_benchmark_failure_still_wipes_span_and_restores_planned_swaps(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["iobench-install-runner"] = "simulated benchmark failure"

    installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 2
    assert step(installer)["status"] == "warned"
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    names = [Path(action.argv[0]).name for action in disk.planned]
    wipes = [i for i, name in enumerate(names) if name == "wipefs"]
    assert len(wipes) == 2
    assert wipes[0] < names.index("iobench-install-runner") < wipes[1]
    assert "dd" not in names


def test_unreapable_runner_preserves_span_and_names_operator_recovery(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["iobench-install-runner"] = ActionUnreapable("runner process could not be reaped")

    with pytest.raises(InstallerError, match="iobench could not be reaped"):
        installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 1
    assert any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    names = [Path(action.argv[0]).name for action in disk.planned]
    assert names.count("wipefs") == 1
    assert names.index("wipefs") < names.index("iobench-install-runner")
    assert "dd" not in names
    assert "operator recovery" in step(installer)["detail"]
    span_number = next(number for number, entry in disk.table.items()
                       if entry.get("name") == SWAP_SPAN_PARTLABEL)
    span_device = f"/dev/vda{span_number}"
    assert not any(
        Path(action.argv[0]).name.startswith(("mkfs.", "mkswap")) and span_device in action.argv
        for action in disk.planned
    )

    with pytest.raises(InstallerError, match="io_benchmark_unreapable_span") as raised:
        installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 1
    assert any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    assert [Path(action.argv[0]).name for action in disk.planned].count("wipefs") == 1
    assert "operator recovery" in str(raised.value)


def test_unreapable_fio_package_step_is_not_reported_as_iobench(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)

    def unreapable_packages(*_args):
        raise ActionUnreapable("apt process could not be reaped")

    installer._packages = unreapable_packages
    with pytest.raises(InstallerError, match="fio package installation could not be reaped") as raised:
        installer._run_io_benchmark()

    package_step = step(installer, "io_benchmark_packages")
    assert package_step["status"] == "failed"
    assert "operator recovery" in package_step["detail"]
    assert "iobench could not be reaped" not in package_step["detail"]
    assert not any(
        Path(action.argv[0]).name in {"wipefs", "iobench-install-runner"}
        for action in disk.planned
    )
    assert "runner has not started" in str(raised.value)


def test_no_tail_swap_span_is_warned_when_all_swap_is_retained_before_root(tmp_path):
    installer, disk = make(tmp_path, swap_disk_total_gb=1, swap_partition_count=1)
    install_pre_root_swap(installer, disk, size_gib=1)
    partitions, target_root = installer._plan_swap_partitions()
    with pytest.raises(InstallerError, match="io_benchmark_requires_tail_swap_span"):
        installer._validate_swap_span_table_replacement(
            disk.table, partitions, installer._planned_before_root(), target_root
        )
    installer._run_io_benchmark()
    with pytest.raises(InstallerError, match="io_benchmark_requires_tail_swap_span"):
        installer._swap_span_partition()

    installer._prepare_swap_span_benchmark()

    assert step(installer)["status"] == "warned"
    assert step(installer)["detail"].startswith("no_tail_swap_span")
    assert not disk.table_writes
    assert not any(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned)


def test_no_tail_warning_still_completes_missing_data_layout(tmp_path):
    installer, disk = make(
        tmp_path,
        swap_disk_total_gb=1,
        swap_partition_count=1,
        partitioning={"data": {"kind": "partitions", "volumes": [
            {"size": 1, "fs": "ext4", "mount": "/data", "label": "DATA"},
        ]}},
    )
    disk.load_entries({
        2: {"start": "2048", "size": str(SECTORS_PER_GIB),
            "type": installer_module.SWAP_TYPE_GUID, "name": ""},
        3: disk.table[3],
    })

    installer._prepare_swap_span_benchmark()

    assert step(installer)["status"] == "warned"
    assert len(disk.table_writes) == 1
    assert any(entry.get("name") == "DATA" for entry in disk.table.values())
    assert not any(Path(action.argv[0]).name == "iobench-install-runner" for action in disk.planned)


@pytest.mark.parametrize("stray_shape", ["span", "swap"])
def test_no_tail_plan_refuses_a_stray_tail_partition(tmp_path, stray_shape):
    installer, disk = make(tmp_path, swap_disk_total_gb=1, swap_partition_count=1)
    install_pre_root_swap(installer, disk, size_gib=1)
    _disk_sectors, root_start, root_size = installer._disk_facts()
    number = 4
    disk.table[number] = {
        "start": str(root_start + root_size + 2048),
        "size": "2048",
        "type": installer_module.SWAP_TYPE_GUID if stray_shape == "swap" else LINUX,
        "name": SWAP_SPAN_PARTLABEL if stray_shape == "span" else "",
    }

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


def test_retained_pre_root_swap_stays_outside_the_temporary_span(tmp_path):
    installer, disk = make(tmp_path)
    install_pre_root_swap(installer, disk, size_gib=1)

    installer._prepare_swap_span_benchmark()

    assert len(disk.table_writes) == 2
    retained = disk.table[2]
    assert retained["start"] == "2048"
    assert int(retained["start"]) + int(retained["size"]) < int(disk.table[3]["start"])
    span = next(entry for entry in disk.table_writes[0].values()
                if entry.get("name") == SWAP_SPAN_PARTLABEL)
    assert int(retained["start"]) + int(retained["size"]) < int(span["start"])


def test_benchmark_disabled_keeps_the_regular_swap_table_shape(tmp_path):
    installer, disk = make(tmp_path, run_io_benchmark=False)
    disk.load_entries({3: disk.table[3]})

    installer._apply_known_swap_shape()

    assert len(disk.table_writes) == 1
    assert not any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())
    swaps = [entry for entry in disk.table.values()
             if entry.get("type", "").lower() == installer_module.SWAP_TYPE_GUID]
    assert len(swaps) == 8
    assert not any(Path(action.argv[0]).name in {"wipefs", "dd", "iobench-install-runner"}
                   for action in disk.planned)


def test_benchmark_disabled_refuses_a_leftover_temporary_span(tmp_path):
    installer, disk = make(tmp_path, run_io_benchmark=False)
    make_span(installer, disk)

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._apply_known_swap_shape()

    assert not disk.table_writes
    assert any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())


@pytest.mark.parametrize("allow_swap_span", [False, True])
def test_plan_geometry_requires_an_explicit_expected_span(tmp_path, allow_swap_span):
    installer, disk = make(tmp_path)
    partitions, target_root = installer._plan_swap_partitions()
    current = {number: dict(entry) for number, entry in disk.table.items()}
    plan = installer._parse_partition_entries(
        installer._write_sfdisk_plan(partitions, target_root, tail_swap_span=True)
    )

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch"):
        installer._validate_plan_geometry(
            current, plan, target_root, allow_swap_span=allow_swap_span,
        )


def test_mixed_span_and_swap_table_is_refused_without_rewrite(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    swap_number = installer._tail_swap_numbers(len(partitions), before_root)[0]
    disk.table[swap_number] = {
        "start": str(int(disk.table[span_number]["start"]) + 2048),
        "size": "2048", "type": installer_module.SWAP_TYPE_GUID,
    }

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch|io_benchmark_swap_span_state_mismatch"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


def test_dry_run_records_both_table_plans_without_changing_the_table(tmp_path):
    installer, disk = make(tmp_path)
    original = {number: dict(entry) for number, entry in disk.table.items()}
    disk.dry_run = True

    installer._prepare_swap_span_benchmark()

    span_plan = disk.files[str(Path(installer.config.state_dir) / "partition-plan-iobench-span.sfdisk")].decode()
    swap_plan = disk.files[str(Path(installer.config.state_dir) / "partition-plan.sfdisk")].decode()
    assert f'name="{SWAP_SPAN_PARTLABEL}"' in span_plan
    assert f"type={installer_module.SWAP_TYPE_GUID}" in swap_plan
    assert disk.table == original
    assert not disk.table_writes


def test_other_partition_intersecting_the_span_is_refused_by_geometry(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    span = disk.table[span_number]
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    disk.table[100] = {
        "start": str(int(span["start"]) + 2048), "size": "2048",
        "type": LINUX, "name": "foreign-data",
    }

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch"):
        installer._validate_swap_span_table_replacement(
            disk.table, partitions, before_root, target_root
        )

    assert not disk.table_writes


def test_incomplete_foreign_geometry_is_refused_before_span_conversion(tmp_path):
    installer, disk = make(tmp_path)
    partitions, target_root = installer._plan_swap_partitions()
    current = {number: dict(entry) for number, entry in disk.table.items()}
    current[100] = {"type": LINUX, "name": "foreign"}

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch"):
        installer._validate_swap_span_table_replacement(
            current, partitions, installer._planned_before_root(), target_root
        )

    assert not disk.table_writes


@pytest.mark.parametrize(
    ("shape", "message"),
    [
        ("incomplete-swap", "cannot safely retain or replace swap partition"),
        ("incomplete-foreign", "cannot safely resize root because partition geometry is missing"),
        ("root-overlap", "partition(s) overlap the current root extent"),
    ],
)
@pytest.mark.parametrize("run_benchmark", [False, True])
def test_live_geometry_refusal_is_named_for_benchmark_plans(
    tmp_path, monkeypatch, shape, message, run_benchmark,
):
    installer, disk = make(tmp_path, run_io_benchmark=run_benchmark)
    root = dict(disk.table[3])
    if shape == "root-overlap":
        foreign = {"start": str(ROOT_START + 2048), "size": "2048", "type": LINUX}
    else:
        foreign = {"type": installer_module.SWAP_TYPE_GUID if shape == "incomplete-swap" else LINUX}
    live = {3: root, 100: foreign}
    monkeypatch.setattr(installer, "_live_partition_entries", lambda *_args: live)

    expected = ("io_benchmark_swap_span_geometry_mismatch: " if run_benchmark else "") + message
    with pytest.raises(InstallerError, match=re.escape(expected)):
        installer._plan_swap_partitions()

    assert not disk.table_writes


def test_noncontiguous_tail_geometry_is_refused_by_span_builders(tmp_path, monkeypatch):
    installer, _disk = make(tmp_path)
    partitions, target_root = installer._plan_swap_partitions()
    noncontiguous = [partitions[0], (partitions[1][0] + 2048, partitions[1][1])]

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch"):
        installer._planned_tail_swap_span(noncontiguous, {})
    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch"):
        installer._write_sfdisk_plan(noncontiguous, target_root, tail_swap_span=True)

    monkeypatch.setattr(installer, "_tail_swap_numbers", lambda *_args: [])
    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch"):
        installer._planned_tail_swap_span(partitions, {})
    with pytest.raises(InstallerError, match="io_benchmark_swap_span_geometry_mismatch"):
        installer._write_sfdisk_plan(partitions, target_root, tail_swap_span=True)


def test_unrelated_data_geometry_is_refused_before_span_conversion(tmp_path):
    installer, disk = make(
        tmp_path,
        partitioning={"data": {"kind": "partitions", "volumes": [
            {"size": 4, "fs": "ext4", "mount": "/data", "label": "DATA"},
        ]}},
    )
    data_number = next(number for number, entry in disk.table.items() if entry.get("name") == "DATA")
    disk.table[data_number]["start"] = str(int(disk.table[data_number]["start"]) + 2048)

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_state_mismatch"):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


@pytest.mark.parametrize("guard", ["mounted", "active-swap", "foreign-signature"])
def test_span_is_not_benchmarked_or_wiped_when_in_use_or_foreign_signed(tmp_path, guard):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    span_device = f"/dev/vda{span_number}"
    if guard == "mounted":
        disk.findmnt_output += f"{span_device} /mnt/span ext4\n"
    elif guard == "active-swap":
        disk.swap_output = f"{span_device} partition 4G -2\n"
    else:
        disk.signatures[span_device] = "ext4"

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_(partition_in_use|foreign_signature)"):
        installer._run_io_benchmark()

    assert step(installer)["status"] == "failed"
    assert not any(Path(action.argv[0]).name in {"iobench-install-runner", "wipefs", "dd"}
                   for action in disk.planned)
    assert not disk.table_writes


@pytest.mark.parametrize("kind", ["swap", "span"])
def test_nested_partition_table_on_swap_or_span_is_foreign(tmp_path, kind):
    installer, disk = make(tmp_path)
    if kind == "span":
        number = make_span(installer, disk)
        operation = installer._run_io_benchmark
    else:
        partitions, _target_root = installer._plan_swap_partitions()
        number = installer._tail_swap_numbers(
            len(partitions), installer._planned_before_root()
        )[0]
        operation = lambda: installer._apply_known_swap_shape(tail_swap_span=True)
    disk.partition_tables[f"/dev/vda{number}"] = "gpt"

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_foreign_signature"):
        operation()

    assert not disk.table_writes
    assert not any(
        Path(action.argv[0]).name in {"wipefs", "iobench-install-runner"}
        for action in disk.planned
    )


def test_final_benchmark_span_identity_mismatch_stops_before_wipe(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)
    installer._mark_step(
        "io_benchmark", "success",
        "PARTUUID=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa start=1 size=2 sectors profile=quick",
    )

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_identity_changed"):
        installer._prepare_swap_span_benchmark()

    assert not any(Path(action.argv[0]).name in {"wipefs", "dd"} for action in disk.planned)
    assert not disk.table_writes


def test_wipe_refuses_before_a_benchmark_step_is_final(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_resume_state"):
        installer._wipe_swap_span()

    assert not any(Path(action.argv[0]).name in {"wipefs", "dd"} for action in disk.planned)
    assert not disk.table_writes


def test_stage_two_benchmark_result_is_consumed_by_iocost_after_the_table_write(tmp_path, monkeypatch):
    installer, disk = make(
        tmp_path,
        iocost_enabled=True,
        partitioning={"root": 10, "data": {"kind": "partitions", "volumes": [
            {"size": "*", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
        ]}},
    )
    make_span(installer, disk)
    disk.mkdir = lambda path: disk.planned.append(PlannedAction(("/usr/bin/mkdir", "-p", path), "fake mkdir"))
    disk.exists = lambda _path: False
    events: list[str] = []
    monkeypatch.setattr(installer, "_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(installer, "_configure_control_plane_memory_min", lambda: None)
    monkeypatch.setattr(installer, "_verify_control_plane_memory_min", lambda: None)
    monkeypatch.setattr(installer, "_verify_and_apply_root_shrink", lambda: False)
    monkeypatch.setattr(installer, "_swap_partitions_already_planned_in_table", lambda: False)
    monkeypatch.setattr(installer, "_disable_active_swap", lambda: None)
    monkeypatch.setattr(installer, "_configure_zswap", lambda: None)
    monkeypatch.setattr(installer, "_configure_cgroup2_flags", lambda: None)
    monkeypatch.setattr(installer, "_configure_ksm", lambda: None)
    monkeypatch.setattr(installer, "_configure_oomd", lambda: None)
    monkeypatch.setattr(installer, "_configure_fstrim", lambda: None)
    monkeypatch.setattr(installer, "_configure_auto_reboot", lambda: None)
    run_benchmark = installer._run_io_benchmark
    def prepare_span_cycle():
        events.append("span-write")
        events.append("benchmark")
        run_benchmark()
        events.append("swap-write")
    monkeypatch.setattr(installer, "_prepare_swap_span_benchmark", prepare_span_cycle)
    monkeypatch.setattr(installer, "_ensure_disk_transaction_manifest", lambda: None)
    configure_iocost = installer._configure_iocost
    monkeypatch.setattr(installer, "_configure_iocost", lambda: (events.append("iocost"), configure_iocost())[1])
    monkeypatch.setattr(installer, "_grow_root_to_plan", lambda: events.append("root-grow"))
    monkeypatch.setattr(installer, "_activate_swap_partitions", lambda: events.append("swap-active"))
    monkeypatch.setattr(installer, "_configure_data_area", lambda: events.append("data-area-format"))
    monkeypatch.setattr(installer, "_health_gate_swap_devices", lambda: None)
    monkeypatch.setattr(installer, "_run_cpu_benchmark", lambda: None)
    monkeypatch.setattr(installer, "_release_apt_timers", lambda: None)
    monkeypatch.setattr(installer, "_cleanup_cloud_init_cache", lambda: None)

    installer._stage2()

    assert events.index("span-write") < events.index("benchmark") < events.index("swap-write")
    assert events.index("swap-write") < events.index("iocost")
    assert events.index("iocost") < events.index("root-grow") < events.index("swap-active")
    assert events.index("swap-write") < events.index("data-area-format")
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

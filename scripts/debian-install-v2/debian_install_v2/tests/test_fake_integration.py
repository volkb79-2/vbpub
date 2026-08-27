from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from debian_install_v2.actions import HostActions, PlannedAction
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer
from debian_install_v2.state import StateStore


CURRENT_DUMP = "label: gpt\ndevice: /dev/vda\n\n/dev/vda3 : start=2500608, size=20971520, type=0fc63daf-8483-4772-8e79-3d69d8477de4"


class FakeHostActions(HostActions):
    def __init__(self) -> None:
        super().__init__(dry_run=False)
        self.outputs = {("/usr/sbin/sfdisk", "--dump", "/dev/vda"): CURRENT_DUMP}
        self.files: dict[str, bytes] = {}
        self.readback: str | None = None
        self.applied = False

    def run(self, argv: list[str], description: str = "", dangerous: bool = False) -> str | None:
        self._validate(list(argv))
        self.planned.append(PlannedAction(tuple(argv), description or " ".join(argv), dangerous))
        if argv[0] == "/usr/sbin/sfdisk" and argv[1] == "--force":
            self.applied = True
            return ""
        if argv[0] == "/usr/sbin/sfdisk" and argv[1] == "--dump":
            if self.applied and self.readback is not None:
                return self.readback
            return self.outputs.get(tuple(argv), CURRENT_DUMP)
        key = tuple(argv)
        if key not in self.outputs:
            return self.outputs.get(key, "")
        return self.outputs[key]

    def write_file(self, path: str, content: str, mode: int = 0o644) -> None:
        if not path.startswith("/"):
            raise AssertionError(path)
        self.planned.append(PlannedAction(("/usr/bin/tee", path), f"write {path}", True))
        self.files[path] = content.encode("utf-8")


def test_fake_write_file_rejects_relative_path():
    actions = FakeHostActions()
    with pytest.raises(AssertionError):
        actions.write_file("relative/path", "x")


def make_installer(tmp_path: Path) -> tuple[Installer, FakeHostActions]:
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        telegram_bot_token="",
        telegram_chat_id="",
        auto_reboot_after_stage1=False,
        swap_disk_total_gb=32,
        swap_file_count=8,
    )
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    actions.outputs[("/usr/bin/findmnt", "-rn", "-o", "SOURCE,TARGET,FSTYPE")] = "/dev/vda3 / ext4\n"
    actions.outputs[("/usr/sbin/blockdev", "--getsize64", "/dev/vda")] = str(512 * 1024 ** 3)
    actions.outputs[("/usr/sbin/partprobe", "/dev/vda")] = ""
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer, actions


def expected_readback(installer: Installer) -> tuple[list[tuple[int, dict[str, str]]], str]:
    partitions, new_root_size = installer._plan_swap_partitions()
    plan_text = installer._write_sfdisk_plan(partitions, new_root_size)
    entries = installer._parse_partition_entries(plan_text)
    ordered = [(number, entries[number]) for number in sorted(entries)]
    return ordered, plan_text


def test_transaction_succeeds_with_checksum_and_manifest(tmp_path):
    installer, actions = make_installer(tmp_path)
    ordered, readback = expected_readback(installer)
    actions.readback = readback
    installer._apply_known_swap_shape()
    backup_name = next(name for name in actions.files if "/backups/ptable-" in name and name.endswith(".sfdisk"))
    checksum_name = backup_name + ".sha256"
    digest = hashlib.sha256(actions.files[backup_name]).hexdigest()
    assert actions.files[checksum_name].decode().startswith(digest)
    manifest = json.loads(actions.files[str(Path(installer.config.state_dir) / "disk-transaction.json")])
    assert manifest["backup"] == backup_name
    assert manifest["current"] == CURRENT_DUMP
    assert len([line for line in manifest["plan"].splitlines() if "type=0657fd6d" in line]) == 8


def test_mismatched_readback_rolls_back(tmp_path):
    installer, actions = make_installer(tmp_path)
    _, readback = expected_readback(installer)
    actions.readback = readback.replace("start=23472128", "start=23472129", 1)
    with pytest.raises(RuntimeError, match="verification failed; restored backup"):
        installer._apply_known_swap_shape()
    rollback = [
        action for action in actions.planned
        if action.argv[:3] == ("/usr/sbin/sfdisk", "--force", "/dev/vda")
        and len(action.argv) == 4
        and action.argv[3].endswith(".sfdisk")
    ]
    assert rollback
    backup_name = rollback[0].argv[3]
    assert actions.files[str(rollback[0].argv[3])].decode() == CURRENT_DUMP


def test_verify_refuses_checksum_mismatch(tmp_path):
    installer, _ = make_installer(tmp_path)
    transaction_dir = Path(installer.config.state_dir)
    backup_dir = transaction_dir / "backups"
    backup_dir.mkdir(parents=True)
    backup = backup_dir / "old.sfdisk"
    backup.write_text(CURRENT_DUMP, encoding="utf-8")
    checksum = backup_dir / "old.sfdisk.sha256"
    checksum.write_text("bad  old.sfdisk\n", encoding="utf-8")
    (transaction_dir / "disk-transaction.json").write_text(json.dumps({
        "backup": str(backup),
        "checksum": str(checksum),
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        installer.verify()

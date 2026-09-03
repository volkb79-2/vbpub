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
        self.exists_result = True

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

    def exists(self, path: str) -> bool:
        # No real block devices in a fake test run — the post-partx/udevadm
        # device-appearance poll in _apply_known_swap_shape() would otherwise
        # spin for 5s per swap partition and then fail. Readback verification
        # (self.readback / expected_readback()) is this fixture's actual
        # source of truth for "did partitioning succeed", not device nodes.
        # exists_result lets a test override this to prove the poll's own
        # failure path (device never appears) still raises correctly.
        return self.exists_result


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


def test_apply_uses_partx_and_udevadm_not_partprobe(tmp_path):
    # P0#3 (DEBIAN-INSTALLv2-REVIEW.md): partprobe alone is less reliable at
    # getting new partition device nodes to exist than partx -a + udevadm
    # settle — the mechanism inuse_partition_editor.Table.write() already
    # uses, ported here to go through HostActions instead of a bare
    # subprocess.run.
    installer, actions = make_installer(tmp_path)
    _, readback = expected_readback(installer)
    actions.readback = readback
    installer._apply_known_swap_shape()
    argvs = [action.argv for action in actions.planned]
    assert ("/usr/sbin/partx", "-a", "/dev/vda") in argvs
    assert ("/usr/bin/udevadm", "settle") in argvs
    assert not any(argv[0] == "/usr/sbin/partprobe" for argv in argvs)


def test_apply_raises_when_swap_device_never_appears(tmp_path):
    installer, actions = make_installer(tmp_path)
    _, readback = expected_readback(installer)
    actions.readback = readback
    actions.exists_result = False
    with pytest.raises(RuntimeError, match="partition device did not appear"):
        installer._apply_known_swap_shape()


def test_disk_facts_tolerates_quoted_gpt_name_attribute(tmp_path):
    # P0#1 (DEBIAN-INSTALLv2-REVIEW.md): a naive line.split() mis-tokenizes
    # a quoted name= value that contains a space (real GPT disks commonly
    # carry one, e.g. a cloud image's "EFI System Partition" label on an
    # earlier partition) — inuse_partition_editor.Table's _ATTR_RE-based
    # parser handles it correctly.
    installer, actions = make_installer(tmp_path)
    actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = (
        'label: gpt\ndevice: /dev/vda\n\n'
        '/dev/vda1 : start=2048, size=1050624, type=c12a7328-f81f-11d2-ba4b-00a0c93ec93b, '
        'name="EFI System Partition"\n'
        '/dev/vda3 : start=2500608, size=20971520, type=0fc63daf-8483-4772-8e79-3d69d8477de4\n'
    )
    disk_sectors, root_start, root_size = installer._disk_facts()
    assert root_start == 2500608
    assert root_size == 20971520


def test_disk_facts_converts_unsupported_disklabel_systemexit_to_runtimeerror(tmp_path):
    # Table.__init__ calls sys.exit() (SystemExit, a BaseException) on an
    # unsupported disklabel -- a clean CLI idiom for the editor's own
    # __main__, but bootstrap.py's `except Exception` around the installer's
    # run does not catch it, skipping the normal error-reporting path every
    # other RuntimeError here goes through (adversarial review finding).
    installer, actions = make_installer(tmp_path)
    actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = "label: sun\nunit: sectors\nsector-size: 512\n"
    with pytest.raises(RuntimeError, match="could not read partition table"):
        installer._disk_facts()


def test_disk_facts_rejects_non_positive_root_size(tmp_path):
    installer, actions = make_installer(tmp_path)
    actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = (
        "label: gpt\ndevice: /dev/vda\n\n/dev/vda3 : start=2500608, size=0, type=0fc63daf-8483-4772-8e79-3d69d8477de4\n"
    )
    with pytest.raises(RuntimeError, match="non-positive size"):
        installer._disk_facts()


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

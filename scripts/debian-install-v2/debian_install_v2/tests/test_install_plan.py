from __future__ import annotations

import json
from pathlib import Path
import sys

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer
from debian_install_v2.state import StateStore


def run_dry_install(tmp_path: Path) -> tuple[Installer, list[str]]:
    from debian_install_v2.state import StateStore
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        stage2_output="/root/custom_script.output2",
        swap_disk_total_gb=32,
        swap_file_count=8,
        telegram_bot_token="",
        telegram_chat_id="",
        never_reboot=True,
        auto_reboot_after_stage1=False,
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    installer.install()
    return installer, [action.description for action in actions.planned]


def test_stage1_plans_expected_configuration_without_host_mutation(tmp_path):
    _, descriptions = run_dry_install(tmp_path)
    joined = "\n".join(descriptions)
    for expected in (
        "write /etc/apt/sources.list.d/debian.sources",
        "write /etc/apt/preferences.d/debian-priorities",
        "write /etc/systemd/system/vbpub-bootstrap-stage2.service",
        "reload stage2 unit",
    ):
        assert expected in joined
    assert "/bin/rm" not in joined


def test_state_new_excludes_secret_and_uses_stable_schema(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        swap_disk_total_gb=32, swap_file_count=8,
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
    )
    store = StateStore(config.state_dir)
    store.save_new(StateStore.new(config))
    manifest = json.loads((Path(config.state_dir) / "state.json").read_text())
    assert manifest["schema_version"] == 1
    assert "telegram_bot_token" not in manifest["config"]
    assert manifest["config"]["swap_disk_total_gb"] == 32
    assert manifest["config"]["swap_file_count"] == 8


def test_known_shape_partition_plan_has_exact_eight_devices(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer.resume()
    plan_path = str(Path(installer.config.state_dir) / "partition-plan.sfdisk")
    assert plan_path in actions.dry_run_writes
    assert not Path(plan_path).exists()
    plan = actions.dry_run_writes[plan_path]
    swaps = [line for line in plan.splitlines() if line.startswith("/dev/vda") and "type=0657fd6d" in line]
    assert len(swaps) == 8
    assert all("start=" in line and "size=" in line and ", type=0657fd6d-a4ab-43c4-84e5-0933c84b4f4f" in line for line in swaps)
    root_lines = [line for line in plan.splitlines() if line.startswith("/dev/vda3 ")]
    assert len(root_lines) == 1
    assert any(part.startswith("size=20971520") for part in root_lines[0].split())


def test_fstab_swap_entries_are_planned(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer.resume()
    fstab = actions.dry_run_writes["/etc/fstab"]
    assert len([line for line in fstab.splitlines() if " none swap sw,pri=10,discard=once 0 0" in line]) == 8


def test_disk_transaction_manifest_and_health_gate_are_planned(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer.resume()
    manifest = json.loads(actions.dry_run_writes[str(Path(config.state_dir) / "disk-transaction.json")])
    assert manifest["preflight"]["mode"] == "dry-run"
    assert manifest["backup"].endswith(".sfdisk")
    assert manifest["checksum"].endswith(".sha256")
    assert any(action.description == "write /etc/fstab" for action in actions.planned)


def test_credentials_are_not_written_to_bootstrap_env(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="123:secret", telegram_chat_id="123456",
        auto_reboot_after_stage1=False,
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer.install()
    env = actions.dry_run_writes["/etc/vbpub/bootstrap.env"]
    assert "VBPUB_TELEGRAM" not in env
    assert "123:secret" not in env
    assert actions.dry_run_writes[str(Path(config.state_dir) / "credentials/telegram_bot_token")] == "123:secret\n"
    assert actions.dry_run_writes[str(Path(config.state_dir) / "credentials/telegram_chat_id")] == "123456\n"


def test_geometry_refuses_existing_partition_after_root():
    config = Config(telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False)
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    current = {
        3: {"start": "2500608", "size": "20971520"},
        4: {"start": "2500609", "size": "100"},
    }
    plan = {
        3: {"start": "2500608", "size": "20971520"},
        4: {"start": "23472128", "size": "8388608"},
    }
    import pytest
    with pytest.raises(RuntimeError, match="existing partition 4 follows root"):
        installer._validate_plan_geometry(current, plan, 20971520)


def test_resume_restores_config_from_manifest_and_records_success(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    store = StateStore(config.state_dir)
    store.save_new(StateStore.new(config))
    installer.resume()
    assert installer.actions.planned[-1].description == "write /etc/fstab"
    assert any(action.description == f"enable {installer._partition_base}{installer.root_number + 8}" for action in installer.actions.planned)

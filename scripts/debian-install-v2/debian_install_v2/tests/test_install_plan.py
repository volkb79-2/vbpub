from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.state import StateStore


ROOT_CAP_CLOUD_INIT_CONFIG = "/etc/cloud/cloud.cfg.d/99-debian-install-v2-root-cap.cfg"
GROWROOT_DISABLED = "/etc/growroot-disabled"


def cap(root_gib: int | None) -> dict:
    """No plan (None) or a root of that size with the rest left unallocated on purpose."""
    return {} if root_gib is None else {"root": root_gib, "reserve_gb": "*"}


def run_dry_install(
    tmp_path: Path,
    *,
    root_max_size_gb: int | None = None,
    preserve_root_size_gb: int = 10,
    will_reboot: bool = False,
    current_root_gib: int | None = None,
) -> tuple[Installer, list[str]]:
    from debian_install_v2.state import StateStore
    if current_root_gib is not None:
        # The dry-run root is 8 GiB; a shrink below the layout's minimum root
        # needs a bigger current root.
        from unittest import mock
        real = Installer._disk_facts

        def facts(self):
            disk, start, _size = real(self)
            return disk, start, current_root_gib * 1024 ** 3 // 512

        with mock.patch.object(Installer, "_disk_facts", facts):
            return run_dry_install(
                tmp_path, root_max_size_gb=root_max_size_gb,
                preserve_root_size_gb=preserve_root_size_gb, will_reboot=will_reboot,
            )
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        stage2_output="/root/custom_script.output2",
        swap_disk_total_gb=32,
        swap_partition_count=8,
        partitioning=cap(root_max_size_gb),
        preserve_root_size_gb=preserve_root_size_gb,
        telegram_bot_token="",
        telegram_chat_id="",
        never_reboot=not will_reboot,
        auto_reboot_after_stage1=will_reboot,
        credential_mode="systemd",
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


@pytest.mark.parametrize("root_max_size_gb", [None, 160])
def test_stage1_installs_root_cap_boot_guards_only_when_capped_before_reboot(
    tmp_path, root_max_size_gb
):
    installer, descriptions = run_dry_install(
        tmp_path,
        root_max_size_gb=root_max_size_gb,
        will_reboot=True,
    )
    expected = root_max_size_gb is not None

    for path in (ROOT_CAP_CLOUD_INIT_CONFIG, GROWROOT_DISABLED):
        assert (path in installer.actions.dry_run_writes) is expected
        assert descriptions.count(f"write {path}") == (1 if expected else 0)
    if expected:
        assert installer.actions.dry_run_writes[ROOT_CAP_CLOUD_INIT_CONFIG] == (
            "growpart: {mode: off}\nresize_rootfs: false\n"
        )
        assert installer.actions.dry_run_writes[GROWROOT_DISABLED] == ""
        last_guard_write = max(
            descriptions.index(f"write {ROOT_CAP_CLOUD_INIT_CONFIG}"),
            descriptions.index(f"write {GROWROOT_DISABLED}"),
        )
        reboot = descriptions.index(
            "schedule delayed reboot into stage2 (avoids stranding the provider's cloud-init tracking)"
        )
        assert last_guard_write < reboot


def test_stage1_writes_root_cap_boot_guards_before_installing_shrink_hook(tmp_path):
    _, descriptions = run_dry_install(
        tmp_path,
        root_max_size_gb=100,
        preserve_root_size_gb=1,
        will_reboot=True,
        current_root_gib=300,
    )

    last_guard_write = max(
        descriptions.index(f"write {ROOT_CAP_CLOUD_INIT_CONFIG}"),
        descriptions.index(f"write {GROWROOT_DISABLED}"),
    )
    hook_write = descriptions.index("write /etc/initramfs-tools/hooks/vbpub-root-shrink")
    rebuild_initramfs = descriptions.index("rebuild initramfs with the root-shrink hook")

    assert last_guard_write < hook_write < rebuild_initramfs


def test_stage1_refused_root_cap_does_not_install_boot_guards(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        stage2_output="/root/custom_script.output2",
        swap_policy="on",
        # 40 + 20 GiB fixed and no "*": the plan does not account for the disk and is refused
        partitioning={"root": 40, "data": {"kind": "partitions", "volumes": [
            {"size": 20, "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}},
        telegram_bot_token="",
        telegram_chat_id="",
        never_reboot=False,
        auto_reboot_after_stage1=True,
        credential_mode="systemd",
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)

    with pytest.raises(InstallerError, match="fixed sizes without a growable entry"):
        installer.install()

    descriptions = [action.description for action in actions.planned]
    for path in (
        ROOT_CAP_CLOUD_INIT_CONFIG,
        GROWROOT_DISABLED,
        "/etc/vbpub/root-shrink-plan.sfdisk",
        "/etc/vbpub/root-shrink-plan.env",
        "/etc/initramfs-tools/hooks/vbpub-root-shrink",
        "/etc/initramfs-tools/scripts/local-premount/vbpub-root-shrink",
    ):
        assert path not in actions.dry_run_writes
        assert f"write {path}" not in descriptions


def test_state_new_excludes_secret_and_uses_stable_schema(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        swap_disk_total_gb=32, swap_partition_count=8,
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
        credential_mode="systemd",
    )
    store = StateStore(config.state_dir)
    store.save_new(StateStore.new(config))
    manifest = json.loads((Path(config.state_dir) / "state.json").read_text())
    assert manifest["schema_version"] == 1
    assert "telegram_bot_token" not in manifest["config"]
    assert manifest["config"]["swap_disk_total_gb"] == 32
    assert manifest["config"]["swap_partition_count"] == 8


def test_default_partition_plan_has_exact_four_swap_devices(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
        credential_mode="systemd",
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
    assert len(swaps) == 4
    assert all("start=" in line and "size=" in line and ", type=0657fd6d-a4ab-43c4-84e5-0933c84b4f4f" in line for line in swaps)
    root_lines = [line for line in plan.splitlines() if line.startswith("/dev/vda3 ")]
    assert len(root_lines) == 1
    # Root keeps its own (dry-run synthetic) size, 8 GiB in sectors -- it
    # must NOT be inflated to the (larger, 10 GiB default) preserve_root_size_gb
    # floor. That inflation was a real bug (see
    # test_plan_swap_partitions_never_grows_root_below_preserve_floor in
    # test_fake_integration.py for the live-host-confirmed regression): Case
    # A never resizes root, so the plan must show root's real, unchanged size.
    assert any(part.startswith("size=16777216") for part in root_lines[0].split())


def test_default_fstab_swap_entries_are_planned(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
        credential_mode="systemd",
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer.resume()
    fstab = actions.dry_run_writes["/etc/fstab"]
    assert len([line for line in fstab.splitlines() if " none swap sw,pri=10,discard=once 0 0" in line]) == 4


@pytest.mark.parametrize("root_max_size_gb", [None, 160])
def test_stage2_disables_boot_time_root_growth_only_when_capped(tmp_path, root_max_size_gb):
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        partitioning=cap(root_max_size_gb),
        swap_policy="on",
        swap_disk_total_gb=8,
        run_io_benchmark=False,
        telegram_bot_token="",
        telegram_chat_id="",
        auto_reboot_after_stage1=False,
        credential_mode="systemd",
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))

    installer.resume()

    expected = root_max_size_gb is not None
    assert (ROOT_CAP_CLOUD_INIT_CONFIG in actions.dry_run_writes) is expected
    assert (GROWROOT_DISABLED in actions.dry_run_writes) is expected
    if expected:
        assert actions.dry_run_writes[ROOT_CAP_CLOUD_INIT_CONFIG] == (
            "growpart: {mode: off}\nresize_rootfs: false\n"
        )
        assert actions.dry_run_writes[GROWROOT_DISABLED] == ""


def test_disk_transaction_manifest_and_health_gate_are_planned(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
        credential_mode="systemd",
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
    installer = Installer(config, actions, inspect_host=False)
    installer.root_number = 3
    current = {
        3: {"start": "2500608", "size": "20971520"},
        4: {"start": "23472128", "size": "100"},
    }
    plan = {
        3: {"start": "2500608", "size": "20971520"},
        4: {"start": "23472128", "size": "8388608"},
    }
    import pytest
    with pytest.raises(RuntimeError, match="non-swap partition 4 follows root"):
        installer._validate_plan_geometry(current, plan, 20971520)


def test_resume_restores_config_from_manifest_and_records_success(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", auto_reboot_after_stage1=False,
        credential_mode="systemd", run_cpu_benchmark=False,  # the CPU benchmark step is covered in test_cpu_benchmark_step.py
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    store = StateStore(config.state_dir)
    store.save_new(StateStore.new(config))
    installer.resume()
    # LT-APT: timers are released after install work; the durable replay marker
    # is the final host write before the completed state is recorded.
    assert installer.actions.planned[-1].description == "write /var/lib/vbpub/install-v2-complete"
    assert installer.actions.planned[-2].description == "enable apt timers (install finished)"
    assert installer.actions.planned[-3].description == "write /etc/fstab"
    assert any(action.description == f"enable {installer._partition_base}{installer.root_number + 4}" for action in installer.actions.planned)

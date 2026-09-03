from __future__ import annotations

import json
from pathlib import Path

import pytest

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config, ConfigError, load_config
from debian_install_v2.installer import Installer
from debian_install_v2.state import StateStore


BASE = {
    "schema_version": 1,
    "swap_disk_total_gb": 32,
    "swap_file_count": 8,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
}


# --- config validation -------------------------------------------------

def test_vm_swappiness_default_is_50():
    assert Config().vm_swappiness == 50


@pytest.mark.parametrize("value", [-1, 101])
def test_vm_swappiness_out_of_range_rejected(value):
    with pytest.raises(ConfigError, match="vm_swappiness"):
        load_config(raw_json=json.dumps(dict(BASE, vm_swappiness=value)))


def test_apt_auto_upgrade_mode_closed_vocabulary():
    with pytest.raises(ConfigError, match="apt_auto_upgrade_mode"):
        load_config(raw_json=json.dumps(dict(BASE, apt_auto_upgrade_mode="always")))


@pytest.mark.parametrize("value", ["3:00", "25:00", "03:60", "3am"])
def test_reboot_window_time_must_be_hhmm(value):
    with pytest.raises(ConfigError, match="reboot_window_time"):
        load_config(raw_json=json.dumps(dict(BASE, reboot_window_time=value)))


def test_reboot_window_time_accepts_hhmm():
    config = load_config(raw_json=json.dumps(dict(BASE, reboot_window_time="04:30")))
    assert config.reboot_window_time == "04:30"


@pytest.mark.parametrize("value", [0, 8761])
def test_docker_cleanup_max_age_hours_bounds(value):
    with pytest.raises(ConfigError, match="docker_cleanup_max_age_hours"):
        load_config(raw_json=json.dumps(dict(BASE, docker_cleanup_max_age_hours=value)))


def test_swap_discard_defaults_true_and_is_boolean_validated():
    assert Config().swap_discard is True
    with pytest.raises(ConfigError, match="must be a JSON boolean"):
        load_config(raw_json=json.dumps(dict(BASE, swap_discard="yes")))


# --- installer dry-run wiring ------------------------------------------

def install_dry(tmp_path, **overrides):
    defaults = dict(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        telegram_bot_token="",
        telegram_chat_id="",
        never_reboot=True,
        auto_reboot_after_stage1=False,
    )
    defaults.update(overrides)
    config = Config(**defaults)
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    installer.install()
    # install() runs under actions.dry_run, so its own state.save_new() never
    # touches disk (matching HostActions' write-nothing dry-run contract) —
    # seed a real state.json directly so resume()'s plain-file load() below
    # has something to read, exactly like test_install_plan.py's pattern.
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer.resume()
    return installer, actions


def test_notify_helper_written_unconditionally(tmp_path):
    _, actions = install_dry(tmp_path)
    assert "/usr/local/sbin/vbpub-notify" in actions.dry_run_writes


def test_ksm_enabled_by_default_and_can_be_disabled(tmp_path):
    _, actions = install_dry(tmp_path)
    assert "/etc/systemd/system/ksm-config.service" in actions.dry_run_writes
    descriptions = "\n".join(a.description for a in actions.planned)
    assert "enable KSM unit" in descriptions

    _, actions_off = install_dry(tmp_path, run_ksm=False)
    assert "/etc/systemd/system/ksm-config.service" not in actions_off.dry_run_writes


def test_oomd_thresholds_written_and_enabled(tmp_path):
    _, actions = install_dry(tmp_path)
    content = actions.dry_run_writes["/etc/systemd/oomd.conf.d/vbpub.conf"]
    assert "SwapUsedLimit=90%" in content
    descriptions = "\n".join(a.description for a in actions.planned)
    assert "enable systemd-oomd with vbpub thresholds" in descriptions


def test_fstrim_daily_override_written(tmp_path):
    _, actions = install_dry(tmp_path)
    content = actions.dry_run_writes["/etc/systemd/system/fstrim.timer.d/vbpub-daily.conf"]
    assert "OnCalendar=daily" in content


def test_docker_cleanup_timer_uses_configured_age_and_excludes_volumes(tmp_path):
    _, actions = install_dry(tmp_path, docker_cleanup_max_age_hours=72)
    service = actions.dry_run_writes["/etc/systemd/system/vbpub-docker-cleanup.service"]
    assert "until=72h" in service
    assert "volume" not in service.lower()


def test_docker_cleanup_skipped_when_docker_install_disabled(tmp_path):
    _, actions = install_dry(tmp_path, run_docker_install=False)
    assert "/etc/systemd/system/vbpub-docker-cleanup.service" not in actions.dry_run_writes


def test_apt_auto_upgrade_full_mode_includes_all_pinned_origins(tmp_path):
    _, actions = install_dry(tmp_path, apt_auto_upgrade_mode="full")
    content = actions.dry_run_writes["/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades"]
    assert "label=Debian-Security" in content
    assert 'suite=testing' in content
    assert 'suite=unstable' in content
    assert 'Automatic-Reboot "false"' in content


def test_apt_auto_upgrade_security_only_excludes_testing_and_unstable(tmp_path):
    _, actions = install_dry(tmp_path, apt_auto_upgrade_mode="security-only")
    content = actions.dry_run_writes["/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades"]
    assert "label=Debian-Security" in content
    assert "suite=testing" not in content
    assert "suite=unstable" not in content


def test_apt_auto_upgrade_notify_only_never_installs_unattended_upgrades(tmp_path):
    _, actions = install_dry(tmp_path, apt_auto_upgrade_mode="notify-only")
    assert "/usr/local/sbin/vbpub-apt-check" in actions.dry_run_writes
    assert "/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades" not in actions.dry_run_writes
    package_calls = [
        a.argv for a in actions.planned
        if a.argv and a.argv[0] == "/usr/bin/apt-get" and "install" in a.argv
    ]
    assert not any("unattended-upgrades" in argv for argv in package_calls)


def test_auto_reboot_writes_scripts_and_configured_window(tmp_path):
    _, actions = install_dry(tmp_path, reboot_window_time="02:15")
    timer = actions.dry_run_writes["/etc/systemd/system/vbpub-reboot-check.timer"]
    assert "OnCalendar=*-*-* 02:15:00" in timer
    assert "/etc/vbpub/rebooted-for-updates" in actions.dry_run_writes["/etc/systemd/system/vbpub-boot-notify.service"]


def test_auto_reboot_disabled_writes_nothing(tmp_path):
    _, actions = install_dry(tmp_path, run_auto_reboot=False)
    assert "/etc/systemd/system/vbpub-reboot-check.timer" not in actions.dry_run_writes


def test_swap_partitions_get_vbpub_labels(tmp_path):
    _, actions = install_dry(tmp_path)
    label_calls = [
        a.argv for a in actions.planned
        if a.argv and a.argv[0] == "/usr/sbin/mkswap"
    ]
    assert len(label_calls) == 8
    labels = sorted(argv[argv.index("-L") + 1] for argv in label_calls)
    assert labels == [f"vbpub-swap{n}" for n in range(1, 9)]


def test_swap_fstab_entries_carry_discard_once(tmp_path):
    _, actions = install_dry(tmp_path)
    fstab = actions.dry_run_writes["/etc/fstab"]
    swap_lines = [line for line in fstab.splitlines() if " none swap " in line]
    assert len(swap_lines) == 8
    assert all(",discard=once" in line for line in swap_lines)


def test_swappiness_configurable_and_flows_into_sysctl(tmp_path):
    _, actions = install_dry(tmp_path, vm_swappiness=5)
    content = actions.dry_run_writes["/etc/sysctl.d/99-vbpub-swap.conf"]
    assert "vm.swappiness = 5" in content


def test_stage2_unit_module_path_and_workdir_resolve(tmp_path):
    # Regression for DEBIAN-INSTALLv2-REVIEW.md P1#4: `-m scripts.debian_install_v2
    # .bootstrap` with WorkingDirectory=parents[2] can never resolve — the package
    # lives one level up from installer.py's grandparent, not two.
    _, actions = install_dry(tmp_path)
    unit = actions.dry_run_writes["/etc/systemd/system/vbpub-bootstrap-stage2.service"]
    assert "ExecStart=" in unit
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert exec_line.endswith("-m debian_install_v2.bootstrap --action resume")
    workdir_line = next(line for line in unit.splitlines() if line.startswith("WorkingDirectory="))
    workdir = workdir_line.removeprefix("WorkingDirectory=")
    assert workdir.endswith("/scripts/debian-install-v2")
    assert (Path(workdir) / "debian_install_v2" / "bootstrap.py").is_file()

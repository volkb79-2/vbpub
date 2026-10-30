from __future__ import annotations

import json

import pytest

from debian_install_v2.config import ConfigError, SCHEMA_VERSION, load_config
from debian_install_v2.actions import HostActions
from debian_install_v2.installer import Installer
from debian_install_v2.profiles import (
    BUILTIN_PROFILE_DIR,
    list_builtin_profiles,
    load_builtin_profile,
    parse_profile_jsonc,
    strip_jsonc_comments,
)


def test_jsonc_comments_are_ignored_but_comment_markers_in_strings_survive():
    text = '''{
      // profile note
      "schema_version": 1,
      "log_dir": "/var/log/a//b/*c*/x"
      /* trailing comment */
    }'''
    parsed = parse_profile_jsonc(text)
    assert parsed["log_dir"] == "/var/log/a//b/*c*/x"
    assert "/var/log/a//b/*c*/x" in strip_jsonc_comments(text)


def test_jsonc_keeps_strict_json_grammar():
    with pytest.raises(ConfigError, match="invalid JSONC"):
        parse_profile_jsonc('{"schema_version":1,}')
    with pytest.raises(ConfigError, match="unterminated JSONC block comment"):
        parse_profile_jsonc('{"schema_version":1} /* unfinished')


@pytest.mark.parametrize(
    "profile",
    [
        '{"schema_version": 2}',
        '{"schema_version": 1, "telegram_bot_token": "secret"}',
        '{"schema_version": 1, "controller_ssh_pubkey": "ssh-ed25519 AAAA"}',
        '{"schema_version": 1, "ssh_authorized_keys": ["ssh-ed25519 AAAA"]}',
        '{"schema_version": 1, "not_a_config_key": true}',
    ],
)
def test_profile_schema_and_secret_fields_are_validated(profile):
    with pytest.raises(ConfigError):
        parse_profile_jsonc(profile)


def test_builtin_profile_ids_and_effective_config_contract():
    assert list_builtin_profiles() == ("default", "vanilla")
    assert load_builtin_profile("default")["schema_version"] == SCHEMA_VERSION
    default = load_config(raw_json=json.dumps(load_builtin_profile("default")))
    vanilla = load_config(raw_json=json.dumps(load_builtin_profile("vanilla")))

    assert default.run_docker_install is True
    assert default.run_apt_config is True
    assert default.run_zswap_config is True
    assert default.run_cgroup2_config is True
    assert default.cloud_init_cleanup_mode == "retain"

    assert vanilla.run_docker_install is False
    assert vanilla.run_apt_config is False
    assert vanilla.run_zswap_config is False
    assert vanilla.run_cgroup2_config is False
    assert vanilla.run_io_benchmark is False
    assert vanilla.cloud_init_cleanup_mode == "retain"


def test_profile_loader_refuses_unknown_or_symlink_profiles(tmp_path):
    with pytest.raises(ConfigError, match="unknown released profile"):
        load_builtin_profile("does-not-exist", profile_dir=tmp_path)
    (tmp_path / "linked.jsonc").symlink_to(BUILTIN_PROFILE_DIR / "default.jsonc")
    with pytest.raises(ConfigError, match="unknown released profile"):
        load_builtin_profile("linked", profile_dir=tmp_path)


@pytest.mark.parametrize("enabled", [True, False])
def test_zswap_health_gate_matches_the_profile_toggle(monkeypatch, enabled):
    config = load_config(raw_json=json.dumps({
        "run_zswap_config": enabled,
        "swap_partition_count": 1,
    }))
    installer = Installer(config, HostActions(dry_run=True), inspect_host=False)
    installer.root_number = 0
    installer.root_disk = "vda"
    observed = []
    monkeypatch.setattr(installer, "_health_gate_zswap", lambda: observed.append("zswap"))
    monkeypatch.setattr(installer, "_health_gate_iocost", lambda: None)
    monkeypatch.setattr(installer, "_mark_step", lambda *args, **kwargs: None)

    installer._health_gate_swap_devices()

    assert observed == (["zswap"] if enabled else [])


@pytest.mark.parametrize("profile_id,expected", [("default", True), ("vanilla", False)])
def test_stage_two_applies_optional_zswap_and_cgroup_configuration_from_profile(
    monkeypatch, profile_id, expected
):
    config = load_config(raw_json=json.dumps(load_builtin_profile(profile_id)))
    installer = Installer(config, HostActions(dry_run=True), inspect_host=False)
    events = []
    monkeypatch.setattr(installer, "_packages", lambda *args: events.append("packages"))
    monkeypatch.setattr(installer, "_verify_and_apply_root_shrink", lambda: False)
    monkeypatch.setattr(installer, "_swap_partitions_already_planned_in_table", lambda: False)
    monkeypatch.setattr(installer, "_run_io_benchmark", lambda **kwargs: events.append("benchmark"))
    monkeypatch.setattr(installer, "_disable_active_swap", lambda: events.append("swap-disable"))
    monkeypatch.setattr(installer, "_configure_zswap", lambda: events.append("zswap"))
    monkeypatch.setattr(installer, "_configure_iocost", lambda: events.append("iocost"))
    monkeypatch.setattr(installer, "_configure_cgroup2_flags", lambda: events.append("cgroup2"))
    monkeypatch.setattr(installer, "_configure_ksm", lambda: events.append("ksm"))
    monkeypatch.setattr(installer, "_configure_oomd", lambda: events.append("oomd"))
    monkeypatch.setattr(installer, "_configure_fstrim", lambda: events.append("fstrim"))
    monkeypatch.setattr(installer, "_configure_auto_reboot", lambda: events.append("auto-reboot"))
    monkeypatch.setattr(installer, "_apply_known_swap_shape", lambda: events.append("swap-shape"))
    monkeypatch.setattr(installer, "_ensure_disk_transaction_manifest", lambda: events.append("disk-manifest"))
    monkeypatch.setattr(installer, "_grow_root_to_plan", lambda: events.append("root-grow"))
    monkeypatch.setattr(installer, "_activate_swap_partitions", lambda: events.append("swap-active"))
    monkeypatch.setattr(installer, "_health_gate_swap_devices", lambda: events.append("health"))
    monkeypatch.setattr(installer, "_release_apt_timers", lambda: events.append("apt-timers"))
    monkeypatch.setattr(installer, "_cleanup_cloud_init_cache", lambda: events.append("cloud-init"))
    monkeypatch.setattr(installer.state, "save", lambda **kwargs: None)

    installer._stage2()

    assert ("zswap" in events) is expected
    assert ("cgroup2" in events) is expected
    assert {
        "packages", "swap-disable", "swap-shape", "disk-manifest", "root-grow",
        "swap-active", "health", "apt-timers", "cloud-init",
    } <= set(events)
    assert events.index("swap-disable") < events.index("swap-shape")
    assert events.index("swap-shape") < events.index("root-grow") < events.index("swap-active")

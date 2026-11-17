"""daemon.json builder.gc: size cap for Docker's embedded builder cache (Docker 29 policy keys)."""
from __future__ import annotations

import json

import pytest

from debian_install_v2.config import Config, ConfigError, load_config, validate_config
from debian_install_v2.installer import InstallerError
from debian_install_v2.tests.test_r1_remaining import _patch_daemon_json_exists, make_installer


def _written(installer, rerun: bool = False):
    files = installer.actions.files if rerun else installer.actions.dry_run_writes
    return json.loads(files["/etc/docker/daemon.json"])


def test_defaults_are_50_and_10_gb():
    config = Config()
    assert (config.docker_builder_gc_max_used_gb, config.docker_builder_gc_reserved_gb) == (50, 10)


def test_default_renders_docker29_policy_keys_and_not_the_legacy_key(tmp_path):
    installer = make_installer(tmp_path)
    installer._configure_docker_daemon()
    gc = _written(installer)["builder"]["gc"]
    assert gc == {"enabled": True, "defaultMaxUsedSpace": "50GB", "defaultReservedSpace": "10GB"}
    assert "defaultKeepStorage" not in gc


def test_custom_values_are_rendered(tmp_path):
    installer = make_installer(tmp_path, docker_builder_gc_max_used_gb=120, docker_builder_gc_reserved_gb=30)
    installer._configure_docker_daemon()
    gc = _written(installer)["builder"]["gc"]
    assert (gc["defaultMaxUsedSpace"], gc["defaultReservedSpace"]) == ("120GB", "30GB")


def test_hand_added_gc_keys_and_other_builder_keys_survive_a_rerun(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False)
    _patch_daemon_json_exists(monkeypatch, {
        "builder": {"entitlements": {"network-host": True}, "gc": {"policy": [{"all": True}], "defaultMaxUsedSpace": "1GB"}},
    })
    installer._configure_docker_daemon()
    builder = _written(installer, rerun=True)["builder"]
    assert builder["entitlements"] == {"network-host": True}
    assert builder["gc"]["policy"] == [{"all": True}]
    assert builder["gc"]["defaultMaxUsedSpace"] == "50GB"


def test_zero_removes_only_the_owned_size_keys(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False, docker_builder_gc_max_used_gb=0, docker_builder_gc_reserved_gb=0)
    _patch_daemon_json_exists(monkeypatch, {
        "builder": {"gc": {"enabled": True, "defaultMaxUsedSpace": "50GB", "defaultReservedSpace": "10GB", "policy": [{"all": True}]}},
    })
    installer._configure_docker_daemon()
    gc = _written(installer, rerun=True)["builder"]["gc"]
    assert "defaultMaxUsedSpace" not in gc and "defaultReservedSpace" not in gc
    assert gc["policy"] == [{"all": True}]


def test_zero_drops_the_empty_builder_block(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False, docker_builder_gc_max_used_gb=0, docker_builder_gc_reserved_gb=0)
    _patch_daemon_json_exists(monkeypatch, {
        "builder": {"gc": {"enabled": True, "defaultMaxUsedSpace": "50GB", "defaultReservedSpace": "10GB"}},
    })
    installer._configure_docker_daemon()
    assert "builder" not in _written(installer, rerun=True)


def test_zero_on_a_fresh_host_writes_no_builder_key(tmp_path):
    installer = make_installer(tmp_path, docker_builder_gc_max_used_gb=0, docker_builder_gc_reserved_gb=0)
    installer._configure_docker_daemon()
    assert "builder" not in _written(installer)


def test_non_object_builder_block_is_refused(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, dry_run=False)
    _patch_daemon_json_exists(monkeypatch, {"builder": "oops"})
    with pytest.raises(InstallerError, match="builder"):
        installer._configure_docker_daemon()


def test_loads_from_config_json():
    config = load_config(raw_json='{"docker_builder_gc_max_used_gb": 80, "docker_builder_gc_reserved_gb": 5}')
    assert (config.docker_builder_gc_max_used_gb, config.docker_builder_gc_reserved_gb) == (80, 5)


@pytest.mark.parametrize(
    "values, message",
    [
        ({"docker_builder_gc_max_used_gb": -1}, "negative"),
        ({"docker_builder_gc_reserved_gb": -1}, "negative"),
        ({"docker_builder_gc_max_used_gb": 5, "docker_builder_gc_reserved_gb": 10}, "must not exceed"),
        ({"docker_builder_gc_max_used_gb": "50"}, "integer"),
        ({"docker_builder_gc_max_used_gb": True}, "integer"),
    ],
)
def test_invalid_values_are_rejected(values, message):
    with pytest.raises(ConfigError, match=message):
        validate_config(Config(**values))


def test_zero_ceiling_allows_any_reserved_value():
    validate_config(Config(docker_builder_gc_max_used_gb=0, docker_builder_gc_reserved_gb=10))

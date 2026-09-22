from __future__ import annotations

import json

import pytest

from debian_install_v2.config import ConfigError, load_config

BASE = {
    "schema_version": 1,
    "fresh_install": True,
    "swap_disk_total_gb": 32,
    "swap_file_count": 8,
    "telegram_bot_token": "123:test",
    "telegram_chat_id": "123123",
}


def test_known_shape_loads():
    config = load_config(raw_json='{"schema_version":1,"swap_disk_total_gb":32,"swap_file_count":8}')
    assert (config.swap_disk_total_gb, config.swap_file_count) == (32, 8)


@pytest.mark.parametrize("name", ["SWAP_ARCH", "SWAP_TOTAL_GB", "SWAP_FILES", "USE_PARTITION"])
def test_obsolete_v1_names_are_rejected(name):
    data = dict(BASE)
    data[name] = 8 if name.endswith("_GB") or name == "SWAP_FILES" else True
    with pytest.raises(ConfigError, match="obsolete v1 setting"):
        load_config(raw_json=json.dumps(data))


def test_unknown_keys_are_rejected():
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(raw_json='{"not_a_setting":true}')


def test_telegram_pair_is_required_together():
    with pytest.raises(ConfigError, match="supplied together"):
        load_config(raw_json='{"telegram_bot_token":"x"}')


def test_non_fresh_install_refused():
    data = dict(BASE, fresh_install=False)
    with pytest.raises(ConfigError, match="fresh_install=true"):
        load_config(raw_json=json.dumps(data))


def test_closed_vocabularies_are_validated():
    for key, value in (
        ("zswap_compressor", "brotli"),
        ("zswap_zpool", "slab"),
    ):
        data = dict(BASE, **{key: value})
        with pytest.raises(ConfigError):
            load_config(raw_json=json.dumps(data))


@pytest.mark.parametrize("key", [
    "zswap_compressor", "zswap_zpool", "apt_auto_upgrade_mode", "credential_mode",
])
def test_unhashable_closed_vocabulary_values_are_reported_as_config_errors(key):
    with pytest.raises(ConfigError, match=key):
        load_config(raw_json=json.dumps(dict(BASE, **{key: []})))


@pytest.mark.parametrize("key", ["log_dir", "state_dir", "stage2_output"])
def test_filesystem_paths_reject_nul(key):
    with pytest.raises(ConfigError, match="NUL"):
        load_config(raw_json=json.dumps(dict(BASE, **{key: "/tmp/invalid\x00path"})))


def test_config_file_path_rejects_nul():
    with pytest.raises(ConfigError, match="configuration path.*NUL"):
        load_config(path="/tmp/invalid\x00config.json")


@pytest.mark.parametrize("newline", ["\n", "\r", "\x00"])
def test_controller_ssh_key_must_be_a_single_authorized_keys_line(newline):
    with pytest.raises(ConfigError, match="one authorized_keys line"):
        load_config(
            raw_json=json.dumps(
                {"controller_ssh_pubkey": f"ssh-ed25519 AAAA operator{newline}extra"}
            )
        )


def test_unsupported_minimal_v2_surface_is_refused():
    data = dict(BASE, run_ssh_setup=True)
    with pytest.raises(ConfigError, match="not part of minimal v2"):
        load_config(raw_json=json.dumps(data))

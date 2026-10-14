from __future__ import annotations

import base64
import json

import pytest

from debian_install_v2.config import (
    CLOUD_INIT_CLEANUP_MODES,
    ConfigError,
    load_config,
)

BASE = {
    "schema_version": 1,
    "fresh_install": True,
    "swap_disk_total_gb": 32,
    "swap_partition_count": 4,
    "telegram_bot_token": "123:test",
    "telegram_chat_id": "123123",
}
VALID_ED25519 = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIcesQXRRI6R1benlU3hpLX712Iokf4htzSKpuk25Q2n "
    "fixture@example.test"
)


def _ed25519_key(public_bytes: bytes, comment: str) -> str:
    key_type = b"ssh-ed25519"
    blob = len(key_type).to_bytes(4, "big") + key_type + len(public_bytes).to_bytes(4, "big") + public_bytes
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}"


def test_known_shape_loads():
    config = load_config(raw_json='{"schema_version":1,"swap_disk_total_gb":32,"swap_file_count":8}')
    assert (config.swap_disk_total_gb, config.swap_partition_count) == (32, 8)
    assert config.cloud_init_cleanup_mode == "retain"


def test_disk_layout_defaults_are_configurable_and_automatic():
    config = load_config(raw_json='{"schema_version":1}')
    assert config.root_resize_policy == "resize"
    assert config.swap_policy == "on"
    assert config.swap_disk_total_gb is None
    assert config.swap_partition_count == 4


@pytest.mark.parametrize("policy", ["resize", "untouched", "grow-only"])
def test_root_resize_policy_accepts_each_documented_value(policy):
    assert load_config(raw_json=json.dumps({"root_resize_policy": policy})).root_resize_policy == policy


@pytest.mark.parametrize("policy", ["on", "off", "untouched"])
def test_swap_policy_accepts_each_documented_value(policy):
    assert load_config(raw_json=json.dumps({"swap_policy": policy})).swap_policy == policy


def test_disk_policy_rejects_unknown_values():
    with pytest.raises(ConfigError, match="root_resize_policy"):
        load_config(raw_json='{"root_resize_policy":"shrink-whatever"}')
    with pytest.raises(ConfigError, match="swap_policy"):
        load_config(raw_json='{"swap_policy":"maybe"}')


def test_swap_partition_count_alias_conflicts_are_refused():
    with pytest.raises(ConfigError, match="conflict"):
        load_config(raw_json='{"swap_file_count":8,"swap_partition_count":4}')


@pytest.mark.parametrize("mode", CLOUD_INIT_CLEANUP_MODES)
def test_cloud_init_cleanup_mode_accepts_each_documented_value(mode):
    config = load_config(raw_json=json.dumps({"cloud_init_cleanup_mode": mode}))
    assert config.cloud_init_cleanup_mode == mode


def test_cloud_init_cleanup_mode_rejects_unknown_values():
    with pytest.raises(ConfigError, match="cloud_init_cleanup_mode must be one of"):
        load_config(raw_json='{"cloud_init_cleanup_mode":"configs-all"}')


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
    ):
        data = dict(BASE, **{key: value})
        with pytest.raises(ConfigError):
            load_config(raw_json=json.dumps(data))


@pytest.mark.parametrize("key", [
    "zswap_compressor", "apt_auto_upgrade_mode", "credential_mode",
    "cloud_init_cleanup_mode",
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


def test_persistent_ssh_keys_accept_multiple_valid_lines_and_preserve_comments():
    other = _ed25519_key(bytes(range(32)), "backup@laptop")
    # Same public key with a different comment is duplicate key material.
    with pytest.raises(ConfigError, match="duplicates an earlier"):
        load_config(raw_json=json.dumps({
            "ssh_authorized_keys": [
                VALID_ED25519,
                VALID_ED25519.replace("fixture@example.test", "backup@laptop"),
            ]
        }))
    loaded = load_config(raw_json=json.dumps({"ssh_authorized_keys": [VALID_ED25519, other]}))
    assert loaded.ssh_authorized_keys == [VALID_ED25519, other]


@pytest.mark.parametrize(
    "line",
    [
        "ssh-ed25519 AAAA fixture@example.test",
        "ssh-rsa not-base64 fixture@example.test",
        "ssh-rsa AAAAC3NzaC1lZDI1NTE5AAAAIIcesQXRRI6R1benlU3hpLX712Iokf4htzSKpuk25Q2n fixture@example.test",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIcesQXRRI6R1benlU3hpLX712Iokf4htzSKpuk25Q2n fixture@example.test\nextra",
    ],
)
def test_persistent_ssh_keys_reject_malformed_or_mismatched_lines(line):
    with pytest.raises(ConfigError, match="ssh_authorized_keys"):
        load_config(raw_json=json.dumps({"ssh_authorized_keys": [line]}))


def test_persistent_ssh_keys_require_a_bounded_array():
    with pytest.raises(ConfigError, match="JSON array"):
        load_config(raw_json='{"ssh_authorized_keys":"ssh-ed25519 AAAA"}')
    with pytest.raises(ConfigError, match="at most"):
        load_config(raw_json=json.dumps({"ssh_authorized_keys": [VALID_ED25519] * 65}))


def test_unsupported_minimal_v2_surface_is_refused():
    data = dict(BASE, run_ssh_setup=True)
    with pytest.raises(ConfigError, match="not part of minimal v2"):
        load_config(raw_json=json.dumps(data))

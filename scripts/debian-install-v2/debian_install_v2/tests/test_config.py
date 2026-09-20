from __future__ import annotations

import json
import pytest

from debian_install_v2.config import Config, ConfigError, load_config


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


def test_mattermost_webhook_is_valid_configuration():
    config = load_config(raw_json=json.dumps({
        "notify_backend": "mattermost",
        "mattermost_webhook_url": "https://mattermost.example.test/hooks/secret",
    }))
    assert config.notify_backend == "mattermost"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"notify_backend": "unknown"}, "notify_backend"),
        ({"notify_backend": "mattermost"}, "requires mattermost_webhook_url"),
        ({"notify_backend": "mattermost", "mattermost_webhook_url": "http://example.test/hooks/x"}, "https://"),
        ({"notify_backend": "mattermost", "mattermost_webhook_url": "https://example.test/hooks/x", "telegram_bot_token": "x", "telegram_chat_id": "y"}, "Telegram"),
        ({"notify_backend": "none", "telegram_bot_token": "x", "telegram_chat_id": "y"}, "cannot have"),
    ],
)
def test_notification_backend_is_strictly_validated(data, message):
    with pytest.raises(ConfigError, match=message):
        load_config(raw_json=json.dumps(data))


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


def test_unsupported_minimal_v2_surface_is_refused():
    data = dict(BASE, run_ssh_setup=True)
    with pytest.raises(ConfigError, match="not part of minimal v2"):
        load_config(raw_json=json.dumps(data))

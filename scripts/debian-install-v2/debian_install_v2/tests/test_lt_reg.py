"""LT-REG: regressions that e3cd117c1 ("adopt cli-extended") silently introduced.

One test group per repaired item; see LT-PREP-REPORT.md "LT-REG".
"""
from __future__ import annotations

import json
import logging

import pytest

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config, ConfigError, load_config, validate_config
from debian_install_v2.installer import Installer
from debian_install_v2.state import StateStore
from debian_install_v2.tests.test_mattermost_notifications import HOOK, _installer, _live

TG = {"telegram_bot_token": "123456789:" + "A" * 35, "telegram_chat_id": "42"}
CRED_DIRS = ("/etc/vbpub/credentials",)
ALL_CREDS = ("telegram_bot_token", "telegram_chat_id", "mattermost_webhook_url")


def _dry_install(tmp_path, **overrides):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        never_reboot=True, auto_reboot_after_stage1=False, **overrides,
    )
    actions = HostActions(dry_run=True)
    Installer(config, actions).install()
    return config, actions


def _removed(config, actions):
    return {
        (directory, name)
        for directory in ("/etc/vbpub/credentials", f"{config.state_dir}/credentials")
        for name in ALL_CREDS
        if f"{directory}/{name}" in actions.dry_run_removals
    }


# 1. stale backend credentials -------------------------------------------------

def test_none_backend_removes_every_stale_credential_file(tmp_path):
    config, actions = _dry_install(tmp_path, notify_backend="none")
    expected = {
        (d, n) for d in ("/etc/vbpub/credentials", f"{config.state_dir}/credentials") for n in ALL_CREDS
    }
    assert _removed(config, actions) == expected
    assert not any("/credentials/" in path for path in actions.dry_run_writes)


def test_mattermost_backend_removes_only_the_telegram_files(tmp_path):
    config, actions = _dry_install(tmp_path, mattermost_webhook_url=HOOK)
    removed_names = {name for _, name in _removed(config, actions)}
    assert removed_names == {"telegram_bot_token", "telegram_chat_id"}
    assert "/etc/vbpub/credentials/mattermost_webhook_url" not in actions.dry_run_removals
    assert "/etc/vbpub/credentials/mattermost_webhook_url" in actions.dry_run_writes


def test_telegram_backend_removes_only_the_webhook_file(tmp_path):
    config, actions = _dry_install(tmp_path, **TG)
    assert {name for _, name in _removed(config, actions)} == {"mattermost_webhook_url"}
    assert "/etc/vbpub/credentials/telegram_bot_token" in actions.dry_run_writes


def test_remove_file_really_deletes_and_tolerates_missing(tmp_path):
    victim = tmp_path / "mattermost_webhook_url"
    victim.write_text("secret")
    actions = HostActions()
    actions.remove_file(str(victim))
    actions.remove_file(str(victim))  # already gone: fine
    assert not victim.exists()
    with pytest.raises(Exception, match="absolute"):
        actions.remove_file("relative/path")


# 2. webhook scheme ------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://mm.example.test/hooks/x", "HTTP://mm.example.test/hooks/x"])
def test_http_webhook_is_rejected_by_validation_and_load_config(url):
    with pytest.raises(ConfigError, match="https://") as info:
        validate_config(Config(mattermost_webhook_url=url))
    assert url not in str(info.value)
    with pytest.raises(ConfigError, match="https://"):
        load_config(raw_json=json.dumps({"schema_version": 1, "mattermost_webhook_url": url}))


# 3. cross-credential errors ---------------------------------------------------

def test_explicit_mattermost_with_telegram_credentials_is_an_error():
    with pytest.raises(ConfigError, match="Telegram credentials require notify_backend=telegram"):
        validate_config(Config(notify_backend="mattermost", mattermost_webhook_url=HOOK, **TG))
    with pytest.raises(ConfigError, match="Telegram credentials require notify_backend=telegram"):
        validate_config(Config(notify_backend="mattermost", **TG))


def test_explicit_telegram_with_a_webhook_is_an_error():
    with pytest.raises(ConfigError, match="mattermost_webhook_url requires notify_backend=mattermost"):
        validate_config(Config(notify_backend="telegram", mattermost_webhook_url=HOOK))


def test_none_with_any_credential_is_an_error():
    with pytest.raises(ConfigError, match="notify_backend=none cannot have notification credentials"):
        validate_config(Config(notify_backend="none", mattermost_webhook_url=HOOK))
    with pytest.raises(ConfigError, match="notify_backend=none cannot have notification credentials"):
        validate_config(Config(notify_backend="none", **TG))


def test_unset_backend_with_both_credentials_still_errors():
    with pytest.raises(ConfigError, match="both Telegram and Mattermost"):
        validate_config(Config(mattermost_webhook_url=HOOK, **TG))


# 4. BaseException handling ----------------------------------------------------

@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_stage1_interrupt_records_failed_notifies_and_reraises(tmp_path, monkeypatch, exc_type):
    installer = _installer(tmp_path)
    posts = _live(installer, monkeypatch)
    monkeypatch.setattr(installer, "_initial_event_text", lambda: "starting")
    monkeypatch.setattr(installer, "_stage1", lambda: (_ for _ in ()).throw(exc_type("stop")))
    with pytest.raises(exc_type):
        installer.install()
    state = installer.state.load()
    assert state["status"] == "failed" and state["phase"] == "stage1"
    assert any("install FAILED" in text for text in posts)


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_stage2_interrupt_records_failed_notifies_and_reraises(tmp_path, monkeypatch, exc_type):
    installer = _installer(tmp_path)
    posts = _live(installer, monkeypatch)
    monkeypatch.setattr(installer, "_stage2", lambda: (_ for _ in ()).throw(exc_type("stop")))
    with pytest.raises(exc_type):
        installer.resume()
    state = installer.state.load()
    assert state["status"] == "failed" and state["phase"] == "stage2"
    assert any("install FAILED" in text for text in posts)


# 5. resume tolerates unknown keys ---------------------------------------------

def test_resume_ignores_unknown_state_keys_with_one_warning(tmp_path, monkeypatch, caplog):
    installer = _installer(tmp_path)
    path = installer.state.path
    manifest = json.loads(path.read_text())
    manifest["config"]["retired_future_option"] = True
    manifest["config"]["another_unknown"] = "x"
    path.write_text(json.dumps(manifest))
    _live(installer, monkeypatch)
    monkeypatch.setattr(installer, "_stage2", lambda: None)
    monkeypatch.setattr(installer, "_remove_controller_ssh_key", lambda: None)
    monkeypatch.setattr("debian_install_v2.installer.collect_host_facts", lambda i: {})
    monkeypatch.setattr("debian_install_v2.installer.format_facts_html", lambda f: "")
    with caplog.at_level(logging.WARNING, logger="debian_install_v2.config"):
        installer.resume()
    assert installer.state.load()["status"] == "success"
    warnings = [r for r in caplog.records if "unknown key" in r.getMessage()]
    assert len(warnings) == 1
    assert "another_unknown" in warnings[0].getMessage()
    assert "retired_future_option" in warnings[0].getMessage()


def test_operator_config_stays_strict_about_unknown_keys():
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(raw_json=json.dumps({"schema_version": 1, "retired_future_option": True}))


# 6. webhook never in state ----------------------------------------------------

def test_state_manifest_never_serializes_mattermost_webhook(tmp_path):
    config = Config(
        state_dir=str(tmp_path), notify_backend="mattermost", mattermost_webhook_url=HOOK,
    )
    store = StateStore(str(tmp_path))
    store.save_new(StateStore.new(config))
    raw = (tmp_path / "state.json").read_text(encoding="utf-8")
    manifest = json.loads(raw)
    assert "mattermost_webhook_url" not in manifest["config"]
    assert "secretid" not in raw and "mattermost.example.test" not in raw
    # also when the secret arrives through the save path of an existing dict
    state = StateStore.new(config)
    state["config"]["mattermost_webhook_url"] = HOOK
    store.save_new(state)
    assert "secretid" not in (tmp_path / "state.json").read_text(encoding="utf-8")


# 8. README sections -----------------------------------------------------------

@pytest.mark.parametrize("needle", [
    "docker volume prune",
    "r1-vm-real-commit",
    "Pin priorities",
    "vbpub-notify",
    "/root/custom_script.output2",
    "SWAP_ARCH",
    "VBPUB_CONFIG_EXTRA_JSON",
])
def test_readme_keeps_the_load_bearing_sections(needle):
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert needle in readme

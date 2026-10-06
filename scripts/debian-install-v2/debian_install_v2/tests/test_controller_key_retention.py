"""LT-KEY: controller_ssh_key_after_install (remove|retain) + folded-in items."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from debian_install_v2.config import Config, ConfigError, load_config, validate_config
from debian_install_v2.customscript import build_customscript_bundle
from debian_install_v2.tests import test_mattermost_notifications as mm
from debian_install_v2.tests import test_r1_coverage as r1
from debian_install_v2.tests.test_bootstrap_remote import load_module
from debian_install_v2.wizard import wizard_field_names

PUBKEY = "ssh-ed25519 AAAAephemeral vbpub-controller-ephemeral"
OPERATOR = "ssh-ed25519 AAAAoperator operator@laptop"


# --- config: the field exists (regression guard for e3cd117c1) ----------------

def test_retain_controller_ssh_key_field_exists_and_defaults_off():
    from dataclasses import fields

    by_name = {f.name: f for f in fields(Config)}
    assert "retain_controller_ssh_key" in by_name
    assert Config().retain_controller_ssh_key is False


def test_retain_loads_from_json_and_bundle():
    assert load_config(raw_json='{"retain_controller_ssh_key": true}').retain_controller_ssh_key is True
    assert build_customscript_bundle(Config(retain_controller_ssh_key=True))["config"]["retain_controller_ssh_key"] is True


@pytest.mark.parametrize("bad", ["yes", 1, None, "true"])
def test_retain_must_be_a_json_boolean(bad):
    with pytest.raises(ConfigError, match="retain_controller_ssh_key"):
        validate_config(Config(retain_controller_ssh_key=bad))


# --- env var + customScript paths --------------------------------------------

def test_env_is_mapped_in_bootstrap_remote(monkeypatch):
    mod = load_module()
    assert mod._BOOL_FIELDS["RETAIN_CONTROLLER_SSH_KEY"] == "retain_controller_ssh_key"
    monkeypatch.setenv("RETAIN_CONTROLLER_SSH_KEY", "yes")
    assert mod.build_config() == {"retain_controller_ssh_key": True}
    monkeypatch.setenv("RETAIN_CONTROLLER_SSH_KEY", "no")
    assert mod.build_config() == {"retain_controller_ssh_key": False}


def test_env_value_is_strictly_validated(monkeypatch):
    mod = load_module()
    monkeypatch.setenv("RETAIN_CONTROLLER_SSH_KEY", "auto")
    with pytest.raises(SystemExit, match="RETAIN_CONTROLLER_SSH_KEY"):
        mod.build_config()


def test_customscript_carries_retain_through_quoted_config_json():
    bundle = build_customscript_bundle(
        Config(retain_controller_ssh_key=True), controller_ssh_placeholder=True
    )
    tokens = shlex.split(bundle["customScript"])
    extra = next(t for t in tokens if t.startswith("VBPUB_CONFIG_EXTRA_JSON="))
    assert json.loads(extra.split("=", 1)[1])["retain_controller_ssh_key"] is True


def test_customscript_default_bundle_says_no_retain():
    assert build_customscript_bundle(Config())["config"]["retain_controller_ssh_key"] is False


# --- installer: the success path honours retain --------------------------------

def _resume_ok(tmp_path, monkeypatch, retain):
    installer = mm._installer(tmp_path, controller_ssh_pubkey=PUBKEY, retain_controller_ssh_key=retain)
    posts = mm._live(installer, monkeypatch)
    removed = []
    monkeypatch.setattr(installer, "_stage1", lambda: installer._reboot())
    monkeypatch.setattr(installer, "_stage2", lambda: None)
    monkeypatch.setattr(installer, "_remove_controller_ssh_key", lambda: removed.append(1))
    monkeypatch.setattr("debian_install_v2.installer.collect_host_facts", lambda i: {})
    monkeypatch.setattr("debian_install_v2.installer.format_facts_html", lambda f: "")
    telegram = []
    original = installer._notify
    monkeypatch.setattr(
        installer, "_notify",
        lambda message, **kw: (telegram.append(message), original(message, **kw))[1],
    )
    installer.install()
    installer.resume()
    return installer, removed, posts, telegram


def test_retain_skips_removal_and_marks_step_retained(tmp_path, monkeypatch):
    installer, removed, _, _ = _resume_ok(tmp_path, monkeypatch, True)
    assert removed == []
    step = installer.state.load()["steps"]["controller_ssh_key_retained"]
    assert step["status"] == "success"
    assert step["detail"] == "configured to retain after successful stage2"


def test_default_still_removes_on_success(tmp_path, monkeypatch):
    installer, removed, _, _ = _resume_ok(tmp_path, monkeypatch, False)
    assert removed == [1]
    assert "controller_ssh_key_retained" not in installer.state.load().get("steps", {})


def test_failure_never_removes_key_in_either_mode(tmp_path, monkeypatch):
    for retain in (False, True):
        installer = mm._installer(tmp_path / str(retain), controller_ssh_pubkey=PUBKEY, retain_controller_ssh_key=retain)
        calls = []
        monkeypatch.setattr(installer, "_remove_controller_ssh_key", lambda: calls.append(1))
        monkeypatch.setattr(installer, "_stage1", lambda i=installer: i._reboot())
        monkeypatch.setattr(installer, "_stage2", lambda: (_ for _ in ()).throw(RuntimeError("stage2 broke")))
        mm._live(installer, monkeypatch)
        installer.install()
        with pytest.raises(RuntimeError):
            installer.resume()
        assert calls == []


def test_remove_helper_still_strips_only_the_controller_line(tmp_path, monkeypatch):
    installer, actions = r1._make_with_pubkey(tmp_path, PUBKEY)
    monkeypatch.setattr(Path, "is_file", r1._fake_is_file(True))
    monkeypatch.setattr(Path, "read_text", r1._fake_read_text(f"{OPERATOR}\n{PUBKEY}\n"))
    installer._remove_controller_ssh_key()
    content = actions.files[r1._AUTHORIZED_KEYS].decode()
    assert OPERATOR in content and PUBKEY not in content


# --- install-complete message ---------------------------------------------------

def test_install_complete_message_says_retained_on_host(tmp_path, monkeypatch):
    _, _, posts, telegram = _resume_ok(tmp_path, monkeypatch, True)
    complete = [p for p in posts if "install complete" in p]
    assert len(complete) == 1 and "controller key retained on host" in complete[0]
    assert any("Install complete" in m and "Controller key retained on host." in m for m in telegram)


def test_install_complete_message_silent_when_removed(tmp_path, monkeypatch):
    _, _, posts, telegram = _resume_ok(tmp_path, monkeypatch, False)
    complete = [p for p in posts if "install complete" in p]
    assert len(complete) == 1 and "retained" not in complete[0]
    assert not any("retained" in m for m in telegram)


# --- wizard ----------------------------------------------------------------------

def test_wizard_offers_the_key_policy_and_pools():
    names = wizard_field_names()
    assert {"retain_controller_ssh_key", "docker_default_address_pools"} <= names

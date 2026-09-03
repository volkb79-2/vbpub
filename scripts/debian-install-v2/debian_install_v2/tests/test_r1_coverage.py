from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

import pytest

from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.bootstrap import build_parser, main
from debian_install_v2.config import Config, ConfigError, load_config
from debian_install_v2.installer import Installer
from debian_install_v2.state import StateError, StateStore


BASE_CONFIG = {
    "schema_version": 1,
    "fresh_install": True,
    "swap_disk_total_gb": 32,
    "swap_file_count": 8,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "auto_reboot_after_stage1": False,
    "never_reboot": True,
    "credential_mode": "systemd",
}


def write_config(tmp_path, **overrides):
    data = {**BASE_CONFIG,
            "state_dir": str(tmp_path / "state"),
            "log_dir": str(tmp_path / "logs")}
    data.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return str(path)


# --- bootstrap.py CLI ---


def test_cli_install_dry_run(tmp_path, capsys):
    cfg = write_config(tmp_path)
    rc = main(["--action", "install", "--config", cfg, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    plan = json.loads(out)
    assert plan["result"] == "planned"


def test_cli_status_requires_state(tmp_path):
    cfg = write_config(tmp_path)
    rc = main(["--action", "status", "--config", cfg, "--dry-run"])
    assert rc == 1


def test_cli_missing_config_refused():
    rc = main(["--action", "install"])
    assert rc == 2


def test_cli_resume_uses_state_config(tmp_path, monkeypatch):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    installer = Installer(config, HostActions(dry_run=True))
    StateStore(config.state_dir).save_new(StateStore.new(config))
    monkeypatch.setenv("VBPUB_STATE_DIR", config.state_dir)
    assert main(["--action", "resume", "--dry-run"]) == 0


def test_cli_verify_after_manifest(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    state_dir = Path(config.state_dir)
    backup_dir = state_dir / "backups"
    backup_dir.mkdir(parents=True)
    backup = backup_dir / "ptable.sfdisk"
    backup.write_text("label: gpt\n")
    checksum = backup_dir / "ptable.sfdisk.sha256"
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  ptable.sfdisk\n")
    (state_dir / "disk-transaction.json").write_text(json.dumps({
        "backup": str(backup), "checksum": str(checksum)
    }))
    assert main(["--action", "verify", "--config", write_config(tmp_path), "--dry-run"]) == 0


def test_cli_disable_and_show_plan(tmp_path):
    cfg = write_config(tmp_path)
    assert main(["--action", "disable-stage2", "--config", cfg, "--dry-run"]) == 0
    assert main(["--action", "show-plan", "--config", cfg, "--dry-run"]) == 0


def test_module_invocation_help():
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "debian_install_v2.bootstrap", "--help"],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--action" in result.stdout


def test_cli_resume_without_env():
    rc = main(["--action", "resume"])
    assert rc == 2


# --- config validation edge cases ---


def test_config_rejects_bad_schema_version(tmp_path):
    data = dict(BASE_CONFIG, schema_version=99)
    with pytest.raises(ConfigError, match="schema_version"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_non_bool(tmp_path):
    data = dict(BASE_CONFIG, fresh_install="yes")
    with pytest.raises(ConfigError, match="must be a JSON boolean"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_non_int_swap_size():
    data = dict(BASE_CONFIG, swap_disk_total_gb="32")
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_swap_size_out_of_range():
    data = dict(BASE_CONFIG, swap_disk_total_gb=999999)
    with pytest.raises(ConfigError, match="swap_disk_total_gb"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_file_count_out_of_range():
    data = dict(BASE_CONFIG, swap_file_count=0)
    with pytest.raises(ConfigError, match="swap_file_count"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_bad_pool_percent():
    data = dict(BASE_CONFIG, zswap_pool_percent=200)
    with pytest.raises(ConfigError, match="zswap_pool_percent"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_bad_priority():
    data = dict(BASE_CONFIG, swap_priority=-1)
    with pytest.raises(ConfigError, match="swap_priority"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_relative_path():
    data = dict(BASE_CONFIG, log_dir="relative/path")
    with pytest.raises(ConfigError, match="absolute path"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_non_string_field():
    data = dict(BASE_CONFIG, log_dir=123)
    with pytest.raises(ConfigError, match="must be a string"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_preserve_root_out_of_range():
    data = dict(BASE_CONFIG, preserve_root_size_gb=0)
    with pytest.raises(ConfigError, match="preserve_root_size_gb"):
        load_config(raw_json=json.dumps(data))


def test_config_rejects_bad_credential_mode():
    data = dict(BASE_CONFIG, credential_mode="magic")
    with pytest.raises(ConfigError, match="credential_mode"):
        load_config(raw_json=json.dumps(data))


def test_config_root_storage_requires_var_lib():
    data = dict(BASE_CONFIG, credential_mode="root-storage",
                state_dir="/tmp/state")
    with pytest.raises(ConfigError, match="/var/lib"):
        load_config(raw_json=json.dumps(data))


def test_config_invalid_json():
    with pytest.raises(ConfigError, match="cannot read configuration"):
        load_config(raw_json="{invalid")


def test_config_non_object_root():
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(raw_json="[1,2]")


def test_config_neither_file_nor_json():
    with pytest.raises(ConfigError, match="exactly one"):
        load_config()


def test_config_both_file_and_json(tmp_path):
    cfg = write_config(tmp_path)
    with pytest.raises(ConfigError, match="exactly one"):
        load_config(path=cfg, raw_json="{}")


# --- state error paths ---


def test_state_load_missing(tmp_path):
    store = StateStore(str(tmp_path / "nonexistent"))
    with pytest.raises(StateError, match="does not exist"):
        store.load()


def test_state_load_corrupt(tmp_path):
    d = tmp_path / "state"
    d.mkdir(parents=True)
    (d / "state.json").write_text("{bad")
    store = StateStore(str(d))
    with pytest.raises(StateError, match="cannot load state manifest"):
        store.load()


def test_state_load_bad_schema(tmp_path):
    d = tmp_path / "state"
    d.mkdir(parents=True)
    (d / "state.json").write_text('{"schema_version": 99}')
    store = StateStore(str(d))
    with pytest.raises(StateError, match="unsupported"):
        store.load()


def test_state_save_updates_and_cleanup(tmp_path):
    d = tmp_path / "state"
    d.mkdir(parents=True)
    store = StateStore(str(d))
    store.save_new(StateStore.new(Config(
        state_dir=str(d), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
    )))
    store.save(status="success")
    assert store.load()["status"] == "success"

    bad = d / "state.json"
    bad.unlink()
    bad.mkdir()
    with pytest.raises(OSError):
        store.save_new({"schema_version": 1, "config": {}})
    assert list(d.glob(".state.json.*")) == []


def test_state_cleanup_file_not_found(tmp_path, monkeypatch):
    import debian_install_v2.state as state_module
    d = tmp_path / "state"
    d.mkdir(parents=True)

    def boom(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(os, "replace", boom)
    store = StateStore(str(d))
    # The temp file gets created, os.replace raises OSError, and cleanup
    # unlink is reached; simulate the unlink racing by making it raise
    # FileNotFoundError once.
    orig_unlink = os.unlink
    calls = {"n": 0}
    def flaky_unlink(path):
        calls["n"] += 1
        raise FileNotFoundError
    monkeypatch.setattr(os, "unlink", flaky_unlink)
    with pytest.raises(OSError):
        store.save_new({"schema_version": 1, "config": {}})
    assert calls["n"] >= 1


# --- actions error paths ---


def test_action_empty_argv():
    actions = HostActions(dry_run=True)
    with pytest.raises(ActionError, match="non-empty"):
        actions.run([])


def test_action_shell_refused_dry_run():
    actions = HostActions(dry_run=True)
    with pytest.raises(ActionError, match="shell commands"):
        actions.run(["/bin/sh", "-c", "echo"])


def test_action_systemctl_unknown_op():
    actions = HostActions(dry_run=True)
    with pytest.raises(ActionError, match="not allowlisted"):
        actions.run(["/usr/bin/systemctl", "destroy", "x"])


def test_action_real_execution_success_and_failure(tmp_path):
    actions = HostActions(dry_run=False)
    ok_dir = tmp_path / "ok"
    actions.run(["/usr/bin/mkdir", "-p", str(ok_dir)], "create dir")
    assert ok_dir.is_dir()
    blocker = tmp_path / "blocker"
    blocker.write_text("file")
    with pytest.raises(ActionError, match="failed"):
        actions.run(["/usr/bin/mkdir", "-p", str(blocker / "sub")], "will fail")


def test_action_read_and_copy_and_helpers(tmp_path):
    actions = HostActions(dry_run=False)
    target = tmp_path / "sub"
    assert actions.read(["/usr/bin/mkdir", "-p", str(target)]) == ""
    src = tmp_path / "src.txt"
    src.write_text("content")
    dst = tmp_path / "dst.txt"
    actions.copy_file(str(src), str(dst))
    assert dst.read_text() == "content"
    assert actions.exists(str(tmp_path)) is True
    assert actions.which("python3") is True
    assert actions.which("definitely-not-a-real-binary-xyz") is False


def test_write_file_cleanup_on_failure(tmp_path):
    actions = HostActions(dry_run=False)
    destination = tmp_path / "occupied"
    destination.mkdir()
    with pytest.raises(OSError):
        actions.write_file(str(destination), "content")
    assert list(tmp_path.glob(".occupied.*")) == []


def test_write_file_cleanup_races(tmp_path, monkeypatch):
    actions = HostActions(dry_run=False)
    destination = tmp_path / "occupied"
    destination.mkdir()
    orig_unlink = os.unlink
    def flaky_unlink(path):
        raise FileNotFoundError
    monkeypatch.setattr(os, "unlink", flaky_unlink)
    with pytest.raises(OSError):
        actions.write_file(str(destination), "content")


def test_write_file_real(tmp_path):
    actions = HostActions(dry_run=False)
    target = tmp_path / "test.txt"
    actions.write_file(str(target), "hello\n")
    assert target.read_text() == "hello\n"


def test_write_file_rejects_relative(tmp_path, monkeypatch):
    # cwd isolated to tmp_path: if this guard's `or` were ever mutated to
    # `and` (a real mutation-testing finding, not hypothetical), the write
    # would silently succeed instead of raising -- and without this chdir it
    # would land in the tracked source tree (the suite's own cwd), dirtying
    # the snapshot assay measures rather than merely failing this one test.
    monkeypatch.chdir(tmp_path)
    actions = HostActions(dry_run=False)
    with pytest.raises(ActionError, match="absolute"):
        actions.write_file("relative.txt", "x")
    assert not (tmp_path / "relative.txt").exists()


# --- installer operational commands ---


def _make_with_state(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer


def test_status_returns_state_and_logs(tmp_path):
    installer = _make_with_state(tmp_path)
    result = installer.status()
    assert result["schema_version"] == 1
    assert result["dry_run"] is True


def test_verify_missing_manifest(tmp_path):
    installer = _make_with_state(tmp_path)
    with pytest.raises(RuntimeError, match="does not exist"):
        installer.verify()


def test_show_plan_returns_partitions(tmp_path):
    installer = _make_with_state(tmp_path)
    plan = installer.show_plan()
    assert len(plan["swap_partitions"]) == 8
    assert plan["release"] == "trixie"


def test_disable_stage2_sets_state(tmp_path):
    installer = _make_with_state(tmp_path)
    installer.disable_stage2()
    assert any(
        action.description == "disable stage2 unit"
        for action in installer.actions.planned
    )


def test_resume_loads_credentials_and_thread(tmp_path, monkeypatch):
    from debian_install_v2.tests.test_fake_integration import FakeHostActions
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    state_dir = Path(config.state_dir)
    cred_dir = state_dir / "credentials"
    cred_dir.mkdir(parents=True)
    (cred_dir / "telegram_bot_token").write_text("123:token\n")
    (cred_dir / "telegram_chat_id").write_text("456\n")
    (state_dir / "telegram_thread_id").write_text("789\n")
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    monkeypatch.setattr(installer, "_stage2", lambda: None)
    installer.resume()
    assert installer.config.telegram_bot_token == "123:token"
    assert installer.config.telegram_chat_id == "456"
    state = StateStore(config.state_dir).load()
    assert state["telegram_thread_id"] == "789"
    assert state["status"] == "success"
    assert (state_dir / "stage2_done").exists()


def test_resume_failure_records_state(tmp_path, monkeypatch):
    from debian_install_v2.tests.test_fake_integration import FakeHostActions
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))

    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(installer, "_stage2", boom)
    with pytest.raises(RuntimeError, match="boom"):
        installer.resume()
    state = StateStore(config.state_dir).load()
    assert state["status"] == "failed"
    assert state["last_error"] == "boom"


def test_discover_root_rejects_non_block(tmp_path):
    from debian_install_v2.tests.test_fake_integration import FakeHostActions
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "overlay\n"
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
    )
    with pytest.raises(RuntimeError, match="plain block-device"):
        Installer(config, actions)


def test_discover_root_rejects_malformed(tmp_path):
    from debian_install_v2.tests.test_fake_integration import FakeHostActions
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/mystery\n"
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
    )
    with pytest.raises(RuntimeError, match="cannot derive"):
        Installer(config, actions)


def test_detect_release_unsupported(tmp_path, monkeypatch):
    from debian_install_v2.tests.test_fake_integration import FakeHostActions
    fake_content = 'PRETTY_NAME="Debian GNU/Linux 99"\nVERSION_CODENAME=notreal\n'
    monkeypatch.setattr("pathlib.Path.read_text", lambda self, **kw: fake_content)
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
    )
    with pytest.raises(ConfigError, match="unsupported or undetected"):
        Installer(config, actions)


def test_show_plan_reads_existing_file(tmp_path):
    installer = _make_with_state(tmp_path)
    plan_path = Path(installer.config.state_dir) / "partition-plan.sfdisk"
    plan_path.write_text("custom-plan\n")
    plan = installer.show_plan()
    assert plan["sfdisk_plan"] == "custom-plan\n"


def test_disk_facts_missing_root(tmp_path):
    from debian_install_v2.tests.test_fake_integration import FakeHostActions
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    actions.outputs[("/usr/sbin/blockdev", "--getsize64", "/dev/vda")] = str(512 * 1024 ** 3)
    actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = "label: gpt\ndevice: /dev/vda\n\n"
    installer = Installer(config, actions)
    with pytest.raises(RuntimeError, match="could not parse"):
        installer._disk_facts()


def test_preflight_rejects_unexpected_mounts(tmp_path):
    from debian_install_v2.tests.test_fake_integration import FakeHostActions
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    actions.outputs[("/usr/bin/findmnt", "-rn", "-o", "SOURCE,TARGET,FSTYPE")] = (
        "/dev/vda3 / ext4\n/dev/vda4 /mnt ext4\n"
    )
    installer = Installer(config, actions)
    with pytest.raises(RuntimeError, match="partitions are mounted"):
        installer._preflight_disk_transaction()


def test_preflight_rejects_holders(tmp_path, monkeypatch):
    from debian_install_v2.tests.test_fake_integration import FakeHostActions
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
        auto_reboot_after_stage1=False,
    )
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    actions.outputs[("/usr/bin/findmnt", "-rn", "-o", "SOURCE,TARGET,FSTYPE")] = "/dev/vda3 / ext4\n"
    installer = Installer(config, actions)
    monkeypatch.setattr("pathlib.Path.is_dir", lambda self, **kw: True)
    monkeypatch.setattr("pathlib.Path.iterdir", lambda self, **kw: iter([Path("/fake/dm-0")]))
    with pytest.raises(RuntimeError, match="active holder"):
        installer._preflight_disk_transaction()

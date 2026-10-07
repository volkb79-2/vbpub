"""LT-EARLY (LT-F-r1002-02): every error after config parsing gets the failure handling.

Live finding: a parseable-but-unplannable config (LT-05: preserve_root_size_gb=1)
made the customScript exit 1 with no state.json, no controller key (host
unreachable) and no failure post, because bootstrap._install() called
installer.show_plan() BEFORE the guarded installer.install(). These tests drive
the real CLI entry (`main(["install", ...])`) with the project's host fakes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from debian_install_v2 import bootstrap
from debian_install_v2.actions import PlannedAction
from debian_install_v2.bootstrap import main
from debian_install_v2.config import Config
from debian_install_v2.failure_notify import recently_notified
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.state import StateStore
from debian_install_v2.tests import test_case_b_root_shrink as case_b
from debian_install_v2.tests.test_fake_integration import FakeHostActions
from debian_install_v2.tests.test_mattermost_notifications import HOOK

PUBKEY = "ssh-ed25519 AAAAlt-early-unique vbpub-controller-ephemeral-lt-early"


class _Actions(FakeHostActions):
    """Case B host (50 GiB disk, 45 GiB root); mkdir faked so /root/.ssh is never touched."""

    def __init__(self) -> None:
        super().__init__()
        self.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
        self.outputs[("/usr/sbin/blockdev", "--getsize64", "/dev/vda")] = str(case_b.DISK_GIB * 1024 ** 3)
        self.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = case_b.CASE_B_DUMP
        self.outputs[("/usr/sbin/dumpe2fs", "-h", "/dev/vda3")] = case_b.DUMPE2FS_OUTPUT
        self.outputs[("/usr/sbin/resize2fs", "-P", "/dev/vda3")] = case_b.RESIZE2FS_OUTPUT

    def mkdir(self, path: str) -> None:
        self.planned.append(PlannedAction(("/usr/bin/mkdir", "-p", path), f"create directory {path}", True))


@pytest.fixture
def host(tmp_path, monkeypatch):
    """(actions, posts): the CLI runs against the fake host; webhook posts are captured."""
    actions = _Actions()
    posts: list[str] = []
    monkeypatch.setattr(bootstrap, "HostActions", lambda dry_run=False: actions)
    monkeypatch.setattr(
        "debian_install_v2.installer.post_webhook",
        lambda url, text, **kw: posts.append(text) or True,
    )
    monkeypatch.setattr("debian_install_v2.installer.collect_host_facts", lambda i: {})
    monkeypatch.setattr("debian_install_v2.installer.format_facts_html", lambda f: "")
    return actions, posts


def _write(tmp_path, **overrides) -> str:
    data = {
        "schema_version": 1, "fresh_install": True,
        "swap_disk_total_gb": 32, "swap_file_count": 8,
        "auto_reboot_after_stage1": False, "never_reboot": True,
        "credential_mode": "systemd",
        "state_dir": str(tmp_path / "state"), "log_dir": str(tmp_path / "logs"),
        "mattermost_webhook_url": HOOK, "notify_host_label": "netcup-1",
        "controller_ssh_pubkey": PUBKEY,
    }
    data.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return str(path)


def _state(tmp_path) -> dict:
    return json.loads((tmp_path / "state" / "state.json").read_text())


def _key_written(actions) -> bool:
    return PUBKEY in actions.files.get("/root/.ssh/authorized_keys", b"").decode()


def _assert_failed_with_cause(tmp_path, actions, posts, cause):
    state = _state(tmp_path)
    assert state["status"] == "failed" and state["phase"] == "stage1"
    assert cause in state["last_error"]
    assert _key_written(actions), "controller key must be on the host for diagnosis"
    assert len(posts) == 1, posts
    assert cause in posts[0] and "install FAILED" in posts[0]
    assert "secretid" not in posts[0]  # no webhook leak
    assert recently_notified(state), "OnFailure notifier dedup mark missing"


# --- the live case ------------------------------------------------------------

def test_lt05_invalid_preserve_root_size_fails_with_state_key_one_post_nonzero(tmp_path, host, capsys):
    actions, posts = host
    cfg = _write(tmp_path, preserve_root_size_gb=1)
    code = main(["install", "--config", cfg, "--yes"])
    assert code != 0
    assert "preserve_root_size_gb" in capsys.readouterr().err
    _assert_failed_with_cause(tmp_path, actions, posts, "preserve_root_size_gb")


def test_lt05_without_a_pubkey_still_records_state_and_posts(tmp_path, host):
    actions, posts = host
    cfg = _write(tmp_path, preserve_root_size_gb=1, controller_ssh_pubkey="")
    assert main(["install", "--config", cfg, "--yes"]) != 0
    assert "/root/.ssh/authorized_keys" not in actions.files
    assert _state(tmp_path)["status"] == "failed"
    assert len(posts) == 1


def test_a_raise_inside_show_plan_is_handled(tmp_path, host, monkeypatch):
    actions, posts = host

    def boom(self):
        raise InstallerError("plan exploded")

    monkeypatch.setattr(Installer, "show_plan", boom)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    _assert_failed_with_cause(tmp_path, actions, posts, "plan exploded")


def test_a_raise_while_inspecting_the_host_is_handled(tmp_path, host, monkeypatch):
    actions, posts = host

    def boom(self):
        raise InstallerError("unsupported or undetected Debian release: 'sid'")

    monkeypatch.setattr(Installer, "_detect_release", boom)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    _assert_failed_with_cause(tmp_path, actions, posts, "undetected Debian release")


def test_an_unexpected_non_domain_exception_in_show_plan_is_handled_too(tmp_path, host, monkeypatch):
    actions, posts = host

    def boom(self):
        raise KeyError("surprise")

    monkeypatch.setattr(Installer, "show_plan", boom)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    _assert_failed_with_cause(tmp_path, actions, posts, "surprise")


# --- exactly one post across the nested guards --------------------------------

def test_a_stage_one_failure_still_posts_exactly_once(tmp_path, host, monkeypatch):
    actions, posts = host
    # Case A host so the plan preview passes and stage one itself fails.
    actions.outputs[("/usr/sbin/blockdev", "--getsize64", "/dev/vda")] = str(512 * 1024 ** 3)

    def boom(self):
        raise InstallerError("stage one broke")

    monkeypatch.setattr(Installer, "_stage1", boom)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    assert len([p for p in posts if "install FAILED" in p]) == 1
    assert _state(tmp_path)["status"] == "failed"


def test_nested_guards_report_one_exception_once_but_a_later_run_again(tmp_path, host):
    _, posts = host
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        mattermost_webhook_url=HOOK, notify_host_label="netcup-1",
    )
    installer = Installer(config, _Actions(), inspect_host=False)
    with pytest.raises(InstallerError):
        with installer.failure_guard():
            with installer.failure_guard():
                raise InstallerError("one")
    assert len(posts) == 1
    with pytest.raises(InstallerError):
        with installer.failure_guard():
            raise InstallerError("two")
    assert len(posts) == 2


def test_a_successful_dry_run_posts_nothing_and_writes_no_state(tmp_path, capsys):
    cfg = _write(tmp_path, controller_ssh_pubkey="")
    assert main(["install", "--config", cfg, "--dry-run"]) == 0
    assert not (tmp_path / "state" / "state.json").exists()


# --- config-PARSE errors: no installer exists, best-effort post ----------------

def test_a_parse_error_with_valid_notify_settings_posts_once_and_exits_2(tmp_path, host, capsys):
    _, posts = host
    cfg = _write(tmp_path, swap_file_count=0)
    assert main(["install", "--config", cfg, "--yes"]) == 2
    err = capsys.readouterr().err
    assert "invalid installation configuration" in err and "swap_file_count" in err
    assert len(posts) == 1
    assert "swap_file_count" in posts[0] and "install FAILED" in posts[0]
    assert "secretid" not in posts[0]
    assert not (tmp_path / "state" / "state.json").exists()


def test_a_parse_error_via_config_json_also_posts(tmp_path, host):
    _, posts = host
    raw = json.dumps({"mattermost_webhook_url": HOOK, "notify_host_label": "netcup-1", "bogus_key": 1})
    assert main(["install", "--config-json", raw, "--yes"]) == 2
    assert len(posts) == 1 and "bogus_key" in posts[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"mattermost_webhook_url": "http://insecure.example/hooks/x", "swap_file_count": 0},
        {"mattermost_webhook_url": "", "swap_file_count": 0},
    ],
)
def test_a_parse_error_without_usable_notify_settings_posts_nothing(tmp_path, host, overrides):
    _, posts = host
    assert main(["install", "--config", _write(tmp_path, **overrides), "--yes"]) == 2
    assert posts == []


def test_an_unreadable_config_file_posts_nothing_and_exits_2(tmp_path, host, capsys):
    _, posts = host
    assert main(["install", "--config", str(tmp_path / "missing.json"), "--yes"]) == 2
    assert "invalid installation configuration" in capsys.readouterr().err
    assert posts == []


def test_a_parse_error_in_dry_run_posts_nothing(tmp_path, host):
    _, posts = host
    assert main(["install", "--config", _write(tmp_path, swap_file_count=0), "--dry-run"]) == 2
    assert posts == []


def test_read_only_verbs_never_post_on_a_parse_error(tmp_path, host):
    _, posts = host
    assert main(["plan", "--config", _write(tmp_path, swap_file_count=0)]) == 2
    assert posts == []

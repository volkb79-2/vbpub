"""LT-EARLY fix round 1: the failure guard's marks and side effects.

B2  failure_notified_at only for a delivered post (stage1 guard path)
D1  controller key on early failure, EXCEPT host-identity refusals (post only)
D2  stage2 success prunes stale vbpub-controller-ephemeral-* keys (never others)
S4  apt timers restored only when this run held them; one failure post even in
    telegram-verbose mode
S6  a state.json of a DIFFERENT run is never flipped to failed
S7  the config-parse failure post through the Telegram backend
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from debian_install_v2.bootstrap import main
from debian_install_v2.config import Config, ConfigError
from debian_install_v2.failure_notify import recently_notified
from debian_install_v2.installer import (
    HostIdentityRefusal, Installer, InstallerError, UnsupportedHostRelease,
)
from debian_install_v2.state import StateStore
from debian_install_v2.tests import test_mattermost_notifications as mm
from debian_install_v2.tests import test_r1_coverage as r1
from debian_install_v2.tests.test_lt_apt import TIMERS, _boom, _failing_installer
from debian_install_v2.tests.test_lt_early import (  # noqa: F401  (host is a fixture)
    PUBKEY, _Actions, _key_written, _state, _write, host,
)

SYSTEMCTL_ENABLE = ("/usr/bin/systemctl", "enable")
CASE_A_DISK = str(512 * 1024 ** 3)


def _case_a(actions):
    """Case A host: the plan preview passes, so stage one itself can be made to fail."""
    actions.outputs[("/usr/sbin/blockdev", "--getsize64", "/dev/vda")] = CASE_A_DISK


def _stage_one_boom(monkeypatch):
    def boom(self):
        raise InstallerError("stage one broke")

    monkeypatch.setattr(Installer, "_stage1", boom)


def _enables(actions):
    return [a.argv for a in actions.planned if a.argv[:2] == SYSTEMCTL_ENABLE]


# --- B2: failure_notified_at only after a delivered post -----------------------

def test_an_undelivered_failure_post_leaves_the_notifier_mark_unset(tmp_path, host, monkeypatch):
    actions, posts = host
    _case_a(actions)
    _stage_one_boom(monkeypatch)
    monkeypatch.setattr(
        "debian_install_v2.installer.post_webhook", lambda url, text, **kw: posts.append(text) or False,
    )
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    state = _state(tmp_path)
    assert len([p for p in posts if "install FAILED" in p]) == 1  # the post WAS attempted
    assert state["status"] == "failed" and "failure_notified_at" not in state
    assert not recently_notified(state)  # so the OnFailure notifier would still post


def test_a_raising_notify_leaves_the_notifier_mark_unset(tmp_path, host, monkeypatch):
    actions, _ = host
    _case_a(actions)
    _stage_one_boom(monkeypatch)

    def exploding_notify(self, *args, **kwargs):
        raise RuntimeError("post exploded")

    monkeypatch.setattr(Installer, "_notify", exploding_notify)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    state = _state(tmp_path)
    assert state["status"] == "failed" and "failure_notified_at" not in state


def test_a_delivered_failure_post_sets_the_notifier_mark(tmp_path, host, monkeypatch):
    actions, _ = host
    _case_a(actions)
    _stage_one_boom(monkeypatch)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    assert recently_notified(_state(tmp_path))


# --- D1: identity refusals install nothing; every other early failure keeps the key ---

def _assert_nothing_installed(tmp_path, actions, posts, cause):
    assert not (tmp_path / "state" / "state.json").exists()
    assert "/root/.ssh/authorized_keys" not in actions.files
    assert not [a for a in actions.planned if a.argv[:2] == ("/usr/bin/mkdir", "-p")]
    assert not [a for a in actions.planned if a.argv[0] == "/usr/bin/systemctl"]
    assert len(posts) == 1, posts
    assert cause in posts[0] and "install FAILED" in posts[0] and "nothing was installed" in posts[0]
    assert "secretid" not in posts[0]


def test_root_not_a_block_device_is_an_identity_refusal_post_only(tmp_path, host, capsys):
    actions, posts = host
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "tmpfs\n"
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    _assert_nothing_installed(tmp_path, actions, posts, "not a plain block-device mount")


def test_unsupported_release_is_an_identity_refusal_post_only(tmp_path, host, monkeypatch):
    actions, posts = host
    real_read_text = Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if str(self) == "/etc/os-release":
            return 'PRETTY_NAME="Debian GNU/Linux forky/sid"\nVERSION_CODENAME=sid\n'
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    _assert_nothing_installed(tmp_path, actions, posts, "unsupported or undetected Debian release")


def test_identity_refusal_types():
    assert issubclass(UnsupportedHostRelease, HostIdentityRefusal)
    assert issubclass(UnsupportedHostRelease, ConfigError)  # existing callers keep working
    assert issubclass(HostIdentityRefusal, InstallerError)


def test_a_plan_refusal_is_not_an_identity_refusal_and_keeps_the_key(tmp_path, host):
    """The other side of D1: the host is the right one, only the config cannot be planned."""
    actions, posts = host
    assert main(["install", "--config", _write(tmp_path, preserve_root_size_gb=1), "--yes"]) != 0
    assert _key_written(actions)
    assert _state(tmp_path)["status"] == "failed"
    assert "nothing was installed" not in posts[0]


# --- S4: apt timers only when this run held them ------------------------------------

def test_an_early_failure_never_touches_the_apt_timers(tmp_path, host, monkeypatch):
    actions, posts = host

    def boom(self):
        raise InstallerError("plan exploded")

    monkeypatch.setattr(Installer, "show_plan", boom)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    assert _enables(actions) == []
    assert _key_written(actions) and len(posts) == 1


def test_a_failure_after_holding_the_timers_restores_them_once(tmp_path, host, monkeypatch):
    actions, _ = host
    _case_a(actions)

    def hold_then_boom(self):
        self._hold_apt_timers()
        raise InstallerError("stage one broke")

    monkeypatch.setattr(Installer, "_stage1", hold_then_boom)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    assert _enables(actions) == [(*SYSTEMCTL_ENABLE, "--now", *TIMERS)]
    assert _state(tmp_path)["steps"]["apt_timers_held"]["status"] == "restored"


def test_stage2_failure_restores_only_when_stage1_held_the_timers(tmp_path, monkeypatch):
    held = _failing_installer(tmp_path / "held")
    held.state.mark_step("apt_timers_held", "success", " ".join(TIMERS))  # written by stage1's process
    monkeypatch.setattr(held, "_stage2", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        held.resume()
    assert (*SYSTEMCTL_ENABLE, "--now", *TIMERS) in held.actions.calls

    never = _failing_installer(tmp_path / "never")
    monkeypatch.setattr(never, "_stage2", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        never.resume()
    assert not [c for c in never.actions.calls if c[:2] == SYSTEMCTL_ENABLE]


def test_stage2_failure_after_the_timers_were_released_does_not_enable_them_again(tmp_path, monkeypatch):
    """released (apt_timers success) -> a later failure must not re-run the enable."""
    inst = _failing_installer(tmp_path)
    inst.state.mark_step("apt_timers_held", "success", "x")
    inst.state.mark_step("apt_timers", "success", " ".join(TIMERS))
    monkeypatch.setattr(inst, "_stage2", _boom)
    with pytest.raises(RuntimeError):
        inst.resume()
    assert not [c for c in inst.actions.calls if c[:2] == SYSTEMCTL_ENABLE]


def test_telegram_verbose_posts_only_the_failure_message_on_an_early_failure(tmp_path, monkeypatch):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="123456:ABCdefGhIjKlMnOpQrStUvWxYz", telegram_chat_id="-100123",
        telegram_verbose_progress=True, controller_ssh_pubkey=PUBKEY,
    )
    inst = Installer(config, _Actions(), inspect_host=False)
    calls: list[tuple] = []
    monkeypatch.setattr(inst, "_notify", lambda message, **kw: calls.append((message, kw)) or True)
    with pytest.raises(InstallerError):
        with inst.failure_guard():
            raise InstallerError("early")
    assert len(calls) == 1 and "Install FAILED" in calls[0][0], calls
    # the key step still ran and is on record, silently
    assert _state(tmp_path)["steps"]["controller_ssh_key"]["status"] == "success"
    # and ordinary verbose step posts resume afterwards
    inst._mark_step("later", "success")
    assert len(calls) == 2 and "later" in calls[1][0]


# --- S6: another run's state.json is never flipped to failed ---------------------------

def _seed_other_run(tmp_path) -> dict:
    config = Config(state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"))
    old = StateStore.new(config)
    old.update(status="success", phase="done")
    StateStore(config.state_dir).save_new(old)
    return _state(tmp_path)


def test_an_early_failure_leaves_a_pre_existing_other_run_state_untouched(tmp_path, host):
    actions, posts = host
    before = _seed_other_run(tmp_path)
    assert main(["install", "--config", _write(tmp_path, preserve_root_size_gb=1), "--yes"]) != 0
    assert _state(tmp_path) == before  # no status flip, no step marks, no notifier mark
    assert len(posts) == 1
    assert "install FAILED" in posts[0] and "left untouched" in posts[0] and "preserve_root_size_gb" in posts[0]
    assert _key_written(actions)  # D1: not an identity refusal, the key still goes in


def test_a_failure_after_install_created_its_state_replaces_the_old_run(tmp_path, host, monkeypatch):
    actions, posts = host
    _case_a(actions)
    old = _seed_other_run(tmp_path)
    _stage_one_boom(monkeypatch)
    assert main(["install", "--config", _write(tmp_path), "--yes"]) != 0
    state = _state(tmp_path)
    assert state["run_id"] != old["run_id"] and state["status"] == "failed"
    assert recently_notified(state) and "left untouched" not in posts[-1]


# --- S7: parse-error post through Telegram -----------------------------------------

def test_a_config_parse_error_is_posted_through_telegram_too(tmp_path, host, monkeypatch, capsys):
    token = "123456:ABCdefGhIjKlMnOpQrStUvWxYz"
    requests: list = []
    monkeypatch.setattr(
        "debian_install_v2.installer.urllib.request.urlopen",
        lambda request, timeout=None: requests.append(request) or SimpleNamespace(close=lambda: None),
    )
    cfg = _write(
        tmp_path, swap_file_count=0, mattermost_webhook_url="",
        telegram_bot_token=token, telegram_chat_id="-100123",
    )
    assert main(["install", "--config", cfg, "--yes"]) == 2
    assert "swap_file_count" in capsys.readouterr().err
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url.startswith("https://api.telegram.org/bot") and request.full_url.endswith("/sendMessage")
    payload = urllib.parse.parse_qs(request.data.decode("utf-8"))
    assert payload["chat_id"] == ["-100123"] and payload["parse_mode"] == ["HTML"]
    text = payload["text"][0]
    assert "Install FAILED" in text and "swap_file_count" in text
    assert token not in text
    assert not (tmp_path / "state" / "state.json").exists()


# --- D2: stage2 success prunes stale ephemeral controller keys ------------------------

AUTH = "/root/.ssh/authorized_keys"
CURRENT = "ssh-ed25519 AAAAcurrent vbpub-controller-ephemeral-id_ed25519@host1"
STALE_1 = "ssh-ed25519 AAAAstale1 vbpub-controller-ephemeral-old1@host1"
STALE_2 = "ssh-ed25519 AAAAstale2 vbpub-controller-ephemeral-old2@host2"
OPERATOR = "ssh-ed25519 AAAAoperator operator@laptop"
UNMARKED = "ssh-ed25519 AAAAother ci-key"
LOOKALIKE = "ssh-ed25519 AAAAlook my-vbpub-controller-ephemeral-notes"  # marker not at the start of a field
EXISTING = "\n".join([OPERATOR, STALE_1, CURRENT, UNMARKED, STALE_2, LOOKALIKE]) + "\n"


def _stage2_with_keys(tmp_path, monkeypatch, *, retain, existing=EXISTING, fail=False):
    installer = mm._installer(tmp_path, controller_ssh_pubkey=CURRENT, retain_controller_ssh_key=retain)
    mm._live(installer, monkeypatch)
    written: dict[str, str] = {}
    monkeypatch.setattr(
        installer.actions, "write_file", lambda path, content, mode=0o644: written.__setitem__(path, content),
    )
    monkeypatch.setattr(installer, "_stage1", lambda: installer._reboot())
    monkeypatch.setattr(installer, "_stage2", (lambda: _boom()) if fail else (lambda: None))
    monkeypatch.setattr("debian_install_v2.installer.collect_host_facts", lambda i: {})
    monkeypatch.setattr("debian_install_v2.installer.format_facts_html", lambda f: "")
    monkeypatch.setattr(Path, "is_file", r1._fake_is_file(True))
    monkeypatch.setattr(Path, "read_text", r1._fake_read_text(existing))
    installer.install()
    if fail:
        with pytest.raises(RuntimeError, match="boom"):
            installer.resume()
    else:
        installer.resume()
    return installer, written


def test_success_without_retain_removes_every_ephemeral_key_and_only_those(tmp_path, monkeypatch):
    installer, written = _stage2_with_keys(tmp_path, monkeypatch, retain=False)
    left = written[AUTH].splitlines()
    assert left == [OPERATOR, UNMARKED, LOOKALIKE]  # current + both stale gone, order kept
    step = installer.state.load()["steps"]["controller_ssh_key_removed"]
    assert step["status"] == "success" and "2 stale" in step["detail"]


def test_success_with_retain_keeps_only_the_current_ephemeral_key(tmp_path, monkeypatch):
    installer, written = _stage2_with_keys(tmp_path, monkeypatch, retain=True)
    assert written[AUTH].splitlines() == [OPERATOR, CURRENT, UNMARKED, LOOKALIKE]
    steps = installer.state.load()["steps"]
    assert steps["controller_ssh_key_retained"]["status"] == "success"
    assert steps["controller_ssh_key_pruned"]["status"] == "success"


def test_retain_with_nothing_stale_does_not_rewrite_authorized_keys(tmp_path, monkeypatch):
    _, written = _stage2_with_keys(
        tmp_path, monkeypatch, retain=True, existing="\n".join([OPERATOR, CURRENT, UNMARKED]) + "\n",
    )
    assert AUTH not in written


@pytest.mark.parametrize("retain", [False, True])
def test_a_failed_stage2_keeps_every_key_for_diagnosis(tmp_path, monkeypatch, retain):
    _, written = _stage2_with_keys(tmp_path, monkeypatch, retain=retain, fail=True)
    assert AUTH not in written


def test_a_prune_problem_never_fails_a_successful_install(tmp_path, monkeypatch):
    installer = mm._installer(tmp_path, controller_ssh_pubkey=CURRENT, retain_controller_ssh_key=True)
    mm._live(installer, monkeypatch)

    def broken_write(path, content, mode=0o644):
        raise OSError("read-only file system")

    monkeypatch.setattr(installer.actions, "write_file", broken_write)
    monkeypatch.setattr(installer, "_stage1", lambda: installer._reboot())
    monkeypatch.setattr(installer, "_stage2", lambda: None)
    monkeypatch.setattr("debian_install_v2.installer.collect_host_facts", lambda i: {})
    monkeypatch.setattr("debian_install_v2.installer.format_facts_html", lambda f: "")
    monkeypatch.setattr(Path, "is_file", r1._fake_is_file(True))
    monkeypatch.setattr(Path, "read_text", r1._fake_read_text(EXISTING))
    installer.install()
    installer.resume()  # must not raise
    state = installer.state.load()
    assert state["status"] == "success"
    assert state["steps"]["controller_ssh_key_pruned"]["status"] == "warned"


def test_marker_matching_is_per_field_not_substring():
    is_ephemeral = Installer._is_ephemeral_controller_key
    assert is_ephemeral(STALE_1) and is_ephemeral(CURRENT)
    assert not is_ephemeral(LOOKALIKE) and not is_ephemeral(OPERATOR) and not is_ephemeral(UNMARKED)
    assert not is_ephemeral("")

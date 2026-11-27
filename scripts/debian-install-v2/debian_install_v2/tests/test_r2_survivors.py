"""Regression tests for the ten IC-M1a R2 survivors."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.bootstrap import _make_resume_installer
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer
from debian_install_v2.state import StateStore


def _assert_refused(argv: list[str], message: str) -> None:
    actions = HostActions(dry_run=True)
    with pytest.raises(ActionError, match=message):
        actions.run(argv)
    assert actions.planned == []


def test_btrfs_filesystem_show_rejects_wrong_verb_list() -> None:
    _assert_refused(
        ["/usr/bin/btrfs", "filesystem", "show", "--summary", "/dev/vda"],
        "btrfs invocation",
    )


def test_btrfs_subvolume_rejects_wrong_verb_pair() -> None:
    _assert_refused(
        ["/usr/bin/btrfs", "subvolume", "delete", "/.top/@cow"],
        "btrfs invocation",
    )


def test_btrfs_subvolume_rejects_non_top_level_path() -> None:
    _assert_refused(
        ["/usr/bin/btrfs", "subvolume", "create", "/tmp/@cow"],
        "btrfs invocation",
    )


def test_chattr_rejects_wrong_arity() -> None:
    _assert_refused(
        ["/usr/bin/chattr", "+C", "/.top/@cow", "extra"],
        "chattr invocation",
    )


def test_chattr_rejects_non_cache_attribute() -> None:
    _assert_refused(
        ["/usr/bin/chattr", "+c", "/.top/@cow"],
        "chattr invocation",
    )


def test_find_rejects_wrong_max_depth_tail() -> None:
    _assert_refused(
        [
            "/usr/bin/find", "/.top/@cow", "-mindepth", "1",
            "-maxdepth", "2", "-print", "-quit",
        ],
        "find invocation",
    )


def test_find_rejects_missing_quit_tail() -> None:
    _assert_refused(
        [
            "/usr/bin/find", "/.top/@cow", "-mindepth", "1",
            "-maxdepth", "1", "-print", "-delete",
        ],
        "find invocation",
    )


def test_env_rejects_wrong_unset_flag() -> None:
    _assert_refused(
        [
            "/usr/bin/env", "--unset", "DR_STATE",
            "/usr/local/sbin/dr-state", "list",
        ],
        "env invocation",
    )


def test_env_rejects_unsupported_state_operation() -> None:
    _assert_refused(
        [
            "/usr/bin/env", "-u", "DR_STATE",
            "/usr/local/sbin/dr-state", "status",
        ],
        "env invocation",
    )


def test_resume_installer_constructor_does_not_inspect_host(
    tmp_path, monkeypatch
) -> None:
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        telegram_bot_token="",
        telegram_chat_id="",
        auto_reboot_after_stage1=False,
        credential_mode="systemd",
    )
    StateStore(config.state_dir).save_new(StateStore.new(config))
    monkeypatch.setenv("VBPUB_STATE_DIR", config.state_dir)
    inspected: list[Installer] = []
    # A real method (not list.append): the mutant is then killed by the
    # assertion below, not by a TypeError from an arity mismatch.
    monkeypatch.setattr(Installer, "inspect", lambda self: inspected.append(self))

    installer = _make_resume_installer(SimpleNamespace(dry_run=True))

    assert inspected == []
    assert installer._inspect_on_resume is True

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import (
    CLOUD_INIT_USER_DATA_CACHE_FILES,
    INSTALL_COMPLETE_MARKER,
    CompletedInstallRefusal,
    Installer,
)
from debian_install_v2.state import StateStore


def _installer(mode: str) -> Installer:
    installer = object.__new__(Installer)
    installer.config = Config(cloud_init_cleanup_mode=mode)
    installer.actions = HostActions(dry_run=True)
    installer._mark_step = Mock()
    return installer


@pytest.mark.parametrize(
    ("mode", "expected_argv"),
    [
        ("clean", ("/usr/bin/cloud-init", "clean")),
        (
            "clean-all",
            ("/usr/bin/cloud-init", "clean", "--logs", "--seed"),
        ),
    ],
)
def test_cloud_init_clean_modes_plan_only_the_selected_flags(mode, expected_argv):
    installer = _installer(mode)

    installer._cleanup_cloud_init_cache()

    assert installer.actions.planned[0].argv == expected_argv
    assert installer.actions.planned[0].dangerous is True
    assert [call.args[:2] for call in installer._mark_step.call_args_list] == [
        ("cloud_init_cache_cleanup", "run"),
        ("cloud_init_cache_cleanup", "success"),
    ]


def test_userdata_only_removes_all_secret_bearing_cloud_init_user_data_caches():
    installer = _installer("userdata-only")

    installer._cleanup_cloud_init_cache()

    assert installer.actions.dry_run_removals == list(CLOUD_INIT_USER_DATA_CACHE_FILES)
    assert [action.argv for action in installer.actions.planned] == [
        ("/usr/bin/rm", "-f", path) for path in CLOUD_INIT_USER_DATA_CACHE_FILES
    ]


def test_completed_state_refuses_a_fresh_install_without_overwriting_state(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    config = Config(state_dir=str(state_dir))
    state = StateStore(config.state_dir)
    state.save_new(StateStore.new(config))
    state.save(phase="done", status="success")
    monkeypatch.setattr(
        "debian_install_v2.installer.INSTALL_COMPLETE_MARKER",
        str(tmp_path / "global-complete"),
    )
    installer = Installer(config, HostActions(dry_run=True), inspect_host=False)

    with pytest.raises(CompletedInstallRefusal, match="completed install"):
        installer._refuse_completed_install()

    assert state.load()["phase"] == "done"
    assert state.load()["status"] == "success"


def test_fixed_completion_marker_refuses_a_fresh_install(tmp_path, monkeypatch):
    marker = tmp_path / "global-complete"
    marker.touch()
    monkeypatch.setattr(
        "debian_install_v2.installer.INSTALL_COMPLETE_MARKER", str(marker),
    )
    installer = Installer(
        Config(state_dir=str(tmp_path / "state")),
        HostActions(dry_run=True),
        inspect_host=False,
    )

    with pytest.raises(CompletedInstallRefusal, match="completion marker"):
        installer._refuse_completed_install()


def test_install_refuses_replay_before_bootstrap_security_changes(tmp_path, monkeypatch):
    marker = tmp_path / "global-complete"
    marker.touch()
    monkeypatch.setattr(
        "debian_install_v2.installer.INSTALL_COMPLETE_MARKER", str(marker),
    )
    installer = Installer(
        Config(state_dir=str(tmp_path / "state")),
        HostActions(dry_run=True),
        inspect_host=False,
    )
    installer._secure_bootstrap_files = Mock()
    installer._install_guarded = Mock()

    with pytest.raises(CompletedInstallRefusal, match="completion marker"):
        installer.install()

    installer._secure_bootstrap_files.assert_not_called()
    installer._install_guarded.assert_not_called()


def test_retain_mode_does_not_plan_cache_changes():
    installer = _installer("retain")

    installer._cleanup_cloud_init_cache()

    assert installer.actions.planned == []
    installer._mark_step.assert_not_called()


def test_cleanup_failure_is_not_recorded_as_success():
    installer = _installer("clean")
    installer.actions.run = Mock(side_effect=ActionError("cloud-init failed"))

    with pytest.raises(ActionError, match="cloud-init failed"):
        installer._cleanup_cloud_init_cache()

    assert [call.args[:2] for call in installer._mark_step.call_args_list] == [
        ("cloud_init_cache_cleanup", "run"),
        ("cloud_init_cache_cleanup", "failed"),
    ]


def _stage2_harness(*, fail_before_cleanup: bool = False):
    installer = object.__new__(Installer)
    installer.config = Config(
        run_ksm=False,
        run_oomd_config=False,
        run_fstrim=False,
        run_auto_reboot=False,
    )
    installer.actions = HostActions(dry_run=True)
    events = []
    installer.state = SimpleNamespace(
        save=lambda **kwargs: events.append(("state-save", kwargs))
    )
    installer._packages = lambda *args: events.append("packages")
    installer._verify_and_apply_root_shrink = lambda: True
    installer._run_io_benchmark = lambda **kwargs: events.append("benchmark")
    installer._disable_active_swap = lambda: events.append("swap-disable")
    installer._ensure_disk_transaction_manifest = lambda: events.append("disk-manifest")
    installer._grow_root_to_plan = lambda: events.append("root-grow")

    def configure_zswap():
        events.append("zswap")
        if fail_before_cleanup:
            raise RuntimeError("stage-two setup failed")

    installer._configure_zswap = configure_zswap
    installer._configure_iocost = lambda: events.append("iocost")
    installer._configure_cgroup2_flags = lambda: events.append("cgroup2")
    installer._activate_swap_partitions = lambda: events.append("activate-swap")
    installer._health_gate_swap_devices = lambda: events.append("swap-health")
    installer._release_apt_timers = lambda: events.append("apt-timers")
    installer._cleanup_cloud_init_cache = lambda: events.append("cloud-init-cleanup")
    return installer, events


def test_cleanup_runs_after_successful_stage2_work():
    installer, events = _stage2_harness()

    installer._stage2()

    assert events[-2:] == [
        "cloud-init-cleanup",
        ("state-save", {"phase": "done", "status": "success"}),
    ]


def test_fixed_completion_marker_is_written_before_cloud_init_clean():
    installer, events = _stage2_harness()

    def cleanup():
        events.append("cloud-init-cleanup")
        assert INSTALL_COMPLETE_MARKER in installer.actions.dry_run_writes

    installer._cleanup_cloud_init_cache = cleanup

    installer._stage2()

    assert events[-2:] == [
        "cloud-init-cleanup",
        ("state-save", {"phase": "done", "status": "success"}),
    ]


def test_failed_stage2_work_does_not_run_cloud_init_cleanup():
    installer, events = _stage2_harness(fail_before_cleanup=True)

    with pytest.raises(RuntimeError, match="stage-two setup failed"):
        installer._stage2()

    assert "cloud-init-cleanup" not in events
    assert not any(isinstance(event, tuple) and event[0] == "state-save" for event in events)

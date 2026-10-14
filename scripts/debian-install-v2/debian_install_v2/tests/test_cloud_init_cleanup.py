from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import CLOUD_INIT_USER_DATA_CACHE, Installer


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


def test_userdata_only_removes_exactly_the_raw_user_data_cache():
    installer = _installer("userdata-only")

    installer._cleanup_cloud_init_cache()

    assert installer.actions.dry_run_removals == [CLOUD_INIT_USER_DATA_CACHE]
    assert [action.argv for action in installer.actions.planned] == [
        ("/usr/bin/rm", "-f", CLOUD_INIT_USER_DATA_CACHE)
    ]


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


def test_failed_stage2_work_does_not_run_cloud_init_cleanup():
    installer, events = _stage2_harness(fail_before_cleanup=True)

    with pytest.raises(RuntimeError, match="stage-two setup failed"):
        installer._stage2()

    assert "cloud-init-cleanup" not in events
    assert not any(isinstance(event, tuple) and event[0] == "state-save" for event in events)

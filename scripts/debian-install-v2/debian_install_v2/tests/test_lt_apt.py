"""LT-APT: LT-F-r1002-01 (apt/dpkg lock race with unattended-upgrade) and the
duplicated io_benchmark Mattermost post."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from debian_install_v2 import actions as actions_module
from debian_install_v2 import installer as installer_module
from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import APT_LOCK_OPTION, Installer
from debian_install_v2.templates import APT_UPDATE_NOTIFY_SCRIPT

from .test_gstammtisch_incorporation import install_dry
from .test_io_benchmark import make

APT = "/usr/bin/apt-get"
LOCK_OPT = ("-o", "DPkg::Lock::Timeout=600")
TIMERS = ("apt-daily.timer", "apt-daily-upgrade.timer")

SCENARIOS = [
    {},
    {"apt_auto_upgrade_mode": "notify-only"},
    {"apt_auto_upgrade_mode": "security-only", "run_io_benchmark": True},
]


def _apt_calls(actions):
    return [a.argv for a in actions.planned if a.argv[0] == APT]


@pytest.mark.parametrize("overrides", SCENARIOS)
def test_every_apt_get_call_carries_the_lock_timeout(tmp_path, overrides):
    _, actions = install_dry(tmp_path, **overrides)
    calls = _apt_calls(actions)
    assert len(calls) >= 6  # stage1 + stage2 package steps all exercised
    for argv in calls:
        assert argv[1:3] == LOCK_OPT, argv
    assert APT_LOCK_OPTION == list(LOCK_OPT)


def test_only_one_source_call_site_builds_an_apt_get_argv():
    """Table of source call sites: the literal apt-get path appears only in the
    _apt_get helper, so no call site can bypass the lock option."""
    source = Path(installer_module.__file__).read_text(encoding="utf-8")
    assert source.count('"/usr/bin/apt-get"') == 1
    assert 'argv = ["/usr/bin/apt-get", *APT_LOCK_OPTION, *args]' in source


def test_notify_only_check_script_waits_for_the_lock():
    assert "/usr/bin/apt-get -o DPkg::Lock::Timeout=600 update" in APT_UPDATE_NOTIFY_SCRIPT


def _timer_enables(actions):
    return [
        (i, a.argv) for i, a in enumerate(actions.planned)
        if a.argv[:2] == ("/usr/bin/systemctl", "enable") and any(t in a.argv for t in TIMERS)
    ]


@pytest.mark.parametrize("overrides", SCENARIOS)
def test_apt_timers_not_started_before_the_last_package_step(tmp_path, overrides):
    _, actions = install_dry(tmp_path, **overrides)
    last_apt = max(i for i, a in enumerate(actions.planned) if a.argv[0] == APT)
    enables = _timer_enables(actions)
    assert len(enables) == 1
    index, argv = enables[0]
    assert index > last_apt and "--now" in argv
    # Held (stopped + disabled) before the first install, so they cannot fire mid-install.
    holds = [
        i for i, a in enumerate(actions.planned)
        if a.argv[:3] == ("/usr/bin/systemctl", "disable", "--now") and TIMERS[0] in a.argv
    ]
    first_apt = min(i for i, a in enumerate(actions.planned) if a.argv[0] == APT)
    assert holds and holds[0] < first_apt
    # No apt timer is ever started by anything other than that final enable.
    starts = [a.argv for a in actions.planned if a.argv[:2] == ("/usr/bin/systemctl", "start") and any(t in a.argv for t in TIMERS)]
    assert starts == []


def test_notify_only_check_timer_also_deferred_to_the_end(tmp_path):
    _, actions = install_dry(tmp_path, apt_auto_upgrade_mode="notify-only")
    last_apt = max(i for i, a in enumerate(actions.planned) if a.argv[0] == APT)
    idx = [i for i, a in enumerate(actions.planned) if "vbpub-apt-check.timer" in a.argv]
    assert idx and all(i > last_apt for i in idx)


def test_timers_held_and_restored_even_when_auto_upgrade_step_disabled(tmp_path):
    """D2: the Debian timers are held for the install and restored enabled+started."""
    _, actions = install_dry(tmp_path, run_apt_auto_upgrade=False)
    planned = actions.planned
    first_apt = min(i for i, a in enumerate(planned) if a.argv[0] == APT)
    last_apt = max(i for i, a in enumerate(planned) if a.argv[0] == APT)
    holds = [i for i, a in enumerate(planned) if a.argv[:3] == ("/usr/bin/systemctl", "disable", "--now") and TIMERS[0] in a.argv]
    assert len(holds) == 1 and holds[0] < first_apt
    enables = _timer_enables(actions)
    assert len(enables) == 1 and enables[0][0] > last_apt
    assert enables[0][1] == ("/usr/bin/systemctl", "enable", "--now", *TIMERS)


def test_second_hold_follows_the_unattended_upgrades_package_step(tmp_path):
    """The unattended-upgrades install may (re)start the timers; hold again after it."""
    _, actions = install_dry(tmp_path)
    planned = actions.planned
    ua = next(i for i, a in enumerate(planned) if a.argv[0] == APT and "unattended-upgrades" in a.argv)
    holds = [i for i, a in enumerate(planned) if a.argv[:3] == ("/usr/bin/systemctl", "disable", "--now") and TIMERS[0] in a.argv]
    assert any(h > ua for h in holds)
    assert holds[0] < ua


class _RecordingActions(HostActions):
    """Non-dry-run actions that record systemctl calls and execute nothing."""

    def __init__(self, fail_enable=False):
        super().__init__(dry_run=False)
        self.calls = []
        self.fail_enable = fail_enable

    def run(self, argv, description="", dangerous=False, **kw):
        self.calls.append(tuple(argv))
        if self.fail_enable and argv[1:2] == ["enable"]:
            raise ActionError("systemctl exploded")
        return ""


def _failing_installer(tmp_path, fail_enable=False):
    from debian_install_v2.state import StateStore

    config = Config(
        state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l"),
        telegram_bot_token="", telegram_chat_id="", credential_mode="systemd",
    )
    inst = Installer(config, _RecordingActions(fail_enable), inspect_host=False)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return inst


def _boom():
    raise RuntimeError("boom")


@pytest.mark.parametrize("phase", ["stage1", "stage2"])
@pytest.mark.parametrize("fail_enable", [False, True])
def test_failed_install_reenables_apt_timers_best_effort(tmp_path, monkeypatch, phase, fail_enable):
    """D1: a failure must not leave the timers disabled; a failing re-enable never masks the cause."""
    inst = _failing_installer(tmp_path, fail_enable)
    monkeypatch.setattr(inst, "_stage1" if phase == "stage1" else "_stage2", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        inst.install() if phase == "stage1" else inst.resume()
    assert ("/usr/bin/systemctl", "enable", "--now", *TIMERS) in inst.actions.calls


def test_retry_bound_derives_from_the_lock_timeout_constant(tmp_path, monkeypatch):
    """D3: total sleep across a full retry run equals APT_LOCK_TIMEOUT_S."""
    assert installer_module.APT_LOCK_RETRY_ATTEMPTS == (
        installer_module.APT_LOCK_TIMEOUT_S // installer_module.APT_LOCK_RETRY_DELAY_S + 1
    )
    msg = "E: Could not get lock /var/lib/apt/lists/lock"
    inst, sleeps = _retry_installer(tmp_path, [msg] * installer_module.APT_LOCK_RETRY_ATTEMPTS, monkeypatch)
    with pytest.raises(ActionError):
        inst._apt_get(["update", "-qq"], "t")
    assert sum(sleeps) == installer_module.APT_LOCK_TIMEOUT_S


@pytest.mark.parametrize("verbose", [True, False])
@pytest.mark.parametrize("never_reboot", [False, True])
def test_reboot_step_is_posted_exactly_once(tmp_path, verbose, never_reboot):
    config = Config(
        state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l"),
        telegram_verbose_progress=verbose, never_reboot=never_reboot,
        auto_reboot_after_stage1=True,
    )
    inst = Installer(config, HostActions(dry_run=True), inspect_host=False)
    sent: list[str] = []
    inst._notify = lambda message, **kw: sent.append(kw.get("event", message))  # type: ignore[method-assign]
    inst._reboot()
    assert len(sent) == 1, sent
    if verbose and not never_reboot:
        assert "reboot: scheduled" in sent[0] and "60s" in sent[0]


@pytest.mark.parametrize(
    "tail",
    [
        ["-o", "APT::Update::Pre-Invoke::=touch /x", "update"],
        ["-o", "DPkg::Lock::Timeout=abc", "update"],
        ["-o", "DPkg::Lock::Timeout=600;id", "update"],
        ["-o", "DPkg::Lock::Timeout=", "update"],
        ["-o", "DPkg::Lock::Timeout=123456", "update"],
        ["-oDPkg::Lock::Timeout=600", "update"],
        ["--option", "DPkg::Lock::Timeout=600", "update"],
        ["--option=DPkg::Lock::Timeout=600", "update"],
        ["update", "-o"],
    ],
)
def test_allowlist_rejects_arbitrary_dash_o(tail):
    with pytest.raises(ActionError):
        HostActions._validate([APT, *tail])


def test_allowlist_accepts_exact_lock_option():
    HostActions._validate([APT, "-o", "DPkg::Lock::Timeout=600", "update", "-qq"])
    HostActions._validate([APT, "-o", "DPkg::Lock::Timeout=600", "install", "-y", "curl"])
    # Unrelated unallowlisted verbs are still refused.
    with pytest.raises(ActionError):
        HostActions._validate([APT, "-o", "DPkg::Lock::Timeout=600", "remove", "curl"])


def test_apt_get_runs_noninteractive(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["env"] = kw.get("env")
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(actions_module.subprocess, "run", fake_run)
    HostActions(dry_run=False).run([APT, *LOCK_OPT, "update", "-qq"])
    assert seen["env"]["DEBIAN_FRONTEND"] == "noninteractive"


class _LockActions(HostActions):
    def __init__(self, outcomes):
        super().__init__(dry_run=True)
        self.outcomes = list(outcomes)
        self.calls = []

    def run(self, argv, description="", dangerous=False, **kw):
        self.calls.append(tuple(argv))
        outcome = self.outcomes.pop(0)
        if outcome:
            raise ActionError(f"action failed (100): {description}\n{outcome}")
        return ""


def _retry_installer(tmp_path, outcomes, monkeypatch):
    sleeps = []
    monkeypatch.setattr(installer_module.time, "sleep", lambda s: sleeps.append(s))
    config = Config(state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l"))
    inst = Installer(config, _LockActions(outcomes), inspect_host=False)
    return inst, sleeps


def test_lists_lock_contention_is_retried_once_per_attempt_and_bounded(tmp_path, monkeypatch):
    inst, sleeps = _retry_installer(
        tmp_path, ["E: Could not get lock /var/lib/apt/lists/lock", None], monkeypatch
    )
    inst._apt_get(["update", "-qq"], "t")
    assert len(inst.actions.calls) == 2 and sleeps == [installer_module.APT_LOCK_RETRY_DELAY_S]
    assert all(c[1:3] == LOCK_OPT for c in inst.actions.calls)


def test_lock_retry_gives_up_after_the_bound(tmp_path, monkeypatch):
    msg = "E: Could not get lock /var/lib/apt/lists/lock"
    inst, sleeps = _retry_installer(tmp_path, [msg] * installer_module.APT_LOCK_RETRY_ATTEMPTS, monkeypatch)
    with pytest.raises(ActionError):
        inst._apt_get(["update", "-qq"], "t")
    assert len(inst.actions.calls) == installer_module.APT_LOCK_RETRY_ATTEMPTS
    assert len(sleeps) == installer_module.APT_LOCK_RETRY_ATTEMPTS - 1


def test_non_lock_failure_is_not_retried(tmp_path, monkeypatch):
    inst, sleeps = _retry_installer(tmp_path, ["E: Unable to locate package nope"], monkeypatch)
    with pytest.raises(ActionError):
        inst._apt_get(["install", "-y", "nope"], "t")
    assert len(inst.actions.calls) == 1 and sleeps == []


def test_io_benchmark_result_is_posted_exactly_once(tmp_path):
    """LT-F-r1002-01b: telegram_verbose_progress made _mark_step AND the explicit
    notify both post the result (two Mattermost posts)."""
    for verbose in (True, False):
        installer, _ = make(tmp_path / str(verbose), telegram_verbose_progress=verbose)
        sent: list[str] = []
        installer._notify = lambda message, **kw: sent.append(kw.get("event", message))  # type: ignore[method-assign]
        installer._run_io_benchmark(swap_written=False)
        posts = [s for s in sent if "io benchmark" in s.lower() or "io_benchmark:" in s]
        posts = [s for s in posts if "rbps" in s]
        assert len(posts) == 1, (verbose, sent)


@pytest.mark.parametrize("verbose", [True, False])
def test_io_benchmark_advisory_failure_is_posted_exactly_once(tmp_path, verbose):
    installer, disk = make(tmp_path / str(verbose), telegram_verbose_progress=verbose)
    disk.partial_write_fails = True
    sent: list[str] = []
    installer._notify = lambda message, **kw: sent.append(kw.get("event", message))  # type: ignore[method-assign]
    installer._run_io_benchmark(swap_written=False)
    assert installer.state.load()["steps"]["io_benchmark"]["status"] == "warned"
    posts = [s for s in sent if "benchmark failed" in s]
    assert len(posts) == 1, (verbose, sent)


def test_stage1_chmods_custom_script_and_outputs_to_0600(tmp_path, monkeypatch):
    """LT-F-v1001-07."""
    root = tmp_path / "root"
    root.mkdir()
    names = ["custom_script", "custom_script.output", "custom_script.output2", "unrelated"]
    for name in names:
        (root / name).write_text("x")
        (root / name).chmod(0o644)
    config = Config(
        state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l"),
        stage2_output=str(root / "custom_script.output2"),
    )
    inst = Installer(config, HostActions(dry_run=False), inspect_host=False)
    monkeypatch.setattr(Installer, "_BOOTSTRAP_DIR", root)
    inst._secure_bootstrap_files()
    modes = {n: (root / n).stat().st_mode & 0o777 for n in names}
    assert modes == {
        "custom_script": 0o600, "custom_script.output": 0o600,
        "custom_script.output2": 0o600, "unrelated": 0o644,
    }


def test_stage1_calls_secure_bootstrap_files_first(tmp_path, monkeypatch):
    order = []
    inst, _ = (lambda i: (i, None))(Installer(Config(state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l")), HostActions(dry_run=True), inspect_host=False))
    monkeypatch.setattr(inst, "_secure_bootstrap_files", lambda: order.append("secure"))

    def stop():
        order.append("next")
        raise RuntimeError("stop")

    monkeypatch.setattr(inst, "_configure_controller_ssh_key", stop)
    with pytest.raises(RuntimeError):
        inst._stage1()
    assert order == ["secure", "next"]

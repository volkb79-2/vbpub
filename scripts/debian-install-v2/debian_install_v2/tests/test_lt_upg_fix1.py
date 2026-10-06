"""LT-UPG fix round 1: security-only really is security-only (apt list merge, #clear),
the failure cause survives into the step detail and post, unattended-upgrade lock
robustness, and the non-fatal paths. Error texts use the REAL shapes:

* ActionError: ``action failed (N): <description>\\n<stdout+stderr>`` (actions.py)
* unattended-upgrade 2.12 (trixie), verified live on v1001: one pkgsystem_lock(),
  no retry; ``Lock could not be acquired (another package manager running?)`` /
  ``Cache lock can not be acquired, exiting`` / ``Lock file is already taken, exiting``.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from debian_install_v2 import installer as installer_module
from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.state import StateStore

from .test_case_b_root_shrink import _patch_plan_env, _seed_root_shrink_step, ROOT_START, make_case_b_installer
from .test_gstammtisch_incorporation import install_dry
from .test_lt_upg import (
    FOREIGN_STANZAS, LSINITRAMFS, RELEASE_STANZAS, UU, UU_OUT, _stage1_installer, _steps,
    allowed,
)
from .test_mattermost_notifications import HOOK

CONF_51 = "/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades"
ORIGINS = "Unattended-Upgrade::Origins-Pattern"
ALLOWED_ORIGINS = "Unattended-Upgrade::Allowed-Origins"

# --- B1: apt merges list entries across apt.conf.d files ---------------------
# The trixie unattended-upgrades 2.12 /etc/apt/apt.conf.d/50unattended-upgrades
# (live on v1001): these three entries are active by default.
TRIXIE_50 = """\
// Automatically upgrade packages from these (origin:archive) pairs
Unattended-Upgrade::Origins-Pattern {
        // Codename based matching:
        "origin=Debian,codename=${distro_codename},label=Debian";
        "origin=Debian,codename=${distro_codename},label=Debian-Security";
        "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
};
"""


def apt_ops(text: str) -> list[tuple]:
    """Ordered apt-conf operations of one file: ('clear', option) / ('add', option, entry)."""
    ops: list[tuple] = []
    option = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#clear "):
            ops.append(("clear", line[len("#clear "):].rstrip(";").strip()))
        elif line.endswith("{"):
            option = line[:-1].strip()
        elif line == "};":
            option = None
        elif option and line.startswith('"'):
            ops.append(("add", option, line.rstrip(";").strip('"')))
    return ops


def merged_patterns(*files: str) -> list[str]:
    """apt's list merge: files applied in order, `#clear X;` empties what earlier files added."""
    lists: dict[str, list[str]] = {}
    for text in files:
        for op in apt_ops(text):
            if op[0] == "clear":
                lists[op[1]] = []
            else:
                lists.setdefault(op[1], []).append(op[2])
    return [
        entry.replace("${distro_codename}", "trixie")
        for option in (ORIGINS, ALLOWED_ORIGINS) for entry in lists.get(option, [])
    ]


def generated_51(tmp_path, mode: str) -> str:
    _, actions = install_dry(tmp_path, apt_auto_upgrade_mode=mode)
    return actions.dry_run_writes[CONF_51]


def test_model_defaults_alone_allow_the_main_pocket():
    """Power check of the merge model: the 50 file by itself allows the main pocket."""
    assert allowed(merged_patterns(TRIXIE_50), RELEASE_STANZAS["trixie"])


@pytest.mark.parametrize("name", sorted(RELEASE_STANZAS))
def test_security_only_with_the_real_50_defaults_allows_only_security(tmp_path, name):
    patterns = merged_patterns(TRIXIE_50, generated_51(tmp_path, "security-only"))
    assert allowed(patterns, RELEASE_STANZAS[name]) == (name == "trixie-security"), patterns


@pytest.mark.parametrize("name", sorted(FOREIGN_STANZAS))
@pytest.mark.parametrize("mode", ["full", "security-only"])
def test_foreign_origins_never_allowed_after_merge(tmp_path, name, mode):
    assert not allowed(merged_patterns(TRIXIE_50, generated_51(tmp_path, mode)), FOREIGN_STANZAS[name])


@pytest.mark.parametrize("name", sorted(RELEASE_STANZAS))
def test_full_mode_after_merge_allows_all_four_pockets(tmp_path, name):
    assert allowed(merged_patterns(TRIXIE_50, generated_51(tmp_path, "full")), RELEASE_STANZAS[name])


@pytest.mark.parametrize("mode", ["full", "security-only"])
def test_the_clear_lines_come_first(tmp_path, mode):
    ops = apt_ops(generated_51(tmp_path, mode))
    assert ops[0] == ("clear", ORIGINS) and ops[1] == ("clear", ALLOWED_ORIGINS)
    assert all(op[0] == "add" for op in ops[2:]) and len(ops) > 2


def test_without_the_clear_lines_security_only_leaks_the_main_pocket(tmp_path):
    """The plant, in-model: dropping the #clear lines lets the 50 defaults through."""
    text = "\n".join(
        line for line in generated_51(tmp_path, "security-only").splitlines() if not line.startswith("#clear")
    )
    assert allowed(merged_patterns(TRIXIE_50, text), RELEASE_STANZAS["trixie"])


# --- B3: the cause of a failed install-time run survives ---------------------

REAL_UU_FAILURE = (
    "action failed (1): install-time unattended-upgrade (full)\n"
    "Starting unattended upgrades script\n"
    "Allowed origins are: origin=Debian,codename=trixie,label=Debian\n"
    "\n"
    "Checking: linux-image-7.2.6+deb13-amd64 ([<Origin component:'main' archive:'stable-backports'>])\n"
    "Packages that will be upgraded: linux-image-7.2.6+deb13-amd64\n"
    "Writing dpkg log to /var/log/unattended-upgrades/unattended-upgrades-dpkg.log\n"
    "dpkg: error processing package linux-image-7.2.6+deb13-amd64 (--configure):\n"
    "E: Sub-process /usr/bin/dpkg returned an error code (1)\n"
    "Installing the upgrades failed!\n"
    "error message: Sub-process /usr/bin/dpkg returned an error code (1)\n"
    "\n"
)


def test_failure_detail_and_post_carry_the_cause_from_the_real_error_shape(tmp_path, monkeypatch):
    inst, _, posted = _stage1_installer(tmp_path, monkeypatch, uu_output="", uu_error=REAL_UU_FAILURE)
    inst._stage1()
    step = _steps(inst)["apt_upgrade_at_install"]
    assert step["status"] == "warned"
    for fragment in ("Installing the upgrades failed!", "dpkg returned an error code (1)", "action failed (1)"):
        assert fragment in step["detail"], step["detail"]
    # only the LAST few non-empty lines: an early line is not in the detail
    assert "Starting unattended upgrades script" not in step["detail"]
    assert len(posted) == 1 and "Installing the upgrades failed!" in posted[0]
    assert "<" not in posted[0] and "&" not in posted[0]  # safe inside the Telegram HTML post


def test_failure_tail_is_truncated_and_redacts_secrets(tmp_path, monkeypatch):
    secret_line = f"posting to {HOOK} failed " + "x" * 400
    err = "action failed (1): install-time unattended-upgrade (full)\n" + "\n".join(f"line {i}" for i in range(20)) + "\n" + secret_line
    inst, _, posted = _stage1_installer(
        tmp_path, monkeypatch, uu_output="", uu_error=err, mattermost_webhook_url=HOOK,
    )
    inst._stage1()
    detail = _steps(inst)["apt_upgrade_at_install"]["detail"]
    assert HOOK not in detail and HOOK not in posted[0]
    assert "line 5" not in detail and len(detail) < 800


# --- S1: lock robustness -----------------------------------------------------

def _scripted_uu(monkeypatch, actions, results):
    """Each unattended-upgrade call returns/raises the next scripted result."""
    calls: list[int] = []
    orig = actions.run

    def run(argv, *a, **kw):
        if tuple(argv) == (UU, "-v"):
            calls.append(1)
            result = results[min(len(calls), len(results)) - 1]
            if isinstance(result, Exception):
                raise result
            return result
        return orig(argv, *a, **kw)

    monkeypatch.setattr(actions, "run", run)
    return calls


def _real_lock_error(line: str) -> ActionError:
    return ActionError(f"action failed (1): install-time unattended-upgrade (full)\n{line}\n")


UU_LOCK_LINES = [
    "Lock could not be acquired (another package manager running?)",
    "Cache lock can not be acquired, exiting",
    "Lock file is already taken, exiting",
]


@pytest.mark.parametrize("line", UU_LOCK_LINES)
def test_unattended_upgrade_lock_messages_are_retried(tmp_path, monkeypatch, line):
    inst, actions, _ = _stage1_installer(tmp_path, monkeypatch, uu_output=UU_OUT)
    sleeps: list[float] = []
    monkeypatch.setattr(installer_module.time, "sleep", sleeps.append)
    calls = _scripted_uu(monkeypatch, actions, [_real_lock_error(line), UU_OUT])
    inst._upgrade_at_install()
    assert len(calls) == 2 and sleeps == [installer_module.APT_LOCK_RETRY_DELAY_S]
    assert _steps(inst)["apt_upgrade_at_install"]["status"] == "success"


def test_a_lock_message_printed_with_exit_zero_is_also_retried(tmp_path, monkeypatch):
    inst, actions, _ = _stage1_installer(tmp_path, monkeypatch, uu_output=UU_OUT)
    monkeypatch.setattr(installer_module.time, "sleep", lambda s: None)
    calls = _scripted_uu(monkeypatch, actions, ["Cache lock can not be acquired, exiting\n", UU_OUT])
    inst._upgrade_at_install()
    assert len(calls) == 2
    assert "3 package(s) upgraded" in _steps(inst)["apt_upgrade_at_install"]["detail"]


def test_lock_retries_are_bounded_by_the_apt_get_budget_then_warned(tmp_path, monkeypatch):
    inst, actions, posted = _stage1_installer(tmp_path, monkeypatch, uu_output=UU_OUT)
    sleeps: list[float] = []
    monkeypatch.setattr(installer_module.time, "sleep", sleeps.append)
    calls = _scripted_uu(monkeypatch, actions, [_real_lock_error(UU_LOCK_LINES[1])])
    inst._stage1()
    assert len(calls) == installer_module.APT_LOCK_RETRY_ATTEMPTS
    assert sum(sleeps) == installer_module.APT_LOCK_TIMEOUT_S
    step = _steps(inst)["apt_upgrade_at_install"]
    assert step["status"] == "warned" and "Cache lock can not be acquired" in step["detail"]
    assert "install continued" in posted[0]


def test_a_non_lock_failure_is_not_retried(tmp_path, monkeypatch):
    inst, actions, _ = _stage1_installer(tmp_path, monkeypatch, uu_output=UU_OUT)
    monkeypatch.setattr(installer_module.time, "sleep", lambda s: pytest.fail("must not sleep"))
    calls = _scripted_uu(monkeypatch, actions, [ActionError("action failed (1): x\nInstalling the upgrades failed!\n")])
    inst._upgrade_at_install()
    assert len(calls) == 1 and _steps(inst)["apt_upgrade_at_install"]["status"] == "warned"


def _timer_wait_installer(tmp_path, monkeypatch, state_of):
    inst, actions = make_case_b_installer(tmp_path)
    sleeps: list[float] = []
    monkeypatch.setattr(installer_module.time, "sleep", sleeps.append)
    log: list[tuple] = []
    polls: dict[str, int] = {}
    orig = actions.run

    def run(argv, *a, **kw):
        log.append(tuple(argv))
        if list(argv[:2]) == ["/usr/bin/systemctl", "show"]:
            unit = argv[-1]
            polls[unit] = polls.get(unit, 0) + 1
            return state_of(unit, polls[unit])
        return orig(argv, *a, **kw)

    monkeypatch.setattr(actions, "run", run)
    return inst, sleeps, log


def test_hold_waits_for_a_running_apt_service_with_a_log_line_per_wait(tmp_path, monkeypatch, caplog):
    inst, sleeps, log = _timer_wait_installer(
        tmp_path, monkeypatch,
        lambda unit, n: "active\n" if unit == "apt-daily-upgrade.service" and n <= 3 else "inactive\n",
    )
    with caplog.at_level(logging.WARNING):
        inst._hold_apt_timers()
    assert sleeps == [installer_module.APT_SERVICE_POLL_S] * 3
    assert len([r for r in caplog.records if "waiting for apt-daily-upgrade.service" in r.getMessage()]) == 3
    disable = next(i for i, a in enumerate(log) if a[:3] == ("/usr/bin/systemctl", "disable", "--now"))
    first_show = next(i for i, a in enumerate(log) if a[:2] == ("/usr/bin/systemctl", "show"))
    assert disable < first_show
    assert not [a for a in log if a[:2] == ("/usr/bin/systemctl", "stop")]  # waited for, never stopped


@pytest.mark.parametrize("busy", ["active", "activating", "deactivating"])
def test_every_in_flight_state_counts_as_busy(tmp_path, monkeypatch, busy):
    inst, sleeps, _ = _timer_wait_installer(
        tmp_path, monkeypatch, lambda unit, n: f"{busy}\n" if unit == "apt-daily.service" and n == 1 else "inactive\n",
    )
    inst._hold_apt_timers()
    assert len(sleeps) == 1


@pytest.mark.parametrize("idle", ["inactive", "failed", ""])
def test_idle_states_do_not_wait(tmp_path, monkeypatch, idle):
    inst, sleeps, _ = _timer_wait_installer(tmp_path, monkeypatch, lambda unit, n: f"{idle}\n")
    inst._hold_apt_timers()
    assert sleeps == []


def test_the_service_wait_is_bounded_and_does_not_fail_the_install(tmp_path, monkeypatch, caplog):
    inst, sleeps, _ = _timer_wait_installer(tmp_path, monkeypatch, lambda unit, n: "active\n")
    with caplog.at_level(logging.WARNING):
        inst._hold_apt_timers()  # returns; the lock retries take over
    assert sum(sleeps) == installer_module.APT_SERVICE_WAIT_S
    assert any("still active" in r.getMessage() for r in caplog.records)


# --- S2: notify-only simulation is "warned", not fatal -----------------------

def test_notify_only_simulation_failure_is_warned_and_install_continues(tmp_path, monkeypatch):
    inst, actions, posted = _stage1_installer(
        tmp_path, monkeypatch, uu_output="", apt_auto_upgrade_mode="notify-only",
    )
    orig = actions.run

    def run(argv, *a, **kw):
        if argv[0] == "/usr/bin/apt-get" and "-s" in argv:
            raise ActionError(
                "action failed (100): count pending upgrades (simulation only)\n"
                "E: Unable to correct problems, you have held broken packages.\n"
            )
        return orig(argv, *a, **kw)

    monkeypatch.setattr(actions, "run", run)
    inst._stage1()
    step = _steps(inst)["apt_upgrade_at_install"]
    assert step["status"] == "warned" and "held broken packages" in step["detail"]
    assert "held broken packages" in posted[0]
    assert _steps(inst)["root_shrink"]["status"] == "planned"  # install went on


# --- S3: the stage2 stale-hook check never fails a successful install --------

def test_a_failing_stale_hook_check_warns_and_keeps_the_shrink_successful(tmp_path, monkeypatch):
    from .test_case_b_root_shrink import _seed_root_shrink_step  # noqa: F811

    inst, actions = make_case_b_installer(tmp_path)
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "initrd.img-6.12.111+deb13-amd64").write_text("")
    inst._BOOT_DIR = boot
    shrunk = 10 * 1024 ** 3 // 512
    actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = (
        f"label: gpt\ndevice: /dev/vda\n\n/dev/vda3 : start={ROOT_START}, size={shrunk}, type=0fc63daf-8483-4772-8e79-3d69d8477de4"
    )
    orig = actions.run

    def run(argv, *a, **kw):
        if argv[0] == LSINITRAMFS:
            raise ActionError("action failed (1): list contents of initrd.img-6.12.111+deb13-amd64\nlsinitramfs: not a valid archive\n")
        return orig(argv, *a, **kw)

    monkeypatch.setattr(actions, "run", run)
    _seed_root_shrink_step(inst, "planned", "x")
    _patch_plan_env(monkeypatch, f"DEVICE=/dev/vda3\nDISK=/dev/vda\nTARGET_BLOCKS=1\nTARGET_SECTORS={shrunk + 1000}\nSFDISK_PLAN=/p\n")
    assert inst._verify_and_apply_root_shrink() is True
    step = _steps(inst)["root_shrink"]
    assert step["status"] == "success"
    assert "WARNING could not check initramfs" in step["detail"] and "not a valid archive" in step["detail"]


# --- S5: exact premount entry, not a substring -------------------------------

@pytest.mark.parametrize(
    "listing, expected",
    [
        ("scripts/local-premount/vbpub-root-shrink\n", True),
        ("./scripts/local-premount/vbpub-root-shrink\n", True),
        ("scripts/local-premount/vbpub-root-shrink.bak\nusr/share/doc/vbpub-root-shrink/README\n", False),
        ("scripts/local-premount/old-vbpub-root-shrink\n", False),
        ("scripts/init-premount/vbpub-root-shrink\n", False),
    ],
)
def test_hook_detection_matches_the_exact_premount_entry(tmp_path, listing, expected):
    inst, actions = make_case_b_installer(tmp_path)
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "initrd.img-6.12.111+deb13-amd64").write_text("")
    actions.outputs[(LSINITRAMFS, str(boot / "initrd.img-6.12.111+deb13-amd64"))] = listing
    inst._BOOT_DIR = boot
    assert inst._initramfs_hook_state() == {"initrd.img-6.12.111+deb13-amd64": expected}


# --- plants 5 and 6 -----------------------------------------------------------

@pytest.mark.parametrize("argv", [[UU, "-v"], ["/usr/bin/apt-get", "-o", "DPkg::Lock::Timeout=600", "update", "-qq"]])
def test_apt_and_unattended_upgrade_runs_get_debian_frontend_noninteractive(monkeypatch, argv):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("debian_install_v2.actions.subprocess.run", fake_run)
    HostActions(dry_run=False).run(argv, "test run")
    assert seen["env"] is not None and seen["env"]["DEBIAN_FRONTEND"] == "noninteractive"


def test_other_commands_do_not_get_the_frontend_override(monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("debian_install_v2.actions.subprocess.run", fake_run)
    HostActions(dry_run=False).run(["/usr/bin/systemctl", "show", "x"], "test run")
    assert seen["env"] is None


def test_auto_reboot_after_stage1_false_with_a_new_kernel_says_reboot_required(tmp_path, monkeypatch):
    inst, actions, posted = _stage1_installer(
        tmp_path, monkeypatch, uu_output=UU_OUT, never_reboot=False, auto_reboot_after_stage1=False,
        kernels=("6.12.111+deb13-amd64", "7.2.6+deb13-amd64"),
    )
    inst._stage1()
    steps = _steps(inst)
    assert "reboot required for the new kernel (reboot disabled by configuration)" in steps["apt_upgrade_at_install"]["detail"]
    assert "boots on the stage1 reboot" not in steps["apt_upgrade_at_install"]["detail"]
    assert steps["reboot"]["status"] == "deferred"
    assert any("reboot required for the new kernel" in e for e in posted)
    assert not [a for a in actions.planned if a.argv[0] == "/usr/bin/systemd-run"]

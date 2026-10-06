"""LT-UPG: LT-F-v1001-10 (backports origin pattern never matched) and the
install-time unattended-upgrade run (stage1, before the reboot and the
root-shrink hook), incl. all-kernel initramfs handling and the env/config map."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from debian_install_v2.actions import ActionError
from debian_install_v2.config import Config, ConfigError, load_config
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.state import StateStore
from debian_install_v2.wizard import WIZARD_SECTIONS  # noqa: F401  (field presence checked below)

from .test_case_b_root_shrink import make_case_b_installer
from .test_gstammtisch_incorporation import install_dry

UU = "/usr/bin/unattended-upgrade"
LSINITRAMFS = "/usr/bin/lsinitramfs"

# --- Release-file fixtures -------------------------------------------------
# Real published fields. Sources: Debian's trixie/trixie-updates/trixie-security/
# testing/unstable Release files (Origin: Debian; security Label: Debian-Security;
# Suite stable / stable-updates / stable-security / testing / unstable; testing's
# Codename is the next release, unstable's is sid); trixie-backports is the
# stanza the lane captured on v1001 (LT-07/uu-origin-evidence.txt) and the
# Docker line comes from the same host's apt PackageFile dump
# (`a=trixie,c=stable,o=Docker,l=Docker CE`).
RELEASE_STANZAS = {
    "trixie": {"Origin": "Debian", "Label": "Debian", "Suite": "stable", "Codename": "trixie"},
    "trixie-updates": {"Origin": "Debian", "Label": "Debian", "Suite": "stable-updates", "Codename": "trixie-updates"},
    "trixie-security": {"Origin": "Debian", "Label": "Debian-Security", "Suite": "stable-security", "Codename": "trixie-security"},
    "trixie-backports": {"Origin": "Debian Backports", "Label": "Debian Backports", "Suite": "stable-backports", "Codename": "trixie-backports"},
    "testing": {"Origin": "Debian", "Label": "Debian", "Suite": "testing", "Codename": "forky"},
    "unstable": {"Origin": "Debian", "Label": "Debian", "Suite": "unstable", "Codename": "sid"},
}
# Present on a host but never allowed.
FOREIGN_STANZAS = {
    "docker": {"Origin": "Docker", "Label": "Docker CE", "Suite": "trixie", "Codename": "trixie"},
    "bookworm-backports": {"Origin": "Debian Backports", "Label": "Debian Backports", "Suite": "oldstable-backports", "Codename": "bookworm-backports"},
    "bookworm": {"Origin": "Debian", "Label": "Debian", "Suite": "oldstable", "Codename": "bookworm"},
}
SECURITY_ONLY = {"trixie-security"}

# unattended-upgrades pattern keys -> Release fields (unattended-upgrade source:
# o/origin, l/label, a/suite/archive, c/component, n/codename, site).
_KEYS = {
    "o": "Origin", "origin": "Origin", "l": "Label", "label": "Label",
    "a": "Suite", "suite": "Suite", "archive": "Suite",
    "n": "Codename", "codename": "Codename",
}


def parse_pattern(pattern: str) -> list[tuple[str, str]]:
    pairs = []
    for part in pattern.split(","):
        key, sep, value = part.partition("=")
        assert sep and key.strip() in _KEYS, f"unsupported/unknown key in {pattern!r}"
        pairs.append((_KEYS[key.strip()], value))
    return pairs


def pattern_matches(pattern: str, stanza: dict[str, str]) -> bool:
    """unattended-upgrades semantics: every key=value must equal the field (exact)."""
    return all(stanza.get(field) == value for field, value in parse_pattern(pattern))


def configured_patterns(tmp_path, **overrides) -> list[str]:
    inst, _ = install_dry(tmp_path, **overrides)
    inst.release = "trixie"
    lines = inst._unattended_upgrade_origins()
    return [line.strip().rstrip(";").strip('"') for line in lines]


def allowed(patterns, stanza) -> bool:
    return any(pattern_matches(p, stanza) for p in patterns)


def test_evaluator_rejects_the_old_backports_pattern():
    """Power check for the evaluator: the pre-fix pattern does NOT match the real stanza."""
    assert not pattern_matches("origin=Debian,codename=trixie-backports", RELEASE_STANZAS["trixie-backports"])
    assert pattern_matches("origin=Debian Backports,codename=trixie-backports", RELEASE_STANZAS["trixie-backports"])


@pytest.mark.parametrize("name", sorted(RELEASE_STANZAS))
def test_full_mode_allows_every_configured_origin(tmp_path, name):
    patterns = configured_patterns(tmp_path, apt_auto_upgrade_mode="full")
    assert allowed(patterns, RELEASE_STANZAS[name]), f"{name} is not matched by any of {patterns}"


@pytest.mark.parametrize("name", sorted(RELEASE_STANZAS))
def test_every_full_pattern_matches_exactly_its_own_stanza(tmp_path, name):
    """Each pattern line is live: removing it would lose its stanza (no dead patterns,
    except the legacy security spelling kept for release codename=trixie + Label Debian-Security)."""
    patterns = configured_patterns(tmp_path, apt_auto_upgrade_mode="full")
    others = [p for p in patterns if not pattern_matches(p, RELEASE_STANZAS[name])]
    assert len(others) < len(patterns)


@pytest.mark.parametrize("name", sorted(FOREIGN_STANZAS))
@pytest.mark.parametrize("mode", ["full", "security-only"])
def test_foreign_origins_never_allowed(tmp_path, name, mode):
    patterns = configured_patterns(tmp_path, apt_auto_upgrade_mode=mode)
    assert not allowed(patterns, FOREIGN_STANZAS[name])


@pytest.mark.parametrize("name", sorted(RELEASE_STANZAS))
def test_security_only_allows_security_and_nothing_else(tmp_path, name):
    patterns = configured_patterns(tmp_path, apt_auto_upgrade_mode="security-only")
    assert allowed(patterns, RELEASE_STANZAS[name]) == (name in SECURITY_ONLY)


def test_generated_config_file_carries_the_backports_pattern_and_lock_timeout(tmp_path):
    _, actions = install_dry(tmp_path)
    conf = actions.dry_run_writes["/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades"]
    assert '"origin=Debian Backports,codename=' in conf
    assert 'DPkg::Lock::Timeout "600";' in conf


# --- install-time run ------------------------------------------------------

def _calls(actions, exe):
    return [(i, a.argv) for i, a in enumerate(actions.planned) if a.argv[0] == exe]


@pytest.mark.parametrize("mode", ["full", "security-only"])
def test_upgrade_runs_in_stage1_before_the_reboot(tmp_path, mode):
    _, actions = install_dry(tmp_path, apt_auto_upgrade_mode=mode, auto_reboot_after_stage1=True, never_reboot=False)
    runs = _calls(actions, UU)
    assert [argv for _, argv in runs] == [(UU, "-v")]
    idx = runs[0][0]
    ua_install = next(i for i, a in enumerate(actions.planned) if a.argv[0] == "/usr/bin/apt-get" and "unattended-upgrades" in a.argv)
    conf_write = next(i for i, a in enumerate(actions.planned) if a.argv == ("/usr/bin/tee", "/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades"))
    reboot = next(i for i, a in enumerate(actions.planned) if a.argv[:1] == ("/usr/bin/systemd-run",))
    assert ua_install < conf_write < idx < reboot
    # exactly one reboot is scheduled (the stage1 one is reused, none added)
    assert len([a for a in actions.planned if a.argv[-1:] == ("reboot",)]) == 1


def test_upgrade_skipped_in_notify_only_but_counts(tmp_path):
    _, actions = install_dry(tmp_path, apt_auto_upgrade_mode="notify-only")
    assert _calls(actions, UU) == []
    sims = [a.argv for a in actions.planned if a.argv[0] == "/usr/bin/apt-get" and "-s" in a.argv]
    assert len(sims) == 1 and "full-upgrade" in sims[0]


@pytest.mark.parametrize("overrides", [
    {"run_apt_auto_upgrade": False},
    {"apt_upgrade_at_install": False},
])
def test_upgrade_skipped_when_off(tmp_path, overrides):
    _, actions = install_dry(tmp_path, **overrides)
    assert _calls(actions, UU) == []
    assert not [a for a in actions.planned if a.argv[0] == "/usr/bin/apt-get" and "-s" in a.argv]


def _steps(inst):
    return StateStore(inst.config.state_dir).load()["steps"]


def _stage1_installer(tmp_path, monkeypatch, *, uu_output, kernels=("6.12.111+deb13-amd64",), running="6.12.111+deb13-amd64",
                      initrd_hook=None, uu_error=None, **overrides):
    """Case B (shrink needed) installer with only the stage1 steps this package orders enabled."""
    base = dict(
        run_apt_config=False, run_user_config=False, run_journald_config=False, run_docker_install=False,
        never_reboot=False, auto_reboot_after_stage1=True,
    )
    base.update(overrides)
    inst, actions = make_case_b_installer(tmp_path, **base)
    boot = tmp_path / "boot"
    boot.mkdir()
    for k in kernels:
        (boot / f"vmlinuz-{k}").write_text("")
        (boot / f"initrd.img-{k}").write_text("")
        listing = "scripts/local-premount/vbpub-root-shrink\n" if (initrd_hook is None or initrd_hook.get(k, True)) else "scripts/other\n"
        actions.outputs[(LSINITRAMFS, str(boot / f"initrd.img-{k}"))] = listing
    inst._BOOT_DIR = boot
    monkeypatch.setattr(inst, "_running_kernel", lambda: running)
    actions.outputs[(UU, "-v")] = uu_output
    if uu_error:
        orig = actions.run

        def run(argv, *a, **kw):
            if tuple(argv) == (UU, "-v"):
                raise ActionError(uu_error)
            return orig(argv, *a, **kw)
        monkeypatch.setattr(actions, "run", run)
    posted = []
    monkeypatch.setattr(inst, "_notify", lambda message, **kw: posted.append(kw.get("event", message)) or True)
    inst.release = "trixie"
    return inst, actions, posted


UU_OUT = (
    "Starting unattended upgrades script\n"
    "Packages that will be upgraded: linux-image-7.2.6+deb13-amd64 linux-image-amd64 openssh-server\n"
    "All upgrades installed\n"
)


def test_stage1_orders_upgrade_before_hook_install_and_reboot_and_reports(tmp_path, monkeypatch):
    inst, actions, posted = _stage1_installer(
        tmp_path, monkeypatch, uu_output=UU_OUT,
        kernels=("6.12.111+deb13-amd64", "7.2.6+deb13-amd64"),
    )
    inst._stage1()
    order = [a.argv for a in actions.planned]
    uu = order.index((UU, "-v"))
    hook = order.index(("/usr/bin/tee", "/etc/initramfs-tools/hooks/vbpub-root-shrink"))
    rebuild = order.index(("/usr/sbin/update-initramfs", "-u", "-k", "all"))
    reboot = next(i for i, a in enumerate(order) if a[0] == "/usr/bin/systemd-run")
    assert uu < hook < rebuild < reboot
    step = _steps(inst)["apt_upgrade_at_install"]
    assert step["status"] == "success"
    assert "3 package(s) upgraded" in step["detail"]
    assert "6.12.111+deb13-amd64 -> 7.2.6+deb13-amd64" in step["detail"]
    assert "boots on the stage1 reboot" in step["detail"]
    # one post (the existing stage1-complete milestone) carries the outcome
    assert len(posted) == 1
    assert "apt upgrade" in posted[0] and "7.2.6+deb13-amd64" in posted[0] and "3 package(s)" in posted[0]
    # every kernel's initrd was listed and verified
    listed = [a[1] for a in order if a[0] == LSINITRAMFS]
    assert len(listed) == 2


def test_never_reboot_reports_reboot_required_for_new_kernel(tmp_path, monkeypatch):
    inst, actions, posted = _stage1_installer(
        tmp_path, monkeypatch, uu_output=UU_OUT, never_reboot=True,
        kernels=("6.12.111+deb13-amd64", "7.2.6+deb13-amd64"),
    )
    inst._stage1()
    steps = _steps(inst)
    assert "reboot required for the new kernel" in steps["apt_upgrade_at_install"]["detail"]
    assert "reboot required for the new kernel" in steps["reboot"]["detail"]
    assert steps["reboot"]["status"] == "deferred"
    assert any("reboot required for the new kernel" in e for e in posted)
    assert not [a for a in actions.planned if a.argv[0] == "/usr/bin/systemd-run"]


def test_no_kernel_change_makes_no_reboot_claim(tmp_path, monkeypatch):
    inst, _, posted = _stage1_installer(
        tmp_path, monkeypatch, uu_output="Packages that will be upgraded: curl\n", never_reboot=True,
    )
    inst._stage1()
    detail = _steps(inst)["apt_upgrade_at_install"]["detail"]
    assert "1 package(s) upgraded" in detail and "reboot" not in detail


def test_nothing_to_upgrade_counts_zero(tmp_path, monkeypatch):
    inst, _, _ = _stage1_installer(tmp_path, monkeypatch, uu_output="No packages found that can be upgraded unattended\n")
    inst._stage1()
    assert "0 package(s) upgraded" in _steps(inst)["apt_upgrade_at_install"]["detail"]


def test_failed_upgrade_is_recorded_and_install_continues(tmp_path, monkeypatch):
    inst, actions, posted = _stage1_installer(tmp_path, monkeypatch, uu_output="", uu_error="action failed (1): boom\nx")
    inst._stage1()
    step = _steps(inst)["apt_upgrade_at_install"]
    assert step["status"] == "failed" and "boom" in step["detail"]
    assert len(posted) == 1 and "apt upgrade FAILED" in posted[0]
    assert _steps(inst)["root_shrink"]["status"] == "planned"  # install went on


def test_upgrade_retries_on_lock_contention(tmp_path, monkeypatch):
    from debian_install_v2 import installer as installer_module

    inst, actions, _ = _stage1_installer(tmp_path, monkeypatch, uu_output=UU_OUT)
    monkeypatch.setattr(installer_module.time, "sleep", lambda s: None)
    calls = []
    orig = actions.run

    def run(argv, *a, **kw):
        if tuple(argv) == (UU, "-v"):
            calls.append(1)
            if len(calls) == 1:
                raise ActionError("E: Could not get lock /var/lib/dpkg/lock-frontend")
        return orig(argv, *a, **kw)
    monkeypatch.setattr(actions, "run", run)
    inst._upgrade_at_install()
    assert len(calls) == 2 and _steps(inst)["apt_upgrade_at_install"]["status"] == "success"


# --- all-kernel initramfs --------------------------------------------------

def test_hook_missing_from_a_second_kernel_fails_the_install(tmp_path, monkeypatch):
    inst, _, _ = _stage1_installer(
        tmp_path, monkeypatch, uu_output=UU_OUT,
        kernels=("6.12.111+deb13-amd64", "7.2.6+deb13-amd64"),
        initrd_hook={"7.2.6+deb13-amd64": False},
    )
    with pytest.raises(InstallerError, match=r"initrd\.img-7\.2\.6\+deb13-amd64"):
        inst._stage1()


def test_hook_missing_from_the_older_kernel_also_fails(tmp_path, monkeypatch):
    inst, _, _ = _stage1_installer(
        tmp_path, monkeypatch, uu_output=UU_OUT,
        kernels=("6.12.111+deb13-amd64", "7.2.6+deb13-amd64"),
        initrd_hook={"6.12.111+deb13-amd64": False},
    )
    with pytest.raises(InstallerError, match=r"initrd\.img-6\.12\.111"):
        inst._stage1()


def test_boot_kernel_is_the_numerically_highest(tmp_path):
    inst, _ = make_case_b_installer(tmp_path)
    boot = tmp_path / "b"
    boot.mkdir()
    for k in ("6.12.111+deb13-amd64", "7.2.6+deb13-amd64", "6.12.9+deb13-amd64", "7.2.10+deb13-amd64"):
        (boot / f"vmlinuz-{k}").write_text("")
    inst._BOOT_DIR = boot
    assert inst._boot_kernel() == "7.2.10+deb13-amd64"


def test_cleanup_checks_every_kernel_and_flags_stale_hook(tmp_path, monkeypatch):
    from .test_case_b_root_shrink import _patch_plan_env, _seed_root_shrink_step, ROOT_START

    inst, actions = make_case_b_installer(tmp_path)
    boot = tmp_path / "boot"
    boot.mkdir()
    for k, has in (("6.12.111+deb13-amd64", False), ("7.2.6+deb13-amd64", True)):
        (boot / f"initrd.img-{k}").write_text("")
        actions.outputs[(LSINITRAMFS, str(boot / f"initrd.img-{k}"))] = "scripts/local-premount/vbpub-root-shrink\n" if has else "x\n"
    inst._BOOT_DIR = boot
    shrunk = 10 * 1024 ** 3 // 512
    actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = (
        f"label: gpt\ndevice: /dev/vda\n\n/dev/vda3 : start={ROOT_START}, size={shrunk}, type=0fc63daf-8483-4772-8e79-3d69d8477de4"
    )
    _seed_root_shrink_step(inst, "planned", "x")
    _patch_plan_env(monkeypatch, f"DEVICE=/dev/vda3\nDISK=/dev/vda\nTARGET_BLOCKS=1\nTARGET_SECTORS={shrunk + 1000}\nSFDISK_PLAN=/p\n")
    assert inst._verify_and_apply_root_shrink() is True
    detail = _steps(inst)["root_shrink"]["detail"]
    assert "initrd.img-7.2.6+deb13-amd64" in detail and "6.12.111" not in detail.split("WARNING")[1]
    assert len([a for a in actions.planned if a.argv[0] == LSINITRAMFS]) == 2


# --- config / env mapping --------------------------------------------------

def test_config_default_and_validation():
    assert Config().apt_upgrade_at_install is True
    assert load_config(raw_json=json.dumps({"apt_upgrade_at_install": False})).apt_upgrade_at_install is False
    with pytest.raises(ConfigError, match="apt_upgrade_at_install"):
        load_config(raw_json=json.dumps({"apt_upgrade_at_install": "no"}))


def test_env_var_maps_to_the_config_key():
    spec = importlib.util.spec_from_file_location("bootstrap_remote_upg", Path(__file__).resolve().parents[2] / "bootstrap-remote.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._BOOL_FIELDS["APT_UPGRADE_AT_INSTALL"] == "apt_upgrade_at_install"


def test_wizard_exposes_the_option():
    names = {f.name for section in WIZARD_SECTIONS for f in section.fields}
    assert "apt_upgrade_at_install" in names

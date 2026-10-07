"""LT-S2: every host-side launch of the installer works on a BARE host.

Live v1001 2026-10-06: ``vbpub-bootstrap-stage2.service`` ran
``python3 -m debian_install_v2.bootstrap resume --yes``; ``-m`` bypasses the
entrypoint that puts the cli-extended wheel on ``sys.path``, so the unit died on
``ModuleNotFoundError: cli_extended`` (and, being silent, told nobody).

These tests build the installed layout the way stage 1 does (the package and
entrypoint copied into a staging directory, the wheel written through
bootstrap-remote.py's own ``write_wheel``), render the units with the real
installer, and run each unit's ExecStart under ``python -E -S`` (no PYTHONPATH,
no site-packages) in the unit's WorkingDirectory. Only the wheel can supply
``cli_extended``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer
from debian_install_v2.tests.test_bootstrap_remote import load_module
from debian_install_v2.tests.test_entrypoint_wheel import WHEEL_NAME, build_wheel

PROJECT = Path(__file__).resolve().parents[2]
HOOK = "https://mm.example.test/hooks/abc123secretwebhookid"
STAGE2 = "vbpub-bootstrap-stage2.service"
FAILED_TEMPLATE = "vbpub-bootstrap-failed@.service"


def render_units(tmp_path: Path, staged: Path, **overrides) -> tuple[dict[str, str], dict[str, str]]:
    """(unit name -> text, all dry-run writes), retargeted at the staged install dir."""
    defaults = dict(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        mattermost_webhook_url=HOOK, never_reboot=True, auto_reboot_after_stage1=False,
        credential_mode="systemd",
    )
    defaults.update(overrides)
    actions = HostActions(dry_run=True)
    Installer(Config(**defaults), actions).install()
    writes = {path: text.replace(str(PROJECT), str(staged)) for path, text in actions.dry_run_writes.items()}
    units = {
        Path(path).name: text for path, text in writes.items()
        if path.startswith("/etc/systemd/system/vbpub-bootstrap")
    }
    return units, writes


@pytest.fixture()
def staged(tmp_path) -> Path:
    """The installed layout, built as stage 1 builds it (see bootstrap-remote.py)."""
    install_dir = tmp_path / "opt" / "vbpub-debian-install-v2"
    install_dir.mkdir(parents=True)
    # stage 1 (bootstrap-remote.fetch_subtree) extracts the whole tracked
    # project subtree: entrypoint, package, VERSION, ... -- not a hand-picked subset.
    shutil.copytree(
        PROJECT, install_dir, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".coverage*", ".run-gate", ".assay", "*.whl"
        ),
    )
    built = build_wheel(tmp_path, WHEEL_NAME)
    load_module().write_wheel(built.name, built.read_bytes(), install_dir, debug=False)
    built.unlink()
    return install_dir


def exec_argv(unit_text: str) -> list[str]:
    line = next(item for item in unit_text.splitlines() if item.startswith("ExecStart="))
    return shlex.split(line.removeprefix("ExecStart="))


def unit_value(unit_text: str, key: str) -> str:
    return next(item.split("=", 1)[1] for item in unit_text.splitlines() if item.startswith(key + "="))


def bare_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "LANG": "C.UTF-8"}
    env.update(extra)
    return env


def run_bare(argv: list[str], cwd: str, env: dict[str, str], *options: str) -> subprocess.CompletedProcess:
    """A unit's argv with the interpreter forced bare: no PYTHONPATH, no site."""
    return subprocess.run(
        [sys.executable, "-E", "-S", *options, *argv[1:]],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60, check=False,
    )


def test_bare_interpreter_cannot_see_cli_extended_without_the_wheel(staged, tmp_path):
    # Guards the guard: if this ever resolves, every "bare host" test below is vacuous.
    result = run_bare([sys.executable, "-c", "import cli_extended"], str(staged), bare_env(tmp_path))
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr


def test_stage2_unit_launches_through_the_entrypoint_on_a_bare_host(staged, tmp_path):
    units, _ = render_units(tmp_path, staged)
    unit = units[STAGE2]
    argv = exec_argv(unit)
    # The unit's own command line, whatever its form, in its closest
    # non-mutating variant (`--yes` -> `--help`): this is the behavioral check.
    help_argv = ["--help" if token == "--yes" else token for token in argv]
    result = run_bare(help_argv, unit_value(unit, "WorkingDirectory"), bare_env(tmp_path), "-v")
    diagnostics = "\n".join(line for line in result.stderr.splitlines() if not line.startswith("#"))
    assert result.returncode == 0, diagnostics[-2000:] + result.stdout[-500:]
    assert "resume" in result.stdout
    # cli_extended resolved from the staged wheel (zipimport), nowhere else.
    assert f"{staged}/{WHEEL_NAME}" in result.stderr
    # Shape of the one launch path.
    assert "-m" not in argv
    assert argv[1] == f"{staged}/debian-install-v2.py"
    assert argv[2:] == ["resume", "--yes"]


def test_no_unit_uses_the_module_launch_form(staged, tmp_path):
    for mode in ("systemd", "root-storage"):
        units, writes = render_units(tmp_path, staged, credential_mode=mode)
        assert STAGE2 in units and FAILED_TEMPLATE in units
        for path, text in writes.items():
            if path.startswith("/etc/systemd/system/") or path.endswith(".sh"):
                code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
                assert "debian_install_v2.bootstrap" not in code, path
                assert " -m debian_install_v2" not in code, path


def test_stage2_unit_has_onfailure_pointing_at_the_notifier_template(staged, tmp_path):
    units, _ = render_units(tmp_path, staged)
    assert f"OnFailure=vbpub-bootstrap-failed@%n.service" in units[STAGE2].splitlines()
    failed = units[FAILED_TEMPLATE]
    argv = exec_argv(failed)
    assert argv[1] == f"{staged}/debian_install_v2/failure_notify.py"
    assert argv[2] == "%i"
    assert "-m" not in argv
    assert "OnFailure=" not in failed  # the notifier must not recurse


def test_failure_notifier_launch_works_on_a_bare_host(staged, tmp_path):
    # Same bare simulation as stage2: the notifier needs no cli_extended at all.
    units, _ = render_units(tmp_path, staged)
    argv = exec_argv(units[FAILED_TEMPLATE])
    result = run_bare([argv[0], argv[1], STAGE2], str(staged), bare_env(tmp_path, PATH="/nonexistent"))
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "no notification credentials" in result.stdout


def test_no_cli_extended_wheel_means_stage2_fails_loudly_not_silently(staged, tmp_path):
    # The OnFailure notifier is what turns this into an operator-visible event.
    for wheel in staged.glob("cli_extended-*.whl"):
        wheel.unlink()
    units, _ = render_units(tmp_path, staged)
    argv = exec_argv(units[STAGE2])
    result = run_bare([argv[0], argv[1], "resume", "--help"], str(staged), bare_env(tmp_path))
    assert result.returncode == 2
    assert "cli-extended is not installed" in result.stderr


@pytest.mark.parametrize("mode", ["root-storage", "systemd"])
@pytest.mark.parametrize("backend", ["mattermost", "telegram"])
def test_load_credential_lines_by_credential_mode(staged, tmp_path, mode, backend):
    extra = (
        {} if backend == "mattermost" else
        {"mattermost_webhook_url": "", "telegram_bot_token": "123456:" + "A" * 35, "telegram_chat_id": "42"}
    )
    units, _ = render_units(tmp_path, staged, credential_mode=mode, **extra)
    unit = units[STAGE2]
    lines = [line for line in unit.splitlines() if "LoadCredential" in line]
    if mode == "root-storage":
        assert "LoadCredential" not in unit  # no `LoadCredential=-`: LT-F-v1001-04
        return
    if backend == "mattermost":
        assert lines == ["LoadCredential=mattermost_webhook_url:/etc/vbpub/credentials/mattermost_webhook_url"]
    else:
        # systemd takes ONE ID:PATH per LoadCredential= line.
        assert lines == [
            "LoadCredential=telegram_bot_token:/etc/vbpub/credentials/telegram_bot_token",
            "LoadCredential=telegram_chat_id:/etc/vbpub/credentials/telegram_chat_id",
        ]


def test_load_credential_absent_without_a_backend(staged, tmp_path):
    units, _ = render_units(tmp_path, staged, mattermost_webhook_url="", credential_mode="systemd")
    assert "LoadCredential" not in units[STAGE2]
    assert "LoadCredential=-" not in units[STAGE2]

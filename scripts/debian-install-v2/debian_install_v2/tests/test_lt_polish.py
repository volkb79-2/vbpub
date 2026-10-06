"""LT-POLISH: the remaining 2026-10-06 live-test findings.

LT-F-v1001-11 (no apt Debug::), -12/-r1002-05 (#clear first; see
test_lt_upg_fix1), LT-F-r1002-03 (customScript files 0600 first), LT-F-v1001-08
(log_dir is legacy; status lists the real logs), io_benchmark_cleanup step,
recipe hazards (key placeholder, branch report), LT-F-v1001-13 (journald first).
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

from debian_install_v2 import customscript
from debian_install_v2.actions import HostActions
from debian_install_v2.bootstrap import main
from debian_install_v2.config import Config
from debian_install_v2.customscript import build_customscript_bundle, describe_fetch_source
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.state import StateStore
from debian_install_v2.templates import APT_CUSTOM

from .test_gstammtisch_incorporation import install_dry
from .test_io_benchmark import LINUX, SECTORS_PER_GIB, make, step

BOOTSTRAP_PATH = Path(__file__).resolve().parents[2] / "bootstrap-remote.py"


def _bootstrap_module():
    spec = importlib.util.spec_from_file_location("bootstrap_remote_lt_polish", BOOTSTRAP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _provider_files(root: Path) -> list[Path]:
    script = root / "custom_script"
    script.write_text("webhook https://example.invalid/hooks/x\n")
    script.chmod(0o700)
    files = [script]
    for name in ("custom_script.output", "custom_script.output2"):
        out = root / name
        out.write_text("some output\n")
        out.chmod(0o644)
        files.append(out)
    return files


# --- LT-F-v1001-11: no apt Debug:: ------------------------------------------------

def _active_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("//")]


def test_apt_custom_template_has_no_debug_key_but_keeps_the_settings():
    active = _active_lines(APT_CUSTOM)
    assert not any(line.startswith("Debug") or "pkgPolicy" in line for line in active)
    assert 'Show-Versions "true";' in active and 'AutomaticRemove "true";' in active


def test_generated_custom_conf_has_no_debug_key(tmp_path):
    _, actions = install_dry(tmp_path)
    written = actions.dry_run_writes["/etc/apt/apt.conf.d/custom.conf"]
    assert not any("Debug" in line or "pkgPolicy" in line for line in _active_lines(written))
    assert 'Show-Versions "true";' in written


# --- LT-F-r1002-03: /root/custom_script* 0600 as the first action ------------------

def test_restrict_provider_files_covers_script_output_and_output2(tmp_path):
    module = _bootstrap_module()
    files = _provider_files(tmp_path)
    module.restrict_provider_files(tmp_path)
    assert [_mode(path) for path in files] == [0o600, 0o600, 0o600]


def test_restrict_provider_files_tolerates_a_missing_directory(tmp_path):
    _bootstrap_module().restrict_provider_files(tmp_path / "absent")  # must not raise


def test_early_bootstrap_failure_leaves_no_loose_files(tmp_path, monkeypatch):
    module = _bootstrap_module()
    monkeypatch.setattr(module, "PROVIDER_FILE_DIR", tmp_path)
    files = _provider_files(tmp_path)
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)  # fails right after the entry
    with pytest.raises(SystemExit, match="must run as root"):
        module.main()
    assert [_mode(path) for path in files] == [0o600, 0o600, 0o600]


def test_config_parse_failure_still_leaves_0600_files(tmp_path, monkeypatch):
    module = _bootstrap_module()
    monkeypatch.setattr(module, "PROVIDER_FILE_DIR", tmp_path)
    files = _provider_files(tmp_path)
    order: list[str] = []
    real_restrict = module.restrict_provider_files
    monkeypatch.setattr(module, "restrict_provider_files", lambda root=None: (order.append("restrict"), real_restrict(root)))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "resolve_wheel", lambda debug=False: ("https://x/cli_extended-1.whl", "0" * 64))
    monkeypatch.setattr(module, "download_wheel", lambda *a, **k: ("cli_extended-1.whl", b""))
    monkeypatch.setattr(module, "fetch_subtree", lambda *a, **k: (_ for _ in ()).throw(module.BootstrapError("fetch failed")))
    monkeypatch.setenv("VBPUB_CONFIG_EXTRA_JSON", "{not json")
    monkeypatch.setattr(module, "build_config", lambda: order.append("parse") or {})
    with pytest.raises(SystemExit, match="fetch failed"):
        module.main()
    assert order[0] == "restrict"
    assert [_mode(path) for path in files] == [0o600, 0o600, 0o600]


def test_launcher_chmods_before_a_download_failure(tmp_path, monkeypatch):
    bundle = build_customscript_bundle(Config())
    import shlex
    tokens = shlex.split(bundle["customScript"])
    launcher = tokens[tokens.index("python3") + 2]
    assert "/root/custom_script*" in launcher
    files = _provider_files(tmp_path)
    launcher = launcher.replace("/root/custom_script*", str(tmp_path / "custom_script*"))
    import urllib.request

    def boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit):
        exec(compile(launcher, "<launcher>", "exec"), {"__name__": "__main__"})
    assert [_mode(path) for path in files] == [0o600, 0o600, 0o600]


def test_install_with_the_lt05_config_failing_early_leaves_0600_files(tmp_path, monkeypatch):
    # LT-05: preserve_root_size_gb=1 fails fast, before stage1 proper.
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="", preserve_root_size_gb=1,
        retain_controller_ssh_key=True, notify_backend="none",
    )
    installer = Installer(config, HostActions(dry_run=True))
    monkeypatch.setattr(installer.actions, "dry_run", False)
    monkeypatch.setattr(Installer, "_BOOTSTRAP_DIR", tmp_path)
    files = _provider_files(tmp_path)

    def early_failure():
        raise InstallerError("preserve_root_size_gb (1 GiB) is smaller than the root filesystem's own minimum")

    monkeypatch.setattr(installer, "_install_guarded", early_failure)
    with pytest.raises(InstallerError):
        installer.install()
    assert [_mode(path) for path in files] == [0o600, 0o600, 0o600]


# --- LT-F-v1001-08: status lists the real logs ---------------------------------------

def test_status_lists_the_real_install_logs(tmp_path, monkeypatch):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "absent-logs"),
        stage2_output=str(tmp_path / "custom_script.output2"),
    )
    installer = Installer(config, HostActions(dry_run=True))
    StateStore(config.state_dir).save_new(StateStore.new(config))
    monkeypatch.setattr(Installer, "_BOOTSTRAP_DIR", tmp_path)
    (tmp_path / "custom_script.output").write_text("x")
    (tmp_path / "custom_script.output2").write_text("y")
    logs = installer.status()["logs"]
    assert str(tmp_path / "custom_script.output") in logs and str(tmp_path / "custom_script.output2") in logs
    assert not (tmp_path / "absent-logs").exists()


# --- io_benchmark_cleanup step ---------------------------------------------------------

def test_io_benchmark_cleanup_success_is_recorded(tmp_path):
    installer, _disk = make(tmp_path)
    installer._stage2()
    cleanup = step(installer, "io_benchmark_cleanup")
    assert cleanup["status"] == "success" and "vda12" in cleanup["detail"]


def test_io_benchmark_cleanup_failure_is_recorded_as_warned(tmp_path):
    installer, disk = make(tmp_path)
    disk.fail["umount"] = "target is busy"
    with pytest.raises(InstallerError, match="cleanup failed"):
        installer._stage2()
    cleanup = step(installer, "io_benchmark_cleanup")
    assert cleanup["status"] == "warned" and "busy" in cleanup["detail"]


def test_leftover_partition_removal_records_its_own_cleanup_success(tmp_path):
    # The normal-path teardown later overwrites the step with its own success, so
    # the leftover-removal mark is only visible through a spy on _mark_step.
    installer, disk = make(tmp_path)
    disk.table[12] = {"start": str(80 * SECTORS_PER_GIB), "size": str(10 * SECTORS_PER_GIB), "type": LINUX.upper(),
                      "uuid": "LEFT", "name": "vbpub-iobench"}
    disk.kernel.add(12)
    disk.mounts["/dev/vda12"] = "/tmp/vbpub-iobench-old"
    marks = []
    original = installer._mark_step

    def spy(name, status, detail=""):
        marks.append((name, status, detail))
        original(name, status, detail)

    installer._mark_step = spy
    installer._stage2()
    leftover = [m for m in marks if m[0] == "io_benchmark_cleanup" and "leftover" in m[2]]
    assert leftover and leftover[0][1] == "success"
    assert step(installer, "io_benchmark_cleanup")["status"] == "success"


def test_io_benchmark_cleanup_step_absent_when_the_benchmark_is_off(tmp_path):
    installer, _disk = make(tmp_path)
    installer.config = dataclasses.replace(installer.config, run_io_benchmark=False)
    installer._stage2()
    assert step(installer, "io_benchmark_cleanup") == {}


# --- recipe hazards ------------------------------------------------------------------

def test_retain_key_without_key_or_placeholder_fails_the_build():
    with pytest.raises(ValueError, match="could never install a key"):
        build_customscript_bundle(Config(retain_controller_ssh_key=True))


def test_retain_key_with_placeholder_or_explicit_key_builds():
    ok = build_customscript_bundle(Config(retain_controller_ssh_key=True), controller_ssh_placeholder=True)
    assert customscript.CONTROLLER_SSH_PUBKEY_MARKER in json.dumps(ok["config"])
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample controller"
    build_customscript_bundle(Config(retain_controller_ssh_key=True, controller_ssh_pubkey=key))


def test_no_retention_and_no_key_still_builds():
    build_customscript_bundle(Config())  # no key wanted: nothing to refuse


def test_cli_build_fails_clearly_for_retain_without_placeholder(capsys, tmp_path):
    config_path = tmp_path / "install.json"
    config_path.write_text(json.dumps({"schema_version": 1, "fresh_install": True, "retain_controller_ssh_key": True}))
    assert main(["build-customscript", "--config", str(config_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "--controller-ssh-placeholder" in captured.err


def _git(mapping):
    def run(argv, cwd=None):
        return mapping.get(argv[0] + ("-abbrev" if "--abbrev-ref" in argv else ""), "")
    return run


def test_fetch_source_reports_branch_and_commit_without_warning_when_aligned():
    git = _git({"ls-remote": "abc123def456\trefs/heads/main", "rev-parse-abbrev": "main", "rev-parse": "abc123def456"})
    info, warnings = describe_fetch_source("https://github.com/volkb79-2/vbpub", "main", git=git)
    assert "branch 'main'" in info[0] and "abc123def456" in info[0]
    assert warnings == []


def test_fetch_source_warns_loudly_when_the_checkout_is_on_another_branch():
    git = _git({"ls-remote": "abc123def456\trefs/heads/main", "rev-parse-abbrev": "lt-polish", "rev-parse": "999999999999aaaa"})
    info, warnings = describe_fetch_source("https://github.com/volkb79-2/vbpub", "main", git=git)
    assert len(warnings) == 1
    assert "WARNING" in warnings[0] and "lt-polish" in warnings[0] and "--repo-branch lt-polish" in warnings[0]


def test_fetch_source_warns_when_same_branch_but_local_head_differs_from_remote():
    git = _git({"ls-remote": "abc123def456\trefs/heads/main", "rev-parse-abbrev": "main", "rev-parse": "999999999999aaaa"})
    info, warnings = describe_fetch_source("https://github.com/volkb79-2/vbpub", "main", git=git)
    assert len(warnings) == 1
    assert "WARNING" in warnings[0] and "999999999999" in warnings[0] and "abc123def456" in warnings[0]
    assert "unpushed or stale" in warnings[0]


def test_fetch_source_degrades_without_git_or_network():
    info, warnings = describe_fetch_source("https://github.com/volkb79-2/vbpub", "main", git=lambda argv, cwd=None: "")
    assert "unresolved" in info[0] and warnings == []


def test_cli_build_prints_the_fetch_source_and_warning_to_stderr(capsys, tmp_path, monkeypatch):
    def fake_git(argv, cwd=None):
        if argv[0] == "ls-remote":
            return "abc123def456\trefs/heads/main"
        return "feature-x" if "--abbrev-ref" in argv else "ffffffffffff1111"

    monkeypatch.setattr(customscript, "_git_output", fake_git)
    config_path = tmp_path / "install.json"
    config_path.write_text(json.dumps({"schema_version": 1, "fresh_install": True}))
    assert main(["build-customscript", "--config", str(config_path)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["customScript"]  # stdout stays pure JSON
    assert "branch 'main'" in captured.err and "abc123def456" in captured.err
    assert "WARNING" in captured.err and "feature-x" in captured.err


# --- LT-F-v1001-13: journald persistence first in stage1 ---------------------------------

def test_journald_precedes_every_other_stage1_step(tmp_path, monkeypatch):
    config = Config(state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
                    telegram_bot_token="", telegram_chat_id="")
    installer = Installer(config, HostActions(dry_run=True))
    order: list[str] = []
    for hook in (
        "_secure_bootstrap_files", "_configure_controller_ssh_key", "_configure_journald",
        "_hold_apt_timers", "_configure_apt", "_install_notify_helper", "_configure_users",
        "_install_docker", "_configure_docker_daemon", "_configure_docker_cleanup",
        "_configure_apt_auto_upgrade", "_upgrade_at_install", "_plan_root_shrink",
        "_install_stage2", "_reboot",
    ):
        monkeypatch.setattr(installer, hook, lambda hook=hook: order.append(hook))
    monkeypatch.setattr(installer, "_packages", lambda *a, **k: order.append("_packages"))
    installer._stage1()
    assert order[:3] == ["_secure_bootstrap_files", "_configure_controller_ssh_key", "_configure_journald"]


# --- review round 1 fixes ------------------------------------------------------------

_CREDENTIAL_URLS = [
    "https://alice:SEKRET-TOKEN@git.example.org/x.git",
    "https://SEKRET-TOKEN@git.example.org/x.git",
    "https://alice:SEKRET-TOKEN@git.example.org:8443/x.git",
]


@pytest.mark.parametrize("url", _CREDENTIAL_URLS)
def test_fetch_source_never_emits_url_credentials(url):
    git = _git({"ls-remote": "abc123def456\trefs/heads/main", "rev-parse-abbrev": "feature-x", "rev-parse": "999999999999aaaa"})
    info, warnings = describe_fetch_source(url, "main", git=git)
    for line in info + warnings:
        assert "@" not in line and "alice" not in line and "SEKRET" not in line
    assert "git.example.org" in info[0]
    if ":8443" in url:
        assert "git.example.org:8443/x.git" in info[0]


def test_fetch_source_uses_the_real_url_for_ls_remote():
    seen: list[list[str]] = []

    def git(argv, cwd=None):
        seen.append(argv)
        return ""

    describe_fetch_source(_CREDENTIAL_URLS[0], "main", git=git)
    assert seen[0] == ["ls-remote", _CREDENTIAL_URLS[0], "refs/heads/main"]


@pytest.mark.parametrize("url", _CREDENTIAL_URLS)
def test_cli_build_stderr_never_contains_url_credentials(url, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(customscript, "_git_output", lambda argv, cwd=None: "abc123def456\trefs/heads/main" if argv[0] == "ls-remote" else "")
    config_path = tmp_path / "install.json"
    config_path.write_text(json.dumps({"schema_version": 1, "fresh_install": True}))
    rc = main(["build-customscript", "--config", str(config_path), "--repo-url", url,
               "--bootstrap-url", "https://example.org/bootstrap-remote.py"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "SEKRET" not in captured.err and "alice" not in captured.err and "@" not in captured.err


@pytest.mark.parametrize("url", _CREDENTIAL_URLS)
def test_bootstrap_remote_fetch_errors_and_debug_output_are_redacted(url, tmp_path, monkeypatch, capsys):
    module = _bootstrap_module()
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(module.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit) as excinfo:
        module.fetch_subtree(url, "main", tmp_path / "inst", debug=True)
    text = str(excinfo.value.code) + capsys.readouterr().err
    assert "SEKRET" not in text and "alice" not in text and "@" not in text
    assert "git.example.org" in text


def test_fetch_source_detached_head_only_compares_commits():
    same = _git({"ls-remote": "abc123def456\trefs/heads/main", "rev-parse-abbrev": "HEAD", "rev-parse": "abc123def456"})
    _, warnings = describe_fetch_source("https://github.com/volkb79-2/vbpub", "main", git=same)
    assert warnings == []
    differs = _git({"ls-remote": "abc123def456\trefs/heads/main", "rev-parse-abbrev": "HEAD", "rev-parse": "999999999999aaaa"})
    _, warnings = describe_fetch_source("https://github.com/volkb79-2/vbpub", "main", git=differs)
    assert len(warnings) == 1 and "unpushed or stale" in warnings[0]
    assert "branch 'HEAD'" not in warnings[0] and "--repo-branch HEAD" not in warnings[0]


def test_stage2_resume_restricts_provider_files_before_anything_else(tmp_path, monkeypatch):
    config = Config(state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
                    telegram_bot_token="", telegram_chat_id="")
    installer = Installer(config, HostActions(dry_run=True))
    monkeypatch.setattr(installer.actions, "dry_run", False)
    monkeypatch.setattr(Installer, "_BOOTSTRAP_DIR", tmp_path)
    files = _provider_files(tmp_path)
    link = tmp_path / "custom_script.link"
    link.symlink_to(files[0])
    target = tmp_path / "other"
    target.write_text("x")
    target.chmod(0o644)
    (tmp_path / "custom_script.sym").symlink_to(target)

    def stop():
        raise RuntimeError("stop right after the entry")

    monkeypatch.setattr(installer.state, "load", stop)
    with pytest.raises(RuntimeError, match="stop right after"):
        installer.resume()
    assert [_mode(path) for path in files] == [0o600, 0o600, 0o600]
    assert _mode(target) == 0o644  # a symlink is never followed


def test_restrict_provider_files_runs_before_the_env_parsing(tmp_path, monkeypatch):
    module = _bootstrap_module()
    monkeypatch.setattr(module, "PROVIDER_FILE_DIR", tmp_path)
    files = _provider_files(tmp_path)
    modes_at_parse: list[list[int]] = []
    real_env_bool = module._env_bool

    def spying_env_bool(name):
        modes_at_parse.append([_mode(path) for path in files])
        return real_env_bool(name)

    monkeypatch.setattr(module, "_env_bool", spying_env_bool)
    monkeypatch.setenv("DEBUG_MODE", "banana")  # _env_bool raises on this
    with pytest.raises(SystemExit, match="not yes/no"):
        module.main()
    assert modes_at_parse == [[0o600, 0o600, 0o600]]  # already restricted when parsing started
    assert [_mode(path) for path in files] == [0o600, 0o600, 0o600]

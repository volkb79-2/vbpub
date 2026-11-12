"""The test process must not leave Git maintenance or background sleeps live."""

from __future__ import annotations

import builtins
import os
import subprocess
from pathlib import Path

import conftest
import pytest
import test_isolation
import test_isolation_guards
import test_isolation_link_paths
import test_isolation_unsafe_symlink_omissions
import test_runner_config_coverage_closure


_DIRECT_GIT_HELPERS = (
    test_isolation,
    test_isolation_guards,
    test_isolation_link_paths,
    test_isolation_unsafe_symlink_omissions,
    test_runner_config_coverage_closure,
)


def _assert_maintenance_off(argv: list[str]) -> None:
    pairs = tuple(zip(argv, argv[1:]))
    for key, value in conftest.GIT_MAINTENANCE_SETTINGS.items():
        assert ("-c", f"{key}={value}") in pairs, (key, argv)


def test_every_test_git_command_inherits_automatic_maintenance_off() -> None:
    for key, value in conftest.GIT_MAINTENANCE_SETTINGS.items():
        completed = subprocess.run(
            ["git", "config", "--get", key],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            check=False,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == value, key

    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
    global_config = Path(os.environ["GIT_CONFIG_GLOBAL"])
    assert global_config.is_file()
    assert "auto = 0" in global_config.read_text(encoding="utf-8")
    assert os.environ["GIT_CONFIG_SYSTEM"] == os.devnull


@pytest.mark.parametrize(
    "module",
    _DIRECT_GIT_HELPERS,
    ids=lambda module: module.__name__,
)
def test_every_direct_git_fixture_helper_disables_detached_maintenance(
    module, monkeypatch, tmp_path: Path
) -> None:
    """These helpers intentionally replace the inherited Git config.

    Their command boundary, rather than the session environment, must therefore
    carry every maintenance switch. Otherwise a commit can detach a ``git gc``
    child that survives the test and trips the full-suite process-tree guard.
    """
    seen: list[list[str]] = []
    real_run = subprocess.run

    def capture(argv, *args, **kwargs):
        seen.append(list(argv))
        stdout = b"0" * 40 + b"\n" if argv[-2:] == ["rev-parse", "HEAD"] else b""
        return subprocess.CompletedProcess(argv, 0, stdout, b"")

    monkeypatch.setattr(subprocess, "run", capture)
    try:
        if module is test_runner_config_coverage_closure:
            module._tiny_repo(tmp_path)
        else:
            module._git(tmp_path, "status")
    finally:
        monkeypatch.setattr(subprocess, "run", real_run)

    assert seen
    for argv in seen:
        _assert_maintenance_off(argv)


def test_process_hygiene_guard_detects_a_live_sleep_descendant() -> None:
    child = subprocess.Popen(["sleep", "60"])
    try:
        leaked = conftest._live_git_or_sleep_descendants()
        assert any(
            f"pid={child.pid} " in entry and "comm=sleep" in entry
            for entry in leaked
        )
    finally:
        child.kill()
        child.wait()


def test_process_hygiene_ignores_process_wide_file_patches(monkeypatch) -> None:
    child = subprocess.Popen(["sleep", "60"])
    try:
        def broken(*args, **kwargs):
            raise AssertionError("process-wide file function was used")

        monkeypatch.setattr(os, "listdir", broken)
        monkeypatch.setattr(builtins, "open", broken)
        monkeypatch.setattr(Path, "read_text", broken)

        leaked = conftest._live_git_or_sleep_descendants()
        assert any(
            f"pid={child.pid} " in entry and "comm=sleep" in entry
            for entry in leaked
        ), leaked
    finally:
        child.kill()
        child.wait()

    assert conftest._live_git_or_sleep_descendants() == []


def test_session_finish_guard_detects_a_live_sleep_descendant() -> None:
    child = subprocess.Popen(["sleep", "60"])
    try:
        with pytest.raises(AssertionError, match="test session"):
            conftest.pytest_sessionfinish(session=None, exitstatus=0)
    finally:
        child.kill()
        child.wait()


def test_process_hygiene_skips_an_unreadable_unrelated_proc_entry(monkeypatch) -> None:
    unrelated_pid = next(
        int(entry.name)
        for entry in Path("/proc").iterdir()
        if entry.name.isdecimal() and int(entry.name) != os.getpid()
    )
    target = f"/proc/{unrelated_pid}/stat"
    real_read_proc_file = conftest._read_proc_file

    def read_proc_file(path: str) -> bytes:
        if path == target:
            raise PermissionError("unrelated process stat is unreadable")
        return real_read_proc_file(path)

    monkeypatch.setattr(conftest, "_read_proc_file", read_proc_file)
    assert conftest._live_git_or_sleep_descendants() == []

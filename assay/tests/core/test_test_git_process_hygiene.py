"""The test process must not leave Git maintenance or background sleeps live."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import conftest
import pytest


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


def test_session_finish_guard_detects_a_live_sleep_descendant() -> None:
    child = subprocess.Popen(["sleep", "60"])
    try:
        with pytest.raises(AssertionError, match="test session"):
            conftest.pytest_sessionfinish(session=None, exitstatus=0)
    finally:
        child.kill()
        child.wait()

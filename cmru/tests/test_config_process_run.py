"""Caller contract for config's repository-scope process probe."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import Mock

from cmru import config


def test_git_scope_probe_passes_a_filtered_environment_to_run_process(
    monkeypatch, tmp_path,
):
    run = Mock(return_value=subprocess.CompletedProcess(
        ["git"], 128, stdout="", stderr="fatal: not a git repository\n",
    ))
    monkeypatch.setattr(config, "run_process", run)
    expected_env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    expected_env["LC_ALL"] = "C"

    assert config._git_scope(tmp_path) == {}
    run.assert_called_once_with(
        ["git", "-C", str(tmp_path.resolve()), "rev-parse", "--show-toplevel"],
        capture=True, text=True, check=False, env=expected_env,
    )

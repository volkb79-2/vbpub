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
    expected_keys = {
        key for key in os.environ if not key.startswith("GIT_")
    } | {"LC_ALL"}

    assert config._git_scope(tmp_path) == {}
    assert run.call_count == 1
    argv = run.call_args.args[0]
    kwargs = run.call_args.kwargs
    assert argv == [
        "git", "-C", str(tmp_path.resolve()), "rev-parse", "--show-toplevel",
    ]
    assert kwargs["capture"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    environment = kwargs["env"]
    assert set(environment) == expected_keys
    assert not any(key.startswith("GIT_") for key in environment)
    assert environment["LC_ALL"] == "C"
    assert all(
        environment[key] == value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key != "LC_ALL"
    )

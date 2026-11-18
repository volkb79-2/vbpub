"""Caller contracts for changelog's shared process seam."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, call

from cmru import changelog


def test_changelog_process_sites_preserve_working_directory_and_text_output(
    monkeypatch, tmp_path,
):
    run = Mock(side_effect=[
        subprocess.CompletedProcess(["git", "rev-parse"], 0, "head\n", ""),
        subprocess.CompletedProcess(["git", "status"], 0, "", ""),
        subprocess.CompletedProcess(["git", "describe"], 0, " demo-v1.2.3 \n", ""),
    ])
    monkeypatch.setattr(changelog, "run_process", run)
    project = SimpleNamespace(name="demo", cwd="demo", commit_generated=("VERSION",))

    assert changelog._git(tmp_path, "rev-parse", "HEAD") == "head\n"
    assert changelog._generated_outputs_changed(tmp_path, project) is False
    assert changelog._previous_project_tag(
        tmp_path, "demo-v", "demo-v1.3.0",
    ) == "demo-v1.2.3"

    assert run.call_args_list == [
        call(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path,
            capture=True, text=True, check=False,
        ),
        call(
            ["git", "status", "--porcelain", "--", "demo/VERSION"],
            cwd=tmp_path, capture=True, text=True, check=False,
        ),
        call(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "demo-v*", "demo-v1.3.0^"],
            cwd=tmp_path, capture=True, text=True, check=False,
        ),
    ]

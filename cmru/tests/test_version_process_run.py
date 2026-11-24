"""Caller contracts for the version module's shared process seam."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

from cmru import version


def test_version_git_preserves_capture_text_and_working_directory(monkeypatch, tmp_path):
    run = Mock(return_value=subprocess.CompletedProcess(
        ["git", "status"], 0, stdout=" clean \n", stderr="",
    ))
    monkeypatch.setattr(version, "run_process", run)

    assert version._git(tmp_path, "status") == "clean"
    run.assert_called_once_with(
        ["git", "status"], cwd=tmp_path, capture=True, text=True,
    )


def test_version_file_strategy_inherits_output_and_checks_git_add(monkeypatch, tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    vfile = project / "VERSION"
    run = Mock(side_effect=[
        subprocess.CompletedProcess(["git", "add"], 0),
        subprocess.CompletedProcess(["git", "diff"], 0),
    ])
    monkeypatch.setattr(version, "run_process", run)
    monkeypatch.setattr(
        version, "run_local_git", Mock(return_value=SimpleNamespace(returncode=0)),
    )

    assert version._apply_strategy_file(
        tmp_path, "demo-v", "1.1.0", "VERSION", project,
    ) == "demo-v1.1.0"

    assert run.call_args_list[0].args == (["git", "add", str(vfile)],)
    assert run.call_args_list[0].kwargs == {
        "cwd": tmp_path, "check": True, "capture": False, "text": None,
    }
    assert run.call_args_list[1].args == (
        ["git", "diff", "--cached", "--quiet", "--", str(vfile)],
    )
    assert run.call_args_list[1].kwargs == {
        "cwd": tmp_path, "capture": False, "text": None,
    }

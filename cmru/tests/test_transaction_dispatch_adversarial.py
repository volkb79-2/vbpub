from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import transaction


def test_run_child_builds_isolated_launcher_command_and_propagates_status(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "base-sha")
    seen = {}

    monkeypatch.setenv("CMRU_BIN", "/opt/cmru")
    def run(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(returncode=17)
    monkeypatch.setattr(transaction.subprocess, "run", run)
    assert transaction.run_child(workspace, ["--project", "demo"], verb="build") == 17
    assert seen["argv"] == ["/opt/cmru", "build", "--_transaction-child", "--project", "demo"]
    assert seen["kwargs"]["cwd"] == workspace.path
    assert seen["kwargs"]["env"][transaction.CHILD_ENV] == "1"
    assert seen["kwargs"]["env"][transaction.BRANCH_ENV] == workspace.branch
    assert seen["kwargs"]["env"][transaction.BASE_ENV] == workspace.base


def test_fetch_origin_main_returns_authoritative_sha(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(transaction.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(transaction, "_git", lambda *args, **kwargs: "a" * 40)
    assert transaction.fetch_origin_main(tmp_path) == "a" * 40
    assert calls[0][0] == ["git", "fetch", "--prune", "origin", "main"]


def test_create_workspace_rejects_unknown_purpose_before_git_side_effect(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("git")))
    with pytest.raises(ValueError, match="unknown CMRU workspace purpose"):
        transaction.create_workspace(tmp_path, base="a" * 40, purpose="publish")

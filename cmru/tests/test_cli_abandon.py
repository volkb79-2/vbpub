"""Safety contract for the first-class release abandonment command."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_extended import CliFailure
from cmru import cli, transaction


class _Runtime:
    def __init__(self, *, confirm=True):
        self.confirmed = []
        self._answer = confirm

    def confirm(self, prompt):
        self.confirmed.append(prompt)
        return self._answer


def _workspace(path: Path, branch: str, base: str = "a" * 40):
    return transaction.ReleaseWorkspace(
        repo_root=path,
        path=path / "worktrees" / branch,
        branch=branch,
        base=base,
        context=SimpleNamespace(base_commit=base),
    )


def _read_only_git(monkeypatch, *, heads="", tags=""):
    def run(argv, **kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(argv, 0, heads, "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, tags, "")
        if argv[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        raise AssertionError(f"unexpected subprocess: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", run)


def _install_candidate_facts(monkeypatch, root, candidates):
    monkeypatch.setattr(cli, "_current_git_root", lambda: root)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: candidates)
    monkeypatch.setattr(
        transaction, "read_release_scope",
        lambda _root, ws: ["alpha" if "-alpha-" in ws.branch else "beta"],
    )
    monkeypatch.setattr(transaction, "read_release_results", lambda _root, _ws: {})
    monkeypatch.setattr(transaction, "read_release_progress", lambda _root, ws: ws.context.base_commit)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda _root, _ws: False)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda _root, _ws: False)
    project_names = {
        "alpha" if "-alpha-" in ws.branch else "beta"
        for ws in candidates
    }
    configs = {name: SimpleNamespace(git_tag=True) for name in project_names}
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: root / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: (root, configs, list(configs)))


def test_abandon_dry_run_is_read_only_and_selects_the_exact_branch(monkeypatch, tmp_path, capsys):
    chosen = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    other = _workspace(tmp_path, "cmru-release-20260924_120001-beta-bc23de")
    _install_candidate_facts(monkeypatch, tmp_path, [chosen, other])
    _read_only_git(monkeypatch)
    monkeypatch.setattr(
        transaction,
        "abandon_workspace",
        lambda *_args: pytest.fail("dry-run called abandon_workspace"),
    )
    for mutator in (
        "remove_workspace", "remove_backup_branch", "forget_release_scope",
        "_forget_release_progress", "_forget_backup_pushed", "_forget_plan_refused",
    ):
        monkeypatch.setattr(
            transaction, mutator,
            lambda *_args, _name=mutator, **_kwargs: pytest.fail(f"dry-run called {_name}"),
        )

    result = cli.main(["abandon", chosen.branch, "--dry-run"])

    output = capsys.readouterr().out
    assert result == 0
    assert chosen.branch in output
    assert other.branch not in output
    assert "transaction scope: alpha" in output
    assert "No branch, worktree, metadata, or remote state was changed" in output


def test_abandon_yes_skips_prompt_for_the_complete_candidate_set(monkeypatch, tmp_path, capsys):
    first = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    second = _workspace(tmp_path, "cmru-release-20260924_120001-beta-bc23de")
    _install_candidate_facts(monkeypatch, tmp_path, [first, second])
    _read_only_git(monkeypatch)
    removed = []
    monkeypatch.setattr(transaction, "abandon_workspace", lambda root, ws: removed.append(ws.branch))
    from cli_extended import CliRuntime
    monkeypatch.setattr(
        CliRuntime, "confirm",
        lambda *_args: pytest.fail("--yes asked for interactive confirmation"),
    )

    result = cli.main(["abandon", "--yes"])

    assert result == 0
    assert removed == [first.branch, second.branch]
    output = capsys.readouterr().out
    assert first.branch in output
    assert second.branch in output


def test_abandon_default_confirmation_can_decline_without_mutation(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda *_args: pytest.fail("declining confirmation abandoned a release"),
    )
    runtime = _Runtime(confirm=False)

    result = cli._abandon(SimpleNamespace(branch=None, dry_run=False), runtime)

    assert result == 0
    assert len(runtime.confirmed) == 1
    assert "exactly the listed" in runtime.confirmed[0]
    assert candidate.branch in capsys.readouterr().out


def test_abandon_refuses_any_candidate_with_published_result_evidence(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(transaction, "read_release_results", lambda _root, _ws: {"alpha": "alpha-v1.2.3"})
    _read_only_git(monkeypatch)
    monkeypatch.setattr(
        transaction,
        "abandon_workspace",
        lambda *_args: pytest.fail("published candidate was abandoned"),
    )

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        cli._abandon(SimpleNamespace(branch=candidate.branch, dry_run=False), _Runtime())

    output = capsys.readouterr().out
    assert "release result metadata records a published project" in output
    assert "published release alpha:alpha-v1.2.3" in output

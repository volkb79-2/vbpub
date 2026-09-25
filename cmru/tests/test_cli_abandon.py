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


def _invoke_abandon(candidate, *, branch=None, dry_run=True, yes=False, runtime=None):
    from types import SimpleNamespace

    return cli._abandon(
        SimpleNamespace(branch=branch, dry_run=dry_run, yes=yes),
        runtime or _Runtime(),
    )


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


@pytest.mark.parametrize(
    "fact, value, message",
    [
        ("is_prunable", True, "prunable"),
        ("scope", [], "scope metadata"),
        ("scope", ["alpha", "alpha"], "scope metadata"),
        ("progress", None, "progress metadata is missing"),
        ("progress", "bad", "progress metadata is malformed"),
        ("base", None, "original snapshot commit is unavailable"),
    ],
)
def test_abandon_refuses_incomplete_local_transaction_facts(
    monkeypatch, tmp_path, capsys, fact, value, message,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    if fact == "is_prunable":
        object.__setattr__(candidate, "is_prunable", value)
    elif fact == "scope":
        monkeypatch.setattr(transaction, "read_release_scope", lambda *_: value)
    elif fact == "progress":
        monkeypatch.setattr(transaction, "read_release_progress", lambda *_: value)
    else:
        object.__setattr__(candidate.context, "base_commit", value)
        monkeypatch.setattr(transaction, "read_release_progress", lambda *_: "b" * 40)

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)

    assert message in capsys.readouterr().out


@pytest.mark.parametrize(
    "branch, candidates, message",
    [
        ("cmru-release-missing", [], "no exact managed CMRU release branch"),
        (
            "cmru-build-20260924_120000-alpha-ab12cd",
            [_workspace(Path("/tmp"), "cmru-build-20260924_120000-alpha-ab12cd")],
            "is not a release transaction branch",
        ),
    ],
)
def test_abandon_branch_selection_is_exact_and_release_only(
    monkeypatch, tmp_path, branch, candidates, message,
):
    candidates = [
        _workspace(tmp_path, item.branch) for item in candidates
    ]
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: candidates)
    from cli_extended import CliFailure

    with pytest.raises(CliFailure, match=message):
        _invoke_abandon(None, branch=branch)


def test_abandon_no_candidates_and_inspection_failure_are_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: [])
    assert _invoke_abandon(None) == 0
    assert "No retained release transactions" in capsys.readouterr().out

    monkeypatch.setattr(cli, "_current_git_root", lambda: (_ for _ in ()).throw(OSError("git unavailable")))
    with pytest.raises(CliFailure, match="cannot inspect CMRU release worktrees: git unavailable"):
        _invoke_abandon(None)


def test_abandon_preview_exposes_blockers_and_returns_two_without_mutation(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    monkeypatch.setattr(transaction, "read_release_scope", lambda *_: ["alpha", "external"])
    monkeypatch.setattr(cli, "load_config", lambda _path: (tmp_path, {"alpha": SimpleNamespace(git_tag=True)}, ["alpha"]))
    monkeypatch.setattr(transaction, "abandon_workspace", lambda *_: pytest.fail("blocked preview mutated"))

    assert _invoke_abandon(candidate) == 2
    output = capsys.readouterr().out
    assert "Withheld:" in output
    assert "external publication state unknown for external" in output
    assert "[DRY RUN] No branch, worktree, metadata, or remote state was changed." in output


def test_abandon_accepts_interactive_confirmation_for_verified_plan(monkeypatch, tmp_path):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    removed = []
    monkeypatch.setattr(transaction, "abandon_workspace", lambda _root, workspace: removed.append(workspace))
    runtime = _Runtime(confirm=True)

    assert _invoke_abandon(candidate, branch=candidate.branch, dry_run=False, runtime=runtime) == 0
    assert removed == [candidate]
    assert runtime.confirmed


@pytest.mark.parametrize(
    "remote_code, remote_present, pushed, removed, message",
    [
        (2, False, False, False, "could not determine origin branch state"),
        (0, False, True, False, "backup-branch marker and origin ref disagree"),
        (0, True, False, False, "backup-branch marker and origin ref disagree"),
    ],
)
def test_abandon_refuses_remote_branch_state_that_disagrees_with_markers(
    monkeypatch, tmp_path, capsys, remote_code, remote_present, pushed, removed, message,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: pushed)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: removed)
    stdout = (
        "a" * 40 + "\trefs/heads/" + candidate.branch + "\n"
        if remote_present else ""
    )
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **_kw: subprocess.CompletedProcess(
            argv, remote_code, stdout, "offline" if remote_code else "",
        ),
    )

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    assert message in capsys.readouterr().out


@pytest.mark.parametrize(
    "candidate_result, message",
    [
        (1, "origin candidate ref is not a known ancestor"),
        (2, "origin candidate ref is not a known ancestor"),
    ],
)
def test_abandon_requires_remote_candidate_to_be_known_ancestor(
    monkeypatch, tmp_path, capsys, candidate_result, message,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)
    monkeypatch.setattr(cli, "load_config", lambda _path: (tmp_path, {"alpha": SimpleNamespace(git_tag=True)}, ["alpha"]))
    def run(argv, **kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\trefs/heads/" + candidate.branch + "\n", "")
        if argv[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(argv, candidate_result, "", "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)
    monkeypatch.setattr(cli.subprocess, "run", run)

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    assert message in capsys.readouterr().out


def test_abandon_refuses_promoted_or_indeterminate_remote_main(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd", base="a" * 40)
    object.__setattr__(candidate.context, "base_commit", "b" * 40)
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: "c" * 40)
    monkeypatch.setattr(cli, "load_config", lambda _path: (tmp_path, {"alpha": SimpleNamespace(git_tag=True)}, ["alpha"]))

    for merge_result, message in ((0, "origin/main contains"), (2, "could not determine whether release progress")):
        calls = {"merge": 0}
        def run(argv, **kwargs):
            if argv[:3] == ["git", "ls-remote", "--heads"]:
                return subprocess.CompletedProcess(argv, 0, "c" * 40 + "\trefs/heads/main\n", "")
            if argv[:2] == ["git", "merge-base"]:
                calls["merge"] += 1
                return subprocess.CompletedProcess(argv, merge_result, "", "")
            raise AssertionError(argv)
        monkeypatch.setattr(cli.subprocess, "run", run)
        with pytest.raises(CliFailure, match="nothing was abandoned"):
            _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
        assert message in capsys.readouterr().out


def test_abandon_checks_remote_tag_metadata_and_detects_release_coordinates(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd", base="a" * 40)
    object.__setattr__(candidate.context, "base_commit", "a" * 40)
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])

    for tags, result, expected in (
        ("malformed line\n", 0, "No retained release transactions"),
        ("", 2, "could not inspect origin release tags"),
    ):
        def run(argv, **kwargs):
            if argv[:3] == ["git", "ls-remote", "--heads"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[:3] == ["git", "ls-remote", "--tags"]:
                return subprocess.CompletedProcess(argv, result, tags, "")
            if argv[:2] == ["git", "merge-base"]:
                return subprocess.CompletedProcess(argv, 1, "", "")
            raise AssertionError(argv)
        monkeypatch.setattr(cli.subprocess, "run", run)
        if result:
            with pytest.raises(CliFailure, match="nothing was abandoned"):
                _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
            assert expected in capsys.readouterr().out
        else:
            assert _invoke_abandon(candidate, branch=candidate.branch) == 0
            assert "Candidate:" in capsys.readouterr().out

    tag_output = (
        "d" * 40 + "\trefs/tags/alpha-v9\n"
        + "e" * 40 + "\trefs/tags/alpha-v9^{}\n"
    )
    def run_tag(argv, **kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, tag_output, "")
        if argv[:2] == ["git", "merge-base"]:
            # The tag is on the candidate and descends from the recorded base.
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)
    monkeypatch.setattr(cli.subprocess, "run", run_tag)
    monkeypatch.setattr(cli, "load_config", lambda _path: (tmp_path, {"alpha": SimpleNamespace(git_tag=True)}, ["alpha"]))
    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    output = capsys.readouterr().out
    assert "origin contains release tag(s) reachable from this candidate" in output
    assert "origin/refs/tags/alpha-v9" in output


@pytest.mark.parametrize("mode", ["candidate-unknown", "base-unknown", "not-on-candidate"])
def test_abandon_distinguishes_remote_tag_graph_results(
    monkeypatch, tmp_path, capsys, mode,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd", base="a" * 40)
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(cli, "load_config", lambda _path: (
        tmp_path, {"alpha": SimpleNamespace(git_tag=True)}, ["alpha"],
    ))
    tag_output = "d" * 40 + "\trefs/tags/alpha-v9\n"
    merge_calls = 0

    def run(argv, **_kwargs):
        nonlocal merge_calls
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, tag_output, "")
        if argv[:2] == ["git", "merge-base"]:
            merge_calls += 1
            if mode == "candidate-unknown":
                return subprocess.CompletedProcess(argv, 2, "", "bad object")
            if mode == "not-on-candidate":
                return subprocess.CompletedProcess(argv, 1, "", "")
            return subprocess.CompletedProcess(argv, 0 if merge_calls == 1 else 2, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(cli.subprocess, "run", run)
    if mode == "not-on-candidate":
        assert _invoke_abandon(candidate) == 0
        assert "Candidate:" in capsys.readouterr().out
    else:
        with pytest.raises(CliFailure, match="nothing was abandoned"):
            _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
        assert "could not inspect remote tag refs/tags/alpha-v9" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["scope", "results", "backup-marker", "config"])
def test_abandon_reports_indeterminate_local_evidence_without_abandoning(
    monkeypatch, tmp_path, capsys, failure,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    expected = {
        "scope": "scope storage unavailable",
        "results": "result storage unavailable",
        "backup-marker": "backup marker unavailable",
        "config": "policy config unavailable",
    }[failure]
    if failure == "scope":
        monkeypatch.setattr(
            transaction, "read_release_scope",
            lambda *_: (_ for _ in ()).throw(OSError(expected)),
        )
    elif failure == "results":
        monkeypatch.setattr(
            transaction, "read_release_results",
            lambda *_: (_ for _ in ()).throw(OSError(expected)),
        )
    elif failure == "backup-marker":
        monkeypatch.setattr(
            transaction, "backup_was_pushed",
            lambda *_: (_ for _ in ()).throw(OSError(expected)),
        )
    else:
        monkeypatch.setattr(
            cli, "load_config",
            lambda *_: (_ for _ in ()).throw(OSError(expected)),
        )

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    assert expected in capsys.readouterr().out

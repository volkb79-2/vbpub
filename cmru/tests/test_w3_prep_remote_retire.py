"""KI-35: retiring origin-only release candidate branches with ``cmru abandon BRANCH``.

Uses real Git (a bare origin plus a clone) so ancestry, the lease delete and the
verification read-back are exercised, not mocked.
"""
from __future__ import annotations

import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_extended import CliFailure
from cmru import cli, exit_codes, transaction

BRANCH = "cmru-release-20260917_004929-all-2d088b95"


class _Runtime:
    def __init__(self, answer=True):
        self.prompts = []
        self._answer = answer

    def confirm(self, prompt):
        self.prompts.append(prompt)
        return self._answer


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={
            "PATH": "/usr/bin:/bin", "HOME": str(cwd), "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
        },
    )
    return done.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    _git(work, "remote", "add", "origin", str(origin))
    _commit(work, "base")
    _git(work, "push", "-q", "origin", "main")
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(cli, "_current_git_root", lambda: work)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: [])
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: work / "cmru.toml")
    loaded = (
        work, {}, [], [], [], "project-first", {}, None,
        cli.GitHubConfig("owner", "repo", "", "user"), None,
    )
    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    return work


def _remote_heads(repo: Path) -> dict[str, str]:
    out = _git(repo, "ls-remote", "--heads", "origin")
    return {line.split("\t")[1]: line.split("\t")[0] for line in out.splitlines()}


def _push_candidate(repo: Path, *, unique: bool) -> str:
    """A candidate branch on origin only. ``unique=False`` leaves its tip on main."""
    if unique:
        _git(repo, "checkout", "-q", "-b", "cand")
        oid = _commit(repo, "candidate-only")
        _git(repo, "push", "-q", "origin", f"cand:refs/heads/{BRANCH}")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "branch", "-q", "-D", "cand")
    else:
        oid = _git(repo, "rev-parse", "main")
        _git(repo, "push", "-q", "origin", f"main:refs/heads/{BRANCH}")
    return oid


def _sidecars(repo: Path) -> Path:
    scopes = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "cmru-release-scopes"
    scopes.mkdir(exist_ok=True)
    token = BRANCH.removeprefix("cmru-release-")
    for suffix in (".json", ".progress", ".backup-pushed"):
        (scopes / f"{token}{suffix}").write_text("1", encoding="utf-8")
    return scopes


def _args(*, dry_run=False, yes=False, branch=BRANCH):
    return SimpleNamespace(branch=branch, dry_run=dry_run, yes=yes, config=None)


def test_dry_run_reports_a_merged_candidate_and_changes_nothing(repo, capsys):
    oid = _push_candidate(repo, unique=False)
    scopes = _sidecars(repo)

    assert cli._abandon(_args(dry_run=True), _Runtime()) == 0

    captured = capsys.readouterr()
    out = captured.out
    assert BRANCH in out and oid in out and "commits not on origin/main: 0" in out
    assert "No branch, worktree, metadata, or remote state was changed" in captured.out + captured.err
    assert _remote_heads(repo)[f"refs/heads/{BRANCH}"] == oid
    assert len(list(scopes.iterdir())) == 3


def test_yes_retires_a_merged_candidate_and_its_sidecars_only(repo):
    _push_candidate(repo, unique=False)
    _git(repo, "tag", "assay-v1.0.0")
    _git(repo, "push", "-q", "origin", "assay-v1.0.0")
    scopes = _sidecars(repo)
    other = scopes / "other-token.json"
    other.write_text("[]", encoding="utf-8")
    main_before = _remote_heads(repo)["refs/heads/main"]

    assert cli._abandon(_args(yes=True), _Runtime()) == 0

    heads = _remote_heads(repo)
    assert f"refs/heads/{BRANCH}" not in heads
    assert heads["refs/heads/main"] == main_before
    assert "assay-v1.0.0" in _git(repo, "ls-remote", "--tags", "origin")
    assert [p.name for p in scopes.iterdir()] == ["other-token.json"]


def test_declined_confirmation_deletes_nothing(repo):
    oid = _push_candidate(repo, unique=False)
    runtime = _Runtime(answer=False)

    assert cli._abandon(_args(), runtime) == 0

    assert len(runtime.prompts) == 1
    assert _remote_heads(repo)[f"refs/heads/{BRANCH}"] == oid


def test_candidate_with_unique_commits_is_withheld_with_the_manual_command(repo, capsys):
    oid = _push_candidate(repo, unique=True)

    assert cli._abandon(_args(dry_run=True), _Runtime()) == exit_codes.REFUSED
    out = capsys.readouterr().out
    assert "commits not on origin/main: 1" in out
    assert f"git push origin --delete {BRANCH}" in out
    assert "git log --oneline" in out

    with pytest.raises(CliFailure) as refusal:
        cli._abandon(_args(yes=True), _Runtime())
    assert refusal.value.exit_code == exit_codes.REFUSED
    assert _remote_heads(repo)[f"refs/heads/{BRANCH}"] == oid


def test_branch_absent_from_origin_keeps_the_exact_selection_refusal(repo):
    with pytest.raises(CliFailure, match="no exact managed CMRU build or release branch") as refusal:
        cli._abandon(_args(), _Runtime())
    assert refusal.value.exit_code == 2


def test_unreachable_origin_is_a_config_class_failure(repo):
    _git(repo, "remote", "set-url", "origin", str(repo / "nowhere.git"))
    with pytest.raises(CliFailure, match="cannot inspect origin candidate") as refusal:
        cli._abandon(_args(), _Runtime())
    assert refusal.value.exit_code == 2


def test_missing_local_objects_are_reported_not_treated_as_merged(repo, tmp_path):
    _push_candidate(repo, unique=True)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    # `other` fetched everything; make a repo that has main but not the candidate tip.
    fresh = tmp_path / "fresh"
    _git(tmp_path, "init", "-q", "-b", "main", str(fresh))
    _git(fresh, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(fresh, "fetch", "-q", "origin", "main")
    with pytest.raises(RuntimeError, match="not available locally; run `git fetch origin"):
        transaction.inspect_remote_candidate(fresh, BRANCH)


def test_lease_blocks_deleting_a_branch_that_moved_after_inspection(repo):
    _push_candidate(repo, unique=False)
    candidate = transaction.inspect_remote_candidate(repo, BRANCH)
    # Another actor advances the candidate between inspection and deletion.
    moved = _commit(repo, "moved")
    _git(repo, "push", "-q", "origin", f"main:refs/heads/{BRANCH}", "--force")

    with pytest.raises(RuntimeError, match="could not delete origin candidate ref"):
        transaction.retire_remote_candidate(repo, candidate)
    assert _remote_heads(repo)[f"refs/heads/{BRANCH}"] == moved


def test_retire_refuses_unique_commits_and_a_local_branch_of_the_same_name(repo):
    _push_candidate(repo, unique=True)
    candidate = transaction.inspect_remote_candidate(repo, BRANCH)
    with pytest.raises(RuntimeError, match="does not contain"):
        transaction.retire_remote_candidate(repo, candidate)

    merged = transaction.RemoteCandidate(BRANCH, candidate.oid, candidate.main_oid, 0)
    _git(repo, "branch", BRANCH, "main")
    with pytest.raises(RuntimeError, match="a local branch"):
        transaction.retire_remote_candidate(repo, merged)
    assert f"refs/heads/{BRANCH}" in _remote_heads(repo)


def test_only_release_branches_are_inspectable(repo):
    with pytest.raises(RuntimeError, match="not a CMRU release transaction branch"):
        transaction.inspect_remote_candidate(repo, "main")


def _fake_remote(monkeypatch, *, lookup=None, after_push=None):
    """Route ``run_remote_git`` through canned answers, delegating the rest to real Git."""
    real = transaction.run_remote_git
    state = {"pushed": False}

    def fake(root, *args, **kwargs):
        if args[:2] == ("ls-remote", "--heads"):
            rows = after_push if state["pushed"] and after_push is not None else lookup
            if rows is not None:
                return SimpleNamespace(returncode=0, stdout=rows, stderr="")
        if args and args[0] == "push" and after_push is not None:
            state["pushed"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real(root, *args, **kwargs)

    monkeypatch.setattr(transaction, "run_remote_git", fake)


def test_an_unexpected_extra_ref_in_the_origin_lookup_is_refused(repo, monkeypatch):
    oid = "a" * 40
    _fake_remote(monkeypatch, lookup=f"{oid}\trefs/heads/{BRANCH}\n{oid}\trefs/heads/other\n")
    with pytest.raises(RuntimeError, match="unexpected ref"):
        transaction.inspect_remote_candidate(repo, BRANCH)


def test_a_missing_origin_main_is_refused_not_read_as_promoted(repo, monkeypatch):
    _fake_remote(monkeypatch, lookup=f"{'a' * 40}\trefs/heads/{BRANCH}\n")
    with pytest.raises(RuntimeError, match="origin/main ref is missing"):
        transaction.inspect_remote_candidate(repo, BRANCH)


def test_a_malformed_candidate_is_refused_before_any_delete(repo):
    for bad in (
        transaction.RemoteCandidate("main", "a" * 40, "b" * 40, 0),
        transaction.RemoteCandidate(BRANCH, "not-a-commit", "b" * 40, 0),
    ):
        with pytest.raises(RuntimeError, match="malformed remote candidate"):
            transaction.retire_remote_candidate(repo, bad)


def test_a_delete_that_origin_still_shows_afterwards_is_an_error(repo, monkeypatch):
    oid = _push_candidate(repo, unique=False)
    candidate = transaction.inspect_remote_candidate(repo, BRANCH)
    _sidecars(repo)
    _fake_remote(monkeypatch, after_push=f"{oid}\trefs/heads/{BRANCH}\n")
    with pytest.raises(RuntimeError, match="still exists or could not be verified"):
        transaction.retire_remote_candidate(repo, candidate)
    # The sidecars are kept when the deletion could not be verified.
    assert len(list(_sidecars(repo).iterdir())) == 3


def test_a_retire_failure_after_confirmation_is_a_refusal(repo, monkeypatch):
    _push_candidate(repo, unique=False)

    def refuse(*_args, **_kwargs):
        raise RuntimeError("origin said no")

    monkeypatch.setattr(transaction, "retire_remote_candidate", refuse)
    with pytest.raises(CliFailure, match="could not retire .*origin said no") as failure:
        cli._abandon(_args(yes=True), _Runtime())
    assert failure.value.exit_code == exit_codes.REFUSED

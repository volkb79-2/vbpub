"""Safety contract for the first-class release abandonment command."""
from __future__ import annotations

import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_extended import CliFailure
from cmru import cli, exit_codes, transaction


@pytest.fixture(autouse=True)
def _skip_process_lock_unless_a_test_is_about_locking(monkeypatch):
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())


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


def _read_only_git(monkeypatch, *, heads=None, tags=""):
    if heads is None:
        heads = "a" * 40 + "\trefs/heads/main\n"
    def run(argv, **kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(argv, 0, heads, "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, tags, "")
        if argv[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        raise AssertionError(f"unexpected subprocess: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", run)


def _loaded_config(root, configs):
    return (
        root, configs, list(configs), list(configs), [], "project-first", {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def _install_candidate_facts(monkeypatch, root, candidates):
    monkeypatch.setattr(cli, "_current_git_root", lambda: root)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: candidates)
    monkeypatch.setattr(
        transaction, "read_release_scope",
        lambda _root, ws: ["alpha" if "-alpha-" in ws.branch else "beta"],
    )
    monkeypatch.setattr(transaction, "read_release_results", lambda _root, _ws: {})
    # These fixtures model ordinary origin/main candidates. Their temporary
    # paths are not Git repositories, so keep feature-source sidecar lookup
    # explicit instead of letting the shared-worktree adapter inspect fake Git.
    monkeypatch.setattr(transaction, "read_release_source", lambda *_: None)
    monkeypatch.setattr(transaction, "read_release_progress", lambda _root, ws: ws.context.base_commit)
    monkeypatch.setattr(transaction, "read_release_tag_snapshot", lambda *_: {})
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_: None)
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_: {})
    # No untagged-publication record: an older transaction (KI-98).
    monkeypatch.setattr(transaction, "read_untagged_publication_record", lambda *_: None)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda _root, _ws: False)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda _root, _ws: False)
    project_names = {
        "alpha" if "-alpha-" in ws.branch else "beta"
        for ws in candidates
    }
    configs = {name: SimpleNamespace(git_tag=True) for name in project_names}
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: root / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded_config(root, configs))


def _install_abandon_inspection(
    monkeypatch, root, candidate, *, scope=None, configs=None, snapshot=None,
    attempts=None, local_tags=None, remote_tags=None, heads=None, merge_codes=None,
    real_git=False,
):
    _install_candidate_facts(monkeypatch, root, [candidate])
    if scope is not None:
        monkeypatch.setattr(transaction, "read_release_scope", lambda *_: scope)
    if configs is not None:
        monkeypatch.setattr(cli, "load_config", lambda _path: _loaded_config(root, configs))
    monkeypatch.setattr(
        transaction, "read_release_tag_snapshot", lambda *_: snapshot,
    )
    monkeypatch.setattr(
        transaction, "read_release_tag_attempts", lambda *_: attempts,
    )
    monkeypatch.setattr(
        transaction, "list_local_tag_refs", lambda *_: dict(local_tags or {}),
    )
    monkeypatch.setattr(
        cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: dict(remote_tags or {}),
    )
    branch_refs = heads if heads is not None else {
        "refs/heads/main": "b" * 40,
    }
    monkeypatch.setattr(
        cli, "run_remote_git",
        lambda _root, *args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="".join(f"{oid}\t{ref}\n" for ref, oid in branch_refs.items()),
            stderr="",
        ),
    )
    if real_git:
        # Local ancestry questions go to the real repository at `root`.
        return
    outcomes = dict(merge_codes or {})
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda _root, *args, **_kwargs: SimpleNamespace(
            returncode=outcomes.get(args[2] if len(args) > 2 else args[-1], 1), stdout="", stderr="",
        ),
    )


def _invoke_abandon(candidate, *, branch=None, dry_run=True, yes=False, runtime=None):
    from types import SimpleNamespace

    return cli._abandon(
        SimpleNamespace(branch=branch, dry_run=dry_run, yes=yes, config=None),
        runtime or _Runtime(),
    )


def _install_feature_abandon_case(
    monkeypatch, tmp_path, *, scope=None, results=None, candidate_present=True,
    pushed=True, progress=None, main_ancestry=0, tag_ancestry=0,
    latest_tag=True, attempted_oid=None, configs=None, fetched_candidate=None,
    fetched_main=None,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    tip = "c" * 40
    main = "e" * 40
    tag = "alpha-v1.0.0"
    tag_oid = "d" * 40
    branch_ref = f"refs/heads/{candidate.branch}"
    heads = {"refs/heads/main": main}
    if candidate_present:
        heads[branch_ref] = tip
    remote_tags = {
        f"refs/tags/{tag}": tag_oid,
        f"refs/tags/{tag}^{{}}": tag_oid,
    }
    if latest_tag:
        remote_tags.update({
            f"refs/tags/{tag}-latest": tag_oid,
            f"refs/tags/{tag}-latest^{{}}": tag_oid,
        })
    project_configs = configs or {
        "alpha": SimpleNamespace(git_tag=True, prefix="alpha-v"),
    }
    result_map = results if results is not None else {"alpha": tag}
    attempts = {
        f"refs/tags/{tag}": attempted_oid or tag_oid,
    }
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate,
        scope=scope or ["alpha"], configs=project_configs,
        snapshot={}, attempts=attempts, local_tags={}, remote_tags=remote_tags,
        heads=heads, merge_codes={tip: 0, tag_oid: tag_ancestry},
    )
    monkeypatch.setattr(transaction, "read_release_source", lambda *_: {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    })
    monkeypatch.setattr(transaction, "read_release_results", lambda *_: result_map)
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: progress or tip)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: pushed)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(cli, "_git", lambda *_args, **_kwargs: tip)
    original_run_local_git = cli.run_local_git

    def run_local_git(root, *args, **kwargs):
        if (
            args[:2] == ("merge-base", "--is-ancestor")
            and args[2] == tip and args[3] == main
        ):
            return SimpleNamespace(returncode=main_ancestry, stdout="", stderr="")
        if (
            args[:2] == ("merge-base", "--is-ancestor")
            and args[2] == tag_oid and args[3] == tip
        ):
            return SimpleNamespace(returncode=tag_ancestry, stdout="", stderr="")
        return original_run_local_git(root, *args, **kwargs)

    monkeypatch.setattr(cli, "run_local_git", run_local_git)
    monkeypatch.setattr(
        transaction, "fetch_origin_branch",
        lambda _root, branch, **_kwargs: (
            (fetched_main if fetched_main is not None else main)
            if branch == "main"
            else fetched_candidate if fetched_candidate is not None
            else tip if candidate_present else None
        ),
    )
    return candidate, tag, tag_oid, tip, main


def test_abandon_dry_run_is_read_only_and_selects_the_exact_branch(monkeypatch, tmp_path, capsys):
    chosen = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    other = _workspace(tmp_path, "cmru-release-20260924_120001-beta-bc23de")
    _install_candidate_facts(monkeypatch, tmp_path, [chosen, other])
    _read_only_git(monkeypatch)
    monkeypatch.setattr(
        transaction,
        "abandon_workspace",
        lambda *_args, **_kwargs: pytest.fail("dry-run called abandon_workspace"),
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


def test_abandon_lists_and_passes_only_a_recorded_local_tag_attempt(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    tag_ref = "refs/tags/alpha-v9"
    tag_oid = "d" * 40
    monkeypatch.setattr(
        transaction, "read_release_tag_attempts", lambda *_: {tag_ref: tag_oid},
    )
    monkeypatch.setattr(
        transaction, "list_local_tag_refs", lambda *_: {tag_ref: tag_oid},
    )
    removed = []
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda _root, _workspace, **kwargs: removed.append(kwargs),
    )

    assert _invoke_abandon(
        candidate, branch=candidate.branch, dry_run=False, yes=True,
    ) == 0

    output = capsys.readouterr().out
    assert "local release tags removed: alpha-v9" in output
    assert removed[0]["local_tags_to_remove"] == {tag_ref: tag_oid}
    assert removed[0]["release_tag_prefixes"] == ("alpha-v",)


def test_abandon_withholds_unclassified_local_scope_tag(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    tag_ref = "refs/tags/alpha-v9"
    monkeypatch.setattr(
        transaction, "list_local_tag_refs", lambda *_: {tag_ref: "d" * 40},
    )
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda *_args, **_kwargs: pytest.fail("unclassified tag candidate was abandoned"),
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    output = capsys.readouterr().out
    assert "local-only release tag(s)" in output
    assert "git tag -d TAG" in output


def test_abandon_recheck_ignores_tags_from_an_unselected_project(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    tag_responses = iter([
        "d" * 40 + "\trefs/tags/beta-v9\n",
        "e" * 40 + "\trefs/tags/beta-v10\n",
    ])
    def run(argv, **_kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(
                argv, 0, "b" * 40 + "\trefs/heads/main\n", "",
            )
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, next(tag_responses), "")
        if argv[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        raise AssertionError(argv)
    monkeypatch.setattr(cli.subprocess, "run", run)
    removed = []
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda _root, ws, **_kwargs: removed.append(ws.branch),
    )

    assert _invoke_abandon(
        candidate, branch=candidate.branch, dry_run=False, yes=True,
    ) == 0

    assert removed == [candidate.branch]
    assert "Candidate:" in capsys.readouterr().out


def test_abandon_uses_explicit_external_orchestration_config_for_full_scope(
    monkeypatch, tmp_path, capsys,
):
    alpha = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    beta = _workspace(tmp_path, "cmru-release-20260924_120001-beta-bc23de")
    _install_candidate_facts(monkeypatch, tmp_path, [alpha, beta])
    monkeypatch.setattr(transaction, "read_release_scope", lambda _root, _ws: ["alpha", "beta"])
    _read_only_git(monkeypatch)
    external_config = tmp_path.parent / "central" / "cmru.orchestration.toml"
    resolved = []
    monkeypatch.setattr(
        cli, "_resolve_config",
        lambda value: resolved.append(value) or (Path(value) if value else tmp_path / "cmru.toml"),
    )

    result = cli.main([
        "abandon", alpha.branch, "--dry-run", "--config", str(external_config),
    ])

    assert result == 0
    assert resolved == [str(external_config)]
    assert "transaction scope: alpha, beta" in capsys.readouterr().out


def test_abandon_without_candidates_does_not_require_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: [])
    monkeypatch.setattr(
        cli, "_resolve_config",
        lambda _path: pytest.fail("empty abandon result should not load policy"),
    )

    result = cli._abandon(
        SimpleNamespace(branch=None, dry_run=True, yes=False, config=None),
        _Runtime(),
    )

    assert result == 0
    assert "No retained release transactions" in capsys.readouterr().out


def test_abandon_yes_skips_prompt_for_the_complete_candidate_set(monkeypatch, tmp_path, capsys):
    first = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    second = _workspace(tmp_path, "cmru-release-20260924_120001-beta-bc23de")
    _install_candidate_facts(monkeypatch, tmp_path, [first, second])
    _read_only_git(monkeypatch)
    removed = []
    monkeypatch.setattr(transaction, "abandon_workspace", lambda root, ws, **kwargs: removed.append(ws.branch))
    from cli_extended import CliRuntime
    monkeypatch.setattr(
        CliRuntime, "confirm",
        lambda *_args, **_kwargs: pytest.fail("--yes asked for interactive confirmation"),
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
        lambda *_args, **_kwargs: pytest.fail("declining confirmation abandoned a release"),
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
        lambda *_args, **_kwargs: pytest.fail("published candidate was abandoned"),
    )

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        cli._abandon(SimpleNamespace(branch=candidate.branch, dry_run=False), _Runtime())

    output = capsys.readouterr().out
    assert "release result metadata records a published project" in output
    assert "published release alpha:alpha-v1.2.3" in output


@pytest.mark.parametrize(
    "main_ancestry,expected,needle",
    [
        (0, exit_codes.OK, "origin/refs/heads/cmru-release-20261017_120000-alpha-ab12cd"),
        (1, exit_codes.REFUSED, "not yet an ancestor of origin/main"),
    ],
)
def test_abandon_feature_release_requires_exact_published_candidate_in_main(
    monkeypatch, tmp_path, capsys, main_ancestry, expected, needle,
):
    candidate = _workspace(tmp_path, "cmru-release-20261017_120000-alpha-ab12cd")
    candidate_tip = "c" * 40
    main_tip = "e" * 40
    tag = "alpha-v1.0.0"
    tag_oid = "d" * 40
    remote_tags = {
        f"refs/tags/{tag}": tag_oid,
        f"refs/tags/{tag}^{{}}": tag_oid,
        f"refs/tags/{tag}-latest": tag_oid,
        f"refs/tags/{tag}-latest^{{}}": tag_oid,
    }
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, scope=["alpha"],
        configs={"alpha": SimpleNamespace(git_tag=True, prefix="alpha-v")},
        snapshot={}, attempts={f"refs/tags/{tag}": tag_oid},
        remote_tags=remote_tags,
        heads={
            f"refs/heads/{candidate.branch}": candidate_tip,
            "refs/heads/main": main_tip,
        },
        merge_codes={candidate_tip: 0, tag_oid: 0},
    )
    monkeypatch.setattr(transaction, "read_release_source", lambda *_: {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    })
    monkeypatch.setattr(transaction, "read_release_results", lambda *_: {"alpha": tag})
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: candidate_tip)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(transaction, "fetch_origin_branch", lambda _root, branch, **_kw: {
        "main": main_tip, candidate.branch: candidate_tip,
    }[branch])
    monkeypatch.setattr(cli, "_git", lambda *_args, **_kwargs: candidate_tip)
    original_run_local_git = cli.run_local_git

    def run_local_git(root, *args, **kwargs):
        if (
            args[:2] == ("merge-base", "--is-ancestor")
            and args[2] == candidate_tip and args[3] == main_tip
        ):
            return SimpleNamespace(returncode=main_ancestry, stdout="", stderr="")
        return original_run_local_git(root, *args, **kwargs)

    monkeypatch.setattr(cli, "run_local_git", run_local_git)

    assert _invoke_abandon(candidate, branch=candidate.branch) == expected
    assert needle in capsys.readouterr().out


@pytest.mark.parametrize(
    "scope,results,configs,needle",
    [
        (["alpha", "beta"], {"alpha": "alpha-v1.0.0"}, {
            "alpha": SimpleNamespace(git_tag=True, prefix="alpha-v"),
            "beta": SimpleNamespace(git_tag=True, prefix="beta-v"),
        }, "partial publication results"),
        (["ghost"], {"ghost": "alpha-v1.0.0"}, {
            "alpha": SimpleNamespace(git_tag=True, prefix="alpha-v"),
        }, "unknown project"),
    ],
)
def test_abandon_withholds_partial_or_unknown_feature_results(
    monkeypatch, tmp_path, capsys, scope, results, configs, needle,
):
    candidate, _tag, _tag_oid, _tip, _main = _install_feature_abandon_case(
        monkeypatch, tmp_path, scope=scope, results=results, configs=configs,
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert needle in capsys.readouterr().out


def test_abandon_allows_missing_pr_branch_only_after_exact_candidate_was_merged(
    monkeypatch, tmp_path, capsys,
):
    candidate, _tag, _tag_oid, _tip, _main = _install_feature_abandon_case(
        monkeypatch, tmp_path, candidate_present=False,
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.OK
    output = capsys.readouterr().out
    assert "exact published candidate tip verified in origin/main" in output


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"candidate_present": False, "main_ancestry": 1}, "candidate branch is absent but its published commit is not in origin/main"),
        ({"candidate_present": False, "pushed": False}, "no candidate-branch ownership record"),
        ({"candidate_present": True, "fetched_main": "f" * 40}, "origin branch refs changed while checking PR retirement"),
        ({"candidate_present": True, "main_ancestry": 2}, "could not verify the published candidate against origin/main"),
        ({"progress": "f" * 40}, "published feature candidate and completed progress do not name the same final commit"),
    ],
)
def test_abandon_withholds_feature_candidate_when_remote_ancestry_is_unproven(
    monkeypatch, tmp_path, capsys, kwargs, needle,
):
    candidate, _tag, _tag_oid, _tip, _main = _install_feature_abandon_case(
        monkeypatch, tmp_path, **kwargs,
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert needle in capsys.readouterr().out


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"attempted_oid": "f" * 40}, "does not match CMRU's recorded origin push"),
        ({"tag_ancestry": 1}, "is not an ancestor of the retained candidate"),
    ],
)
def test_abandon_requires_the_published_tag_to_match_the_push_attempt_and_candidate(
    monkeypatch, tmp_path, capsys, kwargs, needle,
):
    candidate, _tag, _tag_oid, _tip, _main = _install_feature_abandon_case(
        monkeypatch, tmp_path, **kwargs,
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert needle in capsys.readouterr().out


def test_abandon_feature_release_with_untagged_project_withholds_ambiguous_publication(
    monkeypatch, tmp_path, capsys,
):
    configs = {
        "alpha": SimpleNamespace(git_tag=True, prefix="alpha-v"),
        "beta": SimpleNamespace(git_tag=False, prefix="beta-v"),
    }
    candidate, _tag, _tag_oid, _tip, _main = _install_feature_abandon_case(
        monkeypatch, tmp_path, scope=["alpha", "beta"],
        results={"alpha": "alpha-v1.0.0", "beta": "source-aaaaaaaaaaaa"},
        configs=configs,
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "can publish without a Git tag" in capsys.readouterr().out


def test_abandon_feature_release_accepts_a_missing_latest_alias(tmp_path, monkeypatch, capsys):
    candidate, _tag, _tag_oid, _tip, _main = _install_feature_abandon_case(
        monkeypatch, tmp_path, latest_tag=False,
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.OK
    assert "Candidate:" in capsys.readouterr().out


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
        ("cmru-release-missing", [], "no exact managed CMRU build or release branch"),
        ("/nowhere/at/all", [], "no exact managed CMRU build or release branch"),
    ],
)
def test_abandon_branch_selection_is_exact(
    monkeypatch, tmp_path, branch, candidates, message,
):
    candidates = [
        _workspace(tmp_path, item.branch) for item in candidates
    ]
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: candidates)
    # KI-35: an unmatched release branch name is also looked up on origin; it
    # is absent there, so the exact-selection refusal still applies.
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: _loaded_config(tmp_path, {}))
    monkeypatch.setattr(transaction, "inspect_remote_candidate", lambda *_a, **_k: None)
    from cli_extended import CliFailure

    with pytest.raises(CliFailure, match=message) as refusal:
        _invoke_abandon(None, branch=branch)
    assert refusal.value.exit_code == 2


@pytest.mark.parametrize("spelling", [
    "cmru-gate-096e8817b3ad86d49801",
    "/anywhere/.worktrees/cmru-gate-096e8817b3ad86d49801",
])
def test_abandon_points_a_gate_snapshot_name_at_its_own_verb(monkeypatch, tmp_path, spelling):
    """A `cmru-gate-*` snapshot is not a transaction; name the verb that owns it."""
    _install_candidate_facts(monkeypatch, tmp_path, [])
    monkeypatch.setattr(
        cli, "_abandon_remote_candidate",
        lambda *_args, **_kwargs: pytest.fail("a gate snapshot must not be probed on origin"),
    )
    with pytest.raises(CliFailure) as failure:
        _invoke_abandon(None, branch=spelling, dry_run=True)
    assert failure.value.exit_code == 2
    assert "release-gate snapshot" in str(failure.value)
    assert "cmru gate-snapshots --remove --dry-run" in str(failure.value)


def _install_orphan_sidecars(monkeypatch, tmp_path, branch, *, local_branch_rc=1, results=None):
    _install_candidate_facts(monkeypatch, tmp_path, [])
    monkeypatch.setattr(cli, "_abandon_remote_candidate", lambda *_args, **_kwargs: None)
    scope_dir = tmp_path / "scopes"
    scope_dir.mkdir()
    token = branch.removeprefix("cmru-release-")
    files = [scope_dir / f"{token}.json", scope_dir / f"{token}.untagged-publish.json"]
    for path in files:
        path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        transaction, "release_sidecar_paths",
        lambda _root, name: sorted(p for p in scope_dir.glob(f"{name.removeprefix('cmru-release-')}.*")),
    )
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda _root, *args, **_kwargs: SimpleNamespace(returncode=local_branch_rc, stdout="", stderr=""),
    )
    monkeypatch.setattr(transaction, "read_release_scope", lambda *_: ["alpha"])
    monkeypatch.setattr(transaction, "read_release_results", lambda *_: dict(results or {}))
    forgotten = []
    monkeypatch.setattr(
        transaction, "forget_release_scope",
        lambda _root, ws: forgotten.append(ws.branch) or [p.unlink() for p in files if p.exists()],
    )
    return files, forgotten


def test_abandon_retires_orphan_sidecars_of_a_transaction_that_is_gone_everywhere(
    monkeypatch, tmp_path, capsys,
):
    branch = "cmru-release-20261027_162346-cmru-ko6hmt"
    files, forgotten = _install_orphan_sidecars(monkeypatch, tmp_path, branch, results={"cmru": "cmru-v7.0.0"})

    assert _invoke_abandon(None, branch=branch, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Retire (orphan transaction sidecars)" in out
    assert "20261027_162346-cmru-ko6hmt.untagged-publish.json" in out
    assert "recorded results: cmru:cmru-v7.0.0" in out
    assert "No branch, worktree, metadata, or remote state was changed" in out
    assert forgotten == [] and all(p.exists() for p in files)

    runtime = _Runtime(confirm=False)
    assert _invoke_abandon(None, branch=branch, dry_run=False, runtime=runtime) == 0
    assert forgotten == [] and runtime.confirmed

    assert _invoke_abandon(None, branch=branch, dry_run=False, yes=True) == 0
    assert forgotten == [branch]
    assert not any(p.exists() for p in files)
    assert "Retired orphan transaction sidecars" in capsys.readouterr().out
    # Nothing left: the ordinary "no such transaction" refusal is back.
    with pytest.raises(CliFailure) as failure:
        _invoke_abandon(None, branch=branch, dry_run=True)
    assert failure.value.exit_code == 2


def test_abandon_withholds_orphan_sidecars_while_a_local_branch_still_exists(monkeypatch, tmp_path):
    branch = "cmru-release-20261027_144853-cmru-ko4six"
    files, forgotten = _install_orphan_sidecars(monkeypatch, tmp_path, branch, local_branch_rc=0)
    with pytest.raises(CliFailure) as failure:
        _invoke_abandon(None, branch=branch, dry_run=False, yes=True)
    assert failure.value.exit_code == exit_codes.REFUSED
    assert "local branch" in str(failure.value)
    assert forgotten == [] and all(p.exists() for p in files)


def test_abandon_reports_sidecars_an_old_forget_leaves_behind(monkeypatch, tmp_path):
    branch = "cmru-release-20261027_144853-cmru-ko4six"
    files, _forgotten = _install_orphan_sidecars(monkeypatch, tmp_path, branch)
    monkeypatch.setattr(transaction, "forget_release_scope", lambda *_: None)
    with pytest.raises(CliFailure) as failure:
        _invoke_abandon(None, branch=branch, dry_run=False, yes=True)
    assert failure.value.exit_code == 1
    assert "left in place" in str(failure.value)
    assert all(p.exists() for p in files)


def test_release_sidecar_paths_lists_only_the_tokens_regular_files(tmp_path, monkeypatch):
    scope_dir = tmp_path / "scopes"
    scope_dir.mkdir()
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: scope_dir)
    (scope_dir / "20261027_162346-cmru-ko6hmt.json").write_text("[]")
    (scope_dir / "20261027_162346-cmru-ko6hmt.progress").write_text("x")
    (scope_dir / "20261027_162346-cmru-ko6hmt2.json").write_text("[]")  # another token
    (scope_dir / "20261027_162346-cmru-ko6hmt.dir").mkdir()
    listed = transaction.release_sidecar_paths(tmp_path, "cmru-release-20261027_162346-cmru-ko6hmt")
    assert [p.name for p in listed] == [
        "20261027_162346-cmru-ko6hmt.json", "20261027_162346-cmru-ko6hmt.progress",
    ]
    assert transaction.release_sidecar_paths(tmp_path, "cmru-release-20261027_000000-x-nothere") == []
    with pytest.raises(ValueError):
        transaction.release_sidecar_paths(tmp_path, "feature/not-a-transaction")


def test_abandon_relative_path_is_refused_with_the_absolute_path_hint(monkeypatch, tmp_path):
    build = _workspace(tmp_path, "cmru-build-20260924_120000-alpha-ab12cd")
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: [build])
    from cli_extended import CliFailure

    relative = f".worktrees/{Path(build.path).name}"
    with pytest.raises(CliFailure, match="pass the absolute path shown by `cmru worktrees`") as refusal:
        _invoke_abandon(None, branch=relative)
    assert refusal.value.exit_code == 2
    # A bare unknown branch name (no path separator) gets no path hint.
    with pytest.raises(CliFailure) as plain:
        _invoke_abandon(None, branch="nope")
    assert "absolute path" not in str(plain.value)


@pytest.mark.parametrize("by", ["branch", "path"])
def test_abandon_discards_a_retained_build_worktree_by_branch_or_path(
    monkeypatch, tmp_path, capsys, by,
):
    """The former ``cleanup --discard-build-worktree`` (redesign B6)."""
    build = _workspace(tmp_path, "cmru-build-20260924_120000-alpha-ab12cd")
    release = _workspace(tmp_path, "cmru-release-20260924_120001-beta-bc23de")
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: [release, build])
    calls = []

    def discard(root, path, *, dry_run, expected_workspace=None):
        calls.append((dry_run, expected_workspace))
        return build

    monkeypatch.setattr(transaction, "discard_build_workspace", discard)
    selector = build.branch if by == "branch" else str(build.path)

    # dry-run: previews, mutates nothing, never prompts
    runtime = _Runtime()
    assert _invoke_abandon(None, branch=selector, runtime=runtime) == 0
    assert calls == [(True, None)] and runtime.confirmed == []
    assert "Would discard retained build worktree" in capsys.readouterr().out

    # declined confirmation: nothing is discarded
    calls.clear()
    assert _invoke_abandon(
        None, branch=selector, dry_run=False, runtime=_Runtime(confirm=False),
    ) == 0
    assert calls == [(True, None)]

    # --yes: discards exactly the previewed worktree, without prompting
    calls.clear()
    runtime = _Runtime()
    assert _invoke_abandon(
        None, branch=selector, dry_run=False, yes=True, runtime=runtime,
    ) == 0
    assert calls == [(True, None), (False, build)] and runtime.confirmed == []
    assert "Discarded retained build worktree" in capsys.readouterr().out


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
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )
    monkeypatch.setattr(transaction, "abandon_workspace", lambda *_, **__: pytest.fail("blocked preview mutated"))

    assert _invoke_abandon(candidate) == exit_codes.REFUSED
    output = capsys.readouterr().out
    assert "Withheld:" in output
    assert "external publication state unknown for external" in output
    assert "[DRY RUN] No branch, worktree, metadata, or remote state was changed." in output


def test_abandon_accepts_interactive_confirmation_for_verified_plan(monkeypatch, tmp_path):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    removed = []
    monkeypatch.setattr(transaction, "abandon_workspace", lambda _root, workspace, **_kwargs: removed.append(workspace))
    runtime = _Runtime(confirm=True)

    assert _invoke_abandon(candidate, branch=candidate.branch, dry_run=False, runtime=runtime) == 0
    assert removed == [candidate]
    assert runtime.confirmed


def test_abandon_holds_release_lock_across_inspection_confirmation_and_removal(
    monkeypatch, tmp_path,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch)
    events = []

    @contextmanager
    def release_lock(_root):
        events.append("lock")
        yield
        events.append("unlock")

    monkeypatch.setattr(transaction, "release_lock", release_lock)
    monkeypatch.setattr(
        transaction, "list_cmru_workspaces",
        lambda _root: events.append("inspect") or [candidate],
    )

    class Runtime:
        def confirm(self, _prompt):
            events.append("confirm")
            return True

    def abandon(_root, workspace, **_kwargs):
        assert workspace is candidate
        assert "lock" in events and "unlock" not in events
        events.append("abandon")

    monkeypatch.setattr(transaction, "abandon_workspace", abandon)

    assert _invoke_abandon(
        candidate, branch=candidate.branch, dry_run=False, runtime=Runtime(),
    ) == 0
    assert events == ["lock", "inspect", "confirm", "abandon", "unlock"]


@pytest.mark.parametrize("changed_ref", ["candidate", "tag"])
def test_abandon_rechecks_remote_state_after_confirmation_before_mutating(
    monkeypatch, tmp_path, changed_ref,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: "b" * 40)
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )
    calls = {"heads": 0, "tags": 0}

    def run(argv, **_kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            calls["heads"] += 1
            candidate_oid = "a" * 40
            if changed_ref == "candidate" and calls["heads"] == 2:
                candidate_oid = "c" * 40
            return subprocess.CompletedProcess(
                argv, 0,
                candidate_oid + "\trefs/heads/" + candidate.branch + "\n"
                + "d" * 40 + "\trefs/heads/main\n",
                "",
            )
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            calls["tags"] += 1
            tag_output = ""
            if changed_ref == "tag" and calls["tags"] == 2:
                tag_output = "e" * 40 + "\trefs/tags/alpha-v9\n"
            return subprocess.CompletedProcess(argv, 0, tag_output, "")
        if argv[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(argv, 1 if argv[3] == "b" * 40 else 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda *_args, **_kwargs: pytest.fail("stale remote state reached abandonment"),
    )
    runtime = _Runtime(confirm=True)

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(
            candidate, branch=candidate.branch, dry_run=False, runtime=runtime,
        )

    assert runtime.confirmed
    expected_tag_lookups = 1 if changed_ref == "candidate" else 2
    assert calls == {"heads": 2, "tags": expected_tag_lookups}


def test_abandon_withholds_unrequested_origin_branch_refs(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate,
        heads={
            "refs/heads/main": "b" * 40,
            "refs/heads/other": "c" * 40,
        },
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    output = capsys.readouterr().out
    assert "origin branch lookup returned unexpected ref(s)" in output
    assert "refs/heads/other" in output


def _real_git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _legacy_candidate_repo(tmp_path: Path):
    """A REAL repository: old release tag < base == candidate tip, plus a side commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _real_git(repo, "init", "-b", "main")
    _real_git(repo, "config", "user.email", "test@cmru.test")
    _real_git(repo, "config", "user.name", "CMRU test")
    for index, name in enumerate(("first", "base")):
        (repo / f"{name}.txt").write_text(name, encoding="utf-8")
        _real_git(repo, "add", ".")
        _real_git(repo, "commit", "-m", name)
        if index == 0:
            first = _real_git(repo, "rev-parse", "HEAD")
    base = _real_git(repo, "rev-parse", "HEAD")
    branch = "cmru-release-20260924_120000-alpha-ab12cd"
    _real_git(repo, "branch", branch, base)
    return repo, branch, first, base


def _legacy_inspect(monkeypatch, repo, branch, base, tag_oid):
    candidate = _workspace(repo, branch, base=base)
    _install_abandon_inspection(
        monkeypatch, repo, candidate, snapshot=None,
        remote_tags={"refs/tags/alpha-v9": tag_oid},
        heads={"refs/heads/main": base},
        real_git=True,
    )
    return candidate


def test_rel03_legacy_abandon_ignores_an_earlier_release_tag_below_the_base(
    monkeypatch, tmp_path, capsys,
):
    # Every earlier release tag is reachable from the candidate; one that is a
    # strict ancestor of the transaction base cannot have been pushed by it.
    repo, branch, first, base = _legacy_candidate_repo(tmp_path)
    candidate = _legacy_inspect(monkeypatch, repo, branch, base, first)

    assert _invoke_abandon(candidate, branch=branch) == 0
    assert "no pre-attempt origin tag snapshot" not in capsys.readouterr().out


def test_rel03_legacy_abandon_keeps_a_tag_exactly_at_the_base_suspicious(
    monkeypatch, tmp_path, capsys,
):
    repo, branch, _first, base = _legacy_candidate_repo(tmp_path)
    candidate = _legacy_inspect(monkeypatch, repo, branch, base, base)

    assert _invoke_abandon(candidate, branch=branch) == exit_codes.REFUSED
    assert "no pre-attempt origin tag snapshot" in capsys.readouterr().out


def test_rel03_legacy_abandon_refuses_a_tag_on_a_candidate_only_commit(
    monkeypatch, tmp_path, capsys,
):
    repo, branch, _first, base = _legacy_candidate_repo(tmp_path)
    _real_git(repo, "checkout", "-q", branch)
    (repo / "candidate.txt").write_text("candidate", encoding="utf-8")
    _real_git(repo, "add", ".")
    _real_git(repo, "commit", "-m", "release inputs")
    new_tip = _real_git(repo, "rev-parse", "HEAD")
    _real_git(repo, "checkout", "-q", "main")
    candidate = _legacy_inspect(monkeypatch, repo, branch, base, new_tip)

    assert _invoke_abandon(candidate, branch=branch) == exit_codes.REFUSED
    assert "no pre-attempt origin tag snapshot" in capsys.readouterr().out


def test_abandon_legacy_snapshot_refuses_when_a_remote_tag_cannot_be_inspected(
    monkeypatch, tmp_path, capsys,
):
    repo, branch, _first, base = _legacy_candidate_repo(tmp_path)
    candidate = _legacy_inspect(monkeypatch, repo, branch, base, "d" * 40)

    assert _invoke_abandon(candidate, branch=branch) == exit_codes.REFUSED
    assert "could not inspect remote tag alpha-v9" in capsys.readouterr().out


def test_rel03_legacy_abandon_ignores_a_tag_that_is_not_on_the_candidate(
    monkeypatch, tmp_path, capsys,
):
    # A tag on a commit the candidate cannot reach was not made by this transaction.
    repo, branch, _first, base = _legacy_candidate_repo(tmp_path)
    (repo / "side.txt").write_text("side", encoding="utf-8")
    _real_git(repo, "add", ".")
    _real_git(repo, "commit", "-m", "side commit on main")
    side = _real_git(repo, "rev-parse", "HEAD")
    candidate = _legacy_inspect(monkeypatch, repo, branch, base, side)

    assert _invoke_abandon(candidate, branch=branch) == 0
    assert "no pre-attempt origin tag snapshot" not in capsys.readouterr().out


def test_rel03_legacy_abandon_refuses_when_the_base_ancestry_cannot_be_inspected(
    monkeypatch, tmp_path, capsys,
):
    repo, branch, first, base = _legacy_candidate_repo(tmp_path)
    candidate = _legacy_inspect(monkeypatch, repo, branch, base, first)
    real = cli.run_local_git

    def fake(path, *args, **kwargs):
        if args[:3] == ("merge-base", "--is-ancestor", first) and args[-1] == base:
            return subprocess.CompletedProcess(args, 128, "", "fatal")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(cli, "run_local_git", fake)

    assert _invoke_abandon(candidate, branch=branch) == exit_codes.REFUSED
    assert "could not inspect remote tag alpha-v9" in capsys.readouterr().out


def test_abandon_refuses_selected_scope_tag_changes_since_snapshot(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, snapshot={},
        remote_tags={"refs/tags/alpha-v9": "d" * 40},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "selected-scope release tag refs that changed" in capsys.readouterr().out


def test_abandon_refuses_a_baseline_tag_that_disappeared_from_origin(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    tag_ref = "refs/tags/alpha-v9"
    oid = "d" * 40
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate,
        snapshot={tag_ref: oid}, attempts={tag_ref: oid},
        local_tags={tag_ref: oid}, remote_tags={},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    output = capsys.readouterr().out
    assert "selected-scope release tag refs that changed" in output
    assert "origin/refs/tags/alpha-v9" in output


def test_abandon_a_scope_without_a_git_tag_project_and_without_a_record_stays_withheld(
    monkeypatch, tmp_path, capsys,
):
    # Was "no usable Git tag prefix" before KI-98; the prefix is no longer the blocker.
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate,
        configs={"alpha": SimpleNamespace(git_tag=False)},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "can publish without a Git tag" in capsys.readouterr().out


def test_abandon_refuses_scope_with_a_project_that_can_publish_without_a_tag(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, scope=["alpha", "beta"],
        configs={
            "alpha": SimpleNamespace(git_tag=True),
            "beta": SimpleNamespace(git_tag=False),
        },
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "can publish without a Git tag" in capsys.readouterr().out


@pytest.mark.parametrize(
    "progress, merge_code, expected",
    [
        ("c" * 40, 0, "origin/main contains the recorded release progress"),
        ("c" * 40, 2, "could not determine whether release progress reached origin/main"),
    ],
)
def test_abandon_refuses_promoted_or_unclassifiable_release_progress(
    monkeypatch, tmp_path, capsys, progress, merge_code, expected,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, merge_codes={progress: merge_code},
    )
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: progress)

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert expected in capsys.readouterr().out


def test_abandon_refuses_candidate_ref_not_ancestral_to_its_worktree(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    candidate_oid = "c" * 40
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate,
        heads={
            "refs/heads/main": "b" * 40,
            "refs/heads/" + candidate.branch: candidate_oid,
        },
        merge_codes={candidate_oid: 2},
    )
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "not a known ancestor" in capsys.readouterr().out


def test_abandon_refuses_tag_attempt_metadata_outside_project_scope(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, snapshot={},
        attempts={"refs/tags/beta-v9": "d" * 40},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "outside the recorded project scope" in capsys.readouterr().out


@pytest.mark.parametrize(
    "snapshot, attempted_oid, expected",
    [
        ({}, "e" * 40, "changed after its CMRU push attempt"),
        (None, "d" * 40, "has no origin tag baseline"),
    ],
)
def test_abandon_refuses_ambiguous_local_tag_attempts(
    monkeypatch, tmp_path, capsys, snapshot, attempted_oid, expected,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    local_ref = "refs/tags/alpha-v9"
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, snapshot=snapshot,
        attempts={local_ref: attempted_oid}, local_tags={local_ref: "d" * 40},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert expected in capsys.readouterr().out


def test_abandon_ignores_latest_and_origin_present_local_tags(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    origin_ref = "refs/tags/alpha-v9"
    latest_ref = "refs/tags/alpha-latest"
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate,
        snapshot={origin_ref: "d" * 40}, attempts=None,
        local_tags={origin_ref: "d" * 40, latest_ref: "e" * 40},
        remote_tags={origin_ref: "d" * 40},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == 0
    output = capsys.readouterr().out
    assert "Candidate:" in output
    assert "local-only release tag" not in output


def test_abandon_rechecks_local_tags_after_confirmation(monkeypatch, tmp_path):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(monkeypatch, tmp_path, candidate, snapshot={})
    snapshots = iter([{}, {"refs/tags/alpha-v9": "d" * 40}])
    monkeypatch.setattr(
        transaction, "list_local_tag_refs", lambda *_: next(snapshots),
    )
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda *_args, **_kwargs: pytest.fail("stale local tags reached abandonment"),
    )

    with pytest.raises(CliFailure, match="local release tags changed after confirmation"):
        _invoke_abandon(
            candidate, branch=candidate.branch, dry_run=False, yes=True,
        )


def test_abandon_refuses_when_origin_branch_recheck_fails(monkeypatch, tmp_path):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(monkeypatch, tmp_path, candidate, snapshot={})
    calls = 0

    def run_remote(_root, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            returncode=0 if calls == 1 else 2,
            stdout="b" * 40 + "\trefs/heads/main\n",
            stderr="offline",
        )

    monkeypatch.setattr(cli, "run_remote_git", run_remote)
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda *_args, **_kwargs: pytest.fail("unverified origin state reached abandonment"),
    )

    with pytest.raises(CliFailure, match="could not recheck origin branch state"):
        _invoke_abandon(
            candidate, branch=candidate.branch, dry_run=False, yes=True,
        )
    assert calls == 2


def test_abandon_refuses_while_a_local_release_holds_the_lock(monkeypatch, tmp_path):
    @contextmanager
    def occupied_lock(_root):
        raise transaction.ReleaseLockHeld("Another cmru release transaction is already running.")
        yield

    monkeypatch.setattr(transaction, "release_lock", occupied_lock)
    monkeypatch.setattr(
        transaction, "list_cmru_workspaces",
        lambda _root: pytest.fail("candidate inspection ran without the release lock"),
    )
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)

    with pytest.raises(CliFailure, match="Another cmru release transaction is already running"):
        _invoke_abandon(None, dry_run=False)


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
    stdout = ""
    if remote_code == 0:
        if remote_present:
            stdout += "a" * 40 + "\trefs/heads/" + candidate.branch + "\n"
        stdout += "b" * 40 + "\trefs/heads/main\n"
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **_kw: subprocess.CompletedProcess(
            argv, remote_code, stdout, "offline" if remote_code else "",
        ),
    )

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    assert message in capsys.readouterr().out


def test_abandon_refuses_successful_remote_lookup_without_origin_main(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch, heads="")

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)

    assert "origin/main ref is missing" in capsys.readouterr().out


def test_abandon_refuses_malformed_origin_main_object_id(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    _read_only_git(monkeypatch, heads="bad-object-id\trefs/heads/main\n")

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)

    assert "origin branch lookup returned a malformed ref record" in capsys.readouterr().out


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
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )
    def run(argv, **kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            heads = (
                "a" * 40 + "\trefs/heads/" + candidate.branch + "\n"
                + "b" * 40 + "\trefs/heads/main\n"
            )
            return subprocess.CompletedProcess(argv, 0, heads, "")
        if argv[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(argv, candidate_result, "", "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)
    monkeypatch.setattr(cli.subprocess, "run", run)

    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    assert message in capsys.readouterr().out


def test_abandon_refuses_malformed_remote_branch_records(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])

    _read_only_git(
        monkeypatch,
        heads="malformed remote-ref row\n" + "a" * 40 + "\trefs/heads/main\n",
    )
    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    assert "malformed ref record" in capsys.readouterr().out


def test_abandon_parses_remote_refs_and_accepts_unpromoted_candidate(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])

    # Exercise successful remote graph checks and pin the subprocess
    # boundary: all Git probes need captured text and explicit status handling.
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: "b" * 40)
    branch_refs = (
        "a" * 40 + "\trefs/heads/" + candidate.branch + "\n"
        + "c" * 40 + "\trefs/heads/main\n"
    )
    tag_refs = "d" * 40 + "\trefs/tags/alpha-v9\n"
    monkeypatch.setattr(
        transaction, "read_release_tag_snapshot",
        lambda *_: {"refs/tags/alpha-v9": "d" * 40},
    )
    responses = iter([
        (0, branch_refs),  # candidate and main refs are parseable
        (0, ""),           # candidate ref is an ancestor of the local branch
        (1, ""),           # recorded progress is not yet on origin/main
        (0, tag_refs),      # remote tags are readable and match the snapshot
    ])
    calls = []

    def run(argv, **kwargs):
        assert set(kwargs) == {"cwd", "capture_output", "text", "check", "env"}
        assert kwargs == {
            "cwd": tmp_path,
            "capture_output": True,
            "text": True,
            "check": False,
            "env": kwargs["env"],
        }
        assert isinstance(kwargs["env"], dict)
        assert "GITHUB_PUSH_PAT" not in kwargs["env"]
        assert "GITHUB_TOKEN" not in kwargs["env"]
        calls.append(argv)
        code, stdout = next(responses)
        return subprocess.CompletedProcess(argv, code, stdout, "")

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert _invoke_abandon(candidate, branch=candidate.branch) == 0
    assert "Candidate:" in capsys.readouterr().out
    assert len(calls) == 4


def test_abandon_refuses_promoted_or_indeterminate_remote_main(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd", base="a" * 40)
    object.__setattr__(candidate.context, "base_commit", "b" * 40)
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: "c" * 40)
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )

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
        ("malformed line\n", 0, "malformed ref record"),
        ("", 2, "cannot inspect origin release tags"),
    ):
        def run(argv, **kwargs):
            if argv[:3] == ["git", "ls-remote", "--heads"]:
                heads = "b" * 40 + "\trefs/heads/main\n"
                return subprocess.CompletedProcess(argv, 0, heads, "")
            if argv[:3] == ["git", "ls-remote", "--tags"]:
                return subprocess.CompletedProcess(argv, result, tags, "")
            if argv[:2] == ["git", "merge-base"]:
                return subprocess.CompletedProcess(argv, 1, "", "")
            raise AssertionError(argv)
        monkeypatch.setattr(cli.subprocess, "run", run)
        with pytest.raises(CliFailure, match="nothing was abandoned"):
            _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
        assert expected in capsys.readouterr().out

    tag_output = (
        "d" * 40 + "\trefs/tags/alpha-v9\n"
        + "e" * 40 + "\trefs/tags/alpha-v9^{}\n"
    )
    def run_tag(argv, **kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            heads = "b" * 40 + "\trefs/heads/main\n"
            return subprocess.CompletedProcess(argv, 0, heads, "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, tag_output, "")
        if argv[:2] == ["git", "merge-base"]:
            # The tag is on the candidate and descends from the recorded base.
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)
    monkeypatch.setattr(cli.subprocess, "run", run_tag)
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )
    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    output = capsys.readouterr().out
    assert "origin contains selected-scope release tag refs that changed during this transaction" in output
    assert "origin/refs/tags/alpha-v9" in output


def test_abandon_refuses_a_new_release_tag_at_the_original_snapshot_commit(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd", base="a" * 40)
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )
    tag_output = "a" * 40 + "\trefs/tags/alpha-v9\n"
    merge_calls = 0

    def run(argv, **_kwargs):
        nonlocal merge_calls
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(
                argv, 0, "b" * 40 + "\trefs/heads/main\n", "",
            )
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, tag_output, "")
        if argv[:2] == ["git", "merge-base"]:
            merge_calls += 1
            return subprocess.CompletedProcess(argv, 1 if merge_calls == 1 else 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
    output = capsys.readouterr().out
    assert "alpha-v9" in output
    assert "selected-scope release tag refs that changed during this transaction" in output


def test_abandon_allows_a_base_commit_tag_proven_to_precede_the_attempt(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd", base="a" * 40)
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )
    monkeypatch.setattr(
        transaction, "read_release_tag_snapshot",
        lambda *_: {"refs/tags/alpha-v8": "a" * 40},
    )

    def run(argv, **_kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(
                argv, 0, "b" * 40 + "\trefs/heads/main\n", "",
            )
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(
                argv, 0, "a" * 40 + "\trefs/tags/alpha-v8\n", "",
            )
        if argv[:2] == ["git", "merge-base"]:
            is_base_comparison = argv[3:] == ["a" * 40, "b" * 40]
            return subprocess.CompletedProcess(argv, 1 if is_base_comparison else 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert _invoke_abandon(candidate, branch=candidate.branch) == 0
    assert "Candidate:" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["candidate-unknown", "not-on-candidate"])
def test_abandon_distinguishes_remote_tag_candidate_results(
    monkeypatch, tmp_path, capsys, mode,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd", base="a" * 40)
    _install_candidate_facts(monkeypatch, tmp_path, [candidate])
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )
    tag_output = (
        "d" * 40 + "\trefs/tags/alpha-v9\n"
        if mode == "candidate-unknown"
        else "d" * 40 + "\trefs/tags/beta-v9\n"
    )
    if mode == "candidate-unknown":
        monkeypatch.setattr(transaction, "read_release_tag_snapshot", lambda *_: None)
    merge_calls = 0

    def run(argv, **_kwargs):
        nonlocal merge_calls
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            heads = "b" * 40 + "\trefs/heads/main\n"
            return subprocess.CompletedProcess(argv, 0, heads, "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, tag_output, "")
        if argv[:2] == ["git", "merge-base"]:
            merge_calls += 1
            if merge_calls == 1:
                # Progress equals the snapshot base and is not promoted to this
                # independent origin/main commit.
                return subprocess.CompletedProcess(argv, 1, "", "")
            if mode == "candidate-unknown":
                return subprocess.CompletedProcess(argv, 2, "", "bad object")
            return subprocess.CompletedProcess(argv, 1, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(cli.subprocess, "run", run)
    if mode == "not-on-candidate":
        assert _invoke_abandon(candidate) == 0
        assert "Candidate:" in capsys.readouterr().out
    else:
        with pytest.raises(CliFailure, match="nothing was abandoned"):
            _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
        assert "could not inspect remote tag alpha-v9" in capsys.readouterr().out


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

    if failure == "config":
        with pytest.raises(CliFailure, match="cannot load project release policy") as exc:
            _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
        assert expected in str(exc.value)
    else:
        with pytest.raises(CliFailure, match="nothing was abandoned"):
            _invoke_abandon(candidate, branch=candidate.branch, dry_run=False)
        assert expected in capsys.readouterr().out


# --- KI-98: a scope with no git_tag project (mdt-cockpit) is abandonable ------

_UNTAGGED_BRANCH = "cmru-release-20261027_103450-mdt-cockpit-8b3px3"


def _untagged_case(monkeypatch, tmp_path, *, record, scope=None, configs=None, **kwargs):
    candidate = _workspace(tmp_path, _UNTAGGED_BRANCH)
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, scope=scope or ["mdt-cockpit"],
        configs=configs or {"mdt-cockpit": SimpleNamespace(git_tag=False)},
        snapshot={}, **kwargs,
    )
    monkeypatch.setattr(
        transaction, "read_untagged_publication_record", lambda *_: record, raising=False,
    )
    return candidate


def test_ki98_git_tag_false_failed_candidate_is_abandoned(monkeypatch, tmp_path, capsys):
    candidate = _untagged_case(monkeypatch, tmp_path, record=[])
    calls = []
    monkeypatch.setattr(
        transaction, "abandon_workspace", lambda _root, ws, **kw: calls.append(kw),
    )

    assert _invoke_abandon(candidate, branch=candidate.branch, dry_run=False, yes=True) == 0

    assert "Withheld" not in capsys.readouterr().out
    # An empty prefix set means "no tag refs", and is handed down as such.
    assert calls[0]["release_tag_prefixes"] == ()
    assert calls[0]["expected_remote_tag_refs"] == {}
    assert calls[0]["expected_local_tag_refs"] == {}
    assert calls[0]["local_tags_to_remove"] == {}


def test_ki98_dry_run_reports_the_untagged_candidate_without_mutating(
    monkeypatch, tmp_path, capsys,
):
    candidate = _untagged_case(monkeypatch, tmp_path, record=[])
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda *_a, **_k: pytest.fail("dry-run called abandon_workspace"),
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == 0

    output = capsys.readouterr().out
    assert f"Candidate: {candidate.branch}" in output
    assert "transaction scope: mdt-cockpit" in output
    assert "local release tags removed: none" in output
    assert "Withheld" not in output
    assert "No branch, worktree, metadata, or remote state was changed" in output


def test_ki98_untagged_project_whose_publication_began_stays_withheld(
    monkeypatch, tmp_path, capsys,
):
    candidate = _untagged_case(monkeypatch, tmp_path, record=["mdt-cockpit"])

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "can publish without a Git tag" in capsys.readouterr().out


def test_ki98_untagged_scope_without_a_publication_record_stays_withheld(
    monkeypatch, tmp_path, capsys,
):
    # A transaction from before the record existed cannot prove non-publication.
    candidate = _untagged_case(monkeypatch, tmp_path, record=None)

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "can publish without a Git tag" in capsys.readouterr().out


def test_ki98_tag_attempt_in_a_scope_without_tagged_projects_is_still_refused(
    monkeypatch, tmp_path, capsys,
):
    candidate = _untagged_case(
        monkeypatch, tmp_path, record=[], attempts={"refs/tags/mdt-cockpit-v1": "d" * 40},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "outside the recorded project scope" in capsys.readouterr().out


def test_ki98_mixed_scope_still_refuses_when_a_tagged_project_lacks_a_prefix(
    monkeypatch, tmp_path, capsys,
):
    candidate = _untagged_case(
        monkeypatch, tmp_path, record=[], scope=["alpha", "mdt-cockpit"],
        configs={
            "alpha": SimpleNamespace(git_tag=True),
            "mdt-cockpit": SimpleNamespace(git_tag=False),
        },
    )
    monkeypatch.setattr(cli, "_release_tag_prefixes_for_scope", lambda *_a: ())

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "no usable Git tag prefix" in capsys.readouterr().out


def test_ki98_mixed_scope_with_unpublished_untagged_project_uses_only_tagged_prefixes(
    monkeypatch, tmp_path, capsys,
):
    candidate = _untagged_case(
        monkeypatch, tmp_path, record=[], scope=["alpha", "mdt-cockpit"],
        configs={
            "alpha": SimpleNamespace(git_tag=True, prefix="alpha-v"),
            "mdt-cockpit": SimpleNamespace(git_tag=False),
        },
    )
    calls = []
    monkeypatch.setattr(
        transaction, "abandon_workspace", lambda _root, ws, **kw: calls.append(kw),
    )

    assert _invoke_abandon(candidate, branch=candidate.branch, dry_run=False, yes=True) == 0
    assert calls[0]["release_tag_prefixes"] == ("alpha-v",)


def test_ki98_abandon_workspace_with_empty_prefixes_ignores_unrelated_tags(
    monkeypatch, tmp_path,
):
    # Pre-fix, an empty prefix tuple meant "ALL tags" in the recheck, so the
    # empty expected snapshot never matched a real origin with other tags.
    _real_git(tmp_path, "init", "-q")
    other = "d" * 40
    workspace = SimpleNamespace(
        repo_root=tmp_path, path=tmp_path / "workspace", branch="cmru-release-test",
        context=None,
    )

    def run_remote(_root, *args, **_kwargs):
        out = f"{other}\trefs/tags/pwmcp-v1\n" if args[:3] == ("ls-remote", "--tags", "origin") else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    removed = []
    monkeypatch.setattr(transaction, "run_remote_git", run_remote)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: False)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(transaction, "remove_workspace", lambda ws: removed.append(ws))
    monkeypatch.setattr(transaction, "forget_release_scope", lambda *_: None)
    monkeypatch.setattr(
        transaction, "list_local_tag_refs", lambda *_: {"refs/tags/pwmcp-v1": other},
    )
    monkeypatch.setattr(
        transaction, "run_local_git",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    transaction.abandon_workspace(
        tmp_path, workspace, expected_remote_tag_refs={}, release_tag_prefixes=(),
        expected_local_tag_refs={},
    )
    assert removed == [workspace]


def test_ki98_untagged_publication_record_roundtrip_and_forget(tmp_path):
    _real_git(tmp_path, "init", "-q")
    ws = _workspace(tmp_path, _UNTAGGED_BRANCH)
    assert transaction.read_untagged_publication_record(tmp_path, ws) is None
    transaction.start_untagged_publication_record(tmp_path, ws)
    assert transaction.read_untagged_publication_record(tmp_path, ws) == []
    transaction.mark_untagged_publication_started(tmp_path, ws, "mdt-cockpit")
    transaction.mark_untagged_publication_started(tmp_path, ws, "mdt-cockpit")  # idempotent
    transaction.start_untagged_publication_record(tmp_path, ws)  # a retry keeps it
    assert transaction.read_untagged_publication_record(tmp_path, ws) == ["mdt-cockpit"]
    transaction.forget_release_scope(tmp_path, ws)
    assert transaction.read_untagged_publication_record(tmp_path, ws) is None


def test_ki98_origin_only_candidate_branch_is_retired(monkeypatch, tmp_path, capsys):
    # Characterisation: the KI-35 path already handles an origin-only branch whose
    # commits are all in origin/main (it needs no tag prefix). A branch absent from
    # origin is the only way to get "no exact managed ... named".
    branch = "cmru-release-20261027_110937-mdt-cockpit-zn0uvi"
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: [])
    monkeypatch.setattr(cli, "_resolve_config", lambda _p: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _p: _loaded_config(tmp_path, {}))
    candidate = transaction.RemoteCandidate(branch, "a" * 40, "b" * 40, 0)
    monkeypatch.setattr(transaction, "inspect_remote_candidate", lambda *_a, **_k: candidate)
    retired = []
    monkeypatch.setattr(
        transaction, "retire_remote_candidate", lambda _root, c, **_k: retired.append(c.branch),
    )

    assert _invoke_abandon(None, branch=branch, dry_run=True) == 0
    assert retired == []
    assert _invoke_abandon(None, branch=branch, dry_run=False, yes=True) == 0
    assert retired == [branch]
    assert "Retire (origin-only candidate)" in capsys.readouterr().out


# --- KI-98 review follow-up --------------------------------------------------

_REAL_READ_UNTAGGED = transaction.read_untagged_publication_record


def _real_record_case(monkeypatch, tmp_path, **kwargs):
    """An untagged-scope candidate whose publication record is read from disk."""
    _real_git(tmp_path, "init", "-q")
    candidate = _workspace(tmp_path, _UNTAGGED_BRANCH)
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, scope=["mdt-cockpit"],
        configs={"mdt-cockpit": SimpleNamespace(git_tag=False)}, snapshot={}, **kwargs,
    )
    monkeypatch.setattr(transaction, "read_untagged_publication_record", _REAL_READ_UNTAGGED)
    return candidate


def test_ki98_resumed_legacy_transaction_never_gets_a_record_and_stays_withheld(
    monkeypatch, tmp_path, capsys,
):
    # An old cmru published untagged X, crashed before write_release_result; the
    # NEW cmru resumes (its progress seed already exists) and fails at a gate
    # before X is marked. Abandon must still withhold.
    candidate = _real_record_case(monkeypatch, tmp_path)
    transaction.write_release_progress(tmp_path, candidate, "a" * 40)  # the old run's seed
    transaction.start_untagged_publication_record(tmp_path, candidate)
    assert _REAL_READ_UNTAGGED(tmp_path, candidate) is None
    transaction.mark_untagged_publication_started(tmp_path, candidate, "mdt-cockpit")
    assert _REAL_READ_UNTAGGED(tmp_path, candidate) is None  # marking never upgrades

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "can publish without a Git tag" in capsys.readouterr().out


def test_ki98_brand_new_transaction_gets_a_record_and_is_abandonable(
    monkeypatch, tmp_path, capsys,
):
    candidate = _real_record_case(monkeypatch, tmp_path)
    transaction.start_untagged_publication_record(tmp_path, candidate)
    transaction.write_release_progress(tmp_path, candidate, "a" * 40)
    assert _REAL_READ_UNTAGGED(tmp_path, candidate) == []

    assert _invoke_abandon(candidate, branch=candidate.branch) == 0
    assert "Withheld" not in capsys.readouterr().out


@pytest.mark.parametrize("content", ["{not json", '{"started": "mdt-cockpit"}', "[]", '{"started": [1]}'])
def test_ki98_corrupt_real_record_reaching_abandon_is_withheld(
    monkeypatch, tmp_path, capsys, content,
):
    candidate = _real_record_case(monkeypatch, tmp_path)
    transaction._ensure_scope_dir(tmp_path)
    transaction._untagged_publication_path(tmp_path, candidate).write_text(content)

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "untagged publication record" in capsys.readouterr().out


def test_ki98_untagged_source_already_in_origin_main_is_withheld(
    monkeypatch, tmp_path, capsys,
):
    # --no-build promotes the source with no mark; crash before the progress write.
    candidate = _untagged_case(
        monkeypatch, tmp_path, record=[],
        merge_codes={f"refs/heads/{_UNTAGGED_BRANCH}": 0},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "already in origin/main" in capsys.readouterr().out


def test_ki98_untagged_candidate_still_at_its_base_has_no_promoted_source_and_is_abandonable(
    monkeypatch, tmp_path, capsys,
):
    # The candidate branch never advanced past the release base, so nothing could have been promoted:
    # the origin/main ancestry question is not even asked.
    candidate = _untagged_case(monkeypatch, tmp_path, record=[])
    asked = []

    def run_local_git(_root, *args, **_kwargs):
        asked.append(args)
        if args[:1] == ("rev-parse",):
            return SimpleNamespace(returncode=0, stdout=candidate.base + "\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_local_git", run_local_git)

    assert _invoke_abandon(candidate, branch=candidate.branch) == 0
    assert "Withheld" not in capsys.readouterr().out
    assert not any(
        a[:2] == ("merge-base", "--is-ancestor") and a[2] == f"refs/heads/{candidate.branch}"
        for a in asked
    )


def test_ki98_untagged_source_promotion_that_cannot_be_ruled_out_is_withheld(
    monkeypatch, tmp_path, capsys,
):
    candidate = _untagged_case(
        monkeypatch, tmp_path, record=[],
        merge_codes={f"refs/heads/{_UNTAGGED_BRANCH}": 128},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "already in origin/main" in capsys.readouterr().out


def test_ki98_publication_record_changed_after_confirmation_aborts(monkeypatch, tmp_path):
    candidate = _untagged_case(monkeypatch, tmp_path, record=[])
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda *_a, **_k: pytest.fail("abandoned despite a changed record"),
    )
    runtime = _Runtime()
    confirm = runtime.confirm

    def confirm_then_publish(prompt):
        monkeypatch.setattr(
            transaction, "read_untagged_publication_record", lambda *_: ["mdt-cockpit"],
        )
        return confirm(prompt)

    runtime.confirm = confirm_then_publish
    with pytest.raises(CliFailure, match="nothing was abandoned"):
        _invoke_abandon(candidate, branch=candidate.branch, dry_run=False, runtime=runtime)


def test_ki98_mixed_scope_with_tag_on_origin_and_unmarked_untagged_project_is_withheld(
    monkeypatch, tmp_path, capsys,
):
    candidate = _untagged_case(
        monkeypatch, tmp_path, record=[], scope=["alpha", "mdt-cockpit"],
        configs={
            "alpha": SimpleNamespace(git_tag=True, prefix="alpha-v"),
            "mdt-cockpit": SimpleNamespace(git_tag=False),
        },
        remote_tags={"refs/tags/alpha-v1": "d" * 40, "refs/tags/alpha-v1^{}": "d" * 40},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "selected-scope release tag refs that changed" in capsys.readouterr().out


def test_ki98_mixed_scope_with_tagged_project_in_results_and_unmarked_untagged_is_withheld(
    monkeypatch, tmp_path, capsys,
):
    configs = {
        "alpha": SimpleNamespace(git_tag=True, prefix="alpha-v"),
        "beta": SimpleNamespace(git_tag=False),
    }
    candidate, *_ = _install_feature_abandon_case(
        monkeypatch, tmp_path, scope=["alpha", "beta"], configs=configs,
        results={"alpha": "alpha-v1.0.0"},
    )
    monkeypatch.setattr(transaction, "read_untagged_publication_record", lambda *_: [])

    assert _invoke_abandon(candidate, branch=candidate.branch) == exit_codes.REFUSED
    assert "partial publication results" in capsys.readouterr().out


def test_ki98_publication_record_writes_are_atomic_and_durable(monkeypatch, tmp_path):
    _real_git(tmp_path, "init", "-q")
    ws = _workspace(tmp_path, _UNTAGGED_BRANCH)
    synced, replaced = [], []
    real_fsync, real_replace = transaction.os.fsync, transaction.os.replace
    monkeypatch.setattr(transaction.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    monkeypatch.setattr(
        transaction.os, "replace", lambda a, b: (replaced.append((str(a), str(b))), real_replace(a, b)),
    )
    transaction.start_untagged_publication_record(tmp_path, ws)
    assert len(synced) == 2  # the temp file, then the directory
    assert len(replaced) == 1
    src, dst = replaced[0]
    assert Path(src).parent == Path(dst).parent and src != dst

    # A failed replace leaves the previous record intact and no temp file behind.
    def boom(_a, _b):
        raise OSError("disk full")

    monkeypatch.setattr(transaction.os, "replace", boom)
    with pytest.raises(OSError):
        transaction.mark_untagged_publication_started(tmp_path, ws, "mdt-cockpit")
    monkeypatch.setattr(transaction.os, "replace", real_replace)
    assert _REAL_READ_UNTAGGED(tmp_path, ws) == []
    assert [p.name for p in Path(dst).parent.iterdir() if p.name.endswith(".tmp")] == []

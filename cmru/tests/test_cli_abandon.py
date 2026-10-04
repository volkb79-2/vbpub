"""Safety contract for the first-class release abandonment command."""
from __future__ import annotations

import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_extended import CliFailure
from cmru import cli, transaction


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
    monkeypatch.setattr(transaction, "read_release_progress", lambda _root, ws: ws.context.base_commit)
    monkeypatch.setattr(transaction, "read_release_tag_snapshot", lambda *_: {})
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_: None)
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_: {})
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
    outcomes = dict(merge_codes or {})
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda _root, *args, **_kwargs: SimpleNamespace(
            returncode=outcomes.get(args[2], 1), stdout="", stderr="",
        ),
    )


def _invoke_abandon(candidate, *, branch=None, dry_run=True, yes=False, runtime=None):
    from types import SimpleNamespace

    return cli._abandon(
        SimpleNamespace(branch=branch, dry_run=dry_run, yes=yes, config=None),
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

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
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
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: _loaded_config(
            tmp_path, {"alpha": SimpleNamespace(git_tag=True)},
        ),
    )
    monkeypatch.setattr(transaction, "abandon_workspace", lambda *_, **__: pytest.fail("blocked preview mutated"))

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

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
    output = capsys.readouterr().out
    assert "origin branch lookup returned unexpected ref(s)" in output
    assert "refs/heads/other" in output


@pytest.mark.parametrize(
    "tag_rc, expected",
    [
        (0, "no pre-attempt origin tag snapshot"),
        (2, "could not inspect remote tag alpha-v9"),
    ],
)
def test_abandon_legacy_snapshot_uses_conservative_tag_ancestry_check(
    monkeypatch, tmp_path, capsys, tag_rc, expected,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, snapshot=None,
        remote_tags={"refs/tags/alpha-v9": "d" * 40},
        merge_codes={"d" * 40: tag_rc},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
    assert expected in capsys.readouterr().out


def test_abandon_refuses_selected_scope_tag_changes_since_snapshot(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, snapshot={},
        remote_tags={"refs/tags/alpha-v9": "d" * 40},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
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

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
    output = capsys.readouterr().out
    assert "selected-scope release tag refs that changed" in output
    assert "origin/refs/tags/alpha-v9" in output


def test_abandon_refuses_a_scope_without_a_git_tag_prefix(monkeypatch, tmp_path, capsys):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate,
        configs={"alpha": SimpleNamespace(git_tag=False)},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
    assert "no usable Git tag prefix" in capsys.readouterr().out


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

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
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

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
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

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
    assert "not a known ancestor" in capsys.readouterr().out


def test_abandon_refuses_tag_attempt_metadata_outside_project_scope(
    monkeypatch, tmp_path, capsys,
):
    candidate = _workspace(tmp_path, "cmru-release-20260924_120000-alpha-ab12cd")
    _install_abandon_inspection(
        monkeypatch, tmp_path, candidate, snapshot={},
        attempts={"refs/tags/beta-v9": "d" * 40},
    )

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
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

    assert _invoke_abandon(candidate, branch=candidate.branch) == 2
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
        raise RuntimeError("Another cmru release transaction is already running.")
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

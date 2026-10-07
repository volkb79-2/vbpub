"""Behavioral witnesses for remaining changed-line release coverage gaps."""
from __future__ import annotations

import errno
import json
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


def test_local_tag_helpers_validate_git_object_ids_and_existence(monkeypatch, tmp_path):
    oid = "a" * 40
    original_local_git_tag_oid = cli.local_git_tag_oid
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: oid)
    assert cli.local_git_tag_exists(tmp_path, "demo-v1") is True
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: None)
    assert cli.local_git_tag_exists(tmp_path, "demo-v1") is False

    monkeypatch.setattr(cli, "local_git_tag_oid", original_local_git_tag_oid)
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="not-an-object-id\n", stderr="",
    ))
    with pytest.raises(RuntimeError, match="invalid object ID while inspecting local tag"):
        cli.local_git_tag_oid(tmp_path, "demo-v1")


def test_local_tag_oid_uses_explicit_exists_check_then_reads_the_oid(
    monkeypatch, tmp_path,
):
    oid = "a" * 40
    calls = []
    results = iter([
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout=oid, stderr=""),
    ])

    def run_local(_root, *args, **kwargs):
        calls.append(args)
        return next(results)

    monkeypatch.setattr(cli, "run_local_git", run_local)

    assert cli.local_git_tag_oid(tmp_path, "demo-v1") == oid
    assert calls == [
        ("show-ref", "--exists", "refs/tags/demo-v1"),
        ("show-ref", "--hash", "--verify", "refs/tags/demo-v1"),
    ]


def test_local_tag_oid_returns_none_only_for_explicit_missing_status(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "run_local_git", lambda _root, *args, **_kwargs: (
        calls.append(args) or SimpleNamespace(returncode=2, stdout="", stderr="")
    ))
    assert cli.local_git_tag_oid(tmp_path, "demo-v1") is None
    assert calls == [("show-ref", "--exists", "refs/tags/demo-v1")]


def test_local_tag_inspection_preflight_refuses_a_lookup_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="repository unreadable",
    ))
    with pytest.raises(
        RuntimeError,
        match="Failed to verify Git local tag inspection support \\(1\\): repository unreadable",
    ):
        cli._require_local_tag_inspection_support(tmp_path)


def test_local_tag_oid_does_not_fold_lookup_failure_into_absence(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="repository unreadable",
    ))
    with pytest.raises(
        RuntimeError,
        match="Failed to inspect local tag demo-v1 \\(1\\): repository unreadable",
    ):
        cli.local_git_tag_oid(tmp_path, "demo-v1")


def test_local_tag_oid_refuses_git_without_show_ref_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=129, stdout="", stderr="unknown option: --exists",
    ))
    with pytest.raises(
        RuntimeError,
        match="requires Git 2\\.43 or newer.*unknown option: --exists",
    ):
        cli.local_git_tag_oid(tmp_path, "demo-v1")


def test_local_tag_oid_refuses_hash_failure_after_presence_is_confirmed(
    monkeypatch, tmp_path,
):
    results = iter([
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="permission denied"),
    ])
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: next(results))

    with pytest.raises(
        RuntimeError,
        match="Failed to resolve local tag demo-v1 after its presence was confirmed \\(1\\): permission denied",
    ):
        cli.local_git_tag_oid(tmp_path, "demo-v1")


def test_local_tag_oid_reports_recheck_hash_failure_after_presence(monkeypatch, tmp_path):
    results = iter([
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=128, stdout="", stderr="hash lookup failed"),
    ])
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: next(results))

    with pytest.raises(
        RuntimeError,
        match=(
            "Failed to recheck local tag demo-v1 after deletion failed; its presence had been "
            "confirmed \\(128\\): hash lookup failed"
        ),
    ):
        cli.local_git_tag_oid(tmp_path, "demo-v1", action="recheck")


@pytest.mark.parametrize(
    "action, returncode, stdout, stderr, expected",
    [
        ("inspect", 1, "", "bad repository", "Failed to inspect local tag demo-v1 (1): bad repository"),
        ("recheck", 128, "git diagnostic", "", "Failed to recheck local tag demo-v1 after deletion failed (128): git diagnostic"),
        ("inspect", 128, "", "", "Failed to inspect local tag demo-v1 (128): no diagnostic output"),
    ],
)
def test_local_tag_oid_reports_unambiguous_lookup_failures(
    monkeypatch, tmp_path, action, returncode, stdout, stderr, expected,
):
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=stderr,
    ))

    with pytest.raises(RuntimeError, match=re.escape(expected)):
        cli.local_git_tag_oid(tmp_path, "demo-v1", action=action)


def _sidecar_workspace():
    return SimpleNamespace(branch="cmru-release-token")


@pytest.mark.parametrize("reader", ["snapshot", "attempts"])
def test_tag_sidecar_reader_preserves_inspection_os_errors(monkeypatch, tmp_path, reader):
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: tmp_path)
    suffix = ".tags.json" if reader == "snapshot" else ".tag-attempts.json"
    path = tmp_path / f"token{suffix}"
    original_lstat = Path.lstat

    def lstat(candidate):
        if candidate == path:
            raise PermissionError("sidecar is unreadable")
        return original_lstat(candidate)

    monkeypatch.setattr(Path, "lstat", lstat)
    read = (
        transaction.read_release_tag_snapshot
        if reader == "snapshot" else transaction.read_release_tag_attempts
    )
    with pytest.raises(RuntimeError, match="cannot inspect release tag"):
        read(tmp_path, _sidecar_workspace())


@pytest.mark.parametrize(
    "stdout, expected",
    [
        ("", {}),
        ("bad-row\n", "malformed"),
        ("refs/heads/main\t" + "a" * 40 + "\n", "malformed"),
        (
            "refs/tags/demo-v1\t" + "a" * 40 + "\n"
            + "refs/tags/demo-v1\t" + "b" * 40 + "\n",
            "duplicate",
        ),
    ],
)
def test_list_local_tag_refs_validates_git_rows(monkeypatch, tmp_path, stdout, expected):
    monkeypatch.setattr(transaction, "run_local_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout=stdout, stderr="",
    ))
    if expected == {}:
        assert transaction.list_local_tag_refs(tmp_path) == {}
    else:
        with pytest.raises(RuntimeError, match=expected):
            transaction.list_local_tag_refs(tmp_path)


@pytest.mark.parametrize(
    "stdout, stderr, expected",
    [
        ("", "repository unreadable", "repository unreadable"),
        ("git output", "", "git output"),
        ("", "", "no diagnostic output"),
    ],
)
def test_list_local_tag_refs_preserves_git_failure_details(
    monkeypatch, tmp_path, stdout, stderr, expected,
):
    monkeypatch.setattr(transaction, "run_local_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=2, stdout=stdout, stderr=stderr,
    ))
    with pytest.raises(RuntimeError, match=re.escape(expected)):
        transaction.list_local_tag_refs(tmp_path)


@pytest.mark.parametrize(
    "ref, oid",
    [
        ("refs/heads/main", "a" * 40),
        ("refs/tags/demo-v1", "bad"),
    ],
)
def test_write_tag_attempts_refuses_malformed_records(monkeypatch, tmp_path, ref, oid):
    monkeypatch.setattr(transaction, "_ensure_scope_dir", lambda _root: tmp_path)
    with pytest.raises(RuntimeError, match="malformed ref record"):
        transaction.write_release_tag_attempts(
            tmp_path, _sidecar_workspace(), {ref: oid},
        )


def test_write_tag_attempts_preserves_read_refusal(monkeypatch, tmp_path):
    failure = RuntimeError("corrupt existing attempt sidecar")
    monkeypatch.setattr(transaction, "_ensure_scope_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        transaction, "read_release_tag_attempts",
        lambda *_: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(RuntimeError) as caught:
        transaction.write_release_tag_attempts(
            tmp_path, _sidecar_workspace(), {"refs/tags/demo-v1": "a" * 40},
        )
    assert caught.value is failure


@pytest.mark.parametrize("payload", ["not-json", "[]", '{"refs/tags/demo-v1":"bad"}'])
def test_read_tag_attempts_rejects_corrupt_or_invalid_sidecars(
    monkeypatch, tmp_path, payload,
):
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: tmp_path)
    path = tmp_path / "token.tag-attempts.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot read|malformed"):
        transaction.read_release_tag_attempts(tmp_path, _sidecar_workspace())


def test_read_tag_attempts_refuses_a_nonregular_sidecar(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: tmp_path)
    (tmp_path / "token.tag-attempts.json").mkdir()
    with pytest.raises(RuntimeError, match="not a regular file"):
        transaction.read_release_tag_attempts(tmp_path, _sidecar_workspace())




def test_retained_build_cleanup_closes_open_descriptor_when_logs_are_missing(
    monkeypatch, tmp_path,
):
    opened = []
    closed = []

    def open_directory(_name, _flags, *, dir_fd):
        if not opened:
            opened.append(501)
            return 501
        raise OSError(errno.EACCES, "logs directory unavailable")

    monkeypatch.setattr(transaction.os, "open", open_directory)
    monkeypatch.setattr(transaction.os, "close", lambda fd: closed.append(fd))
    output_id = "20260927T120000Z_" + "a" * 40
    with pytest.raises(RuntimeError, match="retained build record is incomplete or unsafe"):
        transaction._retained_build_output_cleanup_facts(
            "alpha", output_id, tmp_path, 10, 11,
        )
    assert closed == [501]


def test_tag_prefix_filter_keeps_matching_annotated_and_lightweight_refs():
    refs = {
        "refs/tags/alpha-v1": "a" * 40,
        "refs/tags/alpha-v1^{}": "b" * 40,
        "refs/tags/beta-v1": "c" * 40,
    }
    assert transaction._tag_refs_for_prefixes(refs, ("alpha-v",)) == {
        "refs/tags/alpha-v1": "a" * 40,
        "refs/tags/alpha-v1^{}": "b" * 40,
    }
    assert transaction._tag_refs_for_prefixes(refs, ()) == refs


def _install_transaction_abandon(
    monkeypatch, tmp_path, *, branch_output="", pushed=False, removed=False,
    tag_output="", local_tag_states=None, push_result=None,
):
    workspace = SimpleNamespace(
        repo_root=tmp_path, path=tmp_path / "workspace", branch="cmru-release-test",
        context=None,
    )
    calls = []
    removed_workspaces = []
    forgotten_scopes = []

    def run_remote(_root, *args, **_kwargs):
        calls.append(args)
        if args[:3] == ("ls-remote", "--heads", "origin"):
            return SimpleNamespace(returncode=0, stdout=branch_output, stderr="")
        if args[:3] == ("ls-remote", "--tags", "origin"):
            return SimpleNamespace(returncode=0, stdout=tag_output, stderr="")
        if args and args[0] == "push":
            return push_result or SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(transaction, "run_remote_git", run_remote)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: pushed)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: removed)
    monkeypatch.setattr(transaction, "remove_workspace", lambda ws: removed_workspaces.append(ws))
    monkeypatch.setattr(
        transaction, "forget_release_scope", lambda *_: forgotten_scopes.append(True),
    )
    monkeypatch.setattr(transaction, "run_local_git", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="", stderr="",
    ))
    states = iter(local_tag_states or [])
    monkeypatch.setattr(
        transaction, "list_local_tag_refs", lambda *_: next(states, {}),
    )
    return workspace, calls, removed_workspaces, forgotten_scopes


def test_abandon_workspace_rejects_unexpected_remote_branch_record(monkeypatch, tmp_path):
    _workspace, _calls, removed, _forgotten = _install_transaction_abandon(
        monkeypatch, tmp_path,
        branch_output="a" * 40 + "\trefs/heads/other\n",
    )
    with pytest.raises(RuntimeError, match="unexpected ref"):
        transaction.abandon_workspace(tmp_path, SimpleNamespace(branch="cmru-release-test"))
    assert removed == []


def test_abandon_workspace_rejects_unreadable_remote_tag_recheck(monkeypatch, tmp_path):
    workspace, _calls, removed, _forgotten = _install_transaction_abandon(
        monkeypatch, tmp_path, tag_output="",
    )
    monkeypatch.setattr(transaction, "run_remote_git", lambda _root, *args, **_kwargs:
        SimpleNamespace(
            returncode=2 if args[:3] == ("ls-remote", "--tags", "origin") else 0,
            stdout="", stderr="offline",
        )
    )
    with pytest.raises(RuntimeError, match="cannot recheck origin release tags"):
        transaction.abandon_workspace(
            tmp_path, workspace, expected_remote_tag_refs={}, release_tag_prefixes=("alpha-v",),
        )
    assert removed == []


def test_abandon_workspace_rejects_changed_local_tag_facts(monkeypatch, tmp_path):
    workspace, _calls, removed, _forgotten = _install_transaction_abandon(
        monkeypatch, tmp_path, local_tag_states=[{"refs/tags/alpha-v1": "b" * 40}],
    )
    with pytest.raises(RuntimeError, match="local release tags changed"):
        transaction.abandon_workspace(
            tmp_path, workspace, expected_local_tag_refs={}, release_tag_prefixes=("alpha-v",),
        )
    assert removed == []


def test_abandon_workspace_rejects_malformed_candidate_object_id(monkeypatch, tmp_path):
    # REL-14: SHA-256 (64 hex) ids are valid now; only malformed ids are refused.
    oid = "a" * 63
    workspace, _calls, removed, _forgotten = _install_transaction_abandon(
        monkeypatch, tmp_path,
        pushed=True,
    )
    monkeypatch.setattr(
        transaction, "parse_ls_remote_refs",
        lambda *_args, **_kwargs: {"refs/heads/cmru-release-test": oid},
    )
    with pytest.raises(RuntimeError, match="object ID is unavailable"):
        transaction.abandon_workspace(tmp_path, workspace)
    assert removed == []


@pytest.mark.parametrize(
    "cleanup_targets, states, expected",
    [
        ({"refs/heads/main": "a" * 40}, [], "malformed local release-tag cleanup target"),
        ({"refs/tags/alpha-v1": "a" * 40}, [{}], None),
        ({"refs/tags/alpha-v1": "a" * 40}, [{"refs/tags/alpha-v1": "b" * 40}], "changed after abandonment inspection"),
    ],
)
def test_abandon_workspace_validates_or_skips_local_tag_cleanup_targets(
    monkeypatch, tmp_path, cleanup_targets, states, expected,
):
    workspace, _calls, removed, forgotten = _install_transaction_abandon(
        monkeypatch, tmp_path, local_tag_states=states,
    )
    if expected is None:
        transaction.abandon_workspace(
            tmp_path, workspace, local_tags_to_remove=cleanup_targets,
        )
        assert removed == [workspace] and forgotten == [True]
    else:
        with pytest.raises(RuntimeError, match=expected):
            transaction.abandon_workspace(
                tmp_path, workspace, local_tags_to_remove=cleanup_targets,
            )
        assert removed == []


@pytest.mark.parametrize(
    "remaining, result, expected",
    [
        ({}, SimpleNamespace(returncode=0, stdout="", stderr=""), None),
        ({"refs/tags/alpha-v1": "b" * 40}, SimpleNamespace(returncode=0, stdout="", stderr=""), "changed during abandonment"),
        ({"refs/tags/alpha-v1": "a" * 40}, SimpleNamespace(returncode=3, stdout="", stderr="update-ref denied"), "update-ref denied"),
    ],
)
def test_abandon_workspace_rechecks_local_tag_after_guarded_deletion(
    monkeypatch, tmp_path, remaining, result, expected,
):
    ref = "refs/tags/alpha-v1"
    oid = "a" * 40
    workspace, calls, removed, forgotten = _install_transaction_abandon(
        monkeypatch, tmp_path,
        local_tag_states=[{ref: oid}, remaining],
    )
    monkeypatch.setattr(transaction, "run_local_git", lambda *_args, **_kwargs: result)
    if expected is None:
        transaction.abandon_workspace(
            tmp_path, workspace, local_tags_to_remove={ref: oid},
        )
        assert removed == [workspace] and forgotten == [True]
    else:
        with pytest.raises(RuntimeError, match=expected):
            transaction.abandon_workspace(
                tmp_path, workspace, local_tags_to_remove={ref: oid},
            )
        assert removed == []
        # Local deletion is issued through the separate local Git wrapper.


def test_local_tag_oid_refuses_hash_failure_after_presence_was_confirmed(
    monkeypatch, tmp_path,
):
    results = iter([
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=128, stdout="", stderr="missing ref"),
    ])
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: next(results))

    with pytest.raises(
        RuntimeError,
        match="Failed to resolve local tag demo-v1 after its presence was confirmed \\(128\\): missing ref",
    ):
        cli.local_git_tag_oid(tmp_path, "demo-v1")


def test_local_tag_deletion_honors_a_previewed_absence(monkeypatch, tmp_path, capsys):
    inspected = []
    monkeypatch.setattr(
        cli, "local_git_tag_oid",
        lambda *args, **kwargs: inspected.append((args, kwargs)) or "a" * 40,
    )

    assert cli.delete_git_tag_local(
        tmp_path, "demo-v1", False, expected_present=False,
    ) is None
    assert inspected == []
    assert "absent from the confirmed cleanup preview" in capsys.readouterr().out


def test_tag_deletion_refuses_malformed_or_incomplete_preview_identity(monkeypatch, tmp_path):
    tag = "demo-v1"
    oid = "a" * 40
    with pytest.raises(RuntimeError, match="invalid captured object ID"):
        cli.delete_git_tag_remote(
            tmp_path, tag, False, expected_present=True, expected_oid="bad",
        )
    monkeypatch.setattr(cli, "list_remote_tag_refs_matching", lambda *_a, **_k: {tag: oid})
    with pytest.raises(RuntimeError, match="has no captured object ID"):
        cli.delete_git_tag_remote(tmp_path, tag, False, expected_present=True)

    with pytest.raises(RuntimeError, match="invalid captured object ID"):
        cli.delete_git_tag_local(
            tmp_path, tag, False, expected_present=True, expected_oid="bad",
        )
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_a, **_k: oid)
    with pytest.raises(RuntimeError, match="has no captured object ID"):
        cli.delete_git_tag_local(tmp_path, tag, False, expected_present=True)


def test_cleanup_preview_does_not_plan_deleting_an_absent_tag(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "list_releases", lambda *_: [
        {"tag_name": "demo-v1.0.0", "id": 7, "assets": []},
    ])
    monkeypatch.setattr(cli, "list_remote_tag_refs_matching", lambda *_a, **_k: {})
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_a, **_k: None)
    plan = cli.CleanupPlan()

    cli.cleanup_project_releases_and_tags(
        tmp_path, "owner", "repo", "token", "demo", [], True, plan=plan,
    )

    assert [description for description, _action in plan.actions] == [
        "GitHub Release demo-v1.0.0 (id=7)",
    ]
    assert "No remote or local Git tag demo-v1.0.0 was present" in capsys.readouterr().out


def test_cleanup_worktree_paths_accepts_text_status_output(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        stdout="?? spaced name.txt\0 M tracked.txt\0",
    ))

    assert cli._cleanup_worktree_paths(tmp_path) == {"spaced name.txt", "tracked.txt"}


def test_cleanup_step_commits_only_after_the_step_reports_success(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(cli, "_cleanup_worktree_paths", lambda _root: {"preexisting.txt"})
    monkeypatch.setattr(
        cli, "cleanup_project_step",
        lambda *args, **kwargs: events.append(("step", args, kwargs)) or True,
    )
    monkeypatch.setattr(
        cli, "cleanup_commit_deletions",
        lambda *args, **kwargs: events.append(("commit", args, kwargs)),
    )
    project = cli.ProjectConfig("demo", {}, {"clean": []})

    cli._run_cleanup_step_and_commit(
        tmp_path, "demo", project, "1.2.3", ["demo-v1.0.0"], "publisher-token",
    )

    assert [event[0] for event in events] == ["step", "commit"]
    assert events[1][2]["before_paths"] == {"preexisting.txt"}

    events.clear()
    monkeypatch.setattr(cli, "cleanup_project_step", lambda *args, **kwargs: events.append(("step", args, kwargs)) or False)
    cli._run_cleanup_step_and_commit(
        tmp_path, "demo", project, "1.2.3", ["demo-v1.0.0"], "publisher-token",
    )
    assert [event[0] for event in events] == ["step"]

    events.clear()

    def fail_step(*args, **kwargs):
        events.append(("step", args, kwargs))
        raise RuntimeError("clean command failed")

    monkeypatch.setattr(cli, "cleanup_project_step", fail_step)
    with pytest.raises(RuntimeError, match="clean command failed"):
        cli._run_cleanup_step_and_commit(
            tmp_path, "demo", project, "1.2.3", ["demo-v1.0.0"], "publisher-token",
        )
    assert [event[0] for event in events] == ["step"]


@pytest.mark.parametrize(
    "scope",
    ["alpha", [], ["alpha", "alpha"], ["", "alpha"], [17]],
)
def test_resume_target_refuses_malformed_saved_scope(tmp_path, scope):
    with pytest.raises(RuntimeError, match="malformed project-scope metadata"):
        cli._release_resume_target(
            tmp_path / "cmru.toml", "alpha", scope,
            {"alpha": object()}, ["alpha"],
        )


def test_resume_target_refuses_a_saved_project_removed_from_the_config(tmp_path):
    with pytest.raises(RuntimeError, match="absent from the selected config: retired"):
        cli._release_resume_target(
            tmp_path / "cmru.toml", None, ["retired"], {}, [],
        )


def test_abandon_propagates_unrelated_release_lock_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(
        transaction, "release_lock",
        lambda _root: (_ for _ in ()).throw(RuntimeError("lock metadata unavailable")),
    )
    monkeypatch.setattr(
        transaction, "list_cmru_workspaces",
        lambda _root: pytest.fail("inspection ran without a valid lock"),
    )

    with pytest.raises(RuntimeError, match="lock metadata unavailable"):
        cli._abandon(SimpleNamespace(branch=None, dry_run=True), SimpleNamespace())


def test_abandon_reports_workspace_enumeration_failure(monkeypatch, tmp_path):
    from cli_extended import CliFailure

    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(transaction, "release_lock", lambda _root: _NullContext())
    monkeypatch.setattr(
        transaction, "list_cmru_workspaces",
        lambda _root: (_ for _ in ()).throw(OSError("Git worktree list is unreadable")),
    )

    with pytest.raises(CliFailure, match="cannot inspect CMRU release worktrees: Git worktree list is unreadable"):
        cli._abandon(SimpleNamespace(branch=None, dry_run=True), SimpleNamespace())


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "README.md").write_text("release fixture\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "feat: initial")
    return root


def _raw_release_worktree(tmp_path: Path, *, suffix: str = "candidate"):
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    branch = f"cmru/release/{suffix}"
    path = tmp_path / "retained"
    _git(root, "worktree", "add", "-q", "-b", branch, str(path), base)
    workspace = transaction.ReleaseWorkspace(root, path, branch, base)
    transaction.write_release_progress(root, workspace, base)
    return root, path, branch, base


def _remove_raw_release_worktree(root: Path, path: Path, branch: str) -> None:
    _git(root, "worktree", "remove", "--force", str(path))
    _git(root, "branch", "-D", branch)


@pytest.mark.parametrize(
    "branch,head,message",
    [
        ("main", "a" * 40, "not a retained cmru release branch"),
        ("cmru/release/valid", "not-an-object-id", "invalid Git HEAD"),
    ],
)
def test_legacy_progress_validation_rejects_bad_branch_or_head(
    tmp_path, branch, head, message,
):
    with pytest.raises(RuntimeError, match=message):
        transaction._validate_legacy_release_progress(tmp_path, tmp_path, branch, head)


def test_legacy_progress_validation_preserves_non_ancestor_git_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: "a" * 40)
    monkeypatch.setattr(transaction, "run_local_git", lambda *args, **kwargs: SimpleNamespace(
        returncode=128, stderr="", stdout="object database unavailable",
    ))

    with pytest.raises(
        RuntimeError,
        match=r"cannot validate legacy release progress.*\(128\): object database unavailable",
    ):
        transaction._validate_legacy_release_progress(
            tmp_path, tmp_path, "cmru/release/candidate", "b" * 40,
        )


def test_resume_migrates_an_existing_legacy_record_without_a_scope_marker(
    monkeypatch, tmp_path,
):
    root, path, branch, base = _raw_release_worktree(tmp_path, suffix="marker-migration")
    shared = transaction._shared_worktree()
    adopted = shared.adopt_workspace(
        root, path, purpose="cmru-legacy", labels={"cmru.purpose": "release"},
        metadata={}, identity_path=path,
    )
    adoption_record = shared.find_workspace(adopted.git_common_dir, path)
    expected_metadata = dict(adoption_record.metadata)
    assert expected_metadata["workspace.identity_path"] == str(path)
    monkeypatch.setattr(
        transaction, "run_remote_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    try:
        resumed = transaction.resume_workspace(root, path)
        record = shared.find_workspace(adopted.git_common_dir, path)

        assert resumed.workspace_id == adopted.workspace_id
        expected_metadata[transaction._LEGACY_RESUME_METADATA_KEY] = (
            transaction._LEGACY_RESUME_METADATA_VALUE
        )
        assert record.metadata == expected_metadata
    finally:
        _remove_raw_release_worktree(root, path, branch)


def test_resume_preserves_an_existing_legacy_scope_marker(monkeypatch, tmp_path):
    root, path, branch, _base = _raw_release_worktree(tmp_path, suffix="existing-marker")
    shared = transaction._shared_worktree()
    adopted = shared.adopt_workspace(
        root, path, purpose="cmru-legacy", labels={"cmru.purpose": "release"},
        metadata={
            transaction._LEGACY_RESUME_METADATA_KEY:
                transaction._LEGACY_RESUME_METADATA_VALUE,
        },
        identity_path=path,
    )
    expected_metadata = dict(shared.find_workspace(adopted.git_common_dir, path).metadata)
    monkeypatch.setattr(
        transaction, "run_remote_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    try:
        resumed = transaction.resume_workspace(root, path)
        record = shared.find_workspace(adopted.git_common_dir, path)
        assert resumed.workspace_id == adopted.workspace_id
        assert record.metadata == expected_metadata
    finally:
        _remove_raw_release_worktree(root, path, branch)


@pytest.mark.parametrize(
    "metadata,message",
    [
        (None, "invalid legacy CMRU workspace metadata"),
        ({transaction._LEGACY_RESUME_METADATA_KEY: "unknown"}, "unrecognized legacy CMRU transaction scope"),
    ],
)
def test_resume_refuses_corrupt_existing_legacy_record_metadata(
    monkeypatch, tmp_path, metadata, message,
):
    root, path, branch, base = _raw_release_worktree(tmp_path, suffix="bad-metadata")
    real_shared = transaction._shared_worktree()
    record = SimpleNamespace(purpose="cmru-legacy", metadata=metadata)
    context = SimpleNamespace(branch=branch, base_commit=base)

    class FakeShared:
        def __getattr__(self, name):
            return getattr(real_shared, name)

        def find_workspace(self, *_args):
            return record

        def ensure_workspace(self, *_args, **_kwargs):
            return context

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: FakeShared())
    try:
        with pytest.raises(RuntimeError, match=message):
            transaction.resume_workspace(root, path)
    finally:
        _remove_raw_release_worktree(root, path, branch)


def test_resume_refuses_legacy_candidate_when_source_git_family_cannot_be_known(
    monkeypatch, tmp_path,
):
    root, path, branch, _base = _raw_release_worktree(tmp_path, suffix="unknown-family")
    monkeypatch.setattr(
        transaction, "_common_git_dir",
        lambda _root: (_ for _ in ()).throw(RuntimeError("git family lookup failed")),
    )
    try:
        with pytest.raises(RuntimeError, match="source Git family is unknown"):
            transaction.resume_workspace(root, path)
    finally:
        _remove_raw_release_worktree(root, path, branch)


def test_resume_rechecks_source_git_family_before_adopting_legacy_candidate(
    monkeypatch, tmp_path,
):
    root, path, branch, _base = _raw_release_worktree(tmp_path, suffix="family-race")
    real_shared = transaction._shared_worktree()
    original_discover = real_shared.discover_git_context

    class RacingShared:
        def __getattr__(self, name):
            return getattr(real_shared, name)

        def discover_git_context(self, candidate):
            result = original_discover(candidate)
            if Path(candidate).resolve() == root.resolve():
                top, common, branch_name, head = result
                return top, common / "changed-during-validation", branch_name, head
            return result

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: RacingShared())
    try:
        with pytest.raises(RuntimeError, match="do not share the exact Git family"):
            transaction.resume_workspace(root, path)
    finally:
        _remove_raw_release_worktree(root, path, branch)


def test_resume_reports_legacy_adoption_write_failure(monkeypatch, tmp_path):
    root, path, branch, _base = _raw_release_worktree(tmp_path, suffix="adoption-failure")
    real_shared = transaction._shared_worktree()

    class FailingShared:
        def __getattr__(self, name):
            return getattr(real_shared, name)

        def adopt_workspace(self, *_args, **_kwargs):
            raise OSError("identity store is read-only")

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: FailingShared())
    monkeypatch.setattr(
        transaction, "run_remote_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    try:
        with pytest.raises(RuntimeError, match="cannot adopt validated legacy release worktree.*identity store is read-only"):
            transaction.resume_workspace(root, path)
    finally:
        _remove_raw_release_worktree(root, path, branch)


@pytest.mark.parametrize("kind,message", [
    ("missing", "release worktree does not exist"),
    ("not-a-worktree", "not a readable Git worktree"),
    ("nested-path", "must name the worktree root"),
    ("wrong-branch", "not a retained CMRU release branch"),
])
def test_read_release_scope_for_path_reports_unverifiable_candidates(
    monkeypatch, tmp_path, kind, message,
):
    path = tmp_path / "candidate"
    path.mkdir()
    nested = path / "nested"
    nested.mkdir()

    class FakeShared:
        def discover_git_context(self, candidate):
            if kind == "not-a-worktree":
                raise OSError("gitfile cannot be read")
            if kind == "nested-path":
                return path, tmp_path / ".git", "cmru/release/candidate", "a" * 40
            branch = "main" if kind == "wrong-branch" else "cmru/release/candidate"
            return Path(candidate), tmp_path / ".git", branch, "a" * 40

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: FakeShared())
    candidate = tmp_path / "absent" if kind == "missing" else nested if kind == "nested-path" else path

    with pytest.raises(RuntimeError, match=message):
        transaction.read_release_scope_for_path(candidate)


def test_cleanup_build_output_refuses_a_valid_but_absent_record_before_apply(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo")
    project.project_root.mkdir()
    (project.project_root / "artifacts").mkdir()
    (project.project_root / "logs").mkdir()
    monkeypatch.setattr(cli, "_resolve_config", lambda _arg: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: (
        tmp_path, {"demo": project}, ["demo"], ["demo"], [], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    ))
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["demo"])
    calls = []
    monkeypatch.setattr(
        transaction, "delete_retained_build_output",
        lambda *_a, **kwargs: calls.append(kwargs) or [],
    )

    assert cli.main([
        "cleanup", "demo", "--delete-build-output",
        "20260101T000000Z_" + "a" * 40, "--yes",
    ]) == 4
    err = capsys.readouterr().err
    assert "retained build record is incomplete or unsafe" in err and "unexpected" not in err
    assert calls == []


def _retained_build_output(tmp_path):
    project_root = tmp_path / "demo"
    output_id = "20260101T000000Z_" + "a" * 40
    artifact_root = project_root / "artifacts" / output_id
    logs_root = project_root / "logs" / output_id
    artifact_root.mkdir(parents=True)
    logs_root.mkdir(parents=True)
    (artifact_root / "dist").mkdir()
    (artifact_root / "dist" / "demo.whl").write_bytes(b"wheel")
    (logs_root / "build.log").write_text("build completed\n", encoding="utf-8")
    (artifact_root / "build.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "cmru-local-build",
        "publication": "forbidden",
        "project": "demo",
        "build_id": output_id,
    }), encoding="utf-8")
    return SimpleNamespace(project_root=project_root), output_id, artifact_root, logs_root


@pytest.mark.parametrize("mutation", ["replace-record", "add-nested-output"])
def test_build_output_cleanup_rechecks_the_previewed_record_identity(tmp_path, mutation):
    project, output_id, artifact_root, logs_root = _retained_build_output(tmp_path)
    identity = transaction.retained_build_output_identity(
        tmp_path, project, "demo", output_id,
    )

    assert transaction.delete_retained_build_output(
        tmp_path, project, "demo", output_id,
        dry_run=True, expected_identity=identity,
    ) == [logs_root, artifact_root]

    if mutation == "replace-record":
        old_artifact_root = artifact_root.with_name(output_id + ".old")
        artifact_root.rename(old_artifact_root)
        artifact_root.mkdir()
        (artifact_root / "build.json").write_text(json.dumps({
            "schema_version": 1,
            "kind": "cmru-local-build",
            "publication": "forbidden",
            "project": "demo",
            "build_id": output_id,
        }), encoding="utf-8")
    else:
        (artifact_root / "dist" / "new.whl").write_bytes(b"arrived after preview")

    with pytest.raises(RuntimeError, match="changed after cleanup preview"):
        transaction.delete_retained_build_output(
            tmp_path, project, "demo", output_id,
            dry_run=False, expected_identity=identity,
        )
    assert artifact_root.is_dir() and logs_root.is_dir()


@pytest.mark.parametrize("parent_name", ["artifacts", "logs"])
def test_build_output_cleanup_refuses_symlinked_output_parent(tmp_path, parent_name):
    project, output_id, artifact_root, logs_root = _retained_build_output(tmp_path)
    parent = project.project_root / parent_name
    outside_parent = tmp_path / f"outside-{parent_name}"
    parent.rename(outside_parent)
    parent.symlink_to(outside_parent, target_is_directory=True)

    with pytest.raises(RuntimeError, match="retained build output path is or crosses a symlink"):
        transaction.retained_build_output_identity(
            tmp_path, project, "demo", output_id,
        )

    assert (outside_parent / output_id).is_dir()
    assert (artifact_root / "build.json").is_file()
    assert (logs_root / "build.log").is_file()


def test_build_output_cleanup_refuses_a_fifo_manifest_without_blocking(tmp_path):
    project, output_id, artifact_root, _logs_root = _retained_build_output(tmp_path)
    manifest_path = artifact_root / "build.json"
    manifest_path.unlink()
    manifest_path.mkdir()
    with pytest.raises(RuntimeError, match="retained build record is incomplete or unsafe"):
        transaction.retained_build_output_identity(
            tmp_path, project, "demo", output_id,
        )
    manifest_path.rmdir()
    os.mkfifo(manifest_path)

    child_code = """
import sys
from pathlib import Path
from types import SimpleNamespace
from cmru import transaction

root = Path(sys.argv[1])
project = SimpleNamespace(project_root=root / "demo")
try:
    transaction.retained_build_output_identity(root, project, "demo", sys.argv[2])
except RuntimeError as exc:
    if "retained build record is incomplete or unsafe" in str(exc):
        raise SystemExit(0)
    raise
raise SystemExit(1)
"""
    environment = os.environ.copy()
    source_root = str(Path(transaction.__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(filter(
        None, (source_root, environment.get("PYTHONPATH")),
    ))
    result = subprocess.run(
        [sys.executable, "-c", child_code, str(tmp_path), output_id],
        cwd=source_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_descriptor_cleanup_refuses_missing_safety_flags_and_noncanonical_paths(
    monkeypatch, tmp_path,
):
    monkeypatch.delattr(transaction.os, "O_NOFOLLOW")
    with pytest.raises(RuntimeError, match="safe descriptor-relative build-output cleanup is unavailable"):
        transaction._directory_open_flags()

    with pytest.raises(RuntimeError, match="cannot safely open non-canonical directory path"):
        transaction._open_directory_path_nofollow(Path("relative/path"))
    with pytest.raises(RuntimeError, match="cannot safely open non-canonical directory path"):
        transaction._open_directory_path_nofollow(tmp_path / ".." / "escape")


def test_open_directory_path_closes_its_current_descriptor_after_open_failure(
    monkeypatch, tmp_path,
):
    opened = []
    original_open = transaction.os.open

    def track_open(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(transaction.os, "open", track_open)
    with pytest.raises(FileNotFoundError):
        transaction._open_directory_path_nofollow(tmp_path / "missing" / "child")

    assert opened
    for descriptor in opened:
        with pytest.raises(OSError) as raised:
            os.fstat(descriptor)
        assert raised.value.errno == errno.EBADF


def test_descriptor_tree_walk_detects_a_directory_changed_before_visit(
    monkeypatch, tmp_path,
):
    descriptor = os.open(tmp_path, transaction._directory_open_flags())
    original_identity = transaction._filesystem_stat_identity
    calls = 0

    def change_identity(info):
        nonlocal calls
        calls += 1
        identity = original_identity(info)
        return (*identity[:-1], identity[-1] + 1) if calls == 2 else identity

    monkeypatch.setattr(transaction, "_filesystem_stat_identity", change_identity)
    try:
        with pytest.raises(RuntimeError, match="retained build output changed during inspection"):
            transaction._filesystem_tree_identity_fd(descriptor)
    finally:
        os.close(descriptor)


def test_manifest_reader_detects_a_file_changed_during_read(monkeypatch, tmp_path):
    (tmp_path / "build.json").write_text("{}", encoding="utf-8")
    parent_fd = os.open(tmp_path, transaction._directory_open_flags())
    original_identity = transaction._filesystem_stat_identity
    calls = 0

    def change_identity(info):
        nonlocal calls
        calls += 1
        identity = original_identity(info)
        return (*identity[:-1], identity[-1] + 1) if calls == 2 else identity

    monkeypatch.setattr(transaction, "_filesystem_stat_identity", change_identity)
    try:
        with pytest.raises(RuntimeError, match="retained build manifest changed during inspection"):
            transaction._read_regular_file_at(
                parent_fd, "build.json", "demo", tmp_path / "build.json",
            )
    finally:
        os.close(parent_fd)


def test_retained_output_parent_open_translates_symlink_errors(monkeypatch, tmp_path):
    project_root = tmp_path / "demo"
    (project_root / "artifacts").mkdir(parents=True)
    (project_root / "logs").mkdir()
    project = SimpleNamespace(project_root=project_root)
    monkeypatch.setattr(transaction, "_assert_no_symlink_components", lambda *_a, **_k: None)
    original_open = transaction.os.open

    def fail_artifacts(path, *args, **kwargs):
        if path == "artifacts" and kwargs.get("dir_fd") is not None:
            raise OSError(errno.ELOOP, "simulated symlink")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(transaction.os, "open", fail_artifacts)
    with pytest.raises(RuntimeError, match="retained build output path is or crosses a symlink"):
        with transaction._retained_build_output_parent_fds(
            tmp_path, project, "demo", "20260101T000000Z_" + "a" * 40,
        ):
            pytest.fail("unsafe parent descriptors were yielded")


def test_retained_output_parent_open_closes_prior_descriptors_on_unexpected_error(
    monkeypatch, tmp_path,
):
    project_root = tmp_path / "demo"
    (project_root / "artifacts").mkdir(parents=True)
    (project_root / "logs").mkdir()
    project = SimpleNamespace(project_root=project_root)
    captured = []
    original_open = transaction.os.open

    def fail_logs(path, *args, **kwargs):
        if path == "logs" and kwargs.get("dir_fd") is not None:
            raise RuntimeError("injected opener failure")
        descriptor = original_open(path, *args, **kwargs)
        if path == "artifacts" and kwargs.get("dir_fd") is not None:
            captured.append(descriptor)
        return descriptor

    monkeypatch.setattr(transaction.os, "open", fail_logs)
    with pytest.raises(RuntimeError, match="injected opener failure"):
        with transaction._retained_build_output_parent_fds(
            tmp_path, project, "demo", "20260101T000000Z_" + "a" * 40,
        ):
            pytest.fail("incomplete parent descriptors were yielded")
    assert len(captured) == 1
    with pytest.raises(OSError) as raised:
        os.fstat(captured[0])
    assert raised.value.errno == errno.EBADF


def test_retained_output_leaf_open_translates_symlink_errors(monkeypatch, tmp_path):
    project, output_id, artifact_root, logs_root = _retained_build_output(tmp_path)
    monkeypatch.setattr(transaction, "_assert_no_symlink_components", lambda *_a, **_k: None)
    original_open = transaction.os.open

    def fail_output_leaf(path, *args, **kwargs):
        if path == output_id and kwargs.get("dir_fd") is not None:
            raise OSError(errno.ELOOP, "simulated symlink")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(transaction.os, "open", fail_output_leaf)
    with pytest.raises(RuntimeError, match="retained build output path is or crosses a symlink"):
        transaction.retained_build_output_identity(tmp_path, project, "demo", output_id)
    assert (artifact_root / "build.json").is_file()
    assert (logs_root / "build.log").is_file()


def test_retained_output_leaf_open_closes_prior_descriptors_on_unexpected_error(
    monkeypatch, tmp_path,
):
    project, output_id, _artifact_root, _logs_root = _retained_build_output(tmp_path)
    captured = []
    original_open = transaction.os.open
    output_opens = 0

    def fail_second_leaf(path, *args, **kwargs):
        nonlocal output_opens
        if path == output_id and kwargs.get("dir_fd") is not None:
            output_opens += 1
            if output_opens == 2:
                raise RuntimeError("injected leaf opener failure")
            descriptor = original_open(path, *args, **kwargs)
            captured.append(descriptor)
            return descriptor
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(transaction.os, "open", fail_second_leaf)
    with pytest.raises(RuntimeError, match="injected leaf opener failure"):
        transaction.retained_build_output_identity(tmp_path, project, "demo", output_id)
    assert len(captured) == 1
    with pytest.raises(OSError) as raised:
        os.fstat(captured[0])
    assert raised.value.errno == errno.EBADF


def test_retained_output_identity_rechecks_root_directories_after_tree_walk(
    monkeypatch, tmp_path,
):
    project, output_id, _artifact_root, _logs_root = _retained_build_output(tmp_path)
    original_walk = transaction._filesystem_tree_identity_fd
    walks = 0

    def add_artifact_after_walk(root_fd):
        nonlocal walks
        walks += 1
        result = original_walk(root_fd)
        if walks == 1:
            descriptor = os.open(
                "late-arrival.txt", os.O_CREAT | os.O_WRONLY, 0o600,
                dir_fd=root_fd,
            )
            os.close(descriptor)
        return result

    monkeypatch.setattr(transaction, "_filesystem_tree_identity_fd", add_artifact_after_walk)
    with pytest.raises(RuntimeError, match="retained build output changed during inspection"):
        transaction.retained_build_output_identity(tmp_path, project, "demo", output_id)


def test_private_cleanup_stage_retries_collisions_and_removes_failed_open(
    monkeypatch, tmp_path,
):
    parent_fd = os.open(tmp_path, transaction._directory_open_flags())
    token = "0" * 24
    stage_name = f".cmru-cleanup-demo-{token}"
    monkeypatch.setattr(transaction.secrets, "token_hex", lambda _count: token)
    (tmp_path / stage_name).mkdir()
    try:
        with pytest.raises(RuntimeError, match="could not allocate a private build-output cleanup directory"):
            transaction._create_private_cleanup_stage(parent_fd, "demo")

        (tmp_path / stage_name).rmdir()
        original_open = transaction.os.open

        def fail_stage_open(path, *args, **kwargs):
            if path == stage_name and kwargs.get("dir_fd") == parent_fd:
                raise RuntimeError("injected stage opener failure")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(transaction.os, "open", fail_stage_open)
        with pytest.raises(RuntimeError, match="injected stage opener failure"):
            transaction._create_private_cleanup_stage(parent_fd, "demo")
        assert not (tmp_path / stage_name).exists()
    finally:
        os.close(parent_fd)


def test_restore_cleanup_stage_record_handles_absence_vacancy_and_collision(
    monkeypatch, tmp_path,
):
    parent = tmp_path / "parent"
    stage = parent / "stage"
    stage.mkdir(parents=True)
    output_id = "output-1"
    parent_fd = os.open(parent, transaction._directory_open_flags())
    stage_fd = os.open(stage, transaction._directory_open_flags())
    try:
        assert transaction._restore_cleanup_stage_record(parent_fd, stage_fd, output_id)
        (stage / "record").mkdir()

        def rename_record(source, source_fd, destination, destination_fd):
            os.rename(
                source, destination, src_dir_fd=source_fd, dst_dir_fd=destination_fd,
            )
            return True

        monkeypatch.setattr(transaction, "_rename_noreplace_at", rename_record)
        assert transaction._restore_cleanup_stage_record(parent_fd, stage_fd, output_id)
        assert (parent / output_id).is_dir()
        (stage / "record").mkdir()
        assert not transaction._restore_cleanup_stage_record(parent_fd, stage_fd, output_id)
    finally:
        os.close(stage_fd)
        os.close(parent_fd)


def test_rename_noreplace_handles_missing_symbol_results_and_unexpected_errors(
    monkeypatch,
):
    monkeypatch.setattr(transaction.ctypes, "CDLL", lambda *_a, **_k: SimpleNamespace())
    assert not transaction._rename_noreplace_at("source", 1, "destination", 2)

    calls = []
    error_number = errno.EEXIST

    def renameat2(*args):
        calls.append(args)
        return 0 if len(calls) == 1 else -1

    monkeypatch.setattr(renameat2, "argtypes", None, raising=False)
    monkeypatch.setattr(renameat2, "restype", None, raising=False)
    monkeypatch.setattr(
        transaction.ctypes, "CDLL",
        lambda *_a, **_k: SimpleNamespace(renameat2=renameat2),
    )
    monkeypatch.setattr(transaction.ctypes, "get_errno", lambda: error_number)

    assert transaction._rename_noreplace_at("source", 1, "destination", 2)
    assert calls[-1][-1] == 1
    assert not transaction._rename_noreplace_at("source", 1, "destination", 2)

    error_number = errno.EIO
    with pytest.raises(OSError, match="Input/output error"):
        transaction._rename_noreplace_at("source", 1, "destination", 2)


def test_delete_retained_build_output_refuses_unsafe_rmtree(tmp_path, monkeypatch):
    project, output_id, artifact_root, logs_root = _retained_build_output(tmp_path)
    monkeypatch.setattr(transaction.shutil.rmtree, "avoids_symlink_attacks", False)
    with pytest.raises(RuntimeError, match="safe descriptor-relative build-output cleanup is unavailable"):
        transaction.delete_retained_build_output(
            tmp_path, project, "demo", output_id, dry_run=False,
        )
    assert artifact_root.is_dir() and logs_root.is_dir()


def test_cleanup_reports_restore_oserror_and_preserves_staged_records(
    monkeypatch, tmp_path,
):
    project, output_id, _artifact_root, _logs_root = _retained_build_output(tmp_path)
    identity = transaction.retained_build_output_identity(tmp_path, project, "demo", output_id)
    monkeypatch.setattr(transaction, "_staged_cleanup_identity_matches", lambda *_a: False)
    monkeypatch.setattr(
        transaction, "_restore_cleanup_stage_record",
        lambda *_a: (_ for _ in ()).throw(OSError(errno.EIO, "injected restore failure")),
    )

    with pytest.raises(RuntimeError, match="cleanup stopped and retained records need inspection at"):
        transaction.delete_retained_build_output(
            tmp_path, project, "demo", output_id,
            dry_run=False, expected_identity=identity,
        )
    assert len(list((project.project_root / "artifacts").glob(".cmru-cleanup-*/record"))) == 1
    assert len(list((project.project_root / "logs").glob(".cmru-cleanup-*/record"))) == 1


def test_cleanup_restores_records_and_ignores_a_stage_removed_during_recovery(
    monkeypatch, tmp_path,
):
    project, output_id, artifact_root, logs_root = _retained_build_output(tmp_path)
    identity = transaction.retained_build_output_identity(tmp_path, project, "demo", output_id)
    monkeypatch.setattr(transaction, "_staged_cleanup_identity_matches", lambda *_a: False)

    def restore_without_race(source, source_fd, destination, destination_fd):
        os.rename(source, destination, src_dir_fd=source_fd, dst_dir_fd=destination_fd)
        return True

    monkeypatch.setattr(transaction, "_rename_noreplace_at", restore_without_race)
    original_rmdir = transaction.os.rmdir
    vanished = False

    def remove_then_report_missing(path, *args, **kwargs):
        nonlocal vanished
        if not vanished and str(path).startswith(".cmru-cleanup-"):
            vanished = True
            original_rmdir(path, *args, **kwargs)
            raise FileNotFoundError(path)
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(transaction.os, "rmdir", remove_then_report_missing)
    with pytest.raises(RuntimeError, match="retained build record changed during cleanup"):
        transaction.delete_retained_build_output(
            tmp_path, project, "demo", output_id,
            dry_run=False, expected_identity=identity,
        )

    assert vanished
    assert (artifact_root / "build.json").is_file()
    assert (logs_root / "build.log").is_file()
    assert list((project.project_root / "artifacts").glob(".cmru-cleanup-*")) == []
    assert list((project.project_root / "logs").glob(".cmru-cleanup-*")) == []


def test_build_output_cleanup_preserves_new_id_when_atomic_restore_is_unavailable(
    monkeypatch, tmp_path,
):
    project, output_id, artifact_root, logs_root = _retained_build_output(tmp_path)
    expected_manifest = (artifact_root / "build.json").read_bytes()
    expected_wheel = (artifact_root / "dist" / "demo.whl").read_bytes()
    expected_log = (logs_root / "build.log").read_bytes()
    identity = transaction.retained_build_output_identity(
        tmp_path, project, "demo", output_id,
    )
    original_identity = transaction._retained_build_output_identity_from_fds
    original_id_calls = 0

    def make_staged_identity_differ(*args):
        nonlocal original_id_calls
        original_id_calls += 1
        staged_identity = original_identity(*args)
        if original_id_calls == 2:
            return replace(staged_identity, manifest_sha256="f" * 64)
        return staged_identity

    restore_calls = []
    artifact_parent = project.project_root / "artifacts"
    artifact_parent_stat = artifact_parent.stat()
    competing_identity = None

    def race_with_unavailable_atomic_restore(
        source, source_fd, destination, destination_fd,
    ):
        nonlocal competing_identity
        restore_calls.append((source, destination))
        destination_stat = os.fstat(destination_fd)
        if (destination_stat.st_dev, destination_stat.st_ino) == (
            artifact_parent_stat.st_dev, artifact_parent_stat.st_ino,
        ):
            assert competing_identity is None
            artifact_root.mkdir()
            created = artifact_root.stat()
            competing_identity = (created.st_dev, created.st_ino)
        return False

    monkeypatch.setattr(
        transaction, "_retained_build_output_identity_from_fds",
        make_staged_identity_differ,
    )
    monkeypatch.setattr(
        transaction, "_rename_noreplace_at", race_with_unavailable_atomic_restore,
    )
    with pytest.raises(
        RuntimeError, match="cleanup stopped and retained records need inspection at",
    ) as raised:
        transaction.delete_retained_build_output(
            tmp_path, project, "demo", output_id,
            dry_run=False, expected_identity=identity,
        )

    staged_artifacts = list((project.project_root / "artifacts").glob(".cmru-cleanup-*/record"))
    staged_logs = list((project.project_root / "logs").glob(".cmru-cleanup-*/record"))
    assert original_id_calls == 2 and len(restore_calls) == 2
    assert competing_identity is not None
    current_competing = artifact_root.stat()
    assert (current_competing.st_dev, current_competing.st_ino) == competing_identity
    assert artifact_root.is_dir() and list(artifact_root.iterdir()) == []
    assert len(staged_artifacts) == len(staged_logs) == 1
    assert (staged_artifacts[0] / "build.json").read_bytes() == expected_manifest
    assert (staged_artifacts[0] / "dist" / "demo.whl").read_bytes() == expected_wheel
    assert (staged_logs[0] / "build.log").read_bytes() == expected_log
    assert str(staged_artifacts[0]) in str(raised.value)
    assert str(staged_logs[0]) in str(raised.value)
    assert not logs_root.exists()


def test_build_output_cleanup_uses_the_previewed_parent_after_symlink_swap(monkeypatch, tmp_path):
    project, output_id, artifact_root, logs_root = _retained_build_output(tmp_path)
    identity = transaction.retained_build_output_identity(
        tmp_path, project, "demo", output_id,
    )
    artifact_parent = project.project_root / "artifacts"
    moved_parent = tmp_path / "moved-artifacts"
    external_parent = tmp_path / "external-artifacts"
    external_output = external_parent / output_id
    external_output.mkdir(parents=True)
    (external_output / "outside.txt").write_text("preserve me\n", encoding="utf-8")
    original_rmtree = transaction.shutil.rmtree
    swapped = False

    def swap_parent_then_rmtree(path, *args, dir_fd=None, **kwargs):
        nonlocal swapped
        assert dir_fd is not None
        if not swapped:
            artifact_parent.rename(moved_parent)
            artifact_parent.symlink_to(external_parent, target_is_directory=True)
            swapped = True
        return original_rmtree(path, *args, dir_fd=dir_fd, **kwargs)

    swap_parent_then_rmtree.avoids_symlink_attacks = original_rmtree.avoids_symlink_attacks
    monkeypatch.setattr(transaction.shutil, "rmtree", swap_parent_then_rmtree)

    transaction.delete_retained_build_output(
        tmp_path, project, "demo", output_id,
        dry_run=False, expected_identity=identity,
    )

    assert swapped
    assert (external_output / "outside.txt").is_file()
    assert not (moved_parent / output_id).exists()
    assert not logs_root.exists()
    assert artifact_parent.is_symlink()


def test_cleanup_cli_removes_a_confirmed_forbidden_build_output(monkeypatch, tmp_path):
    preview_project, output_id, artifact_root, logs_root = _retained_build_output(tmp_path)
    project = cli.ProjectConfig("demo", {}, {}, project_root=preview_project.project_root)
    monkeypatch.setattr(cli, "_resolve_config", lambda _arg: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: (
        tmp_path, {"demo": project}, ["demo"], ["demo"], [], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    ))
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["demo"])

    assert cli.main([
        "cleanup", "demo", "--delete-build-output", output_id, "--yes",
    ]) == 0
    assert not artifact_root.exists() and not logs_root.exists()


def test_transaction_child_rejects_legacy_records_for_build_branches(monkeypatch, tmp_path):
    source = tmp_path / "source"
    child = tmp_path / "child"
    source.mkdir()
    child.mkdir()
    branch = "cmru-build-child"
    record = SimpleNamespace(
        purpose="cmru-legacy", branch=branch, worktree_path=child,
        source_git_root=source, workspace_id="workspace-1", base_commit="a" * 40,
    )

    class Shared:
        def discover_git_context(self, candidate):
            if Path(candidate).resolve() == child.resolve():
                return child, tmp_path / ".git", branch, "a" * 40
            return source, tmp_path / ".git", "main", "b" * 40

        def list_git_worktrees(self, _source):
            return [SimpleNamespace(path=child, is_primary=False, branch=branch)]

        def find_workspace(self, *_args):
            return record

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: Shared())
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(child))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, branch)
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)
    monkeypatch.setenv("CMRU_WORKSPACE_ID", "workspace-1")

    with pytest.raises(RuntimeError, match="recordless legacy compatibility is release-resume only"):
        transaction.is_transaction_child(child)

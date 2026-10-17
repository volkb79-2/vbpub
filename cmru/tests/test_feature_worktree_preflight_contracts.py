"""Behavioral boundaries for feature-source release discovery and provenance."""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from cmru import cli, config, exit_codes, transaction


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["git"], returncode, stdout, stderr)


def test_main_tree_secret_advice_suggests_one_matching_main_file_without_reading_it(
    tmp_path, monkeypatch,
):
    source = tmp_path / "repo"
    main = tmp_path / "main tree"
    nested = source / "nested"
    secret = main / "nested" / "cmru.secret.toml"
    nested.mkdir(parents=True)
    secret.parent.mkdir(parents=True)
    secret.write_text("[github]\ntoken = 'not-read'\n", encoding="utf-8")
    item = SimpleNamespace(
        path=main, branch="main", is_prunable=False, is_bare=False,
    )
    monkeypatch.setattr(
        transaction, "project_git_family_groups", lambda *_: {source: [object()]},
    )
    monkeypatch.setattr(
        transaction, "_shared_worktree",
        lambda: SimpleNamespace(list_git_worktrees=lambda _root: [item]),
    )

    advice = cli._main_tree_secret_advice(
        nested / "cmru.toml", source, {"demo": object()}, ["demo"],
    )

    assert "CMRU did not read it" in advice
    assert f"--secret-file '{secret}'" in advice


@pytest.mark.parametrize(
    "families,worktrees",
    [
        ({Path("/repo-a"): [], Path("/repo-b"): []}, None),
        ({Path("/repo-a"): []}, []),
        ({Path("/repo-a"): []}, [
            SimpleNamespace(
                path=Path("/main"), branch="main", is_prunable=False,
                is_bare=False,
            ),
            SimpleNamespace(
                path=Path("/other-main"), branch="main", is_prunable=False,
                is_bare=False,
            ),
        ]),
    ],
)
def test_main_tree_secret_advice_stays_generic_without_one_unique_main_path(
    families, worktrees, monkeypatch,
):
    monkeypatch.setattr(
        transaction, "project_git_family_groups", lambda *_: families,
    )
    monkeypatch.setattr(
        transaction, "_shared_worktree",
        lambda: SimpleNamespace(list_git_worktrees=lambda _root: worktrees or []),
    )

    advice = cli._main_tree_secret_advice(
        Path("/repo/cmru.toml"), Path("/repo"), {"demo": object()}, ["demo"],
    )

    assert "not selected or read automatically" in advice
    assert "--secret-file PATH" in advice


def test_main_tree_secret_advice_does_not_suggest_a_missing_file(tmp_path, monkeypatch):
    source = tmp_path / "repo"
    main = tmp_path / "main"
    source.mkdir()
    main.mkdir()
    item = SimpleNamespace(
        path=main, branch="main", is_prunable=False, is_bare=False,
    )
    monkeypatch.setattr(
        transaction, "project_git_family_groups", lambda *_: {source: [object()]},
    )
    monkeypatch.setattr(
        transaction, "_shared_worktree",
        lambda: SimpleNamespace(list_git_worktrees=lambda _root: [item]),
    )

    advice = cli._main_tree_secret_advice(
        source / "cmru.toml", source, {"demo": object()}, ["demo"],
    )

    assert "not selected or read automatically" in advice


def test_main_tree_secret_advice_falls_back_when_worktree_discovery_is_indeterminate(
    tmp_path, monkeypatch,
):
    source = tmp_path / "repo"
    monkeypatch.setattr(
        transaction, "project_git_family_groups", lambda *_: {source: [object()]},
    )
    monkeypatch.setattr(
        transaction, "_shared_worktree",
        lambda: SimpleNamespace(
            list_git_worktrees=lambda _root: (_ for _ in ()).throw(OSError("permission denied")),
        ),
    )

    advice = cli._main_tree_secret_advice(
        source / "cmru.toml", source, {"demo": object()}, ["demo"],
    )

    assert "not selected or read automatically" in advice


def _feature_preflight(
    monkeypatch,
    tmp_path,
    *,
    branch="feature/release",
    head="a" * 40,
    rev_head=None,
    remote=None,
    status_results=(),
    ancestry_results=(),
    branch_refs=(),
    contexts=(),
    discover_error=None,
    valid_branch=True,
):
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    common = root / ".git"
    common.mkdir()
    initial = (root, common, branch, head)
    context_values = list(contexts) or [initial, initial]
    context_index = {"value": 0}

    def discover(_path):
        if discover_error is not None and context_index["value"] > 0:
            raise discover_error
        index = min(context_index["value"], len(context_values) - 1)
        context_index["value"] += 1
        return context_values[index]

    shared = SimpleNamespace(discover_git_context=discover)
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: common)
    monkeypatch.setattr(
        transaction, "_valid_local_branch_name", lambda *_: valid_branch,
    )
    monkeypatch.setattr(
        transaction, "fetch_origin_branch",
        lambda _root, _branch, **_kwargs: remote or head,
    )
    statuses = iter(status_results or [_completed(), _completed()])
    ancestry = iter(ancestry_results)

    def run_git(_root, *args, **_kwargs):
        if args[0] == "status":
            return next(statuses)
        if args[0] == "merge-base":
            return next(ancestry)
        raise AssertionError(args)

    refs = iter(branch_refs or [head, head])

    def local_git(_root, *args):
        if args == ("rev-parse", "HEAD"):
            return rev_head or head
        if args[0] == "rev-parse" and args[1].startswith("refs/heads/"):
            return next(refs)
        raise AssertionError(args)

    monkeypatch.setattr(cli, "run_local_git", run_git)
    monkeypatch.setattr(cli, "_git", local_git)
    return root, common, head


@pytest.mark.parametrize(
    "context,needle",
    [
        (lambda root, common, head: (root / "elsewhere", common, "feature/x", head), "same Git worktree"),
        (lambda root, common, head: (root, root / "other.git", "feature/x", head), "same Git worktree"),
        (lambda root, common, head: (root, common, None, head), "named local branch"),
        (lambda root, common, head: (root, common, "main", head), "requires a feature branch"),
    ],
)
def test_feature_preflight_rejects_wrong_or_unnamed_source_context(
    tmp_path, monkeypatch, context, needle,
):
    root, common, head = _feature_preflight(monkeypatch, tmp_path)
    shared = transaction._shared_worktree()
    original = shared.discover_git_context
    replacement = context(root, common, head)
    monkeypatch.setattr(shared, "discover_git_context", lambda _path: replacement)

    with pytest.raises(cli.RefusedBeforeChange, match=needle):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)

    monkeypatch.setattr(shared, "discover_git_context", original)


def test_feature_preflight_wraps_worktree_discovery_failure(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(
        transaction, "_shared_worktree",
        lambda: SimpleNamespace(discover_git_context=lambda _path: (_ for _ in ()).throw(ValueError("detached"))),
    )

    with pytest.raises(cli.RefusedBeforeChange, match="registered Git worktree"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)


def test_feature_preflight_refuses_an_invalid_branch_name(tmp_path, monkeypatch):
    root, _common, _head = _feature_preflight(
        monkeypatch, tmp_path, valid_branch=False,
    )

    with pytest.raises(cli.RefusedBeforeChange, match="not a valid origin branch name"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)


def test_feature_preflight_reports_status_errors_and_dirty_path_preview(tmp_path, monkeypatch):
    root, _common, _head = _feature_preflight(
        monkeypatch, tmp_path, status_results=[_completed(1, stderr="locked")],
    )
    with pytest.raises(RuntimeError, match="cannot inspect caller worktree status: locked"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)

    dirty = "".join(f" M file-{index}\n" for index in range(9))
    root, _common, _head = _feature_preflight(
        monkeypatch, tmp_path / "dirty", status_results=[_completed(stdout=dirty)],
    )
    with pytest.raises(cli.RefusedBeforeChange, match=r"file-0.*file-7, \.\.\."):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)

    root, _common, _head = _feature_preflight(
        monkeypatch, tmp_path / "short-dirty-list",
        status_results=[_completed(stdout=" M file-a\n M file-b\n")],
    )
    with pytest.raises(cli.RefusedBeforeChange, match="file-a, file-b"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)


def test_feature_preflight_rejects_moved_local_head_and_branch_ref(tmp_path, monkeypatch):
    root, _common, head = _feature_preflight(
        monkeypatch, tmp_path, rev_head="b" * 40,
    )
    with pytest.raises(cli.RefusedBeforeChange, match="branch and HEAD changed"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)

    root, _common, head = _feature_preflight(
        monkeypatch, tmp_path / "branch", branch_refs=["b" * 40, head],
    )
    with pytest.raises(cli.RefusedBeforeChange, match="local branch feature/release points"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)

    root, _common, head = _feature_preflight(
        monkeypatch, tmp_path / "branch-moved-after-fetch",
        branch_refs=[head, "b" * 40],
    )
    with pytest.raises(cli.RefusedBeforeChange, match="changed during origin preflight"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)


@pytest.mark.parametrize(
    "codes,remedy",
    [
        ((0, 1), "fast-forward local feature/release"),
        ((1, 0), "push the intended commit"),
        ((1, 1), "reconcile local feature/release"),
        ((2, 2), "repair Git's ancestry data"),
    ],
)
def test_feature_preflight_describes_each_local_remote_divergence(
    tmp_path, monkeypatch, codes, remedy,
):
    root, _common, _head = _feature_preflight(
        monkeypatch,
        tmp_path,
        remote="b" * 40,
        ancestry_results=[_completed(codes[0]), _completed(codes[1])],
    )

    with pytest.raises(cli.RefusedBeforeChange, match=remedy):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)


def test_feature_preflight_rechecks_caller_identity_and_final_status(tmp_path, monkeypatch):
    root, common, head = _feature_preflight(
        monkeypatch, tmp_path,
        contexts=[
            (tmp_path / "repo", tmp_path / "repo" / ".git", "feature/release", "a" * 40),
            (tmp_path / "repo", tmp_path / "repo" / ".git", "feature/changed", "a" * 40),
        ],
    )
    with pytest.raises(cli.RefusedBeforeChange, match="changed during origin preflight"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)

    root, _common, _head = _feature_preflight(
        monkeypatch, tmp_path / "discover",
        discover_error=ValueError("registration disappeared"),
    )
    with pytest.raises(cli.RefusedBeforeChange, match="changed during origin preflight"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)

    root, _common, _head = _feature_preflight(
        monkeypatch, tmp_path / "status-error",
        status_results=[_completed(), _completed(1, stderr="locked")],
    )
    with pytest.raises(RuntimeError, match="cannot recheck caller worktree status"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)

    root, _common, _head = _feature_preflight(
        monkeypatch, tmp_path / "dirty-after-fetch",
        status_results=[_completed(), _completed(stdout=" M changed-after-fetch\n")],
    )
    with pytest.raises(cli.RefusedBeforeChange, match="became dirty during origin preflight"):
        cli._preflight_feature_worktree_source(root, root, git_auth=None)


def test_feature_preflight_accepts_one_clean_commit_shared_with_origin(tmp_path, monkeypatch):
    root, _common, head = _feature_preflight(monkeypatch, tmp_path)

    assert cli._preflight_feature_worktree_source(root, root, git_auth=None) == (
        "feature/release", head,
    )


def _workspace(tmp_path, *, branch="cmru-release-test", labels=None):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    context = SimpleNamespace(
        workspace_id="workspace-id",
        namespace=SimpleNamespace(labels=labels),
    )
    return root, transaction.ReleaseWorkspace(
        root, root / ".worktrees" / branch, branch, "a" * 40, context,
    )


def test_release_source_record_round_trips_exact_branch_and_commit(tmp_path, monkeypatch):
    root, workspace = _workspace(tmp_path, labels={"cmru.source_mode": "worktree"})
    scope = root / ".cmru" / "scope"
    scope.mkdir(parents=True)
    monkeypatch.setattr(transaction, "_ensure_scope_dir", lambda _root: scope)
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: scope)
    monkeypatch.setattr(transaction, "_valid_local_branch_name", lambda *_: True)

    transaction.write_release_source(
        root, workspace, branch="feature/release", commit="a" * 40,
    )

    assert transaction.read_release_source(root, workspace) == {
        "mode": "worktree",
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    with pytest.raises(RuntimeError, match="already exists"):
        transaction.write_release_source(
            root, workspace, branch="feature/release", commit="a" * 40,
        )


@pytest.mark.parametrize(
    "branch,commit,needle",
    [
        ("main", "a" * 40, "invalid source branch"),
        ("feature/release", "bad", "does not match the transaction base"),
        ("feature/release", "b" * 40, "does not match the transaction base"),
    ],
)
def test_release_source_writer_refuses_main_and_unmatched_commits(
    tmp_path, monkeypatch, branch, commit, needle,
):
    root, workspace = _workspace(tmp_path)
    monkeypatch.setattr(transaction, "_valid_local_branch_name", lambda *_: True)

    with pytest.raises(RuntimeError, match=needle):
        transaction.write_release_source(root, workspace, branch=branch, commit=commit)


def test_release_source_reader_distinguishes_default_from_missing_feature_identity(
    tmp_path, monkeypatch,
):
    root, default_workspace = _workspace(tmp_path / "default")
    scope = root / ".cmru" / "scope"
    scope.mkdir(parents=True)
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: scope)
    assert transaction.read_release_source(root, default_workspace) is None

    feature_root, feature_workspace = _workspace(
        tmp_path / "feature", labels={"cmru.source_mode": "worktree"},
    )
    feature_scope = feature_root / ".cmru" / "scope"
    feature_scope.mkdir(parents=True)
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: feature_scope)
    with pytest.raises(RuntimeError, match="identity record is missing"):
        transaction.read_release_source(feature_root, feature_workspace)


@pytest.mark.parametrize(
    "labels,needle",
    [
        ([], "labels are malformed"),
        ({"cmru.source_mode": "unknown"}, "unknown release source mode"),
    ],
)
def test_release_source_reader_refuses_malformed_workspace_labels(
    tmp_path, monkeypatch, labels, needle,
):
    root, workspace = _workspace(tmp_path, labels=labels)
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: tmp_path / "scope")

    with pytest.raises(RuntimeError, match=needle):
        transaction.read_release_source(root, workspace)


def test_release_source_reader_refuses_nonregular_and_malformed_records(tmp_path, monkeypatch):
    root, workspace = _workspace(tmp_path, labels={"cmru.source_mode": "worktree"})
    scope = tmp_path / "scope"
    scope.mkdir()
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: scope)
    record = scope / f"{transaction._release_token(workspace)}.source.json"
    record.mkdir()
    with pytest.raises(RuntimeError, match="not a regular file"):
        transaction.read_release_source(root, workspace)
    record.rmdir()
    record.write_text("not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot read release source record"):
        transaction.read_release_source(root, workspace)
    record.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed or does not match"):
        transaction.read_release_source(root, workspace)


def test_release_source_writer_removes_partial_file_after_write_failure(tmp_path, monkeypatch):
    root, workspace = _workspace(tmp_path, labels={"cmru.source_mode": "worktree"})
    scope = root / ".cmru" / "scope"
    scope.mkdir(parents=True)
    monkeypatch.setattr(transaction, "_ensure_scope_dir", lambda _root: scope)
    monkeypatch.setattr(transaction, "_valid_local_branch_name", lambda *_: True)
    descriptor = {}

    def fail_after_open(fd, *_args, **_kwargs):
        descriptor["fd"] = fd
        raise OSError("disk full")

    monkeypatch.setattr(transaction.os, "fdopen", fail_after_open)
    record = scope / f"{transaction._release_token(workspace)}.source.json"
    with pytest.raises(OSError, match="disk full"):
        transaction.write_release_source(
            root, workspace, branch="feature/release", commit="a" * 40,
        )
    os.close(descriptor["fd"])
    assert not record.exists()


def test_release_source_reader_reports_filesystem_indeterminacy(tmp_path, monkeypatch):
    root, workspace = _workspace(tmp_path, labels={"cmru.source_mode": "worktree"})
    scope = tmp_path / "scope"
    scope.mkdir()
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: scope)
    record = scope / f"{transaction._release_token(workspace)}.source.json"
    original_lstat = Path.lstat

    def denied(path):
        if path == record:
            raise PermissionError("secret path denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(RuntimeError, match="cannot inspect release source record.*secret path denied"):
        transaction.read_release_source(root, workspace)


def test_explicit_missing_secret_file_fails_instead_of_falling_back(tmp_path):
    missing = tmp_path / "main" / "cmru.secret.toml"

    with pytest.raises(SystemExit) as exc:
        config._load_repository_secrets(
            tmp_path, {"demo": tmp_path / "demo" / "cmru.toml"},
            secret_file=missing,
        )
    assert exc.value.code == 2


def test_release_forwards_explicit_secret_file_and_adds_main_tree_advice_on_missing_token(
    tmp_path, monkeypatch,
):
    cfg_path = tmp_path / "repo" / "cmru.toml"
    secret = tmp_path / "main-tree" / "cmru.secret.toml"
    captured = {}
    project = SimpleNamespace(name="demo", git_tag=True)
    github = SimpleNamespace()
    loaded = (
        cfg_path.parent, {"demo": project}, ["demo"], ["demo"], [],
        "project-first", {}, object(), github, object(),
    )
    monkeypatch.setattr(cli, "_resolve_ahead_check_ref", lambda _args: None)
    monkeypatch.setattr(cli, "_apply_output_options", lambda _args: None)
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: cfg_path)

    def load(_path, **kwargs):
        captured["secret_file"] = kwargs.get("secret_file")
        return loaded

    monkeypatch.setattr(cli, "load_config", load)
    monkeypatch.setattr(cli, "_git_auth_for_repository", lambda _config: None)
    monkeypatch.setattr(transaction, "is_transaction_child", lambda _root: False)
    monkeypatch.setattr(cli, "_configure_native_release_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_ordered_configs", lambda configs, _order: configs)
    monkeypatch.setattr(cli, "_select_projects", lambda *_a: ["demo"])
    monkeypatch.setattr(
        cli, "require_project_publish_credentials",
        lambda *_a: (_ for _ in ()).throw(cli.CredentialMissing("publisher token is missing")),
    )
    monkeypatch.setattr(
        cli, "_main_tree_secret_advice",
        lambda *_a: "Pass --secret-file /path/to/main/cmru.secret.toml explicitly.",
    )
    args = SimpleNamespace(
        config=str(cfg_path), secret_file=str(secret), ahead_check_ref=None,
        log_append=False, dry_run=False, resume=None, target="demo",
        set_version=None, minor=False, major=False,
    )

    with pytest.raises(cli.CredentialMissing, match="Pass --secret-file"):
        cli._release_or_status("release", args, ["demo"])

    assert captured["secret_file"] == secret


def test_local_branch_validator_refuses_option_like_names_and_checks_git_format(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        transaction, "run_local_git",
        lambda *_args, **_kwargs: _completed(1),
    )
    assert transaction._valid_local_branch_name("", tmp_path) is False
    assert transaction._valid_local_branch_name("-force", tmp_path) is False
    assert transaction._valid_local_branch_name("feature/bad name", tmp_path) is False
    monkeypatch.setattr(
        transaction, "run_local_git",
        lambda *_args, **_kwargs: _completed(),
    )
    assert transaction._valid_local_branch_name("feature/release", tmp_path) is True


def test_origin_branch_lookup_validates_names_transport_and_ref_scope(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(transaction, "_valid_local_branch_name", lambda *_: False)
    with pytest.raises(transaction.UsageRefusal, match="invalid origin branch"):
        transaction._origin_branch_oid(tmp_path, "bad branch")

    monkeypatch.setattr(transaction, "_valid_local_branch_name", lambda *_: True)
    monkeypatch.setattr(
        transaction, "run_remote_git",
        lambda *_args, **_kwargs: _completed(2, stderr="denied"),
    )
    with pytest.raises(RuntimeError, match="cannot inspect origin branch.*denied"):
        transaction._origin_branch_oid(tmp_path, "feature/release")

    monkeypatch.setattr(
        transaction, "run_remote_git",
        lambda *_args, **_kwargs: _completed(stdout="ref result"),
    )
    monkeypatch.setattr(
        transaction, "parse_ls_remote_refs",
        lambda *_args, **_kwargs: {"refs/heads/other": "a" * 40},
    )
    with pytest.raises(RuntimeError, match="unexpected ref"):
        transaction._origin_branch_oid(tmp_path, "feature/release")

    monkeypatch.setattr(
        transaction, "parse_ls_remote_refs", lambda *_args, **_kwargs: {},
    )
    assert transaction._origin_branch_oid(tmp_path, "feature/release") is None
    monkeypatch.setattr(
        transaction, "parse_ls_remote_refs",
        lambda *_args, **_kwargs: {"refs/heads/feature/release": "a" * 40},
    )
    assert transaction._origin_branch_oid(tmp_path, "feature/release") == "a" * 40


@pytest.mark.parametrize("existing", [None, "a" * 40])
def test_origin_candidate_allocation_requires_an_empty_ref(
    monkeypatch, tmp_path, existing,
):
    monkeypatch.setattr(transaction, "_origin_branch_oid", lambda *_args, **_kwargs: existing)
    if existing is None:
        transaction.assert_origin_branch_absent(tmp_path, "cmru-release-test")
    else:
        with pytest.raises(transaction.RefusedBeforeChange, match="already exists"):
            transaction.assert_origin_branch_absent(tmp_path, "cmru-release-test")


@pytest.mark.parametrize(
    "advertised,fetch_result,tracking,current,expected,needle",
    [
        (None, None, None, None, None, "does not exist"),
        ("a" * 40, _completed(1, stderr="fetch denied"), None, None, None, "could not fetch"),
        ("a" * 40, _completed(), None, "a" * 40, None, "no resolvable commit"),
        ("a" * 40, _completed(), "a" * 40, "b" * 40, None, "moved while CMRU fetched"),
        ("a" * 40, _completed(), "a" * 40, None, None, "moved while CMRU fetched"),
        ("a" * 40, _completed(), "a" * 40, "a" * 40, "a" * 40, None),
    ],
)
def test_fetch_origin_branch_refuses_missing_failed_and_moving_refs(
    monkeypatch, tmp_path, advertised, fetch_result, tracking, current,
    expected, needle,
):
    values = iter([advertised, current])
    monkeypatch.setattr(
        transaction, "_origin_branch_oid", lambda *_args, **_kwargs: next(values),
    )
    monkeypatch.setattr(
        transaction, "run_remote_git",
        lambda *_args, **_kwargs: fetch_result,
    )
    if tracking is None:
        monkeypatch.setattr(
            transaction, "_git", lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("missing tracking ref"),
            ),
        )
    else:
        monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: tracking)

    if needle:
        with pytest.raises((RuntimeError, transaction.RefusedBeforeChange), match=needle):
            transaction.fetch_origin_branch(tmp_path, "feature/release")
    else:
        assert transaction.fetch_origin_branch(tmp_path, "feature/release") == expected


def test_feature_workspace_creation_requires_preflight_and_records_source_mode(
    tmp_path, monkeypatch,
):
    with pytest.raises(ValueError, match="absent-origin-branch preflight"):
        transaction.create_workspace(
            tmp_path, base="a" * 40, purpose="release", feature_source=True,
        )

    root = tmp_path / "repo"
    root.mkdir()
    created = {}
    context = SimpleNamespace()
    shared = SimpleNamespace(
        WorkspaceError=RuntimeError,
        workspace_id_for_path=lambda _path: "workspace-id",
        create_workspace=lambda _root, path, **kwargs: created.update(
            path=path, kwargs=kwargs,
        ) or context,
        remove_workspace=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(
        transaction, "_new_transaction_branch", lambda *_args, **_kwargs: "cmru-release-demo",
    )
    monkeypatch.setattr(transaction, "_worktree_dirname", lambda _branch: "candidate")
    absent = []
    monkeypatch.setattr(
        transaction, "assert_origin_branch_absent",
        lambda *_args, **_kwargs: absent.append(True),
    )
    monkeypatch.setattr(
        transaction, "run_local_git", lambda *_args, **_kwargs: _completed(),
    )

    workspace = transaction.create_workspace(
        root, base="a" * 40, purpose="release", scope="demo",
        require_remote_branch_absent=True, feature_source=True,
    )

    assert absent == [True]
    assert workspace.path == root / ".worktrees" / "candidate"
    assert created["kwargs"]["labels"]["cmru.source_mode"] == "worktree"


def _launcher_case(
    monkeypatch,
    tmp_path,
    *,
    source=None,
    results=None,
    pushed=True,
    existing_remote=None,
    remote_tip="a" * 40,
    progress_tip="a" * 40,
    child_rc=0,
    plan_refused=False,
    dry_run=False,
    from_worktree=True,
    resume=None,
    allow_uncommitted=False,
    ahead_check_ref=None,
    preflight_handoff=None,
    groups=None,
    source_reads=None,
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    commit = "a" * 40
    workspace = transaction.ReleaseWorkspace(
        root, root / ".worktrees" / "candidate", "cmru-release-demo", commit,
    )
    project = SimpleNamespace(evidence_paths=(), git_tag=False, prefix="demo-v")
    configs = {"demo": project}
    captured = {
        "create": None, "child_args": None, "run_child": None,
        "source_writes": [], "sync": [],
    }
    args = SimpleNamespace(
        from_worktree=from_worktree,
        allow_uncommitted=allow_uncommitted,
        ahead_check_ref=ahead_check_ref,
        dry_run=dry_run,
        resume=resume,
        target="demo",
        discard=["logs", "artifacts", "evidence"],
    )
    initial_child_args = ["demo"] + (["--from-worktree"] if from_worktree else [])
    family_groups = groups or {root: [project]}
    monkeypatch.setattr(
        cli, "_dispatch_independent_git_families", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        transaction, "project_git_family_groups", lambda *_args: family_groups,
    )
    monkeypatch.setattr(
        transaction, "source_git_root_for_projects", lambda *_args: root,
    )
    monkeypatch.setattr(cli, "_record_release_controller_identity", lambda _phase: {
        "version": "6.2.0", "wheel_sha256": "b" * 64,
        "distribution_sha256": "c" * 64,
    })
    monkeypatch.setattr(cli, "_consume_release_snapshot_handoff", lambda *_args: None)
    monkeypatch.setattr(
        cli, "_child_release_args",
        lambda *_args, **_kwargs: captured.update(
            child_args=list(initial_child_args),
        ) or list(initial_child_args),
    )
    monkeypatch.setattr(cli, "_preflight_feature_worktree_source", lambda *_args, **_kwargs: (
        "feature/release", commit,
    ))
    monkeypatch.setattr(cli, "_preflight_multi_family_release_tag_support", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transaction, "release_lock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args: {})
    monkeypatch.setattr(cli, "_project_config_paths_at_snapshot", lambda *_args: {
        "demo": Path("demo/cmru.toml"),
    })
    monkeypatch.setattr(cli, "_project_git_tag_policy_at_snapshot", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "_require_local_tag_inspection_support", lambda *_args: None)
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(transaction, "write_release_tag_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        transaction, "create_workspace",
        lambda *_args, **kwargs: captured.update(create=kwargs) or workspace,
    )
    monkeypatch.setattr(
        transaction, "write_release_source",
        lambda *_args, **kwargs: captured["source_writes"].append(kwargs),
    )
    monkeypatch.setattr(transaction, "clear_plan_refused", lambda *_args: None)
    monkeypatch.setattr(cli, "_credential_handoff", lambda *_args: object())
    monkeypatch.setattr(
        transaction, "run_child",
        lambda _workspace, child_args, **_kwargs: captured.update(
            child_args=list(child_args), run_child=True,
        ) or child_rc,
    )
    monkeypatch.setattr(
        transaction, "read_release_scope_for_path", lambda *_args: ["demo"],
    )
    monkeypatch.setattr(transaction, "assert_resume_workspace_committed", lambda *_args: None)
    monkeypatch.setattr(cli, "_project_config_paths_in_candidate", lambda *_args: {
        "demo": Path("demo/cmru.toml"),
    })
    monkeypatch.setattr(cli, "_project_release_policy_in_candidate", lambda *_args: ("demo-v", False))
    monkeypatch.setattr(cli, "_assert_resume_candidate_is_safe_to_replay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transaction, "resume_workspace", lambda *_args, **_kwargs: workspace)
    source_values = iter(source_reads) if source_reads is not None else None
    monkeypatch.setattr(
        transaction, "read_release_source",
        lambda *_args: next(source_values) if source_values is not None else source,
    )
    monkeypatch.setattr(transaction, "read_release_results", lambda *_args: results or {})
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_args: progress_tip)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_args: pushed)
    monkeypatch.setattr(transaction, "mark_backup_pushed", lambda *_args: captured.update(marked=True))
    monkeypatch.setattr(transaction, "_origin_branch_oid", lambda *_args, **_kwargs: existing_remote)
    monkeypatch.setattr(cli, "_git", lambda *_args, **_kwargs: commit)
    monkeypatch.setattr(
        transaction, "fetch_origin_branch", lambda *_args, **_kwargs: remote_tip,
    )
    monkeypatch.setattr(transaction, "retain_success_outputs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(transaction, "remove_backup_branch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transaction, "remove_workspace", lambda *_args, **_kwargs: captured.update(removed=True))
    monkeypatch.setattr(transaction, "forget_release_scope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli, "_sync_local_main_and_report",
        lambda *_args, **_kwargs: captured["sync"].append(True) or False,
    )
    monkeypatch.setattr(
        transaction, "plan_was_refused", lambda *_args: plan_refused,
    )
    return root, workspace, args, configs, captured, preflight_handoff


def _call_launcher(root, workspace, args, configs, preflight_handoff=None):
    with pytest.raises(SystemExit) as exc:
        cli._release_launcher(
            ["demo"], args, root / "cmru.toml", root, configs,
            ["demo"], ["demo"], ["demo"], ["demo"], None,
            preflight_handoff,
            github_config=SimpleNamespace(token="publisher-token"),
        )
    return exc.value.code


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"allow_uncommitted": True}, "cannot be used with --from-worktree"),
        ({"ahead_check_ref": "origin/main"}, "does not apply with --from-worktree"),
        ({"groups": {Path("one"): [], Path("two"): []}}, "one Git family per release"),
    ],
)
def test_feature_release_launcher_refuses_unsupported_source_options(
    tmp_path, monkeypatch, kwargs, needle,
):
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, **kwargs,
    )

    with pytest.raises(cli.UsageRefusal, match=needle):
        _call_launcher(root, workspace, args, configs, handoff)


def test_feature_release_launcher_rejects_the_internal_main_snapshot_handoff(
    tmp_path, monkeypatch, capsys,
):
    root, workspace, args, configs, _captured, _handoff = _launcher_case(
        monkeypatch, tmp_path, preflight_handoff=object(),
    )

    assert _call_launcher(root, workspace, args, configs, object()) == 2
    assert "cannot use the internal origin/main preflight handoff" in capsys.readouterr().err


def test_feature_release_launcher_writes_source_and_retains_published_pr_candidate(
    tmp_path, monkeypatch, capsys,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source, results={"demo": "demo-v1.0.0"},
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 0

    assert captured["source_writes"] == [{
        "branch": "feature/release", "commit": "a" * 40,
    }]
    assert captured["create"]["feature_source"] is True
    assert captured["create"]["require_remote_branch_absent"] is True
    assert "--from-worktree" in captured["child_args"]
    assert captured.get("removed") is None
    output = capsys.readouterr().out
    assert "retained PR candidate" in output
    assert "public while its PR may still be open or blocked" in output


def test_feature_release_launcher_recovers_an_exact_remote_push_without_its_marker(
    tmp_path, monkeypatch, capsys,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source, pushed=False,
        existing_remote="a" * 40,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 0

    assert captured.get("marked") is True
    assert captured.get("removed") is None
    assert "retained candidate origin/cmru-release-demo" in capsys.readouterr().out


def test_feature_release_launcher_refuses_an_unowned_or_mismatched_candidate(
    tmp_path, monkeypatch, capsys,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source, pushed=False,
        existing_remote="b" * 40,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) != 0
    assert "without this transaction's ownership marker" in capsys.readouterr().err


@pytest.mark.parametrize(
    "detail,has_advice",
    [
        ("could not fetch origin/feature/release: offline", True),
        ("caller worktree became dirty during preflight", False),
    ],
)
def test_feature_preflight_network_failure_adds_secret_advice_only_for_origin_access(
    tmp_path, monkeypatch, capsys, detail, has_advice,
):
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path,
    )
    monkeypatch.setattr(
        cli, "_preflight_feature_worktree_source",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError(detail)),
    )
    monkeypatch.setattr(
        cli, "_main_tree_secret_advice",
        lambda *_a: "Pass --secret-file /path/to/main/cmru.secret.toml.",
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 1
    error = capsys.readouterr().err
    assert detail in error
    assert ("Pass --secret-file" in error) is has_advice


def test_feature_release_launcher_refuses_published_results_without_durable_ownership(
    tmp_path, monkeypatch, capsys,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source, results={"demo": "demo-v1.0.0"},
        pushed=False,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) != 0
    assert "published results but no durable" in capsys.readouterr().err


def test_feature_release_launcher_requires_remote_candidate_and_progress_to_match(
    tmp_path, monkeypatch, capsys,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source,
        results={"demo": "demo-v1.0.0"}, pushed=True,
        remote_tip="b" * 40,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) != 0
    assert "do not name the same final commit" in capsys.readouterr().err


def test_feature_release_launcher_refuses_lost_source_record_after_child_success(
    tmp_path, monkeypatch, capsys,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source_reads=[None],
    )

    assert _call_launcher(root, workspace, args, configs, handoff) != 0
    assert "lost its source identity record" in capsys.readouterr().err


@pytest.mark.parametrize(
    "changes,needle",
    [
        ({"allow_uncommitted": True}, "cannot be used with a feature-source resume"),
        ({"ahead_check_ref": "main"}, "does not apply to a feature-source resume"),
    ],
)
def test_feature_resume_rejects_default_mode_only_options(
    tmp_path, monkeypatch, capsys, changes, needle,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source, resume=tmp_path / "retained",
        from_worktree=False, **changes,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 2
    assert needle in capsys.readouterr().err


@pytest.mark.parametrize("already_forwarded", [False, True])
def test_feature_resume_forwards_source_mode_once_and_keeps_unpublished_candidate(
    tmp_path, monkeypatch, capsys, already_forwarded,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source, resume=tmp_path / "retained",
        from_worktree=already_forwarded,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 0

    assert captured["child_args"].count("--from-worktree") == 1
    assert captured.get("removed") is None
    assert "No public release result was recorded" in capsys.readouterr().out


def test_feature_resume_refuses_a_candidate_that_already_published_the_full_scope(
    tmp_path, monkeypatch, capsys,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source,
        results={"demo": "demo-v1.0.0"}, resume=tmp_path / "retained",
        from_worktree=True,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 2
    assert "already published every project" in capsys.readouterr().err


def test_feature_child_failure_preserves_pr_candidate_without_syncing_main(
    tmp_path, monkeypatch, capsys,
):
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    root, workspace, args, configs, captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=source, child_rc=1,
    )
    monkeypatch.setattr(
        cli, "_sync_local_main_and_report",
        lambda *_a, **_k: pytest.fail("feature-source child failure synced main"),
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 1
    assert captured.get("removed") is None
    error = capsys.readouterr().err
    assert "origin/main and local main were not updated" in error
    assert "was retained for inspection" in error


def test_release_child_refuses_source_identity_that_changes_before_candidate_push(
    tmp_path, monkeypatch,
):
    from cmru import version

    root, workspace = _workspace(tmp_path, labels={"cmru.source_mode": "worktree"})
    source = {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    }
    source_reads = iter([source, None])
    project = SimpleNamespace(name="demo")
    configs = {"demo": project}
    monkeypatch.setattr(cli, "_transaction_workspace_from_env", lambda _root: workspace)
    monkeypatch.setattr(transaction, "read_release_source", lambda *_: next(source_reads))
    monkeypatch.setattr(cli, "_release_child_controller_identity", lambda *_: {
        "version": "6.2.0",
        "wheel_sha256": "b" * 64,
        "distribution_sha256": "c" * 64,
    })
    monkeypatch.setattr(cli, "_validate_release_body_handlers", lambda *_: None)
    monkeypatch.setattr(cli, "_known_project_scope_names", lambda *_: None)
    monkeypatch.setattr(
        version, "detect_changed_projects", lambda *_a, **_k: [("demo", object())],
    )
    monkeypatch.setattr(cli, "_check_release_tool_dependencies", lambda *_a, **_k: None)
    args = SimpleNamespace(
        from_worktree=True, dry_run=False, minor=False, major=False,
        set_version=None, allow_tag_ahead_of_head=False,
        allow_stale_tool_deps=False, no_build=False,
    )

    with pytest.raises(cli.UnsafeRecord, match="source mode does not match its recorded transaction identity"):
        cli._release_child(
            args, root, configs, configs, ["demo"], ["demo"], object(), object(), None,
        )


def test_release_child_refuses_feature_source_when_flag_is_missing(tmp_path, monkeypatch):
    root, workspace = _workspace(tmp_path, labels={"cmru.source_mode": "worktree"})
    monkeypatch.setattr(cli, "_transaction_workspace_from_env", lambda _root: workspace)
    monkeypatch.setattr(transaction, "read_release_source", lambda *_: {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    })

    with pytest.raises(
        cli.UnsafeRecord,
        match="source mode does not match its recorded transaction identity",
    ):
        cli._release_child(
            SimpleNamespace(from_worktree=False, dry_run=True),
            root, {}, {}, [], [], object(), object(), None,
        )


def test_feature_source_plan_refusal_discards_candidate_without_syncing_main(
    tmp_path, monkeypatch, capsys,
):
    root, workspace, args, configs, captured, handoff = _launcher_case(
        monkeypatch, tmp_path, from_worktree=True, child_rc=2, plan_refused=True,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 2
    assert captured["removed"] is True
    assert captured["sync"] == []
    assert "Release plan refused before any project started" in capsys.readouterr().err


def test_feature_source_record_failure_reports_when_cleanup_also_fails(
    tmp_path, monkeypatch, capsys,
):
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, from_worktree=True,
    )
    monkeypatch.setattr(
        transaction, "write_release_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("source write failed")),
    )
    removed = []

    def fail_cleanup(candidate):
        removed.append(candidate)
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(transaction, "remove_workspace", fail_cleanup)
    assert _call_launcher(root, workspace, args, configs, handoff) == 1
    assert removed == [workspace]
    error = capsys.readouterr().err
    assert "feature-source identity could not be recorded" in error
    assert "cleanup failed" in error


def test_from_worktree_resume_refuses_a_default_source_candidate(tmp_path, monkeypatch, capsys):
    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=None,
        source_reads=[None], resume=tmp_path / "retained", from_worktree=True,
    )

    assert _call_launcher(root, workspace, args, configs, handoff) == 2
    assert "requires a retained feature-source transaction" in capsys.readouterr().err


def test_default_resume_refuses_project_edits_and_names_the_default_source(
    tmp_path, monkeypatch, capsys,
):
    from cli_extended import CliFailure

    root, workspace, args, configs, _captured, handoff = _launcher_case(
        monkeypatch, tmp_path, source=None,
        resume=tmp_path / "retained", from_worktree=False,
    )
    monkeypatch.setattr(
        cli, "_uncommitted_release_paths", lambda *_a, **_k: {"demo": ["src/file.py"]},
    )

    with pytest.raises(CliFailure, match="origin/main is the only default release source") as exc:
        _call_launcher(root, workspace, args, configs, handoff)
    assert exc.value.exit_code == exit_codes.REFUSED
    error = str(exc.value)
    diagnostics = capsys.readouterr().err
    assert "origin/main is the only default release source" in error
    assert "src/file.py" in diagnostics


def _backup_case(
    monkeypatch, tmp_path, *, pushed, remote, ancestor=None, source=True,
):
    root, workspace = _workspace(tmp_path)
    workspace = transaction.ReleaseWorkspace(
        root, root / ".worktrees" / workspace.branch, workspace.branch,
        workspace.base, workspace.context,
    )
    remote_calls = []
    marked = []
    monkeypatch.setattr(
        transaction, "read_release_source",
        lambda *_args: ({"source_ref": "refs/heads/feature/x"} if source else None),
    )
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_args: pushed)
    monkeypatch.setattr(transaction, "_origin_branch_oid", lambda *_args, **_kwargs: remote)
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: "a" * 40)
    monkeypatch.setattr(
        transaction, "run_local_git",
        lambda *_args, **_kwargs: ancestor or _completed(),
    )
    monkeypatch.setattr(
        transaction, "run_remote_git",
        lambda *args, **kwargs: remote_calls.append((args, kwargs)) or _completed(),
    )
    monkeypatch.setattr(
        transaction, "mark_backup_pushed", lambda *_args: marked.append(True),
    )
    return workspace, remote_calls, marked


def test_feature_backup_push_requires_its_source_identity(tmp_path, monkeypatch):
    workspace, _remote_calls, _marked = _backup_case(
        monkeypatch, tmp_path, pushed=False, remote=None, source=False,
    )

    with pytest.raises(transaction.RefusedBeforeChange, match="lost its source identity"):
        transaction.push_backup_branch(workspace, require_feature_source=True)


def test_feature_backup_uses_empty_lease_and_records_the_push(tmp_path, monkeypatch):
    workspace, remote_calls, marked = _backup_case(
        monkeypatch, tmp_path, pushed=False, remote=None,
    )

    transaction.push_backup_branch(workspace, require_feature_source=True)

    assert remote_calls[0][0] == (
        workspace.path, "push", f"--force-with-lease=refs/heads/{workspace.branch}:" ,
        "origin", f"HEAD:refs/heads/{workspace.branch}",
    )
    assert marked == [True]


def test_feature_backup_recovers_a_push_that_matches_the_candidate_exactly(
    tmp_path, monkeypatch,
):
    workspace, remote_calls, marked = _backup_case(
        monkeypatch, tmp_path, pushed=False, remote="a" * 40,
    )

    transaction.push_backup_branch(workspace, require_feature_source=True)

    assert remote_calls == []
    assert marked == [True]


def test_feature_backup_refuses_a_colliding_remote_ref(tmp_path, monkeypatch):
    workspace, remote_calls, marked = _backup_case(
        monkeypatch, tmp_path, pushed=False, remote="b" * 40,
    )

    with pytest.raises(transaction.RefusedBeforeChange, match="appeared after allocation"):
        transaction.push_backup_branch(workspace)
    assert remote_calls == []
    assert marked == []


@pytest.mark.parametrize(
    "remote,ancestor,exception,needle",
    [
        (None, None, transaction.RefusedBeforeChange, "disappeared during this transaction"),
        ("b" * 40, _completed(1), transaction.RefusedBeforeChange, "not an ancestor"),
        ("b" * 40, _completed(2, stderr="object missing"), RuntimeError, "ancestry: object missing"),
    ],
)
def test_feature_backup_resume_refuses_missing_or_unproven_candidate_ancestry(
    tmp_path, monkeypatch, remote, ancestor, exception, needle,
):
    workspace, remote_calls, marked = _backup_case(
        monkeypatch, tmp_path, pushed=True, remote=remote, ancestor=ancestor,
    )

    with pytest.raises(exception, match=needle):
        transaction.push_backup_branch(workspace)
    assert remote_calls == []
    assert marked == []


def test_feature_backup_resume_refreshes_only_with_an_ancestor_lease(tmp_path, monkeypatch):
    remote = "b" * 40
    workspace, remote_calls, marked = _backup_case(
        monkeypatch, tmp_path, pushed=True, remote=remote, ancestor=_completed(),
    )

    transaction.push_backup_branch(workspace)

    assert remote_calls[0][0] == (
        workspace.path, "push",
        f"--force-with-lease=refs/heads/{workspace.branch}:{remote}",
        "origin", f"HEAD:refs/heads/{workspace.branch}",
    )
    assert marked == [True]


@pytest.mark.parametrize(
    "ancestry,exception,needle",
    [
        (0, None, None),
        (1, cli.UnsafeRecord, "not an ancestor of the retained local candidate"),
        (2, RuntimeError, "cannot verify retained candidate ancestry: missing object"),
    ],
)
def test_feature_resume_requires_published_results_on_durable_candidate(
    tmp_path, monkeypatch, ancestry, exception, needle,
):
    root, workspace = _workspace(tmp_path)
    tag = "demo-v1.0.0"
    tag_oid = "d" * 40
    remote_tags = {
        f"refs/tags/{tag}": tag_oid,
        f"refs/tags/{tag}^{{}}": tag_oid,
    }
    monkeypatch.setattr(
        transaction, "read_release_results", lambda *_: {"demo": tag},
    )
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_: {})
    monkeypatch.setattr(transaction, "fetch_origin_branch", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setattr(cli, "_git", lambda *_args, **_kwargs: "c" * 40)
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: remote_tags)
    outcomes = iter([
        _completed(ancestry, stderr="missing object" if ancestry == 2 else ""),
        _completed(),
        _completed(),
    ])
    monkeypatch.setattr(cli, "run_local_git", lambda *_args, **_kwargs: next(outcomes))

    call = lambda: cli._assert_resume_candidate_is_safe_to_replay(
        root, workspace, ["demo"],
        {"demo": SimpleNamespace(prefix="demo-v", git_tag=True)},
        git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        feature_source=True,
    )
    if exception is None:
        assert call() is None
    else:
        with pytest.raises(exception, match=needle):
            call()


def _abandon_remote_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _abandon_feature_workspace(tmp_path, monkeypatch, *, candidate_present=True, ancestry=0):
    root, workspace = _workspace(tmp_path, labels={"cmru.source_mode": "worktree"})
    tip = "c" * 40
    main = "e" * 40
    candidate_ref = f"refs/heads/{workspace.branch}"
    heads = {"refs/heads/main": main}
    if candidate_present:
        heads[candidate_ref] = tip
    first_response = "".join(f"{oid}\t{ref}\n" for ref, oid in heads.items())
    remote_responses = iter([
        _abandon_remote_result(stdout=first_response),
        _abandon_remote_result(),
        _abandon_remote_result(),
    ]) if candidate_present else iter([_abandon_remote_result(stdout=first_response)])
    remote_calls = []
    removed = []
    monkeypatch.setattr(
        transaction, "run_remote_git",
        lambda _root, *args, **kwargs: remote_calls.append(args)
        or next(remote_responses),
    )
    monkeypatch.setattr(transaction, "read_release_source", lambda *_: {
        "source_ref": "refs/heads/feature/release",
        "source_commit": "a" * 40,
    })
    monkeypatch.setattr(transaction, "read_release_results", lambda *_: {"demo": "demo-v1.0.0"})
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: tip)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(transaction, "_git", lambda *_args, **_kwargs: tip)
    monkeypatch.setattr(
        transaction, "run_local_git",
        lambda *_args, **_kwargs: _abandon_remote_result(ancestry),
    )
    monkeypatch.setattr(transaction, "mark_backup_removed", lambda *_: removed.append("marked"))
    monkeypatch.setattr(transaction, "remove_workspace", lambda _workspace: removed.append("workspace"))
    monkeypatch.setattr(transaction, "forget_release_scope", lambda *_: removed.append("scope"))
    return root, workspace, tip, main, candidate_ref, remote_calls, removed


def test_abandon_published_feature_candidate_only_after_exact_tip_is_in_origin_main(
    tmp_path, monkeypatch,
):
    root, workspace, tip, main, candidate_ref, remote_calls, removed = (
        _abandon_feature_workspace(tmp_path, monkeypatch)
    )

    transaction.abandon_workspace(
        root, workspace,
        expected_remote_candidate_oid=tip,
        expected_remote_main_oid=main,
    )

    assert removed == ["marked", "workspace", "scope"]
    assert remote_calls[0] == (
        "ls-remote", "--heads", "origin", candidate_ref, "refs/heads/main",
    )
    assert remote_calls[1] == (
        "push", f"--force-with-lease={candidate_ref}:{tip}",
        "origin", f":{candidate_ref}",
    )


def test_abandon_published_feature_candidate_may_record_host_deleted_pr_ref(
    tmp_path, monkeypatch,
):
    root, workspace, _tip, main, _candidate_ref, _calls, removed = (
        _abandon_feature_workspace(tmp_path, monkeypatch, candidate_present=False)
    )

    transaction.abandon_workspace(
        root, workspace,
        expected_remote_candidate_oid=None,
        expected_remote_main_oid=main,
        allow_missing_remote_candidate=True,
    )

    assert removed == ["marked", "workspace", "scope"]


def test_abandon_keeps_published_feature_candidate_when_main_does_not_contain_tip(
    tmp_path, monkeypatch,
):
    root, workspace, tip, main, _candidate_ref, _calls, removed = (
        _abandon_feature_workspace(tmp_path, monkeypatch, ancestry=1)
    )

    with pytest.raises(RuntimeError, match="not verified in origin/main"):
        transaction.abandon_workspace(
            root, workspace,
            expected_remote_candidate_oid=tip,
            expected_remote_main_oid=main,
        )

    assert removed == []


def test_abandon_rechecks_expected_main_and_published_progress_before_mutation(
    tmp_path, monkeypatch,
):
    root, workspace, tip, main, _candidate_ref, _calls, removed = (
        _abandon_feature_workspace(tmp_path, monkeypatch)
    )
    with pytest.raises(RuntimeError, match="origin/main changed after abandonment inspection"):
        transaction.abandon_workspace(
            root, workspace,
            expected_remote_candidate_oid=tip,
            expected_remote_main_oid="f" * 40,
        )
    assert removed == []

    # The same observed refs cannot retire a candidate after its local progress
    # record or branch tip changes while confirmation is pending.
    _root, workspace, tip, main, _ref, _calls, removed = (
        _abandon_feature_workspace(tmp_path / "progress", monkeypatch)
    )
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: "b" * 40)
    with pytest.raises(RuntimeError, match="candidate, progress, and origin refs changed"):
        transaction.abandon_workspace(
            workspace.repo_root, workspace,
            expected_remote_candidate_oid=tip,
            expected_remote_main_oid=main,
        )
    assert removed == []


@pytest.mark.parametrize(
    "allow_missing,completed,needle",
    [
        (False, True, "origin candidate ref is missing but its removal was not recorded"),
        (True, False, "an absent origin candidate can be retired only for a completed feature release"),
    ],
)
def test_abandon_absent_candidate_ref_requires_a_completed_merged_feature_release(
    tmp_path, monkeypatch, allow_missing, completed, needle,
):
    root, workspace, _tip, main, _ref, _calls, removed = (
        _abandon_feature_workspace(
            tmp_path, monkeypatch, candidate_present=False,
        )
    )
    if not completed:
        monkeypatch.setattr(transaction, "read_release_results", lambda *_: {})
    with pytest.raises(RuntimeError, match=needle):
        transaction.abandon_workspace(
            root, workspace,
            expected_remote_candidate_oid=None,
            expected_remote_main_oid=main,
            allow_missing_remote_candidate=allow_missing,
        )
    assert removed == []

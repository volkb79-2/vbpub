"""Adversarial witnesses for shared-worktree integration boundaries.

These tests exercise the fail-closed branches around source-tree loading,
independent Git families, child remapping, and retained-workspace recovery.
They are intentionally small and assert the diagnostic contract as well as the
exception type.
"""
from __future__ import annotations

import builtins
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, config, transaction


def _fake_shared(*, discover=None, records=()):
    class SharedError(Exception):
        pass

    discover = discover or (lambda path: (Path(path), Path("/common"), "main", "a" * 40))

    def ensure_workspace(record, *, labels=None, metadata=None):
        if metadata is not None:
            record.metadata = dict(metadata)
        if labels is not None:
            record.labels = dict(labels)
        return record

    def adopt_workspace(source, target, *, purpose="workspace", labels=None, metadata=None, **_kwargs):
        top, common, branch, head = discover(target)
        return SimpleNamespace(
            purpose=f"cmru-{purpose}" if purpose != "workspace" else purpose,
            branch=branch,
            worktree_path=Path(target),
            source_git_root=Path(source),
            workspace_id="seedid",
            base_commit=head,
            metadata=dict(metadata or {}),
            labels=dict(labels or {}),
        )

    return SimpleNamespace(
        WorkspaceError=SharedError,
        workspace_id_for_path=lambda path: "seedid",
        create_workspace=lambda *args, **kwargs: SimpleNamespace(
            worktree_path=Path(args[1]), branch=kwargs["branch"], base_commit=kwargs["base"],
        ),
        remove_workspace=lambda *args, **kwargs: None,
        discover_git_context=discover,
        list_workspaces=lambda common: list(records),
        find_workspace=lambda _common, path: next(
            (record for record in records if record.worktree_path == path), None
        ),
        ensure_workspace=ensure_workspace,
        adopt_workspace=adopt_workspace,
    )


def test_shared_loader_uses_checkout_fallback_when_dependency_is_not_importable(monkeypatch):
    real_import = builtins.__import__
    first = True

    def import_once_missing(name, *args, **kwargs):
        nonlocal first
        if name == "worktree" and first:
            first = False
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_once_missing)
    assert transaction._shared_worktree().__name__ == "worktree"


def test_shared_loader_reports_missing_checkout_dependency(monkeypatch):
    real_import = builtins.__import__

    def always_missing(name, *args, **kwargs):
        if name == "worktree":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", always_missing)
    monkeypatch.setattr(transaction.Path, "is_dir", lambda _path: False)
    with pytest.raises(ModuleNotFoundError, match="worktree"):
        transaction._shared_worktree()


def test_project_family_grouping_covers_empty_and_invalid_selection(monkeypatch, tmp_path):
    shared = _fake_shared(discover=lambda path: (tmp_path, tmp_path / ".git", "main", "a" * 40))
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    assert transaction.project_git_family_groups(tmp_path, []) == {tmp_path: []}

    with pytest.raises(RuntimeError, match="no project_root"):
        transaction.project_git_family_groups(tmp_path, [SimpleNamespace(name="bad")])

    def fail(_path):
        raise RuntimeError("not a repository")

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _fake_shared(discover=fail))
    with pytest.raises(RuntimeError, match="not inside a usable Git worktree"):
        transaction.project_git_family_groups(tmp_path, [SimpleNamespace(project_root="demo")])

    def families(path):
        top = tmp_path / ("left" if path.name == "left" else "right")
        return top, top / ".git", "main", "a" * 40

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _fake_shared(discover=families))
    left = SimpleNamespace(project_root=tmp_path / "left")
    right = SimpleNamespace(project_root="right")
    grouped = transaction.project_git_family_groups(tmp_path, [left, right])
    assert set(grouped) == {tmp_path / "left", tmp_path / "right"}

    monkeypatch.setattr(
        transaction,
        "_shared_worktree",
        lambda: _fake_shared(discover=lambda _path: (_ for _ in ()).throw(RuntimeError("no root"))),
    )
    with pytest.raises(RuntimeError, match="no selected project Git family"):
        transaction.project_git_family_groups(tmp_path, [])


def test_source_family_helper_refuses_multiple_families(monkeypatch, tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    monkeypatch.setattr(
        transaction,
        "project_git_family_groups",
        lambda *_args: {left: [object()], right: [object()]},
    )
    with pytest.raises(RuntimeError, match="independent Git families"):
        transaction.source_git_root_for_projects(tmp_path, [object(), object()])


def test_create_workspace_removes_shared_allocation_when_reset_fails(monkeypatch, tmp_path):
    removed = []
    context = SimpleNamespace()
    shared = _fake_shared()
    shared.create_workspace = lambda *args, **kwargs: context
    shared.remove_workspace = lambda value, **kwargs: removed.append((value, kwargs))
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    calls = []
    monkeypatch.setattr(
        transaction.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(kwargs) or subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="reset failed"
        ),
    )
    with pytest.raises(RuntimeError, match="git reset --hard main failed"):
        transaction.create_workspace(tmp_path, base="main", purpose="build")
    assert calls[0]["check"] is False
    assert calls[0]["text"] is True
    assert removed == [(context, {"force": True})]

    shared.remove_workspace = lambda value, **kwargs: (_ for _ in ()).throw(RuntimeError("cleanup"))
    with pytest.raises(RuntimeError, match="git reset --hard main failed"):
        transaction.create_workspace(tmp_path, base="main", purpose="build")


def test_create_workspace_wraps_shared_workspace_errors(monkeypatch, tmp_path):
    shared = _fake_shared()
    shared.create_workspace = lambda *args, **kwargs: (_ for _ in ()).throw(
        shared.WorkspaceError("allocator refused")
    )
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    with pytest.raises(RuntimeError, match="allocator refused"):
        transaction.create_workspace(tmp_path, base="main", purpose="build")


def test_resume_legacy_workspace_uses_git_fallback_when_shared_record_is_absent(
    monkeypatch, tmp_path
):
    root = tmp_path / "repo"
    path = root / ".worktrees" / "cmru-release-legacy"
    path.mkdir(parents=True)
    common = root / ".git"

    def discover_git_context(value):
        top = root if Path(value).resolve() == root.resolve() else path
        return top, common, "cmru-release-legacy", "b" * 40

    shared = _fake_shared(discover=discover_git_context)
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _value: common)
    monkeypatch.setattr(
        transaction,
        "_git",
        lambda _path, *args: "cmru-release-legacy" if args == ("branch", "--show-current") else "b" * 40,
    )
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_args: "a" * 40)
    monkeypatch.setattr(
        transaction,
        "run_local_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )
    fetched = []
    monkeypatch.setattr(
        transaction.subprocess,
        "run",
        lambda argv, **kwargs: fetched.append((argv, kwargs)) or subprocess.CompletedProcess(argv, 0),
    )
    resumed = transaction.resume_workspace(root, path)
    assert resumed.branch == "cmru-release-legacy"
    assert resumed.context.metadata[transaction._LEGACY_RESUME_METADATA_KEY] == (
        transaction._LEGACY_RESUME_METADATA_VALUE
    )
    assert fetched and fetched[0][0][:3] == ["git", "fetch", "--prune"]


def test_resume_revalidates_leftover_legacy_removal_bridge_record(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    path = root / ".worktrees" / "cmru-release-legacy"
    path.mkdir(parents=True)
    common = root / ".git"
    record = SimpleNamespace(
        purpose="cmru-legacy",
        branch="cmru-release-legacy",
        worktree_path=path,
        source_git_root=root,
        workspace_id="legacy-workspace",
        base_commit="b" * 40,
        metadata={},
    )
    shared = _fake_shared(
        discover=lambda _value: (path, common, "cmru-release-legacy", "b" * 40),
        records=(record,),
    )
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _value: common)
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_args: "a" * 40)
    monkeypatch.setattr(
        transaction,
        "run_local_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )
    fetches = []
    monkeypatch.setattr(
        transaction,
        "run_remote_git",
        lambda repo_root, *args, **kwargs: fetches.append((repo_root, args, kwargs))
        or subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr=""),
    )

    resumed = transaction.resume_workspace(root, path)

    assert resumed.context is record
    assert fetches == [
        (path, ("fetch", "--prune", "origin", "main"), {"auth": None, "check": True}),
    ]
    assert record.metadata == {
        transaction._LEGACY_RESUME_METADATA_KEY: transaction._LEGACY_RESUME_METADATA_VALUE,
    }


@pytest.mark.parametrize("progress,returncode", [(None, 0), ("a" * 40, 1)])
def test_resume_refuses_leftover_legacy_record_without_usable_ancestor_progress(
    monkeypatch, tmp_path, progress, returncode,
):
    root = tmp_path / "repo"
    path = root / ".worktrees" / "cmru-release-legacy"
    path.mkdir(parents=True)
    common = root / ".git"
    record = SimpleNamespace(
        purpose="cmru-legacy",
        branch="cmru-release-legacy",
        worktree_path=path,
        source_git_root=root,
        workspace_id="legacy-workspace",
        base_commit="b" * 40,
        metadata={},
    )
    shared = _fake_shared(
        discover=lambda _value: (path, common, "cmru-release-legacy", "b" * 40),
        records=(record,),
    )
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _value: common)
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_args: progress)
    monkeypatch.setattr(
        transaction,
        "run_local_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], returncode, stdout="", stderr="not an ancestor"
        ),
    )

    message = "no valid CMRU release progress" if progress is None else "not an ancestor"
    with pytest.raises(RuntimeError, match=message):
        transaction.resume_workspace(root, path)
    assert record.metadata == {}


def test_resume_rejects_a_workspace_from_another_git_family(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    path = root / ".worktrees" / "cmru-release-legacy"
    path.mkdir(parents=True)
    monkeypatch.setattr(
        transaction,
        "_shared_worktree",
        lambda: _fake_shared(discover=lambda value: (path, Path("/other"), "branch", "a" * 40)),
    )
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _value: Path("/expected"))
    with pytest.raises(RuntimeError, match="not a worktree"):
        transaction.resume_workspace(root, path)


def test_resume_rejects_a_legacy_workspace_with_an_unrelated_branch(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    path = root / ".worktrees" / "unrelated"
    path.mkdir(parents=True)
    shared = _fake_shared(discover=lambda value: (path, Path("/common"), "branch", "a" * 40))
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _value: (_ for _ in ()).throw(RuntimeError("not git")))
    monkeypatch.setattr(transaction, "_git", lambda *_args: "feature/not-a-cmru-transaction")
    with pytest.raises(RuntimeError, match="not a retained cmru release branch"):
        transaction.resume_workspace(root, path)


def test_discard_build_workspace_reports_shared_discovery_failure(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    path = root / ".worktrees" / "cmru-build-retained"
    path.mkdir(parents=True)
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _value: Path("/same"))
    monkeypatch.setattr(
        transaction,
        "_git",
        lambda _path, *args: "cmru-build-retained" if args == ("branch", "--show-current") else "a" * 40,
    )
    monkeypatch.setattr(
        transaction,
        "_shared_worktree",
        lambda: _fake_shared(discover=lambda _path: (_ for _ in ()).throw(RuntimeError("lookup failed"))),
    )
    with pytest.raises(RuntimeError, match="lookup failed"):
        transaction.discard_build_workspace(root, path, dry_run=True)


def test_run_child_exports_project_scope(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path, "branch", "a" * 40)
    seen = []
    monkeypatch.setattr(
        transaction.subprocess,
        "run",
        lambda argv, **kwargs: seen.append((argv, kwargs)) or subprocess.CompletedProcess(argv, 7),
    )
    assert transaction.run_child(workspace, ["--dry-run"], project_names=["demo"]) == 7
    assert seen[0][1]["env"]["CMRU_TRANSACTION_PROJECTS"] == "demo"


def test_run_child_does_not_invent_a_workspace_id_when_shared_identity_is_empty(
    monkeypatch, tmp_path
):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path, "branch", "a" * 40)
    shared = _fake_shared()
    shared.workspace_id_for_path = lambda _path: ""
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    seen = []
    monkeypatch.setattr(
        transaction.subprocess,
        "run",
        lambda argv, **kwargs: seen.append(kwargs) or subprocess.CompletedProcess(argv, 0),
    )
    assert transaction.run_child(workspace, []) == 0
    assert "CMRU_WORKSPACE_ID" not in seen[0]["env"]


def test_remove_workspace_wraps_shared_removal_failure(monkeypatch, tmp_path):
    shared = _fake_shared()
    shared.remove_workspace = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("remove failed"))
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path, "branch", "a" * 40, context=SimpleNamespace()
    )
    with pytest.raises(RuntimeError, match="remove failed"):
        transaction.remove_workspace(workspace)

    shared.remove_unrecorded_workspace = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("legacy remove failed")
    )
    legacy = transaction.ReleaseWorkspace(tmp_path, tmp_path, "branch", "a" * 40)
    with pytest.raises(RuntimeError, match="legacy remove failed"):
        transaction.remove_workspace(legacy)


def test_config_git_scope_success_and_error_are_distinct(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    fake = SimpleNamespace(
        discover_git_context=lambda path: (repo, repo / ".git", "main", "a" * 40)
    )
    monkeypatch.setitem(__import__("sys").modules, "worktree", fake)
    assert config._git_scope(repo)["source_git_root"] == repo

    fake.discover_git_context = lambda path: (_ for _ in ()).throw(RuntimeError("bad git"))
    with pytest.raises(ValueError, match="could not resolve Git context"):
        config._git_scope(repo)


def test_config_git_scope_source_fallback_and_missing_dependency(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    real_import = builtins.__import__
    fake_worktree = SimpleNamespace(
        discover_git_context=lambda _path: (repo, repo / ".git", "main", "a" * 40)
    )
    monkeypatch.setitem(__import__("sys").modules, "worktree", fake_worktree)
    first = True

    def import_once_missing(name, *args, **kwargs):
        nonlocal first
        if name == "worktree" and first:
            first = False
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_once_missing)
    assert config._git_scope(repo)["source_git_root"] == repo

    def always_missing(name, *args, **kwargs):
        if name == "worktree":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", always_missing)
    monkeypatch.setattr(config.Path, "is_dir", lambda _path: False)
    with pytest.raises(ValueError, match="shared worktree library is unavailable"):
        config._git_scope(repo)


def test_config_rejects_an_unknown_runtime_kind(tmp_path):
    path = tmp_path / "cmru.toml"
    path.write_text(_project_document().replace('kind = "none"', 'kind = "future"'))
    with pytest.raises(SystemExit) as excinfo:
        config._parse_project_document(path, require_repository_facts=False)
    assert excinfo.value.code == 2


def _child_forge(source_root: Path, source_config: Path):
    parsed = SimpleNamespace(
        template_revision=None, env={}, prefix="demo-v", scm_dist=None,
        changelog="CHANGES.md", build_metadata={}, artifact_dirs=[], evidence_paths=[],
        build_step="build", runtime_kind="none", tool_dependencies=[],
    )
    orchestration = SimpleNamespace(
        project_order=["demo"], default_projects=["demo"],
        default_steps=["build"], execution_mode="project-first",
        project_configs={"demo": source_config}, dependencies={},
    )
    return SimpleNamespace(
        repo_root=source_root, orchestration=orchestration, projects={"demo": parsed},
        cleanup=None,
        github=SimpleNamespace(owner="owner", repo="repo", token=None, owner_type="user"),
        targets=SimpleNamespace(registry=[]), env={}, project_tokens={},
    )


def _project_document() -> str:
    return '''schema_version = 1
[runtime]
kind = "none"
[project]
id = "demo"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "patch"
[project.release]
git_tag = true
build_step = "build"
[steps.build]
quiet = true
commands = [{label = "build", argv = ["true"], cwd = "."}]
'''


def test_load_config_remaps_child_scope_and_refuses_bad_scope(monkeypatch, tmp_path):
    source = tmp_path / "source"
    child = tmp_path / "child"
    source_config = source / "demo" / "cmru.toml"
    source_config.parent.mkdir(parents=True)
    source_config.write_text(_project_document())
    child_config = child / "demo" / "cmru.toml"
    child_config.parent.mkdir(parents=True)
    child_config.write_text(_project_document())
    orchestration_path = source / "cmru.orchestration.toml"
    monkeypatch.setattr(cli, "load_forge_config", lambda _path: _child_forge(source, source_config))
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(child))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-demo")
    monkeypatch.setenv("CMRU_TRANSACTION_PROJECTS", "demo")
    monkeypatch.setattr(transaction, "is_transaction_child", lambda path: Path(path) == child.resolve())
    loaded = cli.load_config(orchestration_path, validate_dependencies=False)
    assert loaded[0] == child and loaded[1]["demo"].project_root == child / "demo"

    monkeypatch.setenv("CMRU_TRANSACTION_PROJECTS", "demo,demo")
    with pytest.raises(ValueError, match="unique project names"):
        cli.load_config(orchestration_path, validate_dependencies=False)
    monkeypatch.setenv("CMRU_TRANSACTION_PROJECTS", "missing")
    with pytest.raises(ValueError, match="unknown project"):
        cli.load_config(orchestration_path, validate_dependencies=False)


def test_load_config_uses_project_paths_already_loaded_from_child(monkeypatch, tmp_path):
    source = tmp_path / "source"
    child = source / ".worktrees" / "release-candidate"
    child_config = child / "demo" / "cmru.toml"
    child_config.parent.mkdir(parents=True)
    child_config.write_text(_project_document())
    orchestration_path = child / "cmru.orchestration.toml"
    monkeypatch.setattr(
        cli, "load_forge_config", lambda _path: _child_forge(child, child_config)
    )
    monkeypatch.setattr(
        transaction, "is_transaction_child", lambda path: Path(path) == child.resolve()
    )
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(child))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-demo")
    monkeypatch.setenv("CMRU_TRANSACTION_PROJECTS", "demo")

    loaded = cli.load_config(orchestration_path, validate_dependencies=False)

    assert loaded[0] == child
    assert loaded[1]["demo"].project_root == child / "demo"


@pytest.mark.parametrize(
    "missing_key",
    ["CMRU_WORKSPACE_PATH", "CMRU_SOURCE_GIT_ROOT", transaction.BRANCH_ENV],
)
def test_load_config_refuses_incomplete_child_context(monkeypatch, tmp_path, missing_key):
    source = tmp_path / "source"
    source_config = source / "demo" / "cmru.toml"
    source_config.parent.mkdir(parents=True)
    source_config.write_text(_project_document())
    orchestration_path = source / "cmru.orchestration.toml"
    monkeypatch.setattr(cli, "load_forge_config", lambda _path: _child_forge(source, source_config))

    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(tmp_path / "child"))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-demo")
    monkeypatch.delenv(missing_key, raising=False)

    with pytest.raises(RuntimeError, match="incomplete CMRU transaction child context"):
        cli.load_config(orchestration_path, validate_dependencies=False)


def test_load_config_transaction_scope_requires_child_ownership(monkeypatch, tmp_path):
    source = tmp_path / "source"
    paths = {name: source / name / "cmru.toml" for name in ("demo", "other")}
    for path in paths.values():
        path.parent.mkdir(parents=True)
        path.write_text(_project_document().replace('id = "demo"', f'id = "{path.parent.name}"'))
    parsed = {
        name: SimpleNamespace(
            template_revision=None, env={}, prefix=f"{name}-v", scm_dist=None,
            changelog="CHANGES.md", build_metadata={}, artifact_dirs=[], evidence_paths=[],
            build_step="build", runtime_kind="none", tool_dependencies=[],
        )
        for name in paths
    }
    forge = SimpleNamespace(
        repo_root=source,
        orchestration=SimpleNamespace(
            project_order=["demo", "other"], default_projects=["demo", "other"],
            default_steps=["build"], execution_mode="project-first",
            project_configs=paths, dependencies={},
        ),
        projects=parsed, cleanup=None,
        github=SimpleNamespace(owner="owner", repo="repo", token=None, owner_type="user"),
        targets=SimpleNamespace(registry=[]), env={}, project_tokens={},
    )
    monkeypatch.setattr(cli, "load_forge_config", lambda _path: forge)
    orchestration_path = source / "cmru.orchestration.toml"

    monkeypatch.setenv("CMRU_TRANSACTION_PROJECTS", "demo")
    monkeypatch.delenv(transaction.CHILD_ENV, raising=False)
    loaded = cli.load_config(orchestration_path, validate_dependencies=False)
    assert set(loaded[1]) == {"demo", "other"}

    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.delenv("CMRU_TRANSACTION_PROJECTS", raising=False)
    with pytest.raises(RuntimeError, match="incomplete CMRU transaction child context"):
        cli.load_config(orchestration_path, validate_dependencies=False)


def test_resolve_invocation_context_keeps_project_git_scope(monkeypatch, tmp_path):
    source = tmp_path / "source"
    project_config = source / "demo" / "cmru.toml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(_project_document())
    orchestration = source / "cmru.orchestration.toml"
    target = source / "cfg" / "cmru.orchestration.toml"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    orchestration.symlink_to("cfg/cmru.orchestration.toml")
    forge = _child_forge(source, project_config)
    monkeypatch.setattr(config, "load_forge_config", lambda _path, **_kwargs: forge)
    monkeypatch.setattr(config, "_refuse_unregistered_project", lambda *_args: None)
    monkeypatch.setattr(config, "_project_for_directory", lambda *_args: "demo")
    monkeypatch.setattr(
        config, "_git_scope", lambda path: {"source_git_root": path, "git_common_dir": path / ".git"}
    )
    context = config.resolve_invocation_context(orchestration, cwd=project_config.parent)
    assert context.project_name == "demo"
    assert context.source_git_root == project_config.parent
    assert context.config_path == target.resolve()
    assert context.config_reference_path == orchestration
    assert cli._resolve_config(str(orchestration)) == orchestration


def test_load_config_refuses_missing_or_escaping_child_project(monkeypatch, tmp_path):
    source = tmp_path / "source"
    child = tmp_path / "child"
    source_config = source / "demo" / "cmru.toml"
    source_config.parent.mkdir(parents=True)
    source_config.write_text(_project_document())
    orchestration_path = source / "cmru.orchestration.toml"
    monkeypatch.setattr(cli, "load_forge_config", lambda _path: _child_forge(source, source_config))
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(child))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-demo")
    monkeypatch.setenv("CMRU_TRANSACTION_PROJECTS", "demo")
    monkeypatch.setattr(transaction, "is_transaction_child", lambda path: Path(path) == child.resolve())
    with pytest.raises(ValueError, match="missing from the isolated"):
        cli.load_config(orchestration_path, validate_dependencies=False)

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "cmru.toml").write_text(_project_document())
    child.mkdir()
    (child / "demo").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside orchestration root"):
        cli.load_config(orchestration_path, validate_dependencies=False)


def test_transaction_workspace_from_env_reads_shared_record_and_wraps_errors(monkeypatch, tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    context = SimpleNamespace(source_git_root=tmp_path, branch="cmru-release-x", base_commit="a" * 40)
    record = SimpleNamespace(worktree_path=path, branch="cmru-release-x")
    shared = SimpleNamespace(
        discover_git_context=lambda _path: (path, tmp_path / ".git", "branch", "a" * 40),
        list_workspaces=lambda _common: [record],
        ensure_workspace=lambda _record: context,
    )
    shared.find_workspace = lambda common, target: next(
        (item for item in shared.list_workspaces(common) if item.worktree_path == target),
        None,
    )
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(path))
    result = cli._transaction_workspace_from_env(tmp_path)
    assert result.context is context and result.repo_root == tmp_path

    shared.list_workspaces = lambda _common: [SimpleNamespace(worktree_path=tmp_path / "other")]
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-legacy")
    monkeypatch.setenv(transaction.BASE_ENV, "b" * 40)
    legacy = cli._transaction_workspace_from_env(tmp_path)
    assert legacy.context is None and legacy.branch == "cmru-release-legacy"

    shared.discover_git_context = lambda _path: (_ for _ in ()).throw(RuntimeError("bad record"))
    with pytest.raises(RuntimeError, match="invalid shared workspace context"):
        cli._transaction_workspace_from_env(tmp_path)


def test_dispatch_independent_families_covers_refusal_launcher_and_child_failure(
    monkeypatch, tmp_path
):
    left = SimpleNamespace(name="left")
    right = SimpleNamespace(name="right")
    configs = {"left": left, "right": right}
    groups = {tmp_path / "left": [left], tmp_path / "right": [right]}
    monkeypatch.setattr(transaction, "project_git_family_groups", lambda *_args: groups)
    config_path = tmp_path / "cmru.toml"
    with pytest.raises(RuntimeError, match="resume must target"):
        cli._dispatch_independent_git_families(
            "release", ["--resume", "x"], config_path, tmp_path, configs,
            ["left", "right"], original_target=None,
        )

    monkeypatch.setenv("CMRU_BIN", "/usr/bin/cmru")
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 3))
    assert cli._dispatch_independent_git_families(
        "release", [], config_path, tmp_path, configs, ["left", "right"], original_target=None,
    ) == 3

    monkeypatch.delenv("CMRU_BIN")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0))
    assert cli._dispatch_independent_git_families(
        "build", [], config_path, tmp_path, configs, ["left", "right"], original_target=None,
    ) == 0

    def fail(*_args, **_kwargs):
        raise OSError("cannot execute")

    monkeypatch.setattr(cli.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="could not dispatch"):
        cli._dispatch_independent_git_families(
            "build", [], config_path, tmp_path, configs, ["left", "right"], original_target=None,
        )


def test_dispatch_does_not_split_a_single_project_and_uses_path_launcher(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo")
    called = []
    monkeypatch.setattr(
        transaction, "project_git_family_groups",
        lambda *_args: (_ for _ in ()).throw(AssertionError("single project was split")),
    )
    assert cli._dispatch_independent_git_families(
        "build", [], tmp_path / "cmru.toml", tmp_path, {"demo": project}, ["demo"],
        original_target=None,
    ) is None

    monkeypatch.setenv("CMRU_BIN", "")
    monkeypatch.setattr(shutil, "which", lambda _name: "/found/cmru")
    monkeypatch.setattr(
        transaction, "project_git_family_groups",
        lambda *_args: {tmp_path / "left": [project], tmp_path / "right": [SimpleNamespace(name="other")]},
    )
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **_kwargs: called.append(argv) or subprocess.CompletedProcess(argv, 0),
    )
    assert cli._dispatch_independent_git_families(
        "build", [], tmp_path / "cmru.toml", tmp_path,
        {"demo": project, "other": SimpleNamespace(name="other")}, ["demo", "other"],
        original_target=None,
    ) == 0
    assert called[0][0] == "/found/cmru"


def test_release_dispatch_passes_exact_preflight_snapshot_to_each_family(
    monkeypatch, tmp_path,
):
    left = SimpleNamespace(name="left")
    right = SimpleNamespace(name="right")
    configs = {"left": left, "right": right}
    roots = {tmp_path / "left": [left], tmp_path / "right": [right]}
    snapshots = {
        tmp_path / "left": "a" * 40,
        tmp_path / "right": "b" * 40,
    }
    monkeypatch.setattr(transaction, "project_git_family_groups", lambda *_args: roots)
    monkeypatch.setenv("CMRU_BIN", "/usr/bin/cmru")
    seen = []

    def fake_run(argv, **kwargs):
        handoff = None
        if cli._RELEASE_PREFLIGHT_SNAPSHOT_FD_ENV in kwargs["env"]:
            fd = int(kwargs["env"][cli._RELEASE_PREFLIGHT_SNAPSHOT_FD_ENV])
            assert fd in kwargs["pass_fds"]
            handoff = os.read(fd, 4096).decode("utf-8")
        seen.append((argv, kwargs["env"], handoff))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(
        cli.subprocess, "run", fake_run,
    )

    with pytest.raises(RuntimeError, match="do not match the selected release families"):
        cli._dispatch_independent_git_families(
            "release", [], tmp_path / "cmru.toml", tmp_path, configs,
            ["left", "right"], original_target=None,
            origin_main_snapshots={tmp_path / "left": "a" * 40},
        )
    with pytest.raises(RuntimeError, match="do not match the selected release families"):
        cli._dispatch_independent_git_families(
            "build", [], tmp_path / "cmru.toml", tmp_path, configs,
            ["left", "right"], original_target=None,
            origin_main_snapshots=snapshots,
        )
    assert seen == []

    assert cli._dispatch_independent_git_families(
        "release", [], tmp_path / "cmru.toml", tmp_path, configs,
        ["left", "right"], original_target=None,
        origin_main_snapshots=snapshots,
    ) == 0
    assert [
        handoff for _argv, _env, handoff in seen
    ] == [
        f"{(tmp_path / 'left').resolve()}:{'a' * 40}",
        f"{(tmp_path / 'right').resolve()}:{'b' * 40}",
    ]
    assert all(
        "CMRU_RELEASE_PREFLIGHT_SNAPSHOT" not in env
        for _argv, env, _handoff in seen
    )


def test_child_release_args_removes_only_the_first_original_target(tmp_path):
    config_path = tmp_path / "cmru.toml"
    config_path.write_text("x")
    assert cli._child_release_args(
        ["--dry-run", "other", "demo", "demo"], config_path, tmp_path,
        original_target="demo",
    ) == ["--dry-run", "other", "demo", "--config", "cmru.toml"]


def _main_config_tuple(tmp_path: Path):
    project = cli.ProjectConfig(
        name="demo", env={}, steps={}, project_root=tmp_path / "demo", github_token="token",
    )
    return (
        tmp_path, {"demo": project}, ["demo"], ["demo"], [], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def test_main_exits_with_independent_build_and_release_dispatch_status(monkeypatch, tmp_path):
    cfg = tmp_path / "cmru.toml"
    cfg.write_text("[project]\n")
    monkeypatch.setattr(cli, "_resolve_config", lambda _value: cfg)
    monkeypatch.setattr(cli, "load_config", lambda _path: _main_config_tuple(tmp_path))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_dispatch_independent_git_families", lambda *args, **kwargs: 17)
    build = cli.main(["build", "demo", "--config", str(cfg)])
    assert build == 17

    release = cli.main(["release", "demo", "--dry-run", "--config", str(cfg)])
    assert release == 17


def test_family_rebase_and_dirty_path_guards(monkeypatch, tmp_path):
    project = cli.ProjectConfig(
        name="demo", env={}, steps={}, project_root=tmp_path.parent / "outside-selected"
    )
    with pytest.raises(RuntimeError, match="outside selected Git root"):
        cli._configs_for_git_family({"demo": project}, ["demo"], tmp_path)
    project = cli.ProjectConfig(
        name="demo", env={},
        steps={"build": [cli.Command("x", ["true"], tmp_path / "elsewhere")]},
        project_root=tmp_path / "demo",
    )
    (tmp_path / "demo").mkdir()
    with pytest.raises(RuntimeError, match="cwd escapes project root"):
        cli._configs_for_git_family({"demo": project}, ["demo"], tmp_path)

    assert cli._uncommitted_release_paths(
        tmp_path, {"demo": SimpleNamespace(project_root=None)}, ["demo"]
    ) == {}
    outside_project = cli.ProjectConfig(
        name="demo", env={}, steps={}, project_root=tmp_path.parent / "outside-selected"
    )
    with pytest.raises(RuntimeError, match="outside selected Git root"):
        cli._uncommitted_release_paths(tmp_path, {"demo": outside_project}, ["demo"])


def test_run_project_step_restores_ambient_context(monkeypatch, tmp_path):
    project = cli.ProjectConfig(
        name="demo", env={}, steps={}, cwd="demo", project_root=tmp_path / "demo",
        runner_steps={"build": object()},
    )
    (tmp_path / "demo").mkdir()
    monkeypatch.setenv("CMRU_WORKSPACE_ID", "stale")
    monkeypatch.setattr(cli, "execute_step", lambda *args, **kwargs: None)
    cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")
    assert os.environ["CMRU_WORKSPACE_ID"] == "stale"


@pytest.mark.parametrize("partial_key", [transaction.CHILD_ENV, "CMRU_WORKSPACE_PATH"])
def test_run_project_step_does_not_trust_partial_child_context(monkeypatch, tmp_path, partial_key):
    project = cli.ProjectConfig(
        name="demo", env={}, steps={}, cwd="demo", project_root=tmp_path / "demo",
        runner_steps={"build": object()},
    )
    (tmp_path / "demo").mkdir()
    for key in (transaction.CHILD_ENV, "CMRU_WORKSPACE_PATH", "CMRU_SOURCE_GIT_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(partial_key, "stale-value")
    seen = []
    monkeypatch.setattr(cli, "execute_step", lambda *args, **kwargs: seen.append(kwargs["extra_env"]))
    cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")
    assert transaction.CHILD_ENV not in seen[0]
    assert "CMRU_WORKSPACE_PATH" not in seen[0]


@pytest.mark.parametrize(
    "missing_key",
    ["CMRU_WORKSPACE_PATH", "CMRU_SOURCE_GIT_ROOT", transaction.BRANCH_ENV],
)
def test_run_project_step_refuses_incomplete_marked_child_context(
    monkeypatch, tmp_path, missing_key,
):
    project = cli.ProjectConfig(
        name="demo", env={}, steps={}, cwd="demo", project_root=tmp_path / "demo",
        runner_steps={"build": object()},
    )
    (tmp_path / "demo").mkdir()
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(tmp_path.parent))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-demo")
    monkeypatch.delenv(missing_key, raising=False)
    executed = []
    monkeypatch.setattr(cli, "execute_step", lambda *args, **kwargs: executed.append(True))

    with pytest.raises(RuntimeError, match="incomplete CMRU transaction child context"):
        cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")
    assert executed == []


def test_run_project_step_preserves_owned_child_context(monkeypatch, tmp_path):
    project = cli.ProjectConfig(
        name="demo", env={}, steps={}, cwd="demo", project_root=tmp_path / "demo",
        runner_steps={"build": object()},
    )
    (tmp_path / "demo").mkdir()
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(tmp_path.parent))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-test")
    monkeypatch.setattr(transaction, "is_transaction_child", lambda _path: True)
    seen = []
    monkeypatch.setattr(cli, "execute_step", lambda *args, **kwargs: seen.append(kwargs))
    cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")
    assert seen[0]["protected_env"]["CMRU_WORKSPACE_PATH"] == str(tmp_path)

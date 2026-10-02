from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction
from cmru.runner import StepConfig


def _loaded(root: Path, *projects):
    configs = {project.name: project for project in projects}
    names = list(configs)
    return (
        root, configs, names, names, ["build"], "project-first", {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def _step(name: str, *, cwd: str = "src") -> StepConfig:
    return StepConfig(
        name=name,
        commands=[{"label": "compile", "argv": ["python", "build.py"], "cwd": cwd}],
        bake_set_prefix=None, bake_set_vars=[], no_cache_env=None,
        clean_dirs=["dist"], required_env=["BUILD_TOKEN"], login=None,
        step_env={"MODE": "release"}, env_command=["./resolve-env"], quiet=True,
    )


def test_run_keeps_configured_default_steps_and_dry_run_never_executes(monkeypatch, tmp_path, capsys):
    project_root = tmp_path / "demo"
    project = SimpleNamespace(
        name="demo", cwd="demo", project_root=project_root, env={}, build_metadata={},
        runtime_kind="none", runner_steps={"build": _step("build"), "validate": _step("validate")},
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path, project))
    monkeypatch.setattr(cli, "run_project_step", lambda *_args: (_ for _ in ()).throw(AssertionError("dry-run executed a step")))

    assert cli.main(["run", "demo", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Run plan: build; projects: demo" in output
    assert "demo:build: Would run declared step build" in output
    assert f"cwd={project_root / 'src'}" in output
    assert "resolve-env (not executed during dry-run)" in output
    assert "No project command was started" in output

    assert cli.main(["run", "demo", "--validate", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Run plan: validate; projects: demo" in output
    assert "demo:validate" in output
    assert "demo:build" not in output


def test_version_overrides_are_exclusive_and_refused_for_untagged_projects(
    monkeypatch, tmp_path, capsys,
):
    assert cli.main(["release", "--minor", "--major"]) == 2
    assert "not allowed with argument" in capsys.readouterr().err

    project = cli.ProjectConfig(
        "demo", {}, {}, prefix="demo-v", git_tag=False, project_root=tmp_path,
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(transaction, "is_transaction_child", lambda _root: True)
    assert cli.main(["status", "demo", "--minor"]) == 2
    assert "do not apply to external-version or no-tag" in capsys.readouterr().err


def test_tool_deps_refresh_rejects_output_and_freshness_flags(monkeypatch, capsys):
    assert cli.main(["tool-deps", "--refresh", "provider", "--json"]) == 2
    assert "cannot be combined with --json" in capsys.readouterr().err
    assert cli.main(["tool-deps", "--refresh", "provider", "--allow-stale-tool-deps"]) == 2
    assert "cannot be combined with --json or --allow-stale-tool-deps" in capsys.readouterr().err


def test_user_parser_rejects_transaction_switch_and_removed_aliases(capsys):
    for argv in (
        ["build", "--_transaction-child"],
        ["get", "demo"],
        ["graph"],
        ["dependency-graph"],
        ["release", "--allow-tag-at-head"],
        ["init", "--layout", "1"],
    ):
        assert cli.main(argv) == 2
        capsys.readouterr()


def test_transaction_child_environment_needs_registered_isolated_worktree(
    monkeypatch, tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    common = tmp_path / ".git"

    class Shared:
        def discover_git_context(self, path):
            return source, common, "cmru-release-fake", "a" * 40

        def list_git_worktrees(self, _path):
            return [SimpleNamespace(path=source, is_primary=True, branch="cmru-release-fake")]

        def find_workspace(self, *_args):
            return None

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: Shared())
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(source))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-fake")
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)
    monkeypatch.setenv("CMRU_WORKSPACE_ID", "workspace-1")
    with pytest.raises(RuntimeError, match="not a registered secondary worktree"):
        transaction.is_transaction_child(source)


def _transaction_child_context(monkeypatch, tmp_path, *, branch="cmru-release-child", record=None,
                               child_top=None, source_top=None, child_common=None,
                               source_common=None, actual_branch=None, worktrees=None,
                               discovery_error=None):
    source = tmp_path / "source"
    child = tmp_path / "child"
    source.mkdir(exist_ok=True)
    child.mkdir(exist_ok=True)
    common = tmp_path / ".git"
    child_common = child_common or common
    source_common = source_common or common
    child_top = child_top or child
    source_top = source_top or source
    actual_branch = actual_branch or branch
    if record is False:
        record = None
    elif record is None:
        record = SimpleNamespace(
            purpose="cmru-release" if branch.startswith("cmru-release-") else "cmru-build",
            branch=branch,
            worktree_path=child,
            source_git_root=source,
            workspace_id="workspace-1",
            base_commit="a" * 40,
        )

    class Shared:
        def discover_git_context(self, path):
            if discovery_error is not None:
                raise discovery_error
            if Path(path).resolve() == child.resolve():
                return child_top, child_common, actual_branch, "a" * 40
            return source_top, source_common, "main", "b" * 40

        def list_git_worktrees(self, _path):
            return worktrees if worktrees is not None else [
                SimpleNamespace(path=child, is_primary=False, branch=branch),
            ]

        def find_workspace(self, *_args):
            return record

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: Shared())
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(child))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, branch)
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)
    monkeypatch.setenv("CMRU_WORKSPACE_ID", "workspace-1")
    return source, child


@pytest.mark.parametrize(
    "branch,record",
    [
        ("cmru-release-child", "matching"),
        ("cmru-build-child", "matching"),
    ],
)
def test_transaction_child_accepts_registered_matching_release_and_build_worktrees(
    monkeypatch, tmp_path, branch, record,
):
    if record == "matching":
        record = SimpleNamespace(
            purpose="cmru-release" if branch.startswith("cmru-release-") else "cmru-build",
            branch=branch,
            worktree_path=tmp_path / "child", source_git_root=tmp_path / "source",
            workspace_id="workspace-1", base_commit="a" * 40,
        )
    source, child = _transaction_child_context(
        monkeypatch, tmp_path, branch=branch, record=record,
    )
    assert transaction.is_transaction_child(child) is True


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"missing": "CMRU_WORKSPACE_PATH"}, "incomplete CMRU transaction child context"),
        ({"missing": "CMRU_RELEASE_BASE"}, "incomplete CMRU transaction child context"),
        ({"missing": "CMRU_WORKSPACE_ID"}, "incomplete CMRU transaction child context"),
        ({"expected_path": "wrong"}, "does not match the loaded repository root"),
        ({"discovery_error": OSError("not a worktree")}, "invalid CMRU transaction child worktree"),
        ({"child_top": "wrong"}, "do not resolve to Git worktree roots"),
        ({"child_common": "other-git"}, "different Git family"),
        ({"actual_branch": "cmru-release-other"}, "branch mismatch"),
        ({"actual_branch": "main", "expected_branch": "main"}, "branch is not managed"),
        ({"primary": True}, "not a registered secondary worktree"),
        ({"purpose": "ciu-build"}, "not a CMRU release transaction"),
        ({"record_branch": "cmru-release-other"}, "shared transaction record does not match"),
        ({"record_path": "wrong"}, "shared transaction record does not match"),
        ({"record_source": "wrong"}, "shared transaction record has a different source root"),
        ({"record_id": "other"}, "workspace ID does not match its record"),
        ({"record_base": "other"}, "base does not match its record"),
    ],
)
def test_transaction_child_rejects_each_untrusted_routing_fact(
    monkeypatch, tmp_path, changes, message,
):
    branch = changes.get("expected_branch", "cmru-release-child")
    source = tmp_path / "source"
    child = tmp_path / "child"
    source.mkdir()
    child.mkdir()
    record = SimpleNamespace(
        purpose=changes.get("purpose", "cmru-release"),
        branch=changes.get("record_branch", branch),
        worktree_path=(tmp_path / changes["record_path"] if "record_path" in changes else child),
        source_git_root=(tmp_path / changes["record_source"] if "record_source" in changes else source),
        workspace_id="workspace-1",
        base_commit=("b" * 40 if changes.get("record_base") == "other" else "a" * 40),
    )
    worktrees = (
        [SimpleNamespace(path=child, is_primary=True, branch=branch)]
        if changes.get("primary") else None
    )
    child_top = tmp_path / changes["child_top"] if "child_top" in changes else None
    child_common = tmp_path / changes["child_common"] if "child_common" in changes else None
    source_common = tmp_path / changes["source_common"] if "source_common" in changes else None
    _transaction_child_context(
        monkeypatch, tmp_path, branch=branch, record=record, child_top=child_top,
        child_common=child_common, source_common=source_common,
        actual_branch=changes.get("actual_branch"), worktrees=worktrees,
        discovery_error=changes.get("discovery_error"),
    )
    if "missing" in changes:
        monkeypatch.delenv(changes["missing"])
    if changes.get("expected_path") == "wrong":
        monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(tmp_path / "elsewhere"))
    if changes.get("record_id") == "other":
        monkeypatch.setenv("CMRU_WORKSPACE_ID", "other")

    with pytest.raises(RuntimeError, match=message):
        transaction.is_transaction_child(child)


@pytest.mark.parametrize("branch", ["cmru-release-child", "cmru-build-child"])
def test_transaction_child_rejects_recordless_secondary_worktrees(
    monkeypatch, tmp_path, branch,
):
    source, child = _transaction_child_context(
        monkeypatch, tmp_path, branch=branch, record=False,
    )

    with pytest.raises(RuntimeError, match="no shared ownership record"):
        transaction.is_transaction_child(child)


def test_transaction_child_rejects_legacy_removal_bridge_record_without_resume_validation(
    monkeypatch, tmp_path,
):
    branch = "cmru-release-child"
    record = SimpleNamespace(
        purpose="cmru-legacy",
        branch=branch,
        worktree_path=tmp_path / "child",
        source_git_root=tmp_path / "source",
        workspace_id="workspace-1",
        base_commit="a" * 40,
        metadata={},
    )
    _source, child = _transaction_child_context(
        monkeypatch, tmp_path, branch=branch, record=record,
    )

    with pytest.raises(RuntimeError, match="no validated resume metadata"):
        transaction.is_transaction_child(child)


@pytest.mark.parametrize(
    "progress,returncode,expected",
    [
        ("a" * 40, 0, True),
        ("c" * 40, 1, "not an ancestor"),
    ],
)
def test_transaction_child_rechecks_progress_for_validated_legacy_record(
    monkeypatch, tmp_path, progress, returncode, expected,
):
    branch = "cmru-release-child"
    record = SimpleNamespace(
        purpose="cmru-legacy",
        branch=branch,
        worktree_path=tmp_path / "child",
        source_git_root=tmp_path / "source",
        workspace_id="workspace-1",
        base_commit="a" * 40,
        metadata={
            transaction._LEGACY_RESUME_METADATA_KEY:
                transaction._LEGACY_RESUME_METADATA_VALUE,
        },
    )
    _source, child = _transaction_child_context(
        monkeypatch, tmp_path, branch=branch, record=record,
    )
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_args: progress)
    monkeypatch.setattr(
        transaction,
        "run_local_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], returncode, stdout="", stderr="not an ancestor"
        ),
    )

    if expected is True:
        assert transaction.is_transaction_child(child) is True
    else:
        with pytest.raises(RuntimeError, match=expected):
            transaction.is_transaction_child(child)


def test_repack_is_rejected_before_external_side_effects(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "cmru.handlers.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Docker was called")),
    )
    monkeypatch.setattr(
        "cmru.handlers._docker_login",
        lambda: (_ for _ in ()).throw(AssertionError("Docker login was called")),
    )
    assert cli.main([
        "handler", "oci-image-build", "--cwd", str(tmp_path),
        "--bake-file", "docker-bake.hcl", "--target", "demo", "--repack",
    ]) == 2
    assert "disabled" in capsys.readouterr().err.lower()

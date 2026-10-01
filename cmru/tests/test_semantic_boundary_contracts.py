"""Behavioral boundary cases for retained outputs, CLI errors, and release state."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import (
    bundle,
    changelog,
    cli,
    dependencies,
    getpy,
    handlers,
    release,
    resolve,
    runner,
    scaffold,
    standards,
    tester_gate,
    tool_deps,
    transaction,
)
from cmru.controller import cli as controller_cli

OUTPUT_COMMIT = "a" * 40
OUTPUT_ID = "20260927T120000Z_" + OUTPUT_COMMIT


def _valid_build_output(tmp_path: Path):
    root = tmp_path / OUTPUT_ID
    dist = root / "dist"
    dist.mkdir(parents=True)
    (dist / "alpha.whl").write_bytes(b"wheel")
    manifest = {
        "schema_version": 1,
        "kind": "cmru-local-build",
        "publication": "eligible",
        "project": "alpha",
        "build_id": OUTPUT_ID,
        "source_commit": OUTPUT_COMMIT,
        "source_commit_date": "2026-09-27T12:00:00+00:00",
        "source_tree_changes": [],
        "logs": [],
        "artifacts": [{
            "directory": "dist",
            "files": [{
                "path": "alpha.whl",
                "sha256": hashlib.sha256(b"wheel").hexdigest(),
                "bytes": "5",
            }],
        }],
    }
    (root / "build.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, manifest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("kind", "untrusted-build"),
        ("publication", "unknown"),
        ("project", "other"),
        ("build_id", "other-output"),
    ],
)
def test_publication_requires_each_manifest_identity_fact(tmp_path, field, value):
    root, manifest = _valid_build_output(tmp_path)
    manifest[field] = value
    (root / "build.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest does not authorize publication"):
        transaction.validate_build_output_tree(root, "alpha", OUTPUT_ID)


@pytest.mark.parametrize(
    ("fault", "value"),
    [
        ("reserved-directory", ".."),
        ("duplicate-directory", "dist"),
        ("files-not-list", "alpha.whl"),
        ("empty-files", []),
    ],
)
def test_publication_rejects_each_unsafe_artifact_directory_fact(tmp_path, fault, value):
    root, manifest = _valid_build_output(tmp_path)
    if fault == "duplicate-directory":
        manifest["artifacts"].append(copy.deepcopy(manifest["artifacts"][0]))
        manifest["artifacts"][1]["directory"] = value
    else:
        manifest["artifacts"][0]["directory" if fault == "reserved-directory" else "files"] = value
    (root / "build.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="malformed artifact directory"):
        transaction.validate_build_output_tree(root, "alpha", OUTPUT_ID)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "."),
        ("path", r"folder\\alpha.whl"),
        ("sha256", 17),
        ("sha256", "z" * 64),
        ("bytes", 5),
        ("bytes", "five"),
        ("bytes", "05"),
    ],
)
def test_publication_rejects_each_unsafe_file_coordinate_fact(tmp_path, field, value):
    root, manifest = _valid_build_output(tmp_path)
    manifest["artifacts"][0]["files"][0][field] = value
    (root / "build.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="unsafe file coordinate"):
        transaction.validate_build_output_tree(root, "alpha", OUTPUT_ID)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("kind", "untrusted-build"),
        ("publication", "unknown"),
        ("project", "other"),
        ("build_id", "other-output"),
    ],
)
def test_cleanup_refuses_each_mismatched_build_record_identity(
    tmp_path, monkeypatch, field, value,
):
    project_root = tmp_path / "alpha"
    artifact_root = project_root / "artifacts" / OUTPUT_ID
    logs_root = project_root / "logs" / OUTPUT_ID
    artifact_root.mkdir(parents=True)
    logs_root.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "kind": "cmru-local-build",
        "publication": "eligible",
        "project": "alpha",
        "build_id": OUTPUT_ID,
    }
    manifest[field] = value
    (artifact_root / "build.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        transaction, "_project_roots_for_retention",
        lambda *_args, **_kwargs: (project_root, project_root),
    )

    with pytest.raises(RuntimeError, match="manifest does not authorize cleanup"):
        transaction.delete_retained_build_output(
            tmp_path, SimpleNamespace(), "alpha", OUTPUT_ID, dry_run=True,
        )


def test_removed_backup_marker_creates_its_scope_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    workspace = SimpleNamespace(branch="cmru-release-20260927_120000-alpha-ab12cd")

    transaction.mark_backup_removed(repo, workspace)

    assert transaction.backup_was_removed(repo, workspace)


def test_abandon_uses_explicit_return_code_handling_for_all_remote_mutations(monkeypatch, tmp_path):
    branch = "cmru-release-20260927_120000-alpha-ab12cd"
    ref = f"refs/heads/{branch}"
    calls = []

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if argv[:3] == ["git", "ls-remote", "--heads"] and len(calls) == 1:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\t" + ref + "\n", "")
        if argv[:4] == ["git", "push", "origin", "--delete"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(transaction.subprocess, "run", run)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(transaction, "mark_backup_removed", lambda *_: None)
    monkeypatch.setattr(transaction, "remove_workspace", lambda *_: None)
    monkeypatch.setattr(transaction, "forget_release_scope", lambda *_: None)

    transaction.abandon_workspace(tmp_path, SimpleNamespace(branch=branch))

    assert len(calls) == 3
    assert all(kwargs["check"] is False for _argv, kwargs in calls)


@pytest.mark.parametrize("version", ["", ".", "..", "../archive", r"folder\archive", "/tmp/archive"])
def test_bundle_archive_template_must_resolve_to_a_filename(monkeypatch, tmp_path, version):
    monkeypatch.setenv("CMRU_TEST_ARCHIVE_VERSION", version)
    config = bundle.BundleConfig(
        project_root=tmp_path,
        wheel_project_root=tmp_path,
        dist_dir=tmp_path / "dist",
        bundle_dir=tmp_path / "dist" / "bundle",
        client_dir=tmp_path / "client",
        wheel_enabled=False,
        wheel_python_bin="python",
        wheel_find_links=None,
        archive_template="{version}",
        archive_version_env="CMRU_TEST_ARCHIVE_VERSION",
        archive_format="zip",
        copy_files=[],
        copy_dirs=[],
    )

    if version:
        with pytest.raises(ValueError, match="single filename"):
            bundle.create_archive(config)
    else:
        with pytest.raises(RuntimeError, match="must be set"):
            bundle.create_archive(config)


def test_retained_artifact_error_lists_the_actual_changed_file(tmp_path):
    root, _manifest = _valid_build_output(tmp_path)
    (root / "dist" / "alpha.whl").write_bytes(b"changed wheel bytes")

    with pytest.raises(RuntimeError, match=r"changed=\['dist/alpha.whl'\]"):
        transaction.validate_build_output_tree(root, "alpha", OUTPUT_ID)


def _install_backfill_facts(monkeypatch, repo_root):
    monkeypatch.setattr(changelog, "_git", lambda _root, *args: (
        "a" * 40 if args[0] == "rev-parse" else "2026-09-27"
    ))
    monkeypatch.setattr(changelog, "_previous_project_tag", lambda *_args: None)
    monkeypatch.setattr(
        changelog, "_subject_groups", lambda *_args, **_kwargs: {"Fixed": ["fix: item"]}
    )
    monkeypatch.setattr(changelog, "_generated_exclusions", lambda *_args: [])


def test_backfill_diff_preserves_line_boundaries_and_write_accepts_existing_parent(
    monkeypatch, tmp_path, capsys,
):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    path = project_root / "CHANGES.md"
    original = "# Changelog\n\n<!-- cmru: release history -->\n"
    path.write_text(original, encoding="utf-8")
    project = SimpleNamespace(
        name="demo", cwd="demo", paths=["demo"], prefix="demo-v", changelog="CHANGES.md",
    )
    _install_backfill_facts(monkeypatch, tmp_path)

    assert changelog.backfill_release_changelog(tmp_path, project, "demo-v1.2.3", dry_run=True)
    preview = capsys.readouterr().out
    assert preview.startswith(f"--- {path}\n+++ {path} (planned)\n")
    assert "-<!-- cmru: release history -->\n+<!-- cmru: release history -->" not in preview
    assert preview.endswith("\n")
    assert path.read_text(encoding="utf-8") == original

    # The normal backfill path must also work when its containing directory
    # already exists, as it does for an existing project changelog.
    assert changelog.backfill_release_changelog(tmp_path, project, "demo-v1.2.3")
    assert "backfilled-after-release tag=demo-v1.2.3" in path.read_text(encoding="utf-8")


def test_dependency_graph_write_is_idempotent_and_prints_a_well_formed_diff(
    tmp_path, capsys,
):
    config = tmp_path / "cmru.orchestration.toml"
    config.write_text(
        "schema_version = 1\n[orchestration]\nproject_order = ['demo']\n",
        encoding="utf-8",
    )
    report = dependencies.DependencyReport(("demo",), {"demo": ()}, {}, (), ())

    assert dependencies.write_comment_block(config, report, dry_run=True) is True
    preview = capsys.readouterr().out
    assert preview.startswith(f"--- {config}\n+++ {config} (planned)\n")
    assert "@@" in preview and preview.endswith("\n")
    assert "\n+# END CMRU GENERATED DEPENDENCY GRAPH\n [orchestration]\n" in preview
    assert config.read_text(encoding="utf-8").startswith("schema_version = 1\n[orchestration]")

    assert dependencies.write_comment_block(config, report, dry_run=False) is True
    assert dependencies.write_comment_block(config, report, dry_run=True) is False
    assert capsys.readouterr().out == ""


def test_release_api_refuses_http_400_and_existing_target_creation(monkeypatch, tmp_path):
    gh = release.GitHubReleases(owner="owner", repo="repo", token=None)
    monkeypatch.setattr(gh, "_request", lambda *_args, **_kwargs: (400, "bad request"))
    with pytest.raises(SystemExit) as error:
        gh.get_release_by_tag("demo-v1.2.3")
    assert error.value.code == 1

    asset = tmp_path / "asset.whl"
    asset.write_bytes(b"wheel")

    class NoCreateClient:
        def get_release_by_tag(self, _tag):
            raise AssertionError("existing-target policy should reject before remote access")

    with pytest.raises(SystemExit) as recreate_error:
        release.publish_versioned(
            NoCreateClient(), prefix="demo", version="1.2.3", asset_path=asset,
            require_existing_targets=True, latest_pointer=True,
        )
    assert recreate_error.value.code == 1

    with pytest.raises(SystemExit) as target_error:
        release.publish_versioned(
            NoCreateClient(), prefix="demo", version="1.2.3", asset_path=asset,
            target_commitish="a" * 40, latest_pointer=False,
            require_existing_targets=True,
        )
    assert target_error.value.code == 1


def _install_config_loader(monkeypatch, tmp_path, names=("alpha", "beta"), *, owner="owner", repo="repo"):
    projects = {
        name: SimpleNamespace(
            name=name, cwd=name, project_root=tmp_path / name, prefix=f"{name}-v",
            git_tag=True, env={}, runner_steps={}, tool_dependencies=(),
        )
        for name in names
    }
    config_path = tmp_path / "cmru.orchestration.toml"
    loaded = (
        tmp_path, projects, list(projects), list(projects), [], "project-first", {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig(owner, repo, "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: config_path)
    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    return config_path


def _abandon_workspace(root, *, base_commit="a" * 40):
    branch = "cmru-release-20260927_120000-alpha-ab12cd"
    workspace = transaction.ReleaseWorkspace(
        repo_root=root, path=root / ".worktrees" / branch,
        branch=branch, base="a" * 40,
        context=SimpleNamespace(base_commit=base_commit),
    )
    return workspace


def _install_abandon_facts(monkeypatch, root, workspace, *, progress=None):
    monkeypatch.setattr(cli, "_current_git_root", lambda: root)
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: root / "cmru.toml")
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _root: [workspace])
    monkeypatch.setattr(transaction, "read_release_scope", lambda *_: ["alpha"])
    monkeypatch.setattr(transaction, "read_release_results", lambda *_: {})
    monkeypatch.setattr(
        transaction, "read_release_progress",
        lambda *_: progress if progress is not None else workspace.context.base_commit,
    )
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: False)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(
        cli, "load_config",
        lambda _path: (root, {"alpha": SimpleNamespace(git_tag=True)}, ["alpha"]),
    )


@pytest.mark.parametrize(
    ("entrypoint", "argv", "missing"),
    [
        (controller_cli.main, ["approve"], "--plan"),
        (controller_cli.main, ["hold"], "--plan"),
        (controller_cli.main, ["rollback"], "--plan"),
        (handlers.main, ["wheel-build", "--dry-run"], "--cwd"),
        (handlers.main, ["wheel-validate"], "--prefix"),
        (lambda argv: runner.runner_cli().run(argv=argv), ["alpha"], "--step"),
        (tester_gate.main, ["--", "true"], "--cwd"),
    ],
)
def test_registered_required_options_refuse_omission(capsys, entrypoint, argv, missing):
    assert entrypoint(argv) == 2
    captured = capsys.readouterr()
    diagnostic = captured.out + captured.err
    assert missing in diagnostic
    assert "required" in diagnostic.lower()


def test_handler_dry_run_hides_its_own_control_flag(capsys, tmp_path):
    assert handlers.main([
        "wheel-build", "--cwd", ".", "--dry-run",
    ]) == 0
    output = capsys.readouterr().out
    assert "Would run cmru handler wheel-build" in output
    assert "dry_run" not in output


def test_controller_rollback_accepts_the_minimum_positive_generation(caplog):
    from cmru.controller.planner import LandscapePlan, PlanStep
    from cmru.controller.rollout import RolloutEngine

    caplog.set_level("INFO")
    step = PlanStep(
        plan_id="plan", wave_name="canary", phase=1, wave_type="canary",
        nodes=["node-a"], profiles=["core"], release_tag="demo-v1",
        manifest_url="https://example.invalid/manifest.json", manifest_sha256="a" * 64,
        config_hash="cfg", step_id="plan.phase-1.canary", required=True,
        requires_approval=False,
    )
    engine = RolloutEngine(object(), "landscape", generation_base=3, dry_run=True)

    engine.rollback(LandscapePlan("plan", "landscape", [step]), generation=1)

    assert "action=rollback to 1" in caplog.text


def test_run_step_without_its_required_step_reports_usage(capsys):
    assert runner.runner_cli().run(argv=[]) == 2
    diagnostic = capsys.readouterr().err.lower()
    assert "usage:" in diagnostic
    assert "--step" in diagnostic


def test_init_help_marks_the_guided_command_interactive(capsys):
    assert scaffold.init_main(["--help"]) == 0
    assert "interactive" in capsys.readouterr().out.lower()


def test_init_without_arguments_starts_the_guided_flow(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scaffold, "_run_init", lambda args, _runtime: calls.append(args) or 0,
    )

    assert scaffold.init_main([]) == 0
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("entrypoint", "argv"),
    [
        (getpy.getpy_main, ["ghost"]),
        (getpy.getpy_main, ["alpha,beta", "--output", "installer.py"]),
        (resolve.resolve_main, ["ghost"]),
        (resolve.resolve_main, ["alpha"]),
        (lambda argv: runner.runner_cli().run(argv=argv), ["ghost", "--step", "build"]),
        (lambda argv: runner.runner_cli().run(argv=argv), ["alpha,beta", "--step", "build"]),
        (standards.standards_main, ["ghost"]),
        (standards.standards_main, ["alpha", "--dry-run"]),
        (tool_deps.tool_deps_main, ["--refresh", "alpha", "--json"]),
        (tool_deps.tool_deps_main, ["ghost"]),
    ],
)
def test_invalid_registered_invocations_render_usage(monkeypatch, tmp_path, capsys, entrypoint, argv):
    config_path = _install_config_loader(
        monkeypatch, tmp_path, owner="" if entrypoint is resolve.resolve_main and argv == ["alpha"] else "owner",
    )
    if entrypoint in {getpy.getpy_main, resolve.resolve_main, standards.standards_main, tool_deps.tool_deps_main}:
        argv = [*argv, "--config", str(config_path)] if "--config" not in argv else argv
    elif entrypoint is not None and getattr(entrypoint, "__name__", "") == "<lambda>":
        argv = [*argv, "--config", str(config_path)]

    assert entrypoint(argv) == 2
    captured = capsys.readouterr()
    diagnostic = captured.out + captured.err
    assert "usage:" in diagnostic.lower()
    assert "error" in diagnostic.lower()


def test_run_step_unknown_dry_run_step_reports_a_concise_error_without_usage(
    monkeypatch, tmp_path, capsys,
):
    config_path = _install_config_loader(monkeypatch, tmp_path)

    result = runner.runner_cli().run(argv=[
        "alpha", "--step", "missing", "--dry-run", "--config", str(config_path),
    ])

    diagnostic = capsys.readouterr().err
    assert result == 2
    assert "step 'missing' is not declared" in diagnostic
    assert "usage:" not in diagnostic.lower()


@pytest.mark.parametrize("base_commit", [None, "not-a-commit"])
def test_abandon_refuses_an_unverifiable_base_commit(
    monkeypatch, tmp_path, capsys, base_commit,
):
    workspace = _abandon_workspace(tmp_path, base_commit=base_commit)
    _install_abandon_facts(monkeypatch, tmp_path, workspace, progress="b" * 40)
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("remote inspection ran without a valid base commit"),
    )

    result = cli._abandon(
        SimpleNamespace(branch=workspace.branch, dry_run=True, yes=False),
        SimpleNamespace(confirm=lambda _prompt: pytest.fail("dry-run prompted")),
    )

    assert result == 2
    assert "original snapshot commit is unavailable" in capsys.readouterr().out


def test_abandon_preserves_remote_inspection_error_detail(monkeypatch, tmp_path, capsys):
    workspace = _abandon_workspace(tmp_path)
    _install_abandon_facts(monkeypatch, tmp_path, workspace)
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 2, "", "network unreachable",
        ),
    )

    result = cli._abandon(
        SimpleNamespace(branch=workspace.branch, dry_run=True, yes=False),
        SimpleNamespace(confirm=lambda _prompt: pytest.fail("dry-run prompted")),
    )

    assert result == 2
    assert "network unreachable" in capsys.readouterr().out


def test_abandon_preview_lists_the_remote_candidate_ref(monkeypatch, tmp_path, capsys):
    workspace = _abandon_workspace(tmp_path)
    _install_abandon_facts(monkeypatch, tmp_path, workspace)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: True)

    def run(argv, **_kwargs):
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(
                argv, 0, "a" * 40 + "\trefs/heads/" + workspace.branch + "\n", "",
            )
        if argv[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["git", "ls-remote", "--tags"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(cli.subprocess, "run", run)
    result = cli._abandon(
        SimpleNamespace(branch=workspace.branch, dry_run=True, yes=False),
        SimpleNamespace(confirm=lambda _prompt: pytest.fail("dry-run prompted")),
    )

    assert result == 0
    assert "origin/refs/heads/" + workspace.branch in capsys.readouterr().out


def test_cleanup_usage_error_is_a_usage_failure():
    from cli_extended import CliFailure

    with pytest.raises(CliFailure) as error:
        cli._usage_error("mutually incompatible cleanup modes")
    assert error.value.exit_code == 2
    assert error.value.show_help is True

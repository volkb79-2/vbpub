"""Tests for the cmru CLI: S2 config loading, token resolution (S2.4), verb dispatch.

Stdlib + tmp files only — no network, no git side effects.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from cmru import cli


def test_version_prefers_an_exact_source_tag_over_stale_install_metadata(monkeypatch):
    monkeypatch.setattr(cli, "_source_tree_version", lambda: "2.0.0")
    assert cli._cmru_version() == "2.0.0"


def test_source_dev_version_is_derived_from_the_nearest_cmru_tag():
    assert cli._dev_version_from_describe("cmru-v2.0.1-7-gabc123ef") == "2.0.2.dev7+gabc123ef"
    assert cli._dev_version_from_describe("cmru-v2.0.1-0-gabc123ef") == "2.0.1"
    assert cli._dev_version_from_describe("ciu-v6.0.0-7-gabc123ef") is None
    assert cli._dev_version_from_describe("cmru-v2.0.1-rc1-7-gabc123ef") is None


MINIMAL_S2 = """schema_version = 1
[orchestration]
project_order = ["alpha"]
default_projects = ["alpha"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"
[orchestration.project.alpha]
config = "alpha/cmru.toml"
depends_on = []
[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = ["alpha-latest"]
ghcr_packages = ["*"]
ghcr_delete_packages = []
"""

PROJECT = """schema_version = 1
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = ["ghcr.io"]
[project]
id = "alpha"
description = "alpha"
template_revision = 2
prefix = "alpha-v"
artifacts = ["wheel"]
scm_dist = "alpha"
[project.version]
strategy = "scm"
bump = "conventional"
paths = ["shared"]
[project.release]
git_tag = true
build_step = "build"
[steps.run-tests]
quiet = true
commands = [ { label = "test", argv = ["true"], cwd = "." } ]
[steps.build]
quiet = true
commands = [ { label = "build", argv = ["true"], cwd = "." } ]
[steps.push]
quiet = true
commands = [ { label = "push", argv = ["true"], cwd = "." } ]
"""


def _write(tmp_path: Path, body: str, name: str = "cmru.toml") -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def _valid_config(
    tmp_path: Path, *, project_append: str = "", release_append: str = "",
) -> Path:
    config = _write(tmp_path, MINIMAL_S2, name="cmru.orchestration.toml")
    project = tmp_path / "alpha" / "cmru.toml"
    project.parent.mkdir()
    project.write_text(
        PROJECT.replace("git_tag = true", f"git_tag = true{release_append}") + project_append,
        encoding="utf-8",
    )
    return config


def test_load_config_s2_schema(tmp_path):
    cfg = _valid_config(tmp_path)
    (repo_root, projects, project_order, default_projects, default_steps,
     execution_mode, step_project_order, cleanup, github, env_config) = cli.load_config(cfg)

    assert repo_root == tmp_path
    assert list(projects) == ["alpha"]
    assert project_order == ["alpha"]
    assert default_projects == ["alpha"]          # defaults to project_order
    assert default_steps == ["run-tests", "build", "push"]
    assert execution_mode == "project-first"
    assert github.owner == "octocat" and github.repo == "demo"
    assert github.owner_type == "user"
    assert env_config.registry_url == "ghcr.io"   # from [targets].registry

    alpha = projects["alpha"]
    assert alpha.prefix == "alpha-v"
    assert alpha.artifacts == ("wheel",)
    assert alpha.version.strategy == "scm"
    # change-detection watches cwd plus extra version.paths (S12.3)
    assert alpha.paths == ["alpha", "alpha/shared"]
    assert alpha.changelog == "CHANGES.md"
    assert set(alpha.steps) == {"run-tests", "build", "push"}


def test_orchestration_env_policy_is_merged_before_a_project_override(tmp_path):
    cfg = _valid_config(
        tmp_path,
        project_append='\n[env]\nCMRU_TESTER_CPUS = "2"\n',
    )
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            'execution_mode = "project-first"\n',
            'execution_mode = "project-first"\n'
            '[orchestration.defaults.env]\n'
            'CMRU_TESTER_UNIFIED_IMAGE = "tester-unified:policy"\n'
            'CMRU_TESTER_CPUS = "1.5"\n',
        ),
        encoding="utf-8",
    )

    (_repo, projects, _order, _defaults, _steps, _mode, _step_order,
     _cleanup, _github, env) = cli.load_config(cfg)

    assert env.env["CMRU_TESTER_UNIFIED_IMAGE"] == "tester-unified:policy"
    assert projects["alpha"].env["CMRU_TESTER_UNIFIED_IMAGE"] == "tester-unified:policy"
    assert projects["alpha"].env["CMRU_TESTER_CPUS"] == "2"


def test_checked_in_sample_uses_automatic_release_history(tmp_path):
    sample = Path(__file__).resolve().parents[2] / "cmru.project.sample.toml"
    config = tmp_path / "cmru.toml"
    config.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    _, projects, *_rest = cli.load_config(config)

    assert projects["example-wheel"].changelog == "CHANGES.md"


def test_release_history_defaults_to_project_changes_file(tmp_path):
    cfg = _valid_config(tmp_path)
    _, projects, *_rest = cli.load_config(cfg)

    assert projects["alpha"].changelog == "CHANGES.md"


def test_release_history_opt_out_must_be_explicit(tmp_path):
    cfg = _valid_config(tmp_path, release_append="\nchangelog = false")
    _, projects, *_rest = cli.load_config(cfg)

    assert projects["alpha"].changelog is None


def test_changelog_backfill_dispatches_to_the_migration_helper(tmp_path, monkeypatch):
    config = _valid_config(tmp_path)
    project = SimpleNamespace(name="alpha", changelog="CHANGES.md")
    calls: list[tuple] = []
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda _path: (tmp_path, {"alpha": project}),
    )
    import cmru.changelog
    monkeypatch.setattr(
        cmru.changelog,
        "backfill_release_changelog",
        lambda root, configured_project, tag: calls.append((root, configured_project, tag)) or True,
    )

    cli.main([
        "changelog", "--config", str(config), "--project", "alpha", "--backfill-tag", "alpha-v1.0.0",
    ])

    assert calls == [(tmp_path, project, "alpha-v1.0.0")]


def test_load_config_rejects_retired_config_keys(tmp_path):
    """There is one strict grammar; retired keys must not be silently reinterpreted."""
    legacy = """
repo_root = "."
[github]
username = "octocat"
repo = "demo"
owner_type = "user"
[registry]
url = "ghcr.io"
[orchestration]
project_order = ["a"]
default_projects = ["a"]
default_steps = ["build"]
execution_mode = "project-first"
[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = ["a-latest"]
ghcr_packages = ["*"]
[projects.a]
prefix = "a-v"
[projects.a.steps.build]
commands = [ { label = "b", argv = ["true"], cwd = "a" } ]
"""
    cfg = _write(tmp_path, legacy, name="release.toml")
    with pytest.raises(SystemExit) as exc:
        cli.load_config(cfg)
    assert exc.value.code == 2


def test_token_resolution_order(tmp_path, monkeypatch):
    cfg_path = _valid_config(tmp_path)

    # No configured token is accepted: an absent explicit secret source remains absent.
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.load_config(cfg_path)[8].token == ""

    # The repository-root secret document supplies the credential for the estate.
    (tmp_path / "cmru.secret.toml").write_text('[github]\ntoken = "from-root"\n')
    _root, projects, *_rest = cli.load_config(cfg_path)
    assert _rest[-2].token == "from-root"
    assert projects["alpha"].github_token == "from-root"

    # A project-local secret travels with the project and deep-merges over the
    # repository default only for operations on that project.
    (tmp_path / "alpha" / "cmru.secret.toml").write_text(
        '[github]\ntoken = "from-project"\n'
    )
    _root, projects, *_rest = cli.load_config(cfg_path)
    assert _rest[-2].token == "from-root"
    assert projects["alpha"].github_token == "from-project"

    # Environment is the deliberate invocation-scoped override for all projects.
    monkeypatch.setenv("GITHUB_PUSH_PAT", "from-env")
    assert cli.load_config(cfg_path)[8].token == "from-env"
    _root, projects, *_rest = cli.load_config(cfg_path)
    assert projects["alpha"].github_token == "from-env"


def test_secret_document_rejects_legacy_root_project_override_table(tmp_path):
    cfg_path = _valid_config(tmp_path)
    (tmp_path / "cmru.secret.toml").write_text(
        '[github]\ntoken = "from-root"\n[project.alpha.github]\ntoken = "from-project"\n'
    )

    with pytest.raises(SystemExit) as exc:
        cli.load_config(cfg_path)

    assert exc.value.code == 2


def test_secret_document_rejects_a_non_file_path(tmp_path):
    cfg_path = _valid_config(tmp_path)
    (tmp_path / "cmru.secret.toml").mkdir()

    with pytest.raises(SystemExit) as exc:
        cli.load_config(cfg_path)

    assert exc.value.code == 2


def test_apply_project_release_env_uses_the_selected_project_environment(monkeypatch):
    github = cli.GitHubConfig("owner", "repo", "root-token", "user")
    env = cli.ReleaseEnvConfig({"GLOBAL": "root"}, None)
    project = cli.ProjectConfig(
        name="alpha",
        env={"CMRU_TESTER_UNIFIED_IMAGE": "tester-unified@sha256:test"},
        steps={},
        github_token="project-token",
    )
    monkeypatch.delenv("CMRU_TESTER_UNIFIED_IMAGE", raising=False)
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)

    cli.apply_project_release_env(github, env, project)

    assert os.environ["GITHUB_PUSH_PAT"] == "project-token"
    assert os.environ["CMRU_TESTER_UNIFIED_IMAGE"] == "tester-unified@sha256:test"
    assert os.environ["GLOBAL"] == "root"


def test_apply_project_release_env_clears_a_previous_projects_credential(monkeypatch):
    """A selected project without a credential must not inherit its predecessor's.

    This is a transaction-boundary check, not merely cosmetic environment
    cleanup: a later test/build command could otherwise receive credentials it
    neither declared nor resolved for itself.
    """
    github = cli.GitHubConfig("owner", "repo", "root-token", "user")
    env = cli.ReleaseEnvConfig({}, None)
    project = cli.ProjectConfig(name="uncredentialed", env={}, steps={}, github_token="")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "previous-project-token")
    monkeypatch.setenv("GITHUB_TOKEN", "previous-project-token")

    cli.apply_project_release_env(github, env, project)

    assert "GITHUB_PUSH_PAT" not in os.environ
    assert "GITHUB_TOKEN" not in os.environ


def test_committed_github_token_is_rejected(tmp_path):
    cfg_path = _valid_config(tmp_path)
    project_path = tmp_path / "alpha" / "cmru.toml"
    project_path.write_text(
        PROJECT.replace('owner_type = "user"', 'owner_type = "user"\ntoken = "forbidden"'),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        cli.load_config(cfg_path)

    assert exc.value.code == 2


def test_help_lists_verbs_and_ordering():
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["--help"])
    text = out.getvalue()
    for verb in ("status", "release", "changelog", "build", "worktrees", "publish", "resolve", "get", "cleanup", "version", "run-step", "tool-deps"):
        assert verb in text, f"{verb} missing from help"
    assert "TYPICAL WORKFLOW" in text


def test_help_lists_every_public_option():
    text = cli.usage()
    for option in (
        "--config", "--project", "--minor", "--major", "--set-version", "--dry-run",
        "--no-build", "--resume", "--abandon", "--allow-uncommitted",
        "--show-run-details", "--log-append", "--retain-logs-on-release",
        "--retain-artifacts-on-release", "--backfill-tag", "--update", "--json",
        "--run-tests", "--build", "--push", "--validate", "--remove-assets",
        "--format", "--prefix", "--output", "--delete-unmanaged-release-tag",
        "--delete-build-output", "--discard-build-worktree", "--yes", "--step", "--write",
        "--log-prefix-time-short", "--help",
        "--allow-stale-tool-deps", "--refresh", "--timeout",
    ):
        assert option in text, f"{option} missing from usage()"


def test_default_config_prefers_project_then_current_directory_orchestration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    orchestration = tmp_path / "cmru.orchestration.toml"
    orchestration.write_text("", encoding="utf-8")
    assert cli._default_config_path() == orchestration

    project = tmp_path / "cmru.toml"
    project.write_text("", encoding="utf-8")
    assert cli._default_config_path() == project


def test_default_config_never_searches_a_parent_directory(tmp_path, monkeypatch):
    (tmp_path / "cmru.orchestration.toml").write_text("", encoding="utf-8")
    child = tmp_path / "project"
    child.mkdir()
    monkeypatch.chdir(child)
    assert cli._default_config_path() == child / "cmru.toml"


def test_status_uses_current_directory_orchestration_without_a_shim(tmp_path, monkeypatch):
    _valid_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []

    from cmru import version
    monkeypatch.setattr(
        version,
        "status_cmd",
        lambda root, projects, **kwargs: calls.append((root, list(projects), kwargs)),
    )

    cli.main(["status", "--project", "alpha"])

    assert calls == [(tmp_path, ["alpha"], {"minor": False, "major": False, "set_version": None})]


def test_unknown_verb_exits_2():
    with pytest.raises(SystemExit) as exc:
        cli.main(["frobnicate"])
    assert exc.value.code == 2


def test_version_is_a_verb_not_a_flag(monkeypatch):
    monkeypatch.setattr(cli, "_cmru_version", lambda: "2.0.2")
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["version"])
    assert out.getvalue() == "cmru 2.0.2\n"

    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 2


def test_worktrees_is_config_free_read_only_discovery(tmp_path, monkeypatch):
    workspace = SimpleNamespace(
        branch="cmru/build/debug", path=tmp_path / "retained-build", base="a" * 40,
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n"),
    )
    monkeypatch.setattr(cli.transaction, "list_cmru_workspaces", lambda root: [workspace])

    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["worktrees", "--json"])

    assert json.loads(out.getvalue()) == [{
        "branch": "cmru/build/debug",
        "path": str(workspace.path),
        "purpose": "build",
        "source_commit": "a" * 40,
        "visible": False,
    }]


def test_worktrees_recovery_advice_includes_the_repository_config(tmp_path, monkeypatch):
    config = tmp_path / "cmru.orchestration.toml"
    config.write_text("", encoding="utf-8")
    workspace_path = tmp_path / "retained-build"
    workspace_path.mkdir()
    workspace = SimpleNamespace(
        branch="cmru/build/debug", path=workspace_path, base="a" * 40,
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n"),
    )
    monkeypatch.setattr(cli.transaction, "list_cmru_workspaces", lambda root: [workspace])

    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["worktrees"])

    text = out.getvalue()
    assert f"cmru cleanup --config {config}" in text
    assert f"--discard-build-worktree {workspace_path} --yes" in text


def test_cleanup_uses_current_directory_orchestration_without_a_shim(tmp_path, monkeypatch):
    _valid_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "retained-build"
    calls = []
    monkeypatch.setattr(
        cli.transaction,
        "discard_build_workspace",
        lambda root, path, *, dry_run: calls.append((root, path, dry_run))
        or SimpleNamespace(path=path, branch="cmru/build/debug"),
    )

    cli.main(["cleanup", "--discard-build-worktree", str(target), "--yes"])

    assert calls == [(tmp_path, target, False)]


def test_source_module_invocation_works_from_the_cmru_project_directory():
    project_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            os.environ.get("PYTHON", "python3"), "-m", "cmru.handlers", "--help",
        ],
        cwd=project_dir,
        env={**os.environ, "PYTHONPATH": str(project_dir / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "wheel-build" in result.stdout


def test_fresh_checkout_bootstrap_is_the_only_source_build_launcher():
    repo_root = Path(__file__).resolve().parents[2]
    bootstrap = repo_root / "cmru" / "build-initial-standalone.sh"

    assert not (repo_root / "cmru.py").exists()
    assert bootstrap.is_file()
    assert os.access(bootstrap, os.X_OK)
    source = bootstrap.read_text(encoding="utf-8")
    assert "python3 -m cmru.handlers" not in source
    assert "-m cmru.handlers wheel-build" in source
    assert "CMRU_DOCKER_CGROUP_PARENT" in source
    assert 'CMRU_WHEEL_BUILDER_IMAGE:-wheel-builder:local' not in source
    assert 'CMRU_WHEEL_BUILDER_IMAGE' in source


def test_cleanup_delete_unmanaged_release_requires_confirmation(tmp_path):
    cfg_path = _valid_config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.main([
            "cleanup", "--config", str(cfg_path), "--project", "alpha",
            "--delete-unmanaged-release-tag", "alpha-wheel-latest",
        ])
    assert exc.value.code == 2


def test_cleanup_delete_unmanaged_release_is_project_scoped_and_dry_runnable(tmp_path, monkeypatch):
    cfg_path = _valid_config(tmp_path)
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    (tmp_path / "cmru.secret.toml").write_text(
        '[github]\ntoken = "test-token"\n', encoding="utf-8"
    )
    calls = []
    monkeypatch.setattr(
        cli, "delete_unmanaged_release_tag",
        lambda owner, repo, token, tag, *, dry_run: calls.append(
            (owner, repo, token, tag, dry_run)
        ) or True,
    )

    cli.main([
        "cleanup", "--config", str(cfg_path), "--project", "alpha",
        "--delete-unmanaged-release-tag", "alpha-wheel-latest", "--dry-run",
    ])
    assert calls == [("octocat", "demo", "test-token", "alpha-wheel-latest", True)]


def test_cleanup_delete_unmanaged_release_rejects_a_managed_tag(tmp_path):
    cfg_path = _valid_config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.main([
            "cleanup", "--config", str(cfg_path), "--project", "alpha",
            "--delete-unmanaged-release-tag", "alpha-v1.0.0", "--dry-run",
        ])
    assert exc.value.code == 2


def test_cleanup_delete_build_output_is_project_scoped_and_dry_runnable(tmp_path, monkeypatch):
    cfg_path = _valid_config(tmp_path)
    expected_id = f"19700101T000000Z_{'a' * 40}"
    calls = []
    monkeypatch.setattr(
        cli.transaction,
        "delete_retained_build_output",
        lambda root, project, name, output_id, *, dry_run: calls.append(
            (root, project, name, output_id, dry_run)
        ) or [tmp_path / "alpha" / "logs" / output_id, tmp_path / "alpha" / "artifacts" / output_id],
    )

    cli.main([
        "cleanup", "--config", str(cfg_path), "--project", "alpha",
        "--delete-build-output", expected_id, "--dry-run",
    ])

    assert len(calls) == 1
    assert calls[0][0] == tmp_path
    assert calls[0][2:] == ("alpha", expected_id, True)


def test_invalid_config_missing_github(tmp_path):
    bad = """
[orchestration]
project_order = ["a"]
[project.a]
prefix = "a-v"
[project.a.steps.build]
commands = [ { label = "b", argv = ["true"], cwd = "a" } ]
"""
    cfg = _write(tmp_path, bad)
    with pytest.raises(SystemExit) as exc:
        cli.load_config(cfg)
    assert exc.value.code == 2

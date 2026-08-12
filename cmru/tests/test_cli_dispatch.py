"""Tests for the cmru CLI: S2 config loading, token resolution (S2.4), verb dispatch.

Stdlib + tmp files only — no network, no git side effects.
"""
from __future__ import annotations

import io
import subprocess
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from cmru import cli


MINIMAL_S2 = """schema_version = 1
[orchestration]
project_order = ["alpha"]
default_projects = ["alpha"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"
auth_project = "alpha"
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

    # The project-local cmru.secret.toml overlay is the on-disk explicit source.
    (tmp_path / "alpha" / "cmru.secret.toml").write_text('[github]\ntoken = "from-secret"\n')
    assert cli.load_config(cfg_path)[8].token == "from-secret"

    # Environment is the deliberate invocation-scoped override.
    monkeypatch.setenv("GITHUB_PUSH_PAT", "from-env")
    assert cli.load_config(cfg_path)[8].token == "from-env"


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
    for verb in ("status", "release", "changelog", "build", "publish", "resolve", "get", "cleanup", "run-step"):
        assert verb in text, f"{verb} missing from help"
    assert "TYPICAL WORKFLOW" in text


def test_unknown_verb_exits_2():
    with pytest.raises(SystemExit) as exc:
        cli.main(["frobnicate"])
    assert exc.value.code == 2


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

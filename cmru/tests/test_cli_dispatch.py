"""Tests for the cmru CLI: S2 config loading, token resolution (S2.4), verb dispatch.

Stdlib + tmp files only — no network, no git side effects.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from cmru import cli


MINIMAL_S2 = """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"

[targets]
host = "github"
registry = ["ghcr.io"]

[orchestration]
project_order = ["alpha"]
default_steps = ["build", "push"]
execution_mode = "project-first"

[cleanup]
keep_release_tags = ["alpha-latest"]

[project.alpha]
prefix = "alpha-v"
artifact = "wheel"
scm_dist = "alpha"
cwd = "alpha"
[project.alpha.version]
strategy = "scm"
bump = "conventional"
paths = ["shared"]
[project.alpha.steps.build]
commands = [ { label = "build", argv = ["true"], cwd = "alpha" } ]
[project.alpha.steps.push]
commands = [ { label = "push", argv = ["true"], cwd = "alpha" } ]
"""


def _write(tmp_path: Path, body: str, name: str = "cmru.toml") -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def test_load_config_s2_schema(tmp_path):
    cfg = _write(tmp_path, MINIMAL_S2)
    (repo_root, projects, project_order, default_projects, default_steps,
     execution_mode, step_project_order, cleanup, github, env_config) = cli.load_config(cfg)

    assert repo_root == tmp_path
    assert list(projects) == ["alpha"]
    assert project_order == ["alpha"]
    assert default_projects == ["alpha"]          # defaults to project_order
    assert default_steps == ["build", "push"]
    assert execution_mode == "project-first"
    assert github.username == "octocat" and github.repo == "demo"
    assert github.owner_type == "user"
    assert env_config.registry_url == "ghcr.io"   # from [targets].registry

    alpha = projects["alpha"]
    assert alpha.prefix == "alpha-v"
    assert alpha.artifact == "wheel"
    assert alpha.version.strategy == "scm"
    # change-detection watches cwd plus extra version.paths (S12.3)
    assert alpha.paths == ["alpha", "shared"]
    assert set(alpha.steps) == {"build", "push"}


def test_load_config_legacy_keys_still_accepted(tmp_path):
    """One-release back-compat: [projects] plural + github.username + [registry].url."""
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
    _, projects, _, _, _, _, _, _, github, env_config = cli.load_config(cfg)
    assert list(projects) == ["a"]
    assert github.username == "octocat"
    assert env_config.registry_url == "ghcr.io"


def test_token_resolution_order(tmp_path, monkeypatch):
    cfg_dir = tmp_path
    (cfg_dir / "cmru.toml").write_text(MINIMAL_S2)
    config = {"github": {"token": "from-config"}}
    cfg_path = cfg_dir / "cmru.toml"

    # 3) config value when nothing else present
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli._resolve_token(config, cfg_path) == "from-config"

    # 2) cmru.secret.toml overlay beats config
    (cfg_dir / "cmru.secret.toml").write_text('[github]\ntoken = "from-secret"\n')
    assert cli._resolve_token(config, cfg_path) == "from-secret"

    # 1) env beats everything
    monkeypatch.setenv("GITHUB_PUSH_PAT", "from-env")
    assert cli._resolve_token(config, cfg_path) == "from-env"


def test_help_lists_verbs_and_ordering():
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["--help"])
    text = out.getvalue()
    for verb in ("status", "release", "build", "publish", "resolve", "get", "cleanup", "run-step"):
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
    with pytest.raises(ValueError):
        cli.load_config(cfg)

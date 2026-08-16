"""Direct behavioral witnesses for the final config/version branches."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import config, version


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid"); git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "source.py").write_text("x=1\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def project_doc() -> str:
    return '''schema_version = 1
[github]
owner="acme"
repo="vbpub"
owner_type="org"
[targets]
host="github"
registry=[]
[project]
id="demo"
description="demo"
prefix="demo-v"
artifacts=["wheel"]
[project.version]
strategy="scm"
bump="patch"
[project.release]
git_tag=true
build_step="build"
artifact_dirs=["dist"]
[steps.run-tests]
quiet=true
commands=[{label="tests",argv=["echo"],cwd="."}]
[steps.build]
quiet=true
commands=[{label="build",argv=["echo"],cwd="."}]
[steps.push]
quiet=true
commands=[{label="push",argv=["echo"],cwd="."}]
'''


def orch_doc(entry='config="demo/cmru.toml"', order='["demo"]') -> str:
    return f'''schema_version=1
[orchestration]
project_order={order}
default_projects=["demo"]
default_steps=["run-tests","build","push"]
execution_mode="project-first"
[orchestration.project.demo]
{entry}
[cleanup]
release_tag_prefixes=[]
keep_release_tags=[]
ghcr_packages=[]
ghcr_delete_packages=[]
'''


def test_config_scalar_secret_runner_and_cleanup_errors_name_the_policy(tmp_path, capsys):
    scalar = tmp_path / "cmru.toml"; scalar.write_text("'scalar'\n")
    with patch.object(config.tomllib, "load", return_value="scalar"):
        with pytest.raises(SystemExit):
            config._read_toml(scalar, "cmru.toml")
    with pytest.raises(SystemExit):
        config._secret_token({"token": ""}, "github")
    for raw in (
        {"": {}},
        {"build": {"commands": ["bad"], "quiet": True}},
        {"build": {"commands": [{"label": "", "argv": ["echo"], "cwd": "."}], "quiet": True}},
        {"build": {"commands": [{"label": "x", "argv": ["echo"], "cwd": "."}], "quiet": True, "bake_set_prefix": ""}},
    ):
        with pytest.raises(SystemExit):
            config._validate_runner_steps(raw)
    with pytest.raises(SystemExit):
        config._parse_cleanup(None)
    assert "ERROR" in capsys.readouterr().out


def test_config_project_metadata_and_orchestration_resolution_errors(tmp_path, capsys):
    path = tmp_path / "cmru.toml"
    for raw, text in (
        (project_doc().replace("[project]\n", "[project]\nextra=[]\n"), "unknown keys"),
        (project_doc().replace("[project]\n", "[project]\n") .replace("[steps.run-tests]", "[steps.run-tests]\nlogin=[]"), "login"),
    ):
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)
        assert text in capsys.readouterr().out

    project = tmp_path / "demo"; project.mkdir(); (project / "cmru.toml").write_text(project_doc())
    orch = tmp_path / "cmru.orchestration.toml"
    for raw in (
        orch_doc(entry="config=\"demo/cmru.toml\"\ndepends_on=[]\n", order='["demo","demo"]'),
        orch_doc(entry="config=\"demo/cmru.toml\"\ndepends_on=[\"demo\"]"),
        orch_doc(entry="config=\"demo/cmru.toml\"\ndepends_on=[\"missing\"]"),
    ):
        orch.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(orch)


def _project(strategy="scm", git_tag=True, prefix="demo-v", cwd="demo"):
    return SimpleNamespace(strategy=strategy, git_tag=git_tag, prefix=prefix, cwd=cwd, version=SimpleNamespace(strategy=strategy, base_version="1.0.0"))


def test_version_status_last_tag_and_release_bump_paths(tmp_path, capsys):
    project = _project()
    with patch.object(version, "detect_changed_projects", return_value=[]):
        version.status_cmd(tmp_path, {"demo": project})
    assert "No projects" in capsys.readouterr().out
    with patch.object(version, "detect_changed_projects", return_value=[("demo", project, "demo-v1.2.3", "patch")]):
        version.status_cmd(tmp_path, {"demo": project})
    assert "demo-v1.2.4" in capsys.readouterr().out

    root = repo(tmp_path)
    with patch.object(version, "detect_changed_projects", return_value=[("demo", project, "demo-v1.2.3", "patch")]), patch.object(version, "_apply_strategy_scm", return_value="demo-v1.3.0") as apply:
        assert version.release_cmd(root, {"demo": project}, minor=True, dry_run=True) == ["demo-v1.3.0"]
    apply.assert_called_once_with(root, "demo-v", "1.3.0", dry_run=True)


def test_version_counter_failure_and_release_unknown_strategy_refuse(tmp_path):
    project = _project(strategy="invented")
    root = repo(tmp_path)
    with patch.object(version, "detect_changed_projects", return_value=[("demo", project, None, "patch")]):
        with pytest.raises(SystemExit) as error:
            version.release_cmd(root, {"demo": project})
        assert error.value.code == 2
    with patch.object(version.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout=""), SimpleNamespace(returncode=3)]):
        with pytest.raises(SystemExit) as error:
            version._apply_strategy_counter(root, "demo-v", "1.0.0")
        assert error.value.code == 1

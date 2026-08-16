"""Behavioural coverage for the remaining strict config and CLI boundaries."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, config


def _project_toml(name: str = "demo", *, owner: str = "acme", registry: str = "ghcr.io") -> str:
    return f'''schema_version = 1

[github]
owner = "{owner}"
repo = "vbpub"
owner_type = "org"

[targets]
host = "github"
registry = ["{registry}"]

[project]
id = "{name}"
description = "A test project"
prefix = "{name}-v"
artifacts = ["wheel"]
scm_dist = "{name}"

[project.version]
strategy = "scm"
bump = "patch"
paths = ["src"]

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]
commit_generated = ["VERSION"]

[steps.run-tests]
quiet = true
commands = [{{label = "tests", argv = ["pytest", "-q"], cwd = "."}}]

[steps.build]
quiet = true
commands = [{{label = "build", argv = ["python", "-m", "build"], cwd = "."}}]

[steps.push]
quiet = true
commands = [{{label = "push", argv = ["echo", "push"], cwd = "."}}]
'''


def _orchestration_toml(*, project_path: str = "demo/cmru.toml", mode: str = "project-first",
                       defaults: str = 'defaults = { env = { CI = "estate" } }', project_order: str = '["demo"]',
                       depends: str = "") -> str:
    return f'''schema_version = 1

[orchestration]
project_order = {project_order}
default_projects = ["demo"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "{mode}"
{defaults}

[orchestration.project.demo]
config = "{project_path}"
{depends}

[cleanup]
release_tag_prefixes = ["demo-v"]
keep_release_tags = ["demo-latest"]
ghcr_packages = ["demo"]
ghcr_delete_packages = []
'''


def _write_project(tmp_path: Path, name: str = "demo", **kwargs) -> Path:
    root = tmp_path / name
    root.mkdir()
    path = root / "cmru.toml"
    path.write_text(_project_toml(name, **kwargs), encoding="utf-8")
    return path


def test_duration_and_command_boundaries_have_distinct_results(tmp_path):
    assert cli.parse_duration("1h 30m") == cli.timedelta(hours=1, minutes=30)
    with pytest.raises(ValueError, match="positive"):
        cli.parse_duration("0s")
    commands = cli.parse_commands(tmp_path / "cmru.toml", tmp_path, "build", [
        {"label": "ok", "argv": ["echo", "ok"], "cwd": "."},
    ])
    assert commands[0].cwd == tmp_path.resolve()
    with pytest.raises(ValueError, match="must define at least one"):
        cli.parse_commands(tmp_path / "cmru.toml", tmp_path, "build", [])
    with pytest.raises(ValueError, match="missing label"):
        cli.parse_commands(tmp_path / "cmru.toml", tmp_path, "build", [{"argv": ["echo"], "cwd": "."}])


def test_load_json_preserves_headers_and_refuses_http_or_json_errors(monkeypatch):
    monkeypatch.setattr(cli, "http_request", lambda *args: (200, '{"items": [1]}', {"X": "y"}))
    assert cli.load_json("https://example.test", "token") == ({"items": [1]}, {"X": "y"})
    monkeypatch.setattr(cli, "http_request", lambda *args: (404, "missing", {}))
    with pytest.raises(RuntimeError, match="404"):
        cli.load_json("https://example.test", "token")
    monkeypatch.setattr(cli, "http_request", lambda *args: (200, "not-json", {}))
    with pytest.raises(json.JSONDecodeError):
        cli.load_json("https://example.test", "token")
    monkeypatch.setattr(cli, "http_request", lambda *args: (200, "  ", {"X": "z"}))
    assert cli.load_json("https://example.test", "token") == ([], {"X": "z"})


def test_project_config_loads_effective_contract_and_environment(tmp_path, monkeypatch):
    path = _write_project(tmp_path)
    (tmp_path / "demo" / "cmru.secret.toml").write_text('[github]\ntoken = "project-token"\n', encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)
    forge = config.load_forge_config(path)
    assert forge.projects["demo"].build_step == "build"
    loaded = cli.load_config(path)
    assert loaded[1]["demo"].github_token == "project-token"
    assert loaded[8].owner == "acme" and loaded[9].registry_url == "ghcr.io"


def test_environment_token_is_invocation_authority_over_secret_files(tmp_path, monkeypatch):
    path = _write_project(tmp_path)
    (tmp_path / "cmru.secret.toml").write_text('[github]\ntoken = "file-token"\n', encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "environment-token")
    forge = config.load_forge_config(path)
    assert forge.github.token == "environment-token"
    assert forge.project_tokens["demo"] == "environment-token"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p.replace('schema_version = 1', 'schema_version = 2'), "schema_version"),
        (lambda p: p.replace('artifacts = ["wheel"]', 'artifacts = ["unknown"]'), "unknown"),
        (lambda p: p.replace('quiet = true', 'quiet = "yes"'), "quiet"),
        (lambda p: p.replace('build_step = "build"', 'build_step = "missing"'), "declared"),
    ],
)
def test_project_document_rejects_invalid_contracts(tmp_path, mutate, expected, capsys):
    path = _write_project(tmp_path)
    path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        config.load_forge_config(path)
    assert exc.value.code == 2
    assert expected in capsys.readouterr().out


def test_orchestration_defaults_override_project_environment_and_dependencies(tmp_path):
    project = _write_project(tmp_path)
    project.write_text(project.read_text(encoding="utf-8").replace(
        '[targets]\nhost = "github"\nregistry = ["ghcr.io"]',
        '[targets]\nhost = "github"\nregistry = ["ghcr.io"]\n\n[env]\nCI = "project"\nPROJECT_ONLY = "yes"',
    ), encoding="utf-8")
    orch = tmp_path / "cmru.orchestration.toml"
    orch.write_text(_orchestration_toml(), encoding="utf-8")
    forge = config.load_forge_config(orch, require_orchestration=True)
    assert forge.projects["demo"].env == {"CI": "project", "PROJECT_ONLY": "yes"}
    assert forge.env == {"CI": "estate"}
    assert forge.orchestration.execution_mode == "project-first"


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda s: s.replace('config = "demo/cmru.toml"', 'config = "../cmru.toml"'), "project-relative"),
        (lambda s: s.replace('execution_mode = "project-first"', 'execution_mode = "bad"'), "execution_mode"),
        (lambda s: s.replace('project_order = ["demo"]', 'project_order = ["missing"]'), "unknown project"),
        (lambda s: s.replace('config = "demo/cmru.toml"', 'config = "other/cmru.toml"'), "not found"),
    ],
)
def test_orchestration_refuses_ambiguous_or_missing_project_facts(tmp_path, change, expected, capsys):
    _write_project(tmp_path)
    path = tmp_path / "cmru.orchestration.toml"
    path.write_text(change(_orchestration_toml()), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        config.load_forge_config(path, require_orchestration=True)
    assert exc.value.code == 2
    assert expected in capsys.readouterr().out


def test_orchestration_rejects_dependency_order_and_target_mismatch(tmp_path, capsys):
    _write_project(tmp_path, "demo")
    second = _write_project(tmp_path, "other", owner="other")
    orch = tmp_path / "cmru.orchestration.toml"
    orch.write_text(_orchestration_toml(project_path="demo/cmru.toml")
                    .replace('[orchestration.project.demo]', '[orchestration.project.demo]\ndepends_on = ["other"]')
                    .replace('project_order = ["demo"]', 'project_order = ["demo", "other"]'), encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load_forge_config(orch)
    assert "unknown project" in capsys.readouterr().out
    # Both documents are declared below: the same release repository is a contract fact.
    second.write_text(_project_toml("other", owner="different"), encoding="utf-8")
    orch.write_text(_orchestration_toml(project_path="demo/cmru.toml", project_order='["demo", "other"]')
                    .replace('default_projects = ["demo"]', 'default_projects = ["demo", "other"]')
                    + '\n[orchestration.project.other]\nconfig = "other/cmru.toml"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load_forge_config(orch)
    assert "GitHub release repository" in capsys.readouterr().out


def test_cli_release_policy_and_project_environment_are_explicit():
    spec = cli._parse_version_spec({"strategy": "none", "bump": "patch"}, "demo")
    with pytest.raises(ValueError, match="git_tag=false"):
        cli._parse_release_policy({"artifacts": ["wheel"], "release": {"git_tag": True}}, "demo", spec)
    artifacts, git_tag, generated = cli._parse_release_policy(
        {"artifacts": ["wheel", "bundle"], "release": {"git_tag": False, "commit_generated": ["VERSION"]}},
        "demo", spec,
    )
    assert artifacts == ("wheel", "bundle") and git_tag is False and generated == ("VERSION",)


def test_release_environment_clears_stale_credentials_and_merges_project_values(monkeypatch):
    github = cli.GitHubConfig("owner", "repo", "new-token", "org")
    env = cli.ReleaseEnvConfig({"MODE": "estate"}, "registry")
    project = SimpleNamespace(name="demo", github_token="project-token", env={"MODE": "project", "EMPTY": ""})
    monkeypatch.setenv("GITHUB_TOKEN", "stale")
    cli.apply_project_release_env(github, env, project)
    assert "GITHUB_TOKEN" not in __import__("os").environ
    assert __import__("os").environ["GITHUB_PUSH_PAT"] == "project-token"
    assert __import__("os").environ["MODE"] == "project"
    assert __import__("os").environ["EMPTY"] == ""


@pytest.mark.parametrize(
    "call, needle",
    [
        (lambda: config._parse_version({"strategy": "scm", "bump": ""}, "demo"), "bump"),
        (lambda: config._parse_version({"strategy": "scm", "bump": "patch", "paths": "src"}, "demo"), "paths"),
        (lambda: config._parse_artifacts("demo", {"artifacts": "wheel"}), "artifacts"),
        (lambda: config._parse_artifacts("demo", {"artifacts": []}), "must not be empty"),
        (lambda: config._parse_variants("demo", {"variants": "x"}), "array"),
        (lambda: config._parse_variants("demo", {"variants": [{"name": "bad/name"}]}), "invalid"),
        (lambda: config._github({"owner": "o", "repo": "r"}), "owner_type"),
        (lambda: config._github({"owner": "o", "repo": "", "owner_type": "org"}), "non-empty"),
        (lambda: config._secret_token("token", "secret"), "table"),
        (lambda: config._targets({"host": "github"}), "registry"),
        (lambda: config._validate_runner_steps(None), "steps"),
        (lambda: config._validate_runner_steps({"build": {"commands": [], "quiet": True}}), "commands"),
    ],
)
def test_schema_helpers_refuse_wrong_shapes_without_inventing_values(call, needle, capsys):
    with pytest.raises(SystemExit) as exc:
        call()
    assert exc.value.code == 2
    assert needle in capsys.readouterr().out


def test_secret_document_and_project_release_path_guards(tmp_path, capsys):
    malformed = tmp_path / "cmru.secret.toml"
    malformed.write_text("[github\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config._read_secret_document(malformed)
    assert "invalid TOML" in capsys.readouterr().out
    malformed.write_text("owner = 'wrong'\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config._read_secret_document(malformed)
    assert "unknown keys" in capsys.readouterr().out
    path = _write_project(tmp_path)
    source = path.read_text(encoding="utf-8")
    for original, mutated, needle in [
        ('artifact_dirs = ["dist"]', 'artifact_dirs = ["../dist"]', "artifact_dirs"),
        ('artifact_dirs = ["dist"]', 'artifact_dirs = ["/tmp/dist"]', "artifact_dirs"),
        ('build_step = "build"', 'build_step = ""', "build_step"),
    ]:
        path.write_text(source.replace(original, mutated), encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)
        assert needle in capsys.readouterr().out
    path.write_text(source, encoding="utf-8")

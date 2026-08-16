"""Adversarial CLI contract tests: exit/refusal behavior and boundary routing."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli
from cmru.controller import cli as controller_cli


@pytest.mark.parametrize("raw, seconds", [("2h30m", 9000), ("1 week", 604800), ("10secs", 10)])
def test_duration_parser_accumulates_supported_units(raw, seconds):
    assert cli.parse_duration(raw).total_seconds() == seconds


@pytest.mark.parametrize("raw", ["", "0s", "-1h", "3fortnights", "2hX"])
def test_duration_parser_rejects_ambiguous_or_nonpositive_values(raw):
    with pytest.raises(ValueError):
        cli.parse_duration(raw)


def test_command_and_release_policy_parsers_reject_incomplete_contracts(tmp_path):
    with pytest.raises(ValueError, match="at least one command"):
        cli.parse_commands(tmp_path / "cmru.toml", tmp_path, "test", [])
    with pytest.raises(ValueError, match="missing label"):
        cli.parse_commands(tmp_path / "cmru.toml", tmp_path, "test", [{"argv": ["true"], "cwd": "."}])
    with pytest.raises(ValueError, match="unknown artifact"):
        cli._parse_release_policy({"artifacts": ["mystery"], "release": {"git_tag": True}}, "demo", None)
    with pytest.raises(ValueError, match="requires release.git_tag=false"):
        cli._parse_release_policy({"artifacts": [], "release": {"git_tag": True}}, "demo", SimpleNamespace(strategy="none"))


def test_release_env_clears_stale_token_and_project_override_wins(monkeypatch):
    github = cli.GitHubConfig(owner="o", repo="r", token="new", owner_type="user")
    env = cli.ReleaseEnvConfig(registry_url="registry", env={"CUSTOM": "value"})
    monkeypatch.setenv("GITHUB_TOKEN", "stale")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "older")
    cli.apply_release_env(github, env)
    assert os.environ["GITHUB_PUSH_PAT"] == "new"
    assert "GITHUB_TOKEN" not in os.environ and os.environ["REGISTRY"] == "registry"
    project = SimpleNamespace(github_token="project-token")
    assert cli.github_for_project(github, project).token == "project-token"


def test_publish_credentials_fail_closed_with_named_projects():
    configs = {
        "good": SimpleNamespace(github_token=" token "),
        "bad": SimpleNamespace(github_token=""),
    }
    with pytest.raises(RuntimeError, match="bad"):
        cli.require_project_publish_credentials(configs, ["good", "bad"])


def test_http_and_json_loader_preserve_status_body_and_headers(monkeypatch):
    class Response:
        status = 200
        headers = {"X-Test": "yes"}
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self): return b'[{"id":1}]'
    monkeypatch.setattr(cli, "urlopen", lambda request: Response())
    payload, headers = cli.load_json("https://api.example/x", "token")
    assert payload == [{"id": 1}] and headers["X-Test"] == "yes"
    class Empty(Response):
        def read(self): return b""
    monkeypatch.setattr(cli, "urlopen", lambda request: Empty())
    assert cli.load_json("https://api.example/x", "token")[0] == []


def test_main_dispatches_read_only_version_help_and_rejects_unknown_controller(monkeypatch, capsys):
    cli.main(["version"])
    assert "cmru " in capsys.readouterr().out
    cli.main(["--help"])
    assert "cmru" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        controller_cli.main(["unknown"])
    assert error.value.code == 2  # argparse's unsupported subcommand status


def test_main_run_step_routes_exact_remaining_argv(monkeypatch):
    seen = []
    monkeypatch.setattr("cmru.runner.main", lambda argv: seen.append(argv))
    cli.main(["run-step", "--project", "demo", "test"])
    assert seen == [["--project", "demo", "test"]]


def test_controller_commands_return_contractual_statuses_without_network(tmp_path, monkeypatch, capsys):
    missing = SimpleNamespace(plan=str(tmp_path / "missing.toml"), landscape=None)
    assert controller_cli.cmd_publish(missing) == 2
    assert "Plan file not found" in capsys.readouterr().err
    assert controller_cli.cmd_approve(SimpleNamespace(plan=None, landscape="land")) == 2
    assert controller_cli.cmd_hold(SimpleNamespace(plan=None, landscape="land")) == 2
    monkeypatch.setattr(controller_cli, "_build_backend", lambda args: SimpleNamespace(
        _get=lambda *a: (200, "not-json", {})))
    assert controller_cli.cmd_status(SimpleNamespace(plan=None, landscape="land")) == 0
    assert "Could not parse" in capsys.readouterr().out


def test_controller_engine_errors_are_reported_as_failure(monkeypatch, capsys):
    class Engine:
        def approve(self, plan): raise RuntimeError("backend down")
        def hold(self, plan): raise RuntimeError("backend down")
    monkeypatch.setattr(controller_cli, "_build_engine", lambda args, landscape: Engine())
    assert controller_cli.cmd_approve(SimpleNamespace(plan="p", landscape="l")) == 1
    assert controller_cli.cmd_hold(SimpleNamespace(plan="p", landscape="l")) == 1
    assert "backend down" in capsys.readouterr().err


def test_worktree_dispatch_refuses_non_git_directory(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="not git"))
    with pytest.raises(SystemExit) as error:
        cli.main(["worktrees", "--json"])
    assert error.value.code == 2


def test_build_dispatch_rejects_unknown_project_before_child_execution(monkeypatch, tmp_path, capsys):
    config = tmp_path / "cmru.toml"; config.write_text("[orchestration]\n")
    project = SimpleNamespace(name="demo", github_token="token")
    # Track the environment key before the CLI's real release-env export mutates it;
    # otherwise this test would leak a fake credential into later config tests.
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "load_config", lambda path: (tmp_path, {"demo": project}, ["demo"], ["demo"], [], "project-first", {}, SimpleNamespace(), cli.GitHubConfig("o", "r", "t", "user"), cli.ReleaseEnvConfig({}, None)))
    with pytest.raises(SystemExit) as error:
        cli.main(["build", "--config", str(config), "--project", "missing"])
    assert error.value.code == 2
    assert "Unknown project" in capsys.readouterr().err

"""Final CLI/controller refusal and version-dispatch witnesses."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli
from cmru.controller import cli as controller_cli


def _args(**kwargs):
    values = dict(plan="plan", landscape="land", consul_addr=None, token=None,
                  generation_base=1, dry_run=False, to_tag=None, generation=None)
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_controller_plan_load_failures_and_unknown_dispatch_are_explicit(monkeypatch, tmp_path, capsys):
    plan = tmp_path / "plan.toml"; plan.write_text("bad")
    monkeypatch.setattr("cmru.controller.planner.load_plan", lambda path: (_ for _ in ()).throw(ValueError("malformed")))
    assert controller_cli.cmd_status(_args(plan=str(plan))) == 2
    assert controller_cli.cmd_rollback(_args(plan=str(plan))) == 2
    error = capsys.readouterr().err
    assert "Failed to load plan" in error

    class Parser:
        def parse_args(self, argv): return SimpleNamespace(log_level="INFO", verb="unexpected")
        def print_help(self): print("controller help")
    monkeypatch.setattr(controller_cli, "_build_parser", lambda: Parser())
    with pytest.raises(SystemExit) as exited:
        controller_cli.main([])
    assert exited.value.code == 1
    assert "controller help" in capsys.readouterr().out


def test_cli_load_config_reports_dependency_preflight_errors(monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "cmru.orchestration.toml"
    forge = SimpleNamespace(
        repo_root=tmp_path,
        orchestration=SimpleNamespace(project_order=["demo"], dependencies={}, project_configs={}),
        projects={},
    )
    monkeypatch.setattr(cli, "load_forge_config", lambda path: forge)
    monkeypatch.setattr(cli, "build_report", lambda **kwargs: SimpleNamespace(errors=("cycle", "unknown")))
    with pytest.raises(SystemExit) as error:
        cli.load_config(cfg)
    assert error.value.code == cli.exit_codes.CONFIG_ERROR
    assert "dependency preflight: cycle" in capsys.readouterr().out


def test_cli_load_config_rejects_missing_orchestration_selection(monkeypatch, tmp_path):
    forge = SimpleNamespace(repo_root=tmp_path, orchestration=None, projects={})
    monkeypatch.setattr(cli, "load_forge_config", lambda path: forge)
    with pytest.raises(ValueError, match="no project selection"):
        cli.load_config(tmp_path / "cmru.toml", validate_dependencies=False)


@pytest.mark.parametrize("description, expected", [
    ("cmru-v1.2.3-0-gabc123", "1.2.3"),
    ("cmru-v1.2.3-2-gabc123", "1.2.4.dev2+gabc123"),
    ("other-v1.2.3-2-gabc123", None),
])
def test_source_version_describe_contract_does_not_invent_unknown_shapes(description, expected):
    assert cli._dev_version_from_describe(description) == expected


def test_config_hint_is_actionable_only_when_config_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli._config_hint(tmp_path) == ""
    (tmp_path / "cmru.toml").write_text("[project]\n")
    assert "--config" in cli._config_hint(tmp_path)

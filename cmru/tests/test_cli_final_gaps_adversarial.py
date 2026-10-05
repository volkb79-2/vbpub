"""Final CLI refusal and version-dispatch witnesses."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


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
    assert "dependency preflight: cycle" in capsys.readouterr().err


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

"""KI-83: first-party requirements of nested wheel pyprojects are held to depends_on."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru.config import load_forge_config
from cmru.dependencies import build_report
from tests.test_config_tool_dependencies import _base_toml, _write


def _projects(tmp_path: Path, *, nested_requires, declared_wheels=("client/pyproject.toml",)):
    provider_root = tmp_path / "provider"
    consumer_root = tmp_path / "consumer"
    (consumer_root / "client").mkdir(parents=True)
    provider_root.mkdir()
    (provider_root / "pyproject.toml").write_text(
        '[project]\nname = "provider-lib"\n', encoding="utf-8"
    )
    (consumer_root / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\ndependencies = []\n', encoding="utf-8"
    )
    (consumer_root / "client" / "pyproject.toml").write_text(
        '[project]\nname = "consumer-client"\n'
        f"dependencies = {nested_requires!r}\n".replace("'", '"'),
        encoding="utf-8",
    )
    return {
        "provider": SimpleNamespace(
            project_root=provider_root, scm_dist="provider-lib", tool_dependencies=()
        ),
        "consumer": SimpleNamespace(
            project_root=consumer_root, scm_dist=None, tool_dependencies=(),
            wheel_pyprojects=list(declared_wheels),
        ),
    }


def _report(tmp_path, projects, declared_consumer):
    return build_report(
        repo_root=tmp_path,
        project_order=["provider", "consumer"],
        declared={"provider": [], "consumer": declared_consumer},
        projects=projects,
    )


def test_nested_wheel_requirement_without_depends_on_names_consumer_provider_and_file(tmp_path):
    report = _report(tmp_path, _projects(tmp_path, nested_requires=["provider-lib>=1"]), [])
    assert not report.ok
    (error,) = report.errors
    assert "'consumer'" in error and "'provider-lib'" in error
    assert "client/pyproject.toml" in error
    assert "does not declare 'provider'" in error


def test_nested_wheel_requirement_with_depends_on_passes_and_records_an_edge(tmp_path):
    report = _report(
        tmp_path, _projects(tmp_path, nested_requires=["provider-lib>=1"]), ["provider"],
    )
    assert report.ok, report.errors
    assert any(
        edge.kind == "pyproject" and edge.provider == "provider"
        and "client/pyproject.toml" in edge.source
        for edge in report.edges
    )


def test_an_undeclared_nested_pyproject_is_not_read(tmp_path):
    projects = _projects(tmp_path, nested_requires=["provider-lib>=1"], declared_wheels=())
    assert _report(tmp_path, projects, []).ok


def test_a_declared_nested_pyproject_that_is_missing_or_malformed_is_reported(tmp_path):
    projects = _projects(
        tmp_path, nested_requires=[], declared_wheels=("client/pyproject.toml", "gone/pyproject.toml")
    )
    (tmp_path / "consumer" / "client" / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    errors = _report(tmp_path, projects, []).errors
    assert any("cannot read" in e and "client" in e for e in errors)
    assert any("'gone/pyproject.toml' does not exist" in e for e in errors)


def test_config_parses_wheel_pyprojects(tmp_path):
    cfg = _write(tmp_path, _base_toml('wheel_pyprojects = ["client/pyproject.toml"]\n'))
    assert load_forge_config(cfg).projects["naf"].wheel_pyprojects == ["client/pyproject.toml"]
    cfg = _write(tmp_path, _base_toml())
    assert load_forge_config(cfg).projects["naf"].wheel_pyprojects == []


@pytest.mark.parametrize("value", [
    '"client/pyproject.toml"', "[1]", '[""]', '["/abs/pyproject.toml"]',
    '["../x/pyproject.toml"]', '["client/setup.py"]',
    '["a/pyproject.toml", "a/pyproject.toml"]',
])
def test_config_refuses_malformed_wheel_pyprojects(tmp_path, value):
    cfg = _write(tmp_path, _base_toml(f"wheel_pyprojects = {value}\n"))
    with pytest.raises(SystemExit) as exc:
        load_forge_config(cfg)
    assert exc.value.code == 2

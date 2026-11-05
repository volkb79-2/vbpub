"""Regression tests for Topos's published dependency metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _requirement(name: str) -> Requirement:
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requirements = [Requirement(value) for value in metadata["project"]["dependencies"]]
    textual = [
        requirement for requirement in requirements if requirement.name == name
    ]
    assert len(textual) == 1, f"project.dependencies must contain exactly one {name} requirement"
    return textual[0]


def test_textual_lower_bound_is_current() -> None:
    requirement = _requirement("textual")
    lower_bounds = [
        Version(specifier.version)
        for specifier in requirement.specifier
        if specifier.operator in {">=", ">"}
    ]
    assert lower_bounds
    assert max(lower_bounds) >= Version("8.2.8")


def test_textual_has_no_upper_ceiling() -> None:
    requirement = _requirement("textual")
    assert all(
        specifier.operator not in {"<", "<="} for specifier in requirement.specifier
    )


def test_cli_extended_lower_bound_is_current() -> None:
    requirement = _requirement("cli-extended")
    lower_bounds = [
        Version(specifier.version)
        for specifier in requirement.specifier
        if specifier.operator in {">=", ">"}
    ]
    assert lower_bounds
    assert max(lower_bounds) >= Version("0.5.2")


def test_toposd_console_script_is_registered() -> None:
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert metadata["project"]["scripts"]["toposd"] == "topos.service.cli:main"

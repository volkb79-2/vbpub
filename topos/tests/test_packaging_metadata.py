"""Regression tests for Topos's published dependency metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _textual_requirement() -> Requirement:
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requirements = [Requirement(value) for value in metadata["project"]["dependencies"]]
    textual = [
        requirement for requirement in requirements if requirement.name == "textual"
    ]
    assert len(textual) == 1, (
        "project.dependencies must contain exactly one Textual requirement"
    )
    return textual[0]


def test_textual_lower_bound_is_current() -> None:
    requirement = _textual_requirement()
    lower_bounds = [
        Version(specifier.version)
        for specifier in requirement.specifier
        if specifier.operator in {">=", ">"}
    ]
    assert lower_bounds
    assert max(lower_bounds) >= Version("8.2.8")


def test_textual_has_no_upper_ceiling() -> None:
    requirement = _textual_requirement()
    assert all(
        specifier.operator not in {"<", "<="} for specifier in requirement.specifier
    )

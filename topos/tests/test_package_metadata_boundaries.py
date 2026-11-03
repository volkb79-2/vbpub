"""Source-tree import metadata boundaries."""

from __future__ import annotations

import importlib
import importlib.metadata

import tomllib
from pathlib import Path

import topos


def _declared_fallback_version() -> str:
    """The pyproject ``fallback_version``: the one place the literal is declared."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data["tool"]["setuptools_scm"]["fallback_version"]


def test_source_checkout_uses_the_declared_fallback_version(monkeypatch) -> None:
    """An uninstalled source checkout remains importable before its first wheel."""
    original_version = importlib.metadata.version

    def missing_distribution(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing_distribution)
    try:
        assert importlib.reload(topos).__version__ == _declared_fallback_version()
    finally:
        monkeypatch.setattr(importlib.metadata, "version", original_version)
        importlib.reload(topos)

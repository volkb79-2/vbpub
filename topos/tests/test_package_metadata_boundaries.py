"""Source-tree import metadata boundaries."""

from __future__ import annotations

import importlib
import importlib.metadata

import topos


def test_source_checkout_uses_the_declared_fallback_version(monkeypatch) -> None:
    """An uninstalled source checkout remains importable before its first wheel."""
    original_version = importlib.metadata.version

    def missing_distribution(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing_distribution)
    try:
        assert importlib.reload(topos).__version__ == "0.1.0"
    finally:
        monkeypatch.setattr(importlib.metadata, "version", original_version)
        importlib.reload(topos)

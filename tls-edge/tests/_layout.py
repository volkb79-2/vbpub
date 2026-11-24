"""Shared filesystem layout helpers for CLI boundary tests."""

from __future__ import annotations

from pathlib import Path


def instance_stack_dir(instance_root: Path) -> Path:
    """Return the installed stack directory below an instance root."""
    return instance_root / "stack"

"""Root-resolution contracts for :mod:`ciu.deploy`."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.workspace_env import WorkspaceEnvError  # noqa: E402


def test_explicit_matching_define_root_resolves_to_canonical_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A matching explicit root is the canonical deployment workspace."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setenv("REPO_ROOT", str(repo_root))

    assert deploy.resolve_repo_root(repo_root / ".") == repo_root.resolve()


def test_root_resolution_requires_generated_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset environment root explains how to generate the workspace env."""
    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(WorkspaceEnvError) as exc_info:
        deploy.resolve_repo_root(None)

    message = str(exc_info.value)
    assert "REPO_ROOT" in message
    assert "ciu env generate" in message

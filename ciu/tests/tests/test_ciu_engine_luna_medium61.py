"""DooD preflight failure contracts."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def test_dood_preflight_reports_typed_error_when_docker_is_unavailable(monkeypatch, tmp_path):
    """S1.5: an unavailable Docker CLI becomes an actionable CIU error."""
    logical_root = tmp_path / "logical"
    physical_root = tmp_path / "physical"
    monkeypatch.setenv("REPO_ROOT", str(logical_root))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(physical_root))
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "0")

    def unavailable_docker(*_args, **_kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(engine.procutil, "docker", unavailable_docker)

    with pytest.raises(engine.DooDPreflightError) as exc_info:
        engine._dood_preflight(physical_root / "stack")

    message = str(exc_info.value)
    assert "docker not available for DooD preflight" in message
    assert "docker" in message


def test_dood_preflight_reports_named_volume_remediation_on_probe_failure(monkeypatch, tmp_path):
    """S1.5: a failed daemon mount probe fails closed with remediation."""
    logical_root = tmp_path / "logical"
    physical_root = tmp_path / "physical"
    monkeypatch.setenv("REPO_ROOT", str(logical_root))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(physical_root))
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "0")
    monkeypatch.setattr(
        engine.procutil,
        "docker",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    with pytest.raises(engine.DooDPreflightError) as exc_info:
        engine._dood_preflight(physical_root / "stack")

    message = str(exc_info.value)
    assert "cannot reach PHYSICAL_REPO_ROOT" in message
    assert "Named-volume workspaces" in message
    assert "bind-mount the workspace from the host" in message
    assert "PHYSICAL_REPO_ROOT == REPO_ROOT" in message

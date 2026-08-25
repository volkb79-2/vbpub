"""Focused healthcheck action outcome contracts."""

from __future__ import annotations

from pathlib import Path

from ciu import deploy
from ciu.deploy_pkg.profiles import Profile


def test_healthcheck_empty_selection_is_successful_noop(monkeypatch, tmp_path: Path, capsys):
    """No selected services must not resolve containers or invoke a health gate."""

    monkeypatch.setattr(
        deploy,
        "resolve_selection_health_containers",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not resolve containers")),
    )

    assert deploy.action_healthcheck(tmp_path, Profile(), []) == 0
    assert "No services selected to check" in capsys.readouterr().out


def test_healthcheck_failed_gate_reports_summary_and_returns_one(monkeypatch, tmp_path: Path, capsys):
    """A failed container-health gate remains a deployment-blocking failure."""

    monkeypatch.setattr(
        deploy,
        "resolve_selection_health_containers",
        lambda *_args, **_kwargs: {"project-prod-api": 30.0},
    )
    monkeypatch.setattr(
        deploy,
        "run_container_health_gate",
        lambda *_args, **_kwargs: (False, {"unhealthy": ["project-prod-api"]}),
    )

    assert deploy.action_healthcheck(tmp_path, Profile(), [{"path": "apps/api"}]) == 1

    output = capsys.readouterr().out
    assert "unhealthy: project-prod-api" in output
    assert "[S7.7] health gate failed" in output

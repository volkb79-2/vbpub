"""Container discovery and stop-action failure boundary contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from ciu import deploy


def test_matching_containers_requires_project_and_environment_identity():
    """Container actions fail closed when their anchored namespace is undefined."""

    with pytest.raises(ValueError, match="project_name/environment_tag"):
        deploy._matching_containers({"deploy": {"project_name": "project"}})


def test_matching_containers_normalizes_missing_docker_to_actionable_value_error(monkeypatch):
    """Docker absence is reported as a user-facing deploy configuration failure."""

    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("docker")),
    )

    with pytest.raises(ValueError, match="docker not available: docker"):
        deploy._matching_containers(
            {"deploy": {"project_name": "project", "environment_tag": "prod"}}
        )


def test_stop_no_matching_containers_is_successful_noop(monkeypatch, capsys):
    """Stopping an already-empty project namespace must stay green."""

    monkeypatch.setattr(deploy, "_matching_containers", lambda _config: [])

    assert deploy.action_stop({"deploy": {"project_name": "project", "environment_tag": "prod"}}) == 0
    assert "No matching containers running" in capsys.readouterr().out


def test_stop_reports_missing_docker_after_container_discovery(monkeypatch, capsys):
    """A discovered project namespace remains red if Docker vanishes before stop."""

    monkeypatch.setattr(deploy, "_matching_containers", lambda _config: ["project-prod-api"])
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("docker")),
    )

    assert deploy.action_stop({"deploy": {"project_name": "project", "environment_tag": "prod"}}) == 1
    assert "docker not available: docker" in capsys.readouterr().out

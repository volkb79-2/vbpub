"""Project-volume discovery and no-op removal contracts."""

from __future__ import annotations

from types import SimpleNamespace

from ciu import deploy


def test_list_project_volumes_returns_empty_without_complete_identity():
    """Volume cleanup stays a harmless no-op without a project namespace."""

    assert deploy._list_project_volumes({"deploy": {"project_name": "project"}}) == []


def test_list_project_volumes_treats_missing_or_failed_docker_as_empty(monkeypatch):
    """Best-effort cleanup must not fail merely because volume listing is unavailable."""

    config = {"deploy": {"project_name": "project", "environment_tag": "prod"}}
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("docker")),
    )
    assert deploy._list_project_volumes(config) == []

    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="project-prod-data\n"),
    )
    assert deploy._list_project_volumes(config) == []


def test_remove_project_volumes_returns_without_rm_when_listing_is_empty(monkeypatch):
    """No listed project volumes means no destructive Docker volume-rm call."""

    monkeypatch.setattr(deploy, "_list_project_volumes", lambda _config: [])
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not remove empty list")),
    )

    assert deploy._remove_project_volumes({"deploy": {}}) == []

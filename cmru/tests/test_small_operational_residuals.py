"""Behavioral witnesses for resolver edges."""
from __future__ import annotations

from types import SimpleNamespace

from cmru import resolve


def test_resolve_main_rejects_missing_or_unknown_project(monkeypatch):
    loaded = (
        None, {"known": SimpleNamespace(prefix="known-v", github_token="")}, ["known"], None,
        None, None, None, None, SimpleNamespace(owner="o", repo="r", token=None), None,
    )
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: None)
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    assert resolve.resolve_main(["missing"]) == 2


def test_resolve_main_uses_project_prefix_and_refuses_missing_owner_or_release(monkeypatch, capsys):
    project = SimpleNamespace(prefix="demo-v", github_token="project-token")
    loaded = (
        None, {"demo": project}, ["demo"], None, None, None, None, None,
        SimpleNamespace(owner="owner", repo="repo", token="root-token"), None,
    )
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: None)
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    captured = {}

    class Host:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("cmru.hosts.github.GitHubReleaseHost", Host)
    monkeypatch.setattr(resolve, "resolve", lambda host, prefix, **kwargs: None)
    assert resolve.resolve_main(["demo"]) == 1
    assert captured == {"owner": "owner", "repo": "repo", "token": "project-token"}
    assert "No releases found" in capsys.readouterr().err

    no_owner = loaded[:8] + (SimpleNamespace(owner="", repo="repo", token=None), None)
    monkeypatch.setattr("cmru.cli.load_config", lambda _: no_owner)
    assert resolve.resolve_main(["demo"]) == 2
    assert "owner/repo unknown" in capsys.readouterr().err

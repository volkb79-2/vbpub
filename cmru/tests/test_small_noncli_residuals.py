"""Exact branch witnesses for small non-CLI operational modules."""
from __future__ import annotations

import io
import json
import runpy
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import ghcr, release, standards


def test_standards_assessment_reports_disabled_history_and_manual_projects():
    project = SimpleNamespace(template_revision=4, changelog=None, steps={}, runner_steps={}, env={})
    result = standards.assess_projects(Path("."), {"demo": project}, [], ["demo"])[0]
    assert "source-first release history is disabled" in result.problems
    assert any("not in orchestration.project_order" in message for message in result.messages)


def test_standards_main_unknown_project_is_refused(monkeypatch):
    loaded = (Path("."), {"demo": SimpleNamespace()}, ["demo"])
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: Path("cmru.toml"))
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    assert standards.standards_main(["missing"]) == 2


def test_standards_update_requires_project_local_config(monkeypatch):
    loaded = (Path("."), {"demo": SimpleNamespace(project_root=None)}, ["demo"])
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: Path("cmru.toml"))
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    assert standards.standards_main(["demo", "--update"]) == 2  # config error, not a traceback


def test_standards_atomic_write_cleans_temporary_file_after_replace_failure(monkeypatch, tmp_path):
    path = tmp_path / "cmru.toml"
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        standards._atomic_write(path, "contents")
    assert not path.exists() and not path.with_name(".cmru.toml.cmru-tmp").exists()


def test_ghcr_request_http_error_and_repository_failure_are_explicit(monkeypatch):
    api = ghcr.GitHubPackages("owner", "repo", "token", "org")
    error = urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b"body"))
    monkeypatch.setattr(ghcr, "urlopen", lambda *_: (_ for _ in ()).throw(error))
    assert api._request("GET", "https://example") == (503, "body")
    api._request = lambda *args, **kwargs: (500, "bad")
    with pytest.raises(SystemExit) as raised:
        api.repo_visibility()
    assert raised.value.code == 1


def test_ghcr_request_omits_auth_header_when_token_is_absent(monkeypatch):
    api = ghcr.GitHubPackages("owner", "repo", None, "org")
    seen = {}
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self): return b"{}"
    def open_request(request):
        seen.update(request.header_items())
        return Response()
    monkeypatch.setattr(ghcr, "urlopen", open_request)
    assert api._request("GET", "https://example") == (200, "{}")
    assert "Authorization" not in seen


def test_release_publish_creates_missing_release_and_asset_url_is_deterministic(tmp_path):
    api = release.GitHubReleases("owner", "repo", "token", "org")
    calls = []
    api.get_release_by_tag = lambda tag: None
    api.create_release = lambda *args: (calls.append(args) or {"id": 1, "upload_url": "https://upload/{id}"})
    api.list_assets = lambda _rid: []
    api.upload_asset = lambda *args: calls.append(args)
    result = api.publish("demo-v1", "title", "notes", [])
    assert result["id"] == 1 and calls[0][:3] == ("demo-v1", "title", "notes")
    assert api.asset_download_url("demo-v1", "demo.whl").endswith("/demo-v1/demo.whl")

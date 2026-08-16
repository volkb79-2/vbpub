"""High-impact release/changelog boundary witnesses."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, release


class _Response:
    status = 200
    def __init__(self, body=b"{}"):
        self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self.body


def test_release_http_client_omits_empty_auth_and_returns_http_error_body(monkeypatch):
    seen = []
    def fake_urlopen(request):
        seen.append(dict(request.header_items()))
        return _Response(b'{"ok":true}')
    monkeypatch.setattr(release, "urlopen", fake_urlopen)
    client = release.GitHubReleases("o", "r", "")
    assert client._request("GET", "https://api.test") == (200, '{"ok":true}')
    assert "Authorization" not in seen[0]
    error = urllib.error.HTTPError("u", 404, "missing", {}, io.BytesIO(b"nope"))
    monkeypatch.setattr(release, "urlopen", lambda req: (_ for _ in ()).throw(error))
    assert client._request("GET", "https://api.test") == (404, "nope")


def test_release_get_release_fails_with_action_and_status(monkeypatch, capsys):
    client = release.GitHubReleases("o", "r", "t")
    client._request = lambda *a, **k: (500, "broken")
    with pytest.raises(SystemExit) as failure:
        client.get_release_by_tag("demo-v1")
    assert failure.value.code == 1
    assert "fetch release demo-v1" in capsys.readouterr().err


def test_release_latest_candidates_select_highest_semver_and_ignore_drafts():
    client = release.GitHubReleases("o", "r", "t")
    client.list_releases = lambda: [
        {"tag_name": "demo-v1.9.0", "assets": []},
        {"tag_name": "demo-v1.10.0", "assets": [{"name": "a", "browser_download_url": "u"}]},
        {"tag_name": "demo-v9.0.0", "draft": True, "assets": []},
    ]
    latest = client.resolve_latest("demo")
    assert latest["version"] == "1.10.0" and latest["assets"] == [{"name": "a", "url": "u"}]


def test_changelog_previous_tag_returns_none_when_git_describe_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(changelog.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=128, stdout="", stderr="no tag"))
    assert changelog._previous_project_tag(tmp_path, "demo-v", "demo-v1.0.0") is None


def test_changelog_render_empty_groups_emits_changed_metadata():
    rendered = changelog._render_section("source-abc", {}, source_end="abc")
    assert "### Changed" in rendered and "Release metadata prepared by CMRU." in rendered


def test_release_publish_versioned_dev_result_does_not_claim_immutable_release(tmp_path):
    asset = tmp_path / "demo.tar"; asset.write_bytes(b"x")
    calls = []
    gh = SimpleNamespace(publish=lambda *a, **k: calls.append(a), asset_download_url=lambda *a: "u")
    result = release.publish_versioned(gh, prefix="demo", version="1.0.0.dev1", asset_path=asset)
    assert result["release_tag"] is None and calls[0][0] == "demo-latest"

"""Behavioural coverage for release client/version and transaction safety seams."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


class TestReleaseHttpContracts:
    def test_release_client_routes_json_and_rejects_http_errors(self):
        from cmru.release import GitHubReleases
        gh = GitHubReleases("o", "r", "tok", api_base="https://api")
        calls = []
        def request(method, url, data=None, content_type=None):
            calls.append((method, url, data, content_type))
            if method == "GET" and url.endswith("/releases/tags/v1"): return 200, '{"id":1}'
            if method == "GET" and "/assets" in url: return 200, '[{"id":2,"name":"a"}]'
            if method == "POST": return 201, '{"id":3,"upload_url":"u"}'
            return 204, ""
        gh._request = request
        assert gh.get_release_by_tag("v1")["id"] == 1
        assert gh.list_assets(1)[0]["id"] == 2
        path = Path("/tmp/cmru-release-test-asset"); path.write_bytes(b"x")
        gh.upload_asset("https://upload/{?name}", path, "a")
        assert any(c[0] == "POST" for c in calls)
        gh._request = lambda *a, **k: (500, "bad")
        with pytest.raises(SystemExit): gh.delete_release(1)
        path.unlink()

    def test_resolve_latest_ignores_draft_prerelease_and_pointer(self):
        from cmru.release import GitHubReleases
        gh = GitHubReleases("o", "r", "", api_base="https://api")
        gh.list_releases = lambda: [
            {"tag_name": "demo-latest", "assets": []},
            {"tag_name": "demo-v1.9.0", "draft": True, "assets": []},
            {"tag_name": "demo-v1.2.0", "prerelease": True, "assets": []},
            {"tag_name": "demo-v1.10.0", "assets": [{"name": "x", "browser_download_url": "u"}]},
        ]
        assert gh.resolve_latest("demo")["version"] == "1.10.0"


class TestVersionContracts:
    @pytest.mark.parametrize("current,bump,expected", [("1.2.3", "patch", "1.2.4"), ("1.2.3", "minor", "1.3.0"), ("1.2.3", "major", "2.0.0")])
    def test_bump_version_matrix(self, current, bump, expected):
        from cmru.version import bump_version
        assert bump_version(current, bump) == expected

    def test_invalid_version_and_bump_refuse_invention(self):
        from cmru.version import bump_version
        with pytest.raises(ValueError): bump_version("not-semver", "patch")
        with pytest.raises(ValueError): bump_version("1.0.0", "unknown")

    def test_external_version_requires_a_single_named_fact(self, tmp_path, monkeypatch):
        from cmru.version import _external_version
        p = tmp_path / "cmru.vars"; p.write_text("VERSION=1.2.3\n")
        assert _external_version(tmp_path, "VERSION") == "1.2.3"
        monkeypatch.setenv("VERSION", "9.9.9")
        assert _external_version(tmp_path, "VERSION") == "1.2.3"
        p.unlink()
        with pytest.raises(RuntimeError): _external_version(tmp_path, "VERSION")


class TestTransactionRetentionAndPromotion:
    def test_progress_scope_and_result_records_round_trip(self, tmp_path):
        from cmru.transaction import ReleaseWorkspace, write_release_scope, read_release_scope, write_release_result, read_release_results, write_release_progress, read_release_progress
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        workspace = ReleaseWorkspace(tmp_path, tmp_path / "ws", "cmru/release/x", "abc")
        write_release_scope(tmp_path, workspace, ["a", "b"])
        assert read_release_scope(tmp_path, workspace) == ["a", "b"]
        write_release_result(tmp_path, workspace, "a", "v1")
        assert read_release_results(tmp_path, workspace) == {"a": "v1"}
        write_release_progress(tmp_path, workspace, "abc")
        assert read_release_progress(tmp_path, workspace) == "abc"

    def test_corrupt_scope_is_fail_closed(self, tmp_path):
        from cmru.transaction import ReleaseWorkspace, read_release_scope
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        workspace = ReleaseWorkspace(tmp_path, tmp_path / "ws", "cmru/release/x", "abc")
        marker = tmp_path / ".git" / "cmru-release-scopes" / "x.json"
        marker.parent.mkdir(parents=True); marker.write_text("not-json")
        assert read_release_scope(tmp_path, workspace) is None

    def test_revert_noop_does_not_spawn_git_mutation(self, tmp_path, monkeypatch):
        from cmru.transaction import ReleaseWorkspace, RevertResult, revert_promotion
        ws = ReleaseWorkspace(tmp_path, tmp_path / "ws", "cmru/release/x", "abc")
        monkeypatch.setattr("cmru.transaction._git", lambda *a, **k: "abc")
        with mock.patch("cmru.transaction.subprocess.run") as run:
            result = revert_promotion(ws)
        assert result == RevertResult(ok=True, reverted=False)
        run.assert_not_called()

    def test_sync_local_main_refuses_local_divergence(self, tmp_path, monkeypatch):
        from cmru.transaction import sync_local_main
        calls = []
        monkeypatch.setattr("cmru.transaction._git", lambda root, *args, **kw: {("branch", "--show-current"): "feature", ("rev-parse", "main"): "local", ("merge-base", "main", "origin/main"): "base"}.get(tuple(args), ""))
        monkeypatch.setattr("cmru.transaction.subprocess.run", lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0))
        assert not sync_local_main(tmp_path)
        assert len(calls) == 1  # fetch only; no branch movement

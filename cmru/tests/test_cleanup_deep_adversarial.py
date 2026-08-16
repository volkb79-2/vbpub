"""Deep behavioural coverage for CMRU's cleanup and retention APIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


def _cleanup(**overrides):
    values = dict(release_tag_prefixes=["demo-v"], keep_release_tags=[], ghcr_packages=[], ghcr_delete_packages=[])
    values.update(overrides)
    return cli.CleanupConfig(**values)


def _release(tag, ident, when="2020-01-01T00:00:00Z", **extra):
    return {"tag_name": tag, "id": ident, "published_at": when, **extra}


def test_list_releases_paginates_full_pages_and_stops_on_short_page(monkeypatch):
    calls = []
    full = [_release(f"demo-v1.0.{i}", i) for i in range(100)]
    short = [_release("demo-v2.0.0", 200)]
    def fake_load(url, token):
        calls.append(url)
        return (full if url.endswith("page=1") else short if url.endswith("page=2") else []), {"x": "y"}
    monkeypatch.setattr(cli, "load_json", fake_load)
    result = cli.list_releases("owner", "repo", "token")
    assert len(result) == 101
    assert "page=1" in calls[0] and "page=2" in calls[1]


def test_release_cleanup_selector_cutoff_keep_and_missing_date_are_safe(monkeypatch):
    cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
    releases = [
        _release("demo-v-old", 1, "2020-01-01T00:00:00Z"),
        _release("demo-v-kept", 2, "2020-01-01T00:00:00Z"),
        _release("other-v-old", 3, "2020-01-01T00:00:00Z"),
        _release("demo-v-new", 4, "2025-01-01T00:00:00Z"),
        {"tag_name": "demo-v-no-date", "id": 5},
        {"tag_name": "demo-v-no-id", "published_at": "2020-01-01T00:00:00Z"},
    ]
    deleted = []
    monkeypatch.setattr(cli, "list_releases", lambda *_: releases)
    monkeypatch.setattr(cli, "delete_release", lambda *args: deleted.append(args[3]))
    cli.cleanup_releases("o", "r", "t", cutoff, False, _cleanup(keep_release_tags=["demo-v-kept"]))
    assert deleted == [1]


def test_delete_release_dry_run_and_http_failure(monkeypatch, capsys):
    cli.delete_release("o", "r", "t", 4, True)
    assert "Would delete release 4" in capsys.readouterr().out
    monkeypatch.setattr(cli, "http_request", lambda *_: (500, "broken", {}))
    with pytest.raises(RuntimeError, match="release 4"):
        cli.delete_release("o", "r", "t", 4, False)


def test_unmanaged_release_duplicate_or_non_numeric_id_refuses(capsys, monkeypatch):
    monkeypatch.setattr(cli, "list_releases", lambda *_: [_release("demo-latest", 1), _release("demo-latest", 2)])
    with pytest.raises(RuntimeError, match="expected one"):
        cli.delete_unmanaged_release_tag("o", "r", "t", "demo-latest", dry_run=False)
    monkeypatch.setattr(cli, "list_releases", lambda *_: [{"tag_name": "demo-latest", "id": "bad"}])
    with pytest.raises(RuntimeError, match="numeric ID"):
        cli.delete_unmanaged_release_tag("o", "r", "t", "demo-latest", dry_run=False)


@pytest.mark.parametrize("owner_type, needle", [("org", "/orgs/owner/packages/container/pkg/versions"), ("user", "/users/owner/packages/container/pkg/versions")])
def test_package_version_listing_uses_owner_route_and_pagination(monkeypatch, owner_type, needle):
    urls = []
    def fake_load(url, token):
        urls.append(url)
        return ([{"id": 1}] if "page=1" in url else []), {}
    monkeypatch.setattr(cli, "load_json", fake_load)
    assert cli.list_package_versions("owner", "pkg", "token", owner_type) == [{"id": 1}]
    assert needle in urls[0]


def test_package_listing_filters_empty_names_and_uses_user_route(monkeypatch):
    seen = []
    def fake_load(url, _token):
        seen.append(url)
        return ([{"name": "pkg"}, {"name": ""}, {}], {}) if url.endswith("page=1") else ([], {})
    monkeypatch.setattr(cli, "load_json", fake_load)
    assert cli.list_container_packages("alice", "tok", "user") == ["pkg"]
    assert "/users/alice/packages" in seen[0]


@pytest.mark.parametrize(
    "status, body, expected",
    [(400, "cannot be deleted", "Skipping GHCR cleanup"), (403, "forbidden", "missing package delete scope")],
)
def test_package_version_known_nonfatal_api_errors_are_warnings(monkeypatch, capsys, status, body, expected):
    monkeypatch.setattr(cli, "http_request", lambda *_: (status, body, {}))
    cli.delete_package_version("o", "pkg", "t", 1, "org", False)
    assert expected in capsys.readouterr().out


def test_package_version_unknown_error_and_package_delete_404_are_distinct(monkeypatch, capsys):
    monkeypatch.setattr(cli, "http_request", lambda *_: (500, "oops", {}))
    with pytest.raises(RuntimeError, match="version 1"):
        cli.delete_package_version("o", "pkg", "t", 1, "user", False)
    monkeypatch.setattr(cli, "http_request", lambda *_: (404, "gone", {}))
    cli.delete_package("o", "pkg", "t", "user", False)
    assert "not found" in capsys.readouterr().out


def test_ghcr_cleanup_explicit_package_delete_skips_version_listing(monkeypatch):
    listed = []
    deleted = []
    monkeypatch.setattr(cli, "list_package_versions", lambda *args: listed.append(args) or [])
    monkeypatch.setattr(cli, "delete_package", lambda *args: deleted.append(args))
    cli.cleanup_ghcr("o", "t", "org", datetime.now(timezone.utc), False,
                     _cleanup(ghcr_packages=["pkg"], ghcr_delete_packages=["pkg"]))
    assert deleted and not listed


def test_ghcr_cleanup_age_filter_skips_recent_and_incomplete_versions(monkeypatch):
    deleted = []
    monkeypatch.setattr(cli, "list_package_versions", lambda *_: [
        {"id": 1, "updated_at": "2020-01-01T00:00:00Z"},
        {"id": 2, "updated_at": "2030-01-01T00:00:00Z"},
        {"id": 3},
    ])
    monkeypatch.setattr(cli, "delete_package_version", lambda *args: deleted.append(args[3]))
    cli.cleanup_ghcr("o", "t", "user", datetime(2024, 1, 1, tzinfo=timezone.utc), False,
                     _cleanup(ghcr_packages=["pkg"]))
    assert deleted == [1]


def test_remove_assets_requires_resolved_credential_and_propagates_cutoff(monkeypatch):
    github = cli.GitHubConfig("o", "r", "", "user")
    env = cli.ReleaseEnvConfig({}, None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    with pytest.raises(RuntimeError, match="github.token"):
        cli.remove_assets("1d", True, _cleanup(ghcr_packages=[]), github, env)
    github = cli.GitHubConfig("o", "r", "tok", "user")
    seen = []
    monkeypatch.setattr(cli, "cleanup_releases", lambda *args: seen.append(args))
    monkeypatch.setattr(cli, "cleanup_ghcr", lambda *args: seen.append(args))
    cli.remove_assets("1d", True, _cleanup(ghcr_packages=[]), github, env)
    assert len(seen) == 2 and seen[0][0:3] == ("o", "r", "tok")


def test_remote_tag_listing_filters_dereferenced_and_malformed_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="abc\trefs/tags/demo-v1\nabc^{}\trefs/tags/demo-v1\nmalformed\n\n",
        returncode=0,
    ))
    assert cli.list_remote_tags_matching(tmp_path, "demo-v*") == ["demo-v1"]


def test_tag_deletion_is_idempotent_for_missing_local_and_remote(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="missing"))
    cli.delete_git_tag_remote(tmp_path, "demo-v1", False)
    cli.delete_git_tag_local(tmp_path, "demo-v1", False)
    output = capsys.readouterr().out
    assert "already deleted" in output and "not found" in output


def test_cleanup_commit_deletions_stages_and_commits_only_cached_changes(monkeypatch, tmp_path, capsys):
    outputs = iter([" M file\n", "file\n"])
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: next(outputs))
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0))
    cli.cleanup_commit_deletions(tmp_path, "demo", ["a", "b", "c", "d", "e", "f"], False)
    assert calls[0][-2:] == ["add", "-A"]
    assert calls[1][0:3] == ["git", "-C", str(tmp_path)]
    assert any("(+1 more)" in item for item in calls[1])


def test_latest_version_ignores_draft_and_pointer_records(monkeypatch):
    monkeypatch.setattr(cli, "list_releases", lambda *_: [
        {"tag_name": "demo-latest"}, {"tag_name": "demo-v1.2.0", "draft": True},
        {"tag_name": "demo-v1.1.0"},
    ])
    assert cli._latest_version_for_prefix("o", "r", "t", "demo") == "1.1.0"

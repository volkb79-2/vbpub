from datetime import datetime, timezone
from pathlib import Path

import pytest

from cmru import cli


def cleanup(**overrides):
    values = dict(
        release_tag_prefixes=["v"], keep_release_tags=[],
        ghcr_packages=["pkg"], ghcr_delete_packages=[],
    )
    values.update(overrides)
    return cli.CleanupConfig(**values)


def test_release_and_package_pagination_uses_explicit_pages(monkeypatch):
    urls = []

    def load(url, token):
        urls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        if "releases" in url:
            return ([{"id": n} for n in range(100)] if page == 1 else [{"id": 101}], None)
        if "versions" in url:
            return ([{"id": n} for n in range(100)] if page == 1 else [{"id": 101}], None)
        return ([{"name": "pkg"}, {"name": "  "}], None) if page == 1 else ([], None)

    monkeypatch.setattr(cli, "load_json", load)
    assert len(cli.list_releases("o", "r", "t")) == 101
    assert len(cli.list_package_versions("o", "pkg", "t", "user")) == 101
    assert cli.list_container_packages("o", "t", "org") == ["pkg"]
    assert any("/users/o/packages/container/pkg/versions" in u for u in urls)
    assert any("/orgs/o/packages?" in u for u in urls)
    assert urls.count(urls[0]) == 1


def test_cleanup_releases_filters_selector_keep_cutoff_and_missing_id(monkeypatch):
    old = "2020-01-01T00:00:00Z"
    monkeypatch.setattr(cli, "list_releases", lambda *_: [
        {"id": 1, "tag_name": "v-old", "published_at": old},
        {"id": 2, "tag_name": "v-keep", "published_at": old},
        {"id": 3, "tag_name": "v-new", "published_at": "2030-01-01T00:00:00Z"},
        {"id": 4, "tag_name": "other", "published_at": old},
        {"tag_name": "v-no-id", "published_at": old},
        {"id": 5, "tag_name": "v-no-date"},
    ])
    deleted = []
    monkeypatch.setattr(cli, "delete_release", lambda *args: deleted.append(args[3]))
    cli.cleanup_releases("o", "r", "t", datetime(2021, 1, 1, tzinfo=timezone.utc), False,
                         cleanup(keep_release_tags=["v-keep"]))
    assert deleted == [1]


def test_package_deletion_failures_are_safe_and_dry_run_has_no_http(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "http_request", lambda *args: calls.append(args) or (400, "cannot be deleted", {}))
    cli.delete_package_version("o", "p", "t", 7, "org", False)
    assert "cannot be deleted" in capsys.readouterr().out
    assert "/orgs/o/packages/container/p/versions/7" in calls[-1][1]

    monkeypatch.setattr(cli, "http_request", lambda *args: (403, "forbidden", {}))
    cli.delete_package_version("o", "p", "t", 8, "user", False)
    assert "missing package delete scope" in capsys.readouterr().out
    cli.delete_package("o", "p", "t", "user", False)
    assert "missing package delete scope" in capsys.readouterr().out

    monkeypatch.setattr(cli, "http_request", lambda *args: (404, "gone", {}))
    cli.delete_package("o", "p", "t", "org", False)
    assert "not found" in capsys.readouterr().out
    monkeypatch.setattr(cli, "http_request", lambda *args: (500, "bad", {}))
    with pytest.raises(RuntimeError, match="Failed to delete p version 9"):
        cli.delete_package_version("o", "p", "t", 9, "org", False)
    monkeypatch.setattr(cli, "http_request", lambda *args: (_ for _ in ()).throw(AssertionError("HTTP")))
    cli.delete_package("o", "p", "t", "org", True)
    cli.delete_package_version("o", "p", "t", 9, "org", True)


def test_cleanup_ghcr_applies_cutoff_and_explicit_package_delete(monkeypatch):
    monkeypatch.setattr(cli, "list_package_versions", lambda *_: [
        {"id": 1, "updated_at": "2020-01-01T00:00:00Z"},
        {"id": 2, "updated_at": "2030-01-01T00:00:00Z"},
        {"id": 3},
    ])
    deleted_versions = []
    deleted_packages = []
    monkeypatch.setattr(cli, "delete_package_version", lambda *a: deleted_versions.append(a[3]))
    monkeypatch.setattr(cli, "delete_package", lambda *a: deleted_packages.append(a[1]))
    cli.cleanup_ghcr("o", "t", "user", datetime(2021, 1, 1, tzinfo=timezone.utc), False,
                     cleanup(ghcr_packages=["pkg", "whole"], ghcr_delete_packages=["whole"]))
    assert deleted_versions == [1]
    assert deleted_packages == ["whole"]


def test_tag_helpers_are_idempotent_and_parse_annotated_refs(monkeypatch, tmp_path, capsys):
    calls = []
    class Result:
        returncode = 1
        stdout = "abc\trefs/tags/v1\ndef\trefs/tags/v1^{}\n"
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or Result())
    cli.delete_git_tag_remote(tmp_path, "v1", True)
    cli.delete_git_tag_local(tmp_path, "v1", True)
    assert calls == []
    assert cli.list_remote_tags_matching(tmp_path, "v*") == ["v1"]
    cli.delete_git_tag_remote(tmp_path, "v1", False)
    cli.delete_git_tag_local(tmp_path, "v1", False)
    assert len(calls) == 3
    assert "skipping" in capsys.readouterr().out.lower()


def test_unmanaged_release_is_idempotent_and_rejects_ambiguous_records(monkeypatch):
    monkeypatch.setattr(cli, "list_releases", lambda *_: [])
    assert cli.delete_unmanaged_release_tag("o", "r", "t", "old", dry_run=False) is False
    monkeypatch.setattr(cli, "list_releases", lambda *_: [{"tag_name": "old", "id": 1}, {"tag_name": "old", "id": 2}])
    with pytest.raises(RuntimeError, match="expected one"):
        cli.delete_unmanaged_release_tag("o", "r", "t", "old", dry_run=False)
    monkeypatch.setattr(cli, "list_releases", lambda *_: [{"tag_name": "old", "id": "bad"}])
    with pytest.raises(RuntimeError, match="numeric ID"):
        cli.delete_unmanaged_release_tag("o", "r", "t", "old", dry_run=False)

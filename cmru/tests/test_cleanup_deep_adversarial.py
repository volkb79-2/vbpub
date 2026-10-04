"""Deep behavioural coverage for CMRU's cleanup and retention APIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


def _cleanup(**overrides):
    values = dict(release_tag_prefixes=["demo-v"], keep_release_tags=[], ghcr_packages=[], ghcr_delete_packages=[])
    values.update(overrides)
    return cli.CleanupConfig(**values)


def _release(tag, ident, when="2020-01-01T00:00:00Z", **extra):
    return {"tag_name": tag, "id": ident, "published_at": when, "assets": [], **extra}


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


@pytest.mark.parametrize(
    "changed_release",
    [
        _release("other-v1", 10),
        _release("demo-v-old", 10, "2030-01-01T00:00:00Z"),
        _release("demo-v-kept", 10),
        _release("demo-v-old", 10, updated_at="2030-01-01T00:00:00Z"),
        _release("demo-v-old", 10, assets=[{
            "id": 50, "name": "new.whl", "size": 7, "state": "uploaded",
            "updated_at": "2024-01-01T00:00:00Z",
        }]),
    ],
)
def test_age_cleanup_rechecks_release_identity_and_policy_after_confirmation(
    monkeypatch, capsys, changed_release,
):
    cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
    releases = [_release("demo-v-old", 10)]
    deleted = []
    monkeypatch.setattr(cli, "list_releases", lambda *_: releases)
    monkeypatch.setattr(
        cli, "delete_release",
        lambda _owner, _repo, _token, ident, **_kwargs: deleted.append(ident),
    )
    plan = cli.CleanupPlan()

    cli.cleanup_releases(
        "o", "r", "t", cutoff, True,
        _cleanup(keep_release_tags=["demo-v-kept"]), plan=plan,
    )
    releases[:] = [changed_release]
    plan.apply()

    assert deleted == []
    assert "changed or no longer qualifies after preview" in capsys.readouterr().out


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


def test_unmanaged_release_plan_skips_a_release_retagged_after_preview(monkeypatch, capsys):
    releases = [_release("demo-old", 10)]
    deleted = []
    monkeypatch.setattr(cli, "list_releases", lambda *_: releases)
    monkeypatch.setattr(cli, "delete_release", lambda _o, _r, _t, ident, **_kw: deleted.append(ident))
    plan = cli.CleanupPlan()

    cli.delete_unmanaged_release_tag("o", "r", "t", "demo-old", dry_run=True, plan=plan)
    releases[:] = [_release("demo-new", 10)]
    plan.apply()

    assert deleted == []
    assert "changed or no longer qualifies after preview" in capsys.readouterr().out


@pytest.mark.parametrize("owner_type, needle", [("org", "/orgs/owner/packages/container/pkg/versions"), ("user", "/users/owner/packages/container/pkg/versions")])
def test_package_version_listing_uses_owner_route_and_pagination(monkeypatch, owner_type, needle):
    urls = []
    def fake_load(url, token):
        urls.append(url)
        return ([{"id": 1}] if "page=1" in url else []), {}
    monkeypatch.setattr(cli, "load_json", fake_load)
    assert cli.list_package_versions("owner", "pkg", "token", owner_type) == [{"id": 1}]
    assert needle in urls[0]


@pytest.mark.parametrize(
    "owner_type, expected_url",
    [
        ("org", "https://api.github.com/orgs/owner/packages/container/pkg/versions/17"),
        ("user", "https://api.github.com/users/owner/packages/container/pkg/versions/17"),
    ],
)
def test_get_package_version_uses_exact_version_route(monkeypatch, owner_type, expected_url):
    seen = []

    def request(method, url, token):
        seen.append((method, url, token))
        return 200, '{"id": 17}', {}

    monkeypatch.setattr(cli, "http_request", request)
    assert cli.get_package_version("owner", "pkg", "token", 17, owner_type) == {"id": 17}
    assert seen == [("GET", expected_url, "token")]


def test_get_package_version_rejects_boolean_identifier(monkeypatch):
    monkeypatch.setattr(cli, "http_request", lambda *_: (200, '{"id": true}', {}))
    with pytest.raises(RuntimeError, match="malformed record"):
        cli.get_package_version("owner", "pkg", "token", 1, "org")


def test_get_container_package_rejects_boolean_identifier(monkeypatch):
    monkeypatch.setattr(
        cli,
        "http_request",
        lambda *_: (
            200,
            '{"package_type":"container","id":true,"name":"pkg"}',
            {},
        ),
    )
    with pytest.raises(RuntimeError, match="malformed record"):
        cli.get_container_package("owner", "pkg", "token", "org")


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
    output = capsys.readouterr().out
    assert "may be absent or inaccessible" in output
    assert "deletion was not confirmed" in output


def test_ghcr_cleanup_explicit_package_delete_skips_version_listing(monkeypatch):
    listed = []
    deleted = []
    monkeypatch.setattr(
        cli, "get_container_package",
        lambda _owner, package, *_: {"id": 17, "name": package, "package_type": "container"},
    )
    monkeypatch.setattr(cli, "list_package_versions", lambda *args: listed.append(args) or [])
    monkeypatch.setattr(
        cli, "delete_package", lambda *args, **_kwargs: deleted.append(args),
    )
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


def test_ghcr_cleanup_plan_does_not_rediscover_new_versions(monkeypatch):
    first = {
        "id": 1,
        "updated_at": "2020-01-01T00:00:00Z",
        "metadata": {"container": {"tags": []}},
    }
    versions = [first]
    deleted = []
    monkeypatch.setattr(cli, "list_package_versions", lambda *_: versions)
    monkeypatch.setattr(cli, "get_package_version", lambda *_: first)
    monkeypatch.setattr(cli, "delete_package_version", lambda _o, _p, _t, ident, *_a, **_kw: deleted.append(ident))
    plan = cli.CleanupPlan()

    cli.cleanup_ghcr(
        "o", "t", "user", datetime(2024, 1, 1, tzinfo=timezone.utc), True,
        _cleanup(ghcr_packages=["pkg"]), plan=plan,
    )
    versions.append({"id": 2, "updated_at": "2020-01-01T00:00:00Z"})
    plan.apply()

    assert deleted == [1]


@pytest.mark.parametrize(
    "current",
    [
        {
            "id": 1,
            "updated_at": "2025-01-01T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 1,
            "updated_at": "2020-01-01T00:00:00Z",
            "metadata": {"container": {"tags": ["current"]}},
        },
    ],
)
def test_ghcr_cleanup_plan_skips_a_version_updated_or_retagged_after_preview(
    monkeypatch, capsys, current,
):
    initial = {
        "id": 1,
        "updated_at": "2020-01-01T00:00:00Z",
        "metadata": {"container": {"tags": []}},
    }
    deleted = []
    monkeypatch.setattr(cli, "list_package_versions", lambda *_: [initial])
    monkeypatch.setattr(cli, "get_package_version", lambda *_: current)
    monkeypatch.setattr(
        cli, "delete_package_version",
        lambda _o, _p, _t, ident, *_a, **_kw: deleted.append(ident),
    )
    plan = cli.CleanupPlan()

    cli.cleanup_ghcr(
        "o", "t", "user", datetime(2024, 1, 1, tzinfo=timezone.utc), True,
        _cleanup(ghcr_packages=["pkg"]), plan=plan,
    )
    plan.apply()

    assert deleted == []
    assert "changed or no longer qualifies after preview" in capsys.readouterr().out


def test_ghcr_cleanup_plan_captures_explicit_whole_package_delete(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        cli, "get_container_package",
        lambda _owner, package, *_: {"id": 17, "name": package, "package_type": "container"},
    )
    monkeypatch.setattr(cli, "list_package_versions", lambda *_: pytest.fail("whole-package delete listed versions"))
    monkeypatch.setattr(cli, "delete_package", lambda _o, package, *_a, **_kw: deleted.append(package))
    plan = cli.CleanupPlan()

    cli.cleanup_ghcr(
        "o", "t", "org", datetime(2024, 1, 1, tzinfo=timezone.utc), True,
        _cleanup(ghcr_packages=["pkg"], ghcr_delete_packages=["pkg"]), plan=plan,
    )
    plan.apply()

    assert deleted == ["pkg"]


def test_ghcr_cleanup_plan_treats_package_404_after_preview_as_ambiguous(monkeypatch, capsys):
    current = [
        {"id": 17, "name": "pkg", "package_type": "container"},
        None,
    ]
    deleted = []
    monkeypatch.setattr(cli, "get_container_package", lambda *_: current.pop(0))
    monkeypatch.setattr(cli, "delete_package", lambda *_args, **_kwargs: deleted.append(True))
    plan = cli.CleanupPlan()

    cli.cleanup_ghcr(
        "o", "t", "org", datetime(2024, 1, 1, tzinfo=timezone.utc), True,
        _cleanup(ghcr_packages=["pkg"], ghcr_delete_packages=["pkg"]), plan=plan,
    )
    plan.apply()

    assert deleted == []
    output = capsys.readouterr().out
    assert "may be absent or inaccessible" in output
    assert "after preview" in output
    assert "deletion is skipped" in output


def test_ghcr_cleanup_404_at_package_preview_is_not_certified_as_absent(monkeypatch, capsys):
    deleted = []
    monkeypatch.setattr(cli, "get_container_package", lambda *_: None)
    monkeypatch.setattr(cli, "delete_package", lambda *_args, **_kwargs: deleted.append(True))
    plan = cli.CleanupPlan()

    cli.cleanup_ghcr(
        "o", "t", "org", datetime(2024, 1, 1, tzinfo=timezone.utc), True,
        _cleanup(ghcr_packages=["pkg"], ghcr_delete_packages=["pkg"]), plan=plan,
    )
    plan.apply()

    output = capsys.readouterr().out
    assert deleted == []
    assert "may be absent or inaccessible" in output
    assert "package cleanup is skipped" in output


def test_ghcr_cleanup_404_for_a_version_after_preview_skips_deletion(monkeypatch, capsys):
    deleted = []
    monkeypatch.setattr(cli, "get_package_version", lambda *_: None)
    monkeypatch.setattr(
        cli, "delete_package_version",
        lambda *_args, **_kwargs: deleted.append(True),
    )

    cli._delete_package_version_if_unchanged(
        "o", "pkg", "t", 17, "org", datetime(2024, 1, 1, tzinfo=timezone.utc),
        "2020-01-01T00:00:00Z", (),
    )

    output = capsys.readouterr().out
    assert deleted == []
    assert "may be absent or inaccessible" in output
    assert "deletion is skipped" in output


def test_ghcr_cleanup_plan_skips_replacement_package_identity(monkeypatch, capsys):
    current = [
        {"id": 17, "name": "pkg", "package_type": "container"},
        {"id": 18, "name": "pkg", "package_type": "container"},
    ]
    deleted = []
    monkeypatch.setattr(cli, "get_container_package", lambda *_: current.pop(0))
    monkeypatch.setattr(cli, "delete_package", lambda *_args, **_kwargs: deleted.append(True))
    plan = cli.CleanupPlan()

    cli.cleanup_ghcr(
        "o", "t", "org", datetime(2024, 1, 1, tzinfo=timezone.utc), True,
        _cleanup(ghcr_packages=["pkg"], ghcr_delete_packages=["pkg"]), plan=plan,
    )
    plan.apply()

    assert deleted == []
    assert "was replaced after preview" in capsys.readouterr().out


def test_remove_assets_requires_resolved_credential_and_propagates_cutoff(monkeypatch):
    github = cli.GitHubConfig("o", "r", "", "user")
    env = cli.ReleaseEnvConfig({}, None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    with pytest.raises(RuntimeError, match="github.token"):
        cli.remove_assets("1d", True, _cleanup(ghcr_packages=[]), github, env)
    github = cli.GitHubConfig("o", "r", "tok", "user")
    seen = []
    monkeypatch.setattr(cli, "cleanup_releases", lambda *args, **_kwargs: seen.append(args))
    monkeypatch.setattr(cli, "cleanup_ghcr", lambda *args, **_kwargs: seen.append(args))
    cli.remove_assets("1d", True, _cleanup(ghcr_packages=[]), github, env)
    assert len(seen) == 2 and seen[0][0:3] == ("o", "r", "tok")


def test_remove_assets_applies_only_targets_captured_before_confirmation(monkeypatch):
    github = cli.GitHubConfig("o", "r", "tok", "user")
    env = cli.ReleaseEnvConfig({}, None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    initially_listed = [_release("demo-v1.0.0", 1)]
    monkeypatch.setattr(cli, "list_releases", lambda *_: initially_listed)
    deleted = []
    monkeypatch.setattr(cli, "delete_release", lambda _o, _r, _t, ident, **_kw: deleted.append(ident))

    plan = cli.CleanupPlan()
    cli.remove_assets("1d", True, _cleanup(ghcr_packages=[]), github, env, plan=plan)

    # A second stale release appears after the operator saw the preview. Applying
    # the captured plan must not rediscover or delete that unconfirmed target.
    initially_listed.append(_release("demo-v1.1.0", 2))
    plan.apply()

    assert deleted == [1]


def test_project_cleanup_plan_skips_linked_tag_when_release_changes_after_preview(
    monkeypatch, tmp_path,
):
    releases = [_release("demo-v1.0.0", 1)]
    tags = {"demo-v1.0.0": "a" * 40}
    deleted_releases = []
    deleted_tags = []
    monkeypatch.setattr(cli, "list_releases", lambda *_: releases)
    monkeypatch.setattr(cli, "list_remote_tag_refs_matching", lambda *_args, **_kwargs: tags)
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setattr(cli, "delete_release", lambda _o, _r, _t, ident, **_kw: deleted_releases.append(ident))
    monkeypatch.setattr(cli, "delete_git_tag_remote", lambda _root, tag, *_a, **kw: deleted_tags.append(("remote", tag, kw["expected_present"], kw["expected_oid"])))
    monkeypatch.setattr(cli, "delete_git_tag_local", lambda _root, tag, *_a, **kw: deleted_tags.append(("local", tag, kw["expected_present"], kw["expected_oid"])))
    plan = cli.CleanupPlan()

    cli.cleanup_project_releases_and_tags(
        tmp_path, "o", "r", "t", "demo", [], True, plan=plan,
    )
    releases[:] = [_release("demo-v2.0.0", 1)]
    tags.clear()
    tags["demo-v2.0.0"] = "c" * 40
    plan.apply()

    assert deleted_releases == []
    assert deleted_tags == []


def test_project_cleanup_plan_does_not_delete_tags_added_after_preview(monkeypatch, tmp_path):
    releases = [_release("demo-v1.0.0", 1)]
    remote_tags = {}
    deleted_releases = []
    deleted_local_tags = []
    monkeypatch.setattr(cli, "list_releases", lambda *_: releases)
    monkeypatch.setattr(cli, "list_remote_tag_refs_matching", lambda *_args, **_kwargs: remote_tags)
    def delete_release(_o, _r, _t, ident, **_kw):
        deleted_releases.append(ident)
        releases.clear()
    monkeypatch.setattr(cli, "delete_release", delete_release)
    def local_git(_root, *args, **_kwargs):
        deleted_local_tags.append(args)
        if args[:2] == ("show-ref", "--hash"):
            return SimpleNamespace(returncode=0, stdout="b" * 40, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(cli, "run_local_git", local_git)
    plan = cli.CleanupPlan()
    outcomes = {}

    cli.cleanup_project_releases_and_tags(
        tmp_path, "o", "r", "t", "demo", [], True, plan=plan, outcomes=outcomes,
    )
    deleted_local_tags.clear()
    remote_tags["demo-v1.0.0"] = "a" * 40
    plan.apply()

    assert deleted_releases == [1]
    assert deleted_local_tags == [
        ("show-ref", "--exists", "refs/tags/demo-v1.0.0"),
        ("show-ref", "--hash", "--verify", "refs/tags/demo-v1.0.0"),
        ("update-ref", "-d", "refs/tags/demo-v1.0.0", "b" * 40),
        ("show-ref", "--exists", "refs/tags/demo-v1.0.0"),
        ("show-ref", "--hash", "--verify", "refs/tags/demo-v1.0.0"),
    ]
    assert outcomes["demo-v1.0.0"] is False


def test_project_cleanup_records_a_successfully_removed_tag_only_ref(monkeypatch, tmp_path):
    remote_tags = {"demo-v1.0.0": "a" * 40}
    local_tag = ["b" * 40]
    outcomes = {}
    monkeypatch.setattr(cli, "list_releases", lambda *_: [])
    monkeypatch.setattr(
        cli, "list_remote_tag_refs_matching", lambda *_args, **_kwargs: remote_tags,
    )
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: local_tag[0])

    def remove_remote(_root, tag, *_args, **_kwargs):
        remote_tags.pop(tag, None)

    def remove_local(_root, _tag, *_args, **_kwargs):
        local_tag[0] = None

    monkeypatch.setattr(cli, "delete_git_tag_remote", remove_remote)
    monkeypatch.setattr(cli, "delete_git_tag_local", remove_local)
    plan = cli.CleanupPlan()

    cli.cleanup_project_releases_and_tags(
        tmp_path, "o", "r", "t", "demo", [], True, plan=plan, outcomes=outcomes,
    )
    plan.apply()

    assert outcomes["demo-v1.0.0"] is True


def test_run_cleanup_plan_captures_declared_clean_step_and_generated_commit(
    monkeypatch, tmp_path, capsys,
):
    project = cli.ProjectConfig(
        name="demo", env={}, steps={"clean": []}, prefix="demo-v",
        github_token="tok",
    )
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    github = cli.GitHubConfig("o", "r", "tok", "user")
    monkeypatch.setattr(cli, "github_for_project", lambda *_: github)
    def preview_cleanup(*_args, outcomes=None, **_kwargs):
        if outcomes is not None:
            outcomes["demo-v1.0.0"] = True
        return ["demo-v1.0.0"]
    monkeypatch.setattr(cli, "cleanup_project_releases_and_tags", preview_cleanup)
    monkeypatch.setattr(cli, "_latest_version_for_prefix", lambda *_args, **_kwargs: "2.0.0")
    events = []

    def clean_step(_root, project, version, dry_run, *, publisher_token=None):
        events.append(("clean", project.name, version, dry_run, publisher_token))
        return not dry_run

    def commit(_root, name, tags, dry_run, *, before_paths):
        events.append(("commit", name, tuple(tags), dry_run, before_paths))

    def delete_package(_owner, package, _token, _owner_type, *, dry_run):
        events.append(("package", package, dry_run))

    monkeypatch.setattr(cli, "cleanup_project_step", clean_step)
    monkeypatch.setattr(cli, "cleanup_commit_deletions", commit)
    monkeypatch.setattr(cli, "_cleanup_worktree_paths", lambda _root: {"caller-edit.txt"})
    monkeypatch.setattr(
        cli, "get_container_package",
        lambda _owner, package, *_: {"id": 17, "name": package, "package_type": "container"},
    )
    monkeypatch.setattr(cli, "delete_package", delete_package)
    plan = cli.CleanupPlan()

    cli.run_cleanup_verb(
        tmp_path, {"demo": project}, ["demo"],
        _cleanup(ghcr_delete_packages=["demo-container"]), github,
        cli.ReleaseEnvConfig({}, None), None, True, plan=plan,
    )
    plan.apply()

    assert events == [
        ("clean", "demo", "2.0.0", True, "tok"),
        ("clean", "demo", "2.0.0", False, "tok"),
        ("commit", "demo", ("demo-v1.0.0",), False, {"caller-edit.txt"}),
        ("package", "demo-container", False),
    ]
    output = capsys.readouterr().out
    assert "CMRU_VERSION=2.0.0 is a preview estimate" in output
    assert "re-resolved from surviving Releases after confirmation" in output


def test_project_cleanup_keeps_changed_release_tag_and_recomputes_clean_version(
    monkeypatch, tmp_path, capsys,
):
    project = cli.ProjectConfig(
        name="demo", env={}, steps={"clean": []}, prefix="demo-v",
        github_token="tok",
    )
    github = cli.GitHubConfig("o", "r", "tok", "user")
    env = cli.ReleaseEnvConfig({}, None)
    releases = [_release("demo-v1.0.0", 1)]
    remote_tags = {"demo-v1.0.0": "a" * 40}
    deleted_releases = []
    deleted_tags = []
    clean_versions = []

    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "github_for_project", lambda *_: github)
    monkeypatch.setattr(cli, "list_releases", lambda *_: releases)
    monkeypatch.setattr(cli, "list_remote_tag_refs_matching", lambda *_a, **_kw: remote_tags)
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_a, **_kw: "b" * 40)
    monkeypatch.setattr(
        cli, "delete_release",
        lambda _o, _r, _t, ident, **_kw: deleted_releases.append(ident),
    )
    monkeypatch.setattr(
        cli, "delete_git_tag_remote",
        lambda *_a, **_kw: deleted_tags.append("remote"),
    )
    monkeypatch.setattr(
        cli, "delete_git_tag_local",
        lambda *_a, **_kw: deleted_tags.append("local"),
    )

    def clean_step(_root, _project, version, dry_run, *, publisher_token=None):
        clean_versions.append((version, dry_run, publisher_token))
        return not dry_run

    monkeypatch.setattr(cli, "cleanup_project_step", clean_step)
    monkeypatch.setattr(cli, "_cleanup_worktree_paths", lambda _root: set())
    monkeypatch.setattr(cli, "cleanup_commit_deletions", lambda *_a, **_kw: None)
    plan = cli.CleanupPlan()

    cli.run_cleanup_verb(
        tmp_path, {"demo": project}, ["demo"], _cleanup(), github, env,
        None, True, plan=plan,
    )
    releases[:] = [_release(
        "demo-v1.0.0", 1, updated_at="2026-10-03T19:00:00Z", assets=[{
            "id": 5, "name": "new.whl", "size": 11, "state": "uploaded",
            "updated_at": "2026-10-03T19:00:00Z",
        }],
    )]
    plan.apply()

    assert deleted_releases == []
    assert deleted_tags == []
    assert clean_versions == [("", True, "tok"), ("1.0.0", False, "tok")]
    assert "skipping its Git tag" in capsys.readouterr().out


def test_remote_tag_listing_filters_well_formed_peeled_refs(monkeypatch, tmp_path):
    oid = "a" * 40
    monkeypatch.setattr(cli, "run_remote_git", lambda *args, **kwargs: SimpleNamespace(
        stdout=f"{oid}\trefs/tags/demo-v1\n{oid}\trefs/tags/demo-v1^{{}}\n",
        returncode=0,
    ))
    assert cli.list_remote_tags_matching(tmp_path, "demo-v*") == ["demo-v1"]
    assert cli.list_remote_tag_refs_matching(tmp_path, "demo-v*") == {"demo-v1": oid}


@pytest.mark.parametrize(
    "stdout",
    [
        "malformed\n",
        "\n",
        f"{'a' * 40} refs/tags/demo-v1\n",
        f"{'g' * 40}\trefs/tags/demo-v1\n",
        f"{'a' * 40}\trefs/heads/main\n",
        f"{'a' * 40}\trefs/tags/other-v1\n",
        f"{'a' * 40}\trefs/tags/demo-v^1\n",
        f"{'a' * 40}\trefs/tags/demo-v 1\n",
        f"{'a' * 40}\trefs/tags/demo-v1\textra\n",
        f"{'a' * 40}\trefs/tags/demo-v1\n{'b' * 40}\trefs/tags/demo-v1\n",
        f"{'a' * 40}\trefs/tags/\n",
        "a" * 40 + "\trefs/tags/demo-v1^{}\n",
        "".join((
            "a" * 40 + "\trefs/tags/demo-v1\n",
            "b" * 40 + "\trefs/tags/demo-v1^{}\n",
            "c" * 40 + "\trefs/tags/demo-v1^{}\n",
        )),
    ],
)
def test_remote_tag_listing_refuses_malformed_successful_output(monkeypatch, tmp_path, stdout):
    monkeypatch.setattr(cli, "run_remote_git", lambda *args, **kwargs: SimpleNamespace(
        stdout=stdout, returncode=0,
    ))
    with pytest.raises(RuntimeError, match="Malformed remote tag listing"):
        cli.list_remote_tag_refs_matching(tmp_path, "demo-v*")


def test_remote_tag_listing_refuses_to_report_absence_when_origin_lookup_fails(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(cli, "run_remote_git", lambda *args, **kwargs: SimpleNamespace(
        returncode=128, stdout="", stderr="fatal: authentication failed",
    ))
    with pytest.raises(RuntimeError, match="Failed to list remote tags.*authentication failed"):
        cli.list_remote_tags_matching(tmp_path, "demo-v*")


@pytest.mark.parametrize(
    ("stdout", "stderr", "detail"),
    [("", "denied", "denied"), ("remote diagnostic", "", "remote diagnostic"),
     ("", "", "no diagnostic output")],
)
def test_remote_tag_listing_error_uses_available_diagnostic(
    monkeypatch, tmp_path, stdout, stderr, detail,
):
    monkeypatch.setattr(cli, "run_remote_git", lambda *args, **kwargs: SimpleNamespace(
        returncode=128, stdout=stdout, stderr=stderr,
    ))
    with pytest.raises(RuntimeError, match=f"{detail}"):
        cli.list_remote_tags_matching(tmp_path, "demo-v*")


def test_tag_deletion_is_idempotent_for_missing_local_and_remote(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "run_remote_git", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="", stderr="",
    ))
    def local_git(_root, *args, **kwargs):
        return SimpleNamespace(
            returncode=2 if args[:2] == ("show-ref", "--exists") else 1,
            stdout="", stderr="",
        )

    monkeypatch.setattr(cli, "run_local_git", local_git)
    cli.delete_git_tag_remote(tmp_path, "demo-v1", False)
    cli.delete_git_tag_local(tmp_path, "demo-v1", False)
    output = capsys.readouterr().out
    assert "Remote tag demo-v1 not found" in output
    assert "Local tag demo-v1 not found" in output


def test_remote_tag_delete_fails_if_push_fails_and_tag_still_exists(monkeypatch, tmp_path):
    oid = "a" * 40
    results = iter([
        SimpleNamespace(returncode=0, stdout=f"{oid}\trefs/tags/demo-v1\n", stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="permission denied"),
        SimpleNamespace(returncode=0, stdout=f"{oid}\trefs/tags/demo-v1\n", stderr=""),
    ])
    monkeypatch.setattr(cli, "run_remote_git", lambda *args, **kwargs: next(results))
    with pytest.raises(RuntimeError, match="Failed to delete remote tag demo-v1.*permission denied"):
        cli.delete_git_tag_remote(tmp_path, "demo-v1", False)


def test_remote_tag_delete_treats_confirmed_concurrent_removal_as_idempotent(
    monkeypatch, tmp_path, capsys,
):
    oid = "a" * 40
    results = iter([
        SimpleNamespace(returncode=0, stdout=f"{oid}\trefs/tags/demo-v1\n", stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="already gone"),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
    ])
    monkeypatch.setattr(cli, "run_remote_git", lambda *args, **kwargs: next(results))
    cli.delete_git_tag_remote(tmp_path, "demo-v1", False)
    assert "disappeared or changed during deletion" in capsys.readouterr().out


def test_remote_tag_delete_skips_a_tag_retargeted_after_preview(monkeypatch, tmp_path, capsys):
    preview_oid = "a" * 40
    current_oid = "b" * 40
    calls = []

    def remote(_root, *args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=f"{current_oid}\trefs/tags/demo-v1\n",
            stderr="",
        )

    monkeypatch.setattr(cli, "run_remote_git", remote)
    cli.delete_git_tag_remote(
        tmp_path, "demo-v1", False, expected_present=True, expected_oid=preview_oid,
    )

    assert len(calls) == 1
    assert "changed after the confirmed cleanup preview" in capsys.readouterr().out


def test_remote_tag_delete_uses_a_lease_for_the_previewed_object(monkeypatch, tmp_path):
    oid = "a" * 40
    calls = []

    def remote(_root, *args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=f"{oid}\trefs/tags/demo-v1\n" if args[0] == "ls-remote" else "",
            stderr="",
        )

    monkeypatch.setattr(cli, "run_remote_git", remote)
    cli.delete_git_tag_remote(
        tmp_path, "demo-v1", False, expected_present=True, expected_oid=oid,
    )

    assert calls[-1] == (
        "push", f"--force-with-lease=refs/tags/demo-v1:{oid}",
        "origin", ":refs/tags/demo-v1",
    )


@pytest.mark.parametrize(
    ("stdout", "stderr", "detail"),
    [("", "permission denied", "permission denied"),
     ("push diagnostic", "", "push diagnostic"),
     ("", "", "no diagnostic output")],
)
def test_remote_tag_delete_error_uses_available_diagnostic(
    monkeypatch, tmp_path, stdout, stderr, detail,
):
    oid = "a" * 40
    results = iter([
        SimpleNamespace(returncode=0, stdout=f"{oid}\trefs/tags/demo-v1\n", stderr=""),
        SimpleNamespace(returncode=1, stdout=stdout, stderr=stderr),
        SimpleNamespace(returncode=0, stdout=f"{oid}\trefs/tags/demo-v1\n", stderr=""),
    ])
    monkeypatch.setattr(cli, "run_remote_git", lambda *args, **kwargs: next(results))
    with pytest.raises(RuntimeError, match=f"Failed to delete remote tag demo-v1.*{detail}"):
        cli.delete_git_tag_remote(tmp_path, "demo-v1", False)


def test_local_tag_delete_fails_if_tag_remains_after_git_error(monkeypatch, tmp_path):
    results = iter([
        SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="hook refused"),
        SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
    ])
    monkeypatch.setattr(cli, "run_local_git", lambda *args, **kwargs: next(results))
    with pytest.raises(RuntimeError, match="Failed to delete local tag demo-v1.*hook refused"):
        cli.delete_git_tag_local(tmp_path, "demo-v1", False)


def test_local_tag_delete_skips_a_tag_retargeted_after_preview(monkeypatch, tmp_path, capsys):
    current_oid = "b" * 40
    calls = []

    def local(_root, *args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=current_oid, stderr="")

    monkeypatch.setattr(cli, "run_local_git", local)
    cli.delete_git_tag_local(
        tmp_path, "demo-v1", False, expected_present=True, expected_oid="a" * 40,
    )

    assert calls == [
        ("show-ref", "--exists", "refs/tags/demo-v1"),
        ("show-ref", "--hash", "--verify", "refs/tags/demo-v1"),
    ]
    assert "changed after the confirmed cleanup preview" in capsys.readouterr().out


def test_local_tag_delete_uses_expected_old_object_update(monkeypatch, tmp_path):
    oid = "a" * 40
    calls = []

    def local(_root, *args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=oid, stderr="")

    monkeypatch.setattr(cli, "run_local_git", local)
    cli.delete_git_tag_local(
        tmp_path, "demo-v1", False, expected_present=True, expected_oid=oid,
    )

    assert calls == [
        ("show-ref", "--exists", "refs/tags/demo-v1"),
        ("show-ref", "--hash", "--verify", "refs/tags/demo-v1"),
        ("update-ref", "-d", "refs/tags/demo-v1", oid),
    ]


def test_local_tag_delete_treats_confirmed_concurrent_removal_as_idempotent(
    monkeypatch, tmp_path, capsys,
):
    results = iter([
        SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
        SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="already gone"),
        SimpleNamespace(returncode=2, stdout="", stderr=""),
    ])
    monkeypatch.setattr(cli, "run_local_git", lambda *args, **kwargs: next(results))
    cli.delete_git_tag_local(tmp_path, "demo-v1", False)
    assert "disappeared or changed during deletion" in capsys.readouterr().out


def test_local_tag_delete_fails_on_lookup_or_recheck_errors(monkeypatch, tmp_path):
    first_results = iter([
        SimpleNamespace(returncode=1, stdout="", stderr="repository unreadable"),
    ])
    monkeypatch.setattr(
        cli, "run_local_git", lambda *args, **kwargs: next(first_results),
    )
    with pytest.raises(RuntimeError, match="Failed to inspect local tag.*repository unreadable"):
        cli.delete_git_tag_local(tmp_path, "demo-v1", False)

    results = iter([
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="delete failed"),
        SimpleNamespace(returncode=1, stdout="", stderr="repository unreadable"),
    ])
    monkeypatch.setattr(cli, "run_local_git", lambda *args, **kwargs: next(results))
    with pytest.raises(RuntimeError, match="Failed to recheck local tag.*repository unreadable"):
        cli.delete_git_tag_local(tmp_path, "demo-v1", False)


def test_captured_clean_steps_keep_each_project_token_after_root_ghcr_selection(
    monkeypatch, tmp_path,
):
    alpha = cli.ProjectConfig(
        name="alpha", env={}, steps={"clean": []}, prefix="alpha-v",
        github_token="alpha-token",
    )
    beta = cli.ProjectConfig(
        name="beta", env={}, steps={"clean": []}, prefix="beta-v",
        github_token="beta-token",
    )
    github = cli.GitHubConfig("owner", "repo", "root-token", "org")
    env_config = cli.ReleaseEnvConfig({}, None)
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "cleanup_project_releases_and_tags", lambda *_a, **_k: [])
    monkeypatch.setattr(cli, "_latest_version_for_prefix", lambda *_a, **_k: "1.0.0")
    monkeypatch.setattr(cli, "_cleanup_worktree_paths", lambda *_: set())
    monkeypatch.setattr(cli, "cleanup_commit_deletions", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "delete_package", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "get_container_package",
        lambda _owner, package, *_: {"id": 17, "name": package, "package_type": "container"},
    )

    observed = []

    def execute(_step, _root, _logs, *, extra_env, protected_env=None):
        observed.append((
            extra_env["GITHUB_PUSH_PAT"], os.environ["GITHUB_PUSH_PAT"],
            protected_env["GITHUB_PUSH_PAT"],
        ))

    monkeypatch.setattr("cmru.runner.execute_step", execute)
    plan = cli.CleanupPlan()
    cli.run_cleanup_verb(
        tmp_path, {"alpha": alpha, "beta": beta}, ["alpha", "beta"],
        _cleanup(ghcr_delete_packages=["repo-package"]), github, env_config,
        None, True, plan=plan,
    )

    assert os.environ["GITHUB_PUSH_PAT"] == "root-token"
    plan.apply()
    assert observed == [
        ("alpha-token", "root-token", "alpha-token"),
        ("beta-token", "root-token", "beta-token"),
    ]


def test_cleanup_commit_deletions_stages_only_paths_new_since_clean_step(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli, "_cleanup_worktree_paths",
        lambda _root: {"caller-edit.txt", "generated file.txt"},
    )
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "generated file.txt\n")
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda root, *args, **kwargs: calls.append(["git", *args])
        or SimpleNamespace(returncode=0),
    )
    cli.cleanup_commit_deletions(
        tmp_path, "demo", ["a", "b", "c", "d", "e", "f"], False,
        before_paths={"caller-edit.txt"},
    )
    assert calls[0] == [
        "git", "-C", str(tmp_path), "add", "-A", "--", ":(literal)generated file.txt",
    ]
    assert calls[1][0:4] == ["git", "commit", "--only", "-m"]
    assert calls[1][-2:] == ["--", ":(literal)generated file.txt"]
    assert any("(+1 more)" in item for item in calls[1])


def test_cleanup_commit_does_not_commit_a_path_already_dirty_before_clean_step(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr(
        cli, "_cleanup_worktree_paths", lambda _root: {"user-edit.txt"},
    )
    calls = []
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda root, *args, **kwargs: calls.append(["git", *args])
        or SimpleNamespace(returncode=0),
    )

    cli.cleanup_commit_deletions(
        tmp_path, "demo", ["demo-v1"], False,
        before_paths={"user-edit.txt"},
    )

    assert calls == []
    assert "paths already dirty before steps.clean are excluded" in capsys.readouterr().out


def test_cleanup_commit_can_commit_clean_step_paths_without_deleted_tags(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        cli, "_cleanup_worktree_paths", lambda _root: {"generated.txt"},
    )
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "generated.txt\n")
    calls = []
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda root, *args, **kwargs: calls.append(["git", *args])
        or SimpleNamespace(returncode=1, stderr="hook failed", stdout=""),
    )

    cli.cleanup_commit_deletions(
        tmp_path, "demo", [], False, before_paths=set(),
    )

    assert calls[-1][4] == "chore(demo): commit cleanup-generated files"


def test_cleanup_commit_fails_loudly_when_git_cannot_stage_generated_paths(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        cli, "_cleanup_worktree_paths", lambda _root: {"generated.txt"},
    )
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stderr="index.lock exists", stdout="",
        ),
    )

    with pytest.raises(RuntimeError, match="failed to stage cleanup-generated paths.*index.lock"):
        cli.cleanup_commit_deletions(
            tmp_path, "demo", ["demo-v1"], False, before_paths=set(),
        )


def test_latest_version_ignores_draft_and_pointer_records(monkeypatch):
    monkeypatch.setattr(cli, "list_releases", lambda *_: [
        {"tag_name": "demo-latest"}, {"tag_name": "demo-v1.2.0", "draft": True},
        {"tag_name": "demo-v1.1.0"},
    ])
    assert cli._latest_version_for_prefix("o", "r", "t", "demo") == "1.1.0"


def test_latest_version_excludes_releases_in_the_captured_cleanup_plan(monkeypatch):
    monkeypatch.setattr(cli, "list_releases", lambda *_: [
        {"tag_name": "demo-v2.0.0"},
        {"tag_name": "demo-v1.9.0"},
    ])

    assert cli._latest_version_for_prefix(
        "o", "r", "t", "demo", exclude_tags=["demo-v2.0.0"],
    ) == "1.9.0"


def test_empty_cleanup_plan_has_no_actions_to_apply():
    cli.CleanupPlan().apply()


@pytest.mark.parametrize(
    "release",
    [
        {},
        {"assets": "not-a-list"},
        {"assets": [None]},
        {"assets": [{"id": True, "name": "a.whl", "size": 1, "state": "uploaded", "updated_at": "t"}]},
        {"assets": [{"id": 1, "name": 4, "size": 1, "state": "uploaded", "updated_at": "t"}]},
        {"assets": [{"id": 1, "name": "a.whl", "size": True, "state": "uploaded", "updated_at": "t"}]},
        {"assets": [{"id": 1, "name": "a.whl", "size": 1, "state": None, "updated_at": "t"}]},
        {"assets": [{"id": 1, "name": "a.whl", "size": 1, "state": "uploaded", "updated_at": None}]},
        {"assets": [{"id": 1, "name": "a.whl", "size": 1, "state": "uploaded", "updated_at": "t", "digest": 7}]},
        {"assets": [
            {"id": 1, "name": "a.whl", "size": 1, "state": "uploaded", "updated_at": "t"},
            {"id": 1, "name": "b.whl", "size": 2, "state": "uploaded", "updated_at": "t"},
        ]},
    ],
)
def test_release_asset_inventory_refuses_malformed_or_ambiguous_assets(release):
    with pytest.raises(RuntimeError, match="asset inventory|duplicate asset IDs"):
        cli._release_asset_inventory(release)


def test_release_asset_inventory_sorts_valid_asset_identity():
    assets = [
        {"id": 2, "name": "b.whl", "size": 2, "state": "uploaded", "updated_at": "t"},
        {"id": 1, "name": "a.whl", "size": 1, "state": "uploaded", "updated_at": "t", "digest": "sha256:x"},
    ]
    assert cli._release_asset_inventory({"assets": assets}) == (
        (1, "a.whl", 1, "uploaded", "t", "sha256:x"),
        (2, "b.whl", 2, "uploaded", "t", None),
    )


@pytest.mark.parametrize("releases", [[], [_release("demo-v1", 7), _release("demo-v1", 7)]])
def test_release_delete_skips_a_disappeared_or_ambiguous_release(monkeypatch, capsys, releases):
    deleted = []
    monkeypatch.setattr(cli, "list_releases", lambda *_: releases)
    monkeypatch.setattr(cli, "delete_release", lambda *_args, **_kwargs: deleted.append(True))

    assert cli._delete_release_if_tag_still_matches(
        "o", "r", "t", 7, "demo-v1", expected_updated_at="t",
        expected_asset_inventory=(), eligible=lambda _release: True,
    ) is False
    assert deleted == []
    assert "disappeared or became ambiguous" in capsys.readouterr().out


@pytest.mark.parametrize(
    "current, eligible",
    [
        (_release("demo-v1", 7, updated_at="later"), True),
        (_release("demo-v1", 7), False),
    ],
)
def test_release_delete_skips_changed_or_ineligible_release(
    monkeypatch, capsys, current, eligible,
):
    deleted = []
    monkeypatch.setattr(cli, "list_releases", lambda *_: [current])
    monkeypatch.setattr(cli, "delete_release", lambda *_args, **_kwargs: deleted.append(True))

    assert cli._delete_release_if_tag_still_matches(
        "o", "r", "t", 7, "demo-v1", expected_updated_at="t",
        expected_asset_inventory=(), eligible=lambda _release: eligible,
    ) is False
    assert deleted == []
    assert "changed or no longer qualifies" in capsys.readouterr().out


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (404, "", None),
        (503, "offline", "Failed to recheck pkg version 17"),
        (200, "{", "invalid JSON while rechecking"),
        (200, "[]", "malformed record while rechecking"),
    ],
)
def test_get_package_version_handles_absence_errors_and_malformed_records(
    monkeypatch, status, body, expected,
):
    monkeypatch.setattr(cli, "http_request", lambda *_: (status, body, {}))
    if expected is None:
        assert cli.get_package_version("o", "pkg", "t", 17, "org") is None
    else:
        with pytest.raises(RuntimeError, match=expected):
            cli.get_package_version("o", "pkg", "t", 17, "org")


def test_get_package_version_uses_the_user_package_route(monkeypatch):
    seen = []
    monkeypatch.setattr(
        cli, "http_request",
        lambda method, url, token: seen.append((method, url, token))
        or (200, '{"id":17}', {}),
    )

    assert cli.get_package_version("owner", "pkg", "token", 17, "user") == {"id": 17}
    assert seen == [(
        "GET", "https://api.github.com/users/owner/packages/container/pkg/versions/17", "token",
    )]


@pytest.mark.parametrize(
    "version",
    [
        {},
        {"metadata": None},
        {"metadata": {"container": {"tags": "bad"}}},
        {"metadata": {"container": {"tags": ["ok", 1]}}},
    ],
)
def test_container_version_tags_rejects_malformed_tag_inventories(version):
    with pytest.raises(RuntimeError, match="no usable container tag inventory"):
        cli._container_version_tags(version)


def test_container_version_tags_returns_sorted_tags():
    assert cli._container_version_tags({"metadata": {"container": {"tags": ["z", "a"]}}}) == ("a", "z")


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (404, "", None),
        (500, "offline", "Failed to inspect GHCR package pkg"),
        (200, "{", "invalid JSON while inspecting GHCR package"),
        (200, "[]", "malformed record"),
        (200, '{"package_type":"wrong","id":1,"name":"pkg"}', "malformed record"),
        (200, '{"package_type":"container","id":0,"name":"pkg"}', "malformed record"),
        (200, '{"package_type":"container","id":1,"name":4}', "malformed record"),
        (200, '{"package_type":"container","id":1,"name":"other"}', "malformed record"),
    ],
)
def test_get_container_package_checks_http_and_record_identity(
    monkeypatch, status, body, expected,
):
    monkeypatch.setattr(cli, "http_request", lambda *_: (status, body, {}))
    if expected is None:
        assert cli.get_container_package("o", "pkg", "t", "org") is None
    else:
        with pytest.raises(RuntimeError, match=expected):
            cli.get_container_package("o", "pkg", "t", "org")


def test_get_container_package_uses_user_route_and_accepts_casefolded_name(monkeypatch):
    seen = []
    record = {"package_type": "container", "id": 5, "name": "pkg"}
    monkeypatch.setattr(
        cli, "http_request",
        lambda method, url, token: seen.append((method, url, token)) or (200, json.dumps(record), {}),
    )
    assert cli.get_container_package("owner", "Pkg", "token", "user") == record
    assert seen == [(
        "GET", "https://api.github.com/users/owner/packages/container/Pkg", "token",
    )]


def test_explicit_package_dry_run_without_plan_reports_preview(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_container_package", lambda *_: {"id": 17})
    monkeypatch.setattr(cli, "list_package_versions", lambda *_: pytest.fail("package deletion listed versions"))
    cli.cleanup_ghcr(
        "o", "t", "org", datetime(2024, 1, 1, tzinfo=timezone.utc), True,
        _cleanup(ghcr_packages=["pkg"], ghcr_delete_packages=["pkg"]),
    )
    assert "Would delete GHCR package pkg (id=17)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "dry_run, with_plan, expected",
    [
        (True, True, "no deletion is planned"),
        (False, False, "package cleanup is skipped"),
    ],
)
def test_run_cleanup_verb_skips_inaccessible_explicit_ghcr_package(
    monkeypatch, capsys, dry_run, with_plan, expected,
):
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "get_container_package", lambda *_: None)
    monkeypatch.setattr(
        cli, "_delete_container_package_if_unchanged",
        lambda *_: pytest.fail("inaccessible package was deleted"),
    )
    config = SimpleNamespace(
        owner="owner", repo="repo", token="token", owner_type="org",
    )
    plan = cli.CleanupPlan() if with_plan else None

    cli.run_cleanup_verb(
        Path("."), {}, [], _cleanup(ghcr_delete_packages=["pkg"]), config,
        SimpleNamespace(), None, dry_run, plan=plan,
    )

    assert expected in capsys.readouterr().out


def test_project_tag_cleanup_skips_when_release_remains_after_preview(monkeypatch, tmp_path, capsys):
    deleted = []
    outcomes = {}
    monkeypatch.setattr(cli, "list_releases", lambda *_: [_release("demo-v1", 7)])
    monkeypatch.setattr(
        cli, "delete_git_tag_remote",
        lambda *_args, **_kwargs: deleted.append("remote"),
    )
    monkeypatch.setattr(
        cli, "delete_git_tag_local",
        lambda *_args, **_kwargs: deleted.append("local"),
    )

    cli._delete_project_tag_after_release_check(
        tmp_path, "o", "r", "t", "demo-v1",
        expected_remote_present=True, expected_local_present=True,
        expected_remote_oid="a" * 40, expected_local_oid="a" * 40,
        git_auth=None, release_was_planned=False, release_outcomes={},
        cleanup_outcomes=outcomes,
    )
    assert deleted == []
    assert outcomes == {"demo-v1": False}
    assert "still exists after preview" in capsys.readouterr().out


def test_project_tag_cleanup_skips_when_planned_release_was_not_deleted(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr(
        cli, "list_releases",
        lambda *_: pytest.fail("release list was queried after planned deletion failed"),
    )
    monkeypatch.setattr(
        cli, "delete_git_tag_remote",
        lambda *_args, **_kwargs: pytest.fail("remote tag was deleted"),
    )
    outcomes = {}
    cli._delete_project_tag_after_release_check(
        tmp_path, "o", "r", "t", "demo-v1",
        expected_remote_present=True, expected_local_present=True,
        expected_remote_oid="a" * 40, expected_local_oid="a" * 40,
        git_auth=None, release_was_planned=True, release_outcomes={"demo-v1": False},
        cleanup_outcomes=outcomes,
    )
    assert outcomes == {"demo-v1": False}
    assert "was not deleted after preview" in capsys.readouterr().out

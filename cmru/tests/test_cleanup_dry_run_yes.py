"""CLI-T1: ``cmru cleanup --dry-run --yes`` never applies the captured plan.

Every cleanup mode is driven through the real ``cmru cleanup`` verb with a REAL
``CleanupPlan`` (the plan collects the genuine deletion actions during the
preview).  Only the read-side GitHub/Git/retained-output boundaries are faked;
the delete helpers are NOT stubbed, so removing ``if vargs.dry_run: return``
from the dispatcher makes ``plan.apply()`` run and these tests fail.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction

OLD = "2020-01-01T00:00:00Z"


class _SpyPlan(cli.CleanupPlan):
    """A real CleanupPlan that records itself and refuses to be applied."""

    created: list = []

    def __init__(self):
        super().__init__()
        type(self).created.append(self)

    def apply(self):  # pragma: no cover - reaching this is the failure
        raise AssertionError(
            "plan.apply() ran under --dry-run: " + ", ".join(d for d, _ in self.actions)
        )


@pytest.fixture
def estate(monkeypatch, tmp_path):
    """One-project config plus read-only fakes at the GitHub/Git boundary."""
    project = cli.ProjectConfig(
        name="demo", env={}, steps={}, prefix="demo-v", github_token="token",
    )
    cleanup_cfg = cli.CleanupConfig(
        release_tag_prefixes=["demo-v"], keep_release_tags=[],
        ghcr_packages=["pkg"], ghcr_delete_packages=[],
    )
    loaded = (
        tmp_path, {"demo": project}, ["demo"], [], [], "project-first", {},
        cleanup_cfg, cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_a, **_k: None)

    _SpyPlan.created = []
    monkeypatch.setattr(cli, "CleanupPlan", _SpyPlan)

    releases = [
        {"id": 1, "tag_name": "demo-v1.0.0", "published_at": OLD, "assets": []},
        {"id": 2, "tag_name": "demo-unmanaged-old", "published_at": OLD, "assets": []},
    ]
    monkeypatch.setattr(cli, "list_releases", lambda *_a: releases)
    monkeypatch.setattr(cli, "list_remote_tag_refs_matching", lambda *_a, **_k: {"demo-v1.0.0": "a" * 40})
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_a: None)
    monkeypatch.setattr(cli, "_latest_version_for_prefix", lambda *_a, **_k: "")
    monkeypatch.setattr(cli, "list_container_packages", lambda *_a: ["pkg"])
    monkeypatch.setattr(cli, "get_container_package", lambda *_a: {"id": 7, "name": "pkg"})
    monkeypatch.setattr(cli, "list_package_versions", lambda *_a: [
        {"id": 11, "updated_at": OLD, "metadata": {"container": {"tags": ["old"]}}},
    ])

    def http(method, url, token):
        if method != "GET":
            raise AssertionError(f"destructive request under --dry-run: {method} {url}")
        return 200, "[]", {}

    monkeypatch.setattr(cli, "http_request", http)

    # Retained local outputs and worktrees: real filesystem layouts are heavy,
    # so fake the transaction readers; a non-dry-run call is a hard failure.
    def retained_identity(*_a, **_k):
        return ("identity",)

    def delete_output(_root, _project, _name, _id, *, dry_run, expected_identity=None):
        assert dry_run, "retained build output deleted under --dry-run"
        return [tmp_path / "retained"]

    def discard(_root, path, *, dry_run, expected_workspace=None):
        assert dry_run, "build worktree discarded under --dry-run"
        return SimpleNamespace(path=path, branch="cmru/build/x")

    monkeypatch.setattr(transaction, "retained_build_output_identity", retained_identity)
    monkeypatch.setattr(transaction, "delete_retained_build_output", delete_output)
    monkeypatch.setattr(transaction, "discard_build_workspace", discard)
    return tmp_path


MODES = {
    "policy": ["demo", "--policy"],
    "remove-assets": ["--remove-assets", "30d"],
    "unmanaged-release-tag": ["demo", "--delete-unmanaged-release-tag", "demo-unmanaged-old"],
    "build-output": ["demo", "--delete-build-output", "20260101_000000-" + "a" * 40],
}


@pytest.mark.parametrize("mode", sorted(MODES))
def test_dry_run_yes_never_applies_the_plan_and_exits_zero(estate, mode, capsys):
    rc = cli.main(["cleanup", *MODES[mode], "--dry-run", "--yes"])

    assert rc == 0, capsys.readouterr()
    assert len(_SpyPlan.created) == 1
    plan = _SpyPlan.created[0]
    # The preview really captured deletion actions (it is a real plan, not an
    # empty one), and none of them ran.
    assert plan.actions, f"{mode}: the preview captured no actions"


def test_remove_assets_refuses_a_project_target_before_touching_anything(estate, capsys):
    """CLI-05: the target used to be parsed and ignored (estate-wide deletion)."""
    rc = cli.main(["cleanup", "demo", "--remove-assets", "30d", "--yes"])

    assert rc == 2
    assert "--remove-assets applies estate-wide [cleanup] policy; omit the target" in (
        capsys.readouterr().err
    )
    assert not _SpyPlan.created or not _SpyPlan.created[0].actions


def test_a_bare_cleanup_names_the_required_modes_and_changes_nothing(estate, capsys):
    """Redesign B5: the most destructive mode is never the implicit default."""
    rc = cli.main(["cleanup", "--dry-run"])

    err = capsys.readouterr().err
    assert rc == 2
    for mode in ("--policy", "--remove-assets", "--delete-unmanaged-release-tag", "--delete-build-output"):
        assert mode in err
    assert not _SpyPlan.created


def test_cleanup_no_longer_discards_build_worktrees(estate, capsys):
    """Redesign B6: that object belongs to ``abandon [BRANCH|PATH]``."""
    rc = cli.main(["cleanup", "--discard-build-worktree", "/tmp/cmru-build-x", "--dry-run"])

    assert rc == 2
    # argparse refuses before any work; the mode list it prints has no such mode
    err = capsys.readouterr().err
    assert "--delete-build-output" in err and "--discard-build-worktree" not in err.split("--config")[0]


def test_without_dry_run_the_same_plan_is_applied(estate, monkeypatch):
    """Control: the spy is live, so a missing early return is observable."""
    with pytest.raises(AssertionError, match="plan.apply\\(\\) ran"):
        cli.main(["cleanup", "--remove-assets", "30d", "--yes"])

"""Tests for the generic ``cmru cleanup`` verb (P6).

Covers:
- ``cleanup_project_releases_and_tags``: keeps -latest + keep_release_tags; deletes the rest.
- ``run_cleanup_verb``: end-to-end wiring with a mocked GH client.
- ``--dry-run``: lists targets without deleting.
- Optional ``[steps.clean]`` invocation.
- Idempotency: missing Release/tag is not an error.

No network, no git side-effects: all external calls are patched.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, call, patch

import pytest

from cmru import cli


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_cleanup_config(
    keep_release_tags: List[str] | None = None,
    release_tag_prefixes: List[str] | None = None,
    ghcr_packages: List[str] | None = None,
    ghcr_delete_packages: List[str] | None = None,
) -> cli.CleanupConfig:
    return cli.CleanupConfig(
        keep_release_tags=keep_release_tags or [],
        release_tag_prefixes=release_tag_prefixes or ["*"],
        ghcr_packages=ghcr_packages or ["*"],
        ghcr_delete_packages=ghcr_delete_packages or [],
    )


def _make_github_config(token: str = "tok") -> cli.GitHubConfig:
    return cli.GitHubConfig(
        username="owner", repo="repo", token=token, owner_type="user"
    )


def _make_env_config() -> cli.ReleaseEnvConfig:
    return cli.ReleaseEnvConfig(env={}, registry_url=None)


def _make_project(
    name: str,
    prefix: str = "",
    steps: dict | None = None,
) -> cli.ProjectConfig:
    return cli.ProjectConfig(
        name=name,
        env={},
        steps=steps or {},
        prefix=prefix or f"{name}-v",
        cwd=name,
        artifacts=("wheel",),
    )


# Fake GitHub releases list: each entry is a minimal release dict.
def _release(tag: str, release_id: int) -> dict:
    return {"tag_name": tag, "id": release_id, "published_at": "2024-01-01T00:00:00Z"}


# ─── cleanup_project_releases_and_tags ───────────────────────────────────────

class TestCleanupProjectReleasesAndTags:
    """Unit-test the release+tag cleanup logic with patched network + git calls."""

    def _run(
        self,
        releases: List[dict],
        remote_tags: List[str],
        keep_tags: List[str],
        prefix: str = "ciu",
        dry_run: bool = False,
    ) -> tuple[list[str], list]:
        """Return (deleted_tags, delete_release_calls) from a cleanup run."""
        deleted_release_args: list = []

        def fake_list_releases(owner, repo, token):
            return releases

        def fake_list_remote(repo_root, pattern):
            # Return only tags matching the pattern prefix.
            pfx = pattern.rstrip("*")
            return [t for t in remote_tags if t.startswith(pfx)]

        def fake_delete_release(owner, repo, token, release_id, dry_run):
            deleted_release_args.append(release_id)

        def fake_delete_remote(repo_root, tag, dry_run):
            pass  # just track calls via mock below

        def fake_delete_local(repo_root, tag, dry_run):
            pass

        with (
            patch.object(cli, "list_releases", side_effect=fake_list_releases),
            patch.object(cli, "list_remote_tags_matching", side_effect=fake_list_remote),
            patch.object(cli, "delete_release", side_effect=fake_delete_release),
            patch.object(cli, "delete_git_tag_remote", side_effect=fake_delete_remote),
            patch.object(cli, "delete_git_tag_local", side_effect=fake_delete_local),
        ):
            deleted = cli.cleanup_project_releases_and_tags(
                repo_root=Path("/fake"),
                owner="owner", repo="repo", token="tok",
                prefix=prefix,
                keep_tags=keep_tags,
                dry_run=dry_run,
            )
        return deleted, deleted_release_args

    def test_keeps_latest_tag(self):
        """<prefix>-latest must never be deleted."""
        releases = [
            _release("ciu-latest", 1),
            _release("ciu-v1.0.0", 2),
            _release("ciu-v2.0.0", 3),
        ]
        deleted, deleted_ids = self._run(releases, [], keep_tags=[])
        # ciu-latest is always protected; only versioned ones deleted.
        assert 1 not in deleted_ids
        assert 2 in deleted_ids
        assert 3 in deleted_ids
        assert "ciu-latest" not in deleted

    def test_keeps_explicit_keep_release_tags(self):
        """Tags in keep_release_tags must survive."""
        releases = [
            _release("ciu-v1.0.0", 10),
            _release("ciu-v2.0.0", 20),
            _release("ciu-v3.0.0", 30),
        ]
        deleted, deleted_ids = self._run(
            releases, [], keep_tags=["ciu-v3.0.0"]
        )
        assert 30 not in deleted_ids, "keep_release_tags entry must not be deleted"
        assert 10 in deleted_ids
        assert 20 in deleted_ids

    def test_deletes_old_versioned_releases(self):
        """Old versioned releases not in keep list are deleted."""
        releases = [
            _release("ciu-v0.1.0", 1),
            _release("ciu-v0.2.0", 2),
        ]
        deleted, deleted_ids = self._run(releases, [], keep_tags=[])
        assert {1, 2} == set(deleted_ids)

    def test_dry_run_does_not_call_delete_release(self):
        """--dry-run must not call the underlying delete_release API (guarded by if not dry_run)."""
        releases = [_release("ciu-v1.0.0", 99)]
        deleted, deleted_ids = self._run(releases, [], keep_tags=[], dry_run=True)
        # The real delete_release is NOT called in dry-run mode (guarded by `if not dry_run:`).
        assert deleted_ids == []
        # The tag IS still listed in the return value (what would be deleted).
        assert "ciu-v1.0.0" in deleted

    def test_deletes_remote_stale_tags(self):
        """Remote tags without a matching Release are also cleaned up."""
        # No releases, but there is a stale remote tag.
        remote_tags = ["ciu-v0.0.1"]
        deleted, deleted_ids = self._run([], remote_tags, keep_tags=[])
        assert "ciu-v0.0.1" in deleted

    def test_stale_tag_in_keep_set_survives(self):
        """A remote tag listed in keep_tags must not be deleted."""
        remote_tags = ["ciu-v1.0.0", "ciu-v2.0.0"]
        deleted, _ = self._run([], remote_tags, keep_tags=["ciu-v2.0.0"])
        assert "ciu-v2.0.0" not in deleted
        assert "ciu-v1.0.0" in deleted

    def test_no_releases_no_remote_tags_is_noop(self):
        """Empty state is idempotent (no errors, nothing deleted)."""
        deleted, deleted_ids = self._run([], [], keep_tags=[])
        assert deleted == []
        assert deleted_ids == []

    def test_different_prefix_releases_not_touched(self):
        """Releases for a different prefix must not be deleted."""
        releases = [
            _release("cmru-v1.0.0", 100),  # different prefix
            _release("ciu-v1.0.0", 200),
        ]
        deleted, deleted_ids = self._run(releases, [], keep_tags=[], prefix="ciu")
        # Only ciu-v1.0.0 should be deleted; cmru-v1.0.0 is untouched.
        assert 100 not in deleted_ids
        assert 200 in deleted_ids


# ─── run_cleanup_verb ─────────────────────────────────────────────────────────

class TestRunCleanupVerb:
    """Integration-level test for run_cleanup_verb with fully patched externals."""

    def _run_verb(
        self,
        project_names: List[str],
        prefixes: dict[str, str],
        releases: List[dict],
        remote_tags: List[str],
        keep_tags: List[str],
        project_filter: str | None = None,
        dry_run: bool = False,
        with_clean_step: bool = False,
    ) -> dict:
        """Run the cleanup verb and return a dict of recorded calls.

        Note on dry_run semantics:
        - ``delete_release`` is NOT called (guarded by ``if not dry_run:`` in
          ``cleanup_project_releases_and_tags``).
        - ``delete_git_tag_remote`` / ``_local`` ARE called (they receive dry_run=True
          and early-return; we track the call so dry_run=True tests can assert on tags
          that *would* be deleted).
        - ``cleanup_project_step`` returns False when dry_run=True (real implementation).
        """
        projects = {}
        for name in project_names:
            prefix = prefixes.get(name, f"{name}-v")
            steps = {}
            if with_clean_step:
                steps["clean"] = [
                    cli.Command(label="clean", argv=["true"], cwd=Path("/fake"))
                ]
            projects[name] = _make_project(name, prefix=prefix, steps=steps)

        cleanup = _make_cleanup_config(keep_release_tags=keep_tags)
        github = _make_github_config()
        env_cfg = _make_env_config()

        calls_record: dict = {
            "release_deletes": [],
            "remote_tag_deletes": [],
            "local_tag_deletes": [],
            "clean_steps": [],
            "commits": [],
        }

        def fake_list_releases(owner, repo, token):
            return releases

        def fake_list_remote(repo_root, pattern):
            pfx = pattern.rstrip("*")
            return [t for t in remote_tags if t.startswith(pfx)]

        def fake_delete_release(owner, repo, token, release_id, dry_run=False):
            # Only called when dry_run=False (the caller guards it).
            calls_record["release_deletes"].append(release_id)

        def fake_delete_remote(repo_root, tag, dry_run=False):
            # Called regardless of dry_run; records the tag identified for deletion.
            calls_record["remote_tag_deletes"].append(tag)

        def fake_delete_local(repo_root, tag, dry_run=False):
            calls_record["local_tag_deletes"].append(tag)

        def fake_cleanup_project_step(repo_root, project, version, dry_run=False):
            # Mirror real behaviour: return False when dry_run=True.
            if dry_run:
                return False
            calls_record["clean_steps"].append(project.name)
            return True

        def fake_cleanup_commit(repo_root, name, deleted_tags, dry_run=False):
            calls_record["commits"].append(name)

        def fake_resolve_versions(*args, **kwargs):
            pass

        def fake_apply_env(*args, **kwargs):
            pass

        with (
            patch.object(cli, "list_releases", side_effect=fake_list_releases),
            patch.object(cli, "list_remote_tags_matching", side_effect=fake_list_remote),
            patch.object(cli, "delete_release", side_effect=fake_delete_release),
            patch.object(cli, "delete_git_tag_remote", side_effect=fake_delete_remote),
            patch.object(cli, "delete_git_tag_local", side_effect=fake_delete_local),
            patch.object(cli, "delete_package", return_value=None),
            patch.object(cli, "cleanup_project_step", side_effect=fake_cleanup_project_step),
            patch.object(cli, "cleanup_commit_deletions", side_effect=fake_cleanup_commit),
            patch.object(cli, "resolve_versions_from_git", side_effect=fake_resolve_versions),
            patch.object(cli, "apply_release_env", side_effect=fake_apply_env),
        ):
            cli.run_cleanup_verb(
                repo_root=Path("/fake"),
                configs=projects,
                project_order=project_names,
                cleanup=cleanup,
                github_config=github,
                env_config=env_cfg,
                project_filter=project_filter,
                dry_run=dry_run,
            )
        return calls_record

    def test_keeps_latest_deletes_old(self):
        """End-to-end: -latest kept, old versioned releases deleted."""
        releases = [
            _release("ciu-latest", 1),
            _release("ciu-v1.0.0", 2),
            _release("ciu-v2.0.0", 3),
        ]
        rec = self._run_verb(
            ["ciu"], {"ciu": "ciu-v"},
            releases, [], keep_tags=[],
        )
        assert 2 in rec["release_deletes"]
        assert 3 in rec["release_deletes"]
        assert 1 not in rec["release_deletes"]  # -latest kept

    def test_keep_release_tags_honoured(self):
        """keep_release_tags from CleanupConfig are preserved."""
        releases = [
            _release("ciu-v1.0.0", 10),
            _release("ciu-v2.0.0", 20),
        ]
        rec = self._run_verb(
            ["ciu"], {"ciu": "ciu-v"},
            releases, [], keep_tags=["ciu-v2.0.0"],
        )
        assert 10 in rec["release_deletes"]
        assert 20 not in rec["release_deletes"]

    def test_dry_run_no_deletes(self):
        """--dry-run: delete_release (actual API call) must NOT be invoked.

        delete_git_tag_remote/local ARE called with dry_run=True (they log and
        return without actually removing anything) — that is the intended design
        so that the caller can still see what would be deleted.
        """
        releases = [_release("ciu-v1.0.0", 99)]
        rec = self._run_verb(
            ["ciu"], {"ciu": "ciu-v"},
            releases, [], keep_tags=[], dry_run=True,
        )
        # The underlying delete_release API must NOT be called in dry-run mode.
        assert rec["release_deletes"] == []

    def test_dry_run_no_clean_step(self):
        """--dry-run must not invoke the clean step."""
        releases = [_release("ciu-v1.0.0", 99)]
        rec = self._run_verb(
            ["ciu"], {"ciu": "ciu-v"},
            releases, [], keep_tags=[], dry_run=True,
            with_clean_step=True,
        )
        assert rec["clean_steps"] == []

    def test_project_filter_limits_scope(self):
        """--project limits cleanup to one project."""
        releases = [
            _release("ciu-v1.0.0", 10),
            _release("cmru-v1.0.0", 20),
        ]
        rec = self._run_verb(
            ["ciu", "cmru"], {"ciu": "ciu-v", "cmru": "cmru-v"},
            releases, [], keep_tags=[], project_filter="ciu",
        )
        assert 10 in rec["release_deletes"]
        assert 20 not in rec["release_deletes"]

    def test_clean_step_invoked_when_present(self):
        """[steps.clean] is called when defined and there are deleted tags."""
        releases = [_release("ciu-v1.0.0", 50)]
        rec = self._run_verb(
            ["ciu"], {"ciu": "ciu-v"},
            releases, [], keep_tags=[],
            with_clean_step=True,
        )
        assert "ciu" in rec["clean_steps"]

    def test_commit_called_after_cleanup(self):
        """A commit is attempted after deletions (non-dry-run)."""
        releases = [_release("ciu-v1.0.0", 50)]
        rec = self._run_verb(
            ["ciu"], {"ciu": "ciu-v"},
            releases, [], keep_tags=[],
        )
        assert "ciu" in rec["commits"]

    def test_unknown_project_filter_raises(self):
        """An unknown --project name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown project"):
            self._run_verb(
                ["ciu"], {"ciu": "ciu-v"},
                [], [], keep_tags=[], project_filter="nonexistent",
            )

    def test_no_prefix_project_skipped(self):
        """A project with no prefix is skipped (no Release/tag to clean)."""
        projects = {"noprefix": cli.ProjectConfig(
            name="noprefix", env={}, steps={},
            prefix=None, cwd="noprefix", artifacts=("wheel",),
        )}
        # Should not raise; just logs a skip.
        cleanup = _make_cleanup_config()
        github = _make_github_config()
        env_cfg = _make_env_config()

        with (
            patch.object(cli, "list_releases", return_value=[]),
            patch.object(cli, "list_remote_tags_matching", return_value=[]),
            patch.object(cli, "delete_release"),
            patch.object(cli, "delete_package"),
            patch.object(cli, "resolve_versions_from_git"),
            patch.object(cli, "apply_release_env"),
        ):
            cli.run_cleanup_verb(
                repo_root=Path("/fake"),
                configs=projects,
                project_order=["noprefix"],
                cleanup=cleanup,
                github_config=github,
                env_config=env_cfg,
                project_filter=None,
                dry_run=False,
            )  # must not raise


# ─── delete_git_tag helpers ───────────────────────────────────────────────────

class TestDeleteGitTagHelpers:
    """Unit tests for the git-tag helper stubs (dry-run path, no real git)."""

    def test_delete_git_tag_remote_dry_run(self, tmp_path, capsys):
        cli.delete_git_tag_remote(tmp_path, "ciu-v1.0.0", dry_run=True)
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "ciu-v1.0.0" in out

    def test_delete_git_tag_local_dry_run(self, tmp_path, capsys):
        cli.delete_git_tag_local(tmp_path, "ciu-v1.0.0", dry_run=True)
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "ciu-v1.0.0" in out


# ─── _latest_version_for_prefix (CMRU_VERSION for steps.clean) ────────────────

class TestLatestVersionForPrefix:
    """The clean-step version resolver must return the highest surviving semver."""

    def _patch_releases(self, releases):
        return patch.object(cli, "list_releases", side_effect=lambda o, r, t: releases)

    def test_highest_semver_wins(self):
        releases = [
            {"tag_name": "ciu-v3.0.0"},
            {"tag_name": "ciu-v3.1.0"},
            {"tag_name": "ciu-v3.0.9"},
            {"tag_name": "ciu-latest"},          # thin pointer — ignored (no -v)
            {"tag_name": "other-v9.9.9"},        # different prefix — ignored
        ]
        with self._patch_releases(releases):
            assert cli._latest_version_for_prefix("o", "r", "t", "ciu") == "3.1.0"

    def test_non_empty_when_release_exists(self):
        # Regression for the punch-list MAJOR: CMRU_VERSION must NOT be "".
        with self._patch_releases([{"tag_name": "ciu-v1.0.0"}]):
            assert cli._latest_version_for_prefix("o", "r", "t", "ciu") == "1.0.0"

    def test_empty_when_no_matching_release(self):
        with self._patch_releases([{"tag_name": "ciu-latest"}, {"tag_name": "x-v1.0.0"}]):
            assert cli._latest_version_for_prefix("o", "r", "t", "ciu") == ""

    def test_drafts_and_prereleases_ignored(self):
        releases = [
            {"tag_name": "ciu-v2.0.0", "draft": True},
            {"tag_name": "ciu-v1.9.0", "prerelease": True},
            {"tag_name": "ciu-v1.0.0"},
        ]
        with self._patch_releases(releases):
            assert cli._latest_version_for_prefix("o", "r", "t", "ciu") == "1.0.0"


# ─── cleanup_commit_deletions ─────────────────────────────────────────────────

class TestCleanupCommitDeletions:
    """Verify that the commit helper skips when there is nothing to commit."""

    def test_no_commit_when_dry_run(self, tmp_path):
        """dry_run=True must not run git commit."""
        with patch("subprocess.run") as mock_run:
            cli.cleanup_commit_deletions(tmp_path, "ciu", ["ciu-v1.0.0"], dry_run=True)
            mock_run.assert_not_called()

    def test_no_commit_when_no_deleted_tags(self, tmp_path):
        """Nothing deleted → nothing to commit."""
        with patch("subprocess.run") as mock_run:
            cli.cleanup_commit_deletions(tmp_path, "ciu", [], dry_run=False)
            mock_run.assert_not_called()

    def test_no_empty_commit_when_tree_clean(self, tmp_path):
        """If the working tree is clean after cleanup, no commit is made."""
        with (
            patch.object(cli, "_git", return_value=None),  # no dirty files
            patch("subprocess.run") as mock_run,
        ):
            cli.cleanup_commit_deletions(tmp_path, "ciu", ["ciu-v1.0.0"], dry_run=False)
            mock_run.assert_not_called()

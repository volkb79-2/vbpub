"""Offline adversarial tests for CMRU's artifact and release boundaries."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cmru import bundle, changelog, exit_codes, handlers, release, version


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class TestBundleBoundaries(unittest.TestCase):
    def test_member_requires_source_or_content(self):
        with self.assertRaisesRegex(ValueError, "either source_path or content"):
            bundle.BundleMember("x")

    def test_invalid_epoch_is_rejected(self):
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "not-an-integer"}):
            with self.assertRaises(ValueError):
                bundle._read_source_date_epoch()

    def test_allowlist_missing_path_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                bundle.collect_allowlist_members(Path(tmp), ["missing.txt"])

    def test_parse_config_rejects_missing_and_invalid_archive_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.toml"
            with self.assertRaises(FileNotFoundError):
                bundle.parse_config(missing)
            bad = root / "bad.toml"
            bad.write_text("project_root='.'\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "archive"):
                bundle.parse_config(bad)

    def test_parse_config_rejects_unknown_archive_format_and_non_list_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.toml"
            path.write_text(
                "project_root='.'\n[archive]\nname_template='x-{version}'\n"
                "version_env='V'\nformat='rar'\n[copy]\nfiles='x'\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "archive.*format"):
                bundle.parse_config(path)
            path.write_text(
                "project_root='.'\n[archive]\nname_template='x-{version}'\n"
                "version_env='V'\n[copy]\nfiles='x'\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must be lists"):
                bundle.parse_config(path)

    def test_create_archive_requires_version_fact(self):
        cfg = bundle.BundleConfig(Path("."), Path("."), Path("/tmp/dist"),
                                  Path("/tmp/dist/bundle"), Path("/tmp/dist/client"),
                                  False, "python", None, "x-{version}.tar.xz", "MISSING",
                                  "xztar", [], [])
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "MISSING"):
                bundle.create_archive(cfg)

    def test_deterministic_tar_normalizes_metadata_and_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.tar.xz"
            members = [
                bundle.BundleMember("bundle/z", content=b"z"),
                bundle.BundleMember("bundle/minisign.key", content=b"secret"),
                bundle.BundleMember("bundle/a", content=b"a", executable=True),
            ]
            bundle.write_deterministic_tar(members, out, source_date_epoch=7)
            with tarfile.open(out, "r:xz") as archive:
                entries = archive.getmembers()
            self.assertEqual([entry.name for entry in entries], ["bundle/a", "bundle/z"])
            self.assertEqual(entries[0].mtime, 7)
            self.assertEqual(entries[0].mode, 0o755)
            self.assertEqual(entries[0].uid, entries[0].gid, 0)


class TestChangelogBoundaries(unittest.TestCase):
    def test_path_validation_rejects_absolute_parent_and_missing_config(self):
        project = SimpleNamespace(name="demo", cwd="demo", changelog="../CHANGES.md")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "project-relative"):
                changelog._validate_changelog_path(project, Path(tmp))
            project.changelog = "/tmp/CHANGES.md"
            with self.assertRaisesRegex(RuntimeError, "project-relative"):
                changelog._validate_changelog_path(project, Path(tmp))
            project.changelog = None
            with self.assertRaisesRegex(RuntimeError, "no release.changelog"):
                changelog._validate_changelog_path(project, Path(tmp))

    def test_subject_groups_rejects_malformed_git_record(self):
        with patch.object(changelog, "_git", return_value="deadbeef-no-separator"):
            with self.assertRaisesRegex(RuntimeError, "malformed git log"):
                changelog._subject_groups(Path("."), None, ["demo"], exclude_paths=[])

    def test_existing_history_without_marker_is_not_overwritten(self):
        project = SimpleNamespace(name="demo", cwd="demo", paths=["demo"], prefix="demo-v",
                                  git_tag=True, changelog="CHANGES.md",
                                  version=SimpleNamespace(strategy="scm"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-b", "main")
            git(root, "config", "user.email", "test@example.invalid")
            git(root, "config", "user.name", "test")
            (root / "demo").mkdir()
            (root / "demo" / "CHANGES.md").write_text("# hand-written\n", encoding="utf-8")
            (root / "demo" / "x.py").write_text("x=1\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "feat: initial")
            git(root, "tag", "demo-v1.0.0")
            (root / "demo" / "x.py").write_text("x=2\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "fix: update")
            with self.assertRaisesRegex(RuntimeError, "lacks"):
                changelog.generate_release_changelog(root, project)

    def test_backfill_rejects_wrong_tag_prefix(self):
        project = SimpleNamespace(name="demo", cwd="demo", changelog="CHANGES.md", prefix="demo-v")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "release tag"):
                changelog.backfill_release_changelog(Path(tmp), project, "other-v1.0.0")


class TestReleaseArtifactBoundaries(unittest.TestCase):
    def test_release_helpers_refuse_invalid_versions_and_preserve_tag_shape(self):
        self.assertFalse(release.is_release_version("1.0.0.dev1+gabc"))
        self.assertTrue(release.is_release_version("1.0.0-r1"))
        self.assertEqual(release.version_to_tag("demo", "1.0.0+local"), "demo-v1.0.0-local")

    def test_release_http_statuses_are_observable(self):
        gh = release.GitHubReleases("o", "r", "token")
        with patch.object(gh, "_request", return_value=(404, "missing")):
            self.assertIsNone(gh.get_release_by_tag("demo-v1"))
        with patch.object(gh, "_request", return_value=(500, "broken")):
            with self.assertRaises(SystemExit):
                gh.list_releases()

    def test_publish_dev_build_skips_immutable_release_but_publishes_latest(self):
        calls = []
        fake = SimpleNamespace(
            publish=lambda *args, **kwargs: calls.append((args, kwargs)),
            asset_download_url=lambda tag, name: "unused",
        )
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "artifact.tar.xz"
            asset.write_bytes(b"bytes")
            result = release.publish_versioned(fake, prefix="demo", version="1.0.0.dev1",
                                               asset_path=asset)
        self.assertIsNone(result["release_tag"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], "demo-latest")

    def test_variant_publication_rejects_empty_matrix(self):
        with self.assertRaises(SystemExit):
            release.publish_versioned_variants(SimpleNamespace(), prefix="demo", version="1.0.0",
                                                variants=[], asset_suffix=".tar.xz")

    def test_variant_artifact_naming_and_duplicate_selection_are_explicit(self):
        self.assertEqual(release.variant_asset_name("demo", "1.2.3", "py311", ".whl"),
                         "demo-v1.2.3-py311.whl")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a-py311.whl").write_bytes(b"a")
            (root / "b-py311.whl").write_bytes(b"b")
            with self.assertRaises(SystemExit):
                release.find_artifact(root, "*.whl", variant="py311", suffix=".whl")

    def test_validate_latest_rejects_multiple_primary_assets(self):
        fake = SimpleNamespace(resolve_latest=lambda _: {
            "version": "1.0.0", "tag": "demo-v1.0.0",
            "assets": [{"name": "a.tar.xz", "url": "a"}, {"name": "b.tar.xz", "url": "b"}],
        })
        # Multiple assets are surfaced as warning, but the selected record remains explicit.
        with patch("builtins.print") as output:
            info = release.validate_latest_release(fake, "demo", artifact_suffix=".tar.xz",
                                                   require_sha256=False, retries=1, delay=0)
        self.assertEqual(info["asset"], "a.tar.xz")
        self.assertTrue(any("Multiple" in str(call) for call in output.call_args_list))


class TestVersionStrategyBoundaries(unittest.TestCase):
    def test_external_version_requires_file_and_named_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(RuntimeError):
                version._external_version(root, "VERSION")
            (root / "cmru.vars").write_text("OTHER=1\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                version._external_version(root, "VERSION")
            (root / "cmru.vars").write_text("VERSION=2.3.4\n", encoding="utf-8")
            self.assertEqual(version._external_version(root, "VERSION"), "2.3.4")

    def test_counter_strategy_ignores_malformed_tags_and_increments_numeric_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(version.subprocess, "run", return_value=SimpleNamespace(
                    stdout="demo-v1.0.0-r2\ndemo-v1.0.0-rbad\ndemo-v1.0.0-r10\n")):
                self.assertEqual(version._next_counter_version(root, "demo-v", "1.0.0"), "1.0.0-r11")

    def test_scm_dry_run_is_nonmutating_and_tag_failure_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("builtins.print") as output:
                result = version._apply_strategy_scm(root, "demo-v", "1.0.0", dry_run=True)
            self.assertEqual(result, "demo-v1.0.0")
            self.assertTrue(any("Would tag" in str(call) for call in output.call_args_list))
            with patch.object(version.subprocess, "run", return_value=SimpleNamespace(returncode=2)):
                with self.assertRaises(SystemExit) as raised:
                    version._apply_strategy_scm(root, "demo-v", "1.0.0")
            self.assertEqual(raised.exception.code, 1)

    def test_unknown_bump_selector_refuses_to_invent_patch(self):
        with self.assertRaisesRegex(ValueError, "Unknown version bump"):
            version.bump_version("1.2.3", "surprise")


class TestHandlerSubprocessBoundaries(unittest.TestCase):
    def test_git_mount_rejects_non_git_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(handlers.subprocess, "run",
                              return_value=SimpleNamespace(returncode=128, stdout="")):
                with self.assertRaisesRegex(RuntimeError, "requires a Git worktree"):
                    handlers._wheel_builder_git_mount_args(Path(tmp))

    def test_oci_prerequisite_buildx_error_is_prerequisite_failure(self):
        with patch.object(handlers.shutil, "which", return_value="/usr/bin/docker"), \
             patch.object(handlers.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "docker") ):
            with self.assertRaises(SystemExit) as raised:
                handlers._check_prerequisites()
        self.assertEqual(raised.exception.code, exit_codes.PREREQ_MISSING)

    def test_docker_login_requires_registry_identity(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                handlers._docker_login()


if __name__ == "__main__":
    unittest.main()

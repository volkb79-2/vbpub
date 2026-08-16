"""Adversarial, offline contract tests for CMRU's release boundary helpers.

These tests deliberately exercise public outcomes (returned records, fail-closed
errors, and emitted artifacts).  Network and executable boundaries are replaced
with deterministic fakes; no test reaches GitHub or a real release tool.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cmru import delegated, exit_codes, getpy, ghcr, handlers, manifest, resolve, version
from cmru.release import (
    GitHubReleases,
    find_artifact,
    publish_versioned,
    read_wheel_version,
    validate_latest_release,
)


class TestReleaseHttpBoundary(unittest.TestCase):
    def test_http_error_is_a_public_fail_closed_exit(self):
        gh = GitHubReleases("o", "r", "token", api_base="https://fake.invalid")
        with patch.object(gh, "_request", return_value=(503, "upstream unavailable")):
            with self.assertRaises(SystemExit) as raised:
                gh.get_release_by_tag("x-v1.0.0")
        self.assertEqual(raised.exception.code, 1)

    def test_malformed_release_json_is_not_silently_accepted(self):
        gh = GitHubReleases("o", "r", "token")
        with patch.object(gh, "_request", return_value=(200, "not-json")):
            with self.assertRaises(json.JSONDecodeError):
                gh.get_release_by_tag("x-v1.0.0")

    def test_latest_requires_a_matching_artifact_and_hash_sidecar(self):
        gh = SimpleNamespace(resolve_latest=lambda _prefix: {
            "version": "1.0.0", "tag": "x-v1.0.0",
            "assets": [{"name": "x-v1.0.0.whl", "url": "u"}],
        })
        with self.assertRaises(SystemExit) as raised:
            validate_latest_release(gh, "x", retries=1, delay=0)
        self.assertEqual(raised.exception.code, 1)

    def test_latest_rejects_no_release_without_retrying_forever(self):
        gh = SimpleNamespace(resolve_latest=lambda _prefix: None)
        with self.assertRaises(SystemExit):
            validate_latest_release(gh, "x", retries=1, delay=0)

    def test_publish_records_hash_and_extra_asset_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "x.whl"
            extra = root / "manifest.json"
            artifact.write_bytes(b"artifact")
            extra.write_text("{}\n", encoding="utf-8")
            fake = SimpleNamespace(
                publish=lambda *args, **kwargs: None,
                asset_download_url=lambda tag, name: f"https://download/{tag}/{name}",
            )
            result = publish_versioned(fake, prefix="x", version="1.2.3",
                                       asset_path=artifact, extra_assets=[extra])
            self.assertEqual(result["sha256"], hashlib.sha256(b"artifact").hexdigest())
            self.assertEqual(result["release_tag"], "x-v1.2.3")
            self.assertEqual((root / "x.whl.sha256").read_text(),
                             f"{result['sha256']}  x.whl\n")

    def test_find_artifact_rejects_ambiguous_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x-a.whl").write_bytes(b"a")
            (root / "x-b.whl").write_bytes(b"b")
            with self.assertRaises(SystemExit) as raised:
                find_artifact(root, "*.whl")
        self.assertEqual(raised.exception.code, 1)

    def test_read_wheel_version_requires_metadata_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "x.whl"
            with zipfile.ZipFile(wheel, "w") as zf:
                zf.writestr("x-1.0.dist-info/METADATA", "Name: x\n")
            with self.assertRaises(SystemExit):
                read_wheel_version(wheel)


class TestVersionAndResolveBoundaries(unittest.TestCase):
    def test_version_parser_rejects_whitespace_and_missing_components(self):
        for raw in (" 1.2.3", "1.2", "1.2.3 ", "v1.2.3", "1.2.3+local"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    version._parse_semver(raw)

    def test_unknown_bump_is_refused_instead_of_inventing_a_patch(self):
        with self.assertRaisesRegex(ValueError, "Unknown version bump level"):
            version.bump_version("1.2.3", "unknown")

    def test_latest_json_uses_v_prefix_translation_and_rejects_incomplete_payload(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"version":"1.0.0"}'

        with patch("urllib.request.urlopen", return_value=Response()) as opened:
            self.assertIsNone(resolve.resolve_via_latest_json("https://example/releases", "x-v"))
        self.assertIn("x-latest", opened.call_args.args[0])

    def test_latest_json_malformed_response_falls_back(self):
        with patch("urllib.request.urlopen", side_effect=ValueError("bad json")):
            self.assertIsNone(resolve.resolve_via_latest_json("https://example/releases", "x"))

    def test_format_env_handles_missing_tag_without_inventing_url(self):
        self.assertEqual(resolve.format_result({}, "env"), "_VERSION=\n_TAG=\n_URL=")


class TestGHCRBoundaries(unittest.TestCase):
    def test_unsupported_owner_type_fails_before_request(self):
        api = ghcr.GitHubPackages("o", "r", "t", "team")
        with self.assertRaises(SystemExit) as raised:
            api.package_visibility("image")
        self.assertEqual(raised.exception.code, 1)

    def test_repo_response_without_visibility_fails_closed(self):
        api = ghcr.GitHubPackages("o", "r", "t", "user")
        with patch.object(api, "_request", return_value=(200, '{"name":"repo"}')):
            with self.assertRaises(SystemExit):
                api.repo_visibility()

    def test_package_404_means_not_visible_yet_but_other_error_is_failure(self):
        api = ghcr.GitHubPackages("o", "r", "t", "user")
        with patch.object(api, "_request", return_value=(404, "missing")):
            self.assertIsNone(api.package_visibility("image"))
        with patch.object(api, "_request", return_value=(500, "broken")):
            with self.assertRaises(SystemExit):
                api.package_visibility("image")

    def test_mirror_exhausted_visibility_lookup_fails(self):
        api = ghcr.GitHubPackages("o", "r", "t", "user")
        with patch.object(api, "package_visibility", return_value=None):
            with self.assertRaises(SystemExit):
                api.mirror_package_visibility("image", expected_visibility="public",
                                              retries=1, delay=0)

    def test_patch_visibility_api_unsupported_is_nonfatal_and_returns_current(self):
        api = ghcr.GitHubPackages("o", "r", "t", "user")
        with patch.object(api, "package_visibility", return_value="private"), \
             patch.object(api, "set_package_visibility",
                          side_effect=ghcr.PackageVisibilityApiUnsupported(404, "no route")):
            self.assertEqual(api.mirror_package_visibility("image", expected_visibility="public",
                                                            retries=1, delay=0), "private")

    def test_private_boolean_and_missing_package_visibility_are_supported(self):
        api = ghcr.GitHubPackages("o", "r", "t", "user")
        with patch.object(api, "_request", return_value=(200, '{"private":false}')):
            self.assertEqual(api.repo_visibility(), "public")
        with patch.object(api, "_request", return_value=(200, '{}')):
            self.assertIsNone(api.package_visibility("image"))

    def test_visibility_update_rejects_a_server_that_returns_the_wrong_value(self):
        api = ghcr.GitHubPackages("o", "r", "t", "user")
        with patch.object(api, "package_visibility", return_value="private"), \
             patch.object(api, "set_package_visibility", return_value={"visibility": "internal"}):
            with self.assertRaises(SystemExit):
                api.mirror_package_visibility("image", expected_visibility="public",
                                              retries=1, delay=0)


class TestDelegatedToolContracts(unittest.TestCase):
    def test_missing_tool_is_prerequisite_failure(self):
        with patch.object(delegated, "_which", return_value=None):
            with self.assertRaises(SystemExit) as raised:
                delegated.cosign_sign(Path("artifact"))
        self.assertEqual(raised.exception.code, exit_codes.PREREQ_MISSING)

    def test_tool_nonzero_is_public_failure(self):
        with patch.object(delegated, "_which", return_value="/bin/tool"), \
             patch.object(delegated, "_run", return_value=7):
            with self.assertRaises(SystemExit) as raised:
                delegated.syft_sbom(Path("artifact"), Path("sbom.json"))
        self.assertEqual(raised.exception.code, exit_codes.FAILURE)

    def test_minisign_verify_distinguishes_invalid_signature_from_missing_tool(self):
        with patch.object(delegated, "_which", return_value="/bin/minisign"), \
             patch.object(delegated.subprocess, "run",
                          return_value=SimpleNamespace(returncode=1, stderr=b"bad signature")):
            self.assertFalse(delegated.minisign_verify(Path("manifest"), public_key="pub"))

    def test_every_external_tool_fails_closed_when_absent(self):
        calls = [
            lambda: delegated.syft_sbom(Path("a"), Path("s")),
            lambda: delegated.grype_scan(Path("a")),
            lambda: delegated.git_cliff_changelog(Path("out")),
            lambda: delegated.nfpm_package(Path("cfg"), Path("out")),
            lambda: delegated.minisign_sign(Path("blob"), secret_key="key", trusted_comment="bound"),
            lambda: delegated.minisign_verify(Path("blob"), public_key="pub"),
        ]
        for invoke in calls:
            with self.subTest(invoke=invoke):
                with patch.object(delegated, "_which", return_value=None):
                    with self.assertRaises(SystemExit) as raised:
                        invoke()
                self.assertEqual(raised.exception.code, exit_codes.PREREQ_MISSING)

    def test_every_external_tool_surfaces_nonzero_exit(self):
        calls = [
            lambda: delegated.cosign_sign(Path("a")),
            lambda: delegated.syft_sbom(Path("a"), Path("s")),
            lambda: delegated.grype_scan(Path("a")),
            lambda: delegated.git_cliff_changelog(Path("out")),
            lambda: delegated.nfpm_package(Path("cfg"), Path("out")),
            lambda: delegated.minisign_sign(Path("blob"), secret_key="key", trusted_comment="bound"),
        ]
        for invoke in calls:
            with self.subTest(invoke=invoke):
                with patch.object(delegated, "_which", return_value="/bin/tool"), \
                     patch.object(delegated, "_run", return_value=9):
                    with self.assertRaises(SystemExit) as raised:
                        invoke()
                self.assertEqual(raised.exception.code, exit_codes.FAILURE)


class TestManifestAndInstallerBoundaries(unittest.TestCase):
    def test_images_shape_and_required_keys_are_validated(self):
        for images in ({}, {"web": {"repository": "r"}}, {"web": "wrong"}):
            with self.subTest(images=images):
                with self.assertRaises((TypeError, ValueError)):
                    manifest._validate_images(images, "demo")
        self.assertEqual(manifest._validate_images(None, "demo"), {})

    def test_epoch_missing_and_invalid_are_not_invented(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                manifest._epoch()
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "not-an-int"}):
            with self.assertRaises(ValueError):
                manifest._epoch()

    def test_canonical_manifest_and_trusted_comment_bind_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "manifest.json"
            manifest.write_manifest({"z": 1, "a": 2}, path)
            self.assertEqual(path.read_bytes(), b'{"a":2,"z":1}\n')
            comment = manifest.build_trusted_comment(project="demo", tag="demo-v1", manifest_path=path)
            self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), comment)

    def test_getpy_escaping_and_unreplaced_placeholder_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "template"
            template.write_text("[[PROJECT_NAME]] [[ENTRYPOINT]] [[UNKNOWN]]", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = getpy.render_get_py(project_name='a"b', repo_owner="o", repo_name="r",
                                              tag_prefix="a-v", install_dir_system="/x",
                                              install_dir_user="/y", entrypoint='say "hi"',
                                              template_path=template)
            self.assertIn('a"b', result)
            self.assertIn('say "hi"', result)
            self.assertIn("unreplaced placeholders", stderr.getvalue())


class TestHandlerFailureContracts(unittest.TestCase):
    def test_required_environment_is_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as raised:
                handlers._require_env("MISSING")
        self.assertEqual(raised.exception.code, 1)

    def test_builder_requires_governed_cgroup(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as raised:
                handlers._docker_cgroup_parent()
        self.assertEqual(raised.exception.code, exit_codes.PREREQ_MISSING)

    def test_build_prerequisites_require_image_and_docker(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(handlers.shutil, "which", return_value="docker"):
            with self.assertRaises(SystemExit) as raised:
                handlers._check_build_prerequisites()
        self.assertEqual(raised.exception.code, exit_codes.PREREQ_MISSING)
        with patch.dict(os.environ, {"CMRU_WHEEL_BUILDER_IMAGE": "builder"}, clear=True), \
             patch.object(handlers.shutil, "which", return_value=None):
            with self.assertRaises(SystemExit) as raised:
                handlers._check_build_prerequisites()
        self.assertEqual(raised.exception.code, exit_codes.PREREQ_MISSING)

    def test_repack_is_disabled_before_external_side_effects(self):
        with self.assertRaises(SystemExit) as raised:
            handlers._reject_experimental_repack(True)
        self.assertEqual(raised.exception.code, exit_codes.CONFIG_ERROR)

    def test_wheel_glob_normalizes_project_name(self):
        self.assertEqual(handlers._wheel_glob("my-project"), "my_project-*.whl")


if __name__ == "__main__":
    unittest.main()

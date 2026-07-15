from __future__ import annotations

import subprocess
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts import manifest_sections


ROOT = Path(__file__).resolve().parents[1]


class ReleaseFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = tomllib.loads((ROOT / "cmru.build.toml").read_text())
        cls.env = cls.config["env"]

    def test_direct_push_is_the_safe_release_default(self) -> None:
        self.assertEqual(self.env["RELEASE_IMAGE_FLOW"], "push")
        source = (ROOT / "build-push.py").read_text()
        self.assertIn('or "push"', source)
        self.assertIn("if explicit_build_date:", source)
        self.assertIn("build_date = explicit_build_date", source)

    def test_registry_release_uses_native_zstd_and_standard_attestations(self) -> None:
        self.assertEqual(self.env["IMAGE_COMPRESSION"], "zstd")
        self.assertEqual(self.env["IMAGE_COMPRESSION_LEVEL"], "3")
        self.assertEqual(self.env["IMAGE_FORCE_COMPRESSION"], "true")
        self.assertEqual(self.env["IMAGE_OCI_MEDIA_TYPES"], "true")
        self.assertEqual(self.env["IMAGE_PROVENANCE_MODE"], "max")
        self.assertEqual(self.env["IMAGE_SBOM"], "true")
        wrapper = (ROOT / "scripts/release-bake.sh").read_text()
        self.assertIn("type=registry,compression=${IMAGE_COMPRESSION}", wrapper)
        self.assertIn("attest=type=provenance,mode=${IMAGE_PROVENANCE_MODE}", wrapper)
        self.assertIn("attest+=type=sbom", wrapper)

    def test_builder_and_repack_limits_are_configured(self) -> None:
        self.assertTrue(self.env["BUILDX_BUILDER"])
        for key in (
            "MDT_BUILDER_MEMORY",
            "MDT_BUILDER_MEMORY_SWAP",
            "MDT_BUILDER_CPU_SHARES",
            "MDT_BUILDER_CPU_QUOTA",
            "MDT_BUILDER_CPU_PERIOD",
            "REPACK_JOBS",
            "REPACK_CONCURRENCY",
            "REPACK_VMEM_KB",
            "REPACK_KEEP_FAILED",
        ):
            self.assertTrue(self.env[key], key)

        builder = (ROOT / "scripts/ensure-release-builder.sh").read_text()
        self.assertIn('driver}" != "docker-container"', builder)
        self.assertIn('actual_memory}" != "${expected_memory}', builder)
        self.assertIn('docker buildx rm "${BUILDER}"', builder)

    def test_active_repack_path_is_oci_native(self) -> None:
        bake = (ROOT / "scripts/release-bake.sh").read_text()
        repack = (ROOT / "scripts/release-repack.sh").read_text()
        push_dockerfile = (ROOT / "scripts/repack-push.Dockerfile").read_text()
        self.assertIn("type=oci", bake)
        self.assertIn("unique_by([.digest", bake)
        self.assertIn("oci-layout://", repack)
        self.assertIn("--target manifest", repack)
        self.assertIn("validate-oci-layout.py", repack)
        self.assertIn("FROM scratch AS manifest", push_dockerfile)
        self.assertNotIn("run_low_priority skopeo", repack)
        self.assertNotIn("docker-daemon:", repack)

    def test_published_manifest_extraction_avoids_daemon_image_store(self) -> None:
        source = (ROOT / "build-push.py").read_text()
        self.assertIn("repacked=docker-image://", source)
        self.assertNotIn('["docker", "run", "--rm", first_tag', source)

    def test_volatile_oci_labels_follow_filesystem_work(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertGreater(
            dockerfile.rfind("LABEL org.opencontainers.image.title"),
            dockerfile.rfind("\nRUN "),
        )

    def test_agent_inventory_template_is_in_image_context(self) -> None:
        dockerignore = (ROOT / ".dockerignore").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()
        template = (ROOT / "templates/AGENTS.md").read_text()
        self.assertIn("!templates/AGENTS.md", dockerignore)
        self.assertIn(
            "COPY templates/AGENTS.md "
            "/usr/local/share/modern-debian-tools-python-debug/AGENTS.md.example",
            dockerfile,
        )
        self.assertIn("IMAGE_MANIFEST", template)
        self.assertIn("installed-tools-manifest.md", template)

    def test_human_manifest_uses_variant_tags_and_image_kind(self) -> None:
        rendered = manifest_sections.render_unified_manifest(
            source_manifest_content="",
            debian_version="trixie",
            python_version="3.14",
            image_version="20260715",
            devcontainers_release="",
            devcontainers_version="",
            custom_tooling={},
            python_packages=[],
            system_packages=[],
            args_extra={
                "variant": "php8.5",
                "package_name": "modern-debian-tools-python-debug",
                "username": "example",
                "repo": "repo",
                "built_at": "2026-07-15T03:00:00Z",
            },
        )
        self.assertIn("# Image Manifest — trixie-py3.14-php8.5-20260715", rendered)
        self.assertIn("trixie-py3.14-php8.5-latest", rendered)
        self.assertIn("docker pull ghcr.io/example/modern-debian-tools-python-debug:trixie-py3.14-php8.5-20260715", rendered)
        self.assertNotIn("Target: `unknown`", rendered)
        self.assertNotIn("Devcontainers release: unknown", rendered)
        self.assertIn("Built at (UTC): `2026-07-15T03:00:00Z`", rendered)

    def test_image_identity_is_exposed_in_os_release(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        build_push = (ROOT / "build-push.py").read_text()
        self.assertIn('MDT_IMAGE_TAG="%s"', dockerfile)
        self.assertIn('MDT_IMAGE_REF="%s"', dockerfile)
        self.assertIn('MDT_IMAGE_CREATED="%s"', dockerfile)
        self.assertIn('MDT_IMAGE_BUILT_AT="%s"', dockerfile)
        self.assertIn('_image_ref="${_registry}/', dockerfile)
        self.assertIn('env_vars["BUILD_TIMESTAMP"] = build_timestamp', build_push)
        self.assertIn('--built-at "${BUILD_TIMESTAMP}"', dockerfile)

    def test_installed_manifest_uses_variant_tag(self) -> None:
        rendered = manifest_sections.render_installed_manifest(
            debian_version="trixie",
            python_version="3.14",
            image_version="20260715",
            devcontainers_release="",
            devcontainers_version="",
            custom_tooling={},
            python_packages=[],
            system_packages=[],
            built_at="2026-07-15T03:00:00Z",
            variant="php8.5",
        )
        self.assertIn("Image tag: trixie-py3.14-php8.5-20260715", rendered)
        self.assertIn("Built at (UTC): 2026-07-15T03:00:00Z", rendered)

    def test_template_tracks_php_development_lane(self) -> None:
        template = (ROOT / "templates/devcontainer.json").read_text()
        self.assertIn(":trixie-py3.14-php8.5-latest", template)

    def test_human_manifest_labels_vsc_package_as_devcontainer(self) -> None:
        rendered = manifest_sections.render_unified_manifest(
            source_manifest_content="",
            debian_version="trixie",
            python_version="3.14",
            image_version="20260715",
            devcontainers_release="v1",
            devcontainers_version="2",
            custom_tooling={},
            python_packages=[],
            system_packages=[],
            args_extra={
                "package_name": "modern-debian-tools-python-debug-vsc-devcontainer"
            },
        )
        self.assertIn("# Devcontainer Manifest — trixie-py3.14-20260715", rendered)
        self.assertIn("Devcontainers release: v1", rendered)

    def test_release_shell_scripts_parse(self) -> None:
        scripts = [
            ROOT / "scripts/ensure-release-builder.sh",
            ROOT / "scripts/release-bake.sh",
            ROOT / "scripts/release-repack.sh",
        ]
        subprocess.run(["bash", "-n", *map(str, scripts)], check=True)

    def test_oci_validator_rejects_file_with_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = Path(temp)
            blobs = layout / "blobs" / "sha256"
            blobs.mkdir(parents=True)
            (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')

            layer_stream = io.BytesIO()
            with tarfile.open(fileobj=layer_stream, mode="w") as archive:
                parent = tarfile.TarInfo("conflict")
                parent.size = 1
                archive.addfile(parent, io.BytesIO(b"x"))
                child = tarfile.TarInfo("conflict/child")
                child.size = 1
                archive.addfile(child, io.BytesIO(b"y"))
            layer = layer_stream.getvalue()
            layer_digest = hashlib.sha256(layer).hexdigest()
            (blobs / layer_digest).write_bytes(layer)

            config = b"{}"
            config_digest = hashlib.sha256(config).hexdigest()
            (blobs / config_digest).write_bytes(config)
            manifest = json.dumps(
                {
                    "schemaVersion": 2,
                    "config": {"digest": f"sha256:{config_digest}", "size": len(config)},
                    "layers": [{"digest": f"sha256:{layer_digest}", "size": len(layer)}],
                }
            ).encode()
            manifest_digest = hashlib.sha256(manifest).hexdigest()
            (blobs / manifest_digest).write_bytes(manifest)
            (layout / "index.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "manifests": [
                            {"digest": f"sha256:{manifest_digest}", "size": len(manifest)}
                        ],
                    }
                )
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/validate-oci-layout.py"), str(layout)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("non-directory path has descendants", result.stderr)


if __name__ == "__main__":
    unittest.main()

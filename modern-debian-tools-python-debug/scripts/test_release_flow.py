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

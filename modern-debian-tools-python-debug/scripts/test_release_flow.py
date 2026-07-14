from __future__ import annotations

import subprocess
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = tomllib.loads((ROOT / "cmru.build.toml").read_text())
        cls.env = cls.config["env"]

    def test_repack_is_the_explicit_release_default(self) -> None:
        self.assertEqual(self.env["RELEASE_IMAGE_FLOW"], "repack")
        source = (ROOT / "build-push.py").read_text()
        self.assertIn('or "repack"', source)

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
        ):
            self.assertTrue(self.env[key], key)

        builder = (ROOT / "scripts/ensure-release-builder.sh").read_text()
        self.assertIn('driver}" != "docker-container"', builder)
        self.assertIn('actual_memory}" != "${expected_memory}', builder)
        self.assertIn('docker buildx rm "${BUILDER}"', builder)

    def test_active_repack_path_is_oci_native(self) -> None:
        bake = (ROOT / "scripts/release-bake.sh").read_text()
        repack = (ROOT / "scripts/release-repack.sh").read_text()
        self.assertIn("type=oci", bake)
        self.assertIn("unique_by([.digest", bake)
        self.assertIn("oci-layout://", repack)
        self.assertNotIn("run_low_priority skopeo", repack)
        self.assertNotIn("docker-daemon:", repack)

    def test_release_shell_scripts_parse(self) -> None:
        scripts = [
            ROOT / "scripts/ensure-release-builder.sh",
            ROOT / "scripts/release-bake.sh",
            ROOT / "scripts/release-repack.sh",
        ]
        subprocess.run(["bash", "-n", *map(str, scripts)], check=True)


if __name__ == "__main__":
    unittest.main()

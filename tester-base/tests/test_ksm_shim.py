"""The bundled KSM preload is built from CIU's canonical source."""
from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


class KsmShimSourceAndBuild(unittest.TestCase):
    def test_tester_base_copy_matches_ciu_source(self) -> None:
        canonical = REPO / "ciu" / "src" / "ciu" / "data" / "ksm-optin.c"
        self.assertEqual((ROOT / "ksm-optin.c").read_bytes(),
                         canonical.read_bytes())

    def test_dockerfile_builds_and_advertises_the_shim(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY ksm-optin.c /tmp/ksm-optin.c", dockerfile)
        self.assertRegex(dockerfile,
                         r"gcc -shared -fPIC -nostdlib -O2 .*ksm-optin\.c")
        self.assertIn(
            'org.vbpub.tester.ksm-optin="/usr/local/lib/ksm-optin.so"',
            dockerfile)

    def test_the_shim_builds_without_dynamic_dependencies_when_readelf_exists(
            self) -> None:
        gcc = shutil.which("gcc")
        readelf = shutil.which("readelf")
        if not gcc:
            self.skipTest("gcc is unavailable in this test runner")
        if platform.machine() != "x86_64":
            self.skipTest("CIU's current raw-syscall shim is x86-64 only")

        with tempfile.TemporaryDirectory(prefix="tester-base-ksm-") as temp:
            output = Path(temp) / "ksm-optin.so"
            subprocess.run(
                [gcc, "-shared", "-fPIC", "-nostdlib", "-O2", "-o",
                 str(output), str(ROOT / "ksm-optin.c")],
                check=True, capture_output=True, text=True)
            if not readelf:
                self.skipTest("readelf is unavailable; DT_NEEDED check skipped")
            dynamic = subprocess.run(
                [readelf, "-d", str(output)], check=True,
                capture_output=True, text=True)
            self.assertNotIn("(NEEDED)", dynamic.stdout)


if __name__ == "__main__":
    unittest.main()

"""The bundled KSM preload is built from CIU's canonical source."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
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
            "readelf -d /usr/local/lib/ksm-optin.so | grep -q '(NEEDED)'",
            dockerfile)
        self.assertIn(
            'echo "KSM shim unexpectedly has DT_NEEDED dependencies" >&2; exit 1',
            dockerfile)
        self.assertIn(
            'echo "[INFO] readelf unavailable; skipped KSM shim DT_NEEDED verification" >&2',
            dockerfile)
        self.assertIn(
            'org.vbpub.tester.ksm-optin="/usr/local/lib/ksm-optin.so"',
            dockerfile)
        self.assertNotIn("KSM_OPTIN_TESTING", dockerfile)

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
            if readelf:
                dynamic = subprocess.run(
                    [readelf, "-d", str(output)], check=True,
                    capture_output=True, text=True)
                self.assertNotIn("(NEEDED)", dynamic.stdout)

            env = os.environ.copy()
            env["LD_PRELOAD"] = str(output)
            loaded = subprocess.run(
                [sys.executable, "-c", "pass"], env=env,
                capture_output=True, text=True)
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(loaded.stdout, "")
            self.assertEqual(loaded.stderr, "")

    def test_loaded_shim_is_silent_for_forced_prctl_success_and_failure(
            self) -> None:
        gcc = shutil.which("gcc")
        if not gcc:
            self.skipTest("gcc is unavailable in this test runner")
        if platform.machine() != "x86_64":
            self.skipTest("CIU's current raw-syscall shim is x86-64 only")

        with tempfile.TemporaryDirectory(prefix="tester-base-ksm-test-") as temp:
            output = Path(temp) / "ksm-optin-test.so"
            subprocess.run(
                [gcc, "-shared", "-fPIC", "-nostdlib", "-O2",
                 "-DKSM_OPTIN_TESTING", "-o", str(output),
                 str(ROOT / "ksm-optin.c")],
                check=True, capture_output=True, text=True)
            child_code = (
                "import ctypes; "
                "assert ctypes.c_int.in_dll(ctypes.CDLL(None), "
                "'ksm_optin_test_loaded').value == 1")
            for result in ("success", "failure"):
                with self.subTest(prctl=result):
                    env = os.environ.copy()
                    env["LD_PRELOAD"] = str(output)
                    env["KSM_OPTIN_TEST_PRCTL"] = result
                    loaded = subprocess.run(
                        [sys.executable, "-c", child_code], env=env,
                        capture_output=True, text=True)
                    self.assertEqual(loaded.returncode, 0, loaded.stderr)
                    self.assertEqual(loaded.stdout, "")
                    self.assertEqual(loaded.stderr, "")


if __name__ == "__main__":
    unittest.main()

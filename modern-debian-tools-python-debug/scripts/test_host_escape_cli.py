from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MDT = ROOT / "customization/mdt"
HOST_ESCAPE = ROOT / "customization/host-escape"


class HostEscapeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.docker_log = Path(self.temp.name) / "docker.log"
        docker = self.bin / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'called\\n' >> \"$FAKE_DOCKER_LOG\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        mdt = self.bin / "mdt"
        mdt.write_text(
            "#!/usr/bin/env bash\n"
            "exec bash \"$REAL_MDT\" \"$@\"\n",
            encoding="utf-8",
        )
        mdt.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.env["FAKE_DOCKER_LOG"] = str(self.docker_log)
        self.env["REAL_MDT"] = str(MDT)

    def run_mdt(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(MDT), *args],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def test_help_at_each_command_depth_is_local_and_readable(self) -> None:
        for args in (
            ("--help",),
            ("host-exec", "--help"),
            ("host-shell", "--help"),
            ("doctor", "--help"),
        ):
            with self.subTest(args=args):
                result = self.run_mdt(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = "cgroup2" if args[0] == "doctor" else "host-root authority"
                self.assertIn(expected, result.stdout)
                if args[0] == "doctor":
                    self.assertIn("privileged helper", result.stdout)
                self.assertFalse(self.docker_log.exists())

    def test_host_escape_help_does_not_become_a_host_command(self) -> None:
        result = subprocess.run(
            ["bash", str(HOST_ESCAPE), "--help"],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Host access warning", result.stdout)
        self.assertFalse(self.docker_log.exists())

    def test_invalid_target_fails_before_starting_privileged_helper(self) -> None:
        for args in (("host-exec", "--target"), ("host-exec", "--target", "0")):
            with self.subTest(args=args):
                result = self.run_mdt(*args)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr)
                self.assertFalse(self.docker_log.exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
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


class DoctorHubLoginNoticeTests(HostEscapeCliTests):
    """`mdt doctor` prints a NON-FATAL notice when Docker Hub has no login entry (presence keys only)."""

    NOTICE = "no docker.io login found"
    SECRET = "c2VjcmV0LXRva2Vu-DO-NOT-PRINT"

    def doctor(self, config: object | None) -> subprocess.CompletedProcess[str]:
        home = Path(self.temp.name) / "home"
        (home / ".docker").mkdir(parents=True, exist_ok=True)
        cfg = home / ".docker" / "config.json"
        cfg.unlink(missing_ok=True)
        if config is not None:
            import json

            cfg.write_text(config if isinstance(config, str) else json.dumps(config), encoding="utf-8")
        self.env["HOME"] = str(home)
        self.env.pop("DOCKER_CONFIG", None)
        return self.run_mdt("doctor", "--check-only")

    def test_notice_when_there_is_no_config_or_no_hub_entry(self) -> None:
        for label, config in (
            ("no file", None), ("not json", "{oops"), ("empty", {}),
            ("only ghcr", {"auths": {"ghcr.io": {"auth": self.SECRET}}}),
            ("helper for another registry", {"credHelpers": {"ghcr.io": "pass"}}),
            ("empty credsStore", {"credsStore": ""}),
        ):
            with self.subTest(label):
                result = self.doctor(config)
                self.assertIn(self.NOTICE, result.stderr)
                self.assertEqual(result.returncode, 0, "the notice never fails the doctor")
                self.assertNotIn(self.SECRET, result.stdout + result.stderr)

    def test_no_notice_when_a_hub_entry_exists_and_the_secret_is_never_printed(self) -> None:
        for label, config in (
            ("auths index.docker.io/v1", {"auths": {"https://index.docker.io/v1/": {"auth": self.SECRET}}}),
            ("auths docker.io", {"auths": {"docker.io": {"auth": self.SECRET}}}),
            ("credHelpers", {"credHelpers": {"docker.io": "desktop"}}),
        ):
            with self.subTest(label):
                result = self.doctor(config)
                self.assertNotIn(self.NOTICE, result.stderr)
                self.assertEqual(result.returncode, 0)
                self.assertNotIn(self.SECRET, result.stdout + result.stderr)

    def helper(self, listing: str, exit_code: int = 0) -> Path:
        """A fake docker-credential-fake on PATH; it logs every verb it is called with."""
        self.helper_log = Path(self.temp.name) / "helper.log"
        self.helper_log.unlink(missing_ok=True)
        script = self.bin / "docker-credential-fake"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$1\" >> \"$HELPER_LOG\"\n"
            f"[ \"$1\" = list ] && printf '%s' '{listing}'\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        self.env["HELPER_LOG"] = str(self.helper_log)
        return script

    def test_a_credsStore_is_asked_for_its_server_list_only(self) -> None:
        user = "someone-private-username"
        self.helper(json.dumps({"https://index.docker.io/v1/": user, "ghcr.io": user}))
        result = self.doctor({"credsStore": "fake"})
        self.assertNotIn(self.NOTICE, result.stderr)
        self.assertNotIn("cannot confirm", result.stderr)
        self.assertEqual(self.helper_log.read_text().split(), ["list"], "never `get`: that returns the secret")
        self.assertNotIn(user, result.stdout + result.stderr)

    def test_a_credsStore_without_a_hub_entry_gets_the_normal_notice(self) -> None:
        self.helper(json.dumps({"ghcr.io": "someone-private-username"}))
        result = self.doctor({"credsStore": "fake"})
        self.assertIn(self.NOTICE, result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("someone-private-username", result.stdout + result.stderr)

    def test_a_missing_or_failing_helper_is_cannot_confirm_not_a_failure_and_not_silence(self) -> None:
        (self.bin / "docker-credential-fake").unlink(missing_ok=True)
        absent = self.doctor({"credsStore": "fake"})
        self.assertIn("cannot confirm a Docker Hub login", absent.stderr)
        self.assertEqual(absent.returncode, 0)
        self.helper("{}", exit_code=1)
        failing = self.doctor({"credsStore": "fake"})
        self.assertIn("cannot confirm a Docker Hub login", failing.stderr)
        self.assertEqual(failing.returncode, 0)
        garbage = self.helper("not json")
        self.assertIn("cannot confirm a Docker Hub login", self.doctor({"credsStore": "fake"}).stderr)
        self.assertEqual(garbage.name, "docker-credential-fake")

    def test_a_hostile_store_name_is_never_executed(self) -> None:
        marker = Path(self.temp.name) / "pwned"
        result = self.doctor({"credsStore": f"x; touch {marker}"})
        self.assertFalse(marker.exists())
        self.assertIn("cannot confirm a Docker Hub login", result.stderr)
        self.assertIn("invalid credsStore name", result.stderr)
        self.assertNotIn("touch", result.stderr + result.stdout, "the raw config value is never echoed")

    def test_an_overlong_or_odd_store_name_is_reported_without_being_echoed(self) -> None:
        for name in ("a" * 65, "x\u001b[31my", "pass word"):
            result = self.doctor({"credsStore": name})
            self.assertIn("invalid credsStore name", result.stderr)
            self.assertNotIn(name, result.stderr)
            self.assertEqual(result.returncode, 0)

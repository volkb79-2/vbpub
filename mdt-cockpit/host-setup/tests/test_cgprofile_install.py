"""Host-setup retirement and endpoint publication oracles.

Every shell operation uses a temporary root and command stubs. These tests
never call the host installer, Docker, /etc, or a real endpoint writer.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
HELPER = ROOT / "scripts" / "mdt-host-endpoints.sh"
CHECK = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
LEGACY_PATHS = (
    "/etc/tmpfiles.d/mdt-cgprofile.conf",
    "/etc/tmpfiles.d/mdt-cgprofile-observer.conf",
    "/etc/tmpfiles.d/mdt-topos.conf",
    "/etc/sysusers.d/mdt-cgprofile.conf",
    "/etc/systemd/system/topos.service.d/50-mdt-socket-group.conf",
    "/usr/local/sbin/mdt-cgprofile-install.sh",
    "/etc/profile.d/mdt-buildkit.sh",
)


def run_bash(script: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
    )


def shell_function(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n", source)
    assert match, f"{name}() was not found"
    return match.group(0)


class LegacyHostSetupRefusal(unittest.TestCase):
    def _run_refusal(self, root: Path) -> subprocess.CompletedProcess[str]:
        return run_bash(
            f'set -euo pipefail; source "{HELPER}"; refuse_legacy_host_setup_files "{root}"',
            env=dict(os.environ),
        )

    def test_each_retired_file_refuses_a_fake_host_root_and_names_manual_removal(self) -> None:
        for old_path in LEGACY_PATHS:
            with self.subTest(path=old_path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / old_path.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("old artifact\n", encoding="utf-8")
                result = self._run_refusal(root)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(old_path, result.stderr)
                self.assertIn("sudo rm -f --", result.stderr)
                self.assertTrue(target.exists(), "refusal must leave the fake host untouched")

    def test_the_old_environment_block_refuses_with_a_manual_edit_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "etc/environment"
            target.parent.mkdir(parents=True)
            original = "PATH=/usr/bin\n# BEGIN mdt-buildkit (managed)\nBUILDX_BUILDER=mdt-managed\n# END mdt-buildkit\n"
            target.write_text(original, encoding="utf-8")
            result = self._run_refusal(root)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("# BEGIN mdt-buildkit block", result.stderr)
            self.assertIn("sudo sed -i", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_clean_fake_root_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_refusal(Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")


class EndpointPublisher:
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "host"
        (self.root / "run/mdt-buildkitd").mkdir(parents=True)
        self.socket_path = self.root / "run/mdt-buildkitd/buildkitd.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        listener.close()

        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.capture = self.base / "capture.json"
        self._write(
            self.bin / "getent",
            "#!/bin/sh\n[ \"$1\" = group ] && [ \"$2\" = docker ] || exit 2\nprintf 'docker:x:1234:\\n'\n",
        )
        self._write(
            self.bin / "stat",
            "#!/bin/sh\n"
            "if [ \"$1\" = -c ] && [ \"$2\" = %g ] && [ \"$3\" = \"$FAKE_BUILDKit_SOCKET\" ]; then\n"
            "  printf '2345\\n'; exit 0\n"
            "fi\nexec /usr/bin/stat \"$@\"\n",
        )
        self._write(
            self.bin / "augeaswrap",
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['AUGEASWRAP_CAPTURE'], 'w', encoding='utf-8') as f:\n"
            "    json.dump({'argv': sys.argv[1:], 'stdin': sys.stdin.read(), 'env': dict(os.environ)}, f)\n",
        )
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "AUGEASWRAP_CAPTURE": str(self.capture),
            "ENDPOINT_TEST_TOKEN": "stub-environment-visible",
            "FAKE_BUILDKit_SOCKET": str(self.socket_path),
        }

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _shell(self, command: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return run_bash(
            f'set -euo pipefail; source "{HELPER}"; {command}',
            env=self.env if env is None else env,
        )

    def _capture(self) -> dict[str, object]:
        return json.loads(self.capture.read_text(encoding="utf-8"))


class EndpointPublicationTests(EndpointPublisher, unittest.TestCase):
    def test_publish_passes_the_exact_fragment_entries_and_records_stub_io(self) -> None:
        result = self._shell(f'publish_host_setup_endpoints "{ROOT}" publish "{self.root}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = self._capture()
        expected_entries = [
            "BUILDX_BUILDER=mdt-managed",
            "BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock",
            "MDT_HOST_DOCKER_GID=1234",
            "MDT_BUILDKIT_SOCKET=/run/mdt-buildkitd/buildkitd.sock",
            "MDT_BUILDKIT_SOCKET_DIR=/run/mdt-buildkitd",
            "MDT_BUILDKIT_GID=2345",
            "MDT_DOCKER_API_SOCKET=/run/docker-api/docker.sock",
            "MDT_DOCKER_API_SOCKET_DIR=/run/docker-api",
        ]
        self.assertEqual(
            recorded["argv"],
            [
                "endpoints", "publish", "--owner", "mdt-host-setup", "--writer", "mdt host-setup install",
                *[part for entry in expected_entries for part in ("--entry", entry)],
            ],
        )
        self.assertEqual(recorded["stdin"], "")
        self.assertEqual(recorded["env"]["ENDPOINT_TEST_TOKEN"], "stub-environment-visible")

    def test_withdraw_is_an_explicit_standalone_operation(self) -> None:
        result = self._shell(f'publish_host_setup_endpoints "{ROOT}" withdraw "{self.root}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = self._capture()
        self.assertEqual(recorded["argv"], ["endpoints", "withdraw", "--owner", "mdt-host-setup"])
        self.assertEqual(recorded["stdin"], "")

    def test_configured_binary_name_is_resolved(self) -> None:
        result = self._shell(
            f'publish_host_setup_endpoints "{ROOT}" withdraw "{self.root}"',
            env=dict(self.env, AUGEASWRAP_BIN="augeaswrap"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._capture()["argv"], ["endpoints", "withdraw", "--owner", "mdt-host-setup"])

    def test_dry_run_prints_the_fragment_without_invoking_augeaswrap(self) -> None:
        result = self._shell(f'publish_host_setup_endpoints "{ROOT}" dry-run "{self.root}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.capture.exists(), "dry-run must not call the mutating command")
        self.assertEqual(
            result.stdout,
            "# host-endpoints v1\n# owner=mdt-host-setup\n# writer=mdt host-setup install\n"
            "BUILDX_BUILDER=mdt-managed\n"
            "BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock\n"
            "MDT_HOST_DOCKER_GID=1234\n"
            "MDT_BUILDKIT_SOCKET=/run/mdt-buildkitd/buildkitd.sock\n"
            "MDT_BUILDKIT_SOCKET_DIR=/run/mdt-buildkitd\n"
            "MDT_BUILDKIT_GID=2345\n"
            "MDT_DOCKER_API_SOCKET=/run/docker-api/docker.sock\n"
            "MDT_DOCKER_API_SOCKET_DIR=/run/docker-api\n",
        )

    def test_missing_augeaswrap_refuses_with_the_install_step(self) -> None:
        env = dict(self.env, AUGEASWRAP_BIN="augeaswrap-not-installed")
        result = self._shell(f'publish_host_setup_endpoints "{ROOT}" publish "{self.root}"', env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires augeaswrap 0.1.0", result.stderr)
        self.assertIn("install the augeaswrap wheel", result.stderr)
        self.assertFalse(self.capture.exists())


class EndpointCheckLine(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.fragment = self.base / "mdt-host-setup.env"
        self.function = shell_function(CHECK, "check_host_setup_endpoint_record").replace(
            "/etc/host-endpoints.d/mdt-host-setup.env", str(self.fragment)
        )

    def _run(self, *, writer_status: str | None) -> subprocess.CompletedProcess[str]:
        bindir = self.base / "bin"
        bindir.mkdir(exist_ok=True)
        if writer_status is not None:
            writer = bindir / "augeaswrap-stub"
            writer.write_text(f"#!/bin/sh\nprintf '%s\\n' '{{\"status\":\"{writer_status}\"}}'\n", encoding="utf-8")
            writer.chmod(0o755)
        env = dict(
            os.environ,
            PATH=f"{bindir}:/usr/bin:/bin",
            AUGEASWRAP_BIN="augeaswrap-stub",
        )
        script = (
            'FAIL=0; WARN=0; ok(){ printf "OK %s\\n" "$*"; }; '
            'fail(){ printf "FAIL %s\\n" "$*"; };\n'
            + self.function
            + "\ncheck_host_setup_endpoint_record"
        )
        return run_bash(script, env=env)

    def test_missing_command_is_a_clear_failure_line(self) -> None:
        self.fragment.write_text("fixture\n", encoding="utf-8")
        result = self._run(writer_status=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FAIL mdt-host-setup endpoint record cannot be checked", result.stdout)
        self.assertIn("install the augeaswrap wheel", result.stdout)

    def test_present_clean_record_renders_one_success_line(self) -> None:
        self.fragment.write_text("fixture\n", encoding="utf-8")
        result = self._run(writer_status="clean")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK mdt-host-setup endpoint record is present", result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 1)

    def test_missing_record_or_invalid_aggregate_is_a_failure_line(self) -> None:
        result = self._run(writer_status="clean")
        self.assertIn("FAIL /", result.stdout)
        self.fragment.write_text("fixture\n", encoding="utf-8")
        result = self._run(writer_status="problems")
        self.assertIn("FAIL mdt-host-setup endpoint record or aggregate is invalid", result.stdout)


class RetiredHostSetupWiring(unittest.TestCase):
    def test_deleted_files_and_retired_options_are_absent(self) -> None:
        for old_path in (
            ROOT / "units/mdt-topos.conf",
            ROOT / "units/mdt-topos-service.conf",
            ROOT / "units/mdt-cgprofile.conf",
            ROOT / "units/mdt-cgprofile-observer.conf",
            ROOT / "units/mdt-cgprofile.sysusers",
            ROOT / "scripts/mdt-cgprofile-install.sh",
        ):
            self.assertFalse(old_path.exists(), old_path)
        self.assertNotIn("--observer-only", INSTALL)
        self.assertNotIn("--cgprofile-only", INSTALL)
        self.assertNotIn("--force-cgprofile-restart", INSTALL)
        self.assertNotIn("CGPROFILE_", (ROOT / "host-setup.env.example").read_text(encoding="utf-8"))

    def test_no_old_environment_block_writer_remains(self) -> None:
        self.assertNotIn("_mdt_install_environment_block", INSTALL)
        self.assertNotIn("environment.mdt-buildkit", INSTALL)
        self.assertIn('publish_host_setup_endpoints "$HERE" publish /', INSTALL)

    def test_documented_uninstall_withdraws_before_disabling_or_removing_host_files(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        uninstall = readme[readme.index("## Uninstall"):]
        withdraw = uninstall.index("augeaswrap endpoints withdraw --owner mdt-host-setup")
        disable = uninstall.index("systemctl disable --now")
        remove = uninstall.index("sudo rm")
        self.assertLess(withdraw, disable)
        self.assertLess(withdraw, remove)


if __name__ == "__main__":
    unittest.main()

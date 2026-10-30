"""The docker-buildx shim makes mdt-managed the default builder for every caller.

The shim (scripts/docker-buildx-shim.sh) is a Docker CLI plugin wrapper. These tests run it for
real under /bin/sh against a FAKE packaged plugin that records its argv and environment, so no
Docker daemon is needed. Three constants are redirected into a temp tree by rewriting their
assignment lines in a copy (the shipped script has no override environment variables, so an
ambient variable can never change which binary or socket production uses).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIM = Path(__file__).resolve().parents[1] / "scripts" / "docker-buildx-shim.sh"
FAKE_PLUGIN = """#!/bin/sh
# records how it was called, then behaves like a minimal buildx
{
  printf '%s\\n' "$*"
  env | grep -E '^(BUILDX_|DOCKER_CLI_PLUGIN_|MDT_)' | sort
  echo '---'
} >> "$FAKE_LOG"
if [ "${1:-}" = create ]; then
  [ -n "${FAKE_CREATE_FAILS:-}" ] && { echo "boom: permission denied" >&2; exit 1; }
  # a real create writes the instance file under the config directory it was given
  # (stands in for Docker's passwd-entry fallback when HOME is unset)
  : "${BUILDX_CONFIG:=${DOCKER_CONFIG:-${HOME:-${FAKE_PASSWD_HOME:-/nonexistent}}/.docker}/buildx}"
  mkdir -p "$BUILDX_CONFIG/instances" && : > "$BUILDX_CONFIG/instances/mdt-managed"
fi
exit 0
"""


class ShimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="shim-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.real_dir = self.tmp / "real"
        self.real_dir.mkdir()
        self.plugin = self.real_dir / "docker-buildx"
        self.plugin.write_text(FAKE_PLUGIN)
        self.plugin.chmod(0o755)
        self.socket = self.tmp / "buildkitd.sock"
        self.shared = self.tmp / "etc-mdt-buildx"
        (self.shared / "activity").mkdir(parents=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.log = self.tmp / "log"
        self.shim = self.tmp / "docker-buildx"
        self._write_shim(real_dirs=str(self.real_dir))

    def _write_shim(self, *, real_dirs: str) -> None:
        text = SHIM.read_text()
        for name, value in (
            ("REAL_DIRS", real_dirs),
            ("MDT_SOCKET", str(self.socket)),
            ("MDT_SHARED_CONFIG", str(self.shared)),
        ):
            text, count = re.subn(rf'^{name}=.*$', f'{name}="{value}"', text, flags=re.MULTILINE)
            self.assertEqual(count, 1, f"{name} assignment not found exactly once in the shim")
        self.shim.write_text(text)
        self.shim.chmod(0o755)

    def run_shim(self, *args: str, env: dict[str, str] | None = None, socket: bool = True):
        if socket:
            self.socket.touch()  # only the test for the missing socket removes it; -S is checked below
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.home), "FAKE_LOG": str(self.log)}
        environment.update(env or {})
        # -S needs a real socket node; a unix socket bound in the temp dir stands in for the service
        return subprocess.run(
            ["/bin/sh", str(self.shim), *args], env=environment, capture_output=True, text=True, check=False
        )

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        records = []
        for chunk in self.log.read_text().split("---\n"):
            lines = [line for line in chunk.splitlines() if line]
            if lines:
                records.append({"argv": lines[0], "env": dict(l.split("=", 1) for l in lines[1:])})
        return records

    def make_socket(self) -> None:
        import socket as socketlib

        self.socket.unlink(missing_ok=True)
        server = socketlib.socket(socketlib.AF_UNIX)
        server.bind(str(self.socket))
        self.addCleanup(server.close)

    # --- pass-through cases -------------------------------------------------------------------
    def test_metadata_handshake_reaches_the_real_plugin_untouched(self) -> None:
        self.make_socket()
        result = self.run_shim("docker-cli-plugin-metadata")
        self.assertEqual(result.returncode, 0, result.stderr)
        (call,) = self.calls()
        self.assertEqual(call["argv"], "docker-cli-plugin-metadata")
        self.assertNotIn("BUILDX_BUILDER", call["env"])
        self.assertFalse((self.home / ".docker").exists(), "the handshake must not register anything")

    def test_passthrough_switch_leaves_everything_alone(self) -> None:
        self.make_socket()
        self.run_shim("build", env={"MDT_BUILDX_PASSTHROUGH": "1"})
        (call,) = self.calls()
        self.assertNotIn("BUILDX_BUILDER", call["env"])
        self.assertFalse((self.home / ".docker").exists())

    def test_a_builder_the_caller_named_is_not_replaced_or_registered(self) -> None:
        self.make_socket()
        self.run_shim("build", env={"BUILDX_BUILDER": "other"})
        (call,) = self.calls()
        self.assertEqual(call["env"]["BUILDX_BUILDER"], "other")
        self.assertFalse((self.home / ".docker").exists())

    def test_a_non_local_docker_host_is_not_redirected_to_this_hosts_worker(self) -> None:
        self.make_socket()
        for host in ("tcp://10.0.0.5:2375", "ssh://builder@elsewhere"):
            self.log.unlink(missing_ok=True)
            self.run_shim("build", env={"DOCKER_HOST": host})
            (call,) = self.calls()
            self.assertNotIn("BUILDX_BUILDER", call["env"], host)
        self.log.unlink(missing_ok=True)
        self.run_shim("build", env={"DOCKER_HOST": "unix:///var/run/docker.sock"})
        self.assertEqual(self.calls()[-1]["env"]["BUILDX_BUILDER"], "mdt-managed")

    # --- selection ------------------------------------------------------------------------------
    def test_no_builder_selected_means_mdt_managed(self) -> None:
        self.make_socket()
        result = self.run_shim("build", "-t", "x", ".")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]["env"]["BUILDX_BUILDER"], "mdt-managed")
        self.assertEqual(self.calls()[-1]["argv"], "build -t x .")

    def test_default_set_by_the_docker_cli_for_plain_docker_build_means_mdt_managed(self) -> None:
        # Measured on Docker 29.9.1: `docker build` hands the plugin BUILDX_BUILDER=default.
        self.make_socket()
        self.run_shim("buildx", "build", ".", env={"BUILDX_BUILDER": "default"})
        self.assertEqual(self.calls()[-1]["env"]["BUILDX_BUILDER"], "mdt-managed")

    # --- per-user registration ------------------------------------------------------------------
    def test_first_use_registers_the_remote_node_once_with_a_clean_environment(self) -> None:
        self.make_socket()
        self.run_shim(
            "build", ".",
            env={"BUILDX_BUILDER": "default", "DOCKER_CLI_PLUGIN_SOCKET": "@x", "DOCKER_CLI_PLUGIN_ORIGINAL_CLI_COMMAND": "docker"},
        )
        create, build = self.calls()
        self.assertEqual(
            create["argv"],
            f"create --name mdt-managed --driver remote --driver-opt default-load=true unix:///run/mdt-buildkitd/buildkitd.sock",
        )
        # `create --name X` reads an exported BUILDX_BUILDER=X as an existing instance, and the
        # DOCKER_CLI_PLUGIN_* variables make the binary expect the `buildx` plugin argv form.
        self.assertEqual(create["env"], {})
        self.assertEqual(build["env"]["BUILDX_BUILDER"], "mdt-managed")
        self.assertTrue((self.home / ".docker/buildx/instances/mdt-managed").exists())

        self.log.unlink()
        self.run_shim("build", ".")
        self.assertEqual([c["argv"] for c in self.calls()], ["build ."], "second use must not register again")

    def test_registration_follows_docker_config_and_buildx_config(self) -> None:
        self.make_socket()
        self.run_shim("build", env={"DOCKER_CONFIG": str(self.tmp / "dc")})
        self.assertTrue((self.tmp / "dc/buildx/instances/mdt-managed").exists())
        self.run_shim("build", env={"BUILDX_CONFIG": str(self.tmp / "bc"), "DOCKER_CONFIG": str(self.tmp / "dc2")})
        self.assertTrue((self.tmp / "bc/instances/mdt-managed").exists())
        self.assertFalse((self.tmp / "dc2").exists())

    def test_a_failed_registration_is_reported_and_never_falls_back_to_another_builder(self) -> None:
        self.make_socket()
        result = self.run_shim("build", env={"FAKE_CREATE_FAILS": "1", "BUILDX_BUILDER": "default"})
        self.assertIn("could not register mdt-managed", result.stderr)
        # the message carries what Buildx said, the path that decides where it registers, and the way out
        self.assertIn("boom: permission denied", result.stderr)
        self.assertIn(str(self.home / ".docker/buildx"), result.stderr)
        self.assertIn("BUILDX_CONFIG", result.stderr)
        self.assertIn("MDT_BUILDX_PASSTHROUGH=1", result.stderr)
        # Buildx is still called with mdt-managed selected, so it fails loudly with "no builder".
        self.assertEqual(self.calls()[-1]["env"]["BUILDX_BUILDER"], "mdt-managed")

    def test_a_missing_service_socket_says_so_and_does_not_try_to_register(self) -> None:
        result = self.run_shim("build", socket=False)
        self.assertIn("is missing", result.stderr)
        self.assertIn("systemctl start mdt-buildkitd.service", result.stderr)
        self.assertIn("does NOT fall back", result.stderr)
        self.assertEqual([c["argv"] for c in self.calls()], ["build"])
        self.assertEqual(self.calls()[-1]["env"]["BUILDX_BUILDER"], "mdt-managed")

    def _with_fake_getent(self, home_line: str) -> dict[str, str]:
        """An environment with no HOME whose `getent` (first on PATH) answers with the given passwd home."""
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        getent = bindir / "getent"
        getent.write_text(f"#!/bin/sh\necho 'user:x:1000:1000::{home_line}:/bin/sh'\n")
        getent.chmod(0o755)
        return {"PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_LOG": str(self.log)}

    def test_a_caller_without_home_registers_where_the_docker_cli_will_look(self) -> None:
        # Docker's own CLI falls back to the passwd entry when HOME is unset; so must the shim.
        self.make_socket()
        passwd_home = self.tmp / "passwd-home"
        environment = self._with_fake_getent(str(passwd_home))
        environment["FAKE_PASSWD_HOME"] = str(passwd_home)
        result = subprocess.run(
            ["/bin/sh", str(self.shim), "build"], env=environment, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.stderr, "")
        self.assertTrue((passwd_home / ".docker/buildx/instances/mdt-managed").exists())

    def test_a_caller_without_any_home_is_told_which_variables_to_set(self) -> None:
        self.make_socket()
        environment = self._with_fake_getent("")  # no HOME, DOCKER_CONFIG, BUILDX_CONFIG and no passwd home
        result = subprocess.run(
            ["/bin/sh", str(self.shim), "build"], env=environment, capture_output=True, text=True, check=False
        )
        for name in ("BUILDX_CONFIG", "DOCKER_CONFIG", "HOME", "passwd home"):
            self.assertIn(name, result.stderr)
        self.assertEqual(self.calls()[-1]["env"]["BUILDX_BUILDER"], "mdt-managed", "no silent fallback")

    # --- the retired shared config ----------------------------------------------------------------
    @unittest.skipIf(os.geteuid() == 0, "root can write the shared directory, so nothing is dropped")
    def test_a_stale_shared_buildx_config_the_caller_cannot_write_is_dropped(self) -> None:
        self.make_socket()
        (self.shared / "activity").chmod(0o555)
        self.addCleanup((self.shared / "activity").chmod, 0o755)
        self.run_shim("build", env={"BUILDX_CONFIG": str(self.shared)})
        self.assertNotIn("BUILDX_CONFIG", self.calls()[-1]["env"])
        self.assertTrue((self.home / ".docker/buildx/instances/mdt-managed").exists())

    def test_a_writable_shared_config_or_any_other_config_is_kept(self) -> None:
        self.make_socket()
        self.run_shim("build", env={"BUILDX_CONFIG": str(self.shared)})
        self.assertEqual(self.calls()[-1]["env"]["BUILDX_CONFIG"], str(self.shared))
        self.log.unlink()
        self.run_shim("build", env={"BUILDX_CONFIG": str(self.tmp / "mine")})
        self.assertEqual(self.calls()[-1]["env"]["BUILDX_CONFIG"], str(self.tmp / "mine"))

    # --- no packaged plugin --------------------------------------------------------------------------
    def test_without_a_packaged_plugin_it_stops_with_a_message(self) -> None:
        self._write_shim(real_dirs=str(self.tmp / "nowhere"))
        result = self.run_shim("build")
        self.assertEqual(result.returncode, 1)
        self.assertIn("packaged Buildx plugin is not installed", result.stderr)
        self.assertIn("docker-buildx-plugin", result.stderr)

    # --- the shipped file ------------------------------------------------------------------------------
    def test_shipped_script_has_no_override_environment_and_is_executable(self) -> None:
        text = SHIM.read_text()
        self.assertTrue(stat.S_IMODE(SHIM.stat().st_mode) & 0o111)
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        # the only environment inputs are the documented ones; nothing selects the real plugin or socket
        for forbidden in ("MDT_BUILDX_REAL", "MDT_BUILDKITD_SOCKET"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(
            json.loads(json.dumps(re.findall(r'MDT_SOCKET=(\S+)', text))), ["/run/mdt-buildkitd/buildkitd.sock"]
        )
        self.assertIn(
            'REAL_DIRS="/usr/local/libexec/docker/cli-plugins /usr/lib/docker/cli-plugins /usr/libexec/docker/cli-plugins"',
            text,
        )

    def test_install_script_installs_the_shim_where_docker_searches_first(self) -> None:
        install = (SHIM.parents[1] / "install.sh").read_text()
        self.assertIn("BUILDX_SHIM_TARGET=/usr/local/lib/docker/cli-plugins/docker-buildx", install)
        self.assertIn('"$HERE/scripts/docker-buildx-shim.sh" "$BUILDX_SHIM_TARGET"', install)
        self.assertIn("_mdt_install_environment_block", install)
        self.assertIn("env -i PATH=", install, "the end-to-end probe must run with an empty environment")


if __name__ == "__main__":
    unittest.main()

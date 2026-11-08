"""Isolated oracles for host-setup runtime checks and render-time decisions.

The shell functions are extracted from the scripts by name and run in bash with PATH shims.
Nothing here touches the host.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = (ROOT / "install.sh").read_text(encoding="utf-8")
CHECK_SH = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")


def shell_function(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n", source)
    assert match, f"{name}() not found"
    return match.group(0)


def run_bash(script: str, *, env: dict[str, str] | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env or dict(os.environ), cwd=cwd)


class DockerRestartReminder(unittest.TestCase):
    FUNCTION = shell_function(INSTALL_SH, "_mdt_docker_restart_pending")

    def _pending(self, *, main_pid: str, lstart: str, daemon_json_age_s: int | None, socket: bool) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            (bindir / "systemctl").write_text(f"#!/bin/sh\necho '{main_pid}'\n", encoding="utf-8")
            (bindir / "ps").write_text(f"#!/bin/sh\nprintf '%s\\n' '{lstart}'\n", encoding="utf-8")
            for shim in bindir.iterdir():
                shim.chmod(0o755)
            daemon_json = Path(tmp) / "daemon.json"
            if daemon_json_age_s is not None:
                daemon_json.write_text("{}", encoding="utf-8")
                now = int(subprocess.run(["date", "+%s"], capture_output=True, text=True).stdout)
                os.utime(daemon_json, (now - daemon_json_age_s, now - daemon_json_age_s))
            sock = Path(tmp) / "docker.sock"
            if socket:
                import socket as socketlib

                server = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
                server.bind(str(sock))
            env = dict(
                os.environ,
                PATH=f"{bindir}:{os.environ['PATH']}",
                MDT_DOCKER_DAEMON_JSON=str(daemon_json),
                MDT_DOCKER_API_SOCK=str(sock),
            )
            script = (
                'set -euo pipefail; MDT_DOCKER_DAEMON_JSON="${MDT_DOCKER_DAEMON_JSON}"; '
                'MDT_DOCKER_API_SOCK="${MDT_DOCKER_API_SOCK}"; ' + self.FUNCTION + "\n_mdt_docker_restart_pending"
            )
            result = run_bash(script, env=env)
            if socket:
                server.close()
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.split()

    @staticmethod
    def _lstart(seconds_ago: int) -> str:
        return subprocess.run(["date", "-d", f"@{int(__import__('time').time()) - seconds_ago}", "+%a %b %e %H:%M:%S %Y"],
                              capture_output=True, text=True).stdout.strip()

    def test_nothing_pending_once_dockerd_restarted_after_the_change_and_the_socket_listens(self) -> None:
        self.assertEqual(self._pending(main_pid="4242", lstart=self._lstart(60), daemon_json_age_s=600, socket=True), [])

    def test_daemon_json_newer_than_dockerd_is_pending(self) -> None:
        self.assertEqual(self._pending(main_pid="4242", lstart=self._lstart(600), daemon_json_age_s=60, socket=True), ["daemon-json"])

    def test_an_unknown_dockerd_start_is_pending_not_a_crash(self) -> None:
        """MainPID 0 (dockerd not running / remote DOCKER_HOST) and an empty `ps` answer both used to abort
        install.sh under pipefail, or read as today 00:00 via `date -d ""`."""
        self.assertEqual(self._pending(main_pid="0", lstart="", daemon_json_age_s=600, socket=True), ["daemon-json"])
        self.assertEqual(self._pending(main_pid="4242", lstart="", daemon_json_age_s=600, socket=True), ["daemon-json"])

    def test_a_missing_api_socket_is_pending(self) -> None:
        self.assertEqual(self._pending(main_pid="4242", lstart=self._lstart(60), daemon_json_age_s=600, socket=False), ["api-socket"])
        self.assertEqual(
            self._pending(main_pid="0", lstart="", daemon_json_age_s=None, socket=False), ["daemon-json", "api-socket"]
        )

    def test_the_end_of_run_block_prints_one_reminder_per_pending_reason(self) -> None:
        tail = INSTALL_SH[INSTALL_SH.index('_pending="$(_mdt_docker_restart_pending)"'):]
        self.assertIn("grep -qx daemon-json", tail)
        self.assertIn("grep -qx api-socket", tail)
        self.assertNotRegex(INSTALL_SH, r"(?m)^[^#\n]*\bpidof\b", "pidof is only mentioned in a comment, never run")


class InotifyBudgetCheck(unittest.TestCase):
    BLOCK = CHECK_SH[CHECK_SH.index("# BEGIN inotify-budget"):CHECK_SH.index("# END inotify-budget")]

    def _run(self, *, live: str | None, installed: str | None, shipped: str | None = "fs.inotify.max_user_watches = 524288\n") -> str:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = dict(os.environ)
            for name, content, var in (
                ("proc", live, "MDT_INOTIFY_PROC"),
                ("installed.conf", installed, "MDT_INOTIFY_SYSCTL_FILE"),
                ("shipped.conf", shipped, "MDT_INOTIFY_SHIPPED_FILE"),
            ):
                path = base / name
                if content is not None:
                    path.write_text(content, encoding="utf-8")
                env[var] = str(path)
            harness = base / "h.sh"
            harness.write_text(
                "FAIL=0; WARN=0\nok(){ echo \"OK $*\"; }\nwarn(){ echo \"WARN $*\"; }\nfail(){ echo \"FAIL $*\"; }\n"
                + self.BLOCK + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, env=env)
            self.assertEqual(result.stderr, "")
            return result.stdout

    def test_a_healthy_host_passes_both_checks(self) -> None:
        out = self._run(live="524288\n", installed="fs.inotify.max_user_watches = 524288\n")
        self.assertIn("OK fs.inotify.max_user_watches=524288 (>= 524288)", out)
        self.assertIn("OK", out.splitlines()[1])
        self.assertNotIn("FAIL", out)

    def test_a_lowered_installed_file_fails_both_checks_instead_of_certifying_itself(self) -> None:
        """The persisted check once compared the installed file with its own value."""
        out = self._run(live="8192\n", installed="fs.inotify.max_user_watches = 8192\n")
        self.assertIn("FAIL fs.inotify.max_user_watches=8192 < 524288", out)
        self.assertIn("=8192 < 524288 (lowered by hand?)", out)

    def test_a_raised_installed_file_is_fine(self) -> None:
        out = self._run(live="1048576\n", installed="fs.inotify.max_user_watches = 1048576\n")
        self.assertNotIn("FAIL", out)
        self.assertIn("=1048576 (>= 524288", out)

    def test_a_missing_or_keyless_installed_file_warns(self) -> None:
        self.assertIn("WARN", self._run(live="524288\n", installed=None))
        self.assertIn("sets no numeric fs.inotify.max_user_watches", self._run(live="524288\n", installed="# empty\n"))

    def test_the_policy_minimum_is_524288_without_a_shipped_file_and_a_bad_shipped_value_warns(self) -> None:
        out = self._run(live="524288\n", installed="fs.inotify.max_user_watches = 524288\n", shipped=None)
        self.assertIn("(>= 524288)", out)
        out = self._run(live="524288\n", installed="fs.inotify.max_user_watches = 524288\n", shipped="fs.inotify.max_user_watches = lots\n")
        self.assertIn("WARN shipped fs.inotify.max_user_watches threshold 'lots' is not a number", out)

    def test_an_unreadable_live_value_warns(self) -> None:
        self.assertIn("WARN fs.inotify.max_user_watches unreadable", self._run(live=None, installed=None))


class DockerApiSocketCheck(unittest.TestCase):
    """MDT-037: check.sh verifies the dedicated API socket, its drop-in and a pending daemon restart."""

    BLOCK = CHECK_SH[CHECK_SH.index("# BEGIN docker-api-socket"):CHECK_SH.index("# END docker-api-socket")]

    def _run(self, *, dropin: bool, socket: str | None, daemon_json_newer: bool | None, started: str = "1000") -> str:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = dict(os.environ)
            if dropin:
                (base / "dropin.conf").write_text("[Socket]\n", encoding="utf-8")
            env["MDT_DOCKER_DROPIN"] = str(base / "dropin.conf")
            env["MDT_DOCKER_API_SOCK"] = str(base / "docker.sock")
            env["MDT_DOCKER_DAEMON_JSON"] = str(base / "daemon.json")
            env["MDT_DOCKER_STARTED_EPOCH"] = started
            if daemon_json_newer is not None:
                (base / "daemon.json").write_text("{}", encoding="utf-8")
                os.utime(base / "daemon.json", (2000, 2000) if daemon_json_newer else (500, 500))
            prelude = "FAIL=0; WARN=0\nok(){ echo \"OK $*\"; }\nwarn(){ echo \"WARN $*\"; }\nfail(){ echo \"FAIL $*\"; }\n"
            # a stat shim: the fixture is a plain file, so report the real socket's owner and mode
            stat_shim = ""
            if socket is not None:
                stat_shim = f"stat() {{ if [ \"$1\" = -c ] && [ \"$2\" != %Y ]; then echo '{socket}'; else command stat \"$@\"; fi; }}\n"
                # the test fixture is not a socket: replace the -S test for this run only
                block = self.BLOCK.replace('[ -S "$_docker_api_sock" ]', '[ -e "$_docker_api_sock" ]')
                (base / "docker.sock").write_text("", encoding="utf-8")
            else:
                block = self.BLOCK
            harness = base / "h.sh"
            harness.write_text(prelude + stat_shim + block + "\n", encoding="utf-8")
            result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, env=env)
            return result.stdout

    def test_a_healthy_host_passes_every_check(self) -> None:
        out = self._run(dropin=True, socket="root:docker 660", daemon_json_newer=False)
        self.assertNotIn("FAIL", out)
        self.assertNotIn("WARN", out)
        self.assertIn("no restart pending", out)

    def test_a_missing_dropin_fails(self) -> None:
        out = self._run(dropin=False, socket="root:docker 660", daemon_json_newer=False)
        self.assertIn("FAIL", out)
        self.assertIn("missing", out)

    def test_a_missing_listener_fails(self) -> None:
        out = self._run(dropin=True, socket=None, daemon_json_newer=False)
        self.assertIn("is not a listening socket", out)

    def test_a_socket_with_the_wrong_owner_or_mode_fails(self) -> None:
        out = self._run(dropin=True, socket="root:root 666", daemon_json_newer=False)
        self.assertIn("expected root:docker 660", out)

    def test_a_daemon_json_newer_than_dockerd_is_a_pending_restart_warning_not_a_failure(self) -> None:
        out = self._run(dropin=True, socket="root:docker 660", daemon_json_newer=True)
        self.assertIn("WARN", out)
        self.assertIn("restart is pending", out)
        self.assertNotIn("FAIL", out)


class InotifyInstallNeverLowers(unittest.TestCase):
    """MDT-038: the installed sysctl file carries max(live value, shipped value)."""

    START = INSTALL_SH.index("_inotify_value() {")
    END = INSTALL_SH.index('if [ "$_have_watches" -lt "$_want_watches" ]; then')
    BLOCK = INSTALL_SH[START:END]

    def _installed(self, live: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "proc").write_text(live, encoding="utf-8")
            block = self.BLOCK.replace("/proc/sys/fs/inotify/max_user_watches", str(base / "proc")).replace(
                "/etc/sysctl.d/99-mdt-inotify.conf", str(base / "installed.conf")
            )
            result = run_bash(f'HERE="{ROOT}"\n{block}\ncat "{base}/installed.conf"')
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_a_higher_live_value_is_kept(self) -> None:
        out = self._installed("4194304\n")
        self.assertRegex(out, r"(?m)^fs\.inotify\.max_user_watches = 4194304$")

    def test_a_lower_live_value_installs_the_shipped_file(self) -> None:
        out = self._installed("8192\n")
        self.assertRegex(out, r"(?m)^fs\.inotify\.max_user_watches = 1048576$")
        self.assertNotIn("MDT-038", out)


class EndpointInstallPreflight(unittest.TestCase):
    """The endpoint publisher is required before the install commit point."""

    def test_retired_options_and_cgprofile_service_wiring_are_gone(self) -> None:
        self.assertNotIn("--observer-only", INSTALL_SH)
        self.assertNotIn("--cgprofile-only", INSTALL_SH)
        self.assertNotIn("provision_cgprofile_observer", INSTALL_SH)
        self.assertNotIn("mdt-cgprofile-install.sh", INSTALL_SH)

    def test_legacy_file_refusal_precedes_all_install_mutation(self) -> None:
        refusal = INSTALL_SH.index("refuse_legacy_host_setup_files / || exit")
        commit = INSTALL_SH.index("# === COMMIT POINT")
        self.assertLess(refusal, commit)

    def test_augeaswrap_requirement_precedes_the_commit_point(self) -> None:
        requirement = INSTALL_SH.index("require_augeaswrap || exit")
        first_optional_package_install = INSTALL_SH.index('apt-get install -y --no-install-recommends fio pv')
        commit = INSTALL_SH.index("# === COMMIT POINT")
        self.assertLess(requirement, first_optional_package_install)
        self.assertLess(requirement, commit)

    def test_endpoint_publication_follows_buildkit_readiness_and_probe(self) -> None:
        ready = INSTALL_SH.index('if [ ! -S "$BUILDKITD_SOCKET" ]; then')
        probe = INSTALL_SH.index('echo "ok: fresh user resolves mdt-managed')
        publish = INSTALL_SH.index('publish_host_setup_endpoints "$HERE" publish /')
        self.assertLess(ready, probe)
        self.assertLess(probe, publish)
        self.assertIn('MDT_BUILDKIT_SOCKET="$MDT_BUILDKIT_SOCKET"', INSTALL_SH)

    def test_wizard_no_longer_accepts_retired_cgprofile_keys(self) -> None:
        wizard = (ROOT / "mdt-host-setup-wizard.py").read_text(encoding="utf-8")
        self.assertNotIn("validate_cgprofile", wizard)
        self.assertNotIn('"CGPROFILE_ENABLE"', wizard)


class ReconcileKeepsCapsOnTransientDeviceLoss(unittest.TestCase):
    """MDT-019: a valid baseline with no discovered device does not clear the per-container caps."""

    SRC = (ROOT / "scripts" / "mdt-dev-governance-reconcile.sh").read_text(encoding="utf-8")

    def test_the_clear_is_guarded_by_the_baseline_probe(self) -> None:
        start = self.SRC.index('if [ "${WATCHER_SKIP:-0}" = 1 ]; then')
        block = self.SRC[start:self.SRC.index("else\n    for c in $(docker ps")]
        self.assertIn('[ -z "${IO_DEV_PATH:-}" ]', block)
        self.assertIn("RIOPS_MAX", block)
        self.assertIn("keeping existing per-container IO caps", block)
        self.assertLess(block.index("keeping existing"), block.index("_mdt_clear_container_caps"))


if __name__ == "__main__":
    unittest.main()

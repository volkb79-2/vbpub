"""Oracles for three host-setup code paths that had none (review of 2026-10-27):

* install.sh `_mdt_environment_block_structure` / `_mdt_install_environment_block`: the marked
  block in /etc/environment is validated (orphan BEGIN, END first, duplicates, trailing
  whitespace) and the validation runs in the pre-flight, before the COMMIT POINT;
* install.sh `_mdt_docker_restart_pending`: the end-of-run docker restart reminder;
* check.sh's inotify-budget block: the policy minimum, not the installed file, is the threshold.

The shell functions are extracted from the scripts by name and run in bash with PATH shims, the
same technique as test_guard_events.py. Nothing here touches the host.
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
BLOCK_FILE = ROOT / "etc" / "environment.mdt-buildkit"


def shell_function(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n", source)
    assert match, f"{name}() not found"
    return match.group(0)


def run_bash(script: str, *, env: dict[str, str] | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env or dict(os.environ), cwd=cwd)


class EnvironmentBlockStructure(unittest.TestCase):
    FUNCTIONS = shell_function(INSTALL_SH, "_mdt_environment_block_structure") + shell_function(
        INSTALL_SH, "_mdt_install_environment_block"
    )

    def _structure(self, content: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "environment"
            target.write_text(content, encoding="utf-8")
            return run_bash(f'set -euo pipefail; {self.FUNCTIONS}\n_mdt_environment_block_structure "{target}"')

    def _install(self, content: str | None) -> tuple[subprocess.CompletedProcess, str | None]:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "environment"
            if content is not None:
                target.write_text(content, encoding="utf-8")
            result = run_bash(
                f'set -euo pipefail; {self.FUNCTIONS}\n_mdt_install_environment_block "{BLOCK_FILE}" "{target}"'
            )
            after = target.read_text(encoding="utf-8") if target.exists() else None
            leftovers = [p.name for p in Path(tmp).iterdir() if p.name != "environment"]
            self.assertEqual(leftovers, [], "no temp file is left next to the target")
            return result, after

    def test_sound_structures_pass(self) -> None:
        for content in (
            "",
            "PATH=/usr/bin\n",
            "PATH=/usr/bin\n# BEGIN mdt-buildkit (x)\nA=1\n# END mdt-buildkit\nZ=9\n",
            "# BEGIN mdt-buildkit (x)\nA=1\n# END mdt-buildkit   \n",  # trailing whitespace tolerated
        ):
            with self.subTest(content=content):
                result = self._structure(content)
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)

    def test_defective_structures_are_named_and_refused(self) -> None:
        cases = {
            "PATH=/usr/bin\n# BEGIN mdt-buildkit (x)\nA=1\nZ=9\n": "BEGIN marker without END",
            "# END mdt-buildkit\n# BEGIN mdt-buildkit (x)\nA=1\n": "END marker before its BEGIN",
            "# BEGIN mdt-buildkit (x)\n# BEGIN mdt-buildkit (y)\nA=1\n# END mdt-buildkit\n": "more than one BEGIN marker",
            "# BEGIN mdt-buildkit (x)\nA=1\n# END mdt-buildkit\n# END mdt-buildkit\n": "more than one END marker",
        }
        for content, defect in cases.items():
            with self.subTest(defect=defect):
                result = self._structure(content)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout.strip(), defect)

    def test_install_refuses_a_defective_file_and_leaves_it_byte_for_byte(self) -> None:
        for content in (
            "PATH=/usr/bin\n# BEGIN mdt-buildkit (x)\nA=1\nZ=9\n",  # orphan BEGIN: the old awk dropped Z=9
            "# END mdt-buildkit\nPATH=/usr/bin\n# BEGIN mdt-buildkit (x)\nA=1\n",  # END first: the old code truncated, rc 0
        ):
            with self.subTest(content=content[:20]):
                result, after = self._install(content)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Recovering a damaged /etc/environment block", result.stderr)
                self.assertEqual(after, content)

    def test_install_replaces_the_block_in_place_keeps_other_lines_and_is_idempotent(self) -> None:
        before = "PATH=/usr/bin\n# BEGIN mdt-buildkit (old)\nBUILDX_BUILDER=stale\n# END mdt-buildkit  \nZ=9\n"
        result, after = self._install(before)
        self.assertEqual(result.returncode, 0, result.stderr)
        block = BLOCK_FILE.read_text(encoding="utf-8")
        self.assertEqual(after, "PATH=/usr/bin\nZ=9\n" + block)
        self.assertNotIn("stale", after)
        result, again = self._install(after)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(again, after)

    def test_a_missing_target_is_created_with_the_block(self) -> None:
        result, after = self._install(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(after, BLOCK_FILE.read_text(encoding="utf-8"))

    def test_the_validation_runs_in_the_pre_flight_before_the_commit_point(self) -> None:
        commit = INSTALL_SH.index("# === COMMIT POINT")
        preflight_call = INSTALL_SH.index('_mdt_environment_block_structure /etc/environment')
        self.assertLess(preflight_call, commit, "the structure check must precede the first host mutation")
        self.assertGreater(INSTALL_SH.index("_mdt_install_environment_block \"$HERE/etc/environment.mdt-buildkit\""), commit)


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


if __name__ == "__main__":
    unittest.main()

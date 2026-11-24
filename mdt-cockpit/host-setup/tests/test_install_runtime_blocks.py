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


class OomdDevTierCheck(unittest.TestCase):
    """check.sh check_oomd_dev_tier: oomd must not act on the dev tier (MDT-108), run with systemctl/oomctl shims."""

    FUNCTION = shell_function(CHECK_SH, "check_oomd_dev_tier")
    CLEAN_DUMP = (
        "Dump of Context:\nSwap Monitored CGroups:\nMemory Pressure Monitored CGroups:\n"
        "\tPath: /nyxloom.slice\n\t\tMemory Pressure Limit: 60.00%\n\tPath: /user.slice/user-1000.slice\n"
        "\tPath: /device.slice\n"
    )

    def _run(self, *, props: dict[str, tuple[str, str]] | None = None, dump: str | None = CLEAN_DUMP,
             oomctl_rc: int = 0) -> str:
        """props maps a slice name to (ManagedOOMMemoryPressure, ManagedOOMSwap); the rest are auto.
        dump=None means oomctl is not installed."""
        import shutil

        bash = shutil.which("bash")  # the child's PATH holds only the shims
        props = props or {}
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            for tool in ("cat", "sed", "grep", "tr"):
                (bindir / tool).symlink_to(shutil.which(tool))
            cases = "".join(
                f"{name}.slice) echo 'ManagedOOMMemoryPressure={mp}'; echo 'ManagedOOMSwap={sw}';;\n"
                for name, (mp, sw) in props.items()
            )
            (bindir / "systemctl").write_text(
                f"#!/bin/sh\nfor last; do :; done\ncase \"$last\" in\n{cases}*) echo 'ManagedOOMMemoryPressure=auto'; echo 'ManagedOOMSwap=auto';;\nesac\n",
                encoding="utf-8",
            )
            if dump is not None:
                (dump_file := Path(tmp) / "dump.txt").write_text(dump, encoding="utf-8")
                (bindir / "oomctl").write_text(f"#!/bin/sh\ncat '{dump_file}'\nexit {oomctl_rc}\n", encoding="utf-8")
            for shim in bindir.iterdir():
                if not shim.is_symlink():
                    shim.chmod(0o755)
            harness = (
                'ok(){ echo "OK $*"; }\nwarn(){ echo "WARN $*"; }\nfail(){ echo "FAIL $*"; }\n'
                + self.FUNCTION + "\ncheck_oomd_dev_tier\n"
            )
            return subprocess.run(
                [bash, "-c", harness], capture_output=True, text=True, env={"PATH": str(bindir)}
            ).stdout

    def test_all_auto_and_an_unrelated_dump_is_ok(self) -> None:
        out = self._run()
        self.assertIn("OK systemd-oomd manages no dev*.slice", out)
        self.assertNotIn("FAIL", out)
        self.assertNotIn("WARN", out)

    def test_a_non_auto_property_on_any_dev_slice_fails(self) -> None:
        for name in ("-", "dev", "dev-gates", "dev-background", "dev-interactive", "dev-buildkitd"):
            for value in (("kill", "auto"), ("auto", "kill")):
                out = self._run(props={name: value})
                self.assertIn(f"FAIL {name}.slice has ManagedOOMMemoryPressure={value[0]} ManagedOOMSwap={value[1]}", out)
                self.assertNotIn("OK ", out)

    def test_oomd_watching_a_dev_slice_or_the_root_slice_fails(self) -> None:
        for path in ("/dev.slice", "/dev.slice/dev-gates.slice/docker-abc.scope", "/dev-background.slice", "/", "/-.slice"):
            out = self._run(dump=self.CLEAN_DUMP + f"\tPath: {path}\n")
            self.assertIn("FAIL systemd-oomd monitors the dev tier or the root slice", out, path)
            self.assertNotIn("OK ", out)

    def test_an_unrecognised_dump_format_warns_and_is_never_clean(self) -> None:
        for dump in ("", '{"monitored": ["/"]}\n', "Dump of Context:\n\tPath: /dev.slice\n"):
            out = self._run(dump=dump)
            self.assertIn("WARN oomctl dump format unrecognised", out, dump)
            self.assertNotIn("OK ", out)
            self.assertNotIn("FAIL", out)

    def test_the_root_slice_is_queried_with_a_double_dash(self) -> None:
        self.assertIn('-p ManagedOOMMemoryPressure,ManagedOOMSwap -- "$s.slice"', CHECK_SH)
        self.assertRegex(CHECK_SH, r"for s in - dev ")

    def test_unreadable_properties_warn_never_ok(self) -> None:
        out = self._run(props={"dev-gates": ("", "")})
        self.assertIn("WARN dev-gates.slice: could not read", out)
        self.assertNotIn("OK ", out)

    def test_missing_or_failing_oomctl_warns_and_never_fails(self) -> None:
        out = self._run(dump=None)
        self.assertIn("WARN oomctl not found", out)
        self.assertNotIn("FAIL", out)
        out = self._run(oomctl_rc=1)
        self.assertIn("WARN oomctl dump failed", out)
        self.assertNotIn("FAIL", out)

    def test_a_bad_property_still_fails_when_oomctl_is_missing(self) -> None:
        out = self._run(props={"dev-gates": ("kill", "auto")}, dump=None)
        self.assertIn("FAIL dev-gates.slice", out)

    def test_check_sh_actually_calls_it(self) -> None:
        self.assertRegex(CHECK_SH, r"(?m)^check_oomd_dev_tier$")


class CheckoutSetPreflight(unittest.TestCase):
    """install.sh preflight_checkout_set: a checkout missing a file outside host-setup/ is refused before any host change."""

    FUNCTION = shell_function(INSTALL_SH, "preflight_checkout_set")
    LISTED = re.findall(r"^\s+(\.\./\S+)$", FUNCTION, re.M)

    def _run(self, tree: Path, *, drop: tuple[str, ...] = (), tamper: bool = False) -> subprocess.CompletedProcess:
        here = tree / "mdt-cockpit" / "host-setup"
        here.mkdir(parents=True, exist_ok=True)
        for rel in self.LISTED:
            f = (here / rel).resolve()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x", encoding="utf-8")
        tools = (here / "../../scripts/debian-install-v2/tools").resolve()
        import hashlib

        (tools / "cpu_benchmark.py.sha256").write_text(
            hashlib.sha256(b"x").hexdigest() + "  cpu_benchmark.py\n", encoding="utf-8")
        if tamper:
            (tools / "cpu_benchmark.py").write_text("tampered", encoding="utf-8")
        for rel in drop:
            (here / rel).resolve().unlink()
        return run_bash(f'HERE="{here}"\n{self.FUNCTION}\npreflight_checkout_set')

    def test_a_complete_checkout_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(Path(tmp))
            self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_a_missing_out_of_tree_file_is_refused_naming_it_and_the_required_set(self) -> None:
        drop = ("../../scripts/debian-install-v2/tools/iocost_coef_gen.py", "../../scripts/debian-install-v2/tools/iocost_plausibility.py")
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(Path(tmp), drop=drop)
            self.assertEqual(r.returncode, 1)
            for rel in drop:
                self.assertIn(str((Path(tmp) / "mdt-cockpit" / "host-setup" / rel).resolve()), r.stderr)
            self.assertNotIn("cpu_benchmark", r.stderr)
            self.assertIn("mdt-cockpit/ AND scripts/debian-install-v2/tools/", r.stderr)

    def test_a_cpu_benchmark_that_does_not_match_its_pin_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(Path(tmp), tamper=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("does not match cpu_benchmark.py.sha256", r.stderr)

    # The real argument parsing and the real guard, with only the host checks between them left out
    # (install.sh itself refuses inside a container, so the whole script cannot run here).
    PARSE = INSTALL_SH[INSTALL_SH.index("CPU_BENCHMARK=0\n"):INSTALL_SH.index("# Host setup changes the host's systemd")]
    GUARD = INSTALL_SH[INSTALL_SH.index("# Every mode from here on installs"):INSTALL_SH.index("DOCKER_BIN=/usr/bin/docker")]

    def _modes(self, tree: Path, *args: str, complete: bool) -> subprocess.CompletedProcess:
        here = tree / "mdt-cockpit" / "host-setup"
        if complete:
            self._run(tree)
        else:
            here.mkdir(parents=True, exist_ok=True)
        quoted = " ".join(f"'{a}'" for a in args)
        return run_bash(
            f'set -euo pipefail\nHERE="{here}"\nusage(){{ :; }}\nset -- {quoted}\n{self.PARSE}\n{self.FUNCTION}\n'
            f'{self.GUARD}\necho REACHED-RENDER'
        )

    def test_every_mutating_mode_is_refused_on_an_incomplete_tree_before_render(self) -> None:
        for args in ((), ("--wizard",), ("--with-baseline",), ("--wizard", "--with-baseline"), ("--wizard", "--reset"),
                     ("--restart-docker",)):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as tmp:
                r = self._modes(Path(tmp), *args, complete=False)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertNotIn("REACHED-RENDER", r.stdout)
                self.assertIn("incomplete", r.stderr)

    def test_a_complete_tree_and_apply_protection_pass_the_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = self._modes(Path(tmp), "--wizard", complete=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "REACHED-RENDER"), r.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            r = self._modes(Path(tmp), "--apply-protection", complete=False)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "REACHED-RENDER"), r.stderr)

    def test_every_out_of_tree_file_install_sh_reads_is_in_the_list(self) -> None:
        listed = " ".join(self.LISTED)
        for path in set(re.findall(r'\$HERE/(\.\./[^\s"\\]+)', INSTALL_SH)):
            self.assertIn(path, listed, f"install.sh reads {path} but the preflight does not check it")
        for name in ("iocost_coef_gen.py", "iocost_plausibility.py", "cpu_benchmark.py", "cpu_benchmark.py.sha256", "cpu_benchmark.pins.json"):
            self.assertIn(name, listed)

    def test_it_runs_before_the_first_host_change(self) -> None:
        call = INSTALL_SH.index("preflight_checkout_set || exit 2")
        for later in ('DOCKER_BIN=/usr/bin/docker', 'echo "== scripts =="', 'echo "== packages =="', "systemctl daemon-reload"):
            self.assertLess(call, INSTALL_SH.index(later), later)


class RetiredOomKeysWarning(unittest.TestCase):
    """MDT-108: an old host-setup.env that still sets the retired oomd keys gets one WARN naming them, never a failure."""

    BLOCK = INSTALL_SH[INSTALL_SH.index("_retired_oomd_keys=()"):INSTALL_SH.index('DEV_STATIC_RBW="$MDT_STATIC_RBPS"')]

    def _run(self, assignments: str) -> subprocess.CompletedProcess:
        return run_bash(f'set -euo pipefail\nCONFIG_PATH=/etc/mdt/host-setup.env\n{assignments}\n{self.BLOCK}\necho done')

    def test_names_every_retired_key_present_and_still_succeeds(self) -> None:
        r = self._run("DEV_GATES_OOM_PRESSURE_LIMIT=75%\nMDT_MEMORY_POLICY=oomd\nDEV_BACKGROUND_OOM_PRESSURE_LIMIT=")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "done"), r.stderr)
        self.assertEqual(r.stderr.count("WARN:"), 1)
        for key in ("DEV_GATES_OOM_PRESSURE_LIMIT", "DEV_BACKGROUND_OOM_PRESSURE_LIMIT", "MDT_MEMORY_POLICY"):
            self.assertIn(key, r.stderr)

    def test_is_silent_without_them(self) -> None:
        r = self._run("DEV_GATES_MEMORY_MAX=1G")
        self.assertEqual((r.returncode, r.stderr), (0, ""))


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

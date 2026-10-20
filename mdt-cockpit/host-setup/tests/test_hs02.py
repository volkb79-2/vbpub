"""MDT-HS-02: the real install path on an EXISTING env file (items 1-3).

Why the MDT-HS-01 round-1 tests missed item 1: they exercised the SHELL helper
(`_mdt_missing_declared_keys`) in isolation, but install.sh runs the Python strict
validator (`--validate-config`) FIRST, and that validator had its own idea of
"missing". These tests drive the real entry point.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_io_governance import LIB, ROOT, SCRIPTS, _load, _write_exec  # noqa: E402

PROJECT = ROOT.parent
EXAMPLE = ROOT / "host-setup.env.example"
WIZARD_PATH = ROOT / "mdt-host-setup-wizard.py"
MEMINFO = "MemTotal:       16384000 kB\nMemAvailable:   12000000 kB\nSwapTotal: 8388604 kB\n"
OPTIONAL_RE = re.compile(
    next(ln for ln in (ROOT / "runtime-optional-keys.re").read_text().splitlines() if ln and not ln.startswith("#"))
) if (ROOT / "runtime-optional-keys.re").exists() else None


def example_keys() -> list[str]:
    return re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", EXAMPLE.read_text())


class ValidateConfigOnAnOldEnvFileTests(unittest.TestCase):
    """Item 1: `install.sh` calls `mdt-host-setup-wizard.py --validate-config` first."""

    def _validate(self, text: str):
        """The real install-path entry point (`main --validate-config`), in-process: the CLI
        refuses to start inside a container, so the host-context probe alone is patched."""
        import contextlib
        import io
        wizard = _load(WIZARD_PATH, "mdt_wizard_hs02_validate")
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "env").write_text(text)
        (d / "meminfo").write_text(MEMINFO)
        buf = io.StringIO()
        with mock.patch.object(wizard, "host_context_error", return_value=None), \
             contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = wizard.main(["--validate-config", str(d / "env"), "--example", str(EXAMPLE),
                              "--meminfo-path", str(d / "meminfo")])

        class Result:
            returncode = rc
            stdout = buf.getvalue()

        return Result()

    def _old_env(self, *, drop_required: str | None = None, extra: str = "") -> str:
        keep = []
        for line in EXAMPLE.read_text().splitlines(keepends=True):
            m = re.match(r"([A-Z][A-Z0-9_]*)=", line)
            if m and OPTIONAL_RE and OPTIONAL_RE.fullmatch(m.group(1)):
                continue
            if m and m.group(1) == drop_required:
                continue
            keep.append(line)
        return "".join(keep) + extra

    def test_control_the_complete_example_passes(self) -> None:
        r = self._validate(EXAMPLE.read_text())
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_an_old_env_file_missing_only_the_optional_keys_passes(self) -> None:
        self.assertIsNotNone(OPTIONAL_RE, "runtime-optional-keys.re is the single definition")
        r = self._validate(self._old_env())
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_an_old_env_file_missing_one_required_key_is_refused_for_that_key_only(self) -> None:
        r = self._validate(self._old_env(drop_required="DEV_CPU_RESERVE_CORES"))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("DEV_CPU_RESERVE_CORES", r.stdout)
        for key in example_keys():
            if OPTIONAL_RE and OPTIONAL_RE.fullmatch(key):
                self.assertNotIn(key, r.stdout, f"{key} must not be reported missing")

    def test_a_present_optional_key_is_still_validated(self) -> None:
        r = self._validate(self._old_env(extra="DEV_GATES_IO_WBPS_MAX=20MB\n"))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("DEV_GATES_IO_WBPS_MAX", r.stdout)

    def test_python_and_shell_share_one_definition(self) -> None:
        self.assertTrue((ROOT / "runtime-optional-keys.re").exists())
        # no second copy of the key set in either language
        self.assertNotIn("IO_BASELINE_ROTATIONAL_FLAG)$", LIB.read_text())
        self.assertNotIn("IO_BASELINE_ROTATIONAL_FLAG)$", WIZARD_PATH.read_text())
        shell = subprocess.run(
            ["bash", "-c", f'log(){{ :; }}; . "{LIB}"; _mdt_missing_declared_keys /dev/null "{EXAMPLE}" optional'],
            text=True, stdout=subprocess.PIPE).stdout.split()
        python = [k for k in example_keys() if OPTIONAL_RE and OPTIONAL_RE.fullmatch(k)]
        self.assertEqual(sorted(shell), sorted(python))
        self.assertEqual(len(python), 21)

    def test_install_runs_the_validator_before_anything_it_exempts(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertLess(install.index("--validate-config"), install.index("_mdt_missing_declared_keys"))


class DamonDefaultTests(unittest.TestCase):
    """Item 2: DAMON_STAT is disabled by default (operator decision 2026-10-20)."""

    def test_the_example_and_the_wizard_default_to_yes(self) -> None:
        self.assertRegex(EXAMPLE.read_text(), r'(?m)^DAMON_STAT_DISABLE_DEFAULT="yes"$')
        wizard = _load(WIZARD_PATH, "mdt_wizard_hs02")
        defaults = wizard.parse_env_file(EXAMPLE.read_text())
        seen = {}
        with mock.patch.object(wizard, "ask_yn", side_effect=lambda q, d, **kw: seen.setdefault("d", d)), \
             mock.patch.object(wizard, "out"):
            self.assertEqual(wizard.step_damon_stat_policy({}, defaults), {"DAMON_STAT_DISABLE_DEFAULT": "yes"})
        self.assertTrue(seen["d"])

    def test_the_docs_state_the_new_default_and_why(self) -> None:
        for path in (ROOT / "README.md", PROJECT / "docs" / "CONSUMERS.md", PROJECT / "docs" / "DESIGN-GUIDE.md"):
            flat = " ".join(path.read_text().split())
            self.assertIn("defaults to disabling", flat, path.name)
            self.assertIn("DAMON sysfs", flat, path.name)
        flat = " ".join((ROOT / "README.md").read_text().split())
        self.assertNotIn('uses `DAMON_STAT_DISABLE_DEFAULT="no"`', flat)
        self.assertNotIn("The non-wizard example keeps the kernel default", " ".join((PROJECT / "docs" / "DESIGN-GUIDE.md").read_text().split()))

    def _run(self, policy: str, *, param_exists: bool, preexisting: bool = False):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        bindir = d / "bin"
        bindir.mkdir()
        calls = d / "tmpfiles.log"
        _write_exec(bindir / "systemd-tmpfiles", f'#!/usr/bin/env bash\necho "$*" >> "{calls}"\nexit 0\n')
        param = d / "enabled"
        if param_exists:
            param.write_text("Y\n")
        target = d / "mdt-damon-stat.conf"
        if preexisting:
            target.write_text("stale\n")
        script = (f'log(){{ echo "$*"; }}; . "{LIB}"; HERE="{ROOT}"; DAMON_STAT_DISABLE_DEFAULT={policy}; '
                  f'DAMON_STAT_TMPFILES="{target}"; MDT_DAMON_PARAM="{param}"; _mdt_apply_damon_policy; echo rc=$?')
        r = subprocess.run(["bash", "-c", script], env={**os.environ, "PATH": f"{bindir}:/usr/bin:/bin"}, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r, target, calls

    def test_yes_installs_the_rule_and_applies_it_now_when_the_kernel_exposes_the_parameter(self) -> None:
        r, target, calls = self._run("yes", param_exists=True)
        self.assertIn("rc=0", r.stdout)
        self.assertRegex(target.read_text(), r"(?m)^w /sys/module/damon_stat/parameters/enabled - - - - N$")
        self.assertIn(f"--create {target}", calls.read_text())

    def test_yes_without_the_kernel_parameter_keeps_the_persistent_rule_and_applies_nothing(self) -> None:
        r, target, calls = self._run("yes", param_exists=False)
        self.assertIn("rc=0", r.stdout)
        self.assertTrue(target.exists())
        self.assertFalse(calls.exists())

    def test_no_removes_a_previous_override(self) -> None:
        r, target, _ = self._run("no", param_exists=True, preexisting=True)
        self.assertIn("rc=0", r.stdout)
        self.assertFalse(target.exists())


class GuardPolicyTests(unittest.TestCase):
    """Item 3: a confirmed terminate policy survives a non-wizard re-install; refusal touches nothing.
    H-18/H-19: the confirmation is bound to the policy, the BuildKit image and the host."""

    IMAGE = "moby/buildkit:buildx-stable-1-rootless"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.machine_id = self.tmp / "machine-id"
        self.machine_id.write_text("0123456789abcdef0123456789abcdef\n")
        self.env = {**os.environ, "MDT_MACHINE_ID_FILE": str(self.machine_id)}

    def _decide(self, policy: str, wizard: int, record: Path, image: str | None = None) -> str:
        image = self.IMAGE if image is None else image
        script = f'log(){{ echo "$*" >&2; }}; . "{LIB}"; _mdt_guard_decision "{policy}" {wizard} "{record}" "{image}"'
        return subprocess.run(["bash", "-c", script], text=True, stdout=subprocess.PIPE, env=self.env,
                              stderr=subprocess.DEVNULL).stdout.strip()

    def _record(self, policy: str = "terminate", image: str | None = None) -> Path:
        record = self.tmp / "guard-terminate.confirmed"
        image = self.IMAGE if image is None else image
        r = subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{LIB}"; _mdt_guard_record_write "{record}" "{policy}" "{image}"'],
                           env=self.env)
        self.assertEqual(r.returncode, 0)
        return record

    def test_three_cases(self) -> None:
        missing = self.tmp / "none"
        # 1. the wizard just obtained the second confirmation: proceed and record it
        self.assertEqual(self._decide("terminate", 1, missing), "record")
        # 2. a non-wizard re-install with an unchanged, already-confirmed terminate policy keeps the guard
        record = self._record()
        self.assertIn("confirmed_at=", record.read_text())
        self.assertIn(f"image={self.IMAGE}", record.read_text())
        self.assertEqual(self._decide("terminate", 0, record), "keep")
        # 3. a NEW terminate setting without a recorded confirmation is refused
        self.assertEqual(self._decide("terminate", 0, missing), "refuse")
        # a tampered or other-policy record is not a confirmation
        record.write_text("policy=terminate\nconfirmed_at=2026-01-01T00:00:00Z\nsha256=deadbeef\n")
        self.assertEqual(self._decide("terminate", 0, record), "refuse")
        # report-only clears any record, so a later terminate needs confirming again
        self.assertEqual(self._decide("report-only", 0, record), "clear")
        self.assertEqual(self._decide("report-only", 1, record), "clear")

    def test_the_record_is_bound_to_the_image_and_to_the_host(self) -> None:
        """H-18: not a constant string."""
        record = self._record()
        self.assertEqual(self._decide("terminate", 0, record), "keep")
        self.assertEqual(self._decide("terminate", 0, record, image="moby/buildkit:another"), "refuse")
        self.machine_id.write_text("ffffffffffffffffffffffffffffffff\n")  # the record moved to another host
        self.assertEqual(self._decide("terminate", 0, record), "refuse")

    def test_shell_and_python_compute_the_same_digest(self) -> None:
        guard = _load(SCRIPTS / "mdt-buildkit-guard.py", "mdt_guard_hs02")
        shell = subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{LIB}"; _mdt_guard_digest terminate "{self.IMAGE}"'],
                               env=self.env, text=True, stdout=subprocess.PIPE).stdout.strip()
        self.assertEqual(shell, guard.guard_digest("terminate", self.IMAGE, "0123456789abcdef0123456789abcdef"))
        none = subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{LIB}"; _mdt_guard_digest terminate x'],
                              env={**os.environ, "MDT_MACHINE_ID_FILE": str(self.tmp / "absent")},
                              text=True, stdout=subprocess.PIPE).stdout.strip()
        self.assertEqual(none, guard.guard_digest("terminate", "x", ""))

    def test_the_guard_service_refuses_to_terminate_without_a_valid_record(self) -> None:
        """H-19 guard side: terminate needs the record; otherwise report-only, loudly; a report-only
        configuration withdraws a stale record."""
        guard = _load(SCRIPTS / "mdt-buildkit-guard.py", "mdt_guard_hs02b")
        record = self.tmp / "rec"
        config = guard.GuardConfig(policy="terminate", buildkit_image=self.IMAGE)
        self.assertEqual(guard.enforce_confirmation(config, record, self.machine_id).policy, "report-only")
        self._record_to(record)
        self.assertEqual(guard.enforce_confirmation(config, record, self.machine_id).policy, "terminate")
        other = guard.GuardConfig(policy="terminate", buildkit_image="moby/buildkit:another")
        self.assertEqual(guard.enforce_confirmation(other, record, self.machine_id).policy, "report-only")
        away = guard.GuardConfig(policy="report-only", buildkit_image=self.IMAGE)
        self.assertEqual(guard.enforce_confirmation(away, record, self.machine_id).policy, "report-only")
        self.assertFalse(record.exists(), "a report-only guard withdraws the stale confirmation")
        # ... so changing away and back (with the guard restarted in between) needs a new confirmation
        self.assertEqual(guard.enforce_confirmation(config, record, self.machine_id).policy, "report-only")

    def _record_to(self, record: Path) -> None:
        r = subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{LIB}"; _mdt_guard_record_write "{record}" terminate "{self.IMAGE}"'],
                           env=self.env)
        self.assertEqual(r.returncode, 0)

    def test_the_refusal_path_tears_nothing_down_and_precedes_every_mutation(self) -> None:
        install = (ROOT / "install.sh").read_text()
        a = install.index("_mdt_guard_decision")
        refuse = install[install.index("refuse)", a):]
        refuse = refuse[:refuse.index(";;")]
        self.assertNotIn("systemctl", refuse)
        self.assertIn("exit 2", refuse)
        self.assertIn("left exactly as it was", refuse)
        # the old code disabled the running guard before refusing
        self.assertNotIn("disable --now mdt-buildkit-guard.service", install[:install.index("# === COMMIT POINT")])
        self.assertLess(a, install.index("# === COMMIT POINT"))

    def test_the_confirmation_is_recorded_durably_at_the_commit_point(self) -> None:
        install = (ROOT / "install.sh").read_text()
        commit = install.index("# === COMMIT POINT")
        self.assertIn("_mdt_guard_record_write", install[commit:])
        self.assertIn("/var/lib/mdt/guard-terminate.confirmed", install)
        self.assertIn('"${DEV_BUILDKITD_IMAGE:-}"', install)

    def test_check_sh_warns_about_terminate_without_a_valid_record(self) -> None:
        check = (SCRIPTS / "check.sh").read_text()
        self.assertIn("_mdt_guard_confirmed", check)
        self.assertIn("NO valid confirmation record", check)


class DamonFailureTests(unittest.TestCase):
    """H-20: an optional DAMON write failure must not abort the install halfway."""

    def _step(self, required: int):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        bindir = d / "bin"
        bindir.mkdir()
        _write_exec(bindir / "systemd-tmpfiles", "#!/usr/bin/env bash\nexit 1\n")
        param = d / "enabled"
        param.write_text("Y\n")
        script = (f'log(){{ echo "$*"; }}; . "{LIB}"; HERE="{ROOT}"; DAMON_STAT_DISABLE_DEFAULT=yes; '
                  f'DAMON_STAT_TMPFILES="{d / "rule"}"; MDT_DAMON_PARAM="{param}"; _mdt_apply_damon_step {required}; echo rc=$?')
        return subprocess.run(["bash", "-c", script], env={**os.environ, "PATH": f"{bindir}:/usr/bin:/bin"}, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT), d / "rule"

    def test_a_failed_write_warns_loudly_and_continues_by_default(self) -> None:
        r, rule = self._step(0)
        self.assertIn("rc=0", r.stdout)
        self.assertIn("DAMON_STAT could NOT be disabled now; the install continues", r.stdout)
        self.assertTrue(rule.exists(), "the boot-time rule stays installed")

    def test_the_operator_can_make_it_fatal(self) -> None:
        r, _ = self._step(1)
        self.assertIn("rc=2", r.stdout)
        self.assertIn("--require-damon-disable", r.stdout)

    def test_install_passes_the_flag_to_the_step(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn("--require-damon-disable) REQUIRE_DAMON=1", install)
        self.assertIn('_mdt_apply_damon_step "$REQUIRE_DAMON"', install)


class SharedReaderRuleTests(unittest.TestCase):
    """H-24: exactly one pattern line, in the shell reader and in the Python reader alike."""

    def test_both_readers_refuse_a_file_with_two_pattern_lines(self) -> None:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        two = d / "two.re"
        two.write_text("# c\n^(A)$\n^(B)$\n")
        shell = subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{LIB}"; MDT_RUNTIME_OPTIONAL_FILE="{two}"; _mdt_runtime_optional_re'],
                               text=True, stdout=subprocess.PIPE).stdout
        self.assertEqual(shell.strip(), "")
        wizard = _load(WIZARD_PATH, "mdt_wizard_hs02_reader")
        with mock.patch.object(wizard, "RUNTIME_OPTIONAL_KEYS_FILE", two):
            with self.assertRaises(wizard.TemplateError):
                wizard.runtime_optional_pattern()
        one = d / "one.re"
        one.write_text("# c\n^(A|B)$\n")
        shell = subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{LIB}"; MDT_RUNTIME_OPTIONAL_FILE="{one}"; _mdt_runtime_optional_re'],
                               text=True, stdout=subprocess.PIPE).stdout
        self.assertEqual(shell.strip(), "^(A|B)$")
        with mock.patch.object(wizard, "RUNTIME_OPTIONAL_KEYS_FILE", one):
            self.assertTrue(wizard.runtime_optional_pattern().fullmatch("A"))

    def test_stale_text_about_the_old_design_is_gone(self) -> None:
        self.assertNotIn("MDT_RUNTIME_OPTIONAL_RE", (ROOT / "install.sh").read_text())
        self.assertNotIn("Installed only", (ROOT / "units" / "mdt-damon-stat.conf").read_text())
        flat = " ".join((ROOT / "README.md").read_text().split())
        self.assertNotIn("mdt-damon-stat.conf` (only when selected)", flat)


class PreflightBeforeActionTests(unittest.TestCase):
    """Item 3 audit rule: validate everything first, then act.

    Choice (H-22): a STRUCTURAL scan, not a list of "bad" line starts. Everything between the
    validated candidate and the COMMIT POINT may call only an allowlist of read-only helpers, may
    not run any command that mutates the host (a deny-list matched as command words after quoted
    strings, comments and arithmetic are removed), and may not redirect output anywhere but
    /dev/null, a file descriptor or the candidate in the temp dir. The allowlisted helpers' own
    bodies are scanned with the same rules, so a "read-only" helper cannot quietly start writing.
    Why not run-time enforcement: install.sh refuses to start in a container (the cockpit has no
    host to protect or test), so a read-only mount probe is only possible in the VM, where
    vm-upgrade-acceptance.sh step 2 compares a FULL snapshot of every directory the installer
    writes before and after a refusal. The static scan keeps it from regressing between VM runs.
    """

    DENY = (r"mkdir|install|apt-get|systemctl|rm|mv|ln|touch|cp|chown|chmod|modprobe|tee|dd|docker|render|"
            r"truncate|useradd|groupadd|patch|mount|umount|sysctl|udevadm|ip|date -s|hostnamectl|timedatectl")
    READ_ONLY_HELPERS = {
        "_mdt_missing_declared_keys", "_mdt_resolve_static_io", "_mdt_io_pin_notice", "_mdt_guard_decision",
        "_mdt_runtime_optional_re", "_mdt_size_to_bytes", "_mdt_guard_confirmed", "_mdt_guard_digest",
    }
    ALLOWED_REDIRECT = re.compile(r'\d*>>?\s*(&\d|/dev/null|"?\$CANDIDATE\w*"?)')

    @classmethod
    def violations(cls, text: str, helpers: set[str] | None = None) -> list[tuple[str, str]]:
        helpers = cls.READ_ONLY_HELPERS if helpers is None else helpers
        found, in_heredoc = [], False
        for raw in text.splitlines():
            if in_heredoc:
                in_heredoc = raw.strip() != "PY"
                continue
            if re.search(r"<<'?PY'?\s*$", raw):
                in_heredoc = True
            if raw.lstrip().startswith("#"):
                continue
            line = cls.ALLOWED_REDIRECT.sub(" ", raw)
            line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
            line = re.sub(r"'[^']*'", "''", line)
            line = re.sub(r"\$\(\(.*?\)\)", " ", line)
            line = re.sub(r"\s#.*$", "", line)
            if re.search(rf"(^|[\s;&|(`])({cls.DENY})(\s|$|;|\))", line) or re.search(r"\bsed\s+-\w*i", line):
                found.append(("command", raw.strip()))
            if re.search(r"(?<![<-])>", line):
                found.append(("redirection", raw.strip()))
            for helper in re.findall(r"\b(_mdt_\w+)\b", line):
                if helper not in helpers:
                    found.append(("helper", helper))
        return found

    @staticmethod
    def function_body(lib: str, name: str) -> str:
        start = lib.index(f"{name}() {{")
        return lib[start:lib.index("\n}\n", start)]

    def test_the_scan_is_sensitive_to_every_way_of_mutating(self) -> None:
        for mutating in ('echo x > /etc/mdt/host-setup.env', 'echo x >> /var/lib/mdt/y', 'mv a b', 'touch /etc/x',
                         'ln -s a b', 'render "$HERE/units/x.in" /etc/y', 'printf y | tee /etc/z', 'mkdir -p /etc/q',
                         'sed -i s/a/b/ /etc/f', '_mdt_guard_record_write "$R" terminate', 'docker rm -f x',
                         '_mdt_apply_damon_step 0', 'cp -- "$CONFIG_PATH" "$backup"', 'x=$(rm -rf /etc/y)'):
            self.assertTrue(self.violations(mutating), mutating)
        for harmless in ('echo "install.sh needs systemctl" >&2', 'x=$(nproc 2>/dev/null) || true',
                         'cores=$(( a > b ? a : b ))', '_mdt_guard_decision a b c d',
                         'echo hi > "$CANDIDATE_PATH"', '# mkdir /etc is only a comment'):
            self.assertEqual(self.violations(harmless), [], harmless)

    def test_the_preflight_region_uses_only_read_only_helpers_and_commands(self) -> None:
        install = (ROOT / "install.sh").read_text()
        region = install[install.index('. "$CANDIDATE_PATH"'):install.index("# === COMMIT POINT")]
        self.assertEqual(self.violations(region), [])

    def test_the_allowlisted_helpers_are_themselves_read_only(self) -> None:
        lib = LIB.read_text()
        for helper in sorted(self.READ_ONLY_HELPERS):
            body = self.function_body(lib, helper)
            self.assertEqual(self.violations(body), [], helper)

    def test_the_installed_config_is_replaced_only_at_the_commit_point(self) -> None:
        install = (ROOT / "install.sh").read_text()
        commit = install.index("# === COMMIT POINT")
        self.assertGreater(install.index('install -m 0644 "$CANDIDATE_PATH" "$CONFIG_PATH"'), commit)
        self.assertGreater(install.index('mkdir -p "$CONFIG_DIR"'), commit)


class VmUpgradeStepTests(unittest.TestCase):
    """The VM lane always installed from a fresh, complete env; the upgrade step covers the real path."""

    def test_the_lane_runs_the_upgrade_acceptance_after_the_governance_script(self) -> None:
        lane = (ROOT / "tests" / "run-vm-acceptance.sh").read_text()
        self.assertLess(lane.index("vm-governance-acceptance.sh"), lane.index("vm-upgrade-acceptance.sh"))

    def test_the_upgrade_script_removes_the_optional_keys_and_runs_a_plain_install(self) -> None:
        script = (ROOT / "tests" / "vm-upgrade-acceptance.sh").read_text()
        self.assertIn("runtime-optional-keys.re", script)
        self.assertRegex(script, r'bash "\$HS/install\.sh"')
        self.assertNotIn("--wizard", script.replace("# ", ""))  # a plain install, never the wizard
        for needle in ("has no recorded confirmation", "was already confirmed", "stayed active", "DAMON_STAT"):
            self.assertIn(needle, script)


class BuildkitdSocketWaitTests(unittest.TestCase):
    def test_the_socket_wait_tolerates_a_slow_emulated_guest(self) -> None:
        """The 4th restart in one vm-system run failed at the old 10 s (100 x 0.1 s) limit."""
        unit = (ROOT / "units" / "mdt-buildkitd.service.in").read_text()
        match = re.search(r'while \[ "\$attempt" -lt (\d+) \]; do socket=/run/mdt-buildkitd/buildkitd\.sock', unit)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)) * 0.1, 60)


if __name__ == "__main__":
    unittest.main()

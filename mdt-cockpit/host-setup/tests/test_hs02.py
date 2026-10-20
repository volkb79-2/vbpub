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
    """Item 3: a confirmed terminate policy survives a non-wizard re-install; refusal touches nothing."""

    def _decide(self, policy: str, wizard: int, record: Path) -> str:
        script = (f'log(){{ echo "$*" >&2; }}; . "{LIB}"; _mdt_guard_decision "{policy}" {wizard} "{record}"')
        return subprocess.run(["bash", "-c", script], text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL).stdout.strip()

    def _record(self, tmp: Path, policy: str = "terminate") -> Path:
        record = tmp / "guard-terminate.confirmed"
        r = subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{LIB}"; _mdt_guard_record_write "{record}" "{policy}"'])
        self.assertEqual(r.returncode, 0)
        return record

    def test_three_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            missing = tmp / "none"
            # 1. the wizard just obtained the second confirmation: proceed and record it
            self.assertEqual(self._decide("terminate", 1, missing), "record")
            # 2. a non-wizard re-install with an unchanged, already-confirmed terminate policy keeps the guard
            record = self._record(tmp)
            self.assertIn("confirmed_at=", record.read_text())
            self.assertEqual(self._decide("terminate", 0, record), "keep")
            # 3. a NEW terminate setting without a recorded confirmation is refused
            self.assertEqual(self._decide("terminate", 0, missing), "refuse")
            # a tampered or other-policy record is not a confirmation
            record.write_text("policy=terminate\nconfirmed_at=2026-01-01T00:00:00Z\nsha256=deadbeef\n")
            self.assertEqual(self._decide("terminate", 0, record), "refuse")
            # report-only clears any record, so a later terminate needs confirming again
            self.assertEqual(self._decide("report-only", 0, record), "clear")
            self.assertEqual(self._decide("report-only", 1, record), "clear")

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


class PreflightBeforeActionTests(unittest.TestCase):
    """Item 3 audit rule: validate everything first, then act."""

    MUTATING = re.compile(r'(?m)^\s*(mkdir\b|install\b|apt-get\b|systemctl\b|rm\s|sed\s+-i|cp\s+--\s+"\$CONFIG_PATH"|'
                          r'chown\b|chmod\b|modprobe\b|tee\b)')

    def test_nothing_between_candidate_validation_and_the_commit_point_mutates_the_host(self) -> None:
        install = (ROOT / "install.sh").read_text()
        start = install.index('. "$CANDIDATE_PATH"')
        end = install.index("# === COMMIT POINT")
        region = "\n".join(ln for ln in install[start:end].splitlines() if not ln.lstrip().startswith("#"))
        self.assertIsNone(self.MUTATING.search(region), self.MUTATING.search(region) and self.MUTATING.search(region).group(0))

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

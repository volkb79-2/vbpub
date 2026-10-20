"""Fix round 2 for MDT-HS-01 (review H-11..H-14)."""
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
from test_io_governance import Host, ROOT, SCRIPTS, _load, _write_exec  # noqa: E402

PROJECT = ROOT.parent
TOOLS = ROOT.parents[1] / "scripts" / "debian-install-v2" / "tools"
INFLATED = {"RBW_MAX_BPS": "4287506096", "WBW_MAX_BPS": "2001630742", "RRANDIOPS": "94246",
            "WRANDIOPS": "67748", "DEVICE_ROTATIONAL": "1"}


class RotationalFlagFromConfigTests(unittest.TestCase):
    """H-11: the flag documented as a host-setup.env setting must reach the tool that WRITES the baseline."""

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(ROOT))
        cls.base = _load(SCRIPTS / "mdt-io-baseline.py", "mdt_io_baseline_round2")

    def _conf(self, body: str) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        path = d / "host-setup.env"
        path.write_text(body)
        return path

    def _env(self, conf: Path | None):
        env = {k: v for k, v in os.environ.items() if k != "IO_BASELINE_ROTATIONAL_FLAG"}
        if conf is not None:
            env["MDT_HOST_SETUP_CONF"] = str(conf)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_without_the_flag_the_inflated_numbers_are_refused(self) -> None:
        with self._env(self._conf("IO_BASELINE_ROTATIONAL_FLAG=\n")):
            self.assertIn("implausible", self.base.plausibility_problem(dict(INFLATED)) or "")

    def test_the_flag_set_only_in_the_config_file_is_honoured_by_the_writer(self) -> None:
        for line in ("IO_BASELINE_ROTATIONAL_FLAG=ignore\n", 'IO_BASELINE_ROTATIONAL_FLAG="ignore"\n'):
            with self.subTest(line=line), self._env(self._conf("DEV_IO_CAP_PCT=60\n" + line)):
                self.assertIsNone(self.base.plausibility_problem(dict(INFLATED)))

    def test_the_check_results_path_honours_it_too(self) -> None:
        fields = {k: "1" for k in self.base.REQUIRED_RESULT_FIELDS}
        fields.update(INFLATED)
        fields.update({"RBPS": "4287506096", "WBPS": "2001630742", "RRANDIOPS": "94246", "WRANDIOPS": "67748",
                       "SCHEMA_VERSION": "3", "MEASURE_METHOD": "iocost-coef-gen", "MEASURED_AT": "x"})
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        results = d / "io-baseline.env"
        results.write_text("".join(f"{k}={v}\n" for k, v in fields.items()))
        with self._env(self._conf("IO_BASELINE_ROTATIONAL_FLAG=\n")):
            self.assertIn("implausible", self.base.results_problem(results) or "")
        with self._env(self._conf("IO_BASELINE_ROTATIONAL_FLAG=ignore\n")):
            self.assertIsNone(self.base.results_problem(results))

    def test_the_refusal_says_where_the_flag_is_read(self) -> None:
        with self._env(None):
            problem = self.base.plausibility_problem(dict(INFLATED)) or ""
        self.assertIn("host-setup.env", problem)

    def test_the_wizard_benchmark_child_inherits_the_flag_from_the_existing_config(self) -> None:
        wizard = _load(ROOT / "mdt-host-setup-wizard.py", "mdt_wizard_round2")
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "b.env"
            target = Path(tmp) / "t"
            process = mock.Mock(pid=1)
            with mock.patch.object(wizard, "walk_key", side_effect=[str(results), str(target)]), \
                 mock.patch.object(wizard, "check_benchmark_results", return_value="changed"), \
                 mock.patch.object(wizard.shutil, "which", return_value="/usr/bin/fio"), \
                 mock.patch.object(wizard, "ask_yn", return_value=True), \
                 mock.patch.object(wizard.subprocess, "Popen", return_value=process) as popen, \
                 mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                              if k != "IO_BASELINE_ROTATIONAL_FLAG"}, clear=True):
                wizard.step_io_baseline_start({"IO_BASELINE_ROTATIONAL_FLAG": "ignore"}, {},
                                              SCRIPTS / "mdt-io-baseline.py", "/dev/sda", status_path=None)
            self.assertEqual(popen.call_args.kwargs["env"].get("IO_BASELINE_ROTATIONAL_FLAG"), "ignore")


class NoDeviceStatusTests(unittest.TestCase):
    """H-12: 'systemctl show failed' is not 'dev.slice has no limits'."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Host(Path(self._tmp.name))
        self.host.config_lines[0] = "IO_DEV_PATH="
        self.host.extra_env["FAKE_FINDMNT_SOURCE"] = "overlay"

    def test_a_failing_systemctl_show_is_an_error_not_nothing_to_cap(self) -> None:
        self.host.unit_static = {}
        result = self.host.run(FAKE_SHOW_FAILS="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("ERROR: could not read dev.slice's IO limits", result.stdout)
        self.assertNotIn("nothing to cap", result.stdout)
        self.assertEqual([c for c in self.host.calls() if c.startswith("set-property")], [])

    def test_a_clean_show_with_no_limits_is_still_nothing_to_cap(self) -> None:
        self.host.unit_static = {}
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("nothing to cap", result.stdout)

    def test_the_container_sweep_strip_on_a_transient_findmnt_failure_is_a_filed_backlog_item(self) -> None:
        backlog = (PROJECT / "KNOWN_ISSUES_TODO_BACKLOG.md").read_text()
        self.assertRegex(backlog, r"## MDT-019 - .*(findmnt|no device).*container")


class VmLaneBudgetTests(unittest.TestCase):
    """H-13: evidence must survive a killed ssh session, and the outer timeout must cover the inner ones."""

    def test_the_outer_guest_timeout_exceeds_the_sum_of_the_inner_ones_plus_margin(self) -> None:
        lane = (ROOT / "tests" / "run-vm-acceptance.sh").read_text()
        outer = int(re.search(r'run_with_timeout (\d+)m "\$HARNESS" ssh "\$RUN" -- \\\n\s+"set -eu', lane).group(1))
        pull_s = int(re.search(r"timeout (\d+) docker pull",
                               (ROOT / "tests" / "vm-install-with-diagnostics.sh").read_text()).group(1))
        unit = (ROOT / "units" / "mdt-buildkitd.service.in").read_text()
        start_min = int(re.search(r"(?m)^TimeoutStartSec=(\d+)min$", unit).group(1))
        inner = pull_s / 60 + start_min + 15 + 15  # + install.sh's other work, the governance script, 3 re-installs
        self.assertGreaterEqual(outer, inner + 5, f"outer {outer}m vs inner {inner}m")
        budget = re.search(r'\[lanes\.vm-system\].*?budget = "(\d+)m"', (ROOT.parent / "run-gate.toml").read_text(), re.S)
        self.assertGreater(int(budget.group(1)), outer + 15 + 2 + 10 + 12 + 5 + 2 + 5)  # the steps before it

    def test_diagnostics_are_dumped_from_the_host_side_over_a_fresh_ssh_before_destroy(self) -> None:
        lane = (ROOT / "tests" / "run-vm-acceptance.sh").read_text()
        body = lane[lane.index("cleanup() {"):lane.index("trap cleanup EXIT")]
        self.assertIn("vm-dump-diagnostics.sh", body)
        self.assertLess(body.index("vm-dump-diagnostics.sh"), body.index('destroy "$RUN"'))
        self.assertIn("rc=$?", body)
        # no reliance on a guest-side trap surviving a killed ssh session
        self.assertNotIn("trap 'rc=", lane.replace(body, ""))


class CalibrateMissingModuleTests(unittest.TestCase):
    """H-14"""

    def test_a_missing_shared_module_is_refused_with_a_clear_message(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        tools = tmp / "tools"
        tools.mkdir()
        shutil.copy(TOOLS / "iocost-calibrate.sh", tools / "iocost-calibrate.sh")
        (tools / "iocost_coef_gen.py").write_text("print('254:0 rbps=1 rseqiops=1 rrandiops=1 wbps=1 wseqiops=1 wrandiops=1')\n")
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        for name in ("findmnt", "pv", "dd", "fio"):
            _write_exec(bin_dir / name, "#!/usr/bin/env bash\nexit 0\n")
        env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "IOCOST_RESULT_DIR": str(tmp / "out"),
               "IOCOST_SYSFS_ROOT": str(tmp / "sys")}
        r = subprocess.run(["bash", str(tools / "iocost-calibrate.sh")], input="yes\n", env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("iocost_plausibility.py is missing", r.stdout)
        self.assertNotIn("Traceback", r.stdout)
        self.assertFalse((tmp / "out" / "iocost-model.env").exists())


if __name__ == "__main__":
    unittest.main()


class FinalRoundTests(unittest.TestCase):
    def _bash(self, script: str, **env: str) -> str:
        lib = SCRIPTS / "mdt-container-io-caps.lib.sh"
        return subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{lib}"; {script}'], env={**os.environ, **env},
                              text=True, stdout=subprocess.PIPE).stdout.strip()

    def test_no_active_swap_is_not_reported_as_every_swap_disk_capped(self) -> None:
        """H-15"""
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        head = "Filename Type Size Used Priority\n"
        (d / "swaps").write_text(head)
        self.assertEqual(self._bash("_mdt_active_swap_count", MDT_PROC_ROOT=str(d)), "0")
        (d / "swaps").write_text(head + "/dev/zram0 partition 1 0 100\n")
        self.assertEqual(self._bash("_mdt_active_swap_count", MDT_PROC_ROOT=str(d)), "0")
        (d / "swaps").write_text(head + "/dev/vda6 partition 1 0 -2\n/dev/zram0 partition 1 0 100\n")
        self.assertEqual(self._bash("_mdt_active_swap_count", MDT_PROC_ROOT=str(d)), "1")
        check = (SCRIPTS / "check.sh").read_text()
        self.assertIn("no active swap", check)
        self.assertIn("_mdt_active_swap_count", check)

    def test_pin_only_hosts_are_told_the_slice_level_caps_are_the_whole_protection(self) -> None:
        """H-16"""
        for path in (ROOT / "README.md", PROJECT / "docs" / "CONSUMERS.md"):
            flat = " ".join(path.read_text().split())
            self.assertIn("pin-only host", flat, path.name)
            self.assertIn("IOWeight=1", flat, path.name)
            self.assertIn("inactive", flat, path.name)
            self.assertIn("slice-level caps", flat, path.name)

    def test_the_baseline_tool_uses_a_specific_config_override_not_a_bare_conf(self) -> None:
        """H-17: a generic CONF in an operator's shell must not redirect the baseline tool."""
        src = (SCRIPTS / "mdt-io-baseline.py").read_text()
        self.assertIn("MDT_HOST_SETUP_CONF", src)
        self.assertNotIn('os.environ.get("CONF")', src)

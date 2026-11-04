"""HB-3: `mdt host-setup cpu-benchmark` wrapper, doctor and check.sh section. Hermetic: fake tool dir, fake runner,
fixture facts directory, fake /proc identity. Nothing measures, downloads or touches /var/lib/mdt."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST_SETUP = HERE.parent
TOOLS = HERE.parents[2] / "scripts" / "debian-install-v2" / "tools"
WRAPPER = HOST_SETUP / "scripts" / "mdt-host-cpu-benchmark.py"
CHECK = HOST_SETUP / "scripts" / "check.sh"
INSTALL = HOST_SETUP / "install.sh"

_spec = importlib.util.spec_from_file_location("mdt_host_cpu_benchmark_under_test", WRAPPER)
W = importlib.util.module_from_spec(_spec)
sys.modules["mdt_host_cpu_benchmark_under_test"] = W
_spec.loader.exec_module(W)

MODEL = "AMD EPYC 9634 84-Core Processor"
RECORD = {
    "schema": "mdt-host-cpu-benchmark/1", "tool": "geekbench", "tool_version": "7.1.0", "tool_major": 7,
    "measured_at": "2026-11-04T19:10:00Z", "cpu_model": MODEL, "cpus_online": 4, "hypervisor": True,
    "single_core": 2900, "multi_core": 9000, "result_url": "https://browser.geekbench.com/v7/cpu/1", "reason": None,
}


class FakeEnv:
    """Stands in for the tool's Env: only the two reads cpu_identity() makes."""

    def __init__(self, model: str = MODEL, cpus: int = 4) -> None:
        self.model, self.cpus = model, cpus

    def read_text(self, path: str) -> str:
        assert path == "/proc/cpuinfo"
        return f"processor : 0\nmodel name\t: {self.model}\nflags : fpu hypervisor\n"

    def cpu_count(self) -> int:
        return self.cpus


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.tools, self.facts = root / "lib", root / "facts"
        self.tools.mkdir()
        self.facts.mkdir()
        for name in ("cpu_benchmark.py", "cpu_benchmark.py.sha256", "cpu_benchmark.pins.json"):
            (self.tools / name).write_bytes((TOOLS / name).read_bytes())

    def write_record(self, record: dict | None = None, mode: int = 0o644) -> Path:
        path = self.facts / "cpu-benchmark.json"
        path.write_text(json.dumps(record or RECORD))
        path.chmod(mode)
        return path

    def run_wrapper(self, **kw):
        calls: list[list[str]] = []
        kw.setdefault("psi", lambda: 0.5)
        kw.setdefault("machine", "x86_64")
        code = W.run_cpu_benchmark(tool_dir=self.tools, facts=self.facts, runner=lambda argv: calls.append(argv) or 0, **kw)
        return code, calls


class RunTests(Base):
    def test_measures_with_both_pins_psi_limit_and_produced_by(self) -> None:
        code, calls = self.run_wrapper(force=False, resolve=False)
        self.assertEqual(code, 0)
        argv = calls[0]
        pins = json.loads((TOOLS / "cpu_benchmark.pins.json").read_text())["x86_64"]
        self.assertEqual(argv[argv.index("--geekbench-version") + 1], pins["version"])
        self.assertEqual(argv[argv.index("--sha256") + 1], pins["sha256"])
        self.assertEqual(argv[argv.index("--max-psi") + 1], "5")
        self.assertIn("mdt host-setup", argv[argv.index("--produced-by") + 1])
        self.assertEqual(argv[argv.index("--state-dir") + 1], str(self.facts))
        self.assertNotIn("--force", argv)
        self.assertEqual(argv[1], str(self.tools / "cpu_benchmark.py"))

    def test_refuses_existing_file_without_force_and_never_runs(self) -> None:
        self.write_record()
        with self.assertRaises(W.Refused) as ctx:
            self.run_wrapper(force=False, resolve=False)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--force", str(ctx.exception))

    def test_force_remeasures(self) -> None:
        self.write_record()
        _, calls = self.run_wrapper(force=True, resolve=False)
        self.assertIn("--force", calls[0])

    def test_refuses_under_cpu_pressure_at_5_and_above_not_below(self) -> None:
        for pressure in (5.0, 7.3, 80.0):
            with self.subTest(pressure=pressure), self.assertRaises(W.Refused) as ctx:
                self.run_wrapper(force=False, resolve=False, psi=lambda p=pressure: p)
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("pressure", str(ctx.exception))
        code, calls = self.run_wrapper(force=False, resolve=False, psi=lambda: 4.99)
        self.assertEqual((code, len(calls)), (0, 1))

    def test_unreadable_pressure_refuses(self) -> None:
        with self.assertRaises(W.Refused):
            self.run_wrapper(force=False, resolve=False, psi=lambda: None)

    def test_force_does_not_bypass_the_pressure_refusal(self) -> None:
        self.write_record()
        with self.assertRaises(W.Refused):
            self.run_wrapper(force=True, resolve=False, psi=lambda: 9.0)

    def test_unpinned_architecture_refused(self) -> None:
        with self.assertRaises(W.Refused) as ctx:
            self.run_wrapper(force=False, resolve=False, machine="riscv64")
        self.assertIn("no pinned Geekbench archive", str(ctx.exception))

    def test_tool_tamper_refused_before_anything_runs(self) -> None:
        (self.tools / "cpu_benchmark.py").write_text("#!/usr/bin/env python3\nprint('tampered')\n")
        with self.assertRaises(W.Refused) as ctx:
            self.run_wrapper(force=False, resolve=False)
        self.assertEqual(ctx.exception.code, 3)
        self.assertIn("integrity", str(ctx.exception))

    def test_missing_tool_and_missing_pin_file(self) -> None:
        (self.tools / "cpu_benchmark.pins.json").unlink()
        with self.assertRaises(W.Refused):
            self.run_wrapper(force=False, resolve=False)
        (self.tools / "cpu_benchmark.py").unlink()
        with self.assertRaises(W.Refused) as ctx:
            self.run_wrapper(force=False, resolve=False)
        self.assertEqual(ctx.exception.code, 3)

    def test_resolve_needs_a_record_skips_pressure_and_never_passes_pins_or_force(self) -> None:
        with self.assertRaises(W.Refused):
            self.run_wrapper(force=False, resolve=True)
        self.write_record()
        _, calls = self.run_wrapper(force=False, resolve=True, psi=lambda: 99.0)  # a read of a web page is fine under load
        self.assertIn("--resolve", calls[0])
        for flag in ("--sha256", "--geekbench-version", "--force"):
            self.assertNotIn(flag, calls[0])
        with self.assertRaises(W.Refused):
            self.run_wrapper(force=True, resolve=True)

    def test_exit_code_of_the_tool_is_passed_through(self) -> None:
        code = W.run_cpu_benchmark(force=False, resolve=False, tool_dir=self.tools, facts=self.facts,
                                   psi=lambda: 0.1, machine="x86_64", runner=lambda argv: 4)
        self.assertEqual(code, 4)

    def test_installed_tool_pin_is_the_repo_pin(self) -> None:
        self.assertEqual(hashlib.sha256((TOOLS / "cpu_benchmark.py").read_bytes()).hexdigest(),
                         (TOOLS / "cpu_benchmark.py.sha256").read_text().split()[0])


class DoctorTests(Base):
    def doctor(self, env: FakeEnv | None = None):
        return W.doctor(tool_dir=self.tools, facts=self.facts, env=env or FakeEnv())

    def test_missing_measurement_warns_with_the_command(self) -> None:
        (level, message), = self.doctor()
        self.assertEqual(level, "WARN")
        self.assertIn("cpu-benchmark", message)
        self.assertIn("no CPU benchmark measurement", message)

    def test_current_measurement_is_ok(self) -> None:
        self.write_record()
        (level, message), = self.doctor()
        self.assertEqual(level, "OK")
        self.assertIn("single=2900", message)
        self.assertIn("2026-11-04", message)

    def test_cpu_model_mismatch_is_stale(self) -> None:
        self.write_record()
        findings = self.doctor(FakeEnv(model="Intel(R) Xeon(R) Gold 6330"))
        self.assertEqual([f[0] for f in findings], ["WARN"])
        self.assertIn("STALE", findings[0][1])
        self.assertIn("cpu_model", findings[0][1])
        self.assertIn("--force", findings[0][1])

    def test_vcpu_count_mismatch_is_stale(self) -> None:
        self.write_record()
        findings = self.doctor(FakeEnv(cpus=8))
        self.assertIn("STALE", findings[0][1])
        self.assertIn("cpus_online 4 != live 8", findings[0][1])

    def test_partial_record_points_to_resolve(self) -> None:
        self.write_record({**RECORD, "single_core": None, "multi_core": None, "reason": "SCORES_NOT_FOUND"})
        (level, message), = self.doctor()
        self.assertEqual(level, "WARN")
        self.assertIn("--resolve", message)

    def test_garbage_or_wrong_schema_warns_never_raises(self) -> None:
        (self.facts / "cpu-benchmark.json").write_text("{not json")
        self.assertEqual(self.doctor()[0][0], "WARN")
        self.write_record({"schema": "other/1"})
        self.assertEqual(self.doctor()[0][0], "WARN")

    def test_claim_file_must_be_private_and_the_readable_file_claim_free(self) -> None:
        self.write_record()
        claim = self.facts / "cpu-benchmark.claim.json"
        claim.write_text('{"claim_url": "x"}')
        claim.chmod(0o644)
        findings = self.doctor()
        self.assertTrue(any("claim" in m and "0644" in m for _, m in findings))
        claim.chmod(0o600)
        self.assertEqual([lvl for lvl, _ in self.doctor()], ["OK"])
        self.write_record({**RECORD, "claim_url": "https://browser.geekbench.com/v7/cpu/1/claim?key=1"})
        self.assertTrue(any("mentions a claim link" in m for _, m in self.doctor()))

    def test_world_writable_record_warns(self) -> None:
        self.write_record(mode=0o666)
        self.assertTrue(any("writable by group/others" in m for _, m in self.doctor()))

    def test_tool_tamper_degrades_to_a_warning(self) -> None:
        self.write_record()
        (self.tools / "cpu_benchmark.py").write_text("x = 1\n")
        findings = self.doctor()
        self.assertEqual(findings[-1][0], "WARN")
        self.assertIn("cannot compare", findings[-1][1])


class CheckShTests(Base):
    """check.sh's CPU benchmark section, run for real against a fixture facts dir (bash + python3 only)."""

    def section(self, env_extra: dict[str, str] | None = None, wrapper: Path | None = None) -> str:
        text = CHECK.read_text()
        start = text.index('echo "== per-host CPU benchmark (HB-3) =="')
        end = text.index('echo "== container placement (informational) =="')
        script = (
            'set -uo pipefail\nWARN=0\nok(){ printf "OK|%s\\n" "$*"; }\nwarn(){ printf "WARN|%s\\n" "$*"; }\n'
            'info(){ printf "INFO|%s\\n" "$*"; }\n' + text[start:end]
        )
        env = {**os.environ, "MDT_HOST_FACTS_DIR": str(self.facts), "CPUBENCH_PY": str(wrapper or WRAPPER), **(env_extra or {})}
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, check=True).stdout

    def test_missing_measurement_is_a_warn_line(self) -> None:
        out = self.section()
        self.assertRegex(out, r"WARN\|no CPU benchmark measurement")

    def test_missing_helper_is_a_warn_not_a_failure(self) -> None:
        out = self.section(wrapper=Path(self.tmp.name) / "absent.py")
        self.assertRegex(out, r"WARN\|.*missing")

    def test_section_is_registered_before_the_result_line(self) -> None:
        text = CHECK.read_text()
        self.assertLess(text.index("per-host CPU benchmark (HB-3)"), text.rindex('echo "result:'))


class InstallShTests(unittest.TestCase):
    def test_subcommand_helper_and_usage_are_wired(self) -> None:
        text = INSTALL.read_text()
        self.assertIn("cpu-benchmark) CPU_BENCHMARK=1 ;;", text)
        self.assertIn("sha256sum --check --status cpu_benchmark.py.sha256", text)
        self.assertIn("mdt-host-cpu-benchmark.py", text)
        self.assertIn("install.sh cpu-benchmark [--force|--resolve]", text)
        # the full install only INSTALLS the helper; it never measures
        self.assertEqual(text.count("exec python3 /usr/local/sbin/mdt-host-cpu-benchmark.py cpu-benchmark"), 1)

    def test_install_sh_still_parses(self) -> None:
        subprocess.run(["bash", "-n", str(INSTALL)], check=True)
        subprocess.run(["bash", "-n", str(CHECK)], check=True)

    def test_help_documents_the_subcommand(self) -> None:
        out = subprocess.run(["bash", str(INSTALL), "--help"], capture_output=True, text=True, check=True).stdout
        self.assertIn("cpu-benchmark", out)
        self.assertIn("5 or more", out)

    def test_flags_only_apply_to_the_subcommand(self) -> None:
        # runs before any host action: --force without the subcommand exits 2 after the container/root checks, so
        # assert on the source ordering instead of executing as root.
        text = INSTALL.read_text()
        self.assertLess(text.index("only applies to the cpu-benchmark subcommand"), text.index("if [ \"$OBSERVER_ONLY\" = 1 ]"))


class WrapperHelpTests(unittest.TestCase):
    def test_help_and_executable_bits(self) -> None:
        out = subprocess.run([sys.executable, str(WRAPPER), "--help"], capture_output=True, text=True, check=True).stdout
        self.assertIn("cpu-benchmark", out)
        self.assertIn("doctor", out)


if __name__ == "__main__":
    unittest.main()

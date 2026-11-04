"""HB-1: scripts/debian-install-v2/tools/cpu_benchmark.py, hermetic (no Geekbench, no network, no /proc)."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "cpu_benchmark"
TOOL_DIR = HERE.parents[2] / "scripts" / "debian-install-v2" / "tools"
TOOL_PATH = TOOL_DIR / "cpu_benchmark.py"

_spec = importlib.util.spec_from_file_location("cpu_benchmark_under_test", TOOL_PATH)
CB = importlib.util.module_from_spec(_spec)
sys.modules["cpu_benchmark_under_test"] = CB
_spec.loader.exec_module(CB)

STDOUT_FULL = (FIXTURES / "gb6-stdout-full.txt").read_text()
STDOUT_NO_SCORES = (FIXTURES / "gb6-stdout-no-scores.txt").read_text()
PAGE = (FIXTURES / "gb6-result-page.html").read_text()
PAGE_EMPTY = (FIXTURES / "gb6-result-page-no-scores.html").read_text()
CLAIM = "https://browser.geekbench.com/v6/cpu/15956833/claim?key=526696"
RESULT = "https://browser.geekbench.com/v6/cpu/15956833"
PROC_STAT = ("cpu  {user} 0 {sys} {idle} 0 0 0 {steal} 0 0\n")


def _tarball(members: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (members or {"Geekbench-6.3.0-Linux/geekbench6": b"#!/bin/sh\nexit 0\n"}).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeEnv(CB.Env):
    def __init__(self, *, stdout: str = STDOUT_FULL, rc: int = 0, page: str | Exception = PAGE, psi: float = 1.0,
                 tarball: bytes | None = None, timeout: bool = False) -> None:
        self.stdout, self.rc, self.page, self.psi = stdout, rc, page, psi
        self.tarball = tarball if tarball is not None else _tarball()
        self.timeout = timeout
        self.runs: list[list[str]] = []
        self.page_fetches: list[str] = []
        self.downloads: list[str] = []
        self._ticks = 0
        self._stat_calls = 0

    def now(self):
        import datetime as dt
        return dt.datetime(2026, 11, 4, 19, 10, tzinfo=dt.timezone.utc)

    def monotonic(self) -> float:
        self._ticks += 1
        return 100.0 + 540.0 * (self._ticks - 1)

    def machine(self) -> str:
        return "x86_64"

    def cpu_count(self) -> int:
        return 4

    def read_text(self, path: str) -> str:
        if path == "/proc/pressure/cpu":
            return f"some avg10={self.psi:.2f} avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        if path == "/proc/loadavg":
            return "0.10 0.05 0.01 1/200 1234\n"
        if path == "/proc/stat":
            self._stat_calls += 1
            if self._stat_calls == 1:
                return PROC_STAT.format(user=1000, sys=500, idle=8500, steal=0)
            return PROC_STAT.format(user=1900, sys=600, idle=17400, steal=100)  # +10000 total, +100 steal
        if path == "/proc/cpuinfo":
            return "processor\t: 0\nmodel name\t: AMD EPYC  9634 84-Core Processor\nflags\t\t: fpu vme hypervisor lm\n"
        raise OSError(path)

    def fetch_file(self, url: str, dest: Path) -> None:
        self.downloads.append(url)
        dest.write_bytes(self.tarball)

    def fetch_text(self, url: str) -> str:
        self.page_fetches.append(url)
        if isinstance(self.page, Exception):
            raise self.page
        return self.page

    def run(self, argv, timeout, cwd):
        self.runs.append(list(argv))
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        return self.rc, self.stdout


def _tarball_sha() -> str:
    return hashlib.sha256(_tarball()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "host-facts"

    def argv(self, *extra: str) -> list[str]:
        return ["--state-dir", str(self.state), "--geekbench-version", "6.3.0", "--sha256", _tarball_sha(), *extra]

    def invoke(self, env: FakeEnv, *extra: str, base: bool = True):
        out, err = io.StringIO(), io.StringIO()
        argv = self.argv(*extra) if base else ["--state-dir", str(self.state), *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = CB.main(argv, env)
        return code, out.getvalue(), err.getvalue()

    def record(self) -> dict:
        return json.loads((self.state / CB.RESULT_NAME).read_text())

    def mode(self, name: str) -> int:
        return stat.S_IMODE(os.stat(self.state / name).st_mode)


class ParserTests(unittest.TestCase):
    def test_stdout_with_scores_and_links(self):
        got = CB.parse_stdout(STDOUT_FULL)
        self.assertEqual((got["single_core"], got["multi_core"]), (1801, 5702))
        self.assertEqual(got["version"], "6.3.0")
        self.assertEqual(got["result_url"], RESULT)
        self.assertEqual(got["result_id"], "15956833")
        self.assertEqual(got["claim_url"], CLAIM)

    def test_stdout_without_scores_keeps_links(self):
        got = CB.parse_stdout(STDOUT_NO_SCORES)
        self.assertIsNone(got["single_core"])
        self.assertIsNone(got["multi_core"])
        self.assertEqual(got["result_url"], RESULT)
        self.assertEqual(got["claim_url"], CLAIM)

    def test_stdout_claim_only_still_yields_result_url(self):
        got = CB.parse_stdout("link: " + CLAIM)
        self.assertEqual(got["result_url"], RESULT)

    def test_garbage_has_nothing(self):
        got = CB.parse_stdout("nothing here")
        self.assertTrue(all(v is None for v in got.values()))

    def test_page_scores(self):
        self.assertEqual(CB.parse_result_page(PAGE), {"single_core": 1801, "multi_core": 5702})

    def test_page_without_scores(self):
        self.assertEqual(CB.parse_result_page(PAGE_EMPTY), {"single_core": None, "multi_core": None})

    def test_page_label_before_number_layout(self):
        html = "<td>Single-Core Score</td><td>1,234</td><td>Multi-Core Score</td><td>4,321</td>"
        self.assertEqual(CB.parse_result_page(html), {"single_core": 1234, "multi_core": 4321})

    def test_implausible_scores_are_dropped(self):
        self.assertIsNone(CB.parse_stdout("Single-Core Score  0")["single_core"])
        self.assertIsNone(CB.parse_stdout("Single-Core Score  99999999")["single_core"])

    def test_redact_hides_claim_key(self):
        self.assertNotIn("526696", CB.redact("see " + CLAIM + " now"))


class MeasureTests(Base):
    def test_success_writes_both_files_with_modes(self):
        env = FakeEnv()
        code, out, err = self.invoke(env, "--produced-by", "mdt host-setup test")
        self.assertEqual(code, 0, err)
        rec = self.record()
        self.assertEqual(rec["schema"], "mdt-host-cpu-benchmark/1")
        self.assertEqual((rec["single_core"], rec["multi_core"], rec["score_source"]), (1801, 5702, "stdout"))
        self.assertEqual((rec["cpu_model"], rec["cpus_online"], rec["hypervisor"]), ("AMD EPYC 9634 84-Core Processor", 4, True))
        self.assertEqual((rec["tool"], rec["tool_version"], rec["tool_sha256"]), ("geekbench", "6.3.0", _tarball_sha()))
        self.assertEqual((rec["result_url"], rec["result_id"]), (RESULT, "15956833"))
        self.assertEqual(rec["measured_at"], "2026-11-04T19:10:00Z")
        self.assertEqual(rec["conditions"], {"loadavg1_start": 0.1, "psi_cpu_some_avg10_max": 1.0, "steal_fraction": 0.01,
                                             "duration_seconds": 540})
        self.assertEqual(self.mode(CB.RESULT_NAME), 0o644)
        self.assertEqual(self.mode(CB.CLAIM_NAME), 0o600)
        claim = json.loads((self.state / CB.CLAIM_NAME).read_text())
        self.assertEqual((claim["schema"], claim["claim_url"], claim["result_url"]), (CB.CLAIM_SCHEMA, CLAIM, RESULT))
        self.assertEqual(env.downloads, ["https://cdn.geekbench.com/Geekbench-6.3.0-Linux.tar.gz"])
        self.assertEqual(env.page_fetches, [])

    def test_claim_link_appears_only_in_the_0600_file(self):
        code, out, err = self.invoke(FakeEnv())
        self.assertEqual(code, 0)
        for name in os.listdir(self.state):
            text = (self.state / name).read_text()
            self.assertEqual("526696" in text, name == CB.CLAIM_NAME, name)
        self.assertNotIn("526696", out + err)
        self.assertNotIn("claim", out + err)
        self.assertEqual(sorted(os.listdir(self.state)), [CB.CLAIM_NAME, CB.RESULT_NAME])  # no temp leftovers

    def test_falls_back_to_result_page_when_stdout_lacks_scores(self):
        env = FakeEnv(stdout=STDOUT_NO_SCORES)
        code, _, err = self.invoke(env)
        self.assertEqual(code, 0, err)
        rec = self.record()
        self.assertEqual((rec["single_core"], rec["multi_core"], rec["score_source"]), (1801, 5702, "result-page"))
        self.assertEqual(env.page_fetches, [RESULT])

    def test_unavailable_page_writes_a_partial_record(self):
        env = FakeEnv(stdout=STDOUT_NO_SCORES, page=OSError("offline"))
        code, _, _ = self.invoke(env)
        self.assertEqual(code, CB.EXIT_PARTIAL)
        rec = self.record()
        self.assertIsNone(rec["single_core"])
        self.assertEqual(rec["result_url"], RESULT)
        self.assertEqual(rec["reason"], "RESULT_PAGE_UNAVAILABLE: OSError")
        self.assertEqual(self.mode(CB.CLAIM_NAME), 0o600)

    def test_page_without_scores_is_partial(self):
        code, _, _ = self.invoke(FakeEnv(stdout=STDOUT_NO_SCORES, page=PAGE_EMPTY))
        self.assertEqual(code, CB.EXIT_PARTIAL)
        self.assertEqual(self.record()["reason"], "SCORES_NOT_FOUND")

    def test_nothing_usable_fails_without_files(self):
        code, _, _ = self.invoke(FakeEnv(stdout="Geekbench 6.3.0 crashed"))
        self.assertEqual(code, CB.EXIT_FAILED)
        self.assertFalse(self.state.exists())

    def test_existing_record_refused_without_force(self):
        self.assertEqual(self.invoke(FakeEnv())[0], 0)
        env = FakeEnv()
        code, _, err = self.invoke(env)
        self.assertEqual(code, CB.EXIT_REFUSED)
        self.assertIn("--force", err)
        self.assertEqual((env.runs, env.downloads), ([], []))

    def test_force_remeasures(self):
        self.assertEqual(self.invoke(FakeEnv())[0], 0)
        env = FakeEnv(stdout=STDOUT_FULL.replace("1801", "1900"))
        self.assertEqual(self.invoke(env, "--force")[0], 0)
        self.assertEqual(self.record()["single_core"], 1900)

    def test_cpu_pressure_above_threshold_refuses_before_download_or_run(self):
        env = FakeEnv(psi=25.01)
        code, _, err = self.invoke(env)
        self.assertEqual(code, CB.EXIT_REFUSED)
        self.assertIn("pressure", err)
        self.assertEqual((env.runs, env.downloads), ([], []))
        self.assertFalse(self.state.exists())

    def test_cpu_pressure_at_threshold_is_allowed(self):
        self.assertEqual(self.invoke(FakeEnv(psi=25.0))[0], 0)

    def test_max_psi_is_configurable(self):
        env = FakeEnv(psi=6.0)
        self.assertEqual(self.invoke(env, "--max-psi", "5")[0], CB.EXIT_REFUSED)
        self.assertEqual(env.runs, [])

    def test_sha256_mismatch_refuses_to_run(self):
        env = FakeEnv(tarball=_tarball({"Geekbench-6.3.0-Linux/geekbench6": b"tampered"}))
        code, _, err = self.invoke(env)
        self.assertEqual(code, CB.EXIT_FAILED)
        self.assertIn("sha256 mismatch", err)
        self.assertEqual(env.runs, [])
        self.assertFalse(self.state.exists())

    def test_version_and_sha_pins_are_mandatory(self):
        env = FakeEnv()
        self.assertEqual(self.invoke(env, base=False)[0], CB.EXIT_REFUSED)
        self.assertEqual(self.invoke(env, "--geekbench-version", "5.5.1", "--sha256", _tarball_sha(), base=False)[0],
                         CB.EXIT_REFUSED)
        self.assertEqual(self.invoke(env, "--geekbench-version", "6.3.0", base=False)[0], CB.EXIT_REFUSED)
        self.assertEqual((env.runs, env.downloads), ([], []))

    def test_timeout_fails_without_files(self):
        code, _, err = self.invoke(FakeEnv(timeout=True), "--timeout", "7")
        self.assertEqual(code, CB.EXIT_FAILED)
        self.assertIn("timeout", err)
        self.assertFalse(self.state.exists())

    def test_nonzero_exit_without_scores_fails(self):
        self.assertEqual(self.invoke(FakeEnv(rc=1, stdout="boom"))[0], CB.EXIT_FAILED)

    def test_wrong_major_version_is_refused(self):
        env = FakeEnv(stdout=STDOUT_FULL.replace("Geekbench 6.3.0", "Geekbench 5.5.1"))
        self.assertEqual(self.invoke(env)[0], CB.EXIT_FAILED)
        self.assertFalse(self.state.exists())

    def test_unsafe_archive_member_is_refused(self):
        env = FakeEnv(tarball=_tarball({"../escape": b"x"}))
        sha = hashlib.sha256(env.tarball).hexdigest()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = CB.main(["--state-dir", str(self.state), "--geekbench-version", "6.3.0", "--sha256", sha], env)
        self.assertEqual(code, CB.EXIT_FAILED)
        self.assertEqual(env.runs, [])

    def test_binary_option_skips_download(self):
        binary = self.state.parent / "geekbench6"
        binary.write_text("#!/bin/sh\n")
        env = FakeEnv()
        code, _, err = self.invoke(env, "--binary", str(binary), base=False)
        self.assertEqual(code, 0, err)
        self.assertEqual((env.downloads, env.runs), ([], [[str(binary), "--cpu"]]))

    def test_runs_the_extracted_binary_in_cpu_mode(self):
        env = FakeEnv()
        self.invoke(env)
        self.assertEqual(len(env.runs), 1)
        self.assertTrue(env.runs[0][0].endswith("geekbench6"))
        self.assertEqual(env.runs[0][1:], ["--cpu"])

    def test_umask_does_not_widen_the_claim_file(self):
        old = os.umask(0)
        try:
            self.assertEqual(self.invoke(FakeEnv())[0], 0)
        finally:
            os.umask(old)
        self.assertEqual(self.mode(CB.CLAIM_NAME), 0o600)
        self.assertEqual(self.mode(CB.RESULT_NAME), 0o644)

    def test_readable_record_never_carries_claim_even_through_free_text(self):
        with self.assertRaises(CB.BenchmarkError):
            CB.write_files(self.state, {"schema": CB.SCHEMA, "produced_by": "my claim?key=1"}, None)


class ResolveTests(Base):
    def seed_partial(self) -> None:
        self.assertEqual(self.invoke(FakeEnv(stdout=STDOUT_NO_SCORES, page=OSError("x")))[0], CB.EXIT_PARTIAL)

    def test_resolve_fills_scores_without_executing_geekbench(self):
        self.seed_partial()
        claim_before = (self.state / CB.CLAIM_NAME).read_text()
        env = FakeEnv()
        code, out, err = self.invoke(env, "--resolve", base=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(env.runs, [])
        self.assertEqual(env.downloads, [])
        self.assertEqual(env.page_fetches, [RESULT])
        rec = self.record()
        self.assertEqual((rec["single_core"], rec["multi_core"], rec["score_source"], rec["reason"]),
                         (1801, 5702, "result-page", None))
        self.assertIn("resolved_at", rec)
        self.assertEqual((self.state / CB.CLAIM_NAME).read_text(), claim_before)
        self.assertEqual(self.mode(CB.RESULT_NAME), 0o644)
        self.assertNotIn("526696", out + err)

    def test_resolve_keeps_measured_scores(self):
        self.assertEqual(self.invoke(FakeEnv())[0], 0)
        env = FakeEnv(page=PAGE.replace("1801", "1111"))
        self.assertEqual(self.invoke(env, "--resolve", base=False)[0], 0)
        self.assertEqual(self.record()["single_core"], 1801)

    def test_resolve_without_record_is_refused(self):
        env = FakeEnv()
        self.assertEqual(self.invoke(env, "--resolve", base=False)[0], CB.EXIT_REFUSED)
        self.assertEqual(env.page_fetches, [])

    def test_resolve_page_unavailable_fails_and_leaves_record(self):
        self.seed_partial()
        before = (self.state / CB.RESULT_NAME).read_text()
        self.assertEqual(self.invoke(FakeEnv(page=OSError("down")), "--resolve", base=False)[0], CB.EXIT_FAILED)
        self.assertEqual((self.state / CB.RESULT_NAME).read_text(), before)

    def test_resolve_page_without_scores_stays_partial(self):
        self.seed_partial()
        self.assertEqual(self.invoke(FakeEnv(page=PAGE_EMPTY), "--resolve", base=False)[0], CB.EXIT_PARTIAL)

    def test_resolve_rejects_a_foreign_result_url(self):
        self.state.mkdir(parents=True)
        (self.state / CB.RESULT_NAME).write_text(json.dumps({"result_url": "https://evil.example/v6/cpu/1"}))
        env = FakeEnv()
        self.assertEqual(self.invoke(env, "--resolve", base=False)[0], CB.EXIT_REFUSED)
        self.assertEqual(env.page_fetches, [])


class PinTests(unittest.TestCase):
    def test_sha256_pin_matches_the_tool(self):
        pin = (TOOL_DIR / "cpu_benchmark.py.sha256").read_text().split()
        self.assertEqual(pin[1], "cpu_benchmark.py")
        self.assertEqual(pin[0], hashlib.sha256(TOOL_PATH.read_bytes()).hexdigest(),
                         "tools/cpu_benchmark.py changed: regenerate tools/cpu_benchmark.py.sha256")

    def test_tool_is_stdlib_only(self):
        import ast
        stdlib = set(sys.stdlib_module_names)
        tree = ast.parse(TOOL_PATH.read_text())
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertLessEqual(mods, stdlib)


if __name__ == "__main__":
    unittest.main()

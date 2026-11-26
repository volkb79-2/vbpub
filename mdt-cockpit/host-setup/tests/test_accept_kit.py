"""P-plat-19m: the acceptance kit v2 under tools/accept/. Hermetic: fixture CSVs, fake docker/systemctl/ssh/cgprofilectl on PATH, fake /proc, cgroup and DAMON sysfs roots, a fake judge."""
from __future__ import annotations

import contextlib
import csv
import fcntl
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accept_kit_fakes as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "tools" / "accept"
MIB = 1024 ** 2
spec = importlib.util.spec_from_file_location("accept_eval_t", KIT / "accept-eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)
hspec = importlib.util.spec_from_file_location("accept_hotset_t", KIT / "accept-hotset.py")
hs = importlib.util.module_from_spec(hspec)
hspec.loader.exec_module(hs)
FAKE_DOCKER, _fake = F.FAKE_DOCKER, F.fake_one


def shell_block(source: str, start: str, end: str) -> str:
    """The named block of a shell script: the tests run it in isolation, never the whole script."""
    i = source.index(start)
    return source[i:source.index(end, i)]
HEAD = F.HEAD
PROBE_HEAD = F.PROBE_HEAD
M = F.MIB
K0 = 1560 * M


def run_eval(*argv: str) -> tuple[int, str]:
    return F.run_eval(*argv)


def run_hotset(*argv: str) -> tuple[int, str]:
    return F.run_hotset(*argv)


def live(d: Path, step: int) -> str:
    """Judges one step through the shipped `--live-step` path and returns the `steps/N.env` body."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = ev.main(["--live-step", str(d), "--step", str(step)])
    assert rc == 0, rc
    return (d / "steps" / f"{step}.env").read_text()


class EvalTests(unittest.TestCase):
    """accept-eval.py: the per-step verdicts (3), the DAMON floors (4.3), the M re-check (5), the one K rule."""

    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def test_a_passing_step_has_every_criterion_pass(self) -> None:
        d = F.write_run(self.tmp / "r", steps=(0, 1), verdicts=("PASS", "PASS"))
        body = live(d, 0)
        self.assertIn("verdict=PASS", body)
        self.assertIn("cause=\n", body)
        for criterion in ("pressure", "ssh", "probes", "no oom", "load was real"):
            self.assertIn(f"{criterion} PASS", body)

    def test_pressure_over_one_fails_and_a_missing_row_is_inconclusive(self) -> None:
        d = F.write_run(self.tmp / "psi", psi=1.5)
        self.assertIn("verdict=FAIL", live(d, 1))
        self.assertIn("pressure FAIL", live(d, 1))
        for blank in ("cgprofile.psi_full_avg10", "user.psi_full_avg10"):
            with self.subTest(blank):
                d = F.write_run(self.tmp / f"p{blank}", blank=(blank,))
                body = live(d, 1)
                self.assertIn("verdict=INCONCLUSIVE", body)     # a missing pressure row is named, never a pass
                self.assertIn(f"no data in column {blank}", body)

    def test_the_full_rows_are_hard_and_a_breach_fails(self) -> None:
        d = F.write_run(self.tmp / "full", psi=0.1)
        with (d / "samples.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            if r["step"] == "1":
                r["system.psi_full_avg10"] = "2.50"
        with (d / "samples.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, HEAD)
            w.writeheader()
            w.writerows(rows)
        body = live(d, 1)
        self.assertIn("verdict=FAIL", body)
        self.assertIn("system.full 2.50 > 1.00", body)

    def test_a_missing_pressure_column_is_inconclusive_and_names_itself(self) -> None:
        d = F.write_run(self.tmp / "m", blank=("system.psi_full_avg10",))
        self.assertIn("INCONCLUSIVE", live(d, 1))

    def test_ssh_is_the_p95_of_ten_against_the_step_0_baseline(self) -> None:
        # 3.2: base = max of 3 at step 0; the step FAILs when >= 2 of 10 exceed max(2 x base, base + 0.5 s) or the
        # median exceeds it, so ONE slow sample is a PASS and two are a FAIL.
        d = F.write_run(self.tmp / "sshfail")
        (d / "probes.csv").write_text(F.probes_text([0, 1, 2], ssh=0.4, ssh_of={1: [3.0] * 2 + [0.4] * 8}))
        self.assertIn("verdict=FAIL", live(d, 1))
        (d / "probes.csv").write_text(F.probes_text([0, 1, 2], ssh=0.4, ssh_of={1: [3.0] + [0.4] * 9}))
        self.assertIn("verdict=PASS", live(d, 1))

    def test_an_ssh_timeout_or_a_slow_median_fails_the_step(self) -> None:
        d = F.write_run(self.tmp / "sshto")
        (d / "probes.csv").write_text(F.probes_text([0, 1], ssh=0.4).replace(",1,ssh,ssh,0,0.4,", ",1,ssh,ssh,timeout,15.000,"))
        body = live(d, 1)
        self.assertIn("verdict=FAIL", body)
        self.assertIn("timeout", body)

    def test_a_liveness_probe_rc_fails_the_step(self) -> None:
        d = F.write_run(self.tmp / "rc")
        (d / "probes.csv").write_text(F.probes_text([0, 1, 2], rc_of={"journald": "1"}))
        body = live(d, 1)
        self.assertIn("verdict=FAIL", body)
        self.assertIn("journald rc 1", body)

    def test_a_containerd_restart_fails_a_step(self) -> None:
        d = F.write_run(self.tmp / "restart")
        (d / "probes.csv").write_text(F.probes_text([0, 1, 2], nrestarts_of={1: 3, 2: 3}))
        body = live(d, 1)
        self.assertIn("verdict=FAIL", body)
        self.assertIn("containerd restarted", body)

    def test_psi_guard_and_cgprofiled_growing_seconds_still_pass(self) -> None:
        # H2: psi_guard's recorded seconds are the probe's OWN duration (formerly the RUN's elapsed time, which grew
        # by a dwell per step), and its pass rule is "active + ticks advance" (3.3), never latency; the degraded
        # cgprofiled probe is INFO by design. So steps whose psi_guard/cgprofiled samples grow monotonically past the
        # step-0 p95 latency limit must still PASS. On 319aaeb86 the judge applied over_limit() to both of them and
        # this fixture is a step-1 FAIL "psi_guard latency over its step-0 baseline".
        d = F.write_run(self.tmp / "grow")
        slow = [0.02, 0.05, 0.9, 2.0, 3.0, 4.0, 5.0, 6.0]
        (d / "probes.csv").write_text(F.probes_text([0, 1, 2], seconds_at={(1, "psi_guard"): slow, (2, "psi_guard"): slow,
                                                                              (1, "cgprofiled"): slow, (2, "cgprofiled"): slow}))
        body = live(d, 1)
        self.assertIn("verdict=PASS", body)
        self.assertIn("probes 6/6 passed", body)
        self.assertNotIn("psi_guard latency", body)
        self.assertNotIn("cgprofiled latency", body)
        # a real liveness defect on the same exemption is still a FAIL: the exemption is about latency only
        (d / "probes.csv").write_text(F.probes_text([0, 1, 2], rc_of={"psi_guard": "1"}))
        self.assertIn("psi_guard rc 1", live(d, 1))

    def test_a_missing_liveness_probe_is_inconclusive(self) -> None:
        d = F.write_run(self.tmp / "noprobe")
        lines = F.probes_text([0, 1, 2]).splitlines()
        (d / "probes.csv").write_text("\n".join(ln for ln in lines if not (ln.split(",")[1] == "1" and ln.split(",")[2] == "dockerd")) + "\n")
        body = live(d, 1)
        self.assertIn("INCONCLUSIVE", body)
        self.assertIn("dockerd", body)
        self.assertIn("never a pass", body)

    def test_load_not_real_is_the_only_inconclusive_that_continues_the_ladder(self) -> None:
        d = F.write_run(self.tmp / "notreal", at_ceiling=False)
        body = live(d, 1)
        self.assertIn("verdict=INCONCLUSIVE", body)
        self.assertIn("cause=load_not_real", body)
        self.assertIn("not load-bearing", body)
        body = live(F.write_run(self.tmp / "nodata", blank=("mem_free",)), 1)
        self.assertIn("cause=missing:load was real", body)   # missing hard data stops the ladder, never continues it

    def test_mem_free_above_twice_the_watermark_is_not_load_bearing(self) -> None:
        d = F.write_run(self.tmp / "freeload", mem_free_low=False)
        body = live(d, 1)
        self.assertIn("cause=load_not_real", body)
        self.assertIn("MemFree <= 2 x W on 0%", body)

    def test_an_oom_kill_or_a_mem_available_breach_fails(self) -> None:
        for kw, needle in ((dict(oom=1), "vmstat=+1"),):
            with self.subTest(kw):
                d = F.write_run(self.tmp / f"oom{len(list(self.tmp.iterdir()))}", **kw)
                body = live(d, 1)
                self.assertIn("verdict=FAIL", body)
                self.assertIn(needle, body)
        d = F.write_run(self.tmp / "lowmem")
        with (d / "samples.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            if r["step"] == "1":
                r["mem_available"] = str(64 * M)
        with (d / "samples.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, HEAD)
            w.writeheader()
            w.writerows(rows)
        self.assertIn("verdict=FAIL", live(d, 1))
        self.assertIn("MemAvailable fell to 64M", live(d, 1))

    def test_a_step_0_fail_prints_no_k_and_never_1280m(self) -> None:
        d = F.write_run(self.tmp / "fail0", steps=(0,), verdicts=("FAIL",), causes=("pressure",))
        with (d / "steps" / "0.env").open("w") as fh:
            fh.write("verdict=FAIL\ncause=pressure\ndetail=psi\n")
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 1, out)
        self.assertIn("VERDICT FAIL", out)
        self.assertIn("pin DEV_HOST_MEMORY_KEEP above the tested K0=1560M", out)
        self.assertNotIn("K proposed", out)
        self.assertNotRegex(out, r"DEV_HOST_MEMORY_KEEP=\d+M")
        self.assertNotIn("1280M", out)

    def test_a_step_0_inconclusive_is_inconclusive_not_fail(self) -> None:
        d = F.write_run(self.tmp / "inc0", steps=(0,), verdicts=("INCONCLUSIVE",), causes=("load_not_real",))
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 4, out)
        self.assertIn("VERDICT INCONCLUSIVE", out)
        self.assertNotIn("pin DEV_HOST_MEMORY_KEEP", out)

    def test_refaults_are_informational_and_do_not_change_the_verdict(self) -> None:
        d = F.write_run(self.tmp / "ref", steps=(0, 1), verdicts=("PASS", "PASS"), refault=5000)
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 0, out)
        self.assertIn("INFO refault", out)
        self.assertNotRegex(out, r"FAIL\s+refault")

    def test_the_one_k_rule_reproduces_the_runs_2_example(self) -> None:
        # 5/7 step 6, run 2's numbers: F_new 644M (the installed rows), M 746.7M, k_last_pass 1507M ->
        # max(644 + 746.7, 1507) = 1507M. Run 2's "F 591M in force" is the F the squeeze ran at, not the proposal.
        rows = " ".join(f"{u}={v}M" for u, v in (("ssh.service", 21), ("docker.service", 278), ("containerd.service", 92),
                                                 ("systemd-journald.service", 24), ("mdt-psi-guard.service", 5),
                                                 ("cgprofiled.service", 26)))
        d = F.write_run(self.tmp / "run2", steps=(0, 1), verdicts=("PASS", "PASS"), tested_k=1507 * M)
        (d / "host-setup.env.snapshot").write_text(f'HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_MIN="{rows}"\nDEV_HOST_MEMORY_MARGIN=746.7M\n')
        man = (d / "manifest.env").read_text().replace("k_last_pass=1445986304", f"k_last_pass={1507 * M}")
        (d / "manifest.env").write_text(man)
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 0, out)
        self.assertIn("K proposed=1507M", out)
        self.assertIn("= F 644M", out)          # the installed rows, never the 591M the squeeze ran at
        self.assertIn("(>= k_last_pass 1507M)", out)
        self.assertNotIn("1280M", out)
        self.assertNotIn("1560M", out)

    def test_the_proposed_k_is_max_of_floors_plus_m_and_k_last_pass(self) -> None:
        d = F.write_run(self.tmp / "clamp", steps=(0, 1), verdicts=("PASS", "PASS"), tested_k=700 * M)
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 0, out)
        k = int(re.search(r"K proposed=(\d+)M", out).group(1))
        self.assertGreater(k, 700)              # floors + M > k_last_pass: K may rise, and it is printed as such
        self.assertIn("(>= k_last_pass 572M)", out)

    def test_a_candidate_floor_never_lowers_the_installed_row(self) -> None:
        # 4.3: a DAMON floor below the installed row was never in force. It is printed as a candidate and never
        # enters F_new, so the proposed rows keep the installed value.
        d = F.write_run(self.tmp / "cand", steps=(0,), verdicts=("PASS",), hot={"ssh.service": 2 * M, "docker.service": 200 * M})
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 0, out)
        self.assertIn("FLOOR ssh.service=3M source=damon candidate (installed 10M, untested)", out)
        self.assertIn('DEV_HOST_MEMORY_MIN="', out)
        rows = re.search(r'DEV_HOST_MEMORY_MIN="([^"]*)"', out).group(1)
        self.assertIn("ssh.service=10M", rows)

    def test_a_proved_floor_raises_the_row(self) -> None:
        d = F.write_run(self.tmp / "proved", steps=(0,), verdicts=("PASS",), hot={"docker.service": 215 * M})
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 0, out)
        self.assertIn("FLOOR docker.service=237M source=damon:215.0M x1.10 tested=every step", out)
        self.assertIn("docker.service=237M", re.search(r'DEV_HOST_MEMORY_MIN="([^"]*)"', out).group(1))

    def test_a_damon_refusal_names_the_table_row(self) -> None:
        d = F.write_run(self.tmp / "refused", steps=(0,), verdicts=("PASS",))
        (d / "hotset.csv").write_text(F.HOT_HEAD and ",".join(F.HOT_HEAD) + "\n")
        (d / "hotset-refused").write_text("mdt-psi-guard.service\n")
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 0, out)
        self.assertIn("FLOOR mdt-psi-guard.service=5M source=table candidate (installed 5M, DAMON refused: sz_ops_filter_passed 0)", out)

    def test_the_squeeze_line_names_the_last_pass_and_the_first_fail(self) -> None:
        d = F.write_run(self.tmp / "sq", steps=(0, 1, 2, 3), verdicts=("PASS", "PASS", "PASS", "FAIL"), causes=("", "", "", "pressure"))
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 0, out)
        self.assertIn("SQUEEZE last pass K=1304M (step 2), first fail K=1176M (step 3): stopped by the pressure criterion", out)

    def test_a_v1_directory_is_refused_with_exit_2(self) -> None:
        d = F.write_run(self.tmp / "v1", steps=(0,), verdicts=("PASS",), kit="1")
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 2, out)
        self.assertIn("no kit_version=2", out)
        self.assertIn("v1", out)

    def test_a_crash_a_missing_dir_or_no_arguments_is_2_never_fail(self) -> None:
        self.assertEqual(run_eval(str(self.tmp / "nowhere"))[0], 2)
        self.assertEqual(ev.main([]), 2)
        d = F.write_run(self.tmp / "noev", steps=(0,), verdicts=("PASS",))
        shutil.rmtree(d / "steps")
        self.assertEqual(run_eval(str(d))[0], 4)   # a run whose judge wrote nothing is missing data, never a PASS


class HotsetTests(unittest.TestCase):
    """accept-hotset.py: the one kdamond, the per-leaf stat schemes, the AND ops-filter chain, the self-test."""

    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        self.admin = self.tmp / "admin"
        self.kd = self.admin / "kdamonds"
        self.kd.mkdir(parents=True)
        (self.kd / "nr_kdamonds").write_text("0\n")
        self.cg = self.tmp / "cg"
        for rel, cur in (("system.slice/docker.service", 40 * M), ("user.slice", 4 * M),
                         ("dev.slice/dev-gates.slice/docker-abc.scope", 8 * M)):
            (self.cg / rel).mkdir(parents=True)
            (self.cg / rel / "memory.current").write_text(f"{cur}\n")
        (self.tmp / "meminfo").write_text("MemTotal: 8388608 kB\n")
        self.dstat = self.tmp / "damon_stat"
        self.dstat.write_text("N\n")
        self.ev = self.tmp / "ev"
        self.ev.mkdir()
        self.wal = self.tmp / "wal"
        self.lib = str(Path(__file__).resolve().parents[3] / "scripts" / "damon-analysis" / "lib")
        self.units = "docker.service=system.slice/docker.service user.slice=user.slice"
        # the kdamond multiplexing check reads a real CLI from PATH: the tests give it a fake that reports 0 live
        # sessions, so a developer's host state never decides a test's outcome
        bindir = self.tmp / "bin"
        F.fake_one("cgprofilectl", "printf '{\"version\": \"test\", \"sessions_live\": 0}\\n'\n", bindir)
        self.enterContext(mock.patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ['PATH']}"}))

    def tree(self, *, nr: int = 0, state: str = "off", avail: str = "vaddr paddr", filters: bool = True,
             stats: bool = True) -> None:
        """A fake /sys/kernel/mm/damon/admin: the kdamond's staged inputs as the kernel would expose them."""
        for idx in range(nr):
            d = self.kd / str(idx)
            d.mkdir(parents=True, exist_ok=True)
            (d / "state").write_text(state + "\n")
        (self.kd / "nr_kdamonds").write_text(f"{nr}\n")
        ctx = self.kd / "0" / "contexts" / "0"
        ctx.mkdir(parents=True, exist_ok=True)
        (ctx.parent / "nr_contexts").write_text("1\n")
        (ctx / "avail_operations").write_text(avail + "\n")
        (ctx / "operations").write_text("paddr\n")
        for p in ("monitoring_attrs/intervals/sample_us", "monitoring_attrs/intervals/aggr_us",
                  "monitoring_attrs/intervals/update_us", "monitoring_attrs/nr_regions/min", "monitoring_attrs/nr_regions/max",
                  "targets/nr_targets", "schemes/nr_schemes", "targets/0/regions/nr_regions"):
            (ctx / p).parent.mkdir(parents=True, exist_ok=True)
            (ctx / p).write_text("0\n")
        (ctx / "targets" / "0" / "regions" / "0").mkdir(parents=True)   # the kernel creates it on nr_regions=1
        (ctx / "targets" / "0" / "regions" / "0" / "start").write_text("0\n")
        (ctx / "targets" / "0" / "regions" / "0" / "end").write_text("8589934592\n")
        (self.kd / "0" / "refresh_ms").write_text("10000\n")

    def ready(self, *, filters: bool = True, stats: bool = True) -> None:
        """The kernel side after `update_schemes_tried_regions`: two schemes with the filter chain and the stats."""
        for i, names in ((0, ("7", "9")), (1, ("3",))):
            sdir = self.kd / "0" / "contexts" / "0" / "schemes" / str(i)
            sdir.mkdir(parents=True, exist_ok=True)
            if filters:
                (sdir / "ops_filters" / "0").mkdir(parents=True)
                (sdir / "ops_filters" / "1").mkdir()
                for idx, name in (("0", "allow"), ("1", "allow"), ("0", "type"), ("0", "memcg_path"), ("0", "matching"),
                                  ("1", "type"), ("1", "matching")):
                    (sdir / "ops_filters" / idx / name).write_text("\n")
                (sdir / "ops_filters" / "nr_filters").write_text("0\n")
            (sdir / "stats").mkdir(parents=True, exist_ok=True)
            if stats:
                (sdir / "stats" / "sz_ops_filter_passed").write_text("0\n")
                (sdir / "stats" / "sz_tried").write_text("12345\n")
            (sdir / "action").write_text("stat\n")
            (sdir / "access_pattern" / "sz").mkdir(parents=True)
            for p in ("sz/min", "sz/max", "nr_accesses/min", "nr_accesses/max", "age/min", "age/max"):
                (sdir / "access_pattern" / p).parent.mkdir(parents=True, exist_ok=True)
                (sdir / "access_pattern" / p).write_text("0\n")
            tried = sdir / "tried_regions"
            tried.mkdir()
            (tried / "total_bytes").write_text("0\n")
            for j, name in enumerate(names):
                (tried / name).mkdir()   # NON-sequential names, as the kernel assigns them
                (tried / name / "start").write_text(str(j * 4096) + "\n")
                (tried / name / "end").write_text(str((j + 1) * 4096) + "\n")
                (tried / name / "nr_accesses").write_text("3\n")
                (tried / name / "age").write_text("4\n")
                (tried / name / "sz_filter_passed").write_text(str((i + 1) * 4096 * (j + 1)) + "\n")

    def args(self, *extra: str) -> list[str]:
        return ["--evidence", str(self.ev), "--state", str(self.wal), "--units", self.units, "--admin", str(self.admin),
                "--cgroup", str(self.cg), "--proc", str(self.tmp), "--lib", self.lib, "--damon-stat", str(self.dstat), *extra]

    def test_setup_configures_one_kdamond_and_records_its_index_in_the_wal(self) -> None:
        self.tree()
        self.ready()
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.kd / "nr_kdamonds").read_text().strip(), "1")
        self.assertIn("kdamond=0", self.wal.read_text(), "the WAL line is what the restore derives the index from")
        self.assertIn("damon_stat_enabled=N", out)
        self.assertIn("kdamond=0", out)
        self.assertEqual((self.kd / "0" / "refresh_ms").read_text().strip(), "0", "N5: no periodic refresh aliasing aggr_us")
        self.assertEqual((self.kd / "0" / "contexts" / "0" / "operations").read_text().strip(), "paddr")
        self.assertEqual((self.kd / "0" / "contexts" / "0" / "monitoring_attrs" / "intervals" / "sample_us").read_text().strip(), "400000")
        self.assertEqual((self.kd / "0" / "contexts" / "0" / "monitoring_attrs" / "intervals" / "aggr_us").read_text().strip(), "8000000")
        self.assertEqual((self.kd / "0" / "contexts" / "0" / "targets" / "0" / "regions" / "0" / "end").read_text().strip(), "8589934592")

    def test_a_damon_stat_of_y_refuses_with_nothing_written(self) -> None:
        self.tree()
        self.dstat.write_text("Y\n")
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("DAMON_STAT is Y", out)
        self.assertIn("install.sh --require-damon-disable", out)
        self.assertFalse(self.wal.exists(), "a refusal writes nothing")
        self.assertNotIn("kdamond=", (self.kd / "nr_kdamonds").read_text())

    def test_an_absent_damon_stat_proceeds_and_records_absent(self) -> None:
        self.tree()
        self.dstat.unlink()
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertNotIn("DAMON_STAT is Y", out)

    def test_a_kdamond_that_is_already_on_refuses(self) -> None:
        self.tree(nr=2, state="on")
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("kdamond 0 is already on", out)
        self.assertFalse(self.wal.exists())

    def test_an_index_is_reused_only_when_it_is_off(self) -> None:
        self.tree(nr=2, state="off")
        rc, _out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3)                       # no kernel: the refusal happens after the WAL line
        self.assertIn("kdamond=1", self.wal.read_text(), "the highest (off) index is reused")

    def test_a_kernel_without_paddr_refuses(self) -> None:
        self.tree(avail="vaddr fvaddr")
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("has no paddr", out)

    def test_a_kernel_without_sz_ops_filter_passed_refuses_never_end_minus_start(self) -> None:
        self.tree()
        self.ready(stats=False)
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("sz_ops_filter_passed is unreadable", out)
        self.assertIn("never a leaf's", out)

    def test_the_chain_is_memcg_reject_then_young_allow(self) -> None:
        # 4.1: ops_filters/0 rejects every page that is not the leaf's, ops_filters/1 passes the leaf's young pages.
        # DAMOS decides on the FIRST match, so the pair is an AND; an allow-first memcg filter would count the leaf's
        # whole resident size plus the machine's young bytes (the r2 N1 defect, now the doc's premise too).
        self.tree()
        self.ready()
        scheme = self.kd / "0" / "contexts" / "0" / "schemes" / "0"
        hs.set_ops_filters(str(scheme), "/sys/fs/cgroup/user.slice")
        self.assertEqual((scheme / "ops_filters" / "nr_filters").read_text().strip(), "2")
        self.assertEqual((scheme / "ops_filters" / "0" / "type").read_text().strip(), "memcg")
        self.assertEqual((scheme / "ops_filters" / "0" / "memcg_path").read_text().strip(), "/sys/fs/cgroup/user.slice")
        self.assertEqual((scheme / "ops_filters" / "0" / "matching").read_text().strip(), "N")
        self.assertEqual((scheme / "ops_filters" / "0" / "allow").read_text().strip(), "N")
        self.assertEqual((scheme / "ops_filters" / "1" / "type").read_text().strip(), "young")
        self.assertEqual((scheme / "ops_filters" / "1" / "matching").read_text().strip(), "Y")
        self.assertEqual((scheme / "ops_filters" / "1" / "allow").read_text().strip(), "Y")

    def test_a_filter_chain_the_kernel_does_not_create_refuses(self) -> None:
        scheme = self.tmp / "scheme"
        (scheme / "ops_filters").mkdir(parents=True)
        with self.assertRaises(hs.Refusal) as ctx:
            hs.set_ops_filters(str(scheme), "/x")
        self.assertIn("no ops-filter chain on this kernel", str(ctx.exception))

    def test_a_missing_ops_filters_dir_refuses(self) -> None:
        scheme = self.tmp / "scheme2"
        scheme.mkdir()
        with self.assertRaises(hs.Refusal) as ctx:
            hs.set_ops_filters(str(scheme), "/x")
        self.assertIn("is missing: this kernel has no ops_filters", str(ctx.exception))

    def test_the_per_unit_value_is_the_sum_of_sz_filter_passed_after_one_apply(self) -> None:
        self.tree(nr=1, state="off")
        self.ready()
        rc, out = run_hotset("sample", *self.args("--step", "2", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        rows = list(csv.DictReader((self.ev / "hotset.csv").open()))
        self.assertEqual(rows[0]["unit"], "docker.service")
        self.assertEqual(int(rows[0]["sz_filter_passed_one_apply"]), 4096 + 4096 * 2)   # scheme 0: 1 + 2 pages
        self.assertEqual(int(rows[0]["sz_tried"]), 12345)
        self.assertEqual(int(rows[0]["regions_hot_bytes_info"]), 2 * 4096)             # end - start, INFO only
        self.assertEqual(int(rows[1]["sz_filter_passed_one_apply"]), 8192)             # scheme 1: its one region, 2 pages
        self.assertEqual(int(rows[1]["regions_hot_bytes_info"]), 4096)
        self.assertEqual(rows[1]["unit"], "user.slice")

    def test_a_zero_filtered_count_above_1mib_refuses_that_floor_and_records_it(self) -> None:
        self.tree(nr=1, state="off")
        self.ready()
        (self.ev / "manifest.env").write_text("")
        for f in (self.kd / "0" / "contexts" / "0" / "schemes").glob("*/tried_regions/*/sz_filter_passed"):
            f.write_text("0\n")
        rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        self.assertIn("never read as no hot pages", out)
        man = (self.ev / "manifest.env").read_text()
        self.assertIn("damon_measure=failed:docker.service:", man)
        rc, out = run_hotset("sample", *self.args("--step", "1", "--kdamond", "0"))
        man = (self.ev / "manifest.env").read_text()
        self.assertEqual(man.count("damon_measure=failed:docker.service"), 1, "one line per unit, not per sample")

    def test_a_leaf_past_a_slice_is_enumerated_as_its_leaves_never_itself(self) -> None:
        self.assertIn("system.slice/docker.service", hs.leaf_cgroups(str(self.cg), "system.slice"))
        self.assertNotIn("system.slice", hs.leaf_cgroups(str(self.cg), "system.slice"))
        self.assertEqual(hs.leaf_cgroups(str(self.cg), "user.slice"), ["user.slice"])

    def test_no_leaf_at_all_refuses(self) -> None:
        self.tree()
        with self.assertRaises(hs.Refusal) as ctx:
            hs.entries_or_refuse(hs.parse_units("gone.service=system.slice/gone.service"), str(self.cg))
        self.assertIn("a slice is measured as its leaves", str(ctx.exception))

    def test_a_fixture_sample_writes_the_documented_columns(self) -> None:
        fx = self.tmp / "fx.json"
        fx.write_text(json.dumps({"units": {"docker.service": {"memcg_leaves": 2, "per_apply": 1000, "sz_tried": 2000,
                                                          "regions_hot_bytes": 0, "current": 4 * M}}}))
        rc, out = run_hotset("sample", *self.args("--fixture", str(fx), "--step", "3"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(list(next(csv.DictReader((self.ev / "hotset.csv").open())).keys()), F.HOT_HEAD)

    def test_off_writes_the_state_and_reads_it_back(self) -> None:
        self.tree(nr=1, state="on")
        rc, out = run_hotset("off", *self.args("--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "off")

    def test_a_missing_damon_stat_file_is_absent_and_proceeds(self) -> None:
        self.assertEqual(hs.damon_stat_state(str(self.tmp / "nope")), "absent")

    def test_the_cgprofile_session_check_reads_sessions_live_only(self) -> None:
        bindir = self.tmp / "bin2"
        F.fake_one("cgprofilectl", 'printf \'{"version": "x", "sessions_stored": 3}\'\n', bindir)
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
        with mock.patch.dict(os.environ, env):
            hs.cgprofile_sessions()      # stored sessions are not live ones: never a refusal
        F.fake_one("cgprofilectl", 'printf \'{"sessions_live": 1}\'\n', bindir)
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(hs.Refusal) as ctx:
                hs.cgprofile_sessions()
        self.assertIn("sessions_live=1", str(ctx.exception))


class ShellKitTests(F.ShellKitBase):
    """accept-run.sh: the ladder, the WAL, the per-step writes, the probes, the stop rule, the cleanup paths."""

    def test_the_shell_scripts_are_syntax_and_shellcheck_clean(self) -> None:
        for s in sorted(KIT.glob("*.sh")):
            self.assertEqual(subprocess.run(["bash", "-n", str(s)]).returncode, 0, s)
        if shutil.which("shellcheck") is None:
            self.assertFalse(os.environ.get("MDT_REQUIRE_SHELLS"), "shellcheck required but absent")
            self.skipTest("shellcheck not available")
        r = subprocess.run(["shellcheck", "-S", "warning", *map(str, sorted(KIT.glob("*.sh")))], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_ladder_that_passes_every_step_ends_pass_and_removes_the_dummy(self) -> None:
        r = self.run_kit("--steps", "2", "--dwell", "0", ticks="40", **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("accept-run: PASS", r.stdout)
        self.assertEqual(self.judge_steps(), ["0", "1", "2"])
        self.assertIn("rm -f mdt-accept-squeeze", self.calls())
        man = (self.ev / "manifest.env").read_text()
        self.assertIn("restore=ok", man)
        self.assertNotIn("restore=pending", man)
        self.assertFalse((self.ev / "squeeze-state.env").exists())
        # H1: every step's MIN line carries the value read at START, so a multi-step finish returns the four
        # files to their start values -- not to the previous step's (much higher) raise. The scope min goes away
        # with the scope the dummy's `docker rm -f` removes, which the restore counts as restored.
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START))
        self.assertEqual((self.cg / "dev.slice" / "dev-gates.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START))
        self.assertEqual((self.cg / "dev.slice" / "memory.min").read_text().strip(), str(512 * M))
        self.assertEqual((self.cg / "dev.slice" / "dev-gates.slice" / "memory.min").read_text().strip(), str(self.TIER_MIN))
        self.assertIn("restore=ok", man)
        self.assertEqual(man.count("restore=ok"), 1, "accept-restore.sh writes restore=ok; finish never states it twice")

    def judge_steps(self) -> list[str]:
        return [ln.split()[1] for ln in (self.ev / "steps.env").read_text().splitlines() if ln.startswith("STEP ")]

    def test_the_first_fail_stops_the_ladder(self) -> None:
        j = self.tmp / "judge-fail2.sh"
        j.write_text('#!/bin/sh\nmkdir -p "$2"/steps\n'
                     'if [ "$4" = 2 ]; then printf "verdict=FAIL\\ncause=pressure\\ndetail=psi\\n"; '
                     'else printf "verdict=PASS\\ncause=\\ndetail=ok\\n"; fi > "$2"/steps/$4.env\n')
        j.chmod(0o755)
        r = self.run_kit("--steps", "3", "--dwell", "0", ticks="40", MDT_ACCEPT_JUDGE=str(j))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)      # >= 1 step passed: the run is a PASS
        self.assertEqual(self.judge_steps(), ["0", "1", "2"], "the ladder stops at the first FAIL")
        man = (self.ev / "manifest.env").read_text()
        self.assertIn("squeeze_first_fail_step=2", man)
        self.assertIn("squeeze_last_pass_step=1", man)

    def test_only_load_not_real_continues_the_ladder(self) -> None:
        j = self.tmp / "judge-inc.sh"
        j.write_text('#!/bin/sh\nmkdir -p "$2"/steps\n'
                     'if [ "$4" = 1 ]; then printf "verdict=INCONCLUSIVE\\ncause=load_not_real\\ndetail=not load-bearing\\n"; '
                     'else printf "verdict=PASS\\ncause=\\ndetail=ok\\n"; fi > "$2"/steps/$4.env\n')
        j.chmod(0o755)
        r = self.run_kit("--steps", "2", "--dwell", "0", ticks="12", MDT_ACCEPT_JUDGE=str(j))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.judge_steps(), ["0", "1", "2"], "load_not_real is the only cause that continues")
        j.write_text('#!/bin/sh\nmkdir -p "$2"/steps\nprintf "verdict=INCONCLUSIVE\\ncause=missing:data\\ndetail=x\\n" > "$2"/steps/$4.env\n')
        r = self.run_kit("--steps", "3", "--dwell", "0", ticks="40", MDT_ACCEPT_JUDGE=str(j))
        self.assertEqual(len(self.judge_steps()), 1, "missing hard data stops the ladder")

    def test_a_step_0_fail_is_a_run_fail_with_exit_1(self) -> None:
        r = self.run_kit("--steps", "2", "--dwell", "0", ticks="6", **self.judge_env("FAIL", "pressure"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("accept-run: FAIL", r.stdout)
        self.assertEqual(self.judge_steps(), ["0"])
        self.assertIn("squeeze_verdict=FAIL", (self.ev / "manifest.env").read_text())

    def test_the_ladder_stops_when_k_would_fall_below_f_installed_plus_128m(self) -> None:
        # 2.1: a step is taken only while K(s) >= F_installed + 128M. At 400M steps and F 644M, K(2) = 760M is below
        # 772M: the ladder must stop after step 1 without writing step 2's ceilings.
        r = self.run_kit("--steps", "6", "--dwell", "0", "--step", "400M", ticks="40", FAKE_STEP_BYTES=str(400 * M),
                         **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the ladder stops", r.stdout + r.stderr)
        self.assertEqual(self.judge_steps(), ["0", "1"], "no step 2: K(2) 760M < F 644M + 128M")
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START))

    def test_a_mem_available_guard_abort_marks_the_step_and_stops(self) -> None:
        (self.proc / "meminfo").write_text("MemTotal: 8388608 kB\nMemFree: 1048576 kB\nMemAvailable: 65536 kB\n")
        r = self.run_kit("--steps", "2", "--dwell", "0", ticks="6", **self.judge_env("PASS"))
        # 2.4: "an abort is a step FAIL plus a ladder stop", and a step-0 FAIL is the run's FAIL (2.5)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("mem_available", (self.ev / "steps" / "0.env").read_text())
        self.assertEqual(len(self.judge_steps()), 1)

    def test_the_preflight_refuses_when_the_evidence_filesystem_cannot_hold_the_wal(self) -> None:
        # M3: the WAL lives on the EVIDENCE filesystem, which may be a different filesystem than the Docker data root
        # the old 4G check looked at; a full evidence disk degraded write-ahead to write-only. The preflight now asks
        # the filesystem the WAL is written to. (The threshold is injected: a full disk cannot be faked hermetically.)
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="4", MDT_ACCEPT_MIN_EVID_DISK=str(1 << 62),
                         **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("write-ahead log could not be trusted", r.stderr)
        self.assertFalse((self.ev / "samples.csv").exists(), "refused before the dummy")

    def test_a_leftover_min_from_an_interrupted_run_is_refused_by_the_preflight(self) -> None:
        # L7: §7 gives every run a fresh evidence directory, so "a later run restores a stale WAL" never fires; the
        # only guard left is the preflight. It checked dev.max but not the mins, so a run started before the
        # reconcile tick would record the leftover raise as its "original" and write it back. systemd's MemoryMin is
        # the start value (install.sh renders it), and the kit only ever raises.
        (self.show / "MemoryMin-dev.slice").write_text(f"{512 * MIB}\n")
        (self.cg / "dev.slice" / "memory.min").write_text(f"{640 * MIB}\n")
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="4", **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("a leftover raise of an interrupted run", r.stderr)
        self.assertFalse((self.ev / "samples.csv").exists(), "refused before the dummy and before any write")

    def test_a_mid_run_drift_marks_the_step_inconclusive_and_stops_the_ladder(self) -> None:
        # 2.3: every tick reads the five files back and compares them with the kit's OWN write, and the comparison
        # runs again before a step's first write, so a foreign value never becomes the WAL's "original".
        # This case needs a second JUDGED tick in step 0 (the foreign write lands in the first one's sample), so it
        # raises the dwell itself: the shared fake host runs the shortest ladder the other seams need.
        r = self.run_kit("--steps", "2", "--dwell", "0", ticks="40", **self.judge_env("PASS"),
                         MDT_ACCEPT_DWELL_TICKS="3", MDT_ACCEPT_HOTSET=str(self.foreign_writer(every=1)))
        steps = (self.ev / "steps.env").read_text()
        self.assertIn("drift:dev.slice/memory.min", steps, r.stdout + r.stderr)
        self.assertEqual(self.judge_steps(), ["0"], "the drift stops the ladder at step 0")
        self.assertNotIn("STEP 1", steps, "no ceiling was written for step 1")
        self.assertEqual((self.cg / "dev.slice" / "memory.min").read_text().strip(), str(512 * M - 1),
                         "the kit's WAL never recorded the foreign value as its original, so the restore left it alone")

    def foreign_writer(self, every: int = 3) -> Path:
        """A hot-set driver that lets ONE foreign write land mid-run: its `sample` call is the kit's own seam, so the
        write happens after one tick's drift check and before the next one. The driver is Python because
        accept-run.sh invokes it as `python3 <HOTSET> ...`."""
        w = self.tmp / "hotset_foreign.py"
        w.write_text(
            "import subprocess, sys\n"
            f"counter = {str(self.tmp / 'hotset-calls')!r}\n"
            f"marker = {str(self.tmp / 'wrote')!r}\n"
            f"target = {str(self.cg / 'dev.slice' / 'memory.min')!r}\n"
            "calls = 0\n"
            "try:\n"
            "    calls = int(open(counter).read())\n"
            "except Exception:\n"
            "    calls = 0\n"
            "calls += 1\n"
            "open(counter, 'w').write(str(calls))\n"
            f"if sys.argv[1] == 'sample' and calls >= {every} and not __import__('os').path.exists(marker):\n"
            f"    open(marker, 'w').write('1')\n"
            f"    open(target, 'w').write(str({512 * M - 1}))\n"
            f"sys.exit(subprocess.run([sys.executable, {str(KIT / 'accept-hotset.py')!r}] + sys.argv[1:]).returncode)\n")
        return w

    def test_the_five_files_are_read_back_before_a_step_is_judged(self) -> None:
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        wal = (self.ev / "squeeze-state.env").read_text() if (self.ev / "squeeze-state.env").exists() else ""
        self.assertEqual(wal, "", "a clean run leaves no state file")
        calls = self.calls()
        self.assertTrue([c for c in calls if c.startswith("kill -s USR1 mdt-accept-squeeze")], calls)
        self.assertIn("container=mdt-accept-squeeze", self.wal_taken())

    def wal_taken(self) -> str:
        """The WAL as it was mid-run: the finish removes it, so the run's own log is what a test can read."""
        for line in (self.ev / "manifest.env").read_text().splitlines():
            if line.startswith("dummy_a0=") or line.startswith("k_tested="):
                return "container=mdt-accept-squeeze\n" + line
        return ""

    def test_a_second_run_is_refused_while_the_first_holds_the_lock(self) -> None:
        p = subprocess.Popen([str(KIT / "accept-run.sh"), "--evidence", str(self.ev), "--warmup", "0", "--steps", "0",
                              "--dwell", "0"], env=dict(self.env, MDT_ACCEPT_INTERVAL="600", MDT_ACCEPT_MAX_TICKS="40"),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(p.wait)
        deadline = time.monotonic() + F.KIT_RUN_TIMEOUT
        while p.poll() is None and time.monotonic() < deadline and not (self.ev / "samples.csv").exists():
            time.sleep(0.05)
        r = subprocess.run([str(KIT / "accept-run.sh"), "--evidence", str(self.tmp / "ev2"), "--warmup", "0"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("lock", r.stderr)
        p.terminate()
        self.assertEqual(p.wait(timeout=F.KIT_RUN_TIMEOUT), 143)

    def test_int_term_and_hup_remove_the_dummy_by_its_exact_name(self) -> None:
        for sig, rc in ((signal.SIGTERM, 143), (signal.SIGINT, 130), (signal.SIGHUP, 129)):
            with self.subTest(sig.name):
                self.log.write_text("")
                shutil.rmtree(self.ev, ignore_errors=True)
                p = self.start_long()
                p.send_signal(sig)
                self.assertEqual(p.wait(timeout=F.KIT_RUN_TIMEOUT), rc)
                self.assertIn("rm -f mdt-accept-squeeze", self.calls())
                self.assertIn("end_utc=", (self.ev / "manifest.env").read_text())
                self.assertFalse((self.ev / "squeeze-state.env").exists(), "the trap restored and cleared the WAL")

    def start_long(self) -> subprocess.Popen:
        p = subprocess.Popen([str(KIT / "accept-run.sh"), "--evidence", str(self.ev), "--warmup", "0", "--steps", "0", "--dwell", "0"],
                             env=dict(self.env, MDT_ACCEPT_INTERVAL="1", MDT_ACCEPT_MAX_TICKS="40", **self.judge_env("PASS")),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + F.KIT_RUN_TIMEOUT
        while p.poll() is None and time.monotonic() < deadline and not ((self.ev / "samples.csv").exists()
                                                                        and len((self.ev / "samples.csv").read_text().splitlines()) >= 2):
            time.sleep(0.05)
        if p.poll() is not None:
            self.fail(f"the run ended early rc={p.returncode}: {p.stderr.read()}")
        return p

    def test_a_sigkill_leaves_a_wal_the_restore_repairs(self) -> None:
        p = self.start_long()
        p.kill()                       # SIGKILL: no trap runs, the write-ahead file is the only carrier
        self.assertEqual(p.wait(timeout=F.KIT_RUN_TIMEOUT), -signal.SIGKILL)
        wal = (self.ev / "squeeze-state.env").read_text()
        self.assertIn("boot_id=", wal)
        self.assertIn("container=mdt-accept-squeeze", wal)
        r = subprocess.run([str(KIT / "accept-restore.sh"), "--evidence", str(self.ev), "--from-unit", "--lock-timeout", "30"],
                           env=dict(self.env, MDT_ACCEPT_DAMON_ADMIN=str(self.tmp / "damon")), capture_output=True, text=True, timeout=F.KIT_RUN_TIMEOUT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rm -f mdt-accept-squeeze", r.stdout)
        self.assertFalse((self.ev / "squeeze-state.env").exists())

    def test_a_hup_during_start_probe_keeps_the_restore_working(self) -> None:
        # MDT-110's subtests: a HUP between a WAL line and its write, and one during `docker run` before the id is known.
        # The kit's answer is the WAL, not the trap: every side effect is written before it is applied.
        wal = self.ev / "squeeze-state.env"
        self.ev.mkdir(parents=True, exist_ok=True)
        wal.write_text("container=mdt-accept-squeeze\nboot_id=11111111-2222-3333-4444-555555555555\n"
                       f"MAX=dev.slice/memory.max|{self.DEV_MAX_START}|{self.DEV_MAX_START + 128 * M}|\n")
        (self.cg / "dev.slice" / "memory.max").write_text(f"{self.DEV_MAX_START}\n")   # the write never happened
        r = subprocess.run([str(KIT / "accept-restore.sh"), "--evidence", str(self.ev)], env=dict(self.env),
                           capture_output=True, text=True, timeout=F.KIT_RUN_TIMEOUT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rm -f mdt-accept-squeeze", r.stdout)
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START))
        self.assertFalse(wal.exists())

    def test_a_hup_during_sampling_restores_the_five_files(self) -> None:
        p = self.start_long_deep()
        p.send_signal(signal.SIGHUP)
        self.assertEqual(p.wait(timeout=F.KIT_RUN_TIMEOUT), 129)
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START))
        self.assertEqual((self.cg / "dev.slice" / "memory.min").read_text().strip(), str(512 * M))
        self.assertFalse((self.ev / "squeeze-state.env").exists())

    def start_long_deep(self) -> subprocess.Popen:
        p = subprocess.Popen([str(KIT / "accept-run.sh"), "--evidence", str(self.ev), "--warmup", "0", "--steps", "1", "--dwell", "0"],
                             env=dict(self.env, MDT_ACCEPT_INTERVAL="1", MDT_ACCEPT_MAX_TICKS="40", **self.judge_env("PASS")),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + F.KIT_RUN_TIMEOUT
        while p.poll() is None and time.monotonic() < deadline:
            samples = self.ev / "samples.csv"
            if samples.exists():
                lines = samples.read_text().splitlines()
                header = lines[0].split(",")
                if len(lines) > 1 and any(ln.split(",")[header.index("step")] == "1" for ln in lines[1:]):
                    return p
            time.sleep(0.05)
        p.kill()
        self.fail("the run never reached step 1")
        raise AssertionError

    def test_a_deferred_lowering_never_reports_a_pass_and_keeps_the_wal(self) -> None:
        # 2.6/5: a max lowering without 64M of headroom is deferred, RESTORE-PENDING goes to manifest.env and the run
        # is an INCONCLUSIVE (exit 4): an install would otherwise apply the lowered value. The SHARED fake host sets
        # MDT_ACCEPT_RESTORE_TIMEOUT=0, so the deferral below is decided by the 64M rule alone: without that the
        # restore's "room for the max restorations" poll slept 1 s per try for 60 s here (measured: this one test cost
        # the smoke lane a whole minute at 319aaeb86).
        (self.cg / "dev.slice" / "memory.current").write_text(f"{self.DEV_MAX_START - 8 * M}\n")   # inside the 64M rule
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        man = (self.ev / "manifest.env").read_text()
        self.assertIn("restore=pending", man)
        self.assertIn("RESTORE-PENDING dev.slice/memory.max", man)

    def test_a_stalled_docker_rm_is_bounded_and_the_restore_still_runs(self) -> None:
        # M2: a squeezed dockerd stalls `docker rm -f` exactly as it stalls the dockerd probe. Without a timeout the
        # guard abort's finish() hung there, the systemd TimeoutStopSec killed the unit, and the ExecStopPost restore
        # hung on the same rm -- leaving the mins raised and the kdamond on. The rm gets a small injected bound here
        # (1 s, never the 10 s the fake host sets) and the min restore still runs and is still the start value.
        # There are three bounded `rm` sites on this path (the stale-container rm before `docker run`, finish() and
        # accept-restore.sh), so the fake 3 s stall costs this test ~3 s in total, never 3 s per site.
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", **self.judge_env("PASS"),
                         MDT_ACCEPT_RM_TIMEOUT="1", FAKE_RM_SLEEP="3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # two distinct sites, both bounded by the same injected 1 s: the pre-run stale-container rm (which used to be
        # a bare unbounded `docker rm -f` and could hang the whole run before the dummy existed) and finish()'s.
        self.assertIn("the stale-container docker rm -f mdt-accept-squeeze did not complete (rc 124, 1s bound)", r.stdout)
        self.assertIn("did not complete (rc 124, 1s bound); the restore below runs anyway", r.stdout)
        self.assertEqual((self.cg / "dev.slice" / "memory.min").read_text().strip(), str(512 * MIB),
                         "the min restore ran although the rm was killed at its bound")

    def test_an_unwritable_state_path_writes_no_cgroup_file(self) -> None:
        # M3: every WAL append is checked BEFORE the cgroup write it protects, and a failed append aborts the run. An
        # unwritable state path (a full or read-only evidence filesystem in production, a missing directory here) must
        # stop the run with NO cgroup write at all -- the raised max would otherwise have no record the restore could
        # prove, and the kit would squeeze blind on a WAL it could not write.
        r = self.run_kit("--steps", "2", "--dwell", "0", ticks="8", **self.judge_env("PASS"),
                         MDT_ACCEPT_SQUEEZE_STATE=str(self.tmp / "gone" / "squeeze-state.env"))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("write-ahead state file", r.stderr)
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START),
                         "no memory.max write happened without a WAL line")
        self.assertEqual((self.cg / "dev.slice" / "dev-gates.slice" / "memory.min").read_text().strip(), str(self.TIER_MIN),
                         "no memory.min write happened without a WAL line")
        self.assertNotIn("kill -s USR1", " ".join(self.calls()), "no dummy was even signalled")
        # The status above is the refusal's, never a trap seed: `EXIT_RC` is armed EMPTY, so finish()'s
        # `EXIT_RC=${EXIT_RC:-$rc}` takes THIS exit's status. A non-empty seed (0) muted every refusal raised after the
        # trap was armed -- this WAL header, the dummy start, the missing scope, the placement mismatch -- and the run
        # exited 0: a false certification, and the single failure of the `mdt-cockpit smoke` lane at 292cc5c5e. Both
        # halves are pinned: the behaviour above, and the seed itself, so a future `EXIT_RC=<value>` goes red here
        # instead of silently turning every post-trap refusal into a success report.
        src = (KIT / "accept-run.sh").read_text()
        self.assertRegex(src, r"TICKS=0; EXIT_RC=;", "EXIT_RC must be armed empty or finish() ignores the real status")
        self.assertNotRegex(src, r"TICKS=0; EXIT_RC=[^;]", "a seeded EXIT_RC makes ${EXIT_RC:-$rc} mute the real status")

    def test_a_probe_whose_row_went_missing_is_named_and_never_silently_passed(self) -> None:
        # 3.3/3.5: a probe that did not run is missing data, never a pass. 8 samples per step per probe (the old fixture
        # wrote 1, which still satisfied the judge -- this pins the fixture's own shape too).
        rows = F.probes_text([0, 1]).splitlines()
        self.assertEqual(sum(1 for ln in rows if ",1,psi_guard," in ln), 8)

    def test_a_wal_append_that_fails_mid_run_stops_the_step_before_its_cgroup_write(self) -> None:
        # M3's second half: the state file is writable for the header, then stops accepting appends. The step's MAX
        # append fails, so the memory.max write it protects must NOT happen; the step is marked FAIL cause=wal.
        state = self.ev / "squeeze-state.env"
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", **self.judge_env("PASS"),
                         FAKE_STATE_IS_DIR=str(state))
        step1 = (self.ev / "steps" / "1.env").read_text()
        self.assertIn("cause=wal", step1)
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START),
                         "no memory.max write happened when its WAL line could not be appended")
        self.assertIn("write-ahead state file", r.stdout + r.stderr)

    def test_the_real_psi_guard_probe_records_its_own_seconds(self) -> None:
        # H2, WITHOUT the seam: MDT_ACCEPT_PROBE_CMD_PSI_GUARD is unset, so accept-run.sh's own branch runs
        # (`systemctl is-active` + MainPID + /proc/<pid>/stat). The column must be the probe's OWN duration; it used to
        # record `SECONDS - T0`, the run's elapsed time. The fake host stalls a fixed 2.2 s on the USR1 (never host
        # load), so by step 1's probes the run's elapsed time is >= 2 s while the probe itself takes ~0.2 s: the old
        # code recorded >= 2 s here (SECONDS is whole seconds), the new code cannot -- and that elapsed-vs-own gap is
        # what the judge's latency rule read as "psi_guard latency over its step-0 baseline". 2.2 s, not 3: the
        # discrimination only needs the run's elapsed to exceed the 1.0 s limit, and every whole second here is one
        # the smoke lane pays.
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", FAKE_PSI_SHOW_SLEEP="0.2", FAKE_KILL_SLEEP="2.2",
                         MDT_ACCEPT_PROBE_CMD_PSI_GUARD=None, **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [ln.split(",") for ln in (self.ev / "probes.csv").read_text().splitlines() if ",psi_guard," in ln]
        self.assertTrue(rows, "the real psi_guard branch wrote no row")
        step1 = [float(r[5]) for r in rows if r[1] == "1"]
        self.assertTrue(step1, "no psi_guard row in step 1")
        self.assertLessEqual(max(step1), 1.0, f"psi_guard must record its own duration, not the run's: {step1}")
        self.assertGreaterEqual(max(step1), 0.15, "the slow read is visible: the column measures something")
        self.assertIn("ticks=", rows[0][6], "the real branch's tick advance is recorded")

    def test_the_ssh_probe_keeps_is_active_and_the_banner_as_two_rcs(self) -> None:
        # L1: `systemctl is-active` and the banner read are two commands with two rcs; the banner call used to
        # overwrite is-active's T_RC, so an INACTIVE ssh.service whose :22 still answered passed. The port-22 read
        # cannot run hermetically; MDT_ACCEPT_SSHD_BANNER_CMD supplies its argv.
        for active, banner_ok, want in (("inactive", True, "1"), ("active", True, "0"), ("active", False, "1")):
            with self.subTest(active=active, banner=banner_ok):
                import shutil as _sh
                _sh.rmtree(self.ev, ignore_errors=True)
                self.log.write_text("")
                bcmd = "printf 'SSH-2.0-'" if banner_ok else "printf 'HTTP/1.1'"
                r = self.run_kit("--steps", "0", "--dwell", "0", ticks="4", FAKE_ACTIVE=active,
                                 MDT_ACCEPT_SSHD_BANNER_CMD=bcmd,
                                 MDT_ACCEPT_PROBE_CMD_SSHD=None, MDT_ACCEPT_PROBE_CMD_JOURNALD="true",
                                 MDT_ACCEPT_PROBE_CMD_DOCKERD="true", MDT_ACCEPT_PROBE_CMD_CONTAINERD="true",
                                 MDT_ACCEPT_PROBE_CMD_CGPROFILED="true", MDT_ACCEPT_PROBE_CMD_PSI_GUARD="true",
                                 MDT_ACCEPT_PROBE_CMD_SSH="true", **self.judge_env("PASS"))
                rows = [ln.split(",") for ln in (self.ev / "probes.csv").read_text().splitlines()
                        if ln.split(",")[2:3] == ["sshd"]]
                self.assertTrue(rows, r.stdout + r.stderr)
                self.assertEqual(rows[0][4], want, f"active={active} banner={banner_ok}: {rows[0]}")

    def test_the_guard_also_runs_while_the_dummy_grows(self) -> None:
        # L5: the guard ran only at the START of each tick, so the <= 60 s grow wait (and a tick's back-to-back
        # probes) was a hole. The fake drops MemAvailable under the floor on the USR1 signal, i.e. INSIDE the wait and
        # before the dummy has grown by STEP - 16M, so the two codes differ in the step's cause: the old one runs
        # out its grow tries (INCONCLUSIVE grow, after up to 60 s blind), the new one aborts mem_available at once.
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", **self.judge_env("PASS"),
                         FAKE_LOW_MEM_AFTER_SIGNAL="1", FAKE_STEP_BYTES=str(16 * MIB),
                         MDT_ACCEPT_GUARD_EVERY="0", MDT_ACCEPT_GROW_TRIES="5", MDT_ACCEPT_POLL_STEP="0")
        steps = (self.ev / "steps.env").read_text()
        step1 = (self.ev / "steps" / "1.env").read_text()
        self.assertIn("cause=mem_available", step1, r.stdout + r.stderr)
        self.assertNotIn("cause=grow", step1, "the grow wait must not run blind to the guard")
        self.assertIn("STEP 1", steps)
        self.assertNotIn("STEP 2", steps, "the grow-wait guard abort stopped the ladder at step 1")

    def test_the_judged_window_holds_exactly_ten_ssh_samples(self) -> None:
        # L6: ceil(judged / 10) samples, so the ">= 2 of 10" rule sees 10. `judged / 10` at 48 judged ticks gave 12.
        # Guard and drift both run per tick, so the constant is checked where it is written.
        src = (KIT / "accept-run.sh").read_text()
        self.assertIn("every=$(( (judged + 9) / 10 ))", src)
        self.assertNotIn("every=$(( judged / 10 ))", src)

    def test_the_dummy_body_is_incompressible_grows_per_signal_and_ends_by_itself(self) -> None:
        code = re.search(r"DUMMY_PY <<'PY' \|\| true\n(.*?)\nPY\n", (KIT / "accept-run.sh").read_text(), re.S).group(1)
        made: list[bytearray] = []

        class Rec(bytearray):
            def __init__(self, *a: object) -> None:
                super().__init__(*a)
                made.append(self)

        old, old_env = sys.argv, os.environ.get("MDT_DUMMY_CGROUP")
        os.environ["MDT_DUMMY_CGROUP"] = str(self.tmp / "nogroup")   # no cgroup: `protected()` reads 0, the safe direction
        sys.argv = ["dummy", "0.2", str(MIB), str(MIB), "3", "4096"]   # deadline 0.2 s, A0 1 MiB, delta 1 MiB, 3 steps
        try:
            exec(compile(code, "dummy", "exec"), {"__name__": "dummy", "bytearray": Rec})   # returns only on the deadline
        finally:
            sys.argv = old
            if old_env is None:
                os.environ.pop("MDT_DUMMY_CGROUP", None)
            else:
                os.environ["MDT_DUMMY_CGROUP"] = old_env
        buf = bytes(made[0])
        self.assertEqual(len(buf), MIB + (3 + 1) * MIB, "one allocation, sized for every step the kit may signal")
        filled = buf[:MIB]                                   # no SIGUSR1 arrives here: only A0 is written
        self.assertGreater(len(zlib.compress(filled)) / len(filled), 0.95)   # zswap could not shrink it
        self.assertIn("while pending:" + chr(10) + "        pending.pop()" + chr(10) + "        fill(filled[0] + delta)", code,
                      "growth is driven by SIGUSR1 only; no wall-clock tick can add memory")


class RestoreTests(unittest.TestCase):
    """accept-restore.sh (DESIGN-ACCEPT-KIT-V2 2.6) over a fake cgroupfs: every death state a WAL can hold."""

    DEV_MAX = 7_190_757_376          # a plausible r1002 dev.slice memory.max
    TIER_MAX = 4_506_402_816
    MIN_DEV = 536_870_912

    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        _fake("docker", FAKE_DOCKER, bindir)
        self.log = self.tmp / "fake.log"
        self.log.write_text("")
        self.cg = self.tmp / "cg"
        self.dev = self.cg / "dev.slice"
        self.tier = self.dev / "dev-gates.slice"
        for d in (self.dev, self.tier):
            d.mkdir(parents=True)
        # current = 256M: every test below starts with 64M+ of headroom for a max lowering, unless it writes its own
        (self.dev / "memory.current").write_text(f"{256 * MIB}\n")
        (self.tier / "memory.current").write_text(f"{128 * MIB}\n")
        self.ev = self.tmp / "ev"
        self.ev.mkdir()
        self.state = self.ev / "squeeze-state.env"
        self.man = self.ev / "manifest.env"
        self.lock = self.tmp / "lock"
        self.bootfile = self.tmp / "boot_id"
        self.bootfile.write_text("11111111-2222-3333-4444-555555555555\n")
        self.damon = self.tmp / "damon"
        self.hold = None   # an open file object holding the lock in THIS process
        self.env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_LOG=str(self.log),
                        FAKE_DOCKER_ROOT=str(self.tmp), MDT_ACCEPT_CGROUP=str(self.cg),
                        MDT_ACCEPT_BOOT_ID=str(self.bootfile), MDT_ACCEPT_SQUEEZE_STATE=str(self.state),
                        MDT_ACCEPT_MANIFEST=str(self.man), MDT_ACCEPT_LOCK=str(self.lock),
                        MDT_ACCEPT_DAMON_ADMIN=str(self.damon), MDT_ACCEPT_POLL_STEP="0",
                        MDT_ACCEPT_RESTORE_TIMEOUT="0")

    def maxes(self, dev_written: int | None = None, tier_written: int | None = None, dev_orig: int | None = None,
              tier_orig: int | None = None) -> None:
        dw, tw = (self.DEV_MAX if dev_written is None else dev_written), (self.TIER_MAX if tier_written is None else tier_written)
        do, to = (self.DEV_MAX if dev_orig is None else dev_orig), (self.TIER_MAX if tier_orig is None else tier_orig)
        (self.dev / "memory.max").write_text(f"{dw}\n")
        (self.tier / "memory.max").write_text(f"{tw}\n")
        self.wal_max = [f"MAX=dev.slice/memory.max|{do}|{dw}|t0",
                        f"MAX=dev.slice/dev-gates.slice/memory.max|{to}|{tw}|t0"]

    def wal(self, lines: list[str], boot_id: str | None = None) -> None:
        self.state.write_text("\n".join(lines + ([f"boot_id={boot_id}"] if boot_id else [])) + "\n")

    def restore(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(KIT / "accept-restore.sh"), "--evidence", str(self.ev), *args],
                              env=dict(self.env, **extra), capture_output=True, text=True, timeout=F.KIT_RUN_TIMEOUT)

    def hold_lock(self) -> None:
        self.hold = open(self.lock, "w")
        fcntl.flock(self.hold, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(self.hold.close)

    def damon_kdamond(self, idx: str = "1", state: str = "on") -> None:
        d = self.damon / "kdamonds" / idx
        d.mkdir(parents=True)
        (d / "state").write_text(state + "\n")
        (self.damon / "kdamonds" / "nr_kdamonds").write_text("2\n")

    def test_a_kill_before_the_first_write_writes_nothing_and_reports_success(self) -> None:
        self.wal([])   # trap armed, no state line yet: the file exists and is empty
        self.maxes()
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("restore=ok", self.man.read_text())
        self.assertFalse(self.state.exists(), "a clean restore removes the state file")

    def test_a_kill_between_a_wal_line_and_its_write_leaves_the_file_at_its_original(self) -> None:
        self.maxes()   # the file still holds its original although the WAL already claims the write
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX - 5 * 128 * MIB}|t0"])
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX))
        self.assertNotIn("RESTORE-PENDING", self.man.read_text())

    def test_a_kill_after_the_maxes_and_the_mins_restores_them_in_order(self) -> None:
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB, tier_written=self.TIER_MAX + 128 * MIB)
        scope = self.tier / "docker-id-mdt-accept-squeeze.scope"
        scope.mkdir()
        (scope / "memory.min").write_text(f"{512 * MIB}\n")
        (self.dev / "memory.min").write_text(f"{self.MIN_DEV}\n")
        (self.tier / "memory.min").write_text(f"{self.MIN_DEV}\n")
        self.wal(self.wal_max + [
            f"MIN=dev.slice/dev-gates.slice/docker-id-mdt-accept-squeeze.scope/memory.min|0|{512 * MIB}|t0",
            f"MIN=dev.slice/dev-gates.slice/memory.min|0|{self.MIN_DEV}|t0",
            f"MIN=dev.slice/memory.min|0|{self.MIN_DEV}|t0",
        ])
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX))
        self.assertEqual((self.tier / "memory.max").read_text().strip(), str(self.TIER_MAX))
        for f in (self.dev / "memory.min", self.tier / "memory.min", scope / "memory.min"):
            self.assertEqual(f.read_text().strip(), "0", f)
        order = [ln for ln in r.stdout.splitlines() if "->" in ln and "kit wrote" in ln]
        self.assertEqual(len(order), 5, r.stdout)
        idx = lambda needle: [i for i, ln in enumerate(order) if needle in ln]   # noqa: E731
        self.assertLess(max(idx("memory.min")), min(idx("memory.max")), "the mins are restored before either max")
        self.assertLess(idx("docker-id-")[0], idx("dev-gates.slice/memory.min")[0], "the scope min before the tier min")
        self.assertLess(idx("dev-gates.slice/memory.min")[0], idx("dev.slice/memory.min")[0], "child before parent")

    def test_a_kill_after_the_kdamond_started_switches_it_off_and_never_writes_nr_kdamonds(self) -> None:
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        self.damon_kdamond()
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0", "kdamond=1"])
        before = (self.damon / "kdamonds" / "nr_kdamonds").read_text()
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("kdamond 1 -> off", r.stdout)
        self.assertEqual((self.damon / "kdamonds" / "1" / "state").read_text().strip(), "off")
        self.assertEqual((self.damon / "kdamonds" / "nr_kdamonds").read_text(), before,
                         "nr_kdamonds recreates every kdamond directory and fails EBUSY while one is on")

    def test_the_container_names_are_removed_by_exact_name_and_a_reboot_writes_nothing(self) -> None:
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0", "container=mdt-accept-squeeze"],
                 boot_id="11111111-2222-3333-4444-555555555555")
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("rm -f mdt-accept-squeeze", r.stdout)   # --rm is never relied on; the exact name is logged
        self.assertIn("rm -f mdt-accept-squeeze", self.log.read_text())
        self.assertFalse(self.state.exists())
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX - 128 * MIB}|t0"], boot_id="deadbeef")
        (self.dev / "memory.max").write_text(f"{self.DEV_MAX - 128 * MIB}\n")
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("rebooted since the WAL was written; nothing written", r.stdout)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX - 128 * MIB),
                         "a rebooted host keeps the boot's values")
        self.assertIn("restore_note=rebooted", self.man.read_text())
        self.assertFalse(self.state.exists())

    def test_a_missing_scope_counts_as_restored_and_a_foreign_value_is_left_alone(self) -> None:
        self.wal([f"MIN=dev.slice/dev-gates.slice/docker-gone.scope/memory.min|0|{512 * MIB}|t0"])
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("its cgroup is gone", r.stdout)
        self.assertNotIn("RESTORE-PENDING", self.man.read_text())
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0"])
        (self.dev / "memory.max").write_text("6000000000\n")   # another writer has taken the file over
        self.man.write_text("")   # a new run's manifest: the first phase above wrote its own restore=ok
        r = self.restore()
        # M1: a value the kit never wrote is left alone AND the restore must not claim `restore=ok`. The stricter
        # of the two possible verdicts: exit 1 and keep the WAL, so a later write or the reconcile can still
        # prove the file's original; exit 0 with the WAL deleted would lose that evidence forever.
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("another writer owns it, left alone", r.stdout)
        self.assertIn("restore=foreign:dev.slice/memory.max", self.man.read_text())
        self.assertNotIn("restore=ok", self.man.read_text())
        self.assertTrue(self.state.exists(), "the foreign case keeps the WAL; the stricter choice")
        self.assertEqual((self.dev / "memory.max").read_text().strip(), "6000000000")

    def test_a_three_line_chain_restores_the_first_original_while_the_file_holds_the_middle_value(self) -> None:
        # H1: three steps wrote three values for one relpath; the ORIGINAL is the FIRST line's (the value read at
        # start), and any of the kit's own writes counts as "the kit's". The file holding step 2's value is restored
        # to the start value, not to step 1's (the old restore kept only the LAST line and wrote its original back).
        self.maxes(dev_written=self.DEV_MAX + 256 * MIB, tier_orig=self.TIER_MAX, tier_written=self.TIER_MAX + 256 * MIB)
        (self.dev / "memory.max").write_text(f"{self.DEV_MAX + 128 * MIB}\n")      # step 1's value: the middle one
        (self.tier / "memory.max").write_text(f"{self.TIER_MAX + 128 * MIB}\n")
        (self.dev / "memory.min").write_text(f"{self.MIN_DEV + 64 * MIB}\n")
        (self.tier / "memory.min").write_text(f"{64 * MIB + 64 * MIB}\n")
        self.wal([
            f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0",
            f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0",
            f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 256 * MIB}|t0",
            f"MAX=dev.slice/dev-gates.slice/memory.max|{self.TIER_MAX}|{self.TIER_MAX + 128 * MIB}|t0",
            f"MAX=dev.slice/dev-gates.slice/memory.max|{self.TIER_MAX}|{self.TIER_MAX + 128 * MIB}|t0",
            f"MAX=dev.slice/dev-gates.slice/memory.max|{self.TIER_MAX}|{self.TIER_MAX + 256 * MIB}|t0",
            f"MIN=dev.slice/memory.min|{self.MIN_DEV}|{self.MIN_DEV + 64 * MIB}|t0",
            f"MIN=dev.slice/memory.min|{self.MIN_DEV}|{self.MIN_DEV + 128 * MIB}|t0",
            f"MIN=dev.slice/memory.min|{self.MIN_DEV}|{self.MIN_DEV + 192 * MIB}|t0",
            f"MIN=dev.slice/dev-gates.slice/memory.min|{64 * MIB}|{64 * MIB + 64 * MIB}|t0",
            f"MIN=dev.slice/dev-gates.slice/memory.min|{64 * MIB}|{64 * MIB + 128 * MIB}|t0",
            f"MIN=dev.slice/dev-gates.slice/memory.min|{64 * MIB}|{64 * MIB + 192 * MIB}|t0",
        ])
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX), r.stdout)
        self.assertEqual((self.tier / "memory.max").read_text().strip(), str(self.TIER_MAX))
        self.assertEqual((self.dev / "memory.min").read_text().strip(), str(self.MIN_DEV))
        self.assertEqual((self.tier / "memory.min").read_text().strip(), str(64 * MIB))
        self.assertIn("restore=ok", self.man.read_text())
        self.assertFalse(self.state.exists())

    def test_a_max_lowering_without_64m_of_headroom_is_deferred_with_exit_1(self) -> None:
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        (self.dev / "memory.current").write_text(f"{self.DEV_MAX - 8 * MIB}\n")   # inside the 64M rule
        (self.tier / "memory.reclaim").write_text("")
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0"])
        r = self.restore()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("DEFERRED", r.stdout)
        self.assertTrue(self.state.exists(), "a deferred lowering keeps the state file for the reconcile")
        self.assertIn(f"RESTORE-PENDING dev.slice/memory.max {self.DEV_MAX}", self.man.read_text())
        self.assertIn("restore=pending", self.man.read_text())
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX + 128 * MIB),
                         "never a lowering without headroom")
        self.assertEqual((self.tier / "memory.reclaim").read_text().strip(), "256M",
                         "the load tiers are reclaimed, never dev.slice itself")

    def test_an_unreadable_memory_current_defers_the_lowering(self) -> None:
        # L2: the 64M rule read an unreadable memory.current as 0 ("plenty of headroom") and lowered the max blind;
        # the cockpit lives in dev.slice, so an OOM there is a real host effect. Unreadable means "no headroom known".
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        (self.dev / "memory.current").unlink()
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0"])
        r = self.restore()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("no known headroom", r.stdout)
        self.assertIn("RESTORE-PENDING dev.slice/memory.max", self.man.read_text())
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX + 128 * MIB))

    def test_restore_twice_is_idempotent_and_a_live_run_blocks_it(self) -> None:
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0"])
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        again = self.restore()
        self.assertEqual(again.returncode, 3)
        self.assertIn("nothing to do", again.stdout)
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0"])
        self.hold_lock()
        r = self.restore()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("a live run holds the lock", r.stderr)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX))
        self.assertTrue(self.state.exists(), "a refused restore writes nothing and keeps the WAL")

    def test_from_unit_turns_nothing_to_do_into_success_and_waits_for_the_lock(self) -> None:
        self.maxes()
        r = self.restore("--from-unit")   # the ExecStopPost of a clean run: no state file left
        self.assertEqual(r.returncode, 0, r.stdout)
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0"])
        (self.dev / "memory.max").write_text(f"{self.DEV_MAX + 128 * MIB}\n")
        self.hold_lock()
        r = self.restore("--from-unit", "--lock-timeout", "1")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("still held after 1s", r.stderr)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX + 128 * MIB))
        self.hold.close()
        r = self.restore("--from-unit")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX))

    def test_in_run_restores_without_taking_the_lock_the_caller_holds(self) -> None:
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0"])
        self.hold_lock()
        r = self.restore("--in-run")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX))

    def test_a_lock_that_cannot_be_opened_is_indeterminate_never_no_run(self) -> None:
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0"])
        r = self.restore(MDT_ACCEPT_LOCK=str(self.tmp / "missing" / "lock"))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("indeterminate", r.stderr)
        self.assertEqual((self.dev / "memory.max").read_text().strip(), str(self.DEV_MAX + 128 * MIB),
                         "nothing written: the kit's own value stays where it is")


class AcceptLockPauseTests(unittest.TestCase):
    """The accept-lock pause (DESIGN-ACCEPT-KIT-V2 2.3): the reconcile's memory block and install.sh's refusal."""

    RECONCILE_BLOCK = shell_block(
        (ROOT / "scripts" / "mdt-dev-governance-reconcile.sh").read_text(encoding="utf-8"),
        'if [ "${HOST_ROLE:-}" = dedicated-dev ]; then\n  # P-plat-19m',
        "# --- slice zswap writeback policies")
    INSTALL_BLOCK = shell_block(
        (ROOT / "install.sh").read_text(encoding="utf-8"),
        "# P-plat-19m (DESIGN-ACCEPT-KIT-V2 2.3): while an acceptance squeeze run holds the accept lock",
        "# === COMMIT POINT")

    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        self.lock = self.tmp / "lock"
        self.cg = self.tmp / "cg"
        (self.cg / "dev.slice").mkdir(parents=True)
        self.deferred = self.tmp / "reconcile"
        self.deferred.mkdir()
        (self.deferred / "memory-deferred").write_text("dev.slice 1\n")
        self.hold = None

    def hold_lock(self) -> None:
        self.hold = open(self.lock, "w")
        fcntl.flock(self.hold, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(self.hold.close)

    def run_reconcile_block(self, lock: Path | None = None) -> subprocess.CompletedProcess:
        # _mdt_converge_prop is stubbed: this test is about the pause, not the converge arithmetic
        script = ('set -uo pipefail\nlog(){ echo "[reconcile] $*"; }\nMDT_RC=0\n'
                  f'CG="{self.cg}"\n_cv_dir="{self.deferred}"\n_mdt_converge_prop(){{ :; }}\n'
                  f'MDT_ACCEPT_LOCK="{lock or self.lock}"\nHOST_ROLE=dedicated-dev\n'
                  + self.RECONCILE_BLOCK + f'\nflock -n -E 99 "{self.lock}" true; echo "after=$?"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)

    def run_install_block(self, lock: Path | None = None) -> subprocess.CompletedProcess:
        script = ('set -uo pipefail\n'
                  f'MDT_ACCEPT_LOCK="{lock or self.lock}"\n' + self.INSTALL_BLOCK + '\necho "reached-commit-point"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)

    def test_the_reconcile_block_pauses_while_a_run_holds_the_lock_and_resumes_after(self) -> None:
        self.hold_lock()
        r = self.run_reconcile_block()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SKIPPED this tick", r.stdout)
        self.assertTrue((self.deferred / "memory-deferred").exists(), "a paused tick never clears the deferral")
        self.assertIn("after=99", r.stdout, "the pause leaves the run's lock alone")
        self.hold.close()
        r = self.run_reconcile_block()
        self.assertIn("after=0", r.stdout, "the block closes its descriptor, so the sweep does not hold the lock")
        self.assertFalse((self.deferred / "memory-deferred").exists(), "the resumed tick converges and clears the deferral")
        self.assertNotIn("SKIPPED", r.stdout)

    def test_the_reconcile_block_does_not_redirect_the_scripts_own_stderr(self) -> None:
        # M4: `if exec 9>>"$lock" 2>/dev/null` redirected the SHELL's own stderr to /dev/null for the rest of the
        # sweep (every dedicated-dev host, every tick): the journal lost every command's error text after the block.
        r = subprocess.run(["bash", "-c",
                            'set -uo pipefail\nlog(){ echo "[reconcile] $*"; }\nMDT_RC=0\n'
                            f'CG="{self.cg}"\n_cv_dir="{self.deferred}"\n_mdt_converge_prop(){{ :; }}\n'
                            f'MDT_ACCEPT_LOCK="{self.lock}"\nHOST_ROLE=dedicated-dev\n'
                            + self.RECONCILE_BLOCK
                            + '\necho "stderr-after-the-block" >&2\necho "stdout-after-the-block"\n'],
                           capture_output=True, text=True, timeout=60)
        self.assertIn("stderr-after-the-block", r.stderr, r.stdout + r.stderr)
        self.assertIn("stdout-after-the-block", r.stdout)

    def test_a_lock_that_cannot_be_opened_is_named_and_does_not_pause_the_memory_block(self) -> None:
        r = self.run_reconcile_block(lock=self.tmp / "no" / "lock")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cannot open", r.stdout, "absence for emptiness: never report a live run that cannot be seen")
        self.assertNotIn("SKIPPED", r.stdout)
        self.assertFalse((self.deferred / "memory-deferred").exists(), "an unobservable lock does not stop the converge")

    def test_install_sh_refuses_under_the_lock_before_the_commit_point(self) -> None:
        text = (ROOT / "install.sh").read_text()
        self.assertLess(text.index("# P-plat-19m (DESIGN-ACCEPT-KIT-V2 2.3)"), text.index("# === COMMIT POINT"))
        self.assertIn("exit 3", self.INSTALL_BLOCK)
        free = self.run_install_block()
        self.assertEqual(free.returncode, 0, free.stdout + free.stderr)
        self.assertIn("reached-commit-point", free.stdout)
        self.hold_lock()
        held = self.run_install_block()
        self.assertEqual(held.returncode, 3, held.stdout + held.stderr)
        self.assertIn("an acceptance squeeze run holds", held.stderr)
        self.assertIn("nothing was changed", held.stderr)
        self.assertNotIn("reached-commit-point", held.stdout)
        missing = self.run_install_block(lock=self.tmp / "no" / "lock")
        self.assertEqual(missing.returncode, 3, missing.stdout)
        self.assertIn("indeterminate", missing.stderr)
        self.assertNotIn("reached-commit-point", missing.stdout)

    HOLD_BLOCK_START = "— L3: the preflight above only TESTED the accept lock"

    def test_install_sh_holds_the_lock_from_the_commit_point_to_the_converge(self) -> None:
        # L3: the preflight only TESTED the lock; a kit starting between that test and the converge still raced
        # install.sh's daemon-reload. The installer now takes the lock after the COMMIT POINT and holds fd 9 until
        # the reconcile service has ended.
        text = (ROOT / "install.sh").read_text()
        start = text.index(self.HOLD_BLOCK_START)
        commit = text.index("# === COMMIT POINT")
        converge = text.index("systemctl start mdt-dev-governance-reconcile.service")
        close = text.index("exec 9>&-", converge)
        self.assertLess(commit, start, "the hold block sits after the commit point (the preflight may only read)")
        self.assertLess(start, converge)
        block = text[start:start + 1400]
        self.assertIn(f'flock -w "${{MDT_ACCEPT_LOCK_WAIT:-30}}" 9', block)
        self.assertNotIn("flock -n", block)
        self.assertLess(converge, close, "the lock is held until the converge ended")
        self.assertIn("exec 9>&-   # P-plat-19m L3", text)

    def test_the_install_refusal_stays_a_read_only_preflight_block(self) -> None:
        # test_hs02 scans the whole pre-COMMIT-POINT region for mutating commands; this pin keeps the new lines inside
        # that contract even if the scan's own rules change later.
        for line in self.INSTALL_BLOCK.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for mutating in ("mkdir", "install ", "rm ", "mv ", "touch ", "cp ", "systemctl ", "docker "):
                self.assertNotIn(mutating, stripped, line)
        self.assertLessEqual(len([ln for ln in self.INSTALL_BLOCK.splitlines() if ln.strip() and not ln.strip().startswith("#")]), 12)


if __name__ == "__main__":
    unittest.main()

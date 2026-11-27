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

    def test_missing_containerd_restart_count_is_inconclusive_for_baseline_or_current(self) -> None:
        for missing_step in (0, 1):
            with self.subTest(missing_step=missing_step):
                d = F.write_run(self.tmp / f"restart-missing-{missing_step}")
                lines = (d / "probes.csv").read_text().splitlines()
                for i, line in enumerate(lines):
                    fields = line.split(",")
                    if fields[1] == str(missing_step) and fields[2] == "containerd":
                        fields[6] = "nrestarts= nrestarts_rc=timeout"
                        lines[i] = ",".join(fields)
                (d / "probes.csv").write_text("\n".join(lines) + "\n")
                body = live(d, 1)
                self.assertIn("verdict=INCONCLUSIVE", body)
                self.assertIn("missing data: containerd", body)
                self.assertNotIn("probes PASS", body)

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

    def test_a_blocking_inconclusive_after_a_pass_never_proposes_or_prints_a_k(self) -> None:
        d = F.write_run(self.tmp / "inc-after-pass", steps=(0, 1), verdicts=("PASS", "INCONCLUSIVE"),
                        causes=("", "wal"))
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 4, out)
        self.assertIn("STEP 1 INCONCLUSIVE", out)
        self.assertNotIn("STEP 1 K=", out)
        self.assertIn("no K is proposed", out)
        self.assertNotIn("K proposed=", out)
        self.assertNotIn("SQUEEZE last pass", out)
        self.assertIn("VERDICT INCONCLUSIVE", out)

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

    def test_a_dropped_topology_step_cannot_prove_a_floor_or_pass_as_a_missing_measurement(self) -> None:
        d = F.write_run(self.tmp / "topology-drop", steps=(0, 1), verdicts=("PASS", "PASS"))
        rows = list(csv.DictReader((d / "hotset.csv").open()))
        with (d / "hotset.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, F.HOT_HEAD)
            writer.writeheader()
            writer.writerows(row for row in rows if not (row["unit"] == "docker.service" and row["step"] == "1"))
        (d / "hotset-topology.jsonl").write_text(json.dumps({"kind": "dropped", "step": 1,
                                                              "unit": "docker.service", "path": "system.slice/docker.service",
                                                              "reason": "directory identity changed after commit"}) + "\n")

        evaluated = ev.evaluate(d, None)
        self.assertEqual(ev.run_verdict(evaluated), "PASS", "the cgroup ladder verdict stays independent")
        self.assertFalse(evaluated["floors"]["docker.service"]["proved"])
        self.assertEqual(evaluated["floors"]["docker.service"]["steps"], [0])
        rc, out = run_eval(str(d))
        self.assertEqual(rc, 0, out)
        self.assertIn("FLOOR docker.service=220M source=damon candidate", out)

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
        self.iomem = self.tmp / "iomem"
        self.iomem.write_text("00000000-1ffffffff : System RAM\n")
        self.ev = self.tmp / "ev"
        self.ev.mkdir()
        self.wal = self.tmp / "wal"
        self.lib = str(Path(__file__).resolve().parents[3] / "scripts" / "damon-analysis" / "lib")
        self.units = "docker.service=system.slice/docker.service user.slice=user.slice"
        # the kdamond multiplexing check reads a real CLI from PATH: the tests give it a fake that reports 0 live
        # sessions, so a developer's host state never decides a test's outcome
        bindir = self.tmp / "bin"
        F.fake_one("cgprofilectl", "printf '{\"version\": \"test\", \"sessions_live\": 0}\\n'\n", bindir)
        self.enterContext(mock.patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ['PATH']}",
                                                       "CGPROFILE_CONTROL_SOCKET": str(self.tmp / "cgprofile-control.sock")}))

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
        (self.kd / "0" / "contexts" / "0" / "schemes" / "nr_schemes").write_text("2\n")
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
                (sdir / "ops_filters" / "0" / "memcg_path").write_text(
                    ("/system.slice/docker.service" if i == 0 else "/user.slice") + "\n")
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
        leaves = []
        for unit, path in (("docker.service", "system.slice/docker.service"), ("user.slice", "user.slice")):
            leaves.append({"unit": unit, "path": path, "inode": os.stat(self.cg / path).st_ino})
        (self.ev / hs.TOPOLOGY_LOG).write_text(json.dumps({"kind": "configured", "step": 0, "source": "test",
                                                            "leaves": leaves}) + "\n")

    def args(self, *extra: str) -> list[str]:
        return ["--evidence", str(self.ev), "--state", str(self.wal), "--units", self.units, "--admin", str(self.admin),
                "--cgroup", str(self.cg), "--proc", str(self.tmp), "--iomem", str(self.iomem), "--lib", self.lib,
                "--damon-stat", str(self.dstat), "--retry-delay", "0", *extra]

    def setup_user_topology(self, user_leaves: list[str] | None) -> F.DamonSysfsModel:
        self.units = "user.slice=user.slice docker.service=system.slice/docker.service"
        user_root = self.cg / "user.slice"
        if user_leaves is not None:
            (user_root / "memory.current").unlink(missing_ok=True)
            for name in user_leaves:
                leaf = user_root / name
                leaf.mkdir(parents=True, exist_ok=True)
                (leaf / "memory.current").write_text(f"{8 * M}\n")
        model = self.sysfs_model(count=1)
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 0, out)
        return model

    def topology_records(self) -> list[dict]:
        return [json.loads(line) for line in (self.ev / hs.TOPOLOGY_LOG).read_text().splitlines()]

    def sysfs_model(self, *, count: int = 0, state: str = "off", avail: str = "vaddr paddr",
                    stats: bool = True, filters: bool = True) -> F.DamonSysfsModel:
        model = F.DamonSysfsModel(self.admin, cgroup=self.cg, avail=avail, stats=stats, filters=filters)
        model.initialize(count=count, state=state)
        da = hs.load_lib(self.lib, str(self.admin))
        self.enterContext(mock.patch.object(hs, "load_lib", return_value=da))
        self.enterContext(mock.patch.object(hs, "write_text", model.write))
        self.enterContext(mock.patch.object(hs, "read_lines", model.read_lines))
        self.enterContext(mock.patch.object(hs, "read_int", model.read_int))
        self.enterContext(mock.patch.object(da.SysfsInterface, "_write", model.write))
        self.enterContext(mock.patch.object(da.SysfsInterface, "_read", model.read))
        return model

    def test_damon_fake_materializes_children_at_the_paths_used_by_create_context(self) -> None:
        model = self.sysfs_model()
        model.write(str(self.kd / "nr_kdamonds"), 1)
        contexts = self.kd / "0" / "contexts"
        self.assertTrue((contexts / "nr_contexts").is_file())
        model.write(str(contexts / "nr_contexts"), 1)
        target_count = contexts / "0" / "targets" / "nr_targets"
        self.assertTrue(target_count.is_file())
        model.write(str(target_count), 1)
        regions_count = contexts / "0" / "targets" / "0" / "regions" / "nr_regions"
        self.assertTrue(regions_count.is_file())
        model.write(str(regions_count), 1)
        self.assertTrue((regions_count.parent / "0" / "start").is_file())

    def test_setup_turns_off_its_kdamond_if_the_started_wal_append_fails(self) -> None:
        self.sysfs_model()
        append = hs.append_wal

        def fail_started_append(state: str, *lines: str) -> None:
            if lines == ("damon_started=1",):
                raise hs.Refusal("injected start-record WAL failure")
            append(state, *lines)

        output = io.StringIO()
        with mock.patch.object(hs, "append_wal", side_effect=fail_started_append), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = hs.main(["setup", *self.args()])
        self.assertEqual(rc, 3, output.getvalue())
        self.assertIn("injected start-record WAL failure", output.getvalue())
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "off")
        self.assertEqual((self.kd / "nr_kdamonds").read_text().strip(), "0")
        self.assertIn("damon_started=0", self.wal.read_text())
        self.assertNotIn("damon_started=1", self.wal.read_text())

    def restore_model(self) -> subprocess.CompletedProcess:
        env = dict(os.environ, MDT_ACCEPT_SQUEEZE_STATE=str(self.wal), MDT_ACCEPT_MANIFEST=str(self.ev / "manifest.env"),
                   MDT_ACCEPT_DAMON_ADMIN=str(self.admin), MDT_ACCEPT_LOCK=str(self.tmp / "accept.lock"))
        return subprocess.run([str(KIT / "accept-restore.sh"), "--evidence", str(self.ev), "--in-run"],
                              env=env, capture_output=True, text=True, timeout=F.KIT_RUN_TIMEOUT)

    def test_fresh_nr_zero_creates_the_context_then_runs_the_full_damon_sequence(self) -> None:
        model = self.sysfs_model()
        ctx = self.kd / "0" / "contexts"
        self.assertFalse((ctx / "0").exists(), "the nr_contexts=0 fake must not expose contexts/0")
        rc, out = run_hotset("setup", *self.args())
        if rc != 0:
            self.assertIn("kdamonds/0/contexts/0/avail_operations is unreadable", out)
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.kd / "nr_kdamonds").read_text().strip(), "1")
        self.assertEqual((ctx / "nr_contexts").read_text().strip(), "1")
        self.assertTrue((ctx / "0" / "avail_operations").is_file())
        self.assertIn("damon_owner=created", self.wal.read_text())
        self.assertIn("kdamond=0", self.wal.read_text(), "the WAL names the owned kdamond before either count grows")
        self.assertIn("damon_stat_enabled=N", out)
        self.assertIn("kdamond=0", out)
        self.assertEqual((self.kd / "0" / "refresh_ms").read_text().strip(), "0", "N5: no periodic refresh aliasing aggr_us")
        self.assertEqual((self.kd / "0" / "contexts" / "0" / "operations").read_text().strip(), "paddr")
        self.assertEqual((self.kd / "0" / "contexts" / "0" / "monitoring_attrs" / "intervals" / "sample_us").read_text().strip(), "400000")
        self.assertEqual((self.kd / "0" / "contexts" / "0" / "monitoring_attrs" / "intervals" / "aggr_us").read_text().strip(), "8000000")
        self.assertEqual((self.kd / "0" / "contexts" / "0" / "targets" / "0" / "regions" / "0" / "end").read_text().strip(), str(8 * 1024 ** 3))
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "on")
        rc, out = run_hotset("refresh-schemes", *self.args("--step", "1"))
        self.assertEqual(rc, 0, out)
        rc, out = run_hotset("sample", *self.args("--step", "1"))
        self.assertEqual(rc, 0, out)
        self.assertIn("docker.service", (self.ev / "hotset.csv").read_text())
        self.assertIn("user.slice", (self.ev / "hotset.csv").read_text())
        rc, out = run_hotset("off", *self.args())
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "off")
        self.assertEqual(model.read(str(self.kd / "0" / "contexts" / "0" / "avail_operations")), "vaddr paddr")
        hs.restore_owned_kdamond(str(self.admin), str(self.wal))
        self.assertEqual((self.kd / "nr_kdamonds").read_text().strip(), "0")
        self.assertFalse((self.kd / "0").exists(), "nr_kdamonds=0 removes the kdamond created by the model")

    def test_hotset_preflight_is_read_only_for_a_fresh_nr_zero_interface(self) -> None:
        self.sysfs_model()
        rc, out = run_hotset("preflight", *self.args())
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.kd / "nr_kdamonds").read_text().strip(), "0")
        self.assertFalse(self.wal.exists())
        self.assertFalse((self.kd / "0").exists())

    def test_an_empty_preexisting_off_kdamond_is_taken_and_restored(self) -> None:
        self.sysfs_model(count=1)
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.kd / "nr_kdamonds").read_text().strip(), "1")
        self.assertIn("damon_owner=taken", self.wal.read_text())
        self.assertIn("damon_started=1", self.wal.read_text())
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "on")
        rc, out = run_hotset("refresh-schemes", *self.args("--step", "1"))
        self.assertEqual(rc, 0, out)
        rc, out = run_hotset("sample", *self.args("--step", "1"))
        self.assertEqual(rc, 0, out)
        self.assertIn("docker.service", (self.ev / "hotset.csv").read_text())
        rc, out = run_hotset("off", *self.args())
        self.assertEqual(rc, 0, out)
        r = self.restore_model()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.kd / "nr_kdamonds").read_text().strip(), "1")
        self.assertEqual((self.kd / "0" / "contexts" / "nr_contexts").read_text().strip(), "0")
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "off")
        self.assertFalse(self.wal.exists())

    def test_a_foreign_damon_start_between_configure_and_on_is_left_running(self) -> None:
        model = self.sysfs_model()
        da = hs.load_lib(self.lib, str(self.admin))
        original_write = model.write
        state_path = str(self.kd / "0" / "state")

        def foreign_start(path: str, value: object) -> None:
            if path == state_path and value == "on":
                original_write(path, value)  # the other actor wins immediately before our sysfs write
                raise OSError("device or resource busy")
            original_write(path, value)

        self.enterContext(mock.patch.object(da.SysfsInterface, "_write", foreign_start))
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("immediate DAMON restore is pending", out)
        self.assertIn("damon_started=0", self.wal.read_text())
        self.assertNotIn("damon_started=1", self.wal.read_text())
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "on")

        restored = self.restore_model()
        self.assertEqual(restored.returncode, 1, restored.stdout + restored.stderr)
        self.assertIn("without a successful kit start record", restored.stdout)
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "on")
        self.assertTrue(self.wal.exists(), "the pending WAL remains for an operator to resolve")

    def test_refresh_commit_is_refused_when_the_kdamond_is_off(self) -> None:
        self.sysfs_model()
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 0, out)
        rc, out = run_hotset("off", *self.args())
        self.assertEqual(rc, 0, out)

        rc, out = run_hotset("refresh-schemes", *self.args("--step", "1"))
        self.assertEqual(rc, 3, out)
        self.assertIn("commit requires a running kdamond", out)
        self.assertEqual((self.kd / "0" / "state").read_text().strip(), "off")

    def test_start_refuses_a_memcg_path_the_kernel_cannot_resolve(self) -> None:
        model = self.sysfs_model()
        da = hs.load_lib(self.lib, str(self.admin))
        original_write = model.write
        memcg_path = str(self.kd / "0" / "contexts" / "0" / "schemes" / "0" / "ops_filters" / "0" / "memcg_path")

        def missing_memcg(path: str, value: object) -> None:
            if path == memcg_path:
                original_write(path, "/not-mounted.service")
            else:
                original_write(path, value)

        self.enterContext(mock.patch.object(hs, "write_text", missing_memcg))
        self.enterContext(mock.patch.object(da.SysfsInterface, "_write", missing_memcg))
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("memcg_path '/not-mounted.service' is not an existing cgroup", out)
        self.assertEqual((self.kd / "nr_kdamonds").read_text().strip(), "0",
                         "setup restores the structural count after rejecting invalid filter input")

    def test_a_damon_stat_of_y_refuses_with_nothing_written(self) -> None:
        self.sysfs_model()
        self.dstat.write_text("Y\n")
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("DAMON_STAT is Y", out)
        self.assertIn("install.sh --require-damon-disable", out)
        self.assertFalse(self.wal.exists(), "a refusal writes nothing")
        self.assertNotIn("kdamond=", (self.kd / "nr_kdamonds").read_text())

    def test_an_absent_damon_stat_proceeds_and_records_absent(self) -> None:
        self.sysfs_model()
        self.dstat.unlink()
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DAMON_STAT is Y", out)

    def test_a_kdamond_that_is_already_on_refuses(self) -> None:
        self.sysfs_model(count=2, state="on")
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("kdamond 0 state is 'on', not off", out)
        self.assertFalse(self.wal.exists())

    def test_a_nonempty_off_kdamond_is_never_overwritten(self) -> None:
        model = self.sysfs_model(count=1)
        model.write(str(self.kd / "0" / "contexts" / "nr_contexts"), 1)
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("refusing to overwrite", out)
        self.assertFalse(self.wal.exists())
        self.assertEqual((self.kd / "0" / "contexts" / "nr_contexts").read_text().strip(), "1")

    def test_a_kernel_without_paddr_refuses(self) -> None:
        self.sysfs_model(avail="vaddr fvaddr")
        rc, out = run_hotset("setup", *self.args())
        self.assertEqual(rc, 3, out)
        self.assertIn("has no paddr", out)

    def test_a_kernel_without_sz_ops_filter_passed_refuses_never_end_minus_start(self) -> None:
        self.sysfs_model(stats=False)
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

    def test_sample_retries_after_a_transient_scheme_read_failure(self) -> None:
        self.tree(nr=1, state="off")
        self.ready()
        da = hs.load_lib(self.lib, str(self.admin))
        with mock.patch.object(da.SysfsInterface, "kdamond_update_tried_regions",
                               side_effect=[OSError("transient vanished scheme"), None]) as apply:
            rc, out = run_hotset("sample", *self.args("--step", "2", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(apply.call_count, 2, "the retry performs a fresh apply after rechecking cgroup identities")
        rows = list(csv.DictReader((self.ev / "hotset.csv").open()))
        self.assertEqual([row["unit"] for row in rows], ["docker.service", "user.slice"])

    def test_sample_refuses_after_all_retries_without_writing_a_row(self) -> None:
        self.tree(nr=1, state="off")
        self.ready()
        da = hs.load_lib(self.lib, str(self.admin))
        with mock.patch.object(da.SysfsInterface, "kdamond_update_tried_regions",
                               side_effect=OSError("scheme keeps disappearing")) as apply:
            rc, out = run_hotset("sample", *self.args("--step", "2", "--kdamond", "0"))
        self.assertEqual(rc, 3, out)
        self.assertIn("hot-set sample failed after 3 attempts", out)
        self.assertEqual(apply.call_count, 3)
        self.assertFalse((self.ev / "hotset.csv").exists(), "an incomplete sample is never appended")

    def test_sample_and_refresh_wait_between_retries_with_an_injected_delay(self) -> None:
        self.tree(nr=1, state="off")
        self.ready()
        da = hs.load_lib(self.lib, str(self.admin))
        with mock.patch.object(da.SysfsInterface, "kdamond_update_tried_regions",
                               side_effect=[OSError("transient sample read"), None]), \
                mock.patch.object(hs.time, "sleep") as sample_sleep:
            rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0", "--retry-delay", "0.125"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(sample_sleep.call_args_list, [mock.call(0.125)])

        with mock.patch.object(hs, "load_lib", return_value=object()), \
                mock.patch.object(hs, "physical_ranges", return_value=[(0, 4096)]), \
                mock.patch.object(hs, "configure", side_effect=[hs.Refusal("transient refresh write"), None]), \
                mock.patch.object(hs.time, "sleep") as refresh_sleep:
            rc, out = run_hotset("refresh-schemes", *self.args("--step", "1", "--kdamond", "0", "--retry-delay", "0.125"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(refresh_sleep.call_args_list, [mock.call(0.125)])

    def test_refresh_reenumerates_and_retries_configuration(self) -> None:
        with mock.patch.object(hs, "load_lib", return_value=object()), \
                mock.patch.object(hs, "physical_ranges", return_value=[(0, 4096)]), \
                mock.patch.object(hs, "configure", side_effect=[hs.Refusal("a leaf vanished"), None]) as configure:
            rc, out = run_hotset("refresh-schemes", *self.args("--step", "1"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(configure.call_count, 2)
        self.assertEqual(set(configure.call_args_list[0].args[3]),
                         {("user.slice", "user.slice"), ("docker.service", "system.slice/docker.service")})
        self.assertEqual(configure.call_args_list[0].args[3], configure.call_args_list[1].args[3])
        self.assertTrue(configure.call_args_list[1].kwargs["commit"])

    def test_refresh_refuses_after_all_retries(self) -> None:
        with mock.patch.object(hs, "load_lib", return_value=object()), \
                mock.patch.object(hs, "physical_ranges", return_value=[(0, 4096)]), \
                mock.patch.object(hs, "configure", side_effect=hs.Refusal("a leaf vanished")) as configure:
            rc, out = run_hotset("refresh-schemes", *self.args("--step", "1"))
        self.assertEqual(rc, 3, out)
        self.assertIn("scheme refresh failed after 3 attempts", out)
        self.assertEqual(configure.call_count, 3)

    def test_sample_reconfigures_and_measures_a_leaf_inserted_after_configure(self) -> None:
        self.setup_user_topology(["session-a.scope"])
        inserted = self.cg / "user.slice" / "session-b.scope"
        inserted.mkdir()
        (inserted / "memory.current").write_text(f"{8 * M}\n")

        rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        rows = {row["unit"]: row for row in csv.DictReader((self.ev / "hotset.csv").open())}
        self.assertEqual(set(rows), {"docker.service", "user.slice"})
        self.assertEqual(int(rows["user.slice"]["memcg_leaves"]), 2)
        schemes = self.kd / "0" / "contexts" / "0" / "schemes"
        configured_paths = {p.read_text().strip() for p in schemes.glob("*/ops_filters/0/memcg_path")}
        self.assertIn("/user.slice/session-b.scope", configured_paths)
        self.assertTrue(any(row["kind"] == "topology_change" and row["source"] == "sample"
                            for row in self.topology_records()))

    def test_configured_leaf_removed_after_configure_re_resolves_the_remaining_leaf(self) -> None:
        self.setup_user_topology(["session-a.scope", "session-b.scope"])
        shutil.rmtree(self.cg / "user.slice" / "session-a.scope")

        rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        rows = {row["unit"]: row for row in csv.DictReader((self.ev / "hotset.csv").open())}
        self.assertEqual(int(rows["user.slice"]["memcg_leaves"]), 1)
        changes = [row for row in self.topology_records() if row["kind"] == "topology_change"]
        self.assertTrue(changes)
        self.assertNotIn("user.slice/session-a.scope", {leaf["path"] for leaf in changes[-1]["after"]})

    def test_configured_leaf_unit_that_gains_a_child_is_reconfigured_to_the_child(self) -> None:
        self.setup_user_topology(None)
        child = self.cg / "user.slice" / "session-a.scope"
        child.mkdir()
        (child / "memory.current").write_text(f"{8 * M}\n")

        rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        rows = {row["unit"]: row for row in csv.DictReader((self.ev / "hotset.csv").open())}
        self.assertEqual(int(rows["user.slice"]["memcg_leaves"]), 1)
        schemes = self.kd / "0" / "contexts" / "0" / "schemes"
        paths = {p.read_text().strip() for p in schemes.glob("*/ops_filters/0/memcg_path")}
        self.assertIn("/user.slice/session-a.scope", paths)
        self.assertNotIn("/user.slice", paths)

    def test_unit_restart_with_a_new_leaf_path_is_reconfigured(self) -> None:
        self.setup_user_topology(["session-a.scope"])
        shutil.rmtree(self.cg / "user.slice" / "session-a.scope")
        replacement = self.cg / "user.slice" / "session-b.scope"
        replacement.mkdir()
        (replacement / "memory.current").write_text(f"{8 * M}\n")

        rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        schemes = self.kd / "0" / "contexts" / "0" / "schemes"
        paths = {p.read_text().strip() for p in schemes.glob("*/ops_filters/0/memcg_path")}
        self.assertIn("/user.slice/session-b.scope", paths)
        self.assertNotIn("/user.slice/session-a.scope", paths)

    def test_unit_restart_at_the_same_leaf_path_changes_the_directory_identity(self) -> None:
        self.setup_user_topology(["session-a.scope"])
        leaf = self.cg / "user.slice" / "session-a.scope"
        old_inode = leaf.stat().st_ino
        replacement = self.cg / "user.slice" / "session-replacement.scope"
        replacement.mkdir()
        (replacement / "memory.current").write_text(f"{8 * M}\n")
        retired = self.cg / "user.slice" / "session-retired.scope"
        leaf.rename(retired)
        replacement.rename(leaf)
        shutil.rmtree(retired)
        new_inode = leaf.stat().st_ino
        self.assertNotEqual(old_inode, new_inode)

        rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        changes = [row for row in self.topology_records() if row["kind"] == "topology_change"]
        self.assertTrue(changes)
        before = next(row for row in changes[-1]["before"] if row["path"] == "user.slice/session-a.scope")
        after = next(row for row in changes[-1]["after"] if row["path"] == "user.slice/session-a.scope")
        self.assertNotEqual(before["inode"], after["inode"])

    def test_leaf_changed_between_commit_and_read_is_dropped_with_its_reason(self) -> None:
        self.setup_user_topology(["session-a.scope"])
        inserted = self.cg / "user.slice" / "session-b.scope"
        inserted.mkdir()
        (inserted / "memory.current").write_text(f"{8 * M}\n")
        da = hs.load_lib(self.lib, str(self.admin))
        apply = da.SysfsInterface.kdamond_update_tried_regions

        def remove_after_apply(kdamond: int) -> None:
            apply(kdamond)
            shutil.rmtree(inserted)

        with mock.patch.object(da.SysfsInterface, "kdamond_update_tried_regions", side_effect=remove_after_apply):
            rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        rows = {row["unit"]: row for row in csv.DictReader((self.ev / "hotset.csv").open())}
        self.assertEqual(int(rows["user.slice"]["memcg_leaves"]), 1)
        dropped = [row for row in self.topology_records() if row["kind"] == "dropped"]
        self.assertTrue(any(row["path"] == "user.slice/session-b.scope" and "after commit" in row["reason"]
                            for row in dropped))

    def test_single_leaf_unit_gone_has_no_zero_row_and_other_units_continue(self) -> None:
        self.setup_user_topology(None)
        shutil.rmtree(self.cg / "user.slice")

        rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 0, out)
        rows = list(csv.DictReader((self.ev / "hotset.csv").open()))
        self.assertEqual([row["unit"] for row in rows], ["docker.service"])
        self.assertNotIn("user.slice,0,0,0,0", (self.ev / "hotset.csv").read_text())
        self.assertIn("damon_measure=failed:user.slice:topology step 0", (self.ev / "manifest.env").read_text())

    def test_no_configured_leaf_left_is_inconclusive_without_a_hotset_row(self) -> None:
        self.setup_user_topology(None)
        shutil.rmtree(self.cg / "user.slice")
        shutil.rmtree(self.cg / "system.slice" / "docker.service")

        rc, out = run_hotset("sample", *self.args("--step", "0", "--kdamond", "0"))
        self.assertEqual(rc, 4, out)
        self.assertIn("no configured leaf memcg remains", out)
        self.assertFalse((self.ev / "hotset.csv").exists())

    def test_scheme_attribution_uses_component_prefix_and_refuses_unowned_or_ambiguous_paths(self) -> None:
        units = [("user.slice", "user.slice")]
        self.assertEqual(hs.unit_for_memcg_path("/user.slice/session.scope", units), "user.slice")
        for path, configured in (("/user.slice-x/session.scope", units),
                                 ("/other.slice/session.scope", units),
                                 ("/user.slice/session.scope", units + [("session.scope", "user.slice/session.scope")])):
            with self.subTest(path=path, configured=configured):
                with self.assertRaises(hs.Refusal):
                    hs.unit_for_memcg_path(path, configured)

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

    def test_leaf_enumeration_skips_a_session_scope_that_vanished_mid_walk(self) -> None:
        vanished = self.cg / "user.slice" / "session-gone.scope"
        vanished.mkdir()
        (vanished / "memory.current").write_text("4096\n")
        listdir = os.listdir

        def vanish(path):
            if os.fspath(path) == str(vanished):
                raise FileNotFoundError(path)
            return listdir(path)

        with mock.patch.object(hs.os, "listdir", side_effect=vanish):
            self.assertEqual(hs.leaf_cgroups(str(self.cg), "user.slice"), [])

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
            with self.assertRaises(hs.Refusal) as ctx:
                hs.cgprofile_sessions()
        self.assertIn("sessions_live count", str(ctx.exception))
        F.fake_one("cgprofilectl", 'printf \'{"sessions_live": 1}\'\n', bindir)
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(hs.Refusal) as ctx:
                hs.cgprofile_sessions()
        self.assertIn("sessions_live=1", str(ctx.exception))

    def test_preflight_refuses_missing_endpoint_and_reports_bounded_cgprofile_error(self) -> None:
        missing = self.tmp / "bin-missing"
        F.fake_one("cgprofilectl", 'echo "endpoint-unset: cannot connect" >&2; exit 2\n', missing)
        env = dict(os.environ, PATH=f"{missing}:{os.environ['PATH']}", CGPROFILE_CONTROL_SOCKET="")
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(hs.Refusal) as ctx:
                hs.cgprofile_sessions()
        self.assertIn("CGPROFILE_CONTROL_SOCKET is unset", str(ctx.exception))
        env["CGPROFILE_CONTROL_SOCKET"] = "/run/cgprofile/control/cgprofile.sock"
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(hs.Refusal) as ctx:
                hs.cgprofile_sessions()
        self.assertIn("endpoint-unset: cannot connect", str(ctx.exception))
        self.assertLess(len(str(ctx.exception)), 600, "the command's error text is present but bounded")


class ShellKitTests(F.ShellKitBase):
    """accept-run.sh: the ladder, the WAL, the per-step writes, the probes, the stop rule, the cleanup paths."""

    def test_hotset_sample_runs_before_the_first_judged_row_of_each_window(self) -> None:
        src = (KIT / "accept-run.sh").read_text()
        tick = src[src.index('tick() { # <phase>'):src.index('dwell() { # <phase>')]
        self.assertIn('[ "$i" = 1 ]', tick, "step 0 and every later judged window sample on their first tick")
        self.assertLess(tick.index("hotset sample --step"), tick.index('write_row "$ph" "$s"'))

    def test_product_warmup_formula_and_dummy_deadline_include_the_rounded_warmup(self) -> None:
        src = (KIT / "accept-run.sh").read_text()
        override = src.index('if [ -n "${MDT_ACCEPT_WARMUP_TICKS:-}" ]; then')
        zero_interval = src.index('elif [ "$INTERVAL" = 0 ]; then', override)
        formula = src.index('WARMUP_TICKS=$(( (WARMUP + INTERVAL - 1) / INTERVAL ))', zero_interval)
        elapsed = src.index('WARMUP_RUN_SECONDS=$((WARMUP_TICKS * INTERVAL))', formula)
        warmup_branch = src[override:elapsed]
        self.assertLess(override, zero_interval)
        self.assertIn('else\n  WARMUP_TICKS=$(( (WARMUP + INTERVAL - 1) / INTERVAL ))', warmup_branch,
                      "without the override and with INTERVAL > 0, round the product warm-up up to full ticks")
        self.assertIn('WARMUP_RUN_SECONDS=$((WARMUP_TICKS * INTERVAL))', warmup_branch)
        self.assertEqual((600 + 5 - 1) // 5, 120)
        self.assertEqual(120 * 5, 600)
        self.assertIn('dummy_budget=$((WARMUP_RUN_SECONDS + (STEPS + 2) * DWELL))', src)
        self.assertIn('dummy_deadline=$((2 * dummy_budget))', src)

    def test_post_run_block_requires_the_collected_run_unit_to_be_inactive_before_its_trap(self) -> None:
        readme = (ROOT / "README.md").read_text()
        section = readme.index("4. **Eval, restore, snapshot and restart the reconcile timer.**")
        fence = readme.index("```bash", section)
        end = readme.index("```", fence + len("```bash"))
        block = readme[fence:end]
        check = block.index("UNIT_STATE=$(systemctl is-active mdt-accept-1")
        self.assertLess(check, block.index("restart_reconcile()"))
        self.assertLess(check, block.index("trap restart_reconcile EXIT"))
        self.assertLess(check, block.index("snap()"))
        self.assertIn("wait for the --collect unit to finish", block)
        self.assertIn("systemctl reset-failed mdt-accept-1", block)

    def test_cgprofile_environment_refusal_happens_before_any_host_write(self) -> None:
        r = self.run_kit("--steps", "0", "--dwell", "0", ticks="2", FAKE_SESSIONS_LIVE="1",
                         **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("sessions_live=1", r.stderr)
        self.assertFalse((self.ev / "samples.csv").exists())
        self.assertFalse(any(line.startswith("run ") for line in self.calls()), "no dummy or other Docker write ran")
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START))
        self.assertEqual((self.cg / "dev.slice" / "memory.min").read_text().strip(), str(512 * M))

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

    def test_warmup_uses_its_own_tick_count_and_records_the_ticks_that_ran(self) -> None:
        r = self.run_kit("--steps", "0", "--dwell", "0", ticks="12", MDT_ACCEPT_WARMUP_TICKS="4",
                         **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = list(csv.DictReader((self.ev / "samples.csv").open()))
        self.assertEqual(sum(row["phase"] == "warmup" for row in rows), 4, "the warm-up is not the settle window")
        manifest = (self.ev / "manifest.env").read_text()
        self.assertIn("warmup=0\n", manifest, "the interval-zero test run records no elapsed warm-up seconds")
        self.assertIn("warmup_ticks=4\n", manifest, "the manifest records the four warm-up ticks that ran")

    def test_dummy_deadline_is_twice_the_computed_nominal_run_budget(self) -> None:
        r = self.run_kit("--steps", "2", "--dwell", "300", "--warmup", "0", ticks="12",
                         **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        fields = dict(line.split("=", 1) for line in (self.ev / "manifest.env").read_text().splitlines() if "=" in line)
        nominal = int(fields["warmup"]) + (int(fields["squeeze_step"]) + 2) * int(fields["squeeze_dwell"])
        self.assertEqual(int(fields["dummy_deadline_s"]), 2 * nominal)

    def test_control_pass_abort_is_recorded_as_info_and_keeps_a_ladder_pass(self) -> None:
        # A 3-tick total lets step 0 finish (settle + judge) and aborts the first INFO-only control tick.
        r = self.run_kit("--steps", "0", "--dwell", "0", ticks="3", **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.judge_steps(), ["0"])
        manifest = (self.ev / "manifest.env").read_text()
        self.assertIn("squeeze_verdict=PASS\n", manifest)
        self.assertIn("control_pass=aborted:INCONCLUSIVE:ticks\n", manifest)

    def test_dockerd_probe_requires_success_and_a_version_shaped_stdout(self) -> None:
        body = r'''if [ "$1" = info ] && [ "$3" = "{{.DockerRootDir}}" ]; then
  echo "$FAKE_DOCKER_ROOT"; exit 0
fi
if [ "$1" = info ] && [ "$3" = "{{.ServerVersion}}" ]; then
  n=0; [ -f "$FAKE_DOCKER_COUNT" ] && n=$(cat "$FAKE_DOCKER_COUNT")
  n=$((n + 1)); echo "$n" > "$FAKE_DOCKER_COUNT"
  case $n in
    1) echo "Cannot connect to the Docker daemon" >&2; exit 7 ;;
    2) echo "Cannot connect to the Docker daemon"; exit 0 ;;
    *) echo "29.0.1"; exit 0 ;;
  esac
fi
'''
        _fake("docker", body + F.FAKE_DOCKER, self.tmp / "bin")
        r = self.run_kit("--steps", "2", "--dwell", "0", ticks="8", FAKE_DOCKER_COUNT=str(self.tmp / "docker-count"),
                         MDT_ACCEPT_PROBE_CMD_DOCKERD=None, **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [ln.split(",") for ln in (self.ev / "probes.csv").read_text().splitlines()
                if ln.split(",")[2:3] == ["dockerd"]]
        self.assertEqual(len(rows), 3, rows)
        self.assertEqual(rows[0][4], "7", rows[0])
        self.assertEqual(rows[0][6], "version=", "docker stderr is not accepted as server-version stdout")
        self.assertEqual(rows[1][4], "1", rows[1])
        self.assertEqual(rows[2][4], "0", rows[2])
        self.assertIn("version=29.0.1", rows[2][6])

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
        self.assertIn("verdict=FAIL", (self.ev / "steps" / "0.env").read_text())
        self.assertIn("mem_available", (self.ev / "steps" / "0.env").read_text())
        self.assertEqual(len(self.judge_steps()), 1)

    def test_the_bound_min_guard_remains_a_measured_fail(self) -> None:
        # bound_min is a failed safety invariant, not a kit/evidence failure: it remains FAIL and supplies a first-fail K.
        (self.cg / "system.slice" / "memory.min").write_text(f"{8 * 1024 * MIB - 128 * MIB}\n")
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        step = (self.ev / "steps" / "1.env").read_text()
        self.assertIn("verdict=FAIL", step)
        self.assertIn("cause=bound_min", step)
        self.assertIn("k_first_fail=", (self.ev / "manifest.env").read_text())

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

    def test_a_foreign_stale_wal_refuses_without_reusing_or_truncating_its_evidence(self) -> None:
        # L1: a non-zero stale restore must stop before the new header or WAL can reuse the old evidence directory.
        self.ev.mkdir()
        state = self.ev / "squeeze-state.env"
        original = self.DEV_MAX_START
        written = original + 128 * M
        state_text = (f"MAX=dev.slice/memory.max|{original}|{written}|t0\n"
                      f"boot_id={self.boot.read_text().strip()}\n")
        state.write_text(state_text)
        (self.cg / "dev.slice" / "memory.max").write_text(f"{original + 512 * M}\n")  # a foreign value
        manifest = self.ev / "manifest.env"
        manifest.write_text("old-run=preserved\n")
        steps = self.ev / "steps.env"
        steps.write_text("old ladder evidence\n")

        r = self.run_kit("--steps", "0", "--dwell", "0", ticks="2", **self.judge_env("PASS"))

        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn(f"stale write-ahead state file {state}", r.stderr)
        self.assertEqual(state.read_text(), state_text, "the old WAL is kept byte-for-byte; no new-run lines are appended")
        self.assertEqual(manifest.read_text(), "old-run=preserved\nrestore=foreign:dev.slice/memory.max\n",
                         "the restore's foreign annotation is retained and no new-run header truncates the manifest")
        self.assertEqual(steps.read_text(), "old ladder evidence\n", "refusal does not truncate the previous ladder")
        self.assertFalse((self.ev / "samples.csv").exists(), "the refused run creates no new sample file")
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(original + 512 * M),
                         "the value owned by another writer stays untouched")

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
        # append fails, so the memory.max write it protects must NOT happen; the step is INCONCLUSIVE cause=wal, never
        # a measured FAIL or a PASS inherited from step 0.
        state = self.ev / "squeeze-state.env"
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", **self.judge_env("PASS"),
                         FAKE_STATE_IS_DIR=str(state))
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        step1 = (self.ev / "steps" / "1.env").read_text()
        self.assertIn("verdict=INCONCLUSIVE", step1)
        self.assertIn("cause=wal", step1)
        self.assertIn("squeeze_verdict=INCONCLUSIVE", (self.ev / "manifest.env").read_text())
        self.assertIn("k_first_fail=\n", (self.ev / "manifest.env").read_text())
        self.assertIn("STEP 1 INCONCLUSIVE", r.stdout)
        self.assertNotIn("STEP 1 K=", r.stdout)
        self.assertEqual((self.cg / "dev.slice" / "memory.max").read_text().strip(), str(self.DEV_MAX_START),
                         "no memory.max write happened when its WAL line could not be appended")
        self.assertIn("write-ahead state file", r.stdout + r.stderr)

    def test_a_cgroup_write_failure_is_inconclusive_not_a_false_k_failure(self) -> None:
        # A failed memory.min write after its WAL line is a kit write failure, not evidence that this K failed.
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", **self.judge_env("PASS"),
                         FAKE_FAIL_CGROUP_WRITE="dev.slice/memory.min")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        step = (self.ev / "steps" / "1.env").read_text()
        self.assertIn("verdict=INCONCLUSIVE", step)
        self.assertIn("cause=write:dev.slice/memory.min", step)
        manifest = (self.ev / "manifest.env").read_text()
        self.assertIn("squeeze_verdict=INCONCLUSIVE", manifest)
        self.assertIn("k_first_fail=\n", manifest)
        self.assertIn("STEP 1 INCONCLUSIVE", r.stdout)
        self.assertNotIn("STEP 1 K=", r.stdout)

    def test_the_real_psi_guard_probe_records_its_own_seconds(self) -> None:
        # H2: compare the probe with the same step's sample elapsed value, which is captured before that probe. The
        # fake host's USR1 stall makes run elapsed exceed probe duration; no absolute duration threshold depends on
        # worker load or spawn speed.
        r = self.run_kit("--steps", "1", "--dwell", "0", ticks="8", FAKE_PSI_SHOW_SLEEP="0.2", FAKE_KILL_SLEEP="2.2",
                         MDT_ACCEPT_PROBE_CMD_PSI_GUARD=None, **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [ln.split(",") for ln in (self.ev / "probes.csv").read_text().splitlines() if ",psi_guard," in ln]
        self.assertTrue(rows, "the real psi_guard branch wrote no row")
        step1 = [float(r[5]) for r in rows if r[1] == "1"]
        self.assertTrue(step1, "no psi_guard row in step 1")
        with (self.ev / "samples.csv").open() as fh:
            sample_rows = list(csv.DictReader(fh))
        step1_elapsed = [float(row["elapsed"]) for row in sample_rows
                         if row["step"] == "1" and row["phase"] == "judge"]
        self.assertTrue(step1_elapsed, "step 1 wrote no elapsed sample row")
        self.assertLess(max(step1), max(step1_elapsed) - 1.0,
                        f"psi_guard seconds must be below run elapsed by >1s: probes={step1}, elapsed={step1_elapsed}")
        self.assertIn("ticks=", rows[0][6], "the real branch's tick advance is recorded")

    def test_daemon_reload_check_bounds_both_docker_rm_paths(self) -> None:
        script = (KIT / "daemon-reload-check.sh").read_text()
        self.assertIn('RM_TIMEOUT=${MDT_ACCEPT_RM_TIMEOUT:-30}', script)
        self.assertIn('trap \'timeout -k 5 "$RM_TIMEOUT" docker rm -f "$NAME" >/dev/null 2>&1\'', script)
        self.assertIn('timeout -k 5 "$RM_TIMEOUT" docker rm -f "$NAME" >/dev/null 2>&1   # stale container', script)

    def test_a_blocked_psi_guard_systemctl_call_is_bounded_and_records_timeout(self) -> None:
        # L2: a fake systemctl blocks only this call. The reader's timeout records its 124 status as `timeout`;
        # the assertion is the recorded result, never a wall-clock duration.
        r = self.run_kit("--steps", "0", "--dwell", "0", ticks="2",
                         MDT_ACCEPT_PROBE_CMD_PSI_GUARD=None, MDT_ACCEPT_SYSTEMCTL_TIMEOUT="1",
                         FAKE_SYSTEMCTL_BLOCK_MATCH="is-active mdt-psi-guard.service",
                         FAKE_SYSTEMCTL_BLOCK_ONCE=str(self.tmp / "systemctl-blocked"),
                         FAKE_SYSTEMCTL_BLOCK_SLEEP="5", **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [ln.split(",") for ln in (self.ev / "probes.csv").read_text().splitlines()
                if ln.split(",")[2:3] == ["psi_guard"]]
        self.assertTrue(rows, r.stdout + r.stderr)
        self.assertEqual(rows[0][4], "timeout", rows[0])
        self.assertIn("systemctl is-active mdt-psi-guard.service", self.calls())

    def test_a_missing_containerd_restart_count_stays_blank_and_blocks_probe_pass(self) -> None:
        # L2: both the sample and the liveness observation time out. A blank count and `nrestarts_rc=timeout` remain
        # missing data; they cannot be normalized to zero or let the liveness criterion PASS.
        r = self.run_kit("--steps", "0", "--dwell", "0", ticks="2",
                         MDT_ACCEPT_PROBE_CMD_CONTAINERD=None, MDT_ACCEPT_SYSTEMCTL_TIMEOUT="1",
                         FAKE_SYSTEMCTL_BLOCK_MATCH="show -p NRestarts --value containerd.service",
                         FAKE_SYSTEMCTL_BLOCK="1", FAKE_SYSTEMCTL_BLOCK_SLEEP="5",
                         **self.judge_env("PASS"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with (self.ev / "samples.csv").open() as fh:
            samples = list(csv.DictReader(fh))
        self.assertTrue(samples)
        self.assertTrue(all(row["containerd_restarts"] == "" for row in samples), samples)
        rows = [ln.split(",") for ln in (self.ev / "probes.csv").read_text().splitlines()
                if ln.split(",")[1:3] == ["0", "containerd"]]
        self.assertTrue(rows, r.stdout + r.stderr)
        self.assertEqual(rows[0][4], "0", rows[0])  # docker exec succeeded; NRestarts remains independently missing
        self.assertIn("nrestarts= nrestarts_rc=timeout", rows[0][6])
        body = live(self.ev, 0)
        self.assertIn("probes INCONCLUSIVE", body)
        self.assertIn("missing data: containerd", body)

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

    def test_the_sshd_row_records_the_larger_of_the_two_probe_durations_as_decimal_seconds(self) -> None:
        # Fix round 3, the failure of `mdt-cockpit smoke` at 8031f7732: the sshd row's seconds was
        # `$((secs > T_SECONDS ? secs : T_SECONDS))`, i.e. bash ARITHMETIC over two `S.mmm` decimals. A decimal is no
        # integer operand at all -- bash answered `0.045: syntax error: invalid arithmetic operator (error token is
        # ".045")` -- so `probe_record` never ran: the sshd probe wrote NO row and the step read INCONCLUSIVE
        # `missing data`. Three halves are pinned, each linear in the samples it checks, never in load or speed.
        #
        # (a) the SHIPPED block, driven with durations instead of a clock: `timed` is stubbed, so both reads of the
        #     sshd branch (is-active, then the banner) carry exactly the decimals named here -- `0.045`, `1.500`, and
        #     the timeout's `<limit>.000`. `probe_record` and the rc composition are the shipped ones.
        block = shell_block((KIT / "accept-run.sh").read_text(), "probe_var() { # <name>",
                            "probe_cycle() { # <step> <i> <judged_ticks>")
        probes = self.tmp / "probes-unit.csv"
        # the stub is defined AFTER the block that carries the real `timed`: bash resolves a function name at CALL
        # time, so this is the one `probe_run` sees, and it hands the branch the decimals named in `cases`.
        stub = ("timed() { TW_N=$((TW_N + 1))\n"
                "  if [ \"$TW_N\" = 1 ]; then T_RC=$A_RC; T_SECONDS=$A_SEC; T_OUT=$A_OUT\n"
                "  else T_RC=$B_RC; T_SECONDS=$B_SEC; T_OUT=$B_OUT; fi; return 0; }\n")
        script = ("set -uo pipefail\n"
                  f"PROBES='{probes}'\nSSH_TARGET='root@127.0.0.1'\nTW_N=0\n"
                  + block + stub + "probe_run 0 sshd\n")
        cases = (  # is-active: seconds, rc, output | banner: seconds, rc, output | the row's rc and seconds
            ("0.045", "0", "active", "0.004", "0", "SSH-2.0-x", "0", "0.045"),      # the leading-zero fraction
            ("0.004", "0", "active", "1.500", "0", "SSH-2.0-x", "0", "1.500"),      # the banner is the larger one
            ("2.000", "timeout", "activating", "0.010", "0", "SSH-2.0-x", "1", "2.000"),   # the timeout's limit
            ("1.234", "0", "inactive", "0.012", "0", "SSH-2.0-x", "1", "1.234"),     # L1: the rc is still the pair's
        )
        for (asec, arc, aout, bsec, brc, bout, want_rc, want_secs) in cases:
            with self.subTest(is_active_seconds=asec, banner_seconds=bsec):
                if probes.exists():
                    probes.unlink()
                got = subprocess.run(
                    ["bash", "-c", script], capture_output=True, text=True, timeout=60,
                    env=dict(os.environ, A_SEC=asec, A_RC=arc, A_OUT=aout, B_SEC=bsec, B_RC=brc, B_OUT=bout,
                             MDT_ACCEPT_SSHD_BANNER_CMD="", MDT_ACCEPT_PROBE_CMD_SSHD=""))
                self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
                rows = [ln.split(",") for ln in probes.read_text().splitlines() if ln.split(",")[2:3] == ["sshd"]]
                self.assertEqual(len(rows), 1, f"exactly one sshd row: {got.stdout + got.stderr}")
                self.assertEqual(rows[0][4], want_rc, f"the rc half of L1 is untouched: {rows[0]}")
                self.assertEqual(rows[0][5], want_secs,
                                 f"the LARGER of {asec} s and {bsec} s, as an S.mmm decimal: {rows[0]}")
                self.assertRegex(rows[0][5], r"^\d{1,2}\.\d{3}$", f"the S.mmm form every probe row uses: {rows[0]}")
        # (b) the real path, end to end, with the clock the kit really reads: the fake `systemctl` stalls only on
        #     `is-active ssh.service` (0.20 s) and the banner argv answers at once, so the recorded row must be the
        #     is-active read. One run, one 0.2 s fake stall; the assertion is ordering (`>= 0.200 s`) and format.
        import shutil as _sh
        _sh.rmtree(self.ev, ignore_errors=True)
        self.log.write_text("")
        _fake("systemctl", "case \"$*\" in \"is-active ssh.service\") sleep 0.2 ;; esac\n" + F.FAKE_SYSTEMCTL,
              self.tmp / "bin")
        r = self.run_kit("--steps", "0", "--dwell", "0", ticks="2", FAKE_ACTIVE="active",
                         MDT_ACCEPT_PROBE_CMD_SSHD=None, MDT_ACCEPT_SSHD_BANNER_CMD="printf 'SSH-2.0-'",
                         **self.judge_env("PASS"))
        rows = [ln.split(",") for ln in (self.ev / "probes.csv").read_text().splitlines()
                if ln.split(",")[2:3] == ["sshd"]]
        self.assertTrue(rows, r.stdout + r.stderr)
        row = rows[0]
        self.assertEqual(row[4], "0", "the pass rule is untouched: active + banner = rc 0")
        self.assertIn("is_active_rc=0", row[6])
        self.assertIn("banner_rc=0", row[6], "the banner's own rc stays separate (L1)")
        self.assertRegex(row[5], r"^\d{1,3}\.\d{3}$", f"an S.mmm decimal, like every other probe row: {row}")
        whole, frac = row[5].split(".")
        self.assertGreaterEqual(int(whole) * 1000 + int(frac), 200,
                                f"the larger (0.2 s is-active) of the two reads is recorded: {row}")
        # (c) the conversion on its own: BASE 10 (a leading-zero fraction can never be octal), a non-decimal refused
        helpers = shell_block((KIT / "accept-run.sh").read_text(), "us_of_seconds() {", "timed() {")
        for value, want in (("0.045", 45000), ("0.041", 41000), ("0.078", 78000), ("1.500", 1500000),
                            ("0.5", 500000), ("2.000", 2000000), ("10", 10000000), ("12.345", 12345000)):
            got = subprocess.run(["bash", "-c", helpers + f"\nus_of_seconds '{value}'"], capture_output=True, text=True,
                                 timeout=30)
            self.assertEqual(got.returncode, 0, got.stderr)
            self.assertEqual(got.stdout.strip(), str(want), f"`{value}` is {want} us, read base 10")
        for bad in ("", ".500", "0.", "abc", "0..4"):
            got = subprocess.run(["bash", "-c", helpers + f"\nus_of_seconds '{bad}'"], capture_output=True, text=True,
                                 timeout=30)
            self.assertEqual(got.returncode, 1, f"`{bad}` is refused, never printed as a number: {got.stdout!r}")

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

    def damon_tree(self, *, states: tuple[str, ...], contexts: tuple[int, ...]) -> Path:
        root = self.damon / "kdamonds"
        root.mkdir(parents=True)
        (root / "nr_kdamonds").write_text(f"{len(states)}\n")
        for idx, (state, count) in enumerate(zip(states, contexts, strict=True)):
            kd = root / str(idx)
            kd.mkdir()
            (kd / "state").write_text(f"{state}\n")
            (kd / "contexts").mkdir()
            (kd / "contexts" / "nr_contexts").write_text(f"{count}\n")
        return root

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

    def test_a_legacy_wal_switches_its_named_kdamond_off_without_count_ownership(self) -> None:
        self.maxes(dev_written=self.DEV_MAX + 128 * MIB)
        self.damon_kdamond()
        self.wal([f"MAX=dev.slice/memory.max|{self.DEV_MAX}|{self.DEV_MAX + 128 * MIB}|t0", "kdamond=1"])
        before = (self.damon / "kdamonds" / "nr_kdamonds").read_text()
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("kdamond 1 -> off", r.stdout)
        self.assertEqual((self.damon / "kdamonds" / "1" / "state").read_text().strip(), "off")
        self.assertEqual((self.damon / "kdamonds" / "nr_kdamonds").read_text(), before,
                         "a legacy WAL owns no structural count, so it only switches off its named kdamond")

    def test_restore_removes_only_the_created_kdamond_after_all_are_off(self) -> None:
        root = self.damon_tree(states=("on",), contexts=(1,))
        self.wal(["damon_owner=created", "damon_nr_kdamonds=0", "damon_kdamond=0", "damon_nr_contexts=0", "kdamond=0"])
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nr_kdamonds 1 -> 0", r.stdout)
        self.assertEqual((root / "nr_kdamonds").read_text().strip(), "0")
        self.assertEqual((root / "0" / "state").read_text().strip(), "off")
        self.assertFalse(self.state.exists())

    def test_restore_removes_only_the_context_grown_on_a_taken_kdamond(self) -> None:
        root = self.damon_tree(states=("on",), contexts=(1,))
        self.wal(["damon_owner=taken", "damon_nr_kdamonds=1", "damon_kdamond=0", "damon_nr_contexts=0", "kdamond=0"])
        r = self.restore()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("contexts/nr_contexts 1 -> 0", r.stdout)
        self.assertEqual((root / "nr_kdamonds").read_text().strip(), "1")
        self.assertEqual((root / "0" / "contexts" / "nr_contexts").read_text().strip(), "0")
        self.assertFalse(self.state.exists())

    def test_created_count_restore_refuses_after_count_drift_without_touching_an_unowned_kdamond(self) -> None:
        root = self.damon_tree(states=("on", "on"), contexts=(1, 0))
        self.wal(["damon_owner=created", "damon_nr_kdamonds=0", "damon_kdamond=0", "damon_nr_contexts=0", "kdamond=0"])
        r = self.restore()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("expected 0 or 1", r.stdout)
        self.assertEqual((root / "nr_kdamonds").read_text().strip(), "2")
        self.assertEqual((root / "0" / "state").read_text().strip(), "on", "count drift means even the saved index is not trusted")
        self.assertEqual((root / "1" / "state").read_text().strip(), "on", "the unowned kdamond is never touched")
        self.assertTrue(self.state.exists(), "the pending WAL remains the recovery record")

    def test_taken_count_restore_refuses_before_stopping_a_replaced_kdamond(self) -> None:
        root = self.damon_tree(states=("on", "on"), contexts=(1, 0))
        self.wal(["damon_owner=taken", "damon_nr_kdamonds=1", "damon_kdamond=0", "damon_nr_contexts=0", "kdamond=0"])
        r = self.restore()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("expected unchanged 1", r.stdout)
        self.assertEqual((root / "nr_kdamonds").read_text().strip(), "2")
        self.assertEqual((root / "0" / "state").read_text().strip(), "on", "count drift blocks a write to the old index")
        self.assertEqual((root / "1" / "state").read_text().strip(), "on")
        self.assertTrue(self.state.exists())

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
    INSTALL_HOLD = shell_block(
        (ROOT / "install.sh").read_text(encoding="utf-8"),
        "_cv_accept_hold_rc=0",
        'mkdir -p "$CONFIG_DIR" /var/lib/mdt')

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
        script = self.reconcile_script(lock) + f'\nflock -n -E 99 "{self.lock}" true; echo "after=$?"\n'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)

    def reconcile_script(self, lock: Path | None = None) -> str:
        # _mdt_converge_prop is stubbed: this test is about the pause, not the converge arithmetic.
        return ('set -uo pipefail\nlog(){ echo "[reconcile] $*"; }\nMDT_RC=0\n'
                f'CG="{self.cg}"\n_cv_dir="{self.deferred}"\n_mdt_converge_prop(){{ :; }}\n'
                f'MDT_ACCEPT_LOCK="{lock or self.lock}"\nHOST_ROLE=dedicated-dev\n'
                + self.RECONCILE_BLOCK)

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

    HOLD_BLOCK_START = "— L3/M1: the preflight above only TESTED the accept lock"

    def test_install_uses_a_shared_hold_through_both_reconcile_triggers(self) -> None:
        # M1: install's shared hold lets both the timer tick from enable --now and the explicit service start
        # converge. Its exclusive run/restore callers cannot start before the hold closes.
        text = (ROOT / "install.sh").read_text()
        start = text.index(self.HOLD_BLOCK_START)
        commit = text.index("# === COMMIT POINT")
        timer = text.index("systemctl enable --now mdt-dev-governance-reconcile.timer")
        converge = text.index("systemctl start mdt-dev-governance-reconcile.service")
        close = text.index("exec 9>&-", converge)
        self.assertLess(commit, start, "the hold block sits after the commit point (the preflight may only read)")
        self.assertLess(start, timer)
        self.assertLess(timer, converge)
        self.assertLess(converge, close, "the shared lock is held until the explicit converge ended")
        self.assertIn(f'flock -s -w "${{MDT_ACCEPT_LOCK_WAIT:-30}}" 9', self.INSTALL_HOLD)
        self.assertIn('flock -n -s -E 99 "$_cv_accept_lock" true', self.INSTALL_BLOCK)
        reconcile_source = (ROOT / "scripts" / "mdt-dev-governance-reconcile.sh").read_text()
        self.assertIn("flock -n -s 9", reconcile_source)

        # A fake systemctl starts a reconcile for both timer activation and the explicit start while the extracted
        # install hold is still open. This reproduces the timer-first case where an explicit start can join a
        # reconcile instance that is already running. Both shared holders must converge rather than report SKIPPED.
        import shlex
        child = shlex.quote(self.reconcile_script())
        script = ('set -uo pipefail\n'
                  f'MDT_ACCEPT_LOCK="{self.lock}"\n_cv_accept_lock="$MDT_ACCEPT_LOCK"\n'
                  + self.INSTALL_HOLD
                  + '\nsystemctl() { case "$*" in '
                  + '"enable --now mdt-dev-governance-reconcile.timer"|"start mdt-dev-governance-reconcile.service") '
                  + 'bash -c ' + child + ' ;; esac; }\n'
                  + 'systemctl enable --now mdt-dev-governance-reconcile.timer\n'
                  + 'systemctl start mdt-dev-governance-reconcile.service\n'
                  + 'exec 9>&-\n')
        started = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertNotIn("SKIPPED this tick", started.stdout)
        self.assertFalse((self.deferred / "memory-deferred").exists(), "the timer/service converge runs under install's shared hold")

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

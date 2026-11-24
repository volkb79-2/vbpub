"""P-plat-19g: the acceptance kit under tools/accept/. Hermetic: fixture CSVs, fake docker/systemctl on PATH, fake /proc and cgroup roots."""
from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "tools" / "accept"
MIB = 1024 ** 2
spec = importlib.util.spec_from_file_location("accept_eval_t", KIT / "accept-eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

UNITS = ["ssh.service", "docker.service", "user.slice", "dev-interactive.slice"]
HEAD = ["utc", "elapsed", "phase", "U", "init_current", "W"] + [f"{u}.{c}" for u in UNITS for c in ("current", "active", "refault_anon", "refault_file")]
HEAD += ["system.psi_some_avg10", "user.psi_some_avg10"] + [f"{t}.{c}" for t in ("dev", "gates", "bg") for c in ("max", "oom", "oom_kill")] + ["vmstat_oom_kill"]


def write_run(d: Path, *, refault: int = 0, oom: int = 0, max_rise: int = 5, psi: float = 0.1) -> Path:
    """One warm-up row and 100 window rows: U = i MiB (p99 = 99 MiB), init 50 MiB, W 30 MiB, docker hot set 300 MiB."""
    d.mkdir(parents=True, exist_ok=True)
    with (d / "samples.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, HEAD)
        w.writeheader()
        for i in range(101):
            row = {k: "0" for k in HEAD}
            row.update(utc="00:00:00", elapsed=str(i * 10), phase="warmup" if i == 0 else "window", U=str(max(i, 1) * MIB),
                       init_current=str(50 * MIB), W=str(30 * MIB))
            row.update({"docker.service.active": str(300 * MIB), "ssh.service.active": str(4 * MIB), "dev-interactive.slice.active": str(200 * MIB),
                        "docker.service.refault_file": str(refault * i // 100), "system.psi_some_avg10": str(psi), "dev.max": str(max_rise * i // 100),
                        "vmstat_oom_kill": str(oom if i == 100 else 0)})
            w.writerow(row)
    return d


def run_eval(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = ev.main(list(argv))
    return rc, out.getvalue()


class EvalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def test_pass_prints_env_lines_and_the_m_arithmetic(self) -> None:
        rc, out = run_eval(str(write_run(self.tmp / "r1")), "--ssh-idle", "0.4", "--ssh-loaded", "0.7")
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT PASS", out)
        self.assertIn("DEV_HOST_MEMORY_MARGIN=243M", out)  # 99 + 50 + 30 + 64
        self.assertIn('DEV_HOST_MEMORY_MIN="docker.service=375M"', out)  # 1.25 x 300M > the 210M table row; ssh stays on the table
        self.assertIn("DEV_INTERACTIVE_MEMORY_MIN=250M", out)
        self.assertRegex(out, r'(?m)^DEV_HOST_MEMORY_MEASURED="\d{4}-\d\d-\d\d .*r1"')

    def test_refault_fails_and_the_env_lines_are_not_to_be_applied(self) -> None:
        rc, out = run_eval(str(write_run(self.tmp / "r1", refault=5000)), "--ssh-idle", "1", "--ssh-loaded", "1")
        self.assertEqual(rc, 1)
        self.assertIn("docker.service=+4950", out)
        self.assertIn("# DEV_HOST_MEMORY_MARGIN=", out)
        self.assertIn("DEV_HOST_MEMORY_KEEP=1280M", out)
        self.assertIn("VERDICT FAIL", out)

    def test_oom_kill_fails(self) -> None:
        rc, out = run_eval(str(write_run(self.tmp / "r1", oom=1)), "--ssh-idle", "1", "--ssh-loaded", "1")
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"FAIL\s+no oom: .*vmstat=\+1")

    def test_pressure_and_ssh_latency_fail(self) -> None:
        self.assertEqual(run_eval(str(write_run(self.tmp / "p", psi=2.5)), "--ssh-idle", "1", "--ssh-loaded", "1")[0], 1)
        self.assertEqual(run_eval(str(write_run(self.tmp / "s")), "--ssh-idle", "1", "--ssh-loaded", "2.5")[0], 1)

    def test_no_max_rise_is_inconclusive_even_when_everything_else_passes(self) -> None:
        rc, out = run_eval(str(write_run(self.tmp / "r1", max_rise=0)), "--ssh-idle", "1", "--ssh-loaded", "1")
        self.assertEqual(rc, 4)
        self.assertIn("VERDICT INCONCLUSIVE", out)
        self.assertIn("# DEV_HOST_MEMORY_MARGIN=", out)

    def test_missing_ssh_timings_are_inconclusive_and_ssh_txt_supplies_them(self) -> None:
        d = write_run(self.tmp / "r1")
        self.assertEqual(run_eval(str(d))[0], 4)
        (d / "ssh.txt").write_text("0.5 0.9\n")
        self.assertEqual(run_eval(str(d))[0], 0)

    def test_two_runs_take_the_worst_verdict(self) -> None:
        good, bad = write_run(self.tmp / "a"), write_run(self.tmp / "b", oom=1)
        self.assertEqual(run_eval(str(good), str(bad), "--ssh-idle", "1", "--ssh-loaded", "1")[0], 1)

    def test_usage_error_is_2(self) -> None:
        self.assertEqual(ev.main([]), 2)
        self.assertEqual(ev.main([str(self.tmp / "nowhere")]), 2)


FAKE_DOCKER = '#!/bin/sh\necho "$*" >> "$FAKE_LOG"\ncase "$1" in run) echo fakeid0123 ;; esac\nexit 0\n'
FAKE_SYSTEMCTL = '#!/bin/sh\necho "systemctl $*" >> "$FAKE_LOG"\n[ -n "${FAKE_RELOAD_WRITE:-}" ] && printf %s "$FAKE_RELOAD_WRITE" > "$FAKE_MAX_FILE"\nexit 0\n'


class ShellKitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        fake_ctl = '#!/bin/sh\necho "cgprofilectl $*" >> "$FAKE_LOG"\ncase "$1" in start) echo "sess-$$" ;; esac\nexit 0\n'
        for name, body in (("docker", FAKE_DOCKER), ("systemctl", FAKE_SYSTEMCTL), ("cgprofilectl", fake_ctl)):
            (bindir / name).write_text(body)
            (bindir / name).chmod(0o755)
        self.log = self.tmp / "fake.log"
        self.cg, self.proc = self.tmp / "cg", self.tmp / "proc"
        for sl, cur in (("system.slice", 100), ("user.slice", 50), ("dev.slice", 1000), ("init.scope", 20)):
            (self.cg / sl).mkdir(parents=True)
            (self.cg / sl / "memory.current").write_text(f"{cur * MIB}\n")
        (self.cg / "dev.slice" / "memory.events").write_text("low 0\nhigh 0\nmax 7\noom 0\noom_kill 1\n")
        (self.cg / "dev.slice" / "memory.max").write_text("7190757376\n")
        (self.cg / "system.slice" / "memory.pressure").write_text("some avg10=0.50 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        (self.cg / "system.slice" / "docker.service").mkdir()
        (self.cg / "system.slice" / "docker.service" / "memory.current").write_text(f"{90 * MIB}\n")
        (self.cg / "system.slice" / "docker.service" / "memory.stat").write_text("active_anon 1000\nactive_file 2000\nworkingset_refault_anon 5\nworkingset_refault_file 6\n")
        self.proc.mkdir()
        (self.proc / "meminfo").write_text("MemTotal: 8388608 kB\nMemFree: 1048576 kB\n")
        (self.proc / "vmstat").write_text("nr_free_pages 1\noom_kill 3\n")
        (self.proc / "zoneinfo").write_text("Node 0, zone DMA\n  pages free 1\n        min 10\n        high 20\n    cpu: 0\n              high:  99\nNode 0, zone Normal\n        high 30\n")
        self.henv = self.tmp / "host-setup.env"
        self.henv.write_text("HOST_ROLE=dedicated-dev\n")
        self.env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_LOG=str(self.log), MDT_ACCEPT_CGROUP=str(self.cg), MDT_ACCEPT_PROC=str(self.proc),
                        MDT_ACCEPT_HOST_ENV=str(self.henv), MDT_ACCEPT_INTERVAL="0", MDT_ACCEPT_PAGESIZE="4096",
                        MDT_ACCEPT_UNITS="docker.service=system.slice/docker.service user.slice=user.slice")
        self.log.write_text("")

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def test_both_scripts_are_shellcheck_clean(self) -> None:
        for s in KIT.glob("*.sh"):
            self.assertEqual(subprocess.run(["bash", "-n", str(s)]).returncode, 0, s)
        if shutil.which("shellcheck") is None:
            self.assertFalse(os.environ.get("MDT_REQUIRE_SHELLS"), "shellcheck required but absent")
            self.skipTest("shellcheck not available")
        r = subprocess.run(["shellcheck", "-S", "warning", *map(str, KIT.glob("*.sh"))], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_other_role_is_refused_before_anything_starts(self) -> None:
        self.henv.write_text("HOST_ROLE=shared-prod\n")
        r = subprocess.run([str(KIT / "accept-run.sh"), "--evidence", str(self.tmp / "ev")], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)
        self.assertIn("not dedicated-dev", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_sampler_writes_one_row_per_tick_and_cleans_up_by_exact_name(self) -> None:
        env = dict(self.env, MDT_ACCEPT_MAX_TICKS="3")
        r = subprocess.run([str(KIT / "accept-run.sh"), "--evidence", str(self.tmp / "ev"), "--warmup", "0", "--window", "1800", "--probe-image", "img:1"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = list(csv.reader((self.tmp / "ev" / "samples.csv").open()))
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(len(x) == len(rows[0]) for x in rows), rows)
        first = dict(zip(rows[0], rows[1]))
        self.assertEqual(first["phase"], "window")
        self.assertEqual(int(first["U"]), (8192 - 1024 - 1170) * MIB)  # MemTotal - MemFree - sum of root children
        self.assertEqual(first["init_current"], str(20 * MIB))
        self.assertEqual(first["W"], str(50 * 4096))  # zone `high` lines only, never the per-cpu `high:` line
        self.assertEqual((first["docker.service.active"], first["docker.service.refault_file"]), ("3000", "6"))
        self.assertEqual((first["system.psi_some_avg10"], first["dev.max"], first["dev.oom_kill"], first["gates.max"], first["vmstat_oom_kill"]), ("0.50", "7", "1", "", "3"))
        calls = self.calls()
        runs = [c for c in calls if c.startswith("run ")]
        self.assertEqual(len(runs), 2)
        for name, parent in (("mdt-accept-probe-gates", "dev-gates.slice"), ("mdt-accept-probe-bg", "dev-background.slice")):
            self.assertTrue(any(f"--name {name} " in c and "--memory 4G --memory-swap 12G" in c and f"--cgroup-parent {parent}" in c and "img:1" in c for c in runs), runs)
            self.assertEqual(calls[-2:].count(f"rm -f {name}"), 1, calls)
        self.assertFalse([c for c in calls if "--filter" in c or c.startswith(("ps", "container ls"))], calls)
        starts = [c for c in calls if c.startswith("cgprofilectl start")]
        self.assertEqual([("cgroup:/system.slice/docker.service" in c, "cgroup:/user.slice" in c) for c in starts], [(True, False), (False, True)])
        self.assertEqual(len([c for c in calls if c.startswith("cgprofilectl stop sess-")]), 2)  # every session is stopped at exit
        man = (self.tmp / "ev" / "manifest.env").read_text()
        for key in ("host=", "start_utc=", "end_utc=", "kit_sha=", "memory.max:dev.slice=7190757376", "ticks=3"):  # cgprofile skip note: only without cgprofilectl
            self.assertIn(key, man)
        rc, out = run_eval(str(self.tmp / "ev"))  # the evaluator reads what the sampler writes
        self.assertIn("VERDICT", out)
        self.assertIn(rc, (1, 4))

    def test_term_removes_both_probes_by_exact_name(self) -> None:
        env = dict(self.env, MDT_ACCEPT_INTERVAL="600")
        samples = self.tmp / "ev" / "samples.csv"
        p = subprocess.Popen([str(KIT / "accept-run.sh"), "--evidence", str(self.tmp / "ev"), "--warmup", "0", "--window", "100000"],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not (samples.exists() and len(samples.read_text().splitlines()) >= 2):
            time.sleep(0.05)
        self.assertTrue(samples.exists() and len(samples.read_text().splitlines()) >= 2, "no sample row before the deadline")
        p.terminate()
        self.assertEqual(p.wait(timeout=60), 143)
        tail = self.calls()[-2:]
        self.assertEqual(sorted(tail), ["rm -f mdt-accept-probe-bg", "rm -f mdt-accept-probe-gates"])
        self.assertIn("end_utc=", (self.tmp / "ev" / "manifest.env").read_text())

    def _reload_check(self, reload_write: str) -> subprocess.CompletedProcess:
        f = self.cg / "system.slice" / "docker-fakeid0123.scope" / "memory.max"
        f.parent.mkdir()
        f.write_text("2147483648\n")
        env = dict(self.env, FAKE_RELOAD_WRITE=reload_write, FAKE_MAX_FILE=str(f))
        return subprocess.run([str(KIT / "daemon-reload-check.sh")], env=env, capture_output=True, text=True)

    def test_daemon_reload_check_passes_when_the_declared_cap_survives(self) -> None:
        r = self._reload_check("")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("systemctl daemon-reload", self.calls())
        self.assertEqual(self.calls()[-1], "rm -f mdt-accept-memcheck")

    def test_daemon_reload_check_fails_when_the_reload_rewrites_the_cap(self) -> None:
        r = self._reload_check("8589934592")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("after=8589934592", r.stdout)
        self.assertEqual(self.calls()[-1], "rm -f mdt-accept-memcheck")


if __name__ == "__main__":
    unittest.main()

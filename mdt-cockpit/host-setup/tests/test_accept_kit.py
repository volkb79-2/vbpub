"""P-plat-19g: the acceptance kit under tools/accept/. Hermetic: fixture CSVs, fake docker/systemctl on PATH, fake /proc and cgroup roots."""
from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
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

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "tools" / "accept"
MIB = 1024 ** 2
spec = importlib.util.spec_from_file_location("accept_eval_t", KIT / "accept-eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

UNITS = ["ssh.service", "docker.service", "containerd.service", "systemd-journald.service", "user.slice", "dev-interactive.slice"]
HEAD = ["utc", "elapsed", "phase", "U", "init_current", "W", "swap_used", "pswpin", "pswpout", "zswpout", "zswpwb", "dev.current", "dev.mem_max", "dev.swap_current"]
HEAD += [f"{u}.{c}" for u in UNITS for c in ("current", "active", "refault_anon", "refault_file")]
HEAD += ["system.psi_some_avg10", "user.psi_some_avg10"] + [f"{t}.{c}" for t in ("dev", "gates", "bg") for c in ("max_local", "oom", "oom_kill")] + ["vmstat_oom_kill"]


def write_run(d: Path, *, refault: int = 0, oom: int = 0, rise: bool = True, psi: float = 0.1, docker_hot: int = 200, inter_hot: int = 200,
              u_scale: int = 1, blank: tuple[str, ...] = (), probes: str = "true false", pswpout: bool = True, kernel_oom: int = 0, windows: bool = True) -> Path:
    """One warm-up row and 100 window rows: U = i MiB x u_scale (p99 = 99 MiB x u_scale), init 50 MiB, W 30 MiB."""
    d.mkdir(parents=True, exist_ok=True)
    with (d / "samples.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, HEAD)
        w.writeheader()
        for i in range(101):
            row = {k: "0" for k in HEAD}
            row.update(utc="00:00:00", elapsed=str(i * 10), phase=("warmup" if i == 0 else "window") if windows else "warmup", U=str(max(i, 1) * MIB * u_scale),
                       init_current=str(50 * MIB), W=str(30 * MIB), **{"dev.current": "100", "dev.mem_max": "1000"})
            row.update({"docker.service.active": str(docker_hot * MIB), "ssh.service.active": str(4 * MIB), "dev-interactive.slice.active": str(inter_hot * MIB),
                        "docker.service.refault_file": str(refault * i // 100), "system.psi_some_avg10": str(psi), "dev.max_local": str(i if rise else 0),
                        "pswpout": str(i * 10 if pswpout else ""), "vmstat_oom_kill": str(oom if i == 100 else 0)})
            row.update({c: "" for c in blank})
            w.writerow(row)
    (d / "manifest.env").write_text(f"mem_total={8 * 1024 * MIB}\nswap_total={16 * 1024 * MIB}\nprobe_gates_end={probes}\nprobe_bg_end=true false\nkernel_oom_lines={kernel_oom}\n")
    (d / "host-setup.env.snapshot").write_text("HOST_ROLE=dedicated-dev\n")
    return d


def run_eval(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = ev.main(list(argv))
    return rc, out.getvalue()


class EvalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        self.ssh = ("--ssh-idle", "0.4", "--ssh-loaded", "0.7")

    def ev(self, d: Path, *extra: str) -> tuple[int, str]:
        return run_eval(str(d), *(extra or self.ssh))

    def test_pass_prints_env_lines_and_the_m_arithmetic(self) -> None:
        rc, out = self.ev(write_run(self.tmp / "r1"))
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT PASS", out)
        self.assertIn("= 243.0M", out)  # M = 99 + 50 + 30 + 64
        self.assertIn("DEV_HOST_MEMORY_MARGIN=512M", out)  # never below the margin the run was made at: a lower K is untested
        self.assertIn('DEV_HOST_MEMORY_MIN="docker.service=250M"', out)  # 1.25 x 200M > the 210M table row; the others stay on the table
        self.assertIn("DEV_INTERACTIVE_MEMORY_MIN=512M", out)  # an idle cockpit never lowers the 512M default
        self.assertIn("K rose", out)
        self.assertRegex(out, r'(?m)^DEV_HOST_MEMORY_MEASURED="\d{4}-\d\d-\d\d .*r1"')

    def test_a_measured_margin_above_the_tested_one_is_printed(self) -> None:
        rc, out = self.ev(write_run(self.tmp / "r1", u_scale=10))
        self.assertEqual(rc, 0, out)
        self.assertIn("DEV_HOST_MEMORY_MARGIN=1134M", out)  # 990 + 50 + 30 + 64

    def test_interactive_floor_is_clamped_to_the_low(self) -> None:
        _, out = self.ev(write_run(self.tmp / "r1", inter_hot=3000))
        self.assertRegex(out, r"DEV_INTERACTIVE_MEMORY_MIN=2560M\s+# clamped")

    def test_values_install_sh_would_refuse_are_inconclusive_not_pass(self) -> None:
        rc, out = self.ev(write_run(self.tmp / "r1", docker_hot=300))  # 375M docker row pushes system.slice over 460M
        self.assertEqual(rc, 4, out)
        self.assertIn("would refuse", out)
        self.assertIn("# DEV_HOST_MEMORY_MIN=", out)

    def test_refault_fails_and_the_env_lines_are_not_to_be_applied(self) -> None:
        rc, out = self.ev(write_run(self.tmp / "r1", refault=5000))
        self.assertEqual(rc, 1)
        self.assertIn("docker.service=+4950", out)
        self.assertIn("# DEV_HOST_MEMORY_MARGIN=", out)
        self.assertIn("DEV_HOST_MEMORY_KEEP=1280M", out)

    def test_oom_kill_fails(self) -> None:
        rc, out = self.ev(write_run(self.tmp / "r1", oom=1))
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"FAIL\s+no oom: .*vmstat=\+1")

    def test_pressure_and_ssh_latency_fail(self) -> None:
        self.assertEqual(self.ev(write_run(self.tmp / "p", psi=2.5))[0], 1)
        self.assertEqual(self.ev(write_run(self.tmp / "s"), "--ssh-idle", "1", "--ssh-loaded", "2.5")[0], 1)

    def test_a_load_that_never_hit_the_ceiling_is_inconclusive(self) -> None:
        rc, out = self.ev(write_run(self.tmp / "r1", rise=False))
        self.assertEqual(rc, 4)
        self.assertRegex(out, r"INCONCLUSIVE\s+load was real: dev.slice local max events \+0")

    def test_a_dead_probe_or_no_disk_swap_is_inconclusive(self) -> None:
        self.assertEqual(self.ev(write_run(self.tmp / "a", probes="gone"))[0], 4)
        rc, out = self.ev(write_run(self.tmp / "b", pswpout=False))
        self.assertEqual((rc, "no pswpout data" in out), (4, True))

    def test_missing_columns_are_inconclusive_and_named_never_a_pass(self) -> None:
        for col, expect in (("system.psi_some_avg10", "system.psi_some_avg10"), ("gates.oom", "gates.oom"), ("bg.oom_kill", "bg.oom_kill"),
                            ("docker.service.refault_anon", "docker.service.refault_anon"), ("dev.max_local", "dev.max_local")):
            with self.subTest(col):
                rc, out = self.ev(write_run(self.tmp / col, blank=(col,)))
                self.assertEqual(rc, 4, out)
                self.assertIn(expect, out)
        rc, out = self.ev(write_run(self.tmp / "unit", blank=("containerd.service.active",)))  # a required unit absent from the host
        self.assertEqual((rc, "containerd.service" in out), (4, True))

    def test_missing_ssh_timings_are_inconclusive_and_ssh_txt_supplies_them(self) -> None:
        d = write_run(self.tmp / "r1")
        self.assertEqual(run_eval(str(d))[0], 4)
        (d / "ssh.txt").write_text("0.5 0.9\n")
        self.assertEqual(run_eval(str(d))[0], 0)

    def test_kernel_oom_lines_need_attribution(self) -> None:
        self.assertEqual(self.ev(write_run(self.tmp / "r1", kernel_oom=2))[0], 4)

    def test_no_window_rows_is_inconclusive_a_crash_or_usage_error_is_2_never_fail(self) -> None:
        self.assertEqual(self.ev(write_run(self.tmp / "w", windows=False))[0], 4)
        d = write_run(self.tmp / "bad")
        (d / "ssh.txt").write_text("fast\n")
        self.assertEqual(run_eval(str(d))[0], 2)  # malformed input crashes the evaluator: 2, which the README does not map to a pin
        (d / "ssh.txt").unlink()
        (d / "samples.csv").write_text("")
        self.assertEqual(run_eval(str(d), *self.ssh)[0], 4)
        self.assertEqual(ev.main([]), 2)
        self.assertEqual(run_eval(str(self.tmp / "nowhere"))[0], 2)

    def test_two_runs_take_the_worst_verdict(self) -> None:
        self.assertEqual(run_eval(str(write_run(self.tmp / "a")), str(write_run(self.tmp / "b", oom=1)), *self.ssh)[0], 1)


def _fake(name: str, body: str, d: Path) -> None:
    (d / name).write_text("#!/bin/sh\n" + body)
    (d / name).chmod(0o755)


FAKE_DOCKER = r'''printf '%s\n' "$*" | sed -n 1p >> "$FAKE_LOG"
case "$1" in
  info) echo "$FAKE_DOCKER_ROOT" ;;
  inspect) echo "${FAKE_INSPECT:-true false}" ;;
  run)
    printf '%s\n' "$*" | tail -n 1 >> "$FAKE_LOG.last"
    name=; parent=
    while [ $# -gt 0 ]; do case "$1" in --name) name=$2 ;; --cgroup-parent) parent=$2 ;; esac; shift; done
    [ "${FAKE_FAIL_RUN:-}" = "$name" ] && exit 1
    if [ -n "$parent" ]; then
      id="id-$name"; d="$MDT_ACCEPT_CGROUP/dev.slice/$parent/docker-$id.scope"; mkdir -p "$d"
      echo "${FAKE_MEMMAX:-4294967296}" > "$d/memory.max"; echo "${FAKE_SWAPMAX:-8589934592}" > "$d/memory.swap.max"
    else id=fakeid0123; fi
    echo "$id" ;;
esac
exit 0
'''
FAKE_SYSTEMCTL = r'''echo "systemctl $*" >> "$FAKE_LOG"
case "$1" in
  show) cat "$FAKE_SHOW/$5" 2>/dev/null ;;
  daemon-reload) [ -n "${FAKE_RELOAD_WRITE:-}" ] && printf %s "$FAKE_RELOAD_WRITE" > "$FAKE_MAX_FILE" ;;
esac
exit 0
'''


class ShellKitTests(unittest.TestCase):
    TIER_MAX = {"dev.slice": 7190757376, "dev-gates.slice": 4506402816, "dev-background.slice": 4506402816}

    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        bindir, show = self.tmp / "bin", self.tmp / "show"
        bindir.mkdir()
        show.mkdir()
        _fake("docker", FAKE_DOCKER, bindir)
        _fake("systemctl", FAKE_SYSTEMCTL, bindir)
        self.log = self.tmp / "fake.log"
        self.log.write_text("")
        self.cg, self.proc = self.tmp / "cg", self.tmp / "proc"
        for sl, cur in (("system.slice", 100), ("user.slice", 50), ("dev.slice", 1000), ("init.scope", 20)):
            (self.cg / sl).mkdir(parents=True)
            (self.cg / sl / "memory.current").write_text(f"{cur * MIB}\n")
        for tier in ("dev-gates.slice", "dev-background.slice"):
            (self.cg / "dev.slice" / tier).mkdir()
        for name, value in self.TIER_MAX.items():
            (self.cg / "dev.slice" / (name if name != "dev.slice" else "") / "memory.max").write_text(f"{value}\n")
            (show / name).write_text(f"{value}\n")
        dev = self.cg / "dev.slice"
        (dev / "memory.events").write_text("low 0\nhigh 0\nmax 999\noom 0\noom_kill 1\n")  # hierarchical: counts the probes' own container limits
        (dev / "memory.events.local").write_text("low 0\nhigh 0\nmax 7\noom 0\noom_kill 0\n")
        (self.cg / "system.slice" / "memory.pressure").write_text("some avg10=0.50 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        (self.cg / "system.slice" / "docker.service").mkdir()
        (self.cg / "system.slice" / "docker.service" / "memory.current").write_text(f"{90 * MIB}\n")
        (self.cg / "system.slice" / "docker.service" / "memory.stat").write_text("active_anon 1000\nactive_file 2000\nworkingset_refault_anon 5\nworkingset_refault_file 6\n")
        self.proc.mkdir()
        (self.proc / "meminfo").write_text("MemTotal: 8388608 kB\nMemFree: 1048576 kB\nSwapTotal: 16777216 kB\nSwapFree: 16000000 kB\n")
        (self.proc / "vmstat").write_text("nr_free_pages 1\npswpin 4\npswpout 8\nzswpout 9\nzswpwb 2\noom_kill 3\n")
        (self.proc / "zoneinfo").write_text("Node 0, zone DMA\n  pages free 1\n        min 10\n        high 20\n    cpu: 0\n              high:  99\nNode 0, zone Normal\n        high 30\n")
        self.henv = self.tmp / "host-setup.env"
        self.henv.write_text("HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_MARGIN=\nSECRET_TOKEN=x\n")
        self.env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_LOG=str(self.log), FAKE_SHOW=str(show), FAKE_DOCKER_ROOT=str(self.tmp),
                        MDT_ACCEPT_CGROUP=str(self.cg), MDT_ACCEPT_PROC=str(self.proc), MDT_ACCEPT_HOST_ENV=str(self.henv), MDT_ACCEPT_INTERVAL="0",
                        MDT_ACCEPT_PAGESIZE="4096", MDT_ACCEPT_LOCK=str(self.tmp / "lock"), MDT_ACCEPT_MIN_DISK="1", MDT_ACCEPT_DEFERRED=str(self.tmp / "deferred"),
                        MDT_ACCEPT_UNITS="docker.service=system.slice/docker.service user.slice=user.slice")

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def run_kit(self, *args: str, ticks: str = "3", **extra: str) -> subprocess.CompletedProcess:
        env = dict(self.env, MDT_ACCEPT_MAX_TICKS=ticks, **extra)
        return subprocess.run([str(KIT / "accept-run.sh"), "--evidence", str(self.tmp / "ev"), "--warmup", "0", "--window", "1800", *args],
                              env=env, capture_output=True, text=True, timeout=120)

    def assert_probes_removed(self) -> None:
        self.assertEqual(sorted(self.calls()[-2:]), ["rm -f mdt-accept-probe-bg", "rm -f mdt-accept-probe-gates"])

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
        r = self.run_kit()
        self.assertEqual(r.returncode, 3)
        self.assertIn("not dedicated-dev", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_a_trailing_option_without_a_value_is_a_usage_error_not_a_hang(self) -> None:
        r = subprocess.run([str(KIT / "accept-run.sh"), "--evidence", str(self.tmp / "ev"), "--warmup"], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)

    def test_sampler_writes_one_row_per_tick_and_cleans_up_by_exact_name(self) -> None:
        r = self.run_kit("--probe-image", "img:1")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = list(csv.reader((self.tmp / "ev" / "samples.csv").open()))
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(len(x) == len(rows[0]) for x in rows), rows)
        first = dict(zip(rows[0], rows[1]))
        self.assertEqual(first["phase"], "window")
        self.assertEqual(int(first["U"]), (8192 - 1024 - 1170) * MIB)  # MemTotal - MemFree - sum of root children
        self.assertEqual(first["init_current"], str(20 * MIB))
        self.assertEqual(first["W"], str(50 * 4096))  # zone `high` lines only, never the per-cpu `high:` line
        self.assertEqual((first["swap_used"], first["pswpout"], first["zswpout"], first["zswpwb"]), (str((16777216 - 16000000) * 1024), "8", "9", "2"))
        self.assertEqual((first["docker.service.active"], first["docker.service.refault_file"]), ("3000", "6"))
        # the rise rule reads memory.events.local (7), never the hierarchical memory.events (999); oom counts stay hierarchical
        self.assertEqual((first["dev.max_local"], first["dev.oom_kill"], first["gates.max_local"], first["system.psi_some_avg10"], first["vmstat_oom_kill"]), ("7", "1", "", "0.50", "3"))
        self.assertNotIn("999", ",".join(rows[1]))
        calls = self.calls()
        runs = [c for c in calls if c.startswith("run ")]
        self.assertEqual(len(runs), 2)
        for name, parent in (("mdt-accept-probe-gates", "dev-gates.slice"), ("mdt-accept-probe-bg", "dev-background.slice")):
            self.assertTrue(any(f"--name {name} " in c and " --rm " in c and "--memory 4G --memory-swap 12G" in c and f"--cgroup-parent {parent}" in c and "img:1" in c for c in runs), runs)
        self.assertIn("inspect -f {{.State.Running}} {{.State.OOMKilled}} mdt-accept-probe-gates", calls)
        self.assert_probes_removed()
        self.assertFalse([c for c in calls if "--filter" in c or c.startswith(("ps", "container ls"))], calls)
        man = (self.tmp / "ev" / "manifest.env").read_text()
        for key in ("host=", "start_utc=", "end_utc=", "kit_sha256=", "mem_total=8589934592", "memory.max:dev.slice=7190757376", "probe_gates_end=true false", "ticks=3"):
            self.assertIn(key, man)
        snap = (self.tmp / "ev" / "host-setup.env.snapshot").read_text()
        self.assertEqual(snap, "HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_MARGIN=\n")  # only the limit keys, never the rest of the env file
        rc, out = run_eval(str(self.tmp / "ev"))  # the evaluator reads what the sampler writes: nothing here can FAIL, but nothing is proven either
        self.assertEqual(rc, 4, out)

    def test_the_probe_gets_a_deadline_of_warmup_plus_window_plus_a_margin(self) -> None:
        self.run_kit()
        tails = (self.tmp / "fake.log.last").read_text().splitlines()
        self.assertEqual(len(tails), 2)
        self.assertTrue(all(t.endswith(" 2100 3800 4096 /tmp/probe.dat") for t in tails), tails)  # 0 + 1800 + 300 s, 3800 MiB anon, 4096 MiB file

    def test_a_failed_probe_start_removes_both_names(self) -> None:
        r = self.run_kit(FAKE_FAIL_RUN="mdt-accept-probe-bg")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(len([c for c in self.calls() if c.startswith("run ")]), 1)
        self.assert_probes_removed()

    def test_wrong_placement_or_caps_stop_the_run_and_clean_up(self) -> None:
        for extra in ({"FAKE_MEMMAX": "8589934592"}, {"FAKE_SWAPMAX": "max"}):
            with self.subTest(extra):
                self.log.write_text("")
                r = self.run_kit(**extra)
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertIn("not under", r.stderr)
                self.assert_probes_removed()
                self.assertEqual(len([c for c in self.calls() if c.startswith("run ")]), 1)

    def test_a_cgroup_parent_that_is_not_an_existing_dev_tier_is_refused(self) -> None:
        for parent in ("system.slice", "dev-nowhere.slice"):
            with self.subTest(parent):
                self.log.write_text("")
                r = self.run_kit(CGROUP_PARENT_DEV_GATES=parent)
                self.assertEqual(r.returncode, 3)
                self.assertEqual([c for c in self.calls() if c.startswith("run ")], [])

    def test_too_little_disk_is_refused(self) -> None:
        r = self.run_kit(MDT_ACCEPT_MIN_DISK=str(1 << 62))
        self.assertEqual((r.returncode, [c for c in self.calls() if c.startswith("run ")]), (3, []))

    def _start_long(self) -> subprocess.Popen:
        samples = self.tmp / "ev" / "samples.csv"
        p = subprocess.Popen([str(KIT / "accept-run.sh"), "--evidence", str(self.tmp / "ev"), "--warmup", "0", "--window", "100000"],
                             env=dict(self.env, MDT_ACCEPT_INTERVAL="600"), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 120
        while p.poll() is None and time.monotonic() < deadline and not (samples.exists() and len(samples.read_text().splitlines()) >= 2):
            time.sleep(0.05)
        if p.poll() is not None:
            self.fail(f"the run ended early rc={p.returncode}: {p.stderr.read()}")
        self.assertTrue(samples.exists() and len(samples.read_text().splitlines()) >= 2, "no sample row before the deadline")
        return p

    def test_int_term_and_hup_remove_both_probes_by_exact_name(self) -> None:
        for sig, rc in ((signal.SIGTERM, 143), (signal.SIGINT, 130), (signal.SIGHUP, 129)):
            with self.subTest(sig.name):
                self.log.write_text("")
                shutil.rmtree(self.tmp / "ev", ignore_errors=True)
                p = self._start_long()
                p.send_signal(sig)
                self.assertEqual(p.wait(timeout=60), rc)
                self.assert_probes_removed()
                self.assertIn("end_utc=", (self.tmp / "ev" / "manifest.env").read_text())

    def test_a_second_run_is_refused_while_the_first_holds_the_lock(self) -> None:
        p = self._start_long()
        self.addCleanup(p.wait)
        before = self.calls()
        r = subprocess.run([str(KIT / "accept-run.sh"), "--evidence", str(self.tmp / "ev2")], env=self.env, capture_output=True, text=True, timeout=60)
        p.terminate()
        self.assertEqual(r.returncode, 3)
        self.assertIn("lock", r.stderr)
        self.assertEqual(self.calls()[: len(before)], before)  # the refused run touched no container, so it removed no probe of the first

    def test_the_probe_body_is_incompressible_and_ends_by_itself(self) -> None:
        code = re.search(r"<<'PY'\n(.*?)\nPY\n", (KIT / "accept-run.sh").read_text(), re.S).group(1)
        made: list[bytearray] = []

        class Rec(bytearray):
            def __init__(self, *a: object) -> None:
                super().__init__(*a)
                made.append(self)

        old = sys.argv
        sys.argv = ["probe", "0.2", "4", "2", str(self.tmp / "probe.dat")]
        try:
            exec(compile(code, "probe", "exec"), {"__name__": "probe", "bytearray": Rec})  # returns only because of the deadline
        finally:
            sys.argv = old
        buf = bytes(made[0])
        self.assertEqual(len(buf), 4 * MIB)
        self.assertGreater(len(zlib.compress(buf)) / len(buf), 0.95)  # zswap could not shrink it

    def _reload_check(self, reload_write: str = "") -> subprocess.CompletedProcess:
        f = self.cg / "system.slice" / "docker-fakeid0123.scope" / "memory.max"
        f.parent.mkdir()
        f.write_text("2147483648\n")
        env = dict(self.env, FAKE_RELOAD_WRITE=reload_write, FAKE_MAX_FILE=str(f))
        return subprocess.run([str(KIT / "daemon-reload-check.sh")], env=env, capture_output=True, text=True)

    def test_daemon_reload_check_passes_when_the_declared_cap_survives(self) -> None:
        r = self._reload_check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("systemctl daemon-reload", self.calls())
        self.assertEqual(self.calls()[-1], "rm -f mdt-accept-memcheck")

    def test_daemon_reload_check_fails_when_the_reload_rewrites_the_cap(self) -> None:
        r = self._reload_check("8589934592")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("after=8589934592", r.stdout)
        self.assertEqual(self.calls()[-1], "rm -f mdt-accept-memcheck")

    def test_daemon_reload_check_refuses_with_a_deferred_lowering_or_a_systemd_kernel_mismatch(self) -> None:
        (self.tmp / "deferred").write_text("dev.slice\n")
        r = self._reload_check()
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.calls(), [])
        (self.tmp / "deferred").unlink()
        shutil.rmtree(self.cg / "system.slice" / "docker-fakeid0123.scope")
        (self.tmp / "show" / "dev-gates.slice").write_text("4000000000\n")  # systemd says lower than the kernel holds: a reload would lower it
        r = self._reload_check()
        self.assertEqual(r.returncode, 3)
        self.assertIn("dev-gates.slice MemoryMax", r.stderr)
        self.assertNotIn("systemctl daemon-reload", self.calls())
        self.assertEqual([c for c in self.calls() if c.startswith("run ")], [])


if __name__ == "__main__":
    unittest.main()

"""Tests for scripts/iomon_bpf.py and scripts/iomon-bpf/run.sh.  No docker, no bpftrace."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("iomon_bpf", SCRIPTS / "iomon_bpf.py")
bpf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bpf)
_ISPEC = importlib.util.spec_from_file_location("iomon_t", SCRIPTS / "iomon.py")
iomon = importlib.util.module_from_spec(_ISPEC)
_ISPEC.loader.exec_module(iomon)
RUN_SH = SCRIPTS / "iomon-bpf" / "run.sh"
ID1, ID2 = "a" * 64, "b" * 64


class ParseTests(unittest.TestCase):
    def test_parse_psi_ok(self):
        t = "some avg10=1.50 avg60=0.5 avg300=0.1 total=1234\nfull avg10=0.00 avg60=0 avg300=0 total=5\n"
        self.assertEqual(bpf.parse_psi(t), {"some": {"a10": 1.5, "tot": 1234}, "full": {"a10": 0.0, "tot": 5}})

    def test_parse_psi_malformed_line_is_skipped(self):
        t = "some avg10=oops total=1\ngarbage\nfull avg10=2.0 avg60=0 avg300=0 total=7\nsome avg10 total\n"
        self.assertEqual(bpf.parse_psi(t), {"full": {"a10": 2.0, "tot": 7}})
        self.assertEqual(bpf.parse_psi(None), {})

    def test_parse_io_stat(self):
        t = ("254:0 rbytes=10 wbytes=20 rios=1 wios=2 cost.usage=3 cost.vrate=50.25 dbytes=0\n"
             "bogus line\n8:0 rbytes=x\n")
        d = bpf.parse_io_stat(t)
        self.assertEqual(d["254:0"]["rbytes"], 10)
        self.assertEqual(d["254:0"]["cost.vrate"], 50.25)
        self.assertEqual(d["8:0"]["rbytes"], "x")
        self.assertNotIn("bogus", d)
        self.assertEqual(bpf.parse_io_stat(""), {})


class ScopeTests(unittest.TestCase):
    def test_resolve_scope_finds_and_validates(self):
        with tempfile.TemporaryDirectory() as td:
            scope = Path(td) / "system.slice" / f"docker-{ID1}.scope"
            scope.mkdir(parents=True)
            with mock.patch.object(bpf, "HOSTCG", td):
                p, ino = bpf.resolve_scope(ID1)
                self.assertEqual(p, str(scope))
                self.assertEqual(ino, os.stat(scope).st_ino)
                self.assertEqual(bpf.resolve_scope(ID2), (None, 0))
                for bad in ("", "*", "a" * 12, "a" * 63, "a" * 65, "A" * 64, ID1[:-1] + "*",
                            "../" + ID1, ID1 + "/x", None):
                    self.assertEqual(bpf.resolve_scope(bad), (None, 0), bad)

    def test_preload_check(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(bpf, "read") as rd:
            files = {f"{td}/cgroup.procs": "11\n12\n13\nnotapid\n",
                     "/proc/11/maps": "7f00 r-xp /usr/lib/libeatmydata.so\n7f01 r-xp /lib/libc.so\n",
                     "/proc/11/comm": "srv\n",
                     "/proc/12/maps": "7f01 r-xp /lib/libc.so\n7f02 r-xp /x/ksm-optin.so\n",
                     "/proc/12/comm": "other\n",
                     "/proc/13/comm": "gone\n"}
            rd.side_effect = lambda path: files.get(path)
            r = bpf.preload_check(td)
        self.assertEqual([x["pid"] for x in r], [11, 12, 13])
        self.assertEqual(r[0], {"pid": 11, "comm": "srv", "readable": True, "eatmydata": True, "ksm-optin": False})
        self.assertEqual(r[1]["ksm-optin"], True)
        self.assertEqual(r[1]["eatmydata"], False)
        self.assertEqual(r[2], {"pid": 13, "comm": "gone", "readable": False})
        self.assertNotIn("libc", json.dumps(r))  # maps content never leaves the function
        self.assertEqual(bpf.preload_check(None), [])


class WriterConcurrencyTests(unittest.TestCase):
    def test_threads_with_rotation_lose_nothing_and_never_raise(self):
        with tempfile.TemporaryDirectory() as td:
            w = iomon.Writer(td, cap_bytes=10**9, segment_bytes=2000, prefix="bpf-")
            errors, n_threads, per = [], 4, 400

            def work(i):
                try:
                    for j in range(per):
                        w.write({"k": "x", "i": i, "j": j, "pad": "p" * 20})
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            ts = [threading.Thread(target=work, args=(i,)) for i in range(n_threads)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            w.close()
            self.assertEqual(errors, [])
            lines = []
            files = sorted(x for x in os.listdir(td) if x.startswith("bpf-"))
            self.assertGreater(len(files), 5)  # rotation really happened
            for f in files:
                lines += (Path(td) / f).read_text().splitlines()
            recs = [json.loads(x) for x in lines]  # every line is intact JSON
            self.assertEqual(len(recs), n_threads * per)
            self.assertEqual(len({(r["i"], r["j"]) for r in recs}), n_threads * per)

    def test_cap_enforced_and_current_segment_kept(self):
        with tempfile.TemporaryDirectory() as td:
            w = iomon.Writer(td, cap_bytes=3000, segment_bytes=1000, prefix="bpf-")
            for j in range(300):
                w.write({"j": j, "pad": "p" * 30})
            w.close()
            total = sum(os.path.getsize(Path(td) / f) for f in os.listdir(td))
            self.assertLessEqual(total, 3000 + 1000)
            last = sorted(os.listdir(td))[-1]
            self.assertIn('"j":299', (Path(td) / last).read_text())


class BpfReaderTests(unittest.TestCase):
    def test_reader_survives_a_failing_write(self):
        calls, logs = [], []

        class W:
            def write(self, o):
                calls.append(o)
                if len(calls) == 1:
                    raise ValueError("I/O operation on closed file")

        b = bpf.Bpf.__new__(bpf.Bpf)
        b.name, b.w, b.log, b.helper_errors = "blk", W(), logs.append, 0
        b.p = mock.Mock(stdout=iter(['{"type":"x"}\n', '{"type":"y"}\n', "not json\n",
                                     '{"type":"helper_error"}\n', "[1]\n"]))
        b._out()
        self.assertEqual(len(calls), 4)  # first raised, the other records were still processed
        self.assertEqual(b.helper_errors, 1)
        self.assertTrue(any("dropped a record" in x for x in logs))

    def test_guard_logs_a_dead_reader(self):
        logs = []
        b = bpf.Bpf.__new__(bpf.Bpf)
        b.name, b.log = "blk", logs.append

        def boom():
            raise RuntimeError("x")
        b._guard(boom)
        self.assertTrue(any("died" in x for x in logs))

    def test_stop_all_uses_one_shared_deadline(self):
        class P:
            def __init__(self):
                self.p = mock.Mock()
                self.p.poll.return_value = None
                self.signalled = False
                self.timeouts = []

            def signal_stop(self):
                self.signalled = True

            def stop(self, t):
                self.timeouts.append(t)
        ps = [P(), P(), P()]
        bpf.stop_all(ps, budget=9.0)
        self.assertTrue(all(p.signalled for p in ps))
        self.assertLessEqual(max(p.timeouts[0] for p in ps), 9.0)
        self.assertLess(bpf.STOP_BUDGET + 5 + 10, 60)  # fits docker stop -t 60


class LoggerTests(unittest.TestCase):
    def test_log_is_capped(self):
        with tempfile.TemporaryDirectory() as td:
            log = bpf.make_logger(td, max_bytes=1000, backups=1)
            for i in range(2000):
                log("x" * 100 + str(i))
            size = sum(os.path.getsize(Path(td) / f) for f in os.listdir(td))
            self.assertLessEqual(size, 2 * 1000 + 300)
            self.assertLessEqual(len(os.listdir(td)), 2)


class StopCmdTests(unittest.TestCase):
    def test_stop_warns_when_bpf_link_missing(self):
        with tempfile.TemporaryDirectory() as td:
            err, out = io.StringIO(), io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                iomon.main(["stop", "--out", td, "--bpf-name", "nm1"])
            self.assertTrue((Path(td) / "STOP").exists())
            self.assertIn("WARNING", err.getvalue())
            self.assertIn("docker stop -t 60 nm1", err.getvalue())

    def test_stop_writes_marker_through_link(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as bd:
            os.symlink(bd, os.path.join(td, "bpf"))
            err, out = io.StringIO(), io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                iomon.main(["stop", "--out", td])
            self.assertTrue((Path(bd) / "STOP-BPF").exists())
            self.assertEqual(err.getvalue(), "")


STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
if [ "$1" = inspect ]; then printf '%s\\n' "$STUB_ID"; exit 0; fi
exit 0
"""


class RunShTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.bin = Path(self.td.name) / "bin"
        self.bin.mkdir()
        (self.bin / "docker").write_text(STUB)
        (self.bin / "docker").chmod(0o755)
        self.log = Path(self.td.name) / "docker.log"
        self.out = Path(self.td.name) / "out"
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "STUB_LOG": str(self.log),
                    "STUB_ID": ID1}

    def tearDown(self):
        self.td.cleanup()

    def run_sh(self, *args):
        return subprocess.run(["bash", str(RUN_SH), *args], env=self.env, capture_output=True, text=True,
                              timeout=60)

    def docker_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def assert_rejected(self, *args, code=2):
        r = self.run_sh(*args)
        self.assertEqual(r.returncode, code, r.stderr)
        self.assertFalse([c for c in self.docker_calls() if c.startswith("run")], "docker run was reached")
        return r

    def test_hours_validation(self):
        for bad in ("0", "-1", "abc", "", "1;id", "$(id)", "8'); import os #", "1e3", "999", "0.0", ".5", "5."):
            with self.subTest(hours=bad):
                self.assert_rejected("--hours", bad, "--out", str(self.out), "ctr")

    def test_other_argument_validation(self):
        self.assert_rejected("--out", str(self.out))                                  # no container
        self.assert_rejected("--out", str(self.out), "a", "b", "c")                   # too many
        self.assert_rejected("--out", str(self.out), "--bogus", "ctr")
        self.assert_rejected("--out", str(self.out), "--name", "bad name;x", "ctr")
        self.assert_rejected("--out", str(self.out), "--slice", "x/../y", "ctr")
        self.assert_rejected("--out", str(self.out), "--", "-rf")
        self.assert_rejected("--out", str(self.out) + ",x", "ctr")
        self.assert_rejected("--out", str(self.out) + ":x", "ctr")
        self.assert_rejected("--hours")

    def test_bad_inspect_id_rejected(self):
        self.env["STUB_ID"] = "short"
        self.assert_rejected("--out", str(self.out), "ctr", code=1)

    def test_happy_path_builds_safe_command(self):
        r = self.run_sh("--hours", "0.5", "--out", str(self.out), "--link", str(Path(self.td.name) / "lnk"),
                        "--slice", "x.slice", "ctr1", "ctr2")
        self.assertEqual(r.returncode, 0, r.stderr)
        for _ in range(100):
            if any(c.startswith("run") for c in self.docker_calls()):
                break
            time.sleep(0.1)
        calls = self.docker_calls()
        inspects = [c for c in calls if c.startswith("inspect")]
        self.assertEqual(len(inspects), 2)
        self.assertTrue(all(" -- " in c for c in inspects), inspects)
        run = next(c for c in calls if c.startswith("run"))
        self.assertIn("--mount type=bind,src=", run)
        self.assertNotIn(" -v " + str(self.out), run)
        self.assertNotIn("SYS_PTRACE", run)
        self.assertNotIn("apparmor=unconfined", run)
        self.assertIn("--cap-add SYS_ADMIN", run)
        self.assertIn(f"--container {ID1}", run)
        self.assertIn("--slice x.slice", run)
        self.assertIn("--max-hours 0.5", run)
        self.assertTrue(os.path.islink(Path(self.td.name) / "lnk"))

    def test_optin_privileges_and_default_out_is_not_predictable(self):
        r = self.run_sh("--ptrace", "--apparmor-unconfined", "ctr")
        self.assertEqual(r.returncode, 0, r.stderr)
        outs = [ln[4:] for ln in r.stdout.splitlines() if ln.startswith("OUT=")]
        self.assertEqual(len(outs), 1)
        self.assertNotEqual(outs[0], "/tmp/iomon-bpf-20261027")
        self.assertTrue(os.path.isdir(outs[0]))
        for _ in range(100):
            if any(c.startswith("run") for c in self.docker_calls()):
                break
            time.sleep(0.1)
        run = next(c for c in self.docker_calls() if c.startswith("run"))
        self.assertIn("SYS_PTRACE", run)
        self.assertIn("apparmor=unconfined", run)
        time.sleep(0.3)
        subprocess.run(["rm", "-rf", outs[0]], check=True)


if __name__ == "__main__":
    unittest.main()

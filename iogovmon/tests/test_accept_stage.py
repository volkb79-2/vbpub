"""Unit tests for the safety arithmetic of the acceptance stage runner `tools/accept/stage.py` (no disk access outside temp dirs, no host
state): fake statvfs, 490 MiB/s writer, 1 s check interval. The v3.1 tests (hung subprocess, cron edit, cleanup isolation) and the
IGM-22 minors are in this file and in test_accept_tools.py."""
import os, types, unittest
from unittest import mock

import _acceptload

stage = _acceptload.load("stage.py")
GiB, MiB = stage.GiB, stage.MiB

TOTAL, FRSIZE = 255 * GiB, 4096


def fake_statvfs(used_bytes):
    blocks = TOTAL // FRSIZE
    free = blocks - used_bytes // FRSIZE
    return types.SimpleNamespace(f_blocks=blocks, f_bfree=free, f_bavail=free, f_frsize=FRSIZE, f_files=0, f_ffree=0)


def run_fill(used0, target_pct, limit_pct, rate, interval, margin, cap=True, chunk=32 * GiB):
    """Simulate stage.fill() against a fake fs: fio writes at `rate`; a checker kills it at the first interval boundary where used > limit.
    Returns (final_used, peak_over_limit_bytes)."""
    used, limit_bytes, peak_over = used0, int(limit_pct / 100 * TOTAL), 0
    while True:
        with mock.patch("os.statvfs", lambda p: fake_statvfs(used)):
            _p, u, a, _i = stage.fs_use("/x")
        to_target = stage.bytes_to_pct(u, a, target_pct)
        to_limit = stage.bytes_to_pct(u, a, limit_pct) - margin if cap else 1 << 60
        size = stage.fill_size(to_target, to_limit, chunk)
        if size == 0:
            break
        written, t = 0, 0.0
        while written < size:
            step = min(rate * interval, size - written)
            written += step
            used += step
            t += interval
            peak_over = max(peak_over, used - limit_bytes)
            if used > limit_bytes:  # the 1 s checker sees the violation and kills fio
                return used, peak_over
    return used, peak_over


class T(unittest.TestCase):
    def test_capped_never_exceeds_limit(self):
        used, over = run_fill(int(0.8 * TOTAL), 94, 95, 490 * MiB, 1.0, 3 * GiB)
        self.assertLessEqual(over, 0)
        self.assertLessEqual(used, int(0.94 * TOTAL) + MiB)

    def test_target_above_limit_stops_at_limit_minus_margin(self):
        used, over = run_fill(int(0.9 * TOTAL), 99, 95, 490 * MiB, 1.0, 3 * GiB)
        self.assertLessEqual(used, int(0.95 * TOTAL) - 3 * GiB + MiB)  # stays at least the margin below the hard limit
        self.assertEqual(over, 0)

    def test_runaway_overshoot_below_margin(self):
        # the size cap is OFF (worst case: another writer / a miscalculated size): the 1 s checker alone must keep the overshoot < margin
        used, over = run_fill(int(0.8 * TOTAL), 99, 95, 490 * MiB, 1.0, 3 * GiB, cap=False)
        self.assertGreater(over, 0)
        self.assertLess(over, 3 * GiB)
        self.assertLessEqual(over, 490 * MiB + 1)  # one check interval of writing

    def test_fill_size_aligned_and_bounded(self):
        self.assertEqual(stage.fill_size(10 * GiB, 5 * GiB + 123, 32 * GiB), 5 * GiB)
        self.assertEqual(stage.fill_size(10 * MiB, 50 * GiB, 32 * GiB), 0)
        self.assertEqual(stage.fill_size(50 * GiB, -1, 32 * GiB), 0)

    def test_t0(self):
        W, lead = 3600, 600
        self.assertEqual(stage.utc_t("2026-11-18T10:00:00"), stage.utc_t("2026-11-18T10:00:00Z"))  # naive = UTC
        self.assertEqual(stage.utc_t("2026-11-18T12:00:00+02:00"), stage.utc_t("2026-11-18T10:00:00Z"))
        t0 = stage.utc_t("2026-11-18T10:00:00Z")
        self.assertIsNone(stage.validate_t0(t0, t0 + 60, W, lead, 1800, 7200))
        self.assertIn("older", stage.validate_t0(t0, t0 + 3600, W, lead, 1800, 7200))
        self.assertIn("future", stage.validate_t0(t0, t0 - 3 * 3600, W, lead, 1800, 7200))
        self.assertIn("past", stage.validate_t0(t0 + 3000, t0 + 3700, W, lead, 99999, 7200))  # W0 = 11:00 passed? t0+3000+600 -> 11:00 < now


# ---- v3.1: hung subprocess, cron edit (CRLF, file changed meanwhile), cleanup isolation ------------------------------------------------------
import hashlib, tempfile, time as _time
from pathlib import Path


def make_stage(tmp):
    tmp = Path(tmp)
    cfg = stage.load_cfg(str(_acceptload.ACCEPT_DIR / "stage.example.toml"), True)
    cfg["paths"].update(workdir=str(tmp), log=str(tmp / "log"), snapdir=str(tmp / "snap"), phases=str(tmp / "phases.json"),
                        summary=str(tmp / "sum"), stop_file=str(tmp / "STOP"), cron_fstrim_file=str(tmp / "cron-kernel"))
    args = types.SimpleNamespace(rehearsal=True, dry_annotate=True, rehearsal_raise_in=None, rehearsal_stop_in=None, rehearsal_fake_stdev=None)
    return stage.Stage(cfg, args)


class V31(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.S = make_stage(self.tmp.name)
        self.cron = Path(self.tmp.name) / "cron-kernel"

    def tearDown(self):
        self.tmp.cleanup()

    def test_sh_timeout_and_missing_command_never_raise(self):
        t0 = _time.time()
        self.assertEqual(self.S.sh(["sleep", "300"], timeout=1).returncode, 124)
        self.assertLess(_time.time() - t0, 120)  # returned long before the 300 s sleep ended; the bound is generous so host load cannot fail it
        self.assertEqual(self.S.sh(["/nonexistent/command"]).returncode, 125)

    def test_cron_crlf_roundtrip(self):
        orig = b"# c\r\n33 10 * * * root /sbin/fstrim -a\r\nother 1\r\n"
        self.cron.write_bytes(orig)
        os.chmod(self.cron, 0o640)
        self.S.cron_fstrim_off()
        disabled = self.cron.read_bytes()
        self.assertIn(b"#IOGOV-A2-DISABLED# 33 10", disabled)
        self.assertEqual(disabled.count(b"\r\n"), 3)  # CRLF kept
        self.assertEqual((Path(self.tmp.name) / "cron-fstrim-disabled").exists(), True)
        self.assertTrue(self.S.cron_fstrim_restore())
        self.assertEqual(hashlib.sha256(self.cron.read_bytes()).hexdigest(), hashlib.sha256(orig).hexdigest())
        self.assertEqual(oct(self.cron.stat().st_mode & 0o7777), oct(0o640))
        self.assertFalse((Path(self.tmp.name) / "cron-fstrim-disabled").exists())
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir() if "iogov-tmp" in p.name], [])

    def test_cron_file_changed_meanwhile_keeps_foreign_change(self):
        orig = b"33 10 * * * root /sbin/fstrim -a\n"
        self.cron.write_bytes(orig)
        self.S.cron_fstrim_off()
        self.cron.write_bytes(self.cron.read_bytes() + b"5 5 * * * root /bin/true\n")  # somebody edits the file during the run
        self.assertTrue(self.S.cron_fstrim_restore())
        self.assertEqual(self.cron.read_bytes(), orig + b"5 5 * * * root /bin/true\n")  # our marks gone, the foreign change kept
        self.assertFalse((Path(self.tmp.name) / "cron-fstrim-disabled").exists())

    def test_cleanup_continues_after_a_failing_or_raising_step(self):
        calls = []
        S = self.S
        S.kill_children = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        S.rm_created = lambda why: calls.append("rm")
        S.fstrim_restore = lambda: calls.append("fstrim") or False
        S.discard_restore = lambda: calls.append("discard") or True
        S.cron_fstrim_restore = lambda: calls.append("cron") or True
        self.assertFalse(S.cleanup("exception"))
        self.assertEqual(calls, ["rm", "fstrim", "discard", "cron"])  # nothing after the failures was skipped


if __name__ == "__main__":
    unittest.main(verbosity=2)

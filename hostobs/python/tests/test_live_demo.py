#!/usr/bin/env python3
"""The live-demo verdict, plus an opt-in run over the static cgroup fixture."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
SCRIPT = HOSTOBS / "tools" / "live-demo.py"
FIXTURE_CGROUP = HOSTOBS / "crates" / "kernel" / "tests" / "fixtures" / "cgroupfs"


def load_demo():
    spec = importlib.util.spec_from_file_location("hostobs_live_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEMO = load_demo()


def transcript(
    ticks: str, *, operator: str = "", status: int = -15, probe_stderr: str = ""
) -> str:
    kind = "SIGTERM" if status == -15 else "exit"
    return "\n".join(
        [
            DEMO.WATCH_OBSERVE,
            "hello\tlevel=observe\ttick_ms=1000\tmin_interval_ms=1000\tmax_rows=1024"
            "\tmax_block_bytes=1048576\tinterval_ms=1000",
            ticks,
            "watch exit: 0",
            DEMO.WATCH_OPERATOR,
            operator,
            "watch exit: 0",
            "=== probe stderr ===",
            probe_stderr,
            "=== probe exit ===",
            f"probe exit: {status} ({kind})",
            "",
        ]
    )


def cpu(seq: int, value: int) -> str:
    return (
        f"tick\tseq={seq}\tentity=/\tmetric=cpu.usage_usec"
        f"\tvalue=U64({value})\tquality=ok"
    )


class Verdict(unittest.TestCase):
    def test_pass_needs_ordered_ticks_cpu_change_known_qualities_and_sigterm(self):
        passed, reason = DEMO.assess(transcript(f"{cpu(1, 100)}\n{cpu(2, 200)}"), 2)
        self.assertTrue(passed)
        self.assertEqual(reason, "PASS")

    def test_tick_count_and_order_are_both_checked(self):
        passed, reason = DEMO.assess(transcript(cpu(1, 100)), 2)
        self.assertFalse(passed)
        self.assertIn("expected 2 ticks, got 1", reason)
        passed, reason = DEMO.assess(transcript(f"{cpu(2, 100)}\n{cpu(1, 200)}"), 2)
        self.assertFalse(passed)
        self.assertIn("not increasing", reason)

    def test_static_cpu_is_no_cpu_change(self):
        passed, reason = DEMO.assess(transcript(f"{cpu(1, 100)}\n{cpu(2, 100)}"), 2)
        self.assertFalse(passed)
        self.assertIn("no cpu change", reason)

    def test_an_unknown_quality_code_fails(self):
        bad = cpu(2, 200).replace("quality=ok", "quality=future")
        passed, reason = DEMO.assess(transcript(f"{cpu(1, 100)}\n{bad}"), 2)
        self.assertFalse(passed)
        self.assertIn("unknown quality code", reason)

    def test_absent_is_never_rendered_as_zero(self):
        bad = cpu(2, 200).replace("value=U64(200)\tquality=ok", "value=U64(0)\tquality=absent")
        passed, reason = DEMO.assess(transcript(f"{cpu(1, 100)}\n{bad}"), 2)
        self.assertFalse(passed)
        self.assertIn("absent quality shows value 0", reason)

    def test_probe_must_exit_by_sigterm(self):
        for status in (0, 1, 2):
            with self.subTest(status=status):
                passed, reason = DEMO.assess(
                    transcript(f"{cpu(1, 100)}\n{cpu(2, 200)}", status=status), 2
                )
                self.assertFalse(passed)
                self.assertIn("did not exit by SIGTERM", reason)

    def test_a_panic_fails_even_if_the_probe_was_signalled(self):
        passed, reason = DEMO.assess(
            transcript(
                f"{cpu(1, 100)}\n{cpu(2, 200)}",
                probe_stderr="thread 'main' panicked at src/main.rs:10:5",
            ),
            2,
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "probe panicked")


@unittest.skipUnless(
    os.environ.get("HOSTOBS_PROBE_BIN") and os.environ.get("HOSTOBS_WATCH_BIN"),
    "live integration needs HOSTOBS_PROBE_BIN and HOSTOBS_WATCH_BIN; the conformance "
    "lane builds only the probe until a lane change for the watch example",
)
class FixtureLiveRun(unittest.TestCase):
    def test_static_fixture_fails_for_no_cpu_change(self):
        with tempfile.TemporaryDirectory(prefix="hostobs-live-test-") as temporary:
            out = Path(temporary) / "transcript.txt"
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(SCRIPT),
                    "--probe",
                    os.environ["HOSTOBS_PROBE_BIN"],
                    "--watch",
                    os.environ["HOSTOBS_WATCH_BIN"],
                    "--ticks",
                    "2",
                    "--cgroup-root",
                    str(FIXTURE_CGROUP),
                    "--out",
                    str(out),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=45,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("FAIL no cpu change", result.stdout)
            self.assertEqual(out.read_text(encoding="utf-8"), result.stdout)


if __name__ == "__main__":
    unittest.main()

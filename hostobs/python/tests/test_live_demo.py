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
    ticks: str,
    *,
    operator: str = (
        "tick\tseq=3\tentity=/\tmetric=cpu.usage_usec\tvalue=U64(300)\tquality=ok"
    ),
    status: int = -15,
    probe_stderr: str = "",
    observe_status: int = 0,
    operator_status: int = 0,
    closed_observe: str | None = None,
    closed_operator: str | None = None,
) -> str:
    kind = "SIGTERM" if status == -15 else "exit"
    if closed_observe is None:
        closed_observe = closed(distinct_ticks(ticks))
    if closed_operator is None:
        # `watch` is always run at operator with `--ticks 1`, whatever a fixture prints.
        closed_operator = closed(1)
    return "\n".join(
        [
            DEMO.WATCH_OBSERVE,
            "hello\tlevel=observe\ttick_ms=1000\tmin_interval_ms=1000\tmax_rows=1024"
            "\tmax_block_bytes=1048576\tinterval_ms=1000",
            ticks,
            closed_observe,
            f"watch exit: {observe_status}",
            DEMO.WATCH_OPERATOR,
            operator,
            closed_operator,
            f"watch exit: {operator_status}",
            "=== probe stderr ===",
            probe_stderr,
            "=== probe exit ===",
            f"probe exit: {status} ({kind})",
            "",
        ]
    )


def closed(blocks: int | str, reason: str = "unsubscribed") -> str:
    return f"closed\tblocks={blocks}\treason={reason}"


def distinct_ticks(text: str) -> int:
    seqs = {
        line.split("\t")[1]
        for line in text.splitlines()
        if line.startswith("tick\t") and len(line.split("\t")) > 1
    }
    return len(seqs)


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

    def test_the_closed_line_must_be_the_client_contract_form_and_block_count(self):
        """(a) The `closed` line must be the client's exact form, count and a live reason.

        `watch` prints exactly `closed<TAB>blocks=<n><TAB>reason=<reason>`
        (`crates/client/examples/watch.rs:307-317`), and the only live reasons its client accepts
        are `unsubscribed` and `client-eof` (`crates/client/src/client.rs:190-196`). The count is
        an integer lower bound because `unsubscribe` can count blocks received before its reply.
        """
        ticks = f"{cpu(1, 100)}\n{cpu(2, 200)}"
        operator = cpu(3, 300)
        cases = (
            (closed(1), closed(1), "observe closed line says blocks=1, fewer than"),
            (closed("2.5"), closed(1), "closed line has no block count"),
            (closed(2, "complete"), closed(1), "not a reason the client accepts"),
            (closed(2, "bogus"), closed(1), "not a reason the client accepts"),
            (closed(2) + "\textra=1", closed(1), "not the client's contract form"),
            (closed(2).replace("blocks", "block"), closed(1), "not the client's contract form"),
            ("", closed(1), "expected one closed line, found 0"),
            (closed(2) + "\n" + closed(2), closed(1), "expected one closed line, found 2"),
            (closed(2), closed(1, "complete"), "not a reason the client accepts"),
            (closed(2), closed(0), "operator closed line says blocks=0, fewer than"),
        )
        for observe_closed, operator_closed, message in cases:
            with self.subTest(closed=observe_closed + " / " + operator_closed):
                passed, reason = DEMO.assess(
                    transcript(
                        ticks,
                        operator=operator,
                        closed_observe=observe_closed,
                        closed_operator=operator_closed,
                    ),
                    2,
                )
                self.assertFalse(passed)
                self.assertIn(message, reason)
        for reason_name in ("unsubscribed", "client-eof"):
            with self.subTest(reason=reason_name):
                passed, reason = DEMO.assess(
                    transcript(
                        ticks,
                        operator=operator,
                        closed_observe=closed(2, reason_name),
                        closed_operator=closed(1, reason_name),
                    ),
                    2,
                )
                self.assertTrue(passed, reason)

        passed, reason = DEMO.assess(
            transcript(
                ticks,
                operator=operator,
                closed_observe=closed(3),
                closed_operator=closed(2),
            ),
            2,
        )
        self.assertTrue(passed, reason)

    def test_every_tick_must_have_the_first_tick_row_count(self):
        passed, reason = DEMO.assess(
            transcript(
                f"{cpu(1, 100)}\n{cpu(2, 200)}\n"
                f"{cpu(2, 300).replace('entity=/', 'entity=/child')}"
            ),
            2,
        )
        self.assertFalse(passed)
        self.assertIn("rows where the first tick has 1", reason)
        self.assertIn("cgroup created or removed", reason)

    def test_tick_sequence_numbers_must_be_consecutive(self):
        passed, reason = DEMO.assess(transcript(f"{cpu(1, 100)}\n{cpu(3, 200)}"), 2)
        self.assertFalse(passed)
        self.assertIn("not consecutive", reason)

    def test_operator_section_requires_a_tick_line(self):
        passed, reason = DEMO.assess(
            transcript(f"{cpu(1, 100)}\n{cpu(2, 200)}", operator=""), 2
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "expected 1 operator tick block, got 0")

    def test_operator_section_has_exactly_one_tick_sequence(self):
        operator = f"{cpu(3, 300)}\n{cpu(4, 400)}"
        passed, reason = DEMO.assess(
            transcript(f"{cpu(1, 100)}\n{cpu(2, 200)}", operator=operator), 2
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "expected 1 operator tick block, got 2")

    def test_every_tick_line_requires_a_sequence(self):
        observe = cpu(1, 100).replace("seq=1\t", "") + "\n" + cpu(2, 200)
        passed, reason = DEMO.assess(transcript(observe), 2)
        self.assertFalse(passed)
        self.assertEqual(reason, "invalid tick sequence None")

        operator = cpu(3, 300).replace("seq=3\t", "")
        passed, reason = DEMO.assess(
            transcript(f"{cpu(1, 100)}\n{cpu(2, 200)}", operator=operator), 2
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "invalid tick sequence None")

    def test_operator_tick_cannot_repeat_an_entity_metric_pair(self):
        operator = f"{cpu(3, 300)}\n{cpu(3, 301)}"
        passed, reason = DEMO.assess(
            transcript(f"{cpu(1, 100)}\n{cpu(2, 200)}", operator=operator), 2
        )
        self.assertFalse(passed)
        self.assertIn("operator tick seq=3 repeats entity/metric pair", reason)

    def test_observe_tick_cannot_repeat_an_entity_metric_pair(self):
        ticks = "\n".join(
            (cpu(1, 100), cpu(1, 101), cpu(2, 200), cpu(2, 201))
        )
        passed, reason = DEMO.assess(transcript(ticks), 2)
        self.assertFalse(passed)
        self.assertIn("observe tick seq=1 repeats entity/metric pair", reason)

    def test_every_observe_tick_must_be_a_full_client_row(self):
        ticks = "\n".join(("tick\tseq=1\trows=0\tcolumns=0", cpu(2, 200)))
        passed, reason = DEMO.assess(transcript(ticks), 2)
        self.assertFalse(passed)
        self.assertEqual(reason, "observe tick line is not a client row")

    def test_the_operator_stream_is_held_to_the_observe_quality_rules(self):
        """(d) The operator stream carries the observe stream's quality rules.

        Three operator-only injections, none of which touches the observe section:
        an unknown quality code, a numeric value under a non-`ok` code, and the compact
        empty-block form `watch` prints when a block has no rows
        (`crates/client/examples/watch.rs:232-237`).

        The common quality pass rejects the first two. The full-row check rejects the third:
        it carries no `metric`, so the value/quality rule skips it, and a level that measured
        nothing at all (`rows=0`) must not satisfy the operator stream.
        """
        ticks = f"{cpu(1, 100)}\n{cpu(2, 200)}"
        unknown = cpu(3, 300).replace("quality=ok", "quality=future")
        numeric = cpu(3, 300).replace(
            "value=U64(300)\tquality=ok", "value=U64(0)\tquality=absent"
        )
        for operator, message in (
            (unknown, "unknown quality code 'future'"),
            (numeric, "absent quality shows a value"),
            ("tick\tseq=3\trows=0\tcolumns=0", "operator tick line is not a client row"),
        ):
            with self.subTest(operator=operator):
                passed, reason = DEMO.assess(transcript(ticks, operator=operator), 2)
                self.assertFalse(passed)
                self.assertEqual(reason, message)
        # A compact empty-block line in the operator stream is not a zero-row measurement, and
        # a full client row there is accepted.
        passed, reason = DEMO.assess(transcript(ticks, operator=cpu(3, 300)), 2)
        self.assertTrue(passed, reason)

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

    def test_every_non_ok_quality_must_show_not_measured(self):
        for quality in sorted(DEMO.QUALITY_CODES - {"ok"}):
            with self.subTest(quality=quality):
                bad = cpu(2, 200).replace(
                    "value=U64(200)\tquality=ok", f"value=U64(0)\tquality={quality}"
                )
                passed, reason = DEMO.assess(transcript(f"{cpu(1, 100)}\n{bad}"), 2)
                self.assertFalse(passed)
                self.assertIn(f"{quality} quality shows a value", reason)

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

    def test_each_watch_nonzero_exit_and_a_missing_quality_fail(self):
        for kwargs, reason in (
            ({"observe_status": 1}, "watch observe did not exit 0"),
            ({"operator_status": 1}, "watch operator did not exit 0"),
        ):
            with self.subTest(reason=reason):
                passed, found = DEMO.assess(
                    transcript(f"{cpu(1, 100)}\n{cpu(2, 200)}", **kwargs), 2
                )
                self.assertFalse(passed)
                self.assertEqual(found, reason)
        bad = cpu(2, 200).replace("\tquality=ok", "")
        passed, reason = DEMO.assess(transcript(f"{cpu(1, 100)}\n{bad}"), 2)
        self.assertFalse(passed)
        self.assertEqual(reason, "row is missing its value or quality")


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

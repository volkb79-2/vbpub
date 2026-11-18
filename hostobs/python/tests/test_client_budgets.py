#!/usr/bin/env python3
"""The deterministic gated measurements of WIRE.md 7.8 for `hostobs_client.wire.decode_block`.

    python3 -I -m unittest discover -s python/tests -p 'test_client_budgets.py' -v

Gated: (a) `sys.settrace` line events during `decode_block` are equal at 100 and 1,000 rows
(no per-value Python loop), (b) the `tracemalloc` peak grows by at most 4 KiB from 100 to 1,000
rows and, on CPython 3.14.10 only, the 1,000-row peak is at most 16 KiB. Every measured decode
alternates two distinct rows bodies so the byte-identical skip never fires. CPU time is not
gated (`bench_decode.py` prints it).
"""

from __future__ import annotations

import sys
import tracemalloc
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOSTOBS / "python"))
sys.path.insert(0, str(HOSTOBS / "python" / "tests"))

import sp4_frames  # noqa: E402
from hostobs_client import wire  # noqa: E402
from hostobs_client.wire import Dictionary, decode_block  # noqa: E402

PINNED = (3, 14, 10)
GROWTH_BOUND = 4096
ABSOLUTE_BOUND = 16384


def steady(n: int):
    """The held dictionary, validated once, and the two distinct bare frames of one stream."""
    held = Dictionary.decode(__import__("hostobs_wire_ref").encode_dictionary(sp4_frames.dictionary(n)))
    return held, [sp4_frames.frame(n, v) for v in (0, 1)]


def line_events(n: int) -> int:
    held, frames = steady(n)
    buffers = [bytearray(f) for f in frames]
    decode_block(buffers[0], held)  # first validation: the stream is now in steady state
    count = 0

    def tracer(_frame, event, _arg):
        nonlocal count
        if event == "line":
            count += 1
        return tracer

    # The tracer is installed by settrace only for frames that start after it: the call itself
    # is the first traced frame, and every nested Python frame is traced too.
    outer = sys.gettrace()
    sys.settrace(tracer)
    try:
        decode_block(buffers[1], held)
    finally:
        sys.settrace(outer)
    return count


def peak(n: int) -> int:
    held, frames = steady(n)
    buffers = [bytearray(f) for f in frames]
    decode_block(buffers[0], held)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        block = decode_block(buffers[1], held)
        value = tracemalloc.get_traced_memory()[1] - base
        del block
    finally:
        tracemalloc.stop()
    return value


class Budgets(unittest.TestCase):
    def test_the_frames_have_the_sp4_sizes(self):
        self.assertEqual(len(sp4_frames.frame(100)), 8272)
        self.assertEqual(len(sp4_frames.frame(1000)), 80240)

    def test_line_events_do_not_grow_with_the_row_count(self):
        small, large = line_events(100), line_events(1000)
        print(f"\nsettrace line events: 100 rows {small}, 1000 rows {large}", file=sys.stderr)
        self.assertEqual(small, large)
        self.assertGreater(small, 0)

    def test_the_two_bodies_alternate_so_the_skip_never_fires(self):
        held, frames = steady(100)
        decode_block(bytearray(frames[0]), held)
        retained = held._rows_ok
        decode_block(bytearray(frames[1]), held)
        self.assertNotEqual(retained, held._rows_ok)

    def test_a_per_value_loop_would_be_caught(self):
        # The gate must be able to fail: run the REAL decoder with its entity view replaced by a
        # sequence whose iteration is a Python generator (a per-value loop in the validation).
        class PythonIterated:
            def __init__(self, view):
                self.view = view

            def __iter__(self):
                for value in self.view:
                    yield value

        original = wire._view
        wire._view = lambda mv, fmt: PythonIterated(original(mv, fmt))
        try:
            small, large = line_events(100), line_events(1000)
        finally:
            wire._view = original
        self.assertGreater(large, small + 800)

    def test_the_peak_grows_by_at_most_4_kib_from_100_to_1000_rows(self):
        small, large = peak(100), peak(1000)
        print(f"\ntracemalloc peak: 100 rows {small} B, 1000 rows {large} B, growth {large - small} B", file=sys.stderr)
        self.assertLessEqual(large - small, GROWTH_BOUND)

    @unittest.skipUnless(
        sys.version_info[:3] == PINNED,
        f"the absolute bound is measured on CPython {'.'.join(map(str, PINNED))} only (WIRE.md 7.8)",
    )
    def test_the_absolute_peak_at_1000_rows_is_at_most_16_kib(self):
        self.assertLessEqual(peak(1000), ABSOLUTE_BOUND)


if __name__ == "__main__":
    unittest.main()

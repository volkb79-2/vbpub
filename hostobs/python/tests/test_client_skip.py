#!/usr/bin/env python3
"""Soundness of the retained-rows skip (WIRE.md 7.4) and other stateful decoder rules.

    python3 -I -m unittest discover -s python/tests -p 'test_client_skip.py' -v

Each test is a stateful sequence: a valid block is validated against a held dictionary, then a
block that must be refused follows. A decoder that skips row validation whenever a view is
retained (instead of only for an unchanged rows body in another buffer) fails these.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOSTOBS / "python"))
sys.path.insert(0, str(HOSTOBS / "python" / "tests"))

import frame_tools as ft  # noqa: E402
from hostobs_client import wire  # noqa: E402
from hostobs_client.stream import StreamDicts  # noqa: E402
from hostobs_client.wire import Dictionary, WireError, decode_block  # noqa: E402

ENTITIES = 4
OK_ROWS = [0, 1, 2, 3]


def held_dictionary() -> Dictionary:
    """The dictionary of ``small_frame(ENTITIES, ...)``, as a stream holds it after its first block."""
    return decode_block(bytearray(ft.small_frame(ENTITIES, OK_ROWS))).dictionary


def bare(ents: list[int]) -> bytes:
    return ft.small_frame(ENTITIES, ents, with_dictionary=False)


class CountMax:
    """Counts calls of the row-reference check (`max(ents)`) while installed in `wire`."""

    def __enter__(self):
        self.calls = 0

        def counting(*args, **kwargs):
            self.calls += 1
            return max(*args, **kwargs)

        wire.max = counting
        return self

    def __exit__(self, *exc):
        del wire.max


class SkipSoundness(unittest.TestCase):
    def setUp(self):
        self.held = held_dictionary()
        decode_block(bytearray(bare(OK_ROWS)), self.held)  # validated: the rows are retained
        self.assertIsNotNone(self.held._rows_ok)

    def test_a_dangling_reference_after_a_valid_block_is_refused(self):
        with self.assertRaises(WireError) as caught:
            decode_block(ft.set_ent(bare(OK_ROWS), 0, 99), self.held)
        self.assertEqual((caught.exception.kind, caught.exception.detail["index"]), ("dangling-dictionary-ref", 99))
        self.assertIsNone(self.held._rows_ok, "an error releases the retained rows")

    def test_a_reference_to_a_metric_after_a_valid_block_is_refused(self):
        with self.assertRaises(WireError) as caught:
            decode_block(ft.set_ent(bare(OK_ROWS), 2, ENTITIES), self.held)
        self.assertEqual(
            (caught.exception.kind, caught.exception.detail["index"]), ("row-reference-not-entity", ENTITIES)
        )

    def test_the_refusal_does_not_poison_the_next_valid_block(self):
        with self.assertRaises(WireError):
            decode_block(ft.set_ent(bare(OK_ROWS), 0, 99), self.held)
        decode_block(bytearray(bare(OK_ROWS)), self.held)
        self.assertIsNotNone(self.held._rows_ok)

    def test_an_unchanged_rows_body_in_another_buffer_skips_the_row_checks(self):
        with CountMax() as counter:
            decode_block(bytearray(bare(OK_ROWS)), self.held)
        self.assertEqual(counter.calls, 0)

    def test_a_changed_rows_body_is_validated(self):
        with CountMax() as counter:
            decode_block(bytearray(bare([1, 0, 2, 3])), self.held)
        self.assertEqual(counter.calls, 1)

    def test_a_buffer_overwritten_in_place_is_validated_not_skipped(self):
        buf = bytearray(bare(OK_ROWS))
        decode_block(buf, self.held)
        at = ft.rows_offset(bytes(buf))
        buf[at : at + 4] = (9999).to_bytes(4, "little")
        with self.assertRaises(WireError) as caught:
            decode_block(buf, self.held)
        self.assertEqual((caught.exception.kind, caught.exception.detail["index"]), ("dangling-dictionary-ref", 9999))
        self.assertIsNone(self.held._rows_ok, "no false validated state survives")

    def test_the_same_object_behind_a_memoryview_is_validated_too(self):
        buf = bytearray(bare(OK_ROWS))
        decode_block(buf, self.held)
        at = ft.rows_offset(bytes(buf))
        buf[at : at + 4] = (9999).to_bytes(4, "little")
        with self.assertRaises(WireError):
            decode_block(memoryview(buf), self.held)

    def test_a_dictionary_with_fewer_entities_in_a_later_generation_refuses_the_same_rows(self):
        stream = StreamDicts()
        first = decode_block(bytearray(ft.small_frame(ENTITIES, OK_ROWS)), stream.held(5))
        stream.accept(5, first)
        decode_block(bytearray(bare(OK_ROWS)), stream.held(5))
        smaller = ft.small_frame(2, OK_ROWS, generation=2)
        with self.assertRaises(WireError) as caught:
            decode_block(bytearray(smaller), stream.held(5))
        self.assertIn(caught.exception.kind, ("row-reference-not-entity", "dangling-dictionary-ref"))
        self.assertIsNone(stream.held(5)._rows_ok)

    def test_a_dictionary_with_the_same_text_but_a_new_generation_is_a_new_dictionary(self):
        stream = StreamDicts()
        stream.accept(5, decode_block(bytearray(ft.small_frame(ENTITIES, OK_ROWS)), stream.held(5)))
        newer = decode_block(bytearray(ft.small_frame(ENTITIES, OK_ROWS, generation=2)), stream.held(5))
        self.assertIsNot(newer.dictionary, stream.held(5))
        self.assertEqual(newer.dictionary.generation, 2)


class StreamDictsDropRetainedRows(unittest.TestCase):
    def accepted(self):
        stream = StreamDicts()
        first = decode_block(bytearray(ft.small_frame(ENTITIES, OK_ROWS)), stream.held(5))
        stream.accept(5, first)
        held = stream.held(5)
        block = decode_block(bytearray(bare(OK_ROWS)), held)
        stream.accept(5, block)
        self.assertIsNotNone(held._rows_ok)
        return stream, held

    def test_close_drops_them(self):
        stream, held = self.accepted()
        stream.close(5)
        self.assertIsNone(held._rows_ok)
        self.assertIsNone(stream.held(5))

    def test_reset_drops_them(self):
        stream, held = self.accepted()
        stream.reset()
        self.assertIsNone(held._rows_ok)

    def test_a_hello_drops_them(self):
        stream, held = self.accepted()
        stream.on_hello("b", 1)
        self.assertIsNone(held._rows_ok)

    def test_an_accept_refusal_drops_them(self):
        stream, held = self.accepted()
        stale = decode_block(bytearray(bare(OK_ROWS)), held)
        stale.header = stale.header._replace(dict_gen=7)
        with self.assertRaises(WireError):
            stream.accept(5, stale)
        self.assertIsNone(held._rows_ok)


class RetentionBoundary(unittest.TestCase):
    MIB = 1024 * 1024

    def padded(self, total: int) -> bytearray:
        corr, entries = ft.split(bare(OK_ROWS))
        base = len(ft.assemble(corr, entries + [(9, 1, 0, b"")]))
        entries.append((9, 1, 0, bytes(total - base)))
        frame = ft.assemble(corr, entries)
        self.assertEqual(len(frame), total)
        return bytearray(frame)

    def test_one_mib_is_retained_and_one_mib_plus_eight_is_not(self):
        for total, retained in ((self.MIB - 8, True), (self.MIB, True), (self.MIB + 8, False), (2 * self.MIB, False)):
            held = held_dictionary()
            block = decode_block(self.padded(total), held)
            self.assertEqual(held._rows_ok is not None, retained, total)
            self.assertEqual(block.skipped, [9])

    def test_a_frame_above_the_limit_leaves_its_successor_fully_validated(self):
        held = held_dictionary()
        decode_block(self.padded(self.MIB + 8), held)
        with CountMax() as counter:
            decode_block(bytearray(bare(OK_ROWS)), held)
        self.assertEqual(counter.calls, 1)


class SectionOrder(unittest.TestCase):
    def test_a_column_ahead_of_the_rows_is_a_section_order_refusal(self):
        corr, entries = ft.split(bare(OK_ROWS))
        kinds = [e[0] for e in entries]
        self.assertEqual(kinds, [1, 2, 3])
        swapped = [entries[0], entries[2], entries[1]]
        with self.assertRaises(WireError) as caught:
            decode_block(bytearray(ft.assemble(corr, swapped)))
        self.assertEqual((caught.exception.kind, caught.exception.detail), ("section-order", {"index": 2, "kind_code": 2}))

    def test_a_column_ahead_of_a_time_section_is_refused_too(self):
        corr, entries = ft.split(bare(OK_ROWS))
        time = (4, 1, 0, bytes(16 * 4))
        with self.assertRaises(WireError) as caught:
            decode_block(bytearray(ft.assemble(corr, [entries[0], entries[2], time])))
        self.assertEqual(caught.exception.kind, "section-order")


if __name__ == "__main__":
    unittest.main()

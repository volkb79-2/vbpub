#!/usr/bin/env python3
"""Tests of `hostobs_client.stream`: the reader and client fixtures (WIRE.md 11.4) replayed
against the real `FrameReader` and `StreamDicts`, plus the fill/advance API and the 5.2 rows.

    python3 -I -m unittest discover -s python/tests -p 'test_client_stream.py' -v
"""

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
STREAM = HOSTOBS / "fixtures" / "stream"
sys.path.insert(0, str(HOSTOBS / "python"))

from hostobs_client.stream import FrameReader, Session, StreamDicts  # noqa: E402
from hostobs_client.wire import FRAME_TYPE_NAMES, WireError, decode_block  # noqa: E402

MS = 1_000_000


def frame(code: int, corr: int, total: int, flags: int = 0) -> bytes:
    return struct.pack("<IBBHI", total, code, flags, 0, corr) + bytes(total - 12)


def replay_reader(spec: dict) -> list:
    reader = FrameReader(spec["max_frame"], spec["completion_ms"] * MS)
    outputs = []
    for step in spec["steps"]:
        now = step["t_ms"] * MS
        if "feed_hex" in step:
            frames, error = reader.feed(bytes.fromhex(step["feed_hex"]), now)
            out = [
                {"frame": {"type": FRAME_TYPE_NAMES[f.type], "corr": f.corr, "len": f.length}} for f in frames
            ]
            out += [{"error": error.kind}] if error else []
        elif step.get("poll"):
            error = reader.poll(now)
            out = [{"error": error.kind}] if error else []
        else:
            error = reader.eof()
            out = [{"error": error.kind}] if error else [{"closed": "clean"}]
        outputs.append(out)
    return outputs


def replay_client(spec: dict) -> list:
    dicts = StreamDicts()
    outputs = []
    for step in spec["steps"]:
        if "hello" in step:
            hello = step["hello"]
            outputs.append([{"session": dicts.on_hello(hello["boot_id"], hello["epoch"])}])
            continue
        try:
            block = decode_block(bytearray(bytes.fromhex(step["feed_hex"])), dicts.held(5))
            dicts.accept(5, block)
            outputs.append([{"ok": block.header.dict_gen}])
        except WireError as error:
            dicts.close(5)
            outputs.append([{"error": error.kind}])
    return outputs


class Fixtures(unittest.TestCase):
    def test_the_twelve_reader_cases(self):
        paths = sorted(STREAM.glob("reader-*.json"))
        self.assertEqual(len(paths), 12)
        for path in paths:
            spec = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(replay_reader(spec), spec["expect"], path.name)

    def test_the_six_client_cases(self):
        paths = sorted(STREAM.glob("client-*.json"))
        self.assertEqual(len(paths), 6)
        for path in paths:
            spec = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(replay_client(spec), spec["expect"], path.name)


class ReaderRows(unittest.TestCase):
    def reader(self, max_frame=1 << 20, completion_ms=1000):
        return FrameReader(max_frame, completion_ms * MS)

    def test_header_only_frame_is_emitted_and_returns_to_header_zero(self):
        reader = self.reader()
        frames, error = reader.feed(frame(1, 7, 12), 0)
        self.assertIsNone(error)
        self.assertEqual([(f.type, f.corr, f.length) for f in frames], [(1, 7, 12)])
        self.assertEqual(reader.buffered_len, 0)
        self.assertEqual(reader.buffered_capacity, 12)

    def test_the_frame_buffer_is_one_bytearray_of_len_allocated_after_the_header_validates(self):
        reader = self.reader()
        data = frame(5, 3, 4096, flags=1)
        reader.feed(data[:11], 0)
        self.assertEqual(reader.buffered_capacity, 12)
        reader.feed(data[11:13], 0)
        self.assertEqual(reader.buffered_capacity, 4096)
        frames, _ = reader.feed(data[13:], 0)
        self.assertEqual(bytes(frames[0].data), data)
        self.assertIsInstance(frames[0].data, bytearray)

    def test_an_oversized_len_never_allocates_beyond_the_header_buffer(self):
        reader = self.reader(max_frame=1000)
        frames, error = reader.feed(frame(1, 1, 12)[:0] + struct.pack("<IBBHI", 1001, 1, 0, 0, 1), 0)
        self.assertEqual((frames, error.kind, error.detail), ([], "oversized-frame", {"len": 1001, "max": 1000}))
        self.assertEqual(reader.buffered_capacity, 12)
        self.assertEqual(reader.fill().nbytes, 0)

    def test_fill_and_advance_receive_in_place(self):
        reader = self.reader()
        data = frame(2, 9, 100)
        view = reader.fill()
        self.assertEqual(view.nbytes, 12)
        view[:] = data[:12]
        self.assertEqual(reader.advance(12, 5), ([], None))
        view = reader.fill()
        self.assertEqual(view.nbytes, 88)
        view[:] = data[12:]
        frames, error = reader.advance(88, 6)
        self.assertEqual((error, bytes(frames[0].data)), (None, data))
        with self.assertRaises(ValueError):
            reader.advance(99, 7)

    def test_surplus_bytes_start_the_next_frame_with_started_now(self):
        reader = self.reader(completion_ms=100)
        two = frame(1, 1, 20) + frame(1, 2, 20)[:5]
        frames, error = reader.feed(two, 50 * MS)
        self.assertEqual((len(frames), error), (1, None))
        self.assertIsNone(reader.poll(150 * MS))
        self.assertEqual(reader.poll(150 * MS + 1).kind, "deadline")

    def test_an_empty_feed_is_not_an_event(self):
        reader = self.reader()
        self.assertEqual(reader.feed(b"", 5), ([], None))
        reader.feed(b"\x14", 10 * MS)
        self.assertIsNone(reader.poll(10 * MS + 1000 * MS))
        self.assertEqual(reader.poll(10 * MS + 1000 * MS + 1).kind, "deadline")

    def test_a_frame_completed_before_an_error_comes_back_with_it(self):
        reader = self.reader()
        frames, error = reader.feed(frame(1, 1, 12) + struct.pack("<IBBHI", 20, 9, 0, 0, 0), 0)
        self.assertEqual((len(frames), error.kind), (1, "unknown-frame-type"))
        self.assertEqual(reader.feed(b"x", 1), ([], error))
        self.assertIs(reader.poll(0), error)
        self.assertIs(reader.eof(), error)

    def test_header_defects_each_poison_with_their_own_kind(self):
        for raw, kind in (
            (struct.pack("<IBBHI", 20, 7, 0, 0, 0), "unknown-frame-type"),
            (struct.pack("<IBBHI", 20, 1, 2, 0, 0), "unknown-frame-flags"),
            (struct.pack("<IBBHI", 20, 2, 1, 0, 0), "unknown-frame-flags"),
            (struct.pack("<IBBHI", 20, 1, 0, 1, 0), "non-zero-reserved"),
            (struct.pack("<IBBHI", 11, 1, 0, 0, 0), "truncated"),
        ):
            frames, error = self.reader().feed(raw, 0)
            self.assertEqual((frames, error.kind), ([], kind))

    def test_a_len_above_the_codec_limit_names_the_readers_limit(self):
        reader = FrameReader(1 << 30, 1)
        _frames, error = reader.feed(struct.pack("<IBBHI", (16 << 20) + 1, 1, 0, 0, 0), 0)
        self.assertEqual(error.detail, {"len": (16 << 20) + 1, "max": 1 << 30})

    def test_eof_rows(self):
        clean = self.reader()
        self.assertIsNone(clean.eof())
        self.assertIsNone(clean.eof())
        self.assertEqual(clean.feed(b"", 0), ([], None))
        self.assertEqual(clean.feed(b"x", 0)[1].kind, "trailing-bytes")
        header = self.reader()
        header.feed(b"\x0c\x00", 0)
        error = header.eof()
        self.assertEqual((error.kind, error.detail["have"]), ("truncated", 2))
        body = self.reader()
        body.feed(frame(1, 1, 50)[:30], 0)
        error = body.eof()
        self.assertEqual((error.kind, error.detail["need"], error.detail["have"]), ("truncated", 50, 30))

    def test_the_age_runs_from_the_first_header_byte_not_the_last(self):
        reader = self.reader(completion_ms=1000)
        data = frame(1, 1, 30)
        reader.feed(data[:1], 0)
        reader.feed(data[1:2], 600 * MS)
        reader.feed(data[2:3], 900 * MS)
        self.assertIsNone(reader.poll(1000 * MS))
        self.assertEqual(reader.poll(1000 * MS + 1).kind, "deadline")

    def test_advance_rejects_a_negative_count_before_any_state_branch(self):
        reader = self.reader()
        with self.assertRaises(ValueError):
            reader.advance(-1, 0)
        self.assertIsNone(reader.eof())
        with self.assertRaises(ValueError):
            reader.advance(-1, 0)
        frames, error = reader.advance(3, 0)
        self.assertEqual((frames, error.kind, error.detail), ([], "trailing-bytes", {"extra": 3}))
        self.assertIs(reader.advance(5, 0)[1], error, "poisoned: the same error")

    def test_idle_time_at_a_frame_boundary_does_not_age_the_next_frame(self):
        reader = self.reader(completion_ms=1000)
        self.assertIsNone(reader.poll(10**15))
        data = frame(1, 1, 30)
        reader.feed(data[:10], 10**15)
        self.assertIsNone(reader.poll(10**15 + 1000 * MS))


class Dicts(unittest.TestCase):
    def test_hello_rows_and_close_reset(self):
        dicts = StreamDicts()
        self.assertEqual(dicts.on_hello("b", 1), Session.FIRST)
        self.assertEqual(dicts.on_hello("b", 1), Session.SAME)
        self.assertEqual(dicts.on_hello("b", 2), Session.CHANGED)
        self.assertEqual(dicts.on_hello("c", 2), Session.CHANGED)
        self.assertEqual(dicts.stream_count(), 0)
        dicts.close(5)
        dicts.reset()


if __name__ == "__main__":
    unittest.main()

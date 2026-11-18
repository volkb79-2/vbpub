#!/usr/bin/env python3
"""Tests of the reader stream fixtures (WIRE.md section 11.4, `fixtures/stream/reader-*.json`).

Run manually, like the other reference tests (HOB-4):

    python3 -I -m unittest discover -s python/tests -p 'test_stream_fixtures.py' -v

Three checks: the committed files are exactly what the generator writes, their JSON shape is the
documented one, and a tiny independent replay (written from the WIRE.md section 5.2 table, sharing
no code with the generator's expectations) reproduces every expectation. The replay is not the
P3 `stream.py`; it only proves that the hand-derived expectations are self-consistent.
"""

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
STREAM = HOSTOBS / "fixtures" / "stream"
sys.path.insert(0, str(HOSTOBS / "python" / "reference"))

import make_stream_fixtures  # noqa: E402

FRAME_TYPES = {1: "req", 2: "rep", 3: "evt", 4: "dict", 5: "block", 6: "end"}
HEADER_LEN = 12
CODEC_MAX = 16 * 1024 * 1024
EXPECTED_CASES = [
    "reader-age-from-first-byte",
    "reader-clean-eof",
    "reader-completion-age",
    "reader-dribbled-bytes",
    "reader-feed-after-eof",
    "reader-frame-then-bad-header",
    "reader-len-below-header",
    "reader-oversized-len",
    "reader-poisoned-stays",
    "reader-split-every-offset",
    "reader-truncated-stream",
    "reader-two-frames-one-feed",
]


class Reader:
    """The WIRE.md section 5.2 machine, in the simplest form that is obviously the table."""

    def __init__(self, max_frame: int, completion_ms: int):
        self.max_frame = max_frame
        self.completion_ms = completion_ms
        self.buf = bytearray()
        self.header = None  # (len, type, corr) once 12 bytes validated
        self.started = 0
        self.poisoned = None
        self.closed = False

    def _validate(self):
        length, code, flags, reserved, corr = struct.unpack("<IBBHI", bytes(self.buf[:HEADER_LEN]))
        if code not in FRAME_TYPES:
            return "unknown-frame-type"
        if flags & ~1 or (flags & 1 and code != 5):
            return "unknown-frame-flags"
        if reserved != 0:
            return "non-zero-reserved"
        if length > CODEC_MAX or length > self.max_frame:
            return "oversized-frame"
        if length < HEADER_LEN:
            return "truncated"
        self.header = (length, code, corr)
        return None

    def feed(self, data: bytes, now_ms: int):
        frames = []
        if self.poisoned:
            return frames, self.poisoned
        if self.closed and data:
            self.poisoned = "trailing-bytes"
            return frames, self.poisoned
        for byte in data:
            if not self.buf:
                self.started = now_ms
            self.buf.append(byte)
            if self.header is None and len(self.buf) == HEADER_LEN:
                error = self._validate()
                if error:
                    self.poisoned = error
                    return frames, error
            if self.header is not None and len(self.buf) == self.header[0]:
                length, code, corr = self.header
                frames.append({"frame": {"type": FRAME_TYPES[code], "corr": corr, "len": length}})
                self.buf = bytearray()
                self.header = None
        return frames, None

    def poll(self, now_ms: int):
        if self.poisoned:
            return self.poisoned
        if self.buf and now_ms > self.started + self.completion_ms:
            self.poisoned = "deadline"
            return self.poisoned
        return None

    def eof(self):
        if self.poisoned:
            return self.poisoned
        if self.buf:
            self.poisoned = "truncated"
            return self.poisoned
        self.closed = True
        return None


def replay(spec: dict) -> list:
    reader = Reader(spec["max_frame"], spec["completion_ms"])
    outputs = []
    for step in spec["steps"]:
        if "feed_hex" in step:
            frames, error = reader.feed(bytes.fromhex(step["feed_hex"]), step["t_ms"])
            outputs.append(frames + ([{"error": error}] if error else []))
        elif step.get("poll"):
            error = reader.poll(step["t_ms"])
            outputs.append([{"error": error}] if error else [])
        elif step.get("eof"):
            error = reader.eof()
            outputs.append([{"error": error}] if error else [{"closed": "clean"}])
        else:
            raise AssertionError(f"unknown step {step}")
    return outputs


class ReaderStreamFixtures(unittest.TestCase):
    def test_the_committed_files_are_exactly_what_the_generator_writes(self):
        rendered = make_stream_fixtures.render_all()
        committed = sorted(path.name for path in STREAM.glob("reader-*.json"))
        self.assertEqual(committed, sorted(rendered))
        for name, text in rendered.items():
            self.assertEqual((STREAM / name).read_text(encoding="utf-8"), text, name)

    def test_there_are_exactly_the_twelve_cases_of_section_11_4(self):
        self.assertEqual(sorted(path.stem for path in STREAM.glob("reader-*.json")), EXPECTED_CASES)

    def test_every_file_has_the_documented_shape(self):
        for path in sorted(STREAM.glob("reader-*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                list(spec), ["case", "doc", "side", "max_frame", "completion_ms", "steps", "expect"], path.name
            )
            self.assertEqual(spec["case"], path.stem)
            self.assertIn(spec["side"], ("client", "server"))
            self.assertIsInstance(spec["max_frame"], int)
            self.assertIsInstance(spec["completion_ms"], int)
            self.assertEqual(len(spec["steps"]), len(spec["expect"]), path.name)
            last_t = -1
            for step in spec["steps"]:
                self.assertGreaterEqual(step["t_ms"], last_t, "time never runs backwards")
                last_t = step["t_ms"]
                kinds = [key for key in ("feed_hex", "poll", "eof") if key in step]
                self.assertEqual(len(kinds), 1, step)
                self.assertEqual(sorted(step), sorted(["t_ms", kinds[0]]), step)
                if "feed_hex" in step:
                    bytes.fromhex(step["feed_hex"])
                else:
                    self.assertIs(step[kinds[0]], True)
            for outputs in spec["expect"]:
                errors = [out for out in outputs if "error" in out]
                self.assertLessEqual(len(errors), 1, "at most one error per step")
                if errors:
                    self.assertIs(outputs[-1], errors[0], "the error comes after the frames")
                for out in outputs:
                    self.assertEqual(len(out), 1, out)
                    if "frame" in out:
                        self.assertEqual(sorted(out["frame"]), ["corr", "len", "type"])
                        self.assertIn(out["frame"]["type"], FRAME_TYPES.values())
                    elif "closed" in out:
                        self.assertEqual(out["closed"], "clean")
                    else:
                        self.assertIsInstance(out["error"], str)

    def test_an_independent_replay_reproduces_every_expectation(self):
        for path in sorted(STREAM.glob("reader-*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(replay(spec), spec["expect"], path.name)

    def test_the_cases_pin_what_their_names_say(self):
        specs = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in STREAM.glob("reader-*.json")}
        dribble = specs["reader-dribbled-bytes"]
        self.assertTrue(all(len(bytes.fromhex(step["feed_hex"])) == 1 for step in dribble["steps"]))
        self.assertEqual(sum(len(out) for out in dribble["expect"]), 1)
        self.assertEqual(len(dribble["expect"][-1]), 1)
        split = specs["reader-split-every-offset"]
        frame_len = specs["reader-split-every-offset"]["expect"][1][0]["frame"]["len"]
        self.assertEqual(len(split["steps"]), 2 * (frame_len - 1))
        self.assertEqual(sum(len(out) for out in split["expect"]), frame_len - 1)
        oversized = specs["reader-oversized-len"]
        declared = struct.unpack("<I", bytes.fromhex(oversized["steps"][0]["feed_hex"] + oversized["steps"][1]["feed_hex"])[:4])[0]
        self.assertEqual(declared, oversized["max_frame"] + 1)
        completion = specs["reader-completion-age"]
        self.assertEqual(completion["steps"][1]["t_ms"], completion["completion_ms"])
        self.assertEqual(completion["steps"][2]["t_ms"], completion["completion_ms"] + 1)
        age = specs["reader-age-from-first-byte"]
        self.assertEqual(age["steps"][2]["t_ms"], 60000)
        self.assertGreater(age["steps"][2]["t_ms"], age["completion_ms"], "the idle time exceeds the age")


if __name__ == "__main__":
    unittest.main()

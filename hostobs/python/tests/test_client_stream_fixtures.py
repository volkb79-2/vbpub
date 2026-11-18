#!/usr/bin/env python3
"""Tests of the client stream fixtures (WIRE.md section 11.4, `fixtures/stream/client-*.json`).

Run manually, like the other reference tests (HOB-4):

    python3 -I -m unittest discover -s python/tests -p 'test_client_stream_fixtures.py' -v

Three checks: the committed files are exactly what the generator writes, their JSON shape is the
documented one, and a tiny independent replay (the stream rules of WIRE.md sections 6.1, 6.2, 6.4
and 6.5 over the reference decoder, sharing no code with the generator's expectations)
reproduces every expectation. The replay is not the P3 Python client; it only proves that the
hand-derived expectations are self-consistent.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
STREAM = HOSTOBS / "fixtures" / "stream"
sys.path.insert(0, str(HOSTOBS / "python" / "reference"))

import hostobs_wire_ref as wire  # noqa: E402
import make_client_stream_fixtures  # noqa: E402

EXPECTED_CASES = [
    "client-epoch-change",
    "client-first-block-bare",
    "client-first-block-gen-5",
    "client-generation-regressed",
    "client-generation-step",
    "client-required-section",
]
SESSIONS = ("first", "same", "changed")


def replay(spec: dict) -> list:
    """The client rules written from WIRE.md: one held dictionary for the one stream, the probe identity."""
    held = None  # {"generation", "entries"} or None
    identity = None
    outputs = []
    for step in spec["steps"]:
        if "hello" in step:
            held = None
            seen = (step["hello"]["boot_id"], step["hello"]["epoch"])
            if identity is None:
                answer = "first"
            elif identity == seen:
                answer = "same"
            else:
                answer = "changed"
            identity = seen
            outputs.append([{"session": answer}])
            continue
        try:
            frame = wire.decode_frame(bytes.fromhex(step["feed_hex"]), held)
        except wire.WireError as refusal:
            held = None
            outputs.append([{"error": refusal.refusal}])
            continue
        generation = frame["header"]["dict_gen"]
        carried = frame.get("dictionary")
        if carried is not None:
            if generation > (held["generation"] if held else 0):
                held = {"generation": carried["generation"], "entries": carried["entries"]}
                outputs.append([{"ok": generation}])
            else:
                outputs.append([{"error": "generation-regressed"}])
                held = None
        elif held is not None and held["generation"] == generation:
            outputs.append([{"ok": generation}])
        else:
            outputs.append([{"error": "stale-dictionary-generation"}])
            held = None
    return outputs


class ClientStreamFixtures(unittest.TestCase):
    def test_the_committed_files_are_exactly_what_the_generator_writes(self):
        rendered = make_client_stream_fixtures.render_all()
        committed = sorted(path.name for path in STREAM.glob("client-*.json"))
        self.assertEqual(committed, sorted(rendered))
        for name, text in rendered.items():
            self.assertEqual((STREAM / name).read_text(encoding="utf-8"), text, name)

    def test_there_are_exactly_the_six_cases_of_section_11_4(self):
        self.assertEqual(sorted(path.stem for path in STREAM.glob("client-*.json")), EXPECTED_CASES)

    def test_every_file_has_the_documented_shape(self):
        for path in sorted(STREAM.glob("client-*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                list(spec), ["case", "doc", "side", "max_frame", "completion_ms", "steps", "expect"], path.name
            )
            self.assertEqual(spec["case"], path.stem)
            self.assertEqual(spec["side"], "client")
            self.assertEqual(len(spec["steps"]), len(spec["expect"]), path.name)
            last_t = -1
            for step, outputs in zip(spec["steps"], spec["expect"]):
                self.assertGreaterEqual(step["t_ms"], last_t, "time never runs backwards")
                last_t = step["t_ms"]
                self.assertEqual(len(outputs), 1, "one output per step")
                (output,) = outputs
                self.assertEqual(len(output), 1, output)
                if "feed_hex" in step:
                    self.assertEqual(sorted(step), ["feed_hex", "t_ms"])
                    data = bytes.fromhex(step["feed_hex"])
                    self.assertEqual(int.from_bytes(data[:4], "little"), len(data), "one whole frame")
                    self.assertEqual(data[4], 5, "a BLOCK frame")
                    self.assertEqual(int.from_bytes(data[8:12], "little"), 5, "stream corr 5")
                    if "ok" in output:
                        self.assertIsInstance(output["ok"], int)
                    else:
                        self.assertIsInstance(output["error"], str)
                else:
                    self.assertEqual(sorted(step), ["hello", "t_ms"])
                    self.assertEqual(sorted(step["hello"]), ["boot_id", "epoch"])
                    self.assertIn(output["session"], SESSIONS)

    def test_an_independent_replay_reproduces_every_expectation(self):
        for path in sorted(STREAM.glob("client-*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(replay(spec), spec["expect"], path.name)

    def test_the_cases_pin_what_their_names_say(self):
        specs = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in STREAM.glob("client-*.json")}
        self.assertEqual(specs["client-first-block-gen-5"]["expect"], [[{"ok": 5}], [{"ok": 5}]])
        self.assertEqual(
            specs["client-generation-step"]["expect"],
            [[{"ok": 2}], [{"ok": 2}], [{"ok": 3}], [{"error": "stale-dictionary-generation"}]],
        )
        self.assertEqual(specs["client-generation-regressed"]["expect"][1], [{"error": "generation-regressed"}])
        self.assertEqual(
            specs["client-required-section"]["expect"][1], [{"error": "unsupported-required-section"}]
        )
        sessions = [out[0]["session"] for out in specs["client-epoch-change"]["expect"] if "session" in out[0]]
        self.assertEqual(sessions, ["first", "changed", "same"])


if __name__ == "__main__":
    unittest.main()

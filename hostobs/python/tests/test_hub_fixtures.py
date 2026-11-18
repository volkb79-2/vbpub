#!/usr/bin/env python3
"""Tests of the send-hub fixtures (WIRE.md section 11.4, `fixtures/stream/hub-*.json`).

Part of the registered `conformance` lane (HOB-4); by hand:

    python3 -I -m unittest discover -s python/tests -p 'test_hub_fixtures.py' -v

Two checks: the committed files are exactly what the generator writes (byte for byte, exactly
the 19 expected files), and their JSON shape is the documented one. The Rust replay
(`crates/proto/tests/hub_fixtures.rs`) judges the hub against the expectations.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
STREAM = HOSTOBS / "fixtures" / "stream"
sys.path.insert(0, str(HOSTOBS / "python" / "reference"))

import make_hub_fixtures  # noqa: E402

LIMIT_KEYS = set(make_hub_fixtures.BASE_LIMITS)
OPS = {
    "push_live",
    "push_query",
    "push_control",
    "query_ready",
    "end_stream",
    "head",
    "head_partial",
    "wrote",
    "poll",
    "drain_unpinned",
    "remove",
}
OUTCOMES = {"queued", "coalesced", "dropped-incoming", "backpressure", "ok", "close"}


def committed() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(STREAM.glob("hub-*.json"))
    }


class HubFixtureTests(unittest.TestCase):
    def test_exactly_the_19_cases(self) -> None:
        names = sorted(f"{name}.json" for name in make_hub_fixtures.CASE_NAMES)
        self.assertEqual(len(names), 19)
        self.assertEqual(sorted(committed()), names)

    def test_committed_files_equal_the_generator_output(self) -> None:
        self.assertEqual(committed(), make_hub_fixtures.render_all())

    def test_shape(self) -> None:
        for name, text in committed().items():
            with self.subTest(name):
                doc = json.loads(text)
                self.assertEqual(f"{doc['case']}.json", name)
                self.assertEqual(set(doc), {"case", "doc", "limits", "conns", "steps", "expect"})
                self.assertEqual(set(doc["limits"]), LIMIT_KEYS)
                self.assertTrue(all(isinstance(uid, int) for uid in doc["conns"]))
                self.assertEqual(len(doc["steps"]), len(doc["expect"]))
                last = 0
                for step, outputs in zip(doc["steps"], doc["expect"]):
                    self.assertIn(step["op"], OPS)
                    self.assertGreaterEqual(step["t_ms"], last)
                    last = step["t_ms"]
                    if "c" in step:
                        self.assertLess(step["c"], len(doc["conns"]))
                    for output in outputs:
                        if isinstance(output, str):
                            self.assertIn(output, OUTCOMES)
                        elif output is not None:
                            self.assertIn(
                                next(iter(output)),
                                {"pools", "closed", "head", "counts", "ready", "partial"},
                            )
                    self.assertEqual("watch" in step, any(
                        isinstance(o, dict) and "pools" in o for o in outputs
                    ))


if __name__ == "__main__":
    unittest.main()

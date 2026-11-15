#!/usr/bin/env python3
"""Cross-implementation tests for the independent Python reference codec (plan v4 §B.5).

Run manually (there is no registered `conformance` lane yet; see
`KNOWN_ISSUES_TODO_BACKLOG.md` HOB-4):

    python3 -m unittest discover -s python/tests -v

The tests do not import anything outside the standard library, and they do not import the
Rust code: the fixtures are the contract between the two implementations. The Rust side
asserts the same files in `crates/proto/tests/golden_fixtures.rs`, so a divergence in
either implementation is a red test run rather than a silent mismatch.
"""

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
FIXTURES = HOSTOBS / "fixtures" / "wire"
RATES = HOSTOBS / "fixtures" / "rates"
sys.path.insert(0, str(HOSTOBS / "python" / "reference"))

import hostobs_rate_ref  # noqa: E402
import hostobs_wire_ref  # noqa: E402


CPU_USAGE_FACTS = {
    "unit": "usec",
    "kind": "counter",
    "scope": "cgroup",
    "source": "cgroupfs",
}

MEM_CURRENT_FACTS = {
    "unit": "bytes",
    "kind": "gauge",
    "scope": "cgroup",
    "source": "cgroupfs",
}


class WireFixtures(unittest.TestCase):
    def test_every_fixture_round_trips_in_the_reference_codec(self):
        cases = hostobs_wire_ref.load_cases(FIXTURES)
        self.assertEqual(len(cases), 23, "the fixture case list is part of the contract")
        for spec_path, spec, data in cases:
            with self.subTest(case=spec_path.stem):
                hostobs_wire_ref.check_case(spec, data)

    def test_a_valid_fixture_is_byte_exact_through_encode_and_decode(self):
        for spec_path, spec, data in hostobs_wire_ref.load_cases(FIXTURES):
            if "expect_error" in spec:
                continue
            with self.subTest(case=spec_path.stem):
                encoded = hostobs_wire_ref.encode_frame(spec["frame"])
                self.assertEqual(encoded, data, "the reference encoder must reproduce the committed bytes")
                decoded = hostobs_wire_ref.decode_frame(data)
                self.assertEqual(
                    hostobs_wire_ref.normalise(decoded),
                    hostobs_wire_ref.normalise(spec["frame"]),
                )

    def test_dictionary_trailing_bytes_and_bad_text_are_refused(self):
        body = hostobs_wire_ref.encode_dictionary(
            {"generation": 1, "entries": [{"kind": "free", "text": "abc"}]}
        )
        hostobs_wire_ref.decode_dictionary(body)
        with self.assertRaises(hostobs_wire_ref.WireError) as caught:
            hostobs_wire_ref.decode_dictionary(body[:-1])
        self.assertEqual(caught.exception.refusal, "trailing-bytes")
        with self.assertRaises(hostobs_wire_ref.WireError):
            hostobs_wire_ref.encode_dictionary(
                {"generation": 1, "entries": [{"kind": "free", "text": "x" * 257}]}
            )

    def test_unknown_section_without_the_required_flag_is_skipped_only(self):
        spec = json.loads((FIXTURES / "unknown-optional-section.json").read_text(encoding="utf-8"))
        decoded = hostobs_wire_ref.decode_frame((FIXTURES / "unknown-optional-section.bin").read_bytes())
        self.assertEqual(decoded["skipped"], spec["frame"]["skipped"])

    def test_an_unknown_required_section_is_a_refusal(self):
        spec = json.loads((FIXTURES / "required-section-refused.json").read_text(encoding="utf-8"))
        with self.assertRaises(hostobs_wire_ref.WireError) as caught:
            hostobs_wire_ref.decode_frame((FIXTURES / "required-section-refused.bin").read_bytes())
        self.assertEqual(caught.exception.as_dict()["kind"], spec["expect_error"]["kind"])

    def test_a_dictionary_body_repeating_an_identity_is_refused(self):
        # The wire list is the sender's numbering. A de-duplicating decoder would return a
        # shorter table and shift every later index; both languages must refuse instead.
        body = hostobs_wire_ref.encode_dictionary(
            {
                "generation": 1,
                "entries": [
                    {"kind": "entity", "inc": 7, "text": "/a"},
                    {"kind": "entity", "inc": 7, "text": "/b"},
                    {"kind": "metric", "text": "mem.current", "facts": MEM_CURRENT_FACTS},
                ],
            }
        )
        with self.assertRaises(hostobs_wire_ref.WireError) as caught:
            hostobs_wire_ref.decode_dictionary(body)
        self.assertEqual(caught.exception.as_dict(), {"kind": "duplicate-dictionary-entry", "index": 1})

    def test_a_metric_mask_that_is_not_the_derived_mask_is_refused(self):
        # The mask is derived from the kind, so a subset is as corrupt as an extra bit.
        for mask, derived in ((0, 7), (7 | 8, 7)):
            with self.subTest(mask=mask):
                body = hostobs_wire_ref.encode_dictionary(
                    {
                        "generation": 1,
                        "entries": [
                            {"kind": "metric", "text": "cpu.usage_usec", "facts": CPU_USAGE_FACTS},
                        ],
                    }
                )
                # generation + count + entry header, then unit, kind, scope, source, mask.
                at = 8 + 4 + 4
                body = body[:at] + struct.pack("<H", mask) + body[at + 2 :]
                with self.assertRaises(hostobs_wire_ref.WireError) as caught:
                    hostobs_wire_ref.decode_dictionary(body)
                self.assertEqual(
                    caught.exception.as_dict(),
                    {"kind": "dictionary-entry-mask", "index": 0, "mask": mask, "expected": derived},
                )

    def test_the_fixture_set_pins_every_new_guard(self):
        # Each guard added in the fix round has committed bytes, so a language that loses
        # the guard fails here rather than silently accepting the corrupt frame.
        expected = {
            "duplicate-dictionary-entry": "duplicate-dictionary-entry",
            "bad-metric-mask": "dictionary-entry-mask",
            "row-references-metric": "row-reference-not-entity",
            "column-references-entity": "column-reference-not-metric",
            "column-kind-mismatch": "column-kind-mismatch",
            "no-rows-or-time": "no-rows-or-time",
            "empty-rows-and-time": "rows-and-time",
        }
        for case, refusal in expected.items():
            with self.subTest(case=case):
                spec = json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))
                self.assertEqual(spec["expect_error"]["kind"], refusal)
                with self.assertRaises(hostobs_wire_ref.WireError) as caught:
                    hostobs_wire_ref.decode_frame((FIXTURES / f"{case}.bin").read_bytes())
                self.assertEqual(caught.exception.as_dict()["kind"], refusal)

    def test_the_quality_vocabulary_is_the_one_the_rust_side_declares(self):
        vocabulary = json.loads((FIXTURES / "quality.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["name"] for entry in vocabulary["codes"]], hostobs_wire_ref.QUALITIES)
        self.assertEqual(
            [entry["code"] for entry in vocabulary["codes"]], list(range(len(hostobs_wire_ref.QUALITIES)))
        )
        self.assertEqual(vocabulary["codes"][0], {"code": 0, "name": "ok", "measurement": True})
        self.assertTrue(all(not entry["measurement"] for entry in vocabulary["codes"][1:]))


class RateFixtures(unittest.TestCase):
    def test_every_hand_computed_case_is_reproduced(self):
        cases = json.loads((RATES / "rates.json").read_text(encoding="utf-8"))["cases"]
        self.assertEqual(len(cases), 18)
        for case in cases:
            with self.subTest(case=case["case"]):
                hostobs_rate_ref.check_case(case)

    def test_a_gap_is_divided_by_the_actual_interval_not_the_nominal_one(self):
        case = next(
            case
            for case in json.loads((RATES / "rates.json").read_text(encoding="utf-8"))["cases"]
            if case["case"] == "three-second-gap-is-an-average"
        )
        actual = hostobs_rate_ref.derive_rate(case["previous"], case["current"], case["nominal_interval_ns"])
        self.assertEqual(actual["per_second"], 1000.0)
        self.assertNotEqual(actual["per_second"], 3000.0)
        self.assertEqual(actual["missed"], 2)

    def test_a_refusal_names_the_offending_reading(self):
        case = next(
            case
            for case in json.loads((RATES / "rates.json").read_text(encoding="utf-8"))["cases"]
            if case["case"] == "previous-absent"
        )
        with self.assertRaises(hostobs_rate_ref.RateError) as caught:
            hostobs_rate_ref.derive_rate(case["previous"], case["current"], case["nominal_interval_ns"])
        self.assertEqual(caught.exception.as_dict(), {"reason": "missing", "reading": "previous"})


if __name__ == "__main__":
    unittest.main()

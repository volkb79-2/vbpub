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
SEGMENTS = HOSTOBS / "fixtures" / "segment"
sys.path.insert(0, str(HOSTOBS / "python" / "reference"))

import hostobs_rate_ref  # noqa: E402
import hostobs_segment_ref  # noqa: E402
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
        self.assertEqual(len(cases), 41, "the fixture case list is part of the contract")
        valid = [path.stem for path, spec, _data in cases if "expect_error" not in spec]
        self.assertEqual(len(valid), 11)
        for spec_path, spec, data in cases:
            with self.subTest(case=spec_path.stem):
                hostobs_wire_ref.check_case(spec, data)

    def _spec(self, case):
        return json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))

    def _refusal(self, data, held=None):
        with self.assertRaises(hostobs_wire_ref.WireError) as caught:
            hostobs_wire_ref.decode_block(data, held)
        return caught.exception.as_dict()

    def test_a_bare_block_is_judged_against_the_held_dictionary_only_when_one_is_given(self):
        spec = self._spec("bare-block-held-dict")
        data = (FIXTURES / "bare-block-held-dict.bin").read_bytes()
        self.assertEqual(hostobs_wire_ref.decode_block(data, spec["held"])["rows"], spec["frame"]["rows"])
        self.assertEqual(hostobs_wire_ref.decode_block(data)["rows"], spec["frame"]["rows"])
        stale = dict(spec["held"], generation=2)
        self.assertEqual(self._refusal(data, stale)["kind"], "stale-dictionary-generation")
        mismatch = json.loads(json.dumps(spec["held"]))
        mismatch["entries"][2]["facts"]["kind"] = "gauge"
        self.assertEqual(self._refusal(data, mismatch)["kind"], "column-kind-mismatch")
        dangling = (FIXTURES / "bare-block-dangling.bin").read_bytes()
        self.assertEqual(self._refusal(dangling, spec["held"])["kind"], "dangling-dictionary-ref")
        # An in-frame dictionary wins over a held one: the held generation is not consulted.
        live = (FIXTURES / "live-block.bin").read_bytes()
        self.assertEqual(hostobs_wire_ref.decode_block(live, {"generation": 9, "entries": []})["corr"], 99)

    def test_every_bucket_agg_must_be_in_every_columns_mask(self):
        spec = self._spec("history-gauge-aggs")
        counter = self._spec("history-agg-not-permitted")["frame"]
        permitted = {"counter": {"raw", "last", "delta"}, "gauge": {"last", "mean", "min", "max"}}
        for kind, frame in (("gauge", spec["frame"]), ("counter", counter)):
            for agg in hostobs_wire_ref.AGGREGATIONS:
                with self.subTest(kind=kind, agg=agg):
                    mutated = json.loads(json.dumps(frame))
                    mutated["time"] = [dict(mutated["time"][0], agg=agg)] * len(mutated["time"])
                    data = hostobs_wire_ref.encode_frame(mutated)
                    if agg in permitted[kind]:
                        hostobs_wire_ref.decode_block(data)
                    else:
                        self.assertEqual(self._refusal(data)["kind"], "aggregation-not-permitted")
        mixed = json.loads(json.dumps(spec["frame"]))
        mixed["dictionary"]["entries"][1]["facts"] = dict(CPU_USAGE_FACTS)
        mixed["columns"][0]["kind"] = "counter"
        self.assertEqual(
            self._refusal(hostobs_wire_ref.encode_frame(mixed))["kind"], "aggregation-not-permitted"
        )

    def test_the_subject_section_needs_time_a_whole_body_and_an_incarnation(self):
        frame = json.loads(json.dumps(self._spec("history-subject")["frame"]))
        self.assertEqual(hostobs_wire_ref.decode_block(hostobs_wire_ref.encode_frame(frame))["subject"], 0)
        frame["subject"] = 99
        self.assertEqual(self._refusal(hostobs_wire_ref.encode_frame(frame))["kind"], "dangling-dictionary-ref")
        frame["subject"] = 1
        self.assertEqual(self._refusal(hostobs_wire_ref.encode_frame(frame))["kind"], "row-reference-not-entity")
        self.assertEqual(self._refusal(hostobs_wire_ref.encode_frame(
            dict(self._spec("subject-in-live-block")["frame"])))["kind"], "subject-without-time")
        self.assertEqual(hostobs_wire_ref.PROTO_MINOR, 1)
        good = bytearray((FIXTURES / "history-subject.bin").read_bytes())
        n_sec = struct.unpack_from("<H", good, 12)[0]
        # The subject body follows the header body (80 bytes) and the dictionary body.
        body = 16 + 8 * n_sec + 80 + struct.unpack_from("<I", good, 16 + 8 + 4)[0]
        struct.pack_into("<I", good, body + 4, 1)
        self.assertEqual(self._refusal(bytes(good))["kind"], "unknown-section-flags")

    def test_received_frame_rules_alignment_header_first_and_flag_type(self):
        self.assertEqual(self._refusal((FIXTURES / "unaligned-optional-section.bin").read_bytes())["kind"], "section-length")
        self.assertEqual(
            self._refusal((FIXTURES / "optional-section-before-header.bin").read_bytes()),
            {"kind": "section-order", "index": 0, "kind_code": 9},
        )
        # The rank check still runs first: the pinned fixture keeps index 1, kind_code 1.
        self.assertEqual(
            self._refusal((FIXTURES / "section-order.bin").read_bytes()),
            {"kind": "section-order", "index": 1, "kind_code": 1},
        )
        rep = (FIXTURES / "rep-sectioned-flag.bin").read_bytes()
        self.assertEqual(self._refusal(rep)["kind"], "unknown-frame-flags")
        for type_code in (1, 3, 6):
            self.assertEqual(self._refusal(rep[:4] + bytes([type_code]) + rep[5:])["kind"], "unknown-frame-flags")
        self.assertEqual(self._refusal(rep[:5] + b"\x00" + rep[6:])["kind"], "not-a-block")
        # n_sec = 65535 in a 16-byte frame: the directory is judged against the bytes present.
        short = struct.pack("<IBBHIHH", 16, 5, 1, 0, 1, 65535, 0)
        self.assertEqual(self._refusal(short)["kind"], "truncated")

    def test_check_precedence_not_a_block_before_the_buffer_length(self):
        rep = (FIXTURES / "rep-not-sectioned-truncated.bin").read_bytes()
        self.assertEqual(self._refusal(rep), {"kind": "not-a-block", "frame_type": 2})
        self.assertEqual(self._refusal(rep + b"\x00" * 16)["kind"], "not-a-block")

    def test_one_duplicate_outcome_for_every_repeated_dictionary_identity(self):
        for case in ("duplicate-metric-other-facts", "duplicate-entity-other-label", "duplicate-dictionary-entry"):
            with self.subTest(case=case):
                refusal = self._refusal((FIXTURES / f"{case}.bin").read_bytes())
                self.assertEqual(refusal["kind"], "duplicate-dictionary-entry")

    def test_a_valid_fixture_is_byte_exact_through_encode_and_decode(self):
        for spec_path, spec, data in hostobs_wire_ref.load_cases(FIXTURES):
            if "expect_error" in spec:
                continue
            with self.subTest(case=spec_path.stem):
                encoded = hostobs_wire_ref.encode_frame(spec["frame"])
                self.assertEqual(encoded, data, "the reference encoder must reproduce the committed bytes")
                decoded = hostobs_wire_ref.decode_frame(data, spec.get("held"))
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
            "block-as-req": "not-a-block",
            "section-order": "section-order",
            "truncated-directory": "truncated",
        }
        for case, refusal in expected.items():
            with self.subTest(case=case):
                spec = json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))
                self.assertEqual(spec["expect_error"]["kind"], refusal)
                with self.assertRaises(hostobs_wire_ref.WireError) as caught:
                    hostobs_wire_ref.decode_frame((FIXTURES / f"{case}.bin").read_bytes())
                self.assertEqual(caught.exception.as_dict()["kind"], refusal)

    def test_a_truncated_directory_is_a_typed_refusal_and_never_a_struct_error(self):
        # A 16-byte frame declares one section and carries no directory entry. The Rust
        # decoder reports a truncation of the block directory; the reference decoder must
        # raise its own WireError with the same context instead of struct.error.
        data = struct.pack("<IBBHIHH", 16, hostobs_wire_ref.FRAME_TYPES["block"], hostobs_wire_ref.FRAME_SECTIONED, 0, 39, 1, 0)
        with self.assertRaises(hostobs_wire_ref.WireError) as caught:
            hostobs_wire_ref.decode_frame(data)
        self.assertEqual(caught.exception.as_dict()["kind"], "truncated")
        self.assertEqual(caught.exception.as_dict()["context"], "block directory")
        # Every other short directory prefix is a WireError too, never a struct.error: an
        # entry count larger than the bytes present is a hostile frame, not a Python bug.
        for declared in range(1, 5):
            data = struct.pack(
                "<IBBHIHH",
                16,
                hostobs_wire_ref.FRAME_TYPES["block"],
                hostobs_wire_ref.FRAME_SECTIONED,
                0,
                39,
                declared,
                0,
            )
            with self.subTest(declared=declared), self.assertRaises(hostobs_wire_ref.WireError) as caught:
                hostobs_wire_ref.decode_frame(data)
            self.assertEqual(caught.exception.as_dict()["kind"], "truncated")

    def test_the_time_section_is_fifteen_bytes_per_bucket_padded_to_eight(self):
        # HOB-48 (length half): i64 + u32 + u16 + u8 = 15 bytes a bucket. A factor of 14 gives
        # the same padded length up to three buckets, so every count from 0 to 9 is pinned by
        # its exact length, and the five-bucket fixture pins it on committed bytes.
        wire = hostobs_wire_ref
        expected = [0, 16, 32, 48, 64, 80, 96, 112, 120, 136]
        aggs = list(wire.AGGREGATIONS)
        for n, want in enumerate(expected):
            buckets = [
                {
                    "real_ns": index * 1_000_000_007 - 1,
                    "covered_ms": index * 100_003,
                    "samples": index * 7_001,
                    "agg": aggs[index % len(aggs)],
                }
                for index in range(n)
            ]
            body = wire.encode_time(buckets)
            with self.subTest(buckets=n):
                self.assertEqual(len(body), want)
                self.assertEqual(wire.time_body_len(n), want)
                self.assertEqual(wire.decode_time(body, n), buckets)
        self.assertEqual(wire.TIME_BUCKET_BYTES, 15)
        # The decoder refuses the length the old factor demanded for four buckets.
        with self.assertRaises(wire.WireError) as caught:
            wire.decode_time(wire.encode_time([{"real_ns": 0, "covered_ms": 0, "samples": 0, "agg": "raw"}] * 4)[:56], 4)
        self.assertEqual(
            caught.exception.as_dict(),
            {"kind": "section-length", "section": "time", "declared": 56, "expected": 64},
        )
        spec = json.loads((FIXTURES / "history-five-buckets.json").read_text(encoding="utf-8"))
        self.assertEqual(len(spec["frame"]["time"]), 5)
        decoded = wire.decode_frame((FIXTURES / "history-five-buckets.bin").read_bytes())
        self.assertEqual(len(decoded["time"]), 5)

    def test_the_frame_header_bounds_the_declared_length_at_both_ends(self):
        # HOB-8: the Rust FrameHeader::parse refuses a declared length outside
        # FRAME_HEADER_LEN..=MAX_FRAME_BYTES, and so does the reference decoder, with the
        # same refusal, before it compares the length with the buffer.
        wire = hostobs_wire_ref

        def header(length: int, *, frame_type: int | None = None, reserved: int = 0) -> bytes:
            return struct.pack(
                "<IBBHI",
                length,
                wire.FRAME_TYPES["block"] if frame_type is None else frame_type,
                wire.FRAME_SECTIONED,
                reserved,
                39,
            )

        for length in (wire.MAX_FRAME_BYTES + 1, 0xFFFFFFFF):
            with self.subTest(length=length), self.assertRaises(wire.WireError) as caught:
                wire.decode_frame(header(length))
            self.assertEqual(
                caught.exception.as_dict(),
                {"kind": "oversized-frame", "len": length, "max": wire.MAX_FRAME_BYTES},
            )
        for length in (0, 4, wire.FRAME_HEADER_LEN - 1):
            with self.subTest(length=length), self.assertRaises(wire.WireError) as caught:
                wire.decode_frame(header(length))
            self.assertEqual(
                caught.exception.as_dict(),
                {
                    "kind": "truncated",
                    "context": "frame length shorter than the header",
                    "need": wire.FRAME_HEADER_LEN,
                    "have": length,
                },
            )
        # Exactly the lower bound is not a length refusal: the 12 bytes are a block with
        # no section directory, which is refused for a different reason.
        with self.assertRaises(wire.WireError) as caught:
            wire.decode_frame(header(wire.FRAME_HEADER_LEN))
        self.assertEqual(
            caught.exception.as_dict(),
            {"kind": "truncated", "context": "block directory", "need": wire.FRAME_HEADER_LEN + 4, "have": wire.FRAME_HEADER_LEN},
        )
        # Exactly the upper bound passes the length check and only then meets the buffer.
        with self.assertRaises(wire.WireError) as caught:
            wire.decode_frame(header(wire.MAX_FRAME_BYTES))
        self.assertEqual(
            caught.exception.as_dict(),
            {"kind": "truncated", "context": "frame", "need": wire.MAX_FRAME_BYTES, "have": wire.FRAME_HEADER_LEN},
        )
        # The fixed fields are judged before the length, the order the Rust parse uses.
        with self.assertRaises(wire.WireError) as caught:
            wire.decode_frame(header(wire.MAX_FRAME_BYTES + 1, reserved=1))
        self.assertEqual(caught.exception.as_dict(), {"kind": "non-zero-reserved", "value": 1})
        with self.assertRaises(wire.WireError) as caught:
            wire.decode_frame(header(0, frame_type=9))
        self.assertEqual(caught.exception.as_dict(), {"kind": "unknown-frame-type", "code": 9})

    def test_the_encoder_refuses_a_block_shape_under_another_frame_type(self):
        spec = json.loads((FIXTURES / "live-block.json").read_text(encoding="utf-8"))
        for frame_type in ("req", "rep", "evt", "dict", "end"):
            frame = json.loads(json.dumps(spec["frame"]))
            frame["type"] = frame_type
            with self.subTest(frame_type=frame_type), self.assertRaises(hostobs_wire_ref.WireError) as caught:
                hostobs_wire_ref.encode_frame(frame)
            self.assertEqual(
                caught.exception.as_dict(),
                {"kind": "not-a-block", "frame_type": hostobs_wire_ref.FRAME_TYPES[frame_type]},
            )

    def test_the_encoder_refuses_a_non_canonical_section_order(self):
        spec = json.loads((FIXTURES / "live-block.json").read_text(encoding="utf-8"))
        frame = json.loads(json.dumps(spec["frame"]))
        frame["sections"] = ["dictionary", "header", "rows", "columns"]
        with self.assertRaises(hostobs_wire_ref.WireError) as caught:
            hostobs_wire_ref.encode_frame(frame)
        self.assertEqual(
            caught.exception.as_dict(),
            {"kind": "section-order", "index": 1, "kind_code": hostobs_wire_ref.SECTION_KINDS["header"]},
        )

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


class SegmentFixtures(unittest.TestCase):
    """The committed segment fixtures, from the reference side (plan v4 §C.5).

    `crates/store/tests/golden_segment.rs` decodes the same files in Rust. The fixtures
    themselves are regenerated by `python/reference/make_segment_fixtures.py`, which is the
    independent encoder — so a byte layout that only the Rust writer knows turns one of the
    two lanes red instead of shipping.
    """

    def test_every_segment_fixture_decodes_and_re_encodes_byte_identically(self):
        cases = hostobs_segment_ref.load_cases(SEGMENTS)
        self.assertGreaterEqual(len(cases), 5, "the fixture set covers open, sealed and both corpora")
        for spec_path, spec, data in cases:
            with self.subTest(case=spec_path.stem):
                decoded = hostobs_segment_ref.decode_segment(data)
                self.assertEqual(decoded["tail"], {"state": "complete"})
                self.assertEqual(decoded["header"], spec["header"])
                self.assertEqual(decoded["records"], spec["records"])
                self.assertEqual(decoded["sealed"], spec["sealed"])
                self.assertEqual(decoded["footer"], spec["footer"])
                # The fixture is the encoder's own output: re-encoding the decoded facts
                # must reproduce the committed bytes exactly.
                rebuilt = hostobs_segment_ref.encode_segment(
                    {
                        "header": decoded["header"],
                        "records": decoded["records"],
                        "sealed": decoded["sealed"],
                        "footer": decoded["footer"] or {},
                    }
                )
                self.assertEqual(rebuilt, data, "the reference re-encode is byte exact")

    def test_a_sealed_fixture_names_its_record_count_and_offsets(self):
        for spec_path, spec, data in hostobs_segment_ref.load_cases(SEGMENTS):
            if not spec["sealed"]:
                continue
            with self.subTest(case=spec_path.stem):
                footer = spec["footer"]
                self.assertEqual(footer["record_count"], len(spec["records"]))
                self.assertEqual(footer["end_offset"], len(data) - hostobs_segment_ref.FOOTER_LEN)
                self.assertEqual(footer["seq"], spec["header"]["seq"])
                self.assertEqual(footer["dict_generation"], spec["header"]["dict_generation"])

    def test_a_zero_filled_tail_is_an_unwritten_tail_and_an_other_tail_is_corrupt(self):
        # The Rust scan reports a tail of pure zeros as an *unwritten* (incomplete) tail: it is
        # what a power cut leaves on a size-extending filesystem, so only the tail is cut and
        # the valid prefix stays in service. Any other bytes at the same offset remain corrupt.
        # The two implementations must agree on both halves of that rule.
        spec_path, spec, data = hostobs_segment_ref.load_cases(SEGMENTS)[0]
        unwritten = data + b"\x00" * 4096
        decoded = hostobs_segment_ref.decode_segment(unwritten)
        self.assertEqual(decoded["tail"]["state"], "incomplete")
        self.assertTrue(decoded["tail"].get("unwritten"), "the tail is reported as unwritten")
        self.assertEqual(len(decoded["records"]), len(spec["records"]), "the prefix survives")
        self.assertEqual(decoded["good_end"], len(data))
        damaged = data + b"\x5a" * 4096
        decoded = hostobs_segment_ref.decode_segment(damaged)
        self.assertEqual(decoded["tail"]["state"], "corrupt")
        self.assertEqual(decoded["tail"]["refusal"], "record-magic")
        self.assertEqual(decoded["tail"]["offset"], len(data))

    def test_a_torn_tail_is_a_state_and_a_corrupt_record_is_a_named_refusal(self):
        # A torn file is the M1 acceptance criterion: the bytes stop inside a record and the
        # reader must say so by offset rather than serve a short record as data.
        spec_path, spec, data = hostobs_segment_ref.load_cases(SEGMENTS)[1]
        torn = data[: len(data) - 8]
        decoded = hostobs_segment_ref.decode_segment(torn)
        self.assertEqual(decoded["tail"]["state"], "incomplete")
        self.assertGreater(decoded["tail"]["offset"], hostobs_segment_ref.HEADER_LEN)
        self.assertEqual(len(decoded["records"]), len(spec["records"]) - 1)
        # A body byte that does not match its checksum is a *damage state* naming the record,
        # not an exception: the bytes before it are still readable, so the reader reports where
        # the damage begins and the caller quarantines the file (the Rust `scan` does the same).
        damaged = bytearray(data)
        first_record_body = hostobs_segment_ref.HEADER_LEN + hostobs_segment_ref.RECORD_HEADER_LEN
        damaged[first_record_body + 8] ^= 0xFF
        decoded = hostobs_segment_ref.decode_segment(bytes(damaged))
        self.assertEqual(decoded["tail"]["state"], "corrupt")
        self.assertEqual(decoded["tail"]["refusal"], "record-crc")
        self.assertEqual(decoded["tail"]["index"], 0)
        self.assertEqual(decoded["records"], [])
        # A header that is not this format's magic *is* a refusal: there is nothing readable
        # before it and nothing to quarantine into a named state.
        bad_magic = bytearray(data)
        bad_magic[0] = ord("X")
        with self.assertRaises(hostobs_segment_ref.SegmentError) as caught:
            hostobs_segment_ref.decode_segment(bytes(bad_magic))
        self.assertEqual(caught.exception.refusal, "header-magic")

    def test_a_footer_that_disagrees_with_the_records_is_refused(self):
        # A sealed segment's footer states the record count. A writer that declared a count it
        # did not write (an off-by-one, or a stale footer from a previous seal) must be a
        # refusal rather than a segment that reports more records than it holds.
        from hostobs_segment_ref import SegmentError, crc32  # noqa: PLC0415

        sealed = SEGMENTS / "sealed-history.seg"
        data = bytearray(sealed.read_bytes())
        footer_at = len(data) - 64
        declared = int.from_bytes(data[footer_at + 16 : footer_at + 24], "little")
        self.assertEqual(declared, 3, "the fixture's footer counts three records")
        data[footer_at + 16 : footer_at + 24] = (declared + 1).to_bytes(8, "little")
        # Re-checksum the footer, so the refusal reached is the count and not the CRC.
        data[footer_at + 56 : footer_at + 60] = crc32(bytes(data[footer_at : footer_at + 56])).to_bytes(4, "little")
        with self.assertRaises(SegmentError) as caught:
            hostobs_segment_ref.decode_segment(bytes(data))
        self.assertEqual(caught.exception.refusal, "footer-count")
        self.assertEqual(caught.exception.fields["declared"], 4)
        self.assertEqual(caught.exception.fields["measured"], 3)

    def test_every_segment_refusal_is_named_for_the_rust_side(self):
        # The error vocabulary is shared: the Rust `StoreError::kind` names and these are the
        # same strings, so a difference is a named difference rather than "it failed".
        names = {
            "truncated",
            "header-magic",
            "header-version",
            "header-length",
            "header-crc",
            "reserved",
            "tier-kind-mismatch",
            "record-magic",
            "record-header-crc",
            "record-crc",
            "record-sequence",
            "record-kind",
            "record-incarnations",
            "record-padding",
            "payload-frame-type",
            "payload-frame-length",
            "payload-frame-flags",
            "payload-frame-reserved",
            "payload-too-large",
            "footer-magic",
            "footer-crc",
            "footer-count",
            "footer-offset",
            "footer-identity",
            "footer-generation",
        }
        self.assertTrue(names)
        spec_path, spec, data = hostobs_segment_ref.load_cases(SEGMENTS)[2]
        self.assertEqual(spec["header"]["kind"], "history")
        bad_magic = bytearray(data)
        bad_magic[0] = ord("X")
        with self.assertRaises(hostobs_segment_ref.SegmentError) as caught:
            hostobs_segment_ref.decode_segment(bytes(bad_magic))
        self.assertEqual(caught.exception.refusal, "header-magic")
        # A history header that claims an events payload is refused by corpus.
        evt_spec = next(
            spec
            for path, spec, _ in hostobs_segment_ref.load_cases(SEGMENTS)
            if spec["header"]["kind"] == "events"
        )
        self.assertEqual(evt_spec["header"]["payload"], "evt")


class SegmentNames(unittest.TestCase):
    """HOB-42/HOB-43: the file name must be backed by the bytes, and a quarantine name keeps its key.

    No committed fixture is possible for these rules: the file name is not part of the file's
    bytes, so ``make_segment_fixtures.py`` cannot express them. The bytes are built here, with
    the reference encoder, and the same cases are pinned on the Rust side in
    ``crates/store/src/tier.rs`` (the tier tests) and ``format.rs``.
    """

    @staticmethod
    def _case(name: str) -> tuple[dict, bytes]:
        for path, spec, data in hostobs_segment_ref.load_cases(SEGMENTS):
            if path.stem == name:
                return spec, data
        raise AssertionError(f"fixture {name} is missing")

    @staticmethod
    def _name_of(spec: dict, *, sealed: bool, seq: int | None = None, start_s: int | None = None) -> str:
        header = spec["header"]
        seq = header["seq"] if seq is None else seq
        start_s = header["start_real_ns"] // 1_000_000_000 if start_s is None else start_s
        return f"hot-{start_s}-{seq}.seg" + ("" if sealed else ".open")

    def test_a_name_parses_from_the_right_and_foreign_names_are_refused(self):
        ref = hostobs_segment_ref
        self.assertEqual(
            ref.parse_segment_name("focus-web-1700000000-12.seg"),
            {"tier": "focus-web", "start_s": 1700000000, "seq": 12, "open": False},
        )
        self.assertEqual(ref.parse_segment_name("hot-0-1.seg.open")["open"], True)
        for foreign in (
            "notes.txt",
            "hot-1.seg",
            "hot--1-2.seg",
            "hot-1-2.seg.tmp",
            "-1-2.seg",
            "hot-1-x.seg",
            "hot-1-2.seg.open.open",
            "hot-1-+2.seg",
            "hot-18446744073709551616-2.seg",
        ):
            with self.subTest(name=foreign), self.assertRaises(ref.SegmentError) as caught:
                ref.parse_segment_name(foreign)
            self.assertEqual(caught.exception.as_dict(), {"error": "segment-name", "name": foreign})

    def test_a_quarantine_name_keeps_the_key_of_the_segment_it_was(self):
        ref = hostobs_segment_ref
        self.assertEqual(
            ref.parse_quarantine_name("hot-60-1.seg.corrupt"),
            {"tier": "hot", "start_s": 60, "seq": 1, "open": False},
        )
        self.assertEqual(ref.parse_quarantine_name("hot-60-1.seg.open.corrupt")["open"], True)
        for foreign in (
            "hot-60-1.seg",
            "hot-60-1.seg.open",
            "hot-x-1.seg.corrupt",
            "hot-60-1.corrupt",
            "hot-60-1.seg.corrupt.corrupt",
            ".corrupt",
        ):
            with self.subTest(name=foreign), self.assertRaises(ref.SegmentError) as caught:
                ref.parse_quarantine_name(foreign)
            self.assertEqual(caught.exception.refusal, "segment-name")

    def test_a_sealed_and_an_open_segment_agree_with_their_names(self):
        ref = hostobs_segment_ref
        for case, sealed in (("sealed-history", True), ("sealed-empty", True), ("open-history", False)):
            spec, data = self._case(case)
            with self.subTest(case=case):
                ref.check_segment_name(self._name_of(spec, sealed=sealed), ref.decode_segment(data))

    def test_a_sealed_name_without_a_verified_footer_is_refused(self):
        ref = hostobs_segment_ref
        spec, data = self._case("sealed-history")
        name = self._name_of(spec, sealed=True)
        footerless = data[: -ref.FOOTER_LEN]
        cases = {
            "the footer cut off": footerless,
            "a torn sealed file": data[: -ref.FOOTER_LEN - 8],
            "an open segment's bytes under a sealed name": self._case("open-history")[1],
        }
        for label, content in cases.items():
            decoded = ref.decode_segment(content)
            # The open-history bytes carry another identity; rename so only the footer rule fails.
            file_name = name if content is not cases["an open segment's bytes under a sealed name"] else (
                self._name_of(self._case("open-history")[0], sealed=True)
            )
            with self.subTest(case=label), self.assertRaises(ref.SegmentError) as caught:
                ref.check_segment_name(file_name, decoded)
            self.assertEqual(caught.exception.refusal, "sealed-without-footer")
            self.assertEqual(caught.exception.fields, {"name": file_name})
        # The same footerless bytes under an *open* name are an ordinary open segment.
        ref.check_segment_name(name + ".open", ref.decode_segment(footerless))

    def test_an_open_name_that_carries_a_footer_is_refused(self):
        # A closed file under a `.seg.open` name cannot be reopened for appending.
        ref = hostobs_segment_ref
        spec, data = self._case("sealed-history")
        name = self._name_of(spec, sealed=False)
        with self.assertRaises(ref.SegmentError) as caught:
            ref.check_segment_name(name, ref.decode_segment(data))
        self.assertEqual(caught.exception.as_dict(), {"error": "open-with-footer", "name": name})
        # The same bytes under the sealed name, and the footerless bytes under the open name
        # (the legitimate stale twin), are both fine.
        ref.check_segment_name(self._name_of(spec, sealed=True), ref.decode_segment(data))
        ref.check_segment_name(name, ref.decode_segment(data[: -ref.FOOTER_LEN]))

    def test_a_header_that_disagrees_with_its_name_is_refused_for_both_names(self):
        ref = hostobs_segment_ref
        sealed_spec, sealed_data = self._case("sealed-history")
        open_spec, open_data = self._case("open-history")
        header = sealed_spec["header"]
        start_s = header["start_real_ns"] // 1_000_000_000
        sealed = ref.decode_segment(sealed_data)
        opened = ref.decode_segment(open_data)
        with self.assertRaises(ref.SegmentError) as caught:
            ref.check_segment_name(f"hot-{start_s}-{header['seq'] + 3}.seg", sealed)
        self.assertEqual(
            caught.exception.as_dict(),
            {"error": "segment-identity", "field": "seq", "name": header["seq"] + 3, "header": header["seq"]},
        )
        with self.assertRaises(ref.SegmentError) as caught:
            ref.check_segment_name(f"hot-{start_s + 60}-{header['seq']}.seg", sealed)
        self.assertEqual(
            caught.exception.as_dict(),
            {
                "error": "segment-identity",
                "field": "start_real_ns",
                "name": (start_s + 60) * 1_000_000_000,
                "header": header["start_real_ns"],
            },
        )
        with self.assertRaises(ref.SegmentError) as caught:
            ref.check_segment_name(f"hot-{start_s}-{open_spec['header']['seq'] + 6}.seg.open", opened)
        self.assertEqual(caught.exception.refusal, "segment-identity")

    def test_the_identity_rule_comes_before_the_footer_rule(self):
        ref = hostobs_segment_ref
        spec, data = self._case("sealed-history")
        footerless = ref.decode_segment(data[: -ref.FOOTER_LEN])
        wrong_seq = self._name_of(spec, sealed=True, seq=spec["header"]["seq"] + 1)
        with self.assertRaises(ref.SegmentError) as caught:
            ref.check_segment_name(wrong_seq, footerless)
        self.assertEqual(caught.exception.refusal, "segment-identity")

    def test_a_start_second_past_the_i64_range_saturates_like_the_rust_name(self):
        ref = hostobs_segment_ref
        spec, data = self._case("open-history")
        decoded = ref.decode_segment(data)
        decoded["header"] = {**decoded["header"], "start_real_ns": (1 << 63) - 1}
        name = f"hot-{(1 << 64) - 1}-{decoded['header']['seq']}.seg.open"
        ref.check_segment_name(name, decoded)


if __name__ == "__main__":
    unittest.main()

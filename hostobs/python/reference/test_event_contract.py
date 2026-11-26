#!/usr/bin/env python3
"""Focused standard-library tests for the frozen stored event/1 contract."""

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
EVENT_FIXTURES = HOSTOBS / "fixtures" / "event"
WIRE_FIXTURES = HOSTOBS / "fixtures" / "wire"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hostobs_wire_ref as wire  # noqa: E402


class EventContract(unittest.TestCase):
    def minimal_event(self) -> dict:
        return json.loads((EVENT_FIXTURES / "event-minimal.json").read_text())["event"]

    def assert_refusal(self, function, expected: dict) -> None:
        with self.assertRaises(wire.WireError) as caught:
            function()
        actual = caught.exception.as_dict()
        for key, value in expected.items():
            self.assertEqual(actual.get(key), value)

    def test_event_code_tables_pin_every_name_and_unknown_boundaries(self):
        expected_tables = (
            (
                wire.EVENT_KIND_NAMES,
                wire.event_kind_name,
                {1: "begin", 2: "end", 3: "point"},
                (0, 4),
            ),
            (
                wire.EVENT_OUTCOME_NAMES,
                wire.event_outcome_name,
                {
                    1: "ok",
                    2: "fail",
                    3: "error",
                    4: "skipped",
                    5: "cancelled",
                    6: "timeout",
                    7: "abandoned",
                },
                (0, 8),
            ),
            (
                wire.EVENT_CONTEXT_KEY_NAMES,
                wire.event_context_key_name,
                {0: "run", 1: "lane", 2: "candidate", 3: "txn", 4: "session"},
                (5,),
            ),
        )
        for table, name_function, expected, outside in expected_tables:
            self.assertEqual(table, expected)
            for code, name in expected.items():
                with self.subTest(code=code, name=name):
                    self.assertEqual(name_function(code), name)
            for code in outside:
                with self.subTest(outside_code=code):
                    self.assertIsNone(name_function(code))

    def test_wire_case_loader_keeps_the_seventy_case_corpus(self):
        self.assertEqual(len(wire.load_cases(WIRE_FIXTURES)), 70)

    def test_event_refusal_fixtures_pin_kind_field_and_order_index(self):
        for path in sorted(EVENT_FIXTURES.glob("*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            expected = spec.get("expect_error")
            if expected is None:
                continue
            frame = path.with_suffix(".bin").read_bytes()
            body = wire.event_body_from_frame(frame)
            with self.subTest(case=path.stem):
                self.assertIn("field", expected)
                if expected["kind"] == "event-order":
                    self.assertIn("index", expected)
                self.assert_refusal(
                    lambda: wire.decode_event_payload(body), expected
                )

    def test_control_bytes_refuse_and_printable_edges_round_trip_in_key(self):
        base = self.minimal_event()
        valid_body = wire.encode_event_payload(base)
        _fixed, strings, _ctx_start, _attrs_start, _content_end = wire._event_layout(valid_body)

        for byte in (0x1F, 0x7F):
            expected = {"kind": "event-text", "field": "key"}
            event = dict(base, key=chr(byte))
            with self.subTest(encoded_byte=byte):
                self.assert_refusal(lambda: wire.encode_event_payload(event), expected)

            body = bytearray(valid_body)
            body[strings[1][0]] = byte
            with self.subTest(decoded_byte=byte):
                self.assert_refusal(
                    lambda: wire.decode_event_payload(bytes(body)), expected
                )

        for byte in (0x20, 0x7E):
            event = dict(base, key=chr(byte))
            body = wire.encode_event_payload(event)
            with self.subTest(accepted_byte=byte):
                self.assertEqual(wire.decode_event_payload(body), event)

    def test_encoder_refuses_del_in_an_attribute_value(self):
        event = self.minimal_event()
        event["attrs"] = [{"key": "a", "value": "\x7f"}]
        self.assert_refusal(
            lambda: wire.encode_event_payload(event),
            {"kind": "event-text", "field": "attrs.value"},
        )

    def test_encoder_and_decoder_report_bounds_before_text(self):
        event = dict(self.minimal_event(), name="\x1f", key="k" * 65)
        expected = {"kind": "event-field-bound", "field": "key"}
        self.assert_refusal(lambda: wire.encode_event_payload(event), expected)

        valid_body = wire.encode_event_payload(self.minimal_event())
        _fixed, strings, _ctx_start, _attrs_start, content_end = wire._event_layout(valid_body)
        name_span, key_span = strings[:2]
        body = bytearray(valid_body[:content_end])
        body[name_span[0]] = 0x1F
        key_length_offset = key_span[0] - 2
        body[key_length_offset : key_length_offset + 2] = struct.pack("<H", 65)
        body[key_span[1] : key_span[1]] = b"k" * 64
        body.extend(bytes(-len(body) % 8))
        self.assert_refusal(lambda: wire.decode_event_payload(bytes(body)), expected)

    def test_order_refusal_reports_the_later_entry_index_on_both_paths(self):
        ordered = self.minimal_event()
        ordered["attrs"] = [
            {"key": chr(ord("a") + index), "value": ""} for index in range(7)
        ]
        body = bytearray(wire.encode_event_payload(ordered))
        _fixed, _strings, _ctx_start, attrs_start, _content_end = wire._event_layout(body)
        offset = attrs_start
        key_spans = []
        for _entry in ordered["attrs"]:
            key_length = struct.unpack_from("<H", body, offset)[0]
            key_start = offset + 2
            key_spans.append((key_start, key_start + key_length))
            offset = key_start + key_length
            value_length = struct.unpack_from("<H", body, offset)[0]
            offset += 2 + value_length
        body[key_spans[5][0] : key_spans[5][1]] = body[
            key_spans[4][0] : key_spans[4][1]
        ]
        expected = {"kind": "event-order", "field": "attrs", "index": 5}
        self.assert_refusal(
            lambda: wire.decode_event_payload(bytes(body)), expected
        )

        unordered = dict(ordered)
        unordered["attrs"] = [dict(entry) for entry in ordered["attrs"]]
        unordered["attrs"][5]["key"] = unordered["attrs"][4]["key"]
        self.assert_refusal(lambda: wire.encode_event_payload(unordered), expected)

    def test_inferred_is_valid_only_for_end_events(self):
        end = self.minimal_event()
        end.update(kind=2, outcome=1, span=1, inferred=True)
        encoded_end = wire.encode_event_payload(end)
        self.assertEqual(wire.decode_event_payload(encoded_end), end)

        expected = {"kind": "event-unknown-code", "field": "inferred/kind", "code": 1}
        for kind, span in ((1, 1), (3, 0)):
            event = dict(self.minimal_event(), kind=kind, span=span, inferred=True)
            with self.subTest(encoder_kind=kind):
                self.assert_refusal(lambda: wire.encode_event_payload(event), expected)

            event["inferred"] = False
            body = bytearray(wire.encode_event_payload(event))
            body[42] = 1
            with self.subTest(decoder_kind=kind):
                self.assert_refusal(
                    lambda: wire.decode_event_payload(bytes(body)), expected
                )

    def test_event_frame_length_refusals_use_the_shared_section_kind(self):
        valid = (EVENT_FIXTURES / "event-minimal.bin").read_bytes()
        trailing = valid + b"\x00"
        self.assert_refusal(
            lambda: wire.event_body_from_frame(trailing),
            {"kind": "truncated", "context": "event frame"},
        )

        wrong_count = bytearray(valid)
        struct.pack_into("<H", wrong_count, 12, 2)
        self.assert_refusal(
            lambda: wire.event_body_from_frame(bytes(wrong_count)),
            {"kind": "section-length", "section": wire.EVENT_SECTION_KIND},
        )

        wrong_body_length = bytearray(valid)
        struct.pack_into("<I", wrong_body_length, 20, 1)
        self.assert_refusal(
            lambda: wire.event_body_from_frame(bytes(wrong_body_length)),
            {"kind": "section-length", "section": wire.EVENT_SECTION_KIND},
        )


if __name__ == "__main__":
    unittest.main()

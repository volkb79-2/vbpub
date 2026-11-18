#!/usr/bin/env python3
"""Tests of `hostobs_client.wire` against the 70 wire fixtures and the independent reference.

    python3 -I -m unittest discover -s python/tests -p 'test_client_wire.py' -v

Valid fixtures decode to the committed values, refusal fixtures raise the same `WireError`
kind and fields, and a seeded differential fuzz (byte flips of every valid fixture) must agree
with the reference decoder on the outcome, so no hostile frame raises anything but a typed refusal.
"""

from __future__ import annotations

import array
import json
import random
import struct
import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOSTOBS / "python"))
sys.path.insert(0, str(HOSTOBS / "python" / "reference"))

import hostobs_wire_ref as ref  # noqa: E402
from hostobs_client import wire  # noqa: E402
from hostobs_client.wire import Dictionary, WireError, decode_block  # noqa: E402

FIXTURES = HOSTOBS / "fixtures" / "wire"
RUST_ERROR = HOSTOBS / "crates" / "proto" / "src" / "error.rs"


def cases():
    out = []
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == "quality.json":
            continue
        out.append((json.loads(path.read_text(encoding="utf-8")), path.with_suffix(".bin").read_bytes()))
    return out


def dictionary_form(dictionary: Dictionary) -> dict:
    entries = []
    for entry in dictionary.entries():
        kind = ref.DICTIONARY_KINDS_BY_CODE[entry.kind]
        item = {"kind": kind}
        if kind == "entity":
            item["inc"] = entry.inc
        item["text"] = entry.text
        if kind == "metric":
            facts = entry.facts
            item["facts"] = {
                "unit": ref.UNITS_BY_CODE[facts.unit],
                "kind": ref.COLUMN_KINDS_BY_CODE[facts.kind],
                "scope": ref.SCOPES_BY_CODE[facts.scope],
                "source": ref.SOURCES_BY_CODE[facts.source],
            }
        elif kind == "label":
            item["label_kind"] = entry.label_kind
            item["label_source"] = entry.label_source
        entries.append(item)
    return {"generation": dictionary.generation, "entries": entries}


def fixture_form(block) -> dict:
    h = block.header
    names = []
    for kind in block.directory_kinds:
        name = ref.SECTION_KINDS_BY_CODE.get(kind)
        if name is not None and name not in names:
            names.append(name)
    columns = []
    for column in block.columns():
        bits = bytes(memoryview(column.raw_values).cast("B"))
        item = {
            "metric": column.metric,
            "type": wire.VALUE_TYPE_NAMES[column.value_type],
            "kind": wire.KIND_NAMES[column.kind],
        }
        words = struct.unpack(f"<{column.n_rows}Q", bits)
        if column.value_type == 3:
            item["value_bits"] = [f"0x{w:016x}" for w in words]
        else:
            item["values"] = [str(v) for v in column.raw_values]
        item["quality"] = [column.quality_name(i) for i in range(column.n_rows)]
        columns.append(item)
    rows = []
    if block.rows is not None:
        rows = [
            {"ent": e, "t_off_us": t} for e, t in zip(block.rows.ents(), block.rows.t_off_us(), strict=True)
        ]
    time = []
    if block.time is not None:
        t = block.time
        time = [
            {"real_ns": r, "covered_ms": c, "samples": s, "agg": ref.AGGREGATIONS_BY_CODE[a]}
            for r, c, s, a in zip(t.real_ns(), t.covered_ms(), t.samples(), t.agg(), strict=True)
        ]
    frame = {
        "type": "block",
        "corr": block.corr,
        "sections": names,
        "header": {
            "boot_id": wire.boot_id_text(h.boot_id),
            "epoch": h.epoch,
            "seq": h.seq,
            "mono_start_ns": h.mono_start_ns,
            "mono_end_ns": h.mono_end_ns,
            "boot_ns": h.boot_ns,
            "real_ns": h.real_ns,
            "dict_gen": h.dict_gen,
            "n_rows": h.n_rows,
            "dropped": h.dropped,
            "flags": h.flags,
        },
        "rows": rows,
        "time": time,
        "columns": columns,
        "skipped": list(block.skipped),
    }
    if block.subject is not None:
        frame["subject"] = block.subject
    return frame


def held_of(spec):
    held = spec.get("held")
    return None if held is None else Dictionary.decode(ref.encode_dictionary(held))


class FixtureTests(unittest.TestCase):
    def test_seventy_cases_eleven_valid(self):
        loaded = cases()
        self.assertEqual(len(loaded), 70)
        self.assertEqual(sum(1 for spec, _ in loaded if "expect_error" not in spec), 11)

    def test_every_valid_case_decodes_to_the_committed_values(self):
        for spec, data in cases():
            if "expect_error" in spec:
                continue
            block = decode_block(bytearray(data), held_of(spec))
            expected = ref.normalise(spec["frame"])
            self.assertEqual(fixture_form(block), expected, spec["case"])
            if "dictionary" in spec["frame"]:
                self.assertEqual(dictionary_form(block.dictionary), spec["frame"]["dictionary"], spec["case"])

    def test_every_refusal_case_raises_the_same_kind_and_fields(self):
        for spec, data in cases():
            expect = spec.get("expect_error")
            if expect is None:
                continue
            with self.assertRaises(WireError, msg=spec["case"]) as caught:
                decode_block(bytearray(data), held_of(spec))
            error = caught.exception
            self.assertEqual(error.kind, expect["kind"], spec["case"])
            for key, value in expect.items():
                if key != "kind":
                    self.assertEqual(error.detail.get(key), value, f"{spec['case']}: {key}")

    def test_the_error_kinds_are_the_46_of_the_rust_decoder(self):
        self.assertEqual(len(wire.ERROR_KINDS), 46)
        if RUST_ERROR.exists():
            text = RUST_ERROR.read_text(encoding="utf-8")
            body = text[text.index("pub const fn kind") :]
            body = body[: body.index("#[cfg(test)]")]
            rust = {part.split('"')[1] for part in body.splitlines() if "=> " in part and '"' in part}
            self.assertEqual(set(wire.ERROR_KINDS), rust)

    def test_a_valid_fixture_decodes_from_bytes_and_memoryview_too(self):
        spec, data = next((s, d) for s, d in cases() if s["case"] == "live-block")
        for buf in (bytes(data), memoryview(bytearray(data))):
            self.assertEqual(fixture_form(decode_block(buf)), ref.normalise(spec["frame"]))


class DifferentialFuzz(unittest.TestCase):
    def test_byte_flips_agree_with_the_reference(self):
        rng = random.Random(20261118)
        checked = refused = 0
        for spec, data in cases():
            if "expect_error" in spec:
                continue
            held = spec.get("held")
            for _ in range(250):
                mutant = bytearray(data)
                for _ in range(rng.choice((1, 1, 2, 3))):
                    mutant[rng.randrange(len(mutant))] = rng.randrange(256)
                if rng.random() < 0.15:
                    del mutant[rng.randrange(len(mutant)) :]
                try:
                    expected = ref.decode_frame(bytes(mutant), held)
                    expected_error = None
                except ref.WireError as error:
                    expected, expected_error = None, error.as_dict()
                try:
                    block = decode_block(bytearray(mutant), held_of(spec))
                    actual, actual_error = block, None
                except WireError as error:
                    actual, actual_error = None, {"kind": error.kind, **error.detail}
                checked += 1
                if expected_error is None:
                    self.assertIsNone(actual_error, (spec["case"], bytes(mutant).hex()))
                    self.assertEqual(fixture_form(actual), ref.normalise(expected), spec["case"])
                else:
                    refused += 1
                    self.assertEqual(actual_error, expected_error, (spec["case"], bytes(mutant).hex()))
        self.assertGreater(refused, checked // 4)


class ColumnApi(unittest.TestCase):
    def setUp(self):
        spec, data = next((s, d) for s, d in cases() if s["case"] == "live-block")
        self.block = decode_block(bytearray(data))

    def test_counters_stay_exact_integers(self):
        column = self.block.column("cpu.usage_usec")
        self.assertEqual(list(column.raw_values), [1000, 2**53 + 1, 2**64 - 1])
        self.assertEqual(column.measured(1), 2**53 + 1)
        self.assertIsInstance(column.measured(2), int)
        self.assertEqual(column.measured_list(), [1000, 2**53 + 1, 2**64 - 1])

    def test_a_non_ok_row_is_none_and_never_its_placeholder(self):
        column = self.block.column("mem.current")
        self.assertEqual(column.quality_name(2), "absent")
        self.assertIsNone(column.measured(2))
        self.assertEqual(column.measured(0), 100.0)
        listed = column.measured_list()
        self.assertEqual(listed[0], 100.0)
        self.assertEqual(str(listed[1]), "-0.0")
        self.assertIsNone(listed[2])
        self.assertEqual(list(column.quality), [0, 0, 1])

    def test_columns_are_lazy_and_named(self):
        iterator = self.block.columns()
        self.assertIs(iter(iterator), iterator)
        self.assertEqual([c.name for c in iterator], ["cpu.usage_usec", "mem.current"])

    def test_unknown_metric_is_a_key_error_and_rows_give_views(self):
        with self.assertRaises(KeyError):
            self.block.column("nope")
        self.assertEqual(list(self.block.rows.ents()), [0, 1, 0])
        self.assertEqual(list(self.block.rows.t_off_us()), [0, 20000, 40000])
        self.assertEqual(self.block.header.dropped, 1)
        self.assertEqual(self.block.corr, 99)

    def test_a_dictionary_less_block_has_no_names(self):
        spec, data = next((s, d) for s, d in cases() if s["case"] == "bare-block-held-dict")
        block = decode_block(bytearray(data))
        self.assertIsNone(block.dictionary_in_force)
        with self.assertRaises(LookupError):
            block.column("x")
        self.assertEqual(sum(1 for _ in block.columns()), len(spec["frame"]["columns"]))


class BigEndianPath(unittest.TestCase):
    def test_the_copy_path_gives_the_same_values_as_the_cast_path(self):
        spec, data = next((s, d) for s, d in cases() if s["case"] == "live-block")
        native = decode_block(bytearray(data))
        old = wire._NATIVE_VIEWS
        wire._NATIVE_VIEWS = False
        try:
            copied = decode_block(bytearray(data))
            column = copied.column("cpu.usage_usec")
            self.assertIsInstance(column.raw_values, array.array)
            self.assertEqual(fixture_form(copied), fixture_form(native))
        finally:
            wire._NATIVE_VIEWS = old

    def test_a_big_endian_host_byteswaps_each_array_once(self):
        old_native, old_order = wire._NATIVE_VIEWS, sys.byteorder
        spec, data = next((s, d) for s, d in cases() if s["case"] == "live-block")
        block = decode_block(bytearray(data))
        wire._NATIVE_VIEWS = False
        sys.byteorder = "big"
        try:
            swapped = list(block.rows.ents())
        finally:
            sys.byteorder = old_order
            wire._NATIVE_VIEWS = old_native
        plain = array.array("I", list(decode_block(bytearray(data)).rows.ents()))
        plain.byteswap()
        self.assertEqual(swapped, list(plain))


class RetentionAndReuse(unittest.TestCase):
    def setUp(self):
        spec, data = next((s, d) for s, d in cases() if s["case"] == "live-block")
        self.data = data
        self.held = Dictionary.decode(ref.encode_dictionary(spec["frame"]["dictionary"]))

    def test_validated_rows_are_retained_then_released_on_an_error(self):
        bare = bytearray(self.data)
        # a bare variant: decode the dictionary-bearing block, then keep validating against it
        first = decode_block(bare, self.held)
        self.assertIs(first.dictionary, self.held, "an identical dictionary body reuses the held one")
        self.assertIsNotNone(self.held._rows_ok)
        with self.assertRaises(WireError):
            decode_block(bytearray(self.data[:-8]), self.held)
        self.assertIsNone(self.held._rows_ok)

    def test_a_frame_above_one_mib_is_not_retained(self):
        self.held._rows_ok = None
        old = wire.RETAIN_LIMIT
        wire.RETAIN_LIMIT = 8
        try:
            decode_block(bytearray(self.data), self.held)
        finally:
            wire.RETAIN_LIMIT = old
        self.assertIsNone(self.held._rows_ok)

    def test_a_changed_dictionary_body_decodes_a_new_dictionary(self):
        other = Dictionary.decode(ref.encode_dictionary({"generation": 9, "entries": []}))
        block = decode_block(bytearray(self.data), other)
        self.assertIsNot(block.dictionary, other)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Generate the shared hostobs/1 wire fixtures (plan v4 §B.5).

The fixtures are committed artefacts and this script is the reproducible way to
regenerate them: ``python3 python/reference/make_fixtures.py``. It uses only the
reference codec in this directory, so the bytes in ``fixtures/wire/`` are the
independent Python implementation's output, not the Rust encoder's — which is
what makes the Rust tests that consume them a cross-language check.

Case kinds:

* a **valid** case: ``frame`` is the decoded description, the reference encoder
  must reproduce the committed ``.bin``, and the reference decoder must
  reproduce ``frame``;
* a **refusal** case: ``frame`` is the valid frame the bytes were derived from
  and ``corrupt`` lists the edits applied afterwards; ``expect_error`` is the
  refusal a conforming decoder must produce. Rust tests assert the same refusal
  kind, so the error vocabulary is shared too.

Never edit ``.bin`` by hand: change the description here and regenerate.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = sys.argv[1] if len(sys.argv) > 1 else str(REPO / "fixtures" / "wire")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostobs_wire_ref as wire  # noqa: E402

BOOT_ID = "00112233-4455-6677-8899-aabbccddeeff"
OTHER_BOOT = "ffeeddcc-bbaa-9988-7766-554433221100"


def header(n_rows: int, dict_gen: int = 1, epoch: int = 7, seq: int = 42, boot: str = BOOT_ID) -> dict:
    return {
        "boot_id": boot,
        "epoch": epoch,
        "seq": seq,
        "mono_start_ns": 1_000_000_000,
        "mono_end_ns": 2_000_000_000,
        "boot_ns": 500,
        "real_ns": 1_700_000_000_000_000_000,
        "dict_gen": dict_gen,
        "n_rows": n_rows,
        "dropped": 1,
        "flags": 0,
    }


METRIC_FACTS = {
    "cpu_usage": {
        "unit": "usec",
        "kind": "counter",
        "scope": "cgroup",
        "source": "cgroupfs",
    },
    "mem_current": {
        "unit": "bytes",
        "kind": "gauge",
        "scope": "cgroup",
        "source": "cgroupfs",
    },
    "signed_gauge": {
        "unit": "none",
        "kind": "gauge",
        "scope": "daemon",
        "source": "probe",
    },
    "psi_total": {
        "unit": "usec",
        "kind": "counter",
        "scope": "host",
        "source": "procfs",
    },
    "ksm_state": {
        "unit": "none",
        "kind": "state",
        "scope": "host",
        "source": "sysfs",
    },
}


def dictionary() -> dict:
    return {
        "generation": 1,
        "entries": [
            {"kind": "entity", "inc": 11, "text": "/system.slice"},
            {"kind": "entity", "inc": 12, "text": "/docker/abc"},
            {"kind": "metric", "text": "cpu.usage_usec", "facts": METRIC_FACTS["cpu_usage"]},
            {"kind": "metric", "text": "mem.current", "facts": METRIC_FACTS["mem_current"]},
        ],
    }


def live_frame() -> dict:
    return {
        "type": "block",
        "corr": 99,
        "sections": ["header", "dictionary", "rows", "columns"],
        "header": header(3, dict_gen=1),
        "frame_corr": 99,
        "dictionary": dictionary(),
        "rows": [
            {"ent": 0, "t_off_us": 0},
            {"ent": 1, "t_off_us": 20_000},
            {"ent": 0, "t_off_us": 40_000},
        ],
        "time": [],
        "columns": [
            {
                "metric": 2,
                "type": "u64",
                "kind": "counter",
                "values": ["1000", str(2**53 + 1), str(2**64 - 1)],
                "quality": ["ok", "ok", "ok"],
            },
            {
                "metric": 3,
                "type": "f64",
                "kind": "gauge",
                "value_bits": ["0x4059000000000000", "0x8000000000000000", "0x7ff8000000000000"],
                "quality": ["ok", "ok", "absent"],
            },
        ],
        "skipped": [],
    }


def u64_boundary_frame() -> dict:
    """Exactly the u64 boundaries plan v4 §B.5 asks for, as three rows."""
    return {
        "type": "block",
        "corr": 7,
        "sections": ["header", "dictionary", "rows", "columns"],
        "header": header(3, dict_gen=1),
        "dictionary": {
            "generation": 1,
            "entries": [
                {"kind": "entity", "inc": 1, "text": "/a"},
                {"kind": "entity", "inc": 2, "text": "/b"},
                {"kind": "entity", "inc": 3, "text": "/c"},
                {
                    "kind": "metric",
                    "text": "cpu.usage_usec",
                    "facts": METRIC_FACTS["cpu_usage"],
                },
                {
                    "kind": "metric",
                    "text": "signed.value",
                    "facts": METRIC_FACTS["signed_gauge"],
                },
            ],
        },
        "rows": [
            {"ent": 0, "t_off_us": 0},
            {"ent": 1, "t_off_us": 1},
            {"ent": 2, "t_off_us": 2},
        ],
        "time": [],
        "columns": [
            {
                "metric": 3,
                "type": "u64",
                "kind": "counter",
                "values": ["0", str(2**53 + 1), str(2**64 - 1)],
                "quality": ["ok", "ok", "ok"],
            },
            {
                "metric": 4,
                "type": "i64",
                "kind": "gauge",
                "values": [str(-(2**63)), "-1", str(2**63 - 1)],
                "quality": ["ok", "reset", "ok"],
            },
        ],
        "skipped": [],
    }


def history_frame() -> dict:
    return {
        "type": "block",
        "corr": 3,
        "sections": ["header", "dictionary", "time", "columns"],
        "header": header(3, dict_gen=2, epoch=8, seq=1),
        "dictionary": {
            "generation": 2,
            "entries": [
                {"kind": "entity", "inc": 11, "text": "/system.slice"},
                {
                    "kind": "metric",
                    "text": "mem.current",
                    "facts": METRIC_FACTS["mem_current"],
                },
                {
                    "kind": "metric",
                    "text": "psi.io.some.total",
                    "facts": METRIC_FACTS["psi_total"],
                },
            ],
        },
        "rows": [],
        "time": [
            {"real_ns": 1_700_000_000_000_000_000, "covered_ms": 1000, "samples": 5, "agg": "mean"},
            {"real_ns": -1, "covered_ms": 0, "samples": 0, "agg": "raw"},
            {"real_ns": 1_700_000_000_120_000_000, "covered_ms": 640, "samples": 3, "agg": "max"},
        ],
        "columns": [
            {
                "metric": 1,
                "type": "u64",
                "kind": "gauge",
                "values": ["2048", "0", str(2**64 - 1)],
                "quality": ["ok", "stale", "not-sampled"],
            },
            {
                "metric": 2,
                "type": "u64",
                "kind": "counter",
                "values": ["9000", "0", "1"],
                "quality": ["ok", "absent", "reset"],
            },
        ],
        "skipped": [],
    }


def unicode_dictionary_frame() -> dict:
    return {
        "type": "block",
        "corr": 12,
        "sections": ["header", "dictionary", "rows", "columns"],
        "header": header(1, dict_gen=1, epoch=2, seq=5, boot=OTHER_BOOT),
        "dictionary": {
            "generation": 1,
            "entries": [
                {"kind": "entity", "inc": 4, "text": "/caf\u00e9/na\u00efve"},
                {"kind": "label", "label_kind": 1, "label_source": 1, "text": "web-\u00e9"},
                {"kind": "free", "text": "lane: run-\u00fc"},
                {"kind": "metric", "text": "mem.current", "facts": METRIC_FACTS["mem_current"]},
                {"kind": "metric", "text": "ksm.run", "facts": METRIC_FACTS["ksm_state"]},
            ],
        },
        "rows": [{"ent": 0, "t_off_us": 0}],
        "time": [],
        "columns": [
            {
                "metric": 3,
                "type": "f64",
                "kind": "gauge",
                "value_bits": ["0x40091eb851eb851f"],
                "quality": ["ok"],
            },
        ],
        "skipped": [],
    }


def all_qualities_frame() -> dict:
    return {
        "type": "block",
        "corr": 5,
        "sections": ["header", "dictionary", "rows", "columns"],
        "header": header(8, dict_gen=1),
        "dictionary": {
            "generation": 1,
            "entries": [
                {"kind": "metric", "text": "mem.current", "facts": METRIC_FACTS["mem_current"]},
                {"kind": "metric", "text": "cpu.usage_usec", "facts": METRIC_FACTS["cpu_usage"]},
            ]
            + [{"kind": "entity", "inc": index + 1, "text": f"/cg{index}"} for index in range(8)],
        },
        "rows": [{"ent": index + 2, "t_off_us": index * 125_000} for index in range(8)],
        "time": [],
        "columns": [
            {
                "metric": 0,
                "type": "u64",
                "kind": "gauge",
                "values": ["0"] * 8,
                "quality": list(wire.QUALITIES),
            },
            {
                "metric": 1,
                "type": "u64",
                "kind": "counter",
                "values": ["7"] * 8,
                "quality": list(wire.QUALITIES),
            },
        ],
        "skipped": [],
    }


def empty_frame() -> dict:
    return {
        "type": "block",
        "corr": 0,
        "sections": ["header", "dictionary"],
        "header": header(0, dict_gen=1),
        "dictionary": {"generation": 1, "entries": []},
        "rows": [],
        "time": [],
        "columns": [],
        "skipped": [],
    }


def unknown_optional_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 21
    frame["unknown_sections"] = [{"kind_code": 9, "version": 1, "flags": 0, "body_hex": "00" * 8}]
    frame["skipped"] = [9]
    return frame


def unknown_required_frame() -> dict:
    frame = live_frame()
    frame["unknown_sections"] = [{"kind_code": 9, "version": 1, "flags": 1, "body_hex": "00" * 8}]
    return frame


def bad_quality_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 8
    return frame


def dangling_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 9
    return frame


def generation_mismatch_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 10
    return frame


def future_version_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 11
    return frame


def oversized_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 13
    return frame


def reserved_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 14
    return frame


def truncation_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 15
    return frame


def column_reserved_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 16
    return frame


def duplicate_dictionary_frame() -> dict:
    """Two entries whose identity is the same incarnation: the wire index would shift."""
    frame = live_frame()
    frame["corr"] = 30
    frame["dictionary"] = {
        "generation": 1,
        "entries": [
            {"kind": "entity", "inc": 11, "text": "/system.slice"},
            {"kind": "entity", "inc": 11, "text": "/renamed"},
            {"kind": "metric", "text": "cpu.usage_usec", "facts": METRIC_FACTS["cpu_usage"]},
            {"kind": "metric", "text": "mem.current", "facts": METRIC_FACTS["mem_current"]},
        ],
    }
    return frame


def bad_metric_mask_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 31
    return frame


def row_references_metric_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 32
    return frame


def column_references_entity_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 33
    return frame


def column_kind_mismatch_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 34
    return frame


def no_rows_or_time_frame() -> dict:
    """Three declared rows but neither a rows nor a time section."""
    frame = live_frame()
    frame["corr"] = 35
    frame["sections"] = ["header", "dictionary", "columns"]
    frame["rows"] = []
    return frame


def empty_rows_and_time_frame() -> dict:
    """n_rows == 0 with BOTH sections present (and empty): exclusivity is by presence."""
    frame = empty_frame()
    frame["corr"] = 36
    frame["sections"] = ["header", "dictionary", "rows", "time"]
    return frame


def patch(data: bytes, offset: int, replacement: bytes) -> bytes:
    return data[:offset] + replacement + data[offset + len(replacement) :]


def directory_offset(data: bytes, index: int) -> int:
    return wire.FRAME_HEADER_LEN + 4 + index * wire.SECTION_DIR_ENTRY_LEN


def body_offset(data: bytes, index: int) -> int:
    n_sec = struct.unpack_from("<H", data, wire.FRAME_HEADER_LEN)[0]
    start = wire.FRAME_HEADER_LEN + 4 + n_sec * wire.SECTION_DIR_ENTRY_LEN
    for position in range(index):
        start += struct.unpack_from("<I", data, directory_offset(data, position) + 4)[0]
    return start


def dictionary_entry_offset(data: bytes, entry_index: int) -> int:
    """The body offset of dictionary entry `entry_index` (its kind byte)."""
    start = body_offset(data, section_index(data, "dictionary")) + 8
    offset = start
    for index in range(entry_index):
        kind_code, _flags, length = struct.unpack_from("<BBH", data, offset)
        fixed = {0: 8, 1: 6, 2: 2, 3: 0}[kind_code]
        offset += 4 + fixed + length
    return offset


def section_index(data: bytes, kind_name: str) -> int:
    kind = wire.SECTION_KINDS[kind_name]
    n_sec = struct.unpack_from("<H", data, wire.FRAME_HEADER_LEN)[0]
    for index in range(n_sec):
        if struct.unpack_from("<H", data, directory_offset(data, index))[0] == kind:
            return index
    raise SystemExit(f"the frame has no {kind_name} section")


CASES: list[tuple[str, dict, list[tuple[int, bytes]] | None, dict | None]] = [
    ("live-block", live_frame(), None, None),
    ("u64-boundaries", u64_boundary_frame(), None, None),
    ("history-buckets", history_frame(), None, None),
    ("unicode-dictionary", unicode_dictionary_frame(), None, None),
    ("all-quality-codes", all_qualities_frame(), None, None),
    ("empty-selection", empty_frame(), None, None),
    ("unknown-optional-section", unknown_optional_frame(), None, None),
    (
        "required-section-refused",
        unknown_required_frame(),
        None,
        {"kind": "unsupported-required-section", "kind_code": 9},
    ),
    (
        "bad-quality-code",
        bad_quality_frame(),
        "bad-quality",
        {"kind": "unknown-quality", "row": 0, "code": 8},
    ),
    (
        "dangling-dictionary-ref",
        dangling_frame(),
        "dangling",
        {"kind": "dangling-dictionary-ref", "index": 99, "entries": 4},
    ),
    (
        "generation-mismatch",
        generation_mismatch_frame(),
        "generation",
        {"kind": "stale-dictionary-generation", "needed": 2, "held": 1},
    ),
    (
        "future-section-version",
        future_version_frame(),
        "future-version",
        {"kind": "unsupported-section-version", "kind_code": 3, "version": 2, "supported": 1},
    ),
    (
        "oversized-frame",
        oversized_frame(),
        "oversized",
        {"kind": "oversized-frame", "len": wire.MAX_FRAME_BYTES + 1, "max": wire.MAX_FRAME_BYTES},
    ),
    (
        "truncated-frame",
        truncation_frame(),
        "truncate",
        {"kind": "truncated", "context": "frame"},
    ),
    (
        "non-zero-reserved",
        reserved_frame(),
        "reserved",
        {"kind": "non-zero-reserved", "value": 1},
    ),
    (
        "column-reserved",
        column_reserved_frame(),
        "column-reserved",
        {"kind": "column-reserved", "metric": 2, "pad": 1},
    ),
    (
        "duplicate-dictionary-entry",
        duplicate_dictionary_frame(),
        None,
        {"kind": "duplicate-dictionary-entry", "index": 1},
    ),
    (
        "bad-metric-mask",
        bad_metric_mask_frame(),
        "bad-metric-mask",
        {"kind": "dictionary-entry-mask", "index": 2, "mask": 0, "expected": 7},
    ),
    (
        "row-references-metric",
        row_references_metric_frame(),
        "row-references-metric",
        {"kind": "row-reference-not-entity", "index": 2},
    ),
    (
        "column-references-entity",
        column_references_entity_frame(),
        "column-references-entity",
        {"kind": "column-reference-not-metric", "index": 0},
    ),
    (
        "column-kind-mismatch",
        column_kind_mismatch_frame(),
        "column-kind-mismatch",
        {"kind": "column-kind-mismatch", "index": 2, "declared": 0, "dictionary": 1},
    ),
    (
        "no-rows-or-time",
        no_rows_or_time_frame(),
        None,
        {"kind": "no-rows-or-time", "n_rows": 3},
    ),
    (
        "empty-rows-and-time",
        empty_rows_and_time_frame(),
        None,
        {"kind": "rows-and-time", "rows": 0, "buckets": 0},
    ),
]


def build(name: str, frame: dict, edits: object) -> tuple[bytes, dict | None]:
    """Return the committed bytes and the expected refusal (if any)."""
    data = wire.encode_frame(frame)
    if edits == "bad-quality":
        columns = section_index(data, "columns")
        # The first column's quality byte 0 is at header(8) + n_rows * 8.
        offset = body_offset(data, columns) + 8 + 3 * 8
        return patch(data, offset, b"\x08"), {"kind": "unknown-quality", "row": 0, "code": 8}
    if edits == "dangling":
        rows = section_index(data, "rows")
        return patch(data, body_offset(data, rows), b"\x63\x00\x00\x00"), {
            "kind": "dangling-dictionary-ref",
            "index": 99,
            "len": 4,
        }
    if edits == "generation":
        rows = section_index(data, "rows")
        data = patch(data, body_offset(data, rows), b"\x63\x00\x00\x00")
        header_index = section_index(data, "header")
        offset = body_offset(data, header_index) + 16 + 5 * 8 + 8
        return patch(data, offset, b"\x02\x00\x00\x00"), {"kind": "stale-dictionary-generation", "needed": 2, "held": 1}
    if edits == "future-version":
        columns = section_index(data, "columns")
        return patch(data, directory_offset(data, columns) + 2, b"\x02"), {
            "kind": "unsupported-section-version",
            "kind_code": 3,
            "version": 2,
            "supported": 1,
        }
    if edits == "oversized":
        return patch(data, 0, struct.pack("<I", wire.MAX_FRAME_BYTES + 1)), {
            "kind": "oversized-frame",
            "len": wire.MAX_FRAME_BYTES + 1,
            "max": wire.MAX_FRAME_BYTES,
        }
    if edits == "truncate":
        return data[:-8], {"kind": "truncated", "context": "frame", "need": len(data), "have": len(data) - 8}
    if edits == "reserved":
        return patch(data, 6, b"\x01\x00"), {"kind": "non-zero-reserved", "value": 1}
    if edits == "column-reserved":
        columns = section_index(data, "columns")
        return patch(data, body_offset(data, columns) + 6, b"\x01\x00"), {
            "kind": "column-reserved",
            "metric": 2,
            "pad": 1,
        }
    if edits == "bad-metric-mask":
        # Entry 2 is the cpu.usage_usec metric; its mask field follows the four code bytes.
        kind_at = dictionary_entry_offset(data, 2)
        mask_at = kind_at + 4 + 4
        return patch(data, mask_at, struct.pack("<H", 0)), {
            "kind": "dictionary-entry-mask",
            "index": 2,
            "mask": 0,
            "expected": wire.AGGREGATION_MASKS["counter"],
        }
    if edits == "row-references-metric":
        rows = section_index(data, "rows")
        return patch(data, body_offset(data, rows), struct.pack("<I", 2)), {
            "kind": "row-reference-not-entity",
            "index": 2,
        }
    if edits == "column-references-entity":
        columns = section_index(data, "columns")
        return patch(data, body_offset(data, columns), struct.pack("<I", 0)), {
            "kind": "column-reference-not-metric",
            "index": 0,
        }
    if edits == "column-kind-mismatch":
        columns = section_index(data, "columns")
        return patch(data, body_offset(data, columns) + 5, b"\x00"), {
            "kind": "column-kind-mismatch",
            "index": 2,
            "declared": 0,
            "dictionary": 1,
        }
    if edits is None:
        return data, None
    raise SystemExit(f"unknown edit {edits!r} for case {name}")


def main() -> int:
    out = Path(OUT)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, frame, edits, expect in CASES:
        data, refusal = build(name, frame, edits)
        if expect is not None:
            refusal = expect
        document = {
            "case": name,
            "doc": DOC.get(name, ""),
            "frame": {key: value for key, value in frame.items() if key != "frame_corr"},
        }
        if refusal is not None:
            document["expect_error"] = refusal
        spec_path = out / f"{name}.json"
        bin_path = out / f"{name}.bin"
        spec_path.write_text(json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")
        bin_path.write_bytes(data)
        written.append((name, len(data), refusal))
    vocabulary = {
        "case": "quality-vocabulary",
        "doc": "the closed quality vocabulary of plan v4 C.2; codes are append-only, names are the fixture names",
        "codes": [
            {"code": index, "name": name, "measurement": name == "ok"} for index, name in enumerate(wire.QUALITIES)
        ],
    }
    (out / "quality.json").write_text(
        json.dumps(vocabulary, indent=2) + "\n", encoding="utf-8"
    )
    for name, size, refusal in written:
        detail = "refusal" if refusal else "valid"
        print(f"{name}.bin: {size} bytes ({detail})")
    print(f"wrote {len(written)} fixtures plus quality.json into {out}")
    return 0


DOC = {
    "live-block": (
        "A live block: two incarnations over three rows, a raw u64 counter column and an f64 gauge column. "
        "The counter rows are 1000, 2^53+1 and 2^64-1: an f64 would lose the last two. "
        "The gauge's second row is -0.0 and its third is a NaN placeholder with quality `absent`, "
        "which must never be read as a value."
    ),
    "u64-boundaries": (
        "The u64/i64 boundaries of plan v4 B.5 in one block: 0, 2^53+1, 2^64-1, and i64 -2^63, -1, 2^63-1."
    ),
    "history-buckets": (
        "A history block: `time` replaces `rows`, with a real timestamp, coverage, sample count and "
        "aggregation per bucket, including a negative real_ns and an empty bucket (0 samples)."
    ),
    "unicode-dictionary": (
        "Unicode dictionary text on all four entry kinds, plus a second boot id."
    ),
    "all-quality-codes": (
        "Every quality code of the closed vocabulary on eight rows; the values are zero, so a reader "
        "that ignores the quality column cannot tell the eight apart."
    ),
    "empty-selection": (
        "The smallest legal block: zero rows, an empty dictionary generation, no columns."
    ),
    "unknown-optional-section": (
        "A valid block plus an unknown section without the required flag: a conforming decoder skips it "
        "by its declared length and reports it as skipped."
    ),
    "required-section-refused": (
        "A valid block plus an unknown section WITH the required flag: a conforming decoder refuses the "
        "frame instead of silently dropping data it was promised."
    ),
    "bad-quality-code": "A quality byte outside the closed vocabulary (code 8).",
    "dangling-dictionary-ref": "A row referencing dictionary index 99 in a four-entry generation.",
    "generation-mismatch": "The block declares dictionary generation 2 but carries generation 1.",
    "future-section-version": "A known section kind carrying version 2.",
    "oversized-frame": "A frame header declaring more than the 16 MiB maximum.",
    "truncated-frame": "A valid frame with its last eight body bytes removed.",
    "non-zero-reserved": "A frame header whose reserved u16 is not zero.",
    "column-reserved": "A column whose reserved u16 padding field is not zero.",
    "duplicate-dictionary-entry": (
        "Two dictionary entries with the same incarnation identity. A decoder that "
        "de-duplicated them would shift every later index away from the sender's numbering."
    ),
    "bad-metric-mask": (
        "A metric entry whose aggregation mask is a strict subset (0) of the mask its kind "
        "derives; a decoder that kept it would widen it again on re-encode."
    ),
    "row-references-metric": "A row referencing dictionary index 2, which is a metric entry, not an incarnation.",
    "column-references-entity": "A column referencing dictionary index 0, which is an incarnation entry, not a metric.",
    "column-kind-mismatch": (
        "A column declaring kind `gauge` for a metric the dictionary declares `counter`."
    ),
    "no-rows-or-time": "A block declaring three rows but carrying neither a rows nor a time section.",
    "empty-rows-and-time": (
        "A block with zero rows that carries BOTH the rows and the time section (each empty): "
        "rows and buckets are exclusive by presence, not by count."
    ),
}


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate the shared hostobs/1 BLOCK fixtures (plan v4 §B.5).

The fixtures are committed artefacts and this script is the reproducible way to
regenerate them: ``python3 python/reference/make_fixtures.py``. It uses only the
reference codec in this directory, so the bytes in ``fixtures/wire/`` are the
independent Python implementation's output, not the Rust encoder's — which is
what makes the Rust tests that consume them a cross-language check. Stored
event fixtures are written separately by ``make_event_fixtures.py``.

Case kinds:

* a **valid** case: ``frame`` is the decoded description, the reference encoder
  must reproduce the committed ``.bin``, and the reference decoder must
  reproduce ``frame``;
* a **refusal** case: ``frame`` is the valid frame the bytes were derived from
  and ``corrupt`` lists the edits applied afterwards; ``expect_error`` is the
  refusal a conforming decoder must produce. Rust tests assert the same refusal
  kind, so the error vocabulary is shared too.

A case with a top-level ``held`` key (``{"generation", "entries"}``, the same entry form as
``frame.dictionary``) is decoded against that held dictionary (``decode_frame(data, held)``,
Rust ``decode_block_with``); every other case is decoded with no held dictionary.

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
            {"real_ns": 1_700_000_000_000_000_000, "covered_ms": 1000, "samples": 5, "agg": "last"},
            {"real_ns": -1, "covered_ms": 0, "samples": 0, "agg": "last"},
            {"real_ns": 1_700_000_000_120_000_000, "covered_ms": 640, "samples": 3, "agg": "last"},
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


def history_five_frame() -> dict:
    """A history block with five buckets (HOB-48, length half).

    The time section carries 15 bytes per bucket, so five buckets are 75 bytes padded to 80. A
    factor of 14 reproduces the old answer only up to three buckets (42 -> 48), which is why the
    first history fixture could not catch it.
    """
    frame = history_frame()
    frame["corr"] = 5
    frame["header"] = header(5, dict_gen=2, epoch=8, seq=2)
    frame["time"] = [
        {"real_ns": 1_700_000_000_000_000_000, "covered_ms": 1000, "samples": 5, "agg": "last"},
        {"real_ns": 1_700_000_001_000_000_000, "covered_ms": 1000, "samples": 4, "agg": "last"},
        {"real_ns": 1_700_000_002_000_000_000, "covered_ms": 999, "samples": 65535, "agg": "last"},
        {"real_ns": -1, "covered_ms": 0, "samples": 0, "agg": "last"},
        {"real_ns": 1_700_000_004_000_000_000, "covered_ms": 4_294_967_295, "samples": 1, "agg": "last"},
    ]
    frame["columns"] = [
        {
            "metric": 1,
            "type": "u64",
            "kind": "gauge",
            "values": ["2048", "0", str(2**64 - 1), "7", "1"],
            "quality": ["ok", "stale", "not-sampled", "ok", "ok"],
        },
        {
            "metric": 2,
            "type": "u64",
            "kind": "counter",
            "values": ["9000", "0", "1", "9001", "9002"],
            "quality": ["ok", "absent", "reset", "ok", "ok"],
        },
    ]
    return frame


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


def below_header_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 21
    return frame


def header_only_frame() -> dict:
    frame = live_frame()
    frame["corr"] = 22
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


def block_as_req_frame() -> dict:
    """A canonical block whose header declares the REQ frame type."""
    frame = live_frame()
    frame["corr"] = 37
    return frame


def section_order_frame() -> dict:
    """A canonical block whose header and dictionary sections are swapped."""
    frame = live_frame()
    frame["corr"] = 38
    return frame


def history_dictionary(generation: int = 2) -> dict:
    """An incarnation, two gauges and a counter, for the P2a1 history cases."""
    return {
        "generation": generation,
        "entries": [
            {"kind": "entity", "inc": 11, "text": "/system.slice"},
            {"kind": "metric", "text": "mem.current", "facts": METRIC_FACTS["mem_current"]},
            {"kind": "metric", "text": "signed.value", "facts": METRIC_FACTS["signed_gauge"]},
            {"kind": "metric", "text": "psi.io.some.total", "facts": METRIC_FACTS["psi_total"]},
        ],
    }


def history_columns(metrics: list[int], n: int) -> list[dict]:
    kinds = {1: "gauge", 2: "gauge", 3: "counter"}
    return [
        {
            "metric": metric,
            "type": "u64",
            "kind": kinds[metric],
            "values": [str(100 * metric + index) for index in range(n)],
            "quality": ["ok"] * n,
        }
        for metric in metrics
    ]


def history_frame_of(corr: int, aggs: list[str], metrics: list[int], subject: int | None) -> dict:
    """A history block of `len(aggs)` buckets, one per agg, optionally naming its subject."""
    n = len(aggs)
    frame = {
        "type": "block",
        "corr": corr,
        "sections": ["header", "dictionary"] + (["subject"] if subject is not None else []) + ["time", "columns"],
        "header": header(n, dict_gen=2, epoch=8, seq=0),
        "dictionary": history_dictionary(),
        "rows": [],
        "time": [
            {"real_ns": 1_700_000_000_000_000_000 + index * 1_000_000_000, "covered_ms": 1000, "samples": 5, "agg": agg}
            for index, agg in enumerate(aggs)
        ],
        "columns": history_columns(metrics, n),
        "skipped": [],
    }
    if subject is not None:
        frame["subject"] = subject
    return frame


def history_subject_frame() -> dict:
    """A history block that names its subject (kind 6): counter columns, raw/last/delta buckets."""
    return history_frame_of(41, ["raw", "last", "delta"], [3], 0)


def history_gauge_aggs_frame() -> dict:
    """A gauge-only history block: mean, min, max and last buckets (no subject, minor-0 shape)."""
    return history_frame_of(42, ["mean", "min", "max", "last"], [1, 2], None)


def history_agg_not_permitted_frame() -> dict:
    """`mean` over a counter column: the counter mask is raw/last/delta."""
    return history_frame_of(43, ["last", "mean"], [3], 0)


def subject_in_live_frame() -> dict:
    """A live block carrying a subject section: only a time block may."""
    frame = live_frame()
    frame["corr"] = 44
    frame["sections"] = ["header", "dictionary", "subject", "rows", "columns"]
    frame["subject"] = 0
    return frame


def duplicate_metric_other_facts_frame() -> dict:
    """The metric name `cpu.usage_usec` repeated with another unit (index 3)."""
    frame = live_frame()
    frame["corr"] = 45
    other = dict(METRIC_FACTS["cpu_usage"], unit="bytes")
    frame["dictionary"] = {
        "generation": 1,
        "entries": [
            {"kind": "entity", "inc": 11, "text": "/system.slice"},
            {"kind": "entity", "inc": 12, "text": "/docker/abc"},
            {"kind": "metric", "text": "cpu.usage_usec", "facts": METRIC_FACTS["cpu_usage"]},
            {"kind": "metric", "text": "cpu.usage_usec", "facts": other},
        ],
    }
    return frame


def duplicate_entity_other_label_frame() -> dict:
    """The incarnation 11 repeated with another label (index 2)."""
    frame = live_frame()
    frame["corr"] = 46
    frame["dictionary"] = {
        "generation": 1,
        "entries": [
            {"kind": "entity", "inc": 11, "text": "/system.slice"},
            {"kind": "entity", "inc": 12, "text": "/docker/abc"},
            {"kind": "entity", "inc": 11, "text": "/other-label"},
            {"kind": "metric", "text": "mem.current", "facts": METRIC_FACTS["mem_current"]},
        ],
    }
    return frame


def duplicate_entity_control_frame() -> dict:
    """The repeated incarnation of `duplicate_entity_other_label_frame`, corr 71."""
    frame = duplicate_entity_other_label_frame()
    frame["corr"] = 71
    return frame


def bare_frame(corr: int) -> dict:
    """The live block without its dictionary section: it is judged against a held generation."""
    frame = live_frame()
    frame["corr"] = corr
    frame["sections"] = ["header", "rows", "columns"]
    del frame["dictionary"]
    return frame


def bare_held_dictionary() -> dict:
    """The generation 1 the bare block's stream holds (the live block's dictionary)."""
    return dictionary()


def unaligned_optional_frame() -> dict:
    """An unknown optional section of 8 bytes, last; the edit shortens it to 5 (unaligned)."""
    frame = live_frame()
    frame["corr"] = 47
    frame["unknown_sections"] = [{"kind_code": 9, "version": 1, "flags": 0, "body_hex": "00" * 8}]
    return frame


def rep_frames_bytes(sectioned: bool, truncated: bool) -> bytes:
    """A REP frame (type 2) of 32 declared bytes with 8 bytes of payload after a 12-byte header."""
    flags = wire.FRAME_SECTIONED if sectioned else 0
    data = struct.pack("<IBBHI", 32, wire.FRAME_TYPES["rep"], flags, 0, 48) + b"\x00" * 20
    return data[:24] if truncated else data


def truncated_directory() -> bytes:
    """A 16-byte frame that declares one section but carries no directory entry.

    Both decoders must report a truncation of the directory, never a struct.error: the
    Python reference used to raise struct.error where Rust returns a typed refusal.
    """
    return struct.pack("<IBBHIHH", 16, wire.FRAME_TYPES["block"], wire.FRAME_SECTIONED, 0, 39, 1, 0)


def reorder(data: bytes, order: list[int]) -> bytes:
    """Re-emit `data` with its directory entries and bodies in `order`.

    Every other byte is unchanged, so this is exactly the swap a hostile or buggy sender
    would produce: each declared length still matches the body it sits next to.
    """
    n_sec = struct.unpack_from("<H", data, wire.FRAME_HEADER_LEN)[0]
    entries = [
        data[
            directory_offset(data, index) : directory_offset(data, index) + wire.SECTION_DIR_ENTRY_LEN
        ]
        for index in range(n_sec)
    ]
    bodies = [
        data[
            body_offset(data, index) : body_offset(data, index)
            + struct.unpack_from("<I", entries[index], 4)[0]
        ]
        for index in range(n_sec)
    ]
    out = bytearray(data[: wire.FRAME_HEADER_LEN + 4])
    out += b"".join(entries[index] for index in order)
    out += b"".join(bodies[index] for index in order)
    assert len(out) == len(data), "reordering preserves every byte count"
    return bytes(out)


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


def split(data: bytes) -> list[tuple[int, int, int, bytes]]:
    """The directory entries of a frame as `(kind, version, flags, body)`, in directory order."""
    n_sec = struct.unpack_from("<H", data, wire.FRAME_HEADER_LEN)[0]
    entries = []
    for index in range(n_sec):
        kind, version, flags, length = struct.unpack_from("<HBBI", data, directory_offset(data, index))
        start = body_offset(data, index)
        entries.append((kind, version, flags, data[start : start + length]))
    return entries


def assemble(data: bytes, entries: list[tuple[int, int, int, bytes]]) -> bytes:
    """Re-emit the frame header of `data` around `entries`, with the declared length fixed up."""
    out = bytearray(data[: wire.FRAME_HEADER_LEN])
    out += struct.pack("<HH", len(entries), 0)
    for kind, version, flags, body in entries:
        out += struct.pack("<HBBI", kind, version, flags, len(body))
    for entry in entries:
        out += entry[3]
    out[0:4] = struct.pack("<I", len(out))
    return bytes(out)


def bare_history_frame(corr: int, aggs: list[str], metrics: list[int], subject: int | None = None) -> dict:
    """A history block without its dictionary section, for a held dictionary (generation 2)."""
    frame = history_frame_of(corr, aggs, metrics, subject)
    frame["sections"] = ["header"] + (["subject"] if subject is not None else []) + ["time", "columns"]
    del frame["dictionary"]
    return frame


def history_state_frame(corr: int, agg: str) -> dict:
    """A history block over one `state` column (metric 4 of a five-entry dictionary)."""
    frame = history_frame_of(corr, [agg], [1], None)
    frame["dictionary"]["entries"].append(
        {"kind": "metric", "text": "ksm.run", "facts": METRIC_FACTS["ksm_state"]}
    )
    frame["columns"] = [
        {"metric": 4, "type": "u64", "kind": "state", "values": ["1"], "quality": ["ok"]}
    ]
    return frame


CASES: list[tuple[str, dict, list[tuple[int, bytes]] | None, dict | None]] = [
    ("live-block", live_frame(), None, None),
    ("u64-boundaries", u64_boundary_frame(), None, None),
    ("history-buckets", history_frame(), None, None),
    ("history-five-buckets", history_five_frame(), None, None),
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
        "frame-length-below-header",
        below_header_frame(),
        "length-below-header",
        {"kind": "truncated", "context": "frame length shorter than the header"},
    ),
    (
        "block-without-directory",
        header_only_frame(),
        "header-only",
        {"kind": "truncated", "context": "block directory"},
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
    (
        "block-as-req",
        block_as_req_frame(),
        "not-a-block",
        {"kind": "not-a-block", "frame_type": 1},
    ),
    (
        "section-order",
        section_order_frame(),
        "section-order",
        {"kind": "section-order", "index": 1, "kind_code": 1},
    ),
    ("history-subject", history_subject_frame(), None, None),
    ("history-gauge-aggs", history_gauge_aggs_frame(), None, None),
    ("bare-block-held-dict", bare_frame(50), None, None),
    (
        "unaligned-optional-section",
        unaligned_optional_frame(),
        "unaligned-optional",
        None,
    ),
    (
        "optional-section-before-header",
        unknown_optional_frame(),
        "optional-before-header",
        None,
    ),
    (
        "duplicate-metric-other-facts",
        duplicate_metric_other_facts_frame(),
        None,
        {"kind": "duplicate-dictionary-entry", "index": 3},
    ),
    (
        "duplicate-entity-other-label",
        duplicate_entity_other_label_frame(),
        None,
        {"kind": "duplicate-dictionary-entry", "index": 2},
    ),
    ("rep-not-sectioned-truncated", bare_frame(48), "rep-truncated", None),
    (
        "subject-in-live-block",
        subject_in_live_frame(),
        None,
        {"kind": "subject-without-time", "subject": 0},
    ),
    (
        "history-agg-not-permitted",
        history_agg_not_permitted_frame(),
        None,
        {"kind": "aggregation-not-permitted", "metric": 3, "agg": 3},
    ),
    ("bare-block-dangling", bare_frame(51), "bare-dangling", None),
    ("rep-sectioned-flag", bare_frame(49), "rep-sectioned", None),
    (
        "truncated-directory",
        block_as_req_frame(),
        "truncated-directory",
        {"kind": "truncated", "context": "block directory"},
    ),
    # P2a1 review round 1: parity cases (every refusal below is the same error in both decoders).
    ("duplicate-header-bad-second-body", live_frame(), "dup-header", None),
    ("column-nonzero-pad", live_frame(), "column-pad", None),
    ("time-nonzero-pad", history_frame_of(60, ["last"] * 3, [1], None), "time-pad", None),
    ("dictionary-nonzero-pad", live_frame(), "dictionary-pad", None),
    ("time-bad-agg-and-pad", history_frame_of(61, ["last"] * 3, [1], None), "time-agg-pad", None),
    ("dictionary-count-too-large", live_frame(), "dictionary-count", None),
    ("dictionary-entry-control", live_frame(), "dictionary-control", None),
    ("dictionary-text-past-body", live_frame(), "dictionary-text-past", None),
    ("dictionary-invalid-utf8", live_frame(), "dictionary-utf8", None),
    ("dictionary-short-entry-header", live_frame(), "dictionary-short-header", None),
    ("truncated-unknown-type", block_as_req_frame(), "unknown-type-short", None),
    ("header-required-flag-clear", live_frame(), "header-flag-clear", None),
    ("subject-required-flag-clear", history_frame_of(62, ["last"] * 3, [1], 0), "subject-flag-clear", None),
    ("unknown-section-flags-directory", live_frame(), "directory-flags", None),
    ("unknown-section-flags-header", live_frame(), "header-flags-word", None),
    ("section-reserved", live_frame(), "section-reserved", None),
    ("missing-header", live_frame(), "missing-header", None),
    ("duplicate-section", live_frame(), "duplicate-dictionary-section", None),
    ("conflicting-column", live_frame(), "conflicting-column", None),
    ("subject-wrong-length", history_frame_of(63, ["last"] * 3, [1], 0), "subject-length", None),
    ("subject-reserved-word", history_frame_of(64, ["last"] * 3, [1], 0), "subject-reserved", None),
    ("subject-dangling", history_frame_of(65, ["last"] * 3, [1], 0), "subject-dangling", None),
    ("subject-not-entity", history_frame_of(66, ["last"] * 3, [1], 0), "subject-metric", None),
    ("subject-stale-generation-held", bare_history_frame(67, ["last"], [1], 0), "subject-held", None),
    ("aggregation-not-permitted-held", bare_history_frame(68, ["raw"], [1]), "agg-held", None),
    ("history-gauge-raw", history_frame_of(69, ["raw"], [1], None), "agg-gauge-raw", None),
    ("history-state-delta", history_state_frame(70, "delta"), "agg-state-delta", None),
    ("duplicate-entity-other-label-control-byte", duplicate_entity_control_frame(), "duplicate-control", None),
    ("header-version-and-required-flag-clear", live_frame(), "header-version-flag", None),
]


def build(name: str, frame: dict, edits: object) -> tuple[bytes, dict | None]:
    """Return the committed bytes and the expected refusal (if any)."""
    if edits == "truncated-directory":
        return truncated_directory(), {"kind": "truncated", "context": "block directory"}
    data = wire.encode_frame(frame)
    if edits == "not-a-block":
        # A canonical block with the header's type byte changed to REQ (frame type 1) and the
        # SECTIONED flag cleared: SECTIONED on a REQ is itself a refusal (`rep-sectioned-flag`), so
        # the type/payload confusion is only reachable with the flag clear (WIRE.md 2.1, 3.10).
        return patch(data, 4, bytes([wire.FRAME_TYPES["req"], 0])), {"kind": "not-a-block", "frame_type": 1}
    if edits == "section-order":
        # Header and dictionary swap places, bodies included: every length still matches.
        n_sec = struct.unpack_from("<H", data, wire.FRAME_HEADER_LEN)[0]
        order = list(range(n_sec))
        order[0], order[1] = order[1], order[0]
        return reorder(data, order), {
            "kind": "section-order",
            "index": 1,
            "kind_code": wire.SECTION_KINDS["header"],
        }
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
    if edits == "length-below-header":
        # A header declaring a total of 4 bytes, less than the 12-byte header itself.
        return patch(data, 0, struct.pack("<I", 4)), {
            "kind": "truncated",
            "context": "frame length shorter than the header",
        }
    if edits == "header-only":
        # Exactly the 12 header bytes of a sectioned block and nothing after them: the length
        # is legal, the section directory is missing.
        header_only = struct.pack(
            "<IBBHI", wire.FRAME_HEADER_LEN, wire.FRAME_TYPES["block"], wire.FRAME_SECTIONED, 0, frame["corr"]
        )
        return header_only, {"kind": "truncated", "context": "block directory"}
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
    if edits == "unaligned-optional":
        # The unknown optional section is last: its directory length becomes 5 and 3 bytes go.
        n_sec = struct.unpack_from("<H", data, wire.FRAME_HEADER_LEN)[0]
        data = patch(data, directory_offset(data, n_sec - 1) + 4, struct.pack("<I", 5))[:-3]
        return patch(data, 0, struct.pack("<I", len(data))), {"kind": "section-length", "section": 9, "declared": 5, "expected": 8}
    if edits == "optional-before-header":
        # The unknown optional section moves to directory entry 0; its length (8) is aligned.
        n_sec = struct.unpack_from("<H", data, wire.FRAME_HEADER_LEN)[0]
        return reorder(data, [n_sec - 1] + list(range(n_sec - 1))), {"kind": "section-order", "index": 0, "kind_code": 9}
    if edits == "bare-dangling":
        rows = section_index(data, "rows")
        return patch(data, body_offset(data, rows), struct.pack("<I", 99)), {
            "kind": "dangling-dictionary-ref",
            "index": 99,
            "entries": 4,
        }
    if edits == "rep-truncated":
        return rep_frames_bytes(False, True), {"kind": "not-a-block", "frame_type": 2}
    if edits == "rep-sectioned":
        return rep_frames_bytes(True, False), {"kind": "unknown-frame-flags", "flags": 1}
    if edits == "dup-header":
        # A second header whose body is bad (flags word 1): only the first header is decoded, so
        # the frame is refused as a duplicate section, not for the second body.
        entries = split(data)
        entries.insert(1, (wire.SECTION_KINDS["header"], 1, 1, entries[0][3][:-4] + struct.pack("<I", 1)))
        return assemble(data, entries), {"kind": "duplicate-section", "kind_code": 1}
    if edits == "column-pad":
        # The first column of three rows is 8 + 27 = 35 bytes of content: byte 35 is pad.
        offset = body_offset(data, section_index(data, "columns")) + 8 + 9 * 3
        return patch(data, offset, b"\x01"), {"kind": "non-zero-reserved", "value": 1}
    if edits == "time-pad":
        # Three buckets are 45 bytes of content: bytes 45..47 are pad.
        offset = body_offset(data, section_index(data, "time")) + 15 * 3
        return patch(data, offset, b"\x01"), {"kind": "non-zero-reserved", "value": 1}
    if edits == "dictionary-pad":
        # The four entries end at byte 101 of the body; the body is padded to 104.
        offset = dictionary_entry_offset(data, 4)
        return patch(data, offset, b"\x01"), {"kind": "non-zero-reserved", "value": 1}
    if edits == "time-agg-pad":
        # Both a bad aggregation byte (9, bucket 0) and a non-zero pad byte: the aggregation
        # bytes are judged first (WIRE.md 3.10).
        start = body_offset(data, section_index(data, "time"))
        data = patch(data, start + 14 * 3, b"\x09")
        return patch(data, start + 15 * 3, b"\x01"), {"kind": "unknown-aggregation", "code": 9}
    if edits == "dictionary-count":
        start = body_offset(data, section_index(data, "dictionary"))
        return patch(data, start + 4, struct.pack("<I", 65_537)), {
            "kind": "dictionary-too-large",
            "count": 65_537,
            "max": wire.MAX_DICTIONARY_ENTRIES,
        }
    if edits == "dictionary-control":
        # The first text byte of entry 0 becomes 0x01.
        return patch(data, dictionary_entry_offset(data, 0) + 4 + 8, b"\x01"), {
            "kind": "dictionary-entry-control",
            "index": 0,
            "offset": 0,
        }
    if edits == "dictionary-text-past":
        # Entry 3 declares 200 bytes of text; the body ends long before.
        return patch(data, dictionary_entry_offset(data, 3) + 2, struct.pack("<H", 200)), {
            "kind": "truncated",
            "context": "dictionary section",
        }
    if edits == "dictionary-utf8":
        return patch(data, dictionary_entry_offset(data, 0) + 4 + 8, b"\xff"), {
            "kind": "dictionary-entry-not-utf8",
            "index": 0,
        }
    if edits == "dictionary-short-header":
        # Five entries are declared and four present; the body ends 3 bytes into the fifth
        # entry's 4-byte header.
        start = body_offset(data, section_index(data, "dictionary"))
        return patch(data, start + 4, struct.pack("<I", 5)), {
            "kind": "truncated",
            "context": "dictionary section",
        }
    if edits == "unknown-type-short":
        # Five bytes: the length word and a type byte of 9. The type is judged as soon as its
        # byte exists, so this is not a truncation (WIRE.md 3.10).
        return struct.pack("<IB", 12, 9), {"kind": "unknown-frame-type", "code": 9}
    if edits == "header-flag-clear":
        offset = directory_offset(data, section_index(data, "header")) + 3
        return patch(data, offset, b"\x00"), {"kind": "unknown-section-flags", "kind_code": 1, "flags": 0}
    if edits == "duplicate-control":
        # The repeated incarnation's label also starts with the control byte 0x01: the repeated
        # identity is judged before the text's control bytes.
        return patch(data, dictionary_entry_offset(data, 2) + 4 + 8, b"\x01"), {
            "kind": "duplicate-dictionary-entry",
            "index": 2,
        }
    if edits == "header-version-flag":
        # Version 2 AND a clear required flag on the header entry: the version is judged first.
        offset = directory_offset(data, section_index(data, "header"))
        return patch(data, offset + 2, b"\x02\x00"), {
            "kind": "unsupported-section-version",
            "kind_code": 1,
            "version": 2,
            "supported": 1,
        }
    if edits == "subject-flag-clear":
        offset = directory_offset(data, section_index(data, "subject")) + 3
        return patch(data, offset, b"\x00"), {"kind": "unknown-section-flags", "kind_code": 6, "flags": 0}
    if edits == "directory-flags":
        offset = directory_offset(data, section_index(data, "columns")) + 3
        return patch(data, offset, b"\x02"), {"kind": "unknown-section-flags", "kind_code": 3, "flags": 2}
    if edits == "header-flags-word":
        offset = body_offset(data, section_index(data, "header")) + 76
        return patch(data, offset, struct.pack("<I", 1)), {
            "kind": "unknown-section-flags",
            "kind_code": 1,
            "flags": 1,
        }
    if edits == "section-reserved":
        return patch(data, wire.FRAME_HEADER_LEN + 2, b"\x01\x00"), {"kind": "section-reserved", "value": 1}
    if edits == "missing-header":
        entries = split(data)
        del entries[0]
        return assemble(data, entries), {"kind": "missing-section", "section": "header"}
    if edits == "duplicate-dictionary-section":
        entries = split(data)
        entries.insert(2, entries[1])
        return assemble(data, entries), {"kind": "duplicate-section", "kind_code": 5}
    if edits == "conflicting-column":
        entries = split(data)
        first = next(index for index, entry in enumerate(entries) if entry[0] == wire.SECTION_KINDS["columns"])
        entries.insert(first + 1, entries[first])
        return assemble(data, entries), {"kind": "conflicting-column", "metric": 2}
    if edits == "subject-length":
        entries = split(data)
        index = next(index for index, entry in enumerate(entries) if entry[0] == wire.SECTION_KINDS["subject"])
        kind, version, flags, _body = entries[index]
        entries[index] = (kind, version, flags, struct.pack("<IIII", 0, 0, 0, 0))
        return assemble(data, entries), {"kind": "section-length", "declared": 16, "expected": 8}
    if edits == "subject-reserved":
        offset = body_offset(data, section_index(data, "subject")) + 4
        return patch(data, offset, struct.pack("<I", 1)), {
            "kind": "unknown-section-flags",
            "kind_code": 6,
            "flags": 1,
        }
    if edits == "subject-dangling":
        offset = body_offset(data, section_index(data, "subject"))
        return patch(data, offset, struct.pack("<I", 99)), {
            "kind": "dangling-dictionary-ref",
            "index": 99,
            "entries": 4,
        }
    if edits == "subject-metric":
        offset = body_offset(data, section_index(data, "subject"))
        return patch(data, offset, struct.pack("<I", 1)), {"kind": "row-reference-not-entity", "index": 1}
    if edits == "subject-held":
        # Bare block of generation 2 decoded against a held generation 3 (`HELD`).
        return data, {"kind": "stale-dictionary-generation", "needed": 2, "held": 3}
    if edits in ("agg-held", "agg-gauge-raw"):
        return data, {"kind": "aggregation-not-permitted", "metric": 1, "agg": 0}
    if edits == "agg-state-delta":
        return data, {"kind": "aggregation-not-permitted", "metric": 4, "agg": 2}
    if edits is None:
        return data, None
    raise SystemExit(f"unknown edit {edits!r} for case {name}")


def event_minimal() -> dict:
    return {
        "t_mono_ns": 0,
        "span": 0,
        "peer_dev": 0,
        "peer_inode": 0,
        "uid": 0,
        "peer_pid": 0,
        "kind": 3,
        "outcome": 0,
        "inferred": False,
        "ctx": [],
        "attrs": [],
        "name": "a",
        "key": "k",
        "code": None,
        "comm": None,
    }


def event_full() -> dict:
    return {
        "t_mono_ns": (1 << 63) - 1,
        "span": (1 << 64) - 1,
        "peer_dev": 1,
        "peer_inode": (1 << 64) - 1,
        "uid": (1 << 32) - 1,
        "peer_pid": (1 << 32) - 1,
        "kind": 2,
        "outcome": 7,
        "inferred": True,
        "ctx": [{"key": key, "value": "x" * 128} for key in range(5)],
        "attrs": [
            {
                "key": chr(key),
                "value": "" if key == ord("a") else "v" * 256,
            }
            for key in range(ord("a"), ord("i"))
        ],
        "name": "n" * 64,
        "key": "k" * 64,
        "code": "c" * 64,
        "comm": "m" * 15,
    }


def event_point_ctx() -> dict:
    return {
        "t_mono_ns": 1,
        "span": 0,
        "peer_dev": 0,
        "peer_inode": 0,
        "uid": 1000,
        "peer_pid": 42,
        "kind": 3,
        "outcome": 0,
        "inferred": False,
        "ctx": [
            {"key": 0, "value": "run-1"},
            {"key": 4, "value": "session-1"},
        ],
        "attrs": [],
        "name": "run.point",
        "key": "run.point",
        "code": None,
        "comm": "worker",
    }


def raw_event_frame(body: bytes) -> bytes:
    total = 24 + len(body)
    return (
        struct.pack("<IBBHI", total, wire.FRAME_TYPES["evt"], wire.FRAME_SECTIONED, 0, 0)
        + struct.pack(
            "<HHHBBI",
            1,
            0,
            wire.EVENT_SECTION_KIND,
            wire.EVENT_SECTION_VERSION,
            wire.EVENT_SECTION_REQUIRED_FLAGS,
            len(body),
        )
        + body
    )


def corrupt_event_body(event: dict, operation: str) -> bytes:
    body = bytearray(wire.encode_event_payload(event))
    if operation == "unknown-kind":
        body[40] = 4
        return bytes(body)
    if operation == "control-key":
        name_length = struct.unpack_from("<H", body, 48)[0]
        key_length_offset = 50 + name_length
        key_start = key_length_offset + 2
        body[key_start] = 1
        return bytes(body)

    offset = 48
    for _ in range(4):
        length = struct.unpack_from("<H", body, offset)[0]
        offset += 2 + length
    for _ in event["ctx"]:
        length = struct.unpack_from("<H", body, offset + 1)[0]
        offset += 3 + length
    attrs = []
    for _ in event["attrs"]:
        start = offset
        key_length = struct.unpack_from("<H", body, offset)[0]
        offset += 2 + key_length
        value_length = struct.unpack_from("<H", body, offset)[0]
        offset += 2 + value_length
        attrs.append((start, offset))

    if operation == "unsorted-attrs":
        first_start, first_end = attrs[0]
        second_start, second_end = attrs[1]
        return bytes(
            body[:first_start]
            + body[second_start:second_end]
            + body[first_start:first_end]
            + body[second_end:]
        )
    if operation == "nine-attrs":
        raw = bytearray(body[:offset])
        raw.extend(struct.pack("<H", 1) + b"i" + struct.pack("<H", 0))
        raw[44] = 9
        raw.extend(b"\x00" * (-len(raw) % 8))
        return bytes(raw)
    raise SystemExit(f"unknown event corruption {operation!r}")


def write_event_fixtures(out: Path) -> list[tuple[str, int, str | None]]:
    minimum = event_minimal()
    full = event_full()
    point_ctx = event_point_ctx()
    cases = [
        ("event-minimal", minimum, None, None, None, None),
        ("event-full", full, None, None, None, None),
        ("event-point-ctx", point_ctx, None, None, None, None),
        ("event-unknown-code", minimum, "unknown-kind", "event-unknown-code", "kind", None),
        ("event-overflow", full, "nine-attrs", "event-field-bound", "n_attrs", None),
        ("event-unsorted-attrs", full, "unsorted-attrs", "event-order", "attrs", 1),
        ("event-control-text", full, "control-key", "event-text", "key", None),
    ]
    written = []
    for name, event, operation, refusal, error_field, error_index in cases:
        if operation is None:
            data = wire.encode_event_frame(event)
        else:
            data = raw_event_frame(corrupt_event_body(event, operation))
        document = {
            "case": name,
            "doc": "event/1 body fixture generated from the independent Python codec",
            "event": event,
        }
        if operation is not None:
            document["corrupt"] = {"operation": operation}
            expected = {"kind": refusal, "field": error_field}
            if error_index is not None:
                expected["index"] = error_index
            document["expect_error"] = expected
        (out / f"{name}.json").write_text(
            json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (out / f"{name}.bin").write_bytes(data)
        written.append((name, len(data), refusal))
    return written


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
        if name in HELD:
            document["held"] = HELD[name]
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
    print(
        f"wrote {len(written)} block fixtures plus quality.json into {out}"
    )
    return 0


# Cases whose bytes are decoded against a held dictionary (WIRE.md 6.1): the top-level `held` key
# of the case JSON, `{"generation", "entries"}` as in a frame's `dictionary`.
HELD = {
    "bare-block-held-dict": bare_held_dictionary(),
    "bare-block-dangling": bare_held_dictionary(),
    "subject-stale-generation-held": history_dictionary(3),
    "aggregation-not-permitted-held": history_dictionary(2),
}

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
    "history-five-buckets": (
        "A history block with five buckets: the time section is 15 bytes per bucket (75, padded to 80), "
        "so a body length computed with another per-bucket size is wrong from four buckets up."
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
    "frame-length-below-header": (
        "A frame header declaring a total length of 4 bytes, shorter than the 12-byte header. Only the "
        "refusal kind and its context are contractual; the need/have numbers are not pinned."
    ),
    "block-without-directory": (
        "Exactly the 12 header bytes of a sectioned block frame (declared length 12) and no section "
        "directory. Only the refusal kind and its context are contractual; both decoders report "
        "need 2 / have 0, which is not pinned."
    ),
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
    "history-subject": (
        "A history block naming its subject (section kind 6, incarnation index 0) over a counter column "
        "with raw, last and delta buckets: every agg is permitted by the counter mask."
    ),
    "history-gauge-aggs": (
        "A gauge-only history block with mean, min, max and last buckets, no subject: every agg is "
        "permitted by the gauge mask."
    ),
    "bare-block-held-dict": (
        "The live block without its dictionary section (header dict_gen 1), decoded against the held "
        "generation 1 given in `held`: rows and columns resolve against it."
    ),
    "unaligned-optional-section": (
        "An unknown optional section last in the directory with a length of 5: every section length is a "
        "multiple of 8, unknown kinds included."
    ),
    "optional-section-before-header": (
        "An unknown optional section (length 8) at directory entry 0: entry 0 must be the header."
    ),
    "duplicate-metric-other-facts": (
        "The metric name `cpu.usage_usec` repeated with another unit: a repeated identity is a duplicate "
        "whatever the other fields hold."
    ),
    "duplicate-entity-other-label": (
        "The incarnation 11 repeated with another label at index 2: a repeated identity is a duplicate."
    ),
    "rep-not-sectioned-truncated": (
        "A REP frame (type 2) of declared length 32 with only 24 bytes and no SECTIONED flag: the type is "
        "judged before the buffer length (WIRE.md 3.10), so not-a-block, not truncated."
    ),
    "subject-in-live-block": "A live block (rows) carrying a subject section: it needs the time section.",
    "history-agg-not-permitted": "A `mean` bucket over a counter column; the counter mask is raw/last/delta.",
    "bare-block-dangling": (
        "The bare live block decoded against a held 4-entry generation, its first row naming index 99."
    ),
    "rep-sectioned-flag": (
        "A REP frame with the SECTIONED flag set: SECTIONED is defined for BLOCK only, so it is an unknown "
        "frame flag locally (`bad-frame` on the wire)."
    ),
    "no-rows-or-time": "A block declaring three rows but carrying neither a rows nor a time section.",
    "empty-rows-and-time": (
        "A block with zero rows that carries BOTH the rows and the time section (each empty): "
        "rows and buckets are exclusive by presence, not by count."
    ),
    "duplicate-header-bad-second-body": (
        "A second header section whose body is bad (non-zero flags word): only the first header is "
        "decoded, so the frame is refused as a duplicate section, not for the second body."
    ),
    "column-nonzero-pad": "A column whose pad byte after the quality bytes is 1: non-zero padding is corrupt.",
    "time-nonzero-pad": "A time section (three buckets, 45 bytes of content) whose pad byte is 1.",
    "dictionary-nonzero-pad": "A dictionary body whose pad byte after the last entry is 1.",
    "time-bad-agg-and-pad": (
        "A time section with BOTH an undefined aggregation byte (9) and a non-zero pad byte: the "
        "aggregation bytes are judged before the pad, so the refusal is unknown-aggregation."
    ),
    "dictionary-count-too-large": "A dictionary declaring 65,537 entries, one past the 65,536 bound.",
    "dictionary-entry-control": "A dictionary entry whose text starts with the control byte 0x01.",
    "dictionary-text-past-body": "A dictionary entry declaring 200 bytes of text that the body does not hold.",
    "dictionary-invalid-utf8": "A dictionary entry whose text starts with the byte 0xFF, which is not UTF-8.",
    "dictionary-short-entry-header": (
        "A dictionary declaring five entries with four present: the body ends inside the fifth "
        "entry's 4-byte header."
    ),
    "truncated-unknown-type": (
        "Five bytes: a length word and a frame type byte of 9. The type byte is judged as soon as it "
        "exists, so the refusal is unknown-frame-type, not truncated (WIRE.md 3.10)."
    ),
    "header-required-flag-clear": "The header's directory entry with the required flag clear.",
    "subject-required-flag-clear": "The subject's directory entry with the required flag clear.",
    "unknown-section-flags-directory": "A column's directory entry with flag bit 1 set: only bit 0 is defined.",
    "unknown-section-flags-header": "A header body whose flags word is 1: it must be zero.",
    "section-reserved": "A directory whose reserved u16 is 1.",
    "missing-header": "A frame whose directory holds no header (dictionary, rows, columns only).",
    "duplicate-section": "A directory with the dictionary section twice.",
    "conflicting-column": "A directory with the same column (metric 2) twice.",
    "subject-wrong-length": "A subject section of 16 bytes instead of 8.",
    "subject-reserved-word": "A subject section whose reserved word is 1.",
    "subject-dangling": "A subject naming dictionary index 99 in a four-entry generation.",
    "subject-not-entity": "A subject naming dictionary index 1, a metric.",
    "subject-stale-generation-held": (
        "A bare history block with a subject, header dict_gen 2, decoded against a held generation 3: "
        "the generation is judged first."
    ),
    "aggregation-not-permitted-held": (
        "A bare history block with a `raw` bucket over a gauge column, decoded against a held "
        "dictionary: the mask is judged on the held path too."
    ),
    "history-gauge-raw": "A `raw` bucket over a gauge column: gauges permit last, mean, min and max.",
    "history-state-delta": "A `delta` bucket over a state column: states permit only last.",
    "duplicate-entity-other-label-control-byte": (
        "The incarnation 11 repeated at index 2 with a label that starts with the control byte 0x01: "
        "the repeated identity is judged before the text's control bytes."
    ),
    "header-version-and-required-flag-clear": (
        "The header's directory entry with version 2 and the required flag clear: the version is "
        "judged before the required flag."
    ),
}


if __name__ == "__main__":
    raise SystemExit(main())

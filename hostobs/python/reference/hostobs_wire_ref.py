#!/usr/bin/env python3
"""Independent reference encoder/decoder for the hostobs/1 column codec (plan v4 §B).

This module is written **from the wire specification**
(``hostobs/docs/WIRE-COLUMN-CODEC.md``), not from the Rust implementation. It is
deliberately small: one encoder, one decoder, the closed vocabularies, and typed
errors. It is not a client — no sockets, no scheduling, no store.

Standard library only (plan v4 §B.1: a Python reader is one ``memoryview`` and
``struct.unpack_from`` per column, so nothing here needs a dependency).

The fixtures under ``hostobs/fixtures/wire/`` are the shared contract:
``<case>.json`` is the decoded typed content of ``<case>.bin``. This module
encodes the JSON into canonical bytes and decodes the bytes back into the JSON,
and the Rust codec must agree on both directions; a divergence in either
implementation fails the Python conformance run (manual until HOB-4 lands).
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

MAGIC = b"HOB\x01"
PROTO_MAJOR = 1
PROTO_MINOR = 0
FRAME_HEADER_LEN = 12
SECTION_DIR_ENTRY_LEN = 8
MAX_FRAME_BYTES = 16 * 1024 * 1024
HEADER_BODY_LEN = 80
MAX_ENTRY_TEXT = 256

FRAME_TYPES = {"req": 1, "rep": 2, "evt": 3, "dict": 4, "block": 5, "end": 6}
FRAME_TYPES_BY_CODE = {code: name for name, code in FRAME_TYPES.items()}

SECTION_KINDS = {"header": 1, "rows": 2, "columns": 3, "time": 4, "dictionary": 5}
SECTION_KINDS_BY_CODE = {code: name for name, code in SECTION_KINDS.items()}
ONE_OF = {1, 2, 4, 5}

UNIT_CODES = {
    "none": 0,
    "usec": 1,
    "msec": 2,
    "nsec": 3,
    "bytes": 4,
    "count": 5,
    "ratio": 6,
    "volts": 7,
    "celsius": 8,
}
UNITS_BY_CODE = {code: name for name, code in UNIT_CODES.items()}

COLUMN_KINDS = {"gauge": 0, "counter": 1, "state": 2}
COLUMN_KINDS_BY_CODE = {code: name for name, code in COLUMN_KINDS.items()}

SCOPES = {"host": 0, "cgroup": 1, "process": 2, "daemon": 3}
SCOPES_BY_CODE = {code: name for name, code in SCOPES.items()}

SOURCES = {"cgroupfs": 0, "procfs": 1, "sysfs": 2, "probe": 3, "derived": 4}
SOURCES_BY_CODE = {code: name for name, code in SOURCES.items()}

VALUE_TYPES = {"u64": 1, "i64": 2, "f64": 3}
VALUE_TYPES_BY_CODE = {code: name for name, code in VALUE_TYPES.items()}

AGGREGATIONS = {"raw": 0, "last": 1, "delta": 2, "mean": 3, "min": 4, "max": 5}
AGGREGATIONS_BY_CODE = {code: name for name, code in AGGREGATIONS.items()}

QUALITIES = [
    "ok",
    "absent",
    "unreadable",
    "stale",
    "reset",
    "not-sampled",
    "unsupported",
    "unknown-topology",
]
QUALITY_CODES = {name: code for code, name in enumerate(QUALITIES)}

DICTIONARY_KINDS = {"entity": 0, "metric": 1, "label": 2, "free": 3}
DICTIONARY_KINDS_BY_CODE = {code: name for name, code in DICTIONARY_KINDS.items()}

# The aggregation each metric kind permits (plan v4 §B.3); the codec derives the mask.
AGGREGATION_MASKS = {
    "counter": (1 << AGGREGATIONS["raw"]) | (1 << AGGREGATIONS["last"]) | (1 << AGGREGATIONS["delta"]),
    "gauge": (1 << AGGREGATIONS["last"])
    | (1 << AGGREGATIONS["mean"])
    | (1 << AGGREGATIONS["min"])
    | (1 << AGGREGATIONS["max"]),
    "state": 1 << AGGREGATIONS["last"],
}

FRAME_SECTIONED = 0b0000_0001

# The one order the encoder emits and the decoder requires (plan v4 B.2): header,
# dictionary, rows-or-time, then columns. An unknown section kind is outside this
# version's layout and is exempt (it is skipped by its declared length).
CANONICAL_RANK = {"header": 0, "dictionary": 1, "rows": 2, "time": 2, "columns": 3}


def _check_canonical_order(sections: list[tuple]) -> None:
    """Refuse a known section sequence that is not the canonical order.

    Two byte strings must never decode to one value (plan v4 B.5), so an unordered
    directory is a corrupt frame rather than an alternative spelling of a valid one.
    """
    last = None
    for index, (kind, *_rest) in enumerate(sections):
        name = SECTION_KINDS_BY_CODE.get(kind)
        if name is None:
            continue
        rank = CANONICAL_RANK[name]
        if last is not None and rank < last:
            raise WireError("section-order", index=index, kind_code=kind)
        last = rank


class WireError(ValueError):
    """A decode refusal. ``kind`` and ``fields`` match the fixture vocabulary."""

    def __init__(self, refusal: str, **fields: object) -> None:
        super().__init__(refusal)
        self.refusal = refusal
        self.fields = fields

    def as_dict(self) -> dict:
        return {"kind": self.refusal, **self.fields}

    def __str__(self) -> str:
        details = ", ".join(f"{name}={value}" for name, value in sorted(self.fields.items()))
        return f"{self.refusal}({details})" if details else self.refusal


def _align8(value: int) -> int:
    return value if value % 8 == 0 else value + (8 - value % 8)


def boot_id_bytes(text: str) -> bytes:
    """Parse the dashed or bare 32-hex-digit kernel boot id into 16 bytes."""
    digits = text.replace("-", "")
    if len(digits) != 32:
        raise WireError("boot-id-length", len=len(digits))
    return bytes(int(digits[index : index + 2], 16) for index in range(0, 32, 2))


def boot_id_text(raw: bytes) -> str:
    digits = raw.hex()
    return f"{digits[0:8]}-{digits[8:12]}-{digits[12:16]}-{digits[16:20]}-{digits[20:32]}"


# ---------------------------------------------------------------- encoding


def encode_header_body(header: dict) -> bytes:
    fields = [
        boot_id_bytes(header["boot_id"]),
        struct.pack("<Q", header["epoch"]),
        struct.pack("<Q", header["seq"]),
        struct.pack("<Q", header["mono_start_ns"]),
        struct.pack("<Q", header["mono_end_ns"]),
        struct.pack("<Q", header["boot_ns"]),
        struct.pack("<q", header["real_ns"]),
        struct.pack("<I", header["dict_gen"]),
        struct.pack("<I", header["n_rows"]),
        struct.pack("<I", header["dropped"]),
        struct.pack("<I", header["flags"]),
    ]
    body = b"".join(fields)
    assert len(body) == HEADER_BODY_LEN, "the header body is a fixed 80 bytes"
    return body


def decode_header_body(body: bytes) -> dict:
    if len(body) != HEADER_BODY_LEN:
        raise WireError("section-length", section="header", declared=len(body), expected=HEADER_BODY_LEN)
    offset = 0
    raw_boot = body[offset : offset + 16]
    offset += 16
    values = struct.unpack_from("<QQQQQqIIII", body, offset)
    if values[9] != 0:
        raise WireError("unknown-section-flags", kind_code=SECTION_KINDS["header"], flags=values[9])
    return {
        "boot_id": boot_id_text(raw_boot),
        "epoch": values[0],
        "seq": values[1],
        "mono_start_ns": values[2],
        "mono_end_ns": values[3],
        "boot_ns": values[4],
        "real_ns": values[5],
        "dict_gen": values[6],
        "n_rows": values[7],
        "dropped": values[8],
        "flags": values[9],
    }


def encode_dictionary(dictionary: dict) -> bytes:
    entries = dictionary["entries"]
    out = [struct.pack("<II", dictionary["generation"], len(entries))]
    for index, entry in enumerate(entries):
        kind = DICTIONARY_KINDS[entry["kind"]]
        text = entry.get("text", "")
        raw_text = text.encode("utf-8")
        if len(raw_text) > MAX_ENTRY_TEXT:
            raise WireError("dictionary-entry-too-long", index=index, len=len(raw_text))
        if any(byte < 0x20 or byte == 0x7F for byte in raw_text):
            raise WireError("dictionary-entry-control", index=index)
        fields = struct.pack("<BBH", kind, 0, len(raw_text))
        if kind == DICTIONARY_KINDS["entity"]:
            fields += struct.pack("<Q", entry["inc"])
        elif kind == DICTIONARY_KINDS["metric"]:
            facts = entry["facts"]
            fields += struct.pack(
                "<BBBBH",
                UNIT_CODES[facts["unit"]],
                COLUMN_KINDS[facts["kind"]],
                SCOPES[facts["scope"]],
                SOURCES[facts["source"]],
                AGGREGATION_MASKS[facts["kind"]],
            )
        elif kind == DICTIONARY_KINDS["label"]:
            fields += struct.pack("<BB", entry["label_kind"], entry["label_source"])
        out.append(fields + raw_text)
    body = b"".join(out)
    return body + b"\x00" * (_align8(len(body)) - len(body))


def _entry_identity(entry: dict) -> tuple:
    """The identity a dictionary entry is unique by, matching the Rust `Entry::key`.

    An entity's identity is its incarnation (its label is not identity, plan v4 C.1);
    a metric's is its name; a label's is kind+source+text; a free entry's is its text.
    """
    kind = entry["kind"]
    if kind == "entity":
        return ("entity", entry["inc"])
    if kind == "metric":
        return ("metric", entry["text"])
    if kind == "label":
        return ("label", entry["label_kind"], entry["label_source"], entry["text"])
    return ("free", entry["text"])


def decode_dictionary(body: bytes) -> dict:
    if len(body) < 8:
        raise WireError("truncated", context="dictionary section", need=8, have=len(body))
    generation, count = struct.unpack_from("<II", body, 0)
    offset = 8
    entries = []
    seen_identities: list[object] = []
    for index in range(count):
        kind_code, flags, length = struct.unpack_from("<BBH", body, offset)
        offset += 4
        if flags != 0:
            raise WireError("dictionary-entry-reserved", index=index, flags=flags)
        if length > MAX_ENTRY_TEXT:
            raise WireError("dictionary-entry-too-long", index=index, len=length)
        if kind_code not in DICTIONARY_KINDS_BY_CODE:
            raise WireError("unknown-dictionary-kind", index=index, code=kind_code)
        entry: dict = {"kind": DICTIONARY_KINDS_BY_CODE[kind_code]}
        if kind_code == DICTIONARY_KINDS["entity"]:
            (inc,) = struct.unpack_from("<Q", body, offset)
            offset += 8
            entry["inc"] = inc
        elif kind_code == DICTIONARY_KINDS["metric"]:
            unit, kind, scope, source, mask = struct.unpack_from("<BBBBH", body, offset)
            offset += 6
            if unit not in UNITS_BY_CODE or kind not in COLUMN_KINDS_BY_CODE:
                raise WireError("dictionary-entry-unknown-code", index=index, code=unit if unit not in UNITS_BY_CODE else kind)
            if scope not in SCOPES_BY_CODE or source not in SOURCES_BY_CODE:
                raise WireError("dictionary-entry-unknown-code", index=index, code=scope)
            entry["facts"] = {
                "unit": UNITS_BY_CODE[unit],
                "kind": COLUMN_KINDS_BY_CODE[kind],
                "scope": SCOPES_BY_CODE[scope],
                "source": SOURCES_BY_CODE[source],
            }
            if mask != AGGREGATION_MASKS[COLUMN_KINDS_BY_CODE[kind]]:
                raise WireError(
                    "dictionary-entry-mask",
                    index=index,
                    mask=mask,
                    expected=AGGREGATION_MASKS[COLUMN_KINDS_BY_CODE[kind]],
                )
        elif kind_code == DICTIONARY_KINDS["label"]:
            label_kind, label_source = struct.unpack_from("<BB", body, offset)
            offset += 2
            entry["label_kind"] = label_kind
            entry["label_source"] = label_source
        entry["text"] = body[offset : offset + length].decode("utf-8", errors="strict")
        offset += length
        # A wire list is the sender's numbering: entry `index` must be at index `index`.
        # A repeated identity is a corrupt frame, not a list to de-duplicate, because
        # de-duplicating would shift every later index away from what the sender wrote.
        identity = _entry_identity(entry)
        if identity in seen_identities:
            raise WireError("duplicate-dictionary-entry", index=index)
        seen_identities.append(identity)
        entries.append(entry)
    end = _align8(offset)
    pad = body[offset:end]
    if any(byte != 0 for byte in pad) or end != len(body):
        raise WireError("trailing-bytes", extra=len(body) - offset)
    return {"generation": generation, "entries": entries}


def encode_rows(rows: list[dict]) -> bytes:
    out = bytearray()
    for row in rows:
        out += struct.pack("<I", row["ent"])
    for row in rows:
        out += struct.pack("<I", row["t_off_us"])
    return bytes(out)


def decode_rows(body: bytes, n_rows: int) -> list[dict]:
    expected = n_rows * 8
    if len(body) != expected:
        raise WireError("section-length", section="rows", declared=len(body), expected=expected)
    ents = struct.unpack_from(f"<{n_rows}I", body, 0) if n_rows else ()
    offsets = struct.unpack_from(f"<{n_rows}I", body, n_rows * 4) if n_rows else ()
    return [{"ent": ent, "t_off_us": off} for ent, off in zip(ents, offsets, strict=True)]


def column_body_len(n_rows: int) -> int:
    return _align8(8 + n_rows * 9)


def encode_column(column: dict) -> bytes:
    value_type = column["type"]
    out = [struct.pack("<IBBH", column["metric"], VALUE_TYPES[value_type], COLUMN_KINDS[column["kind"]], 0)]
    if value_type == "f64":
        out.extend(struct.pack("<Q", int(text, 16)) for text in column["value_bits"])
    elif value_type == "i64":
        out.extend(struct.pack("<q", int(text)) for text in column["values"])
    else:
        out.extend(struct.pack("<Q", int(text)) for text in column["values"])
    quality = bytes(QUALITY_CODES[name] for name in column["quality"])
    out.append(quality)
    body = b"".join(out)
    return body + b"\x00" * (_align8(len(body)) - len(body))


def decode_column(body: bytes, n_rows: int) -> dict:
    expected = column_body_len(n_rows)
    if len(body) != expected:
        raise WireError("section-length", section="columns", declared=len(body), expected=expected)
    metric, type_code, kind_code, reserved = struct.unpack_from("<IBBH", body, 0)
    if type_code not in VALUE_TYPES_BY_CODE:
        raise WireError("unknown-value-type", code=type_code)
    if kind_code not in COLUMN_KINDS_BY_CODE:
        raise WireError("unknown-column-kind", code=kind_code)
    if reserved != 0:
        raise WireError("column-reserved", metric=metric, pad=reserved)
    value_type = VALUE_TYPES_BY_CODE[type_code]
    offset = 8
    raw_values = struct.unpack_from(f"<{n_rows}Q", body, offset) if n_rows else ()
    offset += n_rows * 8
    quality_codes = body[offset : offset + n_rows]
    offset += n_rows
    names = []
    for row, code in enumerate(quality_codes):
        if code >= len(QUALITIES):
            raise WireError("unknown-quality", row=row, code=code)
        names.append(QUALITIES[code])
    end = _align8(offset)
    if any(byte != 0 for byte in body[offset:end]) or end != len(body):
        raise WireError("trailing-bytes", extra=len(body) - offset)
    column = {"metric": metric, "type": value_type, "kind": COLUMN_KINDS_BY_CODE[kind_code], "quality": names}
    if value_type == "f64":
        column["value_bits"] = [f"0x{value:016x}" for value in raw_values]
    else:
        signed = [struct.unpack("<q", struct.pack("<Q", value))[0] for value in raw_values]
        column["values"] = [str(value) for value in (signed if value_type == "i64" else raw_values)]
    return column


#: Bytes one time bucket occupies: i64 real_ns (8) + u32 covered_ms (4) + u16 samples (2) + u8 agg (1).
TIME_BUCKET_BYTES = 8 + 4 + 2 + 1


def time_body_len(n_buckets: int) -> int:
    return _align8(n_buckets * TIME_BUCKET_BYTES)


def encode_time(time: list[dict]) -> bytes:
    out = [struct.pack("<q", bucket["real_ns"]) for bucket in time]
    out.extend(struct.pack("<I", bucket["covered_ms"]) for bucket in time)
    out.extend(struct.pack("<H", bucket["samples"]) for bucket in time)
    out.extend(struct.pack("<B", AGGREGATIONS[bucket["agg"]]) for bucket in time)
    body = b"".join(out)
    return body + b"\x00" * (_align8(len(body)) - len(body))


def decode_time(body: bytes, n_buckets: int) -> list[dict]:
    expected = time_body_len(n_buckets)
    if len(body) != expected:
        raise WireError("section-length", section="time", declared=len(body), expected=expected)
    real = struct.unpack_from(f"<{n_buckets}q", body, 0) if n_buckets else ()
    offset = n_buckets * 8
    covered = struct.unpack_from(f"<{n_buckets}I", body, offset) if n_buckets else ()
    offset += n_buckets * 4
    samples = struct.unpack_from(f"<{n_buckets}H", body, offset) if n_buckets else ()
    offset += n_buckets * 2
    aggs = body[offset : offset + n_buckets]
    offset += n_buckets
    end = _align8(offset)
    if any(byte != 0 for byte in body[offset:end]) or end != len(body):
        raise WireError("trailing-bytes", extra=len(body) - offset)
    buckets = []
    for index in range(n_buckets):
        if aggs[index] not in AGGREGATIONS_BY_CODE:
            raise WireError("unknown-aggregation", code=aggs[index])
        buckets.append(
            {
                "real_ns": real[index],
                "covered_ms": covered[index],
                "samples": samples[index],
                "agg": AGGREGATIONS_BY_CODE[aggs[index]],
            }
        )
    return buckets


def encode_frame(frame: dict) -> bytes:
    """Encode one frame from its fixture description."""
    sections = []
    # Sections are emitted in the order the fixture declares them, so the directory order
    # is part of the fixture rather than an accident of this encoder. A `columns` entry
    # expands to one directory entry per column at that position.
    for name in frame["sections"]:
        if name == "columns":
            for column in frame.get("columns", []):
                sections.append((SECTION_KINDS["columns"], 1, 0, encode_column(column)))
            continue
        body = {
            "header": lambda: encode_header_body(frame["header"]),
            "rows": lambda: encode_rows(frame.get("rows", [])),
            "time": lambda: encode_time(frame.get("time", [])),
            "dictionary": lambda: encode_dictionary(frame["dictionary"]),
        }[name]()
        sections.append((SECTION_KINDS[name], 1, 1 if name == "header" else 0, body))
    # Unknown sections are declared, never invented: the fixture spells them out so a
    # conforming decoder's skip-or-refuse behaviour is exercised by committed bytes.
    for extra in frame.get("unknown_sections", []):
        body = bytes.fromhex(extra["body_hex"])
        if len(body) % 8:
            raise WireError("section-length", section=extra["kind_code"], declared=len(body), expected=_align8(len(body)))
        sections.append((extra["kind_code"], extra["version"], extra["flags"], body))
    # The producer contract is the decoder's rule: one canonical order, and only the
    # `block` frame type carries these sections (plan v4 B.1). A fixture that needs the
    # refusal patches canonical bytes instead, which is what a hostile sender does.
    if frame["type"] != "block":
        raise WireError("not-a-block", frame_type=FRAME_TYPES[frame["type"]])
    _check_canonical_order(sections)
    directory = b"".join(
        struct.pack("<HBB I".replace(" ", ""), kind, version, flags, len(body))
        for kind, version, flags, body in sections
    )
    total = FRAME_HEADER_LEN + 4 + len(directory) + sum(len(body) for _, _, _, body in sections)
    if total > MAX_FRAME_BYTES:
        raise WireError("oversized-frame", len=total, max=MAX_FRAME_BYTES)
    out = bytearray()
    out += struct.pack("<IBBH I".replace(" ", ""), total, FRAME_TYPES[frame["type"]], FRAME_SECTIONED, 0, frame["corr"])
    out += struct.pack("<HH", len(sections), 0)
    out += directory
    for _, _, _, body in sections:
        out += body
    assert len(out) == total, "the declared frame length is the encoded length"
    return bytes(out)


def decode_frame(data: bytes) -> dict:
    """Decode canonical bytes into the fixture description."""
    if len(data) < FRAME_HEADER_LEN:
        raise WireError("truncated", context="frame header", need=FRAME_HEADER_LEN, have=len(data))
    length, type_code, flags, reserved, corr = struct.unpack_from("<IBBHI", data, 0)
    if type_code not in FRAME_TYPES_BY_CODE:
        raise WireError("unknown-frame-type", code=type_code)
    if flags & ~FRAME_SECTIONED:
        raise WireError("unknown-frame-flags", flags=flags)
    if reserved != 0:
        raise WireError("non-zero-reserved", value=reserved)
    # HOB-8: the declared length is bounded by the header parse itself (the Rust
    # ``FrameHeader::parse`` does the same), judged after the fixed fields and before any
    # comparison with the buffer, so a streaming reader may allocate ``length`` bytes.
    if length > MAX_FRAME_BYTES:
        raise WireError("oversized-frame", len=length, max=MAX_FRAME_BYTES)
    if length < FRAME_HEADER_LEN:
        raise WireError(
            "truncated", context="frame length shorter than the header", need=FRAME_HEADER_LEN, have=length
        )
    if len(data) < length:
        raise WireError("truncated", context="frame", need=length, have=len(data))
    if len(data) > length:
        raise WireError("trailing-bytes", extra=len(data) - length)
    if not flags & FRAME_SECTIONED:
        raise WireError("missing-section", section="header")
    # Plan v4 B.1: only `block` carries column sections. A block-shaped payload under
    # another frame type is type/payload confusion, refused before any section is read.
    if type_code != FRAME_TYPES["block"]:
        raise WireError("not-a-block", frame_type=type_code)
    # A block that ends inside its own directory header (the smallest legal declared
    # length is the 12-byte frame header) is a truncation, never a struct.error.
    if len(data) < FRAME_HEADER_LEN + 4:
        raise WireError("truncated", context="block directory", need=FRAME_HEADER_LEN + 4, have=len(data))
    n_sec, dir_reserved = struct.unpack_from("<HH", data, FRAME_HEADER_LEN)
    if dir_reserved != 0:
        raise WireError("section-reserved", value=dir_reserved)
    offset = FRAME_HEADER_LEN + 4
    directory = []
    for _ in range(n_sec):
        # A directory shorter than its own declared entry count is a truncation, not a
        # struct.error: the reference decoder refuses untrusted bytes with WireError
        # exactly the way the Rust decoder does.
        if offset + SECTION_DIR_ENTRY_LEN > len(data):
            raise WireError("truncated", context="block directory", need=offset + SECTION_DIR_ENTRY_LEN, have=len(data))
        kind, version, section_flags, body_len = struct.unpack_from("<HBBI", data, offset)
        offset += 8
        directory.append((kind, version, section_flags, body_len))
    _check_canonical_order(directory)
    bodies = []
    for kind, _version, _flags, body_len in directory:
        end = offset + body_len
        if end > len(data):
            raise WireError("truncated", context="section body", need=end, have=len(data))
        bodies.append(data[offset:end])
        offset = end
    if offset != len(data):
        raise WireError("trailing-bytes", extra=len(data) - offset)

    header = None
    skipped = []
    names = []
    for (kind, version, section_flags, _body_len), body in zip(directory, bodies, strict=True):
        if section_flags & ~1:
            raise WireError("unknown-section-flags", kind_code=kind, flags=section_flags)
        if kind not in SECTION_KINDS_BY_CODE:
            if section_flags & 1:
                raise WireError("unsupported-required-section", kind_code=kind)
            skipped.append(kind)
            continue
        if version != 1:
            raise WireError("unsupported-section-version", kind_code=kind, version=version, supported=1)
        if SECTION_KINDS_BY_CODE[kind] not in names:
            names.append(SECTION_KINDS_BY_CODE[kind])
        if kind == SECTION_KINDS["header"]:
            header = decode_header_body(body)
    if header is None:
        raise WireError("missing-section", section="header")

    frame: dict = {
        "type": FRAME_TYPES_BY_CODE[type_code],
        "corr": corr,
        "sections": names,
        "header": header,
        "rows": [],
        "time": [],
        "columns": [],
        "skipped": skipped,
    }
    n_rows = header["n_rows"]
    seen = set()
    saw_rows = False
    saw_time = False
    for (kind, _version, _flags, _body_len), body in zip(directory, bodies, strict=True):
        if kind not in SECTION_KINDS_BY_CODE:
            continue
        name = SECTION_KINDS_BY_CODE[kind]
        if kind in ONE_OF:
            if kind in seen:
                raise WireError("duplicate-section", kind_code=kind)
            seen.add(kind)
        if name == "header":
            continue
        if name == "rows":
            saw_rows = True
            frame["rows"] = decode_rows(body, n_rows)
        elif name == "time":
            saw_time = True
            frame["time"] = decode_time(body, n_rows)
        elif name == "columns":
            column = decode_column(body, n_rows)
            if any(existing["metric"] == column["metric"] for existing in frame["columns"]):
                raise WireError("conflicting-column", metric=column["metric"])
            frame["columns"].append(column)
        elif name == "dictionary":
            frame["dictionary"] = decode_dictionary(body)
    # Rows and buckets are exclusive by PRESENCE: a block carrying both sections is refused
    # even when both are empty, so the two layouts can never be mixed by accident.
    if saw_rows and saw_time:
        raise WireError("rows-and-time", rows=len(frame["rows"]), buckets=len(frame["time"]))
    # Values with neither an entity nor a bucket have no place in a block.
    if n_rows > 0 and not saw_rows and not saw_time:
        raise WireError("no-rows-or-time", n_rows=n_rows)
    dictionary = frame.get("dictionary")
    if dictionary is not None:
        if dictionary["generation"] != header["dict_gen"]:
            raise WireError("stale-dictionary-generation", needed=header["dict_gen"], held=dictionary["generation"])
        entries = dictionary["entries"]
        # A row addresses an incarnation and a column a metric: a reference of the right
        # range but the wrong entry kind would make a consumer read text as an id.
        for row in frame["rows"]:
            if row["ent"] >= len(entries):
                raise WireError("dangling-dictionary-ref", index=row["ent"], entries=len(entries))
            if entries[row["ent"]]["kind"] != "entity":
                raise WireError("row-reference-not-entity", index=row["ent"])
        for column in frame["columns"]:
            if column["metric"] >= len(entries):
                raise WireError("dangling-dictionary-ref", index=column["metric"], entries=len(entries))
            facts = entries[column["metric"]].get("facts")
            if facts is None:
                raise WireError("column-reference-not-metric", index=column["metric"])
            if facts["kind"] != column["kind"]:
                raise WireError(
                    "column-kind-mismatch",
                    index=column["metric"],
                    declared=COLUMN_KINDS[column["kind"]],
                    dictionary=COLUMN_KINDS[facts["kind"]],
                )
    return frame


def normalise(frame: dict) -> dict:
    """Drop keys a case does not declare, so a decode comparison is exact."""
    keys = ("type", "corr", "sections", "header", "rows", "time", "columns", "skipped")
    return {key: frame[key] for key in keys if key in frame}


def load_cases(fixtures_dir: Path) -> list[tuple[Path, dict, bytes]]:
    cases = []
    for spec_path in sorted(fixtures_dir.glob("*.json")):
        if spec_path.name == "quality.json":
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        data_path = spec_path.with_suffix(".bin")
        if not data_path.exists():
            raise SystemExit(f"fixture {spec_path.name} has no {data_path.name}")
        cases.append((spec_path, spec, data_path.read_bytes()))
    return cases


def check_case(spec: dict, data: bytes) -> None:
    """Assert the round trip both ways, or the declared refusal."""
    expect_error = spec.get("expect_error")
    if expect_error is not None:
        try:
            decode_frame(data)
        except WireError as error:
            # The fixture declares the refusal's *identity*; a case that does not spell out
            # a layout-dependent number (a byte offset, a length) does not have to pin it.
            actual = error.as_dict()
            for key, value in expect_error.items():
                if actual.get(key) != value:
                    raise AssertionError(
                        f"{spec['case']}: expected refusal {expect_error}, got {actual}"
                    ) from None
            return
        raise AssertionError(f"{spec['case']}: expected refusal {expect_error}, decode succeeded")
    encoded = encode_frame(spec["frame"])
    if encoded != data:
        raise AssertionError(
            f"{spec['case']}: the reference encoder does not reproduce the committed bytes "
            f"({len(encoded)} vs {len(data)} bytes)"
        )
    decoded = decode_frame(data)
    if normalise(decoded) != normalise(spec["frame"]):
        raise AssertionError(f"{spec['case']}: the reference decoder does not reproduce the fixture")


def main(argv: list[str]) -> int:
    fixtures = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2] / "fixtures" / "wire"
    cases = load_cases(fixtures)
    for spec_path, spec, data in cases:
        check_case(spec, data)
        print(f"{spec_path.stem}: ok")
    valid = sum(1 for _spec, spec, _data in cases if "expect_error" not in spec)
    refused = len(cases) - valid
    print(f"{len(cases)} fixtures round-trip in the reference codec ({valid} valid, {refused} refusals)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

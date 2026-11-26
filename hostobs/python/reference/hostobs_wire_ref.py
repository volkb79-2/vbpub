#!/usr/bin/env python3
"""Independent reference encoder/decoder for the hostobs/1 column codec (plan v4 §B).

This module is written **from the wire specification**
(``docs/WIRE.md`` §3 for the block layout), not from the Rust implementation. It is
deliberately small: one encoder, one decoder, the closed vocabularies, and typed
errors. It is not a client — no sockets, no scheduling, no store.

Standard library only (plan v4 §B.1: a Python reader is one ``memoryview`` and
``struct.unpack_from`` per column, so nothing here needs a dependency).

The fixtures under ``hostobs/fixtures/wire/`` are the shared contract:
``<case>.json`` is the decoded typed content of ``<case>.bin``. This module
encodes the JSON into canonical bytes and decodes the bytes back into the JSON,
and the Rust codec must agree on both directions; a divergence in either
implementation fails the Python conformance run (the registered `conformance` lane, HOB-4).
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

MAGIC = b"HOB\x01"
PROTO_MAJOR = 1
PROTO_MINOR = 1
FRAME_HEADER_LEN = 12
SECTION_DIR_ENTRY_LEN = 8
MAX_FRAME_BYTES = 16 * 1024 * 1024
HEADER_BODY_LEN = 80
MAX_ENTRY_TEXT = 256
MAX_DICTIONARY_ENTRIES = 65_536

FRAME_TYPES = {"req": 1, "rep": 2, "evt": 3, "dict": 4, "block": 5, "end": 6}
FRAME_TYPES_BY_CODE = {code: name for name, code in FRAME_TYPES.items()}

SECTION_KINDS = {"header": 1, "rows": 2, "columns": 3, "time": 4, "dictionary": 5, "subject": 6}
SECTION_KINDS_BY_CODE = {code: name for name, code in SECTION_KINDS.items()}
EVENT_SECTION_KIND = 7
EVENT_SECTION_VERSION = 1
EVENT_SECTION_REQUIRED_FLAGS = 1
ONE_OF = {1, 2, 4, 5, 6}
SUBJECT_BODY_LEN = 8

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
CANONICAL_RANK = {"header": 0, "dictionary": 1, "subject": 2, "rows": 3, "time": 3, "columns": 4}


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
    # WIRE.md 3.1/3.10: entry 0 must be the header. The rank check above already refuses a
    # known non-header kind there when a header follows; only an unknown kind is left.
    if sections and sections[0][0] not in SECTION_KINDS_BY_CODE:
        raise WireError("section-order", index=0, kind_code=sections[0][0])


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


def _event_error(kind: str, **fields: object) -> None:
    raise WireError(kind, **fields)


def _event_take(body: bytes, offset: int, count: int) -> tuple[bytes, int]:
    end = offset + count
    if end > len(body):
        raise WireError(
            "section-length",
            section="event",
            declared=len(body),
            expected=end,
        )
    return body[offset:end], end


def _event_u8(body: bytes, offset: int) -> tuple[int, int]:
    value, end = _event_take(body, offset, 1)
    return value[0], end


def _event_u16(body: bytes, offset: int) -> tuple[int, int]:
    value, end = _event_take(body, offset, 2)
    return struct.unpack("<H", value)[0], end


def _event_layout(body: bytes) -> tuple[tuple, tuple, int, int, int]:
    if len(body) < 48:
        raise WireError("truncated", context="event body", need=48, have=len(body))

    fixed = struct.unpack_from("<qQQQIIBBBBBBH", body, 0)
    offset = 48
    strings = []
    for _ in range(4):
        length, offset = _event_u16(body, offset)
        start = offset
        _raw, offset = _event_take(body, offset, length)
        strings.append((start, offset))

    n_ctx = fixed[9]
    ctx_start = offset
    for _ in range(n_ctx):
        _key, offset = _event_u8(body, offset)
        length, offset = _event_u16(body, offset)
        _value, offset = _event_take(body, offset, length)

    n_attrs = fixed[10]
    attrs_start = offset
    for _ in range(n_attrs):
        key_length, offset = _event_u16(body, offset)
        _key, offset = _event_take(body, offset, key_length)
        value_length, offset = _event_u16(body, offset)
        _value, offset = _event_take(body, offset, value_length)

    aligned = _align8(offset)
    if len(body) != aligned:
        raise WireError(
            "section-length",
            section="event",
            declared=len(body),
            expected=aligned,
        )
    return fixed, tuple(strings), ctx_start, attrs_start, offset


def _event_name(raw: bytes) -> bool:
    return bool(raw) and raw[0:1].islower() and raw[0:1].isascii() and all(
        byte == 0x2e
        or byte == 0x5f
        or byte == 0x2d
        or 0x30 <= byte <= 0x39
        or 0x61 <= byte <= 0x7a
        for byte in raw[1:]
    )


def _event_text(body: bytes, span: tuple[int, int], field: str, grammar: bool = False) -> str:
    raw = body[span[0] : span[1]]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise WireError("event-text", field=field) from None
    if any(byte < 0x20 or byte == 0x7F for byte in raw) or (grammar and not _event_name(raw)):
        raise WireError("event-text", field=field)
    return text


def _event_validate_codes(body: bytes, fixed: tuple, ctx_start: int) -> None:
    kind, outcome, n_ctx = fixed[6], fixed[7], fixed[9]
    if kind not in (1, 2, 3):
        raise WireError("event-unknown-code", field="kind", code=kind)
    if outcome > 7:
        raise WireError("event-unknown-code", field="outcome", code=outcome)
    if (kind == 2) != (outcome != 0):
        raise WireError("event-unknown-code", field="outcome/kind", code=outcome)

    offset = ctx_start
    for _ in range(n_ctx):
        key, offset = _event_u8(body, offset)
        if key > 4:
            raise WireError("event-unknown-code", field="ctx", code=key)
        length, offset = _event_u16(body, offset)
        _value, offset = _event_take(body, offset, length)


def _event_validate_bounds(body: bytes, fixed: tuple, strings: tuple, ctx_start: int, attrs_start: int) -> None:
    t_mono_ns, span, peer_dev, peer_inode = fixed[:4]
    kind, n_ctx, n_attrs = fixed[6], fixed[9], fixed[10]
    if t_mono_ns < 0:
        raise WireError("event-field-bound", field="t_mono_ns")
    if (kind == 3) != (span == 0):
        raise WireError("event-field-bound", field="span")
    if (peer_dev == 0) != (peer_inode == 0):
        raise WireError("event-field-bound", field="peer")
    if n_ctx > 5:
        raise WireError("event-field-bound", field="n_ctx")
    if n_attrs > 8:
        raise WireError("event-field-bound", field="n_attrs")

    bounds = ((1, 64, "name"), (1, 64, "key"), (0, 64, "code"), (0, 15, "comm"))
    for (start, end), (minimum, maximum, field) in zip(strings, bounds):
        length = end - start
        if length < minimum or length > maximum:
            raise WireError("event-field-bound", field=field)

    offset = ctx_start
    for _ in range(n_ctx):
        _key, offset = _event_u8(body, offset)
        length, offset = _event_u16(body, offset)
        if not 1 <= length <= 128:
            raise WireError("event-field-bound", field="ctx.value")
        _value, offset = _event_take(body, offset, length)

    offset = attrs_start
    for _ in range(n_attrs):
        key_length, offset = _event_u16(body, offset)
        if not 1 <= key_length <= 64:
            raise WireError("event-field-bound", field="attrs.key")
        _key, offset = _event_take(body, offset, key_length)
        value_length, offset = _event_u16(body, offset)
        if value_length > 256:
            raise WireError("event-field-bound", field="attrs.value")
        _value, offset = _event_take(body, offset, value_length)


def _event_validate_text(body: bytes, strings: tuple, ctx_start: int, attrs_start: int, fixed: tuple) -> None:
    for span, field in zip(strings, ("name", "key", "code", "comm")):
        _event_text(body, span, field, grammar=field == "name")

    offset = ctx_start
    for _ in range(fixed[9]):
        _key, offset = _event_u8(body, offset)
        length, offset = _event_u16(body, offset)
        start = offset
        _value, offset = _event_take(body, offset, length)
        _event_text(body, (start, offset), "ctx.value")

    offset = attrs_start
    for _ in range(fixed[10]):
        key_length, offset = _event_u16(body, offset)
        key_start = offset
        _key, offset = _event_take(body, offset, key_length)
        value_length, offset = _event_u16(body, offset)
        value_start = offset
        _value, offset = _event_take(body, offset, value_length)
        _event_text(body, (key_start, key_start + key_length), "attrs.key", grammar=True)
        _event_text(body, (value_start, offset), "attrs.value")


def _event_validate_order(body: bytes, fixed: tuple, ctx_start: int, attrs_start: int) -> None:
    previous_context = None
    offset = ctx_start
    for index in range(fixed[9]):
        key, offset = _event_u8(body, offset)
        if previous_context is not None and key <= previous_context:
            raise WireError("event-order", field="ctx", index=index)
        previous_context = key
        length, offset = _event_u16(body, offset)
        _value, offset = _event_take(body, offset, length)

    previous_key = None
    offset = attrs_start
    for index in range(fixed[10]):
        key_length, offset = _event_u16(body, offset)
        key, offset = _event_take(body, offset, key_length)
        if previous_key is not None and key <= previous_key:
            raise WireError("event-order", field="attrs", index=index)
        previous_key = key
        value_length, offset = _event_u16(body, offset)
        _value, offset = _event_take(body, offset, value_length)


def _event_encode_text(value: object, field: str, minimum: int, maximum: int, grammar: bool = False) -> bytes:
    if not isinstance(value, str):
        raise WireError("event-field-bound", field=field)
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        raise WireError("event-text", field=field) from None
    if len(raw) < minimum or len(raw) > maximum:
        raise WireError("event-field-bound", field=field)
    if any(byte < 0x20 or byte == 0x7F for byte in raw) or (grammar and not _event_name(raw)):
        raise WireError("event-text", field=field)
    return raw


def encode_event_payload(event: dict) -> bytes:
    """Encode one event/1 section body, including zero padding to an eight-byte boundary."""
    required = {
        "t_mono_ns", "span", "peer_dev", "peer_inode", "uid", "peer_pid", "kind",
        "outcome", "inferred", "ctx", "attrs", "name", "key", "code", "comm",
    }
    if not isinstance(event, dict) or set(event) != required:
        raise WireError("event-field-bound", field="shape")

    kind = event["kind"]
    outcome = event["outcome"]
    if type(kind) is not int or kind not in (1, 2, 3):
        raise WireError("event-unknown-code", field="kind", code=kind)
    if type(outcome) is not int or outcome < 0 or outcome > 7:
        raise WireError("event-unknown-code", field="outcome", code=outcome)
    if (kind == 2) != (outcome != 0):
        raise WireError("event-unknown-code", field="outcome/kind", code=outcome)

    ctx = event["ctx"]
    attrs = event["attrs"]
    if not isinstance(ctx, list) or not isinstance(attrs, list):
        raise WireError("event-field-bound", field="collections")
    for entry in ctx:
        if not isinstance(entry, dict) or set(entry) != {"key", "value"}:
            raise WireError("event-field-bound", field="ctx")
        key = entry["key"]
        if type(key) is not int or key < 0 or key > 4:
            raise WireError("event-unknown-code", field="ctx", code=key)

    def integer(field: str, minimum: int, maximum: int) -> int:
        value = event[field]
        if type(value) is not int or value < minimum or value > maximum:
            raise WireError("event-field-bound", field=field)
        return value

    t_mono_ns = integer("t_mono_ns", 0, (1 << 63) - 1)
    span = integer("span", 0, (1 << 64) - 1)
    peer_dev = integer("peer_dev", 0, (1 << 64) - 1)
    peer_inode = integer("peer_inode", 0, (1 << 64) - 1)
    uid = integer("uid", 0, (1 << 32) - 1)
    peer_pid = integer("peer_pid", 0, (1 << 32) - 1)
    if (kind == 3) != (span == 0):
        raise WireError("event-field-bound", field="span")
    if (peer_dev == 0) != (peer_inode == 0):
        raise WireError("event-field-bound", field="peer")
    if len(ctx) > 5:
        raise WireError("event-field-bound", field="n_ctx")
    if len(attrs) > 8:
        raise WireError("event-field-bound", field="n_attrs")
    if type(event["inferred"]) is not bool:
        raise WireError("event-field-bound", field="inferred")

    name = _event_encode_text(event["name"], "name", 1, 64, grammar=True)
    key = _event_encode_text(event["key"], "key", 1, 64)
    code_value = event["code"]
    comm_value = event["comm"]
    code = b"" if code_value is None else _event_encode_text(code_value, "code", 1, 64)
    comm = b"" if comm_value is None else _event_encode_text(comm_value, "comm", 1, 15)

    for entry in ctx:
        _event_encode_text(entry["value"], "ctx.value", 1, 128)
    for entry in attrs:
        if not isinstance(entry, dict) or set(entry) != {"key", "value"}:
            raise WireError("event-field-bound", field="attrs")
        _event_encode_text(entry["key"], "attrs.key", 1, 64, grammar=True)
        _event_encode_text(entry["value"], "attrs.value", 0, 256)

    for index in range(1, len(ctx)):
        if ctx[index - 1]["key"] >= ctx[index]["key"]:
            raise WireError("event-order", field="ctx", index=index)
    attr_keys = [entry["key"].encode("utf-8") for entry in attrs]
    for index in range(1, len(attr_keys)):
        if attr_keys[index - 1] >= attr_keys[index]:
            raise WireError("event-order", field="attrs", index=index)

    out = bytearray(
        struct.pack(
            "<qQQQIIBBBBBBH",
            t_mono_ns,
            span,
            peer_dev,
            peer_inode,
            uid,
            peer_pid,
            kind,
            outcome,
            int(event["inferred"]),
            len(ctx),
            len(attrs),
            0,
            0,
        )
    )

    def put_string(value: bytes) -> None:
        out.extend(struct.pack("<H", len(value)))
        out.extend(value)

    for value in (name, key, code, comm):
        put_string(value)
    for entry in ctx:
        value = entry["value"].encode("utf-8")
        out.extend(struct.pack("<B", entry["key"]))
        put_string(value)
    for entry in attrs:
        put_string(entry["key"].encode("utf-8"))
        put_string(entry["value"].encode("utf-8"))
    out.extend(b"\x00" * (-len(out) % 8))
    return bytes(out)


def decode_event_payload(body: bytes) -> dict:
    """Decode one event/1 section body in the normative refusal order."""
    fixed, strings, ctx_start, attrs_start, content_end = _event_layout(body)
    flags = fixed[8]
    if flags & 0xFE:
        raise WireError("non-zero-reserved", value=flags & 0xFE)
    if fixed[11] != 0:
        raise WireError("non-zero-reserved", value=fixed[11])
    if fixed[12] != 0:
        raise WireError("non-zero-reserved", value=fixed[12])
    for byte in body[content_end:]:
        if byte:
            raise WireError("non-zero-reserved", value=byte)

    _event_validate_codes(body, fixed, ctx_start)
    _event_validate_bounds(body, fixed, strings, ctx_start, attrs_start)
    _event_validate_text(body, strings, ctx_start, attrs_start, fixed)
    _event_validate_order(body, fixed, ctx_start, attrs_start)

    ctx = []
    offset = ctx_start
    for _ in range(fixed[9]):
        key_code, offset = _event_u8(body, offset)
        length, offset = _event_u16(body, offset)
        raw, offset = _event_take(body, offset, length)
        ctx.append({"key": key_code, "value": raw.decode("utf-8")})

    attrs = []
    offset = attrs_start
    for _ in range(fixed[10]):
        key_length, offset = _event_u16(body, offset)
        key_raw, offset = _event_take(body, offset, key_length)
        value_length, offset = _event_u16(body, offset)
        value_raw, offset = _event_take(body, offset, value_length)
        attrs.append({"key": key_raw.decode("utf-8"), "value": value_raw.decode("utf-8")})

    texts = [
        _event_text(body, strings[index], field, grammar=field == "name")
        for index, field in enumerate(("name", "key", "code", "comm"))
    ]
    return {
        "t_mono_ns": fixed[0],
        "span": fixed[1],
        "peer_dev": fixed[2],
        "peer_inode": fixed[3],
        "uid": fixed[4],
        "peer_pid": fixed[5],
        "kind": fixed[6],
        "outcome": fixed[7],
        "inferred": bool(flags & 1),
        "ctx": ctx,
        "attrs": attrs,
        "name": texts[0],
        "key": texts[1],
        "code": texts[2] or None,
        "comm": texts[3] or None,
    }


def encode_event_frame(event: dict) -> bytes:
    """Encode the one-section EVT+SECTIONED frame stored in the events corpus."""
    body = encode_event_payload(event)
    total = FRAME_HEADER_LEN + 4 + 8 + len(body)
    if total > MAX_FRAME_BYTES:
        raise WireError("oversized-frame", len=total, max=MAX_FRAME_BYTES)
    return (
        struct.pack("<IBBHI", total, FRAME_TYPES["evt"], FRAME_SECTIONED, 0, 0)
        + struct.pack(
            "<HHHBBI",
            1,
            0,
            EVENT_SECTION_KIND,
            EVENT_SECTION_VERSION,
            EVENT_SECTION_REQUIRED_FLAGS,
            len(body),
        )
        + body
    )


def event_body_from_frame(frame: bytes) -> bytes:
    """Return the body from one canonical stored event frame."""
    if len(frame) < 24:
        raise WireError("truncated", context="event frame", need=24, have=len(frame))
    length, frame_type, flags, reserved, corr = struct.unpack_from("<IBBHI", frame, 0)
    if frame_type != FRAME_TYPES["evt"]:
        raise WireError("unknown-frame-type", code=frame_type)
    if flags != FRAME_SECTIONED:
        raise WireError("unknown-frame-flags", flags=flags)
    if reserved != 0 or corr != 0:
        raise WireError("non-zero-reserved", value=reserved or corr)
    if length > MAX_FRAME_BYTES:
        raise WireError("oversized-frame", len=length, max=MAX_FRAME_BYTES)
    if length != len(frame):
        raise WireError("truncated", context="event frame", need=length, have=len(frame))
    if len(frame) % 8:
        raise WireError(
            "section-length",
            section=EVENT_SECTION_KIND,
            declared=len(frame),
            expected=_align8(len(frame)),
        )
    n_sec, directory_reserved, kind, version, section_flags, body_len = struct.unpack_from(
        "<HHHBBI", frame, 12
    )
    if directory_reserved != 0:
        raise WireError("section-reserved", value=directory_reserved)
    if n_sec != 1:
        raise WireError("section-length", section="event directory", declared=n_sec, expected=1)
    if kind != EVENT_SECTION_KIND:
        raise WireError("unsupported-required-section", kind_code=kind)
    if version != EVENT_SECTION_VERSION:
        raise WireError(
            "unsupported-section-version",
            kind_code=kind,
            version=version,
            supported=EVENT_SECTION_VERSION,
        )
    if section_flags != EVENT_SECTION_REQUIRED_FLAGS:
        raise WireError("unknown-section-flags", kind_code=kind, flags=section_flags)
    if body_len != len(frame) - 24:
        raise WireError(
            "section-length",
            section="event",
            declared=body_len,
            expected=len(frame) - 24,
        )
    return frame[24:]


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
    """Decode a dictionary body, refusing exactly what Rust `Dictionary::decode` refuses.

    The checks run in the same order as the Rust cursor reads them, so one hostile body names
    one error in both languages and no `struct.error` or `UnicodeDecodeError` escapes.
    """
    position = 0

    def take(count: int) -> bytes:
        nonlocal position
        end = position + count
        if end > len(body):
            raise WireError("truncated", context="dictionary section", need=end, have=len(body))
        chunk = body[position:end]
        position = end
        return chunk

    (generation,) = struct.unpack("<I", take(4))
    (count,) = struct.unpack("<I", take(4))
    if count > MAX_DICTIONARY_ENTRIES:
        raise WireError("dictionary-too-large", count=count, max=MAX_DICTIONARY_ENTRIES)
    entries = []
    seen_identities: set[tuple] = set()
    for index in range(count):
        kind_code = take(1)[0]
        flags = take(1)[0]
        if flags != 0:
            raise WireError("dictionary-entry-reserved", index=index, flags=flags)
        (length,) = struct.unpack("<H", take(2))
        if length > MAX_ENTRY_TEXT:
            raise WireError("dictionary-entry-too-long", index=index, len=length)
        if kind_code not in DICTIONARY_KINDS_BY_CODE:
            raise WireError("unknown-dictionary-kind", index=index, code=kind_code)
        entry: dict = {"kind": DICTIONARY_KINDS_BY_CODE[kind_code]}
        if kind_code == DICTIONARY_KINDS["entity"]:
            (inc,) = struct.unpack("<Q", take(8))
            entry["inc"] = inc
        elif kind_code == DICTIONARY_KINDS["metric"]:
            unit = take(1)[0]
            if unit not in UNITS_BY_CODE:
                raise WireError("dictionary-entry-unknown-code", index=index, code=unit)
            kind = take(1)[0]
            if kind not in COLUMN_KINDS_BY_CODE:
                raise WireError("unknown-column-kind", code=kind)
            scope = take(1)[0]
            if scope not in SCOPES_BY_CODE:
                raise WireError("dictionary-entry-unknown-code", index=index, code=scope)
            source = take(1)[0]
            if source not in SOURCES_BY_CODE:
                raise WireError("dictionary-entry-unknown-code", index=index, code=source)
            (mask,) = struct.unpack("<H", take(2))
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
            entry["label_kind"] = take(1)[0]
            entry["label_source"] = take(1)[0]
        raw_text = take(length)
        try:
            entry["text"] = raw_text.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise WireError("dictionary-entry-not-utf8", index=index) from None
        # A wire list is the sender's numbering: entry `index` must be at index `index`.
        # A repeated identity is a corrupt frame, not a list to de-duplicate, because
        # de-duplicating would shift every later index away from what the sender wrote. It is
        # judged before the text's control bytes, as the Rust `intern` does.
        identity = _entry_identity(entry)
        if identity in seen_identities:
            raise WireError("duplicate-dictionary-entry", index=index)
        for offset, byte in enumerate(raw_text):
            if byte < 0x20 or byte == 0x7F:
                raise WireError("dictionary-entry-control", index=index, offset=offset)
        seen_identities.add(identity)
        entries.append(entry)
    for _ in range(_align8(position) - position):
        pad_byte = take(1)[0]
        if pad_byte != 0:
            raise WireError("non-zero-reserved", value=pad_byte)
    if position != len(body):
        raise WireError("trailing-bytes", extra=len(body) - position)
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
    # The body length is exact, so only the pad bytes are left; a non-zero one is corrupt
    # (Rust `Cursor::pad_to_end`).
    for byte in body[offset:_align8(offset)]:
        if byte != 0:
            raise WireError("non-zero-reserved", value=byte)
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
    # The aggregation bytes are judged before the pad, as the Rust decoder reads them first.
    for code in aggs:
        if code not in AGGREGATIONS_BY_CODE:
            raise WireError("unknown-aggregation", code=code)
    for byte in body[offset : _align8(offset)]:
        if byte != 0:
            raise WireError("non-zero-reserved", value=byte)
    buckets = []
    for index in range(n_buckets):
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
            "subject": lambda: struct.pack("<II", frame["subject"], 0),
        }[name]()
        sections.append((SECTION_KINDS[name], 1, 1 if name in ("header", "subject") else 0, body))
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


def decode_frame(data: bytes, held: dict | None = None) -> dict:
    """Decode canonical bytes into the fixture description.

    The checks run in the WIRE.md 3.10 order, so the first failing check names the error.
    ``held`` is the stream's held dictionary (``{"generation", "entries"}``): a block without
    an in-frame dictionary is validated against it (HOB-46); ``None`` does no reference check
    on such a block.
    """
    # The fixed fields are read in order and each is judged as soon as its bytes exist, as the
    # Rust `FrameHeader::parse` cursor does: a 5 to 11 byte buffer with an unknown type byte is
    # `unknown-frame-type`, not `truncated` (WIRE.md 3.10).
    def need(end: int) -> None:
        if len(data) < end:
            raise WireError("truncated", context="frame header", need=end, have=len(data))

    need(4)
    (length,) = struct.unpack_from("<I", data, 0)
    need(5)
    type_code = data[4]
    if type_code not in FRAME_TYPES_BY_CODE:
        raise WireError("unknown-frame-type", code=type_code)
    need(6)
    flags = data[5]
    # SECTIONED is defined for BLOCK only (WIRE.md 2.1): on any other frame it is an unknown flag.
    if flags & ~FRAME_SECTIONED or (flags & FRAME_SECTIONED and type_code != FRAME_TYPES["block"]):
        raise WireError("unknown-frame-flags", flags=flags)
    need(8)
    (reserved,) = struct.unpack_from("<H", data, 6)
    if reserved != 0:
        raise WireError("non-zero-reserved", value=reserved)
    need(FRAME_HEADER_LEN)
    (corr,) = struct.unpack_from("<I", data, 8)
    # HOB-8: the declared length is bounded by the header parse itself (the Rust
    # ``FrameHeader::parse`` does the same), judged after the fixed fields and before any
    # comparison with the buffer, so a streaming reader may allocate ``length`` bytes.
    if length > MAX_FRAME_BYTES:
        raise WireError("oversized-frame", len=length, max=MAX_FRAME_BYTES)
    if length < FRAME_HEADER_LEN:
        raise WireError(
            "truncated", context="frame length shorter than the header", need=FRAME_HEADER_LEN, have=length
        )
    # Plan v4 B.1: only `block` carries column sections. A block-shaped payload under
    # another frame type is type/payload confusion, refused before the buffer is judged
    # (WIRE.md 3.10: not-a-block precedes truncated and trailing-bytes).
    if type_code != FRAME_TYPES["block"]:
        raise WireError("not-a-block", frame_type=type_code)
    if len(data) < length:
        raise WireError("truncated", context="frame", need=length, have=len(data))
    if len(data) > length:
        raise WireError("trailing-bytes", extra=len(data) - length)
    if not flags & FRAME_SECTIONED:
        raise WireError("missing-section", section="header")
    # A block that ends inside its own directory header (the smallest legal declared
    # length is the 12-byte frame header) is a truncation, never a struct.error. The need and
    # have are the directory's own, as in Rust: the bytes after the 12-byte frame header.
    have = len(data) - FRAME_HEADER_LEN
    if have < 2:
        raise WireError("truncated", context="block directory", need=2, have=have)
    if have < 4:
        raise WireError("truncated", context="block directory", need=4, have=have)
    n_sec, dir_reserved = struct.unpack_from("<HH", data, FRAME_HEADER_LEN)
    if dir_reserved != 0:
        raise WireError("section-reserved", value=dir_reserved)
    offset = FRAME_HEADER_LEN + 4
    # HOB-50: the directory's bytes must be present before anything is built from `n_sec`.
    directory_bytes = n_sec * SECTION_DIR_ENTRY_LEN
    if len(data) - offset < directory_bytes:
        raise WireError("truncated", context="block directory", need=directory_bytes, have=len(data) - offset)
    directory = []
    for _ in range(n_sec):
        kind, version, section_flags, body_len = struct.unpack_from("<HBBI", data, offset)
        offset += 8
        directory.append((kind, version, section_flags, body_len))
    # Every section length is a multiple of 8, unknown optional kinds included (HOB-51); it is
    # judged before the order so a frame with both defects reports the length (WIRE.md 3.10).
    for kind, _version, _flags, body_len in directory:
        if body_len % 8 != 0:
            raise WireError("section-length", section=kind, declared=body_len, expected=_align8(body_len))
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
        # The header and the subject are required sections: their directory entry carries
        # the required flag (WIRE.md 3.1, 3.8); a clear flag is refused as a flags defect.
        if kind in (SECTION_KINDS["header"], SECTION_KINDS["subject"]) and not section_flags & 1:
            raise WireError("unknown-section-flags", kind_code=kind, flags=section_flags)
        if SECTION_KINDS_BY_CODE[kind] not in names:
            names.append(SECTION_KINDS_BY_CODE[kind])
        # Only the FIRST header's body is decoded here: a second header is `duplicate-section`
        # below, whatever its body holds (as Rust does).
        if kind == SECTION_KINDS["header"] and header is None:
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
    seen_metrics = set()
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
            if column["metric"] in seen_metrics:
                raise WireError("conflicting-column", metric=column["metric"])
            seen_metrics.add(column["metric"])
            frame["columns"].append(column)
        elif name == "dictionary":
            frame["dictionary"] = decode_dictionary(body)
        elif name == "subject":
            frame["subject"] = decode_subject(body)
    # Rows and buckets are exclusive by PRESENCE: a block carrying both sections is refused
    # even when both are empty, so the two layouts can never be mixed by accident.
    if saw_rows and saw_time:
        raise WireError("rows-and-time", rows=len(frame["rows"]), buckets=len(frame["time"]))
    # Values with neither an entity nor a bucket have no place in a block.
    if n_rows > 0 and not saw_rows and not saw_time:
        raise WireError("no-rows-or-time", n_rows=n_rows)
    # A subject names the incarnation a history block describes: it needs the time section.
    if "subject" in frame and not saw_time:
        raise WireError("subject-without-time", subject=frame["subject"])
    # The in-frame dictionary wins; a bare block is judged against the held one (HOB-46).
    dictionary = frame.get("dictionary")
    if dictionary is None:
        dictionary = held
    if dictionary is not None:
        if dictionary["generation"] != header["dict_gen"]:
            raise WireError("stale-dictionary-generation", needed=header["dict_gen"], held=dictionary["generation"])
        entries = dictionary["entries"]
        if "subject" in frame:
            ent = frame["subject"]
            if ent >= len(entries):
                raise WireError("dangling-dictionary-ref", index=ent, entries=len(entries))
            if entries[ent]["kind"] != "entity":
                raise WireError("row-reference-not-entity", index=ent)
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
    # Every bucket's aggregation must be permitted by every column's mask (HOB-47). The mask
    # follows the column's own kind, which a dictionary (in-frame or held) has just proven equal
    # to the metric's, so the check needs no dictionary and also runs with `held = None`.
    for bucket in frame["time"]:
        agg = AGGREGATIONS[bucket["agg"]]
        for column in frame["columns"]:
            if not AGGREGATION_MASKS[column["kind"]] & (1 << agg):
                raise WireError("aggregation-not-permitted", metric=column["metric"], agg=agg)
    return frame


def decode_subject(body: bytes) -> int:
    """The subject section body: ``u32 ent | u32 0`` (WIRE.md 3.8)."""
    if len(body) != SUBJECT_BODY_LEN:
        raise WireError("section-length", section="subject", declared=len(body), expected=SUBJECT_BODY_LEN)
    ent, reserved = struct.unpack_from("<II", body, 0)
    if reserved != 0:
        raise WireError("unknown-section-flags", kind_code=SECTION_KINDS["subject"], flags=reserved)
    return ent


#: The same function under the name WIRE.md 12 gives the decoder.
decode_block = decode_frame


def normalise(frame: dict) -> dict:
    """Drop keys a case does not declare, so a decode comparison is exact."""
    keys = ("type", "corr", "sections", "header", "subject", "rows", "time", "columns", "skipped")
    return {key: frame[key] for key in keys if key in frame}


def load_cases(fixtures_dir: Path) -> list[tuple[Path, dict, bytes]]:
    cases = []
    for spec_path in sorted(fixtures_dir.glob("*.json")):
        if spec_path.name == "quality.json" or spec_path.stem.startswith("event-"):
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
            decode_frame(data, spec.get("held"))
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
    decoded = decode_frame(data, spec.get("held"))
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

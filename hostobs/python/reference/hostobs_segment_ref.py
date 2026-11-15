#!/usr/bin/env python3
"""Independent reference encoder/decoder for a hostobs segment file (plan v4 §C.5).

This module is written **from the format specification**
(``hostobs/docs/SEGMENT-FORMAT.md``), not from the Rust implementation in
``crates/store``. The two are bound by committed fixtures under
``hostobs/fixtures/segment/``: this module generates ``<case>.seg`` from
``<case>.json`` and decodes it back, and the Rust test
``crates/store/tests/golden_segment.rs`` must decode the same bytes into the same
facts. A layout change that only one side makes turns a lane red.

Standard library only: a segment is a fixed-width little-endian envelope, and
Python needs ``struct.unpack_from`` and one ``memoryview`` for it — no dependency.

The envelope exists because a *file* is not a stream: a crash, a full disk or a
killed process can leave a record half-written, and the frames after it are still
perfectly readable. Every record therefore carries its own total length, its own
body checksum and its own sequence number, so a torn tail is a detected fact
rather than a lucky parse.

Layout (little-endian, every field fixed width):

    SEGMENT := HEADER RECORD* FOOTER?

    HEADER (64)  0 magic7 "HOBSEG\\x01"     7 u8 0               8 u16 version=1
                10 u16 header_len=64      12 u64 seq           20 u64 epoch
                28 u32 dict_generation    32 u8 kind            33 u8 payload
                34 u16 0                  36 i64 start_real_ns  44 u32 max_payload
                48 u32 max_incarnations   52 u32 0              56 u32 header_crc
                60 u32 0

    RECORD (48 + body + pad)              pad = (8 - body % 8) % 8, zero bytes
                body = ids[8 x n_incarnations] || payload
                0 magic4 "HOBR"           4 u32 record_len       8 u64 seq
                16 u8 kind                17 u8 flags           18 u16 0
                20 u32 body_len           24 i64 real_ns         32 u16 n_incarnations
                34 u16 0                  36 u32 body_crc       40 u8 pad
                41 u8[3] 0                44 u32 header_crc

    FOOTER (64)  0 magic4 "HOBF"          4 u32 footer_len=64    8 u64 seq
                16 u64 record_count       24 u64 end_offset     32 u32 dict_generation
                36 u32 0                  40 i64 first_real_ns  48 i64 last_real_ns
                56 u32 footer_crc         60 u32 0
"""

from __future__ import annotations

import struct
from pathlib import Path

SEGMENT_MAGIC = b"HOBSEG\x01"
SEGMENT_VERSION = 1
HEADER_LEN = 64
RECORD_MAGIC = b"HOBR"
RECORD_HEADER_LEN = 48
FOOTER_MAGIC = b"HOBF"
FOOTER_LEN = 64
MAX_RECORD_PAD = 7

#: Payload kind 1 is a ``proto`` BLOCK frame (history), 2 a ``proto`` EVT frame (events).
PAYLOAD_BLOCK = 1
PAYLOAD_EVT = 2
#: Record kind 1 carries a block payload, 2 an event payload.
RECORD_BLOCK = 1
RECORD_EVT = 2
#: Corpus byte: 1 history, 2 events. The payload kind and the record kind follow it.
TIER_KINDS = {"history": 1, "events": 2}
PAYLOAD_KINDS = {1: "block", 2: "evt"}
RECORD_KINDS = {1: "block", 2: "evt"}
#: ``proto`` frame type bytes (plan v4 §B.1): BLOCK 5, EVT 3.
FRAME_TYPES = {"block": 5, "evt": 3}
FRAME_HEADER_LEN = 12
FRAME_FLAG_SECTIONED = 0b0000_0001


class SegmentError(ValueError):
    """A typed refusal, named the way the Rust ``StoreError::kind`` names it."""

    def __init__(self, refusal: str, **fields: object) -> None:
        super().__init__(refusal)
        self.refusal = refusal
        self.fields = fields

    def as_dict(self) -> dict:
        return {"error": self.refusal, **self.fields}

    def __str__(self) -> str:
        detail = ", ".join(f"{key}={value!r}" for key, value in self.fields.items())
        return f"{self.refusal} ({detail})" if detail else self.refusal


def crc32(data: bytes) -> int:
    """The reflected IEEE CRC-32 (poly ``0xEDB8_8320``), the zlib/PNG one.

    Bitwise, because the definition must be re-derivable by hand from the
    polynomial in the format document; the standard check value is asserted in the
    tests, so a wrong implementation is red rather than self-consistent.
    """
    crc = 0xFFFF_FFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB8_8320
            else:
                crc >>= 1
    return crc ^ 0xFFFF_FFFF


def pad_for(body_len: int) -> int:
    """The zero padding a record of ``body_len`` body bytes carries."""
    return (8 - body_len % 8) % 8


def encode_header(header: dict) -> bytes:
    """Encode the 64-byte segment header."""
    kind = TIER_KINDS[header["kind"]]
    payload = PAYLOAD_BLOCK if header["kind"] == "history" else PAYLOAD_EVT
    declared = header.get("payload", PAYLOAD_KINDS[payload])
    if declared != PAYLOAD_KINDS[payload]:
        raise SegmentError("payload-kind", found=declared)
    body = bytearray()
    body += SEGMENT_MAGIC
    body += struct.pack("<B", 0)
    body += struct.pack("<H", SEGMENT_VERSION)
    body += struct.pack("<H", HEADER_LEN)
    body += struct.pack("<Q", header["seq"])
    body += struct.pack("<Q", header["epoch"])
    body += struct.pack("<I", header["dict_generation"])
    body += struct.pack("<B", kind)
    body += struct.pack("<B", payload)
    body += struct.pack("<H", 0)
    body += struct.pack("<q", header["start_real_ns"])
    body += struct.pack("<I", header["max_payload"])
    body += struct.pack("<I", header["max_incarnations"])
    body += struct.pack("<I", 0)
    assert len(body) == HEADER_LEN - 8, "the checksum covers everything before it"
    # The checksum covers the 56 bytes written so far, never the field it lives in.
    body += struct.pack("<I", crc32(bytes(body)))
    body += struct.pack("<I", 0)
    assert len(body) == HEADER_LEN
    return bytes(body)


def encode_record(record: dict, index: int, tier_kind: str) -> bytes:
    """Encode one record: its 48-byte header, its body and its zero padding.

    The record's kind follows the segment's corpus, never a per-record choice: a history
    segment holds block payloads and an events segment holds event payloads.
    """
    payload = bytes.fromhex(record["payload_hex"])
    incarnations = list(record.get("incarnations", []))
    kind = RECORD_EVT if tier_kind == "events" else RECORD_BLOCK
    if record.get("kind", RECORD_KINDS[kind]) != RECORD_KINDS[kind]:
        raise SegmentError("record-kind", index=index, found=record["kind"])
    body = b"".join(struct.pack("<Q", inc) for inc in incarnations) + payload
    pad = pad_for(len(body))
    total = RECORD_HEADER_LEN + len(body) + pad
    header = bytearray()
    header += RECORD_MAGIC
    header += struct.pack("<I", total)
    header += struct.pack("<Q", record.get("seq", index))
    header += struct.pack("<B", kind)
    header += struct.pack("<B", 0)
    header += struct.pack("<H", 0)
    header += struct.pack("<I", len(body))
    header += struct.pack("<q", record["real_ns"])
    header += struct.pack("<H", len(incarnations))
    header += struct.pack("<H", 0)
    header += struct.pack("<I", crc32(body))
    header += struct.pack("<B", pad)
    header += struct.pack("<3B", 0, 0, 0)
    assert len(header) == RECORD_HEADER_LEN - 4
    header += struct.pack("<I", crc32(bytes(header)))
    assert len(header) == RECORD_HEADER_LEN
    return bytes(header) + body + b"\x00" * pad


def encode_footer(footer: dict, header: dict, records: list[dict]) -> bytes:
    """Encode the 64-byte footer, deriving the facts the records themselves fix."""
    end_offset = HEADER_LEN + sum(
        len(encode_record(record, index, header["kind"]))
        for index, record in enumerate(records)
    )
    count = len(records)
    first = records[0]["real_ns"] if records else 0
    last = records[-1]["real_ns"] if records else 0
    declared = {
        "seq": header["seq"],
        "record_count": count,
        "end_offset": end_offset,
        "dict_generation": header["dict_generation"],
        "first_real_ns": first,
        "last_real_ns": last,
    }
    for key, value in footer.items():
        if declared.get(key) != value:
            raise SegmentError("footer-mismatch", field=key, declared=value, measured=declared.get(key))
    body = bytearray()
    body += FOOTER_MAGIC
    body += struct.pack("<I", FOOTER_LEN)
    body += struct.pack("<Q", declared["seq"])
    body += struct.pack("<Q", declared["record_count"])
    body += struct.pack("<Q", declared["end_offset"])
    body += struct.pack("<I", declared["dict_generation"])
    body += struct.pack("<I", 0)
    body += struct.pack("<q", declared["first_real_ns"])
    body += struct.pack("<q", declared["last_real_ns"])
    assert len(body) == FOOTER_LEN - 8
    body += struct.pack("<I", crc32(bytes(body)))
    body += struct.pack("<I", 0)
    assert len(body) == FOOTER_LEN
    return bytes(body)


def encode_segment(spec: dict) -> bytes:
    """Encode a whole segment from its fixture description."""
    header = spec["header"]
    records = spec.get("records", [])
    out = bytearray(encode_header(header))
    for index, record in enumerate(records):
        out += encode_record(record, index, header["kind"])
    if spec.get("sealed"):
        out += encode_footer(spec["footer"], header, records)
    return bytes(out)


def decode_segment(data: bytes) -> dict:
    """Decode canonical bytes into the fixture description.

    Every refusal is a :class:`SegmentError` named like the Rust ``StoreError::kind``:
    ``truncated``, ``header-magic``, ``header-version``, ``header-length``,
    ``header-crc``, ``reserved``, ``tier-kind-mismatch``, ``record-magic``,
    ``record-header-crc``, ``record-crc``, ``record-sequence``, ``record-kind``,
    ``record-incarnations``, ``record-padding``, ``payload-frame-type``,
    ``payload-frame-length``, ``payload-frame-flags``, ``payload-frame-reserved``,
    ``payload-too-large``, ``footer-magic``, ``footer-crc``, ``footer-count``,
    ``footer-offset``, ``footer-identity``, ``footer-generation``,
    ``trailing-bytes``. A damaged *tail* is not a refusal: it is reported as
    ``tail: incomplete|corrupt`` with the offset where the damage begins, which is
    what makes a torn file repairable rather than merely broken.
    """
    if len(data) < HEADER_LEN:
        raise SegmentError("truncated", context="segment header", need=HEADER_LEN, have=len(data))
    header = _decode_header(data[:HEADER_LEN])
    position = HEADER_LEN
    records: list[dict] = []
    footer = None
    tail = {"state": "complete"}
    while position < len(data):
        remaining = len(data) - position
        if remaining == FOOTER_LEN and data[position : position + 4] == FOOTER_MAGIC:
            footer = _decode_footer(data[position:], header, len(records), position)
            position = len(data)
            break
        if remaining < RECORD_HEADER_LEN:
            tail = {"state": "incomplete", "offset": position}
            break
        try:
            record = _decode_record(data, position, len(records), header)
        except SegmentError as error:
            if error.refusal in ("truncated", "payload-length"):
                tail = {"state": "incomplete", "offset": position}
                break
            tail = {"state": "corrupt", "offset": position, "refusal": error.refusal, **error.fields}
            break
        records.append(record["record"])
        position = record["end"]
    else:
        pass
    if footer is None and tail["state"] == "complete" and position < len(data):
        raise SegmentError("trailing-bytes", extra=len(data) - position)
    return {
        "header": header,
        "records": records,
        "footer": footer,
        "sealed": footer is not None,
        "good_end": position,
        "file_len": len(data),
        "tail": tail,
    }


def _decode_header(bytes_: bytes) -> dict:
    if bytes_[:7] != SEGMENT_MAGIC:
        raise SegmentError("header-magic", found=list(bytes_[:7]))
    if bytes_[7] != 0:
        raise SegmentError("reserved", field="segment header magic reserved", found=bytes_[7])
    version = struct.unpack_from("<H", bytes_, 8)[0]
    if version != SEGMENT_VERSION:
        raise SegmentError("header-version", found=version, supported=SEGMENT_VERSION)
    declared = struct.unpack_from("<H", bytes_, 10)[0]
    if declared != HEADER_LEN:
        raise SegmentError("header-length", declared=declared, expected=HEADER_LEN)
    computed = struct.unpack_from("<I", bytes_, 56)[0]
    measured = crc32(bytes_[:56])
    if computed != measured:
        raise SegmentError("header-crc", declared=computed, computed=measured)
    reserved = struct.unpack_from("<I", bytes_, 60)[0]
    if reserved != 0:
        raise SegmentError("reserved", field="segment header reserved", found=reserved)
    kind_code = bytes_[32]
    kind = next((name for name, code in TIER_KINDS.items() if code == kind_code), None)
    if kind is None:
        raise SegmentError("reserved", field="segment kind", found=kind_code)
    payload = bytes_[33]
    expected = PAYLOAD_BLOCK if kind == "history" else PAYLOAD_EVT
    if payload != expected:
        raise SegmentError("tier-kind-mismatch", tier=kind, payload=PAYLOAD_KINDS.get(payload, "outside-the-vocabulary"))
    if struct.unpack_from("<H", bytes_, 34)[0] != 0:
        raise SegmentError("reserved", field="segment header reserved", found=struct.unpack_from("<H", bytes_, 34)[0])
    if struct.unpack_from("<I", bytes_, 52)[0] != 0:
        raise SegmentError("reserved", field="segment header reserved", found=struct.unpack_from("<I", bytes_, 52)[0])
    return {
        "seq": struct.unpack_from("<Q", bytes_, 12)[0],
        "epoch": struct.unpack_from("<Q", bytes_, 20)[0],
        "dict_generation": struct.unpack_from("<I", bytes_, 28)[0],
        "kind": kind,
        "payload": PAYLOAD_KINDS[payload],
        "start_real_ns": struct.unpack_from("<q", bytes_, 36)[0],
        "max_payload": struct.unpack_from("<I", bytes_, 44)[0],
        "max_incarnations": struct.unpack_from("<I", bytes_, 48)[0],
    }


def _decode_record(data: bytes, position: int, index: int, header: dict) -> dict:
    header_bytes = data[position : position + RECORD_HEADER_LEN]
    if header_bytes[:4] != RECORD_MAGIC:
        raise SegmentError("record-magic", index=index, found=list(header_bytes[:4]))
    computed = struct.unpack_from("<I", header_bytes, 44)[0]
    measured = crc32(header_bytes[:44])
    if computed != measured:
        raise SegmentError("record-header-crc", index=index, declared=computed, computed=measured)
    total = struct.unpack_from("<I", header_bytes, 4)[0]
    seq = struct.unpack_from("<Q", header_bytes, 8)[0]
    kind = header_bytes[16]
    if kind not in RECORD_KINDS:
        raise SegmentError("record-kind", index=index, found=kind)
    expected_kind = RECORD_BLOCK if header["kind"] == "history" else RECORD_EVT
    if kind != expected_kind:
        raise SegmentError("record-kind", index=index, found=kind)
    if header_bytes[17] != 0 or struct.unpack_from("<H", header_bytes, 18)[0] != 0:
        raise SegmentError("reserved", field="record flags", found=header_bytes[17])
    body_len = struct.unpack_from("<I", header_bytes, 20)[0]
    real_ns = struct.unpack_from("<q", header_bytes, 24)[0]
    count = struct.unpack_from("<H", header_bytes, 32)[0]
    if struct.unpack_from("<H", header_bytes, 34)[0] != 0:
        raise SegmentError("reserved", field="record reserved", found=struct.unpack_from("<H", header_bytes, 34)[0])
    if count > header["max_incarnations"]:
        raise SegmentError("record-incarnations", index=index, count=count, max=header["max_incarnations"])
    body_crc = struct.unpack_from("<I", header_bytes, 36)[0]
    pad = header_bytes[40]
    if pad != pad_for(body_len):
        raise SegmentError("record-padding", index=index, declared=pad, expected=pad_for(body_len))
    if total != RECORD_HEADER_LEN + body_len + pad:
        raise SegmentError("payload-length", index=index, declared=total, actual=RECORD_HEADER_LEN + body_len + pad)
    end = position + total
    if end > len(data):
        raise SegmentError("truncated", context="record", need=total, have=len(data) - position)
    body = data[position + RECORD_HEADER_LEN : position + RECORD_HEADER_LEN + body_len]
    measured_body = crc32(body)
    if measured_body != body_crc:
        raise SegmentError("record-crc", index=index, declared=body_crc, computed=measured_body)
    table_len = 8 * count
    incarnations = [struct.unpack_from("<Q", body, 8 * slot)[0] for slot in range(count)]
    payload = body[table_len:]
    check_payload(payload, header)
    if seq != index:
        raise SegmentError("record-sequence", index=index, expected=index, found=seq)
    return {
        "record": {
            "seq": seq,
            "kind": RECORD_KINDS[kind],
            "real_ns": real_ns,
            "incarnations": incarnations,
            "payload_hex": payload.hex(),
            "body_len": body_len,
            "pad": pad,
        },
        "end": end,
    }


def check_payload(payload: bytes, header: dict) -> None:
    """Check the four payload frame facts the store itself validates (plan v4 §B.1).

    The store never looks inside a block: the section directory, the columns and the
    dictionary are ``proto``'s refusal. What it must know before storing bytes at all is
    that they are the frame kind its corpus holds, of the length they declare, with the
    sectioned flag and a zero reserved field.
    """
    if len(payload) > header["max_payload"]:
        raise SegmentError("payload-too-large", len=len(payload), max=header["max_payload"])
    if len(payload) < FRAME_HEADER_LEN:
        raise SegmentError("truncated", context="payload frame header", need=FRAME_HEADER_LEN, have=len(payload))
    declared, frame_type, flags, reserved, _corr = struct.unpack_from("<IBBHI", payload, 0)
    if declared != len(payload):
        raise SegmentError("payload-frame-length", declared=declared, have=len(payload))
    expected = FRAME_TYPES["block" if header["kind"] == "history" else "evt"]
    if frame_type != expected:
        raise SegmentError("payload-frame-type", expected=expected, found=frame_type)
    if flags & ~FRAME_FLAG_SECTIONED or not flags & FRAME_FLAG_SECTIONED:
        raise SegmentError("payload-frame-flags", flags=flags)
    if reserved != 0:
        raise SegmentError("payload-frame-reserved", found=reserved)


def _decode_footer(bytes_: bytes, header: dict, measured_count: int, end_offset: int) -> dict:
    if bytes_[:4] != FOOTER_MAGIC:
        raise SegmentError("footer-magic", found=list(bytes_[:4]))
    declared_len = struct.unpack_from("<I", bytes_, 4)[0]
    if declared_len != FOOTER_LEN:
        raise SegmentError("reserved", field="footer length", found=declared_len)
    computed = struct.unpack_from("<I", bytes_, 56)[0]
    measured = crc32(bytes_[:56])
    if computed != measured:
        raise SegmentError("footer-crc", declared=computed, computed=measured)
    if struct.unpack_from("<I", bytes_, 60)[0] != 0:
        raise SegmentError("reserved", field="footer reserved", found=struct.unpack_from("<I", bytes_, 60)[0])
    seq = struct.unpack_from("<Q", bytes_, 8)[0]
    count = struct.unpack_from("<Q", bytes_, 16)[0]
    declared_end = struct.unpack_from("<Q", bytes_, 24)[0]
    generation = struct.unpack_from("<I", bytes_, 32)[0]
    if count != measured_count:
        raise SegmentError("footer-count", declared=count, measured=measured_count)
    if declared_end != end_offset:
        raise SegmentError("footer-offset", declared=declared_end, measured=end_offset)
    if seq != header["seq"]:
        raise SegmentError("footer-identity", header=header["seq"], footer=seq)
    if generation != header["dict_generation"]:
        raise SegmentError("footer-generation", header=header["dict_generation"], footer=generation)
    return {
        "seq": seq,
        "record_count": count,
        "end_offset": declared_end,
        "dict_generation": generation,
        "first_real_ns": struct.unpack_from("<q", bytes_, 40)[0],
        "last_real_ns": struct.unpack_from("<q", bytes_, 48)[0],
    }


def load_cases(fixtures_dir: Path) -> list[tuple[Path, dict, bytes]]:
    """Every ``<case>.json`` beside its ``<case>.seg``, in name order."""
    cases: list[tuple[Path, dict, bytes]] = []
    import json

    for spec_path in sorted(fixtures_dir.glob("*.json")):
        segment_path = spec_path.with_suffix(".seg")
        if not segment_path.is_file():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        cases.append((spec_path, spec, segment_path.read_bytes()))
    return cases


def normalise(decoded: dict) -> dict:
    """The decoded facts as the JSON description spells them (for comparison)."""
    return {
        "header": decoded["header"],
        "records": decoded["records"],
        "footer": decoded["footer"],
        "sealed": decoded["sealed"],
    }

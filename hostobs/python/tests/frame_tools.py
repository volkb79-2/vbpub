"""Byte-level helpers for tests that need frames the reference encoder refuses to write."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOSTOBS / "python" / "reference"))

import hostobs_wire_ref as ref  # noqa: E402


def split(frame: bytes) -> tuple[int, list[tuple[int, int, int, bytes]]]:
    """``(corr, [(kind, version, flags, body)])`` of a BLOCK frame."""
    corr = struct.unpack_from("<I", frame, 8)[0]
    n_sec = struct.unpack_from("<H", frame, 12)[0]
    entries = []
    offset = 16 + 8 * n_sec
    for index in range(n_sec):
        kind, version, flags, length = struct.unpack_from("<HBBI", frame, 16 + 8 * index)
        entries.append((kind, version, flags, bytes(frame[offset : offset + length])))
        offset += length
    return corr, entries


def assemble(corr: int, entries: list[tuple[int, int, int, bytes]]) -> bytes:
    """A BLOCK frame with exactly these directory entries, in this order, whatever it means."""
    directory = b"".join(struct.pack("<HBBI", k, v, f, len(b)) for k, v, f, b in entries)
    bodies = b"".join(b for _k, _v, _f, b in entries)
    total = 12 + 4 + len(directory) + len(bodies)
    return struct.pack("<IBBHI", total, 5, 1, 0, corr) + struct.pack("<HH", len(entries), 0) + directory + bodies


def set_ent(frame: bytes, row: int, value: int) -> bytearray:
    """A copy of a frame with the entity reference of ``row`` overwritten."""
    corr, entries = split(frame)
    out = []
    for kind, version, flags, body in entries:
        if kind == 2:
            body = bytearray(body)
            struct.pack_into("<I", body, 4 * row, value)
            body = bytes(body)
        out.append((kind, version, flags, body))
    return bytearray(assemble(corr, out))


def rows_offset(frame: bytes) -> int:
    n_sec = struct.unpack_from("<H", frame, 12)[0]
    offset = 16 + 8 * n_sec
    for index in range(n_sec):
        kind, _v, _f, length = struct.unpack_from("<HBBI", frame, 16 + 8 * index)
        if kind == 2:
            return offset
        offset += length
    raise AssertionError("no rows section")


def small_frame(entities: int, ents: list[int], generation: int = 1, with_dictionary: bool = True) -> bytes:
    """A live block of ``len(ents)`` rows and one counter column over ``entities`` entities."""
    n = len(ents)
    dictionary = {
        "generation": generation,
        "entries": [{"kind": "entity", "inc": 10 + i, "text": f"/e{i}"} for i in range(entities)]
        + [
            {
                "kind": "metric",
                "text": "m.count",
                "facts": {"unit": "count", "kind": "counter", "scope": "cgroup", "source": "cgroupfs"},
            }
        ],
    }
    spec = {
        "type": "block",
        "corr": 5,
        "sections": ["header"] + (["dictionary"] if with_dictionary else []) + ["rows", "columns"],
        "header": {
            "boot_id": "00112233-4455-6677-8899-aabbccddeeff",
            "epoch": 1,
            "seq": 1,
            "mono_start_ns": 1,
            "mono_end_ns": 2,
            "boot_ns": 3,
            "real_ns": 4,
            "dict_gen": generation,
            "n_rows": n,
            "dropped": 0,
            "flags": 0,
        },
        "dictionary": dictionary,
        "rows": [{"ent": e, "t_off_us": 7 * i} for i, e in enumerate(ents)],
        "columns": [
            {"metric": entities, "type": "u64", "kind": "counter", "values": [str(i) for i in range(n)], "quality": ["ok"] * n}
        ],
    }
    if not with_dictionary:
        # the reference encoder cannot emit a dangling or non-entity bare block's rows check,
        # but it can emit the bare block itself: the dictionary key is simply not written
        spec["sections"] = ["header", "rows", "columns"]
    return ref.encode_frame(spec)

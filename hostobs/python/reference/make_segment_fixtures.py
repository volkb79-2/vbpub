#!/usr/bin/env python3
"""Generate the shared hostobs segment fixtures (plan v4 §C.5, M1 acceptance).

The fixtures are committed artefacts and this script is the reproducible way to
regenerate them: ``python3 python/reference/make_segment_fixtures.py``. It uses only
the reference codec in this directory, so the bytes under ``fixtures/segment/`` are
the independent Python implementation's output, not the Rust writer's — which is
what makes the Rust test that consumes them a cross-language check, and what makes
``docs/SEGMENT-FORMAT.md`` a specification rather than a description of the code.

Every case is a whole segment file, ``<case>.seg``, with its decoded description in
``<case>.json``. The payloads are real ``proto`` BLOCK frames taken from
``fixtures/wire/`` so the store's payload check is exercised on genuine wire bytes.

Never edit ``.seg`` by hand: change the description here and regenerate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "fixtures" / "segment"
WIRE = REPO / "fixtures" / "wire"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostobs_segment_ref as segment  # noqa: E402


def wire(name: str) -> bytes:
    """One committed wire frame, used verbatim as a record payload."""
    return (WIRE / f"{name}.bin").read_bytes()


def header(
    seq: int,
    start_real_ns: int,
    kind: str = "history",
    *,
    epoch: int = 7,
    generation: int = 1,
    max_payload: int = 4096,
    max_incarnations: int = 16,
) -> dict:
    return {
        "seq": seq,
        "epoch": epoch,
        "dict_generation": generation,
        "kind": kind,
        "payload": "block" if kind == "history" else "evt",
        "start_real_ns": start_real_ns,
        "max_payload": max_payload,
        "max_incarnations": max_incarnations,
    }


def cases() -> dict[str, dict]:
    """The fixture set, one entry per segment file."""
    block = wire("live-block")
    empty = wire("empty-rows-and-time")
    unicode = wire("unicode-dictionary")
    return {
        # The ordinary shape: two records in one open (unsealed) segment, each naming the
        # incarnations its columns refer to.
        "open-history": {
            "doc": "an open history segment: two block records, no footer, incarnations 1 and 2",
            "sealed": False,
            "header": header(3, 1_700_000_000_000_000_000),
            "records": [
                {
                    "real_ns": 1_700_000_000_500_000_000,
                    "incarnations": [1, 2],
                    "payload_hex": block.hex(),
                },
                {
                    "real_ns": 1_700_000_001_000_000_000,
                    "incarnations": [3],
                    "payload_hex": empty.hex(),
                },
            ],
        },
        # A sealed segment carries the footer, and the footer's facts are derived from the
        # records: that is why a conforming writer cannot declare a count it did not write.
        "sealed-history": {
            "doc": "a sealed history segment: three records and the footer that closes them",
            "sealed": True,
            "header": header(4, 1_700_000_060_000_000_000),
            "records": [
                {
                    "real_ns": 1_700_000_060_000_000_000,
                    "incarnations": [1],
                    "payload_hex": block.hex(),
                },
                {
                    "real_ns": 1_700_000_061_000_000_000,
                    "incarnations": [1, 4],
                    "payload_hex": unicode.hex(),
                },
                {
                    "real_ns": 1_700_000_062_000_000_000,
                    "incarnations": [],
                    "payload_hex": empty.hex(),
                },
            ],
            "footer": {},
        },
        # A sealed segment with no records at all: the footer still has to describe it,
        # which is where an off-by-one in the count arithmetic shows up.
        "sealed-empty": {
            "doc": "a sealed history segment with no records: count 0, offsets at the header",
            "sealed": True,
            "header": header(5, 1_700_000_120_000_000_000),
            "records": [],
            "footer": {},
        },
        # The events corpus: same envelope, EVT payloads and the events tier kind.
        "open-events": {
            "doc": "an open events segment: one EVT record, tier kind events",
            "sealed": False,
            "header": header(1, 1_700_000_000_000_000_000, kind="events", generation=2),
            "records": [
                {
                    "real_ns": 1_700_000_000_000_000_000,
                    "incarnations": [],
                    "payload_hex": evt_frame().hex(),
                },
            ],
        },
        # A segment whose dictionary generation and epoch are both its own: both are
        # per-segment facts, and a reader that assumed a global one would accept a record
        # written against names it does not hold.
        "open-second-generation": {
            "doc": "an open history segment of a later dictionary generation and epoch",
            "sealed": False,
            "header": header(
                9,
                1_700_000_300_000_000_000,
                epoch=8,
                generation=2,
                max_incarnations=4,
            ),
            "records": [
                {
                    "real_ns": 1_700_000_300_000_000_000,
                    "incarnations": [2, 5, 7, 9],
                    "payload_hex": block.hex(),
                },
            ],
        },
    }


def evt_frame() -> bytes:
    """A minimal well-formed ``proto`` EVT frame: the header and a section directory.

    The store checks the four frame-header facts and nothing else; the section bodies are
    ``proto``'s business. A header section is enough for the frame to be a real ``proto``
    frame rather than a hand-made stub: the header body is 80 bytes and required.
    """
    sections = [(1, 1, 1, bytes(80))]
    body = b"".join(payload for _, _, _, payload in sections)
    directory = b""
    for kind, version, flags, payload in sections:
        directory += kind.to_bytes(2, "little")
        directory += bytes((version, flags))
        directory += len(payload).to_bytes(4, "little")
    total = segment.FRAME_HEADER_LEN + 4 + len(directory) + len(body)
    out = bytearray()
    out += total.to_bytes(4, "little")
    out += bytes((segment.FRAME_TYPES["evt"], segment.FRAME_FLAG_SECTIONED))
    out += b"\x00\x00"
    out += (1).to_bytes(4, "little")
    out += len(sections).to_bytes(2, "little")
    out += b"\x00\x00"
    out += directory
    out += body
    assert len(out) == total, "the declared frame length is the encoded length"
    return bytes(out)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, spec in cases().items():
        payload = segment.encode_segment(spec)
        decoded = segment.decode_segment(payload)
        if decoded["tail"]["state"] != "complete":
            raise SystemExit(f"{name}: the generated segment did not decode cleanly")
        if decoded["sealed"] != spec["sealed"]:
            raise SystemExit(f"{name}: sealed-ness did not survive the round trip")
        spec_out = {
            "case": name,
            "doc": spec["doc"],
            "header": decoded["header"],
            "records": decoded["records"],
            "sealed": decoded["sealed"],
            "footer": decoded["footer"],
        }
        (OUT / f"{name}.json").write_text(
            json.dumps(spec_out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (OUT / f"{name}.seg").write_bytes(payload)
        written.append(f"{name}.seg ({len(payload)} bytes)")
    print("wrote " + ", ".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

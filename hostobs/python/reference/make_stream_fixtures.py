#!/usr/bin/env python3
"""Write the reader stream fixtures (`fixtures/stream/reader-*.json`, WIRE.md section 11.4).

The frame bytes come from the Python reference (`hostobs_wire_ref`: the frame header layout
and `encode_frame` for the BLOCK frame). The expected outputs are NOT produced by running a
reader: each case spells out, by the rules of WIRE.md section 5.2, what every step returns,
so the Rust `FrameReader` (`crates/proto/tests/stream_fixtures.rs`) and the later Python
`stream.py` are both judged against arithmetic written down here.

Format of one case: ``{"case", "doc", "side", "max_frame", "completion_ms", "steps", "expect"}``.
A step is ``{"t_ms", "feed_hex"}``, ``{"t_ms", "poll": true}`` or ``{"t_ms", "eof": true}``;
``expect`` holds one list per step with outputs ``{"frame": {"type", "corr", "len"}}``,
``{"error": kind}`` and ``{"closed": "clean"}``. A feed's list is its frames in order, then its
error; a poll answers its error or nothing; an eof answers its error or ``closed``.

Regenerate with ``python3 python/reference/make_stream_fixtures.py``.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hostobs_wire_ref as wire  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 and __name__ == "__main__" else REPO / "fixtures" / "stream"

SERVER = {"side": "server", "max_frame": 1024, "completion_ms": 5000}
CLIENT = {"side": "client", "max_frame": 16 * 1024 * 1024, "completion_ms": 30000}

CLOSED = {"closed": "clean"}


def raw_frame(frame_type: int, corr: int, payload: bytes = b"", length: int | None = None) -> bytes:
    """One frame: 12-byte header (len, type, flags 0, reserved 0, corr) and the payload."""
    total = wire.FRAME_HEADER_LEN + len(payload) if length is None else length
    return struct.pack("<IBBHI", total, frame_type, 0, 0, corr) + payload


def req_frame(corr: int = 1) -> bytes:
    return raw_frame(wire.FRAME_TYPES["req"], corr, b'{"verb":"hello","args":{}}')


def rep_frame(corr: int = 2) -> bytes:
    return raw_frame(wire.FRAME_TYPES["rep"], corr, b'{"ok":true}')


def block_frame(corr: int = 3) -> bytes:
    """A header-only BLOCK from the reference encoder (the reader never decodes it)."""
    return wire.encode_frame(
        {
            "type": "block",
            "corr": corr,
            "sections": ["header"],
            "header": {
                "boot_id": "00112233-4455-6677-8899-aabbccddeeff",
                "epoch": 7,
                "seq": 42,
                "mono_start_ns": 1_000_000_000,
                "mono_end_ns": 2_000_000_000,
                "boot_ns": 500,
                "real_ns": 1_700_000_000_000_000_000,
                "dict_gen": 1,
                "n_rows": 0,
                "dropped": 0,
                "flags": 0,
            },
        }
    )


def feed(t_ms: int, data: bytes) -> dict:
    return {"t_ms": t_ms, "feed_hex": data.hex()}


def poll(t_ms: int) -> dict:
    return {"t_ms": t_ms, "poll": True}


def eof(t_ms: int) -> dict:
    return {"t_ms": t_ms, "eof": True}


def frame_out(name: str, corr: int, data: bytes) -> dict:
    """The output of one completed frame: its type name, corr and declared length."""
    return {"frame": {"type": name, "corr": corr, "len": len(data)}}


def error_out(kind: str) -> dict:
    return {"error": kind}


def case(name: str, doc: str, limits: dict, steps: list, expect: list) -> tuple[str, dict]:
    assert len(steps) == len(expect), f"{name}: one expect list per step"
    document = {"case": name, "doc": doc, **limits, "steps": steps, "expect": expect}
    return name, document


def build_cases() -> list[tuple[str, dict]]:
    req = req_frame()
    rep = rep_frame()
    blk = block_frame()
    bad_type = raw_frame(9, 3, length=12)
    cases = []

    # Header{have} / Body: one byte per step, the last byte completes the frame. The whole
    # frame takes (len - 1) * 10 ms, far inside the 5,000 ms age.
    steps = [feed(10 * index, bytes([byte])) for index, byte in enumerate(req)]
    expect = [[] for _ in req]
    expect[-1] = [frame_out("req", 1, req)]
    cases.append(
        case(
            "reader-dribbled-bytes",
            "A REQ frame arrives one byte per step, 10 ms apart: nothing until the last byte, then one frame.",
            SERVER,
            steps,
            expect,
        )
    )

    # A REP cut at every offset 1..len-1: each cut is two steps, nothing then the frame.
    steps, expect = [], []
    t = 0
    for cut in range(1, len(rep)):
        steps += [feed(t, rep[:cut]), feed(t + 1, rep[cut:])]
        expect += [[], [frame_out("rep", 2, rep)]]
        t += 2
    cases.append(
        case(
            "reader-split-every-offset",
            "One REP frame cut at each offset 1..len-1 (two feeds per cut): every cut yields exactly one frame, on the second feed.",
            SERVER,
            steps,
            expect,
        )
    )

    cases.append(
        case(
            "reader-two-frames-one-feed",
            "A REQ and a REP in one feed: both come out, in order, from that feed.",
            SERVER,
            [feed(0, req + rep)],
            [[frame_out("req", 1, req), frame_out("rep", 2, rep)]],
        )
    )

    cases.append(
        case(
            "reader-frame-then-bad-header",
            "One feed: a good REQ, then a 12-byte header with type 9. The frame completed before the error is returned with it; a later feed returns the same error and no frame (poisoned).",
            SERVER,
            [feed(0, req + bad_type), feed(1, rep)],
            [
                [frame_out("req", 1, req), error_out("unknown-frame-type")],
                [error_out("unknown-frame-type")],
            ],
        )
    )

    over = raw_frame(wire.FRAME_TYPES["req"], 4, length=SERVER["max_frame"] + 1)
    cases.append(
        case(
            "reader-oversized-len",
            "A header whose len is max_frame + 1: nothing at 11 bytes, oversized-frame at the 12th, then poisoned (more bytes and a poll return it). The reader never reserves for the declared len.",
            SERVER,
            [feed(0, over[:11]), feed(1, over[11:]), feed(2, b"\x00\x01\x02"), poll(3)],
            [[], [error_out("oversized-frame")], [error_out("oversized-frame")], [error_out("oversized-frame")]],
        )
    )

    below = raw_frame(wire.FRAME_TYPES["req"], 1, length=11)
    cases.append(
        case(
            "reader-len-below-header",
            "A header with len = 11 (shorter than the header itself): truncated, poisoned.",
            SERVER,
            [feed(0, below), poll(1)],
            [[error_out("truncated")], [error_out("truncated")]],
        )
    )

    half = len(req) // 2
    limit = SERVER["completion_ms"]
    cases.append(
        case(
            "reader-completion-age",
            "Half a REQ at t=0; a poll at t=completion_ms does nothing (the deadline is strict), a poll at completion_ms+1 is deadline; the reader stays poisoned (eof returns it).",
            SERVER,
            [feed(0, req[:half]), poll(limit), poll(limit + 1), eof(limit + 2)],
            [[], [], [error_out("deadline")], [error_out("deadline")]],
        )
    )

    limit = CLIENT["completion_ms"]
    cases.append(
        case(
            "reader-age-from-first-byte",
            "60 s idle at Header{0} (polls do nothing), then a REQ whose first byte arrives at t=60000: the age counts from that byte, so a poll at +completion_ms is still fine and the frame completes at +completion_ms.",
            CLIENT,
            [
                poll(1000),
                poll(limit),
                poll(60000),
                feed(60000, req[:1]),
                poll(60000 + limit),
                feed(60000 + limit, req[1:]),
            ],
            [[], [], [], [], [], [frame_out("req", 1, req)]],
        )
    )

    cases.append(
        case(
            "reader-truncated-stream",
            "A BLOCK frame cut in its body, then end of stream: truncated.",
            SERVER,
            [feed(0, blk[:20]), eof(1)],
            [[], [error_out("truncated")]],
        )
    )

    cases.append(
        case(
            "reader-clean-eof",
            "A complete BLOCK frame, an idle poll an hour later, then end of stream at the frame boundary: a clean close.",
            SERVER,
            [feed(0, blk), poll(3_600_000), eof(3_600_001)],
            [[frame_out("block", 3, blk)], [], [CLOSED]],
        )
    )

    cases.append(
        case(
            "reader-poisoned-stays",
            "A header with type 9, then a good REQ, a poll and an eof: every later call returns the same error and no frame.",
            SERVER,
            [feed(0, bad_type), feed(1, req), poll(2), eof(3)],
            [
                [error_out("unknown-frame-type")],
                [error_out("unknown-frame-type")],
                [error_out("unknown-frame-type")],
                [error_out("unknown-frame-type")],
            ],
        )
    )

    cases.append(
        case(
            "reader-feed-after-eof",
            "End of stream at Header{0} is a clean close; bytes fed afterwards are trailing-bytes, and stay so.",
            SERVER,
            [eof(0), feed(1, b"\x01\x02\x03"), feed(2, req)],
            [[CLOSED], [error_out("trailing-bytes")], [error_out("trailing-bytes")]],
        )
    )
    return cases


def render_all() -> dict[str, str]:
    """File name -> exact file text of every reader fixture."""
    return {
        f"{name}.json": json.dumps(document, indent=2) + "\n"
        for name, document in build_cases()
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rendered = render_all()
    for name, text in rendered.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(rendered)} reader stream fixtures into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Write the client stream fixtures (`fixtures/stream/client-*.json`, WIRE.md section 11.4).

The frame bytes come from the Python reference encoder (`hostobs_wire_ref.encode_frame`). The
expected outputs are NOT produced by running a client: each case spells out, by the rules of
WIRE.md sections 6.1, 6.2, 6.4 and 6.5, what every step returns, so the Rust `StreamDicts`
(`crates/proto/tests/client_stream_fixtures.rs`) and the later Python client are both judged
against arithmetic written down here.

Format of one case: ``{"case", "doc", "side", "max_frame", "completion_ms", "steps", "expect"}``
as the reader cases (``side`` is ``client``; the two limit keys are carried for a uniform
shape and are not used). A client step is ``{"t_ms", "feed_hex"}`` or ``{"t_ms", "hello":
{"boot_id", "epoch"}}``. A feed is ONE complete BLOCK frame of stream ``corr`` 5: the client
decodes it with ``decode_block_with(bytes, held(5))``; on success ``accept(5, block)`` judges it
against the stream's rules; on any decoder error the stream is closed (WIRE.md 6.5). The step's
outputs are ``{"ok": generation}`` (the block's header ``dict_gen``), or ``{"error": kind}`` for
the first failing check. A hello step drops every dictionary and answers
``{"session": "first|same|changed"}`` (WIRE.md 6.4).

Regenerate with ``python3 python/reference/make_client_stream_fixtures.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hostobs_wire_ref as wire  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 and __name__ == "__main__" else REPO / "fixtures" / "stream"

CLIENT = {"side": "client", "max_frame": 16 * 1024 * 1024, "completion_ms": 30000}
BOOT_ID = "00112233-4455-6677-8899-aabbccddeeff"
CORR = 5


def header(dict_gen: int) -> dict:
    return {
        "boot_id": BOOT_ID,
        "epoch": 7,
        "seq": 42,
        "mono_start_ns": 1_000_000_000,
        "mono_end_ns": 2_000_000_000,
        "boot_ns": 500,
        "real_ns": 1_700_000_000_000_000_000,
        "dict_gen": dict_gen,
        "n_rows": 1,
        "dropped": 0,
        "flags": 0,
    }


def dictionary_block(generation: int, unknown_required: bool = False) -> bytes:
    """Header + dictionary (one entity entry) + one row; optionally an unknown required section."""
    frame = {
        "type": "block",
        "corr": CORR,
        "sections": ["header", "dictionary", "rows"],
        "header": header(generation),
        "dictionary": {
            "generation": generation,
            "entries": [{"kind": "entity", "inc": 11, "text": "/system.slice"}],
        },
        "rows": [{"ent": 0, "t_off_us": 0}],
    }
    if unknown_required:
        frame["unknown_sections"] = [{"kind_code": 9, "version": 1, "flags": 1, "body_hex": "00" * 8}]
    return wire.encode_frame(frame)


def bare_block(generation: int) -> bytes:
    """Header + one row, no dictionary section: decoded against what the stream holds."""
    return wire.encode_frame(
        {
            "type": "block",
            "corr": CORR,
            "sections": ["header", "rows"],
            "header": header(generation),
            "rows": [{"ent": 0, "t_off_us": 0}],
        }
    )


def feed(t_ms: int, data: bytes) -> dict:
    return {"t_ms": t_ms, "feed_hex": data.hex()}


def hello(t_ms: int, epoch: int) -> dict:
    return {"t_ms": t_ms, "hello": {"boot_id": BOOT_ID, "epoch": epoch}}


def ok(generation: int) -> list:
    return [{"ok": generation}]


def error(kind: str) -> list:
    return [{"error": kind}]


def session(kind: str) -> list:
    return [{"session": kind}]


STALE = "stale-dictionary-generation"
REGRESSED = "generation-regressed"
REQUIRED = "unsupported-required-section"


def case(name: str, doc: str, steps: list, expect: list) -> tuple[str, dict]:
    assert len(steps) == len(expect), f"{name}: one expect list per step"
    return name, {"case": name, "doc": doc, **CLIENT, "steps": steps, "expect": expect}


def build_cases() -> list[tuple[str, dict]]:
    cases = []

    cases.append(
        case(
            "client-first-block-gen-5",
            "A stream joins at generation 5: the first block is dictionary-bearing at any generation of at least 1 and a bare block of generation 5 follows it.",
            [feed(0, dictionary_block(5)), feed(1, bare_block(5))],
            [ok(5), ok(5)],
        )
    )

    cases.append(
        case(
            "client-first-block-bare",
            "A first block without a dictionary has nothing to be judged against: stale-dictionary-generation, and the stream still holds nothing for the next bare block.",
            [feed(0, bare_block(1)), feed(1, bare_block(1))],
            [error(STALE), error(STALE)],
        )
    )

    cases.append(
        case(
            "client-generation-step",
            "Dictionary generation 2, a bare block of 2, dictionary generation 3 (held becomes 3), then a bare block of 2: the last is stale (the codec judges it against the held 3).",
            [
                feed(0, dictionary_block(2)),
                feed(1, bare_block(2)),
                feed(2, dictionary_block(3)),
                feed(3, bare_block(2)),
            ],
            [ok(2), ok(2), ok(3), error(STALE)],
        )
    )

    cases.append(
        case(
            "client-generation-regressed",
            "A dictionary-bearing block must exceed the held generation: generation 2 again is generation-regressed and closes the stream (the next bare block finds nothing held), a fresh dictionary at 4 starts it again, and 3 below the held 4 is regressed.",
            [
                feed(0, dictionary_block(2)),
                feed(1, dictionary_block(2)),
                feed(2, bare_block(2)),
                feed(3, dictionary_block(4)),
                feed(4, dictionary_block(3)),
            ],
            [ok(2), error(REGRESSED), error(STALE), ok(4), error(REGRESSED)],
        )
    )

    cases.append(
        case(
            "client-required-section",
            "A block with an unknown required section (kind 9, flags 1) is refused by the decoder and the stream is closed: the bare block that follows finds nothing held.",
            [
                feed(0, dictionary_block(1)),
                feed(1, dictionary_block(2, unknown_required=True)),
                feed(2, bare_block(1)),
            ],
            [ok(1), error(REQUIRED), error(STALE)],
        )
    )

    cases.append(
        case(
            "client-epoch-change",
            "Every hello drops every dictionary: after a hello with a new epoch (session changed) and after a hello with the same epoch (session same) a bare block finds nothing held; the first hello is session first.",
            [
                hello(0, 7),
                feed(1, dictionary_block(1)),
                hello(2, 8),
                feed(3, bare_block(1)),
                feed(4, dictionary_block(1)),
                hello(5, 8),
                feed(6, bare_block(1)),
            ],
            [session("first"), ok(1), session("changed"), error(STALE), ok(1), session("same"), error(STALE)],
        )
    )
    return cases


def render_all() -> dict[str, str]:
    """File name -> exact file text of every client fixture."""
    return {f"{name}.json": json.dumps(document, indent=2) + "\n" for name, document in build_cases()}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rendered = render_all()
    for name, text in rendered.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(rendered)} client stream fixtures into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

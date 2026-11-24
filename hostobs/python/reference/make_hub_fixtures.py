#!/usr/bin/env python3
"""Write the send-hub fixtures (`fixtures/stream/hub-*.json`, WIRE.md section 11.4).

The expectations are NOT produced by running a hub: each case spells out, by the rules of WIRE.md
section 5.3, what every step returns, so the Rust `SendHub` (`crates/proto/tests/hub_fixtures.rs`)
is judged against arithmetic written down here. Blocks are described by length only; the Rust
replay builds every block as a 2-section BLOCK of that length (header at 16 + 16, `dropped` at
offset 104, first tail byte at offset 112 holding `tag`).

Format of one case: ``{"case", "doc", "limits", "conns", "steps", "expect"}``. ``limits`` holds the
nine ``QueueLimits`` fields (times in ms); ``conns`` lists the peer uid of each connection (index
0 is the first ``add``, all at t = 0). A step is ``{"t_ms", "op", ...fields, "watch"?}`` with ``op``
one of ``push_live {c, s, gen, bare|null, dict, tag?, share?}``, ``push_query {c, s, len}``,
``push_control {c, len}``, ``query_room {c, len}``, ``end_stream {c, s}``, ``head {c, block}``,
``head_partial {c}``, ``wrote {c, n}``, ``poll``, ``drain_unpinned {c}``, ``remove {c}``.
``watch`` lists ``c<i>`` (connection), ``u<uid>`` or ``g`` (global) pool keys. ``expect`` holds one
list per step: the operation's own output first (``push_live``: ``queued|coalesced|
dropped-incoming``; ``push_query``: ``queued|backpressure``; ``push_control``: ``ok|close``;
``end_stream``: ``{"counts": {"blocks", "dropped"}}``; ``head``: ``{"head": {"len", ...}}`` or
``null``, with ``corr``, ``dropped`` and ``tail_tag`` when ``block``; ``query_room``:
``{"room": bool}``; ``head_partial``: ``{"partial": bool}``; ``poll``: ``{"closed": [conn
indexes]}``), then one ``{"pools": {key: [live, query, control]}}`` when the step watches pools.
``share`` names a tick object that several pushes (of different connections) use.

Regenerate with ``python3 python/reference/make_hub_fixtures.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 and __name__ == "__main__" else REPO / "fixtures" / "stream"

BASE_LIMITS = {
    "client_queue_bytes": 1000,
    "uid_live_bytes": 2000,
    "global_live_bytes": 8000,
    "query_queue_bytes": 1000,
    "uid_query_bytes": 1000,
    "global_query_bytes": 4000,
    "control_queue_bytes": 500,
    "stall_ms": 5000,
    "frame_completion_ms": 5000,
}
CASE_NAMES = [
    "hub-pin-stamps",
    "hub-coalesce",
    "hub-pinned-never-dropped",
    "hub-coalesce-dict-form",
    "hub-needs-dict-sticky",
    "hub-repair-over-budget",
    "hub-per-connection-shed",
    "hub-global-largest",
    "hub-victim-skips-pinned",
    "hub-uid-cap",
    "hub-interval-skips-generation",
    "hub-query-backpressure",
    "hub-query-own-pool",
    "hub-query-pin-after-corr-reuse",
    "hub-control-cap",
    "hub-frame-completion",
    "hub-stall",
    "hub-stall-after-idle",
    "hub-shared-block",
    "hub-end-stream",
]
TAIL_TAG = 160


class Case:
    """One case under construction: steps and their hand-derived outputs."""

    def __init__(self, name: str, doc: str, conns: list[int], **limits: int) -> None:
        self.name = name
        self.doc = doc
        self.conns = conns
        self.limits = {**BASE_LIMITS, **limits}
        self.steps: list[dict] = []
        self.expect: list[list] = []

    def add(self, t_ms: int, op: str, out: list, watch: dict | None = None, **fields) -> None:
        step = {"t_ms": t_ms, "op": op, **fields}
        outputs = list(out)
        if watch:
            step["watch"] = list(watch)
            outputs.append({"pools": {key: list(value) for key, value in watch.items()}})
        self.steps.append(step)
        self.expect.append(outputs)

    def live(self, t, c, s, bare, dict_len, out, gen=1, watch=None, tag=None, share=None):
        fields = {"c": c, "s": s, "gen": gen, "bare": bare, "dict": dict_len}
        if tag is not None:
            fields["tag"] = tag
        if share is not None:
            fields["share"] = share
        self.add(t, "push_live", [out], watch, **fields)

    def query(self, t, c, s, length, out, watch=None):
        self.add(t, "push_query", [out], watch, c=c, s=s, len=length)

    def room(self, t, c, length, out):
        self.add(t, "query_room", [{"room": out}], None, c=c, len=length)

    def control(self, t, c, length, out, watch=None):
        self.add(t, "push_control", [out], watch, c=c, len=length)

    def head(self, t, c, length, corr, dropped, tag=TAIL_TAG, watch=None):
        body = {"len": length, "corr": corr, "dropped": dropped, "tail_tag": tag}
        self.add(t, "head", [{"head": body}], watch, c=c, block=True)

    def head_control(self, t, c, length):
        self.add(t, "head", [{"head": {"len": length}}], None, c=c, block=False)

    def wrote(self, t, c, n, watch=None):
        self.add(t, "wrote", [], watch, c=c, n=n)

    def end(self, t, c, s, blocks, dropped, watch=None):
        counts = {"counts": {"blocks": blocks, "dropped": dropped}}
        self.add(t, "end_stream", [counts], watch, c=c, s=s)

    def poll(self, t, closed):
        self.add(t, "poll", [{"closed": closed}])

    def document(self) -> dict:
        assert len(self.steps) == len(self.expect)
        return {
            "case": self.name,
            "doc": self.doc,
            "limits": self.limits,
            "conns": self.conns,
            "steps": self.steps,
            "expect": self.expect,
        }


def c0(live=0, query=0, control=0):
    return {"c0": (live, query, control)}


def build_cases() -> list[tuple[str, dict]]:
    cases: list[Case] = []

    k = Case("hub-pin-stamps", "two streams; the pin stamps corr and the pending drops; a drop after the pin is counted in the next pinned block of its stream", [1])
    k.live(0, 0, 1, 200, 240, "queued", watch=c0(240))
    k.live(0, 0, 2, 200, 240, "queued", watch=c0(480))
    k.head(0, 0, 240, 1, 0)
    k.live(0, 0, 1, 200, 240, "queued", watch=c0(680))
    k.live(0, 0, 1, 200, 240, "coalesced", watch=c0(680))
    k.wrote(0, 0, 240)
    k.head(0, 0, 240, 2, 0)
    k.wrote(0, 0, 240)
    k.head(0, 0, 200, 1, 1)
    k.end(0, 0, 1, 2, 0)
    cases.append(k)

    k = Case("hub-coalesce", "two unpinned ticks of one stream: one entry, stamped dropped = 1", [1])
    k.live(0, 0, 1, 200, 240, "queued")
    k.head(0, 0, 240, 1, 0)
    k.wrote(0, 0, 240)
    k.live(0, 0, 1, 200, 240, "queued", watch=c0(200))
    k.live(0, 0, 1, 200, 240, "coalesced", watch=c0(200))
    k.head(0, 0, 200, 1, 1)
    k.end(0, 0, 1, 2, 0)
    cases.append(k)

    k = Case("hub-pinned-never-dropped", "over budget with the pinned head the oldest: the next oldest unpinned entry is shed", [1])
    k.live(0, 0, 1, 300, 400, "queued")
    k.live(0, 0, 2, 300, 400, "queued", watch=c0(800))
    k.head(0, 0, 400, 1, 0)
    k.live(0, 0, 3, 300, 400, "queued", watch=c0(800))
    k.wrote(0, 0, 400)
    k.live(0, 0, 2, 300, 400, "queued", watch=c0(800))
    k.head(0, 0, 400, 3, 0)
    k.wrote(0, 0, 400)
    k.head(0, 0, 400, 2, 1)
    cases.append(k)

    k = Case("hub-coalesce-dict-form", "an unpinned dictionary-bearing tick replaced by a bare tick: the replacement is the dictionary form", [1])
    k.live(0, 0, 1, 200, 240, "queued", watch=c0(240))
    k.live(0, 0, 1, 200, 240, "coalesced", watch=c0(240))
    k.head(0, 0, 240, 1, 1)
    k.wrote(0, 0, 240)
    k.live(0, 0, 1, 200, 240, "queued", watch=c0(200))
    k.head(0, 0, 200, 1, 0)
    cases.append(k)

    k = Case("hub-needs-dict-sticky", "a dropped dictionary-bearing tick keeps needs_dict set across an empty queue: 60 s later the tick is the dictionary form", [1], client_queue_bytes=500)
    k.live(0, 0, 2, 300, 400, "queued")
    k.head(0, 0, 400, 2, 0)
    k.wrote(0, 0, 400)
    k.live(0, 0, 1, 300, 400, "queued")
    k.head(0, 0, 400, 1, 0)
    k.live(0, 0, 2, None, 400, "dropped-incoming", watch=c0(400))
    k.wrote(0, 0, 400)
    k.live(60000, 0, 2, 300, 400, "queued", watch=c0(400))
    k.head(60000, 0, 400, 2, 1)
    cases.append(k)

    k = Case("hub-repair-over-budget", "the dictionary form itself does not fit: dropped and counted, needs_dict stays set, the later tick repairs", [1], client_queue_bytes=500)
    k.live(0, 0, 2, 300, 400, "queued")
    k.head(0, 0, 400, 2, 0)
    k.wrote(0, 0, 400)
    k.live(0, 0, 1, 300, 400, "queued")
    k.head(0, 0, 400, 1, 0)
    k.live(0, 0, 2, None, 400, "dropped-incoming", watch=c0(400))
    k.live(0, 0, 2, 300, 400, "dropped-incoming", watch=c0(400))
    k.wrote(0, 0, 400)
    k.live(60000, 0, 2, 300, 400, "queued", watch=c0(400))
    k.head(60000, 0, 400, 2, 2)
    cases.append(k)

    k = Case("hub-per-connection-shed", "over client_queue_bytes: the oldest unpinned Live entry of the connection is shed and counted on its stream", [1])
    k.live(0, 0, 1, None, 400, "queued")
    k.live(0, 0, 2, None, 400, "queued")
    k.live(0, 0, 3, None, 400, "queued", watch=c0(800))
    k.head(0, 0, 400, 2, 0)
    k.end(0, 0, 1, 0, 1)
    cases.append(k)

    k = Case("hub-global-largest", "global cap: the connection with the most Live bytes loses its oldest unpinned entry; the pushing one is untouched", [1, 2, 3], global_live_bytes=1500)
    k.live(0, 0, 1, None, 400, "queued")
    k.live(0, 0, 2, None, 400, "queued")
    k.live(0, 1, 1, None, 400, "queued")
    k.live(0, 2, 1, None, 200, "queued", watch={"g": (1400, 0, 0)})
    k.live(0, 1, 2, None, 400, "queued", watch={"c0": (400, 0, 0), "c1": (800, 0, 0), "c2": (200, 0, 0), "g": (1400, 0, 0)})
    k.end(0, 0, 1, 0, 1)
    cases.append(k)

    k = Case("hub-victim-skips-pinned", "global cap: a connection holding only a pinned head and Query bytes is not a victim; with no victim the push is dropped", [1, 2, 3, 4], global_live_bytes=1200)
    k.live(0, 0, 1, None, 500, "queued")
    k.query(0, 0, 9, 200, "queued")
    k.head(0, 0, 500, 1, 0)
    k.live(0, 1, 1, None, 200, "queued")
    k.live(0, 1, 2, None, 200, "queued")
    k.live(0, 2, 1, None, 400, "queued", watch={"c0": (500, 200, 0), "c1": (200, 0, 0), "c2": (400, 0, 0), "g": (1100, 200, 0)})
    k.add(0, "drain_unpinned", [], None, c=1)
    k.add(0, "drain_unpinned", [], {"g": (500, 200, 0)}, c=2)
    k.live(0, 2, 1, None, 800, "dropped-incoming", watch={"g": (500, 200, 0)})
    k.wrote(0, 0, 500)
    k.live(0, 2, 1, 120, 800, "queued", watch={"g": (800, 200, 0)})
    k.head(0, 2, 800, 1, 1)
    cases.append(k)

    k = Case("hub-uid-cap", "one uid at its Live cap: the next push sheds that uid's largest connection, the other uid is untouched", [1, 1, 1, 1, 2])
    k.live(0, 0, 1, None, 400, "queued")
    k.live(0, 0, 2, None, 400, "queued")
    k.live(0, 1, 1, None, 400, "queued")
    k.live(0, 2, 1, None, 400, "queued")
    k.live(0, 3, 1, None, 400, "queued")
    k.live(0, 4, 1, None, 400, "queued", watch={"u1": (2000, 0, 0)})
    k.live(0, 3, 2, None, 400, "queued", watch={"c0": (400, 0, 0), "c3": (800, 0, 0), "c4": (400, 0, 0), "u1": (2000, 0, 0), "u2": (400, 0, 0), "g": (2400, 0, 0)})
    k.end(0, 0, 1, 0, 1)
    cases.append(k)

    k = Case("hub-interval-skips-generation", "a stream that missed the generation-changing tick gets the dictionary form on its next push", [1])
    k.live(0, 0, 1, 200, 240, "queued", watch=c0(240))
    k.head(0, 0, 240, 1, 0)
    k.wrote(0, 0, 240)
    k.live(0, 0, 1, 200, 280, "queued", gen=2, watch=c0(280))
    k.head(0, 0, 280, 1, 0)
    k.wrote(0, 0, 280)
    k.live(0, 0, 1, 200, 280, "queued", gen=2, watch=c0(200))
    k.head(0, 0, 200, 1, 0)
    cases.append(k)

    k = Case("hub-query-backpressure", "query_room and push_query refuse a full connection pool and then a full global pool; nothing waits or is dropped, and reading a block restores room for a new request", [1, 2], global_query_bytes=1500)
    k.room(0, 0, 600, True)
    k.query(0, 0, 1, 600, "queued", watch={"c0": (0, 600, 0), "g": (0, 600, 0)})
    k.room(0, 0, 600, False)
    k.query(0, 0, 1, 600, "backpressure")
    k.query(0, 1, 2, 600, "queued", watch={"g": (0, 1200, 0)})
    k.room(0, 1, 400, False)
    k.query(0, 1, 2, 400, "backpressure")
    k.head(0, 0, 600, 1, 0)
    k.room(0, 1, 400, False)
    k.wrote(0, 0, 600)
    k.room(0, 1, 400, True)
    k.query(0, 1, 2, 400, "queued", watch={"c1": (0, 1000, 0), "g": (0, 1000, 0)})
    k.room(0, 1, 1, False)
    k.end(0, 0, 1, 1, 0)
    k.end(0, 1, 2, 2, 0)
    cases.append(k)

    k = Case("hub-query-own-pool", "a query sheds no tick and a tick displaces no query block", [1, 2], global_live_bytes=800, query_queue_bytes=500)
    k.live(0, 1, 1, None, 400, "queued")
    k.live(0, 1, 2, None, 400, "queued")
    k.query(0, 0, 1, 500, "queued", watch={"c0": (0, 500, 0)})
    k.query(0, 0, 2, 112, "backpressure", watch={"c1": (800, 0, 0)})
    k.live(0, 0, 3, None, 200, "queued", watch={"c0": (200, 500, 0), "c1": (400, 0, 0), "g": (600, 500, 0)})
    cases.append(k)

    k = Case("hub-query-pin-after-corr-reuse", "after a Query stream's END, a new Live stream reuses its corr; the old Query pin stamps 0 and leaves the Live stream's pending drop for the next Live pin", [1])
    k.query(0, 0, 3, 200, "queued")
    k.end(0, 0, 3, 1, 0)
    k.live(0, 0, 3, 200, 240, "queued")
    k.live(0, 0, 3, 200, 240, "coalesced")
    k.head(0, 0, 200, 3, 0)
    k.wrote(0, 0, 200)
    k.head(0, 0, 240, 3, 1)
    cases.append(k)

    k = Case("hub-control-cap", "control: an empty pool admits any length, a second frame over the cap closes, the cap itself is inclusive, blocks never compete", [1])
    k.live(0, 0, 1, None, 240, "queued")
    k.control(0, 0, 900, "ok", watch=c0(240, 0, 900))
    k.control(0, 0, 100, "close")
    k.head(0, 0, 240, 1, 0)
    k.wrote(0, 0, 240)
    k.head_control(0, 0, 900)
    k.wrote(0, 0, 900)
    k.control(0, 0, 100, "ok")
    k.control(0, 0, 400, "ok", watch=c0(0, 0, 500))
    k.control(0, 0, 12, "close")
    cases.append(k)

    k = Case("hub-frame-completion", "a dribbling reader: progress does not save a pinned head past frame_completion_ms", [1])
    k.live(0, 0, 1, None, 240, "queued")
    k.head(0, 0, 240, 1, 0)
    k.wrote(4000, 0, 1)
    k.add(4000, "head_partial", [{"partial": True}], None, c=0)
    k.poll(4000, [])
    k.poll(5000, [])
    k.poll(5001, [0])
    cases.append(k)

    k = Case("hub-stall", "a socket that accepts nothing while no head is pinned: closed after stall_ms, strictly", [1])
    k.control(1000, 0, 112, "ok")
    k.poll(6000, [])
    k.poll(6001, [0])
    cases.append(k)

    k = Case("hub-stall-after-idle", "an idle queue never stalls: the clock starts at the first push", [1])
    k.poll(0, [])
    k.live(60000, 0, 1, None, 240, "queued")
    k.poll(60001, [])
    k.poll(65000, [])
    k.poll(65001, [0])
    cases.append(k)

    k = Case("hub-shared-block", "two connections, one tick object: distinct corr and dropped, one shared tail", [1, 2])
    k.live(0, 0, 1, None, 240, "queued", tag=90, share="tick")
    k.live(0, 1, 7, None, 240, "queued", tag=90, share="tick")
    k.live(0, 1, 7, None, 240, "coalesced", tag=90, share="tick")
    k.head(0, 0, 240, 1, 0, tag=90)
    k.head(0, 1, 240, 7, 1, tag=90)
    cases.append(k)

    k = Case("hub-end-stream", "end_stream removes the unpinned tick and counts it; queued Query blocks stay and are counted", [1])
    k.live(0, 0, 1, 200, 240, "queued")
    k.head(0, 0, 240, 1, 0)
    k.live(0, 0, 1, 200, 240, "queued")
    k.end(0, 0, 1, 1, 1, watch=c0(240))
    k.wrote(0, 0, 240)
    k.query(0, 0, 2, 200, "queued")
    k.head(0, 0, 200, 2, 0)
    k.query(0, 0, 2, 200, "queued")
    k.query(0, 0, 2, 200, "queued")
    k.end(0, 0, 2, 3, 0, watch=c0(0, 600, 0))
    k.wrote(0, 0, 200)
    k.head(0, 0, 200, 2, 0)
    cases.append(k)

    assert [case.name for case in cases] == CASE_NAMES
    return [(case.name, case.document()) for case in cases]


def render_all() -> dict[str, str]:
    """File name -> exact file text of every hub fixture."""
    return {
        f"{name}.json": json.dumps(document, indent=2) + "\n"
        for name, document in build_cases()
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rendered = render_all()
    for name, text in rendered.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(rendered)} hub fixtures into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

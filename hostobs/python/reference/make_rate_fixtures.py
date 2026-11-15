#!/usr/bin/env python3
"""Write the hand-computed rate-derivation fixture (`fixtures/rates/rates.json`).

Every expected value here is computed by hand and written down with its arithmetic in
``doc``: these are the SP-2 oracles ("Rust and Python agree with hand-computed
expectations", plan v4 §F.3). The same file is asserted by the Rust test
(`crates/model/tests/rate_fixtures.rs`) and by the independent Python reference
(`python/reference/hostobs_rate_ref.py`), so a change in either implementation's
reasoning is visible as a disagreement with arithmetic, not with code.

Regenerate with `python3 python/reference/make_rate_fixtures.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "fixtures" / "rates"

S = 1_000_000_000


def sample(value=None, quality="ok", mono_ns=0, kind="u64"):
    doc = {"quality": quality, "mono_ns": mono_ns, "type": kind}
    if value is not None:
        doc["value"] = value
    return doc


# Hand arithmetic, written out so a reader can check it without running anything.
CASES = [
    {
        "case": "one-second-linear",
        "doc": "prev=1000 at t=2s, cur=1500 at t=3s: delta=500 over dt=1s -> 500/s; no missed sample.",
        "nominal_interval_ns": S,
        "previous": sample(1000, mono_ns=2 * S),
        "current": sample(1500, mono_ns=3 * S),
        "expect": {"delta": 500, "denominator": S, "per_second": 500, "missed": 0},
    },
    {
        "case": "three-second-gap-is-an-average",
        "doc": (
            "prev=0 at t=0, cur=3000 at t=3s: delta=3000 over dt=3s -> 1000/s. The delta is NOT "
            "attributed to a single nominal bucket (that would be 3000/s), and two nominal samples "
            "were missed: floor(3s/1s)-1 = 2."
        ),
        "nominal_interval_ns": S,
        "previous": sample(0, mono_ns=0),
        "current": sample(3000, mono_ns=3 * S),
        "expect": {"delta": 3000, "denominator": 3 * S, "per_second": 1000, "missed": 2},
    },
    {
        "case": "sub-nominal-interval",
        "doc": "prev=0 at t=0, cur=100 at t=0.25s: delta=100 over dt=0.25s -> 400/s; a shorter interval is not a missed sample.",
        "nominal_interval_ns": S,
        "previous": sample(0, mono_ns=0),
        "current": sample(100, mono_ns=S // 4),
        "expect": {"delta": 100, "denominator": S // 4, "per_second": 400, "missed": 0},
    },
    {
        "case": "zero-delta-is-a-measurement",
        "doc": "prev=cur=42 at t=1s and t=2s: delta=0 over dt=1s -> 0/s. A zero is a value, not an absence (plan §C.2).",
        "nominal_interval_ns": S,
        "previous": sample(42, mono_ns=S),
        "current": sample(42, mono_ns=2 * S),
        "expect": {"delta": 0, "denominator": S, "per_second": 0, "missed": 0},
    },
    {
        "case": "zero-interval-has-no-rate",
        "doc": "prev=5 and cur=9 at the SAME instant t=7: dividing by zero dt is not a rate, so there is none.",
        "nominal_interval_ns": S,
        "previous": sample(5, mono_ns=7),
        "current": sample(9, mono_ns=7),
        "expect_refusal": {"reason": "zero-interval", "reading": None},
    },
    {
        "case": "backwards-interval-has-no-rate",
        "doc": "prev=5 at t=10, cur=9 at t=9: the monotonic clock went backwards. Never clamped to a tiny dt.",
        "nominal_interval_ns": S,
        "previous": sample(5, mono_ns=10),
        "current": sample(9, mono_ns=9),
        "expect_refusal": {"reason": "negative-interval", "reading": None},
    },
    {
        "case": "counter-reset",
        "doc": "prev=100 at t=10, cur=7 at t=11: a decrease. The new reading is the baseline, no delta of either sign.",
        "nominal_interval_ns": S,
        "previous": sample(100, mono_ns=10),
        "current": sample(7, mono_ns=11),
        "expect_refusal": {"reason": "counter-reset", "reading": "current"},
    },
    {
        "case": "smallest-decrease-is-a-reset",
        "doc": "prev=1, cur=0: the smallest possible decrease. Still a reset, never a wrapping delta near 2^64.",
        "nominal_interval_ns": S,
        "previous": sample(1, mono_ns=0),
        "current": sample(0, mono_ns=S),
        "expect_refusal": {"reason": "counter-reset", "reading": "current"},
    },
    {
        "case": "u64-extremes",
        "doc": "prev=0, cur=2^64-1 over dt=1s: the largest representable delta; no wrapping involved.",
        "nominal_interval_ns": S,
        "previous": sample(0, mono_ns=0),
        "current": sample(2**64 - 1, mono_ns=S),
        "expect": {"delta": 2**64 - 1, "denominator": S, "missed": 0},
    },
    {
        "case": "top-equal",
        "doc": "prev=cur=2^64-1 over dt=1s: a zero delta at the top of the range.",
        "nominal_interval_ns": S,
        "previous": sample(2**64 - 1, mono_ns=0),
        "current": sample(2**64 - 1, mono_ns=S),
        "expect": {"delta": 0, "denominator": S, "per_second": 0, "missed": 0},
    },
    {
        "case": "previous-absent",
        "doc": "The previous reading is `absent`: the gap propagates the `absent` code and no rate is invented.",
        "nominal_interval_ns": S,
        "previous": sample(quality="absent", mono_ns=0),
        "current": sample(5, mono_ns=S),
        "expect_refusal": {"reason": "missing", "reading": "previous"},
    },
    {
        "case": "current-unreadable",
        "doc": "The current reading is `unreadable`: that code propagates unchanged.",
        "nominal_interval_ns": S,
        "previous": sample(5, mono_ns=0),
        "current": sample(quality="unreadable", mono_ns=S),
        "expect_refusal": {"reason": "missing", "reading": "current"},
    },
    {
        "case": "current-not-sampled",
        "doc": "The current reading was shed under load (`not-sampled`): a gap, not a zero.",
        "nominal_interval_ns": S,
        "previous": sample(5, mono_ns=0),
        "current": sample(quality="not-sampled", mono_ns=S),
        "expect_refusal": {"reason": "missing", "reading": "current"},
    },
    {
        "case": "wrong-value-type",
        "doc": "An f64 sample is not a counter reading; asking for a rate of it is a caller defect, not a panic.",
        "nominal_interval_ns": S,
        "previous": sample(quality="ok", mono_ns=0, kind="f64"),
        "current": sample(5, mono_ns=S),
        "expect_refusal": {"reason": "wrong-value-type", "reading": "previous"},
    },
    {
        "case": "reading-wins-over-interval",
        "doc": (
            "The current reading is `unreadable` AND the interval is backwards: the missing reading "
            "is reported first, because that is the fact a consumer can act on."
        ),
        "nominal_interval_ns": S,
        "previous": sample(5, mono_ns=10),
        "current": sample(quality="unreadable", mono_ns=9),
        "expect_refusal": {"reason": "missing", "reading": "current"},
    },
    {
        "case": "interval-wins-over-reset",
        "doc": "The interval is backwards AND the counter decreased: the interval is reported, since dt is unusable first.",
        "nominal_interval_ns": S,
        "previous": sample(5, mono_ns=10),
        "current": sample(3, mono_ns=9),
        "expect_refusal": {"reason": "negative-interval", "reading": None},
    },
    {
        "case": "zero-nominal-interval",
        "doc": "A nominal interval of 0 cannot report gaps: it reports 0 missed samples rather than dividing by zero.",
        "nominal_interval_ns": 0,
        "previous": sample(0, mono_ns=0),
        "current": sample(10, mono_ns=S),
        "expect": {"delta": 10, "denominator": S, "per_second": 10, "missed": 0},
    },
    {
        "case": "many-missed-samples",
        "doc": "prev=0 at t=0, cur=3600 at t=3600s: delta=3600 over 3600s -> 1/s, with floor(3600/1)-1 = 3599 missed samples.",
        "nominal_interval_ns": S,
        "previous": sample(0, mono_ns=0),
        "current": sample(3600, mono_ns=3600 * S),
        "expect": {"delta": 3600, "denominator": 3600 * S, "per_second": 1, "missed": 3599},
    },
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    document = {
        "doc": (
            "Hand-computed rate-derivation oracles (plan v4 C.3 and F.3 SP-2). Each case names "
            "the arithmetic in `doc`; the Rust model test and the independent Python reference "
            "both assert these values."
        ),
        "cases": CASES,
    }
    (OUT / "rates.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(CASES)} hand-computed rate cases into {OUT / 'rates.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

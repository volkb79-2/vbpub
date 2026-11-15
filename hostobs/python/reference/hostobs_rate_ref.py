#!/usr/bin/env python3
"""Independent reference implementation of the post-processing probe plan (plan v4 §C.3).

The rule the fixture set pins:

* counters stay raw; a **rate** is a difference divided by the *actual* monotonic
  interval between the two readings;
* `dt == 0` yields no rate, `dt < 0` yields no rate (never clamped);
* a decrease of the raw counter is a **reset**: the new reading is the baseline and no
  delta of either sign is produced;
* a missing reading propagates its own quality code, and is never a zero;
* the number of missed nominal samples is reported, never guessed.

This module is written from the specification, not from the Rust code
(``hostobs-model/src/rate.rs``), and both implementations are checked against the same
committed fixture ``fixtures/rates/rates.json``. Standard library only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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

U64_MIN = 0
U64_MAX = 2**64 - 1


class RateError(ValueError):
    """A refused rate; ``reason`` matches the Rust `NoRate` variant name."""

    def __init__(self, reason: str, reading: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.reading = reading

    def as_dict(self) -> dict:
        return {"reason": self.reason, "reading": self.reading}


def derive_rate(previous: dict, current: dict, nominal_interval_ns: int) -> dict:
    """Return ``{"numerator", "denominator", "per_second", "delta", "missed"}`` or raise."""
    previous = _reading(previous, "previous")
    current = _reading(current, "current")
    elapsed = current["mono_ns"] - previous["mono_ns"]
    if elapsed < 0:
        raise RateError("negative-interval")
    if elapsed == 0:
        raise RateError("zero-interval")
    if current["value"] < previous["value"]:
        raise RateError("counter-reset", "current")
    delta = current["value"] - previous["value"]
    missed = 0
    if nominal_interval_ns > 0:
        intervals = elapsed // nominal_interval_ns
        missed = min(intervals - 1 if intervals > 0 else 0, 2**32 - 1)
    return {
        "delta": delta,
        "denominator": elapsed,
        "per_second": delta * 1_000_000_000.0 / elapsed,
        "missed": missed,
    }


def _reading(sample: dict, which: str) -> dict:
    quality = sample["quality"]
    if quality not in QUALITIES:
        raise RateError(f"unknown-{which}-quality")
    if quality != "ok":
        raise RateError("missing", which)
    if sample.get("type", "u64") != "u64":
        raise RateError("wrong-value-type", which)
    value = sample["value"]
    if not isinstance(value, int) or value < U64_MIN or value > U64_MAX:
        raise RateError(f"{which}-value-out-of-range")
    return {"value": value, "mono_ns": sample["mono_ns"]}


def check_case(case: dict) -> None:
    try:
        actual = derive_rate(case["previous"], case["current"], case["nominal_interval_ns"])
    except RateError as error:
        expected = case.get("expect_refusal")
        if expected is None:
            raise AssertionError(f"{case['case']}: refused ({error.as_dict()}) but a rate was expected") from None
        for key, value in expected.items():
            if error.as_dict().get(key) != value:
                raise AssertionError(
                    f"{case['case']}: expected {expected}, got {error.as_dict()}"
                ) from None
        return
    expected = case.get("expect")
    if expected is None:
        raise AssertionError(f"{case['case']}: produced a rate but a refusal was expected")
    for key, value in expected.items():
        if key == "per_second":
            # The oracle cases all have integral per-second rates, so this is an exact
            # comparison; a case with a fractional rate would carry a tolerance field.
            if abs(actual["per_second"] - value) > case.get("per_second_tolerance", 1e-9):
                raise AssertionError(f"{case['case']}: per_second {actual['per_second']} != {value}")
            continue
        if actual[key] != value:
            raise AssertionError(f"{case['case']}: {key} {actual[key]} != {value}")


def main(argv: list[str]) -> int:
    fixtures = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2] / "fixtures" / "rates"
    cases = json.loads((fixtures / "rates.json").read_text(encoding="utf-8"))["cases"]
    for case in cases:
        check_case(case)
    rates = sum(1 for case in cases if "expect" in case)
    print(
        f"{len(cases)} rate cases agree with hand-computed expectations "
        f"({rates} rates, {len(cases) - rates} refusals)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

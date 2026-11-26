#!/usr/bin/env python3
"""Check the declared R1 floor against a measured `llvm-cov` summary.

Two directions, both able to fail, because either one alone is a false
certification:

- a floor ABOVE the measured value cannot be reached by the lane it declares
  (the gate would be red for a tree that is as covered as it can be);
- a floor more than `MAX_SLACK` points BELOW the measured value protects
  almost nothing and only looks like a measurement.

`coverage-floors.toml` is the declaration (one `[floor]` table with
`pct = <number>`); this tool never writes it, so a rise in coverage is a
deliberate edit with a reason rather than an automatic drift.

Usage: check-floors.py COV-SUMMARY-JSON COVERAGE-FLOORS-TOML
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

# How far below the measured value the declared floor may sit. A floor is a
# regression guard: one full point is the estate's habit, four is the widest
# this tool accepts before it calls the declaration meaningless.
MAX_SLACK = 4.0


def measured_pct(summary: dict) -> float:
    totals = summary["data"][0]["totals"]
    lines = totals["lines"]
    if lines["count"] == 0:
        raise ValueError("cov summary reports zero executable lines")
    return 100.0 * lines["covered"] / lines["count"]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: check-floors.py COV-SUMMARY-JSON COVERAGE-FLOORS-TOML", file=sys.stderr)
        return 2
    summary = __import__("json").loads(Path(argv[1]).read_text(encoding="utf-8"))
    declared = tomllib.loads(Path(argv[2]).read_text(encoding="utf-8"))
    floor = declared["floor"]["pct"]
    measured = measured_pct(summary)
    print(f"coverage-floor: declared {floor:.2f}%, measured {measured:.4f}%")
    if floor > measured:
        print(
            f"ERROR: the declared floor {floor:.2f}% is above the measured {measured:.4f}%; "
            "the lane cannot pass on this tree",
            file=sys.stderr,
        )
        return 1
    if measured - floor > MAX_SLACK:
        print(
            f"ERROR: the declared floor {floor:.2f}% is {measured - floor:.2f} points below the measured "
            f"{measured:.4f}%; raise it in a deliberate edit (max slack {MAX_SLACK})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

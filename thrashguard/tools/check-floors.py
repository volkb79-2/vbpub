#!/usr/bin/env python3
"""Check the declared R1 floor against a measured LCOV artifact.

Two directions, both able to fail, because either one alone is a false
certification:

- a floor ABOVE the measured value cannot be reached by the lane it declares
  (the gate would be red for a tree that is as covered as it can be);
- a floor more than `MAX_SLACK` points BELOW the measured value protects
  almost nothing and only looks like a measurement.

**The measured value is the JUDGED SET, not the whole crate.** Assay's whole-target
lane reports coverage over `judge.targets` only, so a floor compared against the
whole crate would be comparing two different denominators: `src/main.rs` (the
binary) is largely untestable from a unit lane, and including it would let the
floor drift down without anyone deciding to lower it. The judged set is read from
`assay.toml` here, so this check and the lane can never disagree about what is
being floored.

A target that `assay.toml` lists but the artifact does not measure is a REFUSAL,
not a zero: Assay itself refuses such a lane (`NO_MEASUREMENT/TARGET_NOT_MEASURED`),
and a floor computed over a silently smaller set would certify nothing.

`coverage-floors.toml` is the declaration (one `[floor]` table with
`pct = <number>`); this tool never writes it, so a rise in coverage is a
deliberate edit with a reason rather than an automatic drift.

Usage: check-floors.py LCOV-INFO COVERAGE-FLOORS-TOML ASSAY-TOML LANE
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

# How far below the measured value the declared floor may sit. A floor is a
# regression guard: one full point is the estate's habit, four is the widest
# this tool accepts before it calls the declaration meaningless.
MAX_SLACK = 4.0


def read_lcov(path: Path) -> dict[str, list[int]]:
    """`{source file: [hit count per DA record]}`, from an LCOV artifact."""
    files: dict[str, list[int]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("SF:"):
            current = line[3:]
            if current.startswith("./"):
                current = current[2:]
            files.setdefault(current, [])
        elif line.startswith("DA:") and current is not None:
            # DA:<line>,<hits>[,<checksum>] -- hits is what R1 counts.
            fields = line[3:].split(",")
            if len(fields) < 2:
                raise ValueError(f"{path}: malformed DA record {line!r}")
            files[current].append(int(fields[1]))
        elif line == "end_of_record":
            current = None
    return files


def judged_targets(assay_toml: Path, lane: str) -> list[str]:
    declaration = tomllib.loads(assay_toml.read_text(encoding="utf-8"))
    lanes = declaration.get("lanes", {})
    if lane not in lanes:
        raise ValueError(f"{assay_toml}: no lane {lane!r}")
    targets = lanes[lane]["judge"].get("targets")
    if not targets:
        raise ValueError(f"{assay_toml}: lane {lane!r} declares no judge.targets")
    return list(targets)


def match(files: dict[str, list[int]], target: str, crate: str) -> str | None:
    """The artifact key for a project-relative target.

    LCOV records absolute or crate-relative paths; Assay and this check must agree
    on the same file, so a suffix match on `/`-joined components is used and two
    candidates are a refusal (an ambiguous key is not a measurement).
    """
    matches = [
        key
        for key in files
        if key == target or key.endswith("/" + target) or key == f"{crate}/{target}"
    ]
    if len(matches) > 1:
        raise ValueError(f"{target!r} matches several artifact entries: {matches}")
    return matches[0] if matches else None


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "usage: check-floors.py LCOV-INFO COVERAGE-FLOORS-TOML ASSAY-TOML LANE",
            file=sys.stderr,
        )
        return 2
    lcov_path, floors_path, assay_path, lane = (Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4])
    crate = assay_path.parent.name
    declared = tomllib.loads(floors_path.read_text(encoding="utf-8"))
    floor = declared["floor"]["pct"]
    targets = judged_targets(assay_path, lane)
    files = read_lcov(lcov_path)

    covered = 0
    executable = 0
    unmeasured = []
    for target in targets:
        key = match(files, target, crate)
        if key is None:
            unmeasured.append(target)
            continue
        covered += sum(1 for hits in files[key] if hits > 0)
        executable += len(files[key])
    if unmeasured:
        print(
            f"ERROR: the declaration judges {len(targets)} target(s) but the artifact "
            f"measures none of: {unmeasured}; a floor over a smaller set is not a measurement",
            file=sys.stderr,
        )
        return 1
    if executable == 0:
        print("ERROR: the artifact reports zero executable lines in the judged set", file=sys.stderr)
        return 1

    measured = 100.0 * covered / executable
    print(f"coverage-floor: declared {floor:.2f}%, measured {measured:.4f}% over {len(targets)} judged target(s)")
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

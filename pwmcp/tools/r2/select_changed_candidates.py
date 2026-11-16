#!/usr/bin/env python3
"""Select the R2 candidates whose mutation sites fall inside a release diff (PWMCP-71).

The `r2` lane plans the WHOLE target. A release-to-release campaign usually wants only the
code that release changed, which is a supported SUBSET run: the candidates left out are
reported not started and run-gate records the run as `selective`, so it is never a
whole-target R2 PASS.

Two steps that are easy to get wrong by hand, and are therefore computed here:

* reading the NEW-file line ranges out of a `git diff -U0` hunk header
  (`@@ -a,b +c,d @@` -> lines ``c .. c + d - 1``; ``d`` omitted means one line, and a pure
  deletion hunk ``d == 0`` covers no lines at all);
* matching a candidate's ``path``/``lineno`` from ``assay plan r2 --json`` against those
  ranges, with the diff's ``a/`` and ``b/`` prefixes stripped and the paths compared as
  repository-root-relative strings.

Usage (on the release checkout):

    assay plan r2 --json > plan.json
    git diff -U0 <base>..<release> -- <source paths> > release.diff
    python3 tools/r2/select_changed_candidates.py --plan plan.json --diff release.diff \\
        --out .run-gate/only-candidates-<scope>.txt

The output is one candidate id per line, in plan order, ready for
``run-gate r2 --only-candidates FILE``. It prints a one-line summary (selected/planned) to
stderr so the campaign evidence can record it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Repository-root-relative path -> inclusive NEW-file line ranges the diff touched."""

    ranges: dict[str, list[tuple[int, int]]] = {}
    path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].split("\t", 1)[0].strip()
            path = None if target == "/dev/null" else re.sub(r"^b/", "", target)
            if path is not None:
                ranges.setdefault(path, [])
            continue
        if path is None or not line.startswith("@@"):
            continue
        match = HUNK.match(line)
        if match is None:
            raise ValueError(f"unparseable hunk header: {line}")
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        if count <= 0:  # a pure deletion touches no line in the new file
            continue
        ranges[path].append((start, start + count - 1))
    return {p: r for p, r in ranges.items() if r}


def select_candidates(plan: dict, ranges: dict[str, list[tuple[int, int]]]) -> list[str]:
    """Candidate ids whose (path, lineno) fall inside a changed range, in plan order."""

    selected: list[str] = []
    candidates = plan.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("the plan has no 'candidates' list (is this `assay plan r2 --json` output?)")
    for candidate in candidates:
        path = candidate.get("path")
        lineno = candidate.get("lineno")
        candidate_id = candidate.get("id")
        if not (isinstance(path, str) and isinstance(lineno, int) and isinstance(candidate_id, str)):
            raise ValueError(f"malformed candidate in the plan: {candidate!r}")
        for low, high in ranges.get(path, ()):
            if low <= lineno <= high:
                selected.append(candidate_id)
                break
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", required=True, type=Path, help="`assay plan r2 --json` output")
    parser.add_argument("--diff", required=True, type=Path, help="`git diff -U0 <base>..<release>` output")
    parser.add_argument("--out", required=True, type=Path, help="candidate-id list for --only-candidates")
    args = parser.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    ranges = changed_line_ranges(args.diff.read_text(encoding="utf-8"))
    selected = select_candidates(plan, ranges)
    planned = len(plan["candidates"])
    if not selected:
        print(
            f"no candidate falls inside the changed lines of {args.diff.name}; "
            "check the diff base and the paths (a changed-line scope is empty when the release "
            "touched no mutated line)",
            file=sys.stderr,
        )
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(f"{candidate_id}\n" for candidate_id in selected), encoding="utf-8")
    print(f"selected {len(selected)} of {planned} candidates -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Out-of-band recount of a branch's own changed executable lines.

The coverage gate resolves its base from HEAD's parent count, so a merge
commit silently measures the WRONG delta (see the amendment's gate-base
trap). CR-01's first run reported a green 360/360 while measuring the
PREVIOUS package's lines. This script recounts independently: walk
`git diff --unified=0 <base>...HEAD` hunks, intersect each hunk's new-side
line numbers with coverage.py's own statement set for that file, and sum.

Run it from the package root (the directory holding `src/`); the pathspec is
interpreted relative to that, and git's repo-relative output paths are mapped
back with `--relative`, so a package nested inside a larger repo works.

Usage:  count_changed.py [base] [source_dir]     (defaults: main, src/nyxloom)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from coverage.parser import PythonParser

HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def changed_new_lines(base: str, source: str) -> dict[str, set[int]]:
    """{path relative to cwd: added/edited new-side line numbers}."""
    diff = subprocess.run(
        ["git", "diff", "--relative", "--unified=0", f"{base}...HEAD",
         "--", source],
        capture_output=True, text=True, check=True).stdout
    out: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = None if target == "/dev/null" else (
                target[2:] if target.startswith("b/") else target)
            if current is not None:
                out.setdefault(current, set())
        elif line.startswith("@@") and current is not None:
            m = HUNK.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                out[current].update(range(start, start + count))
    return out


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "main"
    source = sys.argv[2] if len(sys.argv) > 2 else "src/nyxloom"
    total = 0
    rows: list[tuple[str, int]] = []
    for rel, lines in sorted(changed_new_lines(base, source).items()):
        if not rel.endswith(".py"):
            continue
        path = Path(rel)
        if not path.exists():
            print(f"  ?? {rel}: not in this checkout")
            continue
        parser = PythonParser(text=path.read_text(encoding="utf-8"),
                              filename=rel)
        parser.parse_source()
        n = len(lines & parser.statements)
        if n:
            rows.append((rel, n))
            total += n
    for rel, n in rows:
        print(f"  {n:5d}  {rel}")
    print(f"TOTAL changed executable lines vs {base}: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

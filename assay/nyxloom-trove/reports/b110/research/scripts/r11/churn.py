#!/usr/bin/env python3
"""R11 churn: for each assay source file, in how many of the consecutive
release intervals (assay-vX tag -> next tag, last tag -> HEAD) did it change?
A file that changes in most intervals is not "stable": moving it into a
separately qualified library would re-qualify the library just as often.

Usage: churn.py <clone_root> <cand.json> [n_intervals]
"""
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
cand = json.load(open(sys.argv[2]))
n = int(sys.argv[3]) if len(sys.argv) > 3 else 12


def git(*a):
    return subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True).stdout


tags = git("tag", "-l", "assay-v*", "--sort=creatordate").split()
points = tags[-n:] + ["HEAD"]
changed = {}
for a, b in zip(points, points[1:]):
    files = set(git("diff", "--name-only", a, b, "--", "assay/src/assay").split())
    for f in files:
        changed.setdefault(f, []).append(b)
intervals = len(points) - 1
rows = []
for rel, v in cand["per_file"].items():
    path = "assay/" + rel
    k = len(changed.get(path, []))
    rows.append((k, v["n"], rel))
rows.sort(key=lambda r: (-r[1]))
print(f"intervals: {intervals} ({points[0]} .. HEAD)")
tot = sum(r[1] for r in rows)
stable = sum(r[1] for r in rows if r[0] <= intervals // 4)
print(f"candidates in files changed in <= {intervals // 4}/{intervals} intervals: {stable} of {tot}")
for k, c, rel in rows:
    if c:
        print(f"  {c:4d} cand  changed {k:2d}/{intervals}  {rel}")

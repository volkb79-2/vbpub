#!/usr/bin/env python3
"""Fail unless every first-party wheel declared in pip/wheels.list is present.

Used by the Dockerfile's first-party wheel RUN. A build whose ``pip/wheels/`` is empty,
or lacks a declared distribution, would otherwise ship an image without ciu/cmru/topos/
nyxloom/cli-extended and nothing would fail until a user ran the missing CLI. There is no
opt-out: an empty or incomplete set is an error in every build mode.

Names are compared as *distributions* (PEP 503 normalisation), not as file names: the
wheel ``cli_extended-0.4.0-py3-none-any.whl`` satisfies the declaration ``cli-extended``.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def declared_names(wheels_list: Path) -> list[str]:
    names = []
    for raw in wheels_list.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(normalize(line))
    return names


def present_names(wheels_dir: Path) -> dict[str, list[str]]:
    """Normalised distribution name -> wheel file names (first filename component)."""
    found: dict[str, list[str]] = {}
    if wheels_dir.is_dir():
        for wheel in sorted(wheels_dir.glob("*.whl")):
            found.setdefault(normalize(wheel.name.split("-", 1)[0]), []).append(wheel.name)
    return found


def check(wheels_dir: Path, wheels_list: Path) -> list[str]:
    """Return the problems; an empty list means the set is complete."""
    declared = declared_names(wheels_list)
    if not declared:
        return [f"{wheels_list} declares no first-party wheels"]
    present = present_names(wheels_dir)
    if not present:
        return [
            f"{wheels_dir} contains no wheels, but {wheels_list} declares: {', '.join(declared)}. "
            "Run scripts/resolve-devcontainers-release.py (or a --local-dev build) first."
        ]
    missing = [name for name in declared if name not in present]
    if missing:
        return [
            f"first-party wheel(s) declared in {wheels_list} but absent from {wheels_dir}: "
            f"{', '.join(missing)} (present: {', '.join(sorted(present))})"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels-dir", type=Path, required=True)
    parser.add_argument("--wheels-list", type=Path, required=True)
    args = parser.parse_args()
    problems = check(args.wheels_dir, args.wheels_list)
    for problem in problems:
        print(f"[ERROR] {problem}", file=sys.stderr)
    if problems:
        return 1
    present = present_names(args.wheels_dir)
    print(f"[INFO] wheels.list declares: {' '.join(declared_names(args.wheels_list))}", file=sys.stderr)
    for files in present.values():
        for name in files:
            print(f"[INFO] first-party wheel present: {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

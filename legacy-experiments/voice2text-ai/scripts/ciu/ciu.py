#!/usr/bin/env python3
"""Wrapper to execute the canonical CIU script from the repository root."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    current = Path(__file__).resolve()
    repo_root = current.parents[4]
    canonical = repo_root / "scripts" / "ciu" / "ciu.py"

    if not canonical.exists():
        raise SystemExit(f"[ERROR] Canonical CIU not found at: {canonical}")

    sys.argv[0] = str(canonical)
    runpy.run_path(str(canonical), run_name="__main__")


if __name__ == "__main__":
    main()

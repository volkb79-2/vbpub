#!/usr/bin/env python3
"""DEPRECATED — use ``./cmru.py`` (or install: ``pip install -e cmru`` → ``cmru``).

Kept as a back-compat shim for one deprecation release (SPEC S-CLI.4): it warns, then
forwards to the cmru CLI unchanged. Will be removed next release.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "cmru" / "src"))

from cmru.cli import main  # noqa: E402


if __name__ == "__main__":
    print("[WARN] release-all.py is deprecated — use ./cmru.py instead (SPEC S-CLI.4).", file=sys.stderr)
    main()

#!/usr/bin/env python3
"""Repo-root entry point for the cmru CLI.

This is a thin shim: it puts ``cmru/src`` on ``sys.path`` and calls ``cmru.cli:main``,
so ``./release-all.py <verb> [args]`` is exactly ``cmru <verb> [args]``. Run
``./release-all.py --help`` for the verb list and the typical workflow. For a logged,
end-to-end run with optional asset cleanup, use ``./release-runner.py`` instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "cmru" / "src"))

from cmru.cli import main  # noqa: E402


if __name__ == "__main__":
    main()

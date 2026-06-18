#!/usr/bin/env python3
"""Publish cmru's wheel — thin delegate to cmru's built-in wheel handler.

The monorepo cmru.toml releases cmru via the built-in handler directly (no script);
this is kept for the standalone cmru/cmru.toml path. Both route through
cmru.handlers → cmru.release, so there is a single implementation — no duplication.

Env contract (set by the step runner / cmru orchestration): GITHUB_PUSH_PAT,
GITHUB_USERNAME, GITHUB_REPO. Release notes: CMRU_RELEASE_NOTES (optional).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # cmru/
sys.path.insert(0, str(ROOT / "src"))

from cmru.handlers import main  # noqa: E402

if __name__ == "__main__":
    main([
        "wheel-publish",
        "--prefix", "cmru",
        "--cwd", str(ROOT),
        "--notes-env", "CMRU_RELEASE_NOTES",
    ])

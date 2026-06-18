#!/usr/bin/env python3
"""Validate the latest CIU wheel — thin delegate to cmru's built-in validator.

Resolution + asset/sha256 assertions live once in cmru.release.validate_latest_release
(via cmru.handlers wheel-validate). This wrapper only locates the repo .env for
standalone runs and passes CIU's prefix.

Env contract: GITHUB_USERNAME, GITHUB_REPO (token optional for public repos:
GH_TOKEN / GITHUB_PUSH_PAT).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]   # vbpub repo root
CIU_ROOT = Path(__file__).resolve().parents[1]    # ciu/
sys.path.insert(0, str(REPO_ROOT / "cmru" / "src"))

from cmru.handlers import main  # noqa: E402

if __name__ == "__main__":
    env_file = os.getenv("CIU_ENV_FILE") or str(REPO_ROOT / ".env")
    if not Path(env_file).exists():
        env_file = str(CIU_ROOT / ".env")
    main(["wheel-validate", "--prefix", "ciu", "--env-file", env_file])

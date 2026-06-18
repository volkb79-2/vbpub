#!/usr/bin/env python3
"""Publish CIU's wheel — thin delegate to cmru's built-in wheel handler.

All wheel-publish logic lives once in cmru.release / cmru.handlers. This wrapper only
adds CIU's standalone conveniences: locate the repo .env (so GITHUB_* resolve when run
by hand) and pass CIU's prefix + release-notes env. When run via cmru orchestration,
cmru exports GITHUB_* itself and the .env is simply a no-op.

Env contract: GITHUB_PUSH_PAT, GITHUB_USERNAME, GITHUB_REPO (env wins over .env).
Release notes: CIU_RELEASE_NOTES (optional).
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
    main([
        "wheel-publish",
        "--prefix", "ciu",
        "--cwd", str(CIU_ROOT),
        "--notes-env", "CIU_RELEASE_NOTES",
        "--env-file", env_file,
    ])

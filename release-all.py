#!/usr/bin/env python3
"""Repo-root wrapper for vbpub release manager."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANAGER_SRC = ROOT / "release-manager" / "src"

sys.path.insert(0, str(MANAGER_SRC))

from release_manager.cli import main  # noqa: E402


if __name__ == "__main__":
    main()

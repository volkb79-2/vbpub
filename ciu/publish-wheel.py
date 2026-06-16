#!/usr/bin/env python3
"""Publish CIU wheel using config-driven step runner."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "cmru" / "src"))

from cmru.runner import run_step  # noqa: E402


def main() -> None:
    config_path = ROOT / "build-push.toml"
    run_step(config_path, "publish-wheel", None)


if __name__ == "__main__":
    main()

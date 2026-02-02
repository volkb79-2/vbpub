#!/usr/bin/env python3
"""Build modern-debian-tools-python-debug images using config-driven step runner."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
MANAGER_SRC = REPO_ROOT / "release-manager" / "src"

sys.path.insert(0, str(MANAGER_SRC))

from release_manager.step_runner import run_step  # noqa: E402


def main() -> None:
    config_path = ROOT / "build-push.toml"
    run_step(config_path, "build-images", None)


if __name__ == "__main__":
    main()

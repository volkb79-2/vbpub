#!/usr/bin/env python3
"""Build stack bundle and wheel using config-driven builder."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
MANAGER_SRC = REPO_ROOT / "release-manager" / "src"

sys.path.insert(0, str(MANAGER_SRC))

from release_manager.bundle_builder import run_bundle  # noqa: E402


def main() -> None:
    config_path = ROOT / "bundle.toml"
    run_bundle(config_path)


if __name__ == "__main__":
    main()

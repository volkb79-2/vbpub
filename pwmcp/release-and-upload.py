#!/usr/bin/env python3
"""Build bundle and upload release assets (config-driven)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(script: str) -> None:
    subprocess.run([sys.executable, str(ROOT / script)], check=True)


def main() -> None:
    run("release-stack-bundle.py")
    run("upload-release-assets.py")


if __name__ == "__main__":
    main()

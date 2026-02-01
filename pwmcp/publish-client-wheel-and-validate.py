#!/usr/bin/env python3
"""Publish and validate pwmcp-client wheel."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(script: str) -> None:
    subprocess.run([sys.executable, str(ROOT / script)], check=True)


def main() -> None:
    run("publish-client-wheel.py")
    run("validate-client-wheel-latest.py")


if __name__ == "__main__":
    main()

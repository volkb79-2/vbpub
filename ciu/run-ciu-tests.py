#!/usr/bin/env python3
"""Run CIU test suite."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    subprocess.run([sys.executable, "-m", "pytest", "tests", "-v"], check=True, cwd=str(ROOT))


if __name__ == "__main__":
    main()

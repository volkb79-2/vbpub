#!/usr/bin/env python3
"""Run Playwright MCP client tests."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        check=True,
        cwd=str(ROOT),
    )


if __name__ == "__main__":
    main()

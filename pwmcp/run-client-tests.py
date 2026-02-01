#!/usr/bin/env python3
"""Run PWMCP client tests."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    python_path_entries = [
        str(ROOT / "client" / "src"),
        str(ROOT / "shared" / "src"),
    ]
    existing_path = os.environ.get("PYTHONPATH")
    if existing_path:
        python_path_entries.append(existing_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)

    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        check=True,
        cwd=str(ROOT),
        env=env,
    )


if __name__ == "__main__":
    main()

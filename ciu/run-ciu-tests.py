#!/usr/bin/env python3
"""Run CIU's isolated, parallel, 100%-coverage release gate.

Coverage is collected by pytest-cov rather than ``coverage run`` so xdist worker
processes are included.  The nyxloom implementation gate adds its changed-line
floor after this complete-suite command; this helper remains usable by releases.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

COV_FAIL_UNDER = "100"


def main() -> None:
    argv = sys.argv[1:]
    cmd = [
        sys.executable, "-m", "pytest", "tests",
        "--cov=ciu",
        "-n", "auto",
        "--cov-branch",
        "--cov-report=term-missing",
        "--cov-report=json:coverage.json",
        f"--cov-fail-under={COV_FAIL_UNDER}",
        *argv,
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


if __name__ == "__main__":
    main()

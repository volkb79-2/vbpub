#!/usr/bin/env python3
"""Run CIU test suite with a coverage gate.

`python run-ciu-tests.py` runs the full suite, prints per-module coverage with the
missing line ranges, and FAILS if total line coverage drops below the floor below.
This is the single command that re-verifies "are the tests still covering the code"
in CI and locally. Ratchet `--cov-fail-under` upward as coverage improves (current
floor reflects the measured baseline; the weak spots are cli.py and the deploy.py
orchestration paths — raise those, then raise the floor).

Pass extra pytest args through: `python run-ciu-tests.py -k provisioning -q`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Total-line-coverage floor. Measured baseline 2026-06-23: 73.9% (706 tests).
# Do not lower; raise it as cli.py / deploy.py coverage improves.
COV_FAIL_UNDER = "73"


def main() -> None:
    argv = sys.argv[1:]
    cmd = [
        sys.executable, "-m", "pytest", "tests",
        "--cov=ciu",
        "--cov-report=term-missing",
        f"--cov-fail-under={COV_FAIL_UNDER}",
        *argv,
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


if __name__ == "__main__":
    main()

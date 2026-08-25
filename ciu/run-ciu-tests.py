#!/usr/bin/env python3
"""Run CIU's isolated, parallel, 100%-coverage release gate.

Coverage is collected by pytest-cov rather than ``coverage run`` so xdist worker
processes are included.  The nyxloom implementation gate adds its changed-line
floor after this complete-suite command; this helper remains usable by releases.

CIU-56: ``--dist loadfile`` (not xdist's default ``load``) is required for
correct coverage of any module ``hooks_runner._load_hook_module`` loads by path
under a synthetic non-``ciu`` module name (e.g. ``src/ciu/hook_templates/*``).
Under ``load``, xdist may split a test file's functions across workers
arbitrarily; a worker that never imports the module normally records zero
coverage for it even though another worker executed it via the synthetic
loader, and pytest-cov's merge does not reliably reconcile the two — a
non-deterministic false-green (or false-red) depending on scheduling luck.
``loadfile`` keeps every test file's functions on one worker, which is
sufficient because the normal-import and synthetic-load paths for a given
module are always exercised within the same file's test collection today.
Reproduced on a clean baseline: 2 of 3 runs under ``load`` under-reported
coverage for ``hook_templates/post_compose_db.py`` by exactly its module size;
0 of 3 did under ``loadfile``.
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
        "--dist", "loadfile",
        "--cov-branch",
        "--cov-report=term-missing",
        "--cov-report=json:coverage.json",
        f"--cov-fail-under={COV_FAIL_UNDER}",
        *argv,
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


if __name__ == "__main__":
    main()

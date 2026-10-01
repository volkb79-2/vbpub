#!/opt/tester-venv/bin/python
"""Run the MDT wizard tests and emit the coverage artifact for assay.

This is deliberately a single process: assay's per-mutant timeout can then
terminate the process it launched without leaving a shell-owned pytest child
behind.  The old ``bash -lc 'coverage ...; coverage json ...'`` shape could
leave the first command's descendants alive after a timeout and exhaust the
tester container's memory during a mutation campaign.
"""

from __future__ import annotations

from pathlib import Path

import coverage
import pytest


def main() -> int:
    Path(".assay").mkdir(exist_ok=True)
    measured = coverage.Coverage(
        branch=True,
        source=["host-setup"],
        data_file=".coverage",
    )
    measured.start()
    try:
        test_exit = int(pytest.main([
            "host-setup/tests/test_buildkit_governance.py",
            "host-setup/tests/test_cli_diagnostics.py",
            "-q",
        ]))
    finally:
        measured.stop()
        measured.save()
    measured.json_report(outfile=".assay/coverage.json")
    return test_exit


if __name__ == "__main__":
    raise SystemExit(main())

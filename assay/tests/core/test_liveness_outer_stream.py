"""O10: `tests/core/test_liveness.py` must not write into an outer candidate's
liveness stream.

When a mutation candidate runs the judge's own suite under the liveness
plugin, `ASSAY_LIVENESS_EVENTS` names the outer stream. The plugin unit tests
in `test_liveness.py` call `pytest_sessionfinish` directly; before the fix
that leaked `session_finish` records into the outer file, and the monitor read
the first one as the end of the session (a quiet test past the post-finish
grace was then marked hung).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import PROJECT_ROOT

from assay import liveness

NESTED_TARGET = "tests/core/test_liveness.py"


def test_o10_nested_liveness_suite_keeps_the_outer_stream_intact(tmp_path: Path) -> None:
    plugin_path = liveness.materialize_liveness_plugin(tmp_path / "plugin")
    events = tmp_path / "events.ndjson"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            NESTED_TARGET,
            "-q",
            "-p",
            liveness.LIVENESS_PLUGIN_MODULE_NAME,
            "-p",
            "no:cacheprovider",
        ],
        cwd=PROJECT_ROOT,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": f"{plugin_path.parent}{os.pathsep}{PROJECT_ROOT / 'src'}",
            liveness.ASSAY_LIVENESS_EVENTS_ENV: str(events),
        },
        capture_output=True,
        text=True,
        timeout=600,  # failsafe only
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    finishes = [index for index, record in enumerate(records) if record["event"] == "session_finish"]
    assert finishes == [len(records) - 1]
    passed = re.search(r"(\d+) passed", result.stdout)
    assert passed is not None
    assert sum(1 for record in records if record["event"] == "test") == int(passed.group(1))

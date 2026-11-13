"""Opt-in, real-binary rehearsal of the planner and IBX driver end to end."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from studies.ibx import driver


@pytest.mark.real_fio
@pytest.mark.time_limit(seconds=1800)
def test_real_fio_rehearses_every_time_based_shape_and_analyzes(tmp_path):
    fio = shutil.which("fio")
    if fio is None:
        pytest.skip(
            "real_fio rehearsal needs upstream fio 3.39; run: "
            f"python3 {driver.DRIVER_PATH} rehearse --fio /path/to/fio-3.39 "
            "--out /tmp/ibx-fio-rehearsal"
        )

    out = tmp_path / "ibx-fio-rehearsal"
    result = subprocess.run(
        [sys.executable, str(driver.DRIVER_PATH), "rehearse", "--fio", fio,
         "--out", str(out)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["fio_version"].startswith("fio-3.39")
    assert summary["cell_sets"] == list(driver.TEST_SCALE_CELL_SETS)
    assert summary["time_based_records"] > 0
    assert summary["file_equivalent_wipe"] == "verified-zeroes"
    assert (out / "rung-choice.md").is_file()

    expected = {
        (cell_set, rung, job.name)
        for cell_set in driver.TEST_SCALE_CELL_SETS
        for rung in driver.CELL_SETS[cell_set][1]
        for job in driver.jobs_for_run(
            "quick" if rung == "part-c" else rung,
            part_c=rung == "part-c", test_scale=True,
        )
        if not job.writes
    }
    records = {}
    for path in (out / "cells").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("fio") is not None:
            records[(record["cell_set"], record["rung"], record["cell"])] = record
    assert expected <= records.keys()
    assert all(records[key]["fio"].get("fio version", "").startswith("fio-3.39")
               for key in expected)
    assert all(str(records[key]["fio"].get("global options", {}).get("time_based", "0")) == "1"
               for key in expected)
    assert "planner-scaled real-fio rehearsal" in (out / "rung-choice.md").read_text(
        encoding="utf-8")

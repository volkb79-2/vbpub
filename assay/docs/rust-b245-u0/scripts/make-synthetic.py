"""Derive the two SYNTHETIC fixture cases from the real `full` case (B245 U0).

Run from assay/tests/fixtures/rust/cargo-mutants. Each synthetic case is a copy of
`full` in which exactly two `CaughtMutant` outcomes get a different Test process
status (`{"Signalled": 9}` and `"Other"`), because no real spike produced those
statuses. Nothing else changes; a test that uses these cases must say so.
"""

import json
import os
import shutil

BASE = "full"
CASES = {
    "synthetic-signalled": {"Signalled": 9},
    "synthetic-other": "Other",
}

for name, status in CASES.items():
    shutil.rmtree(name, ignore_errors=True)
    shutil.copytree(BASE, name)
    os.remove(f"{name}/exit-code.txt")  # no tool run produced this case
    path = f"{name}/mutants.out/outcomes.json"
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    edited = 0
    for outcome in document["outcomes"]:
        if outcome["scenario"] == "Baseline" or outcome["summary"] != "CaughtMutant":
            continue
        if edited == 2:
            break
        for phase in outcome["phase_results"]:
            if phase["phase"] == "Test":
                phase["process_status"] = status
        edited += 1
    assert edited == 2, edited
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    print(name, "edited", edited, "caught mutants ->", status)

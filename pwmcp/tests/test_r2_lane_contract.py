"""PWMCP-59: `r2`'s `max_mutants` is a checkable fact, not folklore.

`pwmcp/assay.toml` `[lanes.r2.judge.mutation] max_mutants` decides whether a release's mutation
campaign can run at all: Assay refuses an over-cap inventory BEFORE executing anything, so a cap
below the measured candidate count turns the release lane into a guaranteed
`BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` after paying for a baseline.

The inventories in `tests/fixtures/r2-inventory/` are RECORDS: each file is one measurement and
says where it came from. A new release plan adds a file; it never edits an old one.
"""

from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path

PWMCP = Path(__file__).resolve().parents[1]
ASSAY = PWMCP / "assay.toml"
RUN_GATE = PWMCP / "run-gate.toml"
INVENTORIES = PWMCP / "tests" / "fixtures" / "r2-inventory"
CONSUMERS = PWMCP / "docs" / "CONSUMERS.md"

# The rule this project publishes: the cap must clear the largest recorded inventory by 10%.
HEADROOM = 1.10


def _lane(path: Path, section: str) -> dict:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    node = config["lanes"]["r2"]
    for key in section.split(".") if section else ():
        node = node[key]
    return node


def _recorded_inventories() -> list[dict]:
    records = []
    for path in sorted(INVENTORIES.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for key in ("recorded", "kind", "candidate_count", "source"):
            assert key in record, f"{path.name}: missing {key}"
        assert record["kind"] in {"measured", "planned"}, record["kind"]
        assert isinstance(record["candidate_count"], int) and record["candidate_count"] > 0, record
        record["fixture"] = path.name
        records.append(record)
    assert records, "no recorded R2 candidate inventory — the cap has nothing to be checked against"
    return records


def test_the_r2_candidate_cap_clears_every_recorded_inventory_with_the_declared_headroom() -> None:
    cap = _lane(ASSAY, "judge.mutation")["max_mutants"]
    records = _recorded_inventories()
    largest = max(records, key=lambda record: record["candidate_count"])
    required = math.ceil(largest["candidate_count"] * HEADROOM)
    assert cap >= required, (
        f"max_mutants={cap} does not clear {largest['fixture']} "
        f"({largest['candidate_count']} candidates, needs >= {required}); an over-cap inventory is "
        "refused before execution, so the release lane cannot complete an unmodified R2 campaign"
    )
    for record in records:
        assert cap > record["candidate_count"], record["fixture"]
    # The cap is a ceiling for ORDINARY growth, not an unreachable number: it stays within the
    # same headroom band it is chosen from, so a typo like 1000000 fails as loudly as 1000.
    assert cap <= math.ceil(largest["candidate_count"] * 2), (
        f"max_mutants={cap} is far above any recorded inventory ({largest['candidate_count']}); "
        "the cap bounds an unexpectedly large run, it is not a free pass"
    )


def test_the_cap_choice_rule_and_the_refresh_obligation_are_documented() -> None:
    """ Consolidation test: a reader can reproduce the number this lane declares. """

    text = " ".join(CONSUMERS.read_text(encoding="utf-8").split())
    lowered = text.lower()
    assert "cap-choice rule" in lowered
    assert "ceil(largest recorded candidate inventory * 1.10)" in text
    assert "10% headroom" in text
    assert "refresh the recorded inventory" in lowered and "tests/fixtures/r2-inventory/" in text
    # the rule names the other declarations it must be reviewed with, and the refusal it prevents
    assert "budget_per_candidate" in text and "jobs_max" in text
    assert "BUDGET_EXCEEDED" in text and "MUTANT_LIMIT_EXCEEDED" in text
    # and it says the cap is a ceiling, not a target -- the misreading that produced this defect
    assert "does not bound a run that is already executing" in text

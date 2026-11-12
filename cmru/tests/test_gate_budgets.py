"""The outer Run-Gate budget of an Assay lane must stay above the Assay lane's own deadline.

The Run-Gate Assay wrappers bound the container; each `assay.toml` lane budget
is the binding deadline inside it. The full R0/R1/R3 lane also nests the
import-break canary attempt budget, while T1's R0/R1 lane has no canary. This
reads both files with `tomllib` and fails when the relation is broken; every
assertion has a controlled-wrong case.
"""
from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
#: (run-gate lane, assay.toml lane): the pairs whose container wraps the inner deadline.
PAIRS = (
    ("assay", "cmru"),
    ("assay-r0-r1", "cmru-r0-r1"),
    ("assay-first-release", "cmru-first-release"),
)
MARGIN_SECONDS = 5 * 60


def seconds(text: str) -> int:
    match = re.fullmatch(r"(\d+)([smh])", text.strip())
    assert match, f"unparsed duration {text!r}"
    return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2)]


def violations(assay_doc: dict, gate_doc: dict) -> list[str]:
    out: list[str] = []
    for outer_name, inner_name in PAIRS:
        outer = gate_doc["lanes"][outer_name]
        inner = assay_doc["lanes"][inner_name]
        if outer["assay_lane"] != inner_name:
            out.append(f"run-gate [{outer_name}] runs assay lane {outer['assay_lane']!r}, not {inner_name!r}")
        if seconds(outer["budget"]) < seconds(inner["budget"]) + MARGIN_SECONDS:
            out.append(
                f"run-gate [{outer_name}] budget {outer['budget']} is not >= assay [{inner_name}] "
                f"budget {inner['budget']} + 5m"
            )
        attempt = inner["judge"].get("canary", {}).get("budget_per_attempt")
        if attempt is not None and seconds(attempt) > seconds(inner["budget"]):
            out.append(
                f"assay [{inner_name}] canary budget_per_attempt {attempt} exceeds its lane budget "
                f"{inner['budget']}"
            )
    return out


def _load() -> tuple[dict, dict]:
    return (
        tomllib.loads((PROJECT / "assay.toml").read_text(encoding="utf-8")),
        tomllib.loads((PROJECT / "run-gate.toml").read_text(encoding="utf-8")),
    )


def test_the_committed_budgets_keep_the_outer_above_the_inner():
    assay_doc, gate_doc = _load()
    assert violations(assay_doc, gate_doc) == []


def test_the_pairs_exist_and_are_what_the_gate_actually_runs():
    assay_doc, gate_doc = _load()
    for outer_name, inner_name in PAIRS:
        assert gate_doc["lanes"][outer_name]["kind"] == "assay"
        assert gate_doc["lanes"][outer_name]["assay_lane"] == inner_name
        judge = assay_doc["lanes"][inner_name]["judge"]
        if inner_name == "cmru-r0-r1":
            assert "canary" not in judge
        else:
            assert "budget_per_attempt" in judge["canary"]


# ─── controlled wrong implementations: each assertion must be able to go red ──────────────

def test_an_outer_budget_equal_to_the_inner_is_reported():
    assay_doc, gate_doc = _load()
    wrong = copy.deepcopy(gate_doc)
    wrong["lanes"]["assay"]["budget"] = assay_doc["lanes"]["cmru"]["budget"]
    assert any("run-gate [assay]" in v and "+ 5m" in v for v in violations(assay_doc, wrong))


def test_an_outer_budget_below_the_inner_is_reported_for_the_first_release_pair_too():
    assay_doc, gate_doc = _load()
    wrong = copy.deepcopy(gate_doc)
    wrong["lanes"]["assay-first-release"]["budget"] = "20m"
    assert any("assay-first-release" in v for v in violations(assay_doc, wrong))


def test_an_outer_budget_below_the_inner_is_reported_for_the_t1_pair_too():
    assay_doc, gate_doc = _load()
    wrong = copy.deepcopy(gate_doc)
    wrong["lanes"]["assay-r0-r1"]["budget"] = "45m"
    assert any("assay-r0-r1" in v for v in violations(assay_doc, wrong))


def test_a_canary_attempt_longer_than_the_lane_is_reported():
    assay_doc, gate_doc = _load()
    wrong = copy.deepcopy(assay_doc)
    wrong["lanes"]["cmru"]["judge"]["canary"]["budget_per_attempt"] = "2h"
    assert any("exceeds its lane budget" in v for v in violations(wrong, gate_doc))


def test_a_miswired_assay_lane_is_reported():
    assay_doc, gate_doc = _load()
    wrong = copy.deepcopy(gate_doc)
    wrong["lanes"]["assay"]["assay_lane"] = "cmru-first-release"
    assert any("not 'cmru'" in v for v in violations(assay_doc, wrong))


def test_the_duration_parser_rejects_what_it_cannot_read():
    assert (seconds("50m"), seconds("60m"), seconds("7h"), seconds("90s")) == (3000, 3600, 25200, 90)
    for bad in ("auto", "1d", "5"):
        try:
            seconds(bad)
        except AssertionError:
            continue
        raise AssertionError(f"{bad!r} was accepted")


#: The known-good cmru suite took up to 825 s under concurrent lanes in the
#: 2026-11-12 R2 rehearsal; the control/candidate timeout must leave headroom above it.
MUTATION_TIMEOUT_FLOOR = 1800


def mutation_timeout_seconds(gate_doc: dict) -> int | None:
    command = "\n".join(gate_doc["lanes"]["mutation"]["argv"])
    match = re.search(r"--timeout-seconds\s+([0-9]+)\b", command)
    return int(match.group(1)) if match else None


def test_the_mutation_lane_timeout_covers_the_known_good_suite():
    _assay, gate = _load()
    timeout = mutation_timeout_seconds(gate)
    assert timeout is not None, "the mutation lane must declare --timeout-seconds"
    assert timeout >= MUTATION_TIMEOUT_FLOOR


def test_a_mutation_timeout_below_the_suite_is_reported():
    _assay, gate = _load()
    wrong = copy.deepcopy(gate)
    wrong["lanes"]["mutation"]["argv"] = [
        part.replace("--timeout-seconds 1800", "--timeout-seconds 120")
        for part in wrong["lanes"]["mutation"]["argv"]
    ]
    assert mutation_timeout_seconds(wrong) == 120 < MUTATION_TIMEOUT_FLOOR

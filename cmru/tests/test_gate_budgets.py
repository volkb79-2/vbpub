"""The outer Run-Gate budget of an Assay lane must stay above the Assay lane's own deadline.

`run-gate.toml [lanes.assay]` bounds the container; `assay.toml [lanes.cmru] budget` is the binding
lane-wide deadline inside it (baseline + import-break canary), and `[judge.canary]
budget_per_attempt` nests inside that. Nothing but a comment tied them together, and a 20m lane
ended BUDGET_EXCEEDED with no failing test on a loaded host (2026-10-21). This reads both files with
`tomllib` and fails when the relation is broken; every assertion has a controlled-wrong case.
"""
from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
#: (run-gate lane, assay.toml lane): the pairs whose container wraps the inner deadline.
PAIRS = (("assay", "cmru"), ("assay-first-release", "cmru-first-release"))
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
        attempt = inner["judge"]["canary"]["budget_per_attempt"]
        if seconds(attempt) > seconds(inner["budget"]):
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
        assert "budget_per_attempt" in assay_doc["lanes"][inner_name]["judge"]["canary"]


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

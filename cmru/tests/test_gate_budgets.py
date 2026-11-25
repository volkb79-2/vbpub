"""cmru-specific budget facts of its own Run-Gate/Assay lanes.

What is left here after P-core-12 (KI-194) is what `run-gate lanes --check`
does NOT decide:

* the EXACT pairing of the two outer lanes with the two inner Assay lanes
  (`--check` only verifies that the named inner lane exists, so a miswire to
  another *existing* lane passes it),
* cmru's canary policy: the R0/R1 lane deliberately has no canary while the
  full lane does,
* the mutation lane's command-level `--timeout-seconds` floor (a command lane's
  argv is not inspected at all).

The generic rules — outer budget >= inner budget + 5m (`--strict`) and the
canary attempt budget <= the lane budget — are enforced by
`run-gate lanes --check --all --strict` in the `estate-lint` lane, so they are
not restated here (the follow-up that closes the remaining gaps above is
tracked by the controller).
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
)


def miswires(assay_doc: dict, gate_doc: dict) -> list[str]:
    """Outer lane -> inner lane name mismatches, for the pairs cmru declares."""
    out: list[str] = []
    for outer_name, inner_name in PAIRS:
        outer = gate_doc["lanes"][outer_name]
        # Existence only: another EXISTING inner lane would pass `lanes --check`.
        if outer["assay_lane"] != inner_name:
            out.append(f"run-gate [{outer_name}] runs assay lane {outer['assay_lane']!r}, not {inner_name!r}")
    return out


def _load() -> tuple[dict, dict]:
    return (
        tomllib.loads((PROJECT / "assay.toml").read_text(encoding="utf-8")),
        tomllib.loads((PROJECT / "run-gate.toml").read_text(encoding="utf-8")),
    )


def test_the_pairs_exist_and_are_what_the_gate_actually_runs():
    assay_doc, gate_doc = _load()
    assert miswires(assay_doc, gate_doc) == []
    for outer_name, inner_name in PAIRS:
        assert gate_doc["lanes"][outer_name]["kind"] == "assay"
        assert gate_doc["lanes"][outer_name]["assay_lane"] == inner_name
        judge = assay_doc["lanes"][inner_name]["judge"]
        if inner_name == "cmru-r0-r1":
            assert "canary" not in judge
        else:
            assert "budget_per_attempt" in judge["canary"]


# ─── controlled wrong implementation: the pairing assertion must be able to go red ─────────

def test_a_miswired_assay_lane_is_reported():
    assay_doc, gate_doc = _load()
    wrong = copy.deepcopy(gate_doc)
    wrong["lanes"]["assay"]["assay_lane"] = "cmru-r0-r1"
    assert any("not 'cmru'" in v for v in miswires(assay_doc, wrong))


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

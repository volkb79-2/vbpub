"""cmru-specific budget facts of its own Run-Gate/Assay lanes.

What is left here after P-core-12 (KI-194) is what `run-gate lanes --check`
does NOT decide:

* the EXACT pairing of the two outer lanes with the two inner Assay lanes
  (`--check` only verifies that the named inner lane exists, so a miswire to
  another *existing* lane passes it),
* cmru's canary policy: the R0/R1 lane deliberately has no canary while the
  full lane does, and
* the mutation commands do not impose an elapsed-time limit on a control or
  candidate (a command lane's argv is not inspected by Run-Gate).

The generic rules — outer budget >= inner budget + 5m (`--strict`) and the
canary attempt budget <= the lane budget — are enforced by
`run-gate lanes --check --all --strict` in the `estate-lint` lane, so they are
not restated here (the follow-up that closes the remaining gaps above is
tracked by the controller).
"""
from __future__ import annotations

import copy
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


def test_mutation_lanes_have_no_elapsed_child_timeout_and_surface_inconclusive():
    _assay, gate = _load()
    for name in ("mutation", "r2"):
        command = "\n".join(gate["lanes"][name]["argv"])
        assert "--timeout-seconds" not in command
        assert gate["lanes"][name]["exit_map"]["2"] == "ERROR"


def test_r2_has_no_elapsed_budget_and_declares_a_stall_watch():
    _assay, gate = _load()
    lane = gate["lanes"]["r2"]

    assert "budget" not in lane
    assert lane["stall_timeout"] == "15m"

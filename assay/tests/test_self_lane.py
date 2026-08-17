"""assay's own ``assay.toml`` loads under the loader assay ships.

This is O2's negative made mechanical. The P01 pre-flight found that the
package's first draft required the five ``judge`` fields unconditionally while
its own work item required an R0-only lane that omits them — *the deliverable
was a lane its own loader had to reject*. A-048 closed that, and this module is
what stops it coming back.

It also cross-checks the two files that have to agree about assay's own gate:
``assay.toml`` (WHAT) and ``nyxloom-trove/nyxloom.toml`` (WHERE). Reading the
second is deliberate — a lane whose budget silently disagrees with the gate that
enforces it is a fact split across two files with nothing holding it together.
"""

from __future__ import annotations

import io
import tomllib

from conftest import PROJECT_ROOT

from assay.cli import main
from assay.config import load_lane_file

SELF_LANE_FILE = PROJECT_ROOT / "assay.toml"
NYXLOOM_TOML = PROJECT_ROOT / "nyxloom-trove" / "nyxloom.toml"
GATE_ID = "tester-unified"


def test_assays_own_lane_file_loads():
    lane_file = load_lane_file(SELF_LANE_FILE)

    assert lane_file.schema_version == 2
    assert lane_file.project_root == PROJECT_ROOT
    assert list(lane_file.lanes) == [GATE_ID]


def test_assays_own_lane_is_r0_only_with_no_judge_table():
    # A-046 / nyxloom.toml's own note: assay has no changed-line coverage, no
    # canary and no mutation yet, so declaring R1/R2/R3 here would be the
    # lane-table-implies-capability failure committed in assay's own config.
    lane = load_lane_file(SELF_LANE_FILE).lane(GATE_ID)

    assert lane.scope == "S1"
    assert lane.rigor == ("R0",)
    assert lane.enforcement == "gate"
    assert lane.judge is None
    assert lane.argv[0] == "python"
    assert "PATH" in lane.env_passthrough


def test_assays_own_lane_declares_all_eight_required_fields():
    declared = load_lane_file(SELF_LANE_FILE).lane(GATE_ID).as_declared()
    raw = tomllib.loads(SELF_LANE_FILE.read_text(encoding="utf-8"))

    assert declared == raw["lanes"][GATE_ID]


def test_lane_name_matches_the_gate_id_p11_requires():
    gates = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"]

    assert GATE_ID in gates
    assert GATE_ID in load_lane_file(SELF_LANE_FILE).lanes


def test_lane_budget_agrees_with_the_gate_timeout():
    # READ, do not invent (§4.2a): the budget is the gate's own
    # timeout_seconds, so the two files cannot drift apart unnoticed.
    gate = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"][GATE_ID]
    lane = load_lane_file(SELF_LANE_FILE).lane(GATE_ID)

    assert lane.budget_seconds == float(gate["timeout_seconds"])


def test_assay_lanes_lists_assays_own_lane(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)
    out, err = io.StringIO(), io.StringIO()

    code = main(["lanes"], stdout=out, stderr=err)

    assert code == 0
    assert err.getvalue() == ""
    assert GATE_ID in out.getvalue()
    assert "judge=none" in out.getvalue()

"""assay's own ``assay.toml`` loads under the loader assay ships.

This is O2's negative made mechanical. The ordinary release lane remains
R0-only; B105 adds a separate full-source R0-R3 qualification lane. This
module keeps both lane declarations loadable and checks that their distinct
roles do not drift.

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
QUALIFICATION_ID = "self-qualification"
RUN_GATE_TOML = PROJECT_ROOT / "run-gate.toml"


def test_assays_own_lane_file_loads():
    lane_file = load_lane_file(SELF_LANE_FILE)

    assert lane_file.schema_version == 2
    assert lane_file.project_root == PROJECT_ROOT
    assert list(lane_file.lanes) == [GATE_ID, QUALIFICATION_ID]


def test_ordinary_release_lane_stays_r0_only_with_no_judge_table():
    # A-046/A-133 still govern the ordinary release gate. B105's new lane is
    # the explicit place where self-qualification policy is declared.
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
    assert QUALIFICATION_ID in out.getvalue()
    assert "judge=none" in out.getvalue()


def test_self_qualification_is_full_source_r0_through_r3():
    lane_file = load_lane_file(SELF_LANE_FILE)
    lane = lane_file.lane(QUALIFICATION_ID)
    assert lane.scope == "S1"
    assert lane.rigor == ("R0", "R1", "R2", "R3")
    assert lane.enforcement == "gate"
    assert lane.judge is not None
    assert lane.judge.mode == "whole_target"
    assert lane.isolation is not None
    assert lane.isolation.snapshot_history == "full"
    assert lane.judge.require_branch is True
    assert lane.judge.fail_under == 100.0
    assert lane.judge.mutation is not None
    assert lane.judge.mutation.jobs == 1
    assert lane.judge.mutation.max_mutants == 10000
    assert lane.judge.mutation.shard_index is None
    assert lane.judge.mutation.shard_count is None
    assert lane.judge.mutation.budget_per_candidate == "auto"
    assert lane.judge.mutation.liveness == "true"
    assert lane.judge.canary is not None
    assert lane.judge.canary.mechanism == "import-break"
    assert "--override-ini=pythonpath=src" in lane.argv
    assert "--override-ini=pythonpath=" not in lane.argv
    deselected = {
        argument.removeprefix("--deselect=")
        for argument in lane.argv
        if argument.startswith("--deselect=")
    }
    assert deselected == {
        "tests/test_runner_snapshot_selection.py::"
        "test_every_release_since_wi1_landed_carries_wi4s_policy_record",
        "tests/test_runner_snapshot_selection.py::"
        "test_wi1s_own_landing_commit_is_the_state_the_embargo_forbids",
    }

    declared = set(lane.judge.targets or ())
    discovered = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "src" / "assay").rglob("*.py")
        if path.is_file()
    }
    assert declared == discovered
    assert tuple(lane.judge.targets or ()) == tuple(sorted(declared))


def test_self_qualification_run_gate_uses_tester_unified_and_verifies_report():
    run_gate = tomllib.loads(RUN_GATE_TOML.read_text(encoding="utf-8"))
    lane = run_gate["lanes"][QUALIFICATION_ID]
    assert lane["kind"] == "command"
    assert lane["environment"] == "tester-unified"
    assert lane["clean_tree"] is True
    assert "self-qualification-gate.sh" in lane["argv"][1]
    assert lane["resources"]["cpus"] == "3"
    assert lane["artifacts"] == [
        ".assay/verdict-self-qualification.json",
        ".assay/progress-self-qualification.jsonl",
    ]

    script = (PROJECT_ROOT / "tools" / "self-qualification-gate.sh").read_text(
        encoding="utf-8"
    )
    assert "--resume" in script
    assert "--progress .assay/progress-self-qualification.jsonl" in script
    assert '"$assay_bin" verify .assay/verdict-self-qualification.json' in script
    assert "ASSAY_SELF_QUALIFICATION_VERIFIED=1" in script


def test_self_qualification_gate_budget_matches_nyxloom_timeout():
    gate = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"][
        QUALIFICATION_ID
    ]
    run_gate = tomllib.loads(RUN_GATE_TOML.read_text(encoding="utf-8"))["lanes"][
        QUALIFICATION_ID
    ]
    assert gate["timeout_seconds"] == 90 * 24 * 60 * 60
    assert run_gate["budget"] == "2160h"

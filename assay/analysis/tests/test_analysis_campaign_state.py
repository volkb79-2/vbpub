"""B108 state-store oracles (W9): O4, O5, O8, O9, O14a, O22, O23.

The store is read-only evidence: every test builds records with
``campaign_support.state_record`` and reads them through the CLI.
"""

from __future__ import annotations

import json

import pytest

from analysis.tests import campaign_support as support
from analysis.tests.test_analysis_campaign import (
    _complete_fixture,
    _fixture_document,
    _invoke,
    _plan_rows,
    _rewrite_progress,
    _validate,
)
from analysis.tests.test_analysis_campaign_oracles import _analyze, _synthetic
from assay import mutation
from assay_analysis import campaign as campaign_api

J_X, J_X1 = support.JUDGE_A, support.JUDGE_B


# ---- W9R-2 ----------------------------------------------------------------

def test_a_resume_claim_with_no_backing_record_in_the_store_is_unreconciled(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")

    def change(records):
        records[:] = [r for r in records if r["event"] != "candidate"]
        i = next(i for i, r in enumerate(records) if r["event"] == "candidates")
        records[i]["pending_total"] = 0
        records[i]["judge_sha256"] = support.JUDGE_A
        records.insert(i, {"event": "resume", "resumed_total": records[i]["selected_total"],
                           "rejected_total": 0, "rejudged_total": 0})

    _rewrite_progress(progress, change)
    state = tmp_path / "state"
    state.mkdir()
    code, out, err = _invoke(root, head, verdict, progress, command_exit=0, extra=("--state-dir", str(state)))
    doc = json.loads(out)
    assert (code, doc["status"]) == (3, "incomplete")
    assert "state_unreconciled" in doc["complete_blockers"]


# ---- O4 -------------------------------------------------------------------

def _set_judge(progress, judge):
    def change(records):
        next(item for item in records if item["event"] == "candidates")["judge_sha256"] = judge

    _rewrite_progress(progress, change)


def test_o4_a_state_record_bucket_that_disagrees_with_the_verdict_names_the_file(
    tmp_path, monkeypatch
):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    rows = _plan_rows(_fixture_document("r2_pass"))
    _set_judge(progress, J_X)
    state = support.write_state(tmp_path / "state", [
        support.state_record(rows[0], "survived", J_X),
        support.state_record(rows[1], "killed", J_X),
    ])
    code, out, err = _invoke(
        root, head, verdict, progress, command_exit=0, extra=("--state-dir", str(state))
    )
    assert code == 2
    document = json.loads(out)
    _validate(document)
    assert document["errors"] == [{
        "source": "state",
        "message": (
            f"state record {rows[0]['id']}.json: bucket 'survived' disagrees "
            "with verified verdict bucket 'killed'"
        ),
    }]
    assert err == f"assay analyze campaign: {document['errors'][0]['message']}\n"


def test_o4_a_state_record_whose_identity_does_not_match_its_file_name_is_refused(
    tmp_path, monkeypatch
):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(head, rows, judge=J_X))
    altered_path = support.state_record(rows[0], "killed", J_X)
    altered_path["path"] = "pkg/moved.py"
    other_id = support.state_record(rows[1], "killed", J_X)
    other_id["candidate_id"] = rows[2]["id"]
    state = support.write_state(tmp_path / "state", [
        altered_path, support.state_record(rows[2], "killed", J_X),
    ])
    (state / f"{rows[1]['id']}.json").write_text(json.dumps(other_id))
    code, err, document = _analyze(root, head, progress, extra=("--state-dir", str(state)))
    assert code == 2
    assert sorted(item["message"] for item in document["errors"]) == sorted([
        f"state record {rows[0]['id']}.json: identity inputs do not reproduce the file name",
        f"state record {rows[1]['id']}.json: candidate_id differs from the file name",
    ])
    assert {item["source"] for item in document["errors"]} == {"state"}
    assert document["errors_truncated"] is False
    assert err.count("\n") == 1


# ---- O5 -------------------------------------------------------------------

def _f2(tmp_path, monkeypatch, *, extra_current=0, judge_in_event=True, mixed=False):
    """The fixture F2: resumed run at X' whose three executed candidates are recorded at X'."""
    root, head, rows = _synthetic(tmp_path, monkeypatch, 9)
    executed, stale, extra = rows[:3], rows[3:5], rows[5:5 + extra_current]
    buckets = {rows[2]["id"]: "survived"}
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(
        head, rows, resume=True, rejected=2, judge=J_X1 if judge_in_event else None,
        executed=[row["id"] for row in executed], buckets=buckets, end=False, terminal=False,
    ))
    foreign = support.make_rows(1, path="pkg/foreign.py")[0]
    records = [
        support.state_record(row, buckets.get(row["id"], "killed"), J_X1) for row in executed
    ]
    if mixed:
        records[0] = support.state_record(executed[0], "killed", J_X)
    records += [support.state_record(row, "survived", J_X) for row in stale]
    records += [support.state_record(row, "killed", J_X1) for row in extra]
    records.append(support.state_record(foreign, "killed", J_X1))
    state = support.write_state(tmp_path / "state", records)
    result = _analyze(root, head, progress, extra=("--state-dir", str(state)))
    return result, rows


def test_o5_f2_a_resumed_run_at_a_new_judge_counts_only_its_own_events(tmp_path, monkeypatch):
    (code, err, document), _rows = _f2(tmp_path, monkeypatch)
    assert (code, err) == (3, "")
    state = document["state"]
    assert state["judge_sha256_current"] == J_X1
    assert state["judge_sha256_source"] == "candidates-event"
    assert state["stale_judge"]["count"] == 2
    assert state["stale_judge"]["judge_sha256_counts"] == {J_X: 2}
    assert state["foreign"]["count"] == 1
    assert state["unreconciled"]["count"] == 0
    assert state["counted"] == 3
    campaign = document["campaign"]
    assert campaign["selected_total"] == 9
    assert campaign["pending_total"] == 6
    assert campaign["completed_total"] == 3
    assert campaign["outcomes"]["killed"] == 2
    assert campaign["outcomes"]["survived"] == 1
    assert document["timing"]["eta_reason"] != "no_remaining_work"
    assert document["status"] == "incomplete"


def test_o5_f2b_current_judge_records_without_an_event_are_unreconciled(tmp_path, monkeypatch):
    (code, err, document), rows = _f2(tmp_path, monkeypatch, extra_current=2)
    assert (code, err) == (3, "")
    state = document["state"]
    assert state["unreconciled"]["count"] == 2
    assert state["unreconciled"]["sample_ids"] == sorted(row["id"] for row in rows[5:7])
    assert state["counted"] == 3
    assert document["campaign"]["completed_total"] == 3
    assert document["status"] == "incomplete"
    assert "state_unreconciled" in document["complete_blockers"]


def test_o5_f2c_without_a_judge_in_the_event_it_is_derived_from_paired_records(
    tmp_path, monkeypatch
):
    (code, _err, document), _rows = _f2(tmp_path, monkeypatch, judge_in_event=False)
    assert code == 3
    state = document["state"]
    assert state["judge_sha256_current"] == J_X1
    assert state["judge_sha256_source"] == "paired-records"
    assert state["counted"] == 3


def test_o5_f2c_two_paired_judges_leave_the_judge_unknown_and_count_nothing(
    tmp_path, monkeypatch
):
    (code, _err, document), _rows = _f2(
        tmp_path, monkeypatch, judge_in_event=False, mixed=True
    )
    assert code == 3
    state = document["state"]
    assert state["judge_sha256_current"] is None
    assert state["judge_sha256_source"] == "unknown"
    assert state["counted"] == 0
    assert state["judge_sha256_counts"] == {J_X: 3, J_X1: 3}
    assert document["status"] == "incomplete"


# ---- O22 ------------------------------------------------------------------

def _hung_setup(tmp_path, monkeypatch, *, evidence, resumed):
    """Three plan rows; the third is a hung record with no event (no verdict)."""
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(
        head, rows, resume=True, resumed=resumed, rejected=1 - resumed, judge=J_X,
        executed=[row["id"] for row in rows[:2]], end=False, terminal=False,
    ))
    extra = {} if evidence is None else {"liveness_resource_evidence": evidence}
    state = support.write_state(tmp_path / "state", [
        support.state_record(rows[2], "hung", J_X, **extra),
    ])
    return _analyze(root, head, progress, extra=("--state-dir", str(state)))


def test_o22_an_old_hung_record_without_evidence_is_not_counted(tmp_path, monkeypatch):
    code, _err, document = _hung_setup(tmp_path, monkeypatch, evidence=None, resumed=0)
    assert code == 3
    state = document["state"]
    assert state["unverified_hung"]["count"] == 1
    assert state["unreconciled"]["count"] == 0
    assert state["counted"] == 0
    assert document["campaign"]["completed_total"] == 2
    assert document["status"] == "incomplete"
    assert "unverified_hung_records" in document["complete_blockers"]


def test_o22_a_hung_record_with_valid_evidence_is_counted_when_resumed(tmp_path, monkeypatch):
    assert mutation.valid_hung_resource_evidence(support.hung_evidence())
    code, _err, document = _hung_setup(
        tmp_path, monkeypatch, evidence=support.hung_evidence(), resumed=1
    )
    assert code == 3
    state = document["state"]
    assert state["unverified_hung"]["count"] == 0
    assert state["unreconciled"]["count"] == 0
    assert state["counted"] == 1
    assert document["campaign"]["completed_total"] == 3
    assert document["campaign"]["outcomes"]["hung"] == 1
    assert "unverified_hung_records" not in document["complete_blockers"]


def test_o22_input_a_a_verdict_resolved_hung_record_without_evidence_does_not_block(
    tmp_path, monkeypatch
):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    rows = _plan_rows(_fixture_document("r2_pass"))
    state = support.write_state(tmp_path / "state", [
        support.state_record(rows[0], "hung", J_X),
        support.state_record(rows[1], "killed", J_X),
    ])
    code, out, err = _invoke(
        root, head, verdict, progress, command_exit=0, extra=("--state-dir", str(state))
    )
    assert (code, err) == (0, "")
    document = json.loads(out)
    _validate(document)
    assert document["status"] == "complete"
    assert document["complete_blockers"] == []
    assert document["state"]["unverified_hung"]["count"] == 1
    assert document["state"]["judge_sha256_source"] == "paired-records"


def test_o22_input_b_an_event_with_valid_evidence_beats_an_older_invalid_record(
    tmp_path, monkeypatch
):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 2)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(
        head, rows, resume=True, rejected=1, judge=J_X, buckets={rows[0]["id"]: "hung"},
        end=False, terminal=False,
        candidate_extra=lambda candidate_id: (
            {"liveness_resource_evidence": support.hung_evidence()}
            if candidate_id == rows[0]["id"] else {}
        ),
    ))
    old_record = support.state_record(
        rows[0], "hung", J_X, liveness_resource_evidence={"schema_version": 1}
    )
    assert not mutation.valid_hung_resource_evidence(old_record["liveness_resource_evidence"])
    state = support.write_state(tmp_path / "state", [old_record])
    code, _err, document = _analyze(root, head, progress, extra=("--state-dir", str(state)))
    assert code == 3
    assert document["state"]["unverified_hung"]["count"] == 1
    assert "unverified_hung_records" not in document["complete_blockers"]
    [hung] = [row for row in document["candidate_details"]["candidates"] if row["outcome"] == "hung"]
    assert hung["candidate_id"] == rows[0]["id"]
    assert hung["outcome_source"] == "progress"
    assert hung["liveness_evidence_status"] == "valid"
    assert hung["liveness_decision"] == "idle-hang"


# ---- O23 ------------------------------------------------------------------

def test_o23_a_hung_row_without_evidence_is_absent_and_a_killed_row_has_none(
    tmp_path, monkeypatch
):
    root, head, verdict, progress = _complete_fixture(
        tmp_path, monkeypatch, "r2_budget_exceeded_candidate_hung"
    )
    code, out, _err = _invoke(root, head, verdict, progress, command_exit=4)
    assert code == 1
    result = json.loads(out)
    _validate(result)
    rows = {row["outcome"]: row for row in result["candidate_details"]["candidates"]}
    assert result["campaign"]["outcomes"]["hung"] == 1
    assert rows["hung"]["liveness_evidence_status"] == "absent"
    assert rows["hung"]["liveness_decision"] is None
    assert (rows["killed"]["liveness_evidence_status"], rows["killed"]["liveness_decision"]) == (None, None)


def test_o23_a_hung_row_reads_its_event_evidence_before_its_record(tmp_path, monkeypatch):
    name = "r2_budget_exceeded_candidate_hung"
    fixture = _fixture_document(name)
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, name)
    plan = _plan_rows(fixture)
    hung_id = fixture["claims"][-1]["mutation"]["hung"][0]["candidate_id"]
    _set_judge(progress, J_X)

    def add_evidence(records):
        for item in records:
            if item.get("candidate_id") == hung_id:
                item["liveness_resource_evidence"] = support.hung_evidence()

    _rewrite_progress(progress, add_evidence)
    recorded = {
        **support.hung_evidence(),
        "decision": "session-finish-hang",
        "candidate_session_finish_seen": True,
        "session_finish_eligible_s": 0.0,
        "required_idle_eligible_s": 30.0,
    }
    assert mutation.valid_hung_resource_evidence(recorded)
    state = support.write_state(tmp_path / "state", [
        support.state_record(
            next(row for row in plan if row["id"] == hung_id), "hung", J_X,
            liveness_resource_evidence=recorded,
        ),
    ])
    code, out, _err = _invoke(
        root, head, verdict, progress, command_exit=4, extra=("--state-dir", str(state))
    )
    assert code == 1
    result = json.loads(out)
    _validate(result)
    [row] = [row for row in result["candidate_details"]["candidates"] if row["outcome"] == "hung"]
    assert (row["liveness_evidence_status"], row["liveness_decision"]) == ("valid", "idle-hang")


# ---- O14a -----------------------------------------------------------------

def test_o14a_a_witness_cold_event_is_counted_without_a_code_change(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(
        head, rows,
        candidate_extra=lambda candidate_id: {
            "execution_mode": "witness-cold" if candidate_id == rows[0]["id"] else "full"
        },
    ))
    code, _err, document = _analyze(root, head, progress)
    assert code == 3
    assert document["campaign"]["execution_mode_counts"] == {"full": 2, "witness-cold": 1}
    modes = {
        row["candidate_id"]: row["execution_mode"]
        for row in document["candidate_details"]["candidates"]
    }
    assert modes[rows[0]["id"]] == "witness-cold"


# ---- O8 -------------------------------------------------------------------

def _stable_view(code, document):
    return {
        "code": code,
        "status": document["status"],
        "blockers": document["complete_blockers"],
        "outcomes": document["campaign"]["outcomes"],
        "unresolved": document["unresolved"],
        "adverse": {
            bucket: [item["candidate_id"] for item in page["candidates"]]
            for bucket, page in document["adverse"].items()
        },
    }


def test_o8_timing_and_resources_never_change_classification(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(
        tmp_path, monkeypatch, "r2_fail_mutants_survived"
    )
    code, out, _err = _invoke(root, head, verdict, progress, command_exit=1)
    fast = json.loads(out)
    assert fast["adverse"]["survived"]["matching_total"] == 1

    def slow(records):
        for item in records:
            if item["event"] == "candidate":
                item["elapsed_seconds"] = item["elapsed_seconds"] * 500.0
                item["emitted_at"] = item["emitted_at"].replace("T12:", "T15:")
                item["cpu_seconds"] = 9000.5
                item["peak_rss_bytes"] = 123456789
                item["phase_seconds"] = {
                    "materialize": 10.0, "command": 20.0, "integrity": 30.0, "teardown": 40.0,
                }
                item["startup_seconds"] = {"to_session_start": 5.0, "to_first_test": 6.0}
            if item["event"] == "verdict_written":
                item["elapsed_s"] = 99999.0

    _rewrite_progress(progress, slow)
    slow_code, out, _err = _invoke(root, head, verdict, progress, command_exit=1)
    slower = json.loads(out)
    _validate(slower)
    assert slower["timing"] != fast["timing"]
    assert slower["candidate_details"] != fast["candidate_details"]
    assert _stable_view(slow_code, slower) == _stable_view(code, fast)


# ---- O9 -------------------------------------------------------------------

def _rows(path, operator, count):
    return [{"path": path, "operator": operator, "id": f"{path}-{index}"} for index in range(count)]


def test_o9_the_size_class_is_taken_from_plan_rows_at_the_boundaries():
    files = {"p10": 10, "p11": 11, "p100": 100, "p101": 101}
    rows = [row for path, count in files.items() for row in _rows(path, "op", count)]
    seconds = {"p10": 1.0, "p11": 2.0, "p100": 2.0, "p101": 3.0}
    samples = [
        campaign_api.CostSample(path, "op", "killed", seconds[path])
        for path in files for _ in range(20)
    ]
    result = campaign_api.project(samples, rows, 4, {})
    basis = result["bases"]["killed"]
    assert basis["fallback_counts"] == {"operator_size_class": 222, "operator": 0, "all": 0}
    assert basis["serial_seconds_p50"] == 10 * 1.0 + 111 * 2.0 + 101 * 3.0
    assert result["size_classes"] == {"small": "≤10", "medium": "11–100", "large": ">100"}
    assert result["wall_scope"]
    assert result["jobs"] == 4


def test_o9_fallbacks_step_from_the_stratum_to_the_operator_to_everything():
    rows = (
        _rows("a.py", "python:compare-swap", 3)
        + _rows("b.py", "python:compare-swap", 12)
        + _rows("c.py", "python:boolop-swap", 101)
    )
    samples = (
        [campaign_api.CostSample("a.py", "python:compare-swap", "killed", 1.0)] * 25
        + [campaign_api.CostSample("c.py", "python:boolop-swap", "killed", 5.0)] * 3
    )
    basis = campaign_api.project(samples, rows, 2, {})["bases"]["killed"]
    assert basis["samples"] == 28
    assert basis["fallback_counts"] == {"operator_size_class": 3, "operator": 12, "all": 101}
    assert basis["reason"] is None


def test_o9_a_basis_under_twenty_samples_is_an_object_with_its_reason():
    rows = _rows("a.py", "op", 4)
    result = campaign_api.project([campaign_api.CostSample("a.py", "op", "killed", 1.0)] * 19, rows, 2, {})
    assert result["bases"]["killed"] == {
        "samples": 19, "candidates_projected": 4, "fallback_counts": None,
        "serial_seconds_p50": None, "serial_seconds_p90": None, "reason": "insufficient_sample",
    }
    assert result["wall_seconds_p50"] is None and result["wall_seconds_p90"] is None


@pytest.mark.parametrize("extra", [
    ("--project",), ("--project-jobs", "2"), ("--project", "--project-jobs", "0"),
    ("--project", "--project-jobs", "65"), ("--project", "--project-jobs", "x"),
])
def test_o9_project_needs_a_jobs_count_in_range(tmp_path, monkeypatch, extra):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    with pytest.raises(SystemExit) as exit_info:
        _invoke(root, head, verdict, progress, command_exit=0, extra=extra)
    assert exit_info.value.code == 2


def test_o9_the_library_entry_refuses_a_jobs_count_out_of_range(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    with pytest.raises(ValueError, match="project jobs must be between 1 and 64"):
        campaign_api.campaign(
            worktree=root, lane_file_path=root / "assay.toml", lane_name="package",
            expected_commit=head, verdict_path=verdict, progress_path=progress,
            command_exit=0, project_jobs=0,
        )

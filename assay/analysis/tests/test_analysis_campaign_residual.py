"""B108 step 3a (W9): the last behaviours and branches of ``campaign.py`` the tables do not reach.

Positive rows: what the command reports (and pins) for evidence that is valid but unusual,
plus the library-only entry points the CLI parser dominates.
"""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta

import pytest

from analysis.tests import campaign_support as support
from analysis.tests.test_analysis_campaign import (
    _complete_fixture,
    _fixture_document,
    _invoke,
    _plan_rows,
    _repository,
    _validate,
    _write_progress,
)
from analysis.tests.test_analysis_campaign_oracles import _analyze, _synthetic
from assay.mutation import select_mutation_shard
from assay_analysis import campaign as campaign_api

J = support.JUDGE_A


def _commit_lane(root, text):
    (root / "assay.toml").write_text(text)
    for arguments in (("add", "-A"), ("commit", "-q", "-m", "lane edit")):
        subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _ingested_lane(root):
    text = (root / "assay.toml").read_text()
    head = text.split("[lanes.package.judge.mutation]")[0]
    head = head.replace('budget = "unbounded"', 'budget = "1m"')
    return head + (
        '[lanes.package.judge.mutation]\nformat = "mutation-report-json"\n'
        'artifact = "mut.json"\nfail_under = 100.0\n'
    )


def test_a_non_native_mutation_lane_is_reported_not_supported(tmp_path, monkeypatch):
    root, head, _verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    head = _commit_lane(root, _ingested_lane(root))
    support.write_records(progress, [{
        "event": "run", "commit": head, "lane": "package", "rigor": ["R0", "R2"],
        "started": support.RUN_START.isoformat(),
    }])
    code, out, err = _invoke(root, head, None, progress)
    document = json.loads(out)
    _validate(document)
    assert (code, err) == (3, "")
    assert document["plan"]["status"] == "not_supported"
    assert document["plan"]["planned_total"] is None
    assert document["plan"]["inventory_sha256"] is None
    assert document["status"] == "incomplete"


def test_request_base_reaches_the_planner_for_a_lane_that_delegates_its_base(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    lane = (root / "assay.toml").read_text().replace('base = "main"\n', 'base_source = "request"\n')
    head = _commit_lane(root, lane)
    received = []

    def spy(_lane_file, _lane_name, request_base):
        received.append(request_base)
        return {"status": "ok", "candidates": rows, "_resolved_base": None}

    monkeypatch.setattr(campaign_api, "_lane_plan", spy)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(head, rows, terminal=False))
    code, err, document = _analyze(root, head, progress, extra=("--request-base", "feature-base"))
    assert (code, err) == (3, "")
    assert received == ["feature-base"]
    assert document["plan"]["planned_total"] == 3


# ---- library-only entry points the CLI parser dominates ---------------------------


@pytest.mark.parametrize("buckets", [[], ["nonsense"], ["killed", "nonsense"]])
def test_the_library_entry_refuses_an_outcome_filter_outside_the_six_buckets(tmp_path, monkeypatch, buckets):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    with pytest.raises(ValueError, match="outcome filters must use"):
        campaign_api.campaign(
            worktree=root, lane_file_path=root / "assay.toml", lane_name="package",
            expected_commit=head, verdict_path=verdict, progress_path=progress,
            command_exit=0, buckets=buckets,
        )


# ---- the positive --log read --------------------------------------------------------


def test_a_readable_gate_log_is_recorded_as_evidence_and_never_parsed(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    log = tmp_path / "gate.log"
    log.write_bytes(b"\xff not UTF-8, and not parsed\n")
    code, out, err = _invoke(root, head, verdict, progress, command_exit=0, extra=("--log", str(log)))
    document = json.loads(out)
    _validate(document)
    assert (code, err) == (0, "")
    artifact = document["evidence"]["log"]
    assert artifact["path"] == str(log)
    assert artifact["bytes"] == log.stat().st_size
    assert document["status"] == "complete"
    without = json.loads(_invoke(root, head, verdict, progress, command_exit=0)[1])
    assert "log" not in without["evidence"]


# ---- verdict mode: a stream with no terminal event -----------------------------------


def test_a_verdict_campaign_whose_stream_has_no_terminal_event_is_incomplete_for_that_reason_alone(
    tmp_path, monkeypatch
):
    document = _fixture_document("r2_pass")
    root, head, verdict = _repository(tmp_path, document)
    rows = _plan_rows(document)
    from analysis.tests.test_analysis_campaign import _install_plan

    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    _write_progress(progress, head=head, document=document, plan_rows=rows, include_terminal=False)
    code, out, err = _invoke(root, head, verdict, progress, command_exit=0)
    result = json.loads(out)
    _validate(result)
    assert (code, err) == (3, "")
    assert result["status"] == "incomplete"
    assert result["complete_blockers"] == ["terminal_disagrees"]
    assert result["runs"][-1]["terminal"] is None


# ---- synthetic streams (no verdict) -------------------------------------------------


def _at(seconds: int) -> str:
    return (support.RUN_START + timedelta(seconds=seconds)).isoformat()


def _command_finished(phase: str, start: int, end: int) -> dict:
    return {
        "event": "command_finished", "phase": phase, "outcome": "PASS", "reason_code": None,
        "returncode": 0, "started": _at(start), "ended": _at(end),
    }


def _stream(tmp_path, monkeypatch, *, count=3, extra_events=(), **run_options):
    root, head, rows = _synthetic(tmp_path, monkeypatch, count)
    progress = tmp_path / "progress.jsonl"
    records = support.run_records(head, rows, terminal=False, **run_options)
    records[1:1] = list(extra_events)
    support.write_records(progress, records)
    return root, head, rows, progress


def _fixed(document):
    projection = document["projection"]
    return projection["fixed_components"], projection["fixed_seconds_measured"], projection["fixed_components_missing"]


def test_projection_fixed_overhead_reads_the_baseline_spans_and_the_first_candidate(tmp_path, monkeypatch):
    root, head, _rows, progress = _stream(
        tmp_path, monkeypatch,
        extra_events=[_command_finished("baseline", 1, 4), _command_finished("r2-baseline", 4, 6)],
    )
    code, err, document = _analyze(root, head, progress, extra=("--project", "--project-jobs", "2"))
    assert (code, err) == (3, "")
    components, measured, missing = _fixed(document)
    # The first candidate is emitted 10 s after the run started: 10 - 3 - 2 is "other".
    assert components == {"coverage_baseline": 3.0, "r2_baseline": 2.0, "other": 5.0}
    assert measured == 10.0
    assert missing == ["r3"]


def test_projection_falls_back_to_the_direct_span_and_the_plan_events_r2_baseline(tmp_path, monkeypatch):
    plan = {"event": "plan", "r2_baseline_s": 4.5}
    root, head, _rows, progress = _stream(
        tmp_path, monkeypatch, extra_events=[_command_finished("direct", 2, 4), plan],
    )
    _code, _err, document = _analyze(root, head, progress, extra=("--project", "--project-jobs", "2"))
    components, measured, missing = _fixed(document)
    assert components == {"coverage_baseline": 2.0, "r2_baseline": 4.5, "other": 3.5}
    assert measured == 10.0
    assert missing == ["r3"]


def test_projection_names_every_fixed_component_it_could_not_measure(tmp_path, monkeypatch):
    root, head, _rows, progress = _stream(tmp_path, monkeypatch, executed=[], end=False)
    _code, _err, document = _analyze(root, head, progress, extra=("--project", "--project-jobs", "2"))
    components, measured, missing = _fixed(document)
    assert components == {"coverage_baseline": None, "r2_baseline": None, "other": None}
    assert measured == 0.0
    assert missing == ["coverage_baseline", "r2_baseline", "other", "r3"]


def test_the_text_format_prints_the_projection_line_only_with_project(tmp_path, monkeypatch):
    root, head, _rows, progress = _stream(tmp_path, monkeypatch)
    code, text, err = _invoke(
        root, head, None, progress, output_format="text", extra=("--project", "--project-jobs", "2")
    )
    assert (code, err) == (3, "")
    line = next(line for line in text.splitlines() if line.startswith("  projection (diagnostic): "))
    assert json.loads(line.split(": ", 1)[1])["jobs"] == 2
    _code, plain, _err = _invoke(root, head, None, progress, output_format="text")
    assert "projection (diagnostic)" not in plain


def _candidate_rows(document):
    return {row["candidate_id"]: row for row in document["candidate_details"]["candidates"]}


def test_a_row_carries_the_phase_and_startup_objects_with_exactly_their_keys(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    first = rows[0]["id"]

    def shapes(candidate_id):
        if candidate_id != first:
            return {}
        return {
            "phase_seconds": {"materialize": 10, "command": None, "integrity": 30.5, "teardown": 40},
            "startup_seconds": {"to_session_start": 5, "to_first_test": None},
        }

    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(head, rows, candidate_extra=shapes))
    _code, _err, document = _analyze(root, head, progress)
    by_id = _candidate_rows(document)
    assert by_id[first]["phase_seconds"] == {
        "materialize": 10.0, "command": None, "integrity": 30.5, "teardown": 40.0,
    }
    assert set(by_id[first]["phase_seconds"]) == {"materialize", "command", "integrity", "teardown"}
    assert by_id[first]["startup_seconds"] == {"to_session_start": 5.0, "to_first_test": None}
    for other in rows[1:]:
        assert by_id[other["id"]]["phase_seconds"] is None
        assert by_id[other["id"]]["startup_seconds"] is None


def test_started_count_and_evidence_command_are_typed_reads_of_the_recorded_evidence(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(head, rows, judge=J))
    state = support.write_state(tmp_path / "state", [
        support.state_record(rows[0], "killed", J, evidence={"started_count": 3, "command": "pytest -q x"}),
        support.state_record(rows[1], "killed", J, evidence={"started_count": True, "command": 5}),
        support.state_record(rows[2], "killed", J),
    ])
    code, err, document = _analyze(root, head, progress, extra=("--state-dir", str(state)))
    assert (code, err) == (3, "")
    by_id = _candidate_rows(document)
    assert (by_id[rows[0]["id"]]["started_count"], by_id[rows[0]["id"]]["evidence_command"]) == (3, "pytest -q x")
    assert (by_id[rows[1]["id"]]["started_count"], by_id[rows[1]["id"]]["evidence_command"]) == (None, None)
    assert (by_id[rows[2]["id"]]["started_count"], by_id[rows[2]["id"]]["evidence_command"]) == (None, None)


def test_a_state_bucket_that_disagrees_with_the_progress_event_is_reclassified_not_hidden(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    moved = rows[0]["id"]
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(
        head, rows, judge=J, buckets={moved: "survived"},
    ))
    state = support.write_state(tmp_path / "state", [
        support.state_record(rows[0], "killed", J),
        support.state_record(rows[1], "killed", J),
        support.state_record(rows[2], "killed", J),
    ])
    code, err, document = _analyze(root, head, progress, extra=("--state-dir", str(state)))
    assert (code, err) == (3, "")
    run_id = document["runs"][0]["run_id"]
    assert document["reclassified"] == [{
        "candidate_id": moved,
        "source": "state_vs_progress",
        "runs": [{"run_id": run_id, "bucket": "survived"}],
        "state_bucket": "killed",
    }]
    # The event is the newer fact: the candidate counts once, as the event's bucket.
    assert document["campaign"]["completed_total"] == 3
    assert document["campaign"]["outcomes"]["survived"] == 1
    assert document["campaign"]["outcomes"]["killed"] == 2
    assert _candidate_rows(document)[moved]["outcome_source"] == "progress"


def test_an_earlier_runs_candidate_outside_the_latest_selection_is_not_counted(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 4)
    ids = [row["id"] for row in rows]
    assigned = [ids[position] for position in select_mutation_shard(ids, index=0, count=2)]
    outside = [candidate_id for candidate_id in ids if candidate_id not in assigned]
    first = support.run_records(head, rows, terminal=False, end=False)
    second = support.run_records(
        head, rows, started=support.RUN_START.replace(hour=13), terminal=False,
        selected=[row for row in rows if row["id"] in assigned],
    )
    second.insert(1, {
        "event": "shard", "shard_index": 0, "shard_count": 2, "selected_total": len(assigned),
    })
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, first + second)
    code, err, document = _analyze(root, head, progress)
    assert (code, err) == (3, "")
    assert document["plan"]["shard"] == "0/2"
    assert document["campaign"]["selected_total"] == len(assigned)
    assert document["campaign"]["completed_total"] == len(assigned)
    reported = set(_candidate_rows(document))
    assert reported == set(assigned)
    assert not reported & set(outside)

"""B108 oracles O17 and O18 (W9): verdict-bound closeout of expiry, shards and buckets.

The verdicts here are shaped from the ``r2_pass`` fixture with the R2 claim replaced
by exactly the candidates an oracle names, so every bucket and every id is known.
"""

from __future__ import annotations

import copy
import json

from analysis.tests import campaign_support as support
from analysis.tests.test_analysis_campaign import (
    _fixture_document,
    _install_plan,
    _invoke,
    _plan_rows,
    _repository,
    _validate,
    _write_progress,
)
from assay.candidate_identity import candidate_id_from_fields

BUCKETS = ("killed", "survived", "crashed", "budget_exceeded", "equivalent", "hung")


def _entry(row):
    return {
        **{key: row[key] for key in (
            "path", "lineno", "start_byte", "end_byte", "operator", "description",
            "source_sha256", "mutated_file_sha256",
        )},
        "replacement_sha256": "c" * 64,
        "candidate_id": row["id"],
        "execution": {"mode": "full"},
    }


def _verdict_document(by_bucket, *, outcome, exit_code, reason, policy=None):
    """``r2_pass`` with its R2 claim rebuilt from ``{bucket: [plan rows]}``."""
    document = _fixture_document("r2_pass")
    document["outcome"] = outcome
    document["exit_code"] = exit_code
    document["judgment"]["r2"].update({"max_mutants": 500, **(policy or {})})
    claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    everything = [row for bucket in BUCKETS for row in by_bucket.get(bucket, [])]
    claim["status"] = outcome
    if reason is not None:
        document["reason_code"] = reason
        claim["reason_code"] = reason
    claim["mutation"] = {
        "candidate_count": len(everything),
        "total": len(everything),
        **{bucket: [_entry(row) for row in by_bucket.get(bucket, [])] for bucket in BUCKETS},
        "candidate_ids": [row["id"] for row in everything],
    }
    return document, everything


def _setup(tmp_path, monkeypatch, by_bucket, **shape):
    tmp_path.mkdir(parents=True, exist_ok=True)
    document, everything = _verdict_document(by_bucket, **shape)
    root, head, verdict = _repository(tmp_path, document)
    _install_plan(monkeypatch, document, everything)
    return root, head, verdict, everything


def _bucket_of(by_bucket):
    return {row["id"]: bucket for bucket, rows in by_bucket.items() for row in rows}


def _line(row):
    return (row["id"], row["path"], row["operator"])


# ---- O17 ------------------------------------------------------------------

def test_o17_f1_a_lane_timeout_with_two_hundred_unstarted_candidates_is_incomplete(
    tmp_path, monkeypatch
):
    rows = support.make_rows(201)
    by_bucket = {"killed": rows[:1], "budget_exceeded": rows[1:]}
    root, head, verdict, everything = _setup(
        tmp_path, monkeypatch, by_bucket,
        outcome="BUDGET_EXCEEDED", exit_code=4, reason="LANE_TIMEOUT",
    )
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(
        head, everything, executed=[rows[0]["id"]], buckets=_bucket_of(by_bucket),
        terminal={"outcome": "BUDGET_EXCEEDED", "exit_code": 4, "reason_code": "LANE_TIMEOUT"},
    ))
    state = support.write_state(
        tmp_path / "state", [support.state_record(rows[0], "killed")]
    )
    code, out, err = _invoke(
        root, head, verdict, progress, command_exit=4, extra=("--state-dir", str(state))
    )
    assert (code, err) == (3, "")
    document = json.loads(out)
    _validate(document)
    assert document["status"] == "incomplete"
    assert document["unresolved"]["matching_total"] == 200
    assert "lane_timeout_or_unstarted" in document["complete_blockers"]
    assert document["campaign"]["completed_total"] == 1
    assert document["campaign"]["outcomes"]["budget_exceeded"] == 200


def test_o17_f4_a_sharded_pass_verdict_is_never_a_complete_campaign(tmp_path, monkeypatch):
    rows = support.make_rows(8)
    from assay.mutation import select_mutation_shard

    ids = [row["id"] for row in rows]
    positions = select_mutation_shard(ids, index=0, count=4)
    shard = [rows[position] for position in positions]
    assert 0 < len(shard) < len(rows)
    document, _shard_rows = _verdict_document(
        {"killed": shard}, outcome="PASS", exit_code=0, reason=None,
        policy={"shard_index": 0, "shard_count": 4},
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    root, head, verdict = _repository(tmp_path, document)
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    records = support.run_records(head, rows, selected=shard)
    records.insert(1, {
        "event": "shard", "shard_index": 0, "shard_count": 4, "selected_total": len(shard),
    })
    support.write_records(progress, records)
    code, out, err = _invoke(root, head, verdict, progress, command_exit=0)
    assert (code, err) == (3, "")
    result = json.loads(out)
    _validate(result)
    assert result["status"] == "incomplete"
    assert "inventory_not_exhausted" in result["complete_blockers"]
    assert result["plan"]["shard"] == "0/4"


# ---- O18 ------------------------------------------------------------------

def _six_bucket_fixture(tmp_path, monkeypatch):
    killed = support.make_rows(1, path="pkg/killed.py", operator="python:compare-swap")
    survived = support.make_rows(2, path="pkg/survived.py", operator="python:boolop-swap")
    crashed = support.make_rows(1, path="pkg/crashed.py", operator="python:compare-swap")
    budget = support.make_rows(1, path="pkg/budget.py", operator="python:compare-swap")
    equivalent = support.make_rows(1, path="pkg/equivalent.py", operator="python:compare-swap")
    hung = support.make_rows(1, path="pkg/hung.py", operator="python:boolop-swap")
    by_bucket = {
        "killed": killed, "survived": survived, "crashed": crashed,
        "budget_exceeded": budget, "equivalent": equivalent, "hung": hung,
    }
    root, head, verdict, everything = _setup(
        tmp_path, monkeypatch, by_bucket,
        outcome="ERROR", exit_code=2, reason="EXEC_FAILED",
        policy={"equivalence_artifact": ".assay/schema-dump.sql"},
    )
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(
        head, everything, buckets=_bucket_of(by_bucket),
        terminal={"outcome": "ERROR", "exit_code": 2, "reason_code": "EXEC_FAILED"},
    ))
    return root, head, verdict, progress, by_bucket


def test_o18_all_six_buckets_are_counted_and_the_adverse_candidates_named(tmp_path, monkeypatch):
    root, head, verdict, progress, by_bucket = _six_bucket_fixture(tmp_path, monkeypatch)
    code, out, err = _invoke(root, head, verdict, progress, command_exit=2)
    assert (code, err) == (1, "")
    document = json.loads(out)
    _validate(document)
    assert document["campaign"]["outcomes"] == {
        "killed": 1, "survived": 2, "equivalent": 1, "crashed": 1, "hung": 1, "budget_exceeded": 1,
    }
    assert document["campaign"]["completed_total"] == 7
    for bucket in ("survived", "crashed", "hung", "budget_exceeded"):
        page = document["adverse"][bucket]
        assert page["matching_total"] == len(by_bucket[bucket])
        assert page["next_offset"] is None
        assert [(row["candidate_id"], row["path"], row["operator"]) for row in page["candidates"]] == [
            _line(row) for row in sorted(by_bucket[bucket], key=lambda row: (row["path"], row["lineno"], row["id"]))
        ]
    assert {row["outcome_source"] for row in document["candidate_details"]["candidates"]} == {"verdict"}
    assert document["status"] == "complete"


def test_o18_a_stale_commit_verdict_is_an_evidence_error(tmp_path, monkeypatch):
    root, head, verdict, progress, _by_bucket = _six_bucket_fixture(tmp_path, monkeypatch)
    stale = json.loads(verdict.read_text())
    stale["commit"] = "1" * 40
    verdict.write_text(json.dumps(stale))
    code, out, _err = _invoke(root, head, verdict, progress, command_exit=2)
    document = json.loads(out)
    _validate(document)
    assert code == 2
    assert document["status"] == "evidence_error"
    assert [error["source"] for error in document["errors"]] == ["verdict"]
    assert document["errors"][0]["message"] == f"verdict commit {'1' * 40} differs from {head}"


def test_o18_a_verdict_for_another_lane_is_an_evidence_error(tmp_path, monkeypatch):
    root, head, verdict, progress, _by_bucket = _six_bucket_fixture(tmp_path, monkeypatch)
    foreign = json.loads(verdict.read_text())
    foreign["lane"] = "other"
    verdict.write_text(json.dumps(foreign))
    code, out, _err = _invoke(root, head, verdict, progress, command_exit=2)
    document = json.loads(out)
    _validate(document)
    assert code == 2
    assert document["status"] == "evidence_error"
    assert document["errors"][0]["message"] == "verdict lane 'other' differs from requested 'package'"


# ---- W9R-3 ----------------------------------------------------------------

def test_a_never_started_leftover_under_a_non_timeout_reason_is_unresolved(tmp_path, monkeypatch):
    document = _fixture_document("r2_error_exec_failed_mutant_crashed")
    mutation = next(c for c in document["claims"] if c["rigor"] == "R2")["mutation"]
    extra = copy.deepcopy(mutation["killed"][0])
    extra["start_byte"] += 1000
    extra["end_byte"] += 1000
    extra["lineno"] += 50
    extra["mutated_file_sha256"] = "ab" * 32
    extra.pop("kill_signal", None)
    extra["candidate_id"] = candidate_id_from_fields(**{k: extra[k] for k in (
        "path", "source_sha256", "start_byte", "end_byte", "mutated_file_sha256", "operator")})
    mutation["budget_exceeded"] = [extra]
    mutation["candidate_count"] = mutation["total"] = 3
    mutation["candidate_ids"] = mutation["candidate_ids"] + [extra["candidate_id"]]
    root, head, verdict = _repository(tmp_path, document)
    rows = _plan_rows(document)
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    started = {o["candidate_id"] for b in ("killed", "crashed") for o in mutation[b]}
    _write_progress(progress, head=head, document=document, plan_rows=rows, reported_ids=started)
    code, out, err = _invoke(root, head, verdict, progress, command_exit=document["exit_code"])
    result = json.loads(out)
    assert document["reason_code"] == "EXEC_FAILED"
    assert (code, result["status"], result["complete_blockers"]) == (3, "incomplete", ["lane_timeout_or_unstarted"])
    assert result["unresolved"] == {"matching_total": 1, "candidates": [extra["candidate_id"]]}

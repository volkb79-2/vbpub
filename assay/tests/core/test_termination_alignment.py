"""Enforce agreement between the termination table and native R2 consumers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from assay.errors import Outcome
from assay.mutation import _bucket_for_run, _MutantRun, _terminal_result_matches_bucket
from assay.termination import Termination
from assay.verdict import (
    MUTATION_BUCKETS,
    MutantOutcome,
    Mutation,
    MutationExecution,
    MutationWitnessReceipt,
    candidate_id_from_fields,
)
from assay.verify import (
    _check_b145_resource_limit_evidence,
    _check_b162_process_tree_evidence,
)
from termination_table import ALL_CASES

_WITNESS = {
    "node_id": "tests/test_subject.py::test_subject",
    "when": "call",
    "outcome": "failed",
    "session_exit_status": 1,
    "process_exit_status": 1,
}


def _run(case) -> _MutantRun:
    tree = case.inputs["process_tree"]
    witness = _WITNESS if case.inputs["decided_by_report"] else None
    execution = MutationExecution(
        mode="full",
        witness=MutationWitnessReceipt(**witness) if witness is not None else None,
    )
    result = SimpleNamespace(
        outcome=case.inputs["outcome"],
        reason_code=case.inputs["reason_code"],
        returncode=case.inputs["returncode"],
        process_tree_evidence=tree,
    )
    return _MutantRun(
        result=result,
        execution=execution,
        resource_limit_evidence=case.inputs["limits"],
        process_tree_evidence=tree,
    )


def _payload(case, bucket: str) -> dict:
    witness = (
        _WITNESS
        if bucket == "killed" and case.inputs["decided_by_report"]
        else None
    )
    execution: dict[str, object] = {"mode": "full"}
    if witness is not None:
        execution["witness"] = witness
    return {
        "terminal_result": {
            "outcome": case.inputs["outcome"].value,
            "reason_code": (
                None
                if case.inputs["reason_code"] is None
                else case.inputs["reason_code"].value
            ),
            "returncode": case.inputs["returncode"],
        },
        "outcome_bucket": bucket,
        "execution": execution,
    }


def _model_outcome(run: _MutantRun, bucket: str | None = None) -> MutantOutcome:
    path = "src/example.py"
    source_sha256 = "a" * 64
    mutated_file_sha256 = "b" * 64
    operator = "python:compare-swap"
    candidate_id = candidate_id_from_fields(
        path=path,
        source_sha256=source_sha256,
        start_byte=0,
        end_byte=1,
        mutated_file_sha256=mutated_file_sha256,
        operator=operator,
    )
    return MutantOutcome(
        path=path,
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="c" * 64,
        operator=operator,
        description="x < y",
        candidate_id=candidate_id,
        source_sha256=source_sha256,
        mutated_file_sha256=mutated_file_sha256,
        execution=(
            run.execution
            if bucket in (None, "killed")
            else MutationExecution(mode=run.execution.mode)
        ),
        resource_limit_evidence=run.resource_limit_evidence,
        process_tree_evidence=run.process_tree_evidence,
    )


@pytest.mark.parametrize(
    "case",
    [case for case in ALL_CASES if case.inputs["process_tree"] is not None],
    ids=lambda case: case.name,
)
def test_producer_revalidation_verifier_model_agree(case):
    run = _run(case)
    bucket = _bucket_for_run(
        run,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
    )
    assert bucket == case.r2_bucket

    payload = _payload(case, bucket)
    state_match = _terminal_result_matches_bucket(
        payload,
        resource_limit_evidence=run.resource_limit_evidence,
        process_tree_evidence=run.process_tree_evidence,
    )
    # NO_MEASUREMENT is never a reusable candidate record; the producer and
    # verdict projections still classify the leaked execution as crashed.
    if case.inputs["outcome"] is Outcome.NO_MEASUREMENT:
        assert state_match is False
    else:
        assert state_match is True

    model_outcome = _model_outcome(run, bucket)
    Mutation(
        candidate_count=1,
        total=1,
        **{
            name: (model_outcome,) if name == bucket else ()
            for name in MUTATION_BUCKETS
        },
    )

    entry = model_outcome.to_dict()
    failures: list[str] = []
    _check_b145_resource_limit_evidence(bucket, entry, failures)
    _check_b162_process_tree_evidence(bucket, entry, failures)
    assert failures == []


def test_a_positive_limit_event_is_refused_as_killed_by_all_layers():
    case = next(case for case in ALL_CASES if case.name == "limit-beats-report")
    run = _run(case)
    wrong_payload = _payload(case, "killed")
    wrong_payload["outcome_bucket"] = "killed"
    assert not _terminal_result_matches_bucket(
        wrong_payload,
        resource_limit_evidence=run.resource_limit_evidence,
        process_tree_evidence=run.process_tree_evidence,
    )

    item = _model_outcome(run)
    with pytest.raises(ValueError, match="cannot be classified as killed"):
        Mutation(
            candidate_count=1,
            total=1,
            killed=(item,),
        )

    entry = item.to_dict()
    failures: list[str] = []
    _check_b145_resource_limit_evidence("killed", entry, failures)
    assert any("positive failed-fork or OOM counter delta" in item for item in failures)


def test_self_signaled_r2_is_crashed_r0_is_fail():
    case = next(case for case in ALL_CASES if case.name == "sigsegv-is-self-signaled")
    run = _run(case)
    assert _bucket_for_run(
        run,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
    ) == "crashed"
    assert case.r0_outcome is Outcome.FAIL
    assert case.termination is Termination.SELF_SIGNALED

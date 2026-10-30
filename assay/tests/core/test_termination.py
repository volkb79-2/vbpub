"""Table-driven termination classification and R2 bucket mapping."""

from __future__ import annotations

import signal

import pytest

from assay.errors import Outcome, ReasonCode
from assay.termination import (
    EXTERNAL_SIGNALS,
    JUDGEABLE,
    Termination,
    classify,
    detail,
    r2_bucket,
)
from termination_table import ALL_CASES, LIMITS_HIT, TERMINATION_CASES


def test_shared_table_has_one_base_row_for_each_termination():
    rows = [case.termination for case in TERMINATION_CASES]
    assert len(rows) == len(Termination)
    assert set(rows) == set(Termination)


@pytest.mark.parametrize("case", TERMINATION_CASES, ids=lambda case: case.name)
def test_each_termination_has_a_shared_decision_row(case):
    classified = classify(**case.inputs)

    assert classified is case.termination
    assert r2_bucket(classified) == case.r2_bucket
    assert (classified in JUDGEABLE) is (
        classified in {Termination.PASSED, Termination.FAILED}
    )


def test_shared_table_r0_and_r3_columns_match_the_contract():
    expected_r0 = {
        Termination.PASSED: (Outcome.PASS, None),
        Termination.FAILED: (Outcome.FAIL, ReasonCode.COMMAND_FAILED),
        Termination.TIMED_OUT: (Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT),
        Termination.HUNG: (Outcome.BUDGET_EXCEEDED, ReasonCode.CANDIDATE_HUNG),
        Termination.NOT_RUN: (Outcome.ERROR, ReasonCode.EXEC_FAILED),
        Termination.KILLED_EXTERNALLY: (Outcome.ERROR, ReasonCode.EXEC_FAILED),
        Termination.LEAKED: (Outcome.ERROR, ReasonCode.EXEC_FAILED),
        Termination.LIMIT_EVENT: (Outcome.ERROR, ReasonCode.EXEC_FAILED),
        Termination.UNOBSERVED: (Outcome.ERROR, ReasonCode.EXEC_FAILED),
        Termination.INCONCLUSIVE: (Outcome.ERROR, ReasonCode.EXEC_FAILED),
    }
    expected_r0_by_case_name = {
        "no-measurement-without-leak-is-not-run": (
            Outcome.NO_MEASUREMENT,
            ReasonCode.TARGET_NOT_MEASURED,
        ),
    }
    expected_r3 = {
        Termination.PASSED: (Outcome.FAIL, ReasonCode.CANARY_SURVIVED),
        Termination.FAILED: (Outcome.PASS, None),
        Termination.TIMED_OUT: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
        Termination.HUNG: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
        Termination.NOT_RUN: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
        Termination.KILLED_EXTERNALLY: (
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        Termination.LEAKED: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
        Termination.LIMIT_EVENT: (
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        Termination.UNOBSERVED: (
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        Termination.INCONCLUSIVE: (
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
    }

    for case in ALL_CASES:
        expected_r0_for_case = expected_r0_by_case_name.get(
            case.name, expected_r0[case.termination]
        )
        assert (case.r0_outcome, case.r0_reason_code) == expected_r0_for_case
        assert (case.r3_attempt_outcome, case.r3_attempt_reason_code) == expected_r3[
            case.termination
        ]


def test_inconclusive_outcome_is_not_judgeable():
    classified = classify(
        outcome=Outcome.INCONCLUSIVE,
        reason_code=None,
        returncode=1,
        decided_by_report=False,
        process_tree=None,
        limits=None,
        observation="not_requested",
        observation_required=False,
    )

    assert classified is Termination.INCONCLUSIVE
    assert classified not in JUDGEABLE


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.name)
def test_termination_precedence_table(case):
    assert classify(**case.inputs) is case.termination
    assert r2_bucket(case.termination) == case.r2_bucket


@pytest.mark.parametrize(
    ("termination", "limits", "reasons", "returncode", "expected"),
    [
        (Termination.PASSED, None, (), 0, None),
        (Termination.FAILED, None, (), 1, None),
        (Termination.TIMED_OUT, None, (), None, None),
        (Termination.HUNG, None, (), None, None),
        (Termination.NOT_RUN, None, (), None, None),
        (
            Termination.KILLED_EXTERNALLY,
            None,
            (),
            -signal.SIGKILL,
            f"killed-by-signal:{signal.SIGKILL}",
        ),
        (
            Termination.KILLED_EXTERNALLY,
            None,
            (),
            None,
            "killed-by-signal:unknown",
        ),
        (
            Termination.LEAKED,
            None,
            (),
            None,
            "leaked-process-tree",
        ),
        (
            Termination.LIMIT_EVENT,
            LIMITS_HIT,
            (),
            None,
            "resource-limit-event:pids_events_max=1",
        ),
        (
            Termination.LIMIT_EVENT,
            None,
            (),
            None,
            "resource-limit-event",
        ),
        (
            Termination.UNOBSERVED,
            None,
            ("resource-counters-unavailable",),
            None,
            "resource-observation-unavailable:resource-counters-unavailable",
        ),
        (
            Termination.UNOBSERVED,
            None,
            (),
            None,
            "resource-observation-unavailable",
        ),
    ],
    ids=[
        "passed",
        "failed",
        "timeout",
        "hung",
        "not-run",
        "signal-number-from-returncode",
        "signal-number-unavailable",
        "leaked-tree",
        "limit-names-and-counts",
        "limit-details-missing",
        "observation-reasons",
        "observation-reasons-missing",
    ],
)
def test_termination_detail_is_deterministic(
    termination, limits, reasons, returncode, expected
):
    assert detail(termination, limits, reasons, returncode) == expected


def test_external_signal_vocabulary_is_closed():
    assert EXTERNAL_SIGNALS == frozenset(
        {signal.SIGKILL, signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
    )

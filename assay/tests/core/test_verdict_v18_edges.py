"""Verdict v18 edges: the R2 profile/schedule/worker-count summary checkers and
the bucket rules for ``underlying_outcome`` / ``first_failing_test`` (B203,
B206, B207, B208).

Every case starts from a document the checker accepts and breaks exactly one
thing, so a checker that stops looking at that thing fails the test.
"""

from __future__ import annotations

import copy
import math

import pytest

from assay import verdict as v
from assay.errors import Outcome
from assay.mutation import candidate_id_from_fields
from assay.observer_windows import validate_profile
from assay.process_tree import ProcessTreeDescendant, ProcessTreeEvidence
from assay.verdict import (
    MutantOutcome,
    Mutation,
    MutationExecution,
    UnderlyingOutcome,
)


# --------------------------------------------------------------------------
# check_resource_profile delegates to the observer client's validator.
# --------------------------------------------------------------------------


def test_check_resource_profile_is_the_observer_validator():
    with pytest.raises(ValueError, match="resource-profile/1"):
        v.check_resource_profile({"schema": "other"})
    with pytest.raises(ValueError, match="resource-profile/1"):
        validate_profile({"schema": "other"})
    from assay import observer_windows as ow

    good = ow.build_profile(None, subject={}, wall_seconds=1.0, not_started=True)
    v.check_resource_profile(good)  # accepted: no raise


# --------------------------------------------------------------------------
# check_r2_worker_counts.
# --------------------------------------------------------------------------


def test_worker_counts_accept_a_consistent_mapping_and_an_empty_one():
    v.check_r2_worker_counts({"1": 3, "4": 2}, 4)
    v.check_r2_worker_counts({}, 7)  # nothing run: any positive jobs


def test_worker_counts_must_be_a_mapping():
    for bad in (None, [], "1"):
        with pytest.raises(ValueError, match="must be a mapping"):
            v.check_r2_worker_counts(bad, 1)


@pytest.mark.parametrize("key", [1, "0", "01", "-1", "1.5", "x", "", "١"])
def test_worker_count_keys_must_be_positive_decimal_integers(key):
    with pytest.raises(ValueError, match="keys must be positive decimal integers"):
        v.check_r2_worker_counts({key: 1}, 1)


@pytest.mark.parametrize("count", [0, -1, True, 1.0, "1", None])
def test_worker_count_values_must_be_positive_integers(count):
    with pytest.raises(ValueError, match="must be a positive integer"):
        v.check_r2_worker_counts({"2": count}, 2)


def test_jobs_must_equal_the_largest_recorded_worker_count():
    with pytest.raises(ValueError, match="campaign maximum worker count 4"):
        v.check_r2_worker_counts({"1": 1, "4": 1}, 2)


# --------------------------------------------------------------------------
# check_r2_profile_summary.
# --------------------------------------------------------------------------


def _profile_summary() -> dict:
    return {
        "status_counts": {"MEASURED": 1, "PARTIAL": 0, "NO_MEASUREMENT": 2},
        "reason_counts": {"OBSERVER_ERROR": 2},
        "observer": {"name": "cgprofile", "version": None, "contracts": [3],
                     "modes": ["cgroup", "process-tree"]},
    }


def _broken(base: dict, path: tuple, value) -> dict:
    document = copy.deepcopy(base)
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return document


def test_profile_summary_accepts_the_consistent_document():
    v.check_r2_profile_summary(_profile_summary())
    ok = _broken(_profile_summary(), ("observer", "version"), "1.2.3")
    v.check_r2_profile_summary(ok)
    v.check_r2_profile_summary(_broken(_profile_summary(), ("observer", "modes"), []))


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("status_counts",), None, "status_counts must name exactly"),
        (("status_counts",), {"MEASURED": 0}, "status_counts must name exactly"),
        (("status_counts", "MEASURED"), -1, "status_counts must be non-negative integers"),
        (("status_counts", "PARTIAL"), True, "status_counts must be non-negative integers"),
        (("reason_counts",), None, "reason_counts keys must be reason codes"),
        (("reason_counts",), {"MADE_UP": 2}, "reason_counts keys must be reason codes"),
        (("reason_counts", "OBSERVER_ERROR"), 0, "reason_counts must be positive integers"),
        (("reason_counts", "OBSERVER_ERROR"), 1, "must account for exactly the NO_MEASUREMENT"),
        (("observer",), None, "observer must have exactly the keys"),
        (("observer",), {"name": "cgprofile"}, "observer must have exactly the keys"),
        (("observer", "name"), "other", "observer.name must be 'cgprofile'"),
        (("observer", "version"), "", "version must be a string or null"),
        (("observer", "version"), 3, "version must be a string or null"),
        (("observer", "contracts"), [], "contracts must be a non-empty list"),
        (("observer", "contracts"), 3, "contracts must be a non-empty list"),
        (("observer", "contracts"), [0], "contracts must be a non-empty list"),
        (("observer", "contracts"), [True], "contracts must be a non-empty list"),
        (("observer", "modes"), "cgroup", "modes must be a sorted, duplicate-free"),
        (("observer", "modes"), ["bogus"], "modes must be a sorted, duplicate-free"),
        (("observer", "modes"), ["process-tree", "cgroup"], "modes must be a sorted, duplicate-free"),
        (("observer", "modes"), ["cgroup", "cgroup"], "modes must be a sorted, duplicate-free"),
    ],
)
def test_profile_summary_rejects_each_broken_field(path, value, message):
    with pytest.raises(ValueError, match=message):
        v.check_r2_profile_summary(_broken(_profile_summary(), path, value))


def test_profile_summary_rejects_a_wrong_top_level_shape():
    for bad in (None, [], {}, {**_profile_summary(), "extra": 1}):
        with pytest.raises(ValueError, match="exactly the keys status_counts/reason_counts/observer"):
            v.check_r2_profile_summary(bad)


# --------------------------------------------------------------------------
# check_r2_schedule_summary.
# --------------------------------------------------------------------------


def _canonical_schedule() -> dict:
    return {
        "policy": "canonical",
        "thresholds": None,
        "memory_budget_bytes": None,
        "jobs": 2,
        "prediction_sources": {},
        "error": None,
    }


def _tem_schedule() -> dict:
    classes = {name: 0 for name in v.R2_SCHEDULE_CLASSES}
    return {
        "policy": "tem-lpt",
        "thresholds": {"high_bytes": 100, "low_bytes": None, "high_factor": 1.5,
                       "low_factor": 0.5},
        "memory_budget_bytes": 4096,
        "jobs": 2,
        "prediction_sources": {"profile": 3, "none": 0},
        "error": {
            "rss_ratio": {"p50": 1.0, "p95": 2.5},
            "wall_ratio": {"p50": None, "p95": None},
            "class_confusion": {name: dict(classes) for name in v.R2_SCHEDULE_CLASSES},
            "admission_overshoots": 0,
        },
    }


def test_schedule_summary_accepts_a_canonical_and_a_tem_document():
    v.check_r2_schedule_summary(_canonical_schedule(), 2)
    v.check_r2_schedule_summary(_tem_schedule(), 2)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("policy",), "other", "policy must be one of"),
        (("jobs",), 0, "jobs must be a positive integer"),
        (("jobs",), True, "jobs must be a positive integer"),
        (("memory_budget_bytes",), 0, "memory_budget_bytes must be at least 1 or null"),
        (("memory_budget_bytes",), -5, "memory_budget_bytes must be a non-negative integer"),
        (("memory_budget_bytes",), 1.5, "memory_budget_bytes must be a non-negative integer"),
        (("memory_budget_bytes",), True, "memory_budget_bytes must be a non-negative integer"),
        (("thresholds",), None, "thresholds is null exactly for the canonical policy"),
        (("thresholds",), [], "thresholds must have exactly the keys"),
        (("thresholds", "extra"), 1, "thresholds must have exactly the keys"),
        (("thresholds", "high_bytes"), -1, "high_bytes must be a non-negative integer"),
        (("thresholds", "low_bytes"), 0.5, "low_bytes must be a non-negative integer"),
        (("thresholds", "high_factor"), 0, "high_factor must be a positive number"),
        (("thresholds", "high_factor"), True, "high_factor must be a positive number"),
        (("thresholds", "low_factor"), math.inf, "low_factor must be a positive number"),
        (("thresholds", "low_factor"), math.nan, "low_factor must be a positive number"),
        (("thresholds", "low_factor"), "1", "low_factor must be a positive number"),
        (("prediction_sources",), None, "prediction_sources keys must be from"),
        (("prediction_sources",), {"magic": 1}, "prediction_sources keys must be from"),
        (("prediction_sources", "profile"), -1, "prediction_sources must be non-negative integers"),
        (("error",), None, "schedule.error must have exactly the keys"),
        (("error", "extra"), 1, "schedule.error must have exactly the keys"),
        (("error", "rss_ratio"), {"p50": 1.0}, "error.rss_ratio must have exactly the keys p50/p95"),
        (("error", "wall_ratio"), None, "error.wall_ratio must have exactly the keys p50/p95"),
        (("error", "rss_ratio", "p95"), -1.0, "rss_ratio.p95 must be a non-negative number"),
        (("error", "wall_ratio", "p50"), math.inf, "wall_ratio.p50 must be a non-negative number"),
        (("error", "wall_ratio", "p50"), True, "wall_ratio.p50 must be a non-negative number"),
        (("error", "class_confusion"), None, "class_confusion must name"),
        (("error", "class_confusion"), {"high": {}}, "class_confusion must name"),
        (("error", "class_confusion", "low"), None, r"class_confusion\['low'\] must count each of"),
        (("error", "class_confusion", "low"), {"high": 0}, r"class_confusion\['low'\] must count each of"),
        (("error", "class_confusion", "high", "low"), -1, r"class_confusion\['high'\] must count each of"),
        (("error", "admission_overshoots"), -1, "admission_overshoots must be a non-negative integer"),
        (("error", "admission_overshoots"), True, "admission_overshoots must be a non-negative integer"),
    ],
)
def test_schedule_summary_rejects_each_broken_field(path, value, message):
    with pytest.raises(ValueError, match=message):
        v.check_r2_schedule_summary(_broken(_tem_schedule(), path, value), 2)


def test_the_canonical_policy_carries_no_thresholds_and_no_error():
    with pytest.raises(ValueError, match="thresholds is null exactly for the canonical policy"):
        v.check_r2_schedule_summary(
            {**_canonical_schedule(), "thresholds": _tem_schedule()["thresholds"]}, 2
        )
    with pytest.raises(ValueError, match="error is null for the canonical policy"):
        v.check_r2_schedule_summary({**_canonical_schedule(), "error": _tem_schedule()["error"]}, 2)


def test_schedule_summary_rejects_a_wrong_top_level_shape():
    for bad in (None, [], {}, {**_canonical_schedule(), "extra": 1}):
        with pytest.raises(ValueError, match="exactly the keys policy/thresholds"):
            v.check_r2_schedule_summary(bad, 2)


# --------------------------------------------------------------------------
# Bucket rules for first_failing_test and underlying_outcome.
# --------------------------------------------------------------------------


def _ingested(**overrides) -> MutantOutcome:
    values = {
        "path": "src/a.py", "lineno": 1, "start_byte": 0, "end_byte": 1,
        "replacement_sha256": "a" * 64, "operator": "stryker:BinaryExpression",
        "description": "invalid mutant", "discard_reason": "compile_error",
    }
    values.update(overrides)
    return MutantOutcome(**values)


def _native(**overrides) -> MutantOutcome:
    identity = {
        "path": "src/a.py", "source_sha256": "d" * 64, "start_byte": 0, "end_byte": 1,
        "mutated_file_sha256": "e" * 64, "operator": "python:compare-swap",
    }
    values = {
        **identity,
        "candidate_id": candidate_id_from_fields(**identity),
        "lineno": 1,
        "replacement_sha256": "a" * 64,
        "description": "replace one comparison",
        "execution": MutationExecution(mode="full"),
        "process_tree_evidence": ProcessTreeEvidence.not_started(),
    }
    values.update(overrides)
    return MutantOutcome(**values)


def test_an_ingested_mutant_cannot_name_a_first_failing_test():
    with pytest.raises(ValueError, match="ingested MutantOutcome cannot carry first_failing_test"):
        _ingested(first_failing_test="tests/test_a.py::test_x")
    assert _ingested().first_failing_test is None


def _leak() -> ProcessTreeEvidence:
    return ProcessTreeEvidence(
        leader_status="exited",
        group_signalled_before_reap=False,
        outliving_descendants=1,
        descendants_signalled=1,
        descendants=(ProcessTreeDescendant(pid=9, ppid=1, comm="child"),),
        clean=True,
    )


@pytest.mark.parametrize("bucket", ["killed", "survived", "budget_exceeded"])
def test_only_the_crashed_bucket_may_carry_an_underlying_outcome(bucket: str):
    leak = _native(
        process_tree_evidence=_leak(),
        underlying_outcome=UnderlyingOutcome(outcome=Outcome.PASS, returncode=0),
    )
    kwargs = {bucket: (leak,), "candidate_ids": (leak.candidate_id,)}
    with pytest.raises(ValueError, match=f"mutation.{bucket} cannot carry underlying_outcome"):
        Mutation(candidate_count=1, total=1, **kwargs)
    crashed = Mutation(candidate_count=1, total=1, crashed=(leak,),
                       candidate_ids=(leak.candidate_id,))
    assert crashed.crashed[0].underlying_outcome is not None


@pytest.mark.parametrize("bucket", ["survived", "crashed", "budget_exceeded"])
def test_a_first_failing_test_is_legal_only_on_a_killed_entry(bucket: str):
    entry = _native(first_failing_test="tests/test_a.py::test_x")
    kwargs = {bucket: (entry,), "candidate_ids": (entry.candidate_id,)}
    with pytest.raises(ValueError, match=f"mutation.{bucket} entry .* carries first_failing_test"):
        Mutation(candidate_count=1, total=1, **kwargs)

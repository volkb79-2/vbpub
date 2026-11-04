"""B203/B205/B206 edges in mutation.py: the state-record fields a current
record must state, the resumed-telemetry fold, the profile sibling file name
and the scheduler's RSS inputs.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from assay import mutation, observer_windows
from assay.mutation import MutationStateError
from observer_support import golden
from test_mutation_judge_identity import _job, _record, _store

JUDGE = "j" * 64


def _load(tmp_path: Path, **overrides):
    job = _job()
    root = _store(tmp_path, job, _record(job, judge=JUDGE, **overrides))
    return mutation._load_validated_state_record(root, job, judge=JUDGE)


def _measured_profile() -> dict:
    reply = copy.deepcopy(golden("close-cgroup")["response"])
    return observer_windows.build_profile(
        observer_windows.WindowResult(
            reply=reply, mode="cgroup", window=reply["window"], asked=True
        ),
        subject={"tool": "assay", "id": "c"}, wall_seconds=1.0,
    )


# --------------------------------------------------------------------------
# State records.
# --------------------------------------------------------------------------


def test_a_current_record_with_a_worker_count_and_profile_resumes(tmp_path: Path):
    loaded = _load(tmp_path)
    assert isinstance(loaded, dict)
    assert loaded["worker_count"] == 1
    assert loaded["profile"]["status"] == "NO_MEASUREMENT"


@pytest.mark.parametrize("count", [0, -1, True, 1.0, "1", None])
def test_a_record_whose_worker_count_is_not_a_positive_integer_is_corrupt(tmp_path: Path, count):
    with pytest.raises(MutationStateError, match="worker_count must be a positive integer"):
        _load(tmp_path, worker_count=count)


def test_a_record_without_a_worker_count_is_corrupt(tmp_path: Path):
    job = _job()
    payload = _record(job, judge=JUDGE)
    del payload["worker_count"]
    root = _store(tmp_path, job, payload)
    with pytest.raises(MutationStateError, match="worker_count must be a positive integer"):
        mutation._load_validated_state_record(root, job, judge=JUDGE)


@pytest.mark.parametrize("profile", [None, [], {"schema": "other"}])
def test_a_record_with_an_invalid_profile_is_corrupt_and_says_why(tmp_path: Path, profile):
    with pytest.raises(MutationStateError, match="has an invalid profile: "):
        _load(tmp_path, profile=profile)


def test_a_record_with_a_measured_profile_resumes(tmp_path: Path):
    loaded = _load(tmp_path, profile=_measured_profile())
    assert loaded["profile"]["status"] == "MEASURED"


def test_a_killed_record_may_name_its_first_failing_test(tmp_path: Path):
    loaded = _load(tmp_path, outcome_bucket="killed", first_failing_test="tests/t.py::test_x")
    assert loaded["first_failing_test"] == "tests/t.py::test_x"
    assert _load(tmp_path, outcome_bucket="killed")["outcome_bucket"] == "killed"


@pytest.mark.parametrize(
    ("bucket", "name"),
    [
        ("killed", ""),
        ("killed", 7),
        ("survived", "tests/t.py::test_x"),
    ],
)
def test_a_first_failing_test_must_be_a_non_empty_string_on_a_killed_record(
    tmp_path: Path, bucket: str, name
):
    with pytest.raises(MutationStateError, match="first_failing_test must be a non-empty string"):
        _load(tmp_path, outcome_bucket=bucket, first_failing_test=name)


# --------------------------------------------------------------------------
# Resumed telemetry.
# --------------------------------------------------------------------------


def test_resumed_records_fold_their_profiles_and_worker_counts_into_the_campaign():
    not_started = observer_windows.build_profile(
        None, subject={}, wall_seconds=0.1, not_started=True
    )
    records = [
        {"worker_count": 2, "profile": _measured_profile()},
        {"worker_count": 1, "profile": not_started},
        {"worker_count": 2, "profile": not_started},
    ]
    folded = mutation._fold_resumed_telemetry(None, records)
    assert folded["profile"]["status_counts"] == {"MEASURED": 1, "NO_MEASUREMENT": 2}
    # Only NO_MEASUREMENT profiles carry a reason; the measured one adds none.
    assert folded["profile"]["reason_counts"] == {"NOT_STARTED": 2}
    assert folded["profile"]["modes"] == ["cgroup"]  # a mode-less profile adds none
    assert folded["worker_counts"] == {"2": 2, "1": 1}
    assert folded["schedule"] is None


def test_resumed_records_add_to_the_executed_telemetry():
    executed = {
        "profile": {"status_counts": {"MEASURED": 3}, "reason_counts": {"OBSERVER_ERROR": 1},
                    "modes": ["process-tree"]},
        "worker_counts": {"4": 5},
        "schedule": {"policy": "canonical"},
    }
    folded = mutation._fold_resumed_telemetry(
        executed, [{"worker_count": 4, "profile": _measured_profile()}]
    )
    assert folded["profile"]["status_counts"] == {"MEASURED": 4}
    assert folded["profile"]["reason_counts"] == {"OBSERVER_ERROR": 1}
    assert folded["profile"]["modes"] == ["cgroup", "process-tree"]
    assert folded["worker_counts"] == {"4": 6}
    assert folded["schedule"] == {"policy": "canonical"}
    assert executed["worker_counts"] == {"4": 5}  # the input is not mutated


# --------------------------------------------------------------------------
# Profile sibling name and RSS inputs.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("progress", "profile"),
    [
        ("progress-lane.jsonl", "profile-lane.jsonl"),
        ("out.jsonl", "out.profile.jsonl"),
        ("a.b.log", "a.b.profile.log"),
        ("noextension", "noextension.profile.jsonl"),
    ],
)
def test_the_profile_file_sits_next_to_the_progress_file(progress: str, profile: str):
    assert mutation._profile_sibling_name(progress) == profile


def test_the_scheduler_rss_input_is_the_peak_rss_of_the_window_reply():
    reply = copy.deepcopy(golden("close-cgroup")["response"])
    result = observer_windows.WindowResult(reply=reply, mode="cgroup", asked=True)
    assert mutation._result_rss_bytes(result) == int(reply["metrics"]["rss_peak_bytes"])
    assert mutation._result_rss_bytes(result) == 6144000
    reply["metrics"]["rss_peak_bytes"] = None
    reply["missing"]["rss_peak_bytes"] = "NOT_COLLECTED"
    assert mutation._result_rss_bytes(result) is None


def test_there_is_no_scheduler_rss_input_without_a_reply():
    assert mutation._result_rss_bytes(None) is None
    failed = observer_windows.WindowResult(reason=observer_windows.OBSERVER_ERROR, asked=True)
    assert mutation._result_rss_bytes(failed) is None

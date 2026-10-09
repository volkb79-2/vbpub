"""R0/R1 coverage for complete native mutation inventory validation."""

from __future__ import annotations

import pytest

from assay._mutation_inventory import verify_complete_mutation_inventory

_BUCKETS = ("killed", "survived", "crashed", "budget_exceeded", "equivalent", "hung")
_ID_A = "a" * 64
_ID_B = "b" * 64


def _payload(planned_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "candidate_count": len(planned_ids),
        "total": len(planned_ids),
        "candidate_ids": list(planned_ids),
        **{bucket: [] for bucket in _BUCKETS},
    }


def test_accepts_a_complete_ordered_partition_of_the_plan():
    mutation = _payload((_ID_A, _ID_B))
    mutation["killed"] = [{"candidate_id": _ID_A}]
    mutation["survived"] = [{"candidate_id": _ID_B}]

    verify_complete_mutation_inventory(
        mutation, (_ID_A, _ID_B), context="analysis R2"
    )


@pytest.mark.parametrize(
    ("mutation", "planned_ids", "message"),
    [
        (None, (_ID_A,), "mutation payload is not an object"),
        (
            {**_payload((_ID_A,)), "candidate_count": True},
            (_ID_A,),
            "candidate_count differs",
        ),
        (
            {**_payload((_ID_A,)), "candidate_count": 2},
            (_ID_A,),
            "candidate_count differs",
        ),
        (
            {**_payload((_ID_A,)), "total": False},
            (_ID_A,),
            "total differs",
        ),
        (
            {**_payload((_ID_A,)), "total": 2},
            (_ID_A,),
            "total differs",
        ),
        (
            {**_payload((_ID_A,)), "candidate_ids": None},
            (_ID_A,),
            "candidate_ids are not an array",
        ),
        (
            {**_payload((_ID_A,)), "candidate_ids": ["bad"]},
            (_ID_A,),
            "candidate_ids contain a malformed ID",
        ),
        (
            {**_payload((_ID_A, _ID_B)), "candidate_ids": [_ID_A, _ID_A]},
            (_ID_A, _ID_B),
            "candidate_ids contains duplicates",
        ),
        (
            {**_payload((_ID_A, _ID_B)), "candidate_ids": [_ID_B, _ID_A]},
            (_ID_A, _ID_B),
            "candidate_ids differ from the ordered full plan",
        ),
        (
            {key: value for key, value in _payload((_ID_A,)).items() if key != "killed"},
            (_ID_A,),
            "mutation.killed is missing or not an array",
        ),
        (
            {**_payload((_ID_A,)), "killed": [None]},
            (_ID_A,),
            r"mutation\.killed\[0\] is not an object",
        ),
        (
            {**_payload((_ID_A,)), "killed": [{"candidate_id": "bad"}]},
            (_ID_A,),
            r"mutation\.killed\[0\] has no 64-hex candidate_id",
        ),
        (
            _payload((_ID_A,)),
            (_ID_A,),
            "bucket outcome count differs from total",
        ),
        (
            {
                **_payload((_ID_A, _ID_B)),
                "killed": [{"candidate_id": _ID_A}],
                "survived": [{"candidate_id": _ID_A}],
            },
            (_ID_A, _ID_B),
            "bucket outcomes contain duplicate candidate_ids",
        ),
        (
            {**_payload((_ID_A,)), "killed": [{"candidate_id": _ID_B}]},
            (_ID_A,),
            "bucket outcomes do not cover the complete candidate_ids",
        ),
    ],
)
def test_rejects_incomplete_or_malformed_campaign_inventories(
    mutation, planned_ids, message
):
    with pytest.raises(ValueError, match=message):
        verify_complete_mutation_inventory(
            mutation, planned_ids, context="analysis R2"
        )

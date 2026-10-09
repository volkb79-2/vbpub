"""R0/R1 coverage for complete native mutation inventory validation."""

from __future__ import annotations

import pytest

from assay._mutation_inventory import verify_complete_mutation_inventory

_BUCKETS = ("killed", "survived", "crashed", "budget_exceeded", "equivalent", "hung")
_ID_A = "a" * 64
_ID_B = "b" * 64
_ID_C = "c" * 64
_ID_D = "d" * 64
_ID_E = "e" * 64
_ID_F = "f" * 64


def _payload(planned_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "candidate_count": len(planned_ids),
        "total": len(planned_ids),
        "candidate_ids": list(planned_ids),
        **{bucket: [] for bucket in _BUCKETS},
    }


def test_accepts_a_complete_ordered_partition_of_the_plan():
    planned_ids = (_ID_A, _ID_B, _ID_C, _ID_D, _ID_E, _ID_F)
    mutation = _payload(planned_ids)
    for bucket, identity in zip(_BUCKETS, planned_ids):
        mutation[bucket] = [{"candidate_id": identity}]

    verify_complete_mutation_inventory(
        mutation, planned_ids, context="analysis R2"
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


@pytest.mark.parametrize("missing_bucket", _BUCKETS)
def test_requires_each_terminal_bucket(missing_bucket):
    mutation = _payload((_ID_A,))
    del mutation[missing_bucket]

    with pytest.raises(
        ValueError,
        match=rf"mutation\.{missing_bucket} is missing or not an array",
    ):
        verify_complete_mutation_inventory(
            mutation, (_ID_A,), context="analysis R2"
        )


@pytest.mark.parametrize("non_array_bucket", _BUCKETS)
def test_requires_each_terminal_bucket_to_be_a_list(non_array_bucket):
    mutation = _payload((_ID_A,))
    mutation[non_array_bucket] = ({"candidate_id": _ID_A},)

    with pytest.raises(
        ValueError,
        match=rf"mutation\.{non_array_bucket} is missing or not an array",
    ):
        verify_complete_mutation_inventory(
            mutation, (_ID_A,), context="analysis R2"
        )

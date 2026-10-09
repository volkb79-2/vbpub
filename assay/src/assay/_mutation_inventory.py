"""Internal checks for complete native mutation campaign inventories."""

from __future__ import annotations

import re
from typing import Any, Sequence

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_BUCKETS = ("killed", "survived", "crashed", "budget_exceeded", "equivalent", "hung")


def verify_complete_mutation_inventory(
    mutation: Any,
    planned_ids: Sequence[str],
    *,
    context: str,
) -> None:
    """Require every planned candidate to occur once across terminal buckets."""
    if not isinstance(mutation, dict):
        raise ValueError(f"{context} mutation payload is not an object")
    candidate_count = mutation.get("candidate_count")
    total = mutation.get("total")
    if type(candidate_count) is not int or candidate_count != len(planned_ids):
        raise ValueError(f"{context} candidate_count differs from the complete plan")
    if type(total) is not int or total != candidate_count:
        raise ValueError(f"{context} total differs from the complete candidate_count")

    candidate_ids = mutation.get("candidate_ids")
    if not isinstance(candidate_ids, list):
        raise ValueError(f"{context} candidate_ids are not an array")
    if any(
        not isinstance(identity, str) or not _HEX64.fullmatch(identity)
        for identity in candidate_ids
    ):
        raise ValueError(f"{context} candidate_ids contain a malformed ID")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(f"{context} candidate_ids contains duplicates")
    if candidate_ids != list(planned_ids):
        raise ValueError(f"{context} candidate_ids differ from the ordered full plan")

    bucket_ids: list[str] = []
    for bucket in _BUCKETS:
        outcomes = mutation.get(bucket)
        if not isinstance(outcomes, list):
            raise ValueError(f"{context} mutation.{bucket} is missing or not an array")
        for index, outcome in enumerate(outcomes):
            if not isinstance(outcome, dict):
                raise ValueError(f"{context} mutation.{bucket}[{index}] is not an object")
            identity = outcome.get("candidate_id")
            if not isinstance(identity, str) or not _HEX64.fullmatch(identity):
                raise ValueError(
                    f"{context} mutation.{bucket}[{index}] has no 64-hex candidate_id"
                )
            bucket_ids.append(identity)

    if len(bucket_ids) != total:
        raise ValueError(f"{context} bucket outcome count differs from total")
    if len(bucket_ids) != len(set(bucket_ids)):
        raise ValueError(f"{context} bucket outcomes contain duplicate candidate_ids")
    if set(bucket_ids) != set(planned_ids):
        raise ValueError(f"{context} bucket outcomes do not cover the complete candidate_ids")

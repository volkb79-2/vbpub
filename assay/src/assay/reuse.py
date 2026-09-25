"""Validated prior-verdict evidence for B106 selective mutation reruns."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import AssayError, Outcome, ReasonCode

MAX_REUSE_ARTIFACT_BYTES = 16 * 1024 * 1024
V12_COLD_START = 12


@dataclass(frozen=True, kw_only=True)
class ReuseSource:
    path: Path
    schema_version: int
    sha256: str
    document: Mapping[str, Any] | None
    cold_start: bool
    complete_unsharded_native: bool
    outcomes: Mapping[str, tuple[str, Mapping[str, Any]]]
    candidate_ids: frozenset[str]


def load_reuse_source(path: str | Path) -> ReuseSource:
    """Read one bounded artifact; v12 is recognized without inspecting its claims."""
    source_path = Path(path).expanduser()
    try:
        with source_path.open("rb") as stream:
            raw = stream.read(MAX_REUSE_ARTIFACT_BYTES + 1)
    except OSError as exc:
        raise _unreadable(source_path, f"cannot read file: {exc}") from exc
    if len(raw) > MAX_REUSE_ARTIFACT_BYTES:
        raise _unreadable(
            source_path,
            f"exceeds the {MAX_REUSE_ARTIFACT_BYTES}-byte artifact limit",
        )
    try:
        text = raw.decode("utf-8", errors="strict")
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_non_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _unreadable(source_path, f"invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _unreadable(source_path, "top-level JSON value must be an object")
    version = document.get("schema_version")
    if type(version) is not int:
        raise _unreadable(source_path, "schema_version must be an integer")
    digest = hashlib.sha256(raw).hexdigest()
    if version == V12_COLD_START:
        # Deliberately stop at the version marker. Nothing under claims or
        # mutation is validated, trusted, or used to select a candidate.
        return ReuseSource(
            path=source_path,
            schema_version=version,
            sha256=digest,
            document=None,
            cold_start=True,
            complete_unsharded_native=False,
            outcomes={},
            candidate_ids=frozenset(),
        )

    from .verdict import VERDICT_SCHEMA_VERSION
    from .verify import verify_document

    if version != VERDICT_SCHEMA_VERSION:
        raise _unreadable(
            source_path,
            f"schema_version {version} is unsupported; expected 12 cold start "
            f"or current version {VERDICT_SCHEMA_VERSION}",
        )
    failures = verify_document(document)
    if failures:
        raise _unreadable(
            source_path,
            "current v13 verifier rejected the artifact: " + "; ".join(failures),
        )

    r2_claim = next(
        (
            claim
            for claim in document.get("claims", [])
            if isinstance(claim, dict) and claim.get("rigor") == "R2"
        ),
        None,
    )
    judgment = document.get("judgment")
    policy = judgment.get("r2") if isinstance(judgment, dict) else None
    mutation = r2_claim.get("mutation") if isinstance(r2_claim, dict) else None
    inventory = mutation.get("candidate_ids") if isinstance(mutation, dict) else None
    complete = (
        isinstance(policy, dict)
        and policy.get("producer") == "native"
        and isinstance(mutation, dict)
        and isinstance(inventory, list)
        and "shard_index" not in policy
        and "shard_count" not in policy
        and not (
            mutation.get("total") == 0
            and mutation.get("candidate_count", 0) > 0
        )
    )
    outcomes: dict[str, tuple[str, Mapping[str, Any]]] = {}
    candidate_ids = frozenset(inventory) if complete else frozenset()
    if complete:
        for bucket in ("killed", "survived", "crashed", "budget_exceeded", "equivalent", "hung"):
            for item in mutation.get(bucket, []):
                candidate = item.get("candidate_id")
                outcomes[candidate] = (bucket, item)
    return ReuseSource(
        path=source_path,
        schema_version=version,
        sha256=digest,
        document=document,
        cold_start=False,
        complete_unsharded_native=bool(complete),
        outcomes=outcomes,
        candidate_ids=candidate_ids,
    )


def classify_candidate(
    source: ReuseSource,
    candidate_id: str,
    *,
    sequential_pytest_supported: bool,
) -> tuple[str, str | None]:
    """Return the plan label and, only for eligible kills, their witness node."""
    if not source.complete_unsharded_native:
        if source.cold_start:
            return "unproven-source", "v12 cold start has no reusable witnesses"
        return "unproven-source", "source is not a complete unsharded native campaign"
    previous = source.outcomes.get(candidate_id)
    if previous is None:
        return "new-candidate", None
    bucket, outcome = previous
    if bucket != "killed":
        return "prior-outcome-requires-full", f"prior outcome was {bucket}"
    execution = outcome.get("execution")
    witness = execution.get("witness") if isinstance(execution, dict) else None
    if not isinstance(witness, dict):
        return "prior-outcome-requires-full", "prior kill has no witness receipt"
    if not sequential_pytest_supported:
        return "prior-outcome-requires-full", "current command is not supported sequential pytest"
    node_id = witness.get("node_id")
    if not isinstance(node_id, str):
        return "prior-outcome-requires-full", "prior witness node ID is malformed"
    return "witness-replay", node_id


def eligible_witnesses(
    source: ReuseSource, *, sequential_pytest_supported: bool
) -> dict[str, tuple[str, str]]:
    """Map eligible candidate IDs to (source digest, exact prior witness node)."""
    if not sequential_pytest_supported or not source.complete_unsharded_native:
        return {}
    eligible: dict[str, tuple[str, str]] = {}
    for candidate in source.candidate_ids:
        label, node_or_reason = classify_candidate(
            source, candidate, sequential_pytest_supported=True
        )
        if label == "witness-replay" and node_or_reason is not None:
            eligible[candidate] = (source.sha256, node_or_reason)
    return eligible


def prior_only_candidates(source: ReuseSource, current: Sequence[str]) -> list[str]:
    if not source.complete_unsharded_native:
        return []
    return sorted(source.candidate_ids - set(current))


def _unreadable(path: Path, detail: str) -> AssayError:
    return AssayError(
        f"--reuse-from {str(path)!r}: {detail}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value!r}")

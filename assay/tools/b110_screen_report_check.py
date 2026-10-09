#!/usr/bin/env python3
"""Bind a B110 survivor-screen verdict to the complete current R2 plan."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PLAN_BYTES = 16 * 1024 * 1024
_MAX_VERDICT_BYTES = 64 * 1024 * 1024
_BUCKETS = ("killed", "survived", "crashed", "budget_exceeded", "equivalent", "hung")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path, *, max_bytes: int) -> Any:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{path} is not a regular file")
        if info.st_size > max_bytes:
            raise ValueError(f"{path} exceeds the {max_bytes}-byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{path} exceeds the {max_bytes}-byte limit")
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not valid UTF-8 JSON: {exc}") from exc


def _require_inventory(plan: Any, *, expected_commit: str, expected_tree: str) -> list[str]:
    if not isinstance(plan, dict):
        raise ValueError("plan is not an object")
    if plan.get("status") != "ok":
        raise ValueError("plan status is not 'ok'")
    if plan.get("lane") != "self-qualification":
        raise ValueError("plan lane is not 'self-qualification'")
    if plan.get("commit") != expected_commit or plan.get("tree") != expected_tree:
        raise ValueError("plan commit/tree differ from the expected source")
    if plan.get("shard") is not None:
        raise ValueError("plan is sharded; the screen must cover the full inventory")
    count = plan.get("candidate_count")
    rows = plan.get("candidates")
    if type(count) is not int or count < 0 or not isinstance(rows, list):
        raise ValueError("plan candidate_count/candidates have invalid types")
    if count != len(rows):
        raise ValueError("plan candidate_count does not match its candidate rows")
    ids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"plan candidate {index} is not an object")
        identity = row.get("id")
        if not isinstance(identity, str) or not _HEX64.fullmatch(identity):
            raise ValueError(f"plan candidate {index} has no 64-hex id")
        ids.append(identity)
    if len(ids) != len(set(ids)):
        raise ValueError("plan candidate inventory contains duplicates")
    return ids


def verify_screen(
    plan: Any,
    verdict: Any,
    *,
    expected_commit: str,
    expected_tree: str,
    expected_exit_code: int,
) -> None:
    if not _HEX40.fullmatch(expected_commit) or not _HEX40.fullmatch(expected_tree):
        raise ValueError("expected commit and tree must be full lowercase Git IDs")
    if type(expected_exit_code) is not int or expected_exit_code < 0:
        raise ValueError("expected producer exit code must be a non-negative integer")
    planned_ids = _require_inventory(
        plan, expected_commit=expected_commit, expected_tree=expected_tree
    )
    if not isinstance(verdict, dict):
        raise ValueError("verdict is not an object")
    if verdict.get("commit") != expected_commit:
        raise ValueError("verdict commit differs from the expected source")
    if verdict.get("lane") != "self-qualification":
        raise ValueError("verdict lane is not 'self-qualification'")
    if verdict.get("reason_code") == "LANE_TIMEOUT":
        raise ValueError("verdict ended with LANE_TIMEOUT")
    if not isinstance(verdict.get("outcome"), str) or type(verdict.get("exit_code")) is not int:
        raise ValueError("verdict has no outcome or integer exit_code")
    if verdict["exit_code"] != expected_exit_code:
        raise ValueError("verdict exit_code differs from the assay run exit status")

    claims = verdict.get("claims")
    if not isinstance(claims, list):
        raise ValueError("verdict claims is not an array")
    by_rigor: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ValueError(f"verdict claim {index} is not an object")
        rigor = claim.get("rigor")
        if rigor in {"R0", "R1", "R2"}:
            if rigor in by_rigor:
                raise ValueError(f"verdict contains duplicate {rigor} claims")
            by_rigor[rigor] = claim
    for rigor in ("R0", "R1"):
        claim = by_rigor.get(rigor)
        if claim is None or claim.get("status") != "PASS":
            raise ValueError(f"{rigor} claim is missing or not PASS")

    r2 = by_rigor.get("R2")
    if r2 is None:
        raise ValueError("R2 claim is missing")
    if r2.get("status") not in ("PASS", "FAIL"):
        raise ValueError("R2 claim is not a completed PASS or FAIL outcome")
    if r2.get("reason_code") == "LANE_TIMEOUT":
        raise ValueError("R2 claim ended with LANE_TIMEOUT")
    judgment = verdict.get("judgment")
    policy = judgment.get("r2") if isinstance(judgment, dict) else None
    if not isinstance(policy, dict) or policy.get("producer") != "native":
        raise ValueError("R2 judgment is not a native mutation run")
    if policy.get("shard_index") is not None or policy.get("shard_count") is not None:
        raise ValueError("R2 judgment is sharded; the screen must cover the full plan")

    mutation = r2.get("mutation")
    if not isinstance(mutation, dict):
        raise ValueError("R2 mutation payload is missing")
    if r2.get("reason_code") == "MUTATION_DISCOVERY_FAILED":
        raise ValueError("R2 mutation discovery did not produce a campaign")
    if r2.get("reason_code") == "LANE_TIMEOUT":
        raise ValueError("R2 claim ended with LANE_TIMEOUT")
    candidate_count = mutation.get("candidate_count")
    total = mutation.get("total")
    if type(candidate_count) is not int or candidate_count != len(planned_ids):
        raise ValueError("R2 mutation candidate_count differs from the full plan")
    if type(total) is not int or total < 0:
        raise ValueError("R2 mutation total is not a non-negative integer")
    bucket_total = 0
    for bucket in _BUCKETS:
        values = mutation.get(bucket)
        if not isinstance(values, list):
            raise ValueError(f"R2 mutation.{bucket} is missing or not an array")
        bucket_total += len(values)
    if total != bucket_total:
        raise ValueError("R2 mutation total differs from its bucket inventory")
    identities = mutation.get("candidate_ids")
    if not isinstance(identities, list) or any(
        not isinstance(identity, str) or not _HEX64.fullmatch(identity)
        for identity in identities
    ):
        raise ValueError("R2 mutation candidate_ids is missing or malformed")
    if len(identities) != len(set(identities)):
        raise ValueError("R2 mutation candidate_ids contains duplicates")
    if identities != planned_ids:
        raise ValueError("R2 mutation candidate_ids differ from the ordered full plan")
    if candidate_count < total:
        raise ValueError("R2 mutation candidate_count is smaller than attempted total")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--verdict", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--expected-exit-code", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        plan = _read_json(args.plan, max_bytes=_MAX_PLAN_BYTES)
        verdict = _read_json(args.verdict, max_bytes=_MAX_VERDICT_BYTES)
        verify_screen(
            plan,
            verdict,
            expected_commit=args.expected_commit,
            expected_tree=args.expected_tree,
            expected_exit_code=args.expected_exit_code,
        )
    except (OSError, ValueError) as exc:
        print(f"b110_screen_report_check: {exc}", file=sys.stderr)
        return 2
    print("B110_SCREEN_VERIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

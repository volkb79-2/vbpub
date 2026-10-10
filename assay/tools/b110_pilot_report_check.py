#!/usr/bin/env python3
"""Attest that a B110 pilot exit 6 represents a complete, source-bound run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import b110_pilot_select as selector
import pilot_r2_evidence
from assay.cli import _plan_rows_from_jobs, _resolve_declared_adapters
from assay.config import load_lane_file
from assay.mutation import (
    MUTATION_BUCKETS,
    MUTATION_STATE_SCHEMA_VERSION,
    MutantEvidence,
    MutationTarget,
    UNSUPPORTED,
    candidate_id,
    collect_mutation_sites,
    _execution_from_state_record,
    _terminal_result_matches_bucket,
    valid_hung_resource_evidence,
)
from assay.resource_limits import ResourceLimitEvidence


_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SEED = "b110-pilot-2026"
_SIZE = 64
_LIMITS = {
    "plan": 32 * 1024 * 1024,
    "candidates": 1024 * 1024,
    "selection": 64 * 1024 * 1024,
    "summary": 64 * 1024 * 1024,
    "deadline": 1024 * 1024,
    "progress": 16 * 1024 * 1024,
    "state": 16 * 1024 * 1024,
    "run_log": 64 * 1024 * 1024,
}
_MAX_PROGRESS_EVENTS = 50_000
_MAX_PROGRESS_LINE_BYTES = 64 * 1024
_PILOT_RIGOR = ["R0", "R1", "R2"]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _is_aware_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _valid_progress_timing(event: dict[str, Any]) -> bool:
    elapsed = event.get("elapsed_s")
    return (
        _is_aware_timestamp(event.get("emitted_at"))
        and type(elapsed) in (int, float)
        and math.isfinite(elapsed)
        and elapsed >= 0
    )


def _read_regular(path: Path, *, limit: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"{path} is not a single-link regular file")
        if info.st_size > limit:
            raise ValueError(f"{path} exceeds the {limit}-byte limit")
        content = bytearray()
        while len(content) <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > limit:
            raise ValueError(f"{path} exceeds the {limit}-byte limit")
        return bytes(content)
    finally:
        os.close(descriptor)


def _read_json(path: Path, *, limit: int) -> tuple[Any, bytes]:
    raw = _read_regular(path, limit=limit)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object), raw
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{path} is not valid UTF-8 JSON: {exc}") from exc


def _require_directory(path: Path) -> None:
    fd_match = re.fullmatch(r"/proc/(?:self|[0-9]+)/fd/([0-9]+)", str(path))
    if fd_match is not None:
        try:
            info = os.fstat(int(fd_match.group(1)))
        except OSError as exc:
            raise ValueError(f"cannot inspect pinned directory {path}: {exc}") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"{path} is not a pinned directory")
        return
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"cannot inspect directory {path}: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{path} is not a real directory")


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def derive_b105_inventory(
    *, repo_root: Path, expected_commit: str, expected_tree: str
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, bytes]]:
    """Rebuild the complete ordered B105 candidate inventory from committed source.

    The bounded B110 screen and pilot both use this same derivation so neither
    can treat a producer-supplied plan as the authority for what was judged.
    """
    try:
        repo_root = repo_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve repository root {repo_root}: {exc}") from exc
    if not _HEX40.fullmatch(expected_commit) or not _HEX40.fullmatch(expected_tree):
        raise ValueError("expected commit and tree must be full lowercase Git IDs")
    current_commit, current_tree = selector._repository_identity(repo_root)
    if (current_commit, current_tree) != (expected_commit, expected_tree):
        raise ValueError("current Git identity differs from the expected source")

    config_relative = "assay/assay.toml"
    try:
        config_raw = selector._read_tree_files(
            (config_relative,), repo_root, commit=expected_commit
        )[config_relative]
        config_path = repo_root / config_relative
        if _read_regular(config_path, limit=16 * 1024 * 1024) != config_raw:
            raise ValueError("current assay.toml bytes differ from the committed lane config")
        lane_file = load_lane_file(config_path)
        if _read_regular(config_path, limit=16 * 1024 * 1024) != config_raw:
            raise ValueError("assay.toml changed while the host checker loaded it")
        lane = lane_file.lane("self-qualification")
    except Exception as exc:
        raise ValueError(f"cannot load the committed B105 self-qualification lane: {exc}") from exc
    if (
        lane_file.project_root != repo_root / "assay"
        or lane.judge is None
        or lane.judge.language != "python"
        or lane.judge.mode != "whole_target"
        or lane.judge.targets is None
        or lane.judge.mutation is None
        or lane.judge.mutation.operators is None
        or lane.judge.mutation.max_mutants is None
        or "R2" not in lane.rigor
    ):
        raise ValueError("committed B105 lane is not the declared Python whole-target mutation lane")
    adapter = _resolve_declared_adapters(lane)
    if adapter is None:
        raise ValueError("committed B105 lane does not resolve a mutation adapter")
    project_prefix = lane_file.project_root.relative_to(repo_root).as_posix()
    source_paths = sorted(
        {
            f"{project_prefix}/{relative}"
            for relative in lane.judge.targets
        }
    )
    try:
        sources = selector._read_tree_files(
            source_paths, repo_root, commit=expected_commit
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the complete committed B105 target inventory: {exc}") from exc
    targets: list[MutationTarget] = []
    for path in source_paths:
        try:
            text = sources[path].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"planned Python source {path!r} is not UTF-8") from exc
        line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
        targets.append(
            MutationTarget(path=path, text=text, lines=frozenset(range(1, line_count + 1)))
        )
    jobs = collect_mutation_sites(
        targets,
        adapter=adapter,
        operators=lane.judge.mutation.operators,
        limit=lane.judge.mutation.max_mutants + 1,
    )
    if jobs == UNSUPPORTED or len(jobs) > lane.judge.mutation.max_mutants:
        raise ValueError("committed B105 targets exceed the supported complete mutation inventory")
    expected_rows = _plan_rows_from_jobs(jobs)
    replacement_sha256_by_id: dict[str, str] = {}
    for job in jobs:
        identity = candidate_id(job)
        if identity in replacement_sha256_by_id:
            raise ValueError(f"rederived mutation inventory repeats candidate {identity}")
        replacement_sha256_by_id[identity] = job.site.replacement_sha256
    return expected_rows, replacement_sha256_by_id, sources


def _verify_plan(
    document: Any,
    *,
    expected_commit: str,
    expected_tree: str,
    repo_root: Path,
    plan_raw: bytes,
    selection: Any,
    candidates_raw: bytes,
) -> tuple[list[dict[str, Any]], list[str], dict[str, str]]:
    if not isinstance(document, dict):
        raise ValueError("plan is not an object")
    if (
        document.get("status") != "ok"
        or document.get("lane") != "self-qualification"
        or document.get("commit") != expected_commit
        or document.get("tree") != expected_tree
        or document.get("shard") is not None
    ):
        raise ValueError("plan is not the complete self-qualification inventory at the expected source")
    rows = document.get("candidates")
    if (
        not isinstance(rows, list)
        or type(document.get("candidate_count")) is not int
        or document["candidate_count"] != len(rows)
    ):
        raise ValueError("plan candidate_count does not match its candidate rows")
    plan_commit, plan_tree, parsed_rows = selector._parse_plan(plan_raw)
    if plan_commit != expected_commit or plan_tree != expected_tree or parsed_rows != rows:
        raise ValueError("plan rows or identity do not match the parsed source-bound plan")

    expected_rows, replacement_sha256_by_id, sources = derive_b105_inventory(
        repo_root=repo_root,
        expected_commit=expected_commit,
        expected_tree=expected_tree,
    )
    if rows != expected_rows:
        raise ValueError(
            "plan candidate inventory differs from the complete committed whole-target lane"
        )

    if not isinstance(selection, dict):
        raise ValueError("selection report is not an object")
    if set(selection) != {
        "schema",
        "plan_commit",
        "plan_tree",
        "plan_sha256",
        "selected_ids",
        "candidates_file_sha256",
        "seed",
        "size",
        "plan_candidate_count",
        "files",
        "operators",
        "stratified",
        "known_hard",
        "overlap",
        "import_time_total",
        "selection_sha256",
    }:
        raise ValueError("selection report has missing or unknown fields")
    if selection.get("schema") != "b110-pilot-selection/1":
        raise ValueError("selection report schema is not b110-pilot-selection/1")
    if selection.get("seed") != _SEED or selection.get("size") != _SIZE:
        raise ValueError("selection report does not use the declared B110 seed and size")
    if selection.get("plan_candidate_count") != len(rows):
        raise ValueError("selection report candidate count differs from the full plan")
    if selection.get("plan_sha256") != hashlib.sha256(plan_raw).hexdigest():
        raise ValueError("selection report does not bind the exact plan bytes")

    stratified, hard, files, operators, import_time_total = selector._select(
        rows, seed=_SEED, size=_SIZE, repo_root=repo_root, sources=sources
    )
    stratified_ids = {row["id"] for row in stratified}
    hard_ids = {row["id"] for row in hard}
    overlap = [row["id"] for row in rows if row["id"] in stratified_ids & hard_ids]
    selected_set = stratified_ids | hard_ids
    selected_ids = [row["id"] for row in rows if row["id"] in selected_set]
    candidates_text = (
        f"# b110 pilot selection seed={_SEED} size={_SIZE} plan_candidates={len(rows)}\n"
        + "".join(f"{identity}\n" for identity in selected_ids)
    ).encode("utf-8")
    if candidates_raw != candidates_text:
        raise ValueError("candidate file differs from the deterministic selection")
    expected_selection = {
        "schema": "b110-pilot-selection/1",
        "plan_commit": expected_commit,
        "plan_tree": expected_tree,
        "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
        "selected_ids": selected_ids,
        "candidates_file_sha256": hashlib.sha256(candidates_text).hexdigest(),
        "seed": _SEED,
        "size": _SIZE,
        "plan_candidate_count": len(rows),
        "files": files,
        "operators": operators,
        "stratified": stratified,
        "known_hard": hard,
        "overlap": overlap,
        "import_time_total": import_time_total,
        "selection_sha256": selector._plan_sha256(selected_ids),
    }
    if selection != expected_selection:
        raise ValueError("selection report differs from the recomputed deterministic selection")
    return rows, selected_ids, replacement_sha256_by_id


def _verify_deadline(
    deadline: Any,
    *,
    campaign: str,
    expected_commit: str,
    expected_tree: str,
    wheel_sha256: str,
    full_plan_sha256: str,
) -> None:
    if not isinstance(deadline, dict):
        raise ValueError("campaign deadline is not an object")
    if set(deadline) != {
        "schema",
        "campaign",
        "commit",
        "git_tree",
        "lanes",
        "assay_version",
        "wheel_sha256",
        "plan_sha256",
        "created_at_utc",
        "expires_at_utc",
    }:
        raise ValueError("campaign deadline has missing or unknown fields")
    if (
        deadline.get("schema") != "assay-campaign-deadline/1"
        or deadline.get("campaign") != campaign
        or deadline.get("commit") != expected_commit
        or deadline.get("git_tree") != expected_tree
        or deadline.get("lanes") != ["self-qualification"]
        or deadline.get("wheel_sha256") != wheel_sha256
        or deadline.get("plan_sha256") != {"self-qualification": full_plan_sha256}
        or not isinstance(deadline.get("assay_version"), str)
        or not deadline["assay_version"]
    ):
        raise ValueError("campaign deadline does not bind the expected source, wheel, lane and full plan")
    try:
        created = datetime.strptime(deadline["created_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        expires = datetime.strptime(deadline["expires_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("campaign deadline timestamps are invalid") from exc
    now = datetime.now(timezone.utc)
    duration = (expires - created).total_seconds()
    if created > now or duration not in {7200, 7201} or expires <= now:
        raise ValueError("campaign deadline is not the active two-hour pilot campaign")


def _verify_attempt_window(
    attempt_window: Any,
    *,
    campaign: str,
    expected_commit: str,
) -> int:
    if not isinstance(attempt_window, dict) or set(attempt_window) != {
        "schema", "campaign", "commit", "started_at_epoch_ns", "expires_at_epoch_ns",
    }:
        raise ValueError("pilot attempt window has missing or unknown fields")
    started = attempt_window.get("started_at_epoch_ns")
    expires = attempt_window.get("expires_at_epoch_ns")
    duration_ns = 90 * 60 * 1_000_000_000
    now_ns = time.time_ns()
    if (
        attempt_window.get("schema") != "assay-b110-pilot-attempt-window/1"
        or attempt_window.get("campaign") != campaign
        or attempt_window.get("commit") != expected_commit
        or type(started) is not int
        or type(expires) is not int
        or expires - started != duration_ns
        or started > now_ns
        or expires <= now_ns
    ):
        raise ValueError("pilot attempt window is not the active 90-minute campaign attempt")
    return hashlib.sha256(
        json.dumps(attempt_window, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verify_summary(
    summary: Any,
    *,
    expected_commit: str,
    selected_ids: list[str],
    rows: list[dict[str, Any]],
    candidates_sha256: str,
    selection_sha256: str,
    expected_state_dir: Path,
    repo_root: Path,
    r2_manifest_path: Path,
) -> tuple[dict[str, dict[str, Any]], str, dict[str, Any]]:
    if not isinstance(summary, dict):
        raise ValueError("pilot summary is not an object")
    expected_fields = {
        "schema", "qualifying", "completed", "lane", "commit", "jobs",
        "requested", "selection_sha256", "judge_sha256", "candidates_file_sha256", "state_dir",
        "r0", "r1", "r2", "r2_command", "r3", "buckets", "candidates", "unresolved",
    }
    if set(summary) != expected_fields:
        raise ValueError("complete pilot summary has missing or unknown fields")
    if (
        summary.get("schema") != "assay-pilot-summary/2"
        or summary.get("qualifying") is not False
        or summary.get("completed") is not True
        or summary.get("lane") != "self-qualification"
        or summary.get("commit") != expected_commit
        or type(summary.get("requested")) is not int
        or summary["requested"] != len(selected_ids)
        or summary.get("selection_sha256") != selection_sha256
        or summary.get("candidates_file_sha256") != candidates_sha256
        or summary.get("state_dir") != str(expected_state_dir)
        or summary.get("r0") != "PASS"
        or summary.get("r1") != "PASS"
        or summary.get("r3") != "not-run: pilot"
        or summary.get("unresolved") != []
    ):
        raise ValueError("pilot summary is not a complete source-bound non-qualifying measurement")
    r2 = summary.get("r2")
    if (
        not isinstance(r2, dict)
        or r2.get("status") not in {
            "PASS", "FAIL", "ERROR", "BUDGET_EXCEEDED", "INCONCLUSIVE"
        }
        or r2.get("reason_code") == "LANE_TIMEOUT"
    ):
        raise ValueError("pilot summary does not contain a completed R2 result")
    r2_baselines = pilot_r2_evidence.validate_r2_command(
        summary.get("r2_command"),
        repo_root=repo_root,
        expected_commit=expected_commit,
        expected_lane="self-qualification",
        manifest_path=r2_manifest_path,
    )
    if type(summary.get("jobs")) is not int or summary["jobs"] != 3:
        raise ValueError("pilot summary job count does not match the registered three-job pilot")
    summary_judge_sha256 = _require_sha(
        summary.get("judge_sha256"), "pilot summary judge_sha256"
    )

    plan_by_id = {row["id"]: row for row in rows}
    candidate_rows = summary.get("candidates")
    if not isinstance(candidate_rows, list) or len(candidate_rows) != len(selected_ids):
        raise ValueError("pilot summary does not contain one disposition per selected candidate")
    by_id: dict[str, dict[str, Any]] = {}
    bucket_counts = {name: 0 for name in MUTATION_BUCKETS}
    for expected_id, outcome in zip(selected_ids, candidate_rows, strict=True):
        plan_row = plan_by_id[expected_id]
        if not isinstance(outcome, dict) or set(outcome) != {
            "id", "path", "operator", "bucket", "execution_mode"
        }:
            raise ValueError(f"pilot disposition for {expected_id} is malformed")
        if (
            outcome.get("id") != expected_id
            or outcome.get("path") != plan_row["path"]
            or outcome.get("operator") != plan_row["operator"]
            or outcome.get("bucket") not in bucket_counts
            or not isinstance(outcome.get("execution_mode"), str)
            or not outcome["execution_mode"]
        ):
            raise ValueError(f"pilot disposition for {expected_id} does not match the plan")
        bucket_counts[outcome["bucket"]] += 1
        by_id[expected_id] = outcome
    reported_buckets = summary.get("buckets")
    if (
        not isinstance(reported_buckets, dict)
        or set(reported_buckets) != set(bucket_counts)
        or any(type(value) is not int for value in reported_buckets.values())
        or reported_buckets != bucket_counts
    ):
        raise ValueError("pilot summary bucket counts do not match its candidate dispositions")
    if bucket_counts["crashed"]:
        expected_r2 = {"status": "ERROR", "reason_code": "EXEC_FAILED"}
    elif bucket_counts["budget_exceeded"]:
        expected_r2 = {"status": "BUDGET_EXCEEDED", "reason_code": "LANE_TIMEOUT"}
    elif bucket_counts["hung"]:
        expected_r2 = {"status": "BUDGET_EXCEEDED", "reason_code": "CANDIDATE_HUNG"}
    elif bucket_counts["survived"]:
        expected_r2 = {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"}
    elif bucket_counts["killed"] == 0 and bucket_counts["equivalent"]:
        expected_r2 = {"status": "INCONCLUSIVE", "reason_code": "ALL_MUTANTS_EQUIVALENT"}
    elif sum(bucket_counts.values()) == 0:
        expected_r2 = {"status": "INCONCLUSIVE", "reason_code": "NO_MUTANTS"}
    else:
        expected_r2 = {"status": "PASS", "reason_code": None}
    if r2 != expected_r2:
        raise ValueError("pilot R2 result differs from the validated candidate outcomes")
    return by_id, summary_judge_sha256, r2_baselines


def _verify_state(
    state_dir: Path,
    *,
    selected_ids: list[str],
    rows: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    selection_sha256: str,
    deadline_sha256: str,
    expected_judge_sha256: str,
    replacement_sha256_by_id: dict[str, str],
    r2_baselines: dict[str, Any],
) -> tuple[list[str], dict[str, str], dict[str, dict[str, Any]]]:
    _require_directory(state_dir)
    expected_names = {"PILOT-STATE", *(f"{identity}.json" for identity in selected_ids)}
    try:
        entries = list(os.scandir(state_dir))
    except OSError as exc:
        raise ValueError(f"cannot inspect pilot state directory: {exc}") from exc
    observed_names: set[str] = set()
    for entry in entries:
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            raise ValueError(f"pilot state entry {entry.name!r} is not a regular file")
        observed_names.add(entry.name)
    if observed_names != expected_names:
        missing = sorted(expected_names - observed_names)
        extra = sorted(observed_names - expected_names)
        raise ValueError(f"pilot state inventory differs (missing={missing}, extra={extra})")

    sentinel, sentinel_raw = _read_json(state_dir / "PILOT-STATE", limit=1024 * 1024)
    if sentinel != {
        "schema": "assay-pilot-state/1",
        "selection_sha256": selection_sha256,
        "lane": "self-qualification",
    }:
        raise ValueError("PILOT-STATE does not bind the completed selection and lane")

    plan_by_id = {row["id"]: row for row in rows}
    judge_digests: set[str] = set()
    records: dict[str, dict[str, Any]] = {}
    state_hashes = {"PILOT-STATE": hashlib.sha256(sentinel_raw).hexdigest()}
    for identity in selected_ids:
        record, record_raw = _read_json(
            state_dir / f"{identity}.json", limit=_LIMITS["state"]
        )
        state_hashes[f"{identity}.json"] = hashlib.sha256(record_raw).hexdigest()
        plan_row = plan_by_id[identity]
        outcome = dispositions[identity]
        if not isinstance(record, dict):
            raise ValueError(f"mutation state record {identity} is not an object")
        if (
            record.get("candidate_id") != identity
            or record.get("path") != plan_row["path"]
            or record.get("operator") != plan_row["operator"]
            or type(record.get("start_byte")) is not int
            or record.get("start_byte") != plan_row["start_byte"]
            or type(record.get("end_byte")) is not int
            or record.get("end_byte") != plan_row["end_byte"]
            or type(record.get("lineno")) is not int
            or record.get("lineno") != plan_row["lineno"]
            or not isinstance(record.get("description"), str)
            or record.get("description") != plan_row["description"]
            or record.get("source_sha256") != plan_row["source_sha256"]
            or record.get("mutated_file_sha256") != plan_row.get("mutated_file_sha256")
            or record.get("outcome_bucket") != outcome["bucket"]
            or record.get("campaign_deadline_sha256") != deadline_sha256
        ):
            raise ValueError(
                f"mutation state record {identity} is stale or disagrees with the source plan or summary"
            )
        if record.get("schema_version") != MUTATION_STATE_SCHEMA_VERSION:
            raise ValueError(f"mutation state record {identity} has a stale schema version")
        judge_sha256 = _require_sha(record.get("judge_sha256"), f"record {identity} judge_sha256")
        judge_digests.add(judge_sha256)
        if judge_sha256 != expected_judge_sha256:
            raise ValueError(f"mutation state record {identity} belongs to a different judge")
        replacement_sha256 = _require_sha(
            record.get("replacement_sha256"), f"record {identity} replacement_sha256"
        )
        if replacement_sha256 != replacement_sha256_by_id[identity]:
            raise ValueError(f"mutation state record {identity} has a different replacement identity")
        _require_sha(record.get("mutated_file_sha256"), f"record {identity} mutated_file_sha256")
        resources = record.get("resource_limit_evidence")
        if not isinstance(resources, dict):
            raise ValueError(f"mutation state record {identity} has no resource-limit evidence")
        try:
            resource_evidence = ResourceLimitEvidence.from_dict(resources)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"mutation state record {identity} has invalid resource-limit evidence: {exc}") from exc
        if resource_evidence.limit_hit:
            raise ValueError(f"mutation state record {identity} records a process or memory limit event")
        if not isinstance(record.get("execution"), dict):
            raise ValueError(f"mutation state record {identity} has no execution evidence")
        try:
            execution = _execution_from_state_record(record)
            terminal_matches = _terminal_result_matches_bucket(
                record,
                cold_witness=True,
                resource_limit_hit=resource_evidence.limit_hit,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"mutation state record {identity} has invalid terminal or execution evidence: {exc}") from exc
        if not terminal_matches or execution.mode != outcome["execution_mode"]:
            raise ValueError(f"mutation state record {identity} does not validate its reported disposition")
        if outcome["bucket"] == "hung" and not valid_hung_resource_evidence(
            record.get("liveness_resource_evidence")
        ):
            raise ValueError(
                f"mutation state record {identity} is missing a valid time-aligned hang resource trace"
            )
        raw_evidence = record.get("evidence")
        if raw_evidence is not None:
            if not isinstance(raw_evidence, dict) or set(raw_evidence) != {
                "command",
                "collection_count",
                "collection_sha256",
                "hook_fingerprint_sha256",
                "started_count",
                "failed_call_index",
            }:
                raise ValueError(f"mutation state record {identity} has malformed execution evidence")
            try:
                MutantEvidence(**raw_evidence)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"mutation state record {identity} has invalid execution evidence: {exc}") from exc
            pilot_r2_evidence.validate_candidate_evidence(
                raw_evidence,
                baselines=r2_baselines,
                candidate_id=identity,
            )
        if (
            outcome["bucket"] in {"killed", "survived"}
            or execution.mode == "witness-cold"
        ) and raw_evidence is None:
            raise ValueError(f"mutation state record {identity} is missing required collection evidence")
        if outcome["bucket"] != "killed" and (
            execution.mode != "full" or execution.witness is not None
        ):
            raise ValueError(
                f"mutation state record {identity} non-kill must use full execution "
                "without a witness"
            )
        if outcome["bucket"] == "killed":
            pilot_r2_evidence.validate_kill_witness(
                record.get("execution"),
                raw_evidence,
                nodes=r2_baselines["nodes"],
                candidate_id=identity,
            )
        pilot_r2_evidence.validate_state_measurements(
            record,
            candidate_id=identity,
        )
        records[identity] = record
    if len(judge_digests) != 1:
        raise ValueError("selected mutation state records do not share one judge identity")
    return sorted(expected_names), state_hashes, records


def _verify_progress(
    progress_path: Path,
    *,
    expected_commit: str,
    selection_sha256: str,
    selected_ids: set[str],
    selected_order: list[str],
    plan_rows: list[dict[str, Any]],
    plan_total: int,
    dispositions: dict[str, dict[str, Any]],
    summary: dict[str, Any],
    expected_judge_sha256: str,
) -> dict[str, Any]:
    raw = _read_regular(progress_path, limit=_LIMITS["progress"])
    if not raw.endswith(b"\n"):
        raise ValueError("pilot progress stream ends with a partial record")
    event_count = raw.count(b"\n")
    if event_count > _MAX_PROGRESS_EVENTS:
        raise ValueError(
            f"pilot progress has more than {_MAX_PROGRESS_EVENTS} events"
        )
    events: list[dict[str, Any]] = []
    for number, line in enumerate(raw[:-1].split(b"\n"), start=1):
        if len(line) > _MAX_PROGRESS_LINE_BYTES:
            raise ValueError(
                f"pilot progress line {number} exceeds {_MAX_PROGRESS_LINE_BYTES} bytes"
            )
        try:
            event = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"pilot progress line {number} is malformed: {exc}") from exc
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError(f"pilot progress line {number} is not an event object")
        if event["event"] in {
            "run", "candidates", "candidate", "resume", "resume_merged"
        } and not _valid_progress_timing(event):
            raise ValueError(f"pilot progress line {number} has malformed event timing")
        events.append(event)
    starts = [
        index
        for index, event in enumerate(events)
        if event.get("event") == "run"
        and event.get("lane") == "self-qualification"
        and event.get("commit") == expected_commit
    ]
    if not starts or any(
        event.get("event") == "run" for event in events[starts[-1] + 1 :]
    ):
        raise ValueError("pilot progress does not end at the expected source run")
    current = events[starts[-1] :]
    if (
        current[0].get("lane") != "self-qualification"
        or current[0].get("commit") != expected_commit
        or current[0].get("rigor") != _PILOT_RIGOR
    ):
        raise ValueError("pilot progress run header does not bind the expected R2 source run")
    verdict_events = [event for event in current if event.get("event") == "verdict_written"]
    if len(verdict_events) != 1:
        raise ValueError("pilot progress must contain exactly one terminal verdict event")
    terminal = current[-1]
    if terminal is not verdict_events[0]:
        raise ValueError("pilot progress has no terminal exit-6 verdict event")
    if set(terminal) != {
        "event", "outcome", "reason_code", "exit_code", "destination",
        "emitted_at", "elapsed_s",
    }:
        raise ValueError("pilot terminal verdict event has missing or unknown fields")
    summary_r2 = summary.get("r2")
    if (
        not isinstance(summary_r2, dict)
        or terminal.get("outcome") != summary_r2.get("status")
        or terminal.get("reason_code") != summary_r2.get("reason_code")
    ):
        raise ValueError("pilot terminal verdict differs from the validated summary")
    if (
        type(terminal.get("exit_code")) is not int
        or terminal["exit_code"] != 6
        or terminal.get("destination") is not None
        or not _is_aware_timestamp(terminal.get("emitted_at"))
        or type(terminal.get("elapsed_s")) not in (int, float)
        or not math.isfinite(terminal["elapsed_s"])
        or terminal["elapsed_s"] < 0
    ):
        raise ValueError("pilot progress has no terminal exit-6 verdict event")
    ends = [event for event in current if event.get("event") == "end"]
    if len(ends) != 1 or ends[0].get("reason") is not None:
        raise ValueError("pilot progress does not contain one completed R2 sweep")
    end = ends[0]
    if set(end) != {
        "event", "candidate_total", "buckets", "reason", "emitted_at", "elapsed_s",
    }:
        raise ValueError("pilot progress end event has missing or unknown fields")
    if (
        not _is_aware_timestamp(end.get("emitted_at"))
        or type(end.get("elapsed_s")) not in (int, float)
        or not math.isfinite(end["elapsed_s"])
        or end["elapsed_s"] < 0
    ):
        raise ValueError("pilot progress end event has malformed timing")
    candidates_events = [event for event in current if event.get("event") == "candidates"]
    if len(candidates_events) != 1:
        raise ValueError("pilot progress does not contain exactly one candidates identity event")
    end_position = next(index for index, event in enumerate(current) if event is ends[0])
    candidates_position = next(
        index for index, event in enumerate(current) if event is candidates_events[0]
    )
    candidate_positions = [
        index for index, event in enumerate(current) if event.get("event") == "candidate"
    ]
    if end_position <= candidates_position or any(
        position <= candidates_position or position >= end_position
        for position in candidate_positions
    ):
        raise ValueError("pilot mutation end event precedes its candidate dispositions")
    candidates_meta = candidates_events[0]
    pilot_total = len(selected_ids)
    if candidates_meta.get("commit") != expected_commit:
        raise ValueError("pilot candidates event does not bind the expected commit")
    judge_sha256 = _require_sha(candidates_meta.get("judge_sha256"), "pilot candidates judge_sha256")
    if judge_sha256 != expected_judge_sha256:
        raise ValueError("pilot progress judge identity differs from the in-memory summary")
    if candidates_meta.get("selection_sha256") != selection_sha256:
        raise ValueError("pilot candidates event selection digest differs from the verified selection")
    if (
        type(candidates_meta.get("candidate_total")) is not int
        or candidates_meta["candidate_total"] != pilot_total
        or type(candidates_meta.get("selected_total")) is not int
        or candidates_meta["selected_total"] != len(selected_ids)
        or type(candidates_meta.get("pending_total")) is not int
        or candidates_meta["pending_total"] < 0
    ):
        raise ValueError("pilot candidates event totals disagree with the source plan and selection")
    baseline_events = [
        event for event in current if event.get("event") == "baseline"
    ]
    if len(baseline_events) != 1:
        raise ValueError("pilot progress does not contain exactly one baseline event")
    baseline = baseline_events[0]
    baseline_position = next(
        index for index, event in enumerate(current) if event is baseline
    )
    expected_baseline = {
        "candidate_index": -1,
        "candidate_total": candidates_meta["candidate_total"],
        "event": "baseline",
        "path": ".",
        "operator": "baseline",
        "start_byte": 0,
        "end_byte": 0,
        "mutated_file_sha256": "",
    }
    baseline_timing = {"emitted_at", "elapsed_s"}
    if (
        set(baseline) != set(expected_baseline) | baseline_timing
        or any(baseline.get(key) != value for key, value in expected_baseline.items())
        or not _is_aware_timestamp(baseline.get("emitted_at"))
        or type(baseline.get("elapsed_s")) not in (int, float)
        or not math.isfinite(baseline["elapsed_s"])
        or baseline["elapsed_s"] < 0
        or baseline_position <= candidates_position
        or baseline_position >= end_position
        or any(position <= baseline_position for position in candidate_positions)
    ):
        raise ValueError("pilot progress baseline event is missing, malformed, or out of order")
    resume_events = [event for event in current if event.get("event") == "resume"]
    merged_events = [event for event in current if event.get("event") == "resume_merged"]
    if len(resume_events) > 1 or len(merged_events) > 1:
        raise ValueError("pilot progress contains duplicate resume accounting events")
    if resume_events:
        resume_position = next(index for index, event in enumerate(current) if event is resume_events[0])
        if resume_position >= candidates_position:
            raise ValueError("pilot resume event follows its candidates identity event")
    if merged_events:
        merged_position = next(index for index, event in enumerate(current) if event is merged_events[0])
        if merged_position >= end_position or merged_position <= baseline_position:
            raise ValueError("pilot resume_merged event is outside the completed sweep")
        if candidate_positions and merged_position <= max(candidate_positions):
            raise ValueError("pilot resume_merged event precedes its pending candidates")
    pending_total = candidates_meta["pending_total"]
    resumed_total = rejected_total = rejudged_total = 0
    if resume_events:
        resume = resume_events[0]
        if type(resume.get("candidate_total")) is not int or resume["candidate_total"] != pilot_total:
            raise ValueError("pilot resume event total differs from the selected candidates")
        for field in ("resumed_total", "rejected_total", "rejudged_total"):
            value = resume.get(field)
            if type(value) is not int or value < 0:
                raise ValueError(f"pilot resume event has invalid {field}")
        resumed_total = resume["resumed_total"]
        rejected_total = resume["rejected_total"]
        rejudged_total = resume["rejudged_total"]
        if (
            resumed_total + pending_total != len(selected_ids)
            or rejected_total + rejudged_total > pending_total
        ):
            raise ValueError("pilot resume totals do not partition the selected candidates")
    elif pending_total != len(selected_ids):
        raise ValueError("pilot pending total omits candidates without resume accounting")
    if (resumed_total > 0) != (len(merged_events) == 1):
        raise ValueError("pilot resume_merged event does not match its resumed candidate count")
    if merged_events and (
        type(merged_events[0].get("resumed_total")) is not int
        or merged_events[0]["resumed_total"] != resumed_total
    ):
        raise ValueError("pilot resume_merged count differs from the resume event")

    observed_candidates: set[str] = set()
    observed_indexes: set[int] = set()
    current_candidate_events: dict[str, dict[str, Any]] = {}
    rows_by_id = {row["id"]: row for row in plan_rows}

    def require_candidate_matches_plan(
        event: dict[str, Any], identity: str, *, context: str
    ) -> None:
        row = rows_by_id[identity]
        for field in (
            "path",
            "operator",
            "start_byte",
            "end_byte",
            "lineno",
            "description",
            "mutated_file_sha256",
        ):
            expected = row[field]
            observed = event.get(field)
            if type(observed) is not type(expected) or observed != expected:
                raise ValueError(
                    f"{context} identity for {identity} differs from the source plan"
                )

    for event in current:
        if event.get("event") != "candidate":
            continue
        identity = event.get("candidate_id")
        if not isinstance(identity, str) or identity not in selected_ids or identity in observed_candidates:
            raise ValueError("pilot progress contains an unknown or duplicate candidate disposition")
        observed_candidates.add(identity)
        current_candidate_events[identity] = event
        index = event.get("candidate_index")
        if type(index) is not int or index < 0 or index >= pending_total or index in observed_indexes:
            raise ValueError("pilot progress contains an invalid or duplicate pending candidate index")
        observed_indexes.add(index)
        if type(event.get("candidate_total")) is not int or event["candidate_total"] != pending_total:
            raise ValueError(f"pilot progress identity for {identity} differs from the source plan")
        require_candidate_matches_plan(
            event, identity, context="pilot progress"
        )
        if (
            event.get("outcome_bucket") != dispositions[identity]["bucket"]
            or event.get("execution_mode") != dispositions[identity]["execution_mode"]
        ):
            raise ValueError(f"pilot progress disposition for {identity} differs from summary")
        evidence = event.get("resource_limit_evidence")
        if not isinstance(evidence, dict):
            raise ValueError(f"pilot progress disposition for {identity} lacks resource-limit evidence")
        try:
            resource_evidence = ResourceLimitEvidence.from_dict(evidence)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"pilot progress disposition for {identity} has invalid resource evidence: {exc}") from exc
        if resource_evidence.limit_hit:
            raise ValueError(f"pilot progress disposition for {identity} records a process or memory limit event")
    if len(observed_candidates) != pending_total or observed_indexes != set(range(pending_total)):
        raise ValueError("pilot progress does not report every pending candidate exactly once")
    end = ends[0]
    end_buckets = end.get("buckets")
    if (
        type(end.get("candidate_total")) is not int
        or end["candidate_total"] != pilot_total
        or not isinstance(end_buckets, dict)
        or set(end_buckets) != set(MUTATION_BUCKETS)
        or any(type(value) is not int or value < 0 for value in end_buckets.values())
    ):
        raise ValueError("pilot progress end totals disagree with the selected candidates")
    pilot_r2_evidence.validate_pilot_terminal_event(
        terminal, context="pilot terminal verdict", end_buckets=end_buckets
    )

    # Each reused state record must have a prior progress disposition from a
    # run with the same source and judge. This ties resume state to observable
    # evidence and lets the complete summary be checked across both segments.
    previous_by_id: dict[str, dict[str, Any]] = {}
    boundaries = [index for index, event in enumerate(events) if event.get("event") == "run"]
    for segment_index, segment_start in enumerate(boundaries):
        segment_end = boundaries[segment_index + 1] if segment_index + 1 < len(boundaries) else len(events)
        if segment_start >= starts[-1]:
            break
        segment = events[segment_start:segment_end]
        run = segment[0]
        if (
            run.get("lane") != "self-qualification"
            or run.get("commit") != expected_commit
            or run.get("rigor") != _PILOT_RIGOR
        ):
            continue
        meta = [event for event in segment if event.get("event") == "candidates"]
        if len(meta) != 1:
            continue
        candidates_meta = meta[0]
        if (
            candidates_meta.get("commit") != expected_commit
            or candidates_meta.get("judge_sha256") != judge_sha256
            or candidates_meta.get("selection_sha256") != selection_sha256
        ):
            continue
        prior_total = candidates_meta.get("candidate_total")
        prior_selected = candidates_meta.get("selected_total")
        prior_pending = candidates_meta.get("pending_total")
        if (
            type(prior_total) is not int
            or prior_total != len(selected_ids)
            or type(prior_selected) is not int
            or prior_selected != len(selected_ids)
            or type(prior_pending) is not int
            or prior_pending < 0
            or prior_pending > len(selected_ids)
        ):
            raise ValueError("prior pilot candidates event totals are invalid")

        meta_position = next(index for index, event in enumerate(segment) if event is candidates_meta)
        end_positions = [
            index for index, event in enumerate(segment)
            if event.get("event") == "end"
        ]
        verdict_positions = [
            index for index, event in enumerate(segment)
            if event.get("event") == "verdict_written"
        ]
        candidate_positions = [
            index for index, event in enumerate(segment)
            if event.get("event") == "candidate"
        ]
        terminal_boundary = min(
            [*end_positions, *verdict_positions], default=len(segment)
        )
        if any(index >= terminal_boundary for index in candidate_positions):
            raise ValueError("prior pilot candidate follows its end or terminal event")
        if (
            len(end_positions) > 1
            or len(verdict_positions) > 1
            or (verdict_positions and not end_positions)
            or (
                end_positions
                and verdict_positions
                and end_positions[0] > verdict_positions[0]
            )
        ):
            raise ValueError("prior pilot progress has malformed terminal ordering")
        if verdict_positions and verdict_positions[0] != len(segment) - 1:
            raise ValueError("prior pilot progress has malformed terminal ordering")
        prior_end: dict[str, Any] | None = None
        if end_positions:
            prior_end = segment[end_positions[0]]
            if set(prior_end) != {
                "event", "candidate_total", "buckets", "reason", "emitted_at", "elapsed_s",
            } or not _valid_progress_timing(prior_end):
                raise ValueError("prior pilot end event is malformed")
            if (
                type(prior_end.get("candidate_total")) is not int
                or prior_end["candidate_total"] != prior_total
            ):
                raise ValueError("prior pilot end total differs from its candidates event")
            end_buckets = prior_end.get("buckets")
            if (
                not isinstance(end_buckets, dict)
                or set(end_buckets) != set(MUTATION_BUCKETS)
                or any(type(value) is not int or value < 0 for value in end_buckets.values())
            ):
                raise ValueError("prior pilot end buckets are malformed")
            if prior_end.get("reason") is not None and (
                not isinstance(prior_end["reason"], str) or not prior_end["reason"]
            ):
                raise ValueError("prior pilot end reason is malformed")
            if prior_end.get("reason") is not None:
                raise ValueError("prior pilot refused end event contradicts its candidates event")

        prior_resume_events = [event for event in segment if event.get("event") == "resume"]
        prior_merged_events = [event for event in segment if event.get("event") == "resume_merged"]
        if len(prior_resume_events) > 1 or len(prior_merged_events) > 1:
            raise ValueError("prior pilot progress has duplicate resume accounting events")
        prior_resumed_total = 0
        if prior_resume_events:
            prior_resume = prior_resume_events[0]
            if set(prior_resume) != {
                "event", "candidate_total", "resumed_total", "rejected_total",
                "rejudged_total", "emitted_at", "elapsed_s",
            } or not _valid_progress_timing(prior_resume):
                raise ValueError("prior pilot resume event is malformed")
            if prior_resume.get("candidate_total") != prior_total:
                raise ValueError("prior pilot resume total differs from its candidates event")
            for field in ("resumed_total", "rejected_total", "rejudged_total"):
                if type(prior_resume.get(field)) is not int or prior_resume[field] < 0:
                    raise ValueError(f"prior pilot resume event has invalid {field}")
            prior_resumed_total = prior_resume["resumed_total"]
            if (
                prior_resumed_total + prior_pending != prior_total
                or prior_resume["rejected_total"] + prior_resume["rejudged_total"] > prior_pending
            ):
                raise ValueError("prior pilot resume totals do not partition the selected candidates")
            if next(index for index, event in enumerate(segment) if event is prior_resume) >= meta_position:
                raise ValueError("prior pilot resume event follows its candidates identity event")
        elif prior_pending != prior_total:
            raise ValueError("prior pilot pending total omits candidates without resume accounting")
        if (
            prior_resumed_total == 0 and prior_merged_events
        ) or (
            prior_resumed_total > 0
            and prior_end is not None
            and len(prior_merged_events) != 1
        ):
            raise ValueError("prior pilot resume_merged event does not match completed resume accounting")
        if prior_merged_events:
            prior_merged = prior_merged_events[0]
            if set(prior_merged) != {"event", "resumed_total", "emitted_at", "elapsed_s"} or (
                prior_merged.get("resumed_total") != prior_resumed_total
                or not _valid_progress_timing(prior_merged)
            ):
                raise ValueError("prior pilot resume_merged event is malformed")
            merged_position = next(index for index, event in enumerate(segment) if event is prior_merged)
            if (
                merged_position <= meta_position
                or (candidate_positions and merged_position <= max(candidate_positions))
                or (end_positions and merged_position >= end_positions[0])
            ):
                raise ValueError("prior pilot resume_merged event is out of order")
        if verdict_positions:
            prior_verdict = segment[verdict_positions[0]]
            if set(prior_verdict) != {
                "event", "outcome", "reason_code", "exit_code", "destination",
                "emitted_at", "elapsed_s",
            } or (
                not isinstance(prior_verdict.get("outcome"), str)
                or (
                    prior_verdict.get("reason_code") is not None
                    and not isinstance(prior_verdict.get("reason_code"), str)
                )
                or type(prior_verdict.get("exit_code")) is not int
                or not _valid_progress_timing(prior_verdict)
            ):
                raise ValueError("prior pilot terminal verdict is malformed")
        baseline_positions = [
            index for index, event in enumerate(segment)
            if event.get("event") == "baseline"
        ]
        if (
            len(baseline_positions) != 1
            or baseline_positions[0] <= meta_position
            or any(index <= baseline_positions[0] for index in candidate_positions)
        ):
            raise ValueError("prior pilot candidates are not ordered after their baseline")

        prior_dispositions = set(previous_by_id)
        prior_ids: set[str] = set()
        prior_indexes: set[int] = set()
        for index in candidate_positions:
            event = segment[index]
            identity = event.get("candidate_id")
            candidate_index = event.get("candidate_index")
            if (
                not isinstance(identity, str)
                or identity not in selected_ids
                or identity in prior_ids
                or type(candidate_index) is not int
                or candidate_index < 0
                or candidate_index >= prior_pending
                or candidate_index in prior_indexes
                or type(event.get("candidate_total")) is not int
                or event["candidate_total"] != prior_pending
                or event.get("outcome_bucket") not in MUTATION_BUCKETS
                or not isinstance(event.get("execution_mode"), str)
                or not event["execution_mode"]
            ):
                raise ValueError("prior pilot candidate event has invalid identity or totals")
            require_candidate_matches_plan(
                event, identity, context="prior pilot progress"
            )
            prior_ids.add(identity)
            prior_indexes.add(candidate_index)
            previous_by_id[identity] = event

        pilot_r2_evidence.validate_pilot_resume_queue(
            selected_order=selected_order,
            prior_dispositions=prior_dispositions,
            candidate_events=[segment[index] for index in candidate_positions],
            pending_total=prior_pending,
            resumed_total=prior_resumed_total,
            context="prior pilot resume queue",
        )

        if prior_end is not None:
            prior_history_buckets = {
                identity: event["outcome_bucket"]
                for identity, event in previous_by_id.items()
                if identity not in prior_ids
            }
            pilot_r2_evidence.validate_pilot_end_accounting(
                selected_order=selected_order,
                candidate_events=[segment[index] for index in candidate_positions],
                prior_buckets=prior_history_buckets,
                pending_total=prior_pending,
                resumed_total=prior_resumed_total,
                end_buckets=prior_end["buckets"],
                context="incomplete prior pilot end",
            )
            missing_pending = prior_pending - len(candidate_positions)
            if missing_pending == 0:
                if prior_indexes != set(range(prior_pending)):
                    raise ValueError(
                        "completed prior pilot candidate indexes do not cover the pending queue"
                    )
                merged_buckets = {name: 0 for name in MUTATION_BUCKETS}
                for identity in selected_ids:
                    event = previous_by_id.get(identity)
                    if event is None:
                        raise ValueError(
                            "prior pilot end has no disposition for a selected candidate"
                        )
                    merged_buckets[event["outcome_bucket"]] += 1
                if prior_end["buckets"] != merged_buckets:
                    raise ValueError(
                        "prior pilot end buckets disagree with its merged candidate events"
                    )
            if verdict_positions:
                pilot_r2_evidence.validate_pilot_terminal_event(
                    prior_verdict,
                    context="prior pilot terminal verdict",
                    end_buckets=prior_end["buckets"],
                )
            if verdict_positions and prior_verdict["exit_code"] == 6 and missing_pending:
                raise ValueError(
                    "prior pilot terminal claims completion with missing candidate events"
                )

    pilot_r2_evidence.validate_pilot_resume_queue(
        selected_order=selected_order,
        prior_dispositions=set(previous_by_id),
        candidate_events=[current[index] for index in candidate_positions],
        pending_total=pending_total,
        resumed_total=resumed_total,
        context="current pilot resume queue",
    )

    merged_buckets = {name: 0 for name in MUTATION_BUCKETS}
    for identity in selected_ids:
        event = current_candidate_events.get(identity) or previous_by_id.get(identity)
        if event is None:
            raise ValueError(
                "pilot end has no same-selection progress disposition for a selected candidate"
            )
        merged_buckets[event["outcome_bucket"]] += 1
    if end["buckets"] != merged_buckets:
        raise ValueError("pilot progress end buckets disagree with its merged candidate events")

    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "judge_sha256": judge_sha256,
        "resumed_ids": selected_ids - observed_candidates,
        "previous_candidates": previous_by_id,
        "current_candidates": current_candidate_events,
        "pending_total": pending_total,
        "resumed_total": resumed_total,
    }


def _verify_resumed_progress(
    progress_facts: dict[str, Any],
    *,
    state_records: dict[str, dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
) -> None:
    resumed_ids = progress_facts["resumed_ids"]
    if len(resumed_ids) != progress_facts["resumed_total"]:
        raise ValueError("pilot resumed candidate IDs do not match the resume event count")
    previous = progress_facts["previous_candidates"]
    for identity in state_records:
        event = progress_facts["current_candidates"].get(identity)
        if event is None:
            event = previous.get(identity)
        if event is None:
            raise ValueError(f"candidate {identity} has no same-selection progress disposition")
        record = state_records[identity]
        if (
            event.get("outcome_bucket") != record.get("outcome_bucket")
            or event.get("outcome_bucket") != dispositions[identity]["bucket"]
            or event.get("execution_mode") != dispositions[identity]["execution_mode"]
            or event.get("mutated_file_sha256") != record.get("mutated_file_sha256")
        ):
            raise ValueError(f"candidate {identity} progress disagrees with its state record")
        try:
            progress_resources = ResourceLimitEvidence.from_dict(event["resource_limit_evidence"])
            state_resources = ResourceLimitEvidence.from_dict(record["resource_limit_evidence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"candidate {identity} has invalid resource evidence: {exc}") from exc
        if progress_resources.to_dict() != state_resources.to_dict():
            raise ValueError(f"candidate {identity} resource evidence differs from its state record")
        progress_measurements = pilot_r2_evidence.validate_progress_measurements(
            event,
            candidate_id=identity,
        )
        state_measurements = pilot_r2_evidence.validate_state_measurements(
            record,
            candidate_id=identity,
        )
        if any(
            progress_measurements[field] != state_measurements[field]
            for field in (
                "cpu_seconds", "peak_rss_bytes", "phase_seconds", "startup_seconds"
            )
        ):
            raise ValueError(f"candidate {identity} cost measurements differ from its state record")
        if record.get("outcome_bucket") == "hung":
            progress_trace = event.get("liveness_resource_evidence")
            if (
                not valid_hung_resource_evidence(progress_trace)
                or progress_trace != record.get("liveness_resource_evidence")
            ):
                raise ValueError(
                    f"hung candidate {identity} progress trace differs from its validated state record"
                )


def _write_manifest(
    files: dict[str, Path],
    manifest_path: Path,
    validated_hashes: dict[str, str],
    *,
    manifest_dir_fd: int | None = None,
    manifest_name: str | None = None,
) -> str:
    lines: list[str] = []
    for relative, path in sorted(files.items()):
        content = _read_regular(path, limit=max(_LIMITS.values()))
        digest = hashlib.sha256(content).hexdigest()
        if validated_hashes.get(relative) != digest:
            raise ValueError(f"{relative} changed after its content was validated")
        lines.append(f"{digest}  {relative}\n")
    manifest = "".join(lines).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    if manifest_dir_fd is None:
        descriptor = os.open(manifest_path, flags, 0o600)
    else:
        if manifest_name is None:
            raise ValueError("pinned manifest publication requires a file name")
        descriptor = os.open(manifest_name, flags, 0o600, dir_fd=manifest_dir_fd)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(manifest)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    return hashlib.sha256(manifest).hexdigest()


def verify_pilot(args: argparse.Namespace) -> str:
    if _HEX40.fullmatch(args.expected_commit) is None or _HEX40.fullmatch(args.expected_tree) is None:
        raise ValueError("expected commit and tree must be full lowercase Git IDs")
    wheel_sha256 = _require_sha(args.expected_wheel_sha256, "expected wheel_sha256")
    repo_root = args.repo_root.resolve(strict=True)
    project_root = args.project_root.resolve(strict=True)
    pinned = args.assay_fd is not None
    project_fd = assay_fd = state_fd = -1
    try:
        if pinned:
            expected_assay = (args.expected_assay_device, args.expected_assay_inode)
            assay_info = os.fstat(args.assay_fd)
            if (
                not stat.S_ISDIR(assay_info.st_mode)
                or (assay_info.st_dev, assay_info.st_ino) != expected_assay
            ):
                raise ValueError("pinned .assay directory differs from host admission")
            project_fd = os.open(
                project_root,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            visible_assay = os.stat(".assay", dir_fd=project_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(visible_assay.st_mode)
                or (visible_assay.st_dev, visible_assay.st_ino) != expected_assay
            ):
                raise ValueError("visible .assay path differs from the pinned admitted directory")
            artifact_dir = Path(f"/proc/self/fd/{args.assay_fd}")
            _require_directory(artifact_dir)
            state_name = "b110-pilot-state"
            if Path(args.state_dir).name != state_name:
                raise ValueError("pinned pilot state must be the b110-pilot-state child of .assay")
            state_fd = os.open(
                state_name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=args.assay_fd,
            )
            state_info = os.fstat(state_fd)
            state_dir = Path(f"/proc/self/fd/{state_fd}")
            _require_directory(state_dir)
            supplied_artifact_dir = Path(
                os.path.normpath(os.path.abspath(os.fspath(args.artifact_dir)))
            )
            supplied_state_dir = Path(
                os.path.normpath(os.path.abspath(os.fspath(args.state_dir)))
            )
            if supplied_state_dir.parent != supplied_artifact_dir:
                raise ValueError(
                    "supplied pilot state path is not a child of the pinned .assay path"
                )
            try:
                supplied_state_info = os.stat(
                    supplied_state_dir, follow_symlinks=True
                )
            except OSError as exc:
                raise ValueError(
                    "supplied pilot state path does not name the pinned state directory"
                ) from exc
            if (
                not stat.S_ISDIR(supplied_state_info.st_mode)
                or (supplied_state_info.st_dev, supplied_state_info.st_ino)
                != (state_info.st_dev, state_info.st_ino)
            ):
                raise ValueError(
                    "supplied pilot state path does not name the pinned state directory"
                )
            deadline_name = Path(args.deadline).name
            expected_deadline_name = f"campaign-deadline-{args.campaign}.json"
            if deadline_name != expected_deadline_name:
                raise ValueError("deadline name does not match the requested pilot campaign")
            deadline_path = artifact_dir / deadline_name
            # Assay records the requested proc-fd spelling for this state
            # directory. Validate that spelling against the pinned inode, then
            # compare the summary with the same input path rather than the
            # inode's replaceable physical pathname.
            expected_state_dir = supplied_state_dir
            def logical_path(path: Path, name: str | None = None) -> str:
                return f".assay/{name if name is not None else path.name}"
        else:
            artifact_dir = project_root / args.artifact_dir
            state_dir = project_root / args.state_dir
            deadline_path = project_root / args.deadline
            expected_state_dir = state_dir.resolve(strict=True)
            _require_directory(artifact_dir)
            _require_directory(state_dir)
            def logical_path(path: Path, name: str | None = None) -> str:
                return path.relative_to(project_root).as_posix()

        attempt_window_path = artifact_dir / "b110-pilot-attempt-window.json"
        r2_manifest_path = artifact_dir / "r2-manifest-b110-pilot.txt"
        plan_path = artifact_dir / "b110-pilot-plan.json"
        candidates_path = artifact_dir / "b110-pilot-candidates.txt"
        selection_path = artifact_dir / "b110-pilot-selection.json"
        summary_path = artifact_dir / "b110-pilot-summary.json"
        run_log_path = artifact_dir / "b110-pilot-run.log"
        progress_path = artifact_dir / "progress-b110-pilot.jsonl"
        manifest_path = artifact_dir / "b110-pilot-artifacts.sha256"

        plan_doc, plan_raw = _read_json(plan_path, limit=_LIMITS["plan"])
        selection_doc, selection_raw = _read_json(selection_path, limit=_LIMITS["selection"])
        candidates_raw = _read_regular(candidates_path, limit=_LIMITS["candidates"])
        rows, selected_ids, replacement_sha256_by_id = _verify_plan(
            plan_doc,
            expected_commit=args.expected_commit,
            expected_tree=args.expected_tree,
            repo_root=repo_root,
            plan_raw=plan_raw,
            selection=selection_doc,
            candidates_raw=candidates_raw,
        )
        candidates_sha256 = hashlib.sha256(candidates_raw).hexdigest()
        selection_sha256 = selector._plan_sha256(selected_ids)

        summary_doc, summary_raw = _read_json(summary_path, limit=_LIMITS["summary"])
        dispositions, expected_judge_sha256, r2_baselines = _verify_summary(
            summary_doc,
            expected_commit=args.expected_commit,
            selected_ids=selected_ids,
            rows=rows,
            candidates_sha256=candidates_sha256,
            selection_sha256=selection_sha256,
            expected_state_dir=expected_state_dir,
            repo_root=repo_root,
            r2_manifest_path=r2_manifest_path,
        )

        deadline_doc, deadline_raw = _read_json(deadline_path, limit=_LIMITS["deadline"])
        full_plan_sha256 = selector._plan_sha256([row["id"] for row in rows])
        _verify_deadline(
            deadline_doc,
            campaign=args.campaign,
            expected_commit=args.expected_commit,
            expected_tree=args.expected_tree,
            wheel_sha256=wheel_sha256,
            full_plan_sha256=full_plan_sha256,
        )
        deadline_sha256 = hashlib.sha256(deadline_raw).hexdigest()
        attempt_window_doc, attempt_window_raw = _read_json(
            attempt_window_path, limit=_LIMITS["deadline"]
        )
        _verify_attempt_window(
            attempt_window_doc,
            campaign=args.campaign,
            expected_commit=args.expected_commit,
        )
        run_log_raw = _read_regular(run_log_path, limit=_LIMITS["run_log"])
        progress_facts = _verify_progress(
            progress_path,
            expected_commit=args.expected_commit,
            selection_sha256=selection_sha256,
            selected_ids=set(selected_ids),
            selected_order=selected_ids,
            plan_rows=rows,
            plan_total=len(rows),
            dispositions=dispositions,
            summary=summary_doc,
            expected_judge_sha256=expected_judge_sha256,
        )
        state_names, state_hashes, state_records = _verify_state(
            state_dir,
            selected_ids=selected_ids,
            rows=rows,
            dispositions=dispositions,
            selection_sha256=selection_sha256,
            deadline_sha256=deadline_sha256,
            expected_judge_sha256=expected_judge_sha256,
            replacement_sha256_by_id=replacement_sha256_by_id,
            r2_baselines=r2_baselines,
        )
        _verify_resumed_progress(
            progress_facts,
            state_records=state_records,
            dispositions=dispositions,
        )

        logical = {
            "plan": logical_path(plan_path),
            "candidates": logical_path(candidates_path),
            "selection": logical_path(selection_path),
            "summary": logical_path(summary_path),
            "run_log": logical_path(run_log_path),
            "attempt_window": logical_path(attempt_window_path),
            "r2_manifest": logical_path(r2_manifest_path),
            "progress": logical_path(progress_path),
            "deadline": logical_path(deadline_path),
        }
        file_map = {
            logical["plan"]: plan_path,
            logical["candidates"]: candidates_path,
            logical["selection"]: selection_path,
            logical["summary"]: summary_path,
            logical["run_log"]: run_log_path,
            logical["attempt_window"]: attempt_window_path,
            logical["r2_manifest"]: r2_manifest_path,
            logical["progress"]: progress_path,
            logical["deadline"]: deadline_path,
            **{
                logical_path(
                    state_dir / name,
                    f"b110-pilot-state/{name}" if pinned else None,
                ): state_dir / name
                for name in state_names
            },
        }
        validated_hashes = {
            logical["plan"]: hashlib.sha256(plan_raw).hexdigest(),
            logical["candidates"]: candidates_sha256,
            logical["selection"]: hashlib.sha256(selection_raw).hexdigest(),
            logical["summary"]: hashlib.sha256(summary_raw).hexdigest(),
            logical["run_log"]: hashlib.sha256(run_log_raw).hexdigest(),
            logical["attempt_window"]: hashlib.sha256(attempt_window_raw).hexdigest(),
            logical["r2_manifest"]: r2_baselines["manifest_sha256"],
            logical["progress"]: progress_facts["sha256"],
            logical["deadline"]: deadline_sha256,
            **{
                logical_path(
                    state_dir / name,
                    f"b110-pilot-state/{name}" if pinned else None,
                ): state_hashes[name]
                for name in state_names
            },
        }
        if pinned:
            try:
                os.stat(manifest_path.name, dir_fd=args.assay_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("pilot attestation manifest already exists")
        elif manifest_path.exists() or manifest_path.is_symlink():
            raise ValueError("pilot attestation manifest already exists")
        digest = _write_manifest(
            file_map,
            manifest_path,
            validated_hashes,
            manifest_dir_fd=args.assay_fd if pinned else None,
            manifest_name=manifest_path.name if pinned else None,
        )
        if pinned:
            visible_state = os.stat(
                "b110-pilot-state", dir_fd=args.assay_fd, follow_symlinks=False
            )
            if (
                not stat.S_ISDIR(visible_state.st_mode)
                or (visible_state.st_dev, visible_state.st_ino)
                != (state_info.st_dev, state_info.st_ino)
            ):
                raise ValueError("visible pilot state directory changed during verification")
            visible_assay = os.stat(".assay", dir_fd=project_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(visible_assay.st_mode)
                or (visible_assay.st_dev, visible_assay.st_ino)
                != (assay_info.st_dev, assay_info.st_ino)
            ):
                raise ValueError("visible .assay path changed during verification")
        return digest
    finally:
        if state_fd >= 0:
            os.close(state_fd)
        if project_fd >= 0:
            os.close(project_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact-dir", type=Path, default=Path(".assay"))
    parser.add_argument("--state-dir", type=Path, default=Path(".assay/b110-pilot-state"))
    parser.add_argument("--deadline", type=Path, required=True)
    parser.add_argument("--assay-fd", type=int)
    parser.add_argument("--expected-assay-device", type=int)
    parser.add_argument("--expected-assay-inode", type=int)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--expected-wheel-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        manifest_sha256 = verify_pilot(args)
    except (OSError, ValueError, selector.SelectionError) as exc:
        print(f"b110_pilot_report_check: {exc}", file=sys.stderr)
        return 2
    print("B110_PILOT_VERIFIED=1")
    print(f"B110_PILOT_ATTESTATION_SHA256={manifest_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

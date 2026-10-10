#!/usr/bin/env python3
"""Verify the complete, non-qualifying B131 analysis R2 pilot evidence set."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import b110_pilot_select as common
import analysis_r2_pilot_select as selector
import pilot_r2_evidence
import pilot_r2_snapshot
from assay.candidate_identity import candidate_id_from_fields
from assay import __version__ as ASSAY_VERSION
from assay.mutation import (
    MUTATION_STATE_SCHEMA_VERSION,
    PROGRESS_EVENTS,
    _execution_from_state_record,
    _terminal_result_matches_bucket,
    _valid_hung_resource_evidence,
)
from assay.resource_limits import ResourceLimitEvidence
from assay.verdict import MUTATION_BUCKETS, MutantEvidence

LANE = selector.LANE
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_PROGRESS_BYTES = 16 * 1024 * 1024
_MAX_PROGRESS_EVENTS = 50_000
_MAX_PROGRESS_LINE_BYTES = 64 * 1024
_MAX_DEADLINE_BYTES = 64 * 1024
_MAX_RUN_LOG_BYTES = 64 * 1024 * 1024
_MAX_STATE_BYTES = 16 * 1024 * 1024


def _read_bytes(
    path: Path,
    *,
    maximum: int,
    require_single_link: bool = True,
    snapshot_out: dict[str, Any] | None = None,
) -> bytes:
    if not require_single_link:
        raise ValueError("pilot evidence files must have exactly one hard link")
    raw, snapshot = pilot_r2_snapshot.read_file(path, maximum=maximum)
    if snapshot_out is not None:
        snapshot_out.clear()
        snapshot_out.update(snapshot)
    return raw


def _verify_unchanged_evidence(
    *,
    inputs: list[tuple[str, Path, bytes, int, dict[str, Any]]],
    manifest_path: Path,
    manifest_snapshot: dict[str, Any],
    state_dir: Path,
    state_snapshot: dict[str, Any],
    verdict_path: Path,
) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for label, path, expected, maximum, expected_identity in inputs:
        current_identity: dict[str, Any] = {}
        current = _read_bytes(
            path,
            maximum=maximum,
            snapshot_out=current_identity,
        )
        if current != expected or current_identity != expected_identity:
            raise ValueError(f"pilot {label} changed during evidence validation")
        files[label] = {
            "identity": expected_identity,
            "sha256": hashlib.sha256(expected).hexdigest(),
        }

    manifest_identity: dict[str, Any] = {}
    manifest_raw = _read_bytes(
        manifest_path,
        maximum=pilot_r2_evidence._MANIFEST_MAX_BYTES,
        snapshot_out=manifest_identity,
    )
    if (
        hashlib.sha256(manifest_raw).hexdigest() != manifest_snapshot["sha256"]
        or manifest_identity != manifest_snapshot["identity"]
    ):
        raise ValueError("pilot R2 manifest changed during evidence validation")
    files["manifest"] = {
        "identity": manifest_snapshot["identity"],
        "sha256": manifest_snapshot["sha256"],
    }

    state_directory = pilot_r2_snapshot.directory_snapshot(state_dir)
    expected_directory = {
        "path": state_snapshot["path"],
        "directory": state_snapshot["directory"],
        "names": state_snapshot["names"],
    }
    if state_directory != expected_directory:
        raise ValueError("pilot state inventory changed during evidence validation")
    for name, expected_file in state_snapshot["files"].items():
        maximum = _MAX_DEADLINE_BYTES if name == "PILOT-STATE" else _MAX_STATE_BYTES
        current_identity: dict[str, Any] = {}
        current = _read_bytes(
            state_dir / name,
            maximum=maximum,
            snapshot_out=current_identity,
        )
        if (
            hashlib.sha256(current).hexdigest() != expected_file["sha256"]
            or current_identity != expected_file["identity"]
        ):
            raise ValueError(f"pilot state record {name} changed during evidence validation")
        files[f"state/{name}"] = expected_file

    # The second metadata sweep catches changes to an earlier file while a later
    # file was being read. The host wrapper repeats this check after the judge
    # container exits, using the attestation emitted below.
    for label, _path, _expected, _maximum, expected_identity in inputs:
        if pilot_r2_snapshot.stat_file(Path(expected_identity["path"])) != expected_identity:
            raise ValueError(f"pilot {label} changed before the evidence boundary")
    if pilot_r2_snapshot.stat_file(manifest_path) != manifest_snapshot["identity"]:
        raise ValueError("pilot R2 manifest changed before the evidence boundary")
    if pilot_r2_snapshot.directory_snapshot(state_dir) != expected_directory:
        raise ValueError("pilot state inventory changed before the evidence boundary")
    for name, expected_file in state_snapshot["files"].items():
        if pilot_r2_snapshot.stat_file(state_dir / name) != expected_file["identity"]:
            raise ValueError(f"pilot state record {name} changed before the evidence boundary")

    verdict_snapshot = pilot_r2_snapshot.path_is_absent(verdict_path)
    return {
        "files": files,
        "state_directory": expected_directory,
        "verdict": verdict_snapshot,
    }


def _write_attestation(path: Path, document: dict[str, Any]) -> str:
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("pilot attestation output is not a single-link regular file")
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write to pilot attestation")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes, *, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=common._unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid unique-key UTF-8 JSON: {exc}") from exc


def _progress_events(raw: bytes) -> list[dict[str, Any]]:
    if len(raw) > _MAX_PROGRESS_BYTES:
        raise ValueError(f"progress stream exceeds the {_MAX_PROGRESS_BYTES}-byte limit")
    if not raw.endswith(b"\n"):
        raise ValueError("progress stream ends with a partial record")
    event_count = raw.count(b"\n")
    if event_count > _MAX_PROGRESS_EVENTS:
        raise ValueError(f"progress stream has more than {_MAX_PROGRESS_EVENTS} events")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw[:-1].split(b"\n"), 1):
        if len(line) > _MAX_PROGRESS_LINE_BYTES:
            raise ValueError(
                f"progress line {line_number} exceeds {_MAX_PROGRESS_LINE_BYTES} bytes"
            )
        if b"\r" in line:
            raise ValueError(f"progress line {line_number} does not use LF-only framing")
        event = _json(line, label=f"progress line {line_number}")
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError(f"progress line {line_number} is not a named event object")
        if not _valid_progress_timing(event):
            raise ValueError(f"progress line {line_number} has malformed event timing")
        events.append(event)
    if not events:
        raise ValueError("progress stream is empty")
    return events


def _plan_index(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    for row in rows:
        identity = row["id"]
        derived = candidate_id_from_fields(
            path=row["path"],
            source_sha256=row["source_sha256"],
            start_byte=row["start_byte"],
            end_byte=row["end_byte"],
            mutated_file_sha256=row["mutated_file_sha256"],
            operator=row["operator"],
        )
        if identity != derived:
            raise ValueError(f"plan candidate id does not match its identity fields: {identity}")
        by_id[identity] = row
        ordered.append(identity)
    return by_id, ordered


def _check_candidate_file(
    raw: bytes,
    selection: dict[str, Any],
    *,
    selected_ids: list[str],
    plan_count: int,
) -> None:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("candidate file is not UTF-8") from exc
    seed, size = selection.get("seed"), selection.get("size")
    if not isinstance(seed, str) or type(size) is not int or size < 1:
        raise ValueError("selection seed/size is malformed")
    expected_header = (
        f"# {LANE} bounded pilot seed={seed} size={size} plan_candidates={plan_count}"
    )
    if not lines or lines[0] != expected_header:
        raise ValueError("candidate file header differs from the selection report")
    ids = lines[1:]
    if any(_HEX64.fullmatch(item) is None for item in ids):
        raise ValueError("candidate file contains a malformed candidate id")
    if len(ids) != len(set(ids)) or ids != selected_ids:
        raise ValueError("candidate file ids differ from the ordered selection")


def _check_selection(
    selection: Any,
    *,
    raw_plan: bytes,
    plan: dict[str, Any],
    rows: list[dict[str, Any]],
    candidate_file: bytes,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(selection, dict):
        raise ValueError("selection report is not an object")
    if selection.get("schema") != "assay-analysis-r2-pilot-selection/1":
        raise ValueError("selection report has an unknown schema")
    if selection.get("lane") != LANE:
        raise ValueError("selection report lane is not analysis-r2")
    for key, expected in (
        ("plan_commit", plan["commit"]),
        ("plan_tree", plan["tree"]),
        ("plan_sha256", hashlib.sha256(raw_plan).hexdigest()),
        ("plan_candidate_count", len(rows)),
        ("targets", list(selector.TARGETS)),
        ("rare_operator_census", selector.RARE_OPERATOR),
    ):
        if selection.get(key) != expected:
            raise ValueError(f"selection {key} differs from the bound plan")
    ids = selection.get("selected_ids")
    selected_rows = selection.get("selected")
    seed, size = selection.get("seed"), selection.get("size")
    if not isinstance(ids, list) or any(
        not isinstance(identity, str) or _HEX64.fullmatch(identity) is None
        for identity in ids
    ):
        raise ValueError("selection selected_ids is missing or malformed")
    if len(ids) != len(set(ids)):
        raise ValueError("selection contains duplicate candidate ids")
    expected_selection_fields = {
        "schema", "lane", "plan_commit", "plan_tree", "plan_sha256",
        "plan_candidate_count", "targets", "files", "operators", "seed",
        "size", "selected_ids", "selected", "selected_by_file",
        "selected_by_operator", "candidate_file_sha256", "selection_sha256",
        "rare_operator_census",
    }
    if set(selection) != expected_selection_fields:
        raise ValueError("selection report has missing or unknown fields")
    if not isinstance(selected_rows, list) or len(selected_rows) != len(ids):
        raise ValueError("selection row count differs from selected_ids")
    if not isinstance(seed, str) or type(size) is not int or size < 1:
        raise ValueError("selection seed/size is malformed")
    common._validate_seed(seed)
    plan_by_id, plan_ids = _plan_index(rows)
    expected_size = min(size, len(rows))
    if len(ids) != expected_size:
        raise ValueError("selection size differs from the declared deterministic sample size")
    if any(identity not in plan_by_id for identity in ids):
        raise ValueError("selection contains an id outside the current plan")
    if [identity for identity in plan_ids if identity in set(ids)] != ids:
        raise ValueError("selection ids are not in plan order")

    ranks = {identity: selector._rank(seed, identity) for identity in plan_ids}
    by_cell: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_operator: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_cell[(row["path"], row["operator"])].append(row["id"])
        by_operator[row["operator"]].append(row["id"])
    selected_set = set(ids)
    if any(not (set(cell_ids) & selected_set) for cell_ids in by_cell.values()):
        raise ValueError("selection misses a candidate-bearing file/operator stratum")
    rare_ids = set(by_operator.get(selector.RARE_OPERATOR, ()))
    if not rare_ids <= selected_set:
        raise ValueError("selection omits a candidate from the rare operator census")

    row_by_selected_id: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    for identity, row in zip(ids, selected_rows, strict=True):
        if not isinstance(row, dict) or row.get("id") != identity:
            raise ValueError("selection rows are not aligned with selected_ids")
        source = plan_by_id[identity]
        for field in (
            "path", "operator", "source_sha256", "mutated_file_sha256",
            "replacement_sha256", "start_byte", "end_byte", "lineno", "description",
        ):
            if row.get(field) != source.get(field):
                raise ValueError(f"selection candidate {identity} has stale {field}")
        if row.get("rank") != ranks[identity]:
            raise ValueError(f"selection candidate {identity} has a stale deterministic rank")
        reason = row.get("selection_reason")
        if reason not in {
            "file-operator-stratum", "rare-operator-census", "deterministic-fill"
        }:
            raise ValueError(f"selection candidate {identity} has an unknown reason")
        reasons[identity] = reason
        row_by_selected_id[identity] = row
    for cell, cell_ids in by_cell.items():
        winner = min(cell_ids, key=lambda identity: (ranks[identity], identity))
        selected_cell = [identity for identity in cell_ids if identity in selected_set]
        if winner not in selected_cell:
            raise ValueError(f"selection misses deterministic first member of stratum {cell}")
        if reasons[winner] not in {"file-operator-stratum", "rare-operator-census"}:
            raise ValueError(f"stratum winner {winner} is mislabeled as fill")
    for identity in rare_ids:
        if reasons[identity] != "rare-operator-census":
            raise ValueError("rare operator candidate is not labeled as a census member")

    expected_ids = set(rare_ids)
    for cell_ids in by_cell.values():
        expected_ids.add(min(cell_ids, key=lambda identity: (ranks[identity], identity)))
    if len(expected_ids) > expected_size:
        raise ValueError("declared sample size is too small for strata and rare-operator census")
    for identity in sorted(
        (candidate for candidate in plan_ids if candidate not in expected_ids),
        key=lambda candidate: (ranks[candidate], candidate),
    ):
        if len(expected_ids) >= expected_size:
            break
        expected_ids.add(identity)
    if selected_set != expected_ids:
        raise ValueError("selection differs from the deterministic stratified sample")

    selected_by_file = dict(sorted(Counter(row["path"] for row in selected_rows).items()))
    selected_by_operator = dict(sorted(Counter(row["operator"] for row in selected_rows).items()))
    if selection.get("selected_by_file") != selected_by_file:
        raise ValueError("selection selected_by_file counts are incorrect")
    if selection.get("selected_by_operator") != selected_by_operator:
        raise ValueError("selection selected_by_operator counts are incorrect")
    if selection.get("files") != sorted({row["path"] for row in rows}):
        raise ValueError("selection files differ from the plan")
    if selection.get("operators") != sorted({row["operator"] for row in rows}):
        raise ValueError("selection operators differ from the plan")

    selection_digest = common._plan_sha256(ids)
    if selection.get("selection_sha256") != selection_digest:
        raise ValueError("selection_sha256 differs from selected plan-order ids")
    candidate_digest = hashlib.sha256(candidate_file).hexdigest()
    if selection.get("candidate_file_sha256") != candidate_digest:
        raise ValueError("candidate_file_sha256 differs from the candidate file")
    _check_candidate_file(
        candidate_file, selection, selected_ids=ids, plan_count=len(rows)
    )
    return ids, row_by_selected_id


def _check_deadline(
    raw: bytes,
    *,
    expected_commit: str,
    expected_tree: str,
    plan_ids: list[str],
    expected_wheel_sha256: str,
) -> str:
    if _HEX64.fullmatch(expected_wheel_sha256) is None:
        raise ValueError("expected wheel digest must be a lowercase SHA-256")
    deadline = _json(raw, label="campaign deadline")
    expected_keys = {
        "schema", "campaign", "commit", "git_tree", "lanes", "assay_version",
        "wheel_sha256", "plan_sha256", "created_at_utc", "expires_at_utc",
    }
    if not isinstance(deadline, dict) or set(deadline) != expected_keys:
        raise ValueError("campaign deadline has unknown or missing fields")
    if deadline.get("schema") != "assay-campaign-deadline/1":
        raise ValueError("campaign deadline schema is not current")
    if deadline.get("campaign") != f"analysis-r2-pilot-{expected_commit[:12]}":
        raise ValueError("campaign deadline names a different pilot")
    if deadline.get("commit") != expected_commit or deadline.get("git_tree") != expected_tree:
        raise ValueError("campaign deadline commit/tree differ from the plan")
    if deadline.get("lanes") != [LANE]:
        raise ValueError("campaign deadline lane inventory differs from B131")
    if deadline.get("wheel_sha256") != expected_wheel_sha256:
        raise ValueError("campaign deadline does not bind the expected exact-source wheel")
    expected_plan_sha = common._plan_sha256(plan_ids)
    if deadline.get("plan_sha256") != {LANE: expected_plan_sha}:
        raise ValueError("campaign deadline plan digest differs from the full analysis R2 plan")
    if deadline.get("assay_version") != ASSAY_VERSION:
        raise ValueError("campaign deadline assay_version differs from the exact-source wheel")
    try:
        created = datetime.strptime(
            deadline["created_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        expires = datetime.strptime(
            deadline["expires_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError("campaign deadline timestamps are malformed") from exc
    duration = expires - created
    now = datetime.now(timezone.utc)
    if (
        created > now
        or duration not in {timedelta(seconds=7200), timedelta(seconds=7201)}
        or expires <= now
    ):
        raise ValueError("campaign deadline is not the active two-hour pilot campaign")
    return hashlib.sha256(raw).hexdigest()


def _check_pilot_state(
    state_dir: Path,
    *,
    selected_ids: list[str],
    plan_by_id: dict[str, dict[str, Any]],
    outcome_by_id: dict[str, dict[str, Any]],
    deadline_sha256: str,
    r2_baselines: dict[str, Any],
    artifact_snapshot: dict[str, Any] | None = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    directory = pilot_r2_snapshot.directory_snapshot(state_dir)
    expected_names = {"PILOT-STATE", *(f"{identity}.json" for identity in selected_ids)}
    observed_names = set(directory["names"])
    if observed_names != expected_names:
        raise ValueError("pilot state inventory differs from the selected candidates")
    if artifact_snapshot is not None:
        artifact_snapshot.update(
            path=directory["path"],
            directory=directory["directory"],
            names=directory["names"],
            files={},
        )
    sentinel_path = state_dir / "PILOT-STATE"
    sentinel_identity: dict[str, Any] = {}
    sentinel_raw = _read_bytes(
        sentinel_path,
        maximum=_MAX_DEADLINE_BYTES,
        snapshot_out=sentinel_identity,
    )
    if artifact_snapshot is not None:
        artifact_snapshot["files"]["PILOT-STATE"] = {
            "identity": sentinel_identity,
            "sha256": hashlib.sha256(sentinel_raw).hexdigest(),
        }
    sentinel = _json(
        sentinel_raw,
        label="PILOT-STATE",
    )
    if (
        not isinstance(sentinel, dict)
        or set(sentinel) != {"schema", "selection_sha256", "lane"}
        or sentinel.get("schema") != "assay-pilot-state/1"
        or sentinel.get("lane") != LANE
        or sentinel.get("selection_sha256") != common._plan_sha256(selected_ids)
    ):
        raise ValueError("PILOT-STATE does not bind this lane and selection")

    record_names: set[str] = set()
    for name in os.listdir(state_dir):
        if _HEX64.fullmatch(name.removesuffix(".json")) and name.endswith(".json"):
            record_names.add(name[:-5])
    if record_names != set(selected_ids):
        raise ValueError("pilot state record inventory differs from the selected candidates")
    resources_by_id: dict[str, dict[str, Any]] = {}
    judge_identities: set[str] = set()
    for identity in selected_ids:
        record_name = f"{identity}.json"
        record_identity: dict[str, Any] = {}
        record_raw = _read_bytes(
            state_dir / record_name,
            maximum=_MAX_STATE_BYTES,
            snapshot_out=record_identity,
        )
        if artifact_snapshot is not None:
            artifact_snapshot["files"][record_name] = {
                "identity": record_identity,
                "sha256": hashlib.sha256(record_raw).hexdigest(),
            }
        record = _json(
            record_raw,
            label=f"pilot state record {identity}",
        )
        planned = plan_by_id[identity]
        outcome = outcome_by_id[identity]
        if not isinstance(record, dict):
            raise ValueError(f"pilot state record {identity} is not an object")
        expected_fields = {
            "candidate_id": identity,
            "path": planned["path"],
            "operator": planned["operator"],
            "source_sha256": planned["source_sha256"],
            "mutated_file_sha256": planned["mutated_file_sha256"],
            "replacement_sha256": planned["replacement_sha256"],
            "start_byte": planned["start_byte"],
            "end_byte": planned["end_byte"],
            "lineno": planned["lineno"],
            "description": planned["description"],
            "outcome_bucket": outcome["bucket"],
            "campaign_deadline_sha256": deadline_sha256,
        }
        for key, expected in expected_fields.items():
            if record.get(key) != expected:
                raise ValueError(f"pilot state record {identity} has stale {key}")
        if (
            type(record.get("schema_version")) is not int
            or record["schema_version"] != MUTATION_STATE_SCHEMA_VERSION
        ):
            raise ValueError(f"pilot state record {identity} has an unknown schema")
        if _HEX64.fullmatch(record.get("judge_sha256", "")) is None:
            raise ValueError(f"pilot state record {identity} has no judge identity")
        judge_identities.add(record["judge_sha256"])
        if not isinstance(record.get("execution"), dict):
            raise ValueError(f"pilot state record {identity} execution differs from its summary")
        try:
            execution = _execution_from_state_record(record)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"pilot state record {identity} has invalid execution evidence: {exc}"
            ) from exc
        if execution.mode != outcome["execution_mode"]:
            raise ValueError(f"pilot state record {identity} execution differs from its summary")
        if outcome["bucket"] == "killed":
            if execution.mode == "full" and execution.witness is None:
                raise ValueError(
                    f"pilot full kill {identity} lacks its failed-call witness"
                )
            if execution.mode not in {"full", "witness-cold"}:
                raise ValueError(
                    f"pilot kill {identity} has unsupported execution mode {execution.mode!r}"
                )
        elif execution.mode != "full" or execution.witness is not None:
            raise ValueError(
                f"pilot non-kill {identity} has an execution receipt reserved for kills"
            )
        if outcome["bucket"] in {"killed", "survived"}:
            evidence = record.get("evidence")
            if not isinstance(evidence, dict) or set(evidence) != {
                "command", "collection_count", "collection_sha256",
                "hook_fingerprint_sha256", "started_count", "failed_call_index",
            }:
                raise ValueError(f"pilot state record {identity} lacks complete cold-witness evidence")
            try:
                checked_evidence = MutantEvidence(**evidence)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"pilot state record {identity} has invalid cold-witness evidence: {exc}") from exc
            if outcome["bucket"] == "survived":
                if checked_evidence.started_count is not None:
                    raise ValueError(f"pilot survivor {identity} carries failed-prefix evidence")
            pilot_r2_evidence.validate_candidate_evidence(
                evidence,
                baselines=r2_baselines,
                candidate_id=identity,
            )
        elif record.get("evidence") is not None:
            pilot_r2_evidence.validate_candidate_evidence(
                record["evidence"],
                baselines=r2_baselines,
                candidate_id=identity,
            )
        if outcome["bucket"] == "killed":
            pilot_r2_evidence.validate_kill_witness(
                record.get("execution"),
                record["evidence"],
                nodes=r2_baselines["nodes"],
                candidate_id=identity,
            )
        elif execution.mode != "full" or execution.witness is not None:
            raise ValueError(
                f"pilot non-kill {identity} must use full execution without a witness"
            )
        cost_resources = pilot_r2_evidence.validate_state_measurements(
            record,
            candidate_id=identity,
        )
        raw_resources = record.get("resource_limit_evidence")
        if not isinstance(raw_resources, dict):
            raise ValueError(f"pilot state record {identity} has no B145 resource evidence")
        try:
            resources = ResourceLimitEvidence.from_dict(raw_resources)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"pilot state record {identity} has invalid B145 resource evidence: {exc}") from exc
        if resources.limit_hit:
            raise ValueError(f"pilot candidate {identity} hit a process or memory limit")
        resources_by_id[identity] = {
            "resource_limit_evidence": resources.to_dict(),
            "liveness_resource_evidence": record.get("liveness_resource_evidence"),
            "cost_resources": cost_resources,
        }
        if not _terminal_result_matches_bucket(
            record, cold_witness=True, resource_limit_hit=False
        ):
            raise ValueError(f"pilot state record {identity} terminal result disagrees with its bucket")
        if outcome["bucket"] == "hung" and not _valid_hung_resource_evidence(
            record.get("liveness_resource_evidence")
        ):
            raise ValueError(f"pilot state record {identity} has no valid hung resource evidence")
    if len(judge_identities) != 1:
        raise ValueError("selected pilot state records do not share one judge identity")
    return next(iter(judge_identities)), resources_by_id


def _valid_progress_timing(event: dict[str, Any]) -> bool:
    emitted_at = event.get("emitted_at")
    elapsed = event.get("elapsed_s")
    if not isinstance(emitted_at, str) or not emitted_at:
        return False
    try:
        timestamp = datetime.fromisoformat(emitted_at)
    except ValueError:
        return False
    return (
        timestamp.tzinfo is not None
        and timestamp.utcoffset() is not None
        and type(elapsed) in (int, float)
        and math.isfinite(elapsed)
        and elapsed >= 0
    )


def _check_progress(
    events: list[dict[str, Any]],
    *,
    expected_commit: str,
    plan_by_id: dict[str, dict[str, Any]],
    selected_ids: list[str],
    outcome_by_id: dict[str, dict[str, Any]],
    expected_judge_sha256: str,
    state_resources_by_id: dict[str, dict[str, Any]],
    summary_r2: dict[str, Any],
) -> None:
    boundaries = [index for index, event in enumerate(events) if event.get("event") == "run"]
    if not boundaries or boundaries[0] != 0:
        raise ValueError("progress does not begin with a run header")
    segments = [
        events[start : boundaries[index + 1] if index + 1 < len(boundaries) else len(events)]
        for index, start in enumerate(boundaries)
    ]
    final_segment = segments[-1]
    final_header = final_segment[0]
    final_metadata = [event for event in final_segment if event.get("event") == "candidates"]
    if (
        final_header.get("lane") != LANE
        or final_header.get("commit") != expected_commit
        or len(final_metadata) != 1
        or final_metadata[0].get("selection_sha256") != common._plan_sha256(selected_ids)
        or final_metadata[0].get("judge_sha256") != expected_judge_sha256
    ):
        raise ValueError("final progress run does not bind this lane, source, selection and judge")

    # A lane can append to an existing progress file. Only segments whose
    # source, selection and judge all match may supply resume dispositions;
    # stale commits and unrelated selections carry no candidate authority.
    bound_events: list[dict[str, Any]] = []
    for segment in segments:
        header = segment[0]
        if header.get("lane") != LANE or header.get("commit") != expected_commit:
            continue
        metadata = [event for event in segment if event.get("event") == "candidates"]
        if (
            len(metadata) == 1
            and metadata[0].get("selection_sha256") == common._plan_sha256(selected_ids)
            and metadata[0].get("judge_sha256") == expected_judge_sha256
        ):
            bound_events.extend(segment)
    events = bound_events

    selected = set(selected_ids)
    observed: dict[str, list[tuple[str, str]]] = defaultdict(list)
    latest_resources: dict[str, dict[str, Any]] = {}
    latest_liveness: dict[str, Any] = {}
    latest_cost_resources: dict[str, dict[str, Any]] = {}
    runs: list[dict[str, Any]] = []
    current_run: dict[str, Any] | None = None
    for index, event in enumerate(events, 1):
        kind = event["event"]
        if kind not in PROGRESS_EVENTS:
            raise ValueError(f"progress event {index} has an unknown event type {kind!r}")
        if kind != "run" and current_run is None:
            raise ValueError(f"progress event {index} precedes its run header")
        if kind == "run":
            current_run = {
                "candidates_seen": False,
                "baseline_seen": False,
                "selected_total": None,
                "pending_total": None,
                "candidate_ids": [],
                "candidate_indexes": [],
                "terminal_exit": None,
                "terminal_event": None,
                "end": None,
                "sweep_complete": False,
                "candidate_events": {},
                "resume_event": None,
                "resume_merged_event": None,
                "prior_candidate_ids": set(observed),
            }
            runs.append(current_run)
            if event.get("lane") != LANE or event.get("commit") != expected_commit:
                raise ValueError(f"progress run header {len(runs)} belongs to another lane or commit")
            if event.get("rigor") != ["R0", "R1", "R2"]:
                raise ValueError(f"progress run header {len(runs)} has unexpected rigor")
        elif kind == "candidates":
            if (
                current_run is None
                or current_run["candidates_seen"]
                or current_run["terminal_exit"] is not None
                or current_run["end"] is not None
            ):
                raise ValueError(f"progress candidates event {index} is out of run order")
            current_run["candidates_seen"] = True
            if (
                event.get("commit") != expected_commit
                or type(event.get("candidate_total")) is not int
                or event["candidate_total"] != len(selected_ids)
                or type(event.get("selected_total")) is not int
                or event["selected_total"] != len(selected_ids)
                or event.get("selection_sha256") != common._plan_sha256(selected_ids)
                or event.get("judge_sha256") != expected_judge_sha256
                or type(event.get("pending_total")) is not int
                or not 0 <= event["pending_total"] <= len(selected_ids)
            ):
                raise ValueError(f"progress candidates event {index} differs from the bound plan/selection")
            resume_event = current_run["resume_event"]
            if resume_event is None:
                if event["pending_total"] != event["selected_total"]:
                    raise ValueError(f"progress candidates event {index} omits resume accounting")
            elif (
                resume_event["candidate_total"] != event["selected_total"]
                or resume_event["resumed_total"] + event["pending_total"] != event["selected_total"]
                or resume_event["rejected_total"] + resume_event["rejudged_total"]
                > event["pending_total"]
            ):
                raise ValueError(f"progress resume accounting {index} does not partition the selected pilot")
            current_run["selected_total"] = event["selected_total"]
            current_run["pending_total"] = event["pending_total"]
        elif kind == "baseline":
            if (
                current_run is None
                or not current_run["candidates_seen"]
                or current_run["baseline_seen"]
                or current_run["candidate_ids"]
                or current_run["terminal_exit"] is not None
                or current_run["end"] is not None
            ):
                raise ValueError(f"progress baseline event {index} is out of run order")
            if set(event) != {
                "event", "candidate_index", "candidate_total", "path", "operator",
                "start_byte", "end_byte", "mutated_file_sha256", "emitted_at", "elapsed_s",
            } or (
                type(event.get("candidate_index")) is not int
                or event["candidate_index"] != -1
                or type(event.get("candidate_total")) is not int
                or event["candidate_total"] != current_run["selected_total"]
                or event.get("path") != "."
                or event.get("operator") != "baseline"
                or type(event.get("start_byte")) is not int
                or event["start_byte"] != 0
                or type(event.get("end_byte")) is not int
                or event["end_byte"] != 0
                or event.get("mutated_file_sha256") != ""
                or not _valid_progress_timing(event)
            ):
                raise ValueError(f"progress baseline event {index} is malformed or stale")
            current_run["baseline_seen"] = True
        elif kind == "candidate":
            if (
                current_run is None
                or not current_run["candidates_seen"]
                or not current_run["baseline_seen"]
                or current_run["terminal_exit"] is not None
                or current_run["end"] is not None
            ):
                raise ValueError(f"progress candidate event {index} precedes its candidates/baseline milestone")
            identity = event.get("candidate_id")
            if not isinstance(identity, str) or identity not in selected:
                raise ValueError(f"progress candidate event {index} is outside the pilot selection")
            planned = plan_by_id[identity]
            for key in ("path", "operator", "lineno", "description", "start_byte", "end_byte", "mutated_file_sha256"):
                if event.get(key) != planned.get(key):
                    raise ValueError(f"progress candidate {identity} has stale {key}")
            # Candidate events describe this invocation's pending queue; the
            # preceding candidates milestone carries the full plan size and
            # the selected/pending counts separately.
            if (
                type(event.get("candidate_total")) is not int
                or event["candidate_total"] != current_run["pending_total"]
            ):
                raise ValueError(f"progress candidate {identity} has a wrong pending candidate_total")
            if (
                type(event.get("candidate_index")) is not int
                or event["candidate_index"] < 0
                or event["candidate_index"] >= current_run["pending_total"]
                or event["candidate_index"] != len(current_run["candidate_ids"])
                or event["candidate_index"] in current_run["candidate_indexes"]
                or (
                    current_run["candidate_indexes"]
                    and event["candidate_index"] <= current_run["candidate_indexes"][-1]
                )
            ):
                raise ValueError(f"progress candidate {identity} has an invalid candidate_index")
            if identity in current_run["candidate_ids"]:
                raise ValueError(f"progress candidate {identity} is duplicated in one run")
            bucket = event.get("outcome_bucket")
            if bucket not in MUTATION_BUCKETS:
                raise ValueError(f"progress candidate {identity} has an unknown bucket")
            resources = event.get("resource_limit_evidence")
            if not isinstance(resources, dict):
                raise ValueError(f"progress candidate {identity} has no B145 resource evidence")
            try:
                resource_evidence = ResourceLimitEvidence.from_dict(resources)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"progress candidate {identity} has invalid resource evidence: {exc}") from exc
            if resource_evidence.limit_hit:
                raise ValueError(f"progress candidate {identity} hit a process or memory limit")
            execution_mode = event.get("execution_mode")
            if not isinstance(execution_mode, str) or not execution_mode:
                raise ValueError(f"progress candidate {identity} has no execution mode")
            cost_measurements = pilot_r2_evidence.validate_progress_measurements(
                event,
                candidate_id=identity,
            )
            current_run["candidate_ids"].append(identity)
            current_run["candidate_indexes"].append(event["candidate_index"])
            current_run["candidate_events"][identity] = event
            observed[identity].append((bucket, execution_mode))
            latest_resources[identity] = resource_evidence.to_dict()
            latest_liveness[identity] = event.get("liveness_resource_evidence")
            latest_cost_resources[identity] = {
                field: cost_measurements[field]
                for field in (
                    "cpu_seconds", "peak_rss_bytes", "phase_seconds", "startup_seconds"
                )
            }
        elif kind == "end":
            if (
                current_run is None
                or not current_run["candidates_seen"]
                or not current_run["baseline_seen"]
                or current_run["terminal_exit"] is not None
                or current_run["end"] is not None
            ):
                raise ValueError(f"progress end event {index} is out of run order")
            if set(event) != {
                "event", "candidate_total", "buckets", "reason", "emitted_at", "elapsed_s",
            }:
                raise ValueError(f"progress end event {index} has missing or unknown fields")
            if (
                type(event.get("candidate_total")) is not int
                or event["candidate_total"] != current_run["selected_total"]
                or event.get("reason") is not None
                or not isinstance(event.get("buckets"), dict)
                or set(event["buckets"]) != set(MUTATION_BUCKETS)
                or any(type(value) is not int or value < 0 for value in event["buckets"].values())
                or not _valid_progress_timing(event)
            ):
                raise ValueError(f"progress end event {index} is not a completed mutation sweep")
            candidate_count = len(current_run["candidate_ids"])
            missing_pending = current_run["pending_total"] - candidate_count
            if missing_pending < 0:
                raise ValueError(f"progress end event {index} has too many candidate events")
            current_run["sweep_complete"] = (
                missing_pending == 0
                and set(current_run["candidate_indexes"])
                == set(range(current_run["pending_total"]))
            )
            resume_event = current_run["resume_event"]
            resumed_total = 0 if resume_event is None else resume_event["resumed_total"]
            if (resumed_total > 0) != (current_run["resume_merged_event"] is not None):
                raise ValueError(
                    f"progress end event {index} precedes or omits its resume_merged marker"
                )
            if sum(event["buckets"].values()) != current_run["selected_total"]:
                raise ValueError(
                    f"progress end event {index} buckets do not account for the selected candidates"
                )
            resume_event = current_run["resume_event"]
            resumed_total = 0 if resume_event is None else resume_event["resumed_total"]
            prior_buckets = {
                identity: history[-1][0]
                for identity, history in observed.items()
                if identity not in current_run["candidate_events"] and history
            }
            pilot_r2_evidence.validate_pilot_end_accounting(
                selected_order=selected_ids,
                candidate_events=list(current_run["candidate_events"].values()),
                prior_buckets=prior_buckets,
                pending_total=current_run["pending_total"],
                resumed_total=resumed_total,
                end_buckets=event["buckets"],
                context=f"progress end event {index}",
                require_prefix_indexes=True,
            )
            if current_run["sweep_complete"]:
                expected_buckets = {name: 0 for name in MUTATION_BUCKETS}
                for identity in selected_ids:
                    history = observed.get(identity)
                    if not history:
                        raise ValueError(
                            f"progress end event {index} has no disposition for selected candidate {identity}"
                        )
                    expected_buckets[history[-1][0]] += 1
                if event["buckets"] != expected_buckets:
                    raise ValueError(
                        f"progress end event {index} buckets differ from its merged candidate events"
                    )
            current_run["end"] = event
        elif kind == "resume":
            if (
                current_run is None
                or current_run["candidates_seen"]
                or current_run["terminal_exit"] is not None
                or current_run["end"] is not None
                or current_run["resume_event"] is not None
            ):
                raise ValueError(f"progress resume event {index} is out of run order")
            if set(event) != {
                "event", "candidate_total", "resumed_total", "rejected_total",
                "rejudged_total", "emitted_at", "elapsed_s",
            } or any(
                type(event.get(field)) is not int or event[field] < 0
                for field in (
                    "candidate_total", "resumed_total", "rejected_total", "rejudged_total"
                )
            ):
                raise ValueError(f"progress resume event {index} has malformed accounting")
            if not _valid_progress_timing(event):
                raise ValueError(f"progress resume event {index} has malformed timing")
            current_run["resume_event"] = event
        elif kind == "verdict_written":
            if current_run is None or current_run["terminal_exit"] is not None:
                raise ValueError(f"progress terminal event {index} is out of run order")
            if set(event) != {
                "event", "outcome", "reason_code", "exit_code", "destination",
                "emitted_at", "elapsed_s",
            }:
                raise ValueError(f"progress terminal event {index} has missing or unknown fields")
            if (
                current_run["end"] is None
                or type(event.get("exit_code")) is not int
                or not _valid_progress_timing(event)
            ):
                raise ValueError(f"progress terminal event {index} is not a pilot-only completion")
            pilot_r2_evidence.validate_pilot_terminal_event(
                event,
                context=f"progress terminal event {index}",
                end_buckets=current_run["end"]["buckets"],
            )
            if event["exit_code"] == 6 and not current_run["sweep_complete"]:
                raise ValueError(
                    f"progress terminal event {index} claims completion with missing candidates"
                )
            current_run["terminal_exit"] = event["exit_code"]
            current_run["terminal_event"] = event
        elif kind == "resume_merged":
            if (
                current_run is None
                or not current_run["candidates_seen"]
                or not current_run["baseline_seen"]
                or current_run["end"] is not None
                or current_run["terminal_exit"] is not None
            ):
                raise ValueError(f"progress resume_merged event {index} is out of run order")
            resume_event = current_run["resume_event"]
            if (
                current_run["resume_merged_event"] is not None
                or resume_event is None
                or type(event.get("resumed_total")) is not int
                or event["resumed_total"] <= 0
                or event["resumed_total"] != resume_event["resumed_total"]
                or set(event) != {"event", "resumed_total", "emitted_at", "elapsed_s"}
                or not _valid_progress_timing(event)
            ):
                raise ValueError(f"progress resume_merged event {index} differs from resume accounting")
            current_run["resume_merged_event"] = event
        elif current_run is not None and current_run["end"] is not None:
            raise ValueError(f"progress event {index} follows the mutation sweep end")
    if not runs:
        raise ValueError("progress has no run header")
    for run_number, run in enumerate(runs, start=1):
        if not run["candidates_seen"]:
            continue
        resume_event = run["resume_event"]
        resumed_total = 0 if resume_event is None else resume_event["resumed_total"]
        pilot_r2_evidence.validate_pilot_resume_queue(
            selected_order=selected_ids,
            prior_dispositions=run["prior_candidate_ids"],
            candidate_events=list(run["candidate_events"].values()),
            pending_total=run["pending_total"],
            resumed_total=resumed_total,
            context=f"progress run {run_number}",
        )
    final_run = runs[-1]
    resume_event = final_run["resume_event"]
    resumed_total = 0 if resume_event is None else resume_event["resumed_total"]
    if (resumed_total > 0) != (final_run["resume_merged_event"] is not None):
        raise ValueError("final progress resume_merged marker differs from its resumed candidate count")
    if (
        not final_run["candidates_seen"]
        or not final_run["baseline_seen"]
        or final_run["end"] is None
        or final_run["terminal_exit"] != 6
        or len(final_run["candidate_ids"]) != final_run["pending_total"]
        or not final_run["sweep_complete"]
    ):
        raise ValueError("final progress run is not a complete exit-6 pilot")
    final_terminal = final_run["terminal_event"]
    if (
        final_terminal is None
        or final_terminal.get("outcome") != summary_r2.get("status")
        or final_terminal.get("reason_code") != summary_r2.get("reason_code")
    ):
        raise ValueError("final pilot terminal verdict differs from the validated summary")
    final_positions = [selected_ids.index(identity) for identity in final_run["candidate_ids"]]
    if final_positions != sorted(final_positions):
        raise ValueError("final progress candidate order differs from the selected plan order")
    for identity, outcome in outcome_by_id.items():
        if not observed.get(identity):
            raise ValueError(f"progress has no matching outcome event for selected candidate {identity}")
        if observed[identity][-1] != (outcome["bucket"], outcome["execution_mode"]):
            raise ValueError(f"latest progress outcome differs from summary for selected candidate {identity}")
        state_evidence = state_resources_by_id.get(identity)
        if latest_resources.get(identity) != state_evidence["resource_limit_evidence"]:
            raise ValueError(f"latest progress resource evidence differs from state for candidate {identity}")
        state_liveness = state_evidence["liveness_resource_evidence"]
        if latest_liveness.get(identity) != state_liveness:
            raise ValueError(f"latest progress liveness evidence differs from state for candidate {identity}")
        if latest_cost_resources.get(identity) != state_evidence["cost_resources"]:
            raise ValueError(f"latest progress cost measurements differ from state for candidate {identity}")
        if outcome["bucket"] == "hung" and (
            not _valid_hung_resource_evidence(latest_liveness.get(identity))
            or latest_liveness[identity] != state_liveness
        ):
            raise ValueError(f"hung candidate {identity} lacks matching liveness resource evidence")


def verify_pilot(
    *,
    plan_raw: bytes,
    selection_raw: bytes,
    candidates_raw: bytes,
    summary_raw: bytes,
    progress_raw: bytes,
    deadline_raw: bytes,
    state_dir: Path,
    expected_commit: str,
    expected_tree: str,
    expected_wheel_sha256: str,
    expected_exit_code: int,
    repo_root: Path,
    r2_manifest_path: Path,
) -> dict[str, Any]:
    if _HEX40.fullmatch(expected_commit) is None or _HEX40.fullmatch(expected_tree) is None:
        raise ValueError("expected commit and tree must be full lowercase Git ids")
    if type(expected_exit_code) is not int or expected_exit_code != 6:
        raise ValueError("complete non-qualifying pilot must report actual exit 6")
    plan_doc, commit, tree, rows, _targets = selector._parse_bound_plan(
        plan_raw, repo_root=repo_root
    )
    if commit != expected_commit or tree != expected_tree:
        raise ValueError("plan commit/tree differ from the expected judged source")
    plan_by_id, plan_ids = _plan_index(rows)
    selection = _json(selection_raw, label="selection report")
    selected_ids, _selected_rows = _check_selection(
        selection,
        raw_plan=plan_raw,
        plan=plan_doc,
        rows=rows,
        candidate_file=candidates_raw,
    )
    deadline_sha256 = _check_deadline(
        deadline_raw,
        expected_commit=commit,
        expected_tree=tree,
        plan_ids=plan_ids,
        expected_wheel_sha256=expected_wheel_sha256,
    )
    summary = _json(summary_raw, label="pilot summary")
    if not isinstance(summary, dict):
        raise ValueError("pilot summary is not an object")
    if summary.get("schema") != "assay-pilot-summary/2":
        raise ValueError("pilot summary has an unknown schema")
    if summary.get("qualifying") is not False or summary.get("completed") is not True:
        raise ValueError("pilot summary is not a complete non-qualifying measurement")
    if "refusal" in summary:
        raise ValueError("completed pilot summary cannot contain a refusal")
    if set(summary) != {
        "schema", "qualifying", "completed", "lane", "commit", "jobs",
        "requested", "selection_sha256", "judge_sha256",
        "candidates_file_sha256", "state_dir", "r0", "r1", "r2", "r3",
        "r2_command", "buckets", "candidates", "unresolved",
    }:
        raise ValueError("pilot summary has missing or unknown fields")
    if summary.get("lane") != LANE or summary.get("commit") != commit:
        raise ValueError("pilot summary lane/commit differs from the bound plan")
    if type(summary.get("jobs")) is not int or summary["jobs"] != 1:
        raise ValueError("pilot must use the lane's declared single worker")
    if type(summary.get("requested")) is not int or summary["requested"] != len(selected_ids):
        raise ValueError("pilot summary requested count differs from its selection")
    if summary.get("selection_sha256") != common._plan_sha256(selected_ids):
        raise ValueError("pilot summary selection_sha256 differs from the selection")
    if summary.get("candidates_file_sha256") != hashlib.sha256(candidates_raw).hexdigest():
        raise ValueError("pilot summary candidate-file digest differs from the selection")
    if summary.get("state_dir") != str(state_dir.resolve(strict=True)):
        raise ValueError("pilot summary state_dir differs from the isolated pilot store")
    if summary.get("r0") != "PASS" or summary.get("r1") != "PASS":
        raise ValueError("pilot R0 and R1 baselines must both PASS")
    if summary.get("r3") != "not-run: pilot":
        raise ValueError("pilot summary does not disclose that R3 was not run")
    r2 = summary.get("r2")
    if not isinstance(r2, dict) or not isinstance(r2.get("status"), str):
        raise ValueError("pilot summary has no completed R2 outcome")
    if r2.get("reason_code") is not None and not isinstance(r2.get("reason_code"), str):
        raise ValueError("pilot summary R2 reason_code is malformed")
    r2_baselines = pilot_r2_evidence.validate_r2_command(
        summary.get("r2_command"),
        repo_root=repo_root,
        expected_commit=commit,
        expected_lane=LANE,
        manifest_path=r2_manifest_path,
    )
    if summary.get("unresolved") != []:
        raise ValueError("pilot summary has unresolved selected candidates")
    raw_buckets = summary.get("buckets")
    if not isinstance(raw_buckets, dict) or set(raw_buckets) != set(MUTATION_BUCKETS):
        raise ValueError("pilot summary buckets do not match the mutation vocabulary")
    if any(type(raw_buckets[key]) is not int or raw_buckets[key] < 0 for key in MUTATION_BUCKETS):
        raise ValueError("pilot summary has an invalid bucket count")
    candidates = summary.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != len(selected_ids):
        raise ValueError("pilot summary candidate inventory is incomplete")
    outcome_by_id: dict[str, dict[str, Any]] = {}
    for identity, outcome in zip(selected_ids, candidates, strict=True):
        if not isinstance(outcome, dict) or outcome.get("id") != identity:
            raise ValueError("pilot summary candidates are not in selection order")
        planned = plan_by_id[identity]
        if outcome.get("path") != planned["path"] or outcome.get("operator") != planned["operator"]:
            raise ValueError(f"pilot summary candidate {identity} differs from the plan")
        if outcome.get("bucket") not in MUTATION_BUCKETS:
            raise ValueError(f"pilot summary candidate {identity} has an unknown bucket")
        if not isinstance(outcome.get("execution_mode"), str) or not outcome["execution_mode"]:
            raise ValueError(f"pilot summary candidate {identity} has no execution mode")
        outcome_by_id[identity] = outcome
    actual_buckets = Counter(outcome["bucket"] for outcome in outcome_by_id.values())
    if any(raw_buckets[bucket] != actual_buckets[bucket] for bucket in MUTATION_BUCKETS):
        raise ValueError("pilot summary bucket counts differ from candidate outcomes")
    if actual_buckets["crashed"]:
        expected_r2 = {"status": "ERROR", "reason_code": "EXEC_FAILED"}
    elif actual_buckets["budget_exceeded"]:
        expected_r2 = {"status": "BUDGET_EXCEEDED", "reason_code": "LANE_TIMEOUT"}
    elif actual_buckets["hung"]:
        expected_r2 = {"status": "BUDGET_EXCEEDED", "reason_code": "CANDIDATE_HUNG"}
    elif actual_buckets["survived"]:
        expected_r2 = {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"}
    elif actual_buckets["killed"] == 0 and actual_buckets["equivalent"]:
        expected_r2 = {"status": "INCONCLUSIVE", "reason_code": "ALL_MUTANTS_EQUIVALENT"}
    elif not sum(actual_buckets.values()):
        expected_r2 = {"status": "INCONCLUSIVE", "reason_code": "NO_MUTANTS"}
    else:
        expected_r2 = {"status": "PASS", "reason_code": None}
    if r2 != expected_r2:
        raise ValueError("pilot R2 result differs from the validated candidate outcomes")

    state_snapshot: dict[str, Any] = {}
    expected_judge_sha256, state_resources_by_id = _check_pilot_state(
        state_dir,
        selected_ids=selected_ids,
        plan_by_id=plan_by_id,
        outcome_by_id=outcome_by_id,
        deadline_sha256=deadline_sha256,
        r2_baselines=r2_baselines,
        artifact_snapshot=state_snapshot,
    )
    if summary.get("judge_sha256") != expected_judge_sha256:
        raise ValueError("pilot summary judge_sha256 differs from state and progress")
    progress_events = _progress_events(progress_raw)
    _check_progress(
        progress_events,
        expected_commit=commit,
        plan_by_id=plan_by_id,
        selected_ids=selected_ids,
        outcome_by_id=outcome_by_id,
        expected_judge_sha256=expected_judge_sha256,
        state_resources_by_id=state_resources_by_id,
        summary_r2=r2,
    )
    return {
        "manifest": {
            "identity": r2_baselines["manifest_identity"],
            "sha256": r2_baselines["manifest_sha256"],
        },
        "state_snapshot": state_snapshot,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--run-log", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--r2-manifest", type=Path, required=True)
    parser.add_argument("--deadline", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--verdict", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--expected-wheel-sha256", required=True)
    parser.add_argument("--expected-exit-code", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        pilot_r2_snapshot.path_is_absent(args.verdict)
        input_identities: dict[str, dict[str, Any]] = {}
        plan_identity: dict[str, Any] = {}
        plan_raw = _read_bytes(
            args.plan, maximum=_MAX_JSON_BYTES, snapshot_out=plan_identity
        )
        input_identities["plan"] = plan_identity.copy()
        selection_identity: dict[str, Any] = {}
        selection_raw = _read_bytes(
            args.selection, maximum=_MAX_JSON_BYTES, snapshot_out=selection_identity
        )
        input_identities["selection"] = selection_identity.copy()
        candidates_identity: dict[str, Any] = {}
        candidates_raw = _read_bytes(
            args.candidates,
            maximum=common._CANDIDATE_FILE_LIMIT,
            snapshot_out=candidates_identity,
        )
        input_identities["candidate file"] = candidates_identity.copy()
        summary_identity: dict[str, Any] = {}
        summary_raw = _read_bytes(
            args.summary, maximum=_MAX_JSON_BYTES, snapshot_out=summary_identity
        )
        input_identities["summary"] = summary_identity.copy()
        run_log_identity: dict[str, Any] = {}
        run_log_raw = _read_bytes(
            args.run_log, maximum=_MAX_RUN_LOG_BYTES, snapshot_out=run_log_identity
        )
        input_identities["run log"] = run_log_identity.copy()
        progress_identity: dict[str, Any] = {}
        progress_raw = _read_bytes(
            args.progress, maximum=_MAX_PROGRESS_BYTES, snapshot_out=progress_identity
        )
        input_identities["progress"] = progress_identity.copy()
        deadline_identity: dict[str, Any] = {}
        deadline_raw = _read_bytes(
            args.deadline, maximum=_MAX_DEADLINE_BYTES, snapshot_out=deadline_identity
        )
        input_identities["deadline"] = deadline_identity.copy()
        snapshot = verify_pilot(
            plan_raw=plan_raw,
            selection_raw=selection_raw,
            candidates_raw=candidates_raw,
            summary_raw=summary_raw,
            progress_raw=progress_raw,
            deadline_raw=deadline_raw,
            state_dir=args.state_dir,
            expected_commit=args.expected_commit,
            expected_tree=args.expected_tree,
            expected_wheel_sha256=args.expected_wheel_sha256,
            expected_exit_code=args.expected_exit_code,
            repo_root=args.repo_root,
            r2_manifest_path=args.r2_manifest,
        )
        attested_snapshot = _verify_unchanged_evidence(
            inputs=[
                ("plan", args.plan, plan_raw, _MAX_JSON_BYTES, input_identities["plan"]),
                (
                    "selection", args.selection, selection_raw, _MAX_JSON_BYTES,
                    input_identities["selection"],
                ),
                (
                    "candidate file",
                    args.candidates,
                    candidates_raw,
                    common._CANDIDATE_FILE_LIMIT,
                    input_identities["candidate file"],
                ),
                (
                    "summary", args.summary, summary_raw, _MAX_JSON_BYTES,
                    input_identities["summary"],
                ),
                (
                    "run log", args.run_log, run_log_raw, _MAX_RUN_LOG_BYTES,
                    input_identities["run log"],
                ),
                (
                    "progress", args.progress, progress_raw, _MAX_PROGRESS_BYTES,
                    input_identities["progress"],
                ),
                (
                    "deadline", args.deadline, deadline_raw, _MAX_DEADLINE_BYTES,
                    input_identities["deadline"],
                ),
            ],
            manifest_path=args.r2_manifest,
            manifest_snapshot=snapshot["manifest"],
            state_dir=args.state_dir,
            state_snapshot=snapshot["state_snapshot"],
            verdict_path=args.verdict,
        )
        attestation_sha256 = _write_attestation(
            args.attestation,
            {
                "schema": "assay-analysis-r2-pilot-evidence-attestation/2",
                "commit": args.expected_commit,
                **attested_snapshot,
            },
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"analysis_r2_pilot_check: {exc}", file=sys.stderr)
        return 2
    print(f"ANALYSIS_R2_PILOT_VERIFIED={attestation_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

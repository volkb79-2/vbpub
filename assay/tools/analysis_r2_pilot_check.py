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
_MAX_STATE_BYTES = 16 * 1024 * 1024


def _read_bytes(path: Path, *, maximum: int) -> bytes:
    pinned = common._pin_input(path)
    try:
        info = os.fstat(pinned.file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{path} is not a regular file")
        if info.st_size > maximum:
            raise ValueError(f"{path} exceeds the {maximum}-byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(pinned.file_fd, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise ValueError(f"{path} exceeds the {maximum}-byte limit")
        return b"".join(chunks)
    finally:
        pinned.close()


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
) -> tuple[str, dict[str, dict[str, Any]]]:
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise ValueError("pilot state directory is missing or is a symlink")
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
        raise ValueError("pilot state inventory differs from the selected candidates")
    sentinel_path = state_dir / "PILOT-STATE"
    sentinel = _json(
        _read_bytes(sentinel_path, maximum=_MAX_DEADLINE_BYTES),
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
        record = _json(
            _read_bytes(state_dir / f"{identity}.json", maximum=_MAX_STATE_BYTES),
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
            else:
                mode = outcome["execution_mode"]
                if mode == "full":
                    if checked_evidence.command != "declared" or checked_evidence.started_count is not None:
                        raise ValueError(f"cold full kill {identity} lacks declared-command evidence")
                elif mode == "witness-cold":
                    if checked_evidence.command != "r2" or checked_evidence.started_count is None:
                        raise ValueError(f"cold witness kill {identity} lacks R2 failed-prefix evidence")
                else:
                    raise ValueError(f"cold kill {identity} has unsupported execution mode {mode!r}")
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
    selected = set(selected_ids)
    observed: dict[str, list[tuple[str, str]]] = defaultdict(list)
    latest_resources: dict[str, dict[str, Any]] = {}
    latest_liveness: dict[str, Any] = {}
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
                "terminal_exit": None,
                "end": None,
                "candidate_events": {},
                "resume_event": None,
                "resume_merged_event": None,
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
                or event["candidate_index"] != len(current_run["candidate_ids"])
                or event["candidate_index"] >= current_run["pending_total"]
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
            current_run["candidate_ids"].append(identity)
            current_run["candidate_events"][identity] = event
            observed[identity].append((bucket, execution_mode))
            latest_resources[identity] = resource_evidence.to_dict()
            latest_liveness[identity] = event.get("liveness_resource_evidence")
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
                or event["candidate_total"] != current_run["pending_total"]
                or event.get("reason") is not None
                or not isinstance(event.get("buckets"), dict)
                or set(event["buckets"]) != set(MUTATION_BUCKETS)
                or any(type(value) is not int or value < 0 for value in event["buckets"].values())
                or not _valid_progress_timing(event)
            ):
                raise ValueError(f"progress end event {index} is not a completed mutation sweep")
            expected_buckets = {name: 0 for name in MUTATION_BUCKETS}
            for candidate in current_run["candidate_events"].values():
                expected_buckets[candidate["outcome_bucket"]] += 1
            if event["buckets"] != expected_buckets:
                raise ValueError(f"progress end event {index} buckets differ from its candidate events")
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
                or event.get("destination") is not None
                or event.get("outcome") != summary_r2.get("status")
                or event.get("reason_code") != summary_r2.get("reason_code")
                or not _valid_progress_timing(event)
            ):
                raise ValueError(f"progress terminal event {index} is not a pilot-only completion")
            current_run["terminal_exit"] = event["exit_code"]
        elif kind == "resume_merged":
            if current_run is None or current_run["end"] is None or current_run["terminal_exit"] is not None:
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
    ):
        raise ValueError("final progress run is not a complete exit-6 pilot")
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
) -> None:
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
    if summary.get("schema") != "assay-pilot-summary/1":
        raise ValueError("pilot summary has an unknown schema")
    if summary.get("qualifying") is not False or summary.get("completed") is not True:
        raise ValueError("pilot summary is not a complete non-qualifying measurement")
    if "refusal" in summary:
        raise ValueError("completed pilot summary cannot contain a refusal")
    if set(summary) != {
        "schema", "qualifying", "completed", "lane", "commit", "jobs",
        "requested", "selection_sha256", "judge_sha256",
        "candidates_file_sha256", "state_dir", "r0", "r1", "r2", "r3",
        "buckets", "candidates", "unresolved",
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

    expected_judge_sha256, state_resources_by_id = _check_pilot_state(
        state_dir,
        selected_ids=selected_ids,
        plan_by_id=plan_by_id,
        outcome_by_id=outcome_by_id,
        deadline_sha256=deadline_sha256,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--deadline", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--verdict", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--expected-wheel-sha256", required=True)
    parser.add_argument("--expected-exit-code", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.verdict.exists() or args.verdict.is_symlink():
            raise ValueError("pilot created a verdict artifact; B131 pilot is measurement only")
        verify_pilot(
            plan_raw=_read_bytes(args.plan, maximum=_MAX_JSON_BYTES),
            selection_raw=_read_bytes(args.selection, maximum=_MAX_JSON_BYTES),
            candidates_raw=_read_bytes(args.candidates, maximum=common._CANDIDATE_FILE_LIMIT),
            summary_raw=_read_bytes(args.summary, maximum=_MAX_JSON_BYTES),
            progress_raw=_read_bytes(args.progress, maximum=_MAX_PROGRESS_BYTES),
            deadline_raw=_read_bytes(args.deadline, maximum=_MAX_DEADLINE_BYTES),
            state_dir=args.state_dir,
            expected_commit=args.expected_commit,
            expected_tree=args.expected_tree,
            expected_wheel_sha256=args.expected_wheel_sha256,
            expected_exit_code=args.expected_exit_code,
            repo_root=args.repo_root,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"analysis_r2_pilot_check: {exc}", file=sys.stderr)
        return 2
    print("ANALYSIS_R2_PILOT_VERIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

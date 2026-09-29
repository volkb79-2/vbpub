"""Outcome-oriented campaign closeout built from explicit Assay evidence.

This module deliberately treats the verdict, progress stream, current lane
plan, coverage artifact, and command exit as separate inputs. A progress
stream is diagnostic evidence, never a verdict; counts are never defaulted
to zero when an input is absent or inconsistent.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from . import analysis, git
from . import coverage as coverage_api
from .config import LaneFile, load_lane_file
from .errors import AssayError
from .mutation import PROGRESS_EVENTS, select_mutation_shard
from .safeio import read_bounded_file
from .verdict import MUTATION_BUCKETS

SCHEMA_VERSION = 1
MAX_PROGRESS_BYTES = 64 * 1024 * 1024
MAX_VERDICT_BYTES = 16 * 1024 * 1024
MAX_LANE_FILE_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_PROGRESS_RUNS = 10_000
MAX_DETAIL_LIMIT = 500
MIN_ETA_SAMPLE = 5
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _finite_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{where} must be finite and non-negative")
    return number


def _timestamp(value: Any, where: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{where} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{where} must include a timezone")
    return parsed


def _read_artifact(path: Path, *, limit: int, label: str) -> tuple[bytes, dict]:
    """Read one named artifact as a bounded regular file without following its leaf symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} artifact is missing: {path}") from exc
    except OSError as exc:
        raise ValueError(f"cannot open {label} artifact {path}: {exc}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} artifact is not a regular file: {path}")
        if before.st_size > limit:
            raise ValueError(f"{label} artifact exceeds {limit} bytes: {path}")
        chunks = []
        size = 0
        while True:
            block = os.read(fd, min(1024 * 1024, limit + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > limit:
                raise ValueError(f"{label} artifact exceeds {limit} bytes: {path}")
        raw = b"".join(chunks)
        after = os.fstat(fd)
        if (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns
        ) or size != after.st_size:
            raise ValueError(f"{label} artifact changed while it was being read: {path}")
    finally:
        os.close(fd)
    return raw, {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _read_verified_verdict(path: Path, *, expected: str) -> dict:
    """Verify one bounded verdict read and retain the identity of those bytes."""
    raw, artifact = _read_artifact(path, limit=MAX_VERDICT_BYTES, label="verdict")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"verdict artifact is not UTF-8: {path}") from exc
    document = analysis._json(text)
    failures = analysis.verify_text(text)
    if failures:
        raise ValueError("invalid Assay verdict: " + "; ".join(failures))
    integrity = document.get("worktree_integrity") or {}
    overridden = integrity.get("overridden_dirty_paths", [])
    if overridden:
        raise ValueError(
            "verdict records --allow-dirty overrides; campaign closeout refuses "
            f"these uncommitted paths: {', '.join(overridden)}"
        )
    if document.get("commit") != expected:
        raise ValueError(
            f"verdict commit {document.get('commit')} differs from {expected}"
        )
    return {"artifact": artifact, "verdict": document}


def _bind_lane_file(path: Path, *, root: Path, commit: str) -> None:
    """Require the named lane file to be the exact regular file in *commit*."""
    if path.is_symlink():
        raise ValueError(f"lane file is a symlink: {path}")
    try:
        relative = path.resolve(strict=True).relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise ValueError("lane file must be inside the expected worktree") from exc
    mode_type = git.run(root, "ls-tree", "-z", commit, "--", relative)
    entries = [entry for entry in mode_type.split("\0") if entry]
    if len(entries) != 1:
        raise ValueError(f"lane file is not a single committed path: {relative}")
    metadata, _separator, recorded_path = entries[0].partition("\t")
    fields = metadata.split()
    if (
        len(fields) != 3
        or fields[0] not in ("100644", "100755")
        or fields[1] != "blob"
        or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", fields[2])
    ):
        raise ValueError(f"lane file is not a committed regular file: {relative}")
    committed = git.run(root, "show", f"{commit}:{relative}")
    try:
        raw, _artifact = _read_artifact(path, limit=MAX_LANE_FILE_BYTES, label="lane file")
        current = raw.decode("utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"cannot read lane file {path}: {exc}") from exc
    if recorded_path != relative or current != committed:
        raise ValueError("lane file bytes differ from the expected committed tree")


def _read_progress(path: Path, *, expected: str, lane: str) -> tuple[dict, list[dict]]:
    raw_bytes, artifact = _read_artifact(
        path, limit=MAX_PROGRESS_BYTES, label="progress"
    )
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"progress artifact is not UTF-8: {path}") from exc
    if not raw.endswith("\n"):
        raise ValueError("progress artifact has a torn final record (missing newline)")
    runs: list[dict] = []
    all_runs: list[dict] = []
    current: dict | None = None
    for line_number, line in enumerate(raw.splitlines(), 1):
        event = analysis._json(line)
        if not isinstance(event, dict) or event.get("event") not in PROGRESS_EVENTS:
            raise ValueError(f"malformed progress event at line {line_number}")
        if event["event"] == "run":
            if current is not None and current["line_end"] is None:
                current["line_end"] = line_number - 1
            commit = event.get("commit")
            if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
                raise ValueError(f"progress run lacks a full commit at line {line_number}")
            if event.get("lane") != lane:
                raise ValueError(
                    f"progress run at line {line_number} names lane "
                    f"{event.get('lane')!r}, expected {lane!r}"
                )
            _timestamp(event.get("started"), f"progress run started at line {line_number}")
            current = {
                "line_start": line_number,
                "line_end": None,
                "run": event,
                "events": [],
                "sweep_end_seen": False,
                "terminal_seen": False,
            }
            all_runs.append(current)
            if commit == expected:
                runs.append(current)
            if len(all_runs) > MAX_PROGRESS_RUNS:
                raise ValueError(f"progress contains more than {MAX_PROGRESS_RUNS} runs")
            continue
        if current is None:
            raise ValueError(f"progress event precedes first run at line {line_number}")
        event_commit = event.get("commit")
        if event_commit is not None and event_commit != current["run"]["commit"]:
            raise ValueError(f"progress event has conflicting commit at line {line_number}")
        if event.get("lane") is not None and event["lane"] != lane:
            raise ValueError(f"progress event names wrong lane at line {line_number}")
        if current["terminal_seen"]:
            raise ValueError(f"progress contains events after verdict_written at line {line_number}")
        if current["sweep_end_seen"] and event["event"] != "verdict_written":
            raise ValueError(f"progress contains sweep events after end at line {line_number}")
        if event["event"] == "end":
            current["sweep_end_seen"] = True
        elif event["event"] == "verdict_written":
            current["terminal_seen"] = True
        current["events"].append((line_number, event))
    if not all_runs:
        raise ValueError(f"progress has no run at expected commit {expected} for lane {lane!r}")
    if all_runs[-1]["run"]["commit"] != expected:
        raise ValueError(
            f"latest progress run is for commit {all_runs[-1]['run']['commit']}, "
            f"expected {expected}"
        )
    if not runs:
        raise ValueError(f"progress has no run at expected commit {expected} for lane {lane!r}")
    return artifact, runs


def _lane_plan(lane_file: LaneFile, lane_name: str, request_base: str | None) -> dict:
    """Use Assay's own planner so analysis and execution share candidate identity."""
    from . import cli as assay_cli

    output = io.StringIO()
    lane = lane_file.lane(lane_name)
    declared_request = (
        request_base
        if lane.judge is not None and lane.judge.base_source == "request"
        else None
    )
    args = argparse.Namespace(
        file=lane_file.path,
        lane=lane_name,
        operators=None,
        allow_dirty=False,
        shard=None,
        reuse_from=None,
        request_base=declared_request,
    )
    code = assay_cli._cmd_plan(args, output)
    if code != 0:
        raise ValueError(f"assay plan exited {code}: {output.getvalue().strip()}")
    document = analysis._json(output.getvalue())
    if not isinstance(document, dict) or document.get("status") not in ("ok", "unsupported"):
        raise ValueError("Assay planner returned a malformed plan")
    if document.get("status") == "ok":
        from . import runner

        expected_base = runner._resolve_declared_base(
            lane_file.project_root,
            runner.resolve_base_declaration(lane, declared_request),
        )
        # The planner does not put the resolved base in its public JSON yet;
        # compare it here with the base independently recorded by the verdict.
        document["_resolved_base"] = expected_base
    return document


def _coverage_from_verdict(document: dict) -> dict:
    claims = document.get("claims", [])
    r1 = next((claim for claim in claims if claim.get("rigor") == "R1"), None)
    r2 = next((claim for claim in claims if claim.get("rigor") == "R2"), None)
    coverage = None if r1 is None else r1.get("coverage")
    r2_mutation = None if r2 is None else r2.get("mutation")
    summary: dict[str, Any] = {
        "status": "not_judged" if coverage is None else "from_verified_verdict",
        "r1": None,
        "r2_floor": None,
    }
    judgment = document.get("judgment", {})
    if coverage is not None:
        r1_policy = judgment.get("r1", {})
        summary["r1"] = {
            "status": r1.get("status"),
            "fail_under": r1_policy.get("fail_under"),
            "covered": coverage.get("covered"),
            "executable": coverage.get("executable"),
            "pct": coverage.get("pct"),
            "branches_covered": coverage.get("branches_covered"),
            "branches_total": coverage.get("branches_total"),
            "branch_capability": coverage.get("branch_capability"),
            "missing_lines": coverage.get("missing_lines"),
            "unclassified_lines": coverage.get("unclassified_lines"),
            "excluded_lines": coverage.get("excluded_lines"),
            "missing_branch_lines": coverage.get("missing_branch_lines"),
        }
    if r2_mutation is not None:
        summary["r2_floor"] = judgment.get("r2", {}).get("fail_under")
    return summary


def _coverage_artifact_summary(
    *, lane_file: LaneFile, lane, verdict: dict, root: Path,
    path: Path | None
) -> dict:
    """Re-derive the judged R1 facts from the lane's declared coverage bytes."""
    policy = None if lane.judge is None else lane.judge.coverage
    verdict_summary = _coverage_from_verdict(verdict)
    if policy is None:
        if path is not None:
            raise ValueError("coverage artifact was supplied for a lane with no R1 coverage declaration")
        return {
            **verdict_summary,
            "artifact_status": "not_applicable",
            "branch_arc_detail_status": "not_applicable",
            "missing_branch_arcs": None,
        }
    declared = (lane_file.project_root / policy.artifact).resolve(strict=False)
    supplied_path = declared if path is None else (
        path if path.is_absolute() else lane_file.project_root / path
    )
    supplied = supplied_path.resolve(strict=False)
    if supplied != declared:
        raise ValueError(
            f"coverage artifact {supplied} differs from lane-declared artifact {declared}"
        )
    raw = read_bounded_file(lane_file.project_root, policy.artifact)
    if raw is None:
        return {
            **verdict_summary,
            "artifact_status": "missing",
            "artifact_error": f"declared coverage artifact is missing: {declared}",
            "branch_arc_detail_status": "unavailable_without_artifact",
            "missing_branch_arcs": None,
        }
    profile = coverage_api.parse_coverage_artifact(
        raw, declared_format=policy.format, producer=policy.producer
    )
    artifact = {
        "path": str(declared),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    r1_claim = next(
        (claim for claim in verdict.get("claims", []) if claim.get("rigor") == "R1"),
        None,
    )
    if r1_claim is None:
        raise ValueError("lane declares R1 but the verified verdict has no R1 claim")
    r1_policy = (verdict.get("judgment") or {}).get("r1") or {}
    judge = lane.judge
    assert judge is not None
    effective_mode = judge.mode or "changed_lines"
    expected_policy = {
        "coverage_artifact": policy.artifact,
        "coverage_format": policy.format,
        "coverage_producer": policy.producer,
        "fail_under": judge.fail_under,
        "allow_excluded": bool(judge.allow_excluded),
        "mode": effective_mode,
        "targets": list(judge.targets or ()) if effective_mode == "whole_target" else None,
        "require_branch": bool(judge.require_branch),
        "allow_test_path_targets": bool(judge.allow_test_path_targets),
    }
    for field, expected_value in expected_policy.items():
        actual = r1_policy.get(
            field, False if field == "allow_test_path_targets" else None
        )
        if actual != expected_value:
            raise ValueError(
                f"R1 verdict policy {field!r} differs from the named lane declaration"
            )
    from . import cli as assay_cli, runner

    adapter = assay_cli._resolve_declared_adapters(lane)
    if adapter is None:
        raise ValueError("R1 lane resolves no coverage adapter")
    resolved = ((verdict.get("judgment") or {}).get("resolved") or {}).get("base")
    observed_r1 = runner.evaluate_r1(
        lane,
        repo=root,
        project_root=lane_file.project_root,
        base=lane.judge.base,
        resolved_base=resolved,
        adapter=adapter,
        profile=profile,
    ).to_dict()
    for field in ("status", "reason_code", "coverage"):
        if observed_r1.get(field) != r1_claim.get(field):
            raise ValueError(
                f"coverage artifact re-evaluation disagrees with the verified "
                f"R1 verdict field {field!r}"
            )

    coverage_claim = r1_claim.get("coverage")
    expected_capability = None if coverage_claim is None else coverage_claim.get("branch_capability")
    observed_capability = coverage_api.derive_branch_capability(profile)
    missing_branch_arcs = None
    arc_status = "unavailable"
    if expected_capability == "reported":
        if observed_capability != "reported":
            raise ValueError("coverage artifact branch capability differs from verified verdict")
        if policy.format == "coverage-py-json":
            document = analysis._json(raw.decode("utf-8"))
            files = document.get("files")
            if not isinstance(files, dict):
                raise ValueError("coverage.py artifact has no files object")
            missing_by_file = (coverage_claim or {}).get("missing_branch_lines", {})
            repo_top = root.resolve()
            project_root = lane_file.project_root.resolve()
            arcs = []
            for file_path, lines in missing_by_file.items():
                raw_path = None
                for key in files:
                    candidate = Path(key)
                    absolute = candidate if candidate.is_absolute() else project_root / candidate
                    try:
                        normalized = absolute.resolve().relative_to(repo_top).as_posix()
                    except (OSError, ValueError):
                        continue
                    if normalized == file_path:
                        if raw_path is not None:
                            raise ValueError(
                                f"coverage artifact has multiple keys for judged file {file_path!r}"
                            )
                        raw_path = key
                if raw_path is None:
                    raise ValueError(f"coverage artifact lacks judged file {file_path!r}")
                record = files.get(raw_path)
                if not isinstance(record, dict):
                    raise ValueError(f"coverage artifact lacks judged file {file_path!r}")
                raw_arcs = record.get("missing_branches")
                if not isinstance(raw_arcs, list):
                    raise ValueError(f"coverage artifact lacks missing branch arcs for {file_path!r}")
                rows = []
                arc_counts: Counter[int] = Counter()
                for pair in raw_arcs:
                    if (not isinstance(pair, list) or len(pair) != 2
                            or any(isinstance(value, bool) or not isinstance(value, int) for value in pair)):
                        raise ValueError(f"coverage artifact has a malformed branch arc for {file_path!r}")
                    source, destination = pair
                    if source in lines:
                        arc_counts[source] += 1
                        rows.append({"path": file_path, "source_line": source,
                                     "destination": destination})
                if lines and not rows:
                    raise ValueError(
                        f"coverage artifact has no missing arcs for verdict-reported branch lines in {file_path!r}"
                    )
                normalized_record = profile.files.get(raw_path)
                if normalized_record is None or normalized_record.branches is None:
                    raise ValueError(f"coverage artifact lacks normalized branch counts for {file_path!r}")
                for source in lines:
                    branch_counts = normalized_record.branches.by_line.get(source)
                    if branch_counts is None or arc_counts[source] != branch_counts[1] - branch_counts[0]:
                        raise ValueError(
                            f"coverage artifact missing-arc destinations do not match "
                            f"its parsed branch counts for {file_path}:{source}"
                        )
                arcs.extend(rows)
            missing_branch_arcs = arcs
            arc_status = "exact_from_coverage_py_artifact_and_reverified_r1"
        else:
            arc_status = "format_does_not_expose_exact_destinations"
    elif expected_capability == "unavailable" and observed_capability != "unavailable":
        raise ValueError("coverage artifact reports branch arcs that the verdict says were unavailable")
    elif expected_capability not in (None, "reported", "unavailable"):
        raise ValueError("verified verdict has an unknown branch_capability")
    return {
        **verdict_summary,
        "artifact_status": "parsed_and_reverified_r1",
        "artifact": artifact,
        "branch_arc_detail_status": arc_status,
        "missing_branch_arcs": missing_branch_arcs,
    }


def _candidate_outcomes(document: dict) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    claims = document.get("claims", [])
    r2 = next((claim for claim in claims if claim.get("rigor") == "R2"), None)
    if r2 is None or "mutation" not in r2:
        return {}, {}
    mutation = r2["mutation"]
    buckets: dict[str, list[dict]] = {}
    by_id: dict[str, dict] = {}
    for bucket in MUTATION_BUCKETS:
        entries = mutation.get(bucket, [])
        if not isinstance(entries, list):
            raise ValueError(f"verdict mutation.{bucket} is not a list")
        buckets[bucket] = entries
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"verdict mutation.{bucket} contains a non-object outcome")
            candidate_id = entry.get("candidate_id")
            if candidate_id is not None:
                if not isinstance(candidate_id, str) or not _SHA256_RE.fullmatch(candidate_id):
                    raise ValueError(f"verdict mutation.{bucket} has an invalid candidate_id")
                if candidate_id in by_id:
                    raise ValueError(f"candidate {candidate_id} appears in more than one outcome")
                by_id[candidate_id] = {"bucket": bucket, **entry}
    return buckets, by_id


def _run_summary(
    run: dict,
    *,
    plan_by_id: dict[str, dict],
    plan_total: int | None,
    verdict_by_id: dict[str, dict],
    compare_verdict: bool,
) -> dict:
    events = run["events"]
    candidates: dict[str, dict] = {}
    candidate_positions: set[int] = set()
    candidates_event = None
    shard_event = None
    resume_event = None
    resume_merged_event = None
    sweep_end = None
    terminal = None
    phase = "header"
    for line_number, event in events:
        kind = event["event"]
        if kind == "candidates":
            if candidates_event is not None:
                raise ValueError("progress run repeats its candidates milestone")
            if phase not in ("header", "resume"):
                raise ValueError("progress candidates milestone is out of order")
            candidates_event = event
            phase = "sweep"
        elif kind == "shard":
            if shard_event is not None:
                raise ValueError("progress run repeats its shard milestone")
            if phase != "header":
                raise ValueError("progress shard milestone is out of order")
            shard_event = event
        elif kind == "resume":
            if resume_event is not None:
                raise ValueError("progress run repeats its resume milestone")
            if phase not in ("header", "shard"):
                raise ValueError("progress resume milestone is out of order")
            resume_event = event
            phase = "resume"
        elif kind == "resume_merged":
            if resume_merged_event is not None:
                raise ValueError("progress run repeats its resume_merged milestone")
            if phase != "sweep":
                raise ValueError("progress resume_merged milestone is out of order")
            resume_merged_event = event
        elif kind == "candidate":
            if phase != "sweep":
                raise ValueError("progress candidate event precedes its candidates milestone")
            candidate_id = event.get("candidate_id")
            if not isinstance(candidate_id, str) or not _SHA256_RE.fullmatch(candidate_id):
                raise ValueError("progress candidate event has an invalid candidate_id")
            if candidate_id in candidates:
                raise ValueError(f"progress run repeats candidate {candidate_id}")
            if candidate_id not in plan_by_id:
                raise ValueError("progress contains a candidate outside the current plan")
            if event.get("outcome_bucket") not in MUTATION_BUCKETS:
                raise ValueError("progress candidate event has an unknown outcome_bucket")
            row = plan_by_id.get(candidate_id)
            for field in ("path", "lineno", "operator", "description", "start_byte", "end_byte"):
                if event.get(field) != row.get(field):
                    raise ValueError(
                        f"progress candidate {candidate_id} {field} differs from the current plan"
                    )
            index = event.get("candidate_index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError("progress candidate event has an invalid candidate_index")
            if index in candidate_positions:
                raise ValueError(f"progress run repeats candidate_index {index}")
            candidate_positions.add(index)
            elapsed = _finite_number(event.get("elapsed_seconds"), "candidate elapsed_seconds")
            emitted_at = _timestamp(event.get("emitted_at"), "candidate emitted_at")
            outcome = verdict_by_id.get(candidate_id) if compare_verdict else None
            if compare_verdict:
                if outcome is None:
                    raise ValueError(
                        f"progress candidate {candidate_id} has no corresponding "
                        "outcome in the verified verdict"
                    )
                if event["outcome_bucket"] != outcome["bucket"]:
                    raise ValueError(
                        f"progress and verdict disagree for candidate {candidate_id}"
                    )
            candidates[candidate_id] = {
                "outcome_bucket": event["outcome_bucket"],
                "elapsed_seconds": elapsed,
                "emitted_at": emitted_at.isoformat(),
            }
        elif kind == "end":
            if sweep_end is not None:
                raise ValueError("progress run repeats its end milestone")
            if phase not in ("header", "sweep"):
                raise ValueError("progress end milestone is out of order")
            sweep_end = event
            phase = "ended"
        elif kind == "verdict_written":
            if terminal is not None:
                raise ValueError("progress run repeats its verdict_written terminal")
            if phase == "terminal":
                raise ValueError("progress run repeats its terminal")
            terminal = event
            phase = "terminal"
        elif kind in {"run", "plan", "baseline", "test", "snapshot_materialized",
                      "command_started", "command_running", "command_finished",
                      "coverage_parsed"}:
            if phase in ("ended", "terminal"):
                raise ValueError(f"progress event {kind!r} follows the run terminal")
        else:
            raise ValueError(f"progress event {kind!r} is not supported by campaign analysis")

    selected_total = None if candidates_event is None else candidates_event.get("selected_total")
    pending_total = None if candidates_event is None else candidates_event.get("pending_total")
    candidate_total = None if candidates_event is None else candidates_event.get("candidate_total")
    plan_ids = list(plan_by_id)
    if shard_event is None:
        selected_ids = set(plan_ids)
        shard = None
    else:
        index = shard_event.get("shard_index")
        count = shard_event.get("shard_count")
        shard_selected = shard_event.get("selected_total")
        if (
            isinstance(index, bool) or not isinstance(index, int)
            or isinstance(count, bool) or not isinstance(count, int)
            or isinstance(shard_selected, bool) or not isinstance(shard_selected, int)
            or shard_selected < 0
        ):
            raise ValueError("progress shard milestone is malformed")
        try:
            positions = select_mutation_shard(plan_ids, index=index, count=count)
        except ValueError as exc:
            raise ValueError(f"progress shard milestone is invalid: {exc}") from exc
        selected_ids = {plan_ids[position] for position in positions}
        if shard_selected != len(selected_ids):
            raise ValueError("progress shard selected_total differs from its deterministic assignment")
        shard = {"index": index, "count": count}

    for name, value in (("selected_total", selected_total), ("pending_total", pending_total),
                        ("candidate_total", candidate_total)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            if candidates_event is not None:
                raise ValueError(f"progress {name} must be a non-negative integer")
    resume_total = 0 if resume_event is None else resume_event.get("resumed_total")
    rejected_total = 0 if resume_event is None else resume_event.get("rejected_total")
    rejudged_total = 0 if resume_event is None else resume_event.get("rejudged_total")
    for name, value in (("resumed_total", resume_total), ("rejected_total", rejected_total),
                        ("rejudged_total", rejudged_total)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"progress {name} must be a non-negative integer")
    if candidates_event is not None:
        if plan_total is None or candidate_total != plan_total:
            raise ValueError("progress candidate_total differs from the current plan")
        if selected_total != len(selected_ids):
            raise ValueError("progress selected_total differs from the current selected inventory")
        if pending_total > selected_total or selected_total != resume_total + pending_total:
            raise ValueError("progress selected, resumed, and pending totals do not reconcile")
        if len(candidates) > pending_total:
            raise ValueError("progress candidate events exceed pending_total")
        if candidate_positions and (
            min(candidate_positions) < 0 or max(candidate_positions) >= pending_total
        ):
            raise ValueError("progress candidate_index is outside the pending candidate range")
        if candidate_positions and candidate_positions != set(range(len(candidate_positions))):
            raise ValueError("progress candidate_index values are not a contiguous execution sequence")
        if any(event.get("candidate_total") != pending_total for _line, event in events
               if event.get("event") == "candidate"):
            raise ValueError("progress candidate events disagree with pending_total")
        if len(candidates) > pending_total:
            raise ValueError("progress candidate events exceed pending_total")
    elif candidates or resume_event is not None or resume_merged_event is not None or shard_event is not None:
        raise ValueError("progress has mutation events without a candidates milestone")

    if resume_merged_event is not None:
        if resume_event is None:
            raise ValueError("progress resume_merged milestone lacks a resume milestone")
        merged_total = resume_merged_event.get("resumed_total")
        if (isinstance(merged_total, bool) or not isinstance(merged_total, int)
                or merged_total != resume_total):
            raise ValueError("progress resume_merged count differs from its resume milestone")

    if sweep_end is not None:
        end_total = sweep_end.get("candidate_total")
        end_buckets = sweep_end.get("buckets")
        if isinstance(end_total, bool) or not isinstance(end_total, int) or end_total < 0:
            raise ValueError("progress end candidate_total is not a non-negative integer")
        if not isinstance(end_buckets, dict) or set(end_buckets) != set(MUTATION_BUCKETS):
            raise ValueError("progress end buckets do not match the canonical mutation outcomes")
        for bucket, count in end_buckets.items():
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"progress end bucket {bucket!r} is not a non-negative integer")
        if sweep_end.get("reason") is None and candidates_event is None:
            raise ValueError("progress records a completed sweep without a candidates milestone")
        if sweep_end.get("reason") is not None and candidates_event is not None:
            raise ValueError("progress records a pre-submission end after candidates were selected")
        if sweep_end.get("reason") is None:
            if candidates_event is None:
                raise ValueError("progress completed a mutation sweep without a candidates milestone")
            if end_total != candidate_total:
                raise ValueError("progress end candidate_total differs from candidate plan total")
            if sum(end_buckets.values()) != selected_total:
                raise ValueError("progress end buckets do not reconcile with selected_total")
            if compare_verdict:
                verdict_counts = {
                    bucket: sum(
                        1 for candidate in verdict_by_id.values()
                        if candidate["bucket"] == bucket
                        and candidate.get("candidate_id") in selected_ids
                    )
                    for bucket in MUTATION_BUCKETS
                }
                if end_buckets != verdict_counts:
                    raise ValueError("progress end buckets disagree with verified verdict outcomes")
        else:
            if candidates_event is not None:
                raise ValueError("progress records a pre-submission stop after selecting candidates")
    if terminal is not None and (
        not isinstance(terminal.get("exit_code"), int)
        or isinstance(terminal.get("exit_code"), bool)
    ):
        raise ValueError("progress terminal verdict_written event has no integer exit_code")
    run_start = _timestamp(run["run"].get("started"), "progress run started")
    terminal_time = None
    terminal_summary = None
    if terminal is not None:
        terminal_time = _timestamp(terminal.get("emitted_at"), "verdict_written emitted_at")
        terminal_summary = {
            "event": "verdict_written",
            "outcome": terminal.get("outcome"),
            "exit_code": terminal.get("exit_code"),
            "reason_code": terminal.get("reason_code"),
            "emitted_at": terminal_time.isoformat(),
            "elapsed_s": _finite_number(
                terminal.get("elapsed_s"), "verdict_written elapsed_s"
            ),
        }
    return {
        "run_id": f"progress-line-{run['line_start']}:{run_start.isoformat()}",
        "started": run_start.isoformat(),
        "ended": None if terminal_time is None else terminal_time.isoformat(),
        "elapsed_seconds": None if terminal is None else _finite_number(
            terminal.get("elapsed_s"), "verdict_written elapsed_s"
        ),
        "progress_line": run["line_start"],
        "commit": run["run"]["commit"],
        "lane": run["run"].get("lane"),
        "rigor": run["run"].get("rigor"),
        "candidate_total": candidate_total,
        "candidate_milestone": candidates_event is not None,
        "selected_total": selected_total,
        "pending_total": pending_total,
        "resumed_total": resume_total,
        "rejected_total": rejected_total,
        "rejudged_total": rejudged_total,
        "shard": shard,
        "selected_ids": sorted(selected_ids) if candidates_event is not None else [],
        "fresh_candidate_events": len(candidates),
        "sweep_end_buckets": sweep_end.get("buckets") if sweep_end is not None else None,
        "sweep_end_reason": sweep_end.get("reason") if sweep_end is not None else None,
        "sweep_end_present": sweep_end is not None,
        "terminal": terminal_summary,
        "candidate_ids": sorted(candidates),
        "_candidate_events": candidates,
    }


def _details(
    buckets_in_verdict: dict[str, list[dict]],
    plan_by_id: dict[str, dict],
    *,
    buckets: list[str],
    path_filter: str | None,
    offset: int,
    limit: int,
) -> dict:
    rows = []
    for bucket in buckets:
        for outcome in buckets_in_verdict.get(bucket, []):
            candidate_id = outcome.get("candidate_id")
            site = plan_by_id.get(candidate_id, outcome)
            path = site.get("path")
            if path_filter is not None and (
                not isinstance(path, str) or not path.startswith(path_filter)
            ):
                continue
            execution = outcome.get("execution") or {}
            rows.append({
                "candidate_id": candidate_id,
                "outcome": bucket,
                "path": path,
                "lineno": site.get("lineno"),
                "operator": site.get("operator"),
                "description": site.get("description"),
                "start_byte": site.get("start_byte"),
                "end_byte": site.get("end_byte"),
                "execution_mode": execution.get("mode"),
            })
    rows.sort(key=lambda row: (
        row["path"] or "", row["lineno"] or 0, row["candidate_id"] or ""
    ))
    total = len(rows)
    page = rows[offset:offset + limit]
    next_offset = offset + len(page) if offset + len(page) < total else None
    return {
        "matching_total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
        "candidates": page,
    }


def campaign(
    *,
    worktree: Path,
    lane_file_path: Path,
    lane_name: str,
    expected_commit: str,
    verdict_path: Path,
    progress_path: Path,
    command_exit: int | None,
    log_path: Path | None = None,
    coverage_path: Path | None = None,
    buckets: list[str] | None = None,
    path_filter: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    """Build one deterministic closeout; all paths are explicit inputs."""
    analysis._report_commit(expected_commit)
    if command_exit is not None and (
        isinstance(command_exit, bool) or not isinstance(command_exit, int) or command_exit < 0
    ):
        raise ValueError("command exit must be a non-negative integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("detail offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_DETAIL_LIMIT:
        raise ValueError(f"detail limit must be between 1 and {MAX_DETAIL_LIMIT}")
    selected_buckets = list(MUTATION_BUCKETS) if buckets is None else buckets
    if not selected_buckets or any(bucket not in MUTATION_BUCKETS for bucket in selected_buckets):
        raise ValueError(f"outcome filters must use {list(MUTATION_BUCKETS)}")
    if len(selected_buckets) != len(set(selected_buckets)):
        raise ValueError("outcome filters contain duplicates")

    root = git.repo_top(worktree)
    head, tree = analysis._identity(root, expected_commit)
    _bind_lane_file(lane_file_path, root=root, commit=head)
    lane_file = load_lane_file(lane_file_path)
    lane = lane_file.lane(lane_name)
    verdict_result = _read_verified_verdict(verdict_path, expected=head)
    verdict = verdict_result["verdict"]
    if verdict.get("lane") != lane_name:
        raise ValueError(f"verdict lane {verdict.get('lane')!r} differs from requested {lane_name!r}")
    if verdict.get("commit") != head:
        raise ValueError("verdict commit differs from current expected HEAD")

    r2_policy = (verdict.get("judgment") or {}).get("r2") or {}
    r1_policy = (verdict.get("judgment") or {}).get("r1") or {}
    if lane.judge is not None and lane.judge.coverage is not None:
        declared_coverage = lane.judge.coverage
        if (
            r1_policy.get("coverage_artifact") != declared_coverage.artifact
            or r1_policy.get("coverage_format") != declared_coverage.format
            or r1_policy.get("fail_under") != declared_coverage.fail_under
        ):
            raise ValueError("R1 verdict policy differs from the named lane declaration")
    native_r2 = (
        "R2" in lane.rigor
        and lane.judge is not None
        and lane.judge.mutation is not None
        and lane.judge.mutation.format is None
        and r2_policy.get("producer", "native") == "native"
    )
    if native_r2:
        mutation_policy = lane.judge.mutation
        assert mutation_policy is not None
        expected_policy = {
            "jobs": mutation_policy.jobs,
            "max_mutants": mutation_policy.max_mutants,
            "operators": list(mutation_policy.operators),
            "mode": lane.judge.mode,
        }
        for key, expected_value in expected_policy.items():
            if r2_policy.get(key) != expected_value:
                raise ValueError(
                    f"R2 verdict policy {key} differs from the named lane declaration"
                )
    plan: dict[str, Any]
    if native_r2:
        resolved_base = ((verdict.get("judgment") or {}).get("resolved") or {}).get("base")
        plan = _lane_plan(lane_file, lane_name, resolved_base)
        if plan.get("status") == "ok" and plan.get("_resolved_base") != resolved_base:
            raise ValueError("reconstructed plan base differs from the verified verdict")
    elif "R2" in lane.rigor:
        plan = {"status": "not_supported", "reason": "ingested or non-native mutation lane"}
    else:
        plan = {"status": "not_applicable", "reason": "lane declares no R2 mutation judgment"}

    buckets_in_verdict, verdict_by_id = _candidate_outcomes(verdict)
    plan_rows = plan.get("candidates", []) if isinstance(plan.get("candidates", []), list) else []
    plan_by_id = {
        row["id"]: row for row in plan_rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    if len(plan_by_id) != len(plan_rows):
        raise ValueError("current lane plan contains duplicate or malformed candidate identities")
    plan_ids = list(plan_by_id)
    plan_id_set = set(plan_ids)
    plan_total = len(plan_ids) if plan.get("status") == "ok" else None

    r2_claim = next(
        (claim for claim in verdict.get("claims", []) if claim.get("rigor") == "R2"),
        None,
    )
    mutation_claim = (
        r2_claim.get("mutation")
        if isinstance(r2_claim, dict) and isinstance(r2_claim.get("mutation"), dict)
        else None
    )
    raw_verdict_ids = None if mutation_claim is None else mutation_claim.get("candidate_ids")
    pre_submission_limit = (
        native_r2
        and mutation_claim is not None
        and r2_claim.get("reason_code") == "MUTANT_LIMIT_EXCEEDED"
        and mutation_claim.get("total") == 0
    )
    if (
        native_r2
        and mutation_claim is not None
        and raw_verdict_ids is None
        and not pre_submission_limit
    ):
        raise ValueError("native R2 verdict has no v13 candidate_ids inventory")
    if raw_verdict_ids is not None and (
        any(not isinstance(item, str) or not _SHA256_RE.fullmatch(item) for item in raw_verdict_ids)
        or len(raw_verdict_ids) != len(set(raw_verdict_ids))
    ):
        raise ValueError("verdict candidate_ids inventory is malformed or duplicated")
    verdict_ids = set() if raw_verdict_ids is None else set(raw_verdict_ids)
    shard_index = r2_policy.get("shard_index")
    shard_count = r2_policy.get("shard_count")
    if native_r2 and mutation_claim is not None:
        if pre_submission_limit:
            if verdict_by_id or any(buckets_in_verdict.values()):
                raise ValueError(
                    "pre-submission mutant-limit verdict unexpectedly has candidate outcomes"
                )
            expected_ids = set()
        else:
            expected_ids = (
                plan_id_set
                if shard_index is None
                else {plan_ids[position] for position in select_mutation_shard(
                    plan_ids, index=shard_index, count=shard_count
                )}
            )
            if verdict_ids != expected_ids:
                raise ValueError("verdict candidate inventory differs from the current lane plan")
            if set(verdict_by_id) != verdict_ids:
                raise ValueError("verdict outcome buckets do not exhaust its candidate inventory")
            for candidate_id, outcome in verdict_by_id.items():
                row = plan_by_id.get(candidate_id)
                if row is None:
                    raise ValueError(f"verdict candidate {candidate_id} is absent from the current plan")
                for field in ("path", "lineno", "operator", "description", "start_byte", "end_byte"):
                    if outcome.get(field) != row.get(field):
                        raise ValueError(
                            f"verdict candidate {candidate_id} {field} differs from the current plan"
                        )
    else:
        expected_ids = verdict_ids

    progress_artifact, progress_runs = _read_progress(
        progress_path, expected=expected_commit, lane=lane_name
    )
    run_summaries = [
        _run_summary(
            run,
            plan_by_id=plan_by_id,
            plan_total=plan_total,
            verdict_by_id=verdict_by_id,
            compare_verdict=index == len(progress_runs) - 1,
        )
        for index, run in enumerate(progress_runs)
    ]
    latest = run_summaries[-1]
    if latest["selected_ids"] and set(latest["selected_ids"]) != expected_ids:
        raise ValueError("latest progress selected inventory differs from the verified verdict")
    if not set(latest["candidate_ids"]) <= expected_ids:
        raise ValueError("latest progress run includes candidates outside the verdict's selected scope")
    terminal = latest["terminal"]
    terminal_matches_verdict = terminal is not None and (
        terminal.get("outcome") == verdict.get("outcome")
        and terminal.get("exit_code") == verdict.get("exit_code")
        and terminal.get("reason_code") == verdict.get("reason_code")
    )
    if terminal is not None and not terminal_matches_verdict:
        raise ValueError("latest progress terminal disagrees with the verified verdict")

    actual_exit_matches = command_exit is not None and command_exit == verdict.get("exit_code")
    if command_exit is not None and not actual_exit_matches:
        raise ValueError(
            f"observed command exit {command_exit} differs from verified verdict exit "
            f"{verdict.get('exit_code')}"
        )
    artifacts = {
        "verdict": verdict_result["artifact"],
        "progress": progress_artifact,
    }
    _lane_bytes, artifacts["lane_file"] = _read_artifact(
        lane_file.path, limit=MAX_LANE_FILE_BYTES, label="lane file"
    )
    if log_path is not None:
        _log_bytes, artifacts["log"] = _read_artifact(
            log_path, limit=MAX_LOG_BYTES, label="gate log"
        )
    coverage_summary = _coverage_artifact_summary(
        lane_file=lane_file, lane=lane, verdict=verdict, root=root,
        path=coverage_path,
    )
    if coverage_summary.get("artifact") is not None:
        artifacts["coverage"] = coverage_summary["artifact"]

    outcome_counts = {bucket: len(buckets_in_verdict.get(bucket, [])) for bucket in MUTATION_BUCKETS}
    total_outcomes = sum(outcome_counts.values())
    fresh_replayed = 0
    fully_executed = 0
    for outcome in verdict_by_id.values():
        mode = (outcome.get("execution") or {}).get("mode")
        if mode == "witness-prefix":
            fresh_replayed += 1
        elif mode == "full":
            fully_executed += 1

    current_pending = None
    if native_r2:
        if pre_submission_limit:
            current_pending = plan_total
        elif (
            latest["candidate_milestone"]
            and latest["sweep_end_present"]
            and latest["sweep_end_reason"] is None
            and set(latest["selected_ids"]) == expected_ids
            and verdict_ids == expected_ids
        ):
            # The terminal sweep and verifier-accepted inventory establish
            # completion even when lane-wide timeout handling synthesizes
            # budget_exceeded outcomes for candidates with no duration event.
            current_pending = 0
        elif latest["pending_total"] is not None:
            current_pending = max(
                0, latest["pending_total"] - latest["fresh_candidate_events"]
            )
        elif plan_total is not None:
            current_pending = plan_total
    completed_events = []
    for candidate_id, event in latest["_candidate_events"].items():
        outcome = verdict_by_id.get(candidate_id, {})
        execution_mode = (outcome.get("execution") or {}).get("mode", "full")
        if (
            event["outcome_bucket"] in ("killed", "survived", "equivalent")
            and execution_mode == "full"
            and event["elapsed_seconds"] > 0
        ):
            completed_events.append(event)
    candidate_samples = [event["elapsed_seconds"] for event in completed_events]
    sample_times = [
        _timestamp(event["emitted_at"], "candidate emitted_at")
        for event in completed_events
    ]
    latest_outcome_counts = Counter(
        event["outcome_bucket"] for event in latest["_candidate_events"].values()
    )
    excluded_counts = {
        name: latest_outcome_counts.get(name, 0)
        for name in ("crashed", "hung", "budget_exceeded")
    }
    non_full_without_duration = sum(
        1 for candidate in verdict_by_id.values()
        if (candidate.get("execution") or {}).get("mode") != "full"
    )
    if non_full_without_duration:
        excluded_counts["non_full_execution"] = non_full_without_duration
    if latest["resumed_total"]:
        excluded_counts["resumed_without_current_duration"] = latest["resumed_total"]
    if not latest["sweep_end_present"] and latest["pending_total"] is not None:
        not_reported = max(
            0, latest["pending_total"] - latest["fresh_candidate_events"]
        )
        if not_reported:
            excluded_counts["not_yet_reported"] = not_reported
    sample = {
        "count": len(candidate_samples),
        "measurement_window": None if not sample_times else {
            "first": min(sample_times).isoformat(),
            "last": max(sample_times).isoformat(),
        },
        "median_candidate_seconds": median(candidate_samples) if candidate_samples else None,
    }
    eta = {
        "status": (
            "no_remaining_work" if current_pending == 0
            else "insufficient_sample" if current_pending is not None
            else "remaining_work_unknown"
        ),
        "sample": sample,
        "minimum_sample_count": MIN_ETA_SAMPLE,
        "excluded_candidate_counts": excluded_counts,
        "remaining_seconds": None,
        "diagnostic_only": True,
    }
    if len(candidate_samples) >= MIN_ETA_SAMPLE and current_pending:
        median_s = median(candidate_samples)
        workers = lane.judge.mutation.jobs if lane.judge and lane.judge.mutation else 1
        eta = {
            "status": "sample_qualified",
            "sample": sample,
            "minimum_sample_count": MIN_ETA_SAMPLE,
            "pending_candidates": current_pending,
            "parallel_jobs": workers,
            "remaining_seconds": round(median_s * current_pending / workers, 3),
            "diagnostic_only": True,
            "excluded_candidate_counts": excluded_counts,
        }
    for run in run_summaries:
        del run["_candidate_events"]
        del run["candidate_ids"]
        del run["selected_ids"]

    complete_inventory = (
        native_r2
        and mutation_claim is not None
        and latest["candidate_milestone"]
        and latest["sweep_end_present"]
        and latest["sweep_end_reason"] is None
        and verdict_ids == expected_ids
        and expected_ids == plan_id_set
        and total_outcomes == len(expected_ids)
    )
    complete_empty_plan = (
        native_r2
        and mutation_claim is not None
        and plan_total == 0
        and raw_verdict_ids == []
        and total_outcomes == 0
        and latest["sweep_end_present"]
        and latest["sweep_end_reason"] == "no_candidates"
        and not latest["candidate_milestone"]
    )
    complete_inventory = complete_inventory or complete_empty_plan
    progress_complete = terminal_matches_verdict
    status = "complete" if complete_inventory and progress_complete and actual_exit_matches else "incomplete"
    coverage_complete = (
        lane.judge is None
        or lane.judge.coverage is None
        or coverage_summary["artifact_status"] == "parsed_and_reverified_r1"
    )
    if not coverage_complete:
        status = "incomplete"
    if (
        not native_r2
        and (mutation_claim is None or r2_policy.get("producer") == "ingested")
        and terminal_matches_verdict
        and actual_exit_matches
        and coverage_complete
    ):
        status = "complete"

    candidate_details = _details(
        buckets_in_verdict,
        plan_by_id,
        buckets=selected_buckets,
        path_filter=path_filter,
        offset=offset,
        limit=limit,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis": "mutation_campaign",
        "status": status,
        "expected_commit": expected_commit,
        "tree": tree,
        "lane": lane_name,
        "lane_file": str(lane_file.path),
        "verdict": {
            "outcome": verdict.get("outcome"),
            "exit_code": verdict.get("exit_code"),
            "reason_code": verdict.get("reason_code"),
            "assay_version": verdict.get("assay_version"),
            "argv_modified": verdict.get("argv_modified"),
            "started": verdict.get("started"),
            "ended": verdict.get("ended"),
            "elapsed_seconds": round(
                (_timestamp(verdict["ended"], "verdict ended")
                 - _timestamp(verdict["started"], "verdict started")).total_seconds(),
                3,
            ),
            "verified": True,
            "observed_command_exit": command_exit,
            "command_exit_source": "explicit caller observation" if command_exit is not None else None,
            "command_exit_matches": actual_exit_matches,
        },
        "plan": {
            "status": plan.get("status"),
            "planned_total": plan_total,
            "inventory_sha256": (
                hashlib.sha256("\n".join(sorted(plan_ids)).encode("ascii")).hexdigest()
                if plan_total is not None else None
            ),
            "shard": None if shard_index is None else f"{shard_index}/{shard_count}",
        },
        "campaign": {
            "planned_total": plan_total,
            "completed_total": total_outcomes if mutation_claim is not None else None,
            "pending_total": None if mutation_claim is None else max(0, (plan_total or 0) - total_outcomes),
            "resumed_total": latest["resumed_total"],
            "rejudged_total": latest["rejudged_total"],
            "per_run_resumed_total": [run["resumed_total"] for run in run_summaries],
            "per_run_rejudged_total": [run["rejudged_total"] for run in run_summaries],
            "freshly_witness_replayed": fresh_replayed,
            "fully_executed": fully_executed,
            "outcomes": outcome_counts if mutation_claim is not None else None,
        },
        "runs": run_summaries,
        "coverage": coverage_summary,
        "timing": eta,
        "evidence": artifacts,
        "candidate_details": candidate_details,
    }


def build_campaign_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser(
        "campaign",
        help="summarize one lane from its explicit plan, progress, verdict, and exit evidence",
    )
    parser.add_argument("lane", help="declared lane name")
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    parser.add_argument("--file", type=Path, required=True, help="explicit assay.toml path")
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--verdict", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--command-exit", type=int)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--outcome", choices=MUTATION_BUCKETS, action="append")
    parser.add_argument("--path-prefix")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--format", choices=("json", "text"), default="json")


def run_campaign_command(args: argparse.Namespace, *, stdout, stderr) -> int:
    try:
        result = campaign(
            worktree=args.worktree,
            lane_file_path=args.file,
            lane_name=args.lane,
            expected_commit=args.expected_commit,
            verdict_path=args.verdict,
            progress_path=args.progress,
            command_exit=args.command_exit,
            log_path=args.log,
            coverage_path=args.coverage,
            buckets=args.outcome,
            path_filter=args.path_prefix,
            offset=args.offset,
            limit=args.limit,
        )
    except (OSError, ValueError, RecursionError, KeyError, TypeError,
            AttributeError, AssayError) as exc:
        print(f"assay analyze campaign: {exc}", file=stderr)
        return 2
    if args.format == "text":
        print(
            f"{result['lane']}: {result['status']} outcome={result['verdict']['outcome']} "
            f"planned={result['campaign']['planned_total']} "
            f"completed={result['campaign']['completed_total']} "
            f"pending={result['campaign']['pending_total']}",
            file=stdout,
        )
        print("  outcomes: " + json.dumps(result["campaign"]["outcomes"], sort_keys=True), file=stdout)
        print(
            f"  candidates: showing {len(result['candidate_details']['candidates'])} of "
            f"{result['candidate_details']['matching_total']} matching; "
            f"next_offset={result['candidate_details']['next_offset']}",
            file=stdout,
        )
        print("  ETA: " + json.dumps(result["timing"], sort_keys=True), file=stdout)
    else:
        print(json.dumps(result, indent=2, sort_keys=True), file=stdout)
    return 0 if result["status"] == "complete" else 3

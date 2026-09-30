"""Outcome-oriented campaign closeout built from explicit Assay evidence.

This module deliberately treats the verdict, progress stream, current lane
plan, coverage artifact, and command exit as separate inputs. A progress
stream is diagnostic evidence, never a verdict; counts are never defaulted
to zero when an input is absent or inconsistent.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import os
import re
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from assay import coverage as coverage_api
from assay import git, mutation
from assay.candidate_identity import candidate_id_from_fields
from assay.config import LaneFile, load_lane_file
from assay.errors import AssayError
from assay.mutation import PROGRESS_EVENTS, select_mutation_shard
from assay.safeio import read_bounded_file
from assay.verdict import MUTATION_BUCKETS

from assay_analysis import evidence

SCHEMA_VERSION = 1
MAX_PROGRESS_BYTES = 64 * 1024 * 1024
MAX_VERDICT_BYTES = 16 * 1024 * 1024
MAX_LANE_FILE_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_PROGRESS_RUNS = 10_000
MAX_DETAIL_LIMIT = 500
MIN_ETA_SAMPLE = 20
MAX_ERRORS = 10
MAX_SAMPLE_IDS = 10
MAX_PROJECT_JOBS = 64
MIN_PROJECTION_SAMPLE = 20
PHASE_SECONDS_KEYS = ("materialize", "command", "integrity", "teardown")
STARTUP_SECONDS_KEYS = ("to_session_start", "to_first_test")
ADVERSE_BUCKETS = ("survived", "hung", "crashed", "budget_exceeded")
IDENTITY_KEYS = ("path", "source_sha256", "start_byte", "end_byte", "mutated_file_sha256", "operator")
EXIT_PASS = 0
EXIT_COMPLETE_NOT_PASS = 1
EXIT_EVIDENCE_ERROR = 2
EXIT_INCOMPLETE = 3
ERROR_DOCUMENT_KIND = "assay-campaign-analysis"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

# The fixed vocabulary of ``errors[].source`` (CD51 Q1): the refused input,
# never an exception class. ``input`` is for a refusal no single input owns.
ERROR_SOURCES = (
    "arguments", "lane", "plan", "progress", "state", "verdict", "coverage", "input",
)


class _Stage:
    """The input the analysis is loading; the decorator tags a refusal with it."""

    def __init__(self) -> None:
        self.name = "input"


def _staged(function):
    """Give ``function`` a leading ``stage`` argument and tag its refusals with it."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        stage = _Stage()
        try:
            return function(stage, *args, **kwargs)
        except Exception as exc:
            exc.source = stage.name
            raise

    return wrapper


def _finite_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{where} must be finite and non-negative")
    return number


def _optional_number(value: Any, where: str) -> float | None:
    return None if value is None else _finite_number(value, where)


def _optional_object(value: Any, keys: tuple[str, ...], where: str) -> dict | None:
    """``None`` or an object with exactly *keys*, each a number or ``None`` (W8's shapes)."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{where} must be null or an object with exactly {list(keys)}")
    return {key: _optional_number(value[key], f"{where}.{key}") for key in keys}


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


def _nearest_rank(ordered: list[float], percentile: int) -> float:
    """The nearest-rank percentile of an ascending sample: ``sorted[ceil(p/100 * n) - 1]``."""
    return ordered[math.ceil(percentile * len(ordered) / 100) - 1]


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
    document = evidence._json(text)
    failures = evidence.verify_text(text)
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


def _read_progress(
    path: Path, *, expected: str, lane: str, tolerate_torn: bool
) -> tuple[dict, list[dict], bool]:
    """Read the progress stream; a torn final record is tolerated only without a verdict."""
    raw_bytes, artifact = _read_artifact(
        path, limit=MAX_PROGRESS_BYTES, label="progress"
    )
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"progress artifact is not UTF-8: {path}") from exc
    torn = not raw.endswith("\n")
    if torn:
        if not tolerate_torn:
            raise ValueError("progress artifact has a torn final record (missing newline)")
        # A live or killed run leaves a partial last line; it is dropped, not parsed.
        raw = raw[: raw.rfind("\n") + 1]
    runs: list[dict] = []
    all_runs: list[dict] = []
    current: dict | None = None
    for line_number, line in enumerate(raw.splitlines(), 1):
        event = evidence._json(line)
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
    return artifact, runs, torn


def _lane_plan(lane_file: LaneFile, lane_name: str, request_base: str | None) -> dict:
    """Use Assay's own planner so analysis and execution share candidate identity."""
    from assay import cli as assay_cli
    from assay import runner

    lane = lane_file.lane(lane_name)
    declared_request = (
        request_base
        if lane.judge is not None and lane.judge.base_source == "request"
        else None
    )
    rows = assay_cli.plan_jobs(lane_file, lane, request_base=declared_request)
    if rows == "UNSUPPORTED":
        return {"status": "unsupported"}
    # The planner does not put the resolved base in its public rows; compare it
    # with the base independently recorded by the verdict.
    return {
        "status": "ok",
        "candidates": rows,
        "_resolved_base": runner.resolve_declared_base(
            lane_file.project_root,
            runner.resolve_base_declaration(lane, declared_request),
        ),
    }


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
    *, lane_file: LaneFile, lane, verdict: dict | None, root: Path,
    path: Path | None
) -> dict:
    """Re-derive the judged R1 facts from the coverage artifact named by ``--coverage``.

    The lane-declared artifact is never read implicitly: without ``--coverage``
    an R1 lane reports ``not_supplied`` (which blocks ``complete``).
    """
    policy = None if lane.judge is None else lane.judge.coverage
    verdict_summary = (
        {"status": "not_judged", "r1": None, "r2_floor": None}
        if verdict is None
        else _coverage_from_verdict(verdict)
    )
    if policy is None:
        if path is not None:
            raise ValueError("coverage artifact was supplied for a lane with no R1 coverage declaration")
        return {
            **verdict_summary,
            "artifact_status": "not_applicable",
            "branch_arc_detail_status": "not_applicable",
            "missing_branch_arcs": None,
        }
    if path is None:
        return {
            **verdict_summary,
            "artifact_status": "not_supplied",
            "branch_arc_detail_status": "unavailable_without_artifact",
            "missing_branch_arcs": None,
        }
    if verdict is None:
        raise ValueError("coverage cannot be reverified without --verdict")
    declared = (lane_file.project_root / policy.artifact).resolve(strict=False)
    supplied = (path if path.is_absolute() else lane_file.project_root / path).resolve(strict=False)
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
    from assay import cli as assay_cli
    from assay import runner

    adapter = assay_cli.resolve_declared_adapters(lane)
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
            document = evidence._json(raw.decode("utf-8"))
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
    mutation_claim = r2["mutation"]
    buckets: dict[str, list[dict]] = {}
    by_id: dict[str, dict] = {}
    for bucket in MUTATION_BUCKETS:
        entries = mutation_claim.get(bucket, [])
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
    plan_event: dict | None = None
    command_finished: list[dict] = []
    phase = "header"
    for line_number, event in events:
        kind = event["event"]
        if kind == "candidates":
            if candidates_event is not None:
                raise ValueError("progress run repeats its candidates milestone")
            if phase not in ("header", "resume"):
                raise ValueError("progress candidates milestone is out of order")
            if "selection_sha256" in event:
                raise ValueError("pilot selection unsupported before P7")
            judge = event.get("judge_sha256")
            if judge is not None and (not isinstance(judge, str) or not _SHA256_RE.fullmatch(judge)):
                raise ValueError("progress judge_sha256 must be a SHA-256 digest")
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
            execution_mode = event.get("execution_mode")
            candidates[candidate_id] = {
                "outcome_bucket": event["outcome_bucket"],
                "elapsed_seconds": elapsed,
                "emitted_at": emitted_at.isoformat(),
                "cpu_seconds": _optional_number(event.get("cpu_seconds"), "candidate cpu_seconds"),
                "peak_rss_bytes": _optional_number(
                    event.get("peak_rss_bytes"), "candidate peak_rss_bytes"
                ),
                "phase_seconds": _optional_object(
                    event.get("phase_seconds"), PHASE_SECONDS_KEYS, "candidate phase_seconds"
                ),
                "startup_seconds": _optional_object(
                    event.get("startup_seconds"), STARTUP_SECONDS_KEYS, "candidate startup_seconds"
                ),
                "execution_mode": execution_mode if isinstance(execution_mode, str) else None,
                "liveness_resource_evidence": event.get("liveness_resource_evidence"),
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
            if kind == "command_finished":
                command_finished.append(event)
            elif kind == "plan":
                plan_event = event
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
        "judge_sha256": None if candidates_event is None else candidates_event.get("judge_sha256"),
        "candidate_ids": sorted(candidates),
        "_candidate_events": candidates,
        "_selection": selected_ids,
        "_plan_event": plan_event,
        "_command_finished": command_finished,
    }


class EvidenceErrors(ValueError):
    """Several refused facts found in one scan; each entry is ``{source, message}``."""

    def __init__(self, errors: list[dict]) -> None:
        super().__init__("; ".join(error["message"] for error in errors))
        self.errors = errors


def _identity_reproduces(fields: dict, expected_id: Any) -> bool:
    """Whether the six identity inputs in *fields* reproduce *expected_id* (section C)."""
    try:
        derived = candidate_id_from_fields(**{key: fields.get(key) for key in IDENTITY_KEYS})
    except ValueError:
        return False
    return derived == expected_id


def _check_plan_identity(plan_by_id: dict[str, dict]) -> None:
    """Every plan row must carry the inputs that reproduce its own id."""
    failures = [
        {
            "source": "plan",
            "message": f"plan row {candidate_id}: identity inputs do not reproduce its id",
        }
        for candidate_id, row in plan_by_id.items()
        if not _identity_reproduces(row, candidate_id)
    ]
    if failures:
        raise EvidenceErrors(failures)


_STATE_NAME = re.compile(r"[0-9a-f]{64}\.json\Z")


def _state_entry(record: dict, stem: str) -> dict:
    """P8's read-only shape checks 4-9 for one state record; the first failure raises."""
    if record.get("schema_version") != 1:
        raise ValueError("schema_version is not 1")
    if record.get("candidate_id") != stem:
        raise ValueError("candidate_id differs from the file name")
    if not _identity_reproduces(record, stem):
        raise ValueError("identity inputs do not reproduce the file name")
    bucket = record.get("outcome_bucket")
    if bucket not in MUTATION_BUCKETS:
        raise ValueError("outcome_bucket is unknown")
    try:
        mode = mutation.execution_from_state_record(record).mode
    except (TypeError, ValueError) as exc:
        raise ValueError(f"execution provenance is invalid: {exc}") from exc
    judge = record.get("judge_sha256")
    if not isinstance(judge, str) or not _SHA256_RE.fullmatch(judge):
        raise ValueError("judge_sha256 is not a SHA-256 digest")
    return {"id": stem, "record": record, "bucket": bucket, "judge": judge, "mode": mode}


def _read_state_dir(state_dir: Path) -> list[dict]:
    """Read every state record by name; all shape failures are reported together."""
    if not state_dir.is_dir():
        raise ValueError(f"state directory is not an existing directory: {state_dir}")
    entries: list[dict] = []
    failures: list[dict] = []
    for name in sorted(os.listdir(state_dir)):
        if not _STATE_NAME.fullmatch(name):
            continue
        try:
            raw, _artifact = _read_artifact(
                state_dir / name, limit=mutation.MUTATION_STATE_RECORD_LIMIT, label="state record"
            )
            record = evidence._json(raw.decode("utf-8"))
            if not isinstance(record, dict):
                raise ValueError("record is not a JSON object")
            entries.append(_state_entry(record, name[: -len(".json")]))
        except ValueError as exc:
            failures.append({"source": "state", "message": f"state record {name}: {exc}"})
    if failures:
        raise EvidenceErrors(failures)
    return entries


def _id_summary(ids) -> dict:
    ordered = sorted(ids)
    return {"count": len(ordered), "sample_ids": ordered[:MAX_SAMPLE_IDS]}


def _no_state() -> dict:
    """The store view when ``--state-dir`` was not supplied."""
    return {
        "output": None, "counted": {}, "current": {}, "unverified_ids": set(), "reclassified": [],
    }


def _reconcile_state(
    entries: list[dict],
    *,
    plan_ids: set[str],
    selection: set[str],
    latest: dict,
    verdict_by_id: dict[str, dict],
) -> dict:
    """CD40 order over shape-valid records: judge, foreign/stale, unverified hung, verdict, reconcile."""
    events = latest["_candidate_events"]
    in_selection = [entry for entry in entries if entry["id"] in selection]
    paired_ids = {
        entry["id"] for entry in in_selection
        if entry["id"] in events and events[entry["id"]]["outcome_bucket"] == entry["bucket"]
    }
    paired_judges = {entry["judge"] for entry in in_selection if entry["id"] in paired_ids}
    if latest["judge_sha256"] is not None:
        current, source = latest["judge_sha256"], "candidates-event"
    elif len(paired_judges) == 1:
        (current,) = paired_judges
        source = "paired-records"
    else:
        current, source = None, "unknown"
    foreign = [entry for entry in entries if entry["id"] not in plan_ids]
    stale = [
        entry for entry in entries
        if entry["id"] in plan_ids and current is not None and entry["judge"] != current
    ]
    same_judge = [entry for entry in in_selection if entry["judge"] == current]
    unverified_ids = {
        entry["id"] for entry in same_judge
        if entry["bucket"] == "hung"
        and not mutation.valid_hung_resource_evidence(
            entry["record"].get("liveness_resource_evidence")
        )
    }
    remaining = [entry for entry in same_judge if entry["id"] not in unverified_ids]
    disagreements = [
        {
            "source": "state",
            "message": (
                f"state record {entry['id']}.json: bucket {entry['bucket']!r} disagrees "
                f"with verified verdict bucket {verdict_by_id[entry['id']]['bucket']!r}"
            ),
        }
        for entry in remaining
        if entry["id"] in verdict_by_id and verdict_by_id[entry["id"]]["bucket"] != entry["bucket"]
    ]
    if disagreements:
        raise EvidenceErrors(disagreements)
    counted: dict[str, dict] = {}
    reclassified: list[dict] = []
    eventless: list[dict] = []
    for entry in remaining:
        event = events.get(entry["id"])
        if event is None:
            eventless.append(entry)
            continue
        counted[entry["id"]] = entry
        if event["outcome_bucket"] != entry["bucket"]:
            reclassified.append({
                "candidate_id": entry["id"],
                "source": "state_vs_progress",
                "runs": [{"run_id": latest["run_id"], "bucket": event["outcome_bucket"]}],
                "state_bucket": entry["bucket"],
            })
    if current is None:
        unreconciled = [entry for entry in in_selection if entry["id"] not in paired_ids]
    elif len(eventless) == latest["resumed_total"]:
        counted.update({entry["id"]: entry for entry in eventless})
        unreconciled = []
    else:
        unreconciled = eventless
    return {
        "output": {
            "records": len(entries),
            "counted": len(counted),
            "foreign": _id_summary(entry["id"] for entry in foreign),
            "stale_judge": {
                "count": len(stale),
                "judge_sha256_counts": dict(sorted(Counter(e["judge"] for e in stale).items())),
            },
            "unreconciled": _id_summary(entry["id"] for entry in unreconciled),
            "unverified_hung": _id_summary(unverified_ids),
            "judge_sha256_current": current,
            "judge_sha256_source": source,
            "judge_sha256_counts": dict(sorted(Counter(e["judge"] for e in entries).items())),
        },
        "counted": counted,
        "current": {entry["id"]: entry for entry in same_judge},
        "unverified_ids": unverified_ids,
        "reclassified": reclassified,
    }


def _typed(value: Any, kind: type) -> Any:
    return value if isinstance(value, kind) and not isinstance(value, bool) else None


def _liveness(bucket: str, event: dict, current_record: dict | None) -> tuple[str | None, str | None]:
    """``(decision, evidence status)`` for a hung row; both ``None`` for other buckets."""
    if bucket != "hung":
        return None, None
    evidence_value = event.get("liveness_resource_evidence")
    if evidence_value is None and current_record is not None:
        evidence_value = current_record["record"].get("liveness_resource_evidence")
    if evidence_value is None:
        return None, "absent"
    status = "valid" if mutation.valid_hung_resource_evidence(evidence_value) else "invalid"
    return _typed(evidence_value.get("decision") if isinstance(evidence_value, dict) else None, str), status


def _row(item: dict, site: dict) -> dict:
    """One per-candidate row (20 keys); an absent fact is ``null``, never 0."""
    event = item["event"] or {}
    outcome = item["outcome"] or {}
    counted = item["record"]
    record = {} if counted is None else counted["record"]
    evidence_object = _typed((outcome or record).get("evidence"), dict) or {}
    mode = next(
        (
            candidate for candidate in (
                (outcome.get("execution") or {}).get("mode"),
                None if counted is None else counted["mode"],
                event.get("execution_mode"),
            ) if candidate is not None
        ),
        None,
    )
    decision, status = _liveness(item["bucket"], event, item["current_record"])
    return {
        "candidate_id": item["candidate_id"],
        "outcome": item["bucket"],
        "path": site.get("path"),
        "lineno": site.get("lineno"),
        "operator": site.get("operator"),
        "description": site.get("description"),
        "start_byte": site.get("start_byte"),
        "end_byte": site.get("end_byte"),
        "execution_mode": mode,
        "elapsed_seconds": event.get("elapsed_seconds"),
        "cpu_seconds": event.get("cpu_seconds"),
        "peak_rss_bytes": event.get("peak_rss_bytes"),
        "phase_seconds": event.get("phase_seconds"),
        "startup_seconds": event.get("startup_seconds"),
        "started_count": _typed(evidence_object.get("started_count"), int),
        "evidence_command": _typed(evidence_object.get("command"), str),
        "outcome_source": item["source"],
        "run_id": item["run_id"],
        "liveness_decision": decision,
        "liveness_evidence_status": status,
    }


def _resolutions(
    *,
    verdict_buckets: dict[str, list[dict]] | None,
    selection: set[str],
    runs: list[dict],
    state: dict,
) -> tuple[list[dict], list[dict]]:
    """Every candidate with one resolved bucket, plus the ``reclassified`` items."""
    latest = runs[-1]
    events = latest["_candidate_events"]

    def item(candidate_id, bucket, source, event, run_id, outcome):
        return {
            "candidate_id": candidate_id, "bucket": bucket, "source": source,
            "event": event, "run_id": run_id, "outcome": outcome,
            "record": state["counted"].get(candidate_id),
            "current_record": state["current"].get(candidate_id),
        }

    found: list[dict] = []
    reclassified = list(state["reclassified"])
    if verdict_buckets is not None:
        for bucket in MUTATION_BUCKETS:
            for outcome in verdict_buckets.get(bucket, []):
                candidate_id = outcome.get("candidate_id")
                event = events.get(candidate_id)
                run_id = None if event is None else latest["run_id"]
                found.append(item(candidate_id, bucket, "verdict", event, run_id, outcome))
        return found, reclassified
    # Progress-only looks at every run at the commit; a store narrows it to the latest.
    scope = runs if state["output"] is None else runs[-1:]
    seen: dict[str, tuple[dict, str]] = {}
    history: dict[str, list[dict]] = {}
    for run in scope:
        for candidate_id, event in run["_candidate_events"].items():
            if candidate_id in selection:
                seen[candidate_id] = (event, run["run_id"])
                history.setdefault(candidate_id, []).append(
                    {"run_id": run["run_id"], "bucket": event["outcome_bucket"]}
                )
    for candidate_id in sorted(selection):
        record = state["counted"].get(candidate_id)
        if candidate_id in seen:
            event, run_id = seen[candidate_id]
            bucket = event["outcome_bucket"]
            source = "state" if record is not None and record["bucket"] == bucket else "progress"
            found.append(item(candidate_id, bucket, source, event, run_id, None))
            if len({entry["bucket"] for entry in history[candidate_id]}) > 1:
                reclassified.append({
                    "candidate_id": candidate_id,
                    "source": "progress_runs",
                    "runs": history[candidate_id],
                    "state_bucket": None,
                })
        elif record is not None:
            found.append(item(candidate_id, record["bucket"], "state", None, None, None))
    return found, reclassified


def _page(rows: list[dict], *, offset: int, limit: int) -> dict:
    rows = sorted(rows, key=lambda row: (
        row["path"] or "", row["lineno"] or 0, row["candidate_id"] or ""
    ))
    page = rows[offset:offset + limit]
    return {
        "matching_total": len(rows),
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
        "candidates": page,
    }


def _details(
    rows: list[dict], *, buckets: list[str], path_filter: str | None, offset: int, limit: int
) -> dict:
    selected = [
        row for row in rows
        if row["outcome"] in buckets
        and (
            path_filter is None
            or (isinstance(row["path"], str) and row["path"].startswith(path_filter))
        )
    ]
    return _page(selected, offset=offset, limit=limit)


def _adverse(rows: list[dict], *, limit: int) -> dict:
    """The four adverse lists; ``--outcome``, ``--path-prefix`` and ``--offset`` never apply."""
    result = {}
    for bucket in ADVERSE_BUCKETS:
        page = _page([row for row in rows if row["outcome"] == bucket], offset=0, limit=limit)
        result[bucket] = {
            "matching_total": page["matching_total"],
            "candidates": page["candidates"],
            "next_offset": page["next_offset"],
        }
    return result


def _timing(latest: dict, *, pending: int | None, jobs: int | None) -> dict:
    """P8's diagnostic ETA object; time never changes ``status`` or the exit code."""
    events = latest["_candidate_events"]
    sample = [
        event for event in events.values()
        if event["outcome_bucket"] in ("killed", "survived", "equivalent")
        and event["elapsed_seconds"] > 0
    ]
    ordered = sorted(event["elapsed_seconds"] for event in sample)
    emitted = [_timestamp(event["emitted_at"], "candidate emitted_at") for event in sample]
    window = None if not sample else {
        "first": min(emitted).isoformat(),
        "last": max(emitted).isoformat(),
        "run_id": latest["run_id"],
    }
    if pending is None:
        reason = "remaining_work_unknown"
    elif pending == 0:
        reason = "no_remaining_work"
    elif len(ordered) < MIN_ETA_SAMPLE:
        reason = "insufficient_sample"
    else:
        reason = None
    eta = None
    if reason is None:
        p50 = _nearest_rank(ordered, 50)
        p90 = _nearest_rank(ordered, 90)
        eta = {
            "jobs": jobs,
            "jobs_source": "lane",
            "pending": pending,
            "p50_candidate_s": round(p50, 1),
            "p90_candidate_s": round(p90, 1),
            "remaining_seconds_p50": round(pending * p50 / jobs, 1),
            "remaining_seconds_p90": round(pending * p90 / jobs, 1),
        }
    counts = Counter(event["outcome_bucket"] for event in events.values())
    return {
        "diagnostic_only": True,
        "eta_reason": reason,
        "sample_count": len(ordered),
        "minimum_sample_count": MIN_ETA_SAMPLE,
        "measurement_window": window,
        "eta": eta,
        "excluded_candidate_counts": {
            "crashed": counts["crashed"],
            "hung": counts["hung"],
            "budget_exceeded": counts["budget_exceeded"],
            "resumed": latest["resumed_total"],
        },
    }


class CostSample(NamedTuple):
    """One measured candidate: where it lives, which operator, its bucket, its seconds."""

    path: str
    operator: str
    bucket: str
    seconds: float


SIZE_CLASSES = {"small": "≤10", "medium": "11–100", "large": ">100"}
STRATA_RULE = "operator×size_class→operator→all, n≥20"
WALL_SCOPE = (
    "fixed_seconds_measured + serial/jobs; excludes preflight, R3 and consolidation"
)


def _size_class(candidates_in_file: int) -> str:
    """C26: by the number of plan candidates in the file, never by samples."""
    if candidates_in_file <= 10:
        return "small"
    return "medium" if candidates_in_file <= 100 else "large"


def _project_basis(
    samples: list[CostSample], rows: Sequence[Mapping[str, Any]], per_file: Counter
) -> tuple[dict, float | None, float | None]:
    """One basis: per-candidate stratum cost with the hierarchical fallback (n >= 20)."""
    result: dict[str, Any] = {
        "samples": len(samples),
        "candidates_projected": len(rows),
        "fallback_counts": None,
        "serial_seconds_p50": None,
        "serial_seconds_p90": None,
        "reason": "insufficient_sample",
    }
    if len(samples) < MIN_PROJECTION_SAMPLE:
        return result, None, None
    by_stratum: dict[tuple[str, str], list[float]] = {}
    by_operator: dict[str, list[float]] = {}
    for sample in samples:
        stratum = (sample.operator, _size_class(per_file[sample.path]))
        by_stratum.setdefault(stratum, []).append(sample.seconds)
        by_operator.setdefault(sample.operator, []).append(sample.seconds)
    pools = {
        "operator_size_class": by_stratum,
        "operator": by_operator,
        "all": {"all": [sample.seconds for sample in samples]},
    }
    cache: dict[tuple[str, Any], tuple[float, float]] = {}
    fallback = {"operator_size_class": 0, "operator": 0, "all": 0}
    total_p50 = total_p90 = 0.0
    for row in rows:
        keys = {
            "operator_size_class": (row["operator"], _size_class(per_file[row["path"]])),
            "operator": row["operator"],
            "all": "all",
        }
        level = next(
            name for name in ("operator_size_class", "operator", "all")
            if len(pools[name].get(keys[name], ())) >= MIN_PROJECTION_SAMPLE
        )
        fallback[level] += 1
        key = (level, keys[level])
        if key not in cache:
            ordered = sorted(pools[level][keys[level]])
            cache[key] = (_nearest_rank(ordered, 50), _nearest_rank(ordered, 90))
        total_p50 += cache[key][0]
        total_p90 += cache[key][1]
    result.update({
        "fallback_counts": fallback,
        "serial_seconds_p50": round(total_p50, 1),
        "serial_seconds_p90": round(total_p90, 1),
        "reason": None,
    })
    return result, total_p50, total_p90


def project(
    samples: Sequence[CostSample],
    rows: Sequence[Mapping[str, Any]],
    jobs: int,
    fixed: Mapping[str, float | None],
) -> dict:
    """Stratified projection (pure): no I/O, no clock, ``size_class`` derived from *rows*."""
    per_file = Counter(row["path"] for row in rows)
    killed, killed_p50, killed_p90 = _project_basis(
        [sample for sample in samples if sample.bucket == "killed"], rows, per_file
    )
    both, _both_p50, _both_p90 = _project_basis(
        [sample for sample in samples if sample.bucket in ("killed", "survived")], rows, per_file
    )
    components = {
        name: fixed.get(name) for name in ("coverage_baseline", "r2_baseline", "other")
    }
    measured = sum(value for value in components.values() if value is not None)
    return {
        "diagnostic_only": True,
        "strata_rule": STRATA_RULE,
        "size_classes": dict(SIZE_CLASSES),
        "bases": {"killed": killed, "killed_and_survived": both},
        "jobs": jobs,
        "jobs_source": "project-jobs",
        "fixed_seconds_measured": round(measured, 1),
        "fixed_components": {
            name: None if value is None else round(value, 1) for name, value in components.items()
        },
        "fixed_components_missing": [
            name for name, value in components.items() if value is None
        ] + ["r3"],
        "wall_seconds_p50": None if killed_p50 is None else round(measured + killed_p50 / jobs, 1),
        "wall_seconds_p90": None if killed_p90 is None else round(measured + killed_p90 / jobs, 1),
        "wall_basis": "killed",
        "wall_scope": WALL_SCOPE,
    }


def _fixed_components(latest: dict) -> dict[str, float | None]:
    """P8's exact fixed-overhead formula over the latest run's events."""

    def span(phase: str) -> float | None:
        for event in latest["_command_finished"]:
            if event.get("phase") == phase:
                return max(0.0, (
                    _timestamp(event.get("ended"), "command_finished ended")
                    - _timestamp(event.get("started"), "command_finished started")
                ).total_seconds())
        return None

    coverage = span("baseline")
    if coverage is None:
        coverage = span("direct")
    r2 = span("r2-baseline")
    if r2 is None and latest["_plan_event"] is not None:
        r2 = _optional_number(latest["_plan_event"].get("r2_baseline_s"), "plan r2_baseline_s")
    first = next(iter(latest["_candidate_events"].values()), None)
    other = None
    if first is not None:
        elapsed = (
            _timestamp(first["emitted_at"], "candidate emitted_at")
            - _timestamp(latest["started"], "progress run started")
        ).total_seconds()
        other = max(0.0, elapsed - (coverage or 0.0) - (r2 or 0.0))
    return {"coverage_baseline": coverage, "r2_baseline": r2, "other": other}


def _native_r2(lane, r2_policy: dict) -> bool:
    return bool(
        "R2" in lane.rigor
        and lane.judge is not None
        and lane.judge.mutation is not None
        and lane.judge.mutation.format is None
        and r2_policy.get("producer", "native") == "native"
    )


def _check_verdict_against_lane(verdict: dict, *, lane_name: str, head: str, lane) -> dict:
    """The verified verdict must be for this lane, commit and declared policy; returns its R2 policy."""
    if verdict.get("lane") != lane_name:
        raise ValueError(f"verdict lane {verdict.get('lane')!r} differs from requested {lane_name!r}")
    if verdict.get("commit") != head:
        raise ValueError("verdict commit differs from current expected HEAD")
    judgment = verdict.get("judgment") or {}
    r2_policy = judgment.get("r2") or {}
    r1_policy = judgment.get("r1") or {}
    if lane.judge is not None and lane.judge.coverage is not None:
        declared_coverage = lane.judge.coverage
        if (
            r1_policy.get("coverage_artifact") != declared_coverage.artifact
            or r1_policy.get("coverage_format") != declared_coverage.format
            or r1_policy.get("fail_under") != declared_coverage.fail_under
        ):
            raise ValueError("R1 verdict policy differs from the named lane declaration")
    if _native_r2(lane, r2_policy):
        mutation_policy = lane.judge.mutation
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
    return r2_policy


def _reconstruct_plan(
    lane_file: LaneFile, lane, lane_name: str, *,
    native_r2: bool, verdict: dict | None, request_base: str | None,
) -> dict:
    """The lane's plan via Assay's own planner; the base comes from the verdict or ``--request-base``."""
    if native_r2:
        resolved_base = (
            None if verdict is None
            else ((verdict.get("judgment") or {}).get("resolved") or {}).get("base")
        )
        if verdict is None and lane.judge.base_source == "request" and request_base is None:
            raise ValueError("plan base cannot be reconstructed without --verdict or --request-base")
        plan = _lane_plan(
            lane_file, lane_name, request_base if verdict is None else resolved_base
        )
        if plan["status"] == "unsupported":
            raise ValueError("lane has no mutation plan")
        if verdict is not None and plan.get("_resolved_base") != resolved_base:
            raise ValueError("reconstructed plan base differs from the verified verdict")
        return plan
    if "R2" in lane.rigor:
        return {"status": "not_supported", "reason": "ingested or non-native mutation lane"}
    return {"status": "not_applicable", "reason": "lane declares no R2 mutation judgment"}


def _verdict_inventory(
    verdict: dict | None, *, native_r2: bool, r2_policy: dict, plan_ids: list[str],
    plan_by_id: dict[str, dict], buckets_in_verdict: dict, verdict_by_id: dict[str, dict],
) -> dict:
    """The verdict's claimed candidate inventory, checked against the reconstructed plan."""
    empty = {
        "mutation_claim": None, "raw_ids": None, "pre_submission_limit": False,
        "verdict_ids": set(), "expected_ids": set(),
    }
    if verdict is None:
        return empty
    r2_claim = next(
        (claim for claim in verdict.get("claims", []) if claim.get("rigor") == "R2"),
        None,
    )
    mutation_claim = (
        r2_claim.get("mutation")
        if isinstance(r2_claim, dict) and isinstance(r2_claim.get("mutation"), dict)
        else None
    )
    raw_ids = None if mutation_claim is None else mutation_claim.get("candidate_ids")
    pre_submission_limit = bool(
        native_r2
        and mutation_claim is not None
        and r2_claim.get("reason_code") == "MUTANT_LIMIT_EXCEEDED"
        and mutation_claim.get("total") == 0
    )
    if native_r2 and mutation_claim is not None and raw_ids is None and not pre_submission_limit:
        raise ValueError("native R2 verdict has no v13 candidate_ids inventory")
    if raw_ids is not None and (
        any(not isinstance(item, str) or not _SHA256_RE.fullmatch(item) for item in raw_ids)
        or len(raw_ids) != len(set(raw_ids))
    ):
        raise ValueError("verdict candidate_ids inventory is malformed or duplicated")
    verdict_ids = set() if raw_ids is None else set(raw_ids)
    expected_ids = verdict_ids
    if native_r2 and mutation_claim is not None:
        shard_index = r2_policy.get("shard_index")
        if pre_submission_limit:
            if verdict_by_id or any(buckets_in_verdict.values()):
                raise ValueError(
                    "pre-submission mutant-limit verdict unexpectedly has candidate outcomes"
                )
            expected_ids = set()
        else:
            expected_ids = (
                set(plan_ids)
                if shard_index is None
                else {plan_ids[position] for position in select_mutation_shard(
                    plan_ids, index=shard_index, count=r2_policy.get("shard_count")
                )}
            )
            if verdict_ids != expected_ids:
                raise ValueError("verdict candidate inventory differs from the current lane plan")
            if set(verdict_by_id) != verdict_ids:
                raise ValueError("verdict outcome buckets do not exhaust its candidate inventory")
            for candidate_id, outcome in verdict_by_id.items():
                row = plan_by_id[candidate_id]
                for field in ("path", "lineno", "operator", "description", "start_byte", "end_byte"):
                    if outcome.get(field) != row.get(field):
                        raise ValueError(
                            f"verdict candidate {candidate_id} {field} differs from the current plan"
                        )
    return {
        "mutation_claim": mutation_claim, "raw_ids": raw_ids,
        "pre_submission_limit": pre_submission_limit,
        "verdict_ids": verdict_ids, "expected_ids": expected_ids,
    }


@_staged
def campaign(
    stage: _Stage,
    *,
    worktree: Path,
    lane_file_path: Path,
    lane_name: str,
    expected_commit: str,
    verdict_path: Path | None,
    progress_path: Path,
    command_exit: int | None,
    state_dir: Path | None = None,
    request_base: str | None = None,
    project_jobs: int | None = None,
    log_path: Path | None = None,
    coverage_path: Path | None = None,
    buckets: list[str] | None = None,
    path_filter: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    """Build one deterministic closeout; all paths are explicit inputs."""
    stage.name = "arguments"
    evidence._report_commit(expected_commit)
    if command_exit is not None and (
        isinstance(command_exit, bool) or not isinstance(command_exit, int) or command_exit < 0
    ):
        raise ValueError("command exit must be a non-negative integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("detail offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_DETAIL_LIMIT:
        raise ValueError(f"detail limit must be between 1 and {MAX_DETAIL_LIMIT}")
    if project_jobs is not None and (
        isinstance(project_jobs, bool)
        or not isinstance(project_jobs, int)
        or not 1 <= project_jobs <= MAX_PROJECT_JOBS
    ):
        raise ValueError(f"project jobs must be between 1 and {MAX_PROJECT_JOBS}")
    selected_buckets = list(MUTATION_BUCKETS) if buckets is None else buckets
    if not selected_buckets or any(bucket not in MUTATION_BUCKETS for bucket in selected_buckets):
        raise ValueError(f"outcome filters must use {list(MUTATION_BUCKETS)}")
    if len(selected_buckets) != len(set(selected_buckets)):
        raise ValueError("outcome filters contain duplicates")

    root = git.repo_top(worktree)
    head, tree = evidence._identity(root, expected_commit)
    stage.name = "lane"
    _bind_lane_file(lane_file_path, root=root, commit=head)
    lane_file = load_lane_file(lane_file_path)
    lane = lane_file.lane(lane_name)
    verdict_result = None
    verdict = None
    r2_policy: dict = {}
    if verdict_path is not None:
        stage.name = "verdict"
        verdict_result = _read_verified_verdict(verdict_path, expected=head)
        verdict = verdict_result["verdict"]
        r2_policy = _check_verdict_against_lane(
            verdict, lane_name=lane_name, head=head, lane=lane
        )
    native_r2 = _native_r2(lane, r2_policy)
    stage.name = "plan"
    plan = _reconstruct_plan(
        lane_file, lane, lane_name,
        native_r2=native_r2, verdict=verdict, request_base=request_base,
    )
    plan_rows = plan.get("candidates", [])
    plan_by_id = {
        row["id"]: row for row in plan_rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    if len(plan_by_id) != len(plan_rows):
        raise ValueError("current lane plan contains duplicate or malformed candidate identities")
    _check_plan_identity(plan_by_id)
    plan_ids = list(plan_by_id)
    plan_id_set = set(plan_ids)
    plan_total = len(plan_ids) if plan.get("status") == "ok" else None

    stage.name = "verdict"
    buckets_in_verdict, verdict_by_id = {}, {}
    if verdict is not None:
        buckets_in_verdict, verdict_by_id = _candidate_outcomes(verdict)
    inventory = _verdict_inventory(
        verdict, native_r2=native_r2, r2_policy=r2_policy, plan_ids=plan_ids,
        plan_by_id=plan_by_id, buckets_in_verdict=buckets_in_verdict,
        verdict_by_id=verdict_by_id,
    )
    mutation_claim = inventory["mutation_claim"]
    raw_verdict_ids = inventory["raw_ids"]
    verdict_ids = inventory["verdict_ids"]
    expected_ids = inventory["expected_ids"]

    stage.name = "progress"
    progress_artifact, progress_runs, torn_final_record = _read_progress(
        progress_path, expected=expected_commit, lane=lane_name,
        tolerate_torn=verdict is None,
    )
    run_summaries = [
        _run_summary(
            run,
            plan_by_id=plan_by_id,
            plan_total=plan_total,
            verdict_by_id=verdict_by_id,
            compare_verdict=verdict is not None and index == len(progress_runs) - 1,
        )
        for index, run in enumerate(progress_runs)
    ]
    latest = run_summaries[-1]
    selection = latest["_selection"]
    stage.name = "input"
    if verdict is not None and latest["selected_ids"] and set(latest["selected_ids"]) != expected_ids:
        raise ValueError("latest progress selected inventory differs from the verified verdict")
    scope = selection if verdict is None else expected_ids
    if not set(latest["candidate_ids"]) <= scope:
        raise ValueError("latest progress run includes candidates outside its selected scope")
    terminal = latest["terminal"]
    terminal_matches_verdict = False
    if verdict is not None and terminal is not None:
        terminal_matches_verdict = (
            terminal.get("outcome") == verdict.get("outcome")
            and terminal.get("exit_code") == verdict.get("exit_code")
            and terminal.get("reason_code") == verdict.get("reason_code")
        )
        if not terminal_matches_verdict:
            raise ValueError("latest progress terminal disagrees with the verified verdict")

    stage.name = "state"
    state = _no_state()
    if state_dir is not None:
        state = _reconcile_state(
            _read_state_dir(state_dir), plan_ids=plan_id_set, selection=selection,
            latest=latest, verdict_by_id=verdict_by_id,
        )

    stage.name = "arguments"
    actual_exit_matches = (
        verdict is not None and command_exit is not None
        and command_exit == verdict.get("exit_code")
    )
    if verdict is not None and command_exit is not None and not actual_exit_matches:
        raise ValueError(
            f"observed command exit {command_exit} differs from verified verdict exit "
            f"{verdict.get('exit_code')}"
        )
    artifacts = {
        "verdict": None if verdict_result is None else verdict_result["artifact"],
        "progress": progress_artifact,
    }
    stage.name = "lane"
    _lane_bytes, artifacts["lane_file"] = _read_artifact(
        lane_file.path, limit=MAX_LANE_FILE_BYTES, label="lane file"
    )
    stage.name = "input"
    if log_path is not None:
        _log_bytes, artifacts["log"] = _read_artifact(
            log_path, limit=MAX_LOG_BYTES, label="gate log"
        )
    stage.name = "coverage"
    coverage_summary = _coverage_artifact_summary(
        lane_file=lane_file, lane=lane, verdict=verdict, root=root,
        path=coverage_path,
    )
    if coverage_summary.get("artifact") is not None:
        artifacts["coverage"] = coverage_summary["artifact"]
    stage.name = "input"

    items, reclassified = _resolutions(
        verdict_buckets=None if verdict is None else buckets_in_verdict,
        selection=selection, runs=run_summaries, state=state,
    )
    rows = [
        _row(item, plan_by_id.get(item["candidate_id"], item["outcome"] or {}))
        for item in items
    ]
    have_outcomes = plan_total is not None or (verdict is not None and mutation_claim is not None)
    outcome_counts = {
        bucket: sum(1 for row in rows if row["outcome"] == bucket) for bucket in MUTATION_BUCKETS
    }
    execution_mode_counts = Counter(
        row["execution_mode"] for row in rows if row["execution_mode"] is not None
    )
    events = latest["_candidate_events"]
    never_started = sorted(
        candidate_id for candidate_id, outcome in verdict_by_id.items()
        if outcome["bucket"] == "budget_exceeded" and candidate_id not in events
    )
    lane_timeout_row = verdict is not None and (
        verdict.get("reason_code") == "LANE_TIMEOUT" or bool(never_started)
    )
    unresolved = None
    if lane_timeout_row:
        unresolved = {"matching_total": len(never_started), "candidates": never_started[:limit]}
    selected_total = None if plan_total is None else len(selection)
    completed_total = len(items) - len(never_started) if have_outcomes else None
    pending_total = None if selected_total is None else max(0, selected_total - completed_total)
    jobs = lane.judge.mutation.jobs if native_r2 else None
    timing = _timing(latest, pending=pending_total, jobs=jobs)
    projection = None
    if project_jobs is not None:
        stage.name = "progress"
        projection = project(
            [
                CostSample(
                    plan_by_id[candidate_id]["path"], plan_by_id[candidate_id]["operator"],
                    event["outcome_bucket"], event["elapsed_seconds"],
                )
                for candidate_id, event in events.items()
                if event["outcome_bucket"] in ("killed", "survived") and event["elapsed_seconds"] > 0
            ],
            list(plan_by_id.values()), project_jobs, _fixed_components(latest),
        )
        stage.name = "input"
    for run in run_summaries:
        for key in [name for name in run if name.startswith("_")]:
            del run[key]
        del run["candidate_ids"]
        del run["selected_ids"]

    total_outcomes = sum(outcome_counts.values())
    complete_inventory = bool(
        native_r2
        and mutation_claim is not None
        and latest["candidate_milestone"]
        and latest["sweep_end_present"]
        and latest["sweep_end_reason"] is None
        and verdict_ids == expected_ids
        and expected_ids == plan_id_set
        and total_outcomes == len(expected_ids)
    )
    complete_empty_plan = bool(
        native_r2
        and mutation_claim is not None
        and plan_total == 0
        and raw_verdict_ids == []
        and total_outcomes == 0
        and latest["sweep_end_present"]
        and latest["sweep_end_reason"] == "no_candidates"
        and not latest["candidate_milestone"]
    )
    ingested_lane = not native_r2 and (
        mutation_claim is None or r2_policy.get("producer") == "ingested"
    )
    inventory_established = verdict is not None and (
        complete_inventory or complete_empty_plan or ingested_lane
    )
    if verdict is None:
        inventory_exhausted = selection == plan_id_set and pending_total == 0
    else:
        inventory_exhausted = inventory_established
    coverage_blocks = coverage_summary["artifact_status"] not in (
        "not_applicable", "parsed_and_reverified_r1",
    )
    blockers = {
        name for name, holds in (
            ("no_verdict", verdict is None),
            ("command_exit_not_observed", command_exit is None),
            ("inventory_not_exhausted", not inventory_exhausted),
            ("terminal_disagrees", verdict is not None and not terminal_matches_verdict),
            ("coverage_not_reverified", coverage_blocks),
            (
                "state_unreconciled",
                state["output"] is not None and state["output"]["unreconciled"]["count"] > 0,
            ),
            (
                "unverified_hung_records",
                any(
                    candidate_id not in verdict_by_id and candidate_id not in events
                    for candidate_id in state["unverified_ids"]
                ),
            ),
            ("lane_timeout_or_unstarted", lane_timeout_row),
        ) if holds
    }
    status = "incomplete" if blockers else "complete"

    verdict_document = None
    if verdict is not None:
        verdict_document = {
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
        }
    if verdict is not None:
        shard = (r2_policy.get("shard_index"), r2_policy.get("shard_count"))
    else:
        shard = ((latest["shard"] or {}).get("index"), (latest["shard"] or {}).get("count"))
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis": "mutation_campaign",
        "status": status,
        "qualifying": True,
        "complete_blockers": sorted(blockers),
        "expected_commit": expected_commit,
        "tree": tree,
        "lane": lane_name,
        "lane_file": str(lane_file.path),
        "torn_final_record": torn_final_record,
        "verdict": verdict_document,
        "plan": {
            "status": plan.get("status"),
            "planned_total": plan_total,
            "inventory_sha256": (
                hashlib.sha256("\n".join(sorted(plan_ids)).encode("ascii")).hexdigest()
                if plan_total is not None else None
            ),
            "shard": None if shard[0] is None else f"{shard[0]}/{shard[1]}",
        },
        "campaign": {
            "planned_total": plan_total,
            "selected_total": selected_total,
            "completed_total": completed_total,
            "pending_total": pending_total,
            "resumed_total": latest["resumed_total"],
            "rejudged_total": latest["rejudged_total"],
            "per_run_resumed_total": [run["resumed_total"] for run in run_summaries],
            "per_run_rejudged_total": [run["rejudged_total"] for run in run_summaries],
            "execution_mode_counts": dict(sorted(execution_mode_counts.items())),
            "outcomes": outcome_counts if have_outcomes else None,
        },
        "runs": run_summaries,
        "coverage": coverage_summary,
        "timing": timing,
        "evidence": artifacts,
        "candidate_details": _details(
            rows, buckets=selected_buckets, path_filter=path_filter, offset=offset, limit=limit
        ),
        "adverse": _adverse(rows, limit=limit),
        "unresolved": unresolved,
        "reclassified": sorted(
            reclassified, key=lambda item: (item["candidate_id"], item["source"])
        ),
        "state": state["output"],
        "projection": projection,
    }


def _project_jobs(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_PROJECT_JOBS:
        raise argparse.ArgumentTypeError(f"must be an integer between 1 and {MAX_PROJECT_JOBS}")
    return value


def build_campaign_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser(
        "campaign",
        help="summarize one lane from its explicit plan, progress, verdict, and exit evidence",
    )
    parser.add_argument("lane", help="declared lane name")
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    parser.add_argument("--file", type=Path, required=True, help="explicit assay.toml path")
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--verdict", type=Path, help="verified verdict (optional; without it the result is never complete)")
    parser.add_argument("--state-dir", type=Path, help="mutation state directory to reconcile (read-only)")
    parser.add_argument("--request-base", help="request base to reconstruct the plan without --verdict")
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--command-exit", type=int)
    parser.add_argument("--project", action="store_true", help="add the diagnostic stratified projection")
    parser.add_argument("--project-jobs", type=_project_jobs, help="jobs for --project (1..64)")
    parser.set_defaults(_campaign_parser=parser)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--outcome", choices=MUTATION_BUCKETS, action="append")
    parser.add_argument("--path-prefix")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--format", choices=("json", "text"), default="json")


def _exit_code(document: dict) -> int:
    """Status row 3-6 exit mapping (A-460 codes 0/1/2; 3 is post-hoc ``incomplete``)."""
    if document["status"] != "complete":
        return EXIT_INCOMPLETE
    return EXIT_PASS if document["verdict"]["outcome"] == "PASS" else EXIT_COMPLETE_NOT_PASS


def _evidence_error_document(args: argparse.Namespace, exc: BaseException) -> dict:
    """The closed seven-key document a refused input produces (status row 1)."""
    errors = (
        exc.errors if isinstance(exc, EvidenceErrors)
        else [{"source": getattr(exc, "source", "input"), "message": str(exc)}]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": ERROR_DOCUMENT_KIND,
        "status": "evidence_error",
        "lane": args.lane,
        "expected_commit": args.expected_commit,
        "errors": errors[:MAX_ERRORS],
        "errors_truncated": len(errors) > MAX_ERRORS,
    }


def run_campaign_command(args: argparse.Namespace, *, stdout, stderr) -> int:
    if args.project != (args.project_jobs is not None):
        args._campaign_parser.error("--project and --project-jobs must be given together")
    try:
        result = campaign(
            worktree=args.worktree,
            lane_file_path=args.file,
            lane_name=args.lane,
            expected_commit=args.expected_commit,
            verdict_path=args.verdict,
            state_dir=args.state_dir,
            request_base=args.request_base,
            project_jobs=args.project_jobs,
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
        print(
            json.dumps(_evidence_error_document(args, exc), indent=2, sort_keys=True),
            file=stdout,
        )
        return EXIT_EVIDENCE_ERROR
    if args.format == "text":
        verdict_outcome = None if result["verdict"] is None else result["verdict"]["outcome"]
        print(
            f"{result['lane']}: {result['status']} outcome={verdict_outcome} "
            f"planned={result['campaign']['planned_total']} "
            f"completed={result['campaign']['completed_total']} "
            f"pending={result['campaign']['pending_total']}",
            file=stdout,
        )
        print("  blockers: " + ", ".join(result["complete_blockers"] or ["none"]), file=stdout)
        print("  outcomes: " + json.dumps(result["campaign"]["outcomes"], sort_keys=True), file=stdout)
        print(
            "  adverse: " + " ".join(
                f"{bucket}={page['matching_total']}"
                + ("" if page["next_offset"] is None else f"(next_offset={page['next_offset']})")
                for bucket, page in result["adverse"].items()
            ),
            file=stdout,
        )
        print(
            f"  candidates: showing {len(result['candidate_details']['candidates'])} of "
            f"{result['candidate_details']['matching_total']} matching; "
            f"next_offset={result['candidate_details']['next_offset']}",
            file=stdout,
        )
        print("  ETA (diagnostic): " + json.dumps(result["timing"], sort_keys=True), file=stdout)
        if result["projection"] is not None:
            print(
                "  projection (diagnostic): " + json.dumps(result["projection"], sort_keys=True),
                file=stdout,
            )
    else:
        print(json.dumps(result, indent=2, sort_keys=True), file=stdout)
    return _exit_code(result)

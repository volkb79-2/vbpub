"""``assay analyze plan-estimate``: project campaign hours from a plan and a measured baseline.

Advisory only. It reads ``assay plan``'s JSON and one progress stream, never
classifies a candidate, and never reads the plan's ``estimated_*``, ``jobs`` or
``budget_per_candidate`` fields.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from assay_analysis import evidence

SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 16 * 1024 * 1024
MAX_PROGRESS_BYTES = 64 * 1024 * 1024
_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_BASELINE_PHASES = ("baseline", "direct")


def _read_bounded(path: Path, label: str, limit: int) -> str:
    if not path.is_file():
        raise ValueError(f"{label} {path}: not a regular file")
    if path.stat().st_size > limit:
        raise ValueError(f"{label} {path}: exceeds {limit} bytes")
    text, _ = evidence._read(path)
    return text


def _plan(text: str) -> dict[str, Any]:
    try:
        plan = evidence._json(text)
    except ValueError as exc:
        raise ValueError("plan: malformed JSON") from exc
    if not isinstance(plan, dict):
        raise ValueError("plan: top level is not an object")
    status = plan.get("status")
    if status == "unsupported":
        reason = plan.get("reason")
        if isinstance(reason, str) and reason:
            # The reason comes from JSON and is printed as one CLI refusal;
            # embedded line separators must not forge additional diagnostics.
            one_line_reason = " ".join(reason.splitlines()).strip()
            if one_line_reason:
                raise ValueError(f"plan: {one_line_reason}")
        raise ValueError("plan: candidate enumeration is unsupported")
    if status != "ok":
        raise ValueError(f"plan: status is {status!r}, not 'ok'")
    lane = plan.get("lane")
    if not isinstance(lane, str) or not lane:
        raise ValueError("plan: lane is missing or not a non-empty string")
    for key in ("commit", "tree"):
        value = plan.get(key)
        if not isinstance(value, str) or not _OBJECT_ID.fullmatch(value):
            raise ValueError(f"plan: {key} is missing or not a full object id "
                             "(a plan from before assay 7.2.0?)")
    count = plan.get("candidate_count")
    if type(count) is not int or count < 0:
        raise ValueError("plan: candidate_count is not a non-negative integer")
    candidates = plan.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != count:
        raise ValueError("plan: candidate_count does not match candidates")
    return plan


def _segments(text: str) -> list[tuple[int, dict[str, Any], list[dict[str, Any]]]]:
    """Each ``run`` header opens a segment: ``(line, run record, later records)``."""
    lines = text.split("\n")
    torn = not text.endswith("\n")
    if not torn:
        lines.pop()
    segments: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
    for number, line in enumerate(lines, start=1):
        try:
            record = evidence._json(line)
        except ValueError:
            if torn and number == len(lines):
                continue
            raise ValueError(f"progress line {number}: malformed JSON") from None
        if not isinstance(record, dict):
            raise ValueError(f"progress line {number}: not an object")
        event = record.get("event")
        if not isinstance(event, str):
            raise ValueError(f"progress line {number}: event is not a string")
        if event == "run":
            lane = record.get("lane")
            if not isinstance(lane, str) or not lane:
                raise ValueError(
                    f"progress line {number}: run header lane is missing or not a "
                    "non-empty string"
                )
            segments.append((number, record, []))
        elif not segments:
            raise ValueError(f"progress line {number}: event precedes the first run header")
        else:
            segments[-1][2].append(record)
    lanes = sorted({run["lane"] for _, run, _ in segments})
    if len(lanes) > 1:
        raise ValueError(f"progress: runs of more than one lane ({lanes}); "
                         "pass a single-lane progress file")
    return segments


def _passed_baselines(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records
            if record["event"] == "command_finished"
            and record.get("phase") in _BASELINE_PHASES and record.get("outcome") == "PASS"]


def _has_baseline(records: list[dict[str, Any]]) -> bool:
    return (any(record["event"] == "plan" and record.get("baseline_s") is not None
                for record in records) or bool(_passed_baselines(records)))


def _measure(line: int, records: list[dict[str, Any]]) -> float:
    plans = [record for record in records if record["event"] == "plan"]
    if len(plans) > 1:
        raise ValueError(f"progress run at line {line}: multiple plan events")
    if plans and plans[0].get("baseline_s") is not None:
        value = plans[0]["baseline_s"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            seconds = None
        else:
            try:
                seconds = float(value)
            except OverflowError:
                seconds = None
        if seconds is None or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"progress run at line {line}: "
                             "plan.baseline_s is not a finite positive number")
        return seconds
    finished = _passed_baselines(records)[-1]
    try:
        started = datetime.fromisoformat(finished["started"])
        ended = datetime.fromisoformat(finished["ended"])
        valid = started.tzinfo is not None and ended.tzinfo is not None and ended > started
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"progress run at line {line}: "
                         "the selected command_finished record has an invalid interval")
    return (ended - started).total_seconds()


def plan_estimate(plan_path: Path, progress_path: Path, *, workers: int = 1) -> dict[str, Any]:
    plan_text = _read_bounded(plan_path, "plan", MAX_PLAN_BYTES)
    progress_text = _read_bounded(progress_path, "progress", MAX_PROGRESS_BYTES)
    plan = _plan(plan_text)
    segments = _segments(progress_text)
    selected = [segment for segment in segments if _has_baseline(segment[2])]
    if not selected:
        raise ValueError("progress: no completed baseline")
    line, run, records = selected[-1]
    seconds = _measure(line, records)
    if run["lane"] != plan["lane"]:
        raise ValueError(
            f"lane mismatch: progress run at line {line} is for {run['lane']!r}, "
            f"plan is for {plan['lane']!r}"
        )
    if run.get("commit") != plan["commit"]:
        raise ValueError(f"commit mismatch: progress run at line {line} is at "
                         f"{run.get('commit')!r}, plan is at {plan['commit']!r}")
    worker_hours = plan["candidate_count"] * seconds / 3600
    if not math.isfinite(worker_hours):
        raise ValueError(f"progress run at line {line}: "
                         "plan.baseline_s is not a finite positive number")
    return {
        "schema_version": SCHEMA_VERSION,
        "lane": plan["lane"],
        "commit": plan["commit"],
        "tree": plan["tree"],
        "candidates": plan["candidate_count"],
        "baseline_s": round(seconds, 3),
        "per_candidate_s": round(seconds, 3),
        "workers": workers,
        "projected_worker_hours": round(worker_hours, 3),
        "projected_wall_hours": round(worker_hours / workers, 3),
    }

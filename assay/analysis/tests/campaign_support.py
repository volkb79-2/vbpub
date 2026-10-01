"""Synthetic campaign evidence builders shared by the B108 oracle tests.

The verdict-seeded helpers live in ``test_analysis_campaign``; these build
plan rows, progress runs and state records that do not need a verdict, so a
test can shape exactly the stream, store or sample size an oracle names.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from assay.candidate_identity import candidate_id_from_fields

IDENTITY = ("path", "source_sha256", "start_byte", "end_byte", "mutated_file_sha256", "operator")
JUDGE_A = "a" * 64
JUDGE_B = "b" * 64
RUN_START = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

_SNAPSHOT = {
    "schema_version": 1,
    "status": "available",
    "cgroup_identity": "fixture-cgroup",
    "host_psi": {"cpu": {"some": 0}, "memory": {"some": 0, "full": 0}, "io": {"some": 0, "full": 0}},
    "cgroup_psi": {"cpu": {"some": 0}, "memory": {"some": 0, "full": 0}, "io": {"some": 0, "full": 0}},
    "cgroup_cpu": {"nr_throttled": 0, "throttled_usec": 0},
}


def hung_evidence() -> dict:
    """A complete B107 hang trace (copied from ``test_mutation_hung_evidence_persistence``)."""
    samples = [
        {
            "wall_elapsed_s": float(elapsed),
            "eligible_elapsed_s": float(elapsed),
            "eligible_interval_s": 0.0 if elapsed == 0 else 1.0,
            "candidate_cpu_s": 3.0,
            "event_count": 0,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "resource_interval": "unknown" if elapsed == 0 else "clear",
            "resource_deltas": {},
            **({"previous_resources": None} if elapsed == 0 else {}),
            "resources": _SNAPSHOT,
        }
        for elapsed in range(32)
    ]
    return {
        "schema_version": 1,
        "policy": "pressure-adjusted-idle-v1",
        "decision": "idle-hang",
        "candidate_pid": 4242,
        "candidate_cpu_source": "process-tree-cpu-seconds",
        "wall_elapsed_s": 31.0,
        "eligible_elapsed_s": 31.0,
        "idle_eligible_s": 31.0,
        "required_idle_eligible_s": 15.0,
        "required_cpu_growth_window_s": 30.0,
        "required_cpu_growth_floor_s": 1.0,
        "candidate_session_finish_seen": False,
        "session_finish_eligible_s": None,
        "trace_complete": True,
        "trace_truncated": False,
        "samples": samples,
    }


def make_rows(count: int, *, path: str = "pkg/checks.py", operator: str = "python:compare-swap") -> list[dict]:
    """Plan rows whose ids reproduce from their own identity inputs."""
    rows = []
    for index in range(count):
        fields = {
            "path": path,
            "source_sha256": "1" * 64,
            "start_byte": 10 * index,
            "end_byte": 10 * index + 1,
            "mutated_file_sha256": f"{index + 1:064x}",
            "operator": operator,
        }
        rows.append({
            "id": candidate_id_from_fields(**fields),
            "lineno": index + 1,
            "description": f"mutation {index}",
            **fields,
        })
    return rows


def run_records(
    head: str,
    rows: list[dict],
    *,
    started: datetime = RUN_START,
    selected: list[dict] | None = None,
    executed: list[str] | None = None,
    buckets: dict[str, str] | None = None,
    resume: bool = False,
    resumed: int = 0,
    rejected: int = 0,
    rejudged: int = 0,
    judge: str | None = None,
    terminal: dict | None | bool = True,
    end: bool = True,
    elapsed: Callable[[int], float] = lambda index: 1.0 + index,
    candidate_extra: Callable[[str], dict] | None = None,
) -> list[dict]:
    """One progress run: header, optional resume, candidates, events, end, terminal."""
    chosen = rows if selected is None else selected
    ids = [row["id"] for row in chosen]
    by_id = {row["id"]: row for row in rows}
    declared_pending = ids[resumed:]
    pending_ids = declared_pending if executed is None else executed
    resolved = {candidate_id: "killed" for candidate_id in ids}
    resolved.update(buckets or {})
    records: list[dict] = [{
        "event": "run", "commit": head, "lane": "package", "rigor": ["R0", "R2"],
        "started": started.isoformat(),
    }]
    if resume or resumed or rejected or rejudged:
        records.append({
            "event": "resume", "resumed_total": resumed, "rejected_total": rejected,
            "rejudged_total": rejudged,
        })
    candidates = {
        "event": "candidates", "commit": head, "candidate_total": len(rows),
        "selected_total": len(ids), "pending_total": len(declared_pending),
    }
    if judge is not None:
        candidates["judge_sha256"] = judge
    records.append(candidates)
    records.append({"event": "baseline", "candidate_index": -1, "candidate_total": len(rows)})
    for index, candidate_id in enumerate(pending_ids):
        row = by_id[candidate_id]
        records.append({
            "event": "candidate",
            "candidate_id": candidate_id,
            "candidate_index": index,
            "candidate_total": len(declared_pending),
            **{key: row[key] for key in ("path", "lineno", "operator", "description", "start_byte", "end_byte")},
            "outcome_bucket": resolved[candidate_id],
            "elapsed_seconds": elapsed(index),
            "emitted_at": (started + timedelta(seconds=10 + index)).isoformat(),
            **({} if candidate_extra is None else candidate_extra(candidate_id)),
        })
    if end:
        counts = {name: 0 for name in ("killed", "survived", "equivalent", "crashed", "hung", "budget_exceeded")}
        for candidate_id in ids:
            counts[resolved[candidate_id]] += 1
        records.append({"event": "end", "candidate_total": len(rows), "buckets": counts, "reason": None})
    if terminal:
        final = {"outcome": "PASS", "exit_code": 0, "reason_code": None}
        if isinstance(terminal, dict):
            final.update(terminal)
        records.append({
            "event": "verdict_written",
            **final,
            "emitted_at": (started + timedelta(seconds=10 + len(pending_ids) + 5)).isoformat(),
            "elapsed_s": float(10 + len(pending_ids) + 5),
        })
    return records


def write_records(path: Path, records: list[dict], *, append: bool = False) -> None:
    text = "".join(json.dumps(item) + "\n" for item in records)
    if append:
        text = path.read_text() + text
    path.write_text(text)


def state_record(row: dict, bucket: str, judge: str = JUDGE_A, **extra) -> dict:
    """A shape-valid mutation-state record for a plan row."""
    return {
        "schema_version": 1,
        "candidate_id": row["id"],
        **{key: row[key] for key in IDENTITY},
        "outcome_bucket": bucket,
        "judge_sha256": judge,
        **extra,
    }


def write_state(directory: Path, records: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for record in records:
        (directory / f"{record['candidate_id']}.json").write_text(json.dumps(record))
    return directory

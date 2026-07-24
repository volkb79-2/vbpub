"""F018 P1 — carver session dataclasses, projector, and IO.

PACKAGE F018 P1 (plan-long-running-carver.md §2.2, §3.1, §4.1):
establishes the durable vocabulary for the strategic carver WITHOUT
launching any session or changing scheduling. All types use the _Serde
mixin for `to_dict`/`from_dict` round-trip (mirrors Attempt/GateResult).

The pure `project_session(events)` projector folds CARVER_* audit events
into a CarverSessionSnapshot. It is deterministic, replayable, and
byte-identical — the replay oracle's target.

`load_session`/`write_session` provide atomic I/O under the state volume
at `<state-volume>/<project>/carver/session.json`, following the same
atomic-write pattern as statefiles in storage.py.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import paths
from .types import (
    CarverStatus, Event, EventType, _Serde, _list_of, _opt, parse_iso,
)


# ---------------------------------------------------------------------------
# MergeDigest (plan §3.1)
# ---------------------------------------------------------------------------

@dataclass
class MergeDigest(_Serde):
    """Typed merge digest — what the carver consumes after every merge,
    replacing whole-repository re-scans. Plan §3.1 field set."""
    schema_version: int
    digest_id: str
    task_id: str
    merge_commit: str
    first_parent: str
    handoff: dict[str, Any]           # {path, sha256, title, input_revision}
    files: list[dict[str, str]]       # [{path, change}]  change=A|M|D|R
    diffstat: dict[str, int]          # {files_changed, insertions, deletions}
    review: dict[str, Any]            # {verdict, attempt_id, report_path, report_sha256}
    spine_delta: dict[str, Any]       # {changed, paths, before_revisions, after_revisions, changed_ids}


# ---------------------------------------------------------------------------
# CarverSessionSnapshot (plan §2.2)
# ---------------------------------------------------------------------------

@dataclass
class CarverSessionSnapshot(_Serde):
    """Durable projection of the carver session's current state.
    Rebuildable from CARVER_* events. Plan §2.2 field list."""
    project: str
    generation: int
    status: CarverStatus
    session_id: str | None = None
    route: dict[str, Any] | None = None
    opened_at: str | None = None
    last_success_at: str | None = None
    last_turn_sequence: int | None = None
    last_consumed_event_sequence: int = 0
    last_proposal_id: str | None = None
    spine_revisions: dict[str, str] = field(default_factory=dict)
    successful_turns_since_compaction: int = 0
    measured_context_tokens: int | None = None
    measured_context_ratio: float | None = None
    resume_failures: int = 0
    last_compaction_at: str | None = None

    _FIELD_TYPES = {
        "status": CarverStatus,
    }


# ---------------------------------------------------------------------------
# CarverTurnResult (plan §4.1)
# ---------------------------------------------------------------------------

@dataclass
class CarverTurnResult(_Serde):
    """Typed turn output envelope — the carver's output after any mode."""
    kind: str
    schema_version: int
    proposal_id: str
    turn_id: str
    source: dict[str, Any]           # {mode, refs, base_revision, merge_digest_cursor}
    artifacts: list[dict[str, Any]]  # [{kind, path, sha256, source_ref}]
    dispositions: list[dict[str, Any]]  # [{source_ref, result, artifact_ref, reason_code}]
    outcome: str
    headroom_estimate: int = 0

    _FIELD_TYPES = {}


# ---------------------------------------------------------------------------
# Planner-view types (plan §4.2) — bounded typed records, no prose
# ---------------------------------------------------------------------------

@dataclass
class ValidatedCarveProposal(_Serde):
    """A proposal that passed schema/hash/lint/revision checks, ready for
    deterministic admission by plan_project(). Carries only enums, ids,
    hashes — no prose."""
    proposal_id: str
    generation: int
    source_mode: str
    artifact_ids: list[str]
    artifact_paths: list[str]
    artifact_hashes: list[str]
    outcome: str

    _FIELD_TYPES = {}


@dataclass
class CarverFeed(_Serde):
    """A pending merge digest for the carver to consume. Bounded typed
    record — no prose."""
    digest_id: str
    merge_commit: str
    task_id: str
    event_sequence: int
    files_changed: int

    _FIELD_TYPES = {}


@dataclass
class HumanIntake(_Serde):
    """A pending human intake entry. Enums/ids only, no raw prose."""
    intake_id: str
    kind: str           # idea|plan|question|feedback
    event_sequence: int

    _FIELD_TYPES = {}


# ---------------------------------------------------------------------------
# Pure projector
# ---------------------------------------------------------------------------

def project_session(events: Iterable[Event]) -> CarverSessionSnapshot:
    """Fold CARVER_* audit events into a session snapshot.

    Deterministic and replayable: calling this twice with the same events
    produces the same snapshot. Events that are not CARVER_* or that are
    out-of-order are handled per spec (defined behavior, not a crash):
    unknown-to-projector events are silently skipped; the projector never
    raises.
    """
    snap = CarverSessionSnapshot(
        project="",
        generation=0,
        status=CarverStatus.ABSENT,
    )

    for ev in events:
        t = ev.type
        p = ev.payload or {}

        if t is EventType.CARVER_SESSION_STARTED:
            snap.project = ev.project
            snap.generation = p.get("generation", snap.generation + 1)
            snap.session_id = p.get("session_id")
            snap.route = p.get("route")
            snap.spine_revisions = dict(p.get("spine_revisions", {}))
            snap.status = CarverStatus.WARM
            snap.opened_at = ev.timestamp.isoformat()
            snap.last_success_at = ev.timestamp.isoformat()

        elif t is EventType.CARVER_SESSION_RESUMED:
            snap.generation = p.get("generation", snap.generation)
            snap.route = p.get("route", snap.route)
            snap.status = CarverStatus.WARM
            snap.last_success_at = ev.timestamp.isoformat()
            if snap.last_turn_sequence is not None:
                snap.last_turn_sequence += 1
            else:
                snap.last_turn_sequence = 1
            snap.successful_turns_since_compaction += 1

        elif t is EventType.CARVER_CONTEXT_CONSUMED:
            snap.last_consumed_event_sequence = max(
                snap.last_consumed_event_sequence,
                p.get("highest_event_sequence", 0),
            )
            snap.measured_context_tokens = p.get("context_tokens", snap.measured_context_tokens)
            snap.measured_context_ratio = p.get("context_ratio", snap.measured_context_ratio)
            snap.spine_revisions.update(p.get("spine_revisions", {}))

        elif t is EventType.CARVER_PROPOSAL_RECORDED:
            snap.last_proposal_id = p.get("proposal_id", snap.last_proposal_id)
            snap.last_turn_sequence = p.get("turn_id", snap.last_turn_sequence)

        elif t is EventType.CARVER_COMPACTION_REQUESTED:
            snap.status = CarverStatus.COMPACTING

        elif t is EventType.CARVER_COMPACTION_FINISHED:
            snap.generation = p.get("new_generation", snap.generation)
            snap.session_id = p.get("new_session_id", snap.session_id)
            snap.successful_turns_since_compaction = 0
            snap.last_compaction_at = ev.timestamp.isoformat()
            snap.status = CarverStatus.WARM
            snap.measured_context_tokens = p.get("tokens_after", snap.measured_context_tokens)
            snap.measured_context_ratio = p.get("ratio_after", snap.measured_context_ratio)

        elif t is EventType.CARVER_SESSION_ROTATED:
            snap.generation = p.get("new_generation", snap.generation + 1)
            snap.session_id = None
            snap.status = CarverStatus.ROTATING
            snap.resume_failures = 0
            snap.successful_turns_since_compaction = 0

        elif t is EventType.CARVER_SESSION_DEGRADED:
            snap.resume_failures = p.get("retry_count", snap.resume_failures + 1)
            snap.status = CarverStatus.DEGRADED
            snap.last_turn_sequence = None

        # Non-CARVER events and unknown event types are silently skipped
        # (defined behavior, not a crash).

    return snap


# ---------------------------------------------------------------------------
# Session IO — atomic projection under the state volume
# ---------------------------------------------------------------------------

_SESSION_FILE = "carver/session.json"


def _session_path(project: str) -> Path:
    return paths.project_dir(project) / _SESSION_FILE


def load_session(project: str) -> CarverSessionSnapshot | None:
    """Read the session snapshot from the state volume, or None if absent."""
    p = _session_path(project)
    if not p.exists():
        return None
    return CarverSessionSnapshot.from_dict(json.loads(p.read_text(encoding="utf-8")))


def write_session(project: str, snapshot: CarverSessionSnapshot) -> None:
    """Atomic write (tmp + rename) of the session snapshot.

    Follows the same atomic-write pattern as storage.save_state: write to a
    .tmp file, then os.replace (atomic on POSIX), so a concurrent reader
    never sees a partial write.
    """
    paths.ensure_layout(project)
    p = _session_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(snapshot.to_dict(), indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, p)

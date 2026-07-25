"""F018 P1 — carver session dataclass round-trip, projector replay,
and session IO tests. See plan-long-running-carver.md §2.2, §3.1, §4.1.

Oracles:
1. Type round-trip: every new dataclass from_dict(to_dict()) == x.
2. Projection replay deterministic/byte-identical.
3. Events not in CARVER_* are handled per spec (silent skip, not a crash).
4. Session IO: write then reload produces byte-identical to_dict().
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom import paths
from nyxloom.carver_session import (
    CarverFeed, CarverSessionSnapshot, CarverTurnResult, HumanIntake,
    MergeDigest, ValidatedCarveProposal, load_session, project_session,
    write_session,
)
from nyxloom.types import (
    Actor, ActorKind, CarverStatus, Event, EventType, utc_now,
)


def _utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _actor() -> Actor:
    return Actor(kind=ActorKind.TICK, id="test")


def _event(ev_type: EventType, project: str = "p1test",
           sequence: int = 1, payload: dict | None = None,
           task_id: str | None = None) -> Event:
    return Event(
        schema_version=1, sequence=sequence,
        timestamp=_utc(2026, 7, 24),
        project=project,
        actor=_actor(),
        type=ev_type,
        payload=payload or {},
        task_id=task_id,
    )


# ==========================================================================
# Oracle 1: type round-trip
# ==========================================================================

class TestCarverSessionSnapshotRoundTrip:
    """Every new _Serde dataclass round-trips through to_dict/from_dict."""

    def test_empty_snapshot_round_trip(self):
        snap = CarverSessionSnapshot(project="p1", generation=0,
                                      status=CarverStatus.ABSENT)
        d = snap.to_dict()
        restored = CarverSessionSnapshot.from_dict(d)
        assert restored.to_dict() == d

    def test_warm_snapshot_round_trip(self):
        snap = CarverSessionSnapshot(
            project="p1", generation=1, status=CarverStatus.WARM,
            session_id="sess-abc123",
            route={"route_id": "carve-3", "model": "deepseek-v4"},
            opened_at="2026-07-24T00:00:00+00:00",
            last_success_at="2026-07-24T01:00:00+00:00",
            last_turn_sequence=5,
            last_consumed_event_sequence=42,
            last_proposal_id="prop-1",
            spine_revisions={"north_star": "abc", "roadmap": "def"},
            successful_turns_since_compaction=3,
            measured_context_tokens=12000,
            measured_context_ratio=0.45,
            resume_failures=0,
            last_compaction_at="2026-07-24T02:00:00+00:00",
        )
        d = snap.to_dict()
        restored = CarverSessionSnapshot.from_dict(d)
        assert restored.to_dict() == d
        assert restored.status is CarverStatus.WARM
        assert restored.generation == 1

    def test_degraded_snapshot_round_trip(self):
        snap = CarverSessionSnapshot(
            project="p1", generation=1, status=CarverStatus.DEGRADED,
            session_id=None, resume_failures=2,
        )
        d = snap.to_dict()
        restored = CarverSessionSnapshot.from_dict(d)
        assert restored.status is CarverStatus.DEGRADED
        assert restored.resume_failures == 2
        assert restored.session_id is None

    def test_snapshot_rejects_unknown_keys(self):
        with pytest.raises(ValueError, match="unknown keys"):
            CarverSessionSnapshot.from_dict({
                "project": "p1", "generation": 0, "status": "ABSENT",
                "bogus_field": "oops",
            })

    def test_snapshot_status_from_string(self):
        snap = CarverSessionSnapshot.from_dict({
            "project": "p1", "generation": 0, "status": "WARM",
        })
        assert snap.status is CarverStatus.WARM

    def test_snapshot_unknown_status_raises(self):
        with pytest.raises(ValueError):
            CarverSessionSnapshot.from_dict({
                "project": "p1", "generation": 0, "status": "UNKNOWN_STATUS",
            })


class TestMergeDigestRoundTrip:
    """MergeDigest round-trip including nested structures."""

    def test_full_digest_round_trip(self):
        d = MergeDigest(
            schema_version=1,
            digest_id="merge:p1:123",
            task_id="demo-P01",
            merge_commit="abc123",
            first_parent="def456",
            handoff={"path": "handoffs/demo-P01.md", "sha256": "aaa",
                     "title": "fix db", "input_revision": "main@abc"},
            files=[{"path": "src/db.py", "change": "M"}],
            diffstat={"files_changed": 1, "insertions": 10, "deletions": 2},
            review={"verdict": "approved", "attempt_id": "att-1",
                    "report_path": None, "report_sha256": None},
            spine_delta={"changed": False, "paths": [],
                         "before_revisions": {}, "after_revisions": {},
                         "changed_ids": {"features": [], "milestones": [],
                                         "backlog_items": []}},
        )
        restored = MergeDigest.from_dict(d.to_dict())
        assert restored.digest_id == d.digest_id
        assert restored.handoff["path"] == "handoffs/demo-P01.md"
        assert restored.files[0]["change"] == "M"
        assert restored.diffstat["files_changed"] == 1

    def test_digest_missing_field_raises(self):
        with pytest.raises(TypeError):
            MergeDigest.from_dict({"digest_id": "x"})


class TestCarverTurnResultRoundTrip:

    def test_full_result_round_trip(self):
        r = CarverTurnResult(
            kind="carve-proposal",
            schema_version=1,
            proposal_id="p1:carve:1:1",
            turn_id="turn-1",
            source={"mode": "headroom", "refs": ["B27"],
                    "base_revision": "abc", "merge_digest_cursor": 100},
            artifacts=[{"kind": "handoff", "path": "handoffs/id.md",
                        "sha256": "aaa", "source_ref": "B27"}],
            dispositions=[{"source_ref": "B27", "result": "handoff",
                           "artifact_ref": "handoffs/id.md",
                           "reason_code": "ready"}],
            outcome="CANDIDATES_READY",
            headroom_estimate=5,
        )
        restored = CarverTurnResult.from_dict(r.to_dict())
        assert restored.kind == "carve-proposal"
        assert restored.outcome == "CANDIDATES_READY"
        assert restored.artifacts[0]["sha256"] == "aaa"


class TestValidatedCarveProposalRoundTrip:

    def test_round_trip(self):
        v = ValidatedCarveProposal(
            proposal_id="p1:carve:1:1",
            generation=1,
            source_mode="headroom",
            artifact_ids=["handoff-1"],
            artifact_paths=["handoffs/id.md"],
            artifact_hashes=["aaa"],
            outcome="CANDIDATES_READY",
        )
        assert ValidatedCarveProposal.from_dict(v.to_dict()).to_dict() == v.to_dict()


class TestCarverFeedRoundTrip:

    def test_round_trip(self):
        f = CarverFeed(
            digest_id="merge:p1:1",
            merge_commit="abc",
            task_id="demo-P01",
            event_sequence=42,
            files_changed=3,
        )
        assert CarverFeed.from_dict(f.to_dict()).digest_id == "merge:p1:1"


class TestHumanIntakeRoundTrip:

    def test_round_trip(self):
        h = HumanIntake(
            intake_id="intake-1",
            kind="idea",
            event_sequence=50,
        )
        assert HumanIntake.from_dict(h.to_dict()).intake_id == "intake-1"


# ==========================================================================
# Oracle 2: projection replay is deterministic/byte-identical
# ==========================================================================

class TestProjectSession:

    def test_empty_events_returns_absent(self):
        snap = project_session([])
        assert snap.status is CarverStatus.ABSENT
        assert snap.generation == 0
        assert snap.project == ""

    def test_started_moves_to_warm(self):
        ev = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                     payload={
                         "generation": 1, "session_id": "sess-1",
                         "route": {"route_id": "carve-3"},
                         "spine_revisions": {"north_star": "abc"},
                     })
        snap = project_session([ev])
        assert snap.status is CarverStatus.WARM
        assert snap.generation == 1
        assert snap.session_id == "sess-1"
        assert snap.spine_revisions == {"north_star": "abc"}

    def test_resumed_increments_turn_sequence(self):
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        resume = _event(EventType.CARVER_SESSION_RESUMED, project="p1",
                         payload={"generation": 1})
        snap = project_session([start, resume])
        assert snap.status is CarverStatus.WARM
        assert snap.last_turn_sequence == 1
        assert snap.successful_turns_since_compaction == 1

    def test_second_resume_increments_turn_sequence(self):
        """Exercises line 184: last_turn_sequence += 1 when it is not None
        (the second resume increments from 1 to 2)."""
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        resume1 = _event(EventType.CARVER_SESSION_RESUMED, project="p1",
                          payload={"generation": 1})
        resume2 = _event(EventType.CARVER_SESSION_RESUMED, project="p1",
                          payload={"generation": 1})
        snap = project_session([start, resume1, resume2])
        assert snap.last_turn_sequence == 2
        assert snap.successful_turns_since_compaction == 2

    def test_proposal_then_resume_does_not_crash_projector(self):
        """Regression (F018 AD3 discovery): a CARVER_PROPOSAL_RECORDED carries a
        STRING turn_id (the carver task id, e.g. 'carver-session-p1-3' -- the
        existing proposal test used an unrealistic int 5, which masked this).
        Before the fix, the projector assigned that string to last_turn_sequence
        (an int COUNTER), so a FOLLOWING CARVER_SESSION_RESUMED folded
        `str += 1` and raised TypeError out of project_session -- a persistent
        per-tick crash the AD3 repair flow (invalid proposal -> repair resume)
        reliably produces. project_session must fold this ordering without
        raising, and last_turn_sequence must remain the resume-turn counter."""
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        resume1 = _event(EventType.CARVER_SESSION_RESUMED, project="p1",
                          payload={"generation": 1})
        prop = _event(EventType.CARVER_PROPOSAL_RECORDED, project="p1",
                       payload={"proposal_id": "p1:carve:1:carver-session-p1-3",
                                "turn_id": "carver-session-p1-3"})
        resume2 = _event(EventType.CARVER_SESSION_RESUMED, project="p1",
                          payload={"generation": 1})
        # Must not raise (the whole point) ...
        snap = project_session([start, resume1, prop, resume2])
        # ... and the counter is intact (two RESUMED turns), NOT the string.
        assert snap.last_turn_sequence == 2
        assert isinstance(snap.last_turn_sequence, int)
        # The proposal's identity is still captured.
        assert snap.last_proposal_id == "p1:carve:1:carver-session-p1-3"

    def test_proposal_recorded_leaves_turn_sequence_counter_untouched(self):
        """A CARVER_PROPOSAL_RECORDED records the proposal id but does NOT touch
        the resume-turn counter (its value comes solely from RESUMED)."""
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        resume = _event(EventType.CARVER_SESSION_RESUMED, project="p1",
                         payload={"generation": 1})
        prop = _event(EventType.CARVER_PROPOSAL_RECORDED, project="p1",
                       payload={"proposal_id": "p1:carve:1:t", "turn_id": "t"})
        snap = project_session([start, resume, prop])
        assert snap.last_turn_sequence == 1  # from the single RESUMED, unchanged
        assert snap.last_proposal_id == "p1:carve:1:t"

    def test_context_consumed_advances_cursor(self):
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        consume = _event(EventType.CARVER_CONTEXT_CONSUMED, project="p1",
                          payload={
                              "highest_event_sequence": 100,
                              "context_tokens": 8000,
                              "context_ratio": 0.50,
                          })
        snap = project_session([start, consume])
        assert snap.last_consumed_event_sequence == 100
        assert snap.measured_context_tokens == 8000
        assert snap.measured_context_ratio == 0.50

    def test_proposal_recorded_sets_proposal_id(self):
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        prop = _event(EventType.CARVER_PROPOSAL_RECORDED, project="p1",
                       payload={"proposal_id": "p1:carve:1:1", "turn_id": 5})
        snap = project_session([start, prop])
        assert snap.last_proposal_id == "p1:carve:1:1"

    def test_compaction_cycle(self):
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        req = _event(EventType.CARVER_COMPACTION_REQUESTED, project="p1", payload={})
        finish = _event(EventType.CARVER_COMPACTION_FINISHED, project="p1",
                         payload={"new_generation": 2, "new_session_id": "sess-2",
                                   "tokens_after": 5000, "ratio_after": 0.30})
        snap = project_session([start, req, finish])
        assert snap.generation == 2
        assert snap.session_id == "sess-2"
        assert snap.successful_turns_since_compaction == 0
        assert snap.last_compaction_at is not None
        assert snap.measured_context_tokens == 5000

    def test_rotation_resets_session(self):
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        rotate = _event(EventType.CARVER_SESSION_ROTATED, project="p1",
                         payload={"new_generation": 2})
        snap = project_session([start, rotate])
        assert snap.generation == 2
        assert snap.session_id is None
        assert snap.status is CarverStatus.ROTATING
        assert snap.resume_failures == 0
        assert snap.successful_turns_since_compaction == 0

    def test_degraded_increments_resume_failures(self):
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        deg = _event(EventType.CARVER_SESSION_DEGRADED, project="p1",
                      payload={"retry_count": 2})
        snap = project_session([start, deg])
        assert snap.status is CarverStatus.DEGRADED
        assert snap.resume_failures == 2

    def test_non_carver_events_are_silently_skipped(self):
        """Known handled events that are not CARVER_* must be silently
        skipped — the projector never raises."""
        ev = _event(EventType.TASK_CREATED, project="p1",
                     payload={"statefile": {}})
        snap = project_session([ev])
        assert snap.status is CarverStatus.ABSENT

    def test_replay_deterministic_byte_identical(self):
        events = [
            _event(EventType.CARVER_SESSION_STARTED, project="p1",
                    payload={"generation": 1, "session_id": "sess-1",
                              "spine_revisions": {"north_star": "abc"}},
                    sequence=1),
            _event(EventType.CARVER_CONTEXT_CONSUMED, project="p1",
                    payload={"highest_event_sequence": 42,
                              "spine_revisions": {"roadmap": "def"}},
                    sequence=2),
            _event(EventType.CARVER_PROPOSAL_RECORDED, project="p1",
                    payload={"proposal_id": "p1:carve:1:1"}, sequence=3),
        ]
        snap1 = project_session(events)
        snap2 = project_session(events)
        assert snap1.to_dict() == snap2.to_dict()

    def test_different_event_streams_produce_different_snapshots(self):
        """Smoke test: projection is not accidentally constant."""
        e1 = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                     payload={"generation": 1, "session_id": "sess-1"})
        e2 = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                     payload={"generation": 2, "session_id": "sess-2"})
        assert project_session([e1]).generation != project_session([e2]).generation

    def test_degraded_marks_status_correctly(self):
        """DEGRADED sets status, last_turn_sequence to None."""
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        resume = _event(EventType.CARVER_SESSION_RESUMED, project="p1",
                         payload={"generation": 1})
        deg = _event(EventType.CARVER_SESSION_DEGRADED, project="p1",
                      payload={"retry_count": 1})
        snap = project_session([start, resume, deg])
        assert snap.status is CarverStatus.DEGRADED
        assert snap.last_turn_sequence is None
        assert snap.resume_failures == 1

    def test_compacting_status_set_on_request(self):
        start = _event(EventType.CARVER_SESSION_STARTED, project="p1",
                        payload={"generation": 1, "session_id": "sess-1"})
        req = _event(EventType.CARVER_COMPACTION_REQUESTED, project="p1", payload={})
        snap = project_session([start, req])
        assert snap.status is CarverStatus.COMPACTING

    def test_unknown_event_type_is_safe(self):
        """Events whose type is not in the CARVER_* family must not crash."""
        ev = _event(EventType.FINDING_RECORDED, project="p1",
                     payload={"kind": "generic", "title": "test"})
        snap = project_session([ev])
        assert snap.status is CarverStatus.ABSENT


# ==========================================================================
# Oracle: from_dict negative — missing required field raises
# ==========================================================================

class TestFromDictNegative:

    def test_snapshot_missing_required_raises_type_error(self):
        """Missing 'project' (a required positional arg via from_dict)
        should raise TypeError from the dataclass constructor."""
        with pytest.raises(TypeError):
            CarverSessionSnapshot.from_dict({"generation": 0, "status": "ABSENT"})


# ==========================================================================
# Oracle 4 (part): session IO — write then reload equals identity
# ==========================================================================

class TestSessionIO:

    def test_load_nonexistent_returns_none(self, tmp_state):
        assert load_session("missing-project") is None

    def test_write_then_load_round_trips(self, tmp_state):
        snap = CarverSessionSnapshot(
            project="p1", generation=1, status=CarverStatus.WARM,
            session_id="sess-abc",
            spine_revisions={"north_star": "abc"},
        )
        write_session("p1", snap)
        loaded = load_session("p1")
        assert loaded is not None
        assert loaded.to_dict() == snap.to_dict()

    def test_write_is_atomic(self, tmp_state):
        """The atomic write pattern (tmp + replace) must leave a valid file."""
        snap = CarverSessionSnapshot(
            project="p1", generation=1, status=CarverStatus.WARM,
        )
        write_session("p1", snap)
        p = paths.project_dir("p1") / "carver" / "session.json"
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["project"] == "p1"
        assert data["status"] == "WARM"

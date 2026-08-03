"""CR-05f: carve dispatch and proposal admission.

The end-to-end launch behaviour is covered by `test_carver_session_executor.py`
and `test_carve_from_brief.py` against real repositories, and by the §5.1
differential. What is here is the packet-assembly detail those cannot reach
cheaply -- the carve packet IS the interface between the factory's state and
the agent that extends it, so what it says about a project with no roadmap,
or with more review follow-ups than it will show, is a contract rather than a
formatting choice.
"""

from __future__ import annotations

import dataclasses

import pytest

from nyxloom import (
    effects, effects_carve, effects_carver, effects_lifecycle, storage,
)
from nyxloom.types import (
    Actor, ActorKind, Event, EventType, TaskState, TaskStateFile, utc_now,
)


def _effector():
    ports = effects.EffectPorts.system()
    return effects_carve.CarveEffector(
        ports, effects_carver.CarverEffector(ports),
        effects_lifecycle.LifecycleEffector(
            ports, effects.ProviderPauseRegistry(ports.clock)))


def _review(task_id: str, result: str, seq: int) -> Event:
    return Event(schema_version=storage.SCHEMA_VERSION, sequence=seq,
                 timestamp=utc_now(), project="demo",
                 actor=Actor(ActorKind.TICK, "t"),
                 type=EventType.REVIEW_RECORDED,
                 payload={"result": result}, task_id=task_id)


class TestRecentReviewFollowUps:

    def test_review_verdicts_come_back_newest_first(self):
        """Newest first because the carver reads the top of the list: the most
        recent rejection is the one most likely to still need follow-up."""
        events = [_review("t1", "approved", 1), _review("t2", "rejected", 2)]
        assert _effector()._recent_review_follow_ups(
            "demo", events=events) == [("t2", "rejected"), ("t1", "approved")]

    def test_the_limit_truncates_rather_than_growing_the_packet(self):
        """Unbounded, this would grow the carve prompt with every review the
        project has ever recorded."""
        events = [_review(f"t{i}", "rejected", i) for i in range(20)]
        out = _effector()._recent_review_follow_ups("demo", limit=3,
                                                    events=events)
        assert out == [("t19", "rejected"), ("t18", "rejected"),
                       ("t17", "rejected")]

    def test_a_verdict_with_no_result_is_reported_as_unknown_not_dropped(self):
        """Dropping it would make a malformed record indistinguishable from no
        record, and the carver would carve as though the review never ran."""
        bare = Event(schema_version=storage.SCHEMA_VERSION, sequence=1,
                     timestamp=utc_now(), project="demo",
                     actor=Actor(ActorKind.TICK, "t"),
                     type=EventType.REVIEW_RECORDED, payload={}, task_id="t1")
        assert _effector()._recent_review_follow_ups(
            "demo", events=[bare]) == [("t1", "?")]

    def test_an_event_with_no_task_is_not_a_follow_up(self):
        wave = Event(schema_version=storage.SCHEMA_VERSION, sequence=1,
                     timestamp=utc_now(), project="demo",
                     actor=Actor(ActorKind.TICK, "t"),
                     type=EventType.REVIEW_RECORDED,
                     payload={"result": "rejected"}, task_id=None)
        assert _effector()._recent_review_follow_ups("demo", events=[wave]) == []

    def test_nothing_recorded_yields_nothing(self):
        assert _effector()._recent_review_follow_ups("demo", events=[]) == []

    def test_a_fresh_read_is_taken_when_the_caller_hands_down_nothing(
            self, tmp_state, sample_project):
        """The `events is not None` short-circuit exists so a caller inside a
        pass reuses the log it already holds; a caller outside one must still
        get a real read rather than an empty list."""
        storage.append_and_apply("demo", {}, actor=Actor(ActorKind.TICK, "t"),
                                 type=EventType.REVIEW_RECORDED,
                                 payload={"result": "rejected"}, task_id="t9")
        assert _effector()._recent_review_follow_ups("demo") == [
            ("t9", "rejected")]


class TestCarveSourceNotes:

    def test_gap_analysis_documents_are_listed_when_present(
            self, tmp_state, sample_project):
        """A gap document is a carve SOURCE -- it names work the roadmap does
        not yet describe -- so the packet has to point at it by path."""
        (sample_project.root / "docs" / "gap-auth.md").write_text(
            "# gap\n", encoding="utf-8")
        (sample_project.root / "docs" / "gap-storage.md").write_text(
            "# gap\n", encoding="utf-8")
        lines = _effector()._carve_source_note_lines(sample_project)
        gap = [ln for ln in lines if ln.startswith("- gap analysis:")]
        assert len(gap) == 1
        assert "docs/gap-auth.md" in gap[0] and "docs/gap-storage.md" in gap[0]

    def test_an_archived_gap_document_is_not_a_live_source(
            self, tmp_state, sample_project):
        """Archived means the project decided it is done with it. Carving from
        it would re-propose work that was deliberately closed."""
        archive = sample_project.root / "docs" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / "gap-old.md").write_text("# gap\n", encoding="utf-8")
        lines = _effector()._carve_source_note_lines(sample_project)
        assert not [ln for ln in lines if "gap-old.md" in ln]

    def test_a_project_with_neither_roadmap_nor_gaps_says_so(
            self, tmp_state, sample_project):
        """Silence would read as "the sources are empty"; the packet has to
        distinguish that from "the sources were not found"."""
        cfg = dataclasses.replace(sample_project, roadmap="docs/NOPE.md")
        lines = _effector()._carve_source_note_lines(cfg)
        assert any("none found" in ln for ln in lines)


class TestCarverCarvePromptFollowUps:

    def test_recorded_follow_ups_are_listed_in_the_prompt(
            self, tmp_state, sample_project):
        storage.append_and_apply("demo", {}, actor=Actor(ActorKind.TICK, "t"),
                                 type=EventType.REVIEW_RECORDED,
                                 payload={"result": "rejected"}, task_id="t7")
        prompt = _effector()._build_carver_carve_prompt(
            sample_project, "demo", "carve-demo-1", 1, {}, generation=1,
            sub_mode="headroom")
        assert "   - t7: rejected" in prompt

    def test_no_follow_ups_says_none_recorded_rather_than_nothing(
            self, tmp_state, sample_project):
        prompt = _effector()._build_carver_carve_prompt(
            sample_project, "demo", "carve-demo-1", 1, {}, generation=1,
            sub_mode="headroom")
        assert "   - none recorded yet" in prompt

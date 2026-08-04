"""Tests for mark IO and mark→phase folding.

`marks.jsonl` can be written by processes this profiler does not control
(DESIGN.md §3), so the two properties that matter most are the ones a single
in-process test can't fake convincingly with mocks: many real writers
appending to the same file at once must never interleave into a corrupt
line, and a reader must survive whatever a writer that got killed mid-line
leaves behind. Both are exercised against the real filesystem here, not
against a mock.
"""

from __future__ import annotations

import json
import threading
import time

from lib.model import Mark, Phase
from lib.phases import emit_mark, load_marks, phases_from_marks


class TestEmitMark:
    def test_writes_one_line_with_the_expected_fields(self, tmp_path):
        run = str(tmp_path)
        mark = emit_mark(run, "job-a", kind="phase", meta={"foo": "bar"}, when=1000.0)
        assert mark.name == "job-a"
        assert mark.kind == "phase"
        assert mark.t == 1000.0
        assert mark.mono is None
        assert mark.meta == {"foo": "bar"}

        lines = (tmp_path / "marks.jsonl").read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["name"] == "job-a"
        assert data["kind"] == "phase"

    def test_default_kind_is_phase_and_when_defaults_to_now(self, tmp_path):
        before = time.time()
        mark = emit_mark(str(tmp_path), "startup")
        after = time.time()
        assert mark.kind == "phase"
        assert before <= mark.t <= after

    def test_source_defaults_to_mark_but_a_wrapper_can_say_so(self, tmp_path):
        run = str(tmp_path)
        own = emit_mark(run, "job-a")
        wrapped = emit_mark(run, "make test", source="wrapper")
        assert own.source == "mark"
        assert wrapped.source == "wrapper"
        # Round-trips through the file, not just the returned object.
        loaded = load_marks(run)
        assert [m.source for m in loaded] == ["mark", "wrapper"]

    def test_appends_rather_than_overwrites(self, tmp_path):
        run = str(tmp_path)
        emit_mark(run, "one", when=1.0)
        emit_mark(run, "two", when=2.0)
        lines = (tmp_path / "marks.jsonl").read_text().splitlines()
        assert len(lines) == 2


class TestLoadMarks:
    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert load_marks(str(tmp_path / "no-such-run")) == []

    def test_empty_file_is_empty(self, tmp_path):
        (tmp_path / "marks.jsonl").write_text("")
        assert load_marks(str(tmp_path)) == []

    def test_round_trips_every_field(self, tmp_path):
        run = str(tmp_path)
        emit_mark(run, "startup", kind="phase", when=10.0)
        emit_mark(run, "checkpoint", kind="event", meta={"n": 1}, when=15.0)
        marks = load_marks(run)
        assert [m.name for m in marks] == ["startup", "checkpoint"]
        assert marks[1].kind == "event"
        assert marks[1].meta == {"n": 1}

    def test_tolerates_a_truncated_final_line(self, tmp_path):
        run = tmp_path
        emit_mark(str(run), "startup", when=0.0)
        emit_mark(str(run), "job-a", when=10.0)
        # Simulate a writer that was killed mid-write: a fragment with no
        # closing brace and no trailing newline.
        with open(run / "marks.jsonl", "a", encoding="utf-8") as fh:
            fh.write('{"t": 20.0, "name": "torn", "kind": "ph')
        marks = load_marks(str(run))
        assert [m.name for m in marks] == ["startup", "job-a"]

    def test_mark_missing_a_required_field_is_skipped_not_fatal(self, tmp_path):
        # Valid JSON, but Mark.from_dict does `data["t"]` unconditionally —
        # a line missing it must be dropped, not crash the whole load.
        run = tmp_path
        emit_mark(str(run), "startup", when=0.0)
        with open(run / "marks.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"name": "no-timestamp", "kind": "event"}) + "\n")
        emit_mark(str(run), "job-a", when=10.0)
        marks = load_marks(str(run))
        assert [m.name for m in marks] == ["startup", "job-a"]

    def test_mark_with_a_non_numeric_t_is_skipped_not_fatal(self, tmp_path):
        # Valid JSON, "t" present but not coercible to float: float(data["t"])
        # raises ValueError, which must be caught the same way as a KeyError.
        run = tmp_path
        emit_mark(str(run), "startup", when=0.0)
        with open(run / "marks.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": "not-a-number", "name": "garbled"}) + "\n")
        emit_mark(str(run), "job-a", when=10.0)
        marks = load_marks(str(run))
        assert [m.name for m in marks] == ["startup", "job-a"]

    def test_invalid_json_in_the_middle_of_the_file_does_not_stop_the_rest(self, tmp_path):
        # A torn write is not guaranteed to land last — a second writer can
        # append its own complete line after the fragment (this is exactly
        # what the concurrent-appends test below stresses at scale; this is
        # the minimal single-bad-line-in-the-middle case).
        run = tmp_path
        emit_mark(str(run), "startup", when=0.0)
        with open(run / "marks.jsonl", "a", encoding="utf-8") as fh:
            fh.write("{not even close to json\n")
        emit_mark(str(run), "job-a", when=10.0)
        marks = load_marks(str(run))
        assert [m.name for m in marks] == ["startup", "job-a"]

    def test_concurrent_appends_from_many_writers_stay_intact(self, tmp_path):
        run = str(tmp_path)
        n_writers = 8
        marks_per_writer = 25

        def writer(i: int) -> None:
            for j in range(marks_per_writer):
                emit_mark(run, f"t{i}-{j}", kind="event", when=float(i * 1000 + j))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_writers)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        marks = load_marks(run)
        # Every appended line must have landed whole: no interleaving from
        # two writers racing the same O_APPEND fd, and none silently lost.
        assert len(marks) == n_writers * marks_per_writer
        assert len({m.name for m in marks}) == n_writers * marks_per_writer


class TestPhasesFromMarks:
    def test_no_marks_is_one_phase_spanning_the_run(self):
        phases = phases_from_marks([], t_start=0.0, t_end=39.0)
        assert len(phases) == 1
        assert phases[0].t0 == 0.0
        assert phases[0].t1 == 39.0

    def test_phase_marks_close_the_previous_and_open_the_next(self):
        marks = [
            Mark(t=0.0, name="startup", kind="phase", mono=0.0),
            Mark(t=0.0, name="job-a", kind="phase", mono=10.0),
            Mark(t=0.0, name="idle", kind="phase", mono=25.0),
        ]
        phases = phases_from_marks(marks, t_start=0.0, t_end=39.0)
        assert [(p.name, p.t0, p.t1) for p in phases] == [
            ("startup", 0.0, 10.0),
            ("job-a", 10.0, 25.0),
            ("idle", 25.0, 39.0),
        ]

    def test_event_marks_never_create_boundaries(self):
        marks = [
            Mark(t=0.0, name="startup", kind="phase", mono=0.0),
            Mark(t=0.0, name="checkpoint", kind="event", mono=5.0),
            Mark(t=0.0, name="job-a", kind="phase", mono=10.0),
        ]
        phases = phases_from_marks(marks, t_start=0.0, t_end=20.0)
        assert [p.name for p in phases] == ["startup", "job-a"]
        assert phases[0].t1 == 10.0  # not cut short by the event mark at mono=5

    def test_out_of_order_marks_are_sorted_before_folding(self):
        marks = [
            Mark(t=0.0, name="idle", kind="phase", mono=25.0),
            Mark(t=0.0, name="startup", kind="phase", mono=0.0),
            Mark(t=0.0, name="job-a", kind="phase", mono=10.0),
        ]
        phases = phases_from_marks(marks, t_start=0.0, t_end=39.0)
        assert [(p.name, p.t0, p.t1) for p in phases] == [
            ("startup", 0.0, 10.0),
            ("job-a", 10.0, 25.0),
            ("idle", 25.0, 39.0),
        ]

    def test_first_phase_always_starts_at_t_start_even_if_later_than_the_mark(self):
        marks = [Mark(t=0.0, name="job-a", kind="phase", mono=3.0)]
        phases = phases_from_marks(marks, t_start=0.0, t_end=10.0)
        assert phases[0].t0 == 0.0
        assert phases[0].t1 == 10.0

    def test_mark_without_mono_falls_back_to_wall_clock_t(self):
        marks = [
            Mark(t=0.0, name="startup", kind="phase", mono=0.0),
            Mark(t=42.0, name="job-a", kind="phase", mono=None),  # mono never resolved
        ]
        phases = phases_from_marks(marks, t_start=0.0, t_end=100.0)
        assert phases[0].t1 == 42.0
        assert phases[1].t0 == 42.0

    def test_source_and_meta_carry_through_from_the_opening_mark(self):
        marks = [Mark(t=0.0, name="job-a", kind="phase", mono=0.0, source="wrapper", meta={"cmd": "make"})]
        phases = phases_from_marks(marks, t_start=0.0, t_end=5.0)
        assert phases[0].source == "wrapper"
        assert phases[0].meta == {"cmd": "make"}

    def test_only_event_marks_falls_back_to_one_phase_like_no_marks_at_all(self):
        # `marks` is non-empty, but none of them are `kind="phase"` — the
        # boundaries list ends up empty just like the zero-marks case, and
        # must take the same fallback, not raise on an empty sequence.
        marks = [
            Mark(t=0.0, name="checkpoint-1", kind="event", mono=3.0),
            Mark(t=0.0, name="checkpoint-2", kind="event", mono=7.0),
        ]
        phases = phases_from_marks(marks, t_start=0.0, t_end=10.0)
        assert len(phases) == 1
        assert phases[0].t0 == 0.0
        assert phases[0].t1 == 10.0

    def test_two_phase_marks_at_the_identical_timestamp_yield_a_zero_length_phase(self):
        # Two writers (or one writer called twice with the same `when`) can
        # legitimately produce this. The tie sits between the *second* and
        # third marks, not the first, since the first phase's start is
        # always forced to t_start regardless of its own mark's position
        # (test_first_phase_always_starts_at_t_start_... covers that case
        # separately) — this is the case where a tie actually collapses a
        # phase's own span to zero. Must fold into a well-formed,
        # zero-duration phase rather than raise or silently drop a name —
        # and since Python's sort is stable, ties keep the order given.
        marks = [
            Mark(t=0.0, name="startup", kind="phase", mono=0.0),
            Mark(t=0.0, name="first", kind="phase", mono=10.0),
            Mark(t=0.0, name="second", kind="phase", mono=10.0),
        ]
        phases = phases_from_marks(marks, t_start=0.0, t_end=30.0)
        assert [(p.name, p.t0, p.t1) for p in phases] == [
            ("startup", 0.0, 10.0),
            ("first", 10.0, 10.0),
            ("second", 10.0, 30.0),
        ]
        assert phases[1].duration == 0.0

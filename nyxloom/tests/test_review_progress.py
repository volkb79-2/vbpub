"""B29 / nyxloom-P105: the review-leg progress watchdog (LESSONS PL11).

Four layers, each with its own negative:

  * the PURE detector (review_progress.evaluate_leg) -- one test per signal,
    each flipping exactly ONE field so no assertion can pass for the wrong
    reason;
  * the event scan (event_progress) -- and specifically that a gate or a
    verdict from BEFORE this leg started is not read as its progress;
  * the LADDER (rules_attempts) -- including the regression guard that a
    measured-but-clean review leg still reaches the quiet-log stall gate;
  * the EFFECT (effects_lifecycle) -- the two events, the wave dedup, and
    the transcript-read degradation.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nyxloom import effects, effects_lifecycle, paths, reconcile, review_progress, storage
from nyxloom.handoff_trace import build_trace
from nyxloom.reconcile import (
    MarkReviewStalled, MarkStalled, InterruptAttempt, ReconcileInput, StallCheck,
    plan_project,
)
from nyxloom.review_progress import (
    REVIEW_NO_CONCRETE_PROGRESS, TRIGGER_BOTH, TRIGGER_ORCHESTRATION_RECORDS,
    TRIGGER_WALL_CLOCK, ReviewLegSignals, ReviewProgressBudget, evaluate_leg,
    event_progress, inspected_paths,
)
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Event, EventType, Role, Route,
    TaskState, TaskStateFile, utc_now,
)

from test_reconcile import make_attempt, make_config, make_frontmatter, make_routes, make_tsf, utc

PROJECT = "demo"
BASE_TS = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _signals(**overrides) -> ReviewLegSignals:
    """A leg that IS stalled by default: growing transcript, nothing
    concrete, well past a 1800s budget. Every test below flips one field, so
    a test that fails to flip anything would fail loudly rather than pass."""
    base = dict(
        elapsed_seconds=3600.0,
        transcript_bytes=400_000,
        transcript_records=2500,
        transcript_growing=True,
        branch_advanced=None,
        gate_active=False,
        finding_recorded=False,
    )
    base.update(overrides)
    return ReviewLegSignals(**base)


WALL_ONLY = ReviewProgressBudget(wall_seconds=1800, max_records=0)
RECORDS_ONLY = ReviewProgressBudget(wall_seconds=0, max_records=1000)
BOTH = ReviewProgressBudget(wall_seconds=1800, max_records=1000)


# ---------------------------------------------------------------------------
# the pure detector


def test_a_loud_leg_with_nothing_concrete_is_stalled_on_wall_clock():
    """The PL11 shape itself: transcript pouring out, no correction, no gate,
    no verdict, past the budget."""
    assert evaluate_leg(_signals(), WALL_ONLY) == TRIGGER_WALL_CLOCK


def test_a_quiet_leg_is_left_to_the_stall_ladder():
    """Signal (a) absent -> not this detector's case, however far over budget.
    The P14 tier-2 CPU-signature path owns a quiet leg and is the more
    careful check; claiming it here would pre-empt it."""
    assert evaluate_leg(_signals(transcript_growing=False,
                                 elapsed_seconds=99_999.0), BOTH) is None


def test_a_gate_run_immunizes_the_leg():
    assert evaluate_leg(_signals(gate_active=True), WALL_ONLY) is None


def test_a_recorded_finding_immunizes_the_leg():
    assert evaluate_leg(_signals(finding_recorded=True), WALL_ONLY) is None


def test_a_branch_that_advanced_immunizes_the_leg():
    assert evaluate_leg(_signals(branch_advanced=True), WALL_ONLY) is None


def test_an_unmeasurABLE_branch_is_not_read_as_progress():
    """The tri-state's whole point. None means 'no baseline exists for this
    leg' -- a permanent, structural condition -- and treating it as progress
    would disable the detector forever for exactly the legs PL11 is about.
    Paired with the False case so this is a statement about None, not about
    falsiness in general."""
    assert evaluate_leg(_signals(branch_advanced=None), WALL_ONLY) == TRIGGER_WALL_CLOCK
    assert evaluate_leg(_signals(branch_advanced=False), WALL_ONLY) == TRIGGER_WALL_CLOCK


def test_a_leg_inside_its_budget_is_not_stalled():
    assert evaluate_leg(_signals(elapsed_seconds=1799.0), WALL_ONLY) is None


def test_the_record_budget_fires_with_no_wall_clock_budget_at_all():
    """The two budgets are independent, not a single threshold wearing two
    names: with wall_seconds=0 an hour-old leg is judged purely on how many
    orchestration records it burned."""
    assert evaluate_leg(_signals(elapsed_seconds=10.0, transcript_records=1001),
                        RECORDS_ONLY) == TRIGGER_ORCHESTRATION_RECORDS
    assert evaluate_leg(_signals(elapsed_seconds=10.0, transcript_records=1000),
                        RECORDS_ONLY) is None


def test_both_budgets_blown_reports_both():
    assert evaluate_leg(_signals(), BOTH) == TRIGGER_BOTH


def test_a_disarmed_budget_never_stalls_anything():
    """0/0 is the off switch, and it is checked before anything else -- a
    project that has not opted in is not measured against a hidden default."""
    assert evaluate_leg(_signals(elapsed_seconds=999_999.0,
                                 transcript_records=999_999),
                        ReviewProgressBudget()) is None


# ---------------------------------------------------------------------------
# the event scan


def _ev(seq: int, type_: EventType, payload: dict | None = None, *,
        task_id: str | None = "demo-T1", attempt_id: str | None = None) -> Event:
    return Event(schema_version=1, sequence=seq,
                 timestamp=BASE_TS + timedelta(seconds=seq), project=PROJECT,
                 actor=Actor(ActorKind.TICK, "nyxloomd"), type=type_,
                 payload=payload or {}, task_id=task_id, attempt_id=attempt_id)


def test_a_gate_from_before_this_leg_started_is_not_its_progress():
    """The trap this test exists for: a task carries gate runs from earlier
    rounds, and a scan that ignored the leg's own start sequence would read
    every review leg as instantly productive -- silently disabling the
    watchdog while looking green. The SAME event after the start DOES count,
    so this is a statement about the boundary and not about the event type."""
    before = [_ev(1, EventType.GATE_FINISHED),
              _ev(2, EventType.ATTEMPT_CREATED, attempt_id="att-r")]
    assert event_progress(before, "att-r", "demo-T1", since_sequence=2) == (False, False)

    after = before + [_ev(3, EventType.GATE_FINISHED)]
    assert event_progress(after, "att-r", "demo-T1", since_sequence=2) == (True, False)


def test_a_verdict_bound_to_this_leg_counts_as_a_finding():
    evs = [_ev(2, EventType.ATTEMPT_CREATED, attempt_id="att-r"),
           _ev(3, EventType.REVIEW_RECORDED, {"result": "rejected"},
               attempt_id="att-r")]
    assert event_progress(evs, "att-r", "demo-T1", since_sequence=2) == (False, True)


def test_another_tasks_finding_is_not_this_legs_progress():
    evs = [_ev(2, EventType.ATTEMPT_CREATED, attempt_id="att-r"),
           _ev(3, EventType.FINDING_RECORDED, task_id="demo-T9",
               attempt_id="att-other")]
    assert event_progress(evs, "att-r", "demo-T1", since_sequence=2) == (False, False)


def test_a_leg_with_both_a_gate_and_a_finding_reports_both():
    """Exercises the early exit once both signals are in hand -- a scan that
    stopped at the first would still report (True, True) here only if it kept
    looking, so this is not a coverage fig leaf."""
    evs = [_ev(2, EventType.ATTEMPT_CREATED, attempt_id="att-r"),
           _ev(3, EventType.GATE_STARTED),
           _ev(4, EventType.SCOPE_AMENDMENT_REQUESTED, {"file": "src/x.py"}),
           _ev(5, EventType.GATE_FINISHED)]
    assert event_progress(evs, "att-r", "demo-T1", since_sequence=2) == (True, True)


def test_a_leg_with_no_event_of_its_own_yet_has_produced_nothing():
    evs = [_ev(1, EventType.GATE_FINISHED),
           _ev(2, EventType.REVIEW_RECORDED, {"result": "approved"})]
    assert event_progress(evs, "att-r", "demo-T1", since_sequence=None) == (False, False)


def test_attempt_start_sequences_takes_the_first_event_per_attempt():
    evs = [_ev(5, EventType.ATTEMPT_CREATED, attempt_id="att-r"),
           _ev(6, EventType.ATTEMPT_STARTED, attempt_id="att-r"),
           _ev(7, EventType.ATTEMPT_CREATED, attempt_id="att-s")]
    assert review_progress.attempt_start_sequences(evs) == {"att-r": 5, "att-s": 7}


# ---------------------------------------------------------------------------
# transcript path extraction


def _tool_use_line(name: str, tool_input: dict) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name,
                                 "input": tool_input}]},
    })


def test_tool_use_paths_are_extracted_deduped_and_sorted():
    lines = [
        _tool_use_line("Read", {"file_path": "src/b.py"}),
        _tool_use_line("Read", {"file_path": "src/a.py"}),
        _tool_use_line("Read", {"file_path": "src/b.py"}),
        _tool_use_line("Grep", {"path": "tests", "pattern": "def foo"}),
    ]
    found, truncated = inspected_paths(lines)
    assert found == ("src/a.py", "src/b.py", "tests")
    assert truncated is False
    # NEGATIVE: the tool's free-text argument is NOT harvested as a path.
    assert "def foo" not in found


def test_a_partially_written_final_line_is_skipped_not_fatal():
    """The normal state of a log belonging to a process that is still
    running -- which is every log this ever reads."""
    lines = [_tool_use_line("Read", {"file_path": "src/a.py"}),
             '{"type": "assistant", "message": {"content": [{"type": "tool_']
    assert inspected_paths(lines)[0] == ("src/a.py",)


def test_a_control_character_path_is_rejected_and_a_long_one_truncated():
    lines = [_tool_use_line("Read", {"file_path": "src/ev\nil.py"}),
             _tool_use_line("Read", {"file_path": "x" * 400})]
    found, _ = inspected_paths(lines)
    assert found == ("x" * review_progress.INSPECTED_PATH_MAX_LEN,)


def test_over_limit_paths_are_capped_and_flagged():
    lines = [_tool_use_line("Read", {"file_path": f"src/f{i:03d}.py"})
             for i in range(review_progress.INSPECTED_PATH_LIMIT + 5)]
    found, truncated = inspected_paths(lines)
    assert len(found) == review_progress.INSPECTED_PATH_LIMIT
    assert truncated is True


def test_an_empty_path_argument_is_dropped_rather_than_recorded():
    """An empty string is a path nobody inspected; recording "" would put a
    meaningless row in the summary a restart is supposed to trust."""
    lines = [_tool_use_line("Read", {"file_path": "   "}),
             _tool_use_line("Read", {"file_path": "src/real.py"})]
    assert inspected_paths(lines)[0] == ("src/real.py",)


def test_the_walk_is_bounded_in_depth_and_in_volume():
    """Both halves of the walk's guard, against a transcript that is agent-
    authored and therefore potentially hostile.

    DEPTH: a tool_use buried below the cap is simply not reached -- real
    stream-json nests it about four levels, so the cap costs nothing and
    bounds recursion on a pathological record. The shallow twin proves the
    walk WOULD have found it, so this is a statement about depth and not
    about the shape being unrecognised.

    VOLUME: far more tool calls than the raw-hit ceiling still terminate and
    still produce a capped, flagged result.
    """
    deep: dict = {"type": "tool_use", "input": {"file_path": "src/buried.py"}}
    for _ in range(12):
        deep = {"wrapper": deep}
    assert inspected_paths([json.dumps(deep)])[0] == ()
    assert inspected_paths([json.dumps({"wrapper": {"wrapper": deep["wrapper"]["wrapper"]}})
                            ])[0] == ()
    shallow = {"type": "tool_use", "input": {"file_path": "src/buried.py"}}
    assert inspected_paths([json.dumps(shallow)])[0] == ("src/buried.py",)

    flood = [_tool_use_line("Read", {"file_path": f"src/f{i:04d}.py"})
             for i in range(review_progress.INSPECTED_PATH_LIMIT * 4 + 20)]
    found, truncated = inspected_paths(flood)
    assert len(found) == review_progress.INSPECTED_PATH_LIMIT
    assert truncated is True


def test_a_non_json_log_yields_nothing_rather_than_raising():
    assert inspected_paths(["plain text", "", "not json at all"]) == ((), False)


# ---------------------------------------------------------------------------
# the ladder


def _review_cfg(wall: int = 1800, records: int = 0):
    cfg = make_config()
    cfg.policy.review_progress_wall_seconds = wall
    cfg.policy.review_progress_max_records = records
    return cfg


def _ladder_inp(*, tsf, attempt, signals=None, log_quiet=0.0, cfg=None,
                now=None) -> ReconcileInput:
    return ReconcileInput(
        now=now or utc(2026, 7, 15, 2),
        cfg=cfg or _review_cfg(),
        routes=make_routes(),
        states={tsf.task_id: tsf},
        frontmatters={tsf.task_id: (make_frontmatter(id=tsf.task_id), "handoff/x.md")},
        lint_clean={tsf.task_id: True},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={attempt.attempt_id: log_quiet},
        pid_alive={attempt.attempt_id: True},
        receipts={attempt.attempt_id: None},
        review_leg_signals=(
            {} if signals is None else {attempt.attempt_id: signals}),
    )


def _review_leg(role: Role = Role.REVIEW_INDEPENDENT) -> Attempt:
    return make_attempt(attempt_id="att-rev", state=AttemptState.RUNNING, role=role)


def test_a_stalled_review_leg_plans_mark_review_stalled_carrying_its_evidence():
    att = _review_leg()
    tsf = make_tsf(task_id="demo-P01", state=TaskState.AWAITING_REVIEW, attempts=[att])
    actions = plan_project(_ladder_inp(tsf=tsf, attempt=att, signals=_signals()))
    marks = [a for a in actions if isinstance(a, MarkReviewStalled)]
    assert len(marks) == 1
    assert marks[0].attempt_id == "att-rev"
    assert marks[0].trigger == TRIGGER_WALL_CLOCK
    # The measurement travels WITH the action, so the durable summary cannot
    # disagree with the evidence the plan was made on.
    assert marks[0].signals.transcript_records == 2500


def test_a_measured_but_clean_review_leg_still_reaches_the_quiet_log_gate():
    """REGRESSION GUARD. The first cut put the budget check inside the
    branch's own condition, so a review leg the daemon MEASURED consumed the
    ladder's `elif` whether or not it stalled -- silently disabling the P14
    quiet-log stall path for every review leg on the project. Here the leg is
    measured, clean (a gate ran), and quiet: the quiet-log gate must still
    fire."""
    att = _review_leg()
    tsf = make_tsf(task_id="demo-P01", state=TaskState.AWAITING_REVIEW, attempts=[att])
    inp = _ladder_inp(tsf=tsf, attempt=att,
                      signals=_signals(gate_active=True, transcript_growing=False),
                      log_quiet=9999.0)
    actions = plan_project(inp)
    assert [a for a in actions if isinstance(a, MarkReviewStalled)] == []
    assert len([a for a in actions if isinstance(a, StallCheck)]) == 1


def test_an_implementer_leg_is_never_review_stalled():
    """Role negative: the SAME signals that stall a reviewer leave an
    implementer alone -- a long silent stretch before the first commit is an
    implementer's normal early state, not a defect."""
    att = make_attempt(attempt_id="att-impl", state=AttemptState.RUNNING,
                       role=Role.IMPLEMENTER)
    tsf = make_tsf(task_id="demo-P01", state=TaskState.ACTIVE, attempts=[att])
    actions = plan_project(_ladder_inp(tsf=tsf, attempt=att, signals=_signals()))
    assert [a for a in actions if isinstance(a, MarkReviewStalled)] == []


def test_a_self_review_leg_is_supervised_too():
    att = _review_leg(role=Role.SELF_REVIEW)
    tsf = make_tsf(task_id="demo-P01", state=TaskState.SELF_REVIEWING, attempts=[att])
    actions = plan_project(_ladder_inp(tsf=tsf, attempt=att, signals=_signals()))
    assert len([a for a in actions if isinstance(a, MarkReviewStalled)]) == 1


def test_the_wall_clock_cap_keeps_precedence_over_the_review_stall():
    """The absolute backstop is branch 3 and stays branch 3: a leg past
    attempt_max_wall_seconds is interrupted outright, not re-routed through
    a softer mark."""
    att = _review_leg()
    tsf = make_tsf(task_id="demo-P01", state=TaskState.AWAITING_REVIEW, attempts=[att])
    inp = _ladder_inp(tsf=tsf, attempt=att, signals=_signals(),
                      now=utc(2026, 7, 20))  # days past the 3h cap
    actions = plan_project(inp)
    assert [a for a in actions if isinstance(a, MarkReviewStalled)] == []
    assert len([a for a in actions if isinstance(a, InterruptAttempt)]) == 1


def test_an_already_stalled_review_leg_is_interrupted_by_the_existing_branch():
    """The handover this package relies on instead of adding a kill path:
    once MarkReviewStalled has put the attempt in STALLED, branch 4 -- which
    predates this package entirely -- interrupts it."""
    att = _review_leg()
    att.state = AttemptState.STALLED
    tsf = make_tsf(task_id="demo-P01", state=TaskState.AWAITING_REVIEW, attempts=[att])
    actions = plan_project(_ladder_inp(tsf=tsf, attempt=att, signals=_signals()))
    assert len([a for a in actions if isinstance(a, InterruptAttempt)]) == 1
    assert [a for a in actions if isinstance(a, MarkReviewStalled)] == []


def test_an_unmeasured_review_leg_plans_exactly_what_it_did_before():
    """Absent from review_leg_signals means NOT JUDGED, not judged clean --
    the property every pre-existing planner test relies on."""
    att = _review_leg()
    tsf = make_tsf(task_id="demo-P01", state=TaskState.AWAITING_REVIEW, attempts=[att])
    actions = plan_project(_ladder_inp(tsf=tsf, attempt=att, signals=None,
                                       log_quiet=9999.0))
    assert [a for a in actions if isinstance(a, MarkReviewStalled)] == []
    assert len([a for a in actions if isinstance(a, StallCheck)]) == 1


# ---------------------------------------------------------------------------
# the effect


def _effector():
    ports = effects.EffectPorts.system()
    return ports, effects_lifecycle.LifecycleEffector(
        ports, effects.ProviderPauseRegistry(ports.clock))


def _running_review_attempt(log_path: str | None) -> Attempt:
    return Attempt(
        attempt_id="att-rev", role=Role.REVIEW_INDEPENDENT,
        state=AttemptState.RUNNING,
        route=Route(route_id="frontier", cli="fake", model="frontier-model"),
        started=utc_now(), log_path=log_path)


def _seed_task(task_id: str, attempt: Attempt) -> TaskStateFile:
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                        project=PROJECT, state=TaskState.AWAITING_REVIEW,
                        since=utc_now(), attempts=[attempt])
    storage.append_and_apply(PROJECT, {}, actor=Actor(ActorKind.TICK, "t"),
                             type=EventType.TASK_CREATED,
                             payload={"statefile": tsf.to_dict()}, task_id=task_id)
    return tsf


def test_the_effect_records_the_typed_summary_then_marks_the_attempt_stalled(
        tmp_state, sample_project, tmp_path):
    log_file = tmp_path / "attempt.log"
    log_file.write_text(
        _tool_use_line("Read", {"file_path": "src/nyxloom/render.py"}) + "\n"
        + _tool_use_line("Grep", {"path": "src/nyxloom/daemon.py"}) + "\n",
        encoding="utf-8")
    attempt = _running_review_attempt(str(log_file))
    tsf = _seed_task("demo-P01", attempt)
    _ports, effector = _effector()
    ctx = effects.EffectContext(project=PROJECT, cfg=sample_project,
                                states={"demo-P01": tsf},
                                ports=effects.EffectPorts.system())

    events = effector.mark_review_stalled(ctx, MarkReviewStalled(
        task_id="demo-P01", attempt_id="att-rev", trigger=TRIGGER_WALL_CLOCK,
        signals=_signals()))

    assert [e.type for e in events] == [EventType.REVIEW_PROGRESS_STALLED,
                                        EventType.ATTEMPT_STALLED]
    summary = events[0].payload
    assert summary["reason"] == REVIEW_NO_CONCRETE_PROGRESS
    assert summary["trigger"] == TRIGGER_WALL_CLOCK
    assert summary["transcript_records"] == 2500
    assert summary["signals"] == {"transcript_growing": True,
                                  "branch_advanced": None,
                                  "gate_active": False,
                                  "finding_recorded": False}
    # The salvage half: what the reviewer actually read, carried forward so a
    # restart need not replay the transcript to rediscover it.
    assert summary["inspected_paths"] == ["src/nyxloom/daemon.py",
                                          "src/nyxloom/render.py"]
    # And the state half, which is what the ladder's interrupt branch reads.
    assert tsf.attempt_by_id("att-rev").state is AttemptState.STALLED
    assert tsf.attempt_by_id("att-rev").ended is None  # still alive, just flagged


def test_a_wave_review_records_the_audit_summary_once_but_marks_every_member(
        tmp_state, sample_project, tmp_path):
    """One attempt, three member tasks, three planned actions in one pass.
    The STATE mark belongs to each member's statefile; the audit record
    belongs to the LEG, and three copies of it would be noise in the very
    trace this package exists to make readable."""
    attempt = _running_review_attempt(None)
    members = {t: _seed_task(t, attempt) for t in ("demo-A", "demo-B", "demo-C")}
    _ports, effector = _effector()
    ctx = effects.EffectContext(project=PROJECT, cfg=sample_project,
                                states=members,
                                ports=effects.EffectPorts.system())

    emitted = []
    for task_id in ("demo-A", "demo-B", "demo-C"):
        emitted += effector.mark_review_stalled(ctx, MarkReviewStalled(
            task_id=task_id, attempt_id="att-rev", trigger=TRIGGER_WALL_CLOCK,
            signals=_signals()))

    kinds = [e.type for e in emitted]
    assert kinds.count(EventType.REVIEW_PROGRESS_STALLED) == 1
    assert kinds.count(EventType.ATTEMPT_STALLED) == 3


def test_an_unreadable_transcript_still_stalls_the_leg_with_no_paths(
        tmp_state, sample_project, tmp_path):
    """The detection half was established from signals already measured;
    losing the salvage half must not cost it."""
    attempt = _running_review_attempt(str(tmp_path / "does-not-exist.log"))
    tsf = _seed_task("demo-P01", attempt)
    _ports, effector = _effector()
    ctx = effects.EffectContext(project=PROJECT, cfg=sample_project,
                                states={"demo-P01": tsf},
                                ports=effects.EffectPorts.system())
    events = effector.mark_review_stalled(ctx, MarkReviewStalled(
        task_id="demo-P01", attempt_id="att-rev", trigger=TRIGGER_BOTH,
        signals=_signals()))
    assert [e.type for e in events] == [EventType.REVIEW_PROGRESS_STALLED,
                                        EventType.ATTEMPT_STALLED]
    assert events[0].payload["inspected_paths"] == []


# ---------------------------------------------------------------------------
# the daemon's measurement half (where the four signals actually come from)


class _StubGit:
    """A GitPort stand-in whose resolve_verify is scripted per ref."""

    def __init__(self, heads: dict[str, str] | None = None, raises: bool = False):
        self.heads = heads or {}
        self.raises = raises

    def resolve_verify(self, root: str, rev: str) -> str | None:
        if self.raises:
            raise OSError("git unavailable")
        return self.heads.get(rev)


def _daemon_with_git(sample_project, git):
    from nyxloom import daemon as daemon_mod
    d = daemon_mod.Daemon({PROJECT: sample_project.root})
    d._ports = dataclasses.replace(d._ports, git=git)
    return d


def _review_cfg_obj(sample_project, wall=1800, records=0):
    cfg = dataclasses.replace(
        sample_project,
        policy=dataclasses.replace(sample_project.policy,
                                   review_progress_wall_seconds=wall,
                                   review_progress_max_records=records))
    return cfg


def _running_review_tsf(task_id: str, attempt: Attempt) -> TaskStateFile:
    return TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                         project=PROJECT, state=TaskState.AWAITING_REVIEW,
                         since=utc_now(), attempts=[attempt])


def test_the_daemon_measures_nothing_when_both_budgets_are_off(
        tmp_state, sample_project):
    """The off switch is checked BEFORE any measurement, so a project that
    has not opted in pays no transcript read and no git call at all."""
    attempt = _running_review_attempt(None)
    tsf = _running_review_tsf("demo-P01", attempt)
    d = _daemon_with_git(sample_project, _StubGit(raises=True))
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project, wall=0, records=0), {"demo-P01": tsf},
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": None}, events=[])
    assert out == {}


def test_the_daemon_measures_a_running_review_leg(
        tmp_state, sample_project, tmp_path):
    log_file = tmp_path / "attempt.log"
    log_file.write_text("a\nb\nc\n", encoding="utf-8")
    attempt = _running_review_attempt(str(log_file))
    tsf = _running_review_tsf("demo-P01", attempt)
    d = _daemon_with_git(sample_project, _StubGit())
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project), {"demo-P01": tsf},
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": None},
        events=[_ev(1, EventType.ATTEMPT_CREATED, attempt_id="att-rev",
                    task_id="demo-P01")])
    sig = out["att-rev"]
    assert sig.transcript_bytes == 6
    assert sig.transcript_records == 3
    assert sig.transcript_growing is True   # 1.0s quiet, far under the 300s gate
    assert sig.branch_advanced is None      # no pre_review_sha, no base_commit
    assert (sig.gate_active, sig.finding_recorded) == (False, False)


def test_a_long_quiet_log_is_reported_as_not_growing(
        tmp_state, sample_project, tmp_path):
    """The discriminator, measured at its real source: past
    stall_log_quiet_seconds the leg belongs to the P14 ladder, and the
    daemon says so rather than the detector guessing."""
    attempt = _running_review_attempt(None)
    tsf = _running_review_tsf("demo-P01", attempt)
    d = _daemon_with_git(sample_project, _StubGit())
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project), {"demo-P01": tsf},
        {"att-rev": 9999.0}, {"att-rev": True}, {"att-rev": None}, events=[])
    assert out["att-rev"].transcript_growing is False


def test_a_leg_that_already_reported_is_not_measured(tmp_state, sample_project):
    """A receipt means an earlier ladder branch owns this attempt."""
    attempt = _running_review_attempt(None)
    tsf = _running_review_tsf("demo-P01", attempt)
    d = _daemon_with_git(sample_project, _StubGit())
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project), {"demo-P01": tsf},
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": {"result": "done"}},
        events=[])
    assert out == {}


def test_a_wave_legs_progress_is_ored_across_its_member_tasks(
        tmp_state, sample_project):
    """One attempt, three members: a gate run on ONE member makes the whole
    leg productive. Judging members in isolation would stall a leg that is
    demonstrably getting somewhere on the strength of its quietest task."""
    attempt = _running_review_attempt(None)
    states = {t: _running_review_tsf(t, attempt) for t in ("demo-A", "demo-B")}
    events = [
        _ev(1, EventType.ATTEMPT_CREATED, attempt_id="att-rev", task_id="demo-A"),
        _ev(2, EventType.ATTEMPT_CREATED, attempt_id="att-rev", task_id="demo-B"),
        _ev(3, EventType.GATE_FINISHED, task_id="demo-B"),
    ]
    d = _daemon_with_git(sample_project, _StubGit())
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project), states,
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": None}, events=events)
    assert len(out) == 1                    # ONE leg, not one per member
    assert out["att-rev"].gate_active is True


def test_a_branch_past_its_pre_review_baseline_reads_as_a_correction(
        tmp_state, sample_project):
    attempt = _running_review_attempt(None)
    tsf = _running_review_tsf("demo-P01", attempt)
    events = [_ev(1, EventType.ATTEMPT_CREATED, {"pre_review_sha": "aaa111"},
                  attempt_id="att-rev", task_id="demo-P01")]
    d = _daemon_with_git(sample_project, _StubGit({"feat/demo-P01": "bbb222"}))
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project), {"demo-P01": tsf},
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": None}, events=events)
    assert out["att-rev"].branch_advanced is True

    # NEGATIVE: the same baseline still at HEAD is measured-and-not-advanced
    # (False), which is a different answer from unmeasurable (None) above.
    d2 = _daemon_with_git(sample_project, _StubGit({"feat/demo-P01": "aaa111"}))
    out2 = d2._review_leg_signals(
        _review_cfg_obj(sample_project), {"demo-P01": tsf},
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": None}, events=events)
    assert out2["att-rev"].branch_advanced is False


def test_a_git_read_fault_makes_the_leg_immune_rather_than_stalled(
        tmp_state, sample_project):
    """Fail-safe direction: never interrupt on a signal that failed to read.
    Distinct from the structurally-unmeasurable None above, which does NOT
    confer immunity."""
    attempt = _running_review_attempt(None)
    tsf = _running_review_tsf("demo-P01", attempt)
    events = [_ev(1, EventType.ATTEMPT_CREATED, {"pre_review_sha": "aaa111"},
                  attempt_id="att-rev", task_id="demo-P01")]
    d = _daemon_with_git(sample_project, _StubGit(raises=True))
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project), {"demo-P01": tsf},
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": None}, events=events)
    assert out["att-rev"].branch_advanced is True
    assert evaluate_leg(out["att-rev"], WALL_ONLY) is None


def test_an_unresolvable_task_branch_also_makes_the_leg_immune(
        tmp_state, sample_project):
    """git answering "no such ref" is a READ that did not land, not evidence
    the reviewer did nothing -- same fail-safe direction as a raised fault,
    reached by a different path (a clean None instead of an exception)."""
    attempt = _running_review_attempt(None)
    tsf = _running_review_tsf("demo-P01", attempt)
    events = [_ev(1, EventType.ATTEMPT_CREATED, {"pre_review_sha": "aaa111"},
                  attempt_id="att-rev", task_id="demo-P01")]
    d = _daemon_with_git(sample_project, _StubGit({}))  # resolve_verify -> None
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project), {"demo-P01": tsf},
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": None}, events=events)
    assert out["att-rev"].branch_advanced is True


def test_a_blank_pre_review_sha_is_no_baseline_at_all(tmp_state, sample_project):
    """A payload key that is present but empty must read as ABSENT, not as a
    baseline of "" that every real sha would then "advance" past."""
    attempt = _running_review_attempt(None)
    tsf = _running_review_tsf("demo-P01", attempt)
    events = [_ev(1, EventType.ATTEMPT_CREATED, {"pre_review_sha": ""},
                  attempt_id="att-rev", task_id="demo-P01")]
    d = _daemon_with_git(sample_project, _StubGit({"feat/demo-P01": "bbb222"}))
    out = d._review_leg_signals(
        _review_cfg_obj(sample_project), {"demo-P01": tsf},
        {"att-rev": 1.0}, {"att-rev": True}, {"att-rev": None}, events=events)
    assert out["att-rev"].branch_advanced is None


def test_an_absent_transcript_counts_as_zero_rather_than_raising(
        tmp_state, sample_project, tmp_path):
    from nyxloom import daemon as daemon_mod
    missing = _running_review_attempt(str(tmp_path / "nope.log"))
    assert daemon_mod.Daemon._transcript_extent(missing) == (0, 0)
    assert daemon_mod.Daemon._transcript_extent(_running_review_attempt(None)) == (0, 0)


# ---------------------------------------------------------------------------
# the dashboard trace (B26's Processing Trace, which B29 surfaces on)


def test_the_stall_appears_on_the_processing_trace_with_its_typed_reason():
    """PL11's visibility requirement: an attempt leg alone cannot distinguish
    a review that reached a verdict from one that burned its budget saying
    nothing. NEGATIVE: a trace without the event carries no such leg, so the
    row is caused by the event and not by the attempt."""
    route = Route(route_id="frontier", cli="fake", model="frontier-model")
    created = Attempt(attempt_id="att-rev", role=Role.REVIEW_INDEPENDENT,
                      state=AttemptState.CREATED, route=route, started=BASE_TS)
    base = [_ev(1, EventType.ATTEMPT_CREATED, {"attempt": created.to_dict()},
                attempt_id="att-rev")]
    assert [leg.kind for leg in build_trace("demo-T1", base).legs] == ["attempt"]

    summary = review_progress.compact_summary(
        attempt_id="att-rev", task_id="demo-T1", role="review-independent",
        route_id="frontier", model="frontier-model", trigger=TRIGGER_WALL_CLOCK,
        signals=_signals(), budget=WALL_ONLY, paths=("src/a.py",),
        paths_truncated=False)
    legs = build_trace("demo-T1", base + [
        _ev(2, EventType.REVIEW_PROGRESS_STALLED, summary, attempt_id="att-rev"),
    ]).legs
    stall = [leg for leg in legs if leg.kind == "review-stall"]
    assert len(stall) == 1
    assert stall[0].outcome == REVIEW_NO_CONCRETE_PROGRESS
    assert stall[0].attempt_id == "att-rev"
    assert stall[0].detail["trigger"] == TRIGGER_WALL_CLOCK
    assert stall[0].detail["inspected_files"] == 1
    # Fixed fields only: no transcript prose reaches the rendered row.
    assert stall[0].summary is None

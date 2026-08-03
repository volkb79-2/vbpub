"""CR-05b: the launch primitives the agent-dispatching families share.

These are the decisions that gate or shape a launch without belonging to any
one family. Each is tested in BOTH directions -- an admission gate that has
only ever admitted is not known to refuse.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nyxloom import effects, effects_dispatch, paths, storage
from nyxloom.config import MutexDef
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Basis, Event, EventType, Role,
    Route, TaskState, TaskStateFile, Usage, utc_now,
)


class _Files:
    """A filesystem port whose read can be made to fail."""

    def __init__(self, present: bool = True, content: str = "",
                 raises: Exception | None = None) -> None:
        self.present, self.content, self.raises = present, content, raises

    def exists(self, path: Path) -> bool:
        return self.present

    def read_text(self, path: Path) -> str:
        if self.raises is not None:
            raise self.raises
        return self.content


def _ctx(cfg, states=None, *, files=None, audit=None):
    ports = dataclasses.replace(effects.EffectPorts.system(),
                                files=files or _Files(present=False))
    return effects.EffectContext(project="demo", cfg=cfg,
                                 states=states or {}, ports=ports,
                                 snapshot_audit=audit)


def _attempt_costing(amount: float, currency: str = "USD") -> Attempt:
    return Attempt(
        attempt_id="a1", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
        route=Route(route_id="r1", cli="fake", model="m"), started=utc_now(),
        usage=Usage(basis=Basis.ACTUAL, cost=amount, currency=currency))


def _task_with(attempts: list[Attempt]) -> TaskStateFile:
    return TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t1",
                         project="demo", state=TaskState.ACTIVE,
                         since=utc_now(), attempts=attempts)


# ---------------------------------------------------------------------------
# pause mode


class TestPauseMode:

    def test_no_flag_file_means_running(self):
        assert effects_dispatch.pause_mode(_Files(present=False), "demo") == "run"

    def test_the_explicit_mode_is_read_from_the_content(self):
        files = _Files(content="drain-agents")
        assert effects_dispatch.pause_mode(files, "demo") == "drain-agents"

    def test_a_legacy_empty_flag_file_is_the_weaker_mode(self):
        """A bare boolean pause flag has always meant "block new dispatch
        only". Reading it as the stricter mode would silently stop in-flight
        work an operator expected to finish."""
        assert effects_dispatch.pause_mode(_Files(content=""), "demo") == "drain-handoffs"

    def test_an_unreadable_flag_file_is_the_weaker_mode_not_a_crash(self):
        """The flag EXISTS, so the project is paused; only the mode is
        unknown. Failing the read must not un-pause it, and must not take the
        pass down either."""
        files = _Files(present=True, raises=OSError("permission denied"))
        assert effects_dispatch.pause_mode(files, "demo") == "drain-handoffs"


# ---------------------------------------------------------------------------
# budget


class TestBudgetRemaining:

    def test_no_cap_configured_is_unbounded(self, sample_project):
        assert effects_dispatch.budget_remaining(sample_project, {}) is None

    def test_recorded_spend_is_subtracted_from_the_cap(self, sample_project):
        cfg = dataclasses.replace(
            sample_project,
            policy=dataclasses.replace(sample_project.policy, max_cost=10.0,
                                       cost_currency="USD"))
        states = {"t1": _task_with([_attempt_costing(2.5), _attempt_costing(1.5)])}
        assert effects_dispatch.budget_remaining(cfg, states) == 6.0

    def test_spend_in_another_currency_is_not_converted(self, sample_project):
        """A guessed exchange rate is a worse answer than a missing one, and
        this number gates whether an agent launches at all."""
        cfg = dataclasses.replace(
            sample_project,
            policy=dataclasses.replace(sample_project.policy, max_cost=10.0,
                                       cost_currency="USD"))
        states = {"t1": _task_with([_attempt_costing(4.0, "EUR")])}
        assert effects_dispatch.budget_remaining(cfg, states) == 10.0

    def test_an_attempt_with_no_recorded_usage_costs_nothing(self, sample_project):
        cfg = dataclasses.replace(
            sample_project,
            policy=dataclasses.replace(sample_project.policy, max_cost=10.0,
                                       cost_currency="USD"))
        bare = Attempt(attempt_id="a0", role=Role.IMPLEMENTER,
                       state=AttemptState.EXITED,
                       route=Route(route_id="r1", cli="fake", model="m"),
                       started=utc_now())
        assert effects_dispatch.budget_remaining(cfg, {"t1": _task_with([bare])}) == 10.0


# ---------------------------------------------------------------------------
# admission


class TestAdmissible:

    def test_an_unpaused_project_admits_every_kind(self, tmp_state, sample_project):
        ctx = _ctx(sample_project)
        for kind in sorted(effects_dispatch.LAUNCH_KINDS):
            assert effects_dispatch.admissible(ctx, kind)[0] is True, kind

    def test_drain_agents_refuses_every_kind(self, tmp_state, sample_project):
        ctx = _ctx(sample_project, files=_Files(content="drain-agents"))
        for kind in sorted(effects_dispatch.LAUNCH_KINDS):
            ok, reason = effects_dispatch.admissible(ctx, kind)
            assert ok is False and reason == "paused:drain-agents", kind

    def test_drain_handoffs_blocks_new_work_and_lets_legs_finish(
            self, tmp_state, sample_project):
        """The self-review leg CONTINUES a task the implementer already ran,
        so it drains with resume and review rather than being blocked as new
        work."""
        ctx = _ctx(sample_project, files=_Files(content=""))
        assert effects_dispatch.admissible(ctx, "dispatch")[0] is False
        assert effects_dispatch.admissible(ctx, "carve")[0] is False
        assert effects_dispatch.admissible(ctx, "resume")[0] is True
        assert effects_dispatch.admissible(ctx, "review")[0] is True
        assert effects_dispatch.admissible(ctx, "self-review")[0] is True

    def test_an_exhausted_budget_refuses_every_kind(self, tmp_state, sample_project):
        """Including the review and resume legs the planner never gated --
        the most expensive things to launch into a spent budget."""
        cfg = dataclasses.replace(
            sample_project,
            policy=dataclasses.replace(sample_project.policy, max_cost=1.0,
                                       cost_currency="USD"))
        ctx = _ctx(cfg, {"t1": _task_with([_attempt_costing(1.0)])})
        for kind in sorted(effects_dispatch.LAUNCH_KINDS):
            ok, reason = effects_dispatch.admissible(ctx, kind)
            assert ok is False and reason == "budget-exhausted", kind

    def test_a_failed_snapshot_audit_refuses_before_anything_else(
            self, tmp_state, sample_project):
        """Checked FIRST, and it names the fault: an effect authorized from
        an incomplete snapshot is the window this gate exists to close."""
        class _Audit:
            permits_effects = False

            def summary(self):
                return "state:unreadable"

        ok, reason = effects_dispatch.admissible(
            _ctx(sample_project, audit=_Audit()), "review")
        assert ok is False and reason == "snapshot-unavailable:state:unreadable"

    def test_a_clean_audit_admits(self, tmp_state, sample_project):
        class _Audit:
            permits_effects = True

        assert effects_dispatch.admissible(
            _ctx(sample_project, audit=_Audit()), "review")[0] is True

    def test_an_absent_audit_is_permitted_and_is_not_the_same_as_clean(
            self, tmp_state, sample_project):
        """Absence means no fan-in ran in this call stack -- an
        operator-initiated verb, an explicit human instruction rather than an
        autonomous decision."""
        assert effects_dispatch.admissible(
            _ctx(sample_project, audit=None), "carve")[0] is True


# ---------------------------------------------------------------------------
# prompt and lease shaping


class TestGateHint:

    def test_a_project_with_no_gates_has_no_hint(self, sample_project):
        cfg = dataclasses.replace(sample_project, gates={})
        assert effects_dispatch.gate_hint(cfg) == ""

    def test_the_lowest_gate_id_wins_when_several_are_declared(self, sample_project):
        from nyxloom.config import GateDef
        cfg = dataclasses.replace(sample_project, gates={
            "z-last": GateDef(gate_id="z-last", argv=["zzz"], phase="implementation",
                              timeout_seconds=1, environment="local"),
            "a-first": GateDef(gate_id="a-first", argv=["aaa", "--x"],
                               phase="implementation", timeout_seconds=1,
                               environment="local"),
        })
        assert effects_dispatch.gate_hint(cfg) == "aaa --x"


class TestFrontmatterFor:

    def test_a_task_with_no_handoff_path_has_no_frontmatter(self, sample_project):
        tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t1",
                            project="demo", state=TaskState.QUEUED,
                            since=utc_now(), handoff_path=None)
        assert effects_dispatch.frontmatter_for(sample_project, tsf) is None

    def test_a_handoff_path_that_does_not_exist_has_no_frontmatter(self, sample_project):
        tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t1",
                            project="demo", state=TaskState.QUEUED,
                            since=utc_now(), handoff_path="handoff/gone.md")
        assert effects_dispatch.frontmatter_for(sample_project, tsf) is None

    def test_an_unparseable_handoff_degrades_rather_than_raising(self, sample_project):
        """ADVISORY input: it shapes a prompt and selects leases, and can only
        ever REDUCE what a launch holds. A task whose handoff cannot be parsed
        launches holding NO leases rather than taking the pass down."""
        (sample_project.root / "handoff" / "broken.md").write_text(
            "---\nnot: [valid: yaml\n---\n", encoding="utf-8")
        tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t1",
                            project="demo", state=TaskState.QUEUED,
                            since=utc_now(), handoff_path="handoff/broken.md")
        assert effects_dispatch.frontmatter_for(sample_project, tsf) is None

    def test_a_valid_handoff_parses(self, sample_project):
        tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                            task_id="demo-P01-sample", project="demo",
                            state=TaskState.QUEUED, since=utc_now(),
                            handoff_path="handoff/demo-P01-sample.md")
        fm = effects_dispatch.frontmatter_for(sample_project, tsf)
        assert fm is not None and fm.id == "demo-P01-sample"


class TestLeaseSpecs:

    def test_no_frontmatter_holds_no_leases(self, sample_project):
        assert effects_dispatch.lease_specs(sample_project, None) == []

    def test_a_mutex_the_project_does_not_declare_is_skipped(self, sample_project):
        """The handoff names it, the project does not define it. Inventing a
        capacity would be worse than holding nothing: a lease with a guessed
        capacity is indistinguishable from a real one."""
        class _FM:
            def effective_mutexes(self):
                return ["undeclared-mutex", "stack"]

        cfg = dataclasses.replace(sample_project, mutexes={
            "stack": MutexDef(name="stack", scope="project", capacity=2)})
        specs = effects_dispatch.lease_specs(cfg, _FM())
        assert [s["name"] for s in specs] == [
            MutexDef(name="stack", scope="project", capacity=2).lease_name("demo")]
        assert specs[0]["capacity"] == 2


class TestScopeAmendmentFiles:

    def _approved(self, task_id: str, filename: str) -> Event:
        return Event(schema_version=storage.SCHEMA_VERSION, sequence=0,
                     timestamp=utc_now(), project="demo",
                     actor=Actor(ActorKind.TICK, "t"),
                     type=EventType.SCOPE_AMENDMENT_APPROVED,
                     payload={"file": filename}, task_id=task_id)

    def test_approved_files_come_back_in_order_deduplicated(self):
        events = [self._approved("t1", "a.py"), self._approved("t1", "b.py"),
                  self._approved("t1", "a.py")]
        assert effects_dispatch.scope_amendment_files(events, "t1") == ["a.py", "b.py"]

    def test_another_task_s_amendments_do_not_leak(self):
        events = [self._approved("t1", "a.py"), self._approved("t2", "other.py")]
        assert effects_dispatch.scope_amendment_files(events, "t1") == ["a.py"]

    def test_a_request_without_an_approval_grants_nothing(self):
        """The APPROVED count is the cap's enforcement, so only approvals
        widen the allowlist -- a raw request must not."""
        requested = Event(schema_version=storage.SCHEMA_VERSION, sequence=0,
                          timestamp=utc_now(), project="demo",
                          actor=Actor(ActorKind.TICK, "t"),
                          type=EventType.SCOPE_AMENDMENT_REQUESTED,
                          payload={"file": "sneaky.py"}, task_id="t1")
        assert effects_dispatch.scope_amendment_files([requested], "t1") == []

"""CR-05b: the launch primitives the agent-dispatching families share.

These are the decisions that gate or shape a launch without belonging to any
one family. Each is tested in BOTH directions -- an admission gate that has
only ever admitted is not known to refuse.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from nyxloom import effects, effects_dispatch, log, paths, storage
from nyxloom.config import MutexDef, RouteDef
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


def _ctx(cfg, states=None, *, files=None, audit=None, provider_pause=None, clock=None):
    replacements = {"files": files or _Files(present=False)}
    if clock is not None:
        replacements["clock"] = clock
    ports = dataclasses.replace(effects.EffectPorts.system(), **replacements)
    return effects.EffectContext(project="demo", cfg=cfg,
                                 states=states or {}, ports=ports,
                                 snapshot_audit=audit, provider_pause=provider_pause)


class _FakeClock:
    """CR-11a: a pinned monotonic clock so a route-pause deadline can be
    placed deterministically before/after "now" without a real sleep."""

    def __init__(self, mono: float = 100.0) -> None:
        self.mono = mono

    def now(self):
        return utc_now()

    def monotonic(self) -> float:
        return self.mono


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
# admission's route-health precondition (CR-11a)


class TestRouteCurrentlyPaused:
    """The FRESH, effect-boundary read of ProviderPauseRegistry --
    deliberately NOT the plan-time provider_ok snapshot, so a route paused
    by another task's outcome mid-pass is caught before THIS launch too."""

    def test_no_registry_wired_degrades_to_not_paused(self, sample_project):
        """provider_pause defaults to None (a test-constructed context
        exercising an unrelated check) -- must never crash."""
        ctx = _ctx(sample_project, provider_pause=None)
        assert effects_dispatch.route_currently_paused(ctx, _route()) is False

    def test_an_unpaused_route_is_not_paused(self, sample_project):
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        ctx = _ctx(sample_project, provider_pause=registry, clock=clock)
        assert effects_dispatch.route_currently_paused(ctx, _route()) is False

    def test_a_route_paused_with_time_remaining_is_paused(self, sample_project):
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r1", 60)
        ctx = _ctx(sample_project, provider_pause=registry, clock=clock)
        assert effects_dispatch.route_currently_paused(ctx, _route()) is True

    def test_a_route_whose_pause_has_expired_is_not_paused(self, sample_project):
        """The deadline itself is the boundary -- clock == paused_until is
        NOT still paused (matches _provider_ok's `now < paused_until`)."""
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r1", 60)
        clock.mono = 160.0
        ctx = _ctx(sample_project, provider_pause=registry, clock=clock)
        assert effects_dispatch.route_currently_paused(ctx, _route()) is False

    def test_a_different_routes_pause_does_not_leak(self, sample_project):
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r2", 3600)
        ctx = _ctx(sample_project, provider_pause=registry, clock=clock)
        assert effects_dispatch.route_currently_paused(ctx, _route(route_id="r1")) is False


class TestAdmissibleRefusesAPausedRoute:
    """CR-11a: admissible() itself refuses a launch into a currently-paused
    route, closing the gap CR-08 Slice 2 named as CR-11's charter -- a route
    paused by one task's LIMIT receipt mid-pass could otherwise still admit
    another task's already-planned dispatch/resume into the SAME route
    later in the SAME pass."""

    def test_refuses_dispatch_into_a_currently_paused_route(self, tmp_state, sample_project):
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r1", 60)
        ctx = _ctx(sample_project, provider_pause=registry, clock=clock)
        ok, reason = effects_dispatch.admissible(ctx, "dispatch", _route(route_id="r1"))
        assert (ok, reason) == (False, "route-paused:r1")

    def test_admits_once_the_pause_expires(self, tmp_state, sample_project):
        """Self-healing: the SAME check that refused now admits, once the
        deadline passes, with no operator action -- unlike a permanent
        stall, this is exactly the pause/budget refusal shape above."""
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r1", 60)
        clock.mono = 160.0
        ctx = _ctx(sample_project, provider_pause=registry, clock=clock)
        ok, reason = effects_dispatch.admissible(
            ctx, "dispatch", _route(route_id="r1", trust="operator"))
        assert (ok, reason) == (True, "")

    def test_a_paused_route_does_not_refuse_a_launch_into_a_different_route(
            self, tmp_state, sample_project):
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r2", 3600)
        ctx = _ctx(sample_project, provider_pause=registry, clock=clock)
        ok, reason = effects_dispatch.admissible(
            ctx, "dispatch", _route(route_id="r1", trust="operator"))
        assert (ok, reason) == (True, "")

    def test_a_paused_route_does_not_refuse_a_launch_with_no_route_given(
            self, tmp_state, sample_project):
        """The self-review/bare-carve shape (route resolved later, or not
        at all) -- route health has nothing to check without a route, same
        as containment."""
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r1", 60)
        ctx = _ctx(sample_project, provider_pause=registry, clock=clock)
        ok, reason = effects_dispatch.admissible(ctx, "self-review")
        assert (ok, reason) == (True, "")

    def test_route_pause_is_checked_before_containment(self, tmp_state, sample_project, monkeypatch):
        """Cheap-refusal-first ordering (the docstring's own stated
        principle): a route that is BOTH paused AND would need an
        unavailable containment refuses on the cheaper route-paused reason,
        never reaching the two docker calls."""
        clock = _FakeClock(mono=100.0)
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r1", 60)
        processes = _Processes(version_rc=1)
        ctx = _ctx_with(sample_project, processes, provider_pause=registry, clock=clock)
        env = {"NYXLOOM_CONTAINMENT_IMAGE": "agent:local",
               "NYXLOOM_CONTAINMENT_HOST_MAP": "/tmp=/tmp"}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        ok, reason = effects_dispatch.admissible(
            ctx, "dispatch", _route(route_id="r1", trust=None))
        assert (ok, reason) == (False, "route-paused:r1")
        assert processes.calls == []


# ---------------------------------------------------------------------------
# admission's containment precondition (CR-13a)


class _Processes:
    """A process port that answers docker without running it."""

    def __init__(self, version_rc=0, inspect_rc=0) -> None:
        self.version_rc, self.inspect_rc = version_rc, inspect_rc
        self.calls: list[list[str]] = []

    def run(self, argv, *, cwd=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        rc = self.version_rc if "version" in argv else self.inspect_rc
        return subprocess.CompletedProcess(argv, rc, "", "")


def _ctx_with(cfg, processes, **kw):
    ctx = _ctx(cfg, **kw)
    return dataclasses.replace(
        ctx, ports=dataclasses.replace(ctx.ports, processes=processes))


def _route(**kw) -> RouteDef:
    base = dict(route_id="r1", cli="fake", model="m")
    base.update(kw)
    return RouteDef(**base)


class TestAdmissionRefusesAnUncontainableRoute:
    """CR-13a: the execute-time gate over containment.

    Both directions, like every other case in this file. The refusing half is
    the acceptance criterion ("a route configured to require containment that
    is unavailable does not launch"); the admitting half is what keeps the
    gate from being a wall in front of the whole factory.
    """

    def _env(self, **over):
        # An identity mapping for the tmp tree these fixtures live under:
        # this process IS containerized, so without a declared mapping the
        # repository path is one the host daemon cannot resolve -- which is
        # itself a refusal, exercised separately below.
        env = {"NYXLOOM_CONTAINMENT_IMAGE": "agent:local",
               "NYXLOOM_CONTAINMENT_HOST_MAP": "/tmp=/tmp"}
        env.update(over)
        return env

    def test_an_operator_trusted_route_never_consults_docker_at_all(
            self, tmp_state, sample_project, monkeypatch):
        """Containment is not required, so the most expensive question is not
        asked -- and a deployment with no docker at all still dispatches its
        trusted routes."""
        processes = _Processes()
        ctx = _ctx_with(sample_project, processes)
        ok, reason = effects_dispatch.admissible(
            ctx, "dispatch", _route(trust="operator"))
        assert (ok, reason) == (True, "")
        assert processes.calls == []

    def test_passing_no_route_leaves_the_gate_exactly_as_it_was(
            self, tmp_state, sample_project):
        processes = _Processes(version_rc=1)
        ctx = _ctx_with(sample_project, processes)
        assert effects_dispatch.admissible(ctx, "dispatch")[0] is True
        assert processes.calls == []

    def test_an_untrusted_route_is_admitted_when_containment_is_available(
            self, tmp_state, sample_project, monkeypatch):
        for name, value in self._env().items():
            monkeypatch.setenv(name, value)
        processes = _Processes()
        ctx = _ctx_with(sample_project, processes)
        ok, reason = effects_dispatch.admissible(
            ctx, "dispatch", _route(trust="untrusted"))
        assert (ok, reason) == (True, "")
        assert any("version" in c for c in processes.calls)
        assert any("inspect" in c for c in processes.calls)

    def test_an_untrusted_route_is_refused_when_the_image_is_missing(
            self, tmp_state, sample_project, monkeypatch):
        for name, value in self._env().items():
            monkeypatch.setenv(name, value)
        ctx = _ctx_with(sample_project, _Processes(inspect_rc=1))
        ok, reason = effects_dispatch.admissible(
            ctx, "dispatch", _route(trust="untrusted"))
        assert ok is False
        assert reason == "containment-unavailable:image-missing:agent:local"

    def test_a_free_route_is_refused_when_no_image_is_configured(
            self, tmp_state, sample_project, monkeypatch):
        """The deployed state today: free routes declared, no agent image
        built. The free tier stays disabled by mechanism rather than by a
        sentence in a prompt."""
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        processes = _Processes()
        ctx = _ctx_with(sample_project, processes)
        ok, reason = effects_dispatch.admissible(
            ctx, "dispatch", _route(status="free"))
        assert ok is False
        assert reason == "containment-unavailable:no-image-configured"
        assert processes.calls == [], (
            "a configuration fault must be answered without asking docker")

    def test_every_launch_kind_is_gated_not_only_dispatch(
            self, tmp_state, sample_project, monkeypatch):
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        ctx = _ctx_with(sample_project, _Processes())
        for kind in sorted(effects_dispatch.LAUNCH_KINDS):
            ok, reason = effects_dispatch.admissible(ctx, kind, _route())
            assert ok is False, kind
            assert reason.startswith("containment-unavailable:"), kind

    def test_a_cheaper_refusal_still_wins_so_docker_is_not_asked_when_paused(
            self, tmp_state, sample_project, monkeypatch):
        """Ordering, stated as a cost property: a paused project must not pay
        two docker round-trips per planned launch to be told it is paused."""
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        processes = _Processes()
        ctx = _ctx_with(sample_project, processes,
                        files=_Files(content="drain-agents"))
        ok, reason = effects_dispatch.admissible(ctx, "dispatch", _route())
        assert (ok, reason) == (False, "paused:drain-agents")
        assert processes.calls == []

    def test_the_refusal_is_logged_at_error_with_the_route_and_the_reason(
            self, tmp_state, sample_project, tmp_path, monkeypatch):
        """Pause and budget heal by themselves; this one repeats every pass
        until a human changes the deployment, so it is the only refusal here
        that reaches the operator's log at ERROR."""
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        log_dir = tmp_path / "logs"
        log.configure(level=log.DEBUG, log_dir=log_dir, console=False)
        ctx = _ctx_with(sample_project, _Processes())

        effects_dispatch.admissible(ctx, "dispatch", _route(route_id="free-1"))

        records = [json.loads(ln) for ln in
                   (log_dir / "nyxloom.jsonl").read_text().splitlines() if ln.strip()]
        refusals = [r for r in records if r.get("msg") == "containment-refused"]
        assert len(refusals) == 1
        assert refusals[0]["level"] == "error"
        assert refusals[0]["route"] == "free-1"
        assert refusals[0]["reason"] == "no-image-configured"


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

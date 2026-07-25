"""GA4 (docs/plan-gate-adoption.md, D-065 cadence mirror): the periodic
carver gate re-verify -- module contract item 16.

Cross-package split follows the D-065/test-health convention
(tests/test_test_health_carve.py): the pure WHEN decision (item 16) is
exercised against `plan_project` here, and the daemon half (durable cadence,
the background-thread execution model, the drain step) is exercised against
a real `Daemon` -- same file, since it is one coherent feature.

Every positive oracle below is paired with the negative that proves it keys
on the thing it claims to. The HARD INVARIANT this whole package exists to
protect (gate_verify_interval_days defaults to 0 -> byte-identical to
pre-GA4 production behavior) gets its own dedicated section.
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from nyxloom import daemon, lint, notify, reconcile, storage
from nyxloom.config import GateDef, MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import ReconcileInput, VerifyGate, plan_project
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Event, EventType, GateResult, Role, Route,
    TaskState, TaskStateFile, utc_now,
)


# --------------------------------------------------------------------------
# local helpers / fixtures (never added to conftest.py -- STANDING.md)

def _cfg(gate_verify_interval_days: int = 7, carve_ahead_target: int = 5,
         headroom_warn: int = 5) -> ProjectConfig:
    return ProjectConfig(
        project_id="demo",
        root=Path("/demo"),
        default_branch="main",
        worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={},
        mutexes={"stack": MutexDef(name="stack", scope="project", capacity=1)},
        policy=Policy(
            carve_ahead_target=carve_ahead_target,
            headroom_warn=headroom_warn,
            gate_verify_interval_days=gate_verify_interval_days,
        ),
    )


def _routes() -> Routes:
    return Routes(
        revision="test-rev",
        tiers={"frontier-review": ["route-review"], "flash-high": ["route-1"]},
        routes={
            "route-1": RouteDef(route_id="route-1", cli="fake", model="fake-model"),
            "route-review": RouteDef(route_id="route-review", cli="fake", model="review-model"),
        },
    )


def _inp(**overrides) -> ReconcileInput:
    """A project with an EMPTY queue -- so item 9's headroom refill also
    wants the pass's single carve slot. That overlap is deliberate: it is
    what makes the non-competition oracles below meaningful (VerifyGate and
    a headroom CarveDispatch BOTH fire in the same pass) rather than
    vacuous."""
    base = dict(
        now=utc_now(),
        cfg=_cfg(),
        routes=_routes(),
        states={},
        frontmatters={},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True, "route-review": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        days_since_gate_verify=None,
    )
    base.update(overrides)
    return ReconcileInput(**base)


def _verifies(inp: ReconcileInput) -> list[VerifyGate]:
    return [a for a in plan_project(inp) if isinstance(a, VerifyGate)]


# ==========================================================================
# A policy knob has TWO homes -- the dataclass and the CFG1 schema
# (test_every_policy_field_is_toml_settable_or_explicitly_infra_sourced in
# test_test_health_carve.py already sweeps EVERY Policy field generically;
# this is the specific instance, mirroring
# test_test_health_interval_days_is_schema_valid_in_the_repos_own_config).
# ==========================================================================

def test_gate_verify_interval_days_is_schema_valid_in_the_repos_own_config():
    import dataclasses

    schema = json.loads(
        (Path(reconcile.__file__).parent / "schemas" / "nyxloom-config.schema.json")
        .read_text(encoding="utf-8"))
    spec = schema["properties"]["policy"]["properties"]["gate_verify_interval_days"]
    assert spec == {"type": "integer", "minimum": 0}
    assert "gate_verify_interval_days" in {f.name for f in dataclasses.fields(Policy)}


# ==========================================================================
# HARD INVARIANT: feature-off (interval == 0) is byte-identical to pre-GA4
# ==========================================================================

@pytest.mark.parametrize("days_since_gate_verify,project_paused", [
    (None, False),   # never verified -- would fire if the knob were on
    (0.0, False),
    (100.0, False),  # wildly overdue -- would fire if the knob were on
    (None, True),
])
def test_feature_off_pure_planner_emits_no_verify_gate_LOAD_BEARING(
        days_since_gate_verify, project_paused):
    """interval=0 (the default) -> plan_project emits NO VerifyGate for ANY
    of these representative inputs, including ones that would obviously fire
    if the cadence were merely 'not yet due'."""
    inp = _inp(cfg=_cfg(gate_verify_interval_days=0),
               days_since_gate_verify=days_since_gate_verify,
               project_paused=project_paused)
    assert _verifies(inp) == []


def test_feature_off_real_run_pass_starts_no_thread_and_appends_no_event_LOAD_BEARING(
        tmp_state, sample_project, monkeypatch):
    """The daemon-level half of the same invariant: gate_verify_interval_days
    defaults to 0 on a freshly-loaded project config, so a REAL run_pass
    (real plan_project, not stubbed) must start NO background thread and
    append NO GATE_VERIFY_RECORDED/NEEDS_OPERATOR{gate-verify-*} -- production
    behavior byte-identical to pre-GA4."""
    from nyxloom import adapters, render, wrapper

    assert sample_project.policy.gate_verify_interval_days == 0

    monkeypatch.setattr(adapters, "probe", lambda route: (True, "ok"))
    monkeypatch.setattr(
        adapters, "build_dispatch",
        lambda route, *, handoff_path, worktree, branch, task_id, gate_hint,
        receipt_path, **_kw: (["fake-cli", "--task", task_id], "prompt"))

    def fake_launch(spec):
        Path(spec.attempt_dir).mkdir(parents=True, exist_ok=True)
        return 4242

    monkeypatch.setattr(wrapper, "launch_detached", fake_launch)
    monkeypatch.setattr(render, "render_after_event", lambda registry: None)
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})

    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    assert d._gate_verify_running == {}
    assert d._gate_verify_results.empty()
    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.GATE_VERIFY_RECORDED for e in events)
    assert not any(
        e.type is EventType.NEEDS_OPERATOR
        and str(e.payload.get("reason", "")).startswith("gate-verify-")
        for e in events
    )


# ==========================================================================
# Item 16 -- the cadence itself (planner, pure)
# ==========================================================================

def test_never_verified_fires_a_verify_gate():
    verifies = _verifies(_inp(days_since_gate_verify=None))
    assert len(verifies) == 1
    assert verifies[0].project == "demo"


def test_overdue_fires():
    assert len(_verifies(_inp(days_since_gate_verify=20.0))) == 1


def test_exactly_at_interval_fires_boundary():
    """>= interval, not >."""
    assert len(_verifies(_inp(days_since_gate_verify=7.0))) == 1


def test_not_yet_due_does_not_fire_THE_OPT_IN_TIMING_DISCRIMINATOR():
    assert _verifies(_inp(days_since_gate_verify=3.0)) == []


def test_paused_project_gets_no_verify_gate():
    assert _verifies(_inp(project_paused=True, days_since_gate_verify=None)) == []


# ==========================================================================
# The design call item 16 documents: OUTSIDE the single-carve-authority mutex
# ==========================================================================

def test_verify_gate_does_not_compete_with_the_single_carve_slot():
    """VerifyGate is planned OUTSIDE the carve mutex -- proven by firing BOTH
    the untargeted headroom-refill carve (empty queue, item 9) and
    gate-verify (never verified) in the SAME pass and getting BOTH actions,
    not just one. If VerifyGate consumed the single carve slot this would
    assert false (only one of the two would ever appear)."""
    actions = plan_project(_inp(days_since_gate_verify=None))
    kinds = {type(a).__name__ for a in actions}
    assert "VerifyGate" in kinds
    assert "CarveDispatch" in kinds


def test_verify_gate_fires_even_with_no_healthy_frontier_route():
    """Unlike item 9/12/15, item 16 needs no LLM route at all -- a gate
    verify is a subprocess probe."""
    inp = _inp(provider_ok={"route-1": True, "route-review": False},
               days_since_gate_verify=None)
    assert len(_verifies(inp)) == 1


def test_verify_gate_fires_even_with_exhausted_budget():
    inp = _inp(budget_remaining=0, days_since_gate_verify=None)
    assert len(_verifies(inp)) == 1


def test_verify_gate_fires_even_with_carver_already_in_flight():
    attempt = Attempt(
        attempt_id="att-1", role=Role.CARVER, state=AttemptState.RUNNING,
        route=Route(route_id="route-review", cli="fake", model="m", routes_rev="test-rev"),
        started=utc_now(),
    )
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[attempt],
    )
    inp = _inp(states={"carve-demo-1": tsf}, days_since_gate_verify=None)
    assert len(_verifies(inp)) == 1


# ==========================================================================
# ReconcileTrace breadcrumb (a NEW requirement item 16 adds, unlike item 15)
# ==========================================================================

def test_gate_verify_breadcrumb_on_fire():
    result = plan_project(_inp(days_since_gate_verify=None))
    fires = [n for n in result.trace.breadcrumbs if n.kind == "gate-verify"]
    assert any(n.detail == "fire" for n in fires)


def test_gate_verify_breadcrumb_on_paused_skip():
    result = plan_project(_inp(days_since_gate_verify=None, project_paused=True))
    skips = [n for n in result.trace.breadcrumbs if n.kind == "gate-verify"]
    assert any(n.detail == "paused" for n in skips)


def test_gate_verify_breadcrumb_absent_when_feature_off():
    result = plan_project(_inp(cfg=_cfg(gate_verify_interval_days=0),
                               days_since_gate_verify=None))
    assert [n for n in result.trace.breadcrumbs if n.kind == "gate-verify"] == []


# ==========================================================================
# daemon._days_since_gate_verify -- durable cadence across restarts
# (mirrors test_test_health_carve.py's _days_since_test_health_carve suite)
# ==========================================================================

def test_days_since_gate_verify_is_none_when_never_verified(tmp_state, sample_project):
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_gate_verify("demo") is None


def _gv_ev(*, age_days: float, sequence: int, verdict: str = "TRUSTWORTHY") -> Event:
    return Event(
        schema_version=storage.SCHEMA_VERSION, sequence=sequence,
        timestamp=utc_now() - timedelta(days=age_days), project="demo",
        actor=Actor(ActorKind.TICK, "test"), type=EventType.GATE_VERIFY_RECORDED,
        payload={"project": "demo", "verdict": verdict, "gate_id": "g1", "at": "x"},
        task_id=None,
    )


def test_days_since_gate_verify_computes_a_real_age_not_just_presence(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(storage, "iter_events",
                        lambda project: [_gv_ev(age_days=30.0, sequence=1)])
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_gate_verify("demo") == pytest.approx(30.0, abs=0.01)


def test_days_since_gate_verify_takes_the_most_recent_of_several(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(storage, "iter_events", lambda project: [
        _gv_ev(age_days=3.0, sequence=1),
        _gv_ev(age_days=90.0, sequence=2),
    ])
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_gate_verify("demo") == pytest.approx(3.0, abs=0.01)


def test_days_since_gate_verify_bad_timestamp_is_zero_not_none_FAIL_SAFE(
        tmp_state, sample_project, monkeypatch):
    ev = _gv_ev(age_days=5.0, sequence=1)
    ev.timestamp = ev.timestamp.replace(tzinfo=None)
    monkeypatch.setattr(storage, "iter_events", lambda project: [ev])
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_gate_verify("demo") == 0.0


def test_days_since_gate_verify_unreadable_log_is_zero_not_none_FAIL_SAFE(
        tmp_state, sample_project, monkeypatch):
    def boom(project):
        raise OSError("event log unreadable")
    monkeypatch.setattr(storage, "iter_events", boom)
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_gate_verify("demo") == 0.0


# ==========================================================================
# Executor idempotence -- background thread + result queue
# ==========================================================================

def test_execute_verify_gate_starts_no_second_thread_while_one_is_alive(
        tmp_state, sample_project, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake_bg(self, project, cfg):
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(daemon.Daemon, "_run_gate_verify_bg", fake_bg)
    d = daemon.Daemon({"demo": sample_project.root})
    try:
        events1 = d._execute_verify_gate("demo", sample_project)
        assert events1 == []
        assert started.wait(timeout=2), "background thread never started"
        first = d._gate_verify_running["demo"]
        assert first.is_alive()

        events2 = d._execute_verify_gate("demo", sample_project)
        assert events2 == []
        second = d._gate_verify_running["demo"]
        assert second is first, "a second thread was started while the first was still alive"
    finally:
        release.set()
        d._gate_verify_running["demo"].join(timeout=5)


def test_execute_verify_gate_starts_a_fresh_thread_once_the_prior_one_finished(
        tmp_state, sample_project, monkeypatch):
    """Positive control for the idempotence test above: once a verify
    genuinely finishes, the NEXT VerifyGate action does start a new thread
    (idempotence guards a live run, not the feature)."""
    calls = []

    def fake_bg(self, project, cfg):
        calls.append(1)

    monkeypatch.setattr(daemon.Daemon, "_run_gate_verify_bg", fake_bg)
    d = daemon.Daemon({"demo": sample_project.root})
    d._execute_verify_gate("demo", sample_project)
    d._gate_verify_running["demo"].join(timeout=5)
    d._execute_verify_gate("demo", sample_project)
    d._gate_verify_running["demo"].join(timeout=5)
    assert len(calls) == 2


def test_execute_dispatches_verify_gate_action_via_the_isinstance_chain(
        tmp_state, sample_project, monkeypatch):
    calls = []

    def fake_execute(self, project, cfg):
        calls.append((project, cfg))
        return []

    monkeypatch.setattr(daemon.Daemon, "_execute_verify_gate", fake_execute)
    d = daemon.Daemon({"demo": sample_project.root})
    action = reconcile.VerifyGate(project="demo")
    result = d._execute("demo", sample_project, {}, action)
    assert result == []
    assert calls == [("demo", sample_project)]


# ==========================================================================
# _run_gate_verify_bg -- verdict derivation (mirrors cli.cmd_gate_verify)
# ==========================================================================

def test_bg_probe_no_gate_declared(tmp_state, sample_project, monkeypatch):
    from nyxloom import gate_runner
    monkeypatch.setattr(gate_runner, "select_verification_gate", lambda cfg: None)
    d = daemon.Daemon({"demo": sample_project.root})
    d._run_gate_verify_bg("demo", sample_project)
    result = d._gate_verify_results.get_nowait()
    assert result == {"project": "demo", "verdict": "NO_GATE", "gate_id": None}


def test_bg_probe_broken_when_good_head_fails(tmp_state, sample_project, monkeypatch):
    from nyxloom import gate_canary, gate_runner

    gate = sample_project.gates["pytest-q"]
    monkeypatch.setattr(gate_runner, "select_verification_gate", lambda cfg: gate)
    monkeypatch.setattr(
        gate_runner, "run_gate_at_commit",
        lambda cfg, gate, commit, phase="post-merge": GateResult(
            gate_id=gate.gate_id, phase=phase, commit=commit, exit_code=1,
            started=utc_now(), ended=utc_now()))

    def must_not_verify(*_a, **_k):
        raise AssertionError("canary must never run when good HEAD already fails")

    monkeypatch.setattr(gate_canary, "verify_gate_rejects_canary", must_not_verify)

    d = daemon.Daemon({"demo": sample_project.root})
    d._run_gate_verify_bg("demo", sample_project)
    result = d._gate_verify_results.get_nowait()
    assert result["verdict"] == "BROKEN"
    assert result["gate_id"] == "pytest-q"


def _bg_result_for_canary(sample_project, monkeypatch, canary_result):
    from nyxloom import gate_canary, gate_runner

    gate = sample_project.gates["pytest-q"]
    monkeypatch.setattr(gate_runner, "select_verification_gate", lambda cfg: gate)
    monkeypatch.setattr(
        gate_runner, "run_gate_at_commit",
        lambda cfg, gate, commit, phase="post-merge": GateResult(
            gate_id=gate.gate_id, phase=phase, commit=commit, exit_code=0,
            started=utc_now(), ended=utc_now()))
    monkeypatch.setattr(
        gate_canary, "verify_gate_rejects_canary",
        lambda cfg, gate, commit: canary_result)
    d = daemon.Daemon({"demo": sample_project.root})
    d._run_gate_verify_bg("demo", sample_project)
    return d._gate_verify_results.get_nowait()


def test_bg_probe_trustworthy_when_canary_killed(tmp_state, sample_project, monkeypatch):
    from nyxloom.gate_canary import CanaryVerifyResult
    result = _bg_result_for_canary(
        sample_project, monkeypatch, CanaryVerifyResult(killed=True, inconclusive=False))
    assert result["verdict"] == "TRUSTWORTHY"


def test_bg_probe_launders_when_every_canary_survives(tmp_state, sample_project, monkeypatch):
    from nyxloom.gate_canary import CanaryVerifyResult
    result = _bg_result_for_canary(
        sample_project, monkeypatch, CanaryVerifyResult(killed=False, inconclusive=False))
    assert result["verdict"] == "LAUNDERS"


def test_bg_probe_inconclusive_when_no_canary_rendered_a_verdict(tmp_state, sample_project, monkeypatch):
    from nyxloom.gate_canary import CanaryVerifyResult
    result = _bg_result_for_canary(
        sample_project, monkeypatch, CanaryVerifyResult(killed=False, inconclusive=True))
    assert result["verdict"] == "INCONCLUSIVE"


def test_bg_probe_inconclusive_when_head_cannot_be_resolved(tmp_state, tmp_path):
    """A cfg.root that is not a git repo at all -- `git rev-parse HEAD` fails,
    so there is no commit to probe against."""
    root = tmp_path / "no-git"
    root.mkdir()
    cfg = ProjectConfig(
        project_id="demo", root=root, default_branch="main", worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={"g1": GateDef(gate_id="g1", argv=["true"], phase="implementation",
                             timeout_seconds=5)},
        mutexes={"stack": MutexDef(name="stack", scope="project", capacity=1)},
        policy=Policy(),
    )
    d = daemon.Daemon({"demo": root})
    d._run_gate_verify_bg("demo", cfg)
    result = d._gate_verify_results.get_nowait()
    assert result["verdict"] == "INCONCLUSIVE"
    assert result["gate_id"] == "g1"


def test_bg_probe_exception_degrades_to_inconclusive_not_a_dead_thread(
        tmp_state, sample_project, monkeypatch):
    from nyxloom import gate_runner

    def boom(cfg):
        raise RuntimeError("boom")

    monkeypatch.setattr(gate_runner, "select_verification_gate", boom)
    d = daemon.Daemon({"demo": sample_project.root})
    d._run_gate_verify_bg("demo", sample_project)
    result = d._gate_verify_results.get_nowait()
    assert result["verdict"] == "INCONCLUSIVE"
    assert result["gate_id"] is None


# ==========================================================================
# _drain_gate_verify_results -- the sole event-append seam (main thread)
# ==========================================================================

def _put(d: "daemon.Daemon", verdict: str, gate_id: str = "g1") -> None:
    d._gate_verify_results.put({"project": "demo", "verdict": verdict, "gate_id": gate_id})


def test_drain_launders_appends_record_and_escalates_once(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    d._gate_verify_running["demo"] = object()  # sentinel: must be cleared
    _put(d, "LAUNDERS")

    events = d._drain_gate_verify_results("demo", sample_project, {})

    types_ = {e.type for e in events}
    assert EventType.GATE_VERIFY_RECORDED in types_
    assert EventType.NEEDS_OPERATOR in types_
    needs = next(e for e in events if e.type is EventType.NEEDS_OPERATOR)
    assert needs.payload["reason"] == "gate-verify-launders"
    assert "demo" not in d._gate_verify_running


def test_drain_broken_appends_record_and_escalates(tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    _put(d, "BROKEN")

    events = d._drain_gate_verify_results("demo", sample_project, {})

    needs = next(e for e in events if e.type is EventType.NEEDS_OPERATOR)
    assert needs.payload["reason"] == "gate-verify-broken"


def test_drain_trustworthy_appends_record_but_no_escalation_THE_NEGATIVE(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    _put(d, "TRUSTWORTHY")

    events = d._drain_gate_verify_results("demo", sample_project, {})

    types_ = {e.type for e in events}
    assert EventType.GATE_VERIFY_RECORDED in types_
    assert EventType.NEEDS_OPERATOR not in types_


def test_drain_debounces_a_repeated_launders_escalation(tmp_state, sample_project, monkeypatch):
    """The SAME bad verdict twice in a row escalates ONCE -- the second drain
    still records the cadence marker but must not re-notify an already-open
    episode (mirrors _needs_operator_recently_emitted's own contract)."""
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})

    _put(d, "LAUNDERS")
    first = d._drain_gate_verify_results("demo", sample_project, {})
    assert any(e.type is EventType.NEEDS_OPERATOR for e in first)

    _put(d, "LAUNDERS")
    second = d._drain_gate_verify_results("demo", sample_project, {})
    assert any(e.type is EventType.GATE_VERIFY_RECORDED for e in second)
    assert not any(e.type is EventType.NEEDS_OPERATOR for e in second)


def test_drain_requeues_results_belonging_to_other_projects(
        tmp_state, sample_project, monkeypatch):
    """The result queue is shared across every registered project (one
    Daemon, one queue) -- a drain call scoped to 'demo' must not consume (or
    lose) a result stamped for a different project."""
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    d._gate_verify_results.put({"project": "other", "verdict": "TRUSTWORTHY", "gate_id": "g1"})

    events = d._drain_gate_verify_results("demo", sample_project, {})

    assert events == []
    requeued = d._gate_verify_results.get_nowait()
    assert requeued["project"] == "other"
    assert d._gate_verify_results.empty()


# ==========================================================================
# End-to-end: a real dispatched VerifyGate, a real background thread, and
# the next pass's drain closing the loop
# ==========================================================================

def _scripted(monkeypatch, sequence):
    seq = list(sequence)

    def fake(inp):
        if seq:
            return seq.pop(0)
        return []

    monkeypatch.setattr(reconcile, "plan_project", fake)


def test_end_to_end_dispatch_thread_then_drain_closes_the_cadence(
        tmp_state, sample_project, monkeypatch):
    """Full round trip, with only the SLOW gate/canary calls faked (per the
    handoff: 'monkeypatch verify_gate_rejects_canary and the good-HEAD gate
    run so no real minutes-long gate runs') -- the thread itself, and
    _execute_verify_gate/_run_gate_verify_bg/_drain_gate_verify_results, all
    run for real."""
    from nyxloom import gate_canary, gate_runner

    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)

    gate = sample_project.gates["pytest-q"]
    monkeypatch.setattr(gate_runner, "select_verification_gate", lambda cfg: gate)
    monkeypatch.setattr(
        gate_runner, "run_gate_at_commit",
        lambda cfg, gate, commit, phase="post-merge": GateResult(
            gate_id=gate.gate_id, phase=phase, commit=commit, exit_code=0,
            started=utc_now(), ended=utc_now()))
    monkeypatch.setattr(
        gate_canary, "verify_gate_rejects_canary",
        lambda cfg, gate, commit: gate_canary.CanaryVerifyResult(killed=True, inconclusive=False))

    _scripted(monkeypatch, [[reconcile.VerifyGate(project="demo")]])

    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    t = d._gate_verify_running.get("demo")
    assert t is not None, "VerifyGate did not start a background thread"
    t.join(timeout=5)
    assert not t.is_alive()

    # Next pass: plan_project (still scripted, now to []) drains the
    # finished result.
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    recorded = [e for e in events if e.type is EventType.GATE_VERIFY_RECORDED]
    assert len(recorded) == 1
    assert recorded[0].payload["verdict"] == "TRUSTWORTHY"
    assert recorded[0].payload["gate_id"] == "pytest-q"
    assert not any(e.type is EventType.NEEDS_OPERATOR for e in events)

    age = d._days_since_gate_verify("demo")
    assert age is not None and age < 1.0
    assert "demo" not in d._gate_verify_running

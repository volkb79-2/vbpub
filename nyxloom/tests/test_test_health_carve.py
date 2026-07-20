"""D-065 (B63 2026-07-20): the strategic TEST-HEALTH carve trigger --
reconcile module contract item 15, plus the daemon plumbing that makes its
cadence durable and keeps it from contaminating the WORK roadmap's signals.

Cross-package split follows the existing carve convention: the pure WHEN
decision (item 15) is exercised against `plan_project` here rather than in
test_reconcile.py because it is one coherent feature with its daemon half;
the packet SHAPE and the carve-outcome suppression are driven through the
real daemon (`run_pass`), mirroring test_carver.py's harness.

Every positive oracle below is paired with the negative that proves it keys
on the thing it claims to: a trigger that "fires" is only interesting next
to the identical input where it must not, and a suppression is only real
next to the identical summary that must still emit.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from nyxloom import daemon, lint, paths, reconcile, storage
from nyxloom.config import MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import CarveDispatch, ReconcileInput, plan_project
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Receipt, ReceiptResult,
    Role, Route, TaskState, TaskStateFile, utc_now,
)


# --------------------------------------------------------------------------
# local helpers / fixtures (never added to conftest.py -- STANDING.md)

def _cfg(test_health_interval_days: int = 14, carve_ahead_target: int = 5,
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
            test_health_interval_days=test_health_interval_days,
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
    wants the pass's single carve slot unless something outranks it. That
    overlap is deliberate: it is what makes the ordering oracles below
    meaningful rather than vacuous."""
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
        days_since_test_health_carve=None,
    )
    base.update(overrides)
    return ReconcileInput(**base)


def _carves(inp: ReconcileInput) -> list[CarveDispatch]:
    return [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]


# ==========================================================================
# A policy knob has TWO homes -- the dataclass and the CFG1 schema
# ==========================================================================

# Known, PRE-EXISTING gap (not introduced by B63): http_bind is a real
# Policy field documented as toml-settable, but absent from the schema's
# policy properties, so a project that sets it gets a spurious CFG1 error.
# Left alone deliberately -- whether http_bind should be toml-settable at all
# (an env override already exists) is its own call, not a side effect of this
# package. Listed here so the invariant below is enforceable today and the
# gap stays visible instead of buried.
_KNOWN_SCHEMA_GAPS = {"http_bind"}


def test_every_policy_field_is_in_the_config_schema():
    """The gate caught B63 adding test_health_interval_days to the Policy
    dataclass but not to nyxloom-config.schema.json, whose policy object is
    additionalProperties:false -- so nyxloom's OWN nyxloom.toml became
    CFG1-invalid the moment it used the new knob. That coupling is invisible
    at the dataclass, so pin it: any future knob fails HERE, with a message
    naming the file to edit, instead of as a puzzling lint failure."""
    import dataclasses

    schema = json.loads(
        (Path(reconcile.__file__).parent / "schemas" / "nyxloom-config.schema.json")
        .read_text(encoding="utf-8"))
    props = set(schema["properties"]["policy"]["properties"])
    fields = {f.name for f in dataclasses.fields(Policy)}
    missing = fields - props - _KNOWN_SCHEMA_GAPS
    assert not missing, (
        f"Policy field(s) {sorted(missing)} are missing from "
        "src/nyxloom/schemas/nyxloom-config.schema.json (policy.properties). "
        "That object is additionalProperties:false, so any project setting "
        "them in nyxloom.toml gets a CFG1 error."
    )


def test_test_health_interval_days_is_schema_valid_in_the_repos_own_config():
    """The specific instance: nyxloom's own toml sets the knob (dogfooding,
    and what keeps it off P43's dead-stub list), so it must lint clean."""
    import dataclasses

    schema = json.loads(
        (Path(reconcile.__file__).parent / "schemas" / "nyxloom-config.schema.json")
        .read_text(encoding="utf-8"))
    spec = schema["properties"]["policy"]["properties"]["test_health_interval_days"]
    assert spec == {"type": "integer", "minimum": 0}
    assert "test_health_interval_days" in {f.name for f in dataclasses.fields(Policy)}


# ==========================================================================
# Item 15 -- the cadence itself
# ==========================================================================

def test_never_carved_fires_a_test_health_carve():
    """None ('never run') means FIRE, not 'no data, skip': turning the knob
    on is itself the request for a first pass."""
    carves = _carves(_inp(days_since_test_health_carve=None))
    assert len(carves) == 1
    assert carves[0].kind == "test-health"
    assert carves[0].project == "demo"


def test_disabled_interval_never_fires_test_health_THE_OPT_IN_DISCRIMINATOR():
    """interval=0 disables. NEGATIVE of the test above with the SAME
    never-carved input: proves the trigger keys on the opt-in knob and not
    merely on 'no test-health carve has ever run'. Item 9 still gets its
    slot, so disabling test-health costs no work carving."""
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0),
                          days_since_test_health_carve=None))
    assert len(carves) == 1
    assert carves[0].kind == "headroom"


def test_overdue_fires():
    assert [c.kind for c in _carves(_inp(days_since_test_health_carve=20.0))] == ["test-health"]


def test_exactly_at_interval_fires_boundary():
    """>= interval, not > -- a 14-day cadence fires ON day 14."""
    assert [c.kind for c in _carves(_inp(days_since_test_health_carve=14.0))] == ["test-health"]


def test_recent_carve_does_not_fire_and_does_not_consume_the_work_slot():
    """3 days into a 14-day cadence: no test-health carve. And the
    DISCRIMINATOR half -- item 9's headroom refill still fires, proving a
    test-health MISS releases the slot rather than swallowing the pass."""
    carves = _carves(_inp(days_since_test_health_carve=3.0))
    assert len(carves) == 1
    assert carves[0].kind == "headroom"


# ==========================================================================
# Ordering -- the design call item 15 documents
# ==========================================================================

def test_test_health_outranks_headroom_refill_for_the_single_slot():
    """Both triggers want the slot (empty queue -> item 9 wants one; never
    carved -> item 15 wants one). EXACTLY ONE CarveDispatch is planned --
    the single-strategic-carver invariant -- and it is the test-health one.
    Paired with test_disabled_interval_... above (identical input, knob off
    -> the ONE carve is 'headroom'), this proves precedence rather than
    just 'something fired'."""
    carves = _carves(_inp(days_since_test_health_carve=None))
    assert len(carves) == 1
    assert carves[0].kind == "test-health"


def test_ready_to_carve_rescope_outranks_test_health():
    """Item 12 (finishing already-started work) still beats item 15, which
    beats item 9. Again exactly one carve, and it is the re-scope: its
    task_id names the origin and its kind stays the default."""
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P01", project="demo",
        state=TaskState.READY_TO_CARVE, since=utc_now(), handoff_path=None,
    )
    carves = _carves(_inp(states={"demo-P01": tsf}, days_since_test_health_carve=None))
    assert len(carves) == 1
    assert carves[0].task_id == "demo-P01"
    assert carves[0].kind == "headroom"


# ==========================================================================
# Shared guards -- item 15 must honor every stop item 9 honors
# ==========================================================================

def test_paused_project_gets_no_test_health_carve():
    """P52's live incident was exactly this gap for the other two triggers:
    a paused project must start NO new agent process of any kind."""
    assert _carves(_inp(project_paused=True, days_since_test_health_carve=None)) == []


def test_exhausted_budget_blocks_test_health_carve():
    assert _carves(_inp(budget_remaining=0, days_since_test_health_carve=None)) == []


def test_no_healthy_frontier_route_blocks_test_health_carve():
    """The carver runs on the frontier-review tier; with no healthy route
    there is nothing to dispatch into."""
    assert _carves(_inp(provider_ok={"route-1": True, "route-review": False},
                        days_since_test_health_carve=None)) == []


def test_carver_already_in_flight_blocks_test_health_carve():
    """The single-carve-authority slot is held by a live CARVER attempt on
    a non-terminal task."""
    attempt = Attempt(
        attempt_id="att-1", role=Role.CARVER, state=AttemptState.RUNNING,
        route=Route(route_id="route-review", cli="fake", model="m", routes_rev="test-rev"),
        started=utc_now(),
    )
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[attempt],
    )
    assert _carves(_inp(states={"carve-demo-1": tsf},
                        days_since_test_health_carve=None)) == []


# ==========================================================================
# daemon._carve_kind -- recovering the kind at outcome time
# ==========================================================================

@pytest.mark.parametrize("notes,expected", [
    ("carve seq=1 authority=branch kind=test-health", "test-health"),
    ("carve seq=1 authority=branch", "headroom"),          # pre-B63 notes
    ("carve seq=1 authority=branch item=B12", "headroom"),  # targeted carve
    ("", "headroom"),
    (None, "headroom"),
])
def test_carve_kind_reads_the_notes_marker(notes, expected):
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, notes=notes,
    )
    assert daemon.Daemon._carve_kind({"carve-demo-1": tsf}, "carve-demo-1") == expected


def test_carve_kind_unknown_task_is_headroom():
    assert daemon.Daemon._carve_kind({}, "nope") == "headroom"
    assert daemon.Daemon._carve_kind({}, None) == "headroom"


# ==========================================================================
# daemon._days_since_test_health_carve -- durable cadence across restarts
# ==========================================================================

def _append_carve_created(project: str, task_id: str, *, kind: str | None,
                          when=None) -> None:
    """Append a TASK_CREATED exactly as _execute_carve_dispatch would: the
    structured `carve_kind` key is present ONLY for a non-headroom carve."""
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.ACTIVE, since=when or utc_now(), handoff_path=None,
    )
    payload: dict = {"statefile": tsf.to_dict()}
    if kind is not None:
        payload["carve_kind"] = kind
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id=task_id,
    )


def test_days_since_is_none_when_never_carved(tmp_state, sample_project):
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_test_health_carve("demo") is None


def test_days_since_ignores_a_plain_carve_THE_MARKER_DISCRIMINATOR(
        tmp_state, sample_project):
    """A headroom carve's TASK_CREATED carries NO carve_kind. If the scan
    keyed on 'a carve task was created' instead of the marker, this would
    return a number and the cadence would reset on every ordinary carve --
    silently never firing. Paired with the test below on identical shape."""
    d = daemon.Daemon({"demo": sample_project.root})
    _append_carve_created("demo", "carve-demo-1", kind=None)
    assert d._days_since_test_health_carve("demo") is None


def test_days_since_finds_the_marked_carve(tmp_state, sample_project):
    d = daemon.Daemon({"demo": sample_project.root})
    _append_carve_created("demo", "carve-demo-1", kind="test-health")
    age = d._days_since_test_health_carve("demo")
    assert age is not None
    assert age < 1.0  # just written


def _ev(task_id: str, *, kind: str | None, age_days: float, sequence: int):
    """A TASK_CREATED at a CONTROLLED age. storage.append_and_apply always
    stamps `now`, so a real append cannot produce an old event -- feeding
    these through a patched iter_events is the only way to exercise the
    age arithmetic and the max() selection at all."""
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload: dict = {"statefile": tsf.to_dict()}
    if kind is not None:
        payload["carve_kind"] = kind
    from nyxloom.types import Event
    return Event(
        schema_version=storage.SCHEMA_VERSION, sequence=sequence,
        timestamp=utc_now() - timedelta(days=age_days), project="demo",
        actor=Actor(ActorKind.TICK, "test"), type=EventType.TASK_CREATED,
        payload=payload, task_id=task_id,
    )


def test_days_since_computes_a_real_age_not_just_presence(
        tmp_state, sample_project, monkeypatch):
    """A 30-day-old marked carve reads as ~30 days. Pins the ARITHMETIC:
    the tests above only distinguish None from 'recent', so a helper that
    returned a constant would satisfy them."""
    monkeypatch.setattr(storage, "iter_events",
                        lambda project: [_ev("carve-demo-1", kind="test-health",
                                             age_days=30.0, sequence=1)])
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_test_health_carve("demo") == pytest.approx(30.0, abs=0.01)


def test_days_since_takes_the_most_recent_of_several(tmp_state, sample_project,
                                                     monkeypatch):
    """Two marked carves 90 and 3 days old -> 3 (the LATEST), regardless of
    log order. A first-match or last-match scan would return 90 here (and a
    90-day-old carve would keep re-firing a 14-day cadence forever)."""
    monkeypatch.setattr(storage, "iter_events", lambda project: [
        _ev("carve-demo-2", kind="test-health", age_days=3.0, sequence=1),
        _ev("carve-demo-1", kind="test-health", age_days=90.0, sequence=2),
    ])
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_test_health_carve("demo") == pytest.approx(3.0, abs=0.01)


def test_days_since_bad_timestamp_is_zero_not_none_FAIL_SAFE(
        tmp_state, sample_project, monkeypatch):
    """A NAIVE (tz-less) timestamp in the log makes the age subtraction raise
    TypeError. Same fail-safe direction as an unreadable log: 'just carved',
    never None -- a corrupt event must not be what authorizes agent spend."""
    ev = _ev("carve-demo-1", kind="test-health", age_days=5.0, sequence=1)
    ev.timestamp = ev.timestamp.replace(tzinfo=None)
    monkeypatch.setattr(storage, "iter_events", lambda project: [ev])
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_test_health_carve("demo") == 0.0


def test_days_since_unreadable_log_is_zero_not_none_FAIL_SAFE(
        tmp_state, sample_project, monkeypatch):
    """The fail-safe direction matters: this value gates spawning a real
    agent process, so an I/O error must mean 'just carved' (don't fire),
    never None ('never carved' -> FIRE). Asserting `is not None` alone
    would pass on the wrong answer, so pin the value."""
    def boom(project):
        raise OSError("event log unreadable")
    monkeypatch.setattr(storage, "iter_events", boom)
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._days_since_test_health_carve("demo") == 0.0


# ==========================================================================
# Carve-outcome cross-talk suppression -- the WORK roadmap's signals
# ==========================================================================

_SUMMARY = {
    "carved": [],
    "review_reflection": "suite is healthy",
    "headroom_estimate": 0,
    "headroom_rationale": "no further test-health areas",
    "outcome": "ROADMAP_EXHAUSTED",
}


def _scripted(monkeypatch, sequence):
    seq = list(sequence)

    def fake(inp):
        if seq:
            return seq.pop(0)
        return []

    monkeypatch.setattr(reconcile, "plan_project", fake)


def _seed_carve_task(project: str, seq: int, worktree: Path, *, notes: str) -> tuple[str, str]:
    task_id = f"carve-{project}-{seq}"
    attempt_id = f"att-carve-{seq}"
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(attempt_id=attempt_id, role=Role.CARVER, state=AttemptState.RUNNING,
                      route=route, started=utc_now(), worktree=str(worktree))
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[attempt],
        notes=notes,
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return task_id, attempt_id


def _run_carve_exit(monkeypatch, cfg, *, notes: str) -> set[str]:
    """Drive one real run_pass over a finished carver attempt and return the
    set of SPEC_ATTENTION reasons it emitted."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root, notes=notes)
    d = cfg.root / cfg.reports_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "CARVE-1.md").write_text(json.dumps(_SUMMARY), encoding="utf-8")
    ad = paths.attempt_dir("demo", attempt_id)
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "receipt.json").write_text(
        json.dumps(Receipt(result=ReceiptResult.DONE, exit_code=0).to_dict()), encoding="utf-8")

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")
    return {e.payload["reason"] for e in storage.iter_events("demo")
            if e.type is EventType.SPEC_ATTENTION}


def test_headroom_carve_still_emits_both_roadmap_signals_THE_NEGATIVE(
        tmp_state, sample_project, monkeypatch):
    """Baseline that must NOT change: an ordinary carve reporting
    ROADMAP_EXHAUSTED with headroom 0 emits both work-roadmap signals.
    Without this, the suppression test below would pass just as well if the
    branch never emitted anything for anyone."""
    reasons = _run_carve_exit(monkeypatch, sample_project,
                              notes="carve seq=1 authority=branch")
    assert reasons == {"headroom-low", "roadmap-exhausted"}


def test_test_health_carve_suppresses_both_roadmap_signals(
        tmp_state, sample_project, monkeypatch):
    """The identical summary from a TEST-HEALTH carve emits NEITHER. These
    are readings of the WORK roadmap's runway, and 'roadmap-exhausted' is
    read straight back by reconcile as roadmap_exhausted_open, which
    throttles item 9. A healthy suite reporting '0 test-debt areas left'
    must not announce the product roadmap is exhausted and stall all work
    carving."""
    reasons = _run_carve_exit(monkeypatch, sample_project,
                              notes="carve seq=1 authority=branch kind=test-health")
    assert reasons == set()


# ==========================================================================
# End-to-end execution -- the marker the whole cadence depends on
# ==========================================================================

@pytest.fixture()
def _siblings(monkeypatch):
    """Local twin of test_daemon.py's patch_siblings, trimmed to the seams a
    carve dispatch touches (never added to conftest.py -- STANDING.md)."""
    from nyxloom import adapters, notify, render, wrapper

    monkeypatch.setattr(adapters, "probe", lambda route: (True, "ok"))
    monkeypatch.setattr(
        adapters, "build_dispatch",
        lambda route, *, handoff_path, worktree, branch, task_id, gate_hint,
        receipt_path, **_kw: (["fake-cli", "--task", task_id], "prompt"))

    def fake_launch(spec):
        Path(spec.attempt_dir).mkdir(parents=True, exist_ok=True)
        return 4242

    monkeypatch.setattr(wrapper, "launch_detached", fake_launch)
    monkeypatch.setattr(render, "render_after_event", lambda registry: paths.www_dir())
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})


def _dispatch_carve(monkeypatch, cfg, kind: str) -> str:
    from nyxloom.config import Routes  # noqa: F401 -- routes file below is the seam
    paths.routes_path().write_text(
        (paths.routes_path().read_text(encoding="utf-8")
         + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"),
        encoding="utf-8")
    _scripted(monkeypatch, [[reconcile.CarveDispatch(project="demo", kind=kind)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")
    return "carve-demo-1"


def test_test_health_dispatch_stamps_the_durable_marker_END_TO_END(
        tmp_state, sample_project, _siblings, monkeypatch):
    """The cadence is only durable if the dispatch actually WRITES the marker
    the age scan reads back. Drive a real test-health CarveDispatch through
    run_pass, then assert the round trip: TASK_CREATED carries carve_kind,
    the notes carry the human-legible twin, _carve_kind recovers it, and
    _days_since_test_health_carve now returns an age instead of None. Without
    this the two halves could drift apart and every pass would re-fire."""
    task_id = _dispatch_carve(monkeypatch, sample_project, "test-health")

    created = next(e for e in storage.iter_events("demo")
                   if e.type is EventType.TASK_CREATED and e.task_id == task_id)
    assert created.payload["carve_kind"] == "test-health"

    tsf = storage.load_state("demo", task_id)
    assert "kind=test-health" in tsf.notes
    assert daemon.Daemon._carve_kind({task_id: tsf}, task_id) == "test-health"

    age = daemon.Daemon({"demo": sample_project.root})._days_since_test_health_carve("demo")
    assert age is not None and age < 1.0

    packet = (paths.attempt_dir("demo", tsf.attempts[0].attempt_id)
              / "packet" / "packet.md").read_text(encoding="utf-8")
    assert "TEST-HEALTH" in packet


def test_headroom_dispatch_stamps_no_marker_THE_NEGATIVE(
        tmp_state, sample_project, _siblings, monkeypatch):
    """The identical path with the default kind writes NO carve_kind and no
    notes marker -- so an ordinary carve cannot reset the test-health cadence
    (which would silently stop it ever firing on a busy project)."""
    task_id = _dispatch_carve(monkeypatch, sample_project, "headroom")

    created = next(e for e in storage.iter_events("demo")
                   if e.type is EventType.TASK_CREATED and e.task_id == task_id)
    assert "carve_kind" not in created.payload

    tsf = storage.load_state("demo", task_id)
    assert "kind=" not in tsf.notes
    assert daemon.Daemon({"demo": sample_project.root})._days_since_test_health_carve(
        "demo") is None


# ==========================================================================
# Packet shape -- the carver must be told what this pass actually is
# ==========================================================================

def _packet(cfg, kind: str) -> str:
    d = daemon.Daemon({"demo": cfg.root})
    return d._build_carve_packet(cfg, "demo", 7, {}, own_task_id="carve-demo-7", kind=kind)


def test_test_health_packet_states_the_pass_and_authorizes_carving_nothing(
        tmp_state, sample_project):
    text = _packet(sample_project, "test-health")
    assert "TEST-HEALTH" in text
    # The anti-busywork authorization is load-bearing: a periodic trigger
    # that must always produce output manufactures work.
    assert '"carved": []' in text
    assert "MILESTONE_COMPLETE" in text
    # And the steer away from the cross-talk outcome.
    assert "Do NOT report outcome ROADMAP_EXHAUSTED" in text


def test_test_health_packet_omits_the_work_sources_THE_DISCRIMINATOR(
        tmp_state, sample_project):
    """A test-health carve is untargeted (no item_id, no rescope), so
    without the kind check it would fall straight through to the WORK
    source section and the carver would refill the queue instead of
    auditing the suite. Paired with its inverse below."""
    text = _packet(sample_project, "test-health")
    assert "Carve sources (v2 SS8)" not in text


def test_headroom_packet_keeps_the_work_sources_and_omits_test_health(
        tmp_state, sample_project):
    text = _packet(sample_project, "headroom")
    assert "Carve sources (v2 SS8)" in text
    assert "TEST-HEALTH" not in text


def test_both_packets_share_the_authority_and_output_contract_tail(
        tmp_state, sample_project):
    """kind shapes the SOURCE section only -- authority/queue/output
    contract are identical, which is what keeps the single-carver
    invariant and the carve-summary parser working unchanged."""
    for kind in ("test-health", "headroom"):
        text = _packet(sample_project, kind)
        assert "## Carve authority:" in text
        assert "## REQUIRED OUTPUT CONTRACT" in text
        assert "## Current queue" in text

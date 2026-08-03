"""CR-02a fault matrix: every authoritative snapshot input, injected at its
real acquisition boundary, must produce ZERO irreversible effects and exactly
ONE actionable durable event.

WHY THESE TESTS LOOK LIKE THIS
------------------------------
The oracle is behavioural, not structural. Every case drives the REAL
`Daemon.run_pass` with the REAL `reconcile.plan_project` over a project whose
one task is genuinely dispatch-eligible -- so the clean pass DOES launch an
agent (`test_clean_pass_dispatches`, the positive control every fault case is
measured against). A fault case then asserts that the same pass launches
nothing.

That is what makes the suite mutation-sensitive rather than hollow: if any
acquisition is reverted to the sentinel it used to return -- `findings = {}`,
`return []`, `return False`, `return None` -- the planner sees a plausible
snapshot again, the dispatch fires again, and the case fails on
`launches == []` before it ever reaches the event assertion.

Determinism (AUTHORING.md 3b):
  * no wall-clock deadline, sleep, or iteration count decides any verdict;
  * `wrapper.launch_detached` is replaced by a recorder, so no process is
    forked and no timing is involved;
  * every fault is injected by monkeypatching the module attribute the daemon
    actually calls (monkeypatch restores it), never by mutating global state
    a sibling test could observe;
  * git is exercised through a real repo the fixture creates in `tmp_path`,
    and git FAULTS are injected by intercepting `daemon.subprocess.run` for
    the one argv under test -- no reliance on ambient repo state.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from nyxloom import (
    adapters, config, daemon, decision_chat, decisions, frontmatter, leases, lint,
    notify, paths, render, snapshot, storage, wrapper,
)
from nyxloom.config import ProjectConfig
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Role, Route, TaskState,
    TaskStateFile, utc_now,
)

TASK_ID = "demo-P01-sample"

# lint's L11/L12 require the BODY to mention the worktree path, the branch, an
# out-of-scope reference, a context section and a BLOCKED marker -- conftest's
# own SAMPLE_HANDOFF body does not, so it never reaches lint_clean=True. This
# body does, which is what makes the positive control a REAL dispatch.
CLEAN_HANDOFF = """---
schema_version: 1
id: demo-P01-sample
project: demo
title: Sample bounded package
tier: flash-high
input_revision: "0000000"
source: {kind: roadmap, ref: docs/ROADMAP.md}
scope:
  touch: ["src/demo/thing.py", "tests/test_thing.py"]
  forbid: ["src/demo/core.py"]
oracles:
  - id: O1
    observable: "pytest tests/test_thing.py::test_bound passes"
    negative: "a value over the limit raises BoundError (test_bound_violation)"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Sample bounded package

Work in the worktree `.worktrees/feat/demo-P01-sample` on branch
`feat/demo-P01-sample`. Touch only the scope files; `src/demo/core.py` is
out of scope (forbid list).

## Context to read first
- docs/ROADMAP.md

## Rules
If a named contract cannot be met as specified, STOP, write
`BLOCKED: <reason>` to the LOG, commit, exit.
"""


# --------------------------------------------------------------------------
# local fixtures (never added to the frozen tests/conftest.py)


@pytest.fixture()
def fault_project(sample_project) -> ProjectConfig:
    """sample_project upgraded to lint-clean, with the referenced paths that
    lint's L7 rule resolves."""
    cfg = sample_project
    (cfg.root / "handoff" / "demo-P01-sample.md").write_text(CLEAN_HANDOFF)
    (cfg.root / "docs" / "ROADMAP.md").write_text("# Roadmap\n- R1 sample\n")
    (cfg.root / "src" / "demo").mkdir(parents=True, exist_ok=True)
    (cfg.root / "src" / "demo" / "core.py").write_text("# frozen\n")
    return ProjectConfig.load(cfg.root)


@pytest.fixture()
def fault_mp():
    """A MonkeyPatch instance dedicated to FAULT injection.

    Deliberately separate from the shared `monkeypatch` fixture: a case has
    to lift its fault before reading the event log back (an unreadable log
    cannot be read back), and `monkeypatch.undo()` on the shared instance
    would also revert `tmp_state`'s NYXLOOM_STATE and the `launches`
    recorder -- silently pointing the read at a different state root. This
    fixture undoes exactly the fault and nothing else, and its own teardown
    restores everything it touched (anti-pattern rule B)."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture()
def launches(monkeypatch) -> list:
    """Record every agent launch instead of forking one.

    This is the effect the whole package exists to prevent, so it is the
    primary observable: a fault case asserts this list stays EMPTY, and the
    positive control asserts it does not."""
    recorded: list = []

    def _record(spec):
        recorded.append(spec)
        return 4242

    monkeypatch.setattr(wrapper, "launch_detached", _record)
    monkeypatch.setattr(adapters, "probe", lambda route: (True, "ok"))
    monkeypatch.setattr(notify, "notify_event", lambda *a, **k: None)
    monkeypatch.setattr(render, "render_after_event", lambda *a, **k: None)
    monkeypatch.setattr(render, "render_all", lambda *a, **k: None)
    return recorded


def _seed_queued(project: str, task_id: str = TASK_ID) -> None:
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.QUEUED, since=utc_now(),
        handoff_path=f"handoff/{task_id}.md",
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()},
        task_id=task_id)


def _events(project: str = "demo"):
    return list(storage.iter_events(project))


def _types(project: str = "demo"):
    return [e.type for e in _events(project)]


#: Events that represent an irreversible effect -- a launched process, a
#: published merge, an authorizing gate verdict. None of these may appear in a
#: pass whose snapshot was not fully available.
IRREVERSIBLE = (
    EventType.ATTEMPT_CREATED,
    EventType.ATTEMPT_PREFLIGHTED,
    EventType.ATTEMPT_STARTED,
    EventType.ATTEMPT_RESUMED,
    EventType.MERGE_RECORDED,
    EventType.GATE_STARTED,
    EventType.GATE_FINISHED,
    EventType.GATE_VERIFY_RECORDED,
    EventType.CARVER_PROPOSAL_ADMITTED,
)


def assert_failed_closed(project: str, launches: list, *, source: str,
                          reason: snapshot.Reason | None = None):
    """The full fail-closed contract, asserted in one place.

    Zero launches, zero irreversible events, exactly ONE
    SNAPSHOT_UNAVAILABLE, and that event names the source with a stable
    reason code and a resolvable provenance."""
    assert launches == [], f"{source}: an unavailable input authorized a launch"
    types = _types(project)
    for banned in IRREVERSIBLE:
        assert banned not in types, f"{source}: irreversible {banned.value} occurred"

    unavailable = [e for e in _events(project)
                   if e.type is EventType.SNAPSHOT_UNAVAILABLE]
    assert len(unavailable) == 1, (
        f"{source}: expected exactly one actionable event, got {len(unavailable)}")

    payload = unavailable[0].payload
    named = {row["name"] for row in payload["unavailable"]}
    assert source in named, f"expected {source} in {sorted(named)}"
    row = next(r for r in payload["unavailable"] if r["name"] == source)
    assert row["class"] == "authoritative"
    assert row["status"] in ("unavailable", "malformed", "stale")
    assert row["reason"], "a fault row must carry a stable reason code"
    if reason is not None:
        assert row["reason"] == reason.value
    assert row["provenance"]["kind"], "a fault row must name its provenance"
    assert payload["digest"], "the payload must carry a dedup digest"
    return payload


# --------------------------------------------------------------------------
# positive control


def test_clean_pass_dispatches(tmp_state, fault_project, launches):
    """THE control every fault case is measured against.

    If this stops dispatching, every "no launch happened" assertion below
    becomes vacuous -- so this test is what keeps the matrix honest."""
    _seed_queued("demo")
    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")

    assert len(launches) == 1
    types = _types()
    assert EventType.ATTEMPT_CREATED in types
    assert EventType.ATTEMPT_PREFLIGHTED in types
    assert EventType.SNAPSHOT_UNAVAILABLE not in types
    assert storage.load_state("demo", TASK_ID).state is TaskState.ACTIVE


# --------------------------------------------------------------------------
# the authoritative fault matrix
#
# Each entry injects at the boundary the daemon actually calls. `patch` takes
# (monkeypatch, cfg) so a case can reach the project when it needs to.


class _Boom(RuntimeError):
    pass


def _raiser(*_a, **_k):
    raise _Boom("simulated source failure")


def _patch_config(mp, cfg):
    mp.setattr(config.ProjectConfig, "load", staticmethod(_raiser))


def _patch_state(mp, cfg):
    mp.setattr(storage, "list_states", _raiser)


def _patch_event_log(mp, cfg):
    mp.setattr(storage, "iter_events", _raiser)


def _patch_decision_reconcile(mp, cfg):
    mp.setattr(decisions, "reconcile_decisions", _raiser)


def _patch_decisions_inbox(mp, cfg):
    mp.setattr(decisions, "parse_inbox", _raiser)


def _patch_decisions_open(mp, cfg):
    mp.setattr(decisions, "open_ids", _raiser)


def _patch_routes(mp, cfg):
    mp.setattr(config.Routes, "load", staticmethod(_raiser))


def _patch_handoffs(mp, cfg):
    mp.setattr(frontmatter, "discover_handoffs", _raiser)


def _patch_lint(mp, cfg):
    mp.setattr(lint, "lint_project", _raiser)


def _patch_leases(mp, cfg):
    mp.setattr(leases, "holder_info", _raiser)


def _fail_git(mp, marker: str):
    """Make ONE git subcommand fail, leaving every other subprocess real.

    Injecting at `daemon.subprocess.run` is the actual acquisition boundary
    for the git facts -- a stub of `_merged_branches`/`_head_revision` would
    test the fan-in against itself."""
    real = subprocess.run

    class _Fail:
        returncode = 128
        stdout = ""
        stderr = "fatal: simulated git failure"

    def _run(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and marker in list(cmd):
            return _Fail()
        return real(cmd, *a, **k)

    mp.setattr(daemon.subprocess, "run", _run)


def _patch_merged_branches(mp, cfg):
    _fail_git(mp, "--merged")


def _patch_head_revision(mp, cfg):
    _fail_git(mp, "rev-parse")


AUTHORITATIVE_FAULTS = [
    ("config", _patch_config, snapshot.Reason.SOURCE_ERROR),
    ("state", _patch_state, snapshot.Reason.SOURCE_ERROR),
    ("event_log", _patch_event_log, snapshot.Reason.SOURCE_ERROR),
    ("decision_reconcile", _patch_decision_reconcile, snapshot.Reason.SOURCE_ERROR),
    ("decisions_inbox", _patch_decisions_inbox, snapshot.Reason.SOURCE_ERROR),
    ("decisions_open", _patch_decisions_open, snapshot.Reason.SOURCE_ERROR),
    ("routes", _patch_routes, snapshot.Reason.SOURCE_ERROR),
    ("handoffs", _patch_handoffs, snapshot.Reason.SOURCE_ERROR),
    ("lint", _patch_lint, snapshot.Reason.SOURCE_ERROR),
    ("leases", _patch_leases, snapshot.Reason.PROBE_FAILED),
    ("git_merged_branches", _patch_merged_branches, snapshot.Reason.PROBE_FAILED),
    ("git_head_revision", _patch_head_revision, snapshot.Reason.PROBE_FAILED),
]


@pytest.mark.parametrize("source, patch, reason",
                          AUTHORITATIVE_FAULTS,
                          ids=[f[0] for f in AUTHORITATIVE_FAULTS])
def test_authoritative_fault_yields_zero_effects_and_one_event(
        tmp_state, fault_project, launches, fault_mp, source, patch, reason):
    _seed_queued("demo")
    patch(fault_mp, fault_project)

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 0

    # An unreadable event LOG cannot also be read back to check the event, so
    # the fault is lifted first -- the append itself still had to succeed
    # DURING the fault, which is what the assertion proves.
    fault_mp.undo()
    assert_failed_closed("demo", launches, source=source, reason=reason)
    # The task did not move: fail-closed means parked, not failed.
    assert storage.load_state("demo", TASK_ID).state is TaskState.QUEUED


def test_lint_exception_can_never_produce_lint_clean_true(
        tmp_state, fault_project, launches, fault_mp):
    """The named CR-02 defect, asserted at the level that matters.

    `Daemon._build_input` used to answer a lint exception with an empty
    finding set, so `has_blocking([])` was False and EVERY task in the
    project was marked lint_clean=True -- an unreadable lint result
    authorizing dispatch. Reverting that one line makes this test fail on the
    launch assertion, before it reaches the event."""
    _seed_queued("demo")
    fault_mp.setattr(lint, "lint_project", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")

    assert launches == []
    fault_mp.undo()
    assert_failed_closed("demo", launches, source="lint")


def test_a_normal_empty_lint_finding_set_still_permits_progress(
        tmp_state, fault_project, launches, monkeypatch):
    """The negative control for the test above, and the distinction the whole
    descriptor vocabulary exists to preserve: a CLEAN lint result is also
    "no findings", and it must stay usable. If fail-closed were implemented
    by treating emptiness as suspicious, this dispatch would stop working."""
    _seed_queued("demo")
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")

    assert len(launches) == 1
    assert EventType.SNAPSHOT_UNAVAILABLE not in _types()


def test_malformed_receipt_is_not_the_same_as_an_absent_receipt(
        tmp_state, fault_project, launches, monkeypatch):
    """Receipts: the sentinel collapse.

    A half-written receipt.json used to be stored as the same `None` an
    ABSENT receipt gets, and downstream `None` means "the attempt has not
    reported yet" -- so a truncated receipt read as a still-running attempt
    and its real verdict was discarded forever. It is now a MALFORMED
    authoritative fault naming the attempt."""
    attempt_id = "att-malformed"
    att = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER,
                  state=AttemptState.EXITED,
                  route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
                  started=utc_now(), ended=utc_now())
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="t-receipt", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[att])
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()},
        task_id="t-receipt")

    attempt_dir = paths.attempt_dir("demo", attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "receipt.json").write_text('{"result": "do', encoding="utf-8")

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 0

    payload = assert_failed_closed("demo", launches, source="receipts",
                                    reason=snapshot.Reason.MALFORMED_VALUE)
    row = next(r for r in payload["unavailable"] if r["name"] == "receipts")
    assert row["status"] == "malformed"
    assert attempt_id in row["detail"], "the fault must name the offending attempt"


def test_absent_receipt_remains_a_clean_not_yet_reported_reading(
        tmp_state, fault_project, launches):
    """Negative control for the case above: no receipt on disk is NOT a
    fault. Without this the malformed assertion could be satisfied by
    flagging every attempt that has not written a receipt yet."""
    att = Attempt(attempt_id="att-running", role=Role.IMPLEMENTER,
                  state=AttemptState.RUNNING,
                  route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
                  started=utc_now())
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="t-noreceipt", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[att])
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()},
        task_id="t-noreceipt")

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")
    assert EventType.SNAPSHOT_UNAVAILABLE not in _types()


# --------------------------------------------------------------------------
# stale / wrong-identity data is unavailable, never truthy by accident


def test_a_head_revision_that_resolves_to_nothing_is_unavailable_not_none(
        tmp_state, fault_project, launches, fault_mp):
    """`git rev-parse` can exit 0 with empty stdout. The old helper returned
    `sha or None`, and None fed the drift-guard's "no drift" fail-safe -- so
    an empty answer silently asserted every premise was still current."""
    real = subprocess.run

    class _Empty:
        returncode = 0
        stdout = "\n"
        stderr = ""

    def _run(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and "rev-parse" in list(cmd):
            return _Empty()
        return real(cmd, *a, **k)

    fault_mp.setattr(daemon.subprocess, "run", _run)
    _seed_queued("demo")

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 0
    fault_mp.undo()
    assert_failed_closed("demo", launches, source="git_head_revision")


def test_a_receipt_bound_to_a_wrong_attempt_never_becomes_truthy(
        tmp_state, fault_project):
    """Identity, not content: `_attempt_has_review_recorded` answers about a
    specific attempt id, and an unreadable log must not answer "no binding"
    (which authorizes re-consuming a verdict). The wrong-attempt case is a
    genuine False; the unreadable case is a typed fault. The two are
    asserted together so neither can be satisfied by the other."""
    d = daemon.Daemon({"demo": fault_project.root})
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.REVIEW_RECORDED, payload={"result": "approved"},
        task_id="t-a", attempt_id="att-real")

    assert d._exit._attempt_has_review_recorded("demo", "att-real") is True
    assert d._exit._attempt_has_review_recorded("demo", "att-other") is False


# --------------------------------------------------------------------------
# advisory degradation: allowed progress continues, visibly


def test_provider_probe_failure_degrades_visibly_and_still_progresses(
        tmp_state, fault_project, launches, fault_mp):
    """An advisory fault must not stop the pass -- but it must be recorded.

    A probe that cannot run marks the route unusable, which only ever
    REMOVES a dispatch option. The pass therefore still runs and the allowed
    progress (the CARVED->QUEUED lifecycle move for a newly-discovered
    handoff) still happens; the degradation shows up as one typed
    SNAPSHOT_DEGRADED naming the source."""
    fault_mp.setattr(adapters, "probe", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")

    types = _types()
    assert EventType.SNAPSHOT_UNAVAILABLE not in types
    assert EventType.TASK_CREATED in types, "allowed progress must continue"

    degraded = [e for e in _events() if e.type is EventType.SNAPSHOT_DEGRADED]
    assert len(degraded) == 1
    names = {row["name"] for row in degraded[0].payload["degraded"]}
    assert "provider_probe" in names
    row = next(r for r in degraded[0].payload["degraded"]
               if r["name"] == "provider_probe")
    assert row["class"] == "advisory"
    assert row["reason"] == snapshot.Reason.PROBE_FAILED.value
    assert "fake-cli" in row["detail"]


def test_an_unparsable_handoff_degrades_without_stopping_the_others(
        tmp_state, fault_project, launches):
    """A handoff whose frontmatter will not parse cannot become a task, which
    is already fail-closed for THAT package. What was missing is that it
    vanished silently. It is now a bounded advisory degradation naming the
    file -- and the sibling handoff still dispatches, which is the "allowed
    progress continues" half."""
    _seed_queued("demo")
    (fault_project.root / "handoff" / "broken.md").write_text(
        "---\nnot: [valid frontmatter\n", encoding="utf-8")

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")

    assert len(launches) == 1, "the healthy handoff must still dispatch"
    degraded = [e for e in _events() if e.type is EventType.SNAPSHOT_DEGRADED]
    assert len(degraded) == 1
    row = next(r for r in degraded[0].payload["degraded"]
               if r["name"] == "handoff_frontmatter")
    assert row["status"] == "malformed"
    assert "broken.md" in row["detail"]


def test_advisory_degradation_is_deduplicated_across_passes(
        tmp_state, fault_project, launches, fault_mp):
    """A persistent advisory fault records ONCE (onset), not once per pass --
    the notification-storm shape watchdog.py exists to prevent -- and records
    once more on RECOVERY so the last durable word is not a stale
    degradation. Three passes, two events."""
    d = daemon.Daemon({"demo": fault_project.root})
    fault_mp.setattr(adapters, "probe", _raiser)
    d.run_pass("demo")
    d.run_pass("demo")
    onset = [e for e in _events() if e.type is EventType.SNAPSHOT_DEGRADED]
    assert len(onset) == 1, "a persistent degradation must not re-emit per pass"
    assert onset[0].payload["degraded"], "onset carries the degradation"

    # Recovery. The probe memo caches the failed probe for PROBE_TTL_SECONDS,
    # so clear it -- this is the daemon's own disposable memory, not a clock
    # the assertion depends on.
    d._probe_memo.clear()
    fault_mp.undo()          # restores the `launches` fixture's healthy probe
    d.run_pass("demo")
    all_degraded = [e for e in _events() if e.type is EventType.SNAPSHOT_DEGRADED]
    assert len(all_degraded) == 2, "recovery must be recorded exactly once"
    assert all_degraded[-1].payload["degraded"] == [], (
        "the recovery event must show an empty degradation set, not a stale one")


# --------------------------------------------------------------------------
# multiple simultaneous faults, ordering, replay, recovery


def test_multiple_simultaneous_faults_collapse_into_one_ordered_event(
        tmp_state, fault_project, launches, fault_mp):
    """Three authoritative sources fail at once. That is ONE actionable
    event listing all three by name in a deterministic order -- not three
    events, and not one that mentions whichever failed first."""
    _seed_queued("demo")
    fault_mp.setattr(lint, "lint_project", _raiser)
    fault_mp.setattr(decisions, "open_ids", _raiser)
    fault_mp.setattr(leases, "holder_info", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 0
    fault_mp.undo()

    assert launches == []
    unavailable = [e for e in _events() if e.type is EventType.SNAPSHOT_UNAVAILABLE]
    assert len(unavailable) == 1
    names = [row["name"] for row in unavailable[0].payload["unavailable"]]
    assert names == sorted(names), "fault rows must be deterministically ordered"
    assert set(names) == {"decisions_open", "leases", "lint"}
    assert unavailable[0].payload["summary"] == (
        "decisions_open:unavailable:source-error; "
        "leases:unavailable:probe-failed; "
        "lint:unavailable:source-error")


def test_the_same_multi_fault_pass_replays_to_the_same_aggregate(
        tmp_state, fault_project, launches, fault_mp):
    """Replay equivalence: rebuilding the audit from the durable payload
    yields the same failure set, the same summary and the same digest, so an
    operator reading the log a week later sees exactly what the pass saw."""
    _seed_queued("demo")
    fault_mp.setattr(lint, "lint_project", _raiser)
    fault_mp.setattr(leases, "holder_info", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")
    d.run_pass("demo")
    fault_mp.undo()

    payloads = [e.payload for e in _events()
                if e.type is EventType.SNAPSHOT_UNAVAILABLE]
    assert len(payloads) == 2, "one actionable event per affected pass"
    first, second = (snapshot.SnapshotAudit.from_payload(p) for p in payloads)
    assert first.digest() == second.digest()
    assert first.summary() == second.summary()
    assert not first.permits_effects and not second.permits_effects
    assert [i.name for i in first.failures()] == ["leases", "lint"]


def test_recovery_on_the_next_clean_pass_leaves_no_false_latch(
        tmp_state, fault_project, launches, fault_mp):
    """The fault is parked state, not terminal state. Once the source is
    healthy again the very next pass dispatches normally, and nothing about
    the closed pass persists as a false-clean or false-closed latch."""
    _seed_queued("demo")
    fault_mp.setattr(lint, "lint_project", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")
    assert launches == []
    assert storage.load_state("demo", TASK_ID).state is TaskState.QUEUED

    fault_mp.undo()          # the source is healthy again; nothing else changes

    d.run_pass("demo")
    assert len(launches) == 1
    assert storage.load_state("demo", TASK_ID).state is TaskState.ACTIVE
    # Exactly one closed pass is recorded; recovery does not retroactively
    # erase it, and does not add another.
    assert len([e for e in _events()
                if e.type is EventType.SNAPSHOT_UNAVAILABLE]) == 1


def test_the_effect_boundary_refuses_even_if_planning_is_bypassed(
        tmp_state, fault_project, monkeypatch):
    """`run_pass` refuses to plan on a failed audit, so the effect-boundary
    check is belt-and-braces today. It becomes load-bearing when CR-05 moves
    execution behind a handler registry that can be driven from elsewhere, so
    it is asserted directly: with a failed audit in hand, `_dispatch_admissible`
    refuses every kind and names the reason."""
    d = daemon.Daemon({"demo": fault_project.root})
    states = storage.list_states("demo")
    assert d._dispatch_admissible("demo", fault_project, states, "dispatch")[0] is True

    d._snapshot_audit["demo"] = snapshot.SnapshotAudit((
        snapshot.SnapshotInput.failed(
            "lint", snapshot.InputClass.AUTHORITATIVE, snapshot.Reason.SOURCE_ERROR,
            provenance=snapshot.Provenance("lint", "demo")),))
    for kind in ("dispatch", "resume", "review", "carve", "self-review"):
        ok, reason = d._dispatch_admissible("demo", fault_project, states, kind)
        assert ok is False, f"{kind} was admitted on an unavailable snapshot"
        assert reason.startswith("snapshot-unavailable:")
        assert "lint" in reason


def test_the_audit_does_not_leak_between_passes(tmp_state, fault_project, launches,
                                                 fault_mp):
    """A pass's audit describes THAT pass. Leaving it behind would let a
    later operator-initiated effect consult a stale verdict -- refusing work
    because of a fault that has since cleared."""
    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")
    assert "demo" not in d._snapshot_audit

    fault_mp.setattr(lint, "lint_project", _raiser)
    d.run_pass("demo")
    assert "demo" not in d._snapshot_audit


def test_the_reconcile_input_carries_the_audit_for_downstream_packages(
        tmp_state, fault_project, launches, fault_mp):
    """The classification is centralized: a downstream consumer reads ONE
    already-classified audit off the input instead of re-deriving
    authoritative-vs-advisory policy per call site."""
    fault_mp.setattr(adapters, "probe", _raiser)
    d = daemon.Daemon({"demo": fault_project.root})
    builder = snapshot.SnapshotBuilder()
    inp = d._build_input("demo", fault_project, storage.list_states("demo"),
                          builder=builder)
    assert inp is not None
    assert inp.snapshot_audit.permits_effects
    assert [i.name for i in inp.snapshot_audit.degradations()] == ["provider_probe"]
    # Domain names stay explicit -- this is not an Any-valued bag.
    assert inp.snapshot_audit.get("lint").ok
    assert inp.snapshot_audit.get("leases").ok
    assert inp.snapshot_audit.get("git_head_revision").ok
    assert inp.snapshot_audit.get("receipts").ok


def test_bounded_diagnostics_never_carry_raw_output_or_secrets(
        tmp_state, fault_project, launches, fault_mp):
    """Diagnostics are bounded and redacted before they reach the durable
    log. A provider CLI's stderr routinely quotes the command line, and that
    carries tokens."""
    def leaky(*_a, **_k):
        raise RuntimeError("probe failed: curl -H 'Authorization: Bearer sk-SECRET' "
                            + "x" * 4000)

    fault_mp.setattr(lint, "lint_project", leaky)
    _seed_queued("demo")

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")
    fault_mp.undo()

    payload = assert_failed_closed("demo", launches, source="lint")
    detail = next(r for r in payload["unavailable"] if r["name"] == "lint")["detail"]
    assert "sk-SECRET" not in detail
    assert "<redacted>" in detail
    assert len(detail) <= snapshot.DETAIL_MAX
    assert json.dumps(payload)  # the whole payload stays JSON-serialisable


# --------------------------------------------------------------------------
# the fan-in's own edges: paths reached only when a fault lands beside
# another fault, beside an append, or beside a partially-usable source.
# Each one is a place the fail-closed pass still has work to do correctly.


_OPEN_INBOX = (
    "# Decisions inbox\n\n"
    "## D-001 · 2026-08-03 · carver · OPEN\n\n"
    "Should the sample package ship?\n"
)


def _seed_open_decision(cfg: ProjectConfig) -> None:
    """An inbox with one OPEN decision, so the pass's FIRST act is to append
    a DECISION_OPENED before any later acquisition can fail."""
    path = cfg.root / cfg.decisions_inbox
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_OPEN_INBOX, encoding="utf-8")


def test_a_fail_closed_pass_still_renders_what_it_already_appended(
        tmp_state, fault_project, launches, fault_mp, monkeypatch):
    """A pass can append BEFORE it fails closed.

    `_reconcile_decisions` runs on phase-A facts and records that a human
    opened D-001; a later authoritative fault then stops the pass. The
    decision is durable either way, but the dashboard is rendered from
    events, so skipping the render on the fail-closed return would leave the
    operator looking at a page that does not mention the decision they just
    opened -- while the daemon has stopped making progress *because* of a
    fault they also cannot see. Rendering is the only channel that shows
    both."""
    rendered: list = []
    monkeypatch.setattr(render, "render_after_event", lambda *a, **k: rendered.append(a))
    _seed_open_decision(fault_project)
    _seed_queued("demo")
    fault_mp.setattr(lint, "lint_project", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 0
    fault_mp.undo()

    assert_failed_closed("demo", launches, source="lint")
    assert EventType.DECISION_OPENED in _types(), "the appended decision was lost"
    assert rendered, "a fail-closed pass that appended events skipped the render"


def test_a_feedback_push_failure_degrades_without_undoing_the_decision(
        tmp_state, fault_project, launches, fault_mp):
    """The DECISION_OPENED is already durable when the extra feedback-channel
    push is attempted, so a push failure must not be able to unwind it -- and
    must not fail the pass either, because the decision is recorded and the
    operator can still see it in the dashboard.

    It is still not silent: N failed pushes collapse into ONE advisory
    descriptor (descriptor names are unique per audit, and N failures of one
    channel are one degradation of that channel), and the dedup cursor is
    still advanced so the next pass does not re-fire the same DECISION_OPENED
    forever."""
    _seed_open_decision(fault_project)
    _seed_queued("demo")
    fault_mp.setattr(decision_chat, "notify_decision_opened", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")
    fault_mp.undo()

    types = _types()
    assert EventType.DECISION_OPENED in types
    assert EventType.SNAPSHOT_UNAVAILABLE not in types, "an advisory push failed the pass"

    degraded = [e for e in _events() if e.type is EventType.SNAPSHOT_DEGRADED]
    assert len(degraded) == 1
    row = next(r for r in degraded[0].payload["degraded"]
               if r["name"] == "decision_feedback_push")
    assert row["class"] == "advisory"
    assert "D-001" in row["detail"] and "_Boom" in row["detail"]

    # The dedup cursor advanced: a second pass does not re-open D-001.
    before = len([e for e in _events() if e.type is EventType.DECISION_OPENED])
    d.run_pass("demo")
    after = len([e for e in _events() if e.type is EventType.DECISION_OPENED])
    assert after == before, "the seen-status cursor was not advanced"


def test_an_unreadable_store_cannot_stop_the_pass_from_failing_closed(
        tmp_state, fault_project, launches, fault_mp):
    """The last-resort path: the config is unavailable AND the event store
    cannot record that fact.

    There is no channel left but the log line, and nothing this method could
    do would make the pass less closed -- it has already executed zero
    effects. What must NOT happen is the append error escaping, because
    run_pass's own `except` would then classify a fail-closed pass as a
    TICK_ERROR and hide which authoritative source was actually missing."""
    _seed_queued("demo")
    fault_mp.setattr(config.ProjectConfig, "load", staticmethod(_raiser))
    fault_mp.setattr(storage, "append_and_apply", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 0
    fault_mp.undo()

    assert launches == []
    # Neither the fail-closed event nor a TICK_ERROR could be written -- the
    # store was the thing that was broken. The contract that survives is
    # "zero effects", and it held.
    assert EventType.SNAPSHOT_UNAVAILABLE not in _types()
    for banned in IRREVERSIBLE:
        assert banned not in _types()


def test_build_input_acquires_its_own_event_log_when_called_alone(
        tmp_state, fault_project, fault_mp):
    """`_build_input` is reachable without a pass around it (tests, and every
    future caller CR-05's handler registry introduces). Called that way it
    acquires the log itself and returns None on a fault -- never a
    ReconcileInput built over a silently empty history, which is what an
    `events or []` default would have produced."""
    d = daemon.Daemon({"demo": fault_project.root})
    states = storage.list_states("demo")
    fault_mp.setattr(storage, "iter_events", _raiser)

    assert d._build_input("demo", fault_project, states) is None

    fault_mp.undo()
    assert d._build_input("demo", fault_project, states) is not None


#: Every AUTHORITATIVE input the fan-in may acquire, by descriptor name.
#: Closed on purpose -- see the test below for what widening it obliges.
DECLARED_AUTHORITATIVE = {
    "config", "state", "event_log",
    "decision_reconcile", "decisions_inbox", "decisions_open",
    "routes", "handoffs", "lint", "leases",
    "git_merged_branches", "git_head_revision", "receipts",
}


def test_the_authoritative_input_set_is_closed(
        tmp_state, fault_project, launches, fault_mp):
    """The structural invariant that replaced an unreachable runtime guard.

    `_build_input` checks `permits_effects` once, immediately before the
    `.require()` calls that consume the authoritative values. Everything
    acquired below that point -- provider probes, handoff frontmatter,
    carve-proposal artifacts -- is ADVISORY by construction, which is what
    makes one guard sufficient. A trailing second guard used to sit at the
    end of the method; it could never fire, so it was protection in
    appearance only and a line no test could honestly cover.

    This asserts the property that made it redundant. Classify any
    acquisition below the guard as authoritative and this fails, which is
    the signal to move the guard rather than to widen the list. It is
    checked over a REAL clean pass so the set is the one the daemon actually
    produces, not one re-derived here."""
    _seed_queued("demo")
    d = daemon.Daemon({"demo": fault_project.root})
    d.run_pass("demo")

    audit = d._build_input("demo", fault_project, storage.list_states("demo")).snapshot_audit
    acquired_authoritative = {i.name for i in audit.inputs if i.authoritative}

    assert acquired_authoritative <= DECLARED_AUTHORITATIVE, (
        "a new AUTHORITATIVE snapshot input appeared: "
        f"{sorted(acquired_authoritative - DECLARED_AUTHORITATIVE)}. If it is "
        "acquired below _build_input's permits_effects guard, the guard no "
        "longer covers every authoritative input -- move it, do not just add "
        "the name here.")

    # Non-vacuous: the acquisitions that DO happen below the guard are
    # reachable and really are advisory. A probe fault is the one that fires
    # furthest down, so it is the one that proves the single guard is
    # sufficient rather than merely untriggered.
    fault_mp.setattr(adapters, "probe", _raiser)
    # The clean pass above already probed and memoized a healthy answer, and
    # the memo answers for PROBE_TTL_SECONDS. Clearing it is what makes the
    # fault reach the acquisition -- a TTL wait here would be a wall-clock
    # budget deciding the verdict, which STANDING.md L20 forbids outright.
    d._probe_memo.clear()
    below_the_guard = d._build_input(
        "demo", fault_project, storage.list_states("demo")).snapshot_audit
    fault_mp.undo()

    probe = below_the_guard.get("provider_probe")
    assert probe is not None, "the below-the-guard acquisition never ran"
    assert not probe.authoritative
    assert below_the_guard.permits_effects, (
        "an acquisition below the guard closed the pass -- the guard above it "
        "cannot have covered it")


def test_blank_lines_in_the_merged_branch_list_are_not_branch_names(
        tmp_state, fault_project, launches, fault_mp):
    """`git branch --merged` output is line-oriented and its last line is
    empty. An empty string entering the merged-branch set would compare equal
    to nothing real -- but a task whose branch field is empty (a
    not-yet-branched task) would then read as MERGED, which authorizes the
    single most irreversible transition the daemon has."""
    real = subprocess.run

    class _Padded:
        returncode = 0
        stdout = "  main\n\n* feat/demo-P01-sample\n   \n"
        stderr = ""

    def _run(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and "--merged" in list(cmd):
            return _Padded()
        return real(cmd, *a, **k)

    fault_mp.setattr(daemon.subprocess, "run", _run)
    d = daemon.Daemon({"demo": fault_project.root})
    merged = d._merged_branches(fault_project, storage.list_states("demo"))
    fault_mp.undo()

    assert merged.ok
    names = merged.require()
    assert "" not in names, "an empty line became a branch name"
    assert "main" in names and "feat/demo-P01-sample" in names


def test_a_crashing_watchdog_removes_a_safety_net_without_closing_the_gate(
        tmp_state, fault_project, launches, fault_mp):
    """The one detector whose failure must NOT fail the pass closed.

    `detect_runaways` only ever SUPPRESSES actions the planner already
    decided are correct, so a detector crash removes a safety net rather than
    opening a gate -- treating it as authoritative would let a buggy detector
    halt the whole factory. It is still logged, because a permanently
    crashing watchdog is a silently disabled watchdog, which is exactly
    RISK-007's shape."""
    _seed_queued("demo")
    fault_mp.setattr(daemon.watchdog, "detect_runaways", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    executed = d.run_pass("demo")
    fault_mp.undo()

    assert executed >= 1, "a detector crash suppressed the planned action"
    assert launches, "the dispatch the positive control proves must still happen"
    assert EventType.SNAPSHOT_UNAVAILABLE not in _types()


# --------------------------------------------------------------------------
# CR-02b: reporting a failure must never itself become one.
#
# The handlers below are the pass's containment net: they run where the
# effect they guard has ALREADY failed. Each was classified by CR-02b's
# census, and a census tag on an untested handler is a claim nobody checked
# -- so each one is driven here.


def _fail_execute(mp) -> None:
    """Make the one planned action raise, without touching the fan-in.

    Injected at `Daemon._execute` because that is the boundary the per-action
    isolation handler wraps; a fault inside the planner would be caught by the
    pass-level net instead and prove nothing about this one."""
    mp.setattr(daemon.Daemon, "_execute", lambda *a, **k: _raiser())


def test_an_action_failure_is_contained_and_the_pass_survives_a_dead_transport(
        tmp_state, fault_project, launches, fault_mp):
    """Per-action isolation, with the notification channel also broken.

    One action raising must not abort the pass -- the remaining actions are
    independent of it -- and the TICK_ERROR that records it must still land
    even though notifying about it fails. Containment, not authority: the
    effect already failed, so neither handler can turn a refusal into a
    permission."""
    _seed_queued("demo")
    _fail_execute(fault_mp)
    fault_mp.setattr(notify, "notify_event", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 1, "the pass reported the action it planned"
    fault_mp.undo()

    assert launches == []
    errors = [e for e in _events() if e.type is EventType.TICK_ERROR]
    assert len(errors) == 1, "the contained failure was not recorded"
    assert "_Boom" in errors[0].payload["error"]
    assert EventType.SNAPSHOT_UNAVAILABLE not in _types(), (
        "an action failure was misreported as an unavailable snapshot")


def test_an_action_failure_that_cannot_even_be_recorded_still_does_not_escape(
        tmp_state, fault_project, launches, fault_mp):
    """The store is the thing that is broken, so there is nowhere left to
    write the TICK_ERROR. What must NOT happen is the append error escaping
    the action loop: it would abort every remaining action in the pass on
    behalf of a failure that had already been contained."""
    _seed_queued("demo")
    _fail_execute(fault_mp)
    fault_mp.setattr(storage, "append_and_apply", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 1
    fault_mp.undo()

    assert launches == []
    assert EventType.TICK_ERROR not in _types(), "the store was supposed to be down"


def test_a_pass_level_fault_is_reported_even_when_the_transport_is_dead(
        tmp_state, fault_project, launches, fault_mp):
    """The pass-level net, distinct from the fail-closed return above.

    SNAPSHOT_UNAVAILABLE names WHICH authoritative source was missing;
    TICK_ERROR only knows that something outside the fan-in raised, which is
    why CR-16 watches its streaks rather than treating one as actionable. A
    dead notification channel must not stop the record being written."""
    _seed_queued("demo")
    fault_mp.setattr(daemon.reconcile, "plan_project", _raiser)
    fault_mp.setattr(notify, "notify_event", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 0
    fault_mp.undo()

    assert launches == []
    errors = [e for e in _events() if e.type is EventType.TICK_ERROR]
    assert len(errors) == 1
    assert "_Boom" in errors[0].payload["error"]
    assert EventType.SNAPSHOT_UNAVAILABLE not in _types(), (
        "a planner fault was misreported as an unavailable snapshot -- the two "
        "call for different operator responses")


def test_a_pass_level_fault_with_no_store_left_returns_zero_rather_than_raising(
        tmp_state, fault_project, launches, fault_mp):
    """The last containment layer. If this raised, `run_pass`'s caller (the
    reconcile loop) would see an exception from a method whose whole contract
    is to return a count -- and the loop would take the project down with
    it rather than retrying on the next tick."""
    _seed_queued("demo")
    fault_mp.setattr(daemon.reconcile, "plan_project", _raiser)
    fault_mp.setattr(storage, "append_and_apply", _raiser)

    d = daemon.Daemon({"demo": fault_project.root})
    assert d.run_pass("demo") == 0
    fault_mp.undo()

    assert launches == []
    for banned in IRREVERSIBLE:
        assert banned not in _types()

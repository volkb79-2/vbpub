"""CR-00 semantic characterization corpus.

The JSON fixture is a compact behavioural boundary: ordered events, projected
state, artifact bindings, recorded merge evidence, the planner's ordered
actions, and a wait classification.  It does not snapshot log text, generated
prompts, or implementation method calls.  Core refactors (CR-05..CR-07) compare
against this corpus before replacing the planner/executor seams.

Three rules keep the corpus from going hollow, and every oracle below is
written to obey them:

1. **Evidence is read from events, never inferred from configuration.**  A
   merge is backed by evidence only when the recorded event stream contains a
   verdict/gate result *before* the merge, and (for a review verdict) bound to
   an attempt the projection actually knows.  A pipeline that merely *contains*
   a ``review_independent`` stage proves nothing -- `_merge_evidence` never
   looks at `stages.compose`.
2. **Progress/wait is computed, never read back.**  `_classify_wait` is an
   independently specified oracle (WAIT_TAXONOMY) whose inputs are production
   artifacts only: the projected `TaskStateFile`, `plan_project`'s actions,
   `plan_project`'s own `trace` breadcrumbs (which carry reconcile's verbatim
   `dispatch_eligible` reason strings), and recorded `NEEDS_OPERATOR` events.
   The property test asserts on the *computed* output; the fixture's recorded
   value is only a corruption tripwire, checked separately.
3. **Inputs are fixture data, outputs are production data.**  Nothing in this
   module branches on a scenario's name.  Every knob that shapes a plan
   (provider health, open decisions, attempts on record, policy caps) is a
   declared field of the scenario, so a reader can see why an expectation holds.

The five-band ladder fixtures are classified INVENTORY, not tests: they are
``executable: false`` with a machine-checked activation contract naming the
production vocabulary CR-09 must add.  `test_future_band_inventory_activates_
when_production_vocabulary_lands` fails -- loudly and with instructions -- the
moment that vocabulary exists, so the inventory cannot rot into permanent
decoration.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom import reconcile as reconcile_mod
from nyxloom.config import MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import (
    ReconcileInput,
    Transition,
    dispatch_eligible,
    plan_project,
)
from nyxloom.stages import PRESETS, compose
from nyxloom.storage import apply_event
from nyxloom.types import (
    TERMINAL_TASK_STATES,
    Actor,
    ActorKind,
    Attempt,
    AttemptState,
    Event,
    EventType,
    Frontmatter,
    GateResult,
    Receipt,
    ReceiptResult,
    Role,
    Route,
    Scope,
    Source,
    TaskState,
    TaskStateFile,
)


FIXED_TIME = datetime(2026, 8, 2, tzinfo=timezone.utc)
TASK_ID = "demo-P01"
HANDOFF_PATH = "handoff/demo-P01.md"
ROUTE_ID = "route-1"
TIER = "flash-high"
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "core_characterization_v1.json"
REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = (
    REPO_ROOT / "nyxloom-trove" / "reports"
    / "CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md"
)

# The wait oracle (rule 2 above).  `durable` means production will hold the task
# here until an external actor acts -- it is not a spin.  `bounded_by` names the
# mechanism that ends the wait; "operator" means only a human ends it.  A
# non-durable kind is a characterized GAP: production neither progresses nor
# durably parks, and the fixture must list it under `known_gaps` so the corpus
# reports it instead of quietly blessing it.
WAIT_TAXONOMY: dict[str, dict[str, object]] = {
    "human-decision": {"durable": True, "bounded_by": "operator"},
    "blocked": {"durable": True, "bounded_by": "operator"},
    "review-wave-pending": {"durable": True, "bounded_by": "policy.wave_open_after_seconds"},
    "dispatch-skip:no-healthy-route": {"durable": False, "bounded_by": "provider recovery"},
    "operator-signalled-retry": {"durable": False, "bounded_by": "operator"},
}


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _event(sequence: int, kind: EventType, payload: dict, *, attempt_id: str | None = None,
           decision_id: str | None = None) -> Event:
    return Event(
        schema_version=1,
        sequence=sequence,
        timestamp=FIXED_TIME,
        project="demo",
        actor=Actor(kind=ActorKind.TICK, id="characterization"),
        type=kind,
        payload=payload,
        task_id=TASK_ID,
        attempt_id=attempt_id,
        decision_id=decision_id,
    )


def _initial_state(scenario: dict) -> TaskStateFile:
    return TaskStateFile(
        schema_version=1,
        task_id=TASK_ID,
        project="demo",
        state=TaskState(scenario["initial_state"]),
        since=FIXED_TIME,
        handoff_path=HANDOFF_PATH,
    )


def _attempt(item: dict) -> Attempt:
    """An attempt record shaped exactly as the daemon persists one."""
    receipt = None
    if item.get("receipt") is not None:
        receipt = Receipt(
            result=ReceiptResult(item["receipt"]),
            exit_code=0 if item["receipt"] == "done" else 1,
        )
    return Attempt(
        attempt_id=item["attempt_id"],
        role=Role(item["role"]),
        state=AttemptState(item.get("state", "EXITED")),
        route=Route(route_id=ROUTE_ID, cli="fake", model="fake"),
        started=FIXED_TIME,
        ended=FIXED_TIME,
        receipt=receipt,
    )


def _history(scenario: dict) -> list[Event]:
    """The scenario's recorded event stream, in production event shapes."""
    events = [_event(1, EventType.TASK_CREATED, {"statefile": _initial_state(scenario).to_dict()})]
    for index, item in enumerate(scenario["history"], start=2):
        kind = item["kind"]
        if kind in ("transition", "malformed_transition"):
            events.append(_event(index, EventType.TASK_TRANSITIONED, {"to": item["to"]}))
        elif kind == "attempt":
            # ATTEMPT_EXITED carries the whole attempt record (daemon.py's
            # EmitAttemptExit payload), which storage.apply_event projects into
            # tsf.attempts -- this is what makes a review verdict bindable.
            events.append(_event(index, EventType.ATTEMPT_EXITED,
                                 {"attempt": _attempt(item).to_dict()},
                                 attempt_id=item["attempt_id"]))
        elif kind == "gate":
            gate = GateResult(
                gate_id=item.get("gate_id", "characterization-gate"),
                phase=item.get("phase", "post-merge"),
                commit="a" * 40,
                exit_code=0 if item["verdict"] == "pass" else 1,
                started=FIXED_TIME,
                ended=FIXED_TIME,
                artifacts=[item["artifact"]],
            )
            events.append(_event(index, EventType.GATE_FINISHED, {"gate_result": gate.to_dict()}))
        elif kind == "merge":
            events.append(_event(index, EventType.MERGE_RECORDED, {"merge_commit": item["commit"]}))
        elif kind == "needs_operator":
            events.append(_event(index, EventType.NEEDS_OPERATOR, {"reason": item["reason"]}))
        elif kind == "decision_opened":
            events.append(_event(index, EventType.DECISION_OPENED, {}, decision_id=item["decision_id"]))
        elif kind == "provider_pause":
            events.append(_event(index, EventType.PROVIDER_STATE_CHANGED,
                                 {"route_id": item["route_id"], "state": "paused"}))
        elif kind == "review_recorded":
            # daemon.py's reviewer branch: payload {"result": ...} plus the
            # REVIEWER ATTEMPT id the verdict is bound to (A7 binding).
            payload = {"result": item["result"]}
            if item.get("reject_class"):
                payload["reject_class"] = item["reject_class"]
            events.append(_event(index, EventType.REVIEW_RECORDED, payload,
                                 attempt_id=item["attempt_id"]))
        else:
            raise AssertionError(f"unknown characterization event kind: {kind}")
    return events


def _project(events: list[Event]) -> dict[str, TaskStateFile]:
    states: dict[str, TaskStateFile] = {}
    for event in events:
        apply_event(states, event)
    return states


def _frontmatter(scenario: dict) -> Frontmatter:
    return Frontmatter(
        schema_version=1,
        id=TASK_ID,
        project="demo",
        title="characterization",
        tier=TIER,
        input_revision="0000000",
        source=Source(kind="roadmap"),
        scope=Scope(touch=["src/demo.py"]),
        oracles=[],
        gates=[],
        escalate_if=[],
        depends_on=list(scenario.get("depends_on", [])),
    )


def _copy_state(state: TaskStateFile, *, state_override: TaskState | None = None) -> TaskStateFile:
    copied = TaskStateFile.from_dict(state.to_dict())
    if state_override is not None:
        copied.state = state_override
    return copied


def _plan_input(scenario: dict, state: TaskStateFile) -> ReconcileInput:
    """A ReconcileInput built ONLY from declared scenario fields.

    Every field that can change a plan is fixture data, so an expectation can
    be read against its stated cause instead of a test-file special case.
    """
    policy = Policy(
        max_attempts_per_task=scenario.get("max_attempts", 3),
        merge_mode="guarded-automatic",
        carve_ahead_target=0,
    )
    cfg = ProjectConfig(
        project_id="demo",
        root=Path("/demo"),
        default_branch="main",
        worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={},
        mutexes={"stack": MutexDef(name="stack")},
        policy=policy,
        pipeline=list(PRESETS[scenario["pipeline"]]),
    )
    return ReconcileInput(
        now=FIXED_TIME,
        cfg=cfg,
        routes=Routes(
            revision="characterization",
            tiers={TIER: [ROUTE_ID]},
            routes={ROUTE_ID: RouteDef(route_id=ROUTE_ID, cli="fake", model="fake")},
        ),
        states={TASK_ID: state},
        frontmatters={TASK_ID: (_frontmatter(scenario), HANDOFF_PATH)},
        lint_clean={TASK_ID: True},
        project_paused=False,
        decisions_open=set(scenario.get("open_decisions", [])),
        merged_branches=set(),
        leases_free={},
        provider_ok={ROUTE_ID: scenario.get("provider_ok", True)},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        triage_class={TASK_ID: scenario["triage_class"]} if "triage_class" in scenario else {},
    )


def _action_name(action: object) -> str:
    if isinstance(action, Transition):
        assert action.to is not None
        return f"Transition:{action.to.value}"
    return type(action).__name__


def _plan(scenario: dict, state: TaskStateFile):
    return plan_project(_plan_input(scenario, state))


def _classify_wait(scenario: dict, state: TaskStateFile, events: list[Event], result) -> str | None:
    """The wait oracle.  Production artifacts in, WAIT_TAXONOMY key out.

    Rules, in order (first match wins), each grounded in something production
    produced -- never in the scenario's narrative or name:

    0. a terminal task is not waiting (types.TERMINAL_TASK_STATES);
    1. reconcile's own `dispatch-skip` breadcrumb wins: its detail IS
       `dispatch_eligible`'s verbatim reason, i.e. production explaining why it
       refuses to progress this task;
    2. a plan that transitions the task is progress, not a wait;
    3. NEEDS_DECISION / BLOCKED are the states production parks a task in and
       will not leave without an operator;
    4. AWAITING_REVIEW parks until the wave trigger fires
       (`wave_open_after_seconds`), which is why an OpenWave in the plan is
       progress and its absence is the wait;
    5. a recorded NEEDS_OPERATOR with a plan that only retries an effect (no
       transition) is the retry-until-operator shape.
    """
    if state.state in TERMINAL_TASK_STATES:
        return None
    for note in result.trace.breadcrumbs:
        if note.kind == "dispatch-skip" and note.task_id == TASK_ID:
            return f"dispatch-skip:{note.detail}"
    if any(isinstance(action, Transition) for action in result):
        return None
    if state.state is TaskState.NEEDS_DECISION:
        return "human-decision"
    if state.state is TaskState.BLOCKED:
        return "blocked"
    if state.state is TaskState.AWAITING_REVIEW:
        return "review-wave-pending"
    if any(event.type is EventType.NEEDS_OPERATOR for event in events):
        return "operator-signalled-retry"
    return None


def _merge_evidence(events: list[Event], state: TaskStateFile) -> list[str]:
    """Evidence, read from the event stream, that authorized the merge.

    Never consults the pipeline: a configured `review_independent` stage is a
    plan to obtain a verdict, not a verdict.  An item qualifies only when it is
    recorded BEFORE the MERGE_RECORDED event and, for a review verdict, bound
    to an attempt the projection knows and whose role is the independent
    reviewer (an unbindable verdict is the stale-result shape, reported by
    `_unbound_verdicts` instead).
    """
    merge_sequences = [e.sequence for e in events if e.type is EventType.MERGE_RECORDED]
    if not merge_sequences:
        return []
    cutoff = min(merge_sequences)
    reviewer_attempts = {
        att.attempt_id for att in state.attempts if att.role is Role.REVIEW_INDEPENDENT
    }
    evidence: list[str] = []
    for event in events:
        if event.sequence >= cutoff:
            break
        if event.type is EventType.GATE_FINISHED:
            gate = event.payload["gate_result"]
            if gate["exit_code"] == 0:
                evidence.append(f"gate:pass:{gate['gate_id']}")
        elif event.type is EventType.REVIEW_RECORDED:
            if (event.payload.get("result") == "approved"
                    and event.attempt_id in reviewer_attempts):
                evidence.append(f"review:approved:{event.attempt_id}")
    return evidence


def _unbound_verdicts(events: list[Event], state: TaskStateFile) -> list[str]:
    """Recorded verdicts naming an attempt the projection does not have.

    This is the stale/misbound-result observable: the daemon binds every
    REVIEW_RECORDED to the attempt that produced it, so a verdict whose attempt
    is absent from the projection can never be evidence for anything.
    """
    known = {att.attempt_id for att in state.attempts}
    return [
        event.attempt_id for event in events
        if event.type is EventType.REVIEW_RECORDED and event.attempt_id not in known
    ]


def _semantic_output(scenario: dict) -> dict:
    events = _history(scenario)
    state = _project(events)[TASK_ID]
    result = _plan(scenario, state)
    output = {
        "stage_names": compose(scenario["pipeline"]),
        "event_types": [event.type.value for event in events],
        "final_state": state.state.value,
        "artifacts": {
            "handoff_path": state.handoff_path,
            "merge_commit": state.merge_commit,
            "gate_verdicts": ["pass" if g.exit_code == 0 else "fail" for g in state.gate_results],
            "gate_artifacts": [a for g in state.gate_results for a in g.artifacts],
            "attempts": [f"{a.role.value}:{a.attempt_id}" for a in state.attempts],
        },
        "merge_evidence": _merge_evidence(events, state),
        "unbound_verdicts": _unbound_verdicts(events, state),
        "wait": _classify_wait(scenario, state, events, result),
        "planned_actions": [_action_name(action) for action in result],
    }
    # An explicitly declared post-state probe: the ordered actions production
    # plans from each named state, used to characterize a chain (e.g. the
    # post-merge region) that the recorded final state has already passed
    # through.  Declared per scenario -- never inferred from its name.
    if scenario.get("plan_from_states"):
        chain: list[str] = []
        for name in scenario["plan_from_states"]:
            probe = _copy_state(state, state_override=TaskState(name))
            chain.extend(_action_name(action) for action in _plan(scenario, probe))
        output["post_merge_chain"] = chain
    return output


def _assert_semantics(scenario: dict) -> None:
    assert _semantic_output(scenario) == scenario["expected"]


def _active(fixture: dict | None = None) -> list[dict]:
    return [s for s in (fixture or _fixture())["active"] if "expected_error" not in s]


# ---------------------------------------------------------------------------
# corpus shape and recorded semantics


def test_active_corpus_records_current_semantics():
    corpus = _fixture()
    assert corpus["schema_version"] == 1
    expected_names = {
        "full_happy", "gated_happy", "lean_happy", "self_review_rejection",
        "independent_rejection_fixable", "independent_rejection_architectural",
        "independent_rejection_product", "gate_failure", "merge_conflict",
        "human_decision", "provider_pause", "crash_replay", "stale_result",
        "malformed_result", "attempt_exhaustion",
    }
    assert {scenario["name"] for scenario in corpus["active"]} == expected_names
    for scenario in _active(corpus):
        _assert_semantics(scenario)


def test_malformed_transition_fails_closed_and_preserves_the_projection():
    """A malformed transition target is rejected AT the offending event and
    leaves the projection exactly where it was -- the fail-closed contract, not
    merely 'something raised somewhere'."""
    scenario = next(s for s in _fixture()["active"] if "expected_error" in s)
    events = _history(scenario)
    bad = next(e for e in events
               if e.type is EventType.TASK_TRANSITIONED and e.payload["to"] == "NOT_A_STATE")
    states: dict[str, TaskStateFile] = {}
    for event in events:
        if event is bad:
            break
        apply_event(states, event)
    before = states[TASK_ID].to_dict()
    with pytest.raises(ValueError):
        apply_event(states, bad)
    assert states[TASK_ID].to_dict() == before
    assert states[TASK_ID].state.value == scenario["expected_state_after_rejection"]


def test_replay_is_deterministic_and_order_chunk_independent():
    """Replay twice, and replay split in two chunks, all project identically --
    so a rewritten store cannot pass by being merely self-consistent."""
    for scenario in _active():
        events = _history(scenario)
        first = _project(events)[TASK_ID].to_dict()
        second = _project(events)[TASK_ID].to_dict()
        assert first == second, scenario["name"]
        split = len(events) // 2
        states: dict[str, TaskStateFile] = {}
        for event in events[:split]:
            apply_event(states, event)
        for event in events[split:]:
            apply_event(states, event)
        assert states[TASK_ID].to_dict() == first, scenario["name"]


# ---------------------------------------------------------------------------
# progress / wait


def test_progress_wait_or_bounded_terminal_is_computed_not_read():
    """The plan-item-3 property, asserted on COMPUTED output.

    Nothing here reads `scenario["expected"]`: progress is `plan_project`'s
    action list, the wait is `_classify_wait`'s verdict, and terminality is
    production's own `TERMINAL_TASK_STATES`.  A non-durable wait is allowed
    only when the fixture already reports it as a known gap, so a spin can
    never be silently characterized as healthy.
    """
    known_gaps = {gap["wait"]: gap for gap in _fixture()["known_gaps"]}
    for scenario in _active():
        output = _semantic_output(scenario)
        wait = output["wait"]
        terminal = TaskState(output["final_state"]) in TERMINAL_TASK_STATES
        assert output["planned_actions"] or wait or terminal, scenario["name"]
        if wait is not None:
            assert wait in WAIT_TAXONOMY, f"{scenario['name']}: undeclared wait {wait!r}"
            if not WAIT_TAXONOMY[wait]["durable"]:
                assert wait in known_gaps, (
                    f"{scenario['name']} waits in the non-durable class {wait!r}, which is "
                    "not declared in the fixture's known_gaps")
                assert known_gaps[wait]["owner"], wait


def test_known_gaps_are_real_and_none_is_stale():
    """Each declared gap names a scenario that still exhibits it -- so a gap
    fixed by a later package fails here instead of lingering as folklore."""
    observed = {}
    for scenario in _active():
        wait = _semantic_output(scenario)["wait"]
        if wait is not None:
            observed.setdefault(wait, []).append(scenario["name"])
    for gap in _fixture()["known_gaps"]:
        assert gap["wait"] in WAIT_TAXONOMY
        assert not WAIT_TAXONOMY[gap["wait"]]["durable"], gap["wait"]
        assert observed.get(gap["wait"]) == gap["scenarios"], (
            f"known_gap {gap['wait']!r} claims {gap['scenarios']} but the corpus "
            f"now shows {observed.get(gap['wait'])} -- update or retire the gap")


def test_wait_classification_follows_production_not_the_fixture():
    """The provider-health wait is reconcile's own `dispatch_eligible` reason.

    Flipping the declared provider health flips the computed wait and the plan,
    with the fixture's recorded event stream untouched -- proving the wait is a
    function of production inputs, not of which events the fixture narrates.
    """
    scenario = copy.deepcopy(next(s for s in _fixture()["active"] if s["name"] == "provider_pause"))
    assert scenario["provider_ok"] is False
    state = _project(_history(scenario))[TASK_ID]
    # The classification is production's verdict verbatim, not a label this
    # module invented for the scenario.
    assert dispatch_eligible(_frontmatter(scenario), state,
                             _plan_input(scenario, state)) == (False, "no-healthy-route")
    assert _semantic_output(scenario)["wait"] == "dispatch-skip:no-healthy-route"
    scenario["provider_ok"] = True
    healthy = _semantic_output(scenario)
    assert healthy["wait"] is None
    assert healthy["planned_actions"] == ["DispatchImplementer"]
    assert healthy["event_types"] == scenario["expected"]["event_types"]


# ---------------------------------------------------------------------------
# merge evidence


def _assert_merge_is_evidenced(scenario: dict) -> None:
    """THE merge oracle: recorded, ordered, attempt-bound evidence."""
    events = _history(scenario)
    state = _project(events)[TASK_ID]
    if state.merge_commit is None:
        return
    merge_seq = min(e.sequence for e in events if e.type is EventType.MERGE_RECORDED)
    evidence = _merge_evidence(events, state)
    assert any(item.startswith("review:approved:") for item in evidence), (
        f"{scenario['name']}: merge {state.merge_commit} has no attempt-bound independent "
        f"review approval recorded before it (evidence={evidence})")
    for item in evidence:
        origin = item.rsplit(":", 1)[1]
        source = next(
            (e for e in events
             if (e.type is EventType.REVIEW_RECORDED and e.attempt_id == origin)
             or (e.type is EventType.GATE_FINISHED
                 and e.payload["gate_result"]["gate_id"] == origin)),
            None)
        # Evidence with no source event is fabricated, not recorded -- the
        # failure mode a stage-name (or any configuration) inference produces.
        assert source is not None, f"{scenario['name']}: {item} has no recorded source event"
        assert source.sequence < merge_seq, f"{scenario['name']}: {item} is not pre-merge"
    # A merge must not follow a still-failing gate: the newest pre-merge gate
    # verdict is the one that authorized it.
    pre_merge_gates = [
        e.payload["gate_result"] for e in events
        if e.type is EventType.GATE_FINISHED and e.sequence < merge_seq
    ]
    if pre_merge_gates:
        assert pre_merge_gates[-1]["exit_code"] == 0, (
            f"{scenario['name']}: merge recorded after a failing gate")


def test_every_characterized_merge_is_backed_by_recorded_evidence():
    for scenario in _active():
        _assert_merge_is_evidenced(scenario)


@pytest.mark.parametrize("corruption", ["drop-approval", "unbind-approval",
                                        "reject-verdict", "approval-after-merge",
                                        "fail-pre-merge-gate"])
def test_merge_evidence_oracle_rejects_corrupted_histories(corruption: str):
    """Corrupt the recorded HISTORY (the input), not the expectation.

    Each mutation is a way a rewritten engine could merge without real
    authority; every one must fail the oracle.  This is what makes the merge
    check non-hollow: it cannot be satisfied by configuration.
    """
    scenario = copy.deepcopy(next(s for s in _fixture()["active"] if s["name"] == "full_happy"))
    history = scenario["history"]
    approval = next(i for i in history
                    if i["kind"] == "review_recorded" and i["result"] == "approved")
    if corruption == "drop-approval":
        history.remove(approval)
    elif corruption == "unbind-approval":
        approval["attempt_id"] = "never-created-attempt"
    elif corruption == "reject-verdict":
        approval["result"] = "rejected"
    elif corruption == "approval-after-merge":
        history.remove(approval)
        history.insert(history.index(next(i for i in history if i["kind"] == "merge")) + 1,
                       approval)
    else:
        next(i for i in history if i["kind"] == "gate")["verdict"] = "fail"
    with pytest.raises(AssertionError):
        _assert_merge_is_evidenced(scenario)


def test_recorded_stale_verdict_is_never_evidence():
    """A verdict bound to an attempt the projection never saw is reported as
    unbound and contributes nothing -- the stale-result contract."""
    scenario = next(s for s in _fixture()["active"] if s["name"] == "stale_result")
    output = _semantic_output(scenario)
    assert output["unbound_verdicts"], "stale_result must record an unbindable verdict"
    assert output["merge_evidence"] == []


def test_declared_triage_class_is_backed_by_a_recorded_reject_class():
    """The planner's triage input is derived by the daemon from the latest
    rejection verdict's ``reject_class``.  A scenario may not assert a triage
    routing that no recorded verdict supports, and a recorded reject class may
    not be silently ignored -- otherwise the rejection fixtures would prove only
    that the planner honours a dict the test handed it."""
    for scenario in _active():
        recorded = [
            item["reject_class"] for item in scenario["history"]
            if item["kind"] == "review_recorded" and item["result"] == "rejected"
            and item.get("reject_class")
        ]
        declared = scenario.get("triage_class")
        assert (recorded[-1] if recorded else None) == declared, scenario["name"]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("transition", "BLOCKED"),
        ("artifact", "0" * 40),
        ("gate", "fail"),
        ("evidence", "review:approved:forged"),
    ],
)
def test_corpus_is_sensitive_to_transition_artifact_and_gate_corruption(field: str, replacement: str):
    """The stop-loss acceptance from the amendment: corrupting one expected
    transition, artifact binding, gate verdict or evidence item must make a
    characterization test fail."""
    scenario = copy.deepcopy(next(item for item in _fixture()["active"] if item["name"] == "full_happy"))
    if field == "transition":
        scenario["expected"]["final_state"] = replacement
    elif field == "artifact":
        scenario["expected"]["artifacts"]["merge_commit"] = replacement
    elif field == "gate":
        scenario["expected"]["artifacts"]["gate_verdicts"][0] = replacement
    else:
        scenario["expected"]["merge_evidence"] = [replacement]
    with pytest.raises(AssertionError):
        _assert_semantics(scenario)


def test_history_corruption_breaks_the_recorded_expectation():
    """The other direction: mutate the INPUT and the recorded expectation must
    no longer hold.  Together with the test above this pins the corpus to a
    real function of its input rather than to a self-consistent pair."""
    scenario = copy.deepcopy(next(s for s in _fixture()["active"] if s["name"] == "full_happy"))
    scenario["history"].remove(next(i for i in scenario["history"] if i["kind"] == "merge"))
    with pytest.raises(AssertionError):
        _assert_semantics(scenario)


# ---------------------------------------------------------------------------
# future five-band ladder: INVENTORY, with an activation contract


def test_future_band_inventory_is_complete_and_classified():
    """Not tests -- typed inventory.  Each item declares that it is
    non-executable, what CR-09 must build to activate it, and what retires it."""
    future = _fixture()["future"]
    ladder = [item for item in future if "decline_count" not in item["input"]]
    assert {item["input"]["effective_band"] for item in ladder} == {1, 2, 3, 4, 5}
    for item in future:
        assert item["executable"] is False
        assert item["classification"] == "inventory"
        activation = item["activation"]
        assert activation["package"] == "CR-09"
        assert activation["driver"]
        assert activation["retire_by"]
        assert activation["required_event_types"]
        assert activation["required_actions"]
        assert item["input"]["route_id"] and item["input"]["task_fingerprint"]
        assert item["expected"]["ordered_actions"] and item["expected"]["events"]
        assert {"lifecycle", "effective_band"} <= set(item["expected"]["final"])
        # The declared vocabulary must actually cover the expectation it
        # claims to constrain, or the contract is decoration.
        assert set(item["expected"]["events"]) <= set(activation["required_event_types"]) | {
            e.value for e in EventType}
        planned = {action.split(":", 1)[0] for action in item["expected"]["ordered_actions"]}
        assert planned <= set(activation["required_actions"])
    terminal = next(item for item in future if item["name"] == "band_5_decline_becomes_human_wait")
    assert terminal["expected"]["wait"] == "human-decision"
    assert terminal["expected"]["wait"] in WAIT_TAXONOMY


def test_future_band_inventory_activates_when_production_vocabulary_lands():
    """The retirement trigger.

    While CR-09's vocabulary is absent these fixtures cannot be executed, and
    saying so is honest.  The moment production grows ALL of an item's required
    event types and actions, that excuse is gone -- this test fails with the
    activation instructions rather than letting inventory masquerade as
    coverage forever.
    """
    known_events = {e.value for e in EventType}
    known_actions = {
        name for name, obj in vars(reconcile_mod).items()
        if isinstance(obj, type) and issubclass(obj, reconcile_mod.Action)
    }
    for item in _fixture()["future"]:
        activation = item["activation"]
        missing_events = [e for e in activation["required_event_types"] if e not in known_events]
        missing_actions = [a for a in activation["required_actions"] if a not in known_actions]
        assert missing_events or missing_actions, (
            f"{item['name']}: CR-09 vocabulary has landed in production. "
            f"{activation['retire_by']}")


# ---------------------------------------------------------------------------
# ownership inventory: mechanically checked, without line-count churn


_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([0-9,]+|new)\s*\|")
# A recorded size is a review-allocation signal, so it is checked with a band
# rather than for equality: an ordinary edit must not churn the document, but
# real drift must fail.  Stated in the inventory itself.
SIZE_TOLERANCE_FRACTION = 0.10
SIZE_TOLERANCE_FLOOR = 40


def _inventory_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in INVENTORY_PATH.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def _control_plane_closure() -> set[str]:
    """Every package module the control plane imports directly.

    Derived by parsing daemon.py and reconcile.py, so membership is a
    structural fact about the code rather than a list this test could drift
    from -- and a new control-plane dependency fails until it is owned.
    """
    src = REPO_ROOT / "src" / "nyxloom"
    modules: set[str] = set()
    for name in ("daemon.py", "reconcile.py"):
        tree = ast.parse((src / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module:
                    modules.add(node.module.split(".")[0])
                else:
                    modules.update(alias.name for alias in node.names)
        modules.add(name[:-3])
    return {m for m in modules if (src / f"{m}.py").exists()}


def test_inventory_paths_all_exist():
    rows = _inventory_rows()
    assert rows, "inventory tables did not parse"
    missing = [path for path in rows if not (REPO_ROOT / path).exists()]
    assert not missing, f"inventory names paths that no longer exist: {missing}"


def test_inventory_covers_the_whole_control_plane_import_closure():
    listed = {Path(path).stem for path in _inventory_rows() if path.startswith("src/nyxloom/")}
    missing = sorted(_control_plane_closure() - listed)
    assert not missing, (
        "daemon.py/reconcile.py now import package modules with no declared redesign "
        f"ownership: {missing} -- add a row to the inventory")


def test_inventory_declares_test_ownership_for_every_rewritten_surface():
    """A package that rewrites a surface must also declare what happens to that
    surface's tests -- otherwise CR-05..07 meet the retirement policy with an
    undeclared 7,000-line test file."""
    text = INVENTORY_PATH.read_text(encoding="utf-8")
    rewrite_packages = ("CR-05", "CR-06", "CR-07")
    listed_tests = {p for p in _inventory_rows() if p.startswith("tests/")}
    missing: list[str] = []
    for line in text.splitlines():
        match = _ROW.match(line)
        if not match or not match.group(1).startswith("src/nyxloom/"):
            continue
        if not any(pkg in line for pkg in rewrite_packages):
            continue
        test_path = f"tests/test_{Path(match.group(1)).stem}.py"
        if (REPO_ROOT / test_path).exists() and test_path not in listed_tests:
            missing.append(test_path)
    assert not missing, (
        f"surfaces owned by {rewrite_packages} whose test module has no declared "
        f"retirement handling: {sorted(set(missing))}")


def test_inventory_sizes_are_within_the_declared_tolerance():
    stale: list[str] = []
    for path, recorded in _inventory_rows().items():
        if recorded == "new":
            continue
        actual = len((REPO_ROOT / path).read_text(encoding="utf-8").splitlines())
        allowed = max(SIZE_TOLERANCE_FLOOR, int(actual * SIZE_TOLERANCE_FRACTION))
        if abs(int(recorded.replace(",", "")) - actual) > allowed:
            stale.append(f"{path}: recorded {recorded}, actual {actual} (tolerance {allowed})")
    assert not stale, "inventory sizes have drifted; re-measure and update:\n" + "\n".join(stale)


def test_inventory_states_its_own_mechanical_contract():
    """The document must state the rules this module enforces, so a reader
    updating it knows what will fail and why."""
    text = INVENTORY_PATH.read_text(encoding="utf-8")
    for marker in ("import closure", "tolerance", "tests/test_core_characterization.py"):
        assert marker in text, f"inventory does not state its {marker!r} contract"

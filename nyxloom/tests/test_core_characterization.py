"""CR-00 semantic characterization corpus.

The JSON fixture is intentionally a compact behavioural boundary: it records
only ordered actions/events, projected state, artifact bindings, and durable
waits.  It does not snapshot log text, generated prompts, or implementation
method calls.  Core refactors compare against this corpus before replacing the
planner/executor seams.

The future five-band fixtures are *expected-red-until-CR-09*.  They are not
skipped or xfailed tests: this module validates their complete, typed contract
now.  CR-09 must add a ladder driver and move them into ``active``; until then
they cannot be honestly executed against an implementation that does not yet
exist, and therefore cannot make the current gate red.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom.config import MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import AutoMergeTask, ReconcileInput, RunPostMergeGate, Transition, plan_project
from nyxloom.stages import PRESETS, compose
from nyxloom.storage import apply_event
from nyxloom.types import (
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
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "core_characterization_v1.json"


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


def _history(scenario: dict) -> list[Event]:
    events = [_event(1, EventType.TASK_CREATED, {"statefile": _initial_state(scenario).to_dict()})]
    for index, item in enumerate(scenario["history"], start=2):
        kind = item["kind"]
        if kind == "transition":
            events.append(_event(index, EventType.TASK_TRANSITIONED, {"to": item["to"]}))
        elif kind == "malformed_transition":
            events.append(_event(index, EventType.TASK_TRANSITIONED, {"to": item["to"]}))
        elif kind == "gate":
            exit_code = 0 if item["verdict"] == "pass" else 1
            gate = GateResult(
                gate_id="characterization-gate",
                phase="post-merge",
                commit="a" * 40,
                exit_code=exit_code,
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
            events.append(_event(index, EventType.PROVIDER_STATE_CHANGED, {"route_id": item["route_id"], "state": "paused"}))
        elif kind == "review_recorded":
            events.append(_event(index, EventType.REVIEW_RECORDED, {"result": "approved"}, attempt_id=item["attempt_id"]))
        else:
            raise AssertionError(f"unknown characterization event kind: {kind}")
    return events


def _project(events: list[Event]) -> dict[str, TaskStateFile]:
    states: dict[str, TaskStateFile] = {}
    for event in events:
        apply_event(states, event)
    return states


def _frontmatter(scenario: dict) -> Frontmatter:
    depends_on = ["D-101"] if scenario["name"] == "human_decision" else []
    return Frontmatter(
        schema_version=1,
        id=TASK_ID,
        project="demo",
        title="characterization",
        tier="flash-high",
        input_revision="0000000",
        source=Source(kind="roadmap"),
        scope=Scope(touch=["src/demo.py"]),
        oracles=[],
        gates=[],
        escalate_if=[],
        depends_on=depends_on,
    )


def _copy_state(state: TaskStateFile, *, state_override: TaskState | None = None) -> TaskStateFile:
    copied = TaskStateFile.from_dict(state.to_dict())
    if state_override is not None:
        copied.state = state_override
    return copied


def _attempts(scenario: dict) -> list[Attempt]:
    used = scenario.get("attempts_used", 0)
    return [
        Attempt(
            attempt_id=f"attempt-{number}",
            role=Role.IMPLEMENTER,
            state=AttemptState.EXITED,
            route=Route(route_id="route-1", cli="fake", model="fake"),
            started=FIXED_TIME,
            receipt=Receipt(result=ReceiptResult.ERROR, exit_code=1),
        )
        for number in range(1, used + 1)
    ]


def _plan_input(scenario: dict, state: TaskStateFile) -> ReconcileInput:
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
    provider_ok = {"route-1": scenario["name"] == "self_review_rejection"}
    return ReconcileInput(
        now=FIXED_TIME,
        cfg=cfg,
        routes=Routes(
            revision="characterization",
            tiers={"flash-high": ["route-1"]},
            routes={"route-1": RouteDef(route_id="route-1", cli="fake", model="fake")},
        ),
        states={TASK_ID: state},
        frontmatters={TASK_ID: (_frontmatter(scenario), HANDOFF_PATH)},
        lint_clean={TASK_ID: True},
        project_paused=False,
        decisions_open={"D-101"} if scenario["name"] == "human_decision" else set(),
        merged_branches=set(),
        leases_free={},
        provider_ok=provider_ok,
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        triage_class={TASK_ID: scenario["triage_class"]} if "triage_class" in scenario else {},
    )


def _action_name(action: object) -> str:
    if isinstance(action, Transition):
        assert action.to is not None
        return f"Transition:{action.to.value}"
    if isinstance(action, (AutoMergeTask, RunPostMergeGate)):
        return type(action).__name__
    return type(action).__name__


def _planned_actions(scenario: dict, final_state: TaskStateFile) -> list[str]:
    if scenario["name"] in {"full_happy", "gated_happy", "lean_happy"}:
        observations = [TaskState.MERGED, TaskState.VALIDATING]
    else:
        observations = [final_state.state]
    planned: list[str] = []
    for observed_state in observations:
        state = _copy_state(final_state, state_override=observed_state)
        state.attempts = _attempts(scenario)
        planned.extend(_action_name(action) for action in plan_project(_plan_input(scenario, state)))
    return planned


def _wait_kind(scenario: dict, state: TaskStateFile, events: list[Event], actions: list[str]) -> str | None:
    if "Transition:NEEDS_DECISION" in actions or any(event.type is EventType.NEEDS_OPERATOR for event in events):
        return "human-decision"
    if any(event.type is EventType.PROVIDER_STATE_CHANGED for event in events):
        return "provider-backoff"
    if state.state is TaskState.NEEDS_DECISION:
        return "human-decision"
    if state.state is TaskState.BLOCKED:
        return "blocked"
    if state.state is TaskState.AWAITING_REVIEW:
        return "review"
    return None


def _semantic_output(scenario: dict) -> dict:
    events = _history(scenario)
    state = _project(events)[TASK_ID]
    gate_verdicts = ["pass" if gate.exit_code == 0 else "fail" for gate in state.gate_results]
    gate_artifacts = [artifact for gate in state.gate_results for artifact in gate.artifacts]
    actions = _planned_actions(scenario, state)
    return {
        "stage_names": compose(scenario["pipeline"]),
        "event_types": [event.type.value for event in events],
        "final_state": state.state.value,
        "artifacts": {
            "handoff_path": state.handoff_path,
            "merge_commit": state.merge_commit,
            "gate_verdicts": gate_verdicts,
            "gate_artifacts": gate_artifacts,
        },
        "wait": _wait_kind(scenario, state, events, actions),
        "planned_actions": actions,
    }


def _assert_semantics(scenario: dict) -> None:
    assert _semantic_output(scenario) == scenario["expected"]


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
    for scenario in corpus["active"]:
        if "expected_error" in scenario:
            with pytest.raises(ValueError):
                _semantic_output(scenario)
        else:
            _assert_semantics(scenario)


def test_replay_is_deterministic_for_each_valid_history():
    for scenario in _fixture()["active"]:
        if "expected_error" in scenario:
            continue
        events = _history(scenario)
        first = _project(events)[TASK_ID].to_dict()
        second = _project(events)[TASK_ID].to_dict()
        assert first == second, scenario["name"]


def test_progress_wait_or_bounded_terminal_is_explicit():
    for scenario in _fixture()["active"]:
        if "expected_error" in scenario:
            continue
        expected = scenario["expected"]
        has_progress = bool(expected["planned_actions"])
        has_wait = expected["wait"] is not None
        is_terminal = expected["final_state"] == TaskState.COMPLETED.value
        assert has_progress or has_wait or is_terminal, scenario["name"]


def test_every_characterized_merge_has_review_or_gate_evidence():
    for scenario in _fixture()["active"]:
        if "expected_error" in scenario:
            continue
        output = _semantic_output(scenario)
        if output["artifacts"]["merge_commit"] is None:
            continue
        has_gate = "pass" in output["artifacts"]["gate_verdicts"]
        has_independent_review = "review_independent" in output["stage_names"]
        assert has_gate or has_independent_review, scenario["name"]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("transition", "BLOCKED"),
        ("artifact", "0" * 40),
        ("gate", "fail"),
    ],
)
def test_corpus_is_sensitive_to_transition_artifact_and_gate_corruption(field: str, replacement: str):
    scenario = copy.deepcopy(next(item for item in _fixture()["active"] if item["name"] == "full_happy"))
    if field == "transition":
        scenario["expected"]["final_state"] = replacement
    elif field == "artifact":
        scenario["expected"]["artifacts"]["merge_commit"] = replacement
    else:
        scenario["expected"]["artifacts"]["gate_verdicts"][0] = replacement
    with pytest.raises(AssertionError):
        _assert_semantics(scenario)


def test_future_five_band_contracts_are_complete_but_not_activated():
    """CR-09 activation is a data move plus ladder-driver implementation.

    The deliberately red contracts remain executable data rather than skipped
    tests.  They cannot compare against a non-existent ladder today, so this
    structural oracle keeps their vocabulary, ordered effects, terminal/wait
    result, and all five boundaries reviewable without laundering a red gate.
    """
    future = _fixture()["future"]
    assert {item["input"]["effective_band"] for item in future if "decline_count" not in item["input"]} == {1, 2, 3, 4, 5}
    for item in future:
        assert item["status"] == "expected-red-until-CR-09"
        assert item["activation"] == "CR-09"
        assert item["input"]["route_id"]
        assert item["input"]["task_fingerprint"]
        assert item["expected"]["ordered_actions"]
        assert item["expected"]["events"]
        assert "lifecycle" in item["expected"]["final"]
        assert "effective_band" in item["expected"]["final"]
    terminal = next(item for item in future if item["name"] == "band_5_decline_becomes_human_wait")
    assert terminal["expected"]["wait"] == "human-decision"

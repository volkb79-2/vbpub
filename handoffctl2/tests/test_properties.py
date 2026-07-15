"""Property tests over the frozen core (SPEC §14.1). Package P11.

Oracles 1-5 from handoff/P11-properties-crash.md:
  1. Transition soundness (exhaustive, not hypothesis).
  2. Serde round-trip fuzz (TaskStateFile / Event / Frontmatter) + unknown-key
     rejection.
  3. Replay determinism over a random valid lifecycle.
  4. Sequence integrity under concurrent append_event (multiprocessing).
  5. apply_event tolerance (unknown attempt upserts, unknown task no-ops,
     invalid task transition raises).
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import random
from datetime import timezone

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from handoffctl import paths, storage
from handoffctl.types import (
    ATTEMPT_TRANSITIONS,
    TASK_TRANSITIONS,
    TERMINAL_ATTEMPT_STATES,
    TERMINAL_TASK_STATES,
    Actor,
    ActorKind,
    Attempt,
    AttemptState,
    Base,
    Basis,
    Blocker,
    BlockerType,
    Budget,
    Event,
    EventType,
    Frontmatter,
    GateResult,
    Oracle,
    OracleResult,
    Receipt,
    ReceiptResult,
    Role,
    Route,
    Scope,
    Source,
    TaskState,
    TaskStateFile,
    TransitionError,
    Usage,
    check_attempt_transition,
    check_task_transition,
    utc_now,
)

# ---------------------------------------------------------------------------
# Oracle 1: transition soundness (exhaustive loops, not hypothesis)


def test_check_task_transition_exhaustive():
    for cur in TaskState:
        allowed = TASK_TRANSITIONS[cur]
        for nxt in TaskState:
            if nxt in allowed:
                check_task_transition(cur, nxt)  # must not raise
            else:
                with pytest.raises(TransitionError):
                    check_task_transition(cur, nxt)


def test_check_attempt_transition_exhaustive():
    for cur in AttemptState:
        allowed = ATTEMPT_TRANSITIONS[cur]
        for nxt in AttemptState:
            if nxt in allowed:
                check_attempt_transition(cur, nxt)  # must not raise
            else:
                with pytest.raises(TransitionError):
                    check_attempt_transition(cur, nxt)


def test_task_transition_graph_shape():
    assert set(TASK_TRANSITIONS) == set(TaskState)
    for s in TERMINAL_TASK_STATES:
        assert TASK_TRANSITIONS[s] == frozenset()
    for s in set(TaskState) - TERMINAL_TASK_STATES:
        assert TASK_TRANSITIONS[s] != frozenset(), f"{s} is non-terminal but has no successors"


def test_attempt_transition_graph_shape():
    assert set(ATTEMPT_TRANSITIONS) == set(AttemptState)
    for s in TERMINAL_ATTEMPT_STATES:
        assert ATTEMPT_TRANSITIONS[s] == frozenset()
    for s in set(AttemptState) - TERMINAL_ATTEMPT_STATES:
        assert ATTEMPT_TRANSITIONS[s] != frozenset(), f"{s} is non-terminal but has no successors"


# ---------------------------------------------------------------------------
# Oracle 2: serde round-trip fuzz — shared strategy building blocks

PRINTABLE = st.characters(min_codepoint=0x20, max_codepoint=0x7E)


def short_text(min_size: int = 0, max_size: int = 40):
    return st.text(alphabet=PRINTABLE, min_size=min_size, max_size=max_size)


def opt_text(min_size: int = 0, max_size: int = 40):
    return st.one_of(st.none(), short_text(min_size=min_size, max_size=max_size))


def id_text():
    return st.text(alphabet=PRINTABLE, min_size=1, max_size=20)


def key_text():
    return st.text(alphabet=PRINTABLE, min_size=1, max_size=20)


def utc_datetime():
    return st.datetimes(timezones=st.just(timezone.utc))


def json_scalar():
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-10**9, max_value=10**9),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        short_text(max_size=20),
    )


def json_value():
    return st.recursive(
        json_scalar(),
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(short_text(min_size=1, max_size=10), children, max_size=4),
        ),
        max_leaves=10,
    )


def json_dict():
    return st.dictionaries(short_text(min_size=1, max_size=10), json_value(), max_size=4)


@st.composite
def usage_strategy(draw):
    return Usage(
        basis=draw(st.sampled_from(list(Basis))),
        tokens_in=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=10_000_000))),
        tokens_out=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=10_000_000))),
        cached_in=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=10_000_000))),
        cost=draw(st.one_of(st.none(), st.floats(min_value=0, max_value=1e6, allow_nan=False,
                                                   allow_infinity=False))),
        currency=draw(opt_text(max_size=8)),
        price_rev=draw(opt_text()),
    )


@st.composite
def oracle_result_strategy(draw):
    return OracleResult(id=draw(id_text()), result=draw(st.sampled_from(["pass", "fail", "not-run"])))


@st.composite
def receipt_strategy(draw):
    return Receipt(
        result=draw(st.sampled_from(list(ReceiptResult))),
        exit_code=draw(st.integers(min_value=0, max_value=255)),
        oracles=draw(st.lists(oracle_result_strategy(), max_size=5)),
        blocked_reason=draw(opt_text()),
        files_touched=draw(st.lists(short_text(min_size=1), max_size=5)),
        head_commit=draw(opt_text(max_size=40)),
    )


@st.composite
def route_strategy(draw):
    return Route(
        route_id=draw(id_text()),
        cli=draw(short_text(min_size=1)),
        model=draw(short_text(min_size=1)),
        variant=draw(opt_text()),
        effort=draw(opt_text()),
        routes_rev=draw(opt_text()),
    )


@st.composite
def attempt_strategy(draw):
    return Attempt(
        attempt_id=draw(id_text()),
        role=draw(st.sampled_from(list(Role))),
        state=draw(st.sampled_from(list(AttemptState))),
        route=draw(route_strategy()),
        started=draw(utc_datetime()),
        ended=draw(st.one_of(st.none(), utc_datetime())),
        worktree=draw(opt_text()),
        branch=draw(opt_text()),
        base_commit=draw(opt_text()),
        pid=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=2**31 - 1))),
        pgid=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=2**31 - 1))),
        log_path=draw(opt_text()),
        session_handle=draw(opt_text()),
        receipt=draw(st.one_of(st.none(), receipt_strategy())),
        usage=draw(st.one_of(st.none(), usage_strategy())),
        wave_id=draw(opt_text()),
    )


@st.composite
def gate_result_strategy(draw):
    return GateResult(
        gate_id=draw(id_text()),
        phase=draw(st.sampled_from(["implementation", "review", "pre-merge", "post-merge"])),
        commit=draw(short_text(min_size=1)),
        exit_code=draw(st.integers(min_value=0, max_value=255)),
        started=draw(utc_datetime()),
        ended=draw(utc_datetime()),
        environment=draw(opt_text()),
        artifacts=draw(st.lists(short_text(min_size=1), max_size=5)),
    )


@st.composite
def blocker_strategy(draw):
    return Blocker(
        type=draw(st.sampled_from(list(BlockerType))),
        unblock_condition=draw(short_text(min_size=1)),
        detail=draw(opt_text()),
    )


@st.composite
def task_state_file_strategy(draw):
    return TaskStateFile(
        schema_version=draw(st.integers(min_value=1, max_value=5)),
        task_id=draw(id_text()),
        project=draw(short_text(min_size=1, max_size=20)),
        state=draw(st.sampled_from(list(TaskState))),
        since=draw(utc_datetime()),
        handoff_path=draw(opt_text()),
        wave_id=draw(opt_text()),
        paused=draw(st.booleans()),
        blocker=draw(st.one_of(st.none(), blocker_strategy())),
        attempts=draw(st.lists(attempt_strategy(), max_size=4)),
        gate_results=draw(st.lists(gate_result_strategy(), max_size=4)),
        leases_held=draw(st.lists(short_text(min_size=1), max_size=4)),
        progress_units=draw(st.lists(short_text(min_size=1), max_size=4)),
        merge_commit=draw(opt_text(max_size=40)),
        notes=draw(opt_text()),
    )


@st.composite
def actor_strategy(draw):
    return Actor(kind=draw(st.sampled_from(list(ActorKind))), id=draw(short_text(min_size=1)))


@st.composite
def event_strategy(draw):
    return Event(
        schema_version=draw(st.integers(min_value=1, max_value=5)),
        sequence=draw(st.integers(min_value=1, max_value=10_000)),
        timestamp=draw(utc_datetime()),
        project=draw(short_text(min_size=1, max_size=20)),
        actor=draw(actor_strategy()),
        type=draw(st.sampled_from(list(EventType))),
        payload=draw(json_dict()),
        task_id=draw(opt_text()),
        attempt_id=draw(opt_text()),
        wave_id=draw(opt_text()),
        decision_id=draw(opt_text()),
    )


@st.composite
def source_strategy(draw):
    return Source(
        kind=draw(st.sampled_from(["review", "backlog", "roadmap", "product-goal", "user", "spec-gap"])),
        ref=draw(opt_text()),
    )


@st.composite
def scope_strategy(draw):
    return Scope(
        touch=draw(st.lists(short_text(min_size=1), max_size=5)),
        forbid=draw(st.lists(short_text(min_size=1), max_size=5)),
    )


@st.composite
def oracle_strategy(draw):
    return Oracle(
        id=draw(id_text()),
        observable=draw(short_text()),
        negative=draw(short_text()),
        gate=draw(short_text(min_size=1)),
    )


@st.composite
def base_strategy(draw):
    return Base(branch=draw(short_text(min_size=1)), after=draw(opt_text()))


@st.composite
def budget_strategy(draw):
    return Budget(
        max_attempts=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=100))),
        max_wall_seconds=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=100_000))),
        max_cost=draw(st.one_of(st.none(), st.floats(min_value=0, max_value=1e6, allow_nan=False,
                                                       allow_infinity=False))),
        currency=draw(opt_text(max_size=8)),
    )


@st.composite
def frontmatter_strategy(draw):
    return Frontmatter(
        schema_version=draw(st.integers(min_value=1, max_value=5)),
        id=draw(id_text()),
        project=draw(short_text(min_size=1, max_size=20)),
        title=draw(short_text()),
        tier=draw(short_text(min_size=1)),
        input_revision=draw(short_text(min_size=1)),
        source=draw(source_strategy()),
        scope=draw(scope_strategy()),
        oracles=draw(st.lists(oracle_strategy(), max_size=5)),
        gates=draw(st.lists(short_text(min_size=1), max_size=5)),
        escalate_if=draw(st.lists(short_text(), max_size=5)),
        stack=draw(short_text(min_size=1, max_size=10)),
        mutexes=draw(st.lists(short_text(min_size=1), max_size=5)),
        depends_on=draw(st.lists(short_text(min_size=1), max_size=5)),
        base=draw(st.one_of(st.none(), base_strategy())),
        session=draw(short_text(min_size=1, max_size=10)),
        advances=draw(st.lists(short_text(min_size=1), max_size=5)),
        budget=draw(st.one_of(st.none(), budget_strategy())),
        carve_affinity=draw(opt_text()),
    )


@given(task_state_file_strategy())
@settings(max_examples=50, deadline=None)
def test_task_state_file_round_trip(tsf):
    d1 = tsf.to_dict()
    rt = TaskStateFile.from_dict(json.loads(json.dumps(d1)))
    assert rt.to_dict() == d1


@given(task_state_file_strategy(), key_text())
@settings(max_examples=50, deadline=None)
def test_task_state_file_unknown_key_rejected(tsf, key):
    d = tsf.to_dict()
    assume(key not in d)
    d[key] = "injected"
    with pytest.raises(ValueError):
        TaskStateFile.from_dict(d)


@given(event_strategy())
@settings(max_examples=50, deadline=None)
def test_event_round_trip(ev):
    d1 = ev.to_dict()
    rt = Event.from_dict(json.loads(json.dumps(d1)))
    assert rt.to_dict() == d1


@given(event_strategy(), key_text())
@settings(max_examples=50, deadline=None)
def test_event_unknown_key_rejected(ev, key):
    d = ev.to_dict()
    assume(key not in d)
    d[key] = "injected"
    with pytest.raises(ValueError):
        Event.from_dict(d)


@given(frontmatter_strategy())
@settings(max_examples=50, deadline=None)
def test_frontmatter_round_trip(fm):
    d1 = fm.to_dict()
    rt = Frontmatter.from_dict(json.loads(json.dumps(d1)))
    assert rt.to_dict() == d1


@given(frontmatter_strategy(), key_text())
@settings(max_examples=50, deadline=None)
def test_frontmatter_unknown_key_rejected(fm, key):
    d = fm.to_dict()
    assume(key not in d)
    d[key] = "injected"
    with pytest.raises(ValueError):
        Frontmatter.from_dict(d)


# ---------------------------------------------------------------------------
# Oracle 3: replay determinism over a random valid lifecycle
#
# Uses tmp_path_factory (session-scoped) + a manual os.environ contextmanager
# instead of the function-scoped `tmp_state` fixture, per the handoff's
# guidance: a function-scoped fixture would be reused (not rebuilt) across
# hypothesis examples within one test invocation.


@contextlib.contextmanager
def _state_root(path):
    prev = os.environ.get("HANDOFFCTL_STATE")
    os.environ["HANDOFFCTL_STATE"] = str(path)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("HANDOFFCTL_STATE", None)
        else:
            os.environ["HANDOFFCTL_STATE"] = prev


# (from_state, to_state) -> the EventType that carries that attempt
# transition. Only pairs that are valid per ATTEMPT_TRANSITIONS AND have a
# dedicated EventType are included (ABANDONED has no ATTEMPT_ABANDONED event
# in the frozen EventType enum, so the walk below never targets it).
_ATTEMPT_EVENT_FOR = {
    (AttemptState.CREATED, AttemptState.PREFLIGHTING): EventType.ATTEMPT_PREFLIGHTED,
    (AttemptState.PREFLIGHTING, AttemptState.RUNNING): EventType.ATTEMPT_STARTED,
    (AttemptState.RUNNING, AttemptState.STALLED): EventType.ATTEMPT_STALLED,
    (AttemptState.RUNNING, AttemptState.INTERRUPTED): EventType.ATTEMPT_INTERRUPTED,
    (AttemptState.RUNNING, AttemptState.EXITED): EventType.ATTEMPT_EXITED,
    (AttemptState.RUNNING, AttemptState.FAILED): EventType.ATTEMPT_FAILED,
    (AttemptState.STALLED, AttemptState.RUNNING): EventType.ATTEMPT_RESUMED,
    (AttemptState.STALLED, AttemptState.INTERRUPTED): EventType.ATTEMPT_INTERRUPTED,
    (AttemptState.STALLED, AttemptState.EXITED): EventType.ATTEMPT_EXITED,
    (AttemptState.STALLED, AttemptState.FAILED): EventType.ATTEMPT_FAILED,
    (AttemptState.INTERRUPTED, AttemptState.RUNNING): EventType.ATTEMPT_RESUMED,
}


@given(seed=st.integers(min_value=0, max_value=2**32 - 1), n_steps=st.integers(min_value=1, max_value=6))
@settings(max_examples=20, deadline=None)
def test_replay_determinism(seed, n_steps, tmp_path_factory):
    rng = random.Random(seed)
    state_root = tmp_path_factory.mktemp("replay")
    with _state_root(state_root):
        project = "replay-proj"
        actor = Actor(kind=ActorKind.TICK, id="prop-drill")
        task_id = "task-replay"
        states: dict[str, TaskStateFile] = {}

        init_tsf = TaskStateFile(
            schema_version=1, task_id=task_id, project=project,
            state=TaskState.CARVED, since=utc_now(),
        )
        storage.append_and_apply(
            project, states, actor=actor, type=EventType.TASK_CREATED,
            payload={"statefile": init_tsf.to_dict()}, task_id=task_id,
        )

        cur = TaskState.CARVED
        attempt_id = "att-replay"
        attempt_state: AttemptState | None = None
        route = Route(route_id="fake-cli", cli="fake", model="fake-model")
        attempt_started = None

        for i in range(n_steps):
            nxts = sorted(TASK_TRANSITIONS[cur], key=lambda s: s.value)
            if not nxts:
                break

            # Intersperse PROGRESS_RECORDED / LEASE_* events "in the mix".
            r = rng.random()
            if r < 0.25:
                storage.append_and_apply(
                    project, states, actor=actor, type=EventType.PROGRESS_RECORDED,
                    payload={"units": [f"unit-{i}"]}, task_id=task_id,
                )
            elif r < 0.45:
                storage.append_and_apply(
                    project, states, actor=actor, type=EventType.LEASE_ACQUIRED,
                    payload={"lease": "stack"}, task_id=task_id,
                )
            elif r < 0.6 and "stack" in states[task_id].leases_held:
                storage.append_and_apply(
                    project, states, actor=actor, type=EventType.LEASE_RELEASED,
                    payload={"lease": "stack"}, task_id=task_id,
                )

            nxt = rng.choice(nxts)
            storage.append_and_apply(
                project, states, actor=actor, type=EventType.TASK_TRANSITIONED,
                payload={"from": cur.value, "to": nxt.value, "notes": None}, task_id=task_id,
            )
            cur = nxt

            if attempt_state is None:
                attempt_state = AttemptState.CREATED
                attempt_started = utc_now()
                att = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER,
                              state=attempt_state, route=route, started=attempt_started)
                storage.append_and_apply(
                    project, states, actor=actor, type=EventType.ATTEMPT_CREATED,
                    payload={"attempt": att.to_dict()}, task_id=task_id, attempt_id=attempt_id,
                )
            else:
                options = sorted(
                    ((to, ev) for (frm, to), ev in _ATTEMPT_EVENT_FOR.items() if frm == attempt_state),
                    key=lambda p: p[0].value,
                )
                if options:
                    to_state, ev_type = rng.choice(options)
                    ended = utc_now() if to_state in TERMINAL_ATTEMPT_STATES else None
                    att = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER, state=to_state,
                                  route=route, started=attempt_started, ended=ended)
                    storage.append_and_apply(
                        project, states, actor=actor, type=ev_type,
                        payload={"attempt": att.to_dict()}, task_id=task_id, attempt_id=attempt_id,
                    )
                    attempt_state = to_state

        saved = storage.load_state(project, task_id)
        replayed = storage.replay(project)[task_id]
        assert replayed.to_dict() == saved.to_dict()


# ---------------------------------------------------------------------------
# Oracle 4: sequence integrity under concurrent append_event


def _append_worker(project: str, count: int, owner: str) -> None:
    actor = Actor(kind=ActorKind.TICK, id=owner)
    for i in range(count):
        storage.append_event(
            project, actor=actor, type=EventType.PROGRESS_RECORDED,
            payload={"units": [f"{owner}-{i}"]},
        )


def test_sequence_integrity_under_concurrency(tmp_state):
    project = "concurrency-demo"
    paths.ensure_layout(project)
    n_procs, n_each = 4, 25

    procs = [
        multiprocessing.Process(target=_append_worker, args=(project, n_each, f"w{i}"))
        for i in range(n_procs)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0, f"worker exited {p.exitcode}"

    lines = [ln for ln in paths.events_path(project).read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == n_procs * n_each

    seqs = [json.loads(ln)["sequence"] for ln in lines]
    assert len(seqs) == n_procs * n_each
    assert set(seqs) == set(range(1, n_procs * n_each + 1))


# ---------------------------------------------------------------------------
# Oracle 5: apply_event tolerance


def test_apply_event_attempt_started_never_created_upserts():
    states: dict[str, TaskStateFile] = {
        "t1": TaskStateFile(schema_version=1, task_id="t1", project="p",
                             state=TaskState.QUEUED, since=utc_now()),
    }
    att = Attempt(attempt_id="new-att", role=Role.IMPLEMENTER, state=AttemptState.RUNNING,
                  route=Route(route_id="r", cli="fake", model="m"), started=utc_now())
    ev = Event(
        schema_version=1, sequence=1, timestamp=utc_now(), project="p",
        actor=Actor(kind=ActorKind.WRAPPER, id="w"), type=EventType.ATTEMPT_STARTED,
        payload={"attempt": att.to_dict()}, task_id="t1", attempt_id="new-att",
    )
    affected = storage.apply_event(states, ev)
    assert affected == ["t1"]
    assert len(states["t1"].attempts) == 1
    assert states["t1"].attempts[0].attempt_id == "new-att"
    assert states["t1"].attempts[0].state is AttemptState.RUNNING


@given(
    task_id=st.text(alphabet=PRINTABLE, min_size=1, max_size=20),
    ev_type=st.sampled_from([t for t in EventType if t not in (EventType.TASK_CREATED, EventType.WAVE_OPENED)]),
)
@settings(max_examples=30, deadline=None)
def test_apply_event_unknown_task_is_noop(task_id, ev_type):
    states: dict[str, TaskStateFile] = {}
    ev = Event(
        schema_version=1, sequence=1, timestamp=utc_now(), project="p",
        actor=Actor(kind=ActorKind.TICK, id="t"), type=ev_type, payload={}, task_id=task_id,
    )
    affected = storage.apply_event(states, ev)
    assert affected == []
    assert states == {}


def test_apply_event_task_transitioned_violating_graph_raises():
    states: dict[str, TaskStateFile] = {
        "t1": TaskStateFile(schema_version=1, task_id="t1", project="p",
                             state=TaskState.COMPLETED, since=utc_now()),
    }
    ev = Event(
        schema_version=1, sequence=1, timestamp=utc_now(), project="p",
        actor=Actor(kind=ActorKind.TICK, id="t"), type=EventType.TASK_TRANSITIONED,
        payload={"from": "COMPLETED", "to": "QUEUED"}, task_id="t1",
    )
    with pytest.raises(TransitionError):
        storage.apply_event(states, ev)


@given(cur=st.sampled_from(list(TaskState)), to=st.sampled_from(list(TaskState)))
@settings(max_examples=50, deadline=None)
def test_apply_event_task_transition_enforces_graph(cur, to):
    assume(to not in TASK_TRANSITIONS[cur])
    states: dict[str, TaskStateFile] = {
        "t1": TaskStateFile(schema_version=1, task_id="t1", project="p", state=cur, since=utc_now()),
    }
    ev = Event(
        schema_version=1, sequence=1, timestamp=utc_now(), project="p",
        actor=Actor(kind=ActorKind.TICK, id="t"), type=EventType.TASK_TRANSITIONED,
        payload={"from": cur.value, "to": to.value}, task_id="t1",
    )
    with pytest.raises(TransitionError):
        storage.apply_event(states, ev)

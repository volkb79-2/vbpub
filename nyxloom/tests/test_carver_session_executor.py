"""F018 P3a — persistent carver session executor (Start + Resume) tests.

Covers docs/plan-long-running-carver.md §12 Package 3's Start/Resume half:
daemon._execute_start_carver_session / _execute_resume_carver_session (the
LAUNCH-time half, mirroring _execute_carve_dispatch's own shape) and
daemon._consume_carver_session_exit (the EXIT-CONSUMPTION half, mirroring
_consume_carve_exit's own shape but deciding CARVER_SESSION_STARTED/RESUMED
vs CARVER_SESSION_DEGRADED from the turn's real outcome, never from process
exit alone).

Test technique (mirrors tests/test_carver.py + tests/test_daemon.py's own
established convention for this exact codebase, NOT a real subprocess):
wrapper.launch_detached is monkeypatched (a real double-forked OS spawn of
argv[0]="fake" would need a PATH shim, is slow/racy, and buys nothing extra
-- adapters.build_dispatch/build_resume are pure and run for REAL). A turn's
outcome (the wrapper's own async post-launch capture_session + its receipt)
is simulated by directly mutating the resulting Attempt's session_handle/
receipt and persisting via storage.save_state -- the SAME technique
test_behavioral.py's test_transient_throttle_resumes_same_attempt_end_to_end
uses for session_handle ("the fake CLI has no real session-capture wiring
... so session_handle is seeded directly"), and test_carver.py's
_seed_carve_task/_write_receipt use for a CARVER attempt's outcome.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dataclasses import replace as dc_replace

from nyxloom import carver_session, daemon, lint, paths, reconcile, storage
from nyxloom.config import ProjectConfig, register_project
from nyxloom.types import (
    Actor, ActorKind, AttemptState, Blocker, BlockerType, CarverStatus, EventType,
    Receipt, ReceiptResult, Role, TaskState, TaskStateFile, utc_now,
)

# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py -- see its own
# docstring; every test_*.py in this suite keeps local helpers local)

CARVER_PROJECT_TOML = """\
[project]
id = "demo"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]
infra_globs = ["infra/**"]

[gates.pytest-q]
argv = ["true"]
phase = "implementation"
timeout_seconds = 60
environment = "local"

[mutexes.stack]
scope = "project"
capacity = 1

[policy]
max_active_tasks = 2
ready_queue_target = 3
carve_authority = "files"

[stage.carve]
session = "project-persistent"
retain_merge_digests = 5

[notify]
"""

# Same as CARVER_PROJECT_TOML but WITHOUT carve_authority="files" -- the
# policy default ("branch") is what exercises _ensure_worktree's real git
# worktree/branch minting (daemon.py L3396-3400/3500-3504) rather than
# running every launch at cfg.root.
CARVER_PROJECT_TOML_BRANCH_AUTHORITY = """\
[project]
id = "demo"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]
infra_globs = ["infra/**"]

[gates.pytest-q]
argv = ["true"]
phase = "implementation"
timeout_seconds = 60
environment = "local"

[mutexes.stack]
scope = "project"
capacity = 1

[policy]
max_active_tasks = 2
ready_queue_target = 3

[stage.carve]
session = "project-persistent"
retain_merge_digests = 5

[notify]
"""

# role_default="review-independent" makes for_role(...) resolve this SAME
# route for the Start launch; its own route_id is what gets pinned into
# CARVER_SESSION_STARTED's route snapshot and re-resolved (pinned, not
# re-selected) on every Resume.
CARVER_ROUTES_TOML = """\
revision = "test-p3a"

[tiers.flash-high]
routes = ["fake-cli"]

[routes.fake-cli]
cli = "fake"
model = "fake-model"
probe = ["true"]
usage_source = "none"
role_default = "review-independent"
resume = ["fake", "--resume", "{session}", "{prompt}"]
"""


def _make_carver_repo(tmp_path: Path, project_toml: str) -> Path:
    root = tmp_path / "demo-repo"
    (root / ".nyxloom").mkdir(parents=True)
    (root / "handoff").mkdir()
    (root / "docs").mkdir()
    (root / ".nyxloom" / "project.toml").write_text(project_toml)
    (root / "docs" / "DECISIONS-INBOX.md").write_text("# Decisions inbox\n\n---\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=root, check=True)
    paths.routes_path().write_text(CARVER_ROUTES_TOML)
    register_project("demo", root)
    paths.ensure_layout("demo")
    return root


@pytest.fixture()
def carver_project(tmp_state, tmp_path) -> ProjectConfig:
    """A registered git repo whose project.toml already opts into
    cfg.carve.session == "project-persistent" (carve_authority="files" so
    every launch runs at cfg.root -- no worktree/branch minting noise)."""
    root = _make_carver_repo(tmp_path, CARVER_PROJECT_TOML)
    return ProjectConfig.load(root)


@pytest.fixture()
def carver_project_branch_authority(tmp_state, tmp_path) -> ProjectConfig:
    """Same repo shape, but carve_authority defaults to "branch" -- exercises
    the real _ensure_worktree git worktree/branch minting path."""
    root = _make_carver_repo(tmp_path, CARVER_PROJECT_TOML_BRANCH_AUTHORITY)
    return ProjectConfig.load(root)


@pytest.fixture()
def patch_launch(monkeypatch):
    """Stub only wrapper.launch_detached (build_dispatch/build_resume are
    pure and run for real -- see module docstring). Records every spec."""
    calls: list = []

    def fake_launch_detached(spec):
        calls.append(spec)
        Path(spec.attempt_dir).mkdir(parents=True, exist_ok=True)
        return 4242

    import nyxloom.wrapper as wrapper_mod
    monkeypatch.setattr(wrapper_mod, "launch_detached", fake_launch_detached)
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    return calls


def _scripted(monkeypatch, sequence):
    """monkeypatch reconcile.plan_project to pop one actions-list per call
    (extra calls get []); local twin of test_daemon.py's/test_carver.py's
    own identical helper (per STANDING.md, local fixtures never move to
    conftest.py or get imported across test files)."""
    seq = list(sequence)

    def fake(inp):
        if seq:
            return seq.pop(0)
        return []

    monkeypatch.setattr(reconcile, "plan_project", fake)


def _mark_turn_outcome(project: str, task_id: str, attempt_id: str, *,
                       session_handle: str | None, result: ReceiptResult) -> None:
    """Simulate 'the wrapper's async post-launch capture_session ran, and
    the CLI process exited' -- see module docstring for why this (not a
    real subprocess) is this suite's established technique."""
    tsf = storage.load_state(project, task_id)
    attempt = tsf.attempt_by_id(attempt_id)
    attempt.state = AttemptState.EXITED
    attempt.session_handle = session_handle
    attempt.receipt = Receipt(result=result, exit_code=0 if result is ReceiptResult.DONE else 1)
    storage.save_state(tsf)


def _write_receipt(project: str, attempt_id: str, result: ReceiptResult = ReceiptResult.DONE) -> None:
    """For the true-E2E tests that drive EmitAttemptExit through run_pass:
    the outer EmitAttemptExit handler reads receipt.json off disk (healing
    path) BEFORE dispatching to the role-specific consumer -- mirrors
    test_carver.py's identical helper."""
    d = paths.attempt_dir(project, attempt_id)
    d.mkdir(parents=True, exist_ok=True)
    receipt = Receipt(result=result, exit_code=0 if result is ReceiptResult.DONE else 1)
    (d / "receipt.json").write_text(json.dumps(receipt.to_dict()), encoding="utf-8")


def _snapshot(project: str) -> carver_session.CarverSessionSnapshot:
    return carver_session.project_session(storage.iter_events(project))


# ==========================================================================
# _execute_start_carver_session -- launch half (plan §5.1 items 1-4)
# ==========================================================================

def test_start_carver_session_launches_bootstrap_under_strategic_carver_lease(
        tmp_state, carver_project, patch_launch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.StartCarverSession(project="demo", mode="headroom")

    events = d._execute_start_carver_session("demo", cfg, states, action)

    types = [e.type for e in events]
    assert types == [EventType.TASK_CREATED, EventType.ATTEMPT_CREATED,
                      EventType.ATTEMPT_PREFLIGHTED]
    assert len(patch_launch) == 1
    spec = patch_launch[0]
    assert spec.leases == [{"name": "demo.strategic-carver", "capacity": 1}]
    assert spec.cwd == str(cfg.root)  # carve_authority="files" -> no worktree

    task_id = events[0].task_id
    assert task_id.startswith("carver-session-demo-")
    tsf = states[task_id]
    assert tsf.state is TaskState.ACTIVE
    assert len(tsf.attempts) == 1
    assert tsf.attempts[0].role is Role.CARVER
    assert "kind=start" in tsf.notes
    assert "generation=1" in tsf.notes

    # bootstrap packet was written and points at the durable spine (§2.5) --
    # by pointer, never inlined content.
    packet_path = paths.attempt_dir("demo", tsf.attempts[0].attempt_id) / "packet" / "packet.md"
    text = packet_path.read_text(encoding="utf-8")
    assert "reference/AUTHORING.md" in text
    assert "docs/DECISIONS-INBOX.md" in text
    assert "READ-ONLY" in text


def test_start_carver_session_no_route_emits_needs_operator_no_launch(
        tmp_state, carver_project, patch_launch, monkeypatch):
    cfg = carver_project
    # No route flagged/tiered for review-independent -> for_role([]) empty.
    empty_routes_toml = "revision = \"empty\"\n"
    paths.routes_path().write_text(empty_routes_toml)
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.StartCarverSession(project="demo")

    events = d._execute_start_carver_session("demo", cfg, states, action)

    assert len(events) == 1
    assert events[0].type is EventType.NEEDS_OPERATOR
    assert events[0].payload == {"reason": "carver-no-route"}
    assert patch_launch == []


def test_start_carver_session_refuses_when_carve_already_in_flight(
        tmp_state, carver_project, patch_launch):
    """P55/R5-style execution-time recheck (plan §5.1 item 1): a live
    Role.CARVER attempt on ANY non-terminal task refuses a second launch,
    cleanly (no event, no wrapper launch) -- mirrors reconcile.py's own
    carve_in_flight planner gate, recomputed fresh at execution time."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    # First launch occupies the slot.
    d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    assert len(patch_launch) == 1

    events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))

    assert events == []
    assert len(patch_launch) == 1  # no second launch


def test_start_carver_session_refuses_on_stale_warm_snapshot(
        tmp_state, carver_project, patch_launch):
    """Execution-time recheck of the planner's own status gate: if the
    durable snapshot is already WARM (e.g. a prior pass's bootstrap already
    completed) by the time this action executes, refuse cleanly rather than
    starting a second generation."""
    cfg = carver_project
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_SESSION_STARTED,
        payload={"generation": 1, "session_id": "S1", "route": {"route_id": "fake-cli"},
                 "spine_revisions": {}},
    )
    d = daemon.Daemon({"demo": cfg.root})
    events = d._execute_start_carver_session(
        "demo", cfg, {}, reconcile.StartCarverSession(project="demo"))

    assert events == []
    assert patch_launch == []


def test_start_carver_session_refuses_when_paused(tmp_state, carver_project, patch_launch):
    cfg = carver_project
    paths.pause_flag("demo").write_text("drain-agents")
    d = daemon.Daemon({"demo": cfg.root})
    events = d._execute_start_carver_session(
        "demo", cfg, {}, reconcile.StartCarverSession(project="demo"))
    assert events == []
    assert patch_launch == []


# ==========================================================================
# _execute_resume_carver_session -- launch half (plan §5.2)
# ==========================================================================

def _bootstrap_to_warm(d: daemon.Daemon, cfg: ProjectConfig, patch_launch,
                      session_id: str = "S1") -> str:
    """Drive one full Start->consume cycle to a WARM S1 session. Returns
    the bootstrap turn's task_id."""
    states: dict = {}
    events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id,
                       session_handle=session_id, result=ReceiptResult.DONE)
    states = storage.list_states("demo")
    d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)
    assert _snapshot("demo").status is CarverStatus.WARM
    assert _snapshot("demo").session_id == session_id
    return task_id


def test_resume_carver_session_pins_route_and_reuses_session_id(
        tmp_state, carver_project, patch_launch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch)
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.ResumeCarverSession(
        project="demo", mode="merge-feed", source_ids=("merge:demo:1",), generation=1)
    events = d._execute_resume_carver_session("demo", cfg, states, action)

    types = [e.type for e in events]
    assert types == [EventType.TASK_CREATED, EventType.ATTEMPT_CREATED,
                      EventType.ATTEMPT_PREFLIGHTED]
    assert len(patch_launch) == 1
    argv = patch_launch[0].argv
    assert argv == ["fake", "--resume", "S1", argv[-1]]
    assert "merge:demo:1" in argv[-1]

    task_id = events[0].task_id
    assert task_id.startswith("carver-session-demo-")
    tsf = states[task_id]
    assert "kind=resume" in tsf.notes
    assert "mode=merge-feed" in tsf.notes


def test_resume_carver_session_recover_mode_requires_degraded_status(
        tmp_state, carver_project, patch_launch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch)  # status is WARM, not DEGRADED
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.ResumeCarverSession(project="demo", mode="recover", generation=1)
    events = d._execute_resume_carver_session("demo", cfg, states, action)

    assert events == []
    assert patch_launch == []


def test_resume_carver_session_unresolvable_pinned_route_needs_operator(
        tmp_state, carver_project, patch_launch, monkeypatch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch)
    patch_launch.clear()
    # routes.toml changed since bootstrap -- the pinned route_id no longer resolves.
    paths.routes_path().write_text("revision = \"v2\"\n")

    states = storage.list_states("demo")
    action = reconcile.ResumeCarverSession(project="demo", mode="merge-feed",
                                           source_ids=("d1",), generation=1)
    events = d._execute_resume_carver_session("demo", cfg, states, action)

    assert len(events) == 1
    assert events[0].type is EventType.NEEDS_OPERATOR
    assert events[0].payload == {"reason": "carver-no-route"}
    assert patch_launch == []


# ==========================================================================
# _consume_carver_session_exit -- exit-consumption half (plan §5.1 items
# 5-7 / §5.2)
# ==========================================================================

def test_consume_start_success_emits_started_and_supersedes_turn_task(
        tmp_state, carver_project, patch_launch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    launch_events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    started = [e for e in events if e.type is EventType.CARVER_SESSION_STARTED]
    assert len(started) == 1
    assert started[0].payload["generation"] == 1
    assert started[0].payload["session_id"] == "S1"
    assert started[0].payload["route"]["route_id"] == "fake-cli"
    assert started[0].payload["spine_revisions"] == {}  # no spine docs configured in this fixture

    superseded = [e for e in events if e.type is EventType.TASK_SUPERSEDED]
    assert len(superseded) == 1
    assert storage.load_state("demo", task_id).state is TaskState.SUPERSEDED

    snap = _snapshot("demo")
    assert snap.status is CarverStatus.WARM
    assert snap.session_id == "S1"
    assert snap.generation == 1


def test_consume_start_capture_failure_emits_degraded_never_started(
        tmp_state, carver_project, patch_launch):
    """THE negative oracle: a capture failure (no session_handle) must
    never record STARTED/WARM, even though the process itself exited
    cleanly (receipt DONE) -- plan §5.1: 'Never record warm based only on
    process exit.'"""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    launch_events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle=None,
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_STARTED for e in events)
    degraded = [e for e in events if e.type is EventType.CARVER_SESSION_DEGRADED]
    assert len(degraded) == 1

    snap = _snapshot("demo")
    assert snap.status is CarverStatus.DEGRADED
    assert snap.status is not CarverStatus.WARM
    assert snap.session_id is None


def test_consume_start_turn_error_receipt_emits_degraded(
        tmp_state, carver_project, patch_launch):
    """Companion negative: a captured session_handle but a non-DONE receipt
    (provider error/limit/blocked) is ALSO not a valid bootstrap ack."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    launch_events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.ERROR)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_STARTED for e in events)
    assert any(e.type is EventType.CARVER_SESSION_DEGRADED for e in events)
    assert _snapshot("demo").status is CarverStatus.DEGRADED


def test_consume_resume_success_emits_resumed_and_reuses_sticky_session_id(
        tmp_state, carver_project, patch_launch):
    """'Resume reuses S1 with a fresh turn-id' oracle."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    resume_events = d._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="merge-feed",
                                      source_ids=("d1",), generation=1))
    task_id = resume_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    # The resumed turn's OWN capture may or may not re-affirm a session id --
    # this fixture's fake route has no real capture wiring either way, so a
    # fresh, different id is deliberately seeded to prove RESUMED's payload
    # never overwrites the sticky projected session_id (§4.2 payload only
    # {generation, route} by contract).
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1-turn-2",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    resumed = [e for e in events if e.type is EventType.CARVER_SESSION_RESUMED]
    assert len(resumed) == 1
    assert set(resumed[0].payload.keys()) == {"generation", "route"}
    assert resumed[0].payload["generation"] == 1

    snap = _snapshot("demo")
    assert snap.status is CarverStatus.WARM
    assert snap.session_id == "S1"  # unchanged -- sticky, never overwritten by RESUMED


def test_consume_resume_failure_emits_degraded(tmp_state, carver_project, patch_launch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    resume_events = d._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="targeted-intake",
                                      source_ids=("i1",), generation=1))
    task_id = resume_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle=None,
                       result=ReceiptResult.ERROR)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)
    assert any(e.type is EventType.CARVER_SESSION_DEGRADED for e in events)
    assert _snapshot("demo").status is CarverStatus.DEGRADED
    # session_id is preserved by the projector (DEGRADED never clears it) --
    # a later bounded recovery resumes the SAME S1, not a fresh generation.
    assert _snapshot("demo").session_id == "S1"


# ==========================================================================
# True end-to-end (via _scripted + d.run_pass, mirroring test_carver.py's
# own EmitAttemptExit-consumption convention): bootstrap -> WARM; two
# resumes (a second carve-equivalent turn and a feed) both target S1;
# daemon "restart" (a fresh Daemon instance) still resumes S1.
# ==========================================================================

def test_e2e_bootstrap_reaches_warm_via_run_pass(
        tmp_state, carver_project, patch_launch, monkeypatch):
    cfg = carver_project
    _scripted(monkeypatch, [[reconcile.StartCarverSession(project="demo")]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    states = storage.list_states("demo")
    task_id = next(iter(states))
    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert any(e.type is EventType.CARVER_SESSION_STARTED for e in events)
    assert not any(e.type is EventType.TICK_ERROR for e in events)
    assert _snapshot("demo").status is CarverStatus.WARM
    assert storage.load_state("demo", task_id).state is TaskState.SUPERSEDED


def test_e2e_two_resumes_both_target_same_s1_with_distinct_turn_ids(
        tmp_state, carver_project, patch_launch):
    """'a second carve and a feed both resume the same S1' oracle."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")

    seen_sessions = []
    seen_task_ids = []
    for mode, source_ids in (("merge-feed", ("d1",)), ("targeted-intake", ("i1",))):
        patch_launch.clear()
        states = storage.list_states("demo")
        events = d._execute_resume_carver_session(
            "demo", cfg, states,
            reconcile.ResumeCarverSession(project="demo", mode=mode,
                                          source_ids=source_ids, generation=1))
        task_id = events[0].task_id
        attempt_id = states[task_id].attempts[0].attempt_id
        seen_sessions.append(patch_launch[0].argv[2])  # ["fake", "--resume", "<session>", ...]
        seen_task_ids.append((task_id, attempt_id))
        _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                           result=ReceiptResult.DONE)
        states = storage.list_states("demo")
        d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert seen_sessions == ["S1", "S1"]
    # distinct turn ids -- the persistent session is a cache, never an
    # evidence identity (§5.2).
    assert seen_task_ids[0] != seen_task_ids[1]
    assert len({tid for tid, _ in seen_task_ids}) == 2
    assert len({aid for _, aid in seen_task_ids}) == 2
    assert _snapshot("demo").session_id == "S1"


def test_e2e_daemon_restart_still_resumes_s1(tmp_state, carver_project, patch_launch):
    """Nothing about the session lives in daemon memory -- a FRESH Daemon
    instance, rebuilding its snapshot purely from durable events, still
    resumes the same S1."""
    cfg = carver_project
    d1 = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d1, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    d2 = daemon.Daemon({"demo": cfg.root})  # simulated restart: brand new instance
    states = storage.list_states("demo")
    events = d2._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="merge-feed",
                                      source_ids=("d1",), generation=1))
    assert len(patch_launch) == 1
    assert patch_launch[0].argv[2] == "S1"
    assert events[0].type is EventType.TASK_CREATED


# ==========================================================================
# Lease contention: reconcile.py's OWN (unmodified) FAILED-attempt handling
# already generically frees a Role.CARVER slot on a lease-lost-race -- see
# reconcile.py's Transition(to=SUPERSEDED, notes="carve attempt failed
# (lease-race/spawn) ...") branch. This proves a carver-session task
# integrates with that EXISTING mechanism for free: the loser records no
# CARVER_SESSION_* event, advances no cursor, and creates no new generation.
# ==========================================================================

def test_lease_lost_race_loser_creates_no_generation_advances_no_cursor(
        tmp_state, carver_project, patch_launch, monkeypatch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    launch_events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id

    # Simulate the WRAPPER's own lease-lost-race outcome (wrapper.py step 2,
    # unchanged/unread here beyond its documented contract): ATTEMPT_FAILED,
    # never ATTEMPT_EXITED.
    tsf = storage.load_state("demo", task_id)
    attempt = tsf.attempt_by_id(attempt_id)
    attempt.state = AttemptState.FAILED
    attempt.receipt = Receipt(result=ReceiptResult.ERROR, exit_code=75,
                              blocked_reason="lease-lost-race")
    storage.save_state(tsf)

    baseline_snapshot = _snapshot("demo")  # ABSENT -- no STARTED landed yet

    _scripted(monkeypatch, [[reconcile.Transition(
        task_id=task_id, to=TaskState.SUPERSEDED,
        notes="carve attempt failed (lease-race/spawn) -- freeing carve slot")]])
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type.name.startswith("CARVER_SESSION_") for e in events)
    assert storage.load_state("demo", task_id).state is TaskState.SUPERSEDED

    final_snapshot = _snapshot("demo")
    assert final_snapshot.status is baseline_snapshot.status is CarverStatus.ABSENT
    assert final_snapshot.generation == baseline_snapshot.generation == 0
    assert final_snapshot.last_consumed_event_sequence == 0


# ==========================================================================
# Branch-authority worktree minting (daemon.py's _ensure_worktree, real git)
# and the ResumeCarverSession _execute dispatch branch (only ever exercised
# via a real _scripted + run_pass round-trip in the tests above for Start --
# this covers the identical wiring for Resume).
# ==========================================================================

def test_start_and_resume_under_branch_authority_mint_real_worktrees(
        tmp_state, carver_project_branch_authority, patch_launch):
    cfg = carver_project_branch_authority
    d = daemon.Daemon({"demo": cfg.root})

    launch_events = d._execute_start_carver_session(
        "demo", cfg, {}, reconcile.StartCarverSession(project="demo"))
    task_id = launch_events[0].task_id
    spec = patch_launch[0]
    assert spec.cwd != str(cfg.root)
    worktree = Path(spec.cwd)
    assert worktree.exists()
    assert (worktree / ".git").exists()

    states = storage.list_states("demo")
    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")
    d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)
    patch_launch.clear()

    states = storage.list_states("demo")
    resume_events = d._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="merge-feed",
                                      source_ids=("d1",), generation=1))
    resume_spec = patch_launch[0]
    assert resume_spec.cwd != str(cfg.root)
    assert Path(resume_spec.cwd).exists()
    assert resume_events[0].type is EventType.TASK_CREATED


def test_e2e_resume_dispatch_branch_via_run_pass(
        tmp_state, carver_project, patch_launch, monkeypatch):
    """Covers the ResumeCarverSession branch in _execute's isinstance
    dispatch chain (only Start was exercised via run_pass above)."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    _scripted(monkeypatch, [[reconcile.ResumeCarverSession(
        project="demo", mode="merge-feed", source_ids=("d1",), generation=1)]])
    d.run_pass("demo")

    assert len(patch_launch) == 1
    assert patch_launch[0].argv[2] == "S1"
    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in events)


# ==========================================================================
# Recover mode (bounded DEGRADED recovery, plan §2.4/§5.4): a DEGRADED
# snapshot resumes mode="recover", which also exercises _build_carver_
# resume_prompt's "recover" branch.
# ==========================================================================

def test_resume_recover_mode_launches_from_degraded_status(
        tmp_state, carver_project, patch_launch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_SESSION_DEGRADED,
        payload={"reason": "turn-failed"},
    )
    assert _snapshot("demo").status is CarverStatus.DEGRADED
    assert _snapshot("demo").session_id == "S1"  # DEGRADED never clears it

    states = storage.list_states("demo")
    events = d._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="recover", generation=1))

    assert len(patch_launch) == 1
    assert patch_launch[0].argv[2] == "S1"
    assert "Recover this session" in patch_launch[0].argv[-1]
    assert events[0].type is EventType.TASK_CREATED
    assert "kind=resume" in events[0].payload["statefile"]["notes"]
    assert "mode=recover" in events[0].payload["statefile"]["notes"]


def test_resume_carver_session_refuses_when_paused(
        tmp_state, carver_project, patch_launch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()
    paths.pause_flag("demo").write_text("drain-agents")

    states = storage.list_states("demo")
    events = d._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="merge-feed",
                                      source_ids=("d1",), generation=1))
    assert events == []
    assert patch_launch == []


# ==========================================================================
# Small pure-helper edge cases (spine hashing, digest-id bound/error paths,
# bootstrap-packet task annotations, turn-marker fallbacks).
# ==========================================================================

def test_spine_revisions_hashes_existing_paths_and_skips_missing_ones(
        tmp_state, carver_project):
    cfg = carver_project
    (cfg.root / "docs" / "NORTH-STAR.md").write_text("north star content\n")
    cfg2 = dc_replace(cfg, north_star="docs/NORTH-STAR.md",
                      product_definition="docs/MISSING-DOC.md")  # never written
    d = daemon.Daemon({"demo": cfg.root})

    revisions = d._spine_revisions(cfg2)

    assert "north_star" in revisions
    assert len(revisions["north_star"]) == 64  # sha256 hex digest
    assert "product_definition" not in revisions  # missing file -> skipped
    assert "roadmap" not in revisions  # not configured -> skipped


def test_recent_merge_digest_ids_zero_limit_and_storage_error_degrade_to_empty(
        tmp_state, carver_project, monkeypatch):
    d = daemon.Daemon({"demo": carver_project.root})
    assert d._recent_merge_digest_ids("demo", 0) == []

    def _boom(project, since=0):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(storage, "iter_events", _boom)
    assert d._recent_merge_digest_ids("demo", 5) == []


def test_bootstrap_packet_annotates_terminal_handoff_and_blocker_tasks(
        tmp_state, carver_project):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states = {
        "demo-done": TaskStateFile(
            schema_version=1, task_id="demo-done", project="demo",
            state=TaskState.COMPLETED, since=utc_now()),
        "demo-active": TaskStateFile(
            schema_version=1, task_id="demo-active", project="demo",
            state=TaskState.ACTIVE, since=utc_now(),
            handoff_path="handoff/demo-active.md"),
        "demo-blocked": TaskStateFile(
            schema_version=1, task_id="demo-blocked", project="demo",
            state=TaskState.BLOCKED, since=utc_now(),
            blocker=Blocker(type=BlockerType.CONTRACT,
                            unblock_condition="operator decision",
                            detail="needs a decision")),
    }

    text = d._build_carver_bootstrap_packet(cfg, "demo", 1, states)

    assert "demo-done" not in text  # terminal task -- skipped
    assert "demo-active" in text and "handoff/demo-active.md" in text
    assert "demo-blocked" in text and "[blocked: contract]" in text


def test_carver_turn_marker_defaults_and_malformed_generation(tmp_state, carver_project):
    d = daemon.Daemon({"demo": carver_project.root})

    assert d._carver_turn_marker({}, "does-not-exist") == ("start", "headroom", 0)

    tsf_no_notes = TaskStateFile(
        schema_version=1, task_id="t1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), notes=None)
    assert d._carver_turn_marker({"t1": tsf_no_notes}, "t1") == ("start", "headroom", 0)

    tsf_malformed = TaskStateFile(
        schema_version=1, task_id="t2", project="demo",
        state=TaskState.ACTIVE, since=utc_now(),
        notes="carver-session seq=1 kind=resume mode=recover generation=NaN")
    kind, mode, generation = d._carver_turn_marker({"t2": tsf_malformed}, "t2")
    assert (kind, mode, generation) == ("resume", "recover", 0)


# ==========================================================================
# Feature-off byte-identical (structural, but asserted): with
# cfg.carve.session == "fresh" (default), the planner never emits Start/
# ResumeCarverSession, so _execute never takes the new branches -- a
# representative run_pass produces no CARVER_SESSION_* event and no
# TICK_ERROR (which an unhandled-action ValueError would surface as).
# ==========================================================================

def test_feature_off_run_pass_never_touches_new_branches(tmp_state, sample_project):
    """sample_project (tests/conftest.py) has NO [stage.carve] override --
    cfg.carve.session defaults to "fresh"."""
    cfg = sample_project
    assert cfg.carve.session == "fresh"
    d = daemon.Daemon({"demo": cfg.root})

    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type.name.startswith("CARVER_SESSION_") for e in events)
    assert not any(e.type is EventType.TICK_ERROR for e in events)
    assert carver_session.project_session(events).status is CarverStatus.ABSENT

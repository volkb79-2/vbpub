"""F018 P3a/P3c — persistent carver session executor tests.

Covers docs/plan-long-running-carver.md §12 Package 3's Start/Resume half
(P3a): daemon._execute_start_carver_session / _execute_resume_carver_session
(the LAUNCH-time half, mirroring _execute_carve_dispatch's own shape) and
daemon._consume_carver_session_exit (the EXIT-CONSUMPTION half, mirroring
_consume_carve_exit's own shape but deciding CARVER_SESSION_STARTED/RESUMED
vs CARVER_SESSION_DEGRADED from the turn's real outcome, never from process
exit alone).

Also covers Package 3's PRODUCING half (P3c, appended below the P3a
sections): the _execute_carve_dispatch NORMALIZE branch
(_execute_carve_via_session_resume) that turns a legacy CarveDispatch into a
write-authority carve-mode session RESUME when the persistent session is
WARM, and _consume_carver_session_exit's extension that parses the turn's
CarverTurnResult envelope and emits CARVER_PROPOSAL_RECORDED (or degrades on
a missing/malformed one) -- closing the loop P3b (validation/admission)
already consumes.

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
_seed_carve_task/_write_receipt use for a CARVER attempt's outcome. P3c's
own fake carve TURN (writing a handoff + a CarverTurnResult envelope) is
simulated the same way: the test writes the files directly rather than
running a real CLI, mirroring how test_carver_proposal_admission.py
synthesizes CARVER_PROPOSAL_RECORDED for P3b's own tests.
"""

from __future__ import annotations

import hashlib
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


def _write_bootstrap_ack(d: daemon.Daemon, cfg: ProjectConfig,
                          attempt: Attempt) -> None:
    """Write a valid BOOTSTRAP-ACK.json for the given attempt's start/recover
    turn, satisfying P3d ACK validation (kind=start or kind=resume+mode=recover
    require it). The ack echoes the current spine revisions."""
    ack_path = d._carver_bootstrap_ack_path(cfg, attempt)
    ack_path.parent.mkdir(parents=True, exist_ok=True)
    spine = d._spine_revisions(cfg)
    ack_path.write_text(json.dumps(
        {"kind": "bootstrap-ack", "spine_revisions": spine}), encoding="utf-8")


_HANDOFF_TEMPLATE = """---
schema_version: 1
id: {id}
project: demo
title: Sample bounded package
tier: flash-high
input_revision: "{input_revision}"
source: {{kind: roadmap, ref: docs/DECISIONS-INBOX.md}}
scope:
  touch: ["src/demo/thing.py", "tests/test_thing.py"]
oracles:
  - id: O1
    observable: "pytest tests/test_thing.py::test_bound passes"
    negative: "a value over the limit raises BoundError (test_bound_violation)"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Sample bounded package

Worktree: none (carve_authority=files). Branch: feat/{id}. Context to read first: docs/DECISIONS-INBOX.md.
Out of scope: everything else; do not touch forbidden files.

Contract body. If a named contract cannot be met as specified, STOP, write
`BLOCKED: <reason>` to the LOG, commit, exit.
"""


def _write_handoff(root: Path, id_: str, *, input_revision: str = "abc1234") -> tuple[str, str]:
    """Write a real handoff file under root/handoff/, mirroring
    test_carver_proposal_admission.py's own identical local helper (never
    imported across test files, per this suite's convention). Returns
    (relpath, sha256)."""
    text = _HANDOFF_TEMPLATE.format(id=id_, input_revision=input_revision)
    p = root / "handoff" / f"{id_}.md"
    p.write_text(text)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    return f"handoff/{id_}.md", sha


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
    the bootstrap turn's task_id. Writes the required BOOTSTRAP-ACK.json
    to simulate the carver's acknowledgement."""
    states: dict = {}
    events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id

    # Write the BOOTSTRAP-ACK.json the carver would produce (P3d ACK
    # validation requires it for kind=start turns).
    attempt = states[task_id].attempt_by_id(attempt_id)
    _write_bootstrap_ack(d, cfg, attempt)

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


def test_resume_carver_session_refuses_when_carve_already_in_flight(
        tmp_state, carver_project, patch_launch):
    """Symmetric with the Start-side recheck: a live (not-yet-consumed)
    carver-session turn already occupies the pass's single carve slot, so a
    second Resume launch refuses cleanly."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch)
    patch_launch.clear()

    states = storage.list_states("demo")
    first = d._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="merge-feed",
                                      source_ids=("d1",), generation=1))
    assert len(first) == 3  # occupies the slot -- left ACTIVE, not consumed
    assert len(patch_launch) == 1

    second = d._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="targeted-intake",
                                      source_ids=("i1",), generation=1))
    assert second == []
    assert len(patch_launch) == 1  # no second launch


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

    # Write BOOTSTRAP-ACK.json (P3d ACK validation requires it for start).
    attempt = states[task_id].attempt_by_id(attempt_id)
    _write_bootstrap_ack(d, cfg, attempt)

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
    # never overwrites the sticky projected session_id.
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1-turn-2",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    resumed = [e for e in events if e.type is EventType.CARVER_SESSION_RESUMED]
    assert len(resumed) == 1
    # F018 P3c (plan §2.2 fold-in): RESUMED now also carries mode/turn_id/
    # source_ids, not just {generation, route}.
    assert set(resumed[0].payload.keys()) == {
        "generation", "route", "mode", "turn_id", "source_ids"}
    assert resumed[0].payload["generation"] == 1
    assert resumed[0].payload["mode"] == "merge-feed"
    assert resumed[0].payload["turn_id"] == task_id
    assert resumed[0].payload["source_ids"] == ["d1"]

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
    # Write BOOTSTRAP-ACK.json (P3d ACK validation requires it for start).
    states = storage.list_states("demo")
    attempt = states[task_id].attempt_by_id(attempt_id)
    _write_bootstrap_ack(d, cfg, attempt)

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
    # Write BOOTSTRAP-ACK.json (P3d ACK validation requires it for start).
    attempt = states[task_id].attempt_by_id(attempt_id)
    _write_bootstrap_ack(d, cfg, attempt)
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

    assert d._carver_turn_marker({}, "does-not-exist") == ("start", "headroom", 0, ())

    tsf_no_notes = TaskStateFile(
        schema_version=1, task_id="t1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), notes=None)
    assert d._carver_turn_marker({"t1": tsf_no_notes}, "t1") == ("start", "headroom", 0, ())

    tsf_malformed = TaskStateFile(
        schema_version=1, task_id="t2", project="demo",
        state=TaskState.ACTIVE, since=utc_now(),
        notes="carver-session seq=1 kind=resume mode=recover generation=NaN")
    kind, mode, generation, source_ids = d._carver_turn_marker({"t2": tsf_malformed}, "t2")
    assert (kind, mode, generation, source_ids) == ("resume", "recover", 0, ())

    # F018 P3c: a real 'sources=' token round-trips via comma-split.
    tsf_sourced = TaskStateFile(
        schema_version=1, task_id="t3", project="demo",
        state=TaskState.ACTIVE, since=utc_now(),
        notes="carver-session seq=2 kind=resume mode=merge-feed generation=1 "
              "sources=merge:demo:1,merge:demo:2")
    marker = d._carver_turn_marker({"t3": tsf_sourced}, "t3")
    assert marker == ("resume", "merge-feed", 1, ("merge:demo:1", "merge:demo:2"))


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


# ==========================================================================
# F018 P3c -- the NORMALIZE branch: _execute_carve_dispatch, when the
# persistent session is WARM, launches a write-authority carve-mode SESSION
# RESUME (_execute_carve_via_session_resume) instead of a fresh throwaway
# carve. Mirrors the _execute_resume_carver_session section's own test
# shape/fixtures above.
# ==========================================================================

def _carve_envelope(task_id: str, *, generation: int = 1, mode: str = "headroom",
                     artifacts: list | None = None, dispositions: list | None = None,
                     base_revision: str = "abc1234", outcome: str = "CANDIDATES_READY",
                     headroom_estimate: int = 0) -> dict:
    """A well-formed CarverTurnResult envelope dict (plan §4.1) -- the exact
    shape _parse_carver_turn_result/_validate_carve_proposal_payload both
    consume. Mirrors test_carver_proposal_admission.py's own
    _proposal_payload helper (never imported across test files)."""
    return {
        "kind": "carve-proposal", "schema_version": 1,
        "proposal_id": f"demo:carve:{generation}:{task_id}", "turn_id": task_id,
        "source": {"mode": mode, "refs": [], "base_revision": base_revision,
                   "merge_digest_cursor": 0},
        "artifacts": artifacts or [], "dispositions": dispositions or [],
        "outcome": outcome, "headroom_estimate": headroom_estimate,
    }


def _write_envelope(report_dir: Path, seq: str, envelope: dict) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"CARVER-PROPOSAL-{seq}.json").write_text(
        json.dumps(envelope), encoding="utf-8")


def test_carve_dispatch_normalizes_to_session_resume_when_warm(
        tmp_state, carver_project, patch_launch):
    """Headline routing oracle: with a WARM persistent session, a headroom
    CarveDispatch is normalized to a carve-mode session RESUME (task_id
    prefix carver-session-, argv is --resume S1 ...) instead of minting the
    legacy fresh-carve task_id prefix carve-."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    types = [e.type for e in events]
    assert types == [EventType.TASK_CREATED, EventType.ATTEMPT_CREATED,
                      EventType.ATTEMPT_PREFLIGHTED]
    task_id = events[0].task_id
    assert task_id.startswith("carver-session-demo-")
    tsf = states[task_id]
    assert "kind=resume" in tsf.notes
    assert "mode=carve" in tsf.notes
    assert "sources=(none)" in tsf.notes

    assert len(patch_launch) == 1
    argv = patch_launch[0].argv
    assert argv == ["fake", "--resume", "S1", argv[-1]]
    # the carve-mode prompt carries the SAME source-context vocabulary the
    # legacy dispatch packet uses, plus the NEW envelope contract.
    assert "Carve sources" in argv[-1]
    assert "carve-proposal" in argv[-1]
    assert f"demo:carve:1:{task_id}" in argv[-1]


def _seed_ready_to_carve_origin(project: str) -> dict:
    states = storage.list_states(project)
    states["demo-origin"] = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-origin", project=project,
        state=TaskState.READY_TO_CARVE, since=utc_now())
    storage.save_state(states["demo-origin"])
    return storage.list_states(project)


def test_carve_dispatch_normalize_rescope_carries_context_no_launch_time_supersede(
        tmp_state, carver_project, patch_launch):
    """AD1 (2026-07-25 adversarial review): a normalized re-scope
    CarveDispatch (action.task_id set) normalizes the SAME way and carries
    the rejected task's context into the prompt, but -- UNLIKE the legacy
    B7 launch-time supersede -- does NOT supersede the origin at launch.
    Plan §4.2 tightens B7: supersession waits until a replacement is
    validated/admitted (P3b's _execute_admit_carve_proposal), never merely
    after a carve launches. The origin stays READY_TO_CARVE right after
    this launch."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = _seed_ready_to_carve_origin("demo")

    action = reconcile.CarveDispatch(project="demo", task_id="demo-origin")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    created = next(e for e in events if e.type is EventType.TASK_CREATED)
    task_id = created.task_id
    assert task_id.startswith("carver-session-demo-")
    tsf = states[task_id]
    assert "mode=carve" in tsf.notes
    assert "rescope-of=demo-origin" in tsf.notes
    assert "sources=demo-origin" in tsf.notes

    # AD1: NO TASK_SUPERSEDED for the origin at launch time.
    assert [e for e in events if e.type is EventType.TASK_SUPERSEDED] == []
    assert storage.load_state("demo", "demo-origin").state is TaskState.READY_TO_CARVE

    argv = patch_launch[0].argv
    assert "RE-SCOPING" in argv[-1]
    assert "demo-origin" in argv[-1]
    assert '"result": "handoff"' in argv[-1]  # the disposition instruction for a re-carve


def test_ad1_empty_proposal_from_normalized_rescope_leaves_origin_ready_to_carve(
        tmp_state, carver_project, patch_launch):
    """AD1 regression (data-loss oracle): a normalized re-scope carve turn
    that produces an EMPTY/invalid envelope must leave the origin
    READY_TO_CARVE -- not superseded with nothing to replace it. Covers
    BOTH a structurally-empty-artifacts envelope (AD2's own 'carved
    nothing' case) and a fully missing envelope (turn-failed-to-produce)."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = _seed_ready_to_carve_origin("demo")
    action = reconcile.CarveDispatch(project="demo", task_id="demo-origin")
    launch_events = d._execute_carve_dispatch("demo", cfg, states, action)
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id

    # The carve turn produced NO usable envelope at all (e.g. the carver
    # decided the rejection was unfixable and only opened a decision).
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")
    d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    # No proposal was ever recorded, so a real run_pass has nothing to
    # admit -- the origin must still be READY_TO_CARVE, never superseded.
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in events)
    assert not any(e.type is EventType.CARVER_PROPOSAL_RECORDED for e in events)
    assert [e for e in events if e.type is EventType.TASK_SUPERSEDED
            and e.task_id == "demo-origin"] == []
    assert storage.load_state("demo", "demo-origin").state is TaskState.READY_TO_CARVE


def test_ad1_valid_rescope_proposal_supersedes_origin_only_on_admission(
        tmp_state, carver_project, patch_launch):
    """AD1 happy path: a VALID re-scope proposal naming the origin in its
    disposition supersedes the origin only after a SECOND, real run_pass
    admits it -- proving supersession moved from launch to admission."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = _seed_ready_to_carve_origin("demo")
    action = reconcile.CarveDispatch(project="demo", task_id="demo-origin")
    launch_events = d._execute_carve_dispatch("demo", cfg, states, action)
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id

    relpath, sha = _write_handoff(cfg.root, "demo-P902")
    envelope = _carve_envelope(
        task_id, mode="rescope",
        artifacts=[{"kind": "handoff", "path": relpath, "sha256": sha,
                   "source_ref": "demo-origin"}],
        dispositions=[{"source_ref": "demo-origin", "result": "handoff",
                       "artifact_ref": relpath, "reason_code": "re-scoped"}])
    seq = task_id.rsplit("-", 1)[-1]
    _write_envelope(cfg.root / cfg.reports_dir, seq, envelope)

    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")
    consume_events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)
    assert any(e.type is EventType.CARVER_PROPOSAL_RECORDED for e in consume_events)

    # Right after consumption (before admission), the origin is UNCHANGED.
    assert storage.load_state("demo", "demo-origin").state is TaskState.READY_TO_CARVE

    # Second, unstubbed pass: validates + admits, THEN supersedes the origin.
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in events)
    assert any(e.type is EventType.CARVER_PROPOSAL_ADMITTED for e in events)
    assert storage.load_state("demo", "demo-origin").state is TaskState.SUPERSEDED
    sup = [e for e in events if e.type is EventType.TASK_SUPERSEDED
           and e.task_id == "demo-origin"]
    assert len(sup) == 1
    assert sup[0].payload["outcome"] == "RESCOPED"


def test_carve_dispatch_normalize_targeted_intake_sub_mode(
        tmp_state, carver_project, patch_launch):
    """A targeted carve (action.item_id set) normalizes with sub_mode
    'targeted-intake' and carries the item id through notes/source_ids."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", item_id="B27")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    task_id = events[0].task_id
    tsf = states[task_id]
    assert "mode=carve" in tsf.notes
    assert "item=B27" in tsf.notes
    assert "sources=B27" in tsf.notes
    assert "B27" in patch_launch[0].argv[-1]


def test_carve_dispatch_normalize_test_health_sub_mode(
        tmp_state, carver_project, patch_launch):
    """A test-health CarveDispatch (action.kind='test-health') normalizes
    with the SAME test-suite-focused source section the legacy packet
    builder uses."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="test-health")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    assert "TEST-HEALTH" in patch_launch[0].argv[-1]


def test_carve_dispatch_falls_back_to_fresh_carve_when_no_session_yet(
        tmp_state, carver_project, patch_launch):
    """Feature ON but NO session exists yet (ABSENT) -- CarveDispatch must
    NOT normalize; with P3d the executor refuses cleanly (no side effect)
    rather than falling through to legacy fresh-carve while feature-on and
    not-WARM. The planner should emit StartCarverSession instead."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.CarveDispatch(project="demo", kind="headroom")

    events = d._execute_carve_dispatch("demo", cfg, states, action)

    # Feature-on with ABSENT session: no legacy carve, no TASK_CREATED.
    assert not any(e.type is EventType.TASK_CREATED for e in events)
    assert patch_launch == []


def test_carve_dispatch_rotates_when_session_degraded_exhausted(
        tmp_state, carver_project, patch_launch):
    """A DEGRADED (not WARM) session with exhausted recovery rotates instead
    of falling through to the legacy fresh-carve path. O3: emits
    CARVER_SESSION_ROTATED{reason:"degraded-recovery-exhausted"}, creates NO
    carve task."""
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

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    # Rotates, no legacy carve task minted.
    rotated = [e for e in events if e.type is EventType.CARVER_SESSION_ROTATED]
    assert len(rotated) == 1
    assert rotated[0].payload["reason"] == "degraded-recovery-exhausted"
    assert not any(e.type is EventType.TASK_CREATED for e in events)
    assert _snapshot("demo").status is CarverStatus.ROTATING


def test_carve_dispatch_byte_identical_when_feature_off(
        tmp_state, sample_project, patch_launch):
    """cfg.carve.session == 'fresh' (default, sample_project has no
    [stage.carve] override): _carver_session's MASTER GATE returns None, so
    the normalize branch is never entered -- the fresh-carve path (legacy
    CARVE-<seq>.md REQUIRED OUTPUT CONTRACT) runs exactly as it did before
    P3c, and no CARVER_PROPOSAL_RECORDED is ever produced.

    sample_project's OWN routes.toml (conftest.SAMPLE_ROUTES_TOML) has no
    role_default, so review_routes would be empty and the legacy path would
    short-circuit on 'carve-no-route' before ever reaching TASK_CREATED --
    mirrors test_daemon.py's OWN identical setup for every other
    _execute_carve_dispatch test using sample_project (e.g.
    test_execute_carve_dispatch_untargeted_supersedes_nothing)."""
    from conftest import SAMPLE_ROUTES_TOML
    cfg = sample_project
    assert cfg.carve.session == "fresh"
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.CarveDispatch(project="demo", kind="headroom")

    events = d._execute_carve_dispatch("demo", cfg, states, action)

    task_id = next(e.task_id for e in events if e.type is EventType.TASK_CREATED)
    assert task_id.startswith("carve-demo-")
    assert not task_id.startswith("carver-session-")
    packet_path = (paths.attempt_dir("demo", states[task_id].attempts[0].attempt_id)
                   / "packet" / "packet.md")
    text = packet_path.read_text(encoding="utf-8")
    assert "REQUIRED OUTPUT CONTRACT" in text
    assert "CARVE-" in text and ".md" in text  # legacy contract, not the P3c envelope
    assert "carve-proposal" not in text


def test_normalize_branch_unresolvable_pinned_route_needs_operator(
        tmp_state, carver_project, patch_launch):
    """Mirrors _execute_resume_carver_session's own equivalent oracle: the
    normalize branch resolves the session's PINNED route, never re-selects
    via for_role -- a routes.toml drift since bootstrap needs-operators
    cleanly instead of silently launching under a stale route."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()
    paths.routes_path().write_text("revision = \"v2\"\n")

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    assert len(events) == 1
    assert events[0].type is EventType.NEEDS_OPERATOR
    assert events[0].payload == {"reason": "carver-no-route"}
    assert patch_launch == []


def test_branch_authority_normalize_reads_envelope_at_worktree_report_path(
        tmp_state, carver_project_branch_authority, patch_launch):
    """Under branch authority, the envelope's expected path is resolved via
    the SAME worktree-prefix logic _consume_carve_exit uses for
    CARVE-<seq>.md (P51/P58) -- proves the normalize/consume path
    integrates with real git worktrees, not just carve_authority='files'."""
    cfg = carver_project_branch_authority
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    launch_events = d._execute_carve_dispatch("demo", cfg, states, action)
    task_id = launch_events[0].task_id
    worktree = Path(states[task_id].attempts[0].worktree)
    assert worktree.resolve() != cfg.root.resolve()

    seq = task_id.rsplit("-", 1)[-1]
    # carver_project_branch_authority is a REPO-ROOT project (worktree_root
    # = '.worktrees'), so `git rev-parse --show-prefix` from cfg.root is ""
    # -- the report lives at <worktree>/<reports_dir>, no extra segment.
    # AD2 (2026-07-25): this oracle asserts CARVER_PROPOSAL_RECORDED, so the
    # envelope must carry a NON-empty artifacts list -- an empty-artifacts
    # envelope is a legitimate "carved nothing" no-op that records RESUMED but
    # no proposal (see test_ad2_carved_nothing_turn_records_resumed_not_proposal_
    # stays_warm). The consume-time parse is STRUCTURAL-only (no artifact
    # hash/file check -- that is P3b admission's job), so a synthetic artifact
    # dict suffices to prove the worktree-prefix path resolution found+recorded it.
    _write_envelope(worktree / cfg.reports_dir, seq, _carve_envelope(
        task_id, artifacts=[{"kind": "handoff", "path": "handoff/demo-P901.md",
                             "sha256": "0" * 64, "source_ref": None}]))

    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert any(e.type is EventType.CARVER_PROPOSAL_RECORDED for e in events)


# ==========================================================================
# F018 P3c -- _consume_carver_session_exit's extension: parse the
# CarverTurnResult envelope a write-authority turn wrote and emit
# CARVER_PROPOSAL_RECORDED (or degrade on a missing/malformed one).
# ==========================================================================

def test_write_authority_turn_missing_envelope_degrades_no_proposal(
        tmp_state, carver_project, patch_launch):
    """A DONE turn with a captured session but NO envelope file at all --
    'nothing usable landed' -- degrades the session; never a half-proposal."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    launch_events = d._execute_carve_dispatch("demo", cfg, states, action)
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)
    assert not any(e.type is EventType.CARVER_PROPOSAL_RECORDED for e in events)
    degraded = [e for e in events if e.type is EventType.CARVER_SESSION_DEGRADED]
    assert len(degraded) == 1
    assert degraded[0].payload["reason"] == "proposal-invalid"
    assert degraded[0].payload["mode"] == "carve"
    assert _snapshot("demo").status is CarverStatus.DEGRADED


def test_write_authority_turn_malformed_envelope_degrades_no_proposal(
        tmp_state, carver_project, patch_launch):
    """A present-but-structurally-invalid envelope (missing required
    CarverTurnResult fields) is treated the SAME as a missing one --
    CarverTurnResult.from_dict rejects it, never a half-proposal."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    launch_events = d._execute_carve_dispatch("demo", cfg, states, action)
    task_id = launch_events[0].task_id
    seq = task_id.rsplit("-", 1)[-1]
    # Missing required fields (artifacts/dispositions/outcome/source/...) --
    # CarverTurnResult.from_dict raises TypeError (missing positional args).
    _write_envelope(cfg.root / cfg.reports_dir, seq,
                     {"kind": "carve-proposal", "schema_version": 1})

    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)
    assert not any(e.type is EventType.CARVER_PROPOSAL_RECORDED for e in events)
    assert any(e.type is EventType.CARVER_SESSION_DEGRADED for e in events)
    assert _snapshot("demo").status is CarverStatus.DEGRADED


def test_read_only_resume_turn_never_records_proposal_even_if_envelope_present(
        tmp_state, carver_project, patch_launch):
    """Defense in depth: a read-only mode (merge-feed) must NEVER attempt
    to parse/record a proposal, even if an envelope-shaped file happens to
    sit at the exact path a write-authority turn would use."""
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
    seq = task_id.rsplit("-", 1)[-1]
    _write_envelope(cfg.root / cfg.reports_dir, seq,
                     _carve_envelope(task_id, mode="merge-feed"))

    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)
    assert not any(e.type is EventType.CARVER_PROPOSAL_RECORDED for e in events)


def test_ad2_carved_nothing_turn_records_resumed_not_proposal_stays_warm(
        tmp_state, carver_project, patch_launch):
    """AD2 (2026-07-25 adversarial review): a STRUCTURALLY VALID envelope
    with an EMPTY artifacts list ("carved nothing this turn" -- explicitly
    authorized by the carve prompt, mirroring the legacy 'carved: []'
    allowance) is a legitimate no-op, not a proposal. P3b's
    _validate_carve_proposal_payload rejects an empty-artifacts payload and
    counts it toward the bounded repair budget, so recording one here for a
    healthy idle carver would spuriously burn that budget. Assert: RESUMED
    is recorded (the turn itself succeeded), NO CARVER_PROPOSAL_RECORDED,
    the session stays WARM, and a real run_pass afterward never escalates
    NEEDS_OPERATOR{carver-proposal-invalid}."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    launch_events = d._execute_carve_dispatch("demo", cfg, states, action)
    task_id = launch_events[0].task_id
    seq = task_id.rsplit("-", 1)[-1]
    envelope = _carve_envelope(task_id, artifacts=[], dispositions=[],
                               outcome="ROADMAP_EXHAUSTED", headroom_estimate=0)
    _write_envelope(cfg.root / cfg.reports_dir, seq, envelope)

    attempt_id = states[task_id].attempts[0].attempt_id
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)
    assert not any(e.type is EventType.CARVER_PROPOSAL_RECORDED for e in events)
    assert not any(e.type is EventType.CARVER_SESSION_DEGRADED for e in events)
    assert _snapshot("demo").status is CarverStatus.WARM

    # A real pass afterward finds nothing to validate/repair-escalate.
    d.run_pass("demo")
    later = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in later)
    assert not any(e.type is EventType.NEEDS_OPERATOR
                   and e.payload.get("reason") == "carver-proposal-invalid" for e in later)


# ==========================================================================
# F018 P3c headline oracle -- THE FULL LOOP, end to end: normalize -> the
# fake carve turn authors a handoff + CarverTurnResult envelope -> consume
# parses it and records CARVER_PROPOSAL_RECORDED -> a SECOND, UNSTUBBED
# run_pass (real reconcile.plan_project) validates it (P3b's
# _validated_carve_proposals) and admits it (AdmitCarveProposal) ->
# TASK_CREATED + CARVER_PROPOSAL_ADMITTED. Mirrors
# test_carver_proposal_admission.py's own test_ad1_real_run_pass_admits_
# and_supersedes_origin, but the RECORDING half is now produced by THIS
# package instead of being hand-synthesized.
# ==========================================================================

def test_full_loop_normalize_record_validate_admit_end_to_end(
        tmp_state, carver_project, patch_launch):
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    launch_events = d._execute_carve_dispatch("demo", cfg, states, action)
    task_id = launch_events[0].task_id
    assert task_id.startswith("carver-session-demo-")
    attempt_id = states[task_id].attempts[0].attempt_id

    # Simulate the carve turn: author a real handoff + its CarverTurnResult
    # envelope, exactly as the REQUIRED OUTPUT CONTRACT the resume prompt
    # dictates instructs a real carver to do.
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    seq = task_id.rsplit("-", 1)[-1]
    envelope = _carve_envelope(
        task_id, artifacts=[{"kind": "handoff", "path": relpath, "sha256": sha,
                             "source_ref": None}],
        headroom_estimate=3)
    _write_envelope(cfg.root / cfg.reports_dir, seq, envelope)

    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")
    consume_events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    resumed = [e for e in consume_events if e.type is EventType.CARVER_SESSION_RESUMED]
    assert len(resumed) == 1
    recorded = [e for e in consume_events if e.type is EventType.CARVER_PROPOSAL_RECORDED]
    assert len(recorded) == 1
    assert recorded[0].payload["proposal_id"] == envelope["proposal_id"]
    assert storage.load_state("demo", task_id).state is TaskState.SUPERSEDED

    # Second pass: REAL reconcile.plan_project (no _scripted stub) validates
    # and admits the recorded proposal.
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in events)
    admitted = [e for e in events if e.type is EventType.CARVER_PROPOSAL_ADMITTED]
    assert len(admitted) == 1
    assert admitted[0].payload["proposal_id"] == envelope["proposal_id"]
    created = [e for e in events if e.type is EventType.TASK_CREATED
               and e.task_id == "demo-P901"]
    assert len(created) == 1
    assert storage.load_state("demo", "demo-P901").state is TaskState.CARVED


# ==========================================================================
# F018 P3d oracle tests (O1-O7)
# ==========================================================================

# --- O1: AF1 rotate-fallback ---

def test_o1_compact_rotate_fallback_emits_rotated_no_launch(
        tmp_state, carver_project, patch_launch):
    """O1 positive: with WARM session at generation N and CompactCarverSession
    action, _execute_compact_carver_session returns exactly one
    CARVER_SESSION_ROTATED with new_generation == N+1 and no wrapper launch."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()
    snap = _snapshot("demo")
    gen_before = snap.generation

    states = storage.list_states("demo")
    action = reconcile.CompactCarverSession(
        project="demo", generation=gen_before, trigger="turns")
    events = d._execute_compact_carver_session("demo", cfg, states, action)

    rotated = [e for e in events if e.type is EventType.CARVER_SESSION_ROTATED]
    assert len(rotated) == 1
    assert rotated[0].payload["new_generation"] == gen_before + 1
    assert rotated[0].payload["reason"] == "compaction-rotate-fallback"
    assert rotated[0].payload["trigger"] == "turns"
    assert rotated[0].payload["from_generation"] == gen_before
    assert patch_launch == []
    assert _snapshot("demo").status is CarverStatus.ROTATING


def test_o1_compact_unsupported_strategy_emits_needs_operator_no_rotate(
        tmp_state, carver_project, patch_launch):
    """O1 negative: with compaction_strategy set to an unsupported driver
    name, it emits NEEDS_OPERATOR{carver-compaction-no-driver} and does NOT
    rotate."""
    cfg = dc_replace(carver_project)
    cfg.carve.compaction_strategy = "some-driver"  # unsupported
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()
    gen_before = _snapshot("demo").generation

    states = storage.list_states("demo")
    action = reconcile.CompactCarverSession(
        project="demo", generation=gen_before, trigger="turns")
    events = d._execute_compact_carver_session("demo", cfg, states, action)

    assert not any(e.type is EventType.CARVER_SESSION_ROTATED for e in events)
    needs_op = [e for e in events if e.type is EventType.NEEDS_OPERATOR]
    assert len(needs_op) == 1
    assert needs_op[0].payload["reason"] == "carver-compaction-no-driver"
    assert _snapshot("demo").status is CarverStatus.WARM  # not rotated


# --- O2: routing regression ---

def test_o2_compact_carver_session_no_longer_raises_value_error(
        tmp_state, carver_project, patch_launch):
    """O2: _execute dispatch of CompactCarverSession no longer raises
    ValueError (would surface as TICK_ERROR)."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    # Drive through run_pass with a CompactCarverSession action.
    from unittest.mock import patch as mock_patch
    with mock_patch.object(d, "_execute_compact_carver_session",
                           return_value=[]):
        # If the _execute dispatch doesn't have a CompactCarverSession
        # branch, this would raise ValueError.
        action = reconcile.CompactCarverSession(
            project="demo", generation=1, trigger="turns")
        events = d._execute("demo", cfg, {}, action)
        # No ValueError means the branch exists.
        assert events == []


# --- O3: concern-3 fall-through closed ---

def test_o3_degraded_exhausted_rotates_no_carve_task(
        tmp_state, carver_project, patch_launch):
    """O3 positive: DEGRADED with exhausted recovery, CarveDispatch
    (headroom) → CARVER_SESSION_ROTATED, no carve task, no TASK_SUPERSEDED."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()
    # Push the session to DEGRADED (simulating exhausted recovery).
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_SESSION_DEGRADED,
        payload={"reason": "turn-failed"},
    )
    assert _snapshot("demo").status is CarverStatus.DEGRADED

    states = storage.list_states("demo")
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    rotated = [e for e in events if e.type is EventType.CARVER_SESSION_ROTATED]
    assert len(rotated) == 1
    assert rotated[0].payload["reason"] == "degraded-recovery-exhausted"
    assert not any(e.type is EventType.TASK_CREATED for e in events)
    assert not any(e.type is EventType.TASK_SUPERSEDED for e in events)
    assert _snapshot("demo").status is CarverStatus.ROTATING


def test_o3_rescope_stays_ready_to_carve_on_degraded_rotation(
        tmp_state, carver_project, patch_launch):
    """O3: a re-scope CarveDispatch with task_id= on a DEGRADED session
    rotates WITHOUT superseding the origin (stays READY_TO_CARVE)."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_SESSION_DEGRADED,
        payload={"reason": "turn-failed"},
    )

    states = _seed_ready_to_carve_origin("demo")
    action = reconcile.CarveDispatch(project="demo", task_id="demo-origin")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    assert any(e.type is EventType.CARVER_SESSION_ROTATED for e in events)
    assert not any(e.type is EventType.TASK_CREATED for e in events)
    assert not any(e.type is EventType.TASK_SUPERSEDED for e in events)
    # Origin stays READY_TO_CARVE.
    assert storage.load_state("demo", "demo-origin").state is TaskState.READY_TO_CARVE


def test_o3_feature_off_fresh_carve_unchanged(
        tmp_state, sample_project, patch_launch):
    """O3 negative: session=='fresh' (feature off), CarveDispatch runs
    legacy fresh carve path (byte-identical)."""
    from conftest import SAMPLE_ROUTES_TOML
    cfg = sample_project
    assert cfg.carve.session == "fresh"
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    # Legacy path: a TASK_CREATED with carve- prefix is minted.
    task_ids = [e.task_id for e in events if e.type is EventType.TASK_CREATED]
    assert any(tid.startswith("carve-demo-") for tid in task_ids)


# --- O4: ack validation ---

def test_o4_start_no_ack_degrades(
        tmp_state, carver_project, patch_launch):
    """O4 negative: a START turn that is DONE + captured session but writes
    NO BOOTSTRAP-ACK.json → CARVER_SESSION_DEGRADED{bootstrap-ack-invalid},
    never STARTED/WARM."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    launch_events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    # Do NOT write the ACK file.
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_STARTED for e in events)
    degraded = [e for e in events if e.type is EventType.CARVER_SESSION_DEGRADED]
    assert len(degraded) == 1
    assert degraded[0].payload["reason"] == "bootstrap-ack-invalid"
    assert _snapshot("demo").status is CarverStatus.DEGRADED


def test_o4_start_valid_ack_reaches_warm(
        tmp_state, carver_project, patch_launch):
    """O4 positive: a START turn with a valid, spine-matching ACK →
    CARVER_SESSION_STARTED, WARM."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    launch_events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id
    attempt = states[task_id].attempt_by_id(attempt_id)
    _write_bootstrap_ack(d, cfg, attempt)
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert any(e.type is EventType.CARVER_SESSION_STARTED for e in events)
    assert _snapshot("demo").status is CarverStatus.WARM


def test_o4_recover_no_ack_degrades(
        tmp_state, carver_project, patch_launch):
    """O4: a recover-mode resume without valid ACK → DEGRADED with
    bootstrap-ack-invalid reason."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    # Push to DEGRADED.
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_SESSION_DEGRADED,
        payload={"reason": "turn-failed"},
    )

    # Launch a recover-mode resume.
    states = storage.list_states("demo")
    resume_events = d._execute_resume_carver_session(
        "demo", cfg, states,
        reconcile.ResumeCarverSession(project="demo", mode="recover", generation=1))
    task_id = resume_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id

    # Remove any BOOTSTRAP-ACK.json left by _bootstrap_to_warm.
    attempt = states[task_id].attempt_by_id(attempt_id)
    ack_path = d._carver_bootstrap_ack_path(cfg, attempt)
    if ack_path.exists():
        ack_path.unlink()

    # Consume WITHOUT ACK → should degrade.
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")
    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)
    degraded = [e for e in events if e.type is EventType.CARVER_SESSION_DEGRADED]
    assert len(degraded) >= 1
    assert degraded[-1].payload["reason"] == "bootstrap-ack-invalid"


def test_o4_merge_feed_unaffected_by_ack(
        tmp_state, carver_project, patch_launch):
    """O4 negative: a merge-feed turn (read-only, non-recover) requires NO
    ack and is byte-identical to P3a."""
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
    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_DEGRADED for e in events)
    assert any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)
    assert _snapshot("demo").status is CarverStatus.WARM


# --- O5: debounce ---

def test_o5_carver_no_route_debounced(
        tmp_state, carver_project, patch_launch):
    """O5: two consecutive passes with an unresolvable pinned route emit
    NEEDS_OPERATOR{carver-no-route} on the FIRST pass only."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    # Make routes empty so the pinned route is unresolvable.
    paths.routes_path().write_text("revision = \"v2\"\n")

    states = storage.list_states("demo")
    action = reconcile.ResumeCarverSession(
        project="demo", mode="merge-feed", source_ids=("d1",), generation=1)

    # First pass: emits NEEDS_OPERATOR.
    events1 = d._execute_resume_carver_session("demo", cfg, states, action)
    needs1 = [e for e in events1 if e.type is EventType.NEEDS_OPERATOR]
    assert len(needs1) == 1
    assert needs1[0].payload["reason"] == "carver-no-route"
    # Persist the events so the debounce scanner sees them.
    for ev in events1:
        storage.append_and_apply("demo", states, actor=ev.actor, type=ev.type,
                                  payload=ev.payload, task_id=ev.task_id)

    # Second pass (same episode, no rotation/started in between): suppressed.
    events2 = d._execute_resume_carver_session("demo", cfg, states, action)
    needs2 = [e for e in events2 if e.type is EventType.NEEDS_OPERATOR]
    assert needs2 == []


def test_o5_debounce_re_armed_by_rotation(
        tmp_state, carver_project, patch_launch):
    """O5: a ROTATED between two unresolvable passes re-arms the debounce;
    a later unresolved pass may escalate again."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    _bootstrap_to_warm(d, cfg, patch_launch, session_id="S1")
    patch_launch.clear()

    paths.routes_path().write_text("revision = \"v2\"\n")

    states = storage.list_states("demo")
    action = reconcile.ResumeCarverSession(
        project="demo", mode="merge-feed", source_ids=("d1",), generation=1)

    # First pass emits.
    events1 = d._execute_resume_carver_session("demo", cfg, states, action)
    assert any(e.type is EventType.NEEDS_OPERATOR for e in events1)
    # Persist events.
    for ev in events1:
        storage.append_and_apply("demo", states, actor=ev.actor, type=ev.type,
                                  payload=ev.payload, task_id=ev.task_id)

    # Emit a ROTATED event (simulating a new generation).
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_SESSION_ROTATED,
        payload={"new_generation": 2, "reason": "test"},
    )

    # After rotation, the debounce helper should be re-armed: the same
    # reason emitted again returns True for a fresh NEEDS_OPERATOR.
    assert not d._needs_operator_recently_emitted("demo", "carver-no-route")
    # Emit a fresh NEEDS_OPERATOR (post-rotation, re-armed).
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.NEEDS_OPERATOR,
        payload={"reason": "carver-no-route"},
    )
    assert d._needs_operator_recently_emitted("demo", "carver-no-route")


# --- O6: enablement WARN ---

def test_o6_enablement_warn_emitted_once(tmp_state, carver_project, monkeypatch):
    """O6: loading a project with session=='project-persistent' logs exactly one
    informational carver.enablement.active acknowledgement (once per daemon
    instance). Post-P4a the checklist is fully clear, so the event is an
    operator-acknowledged pilot notice -- NOT the old 'premature' warning and
    NOT a hard reject -- and it no longer advertises any missing_items."""
    cfg = carver_project
    assert cfg.carve.session == "project-persistent"
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "nyxloom.daemon.log.warning",
        lambda event, **kw: calls.append((event, kw)))
    d = daemon.Daemon({"demo": cfg.root})
    d._carver_session("demo", cfg)
    d._carver_session("demo", cfg)

    assert len(calls) == 1  # once per daemon instance
    event, kw = calls[0]
    assert event == "carver.enablement.active"
    assert event != "carver.enablement.premature"  # not the pre-P4a warning
    assert "missing_items" not in kw  # checklist clear -> nothing missing
    assert kw["session"] == "project-persistent"


def test_o6_fresh_project_no_warn(tmp_state, sample_project, monkeypatch):
    """O6: a session=='fresh' project logs no enablement WARN."""
    cfg = sample_project
    assert cfg.carve.session == "fresh"
    warnings_emitted: list[str] = []
    monkeypatch.setattr(
        "nyxloom.daemon.log.warning",
        lambda event, **kw: warnings_emitted.append(event))
    d = daemon.Daemon({"demo": cfg.root})
    d._carver_session("demo", cfg)

    assert warnings_emitted == []


# --- O7: byte-identical-off ---
# Already verified by the full suite passing with session=="fresh" defaults.
# This test explicitly asserts the legacy carve path is unchanged.

def test_o7_byte_identical_fresh_carve_dispatch(tmp_state, sample_project, patch_launch):
    """O7: with cfg.carve.session=='fresh', _execute_carve_dispatch runs
    the exact pre-P3c fresh-carve path (task_id carve- prefix, not
    carver-session-)."""
    from conftest import SAMPLE_ROUTES_TOML
    cfg = sample_project
    assert cfg.carve.session == "fresh"
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.CarveDispatch(project="demo", kind="headroom")
    events = d._execute_carve_dispatch("demo", cfg, states, action)

    task_ids = [e.task_id for e in events if e.type is EventType.TASK_CREATED]
    assert len(task_ids) == 1
    assert task_ids[0].startswith("carve-demo-")
    assert not task_ids[0].startswith("carver-session-")


# ==========================================================================
# Coverage sweeps (P3d uncovered lines — reachable edge cases)
# ==========================================================================

def test_needs_operator_debounce_storage_error(tmp_state, carver_project, monkeypatch):
    """Cover daemon.py:1719-1720: when storage.iter_events raises, debounce
    returns False (allow emission)."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    monkeypatch.setattr(storage, "iter_events",
                        lambda project: (_ for _ in ()).throw(RuntimeError("boom")))
    assert not d._needs_operator_recently_emitted("demo", "carver-no-route")


def test_compact_stale_plan_refuses_cleanly(tmp_state, carver_project):
    """Cover daemon.py:4319: _execute_compact_carver_session with no WARM
    session returns empty events (stale plan)."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    action = reconcile.CompactCarverSession(
        project="demo", generation=0, trigger="turns")
    events = d._execute_compact_carver_session("demo", cfg, {}, action)
    assert events == []


def test_bootstrap_packet_with_spine_revisions(tmp_state, carver_project):
    """Cover daemon.py:3789,3804-3805,3809: bootstrap packet includes spine
    revisions when spine docs are configured."""
    cfg = carver_project
    (cfg.root / "docs" / "NORTH-STAR.md").write_text("north star content\n")
    (cfg.root / "docs" / "ROADMAP.md").write_text("roadmap content\n")
    cfg2 = dc_replace(cfg, north_star="docs/NORTH-STAR.md",
                      roadmap="docs/ROADMAP.md")
    d = daemon.Daemon({"demo": cfg.root})
    text = d._build_carver_bootstrap_packet(cfg2, "demo", 1, {})
    assert "north_star" in text
    assert "roadmap" in text
    assert "BOOTSTRAP-ACK.json" in text
    # The spine revisions listing and JSON template should be present.
    spine_revs = d._spine_revisions(cfg2)
    for level, rev in spine_revs.items():
        assert level in text
        assert rev in text


def test_bootstrap_ack_malformed_json(tmp_state, carver_project, patch_launch):
    """Cover daemon.py:4405-4406: a malformed BOOTSTRAP-ACK.json (invalid
    JSON) degrades the session."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    launch_events = d._execute_start_carver_session(
        "demo", cfg, states, reconcile.StartCarverSession(project="demo"))
    task_id = launch_events[0].task_id
    attempt_id = states[task_id].attempts[0].attempt_id

    attempt = states[task_id].attempt_by_id(attempt_id)
    ack_path = d._carver_bootstrap_ack_path(cfg, attempt)
    ack_path.parent.mkdir(parents=True, exist_ok=True)
    # Write invalid JSON.
    ack_path.write_text("not valid json", encoding="utf-8")

    _mark_turn_outcome("demo", task_id, attempt_id, session_handle="S1",
                       result=ReceiptResult.DONE)
    states = storage.list_states("demo")

    events = d._consume_carver_session_exit("demo", cfg, states, task_id, attempt_id)

    assert not any(e.type is EventType.CARVER_SESSION_STARTED for e in events)
    degraded = [e for e in events if e.type is EventType.CARVER_SESSION_DEGRADED]
    assert len(degraded) == 1
    assert degraded[0].payload["reason"] == "bootstrap-ack-invalid"

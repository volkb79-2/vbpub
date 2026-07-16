"""Tests for nyxloom.daemon. PACKAGE P09.

Cross-package seams (reconcile.plan_project, wrapper.launch_detached,
adapters.probe/build_dispatch/build_resume, render.render_after_event,
notify.notify_event, lint.lint_project) are monkeypatched per the P09
handoff's test strategy so this suite is independent of sibling packages'
implementation state.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import SAMPLE_ROUTES_TOML

from nyxloom import adapters, daemon, decision_chat, decisions, lint, notify, paths, reconcile, render, storage, wrapper
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, EventType,
    Receipt, ReceiptResult, Role, Route, TaskState, TaskStateFile, utc_now,
)


# --------------------------------------------------------------------------
# local helpers / fixtures (never added to conftest.py)

MUTEX_HANDOFF = """\
---
schema_version: 1
id: demo-P02-mutex
project: demo
title: Mutex sample
tier: flash-high
input_revision: "0000000"
source: {kind: roadmap, ref: docs/ROADMAP.md}
scope:
  touch: ["src/demo/other.py"]
mutexes: [stack]
oracles:
  - id: O1
    observable: "pytest tests/test_other.py::test_x passes"
    negative: "a bad value raises ValueError (test_x_violation)"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Mutex sample

Contract body: worktree /workspace, branch feat/demo-P02-mutex, out of scope
other files, context to read: this file. BLOCKED: none.
"""


@pytest.fixture()
def patch_siblings(monkeypatch):
    """Stub the cross-package seams the P09 handoff names; record calls."""
    calls = {
        "build_dispatch": [], "build_resume": [], "launch_detached": [],
        "render_after_event": [], "notify_event": [], "probe": [],
    }

    def fake_probe(route):
        calls["probe"].append(route.route_id)
        return (True, "ok")

    def fake_build_dispatch(route, *, handoff_path, worktree, branch, task_id, gate_hint, receipt_path):
        argv = ["fake-cli", "--task", task_id, "--worktree", worktree]
        calls["build_dispatch"].append({
            "route": route.route_id, "handoff_path": handoff_path, "worktree": worktree,
            "branch": branch, "task_id": task_id, "gate_hint": gate_hint,
            "receipt_path": receipt_path, "argv": argv,
        })
        return argv, "prompt"

    def fake_build_resume(route, *, session, worktree, prompt):
        argv = ["fake-cli", "--resume", session or "", "--worktree", worktree]
        calls["build_resume"].append({"route": route.route_id, "session": session, "worktree": worktree})
        return argv

    def fake_launch_detached(spec):
        calls["launch_detached"].append(spec)
        Path(spec.attempt_dir).mkdir(parents=True, exist_ok=True)
        Path(spec.attempt_dir, "spec.json").write_text(json.dumps(spec.to_dict()), encoding="utf-8")
        return 4242

    def fake_render_after_event(registry):
        calls["render_after_event"].append(dict(registry))
        return paths.www_dir()

    def fake_notify_event(cfg, states, ev):
        calls["notify_event"].append(ev.type)

    monkeypatch.setattr(adapters, "probe", fake_probe)
    monkeypatch.setattr(adapters, "build_dispatch", fake_build_dispatch)
    monkeypatch.setattr(adapters, "build_resume", fake_build_resume)
    monkeypatch.setattr(wrapper, "launch_detached", fake_launch_detached)
    monkeypatch.setattr(render, "render_after_event", fake_render_after_event)
    monkeypatch.setattr(notify, "notify_event", fake_notify_event)
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    return calls


def _scripted(monkeypatch, sequence):
    """monkeypatch reconcile.plan_project to pop one actions-list per call
    (extra calls get []); returns the list of captured ReconcileInput."""
    seq = list(sequence)
    captured = []

    def fake(inp):
        captured.append(inp)
        if seq:
            return seq.pop(0)
        return []

    monkeypatch.setattr(reconcile, "plan_project", fake)
    return captured


def _seed_task(project, task_id, state, handoff_path=None, paused=False):
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.CARVED, since=utc_now(), handoff_path=handoff_path,
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    cur = storage.load_state(project, task_id)
    if state is not TaskState.CARVED or paused:
        cur.state = state
        cur.paused = paused
        storage.save_state(cur)
    return cur


def _seed_running_attempt(project, task_id, attempt_id, prior_attempts=None):
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    tsf.attempts = list(prior_attempts or [])
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER, state=AttemptState.RUNNING,
                       route=route, started=utc_now(), pid=None)
    tsf.attempts.append(attempt)
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return storage.load_state(project, task_id)


def _write_receipt(project, attempt_id, result, exit_code=0, blocked_reason=None):
    d = paths.attempt_dir(project, attempt_id)
    d.mkdir(parents=True, exist_ok=True)
    receipt = Receipt(result=result, exit_code=exit_code, blocked_reason=blocked_reason)
    (d / "receipt.json").write_text(json.dumps(receipt.to_dict()), encoding="utf-8")


def _make_feature_branch(root, task_id, filename, content):
    subprocess.run(["git", "-C", str(root), "branch", f"feat/{task_id}"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", f"feat/{task_id}"], check=True, capture_output=True)
    (root / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "add", "-A"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "commit",
                    "-qm", f"feat {task_id}"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True, capture_output=True)


def _set_ephemeral_http_port(cfg):
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "http_port" not in text:
        text = text.replace("[policy]\n", "[policy]\nhttp_port = 0\n", 1)
        ptoml.write_text(text, encoding="utf-8")


def _drive_until(d, project, predicate, timeout=15.0, pass_gap=0.4):
    """P14 2026-07-15: repeatedly call run_pass (no background daemon
    thread/loop -- the 'monkeypatched pass cadence' the P14 handoff asks
    for) until predicate() is True or timeout elapses. Returns the final
    predicate() result."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d.run_pass(project)
        if predicate():
            return True
        time.sleep(pass_gap)
    return predicate()


# --------------------------------------------------------------------------
# P37 2026-07-16 Oracle 1: nyxloomd runs under tini + a supervisor loop, not
# as container PID 1 -- see docs/runtime-process-model.md §2. Mirrors the
# P27 sibling-file-parity pattern in test_render.py (NYXLOOMD_DIR / a
# line-level read, since ciu.compose.yml.j2 is not valid YAML as-is).

NYXLOOMD_DIR = Path(__file__).resolve().parent.parent / "nyxloomd"

# `init: true` must match a real service DIRECTIVE (indentation, then the key),
# not the several prose mentions of it in these files' header comments -- a
# plain `"init: true" in text` is satisfied by the comments alone and stays
# green with tini actually removed, i.e. blind to the very regression this
# test exists to catch.
_INIT_DIRECTIVE = re.compile(r"^[ \t]*init:[ \t]*true\b", re.M)


def test_nyxloomd_compose_runs_daemon_under_tini_supervisor_not_pid1():
    """Both the .j2 template and its pre-rendered docker-compose.yml sibling
    set `init: true` (tini as container PID 1) and run the daemon through a
    `while` supervisor loop with NO `exec` -- an `exec`'d daemon would still
    be PID 1, and its crash or restart would tear down the whole container,
    killing every in-flight agent (the P37 hazard)."""
    for fname in ("ciu.compose.yml.j2", "docker-compose.yml"):
        text = (NYXLOOMD_DIR / fname).read_text(encoding="utf-8")
        assert _INIT_DIRECTIVE.search(text), f"{fname} missing `init: true` (tini as PID 1)"
        assert "while true" in text, f"{fname} missing the supervisor loop"
        assert "exec " not in text, f"{fname} still execs the daemon (would be PID 1)"
        assert "nyxloom.cli daemon" in text


# --------------------------------------------------------------------------
# Oracle 1: CreateTask/Transition

def test_create_task_and_transition(tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    task_id = "demo-P01-sample"
    from nyxloom import frontmatter as fm_mod
    fm_obj, _body = fm_mod.parse_handoff(cfg.root / "handoff" / "demo-P01-sample.md")

    _scripted(monkeypatch, [
        [reconcile.CreateTask(task_id=task_id, fm=fm_obj, handoff_path="handoff/demo-P01-sample.md")],
    ])
    d = daemon.Daemon({"demo": cfg.root})
    n1 = d.run_pass("demo")
    assert n1 == 1
    tsf = storage.load_state("demo", task_id)
    assert tsf is not None
    assert tsf.state is TaskState.CARVED
    assert tsf.handoff_path == "handoff/demo-P01-sample.md"

    _scripted(monkeypatch, [[reconcile.Transition(task_id=task_id, to=TaskState.QUEUED, notes=None)]])
    n2 = d.run_pass("demo")
    assert n2 == 1
    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.QUEUED

    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.TASK_CREATED in types
    assert EventType.TASK_TRANSITIONED in types


def test_transition_noop_when_from_equals_to_is_silent(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Regression: a Transition whose target equals the current state is a
    race-tolerant NO-OP, not a QUEUED->QUEUED TransitionError surfacing as a
    TICK_ERROR. This arises when two planning passes both planned the same
    edge from a shared snapshot under a transient double-dispatcher (the
    observed production symptom: recurring 'task transition QUEUED -> QUEUED
    not allowed' TICK_ERRORs). The guard lives in Daemon._execute; root
    singleton enforcement is P19 (ciu-managed container)."""
    cfg = sample_project
    task_id = "demo-P01-sample"
    _seed_task("demo", task_id, TaskState.QUEUED,
               handoff_path="handoff/demo-P01-sample.md")

    # plan a QUEUED->QUEUED edge (from == to): must be a silent no-op
    _scripted(monkeypatch, [[reconcile.Transition(task_id=task_id, to=TaskState.QUEUED, notes=None)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")  # must not raise

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.QUEUED  # unchanged

    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.TICK_ERROR not in types          # no error surfaced
    assert EventType.TASK_TRANSITIONED not in types   # no spurious transition emitted


def test_transition_to_blocked_emits_task_blocked_with_typed_blocker(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """P14 2026-07-15 item 4 (daemon side of the INTERRUPTED silent-dead-end
    fix): a Transition(to=BLOCKED, blocker=...) action emits TASK_BLOCKED --
    not a plain TASK_TRANSITIONED -- so tsf.blocker actually gets set."""
    task_id, attempt_id = "t-dead-end", "att-dead-end"
    _seed_running_attempt("demo", task_id, attempt_id)
    tsf = storage.load_state("demo", task_id)
    att = tsf.attempt_by_id(attempt_id)
    att.state = AttemptState.INTERRUPTED
    storage.save_state(tsf)

    blocker = Blocker(type=BlockerType.ENVIRONMENT, unblock_condition="operator: inspect attempts",
                       detail="interrupted attempt has no resume handle or attempts are exhausted")
    _scripted(monkeypatch, [[reconcile.Transition(task_id=task_id, to=TaskState.BLOCKED,
                                                   notes="interrupted-dead-end", blocker=blocker)]])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.BLOCKED
    assert tsf2.blocker is not None
    assert tsf2.blocker.type is BlockerType.ENVIRONMENT
    assert tsf2.blocker.unblock_condition == "operator: inspect attempts"

    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.TASK_BLOCKED in types
    assert EventType.TASK_TRANSITIONED not in types


def test_mark_stalled_emits_attempt_stalled_not_ended(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """P14 2026-07-15 item 2 (daemon side): MarkStalled emits ATTEMPT_STALLED
    with state STALLED; the attempt is NOT ended (the process is still
    running, only flagged as unresponsive) -- a confirmed stall must be
    VISIBLE, not silently interrupted with zero event trace."""
    task_id, attempt_id = "t-stall", "att-stall"
    _seed_running_attempt("demo", task_id, attempt_id)

    _scripted(monkeypatch, [[reconcile.MarkStalled(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.ATTEMPT_STALLED in types
    tsf = storage.load_state("demo", task_id)
    att = tsf.attempt_by_id(attempt_id)
    assert att.state is AttemptState.STALLED
    assert att.ended is None


# --------------------------------------------------------------------------
# Oracle 2: DispatchImplementer

def test_dispatch_implementer(tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    (cfg.root / "handoff" / "demo-P02-mutex.md").write_text(MUTEX_HANDOFF, encoding="utf-8")
    task_id = "demo-P02-mutex"
    _seed_task("demo", task_id, TaskState.QUEUED, handoff_path="handoff/demo-P02-mutex.md")

    _scripted(monkeypatch, [[reconcile.DispatchImplementer(task_id=task_id, route_id="fake-cli")]])
    d = daemon.Daemon({"demo": cfg.root})
    n = d.run_pass("demo")
    assert n == 1

    branch = f"feat/{task_id}"
    worktree = cfg.root / ".worktrees" / branch
    assert worktree.exists()
    check = subprocess.run(["git", "-C", str(cfg.root), "rev-parse", "--verify", branch],
                            capture_output=True, text=True)
    assert check.returncode == 0

    events = list(storage.iter_events("demo"))
    types = [e.type for e in events]
    assert types.index(EventType.ATTEMPT_CREATED) < types.index(EventType.ATTEMPT_PREFLIGHTED)

    created_ev = next(e for e in events if e.type is EventType.ATTEMPT_CREATED)
    assert created_ev.payload["attempt"]["route"]["routes_rev"] == "test-rev"
    preflighted_ev = next(e for e in events if e.type is EventType.ATTEMPT_PREFLIGHTED)
    assert preflighted_ev.payload["attempt"]["pid"] == 4242

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.ACTIVE
    attempt_id = tsf2.attempts[0].attempt_id

    spec = json.loads((paths.attempt_dir("demo", attempt_id) / "spec.json").read_text())
    assert spec["argv"] == patch_siblings["build_dispatch"][0]["argv"]
    assert spec["leases"] == [{"name": "demo.stack", "capacity": 1}]

    # Re-run when the worktree dir was removed but the branch still exists:
    # must add without -b and must not raise. (Simulate a legitimate
    # requeue-and-redispatch so the QUEUED->ACTIVE transition stays valid.)
    subprocess.run(["git", "-C", str(cfg.root), "worktree", "remove", "--force", str(worktree)],
                    check=True, capture_output=True)
    requeued = storage.load_state("demo", task_id)
    requeued.state = TaskState.QUEUED
    storage.save_state(requeued)
    _scripted(monkeypatch, [[reconcile.DispatchImplementer(task_id=task_id, route_id="fake-cli")]])
    n2 = d.run_pass("demo")
    assert n2 == 1
    assert worktree.exists()


# --------------------------------------------------------------------------
# P16 2026-07-15: CarveDispatch execution (carver automation, module
# docstring's carve-automation section). The trigger itself is
# test_reconcile.py's concern; these drive daemon._execute directly via
# _scripted, mirroring test_dispatch_implementer/test_open_wave_and_
# launch_review's pattern.

def test_carve_dispatch_branch_authority_creates_worktree_and_carver_attempt(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )
    _scripted(monkeypatch, [[reconcile.CarveDispatch(project="demo")]])
    d = daemon.Daemon({"demo": cfg.root})
    n = d.run_pass("demo")
    assert n == 1

    branch = "carve/demo-1"
    worktree = cfg.root / ".worktrees" / branch
    assert worktree.exists()
    check = subprocess.run(["git", "-C", str(cfg.root), "rev-parse", "--verify", branch],
                            capture_output=True, text=True)
    assert check.returncode == 0

    task_id = "carve-demo-1"
    tsf = storage.load_state("demo", task_id)
    assert tsf is not None
    assert tsf.state is TaskState.ACTIVE
    assert len(tsf.attempts) == 1
    attempt = tsf.attempts[0]
    assert attempt.role is Role.CARVER
    assert attempt.branch == branch
    assert attempt.worktree == str(worktree)

    events = list(storage.iter_events("demo"))
    created_ev = next(e for e in events
                       if e.type is EventType.ATTEMPT_CREATED and e.task_id == task_id)
    assert created_ev.payload["attempt"]["role"] == "carver"
    preflighted_ev = next(e for e in events
                          if e.type is EventType.ATTEMPT_PREFLIGHTED and e.task_id == task_id)
    assert preflighted_ev.payload["attempt"]["pid"] == 4242

    packet_dir = paths.attempt_dir("demo", attempt.attempt_id) / "packet"
    packet_md = (packet_dir / "packet.md").read_text(encoding="utf-8")
    assert "## Your role: CARVER" in packet_md
    assert "REQUIRED OUTPUT CONTRACT" in packet_md
    assert "handoff/reports/CARVE-1.md" in packet_md
    assert "## Carve authority: branch" in packet_md
    assert "Do NOT merge" in packet_md


def test_carve_dispatch_main_authority_uses_project_root_no_worktree(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8").replace(
        "[policy]\n", '[policy]\ncarve_authority = "main"\n', 1)
    ptoml.write_text(text, encoding="utf-8")

    _scripted(monkeypatch, [[reconcile.CarveDispatch(project="demo")]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    task_id = "carve-demo-1"
    tsf = storage.load_state("demo", task_id)
    attempt = tsf.attempts[0]
    assert attempt.worktree == str(cfg.root)
    assert attempt.branch is None
    assert not (cfg.root / ".worktrees" / "carve").exists()

    packet_dir = paths.attempt_dir("demo", attempt.attempt_id) / "packet"
    packet_md = (packet_dir / "packet.md").read_text(encoding="utf-8")
    assert "## Carve authority: main" in packet_md
    assert "lint-gated" in packet_md


def test_carve_dispatch_files_authority_uses_project_root_no_git(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8").replace(
        "[policy]\n", '[policy]\ncarve_authority = "files"\n', 1)
    ptoml.write_text(text, encoding="utf-8")

    _scripted(monkeypatch, [[reconcile.CarveDispatch(project="demo")]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    task_id = "carve-demo-1"
    tsf = storage.load_state("demo", task_id)
    attempt = tsf.attempts[0]
    assert attempt.worktree == str(cfg.root)
    assert attempt.branch is None

    packet_dir = paths.attempt_dir("demo", attempt.attempt_id) / "packet"
    packet_md = (packet_dir / "packet.md").read_text(encoding="utf-8")
    assert "## Carve authority: files" in packet_md
    assert "WITHOUT committing" in packet_md


def test_carve_dispatch_no_frontier_route_pushes_needs_operator_no_task(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """SAMPLE_ROUTES_TOML has no frontier-review tier: the daemon must not
    mint an orphaned synthetic carve task -- it pushes a typed NEEDS_OPERATOR
    instead (defense in depth; reconcile.py's own trigger already guards
    against this, but daemon._execute_carve_dispatch never trusts that)."""
    cfg = sample_project
    _scripted(monkeypatch, [[reconcile.CarveDispatch(project="demo")]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert storage.load_state("demo", "carve-demo-1") is None
    needs_op = [e for e in storage.iter_events("demo") if e.type is EventType.NEEDS_OPERATOR]
    assert len(needs_op) == 1
    assert needs_op[0].payload == {"reason": "carve-no-route"}


# --------------------------------------------------------------------------
# Oracle 3: EmitAttemptExit healing, one test per receipt.result

def test_emit_attempt_exit_done(tmp_state, sample_project, patch_siblings, monkeypatch):
    task_id, attempt_id = "t-done", "att-done"
    _seed_running_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.AWAITING_REVIEW
    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.ATTEMPT_EXITED in types
    assert EventType.TASK_TRANSITIONED in types


def test_emit_attempt_exit_blocked(tmp_state, sample_project, patch_siblings, monkeypatch):
    task_id, attempt_id = "t-blocked", "att-blocked"
    _seed_running_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.BLOCKED, exit_code=1,
                    blocked_reason="missing fixture data")
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.BLOCKED
    assert tsf.blocker is not None
    assert tsf.blocker.type is BlockerType.CONTRACT


def test_emit_attempt_exit_limit(tmp_state, sample_project, patch_siblings, monkeypatch):
    task_id, attempt_id = "t-limit", "att-limit"
    _seed_running_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.LIMIT, exit_code=1)

    captured = _scripted(monkeypatch, [
        [reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)],
        [],
    ])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.QUEUED

    events = list(storage.iter_events("demo"))
    types = [e.type for e in events]
    assert EventType.PROVIDER_STATE_CHANGED in types
    psc = next(e for e in events if e.type is EventType.PROVIDER_STATE_CHANGED)
    assert psc.payload == {"route_id": "fake-cli", "state": "limited"}
    assert EventType.NEEDS_OPERATOR in types

    d.run_pass("demo")
    assert len(captured) == 2
    assert captured[1].provider_ok.get("fake-cli") is False


def test_emit_attempt_exit_error_retry(tmp_state, sample_project, patch_siblings, monkeypatch):
    task_id, attempt_id = "t-err1", "att-err1"
    _seed_running_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.ERROR, exit_code=1)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.QUEUED


def test_emit_attempt_exit_error_exhausted(tmp_state, sample_project, patch_siblings, monkeypatch):
    task_id, attempt_id = "t-err2", "att-err2"
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    prior = [
        Attempt(attempt_id=f"prior-{i}", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
                route=route, started=utc_now(), ended=utc_now(),
                receipt=Receipt(result=ReceiptResult.ERROR, exit_code=1))
        for i in range(3)
    ]
    _seed_running_attempt("demo", task_id, attempt_id, prior_attempts=prior)
    _write_receipt("demo", attempt_id, ReceiptResult.ERROR, exit_code=1)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.BLOCKED
    assert tsf.blocker.type is BlockerType.ENVIRONMENT


# --------------------------------------------------------------------------
# P21 oracle 3: receipt head_commit crosscheck against real git state

def test_emit_attempt_exit_head_commit_crosscheck_branch_ahead(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """A done-receipt with head_commit=null on a branch that has a real
    commit ahead of default must record the REAL commit -- a receipt has
    been observed lying null even when the branch held real work (live
    P93 lesson); a lying null must never read as "no work done"."""
    cfg = sample_project
    task_id, attempt_id = "t-head-ahead", "att-head-ahead"
    _seed_running_attempt("demo", task_id, attempt_id)
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    real_head = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", f"feat/{task_id}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert real_head
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    exited = next(e for e in storage.iter_events("demo") if e.type is EventType.ATTEMPT_EXITED)
    assert exited.payload["attempt"]["receipt"]["head_commit"] == real_head

    tsf = storage.load_state("demo", task_id)
    att = tsf.attempt_by_id(attempt_id)
    assert att.receipt.head_commit == real_head


def test_emit_attempt_exit_head_commit_crosscheck_no_commits_ahead(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Negative case: a branch that exists but has NO commits ahead of
    default still records null/none -- the crosscheck is defensive, not a
    fabrication; it must not invent a commit when there genuinely is none."""
    cfg = sample_project
    task_id, attempt_id = "t-head-none", "att-head-none"
    _seed_running_attempt("demo", task_id, attempt_id)
    subprocess.run(["git", "-C", str(cfg.root), "branch", f"feat/{task_id}"],
                    check=True, capture_output=True)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    exited = next(e for e in storage.iter_events("demo") if e.type is EventType.ATTEMPT_EXITED)
    assert exited.payload["attempt"]["receipt"]["head_commit"] is None


def test_emit_attempt_exit_head_commit_receipt_trusted_when_present(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """A receipt that already reports a head_commit is trusted as-is (no
    crosscheck override) even though no matching branch exists."""
    cfg = sample_project
    task_id, attempt_id = "t-head-trusted", "att-head-trusted"
    _seed_running_attempt("demo", task_id, attempt_id)
    d0 = paths.attempt_dir("demo", attempt_id)
    d0.mkdir(parents=True, exist_ok=True)
    receipt = Receipt(result=ReceiptResult.DONE, exit_code=0, head_commit="deadbeef")
    (d0 / "receipt.json").write_text(json.dumps(receipt.to_dict()), encoding="utf-8")

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    exited = next(e for e in storage.iter_events("demo") if e.type is EventType.ATTEMPT_EXITED)
    assert exited.payload["attempt"]["receipt"]["head_commit"] == "deadbeef"


# --------------------------------------------------------------------------
# P33 2026-07-16: robust review verdict -- derive the FRONTIER_REVIEW merge
# decision from the committed <task>-REVIEW.md verdict, never from bare
# process exit (live P26 incident: a REJECTED review report + clean process
# exit -> receipt DONE -> rubber-stamped MERGE_READY).

def _seed_review_attempt(project, task_id, attempt_id, wave_id="wave-1"):
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.AWAITING_REVIEW, since=utc_now(), handoff_path=None, wave_id=wave_id,
    )
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(attempt_id=attempt_id, role=Role.FRONTIER_REVIEW, state=AttemptState.RUNNING,
                       route=route, started=utc_now(), wave_id=wave_id)
    tsf.attempts.append(attempt)
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return storage.load_state(project, task_id)


def _commit_review_report(root, task_id, reports_dir, content):
    """Commit `<reports_dir>/<task_id>-REVIEW.md` onto feat/<task_id> --
    that branch must already exist (see _make_feature_branch)."""
    branch = f"feat/{task_id}"
    subprocess.run(["git", "-C", str(root), "checkout", branch], check=True, capture_output=True)
    report_dir = root / reports_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{task_id}-REVIEW.md").write_text(content, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "add", "-A"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "commit",
                    "-qm", f"review {task_id}"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True, capture_output=True)


def test_frontier_review_done_receipt_rejected_report_yields_review_rejected(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Oracle O1: reproduces the live P26 incident exactly -- a clean
    process exit (receipt DONE) whose committed REVIEW.md verdict is
    REJECTED must transition the task to REVIEW_REJECTED, NOT MERGE_READY,
    and REVIEW_RECORDED's payload result must read 'rejected'."""
    cfg = sample_project
    task_id, attempt_id = "t-rev-rejected", "att-rev-rejected"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nFindings: the daemon-core change is unsafe.\n\n"
        "VERDICT: REJECTED — daemon-core change is unsafe\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"


def test_frontier_review_done_receipt_approved_report_yields_merge_ready(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Oracle O2: the approval path is preserved -- a DONE receipt whose
    committed REVIEW.md verdict is APPROVED still reaches MERGE_READY."""
    cfg = sample_project
    task_id, attempt_id = "t-rev-approved", "att-rev-approved"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nFindings: none. Looks good.\n\nVERDICT: APPROVED\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.MERGE_READY

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "approved"


def test_frontier_review_missing_report_fails_safe_to_rejected(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Oracle O3 (missing): a DONE receipt whose <task>-REVIEW.md was never
    committed must fail safe to REVIEW_REJECTED, never MERGE_READY."""
    cfg = sample_project
    task_id, attempt_id = "t-rev-missing", "att-rev-missing"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    # deliberately: no REVIEW.md committed onto feat/<task_id>
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"


def test_frontier_review_ambiguous_report_fails_safe_to_rejected(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Oracle O3 (ambiguous): a REVIEW.md with conflicting VERDICT lines (no
    unambiguous single APPROVED) must also fail safe to REVIEW_REJECTED."""
    cfg = sample_project
    task_id, attempt_id = "t-rev-ambiguous", "att-rev-ambiguous"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nVERDICT: APPROVED\n\nOn reflection:\nVERDICT: REJECTED — actually no\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED


def test_frontier_review_nondone_receipt_is_defense_in_depth_rejected(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Non-DONE receipt (BLOCKED/nonzero) stays REVIEW_REJECTED regardless
    of the report -- defense-in-depth kept even if a REVIEW.md APPROVED
    verdict was somehow committed."""
    cfg = sample_project
    task_id, attempt_id = "t-rev-blocked-receipt", "att-rev-blocked-receipt"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nVERDICT: APPROVED\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.BLOCKED, exit_code=1,
                    blocked_reason="reviewer crashed mid-run")
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED


def test_launch_review_packet_requires_machine_readable_verdict_line(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Oracle O4: the review packet instructs the reviewer to write an
    unambiguous `VERDICT: APPROVED|REJECTED` line into <task>-REVIEW.md,
    in addition to the existing BLOCKED: rejected final-line signal."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )
    _seed_task("demo", "t1", TaskState.AWAITING_REVIEW, handoff_path=None)
    _make_feature_branch(cfg.root, "t1", "t1.py", "# t1\n")

    _scripted(monkeypatch, [[reconcile.OpenWave(task_ids=["t1"])]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")
    wave_id = next(e for e in storage.iter_events("demo") if e.type is EventType.WAVE_OPENED).wave_id

    _scripted(monkeypatch, [[reconcile.LaunchReview(wave_id=wave_id, task_ids=["t1"])]])
    d.run_pass("demo")

    created = next(e for e in storage.iter_events("demo")
                    if e.type is EventType.ATTEMPT_CREATED and e.wave_id == wave_id)
    packet_md = (paths.attempt_dir("demo", created.attempt_id) / "packet" / "packet.md").read_text(
        encoding="utf-8")

    assert "VERDICT: APPROVED" in packet_md
    assert "VERDICT: REJECTED" in packet_md
    assert "BLOCKED: rejected" in packet_md

    # REVIEW-FIX 2026-07-16: the packet must name the SAME file
    # _parse_review_verdict reads, or the reviewer writes a verdict the
    # daemon never sees and the review fail-safes to rejected. The path was
    # hardcoded to a stale `topos/handoff/reports/` matching no project.
    assert f"{cfg.reports_dir}/<task>-REVIEW.md" in packet_md
    assert "topos/handoff/reports" not in packet_md


def test_parse_review_verdict_when_project_root_is_a_repo_subdir(tmp_state, tmp_path):
    """REVIEW-FIX 2026-07-16 regression (O2 in the REAL layout): nyxloom
    self-hosts with the project root NESTED under the git repo root
    (nyxloom.toml: worktree_root = "../.worktrees", "vbpub is the git repo;
    nyxloom is a subdir"). `git show <rev>:<path>` resolves a bare <path>
    from the REPO ROOT and ignores `-C`, so the APPROVED report was
    unreadable -> every review, approvals included, fail-safed to rejected
    and no task could reach MERGE_READY. Every other test git-inits AT
    cfg.root, so this layout was unexercised and the bug shipped green."""
    from conftest import SAMPLE_PROJECT_TOML
    from nyxloom.config import ProjectConfig

    repo = tmp_path / "outer-repo"
    proj = repo / "proj"                      # cfg.root != git repo root
    (proj / ".nyxloom").mkdir(parents=True)
    (proj / "handoff" / "reports").mkdir(parents=True)
    (proj / ".nyxloom" / "project.toml").write_text(SAMPLE_PROJECT_TOML)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)

    cfg = ProjectConfig.load(proj)
    assert cfg.root == proj
    assert cfg.reports_dir == "handoff/reports"   # relative to cfg.root, not the repo root

    d = daemon.Daemon({"demo": proj})

    def _commit_report(task_id, body):
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", f"feat/{task_id}", "main"],
                        check=True, capture_output=True)
        # git tracks no empty dirs, so reports/ vanishes on checkout back to main
        (proj / "handoff" / "reports").mkdir(parents=True, exist_ok=True)
        (proj / "handoff" / "reports" / f"{task_id}-REVIEW.md").write_text(body, encoding="utf-8")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo),
                        "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo),
                        "commit", "-qm", f"review {task_id}"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True, capture_output=True)

    # the regression: an APPROVED report under a nested root must be READ,
    # not silently missed and fail-safed to rejected.
    _commit_report("t-nested-approved", "# Review\n\nVERDICT: APPROVED\n")
    assert d._parse_review_verdict(cfg, "t-nested-approved") == "approved"

    # and the fail-safe still discriminates under the same layout.
    _commit_report("t-nested-rejected", "# Review\n\nVERDICT: REJECTED — nope\n")
    assert d._parse_review_verdict(cfg, "t-nested-rejected") == "rejected"
    assert d._parse_review_verdict(cfg, "t-nested-never-written") == "rejected"


# --------------------------------------------------------------------------
# Oracle 4: MarkInterrupted/ResumeAttempt/InterruptAttempt

def test_mark_interrupted_and_resume(tmp_state, sample_project, patch_siblings, monkeypatch):
    task_id, attempt_id = "t-int", "att-int"
    _seed_running_attempt("demo", task_id, attempt_id)

    _scripted(monkeypatch, [[reconcile.MarkInterrupted(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.ATTEMPT_INTERRUPTED in types
    tsf = storage.load_state("demo", task_id)
    att = tsf.attempt_by_id(attempt_id)
    assert att.state is AttemptState.INTERRUPTED
    assert att.ended is not None

    att.session_handle = "sess-1"
    att.worktree = str(sample_project.root)
    storage.save_state(tsf)

    _scripted(monkeypatch, [[reconcile.ResumeAttempt(task_id=task_id, attempt_id=attempt_id)]])
    d.run_pass("demo")

    types2 = [e.type for e in storage.iter_events("demo")]
    assert EventType.ATTEMPT_RESUMED in types2
    tsf2 = storage.load_state("demo", task_id)
    att2 = tsf2.attempt_by_id(attempt_id)
    assert att2.state is AttemptState.RUNNING
    assert patch_siblings["build_resume"][-1]["session"] == "sess-1"

    # P14 2026-07-15 item 5 (resume bookkeeping drift): pid AND log_path
    # must both be refreshed to the resumed process's own values at resume
    # time, not left stale pointing at the original attempt's pid/log.
    assert att2.pid == 4242  # patch_siblings' fake_launch_detached pid
    attempt_dir = paths.attempt_dir("demo", attempt_id)
    assert att2.log_path == str(attempt_dir / "attempt.resume-1.log")
    assert att2.log_path != str(attempt_dir / "attempt.log")


def test_interrupt_attempt_signals_pgid(tmp_state, sample_project, patch_siblings, monkeypatch):
    task_id, attempt_id = "t-kill", "att-kill"
    _seed_running_attempt("demo", task_id, attempt_id)
    attempt_dir = paths.attempt_dir("demo", attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    child = subprocess.Popen(["sleep", "5"], start_new_session=True)
    (attempt_dir / "child.pid").write_text(str(child.pid), encoding="utf-8")

    _scripted(monkeypatch, [[reconcile.InterruptAttempt(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    child.wait(timeout=5)
    assert child.returncode is not None

    # ESRCH path: stale child.pid must not raise.
    (attempt_dir / "child.pid").write_text("999999", encoding="utf-8")
    _scripted(monkeypatch, [[reconcile.InterruptAttempt(task_id=task_id, attempt_id=attempt_id)]])
    d.run_pass("demo")  # no exception


def test_hang_detection_full_pipeline_real(tmp_state, sample_project, monkeypatch):
    """P14 2026-07-15 HEADLINE oracle 1: a real detached CLI that writes one
    line then hangs (sleep 600) is detected as stalled -- ATTEMPT_STALLED
    is VISIBLE before any interrupt (item 2) -- then interrupted (real
    SIGTERM, real wrapper self-report), and, since no resume handle was
    captured, the task lands BLOCKED with a typed environment blocker
    (item 4). Real reconcile.plan_project (NOT monkeypatched via
    _scripted/patch_siblings), real wrapper.launch_detached, shrunk
    stall_log_quiet_seconds, driven via repeated run_pass calls (the
    'monkeypatched pass cadence' the handoff asks for -- no background
    daemon thread)."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(wrapper, "SESSION_CAPTURE_DELAY", 0)

    cfg = sample_project
    # Remove the fixture's own sample handoff: with the REAL (unmonkeypatched)
    # planner running here, it would otherwise get auto-CreateTask'd ->
    # QUEUED -> dispatched through the literal "fake" cli (not a real
    # executable) -- unrelated async noise this test doesn't need.
    (cfg.root / "handoff" / "demo-P01-sample.md").unlink()

    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    text = text.replace("[policy]\n", "[policy]\nstall_log_quiet_seconds = 1\n", 1)
    ptoml.write_text(text, encoding="utf-8")

    project = "demo"
    task_id, attempt_id = "hang-task", "att-hang"
    # No matching handoff file (deliberately -- keeps the wall-clock cap at
    # its huge default so only the stall path is exercised here; the cap
    # itself has its own dedicated planner tests).
    _seed_running_attempt(project, task_id, attempt_id)

    script = cfg.root / "hang.sh"
    script.write_text("#!/bin/sh\necho starting\nsleep 600\n")
    script.chmod(0o755)

    attempt_dir = paths.attempt_dir(project, attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    spec = wrapper.WrapperSpec(
        project=project, task_id=task_id, attempt_id=attempt_id,
        argv=[str(script)], cwd=str(cfg.root),
        log_path=str(attempt_dir / "attempt.log"),
        receipt_path=str(attempt_dir / "receipt.json"),
        attempt_dir=str(attempt_dir),
        route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
        term_grace_seconds=2,
    )
    wrapper_pid = wrapper.launch_detached(spec)

    def _running():
        t = storage.load_state(project, task_id)
        a = t.attempt_by_id(attempt_id)
        return a.state is AttemptState.RUNNING and a.pid

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _running():
        time.sleep(0.1)
    assert _running(), "wrapper never reported RUNNING"

    d = daemon.Daemon({project: cfg.root})

    def _stalled_seen():
        return any(e.type is EventType.ATTEMPT_STALLED for e in storage.iter_events(project))

    try:
        assert _drive_until(d, project, _stalled_seen, timeout=15.0), \
            "ATTEMPT_STALLED never observed"
        tsf_stalled = storage.load_state(project, task_id)
        assert tsf_stalled.attempt_by_id(attempt_id).state is AttemptState.STALLED

        def _interrupted_seen():
            return any(e.type is EventType.ATTEMPT_INTERRUPTED for e in storage.iter_events(project))

        assert _drive_until(d, project, _interrupted_seen, timeout=15.0), \
            "ATTEMPT_INTERRUPTED never observed"

        def _blocked():
            return storage.load_state(project, task_id).state is TaskState.BLOCKED

        assert _drive_until(d, project, _blocked, timeout=10.0), "task never reached BLOCKED"
        tsf_final = storage.load_state(project, task_id)
        assert tsf_final.blocker is not None
        assert tsf_final.blocker.type is BlockerType.ENVIRONMENT
    finally:
        try:
            os.kill(wrapper_pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


# --------------------------------------------------------------------------
# Oracle 5: OpenWave/LaunchReview

def test_open_wave_and_launch_review(tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )

    for tid in ("t1", "t2"):
        _seed_task("demo", tid, TaskState.AWAITING_REVIEW, handoff_path=None)
        _make_feature_branch(cfg.root, tid, f"{tid}.py", f"# {tid}\n")

    _scripted(monkeypatch, [[reconcile.OpenWave(task_ids=["t1", "t2"])]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    wave_ev = next(e for e in events if e.type is EventType.WAVE_OPENED)
    wave_id = wave_ev.wave_id
    assert wave_ev.payload["task_ids"] == ["t1", "t2"]
    assert storage.load_state("demo", "t1").wave_id == wave_id
    assert storage.load_state("demo", "t2").wave_id == wave_id

    _scripted(monkeypatch, [[reconcile.LaunchReview(wave_id=wave_id, task_ids=["t1", "t2"])]])
    d.run_pass("demo")

    events2 = list(storage.iter_events("demo"))
    created = [e for e in events2 if e.type is EventType.ATTEMPT_CREATED and e.wave_id == wave_id]
    assert len(created) == 1
    attempt_payload = created[0].payload["attempt"]
    assert attempt_payload["role"] == "frontier-review"
    assert attempt_payload["route"]["route_id"] == "fake-cli"

    attempt_id = created[0].attempt_id
    packet_dir = paths.attempt_dir("demo", attempt_id) / "packet"
    diff1 = packet_dir / "t1.diff"
    assert diff1.exists() and diff1.stat().st_size > 0
    packet_md = (packet_dir / "packet.md").read_text(encoding="utf-8")
    assert "t1" in packet_md and "t2" in packet_md


# --------------------------------------------------------------------------
# P21 oracles 1+2: review packet git-truth (uncommitted state + reviewer text)

def test_launch_review_packet_captures_uncommitted_worktree(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """P21 oracle 1: a task whose worktree holds an uncommitted change gets
    that change surfaced under an UNCOMMITTED heading in packet.md (not
    just the COMMITTED default...branch diff) -- "experience shows the
    commit requirement is often not honored" (user directive). A task
    whose worktree is already torn down gets an explicit absent-note
    rather than a silent omission."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )

    for tid in ("t1", "t2"):
        _seed_task("demo", tid, TaskState.AWAITING_REVIEW, handoff_path=None)
        _make_feature_branch(cfg.root, tid, f"{tid}.py", f"# {tid}\n")

    d = daemon.Daemon({"demo": cfg.root})

    # t1: a real worktree with an uncommitted (unstaged) edit sitting in it.
    wt1 = cfg.root / ".worktrees" / "feat/t1"
    d._ensure_worktree(cfg.root, "feat/t1", wt1, cfg.default_branch)
    (wt1 / "t1.py").write_text("# t1\nUNCOMMITTED_MARKER_LINE\n", encoding="utf-8")
    # t2: deliberately NO worktree (simulates already-torn-down teardown).

    _scripted(monkeypatch, [[reconcile.OpenWave(task_ids=["t1", "t2"])]])
    d.run_pass("demo")
    wave_id = next(e for e in storage.iter_events("demo") if e.type is EventType.WAVE_OPENED).wave_id

    _scripted(monkeypatch, [[reconcile.LaunchReview(wave_id=wave_id, task_ids=["t1", "t2"])]])
    d.run_pass("demo")

    created = next(e for e in storage.iter_events("demo")
                    if e.type is EventType.ATTEMPT_CREATED and e.wave_id == wave_id)
    packet_md = (paths.attempt_dir("demo", created.attempt_id) / "packet" / "packet.md").read_text(
        encoding="utf-8")

    assert "### COMMITTED" in packet_md
    assert "### UNCOMMITTED" in packet_md
    assert "UNCOMMITTED_MARKER_LINE" in packet_md
    assert "is absent (already torn down)" in packet_md


def test_launch_review_packet_reviewer_text_has_git_truth_clause(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """P21 oracle 2: the reviewer role text tells the reviewer to verify
    real git state and NOT trust the receipt's head_commit/files_touched/
    oracles fields (live P93 lesson: they were observed null/empty even
    when real work was committed)."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )
    _seed_task("demo", "t1", TaskState.AWAITING_REVIEW, handoff_path=None)
    _make_feature_branch(cfg.root, "t1", "t1.py", "# t1\n")

    _scripted(monkeypatch, [[reconcile.OpenWave(task_ids=["t1"])]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")
    wave_id = next(e for e in storage.iter_events("demo") if e.type is EventType.WAVE_OPENED).wave_id

    _scripted(monkeypatch, [[reconcile.LaunchReview(wave_id=wave_id, task_ids=["t1"])]])
    d.run_pass("demo")

    created = next(e for e in storage.iter_events("demo")
                    if e.type is EventType.ATTEMPT_CREATED and e.wave_id == wave_id)
    packet_md = (paths.attempt_dir("demo", created.attempt_id) / "packet" / "packet.md").read_text(
        encoding="utf-8")

    assert "git state is truth, receipts" in packet_md
    assert "git log" in packet_md and "git status" in packet_md
    assert "Do NOT trust the receipt's" in packet_md
    assert "head_commit" in packet_md and "files_touched" in packet_md and "oracles" in packet_md
    assert "do not treat uncommitted" in packet_md.lower()


# --------------------------------------------------------------------------
# Oracle 6: SpecAttention

def test_spec_attention(tmp_state, sample_project, patch_siblings, monkeypatch):
    _scripted(monkeypatch, [[reconcile.SpecAttention(reason="ratchet", detail="3 zero-progress merges")]])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    ev = next(e for e in storage.iter_events("demo") if e.type is EventType.SPEC_ATTENTION)
    assert ev.payload["reason"] == "ratchet"


# --------------------------------------------------------------------------
# Oracle 7: TICK_ERROR

def test_tick_error_recovers(tmp_state, sample_project, patch_siblings, monkeypatch):
    def boom(inp):
        raise RuntimeError("boom")

    monkeypatch.setattr(reconcile, "plan_project", boom)
    d = daemon.Daemon({"demo": sample_project.root})

    n1 = d.run_pass("demo")
    assert n1 == 0
    types = [e.type for e in storage.iter_events("demo")]
    assert types.count(EventType.TICK_ERROR) == 1

    n2 = d.run_pass("demo")  # loop-callable again
    assert n2 == 0
    types2 = [e.type for e in storage.iter_events("demo")]
    assert types2.count(EventType.TICK_ERROR) == 2


# --------------------------------------------------------------------------
# Oracle 8: Input building (the one non-monkeypatched plan test)

def test_input_building(tmp_state, sample_project, monkeypatch):
    cfg = sample_project
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(decisions, "open_ids", lambda cfg: {"D-002"})
    monkeypatch.setattr(adapters, "probe", lambda route: (True, "ok"))

    paths.pause_flag("demo").parent.mkdir(parents=True, exist_ok=True)
    paths.pause_flag("demo").touch()

    task_id, att_running = "t-running", "att-running"
    _seed_running_attempt("demo", task_id, att_running)
    _write_receipt("demo", att_running, ReceiptResult.DONE, exit_code=0)

    task_id2, att_dead = "t-dead", "att-dead"
    tsf2 = _seed_running_attempt("demo", task_id2, att_dead)
    att_obj = tsf2.attempt_by_id(att_dead)
    att_obj.pid = 999999
    storage.save_state(tsf2)

    captured = _scripted(monkeypatch, [[]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert len(captured) == 1
    inp = captured[0]
    assert "demo-P01-sample" in inp.frontmatters
    assert inp.lint_clean.get("demo-P01-sample") is True
    assert inp.project_paused is True
    # P15 2026-07-15: a legacy EMPTY pause flag file (touch(), no content --
    # exactly what this test writes above) means 'drain-handoffs', the
    # pre-P15 boolean-pause behaviour (dispatch blocked only).
    assert inp.pause_mode == "drain-handoffs"
    assert isinstance(inp.receipts[att_running], dict)
    assert inp.pid_alive[att_dead] is False
    assert inp.decisions_open == {"D-002"}


# --------------------------------------------------------------------------
# P15 2026-07-15: factory-state pause MODE reading (Daemon._pause_mode)

def test_pause_mode_absent_flag_is_run(tmp_state, sample_project):
    assert not paths.pause_flag("demo").exists()
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._pause_mode("demo") == "run"


def test_pause_mode_explicit_drain_agents_content(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("drain-agents", encoding="utf-8")
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._pause_mode("demo") == "drain-agents"


def test_pause_mode_explicit_drain_handoffs_content(tmp_state, sample_project):
    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("drain-handoffs", encoding="utf-8")
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._pause_mode("demo") == "drain-handoffs"


# --------------------------------------------------------------------------
# P14 2026-07-15 item 5: _attempt_scan belt-and-braces wrapper.pid fallback

def test_attempt_scan_wrapper_pid_fallback_recovers_liveness(tmp_state, sample_project):
    """A stale attempt.pid (e.g. from bookkeeping drift across a resume)
    must not hide a genuinely live process: when the recorded pid looks
    dead, _attempt_scan cross-checks the freshest wrapper.pid file on disk
    and recovers liveness from it."""
    project = "demo"
    task_id, attempt_id = "t-fallback", "att-fallback"
    tsf = _seed_running_attempt(project, task_id, attempt_id)
    att = tsf.attempt_by_id(attempt_id)
    att.pid = 999999  # definitely dead
    storage.save_state(tsf)

    attempt_dir = paths.attempt_dir(project, attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "wrapper.pid").write_text(str(os.getpid()), encoding="utf-8")

    d = daemon.Daemon({project: sample_project.root})
    _log_quiet, pid_alive, _receipts = d._attempt_scan(project, storage.list_states(project))
    assert pid_alive[attempt_id] is True


def test_attempt_scan_wrapper_pid_fallback_stays_dead(tmp_state, sample_project):
    """Negative: both the recorded pid AND the wrapper.pid file are dead ->
    the fallback must not manufacture liveness."""
    project = "demo"
    task_id, attempt_id = "t-fallback2", "att-fallback2"
    tsf = _seed_running_attempt(project, task_id, attempt_id)
    att = tsf.attempt_by_id(attempt_id)
    att.pid = 999999
    storage.save_state(tsf)

    attempt_dir = paths.attempt_dir(project, attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "wrapper.pid").write_text("999998", encoding="utf-8")

    d = daemon.Daemon({project: sample_project.root})
    _log_quiet, pid_alive, _receipts = d._attempt_scan(project, storage.list_states(project))
    assert pid_alive[attempt_id] is False


# --------------------------------------------------------------------------
# P37 2026-07-16: orphan re-adoption under tini (daemon crash + respawn)

def test_reconcile_pass_treats_orphaned_live_wrapper_as_alive_not_interrupted(
        tmp_state, sample_project, monkeypatch):
    """Under the tini+supervisor model (docs/runtime-process-model.md §2), a
    freshly respawned daemon (post-crash) finds still-live wrapper processes
    it did NOT spawn -- reparented to tini, not to this daemon instance. The
    pid liveness check (_attempt_scan / os.kill(pid, 0)) is process-identity
    agnostic, so a REAL reconcile pass over an attempt whose recorded pid is
    a real process this test spawned directly (never a child of the Daemon
    object below) must treat it as alive: no MarkInterrupted action, no
    ATTEMPT_INTERRUPTED event, the attempt stays RUNNING. Regression for the
    respawn-then-interrupt failure mode that would defeat crash-safety --
    agents surviving the crash only to be killed by the supervisor's
    respawn."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    # Real (unmonkeypatched) reconcile.plan_project runs here; drop the
    # fixture's own sample handoff so it isn't auto-CreateTask'd/dispatched
    # through the non-executable "fake" cli -- unrelated async noise.
    (cfg.root / "handoff" / "demo-P01-sample.md").unlink()

    project = "demo"
    task_id, attempt_id = "orphan-task", "att-orphan"
    tsf = _seed_running_attempt(project, task_id, attempt_id)

    orphan = subprocess.Popen(["sleep", "300"])
    try:
        att = tsf.attempt_by_id(attempt_id)
        att.pid = orphan.pid
        storage.save_state(tsf)

        d = daemon.Daemon({project: cfg.root})
        d.run_pass(project)

        tsf_after = storage.load_state(project, task_id)
        assert tsf_after.attempt_by_id(attempt_id).state is AttemptState.RUNNING
        assert not any(e.type is EventType.ATTEMPT_INTERRUPTED for e in storage.iter_events(project))
        assert not any(e.type is EventType.TICK_ERROR for e in storage.iter_events(project))
    finally:
        orphan.terminate()
        orphan.wait(timeout=5)


# --------------------------------------------------------------------------
# P14 2026-07-15 item 3: _confirm_stall made REAL (CPU-descendant-aware)

def test_confirm_stall_idle_process_confirmed_after_two_reads(tmp_state, sample_project):
    """Positive: a genuinely idle child process (sleep, no children) with a
    quiet log is confirmed stalled once its CPU signature is unchanged
    across two consecutive _confirm_stall reads."""
    project = "demo"
    task_id, attempt_id = "t-idle", "att-idle"
    tsf = _seed_running_attempt(project, task_id, attempt_id)
    child = subprocess.Popen(["sleep", "5"], start_new_session=True)
    try:
        att = tsf.attempt_by_id(attempt_id)
        att.pid = child.pid
        storage.save_state(tsf)

        cfg = sample_project
        d = daemon.Daemon({project: cfg.root})
        log_quiet = {attempt_id: 400.0}   # over the default 300s threshold
        pid_alive = {attempt_id: True}

        out1 = d._confirm_stall(storage.list_states(project), log_quiet, pid_alive, cfg)
        assert out1[attempt_id] is False  # no prior baseline yet on read 1

        time.sleep(0.3)
        out2 = d._confirm_stall(storage.list_states(project), log_quiet, pid_alive, cfg)
        assert out2[attempt_id] is True  # unchanged CPU across two reads
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_confirm_stall_cpu_active_child_not_confirmed(tmp_state, sample_project):
    """P14 headline oracle 2 (tier-2 negative): a process whose own
    top-level CPU stays idle but that has spawned a BUSY child (tight loop,
    no output) must NOT be confirmed stalled -- the child's rising
    utime/stime must be part of the composite tier-2 signature. Regression
    for the pre-P14 bug where only the top-level pid's own /proc/<pid>/stat
    was checked, so a busy grandchild went completely unnoticed."""
    project = "demo"
    task_id, attempt_id = "t-busy-child", "att-busy-child"
    tsf = _seed_running_attempt(project, task_id, attempt_id)

    # Parent shell idles (sleep) while its background child burns CPU.
    parent = subprocess.Popen(
        ["sh", "-c", "( while true; do :; done ) & sleep 10"],
        start_new_session=True,
    )
    try:
        att = tsf.attempt_by_id(attempt_id)
        att.pid = parent.pid
        storage.save_state(tsf)

        cfg = sample_project
        d = daemon.Daemon({project: cfg.root})
        log_quiet = {attempt_id: 400.0}
        pid_alive = {attempt_id: True}

        d._confirm_stall(storage.list_states(project), log_quiet, pid_alive, cfg)
        time.sleep(0.5)  # let the busy child accumulate CPU ticks
        out2 = d._confirm_stall(storage.list_states(project), log_quiet, pid_alive, cfg)
        assert out2[attempt_id] is False
    finally:
        try:
            os.killpg(parent.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        parent.wait(timeout=5)


# --------------------------------------------------------------------------
# Oracle 9: pidfile

def test_pidfile_alive_blocks_start(tmp_state, sample_project):
    pidfile = paths.daemon_dir() / "nyxloomd.pid"
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    d = daemon.Daemon({"demo": sample_project.root})
    with pytest.raises(RuntimeError, match=str(os.getpid())):
        d.run()


def test_pidfile_dead_pid_allowed(tmp_state, sample_project, patch_siblings, monkeypatch):
    _set_ephemeral_http_port(sample_project)
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    pidfile = paths.daemon_dir() / "nyxloomd.pid"
    pidfile.write_text("999999", encoding="utf-8")

    d = daemon.Daemon({"demo": sample_project.root})
    d._stop_event.set()  # loop flag pre-set to stop immediately
    d.run()  # must not raise

    assert not pidfile.exists()


# --------------------------------------------------------------------------
# Oracle 12: run_once (checked before 10/11 since it needs no HTTP fixture)

def test_run_once_no_pidfile_no_port(tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [reconcile.SpecAttention(reason="ratchet")])

    def boom(*a, **kw):
        raise AssertionError("HTTP server must not be started by run_once")

    monkeypatch.setattr(daemon.http.server, "ThreadingHTTPServer", boom)

    n = daemon.run_once("demo")
    assert n == 1
    assert not (paths.daemon_dir() / "nyxloomd.pid").exists()


# --------------------------------------------------------------------------
# Oracles 10 & 11: HTTP / SSE

@pytest.fixture()
def http_daemon(tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    _set_ephemeral_http_port(sample_project)

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert d.http_port != 0
    try:
        yield d
    finally:
        d.stop()
        t.join(timeout=5)


def test_http_endpoints(http_daemon):
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    _seed_task("demo", "t-http", TaskState.QUEUED)

    data = json.loads(urllib.request.urlopen(f"{base}/api/projects", timeout=5).read())
    assert any(p["project_id"] == "demo" for p in data)

    tasks = json.loads(urllib.request.urlopen(f"{base}/api/tasks?project=demo", timeout=5).read())
    assert any(t["task_id"] == "t-http" for t in tasks)

    one = json.loads(urllib.request.urlopen(f"{base}/api/task/demo/t-http", timeout=5).read())
    assert one["task_id"] == "t-http"

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base}/api/task/demo/does-not-exist", timeout=5)
    assert exc_info.value.code == 404

    attempt_id = "att-log"
    log_dir = paths.attempt_dir("demo", attempt_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    prefix = "x" * 200
    secret_line = "password=supersecret\n"
    (log_dir / "attempt.log").write_text(prefix + "\n" + secret_line, encoding="utf-8")
    body = urllib.request.urlopen(f"{base}/api/log/demo/{attempt_id}?tail=100", timeout=5).read().decode()
    assert "supersecret" not in body
    assert "[REDACTED]" in body
    assert len(body) <= 100 + len("[REDACTED]")  # last 100 raw bytes, then redacted in place

    www = paths.www_dir()
    www.mkdir(parents=True, exist_ok=True)
    (www / "hello.html").write_text("<html>hi</html>", encoding="utf-8")
    got = urllib.request.urlopen(f"{base}/www/hello.html", timeout=5).read()
    assert got == b"<html>hi</html>"

    conn = http.client.HTTPConnection("127.0.0.1", d.http_port, timeout=5)
    conn.request("GET", "/www/../registry.toml")
    resp = conn.getresponse()
    traversal_body = resp.read()
    assert resp.status >= 400
    assert b"projects.demo" not in traversal_body
    conn.close()

    evs = json.loads(urllib.request.urlopen(f"{base}/api/events?project=demo&since=0", timeout=5).read())
    assert isinstance(evs, list)

    with pytest.raises(urllib.error.HTTPError) as exc2:
        urllib.request.urlopen(f"{base}/unknown/path", timeout=5)
    assert exc2.value.code == 404


# --------------------------------------------------------------------------
# P22: GET /api/drilldown/<project>/<attempt_id> — read-only agent drilldown

def test_drilldown_endpoint_renders_transcript_readonly_and_redacted(http_daemon):
    """Oracle 3: the endpoint returns the human-readable rendering of an
    attempt's stream-json log (assistant text + tool names), never raw
    JSON, redacted like the raw log endpoint, and with no mutating
    control anywhere on the page."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    attempt_id = "att-drill-1"
    log_dir = paths.attempt_dir("demo", attempt_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "attempt.log").write_text(
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"Investigating the failing test, password=supersecret in the fixture."}]}}\n'
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Bash","input":{"command":"pytest -q"}}]}}\n',
        encoding="utf-8",
    )

    resp = urllib.request.urlopen(f"{base}/api/drilldown/demo/{attempt_id}", timeout=5)
    assert resp.headers.get("Content-Type", "").startswith("text/html")
    body = resp.read().decode("utf-8")

    assert "Investigating the failing test" in body
    assert "[tool: Bash]" in body
    assert '"type":"assistant"' not in body   # never raw JSON
    assert '"tool_use"' not in body
    assert "supersecret" not in body          # redacted
    assert "[REDACTED]" in body
    assert "<form" not in body.lower()        # READ-ONLY: no mutating control
    assert "<button" not in body.lower()
    assert 'http-equiv="refresh"' in body


def test_drilldown_endpoint_404_for_missing_attempt(http_daemon):
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base}/api/drilldown/demo/does-not-exist", timeout=5)
    assert exc_info.value.code == 404


def test_drilldown_endpoint_404_for_unknown_project(http_daemon):
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base}/api/drilldown/no-such-project/att-x", timeout=5)
    assert exc_info.value.code == 404
def test_decision_reply_endpoint(http_daemon, sample_project, monkeypatch):
    """P18 oracle 4: POST /api/decision/reply drives the decision-chat
    bridge for an OPEN decision; unknown decision_id -> 404; a malformed
    body -> 400. decision_chat.advance_chat itself is stubbed here -- its
    real turn mechanics are covered by test_decision_chat.py; this test is
    scoped to the HTTP endpoint's own contract."""
    inbox = sample_project.root / "docs" / "DECISIONS-INBOX.md"
    inbox.write_text(
        "# Decisions inbox\n\n---\n\n"
        "## D-050 · 2026-07-16 · test · OPEN\n\n"
        "**Question:** Ship it?\n\n---\n",
        encoding="utf-8",
    )

    calls = []

    def fake_advance_chat(cfg, project, decision_id, text):
        calls.append((project, decision_id, text))
        return "ok"

    monkeypatch.setattr(decision_chat, "advance_chat", fake_advance_chat)

    base = f"http://127.0.0.1:{http_daemon.http_port}"

    body = json.dumps({"decision_id": "D-050", "text": "go ahead"}).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/decision/reply", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req, timeout=5)
    assert resp.status == 200
    assert calls == [("demo", "D-050", "go ahead")]

    unknown_body = json.dumps({"decision_id": "D-999", "text": "hi"}).encode("utf-8")
    req2 = urllib.request.Request(f"{base}/api/decision/reply", data=unknown_body,
                                   headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req2, timeout=5)
    assert exc.value.code == 404

    req3 = urllib.request.Request(f"{base}/api/decision/reply", data=b"{}",
                                   headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc3:
        urllib.request.urlopen(req3, timeout=5)
    assert exc3.value.code == 400

    # GET on a POST-only path -> 405 (same guard as the P15 config endpoints).
    with pytest.raises(urllib.error.HTTPError) as exc4:
        urllib.request.urlopen(f"{base}/api/decision/reply", timeout=5)
    assert exc4.value.code == 405


def test_start_cmd_listener_wraps_handler_for_decision_routing(tmp_state, sample_project, monkeypatch):
    """P18: _start_cmd_listener wraps the CommandListener's handle_message
    with decision_chat.wrap_command_handler (feedback-channel routing)
    rather than leaving the raw verb dispatcher in place."""
    ptoml = sample_project.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    text = text.replace(
        "[notify]\n",
        '[notify]\ncmd_topic = "nyxloom-cmd"\ncmd_token_env = "NTFY_CMD_TOKEN_TEST"\n',
        1,
    )
    ptoml.write_text(text, encoding="utf-8")
    monkeypatch.setenv("NTFY_CMD_TOKEN_TEST", "dummy")

    class FakeListener:
        def __init__(self, registry):
            self.registry = registry
            self.handle_message = self._base_handle_message
            self.started = False

        def _base_handle_message(self, text, tags):
            return "base-dispatch"

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

    monkeypatch.setattr(daemon.commands, "CommandListener", FakeListener)

    d = daemon.Daemon({"demo": sample_project.root})
    d._start_cmd_listener()
    try:
        assert d._cmd_listener is not None
        assert d._cmd_listener.started

        # A verb command still falls through to the original dispatcher.
        assert d._cmd_listener.handle_message("help", []) == "base-dispatch"

        # A decision-chat-routed message is intercepted BEFORE the base
        # dispatcher ever sees it (proves the wrap, not just a passthrough).
        monkeypatch.setattr(decision_chat, "handle_feedback_message",
                             lambda registry, text, tags: None)
        assert d._cmd_listener.handle_message("D-001: discuss", []) is None
    finally:
        d._stop_cmd_listener()


def test_sse_stream_and_stop(tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    monkeypatch.setattr(daemon, "SSE_POLL_SECONDS", 0.05)
    monkeypatch.setattr(daemon, "SSE_HEARTBEAT_SECONDS", 100.0)
    _set_ephemeral_http_port(sample_project)

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert d.http_port != 0

    received: dict = {}

    def reader():
        conn = http.client.HTTPConnection("127.0.0.1", d.http_port, timeout=10)
        conn.request("GET", "/api/stream?project=demo")
        resp = conn.getresponse()
        buf = b""
        deadline2 = time.monotonic() + 10
        found = None
        while time.monotonic() < deadline2:
            chunk = resp.read(1)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                line, buf = buf.split(b"\n\n", 1)
                if line.startswith(b"data: "):
                    payload = json.loads(line[len(b"data: "):])
                    if payload.get("type") == "SPEC_ATTENTION":
                        found = payload
                        break
            if found is not None:
                break
        received["found"] = found

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    time.sleep(0.3)
    storage.append_event("demo", actor=Actor(ActorKind.OPERATOR, "test"),
                          type=EventType.SPEC_ATTENTION, payload={"reason": "test"})
    reader_thread.join(timeout=10)

    assert received.get("found") is not None
    assert received["found"]["type"] == "SPEC_ATTENTION"

    start = time.monotonic()
    d.stop()
    t.join(timeout=5)
    assert not t.is_alive()
    assert time.monotonic() - start < 5

"""Tests for handoffctl.daemon. PACKAGE P09.

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
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import SAMPLE_ROUTES_TOML

from handoffctl import adapters, daemon, decisions, lint, notify, paths, reconcile, render, storage, wrapper
from handoffctl.types import (
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
    ptoml = cfg.root / ".handoffctl" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "http_port" not in text:
        text = text.replace("[policy]\n", "[policy]\nhttp_port = 0\n", 1)
        ptoml.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# Oracle 1: CreateTask/Transition

def test_create_task_and_transition(tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    task_id = "demo-P01-sample"
    from handoffctl import frontmatter as fm_mod
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
    assert isinstance(inp.receipts[att_running], dict)
    assert inp.pid_alive[att_dead] is False
    assert inp.decisions_open == {"D-002"}


# --------------------------------------------------------------------------
# Oracle 9: pidfile

def test_pidfile_alive_blocks_start(tmp_state, sample_project):
    pidfile = paths.daemon_dir() / "handoffd.pid"
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    d = daemon.Daemon({"demo": sample_project.root})
    with pytest.raises(RuntimeError, match=str(os.getpid())):
        d.run()


def test_pidfile_dead_pid_allowed(tmp_state, sample_project, patch_siblings, monkeypatch):
    _set_ephemeral_http_port(sample_project)
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    pidfile = paths.daemon_dir() / "handoffd.pid"
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
    assert not (paths.daemon_dir() / "handoffd.pid").exists()


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

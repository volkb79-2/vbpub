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
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import SAMPLE_ROUTES_TOML

from nyxloom import (
    adapters, cli, daemon, decision_chat, decisions, doctor, lint, log, notify, paths,
    reconcile, render, storage, wrapper,
)
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

    def fake_build_dispatch(route, *, handoff_path, worktree, branch, task_id, gate_hint,
                             receipt_path, **_kw):
        # P44 2026-07-19: **_kw absorbs the new role=/carve_authority= kwargs
        # the three daemon.py call sites now pass explicitly (role-scoped
        # prompt text) -- this fake only records argv/routing, not prompt
        # text, so it never needed to care which role dispatched it.
        argv = ["fake-cli", "--task", task_id, "--worktree", worktree]
        calls["build_dispatch"].append({
            "route": route.route_id, "handoff_path": handoff_path, "worktree": worktree,
            "branch": branch, "task_id": task_id, "gate_hint": gate_hint,
            "receipt_path": receipt_path, "argv": argv,
            # B4b 2026-07-20: record the new prior_verdict kwarg (the re-dispatch
            # review-verdict embed) so the DispatchImplementer wiring can be
            # asserted end-to-end; None on a first dispatch.
            "prior_verdict": _kw.get("prior_verdict"),
        })
        return argv, "prompt"

    def fake_build_resume(route, *, session, worktree, prompt):
        argv = ["fake-cli", "--resume", session or "", "--worktree", worktree]
        # B6/P74: also record the resume PROMPT so the reviewer-session-reuse
        # tests can assert the A7 `(attempt <id>)` stamp is threaded onto the
        # WARM resume path (purely additive -- pre-B6 tests read only route/
        # session/worktree).
        calls["build_resume"].append({"route": route.route_id, "session": session,
                                      "worktree": worktree, "prompt": prompt})
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


def _set_http_bind(cfg, bind):
    """Write an http_bind into the project's toml. 2026-07-20: http_bind is
    INFRA-sourced (NYXLOOM_HTTP_BIND), NOT a toml key, so this now writes a
    value that ProjectConfig.load must IGNORE -- used only to prove that
    ignoring (test_toml_http_bind_never_reaches_the_real_bind). To actually
    set the bind, monkeypatch.setenv NYXLOOM_HTTP_BIND instead."""
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "http_bind" not in text:
        text = text.replace("[policy]\n", f'[policy]\nhttp_bind = "{bind}"\n', 1)
        ptoml.write_text(text, encoding="utf-8")


def _set_pipeline(cfg, pipeline):
    """B5: write an explicit `pipeline = [...]` into the project's toml (under
    [project]) so the daemon composes that exact pipeline instead of the default
    -- used to pin the legacy (no-self_review) routing."""
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "\npipeline" not in text:
        toml_list = "[" + ", ".join(f'"{s}"' for s in pipeline) + "]"
        text = text.replace("[project]\n", f"[project]\npipeline = {toml_list}\n", 1)
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
# P38 2026-07-16 Oracle 2: nyxloomd moves off host-networking onto a ciu-owned
# bridge network and binds the dashboard on it -- docs/runtime-process-model.md
# §3: host-net left the dashboard reachable only in the daemon's own netns
# (127.0.0.1 on the docker host), invisible to the devcontainer, so VS Code
# could never auto-forward it. The bind change and the bridge move are
# inseparable: 0.0.0.0 must NEVER ship while still on host-networking.

_NETWORK_MODE_HOST = re.compile(r"^[ \t]*network_mode:[ \t]*host\b", re.M)
_HTTP_BIND_ALL = re.compile(r"NYXLOOM_HTTP_BIND:\s*\"?0\.0\.0\.0\"?")
# The alias must be the exact token `nyxloomd`, NOT merely a `- nyxloomd-net`
# network list item: `- nyxloomd\b` would match that too (\b sits at the d/-
# boundary), so deleting the aliases block entirely would still pass. Anchor
# on the end of the item instead (2026-07-16 review).
_BRIDGE_ALIAS = re.compile(r"^[ \t]*-[ \t]*nyxloomd[ \t]*(?:#.*)?$", re.M)
# O2: the healthcheck must probe the bind address, not the old host loopback.
_HEALTHCHECK_ON_BIND = re.compile(r"</dev/tcp/0\.0\.0\.0/")


def test_nyxloomd_compose_drops_host_network_and_binds_bridge_address():
    """Both compose files: no `network_mode: host`, the daemon's http_bind is
    set to 0.0.0.0 (safe -- private bridge network, not host), the service
    joins an explicit bridge network under a stable alias, the healthcheck
    probes that bind, and DooD (docker.sock) + the physical repo binds survive
    the move."""
    for fname in ("ciu.compose.yml.j2", "docker-compose.yml"):
        text = (NYXLOOMD_DIR / fname).read_text(encoding="utf-8")
        assert not _NETWORK_MODE_HOST.search(text), f"{fname} still on host networking"
        assert _HTTP_BIND_ALL.search(text), f"{fname} missing the 0.0.0.0 bridge bind"
        assert "networks:" in text, f"{fname} missing an explicit bridge network join"
        assert re.search(r"^[ \t]*aliases:[ \t]*$", text, re.M), \
            f"{fname} missing the service's network aliases block"
        assert _BRIDGE_ALIAS.search(text), \
            f"{fname} missing the stable 'nyxloomd' network alias"
        assert _HEALTHCHECK_ON_BIND.search(text), \
            f"{fname} healthcheck does not probe the 0.0.0.0 bind address"
        assert "/var/run/docker.sock:/var/run/docker.sock" in text, \
            f"{fname} lost the docker.sock mount (DooD) in the network move"
        assert "/home/vb/volkb79-2/vbpub:/workspaces/vbpub" in text, \
            f"{fname} lost the physical repo bind in the network move"


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
# P55 2026-07-19 (Wave-A3, R5): execute-time admission gate. Every launch
# is re-checked against pause + budget at the EFFECT BOUNDARY, not just at
# plan time -- so a mid-pass auto-pause or any planner gap cannot slip an
# agent through. Tests hand-feed the launch action (bypassing the planner's
# own guard) so the EXECUTOR's gate is what must refuse.

def test_admission_gate_drain_agents_blocks_dispatch_at_execute(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    (cfg.root / "handoff" / "demo-P02-mutex.md").write_text(MUTEX_HANDOFF, encoding="utf-8")
    task_id = "demo-P02-mutex"
    _seed_task("demo", task_id, TaskState.QUEUED, handoff_path="handoff/demo-P02-mutex.md")
    paths.pause_flag("demo").parent.mkdir(parents=True, exist_ok=True)
    paths.pause_flag("demo").write_text("drain-agents", encoding="utf-8")

    _scripted(monkeypatch, [[reconcile.DispatchImplementer(task_id=task_id, route_id="fake-cli")]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    # No wrapper launch, and the paired QUEUED->ACTIVE transition was skipped too.
    assert patch_siblings["launch_detached"] == []
    assert storage.load_state("demo", task_id).state is TaskState.QUEUED


def test_admission_gate_drain_agents_blocks_review_at_execute(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Review launch is execute-gated too (previously ungated for budget --
    M9); under drain-agents a hand-fed LaunchReview produces no launch."""
    cfg = sample_project
    task_id = "demo-P02-mutex"
    _seed_task("demo", task_id, TaskState.AWAITING_REVIEW, handoff_path="handoff/demo-P02-mutex.md")
    paths.pause_flag("demo").parent.mkdir(parents=True, exist_ok=True)
    paths.pause_flag("demo").write_text("drain-agents", encoding="utf-8")

    _scripted(monkeypatch, [[reconcile.LaunchReview(wave_id="wave-1", task_ids=[task_id])]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert patch_siblings["launch_detached"] == []


def test_admission_gate_mode_and_budget_matrix(tmp_state, sample_project):
    """_dispatch_admissible enforces the mode-aware pause rules (matching the
    planner) and ADDS the budget check the planner omits for resume/review
    (M9). Direct unit test of the gate over the full kind x condition matrix."""
    import dataclasses
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")
    flag = paths.pause_flag("demo")
    flag.parent.mkdir(parents=True, exist_ok=True)
    kinds = ("dispatch", "resume", "review", "carve")

    # run mode -> everything admissible
    if flag.exists():
        flag.unlink()
    assert all(d._dispatch_admissible("demo", cfg, states, k)[0] for k in kinds)

    # drain-agents -> no new agent process of ANY kind
    flag.write_text("drain-agents", encoding="utf-8")
    assert not any(d._dispatch_admissible("demo", cfg, states, k)[0] for k in kinds)

    # drain-handoffs -> block NEW work, allow in-flight completion
    flag.write_text("drain-handoffs", encoding="utf-8")
    assert d._dispatch_admissible("demo", cfg, states, "dispatch")[0] is False
    assert d._dispatch_admissible("demo", cfg, states, "carve")[0] is False
    assert d._dispatch_admissible("demo", cfg, states, "resume")[0] is True
    assert d._dispatch_admissible("demo", cfg, states, "review")[0] is True

    # budget exhausted -> block ALL kinds, including review/resume (the M9 fix)
    flag.unlink()
    cfg0 = dataclasses.replace(cfg, policy=dataclasses.replace(cfg.policy, max_cost=0.0))
    assert not any(d._dispatch_admissible("demo", cfg0, states, k)[0] for k in kinds)


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


def test_carve_dispatch_wrapper_spec_carries_strategic_carver_lease(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """P47 2026-07-19: the untargeted headroom-refill CarveDispatch path
    must populate WrapperSpec.leases so wrapper_main's existing (frozen)
    lease-acquisition step actually enforces the single-strategic-carver
    invariant -- before this package the field was omitted entirely, so
    two racing carve dispatches could both spawn a real CARVER attempt."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )
    _scripted(monkeypatch, [[reconcile.CarveDispatch(project="demo")]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert len(patch_siblings["launch_detached"]) == 1
    spec = patch_siblings["launch_detached"][0]
    assert spec.leases == [{"name": "demo.strategic-carver", "capacity": 1}]


def test_dispatch_targeted_carve_wrapper_spec_carries_strategic_carver_lease(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """P47: the operator-initiated dispatch_targeted_carve path (P41) has
    no reconcile-pass carve_in_flight scan in front of it at all -- it is
    the path most exposed to the race this package closes, so it must get
    the same lease population as the untargeted trigger above."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )
    backlog = cfg.root / "nyxloom-trove" / "4-backlog.md"
    backlog.parent.mkdir(parents=True, exist_ok=True)
    backlog.write_text(
        "---\nkind: backlog\nschema_version: 1\nitems:\n"
        "- id: B1\n  title: sample item\n  type: feature\n  component: ops\n"
        "  context_estimate: small\n---\n\n# backlog\n",
        encoding="utf-8",
    )
    d = daemon.Daemon({"demo": cfg.root})
    d.dispatch_targeted_carve("demo", "B1")

    assert len(patch_siblings["launch_detached"]) == 1
    spec = patch_siblings["launch_detached"][0]
    assert spec.leases == [{"name": "demo.strategic-carver", "capacity": 1}]


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
# B7 2026-07-20 (P75, D-060 carver re-scope entry): a task triage routed to
# READY_TO_CARVE (architectural / stale-premise / attempt-exhausted) is
# RE-SCOPED -- the carve packet embeds the rejected task's handoff + review
# verdict + input_revision drift, and the ORIGINAL task is superseded (RESCOPED
# outcome) ONLY after the re-scope carve actually launches (critique A10/M20).

def _write_origin_handoff(root, task_id, input_revision):
    """A minimal schema-valid handoff for `task_id` with a chosen input_revision,
    written (untracked) to the on-disk working tree so _rescope_context's
    parse_handoff can read it. MUST be written while HEAD is on main and AFTER any
    _make_feature_branch/_commit_review_report calls (those check out and back,
    which would drop an untracked file from the working tree)."""
    text = (
        "---\n"
        "schema_version: 1\n"
        f"id: {task_id}\n"
        "project: demo\n"
        "title: Origin sample\n"
        "tier: flash-high\n"
        f'input_revision: "{input_revision}"\n'
        "source: {kind: roadmap, ref: docs/ROADMAP.md}\n"
        "scope:\n"
        '  touch: ["src/demo/origin.py"]\n'
        "oracles:\n"
        "  - id: O1\n"
        '    observable: "pytest tests/test_origin.py::test_x passes"\n'
        '    negative: "a bad value raises ValueError (test_x_violation)"\n'
        "    gate: pytest-q\n"
        "gates: [pytest-q]\n"
        'escalate_if: ["a named contract cannot be met as specified"]\n'
        "---\n\n# Origin sample\nBody.\n"
    )
    rel = f"handoff/{task_id}.md"
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return rel


def test_build_carve_packet_rescope_embeds_verdict_and_drift(
        tmp_state, sample_project, patch_siblings):
    """The re-scope packet branch renders the origin handoff pointer, the DRIFTED
    premise line, and the reviewer's verdict prose (quoted). This is what makes a
    re-scope more than a blind re-carve -- the carver reads WHY it was rejected."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    rescope = {
        "origin_task_id": "P01", "handoff_path": "handoff/P01.md",
        "verdict": "Findings: the design is architecturally wrong.\nSecond finding.",
        "input_revision": "deadbeef", "head_revision": "abc1234567", "drifted": True,
    }
    packet = d._build_carve_packet(cfg, "demo", 1, {}, rescope=rescope)
    assert "RE-SCOPING" in packet
    assert "Re-scope source: the rejected task" in packet
    assert "handoff/P01.md" in packet
    assert "DRIFTED" in packet
    assert "the design is architecturally wrong" in packet
    assert "> Findings:" in packet          # verdict is quoted line-by-line
    assert "Do NOT simply re-emit the original handoff" in packet


def test_build_carve_packet_rescope_no_drift_no_verdict_negative(
        tmp_state, sample_project, patch_siblings):
    """Differently-routing NEGATIVE of the above: drifted=False renders the
    'current' premise line (NOT 'DRIFTED'), and a missing verdict renders the
    explicit 'no committed review report' note rather than a quote block."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    rescope = {
        "origin_task_id": "P02", "handoff_path": "handoff/P02.md",
        "verdict": None, "input_revision": "abc1234", "head_revision": "abc1234def",
        "drifted": False,
    }
    packet = d._build_carve_packet(cfg, "demo", 1, {}, rescope=rescope)
    assert "RE-SCOPING" in packet
    assert "current --" in packet
    assert "DRIFTED" not in packet
    assert "no committed review report found" in packet


def test_build_carve_packet_untargeted_has_no_rescope_section(
        tmp_state, sample_project, patch_siblings):
    """Discrimination: the untargeted headroom packet (rescope=None, item_id=None)
    carries NONE of the re-scope framing -- it is the general 'propose NEW
    packages' packet. Neutering the `if rescope is not None` guard would leak the
    re-scope section into every headroom carve."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    packet = d._build_carve_packet(cfg, "demo", 1, {})
    assert "RE-SCOPING" not in packet
    assert "Re-scope source" not in packet
    assert "You are proposing NEW handoff packages" in packet


def test_rescope_context_reads_handoff_verdict_and_drift(
        tmp_state, sample_project, patch_siblings):
    """_rescope_context assembles the packet inputs from real state: the origin's
    handoff_path + parsed input_revision, its committed review verdict, and the
    computed drift flag (a bogus input_revision vs a real main HEAD -> drifted)."""
    cfg = sample_project
    _make_feature_branch(cfg.root, "demo-P01", "P01.py", "# P01\n")
    _commit_review_report(
        cfg.root, "demo-P01", cfg.reports_dir,
        "# Review\n\nFindings: the module boundary is wrong.\n\nVERDICT: REJECTED\n"
        "REJECT_CLASS: architectural\n")
    rel = _write_origin_handoff(cfg.root, "demo-P01", "deadbeefdeadbeef")
    _seed_task("demo", "demo-P01", TaskState.READY_TO_CARVE, handoff_path=rel)
    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")

    ctx = d._rescope_context(cfg, states, "demo-P01")
    assert ctx["origin_task_id"] == "demo-P01"
    assert ctx["handoff_path"] == rel
    assert ctx["input_revision"] == "deadbeefdeadbeef"
    assert ctx["drifted"] is True                       # bogus rev != real main HEAD
    assert ctx["verdict"] is not None
    assert "module boundary is wrong" in ctx["verdict"]


def test_rescope_context_graceful_when_no_handoff_and_no_review(
        tmp_state, sample_project, patch_siblings):
    """Differently-routing NEGATIVE: an origin task with NO handoff_path and NO
    committed review degrades to handoff_path/input_revision None, verdict None,
    and drifted False (fail-safe-to-no-drift) -- the re-scope carve must still be
    launchable; the atomic supersede is what must never be skipped, not richness."""
    cfg = sample_project
    _seed_task("demo", "demo-P09", TaskState.READY_TO_CARVE, handoff_path=None)
    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")

    ctx = d._rescope_context(cfg, states, "demo-P09")
    assert ctx["handoff_path"] is None
    assert ctx["input_revision"] is None
    assert ctx["verdict"] is None
    assert ctx["drifted"] is False


def test_execute_carve_dispatch_rescope_supersedes_origin_with_rescoped_outcome(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """END-TO-END oracle (critique CRITIQUE.md:249): a CarveDispatch(task_id=P01)
    for a rejected-architectural task launches the re-scope carve AND supersedes
    the ORIGINAL task with the RESCOPED outcome, and the carve packet embeds the
    review verdict. Driven through run_pass/_execute so the CarveDispatch.task_id
    -> _execute_carve_dispatch plumbing is exercised, not just the leaf method."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n")
    _make_feature_branch(cfg.root, "demo-P01", "P01.py", "# P01\n")
    _commit_review_report(
        cfg.root, "demo-P01", cfg.reports_dir,
        "# Review\n\nFindings: the whole approach is wrong-layer.\n\n"
        "VERDICT: REJECTED\nREJECT_CLASS: architectural\n")
    rel = _write_origin_handoff(cfg.root, "demo-P01", "deadbeefdeadbeef")
    _seed_task("demo", "demo-P01", TaskState.READY_TO_CARVE, handoff_path=rel)
    _scripted(monkeypatch, [[reconcile.CarveDispatch(project="demo", task_id="demo-P01")]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    # the re-scope carve launched: a synthetic ACTIVE carve task exists
    carve = storage.load_state("demo", "carve-demo-1")
    assert carve is not None and carve.state is TaskState.ACTIVE
    # the ORIGINAL task is now SUPERSEDED, with the RESCOPED outcome recorded
    origin = storage.load_state("demo", "demo-P01")
    assert origin.state is TaskState.SUPERSEDED
    sup = [e for e in storage.iter_events("demo")
           if e.type is EventType.TASK_SUPERSEDED and e.task_id == "demo-P01"]
    assert len(sup) == 1
    assert sup[0].payload["outcome"] == daemon._RESCOPE_OUTCOME
    assert sup[0].payload["carve_task_id"] == "carve-demo-1"
    # the carve packet embeds the reviewer's verdict prose (a real re-scope, not
    # a blind re-carve)
    carve_att = carve.attempts[0]
    packet_md = (paths.attempt_dir("demo", carve_att.attempt_id) / "packet"
                 / "packet.md").read_text(encoding="utf-8")
    assert "RE-SCOPING" in packet_md
    assert "wrong-layer" in packet_md


def test_execute_carve_dispatch_rescope_no_supersede_when_admission_refused(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """M20 ATOMICITY oracle (the load-bearing one): when admission is refused at
    the effect boundary, the executor early-returns and the origin task is NOT
    superseded -- it stays in READY_TO_CARVE for a later pass. This is exactly the
    bug the pre-B7 split (Transition planned independently of the carve) had: the
    supersede fired even though no carve launched. Differently-routing NEGATIVE of
    the end-to-end test above (same origin, admission the only difference)."""
    cfg = sample_project
    rel = _write_origin_handoff(cfg.root, "demo-P01", "deadbeefdeadbeef")
    _seed_task("demo", "demo-P01", TaskState.READY_TO_CARVE, handoff_path=rel)
    d = daemon.Daemon({"demo": cfg.root})
    monkeypatch.setattr(d, "_dispatch_admissible", lambda *a, **k: (False, "paused"))
    states = storage.list_states("demo")

    events = d._execute_carve_dispatch(
        "demo", cfg, states, reconcile.CarveDispatch(project="demo", task_id="demo-P01"))

    assert events == []                                  # refused before any effect
    assert storage.load_state("demo", "demo-P01").state is TaskState.READY_TO_CARVE
    assert [e for e in storage.iter_events("demo")
            if e.type is EventType.TASK_SUPERSEDED and e.task_id == "demo-P01"] == []
    assert patch_siblings["launch_detached"] == []       # no carve launched


def test_execute_carve_dispatch_untargeted_supersedes_nothing(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Discrimination: an UNTARGETED headroom carve (task_id=None) is NOT a
    re-scope -- the executor launches the carve but emits NO TASK_SUPERSEDED for
    any origin task (the synthetic carve task is retired later by
    _consume_carve_exit, never here). Neutering the `is_rescope` guard would make
    every headroom carve try to supersede a None task."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n")
    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")

    events = d._execute_carve_dispatch(
        "demo", cfg, states, reconcile.CarveDispatch(project="demo"))

    assert any(e.type is EventType.ATTEMPT_PREFLIGHTED for e in events)  # carve launched
    assert [e for e in events if e.type is EventType.TASK_SUPERSEDED] == []


# --------------------------------------------------------------------------
# Oracle 3: EmitAttemptExit healing, one test per receipt.result

def test_emit_attempt_exit_done(tmp_state, sample_project, patch_siblings, monkeypatch):
    """B5 2026-07-20: the default pipeline now includes self_review, so an
    implementer DONE exit routes to SELF_REVIEWING (the warm self-check before
    the expensive frontier reviewer) rather than straight to AWAITING_REVIEW.
    The legacy (no-self_review) routing is pinned by the discrimination partner
    test_emit_attempt_exit_done_legacy_pipeline below."""
    task_id, attempt_id = "t-done", "att-done"
    _seed_running_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.SELF_REVIEWING
    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.ATTEMPT_EXITED in types
    assert EventType.TASK_TRANSITIONED in types


def test_emit_attempt_exit_done_legacy_pipeline(tmp_state, sample_project, patch_siblings, monkeypatch):
    """B5 parity / opt-out: a project composing the pre-B5 pipeline (no
    self_review) routes an implementer DONE exit STRAIGHT to AWAITING_REVIEW --
    byte-identical to pre-B5. Discrimination partner of test_emit_attempt_exit_
    done: same seed and receipt, only the composed pipeline differs. Neutering
    the `"self_review" in cfg.pipeline` guard would send this to SELF_REVIEWING."""
    _set_pipeline(sample_project, ["carve", "implement", "frontier_review",
                                   "triage", "auto_merge", "post_merge_gate"])
    task_id, attempt_id = "t-done-legacy", "att-done-legacy"
    _seed_running_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    assert storage.load_state("demo", task_id).state is TaskState.AWAITING_REVIEW


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


# --------------------------------------------------------------------------
# B5 2026-07-20: the self_review leg. Verdict comes from the COMMITTED
# <task>-SELFREVIEW.md (same P33 lesson as the frontier reviewer), never the
# process receipt. approved -> AWAITING_REVIEW; rejected -> QUEUED; a missing
# verdict degrades to AWAITING_REVIEW (self-review is an OPTIONAL pre-gate).

def _seed_self_review_attempt(project, task_id, attempt_id):
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.SELF_REVIEWING, since=utc_now(), handoff_path=None,
    )
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(attempt_id=attempt_id, role=Role.SELF_REVIEW, state=AttemptState.RUNNING,
                       route=route, started=utc_now())
    tsf.attempts.append(attempt)
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return storage.load_state(project, task_id)


def _commit_self_review_report(root, task_id, reports_dir, content):
    """Commit `<reports_dir>/<task_id>-SELFREVIEW.md` onto feat/<task_id> --
    that branch must already exist (see _make_feature_branch)."""
    branch = f"feat/{task_id}"
    subprocess.run(["git", "-C", str(root), "checkout", branch], check=True, capture_output=True)
    report_dir = root / reports_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{task_id}-SELFREVIEW.md").write_text(content, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "add", "-A"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "commit",
                    "-qm", f"self-review {task_id}"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True, capture_output=True)


def test_self_review_approved_goes_to_awaiting_review(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """A self_review leg whose COMMITTED verdict is APPROVED hands the task to
    the frontier reviewer (AWAITING_REVIEW)."""
    cfg = sample_project
    task_id, attempt_id = "t-sr-ok", "att-sr-ok"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_self_review_report(cfg.root, task_id, cfg.reports_dir,
                               "# Self-review\n\nLooks good.\n\nSELF_REVIEW: APPROVED\n")
    _seed_self_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert storage.load_state("demo", task_id).state is TaskState.AWAITING_REVIEW


def test_self_review_rejected_goes_to_queued(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """A REJECTED self_review verdict routes to QUEUED -- a fresh, budget-bounded
    fix attempt (deliberately NOT ACTIVE; see D-063). Neutering the committed-
    verdict parse (trusting the clean process exit as approved) would send it to
    AWAITING_REVIEW instead -- so this pins the P33 committed-verdict contract."""
    cfg = sample_project
    task_id, attempt_id = "t-sr-no", "att-sr-no"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_self_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Self-review\n\nFound a bug I cannot fix in-session.\n\nSELF_REVIEW: REJECTED\n")
    _seed_self_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert storage.load_state("demo", task_id).state is TaskState.QUEUED


def test_self_review_missing_verdict_proceeds_to_awaiting_review(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Self-review is an OPTIONAL pre-gate: a DONE receipt with NO committed
    verdict file must NOT block the task -- it degrades to AWAITING_REVIEW so the
    frontier reviewer (the real gate) still runs. (Contrast the frontier path,
    where a missing verdict fails SAFE to REVIEW_REJECTED -- self-review must not
    punish work for a self-check that could not complete.)"""
    cfg = sample_project
    task_id, attempt_id = "t-sr-missing", "att-sr-missing"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    # deliberately NO SELF_REVIEW report committed
    _seed_self_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert storage.load_state("demo", task_id).state is TaskState.AWAITING_REVIEW


def test_launch_self_review_mints_warm_borrowed_session_attempt(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """B5: LaunchSelfReview mints a NEW Attempt(role=SELF_REVIEW) that BORROWS
    the implementer's session_handle (a WARM resume via build_resume, never a
    cold build_dispatch). The task stays SELF_REVIEWING -- its own exit is what
    moves it on."""
    cfg = sample_project
    task_id = "t-sr-launch"
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    impl = Attempt(attempt_id="att-impl", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
                   route=route, started=utc_now(), ended=utc_now(),
                   worktree=str(cfg.root), branch=f"feat/{task_id}",
                   session_handle="sess-warm-123")
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id, project="demo",
                        state=TaskState.SELF_REVIEWING, since=utc_now(), handoff_path=None,
                        attempts=[impl])
    storage.append_and_apply("demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
                             type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()},
                             task_id=task_id)
    _scripted(monkeypatch, [[reconcile.LaunchSelfReview(task_id=task_id, source_attempt_id="att-impl")]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf2 = storage.load_state("demo", task_id)
    sr = [a for a in tsf2.attempts if a.role is Role.SELF_REVIEW]
    assert len(sr) == 1
    assert sr[0].session_handle == "sess-warm-123"          # borrowed the warm session
    assert tsf2.state is TaskState.SELF_REVIEWING            # unchanged; its own exit moves it
    assert patch_siblings["build_resume"][-1]["session"] == "sess-warm-123"  # warm resume
    assert patch_siblings["build_dispatch"] == []           # never a cold dispatch


def test_launch_self_review_no_session_handle_skips_to_awaiting_review(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """B5 graceful degradation: if the implementer left NO session_handle (early
    capture failed), the leg cannot resume warm -- so skip it and proceed to the
    frontier reviewer (the real gate) rather than stranding the task in
    SELF_REVIEWING. No SELF_REVIEW attempt is minted."""
    cfg = sample_project
    task_id = "t-sr-nohandle"
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    impl = Attempt(attempt_id="att-impl", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
                   route=route, started=utc_now(), ended=utc_now(),
                   worktree=str(cfg.root), branch=f"feat/{task_id}", session_handle=None)
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id, project="demo",
                        state=TaskState.SELF_REVIEWING, since=utc_now(), handoff_path=None,
                        attempts=[impl])
    storage.append_and_apply("demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
                             type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()},
                             task_id=task_id)
    _scripted(monkeypatch, [[reconcile.LaunchSelfReview(task_id=task_id, source_attempt_id="att-impl")]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.AWAITING_REVIEW
    assert [a for a in tsf2.attempts if a.role is Role.SELF_REVIEW] == []  # nothing minted
    assert patch_siblings["build_resume"] == []


def test_attempt_scan_surfaces_self_review_receipt_only_when_self_reviewing(
    tmp_state, sample_project
):
    """B5: _attempt_scan is the input-builder that feeds reconcile's `receipts`
    map; its eligibility tuple must include (SELF_REVIEWING, SELF_REVIEW) so a
    finished self_review leg's receipt reaches the planner (else EmitAttemptExit
    is never planned and the task strands in SELF_REVIEWING). The _scripted
    daemon tests stub plan_project and bypass this method, so this is its
    dedicated coverage -- AND a state-scoping neuter control: the SAME attempt on
    an ACTIVE task must NOT surface, proving the branch keys on the state, not
    the role alone (which would break every non-SELF_REVIEWING path)."""
    project, task_id, attempt_id = "demo", "t-sr-scan", "att-sr-scan"
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    att = Attempt(attempt_id=attempt_id, role=Role.SELF_REVIEW, state=AttemptState.EXITED,
                  route=route, started=utc_now(), ended=utc_now())
    _write_receipt(project, attempt_id, ReceiptResult.DONE, exit_code=0)
    d = daemon.Daemon({"demo": sample_project.root})

    tsf_sr = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
                           state=TaskState.SELF_REVIEWING, since=utc_now(), handoff_path=None,
                           attempts=[att])
    _lq, _pa, receipts = d._attempt_scan(project, {task_id: tsf_sr})
    assert receipts.get(attempt_id) is not None  # surfaced for consumption

    tsf_active = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
                               state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
                               attempts=[att])
    _lq2, _pa2, receipts2 = d._attempt_scan(project, {task_id: tsf_active})
    assert receipts2.get(attempt_id) is None  # NOT surfaced -- state-scoped, not role-alone


def test_frontier_review_wave_exit_fans_out_per_member_verdicts(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """P61 2026-07-20 (A9): ONE review attempt covers a whole wave; its single
    exit fans out to a PER-MEMBER verdict + transition. Two members reviewed
    by one session -- one APPROVED, one REJECTED -- must land in MERGE_READY
    and REVIEW_REJECTED respectively, each verdict parsed from that member's
    OWN committed REVIEW.md. IDEMPOTENT: scripting an EmitAttemptExit for BOTH
    members (as the receipt-based reconcile scan would, since the shared
    attempt is recorded on each) transitions every member exactly once -- the
    first fans out, the second finds no AWAITING_REVIEW members and no-ops."""
    cfg = sample_project
    attempt_id = "att-wave-fanout"
    for tid, v in (("t-fan-ok", "APPROVED"), ("t-fan-no", "REJECTED")):
        _make_feature_branch(cfg.root, tid, f"{tid}.py", f"# {tid}\n")
        _commit_review_report(cfg.root, tid, cfg.reports_dir, f"# Review\n\nVERDICT: {v}\n")
        # the SAME attempt_id recorded on EACH member (option-a wave attempt)
        _seed_review_attempt("demo", tid, attempt_id, wave_id="wave-fan")
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    d = daemon.Daemon({"demo": cfg.root})

    # A SINGLE exit (anchored on the first member only) must transition BOTH
    # members -- this is the discriminator vs the pre-A9 single-task consumer,
    # which would leave t-fan-no stuck in AWAITING_REVIEW.
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id="t-fan-ok", attempt_id=attempt_id)]])
    d.run_pass("demo")
    assert storage.load_state("demo", "t-fan-ok").state is TaskState.MERGE_READY
    assert storage.load_state("demo", "t-fan-no").state is TaskState.REVIEW_REJECTED
    recorded = [e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED]
    assert {e.task_id: e.payload["result"] for e in recorded} == {
        "t-fan-ok": "approved", "t-fan-no": "rejected"}
    assert {e.attempt_id for e in recorded} == {attempt_id}

    # IDEMPOTENT: the sibling EmitAttemptExit the scan also emits for t-fan-no
    # (its attempt copy is receipt-bearing) must be a no-op -- both members
    # already left AWAITING_REVIEW -- so no second REVIEW_RECORDED, no error,
    # no state change.
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id="t-fan-no", attempt_id=attempt_id)]])
    d.run_pass("demo")
    assert storage.load_state("demo", "t-fan-ok").state is TaskState.MERGE_READY
    assert storage.load_state("demo", "t-fan-no").state is TaskState.REVIEW_REJECTED
    recorded2 = [e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED]
    assert len(recorded2) == len(recorded), "no duplicate REVIEW_RECORDED on the idempotent sibling exit"


def test_frontier_review_missing_report_fails_safe_to_rejected(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Oracle O3 (missing): a DONE receipt whose <task>-REVIEW.md was never
    committed must fail safe to REVIEW_REJECTED, never MERGE_READY.

    SELF-CORRECT 2026-07-16 (bug 1 fix): the REVIEW_RECORDED payload value
    for this exact scenario changed from the bare "rejected" string to the
    distinguishing "missing" signal -- NO review artifact exists anywhere
    for this task (a review-LEG failure), which must never be conflated
    with a reviewer's genuine REJECTED verdict downstream. The task-STATE
    fail-safe (REVIEW_REJECTED, never MERGE_READY) is unchanged."""
    cfg = sample_project
    task_id, attempt_id = "t-rev-missing", "att-rev-missing"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    # deliberately: no REVIEW.md of any name committed onto feat/<task_id>
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "missing"


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


def test_frontier_review_limit_receipt_is_incomplete_not_rejected_and_pauses_provider(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """P56 2026-07-20 (M7, decoupled subset). A non-DONE review receipt is an
    INFRA failure of the review leg, not a quality rejection: it records
    result='incomplete' (so review_rejections_by_area, which counts only
    'rejected', is NOT polluted -> no false SpecAttention('rejections')
    runaway auto-pause on a provider outage) and ProviderPauses the review
    route on a LIMIT (so the re-review does not dive back into the same rate
    limit). Task still REVIEW_REJECTED (defense-in-depth unchanged; replacing
    the wasteful re-implementation with a review relaunch is A9/P61)."""
    cfg = sample_project
    task_id, attempt_id = "t-rev-limit", "att-rev-limit"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.LIMIT, exit_code=1)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "incomplete"       # NOT "rejected"

    # ProviderPause fired on the review route
    assert any(e.type is EventType.PROVIDER_STATE_CHANGED for e in storage.iter_events("demo"))

    # de-pollution: an infra failure is not counted as a quality rejection
    _mh, _co, rej_by_area, _bu = d._history("demo")
    assert rej_by_area == {}

    # defense-in-depth preserved: the task still lands REVIEW_REJECTED
    assert storage.load_state("demo", task_id).state is TaskState.REVIEW_REJECTED


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
    # SELF-CORRECT 2026-07-16 (bug 1 fix): no branch/artifact for this task
    # was ever created -- the distinguishing "missing" signal, not the bare
    # "rejected" this returned before the fix (see the two tests below for
    # the full O1 contract under the normal, non-nested layout).
    assert d._parse_review_verdict(cfg, "t-nested-never-written") == "missing"


# --------------------------------------------------------------------------
# SELF-CORRECT 2026-07-16: robust review-verdict derivation (bug 1) + the
# REVIEW_REJECTED reject-loop (bug 2). See daemon.py's _parse_review_verdict
# docstring and reconcile.py's module-contract item 10 for the full design
# rationale (including the documented, out-of-scope BLOCKED gap).

def test_parse_review_verdict_misnamed_file_still_found_and_approves(
    tmp_state, sample_project
):
    """O1 (bug 1, the live incident this package fixes): a reviewer who
    commits `P42-REVIEW.md` instead of the documented `<task_id>-REVIEW.md`
    must still have their APPROVED verdict found -- before this fix, the
    rigid single-path lookup missed it entirely and fail-safed a
    genuinely-APPROVED task to REJECTED."""
    cfg = sample_project
    task_id = "proj-P42"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    branch = f"feat/{task_id}"
    subprocess.run(["git", "-C", str(cfg.root), "checkout", branch],
                    check=True, capture_output=True)
    report_dir = cfg.root / cfg.reports_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    # Misnamed: "P42-REVIEW.md", NOT the documented "proj-P42-REVIEW.md" --
    # but the content names the full task_id, as a reviewer's own write-up
    # naturally would.
    (report_dir / "P42-REVIEW.md").write_text(
        f"# Review for {task_id}\n\nFindings: none.\n\nVERDICT: APPROVED\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(cfg.root),
                    "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(cfg.root),
                    "commit", "-qm", "review (misnamed)"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(cfg.root), "checkout", "main"],
                    check=True, capture_output=True)

    d = daemon.Daemon({"demo": cfg.root})
    assert d._parse_review_verdict(cfg, task_id) == "approved"


def test_parse_review_verdict_file_present_no_verdict_line_still_rejected(
    tmp_state, sample_project
):
    """O1 (fail-safe preserved): a review file exists (correctly named) but
    never writes a VERDICT line at all -- a malformed review, not an absent
    one -- must still fail safe to "rejected", exactly as before this
    package. Distinct from the "missing" signal (see the next test), which
    is reserved for NO review artifact existing anywhere."""
    cfg = sample_project
    task_id = "t-no-verdict-line"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nFindings: looks fine, forgot to write a verdict line.\n",
    )
    d = daemon.Daemon({"demo": cfg.root})
    assert d._parse_review_verdict(cfg, task_id) == "rejected"


def test_parse_review_verdict_no_artifact_anywhere_returns_missing(
    tmp_state, sample_project
):
    """O1 (bug 1 fix): when NO review artifact for this task exists
    anywhere on the branch (the reviewer never produced any output at all
    -- a review-LEG failure), the return value is the distinguishing
    "missing" signal, NOT the same "rejected" string a genuine reviewer
    REJECTED verdict returns -- so a future reconcile pass or operator can
    tell "nobody reviewed this" apart from "a reviewer rejected this"."""
    cfg = sample_project
    task_id = "t-no-artifact-anywhere"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    # deliberately: no REVIEW.md of any name committed onto feat/<task_id>
    d = daemon.Daemon({"demo": cfg.root})
    assert d._parse_review_verdict(cfg, task_id) == "missing"


# --------------------------------------------------------------------------
# P59b 2026-07-20 (A7, M6/I8): verdict-attempt binding. A re-review must bind
# the verdict to THIS review attempt and ignore any verdict a PRIOR (or other)
# attempt left committed on the branch -- both a stale REJECTED (which would
# wrongly re-trigger re-implementation, I8) and, worse, a foreign APPROVED
# (which would rubber-stamp an unreviewed merge, M6). The first review keeps
# the unbound-verdict path (no prior attempt => staleness impossible).

_ATT_OLD = "att-aaaaaaaaaaaa"
_ATT_CUR = "att-bbbbbbbbbbbb"
_ATT_FOREIGN = "att-cccccccccccc"


def test_parse_review_verdict_rereview_ignores_stale_prior_reject(
    tmp_state, sample_project
):
    """I8: on a re-review, a stale `VERDICT: REJECTED (attempt <old>)` left on
    the branch by a PRIOR review attempt -- with nothing from the current
    attempt -- must NOT be consumed as this attempt's rejection (which would
    kick off a full, wasteful re-implementation of possibly-fine work). It is
    a review-LEG failure of the current attempt => "missing" => relaunch."""
    cfg = sample_project
    task_id = "t-rereview-stale-reject"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        f"# Review\n\nPrior cycle found problems.\n\nVERDICT: REJECTED (attempt {_ATT_OLD})\n",
    )
    d = daemon.Daemon({"demo": cfg.root})
    # current attempt is _ATT_CUR and there WAS a prior review (is_first_review=False):
    # the stale verdict is bound to att-old, so it is ignored -> "missing" (relaunch),
    # NOT consumed as this attempt's rejection.
    assert d._parse_review_verdict(
        cfg, task_id, current_attempt_id=_ATT_CUR, is_first_review=False) == "missing"


def test_parse_review_verdict_rereview_ignores_foreign_approved(
    tmp_state, sample_project
):
    """M6 (the dangerous one): a `VERDICT: APPROVED (attempt <foreign>)` bound
    to some OTHER attempt must never rubber-stamp THIS review to MERGE_READY.
    On a re-review with nothing bound to the current attempt, a foreign
    approval is ignored => "missing" => relaunch, not "approved"."""
    cfg = sample_project
    task_id = "t-rereview-foreign-approve"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        f"# Review\n\nLooks good.\n\nVERDICT: APPROVED (attempt {_ATT_FOREIGN})\n",
    )
    d = daemon.Daemon({"demo": cfg.root})
    assert d._parse_review_verdict(
        cfg, task_id, current_attempt_id=_ATT_CUR, is_first_review=False) == "missing"


def test_parse_review_verdict_rereview_consumes_current_attempt_verdict(
    tmp_state, sample_project
):
    """The current attempt's verdict IS authoritative even when a stale prior
    verdict is still present: a file carrying BOTH a stale REJECTED (old
    attempt) and the current attempt's APPROVED resolves to "approved" -- only
    the current-bound verdict is counted, the stale one ignored."""
    cfg = sample_project
    task_id = "t-rereview-current-wins"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    # BOTH verdict lines are at line-start so the parser sees both -- the test
    # only means something if the stale REJECTED is genuinely competing and
    # must be IGNORED in favour of the current-attempt APPROVED.
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        f"# Review\n\nVERDICT: REJECTED (attempt {_ATT_OLD})\n\n"
        f"Re-review after fixes; issues resolved.\n\nVERDICT: APPROVED (attempt {_ATT_CUR})\n",
    )
    d = daemon.Daemon({"demo": cfg.root})
    assert d._parse_review_verdict(
        cfg, task_id, current_attempt_id=_ATT_CUR, is_first_review=False) == "approved"


def test_parse_review_verdict_first_review_accepts_current_bound_verdict(
    tmp_state, sample_project
):
    """A first review whose reviewer DID stamp the current attempt id is
    classified from that binding (approved) -- the binding path works on the
    first cycle too, not only unbound back-compat."""
    cfg = sample_project
    task_id = "t-first-bound"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        f"# Review\n\nAll good.\n\nVERDICT: APPROVED (attempt {_ATT_CUR})\n",
    )
    d = daemon.Daemon({"demo": cfg.root})
    assert d._parse_review_verdict(
        cfg, task_id, current_attempt_id=_ATT_CUR, is_first_review=True) == "approved"


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


def test_resume_archives_stale_receipt_so_no_premature_exit(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """P53 2026-07-19 (M1, CRITICAL). The wrapper ALWAYS writes an
    interrupt/error receipt.json before ATTEMPT_INTERRUPTED (wrapper.py
    step 9), so a real INTERRUPTED attempt has a receipt on disk -- the
    input EVERY existing resume test omits by seeding receipt=None, an
    input the wrapper contract cannot actually produce. On resume, that
    stale receipt must be archived; otherwise the next pass sees the
    resumed attempt back in RUNNING (in _INTERRUPTIBLE_STATES) WITH a
    receipt, fires a premature EmitAttemptExit on the STALE receipt,
    transitions the task off ACTIVE while the resumed session is still
    live, and dispatches a second implementer into the same worktree.

    Non-hollow: seeds the realistic on-disk receipt, then asserts the real
    daemon._attempt_scan surfaces NO receipt for the resumed attempt (so
    plan_project cannot emit the premature exit). Fails against pre-P53
    code, where receipt.json is left in place."""
    task_id, attempt_id = "t-m1", "att-m1"
    _seed_running_attempt("demo", task_id, attempt_id)

    tsf = storage.load_state("demo", task_id)
    att = tsf.attempt_by_id(attempt_id)
    att.state = AttemptState.INTERRUPTED
    att.session_handle = "sess-1"
    att.worktree = str(sample_project.root)
    storage.save_state(tsf)

    attempt_dir = paths.attempt_dir("demo", attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    receipt_json = attempt_dir / "receipt.json"
    receipt_json.write_text(
        json.dumps(Receipt(result=ReceiptResult.ERROR, exit_code=143,
                           blocked_reason="interrupted").to_dict()),
        encoding="utf-8")

    _scripted(monkeypatch, [[reconcile.ResumeAttempt(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    # The stale receipt is archived (audit trail), not left where the scan reads it.
    assert not receipt_json.exists()
    assert (attempt_dir / "receipt.pre-resume-1.json").exists()

    tsf2 = storage.load_state("demo", task_id)
    att2 = tsf2.attempt_by_id(attempt_id)
    assert att2.state is AttemptState.RUNNING

    # The real scan therefore surfaces NO receipt for the resumed attempt,
    # so plan_project cannot fire a premature EmitAttemptExit on the stale one.
    _log_quiet, _pid_alive, receipts = d._attempt_scan("demo", {task_id: tsf2})
    assert receipts.get(attempt_id) is None


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

def test_run_pass_isolates_a_failing_action_from_the_rest(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """P62 2026-07-20 (A10, M12): one action whose executor RAISES must not
    starve the remaining actions in the pass. The old single try/except spanned
    the WHOLE action loop, so the first raise aborted every remaining action --
    starving unrelated tasks. Now each action is isolated: the failure is
    surfaced as a TICK_ERROR and the pass continues."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    a1 = reconcile.OpenWave(task_ids=["boom"])   # will be made to raise
    a2 = reconcile.OpenWave(task_ids=["ok"])     # must still run
    _scripted(monkeypatch, [[a1, a2]])
    real_execute = d._execute

    def flaky_execute(project, cfg_, states, action):
        if action is a1:
            raise RuntimeError("boom-in-action")
        return real_execute(project, cfg_, states, action)

    monkeypatch.setattr(d, "_execute", flaky_execute)
    d.run_pass("demo")   # must NOT raise

    events = list(storage.iter_events("demo"))
    # the SECOND action still executed (its WAVE_OPENED landed) despite the first raising ...
    assert any(e.type is EventType.WAVE_OPENED and e.payload.get("task_ids") == ["ok"]
               for e in events), "the action after the failing one was starved (M12)"
    # ... and the failure was surfaced, not swallowed nor allowed to abort the pass.
    assert any(e.type is EventType.TICK_ERROR and "boom-in-action" in (e.payload.get("error") or "")
               for e in events), "the failing action must be logged as a TICK_ERROR"


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
    # P61 (A9): ONE frontier session reviews the whole wave -- exactly one
    # distinct attempt_id -- but that single attempt is RECORDED ON EVERY
    # member (so each member's latest attempt is the review and A7's per-member
    # is_first_review reads a correct history). A 2-task wave => 2
    # ATTEMPT_CREATED events, ONE attempt_id, one per member task.
    assert {e.attempt_id for e in created} == {created[0].attempt_id}, "must be ONE frontier session"
    assert sorted(e.task_id for e in created) == ["t1", "t2"], "recorded on every member"
    for e in created:
        assert e.payload["attempt"]["role"] == "frontier-review"
        assert e.payload["attempt"]["route"]["route_id"] == "fake-cli"
    # both members carry the review attempt in their state
    assert any(a.attempt_id == created[0].attempt_id
               for a in storage.load_state("demo", "t1").attempts)
    assert any(a.attempt_id == created[0].attempt_id
               for a in storage.load_state("demo", "t2").attempts)

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


# --------------------------------------------------------------------------
# P38 2026-07-16 Oracle 1: the HTTP bind address is configurable via
# policy.http_bind (config.Policy, default "127.0.0.1" -- safe/loopback by
# default); daemon.py binds ThreadingHTTPServer on it instead of a hardcoded
# "127.0.0.1", carried from the min-http_port project (mirrors the existing
# http_port selection). See docs/runtime-process-model.md §3.

def test_http_bind_defaults_to_loopback(http_daemon):
    """With no http_bind configured, the server binds 127.0.0.1 (safe default)."""
    d = http_daemon
    assert d.http_bind == "127.0.0.1"
    assert d._httpd.server_address[0] == "127.0.0.1"


def test_http_bind_overridable_to_bridge_address(tmp_state, sample_project, patch_siblings, monkeypatch):
    """NYXLOOM_HTTP_BIND = "0.0.0.0" makes the server bind all interfaces --
    used on a private ciu bridge network, never on host-network. 2026-07-20:
    the bind is INFRA-sourced from the env var, no longer a toml key (see
    config.Policy.http_bind); this drives the capability end-to-end through the
    real ThreadingHTTPServer."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    _set_ephemeral_http_port(sample_project)
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "0.0.0.0")

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        assert d.http_port != 0
        assert d.http_bind == "0.0.0.0"
        assert d._httpd.server_address[0] == "0.0.0.0"
    finally:
        d.stop()
        t.join(timeout=5)


def test_toml_http_bind_never_reaches_the_real_bind(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """2026-07-20 (contract change): http_bind is INFRA-sourced, so a hand-
    edited toml http_bind must NOT reach the running socket even with NO env
    set -- otherwise the SAME bind-mounted toml read on the host could silently
    expose the unauthenticated control plane on the LAN. The toml here asks for
    0.0.0.0 (the dangerous value); the real ThreadingHTTPServer must still bind
    127.0.0.1. This is the end-to-end twin of test_config.py's unit assertion.
    (Replaces the pre-2026-07-20 test that proved env 'overrides' toml -- toml
    is no longer a source to override.)"""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    monkeypatch.delenv("NYXLOOM_HTTP_BIND", raising=False)
    _set_ephemeral_http_port(sample_project)
    _set_http_bind(sample_project, "0.0.0.0")  # writes a toml http_bind that must be ignored

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        assert d.http_port != 0
        assert d.http_bind == "127.0.0.1"
        assert d._httpd.server_address[0] == "127.0.0.1"
    finally:
        d.stop()
        t.join(timeout=5)


def _read_log_records(log_dir: Path) -> list[dict]:
    """P01: the http_bind notice moved from a stderr `print` to
    `log.warning(...)` -- read back the rendered JSONL records the same way
    test_log.py does, rather than capturing stderr."""
    p = log_dir / "nyxloom.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_nonloopback_bind_prints_unauthenticated_notice(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """2026-07-20: a non-loopback bind states the security assumption out loud
    at startup -- the control plane is unauthenticated, only safe on a private
    unpublished network. 2026-07-21 (P01): the notice is now a structured
    `log.warning` record (read back from the JSONL file) rather than a raw
    stderr print."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "0.0.0.0")
    _set_ephemeral_http_port(sample_project)
    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    d.stop()
    t.join(timeout=5)

    warnings = [r for r in _read_log_records(log_dir) if r.get("level") == "warning"]
    assert any(
        "UNAUTHENTICATED" in r.get("msg", "") and r.get("http_bind") == "0.0.0.0"
        for r in warnings
    )


def test_loopback_bind_prints_no_notice_THE_NEGATIVE(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """The default loopback bind is safe and needs no callout -- so the notice
    must NOT fire, or it becomes boot noise on every safe daemon and the real
    (non-loopback) case stops standing out. 2026-07-21 (P01): asserts against
    the structured log record stream, not stderr."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    monkeypatch.delenv("NYXLOOM_HTTP_BIND", raising=False)
    _set_ephemeral_http_port(sample_project)
    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    d.stop()
    t.join(timeout=5)

    assert d.http_bind == "127.0.0.1"
    warnings = [r for r in _read_log_records(log_dir) if r.get("level") == "warning"]
    assert not any("UNAUTHENTICATED" in r.get("msg", "") for r in warnings)


# --------------------------------------------------------------------------
# P38 2026-07-16 Oracle 3: `nyxloom doctor`'s dashboard-URL line reflects
# reachability -- a bridge bind (0.0.0.0) also names the alias address
# reachable from a co-networked container (e.g. the devcontainer), not only
# the host-loopback address a devcontainer operator could never reach.

def test_doctor_dashboard_line_stays_loopback_by_default(tmp_state, sample_project, capsys, monkeypatch):
    monkeypatch.setattr(doctor, "doctor_project", lambda cfg: [])
    exit_code = cli.main(["doctor"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:" in out
    assert "nyxloomd" not in out


def test_doctor_dashboard_line_names_bridge_alias_when_bind_is_bridged(
        tmp_state, sample_project, capsys, monkeypatch):
    monkeypatch.setattr(doctor, "doctor_project", lambda cfg: [])
    # 2026-07-20: bind is env-sourced now, so drive the bridge case via the env
    # var rather than a (now-ignored) toml http_bind.
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "0.0.0.0")
    exit_code = cli.main(["doctor"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "http://nyxloomd:" in out
    assert "http://127.0.0.1:" in out  # host-loopback still documented as working


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


# --------------------------------------------------------------------------
# P44 2026-07-16 (anti-runaway self-correction) Oracle 1: _history's
# review_rejections_by_area is now WINDOWED (HISTORY_REJECTION_WINDOW_SECONDS)
# -- before this fix it counted rejections over the ENTIRE event log and
# only ever increased, so a project that once hit >= 2 rejections in one
# area stayed >= 2 forever, even with every rejection long resolved. Real
# event fixtures with explicit timestamps (storage.append_and_apply's
# `timestamp` kwarg) prove the window actually ages old ones out.

def test_history_windowed_rejection_count_ages_out_old_rejections(tmp_state, sample_project):
    """Oracle 1: 3 rejections OLDER than the window + 1 rejection INSIDE
    the window, same area -- review_rejections_by_area drops to just the
    1 recent one (below the SpecAttention('rejections') threshold of 2).
    Pre-fix, this would read 4 (whole-log count, never drops)."""
    project = "demo"
    now = utc_now()
    old_ts = now - timedelta(seconds=daemon.HISTORY_REJECTION_WINDOW_SECONDS + 3600)
    recent_ts = now - timedelta(seconds=60)

    for _ in range(3):
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.REVIEW_RECORDED, payload={"result": "rejected", "area": "ui"},
            timestamp=old_ts,
        )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.REVIEW_RECORDED, payload={"result": "rejected", "area": "ui"},
        timestamp=recent_ts,
    )

    d = daemon.Daemon({"demo": sample_project.root})
    _merge_history, _carve_outcomes, review_rejections_by_area, _blocked = d._history(project)

    assert review_rejections_by_area.get("ui", 0) == 1


def test_history_windowed_rejection_count_within_window_all_count(tmp_state, sample_project):
    """Companion (no regression): rejections that ARE within the window
    still count normally -- preserves the pre-fix >= 2 threshold behavior
    for a genuinely CURRENT rejection streak."""
    project = "demo"
    recent_ts = utc_now() - timedelta(seconds=60)

    for _ in range(2):
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.REVIEW_RECORDED, payload={"result": "rejected", "area": "ui"},
            timestamp=recent_ts,
        )

    d = daemon.Daemon({"demo": sample_project.root})
    _merge_history, _carve_outcomes, review_rejections_by_area, _blocked = d._history(project)

    assert review_rejections_by_area.get("ui", 0) == 2


def test_history_merge_progress_units_from_changed_files(tmp_state, sample_project):
    """P64 2026-07-20 (A12, D-061/M17): _history derives a merge's progress
    units from the files it changed (the progress_units payload both merge
    paths now write). Pre-A12 NOTHING emitted progress_units, so units read 0
    for EVERY merge and the ratchet false-fired after any N merges. A merge
    that changed files must read units>0; a genuinely empty merge reads 0."""
    project = "demo"
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "abc123",
                 "progress_units": ["a.py", "b.py", "c.py"], "source_kind": "review"},
        task_id="t-real",
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "def456", "progress_units": [], "source_kind": "review"},
        task_id="t-empty",
    )
    d = daemon.Daemon({"demo": sample_project.root})
    merge_history, _co, _rej, _bu = d._history(project)
    by_task = {t: (u, s) for t, u, s in merge_history}
    assert by_task["t-real"] == (3, "review"), "3 changed files => 3 progress units, not zero"
    assert by_task["t-empty"] == (0, "review"), "an empty merge is genuinely zero-progress"


def test_history_windowed_blocked_underspecified_ages_out(tmp_state, sample_project):
    """P64 2026-07-20 (A12, M16): the contract-blocker count is now WINDOWED
    like review_rejections. 3 contract blockers OLDER than the window + 1
    recent => count drops to 1. Pre-A12 it was a full-log-forever count that
    stayed high and re-fired SpecAttention('blocked-underspecified') whenever
    its dedup event scrolled out of the 500-event window."""
    project = "demo"
    now = utc_now()
    old_ts = now - timedelta(seconds=daemon.HISTORY_REJECTION_WINDOW_SECONDS + 3600)
    recent_ts = now - timedelta(seconds=60)
    for _ in range(3):
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.TASK_BLOCKED,
            payload={"from": "MERGED", "blocker": {"type": "contract", "detail": "old"}},
            timestamp=old_ts,
        )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_BLOCKED,
        payload={"from": "MERGED", "blocker": {"type": "contract", "detail": "recent"}},
        timestamp=recent_ts,
    )
    d = daemon.Daemon({"demo": sample_project.root})
    _mh, _co, _rej, blocked = d._history(project)
    assert blocked == 1, "old contract blockers must age out of the underspecified count"


def test_history_environment_blocker_not_counted_as_underspecified(tmp_state, sample_project):
    """P64 2026-07-20 (A12, M16): a post-merge GATE failure is now typed
    ENVIRONMENT, not CONTRACT, so it must NOT inflate the
    blocked_underspecified (contract) count -- otherwise every failing
    post-merge gate would read as an 'underspecified handoff'."""
    project = "demo"
    recent_ts = utc_now() - timedelta(seconds=60)
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_BLOCKED,
        payload={"from": "VALIDATING",
                 "blocker": {"type": "environment", "detail": "post-merge gate exit_code=1"}},
        timestamp=recent_ts,
    )
    d = daemon.Daemon({"demo": sample_project.root})
    _mh, _co, _rej, blocked = d._history(project)
    assert blocked == 0, "an ENVIRONMENT (gate-failure) blocker is not an underspecified-handoff signal"


# --------------------------------------------------------------------------
# P44 2026-07-16 Oracle 3 (integration half): a PERSISTENT runaway
# auto-pauses the project and emits exactly ONE escalation, not
# one-per-cycle. (The pure-function half of Oracle 3 -- detect_runaways on
# synthetic event streams -- lives in test_watchdog.py.)

def test_watchdog_persistent_runaway_auto_pauses_project_single_escalation(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Seed 7 ATTEMPT_CREATED events for one task_id (> watchdog's default
    attempt_loop_count=5) with NO progress event -- an 'attempt-loop'
    RunawaySignal that keeps re-detecting identically on every pass (the
    event log doesn't change between passes here: plan_project is
    monkeypatched to [] so nothing else gets appended). After
    RUNAWAY_PERSIST_AFTER_CYCLES consecutive passes: the project is
    auto-paused ('drain-agents'), but only ONE NEEDS_OPERATOR{reason:
    'runaway'} escalation exists in the whole event log -- not one per
    pass."""
    project = "demo"
    task_id = "demo-attempt-loop-task"
    now = utc_now()
    for i in range(7):
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.ATTEMPT_CREATED, payload={}, task_id=task_id,
            timestamp=now - timedelta(seconds=(7 - i)),
        )

    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    d = daemon.Daemon({"demo": sample_project.root})

    assert not paths.pause_flag(project).exists()

    for _ in range(daemon.RUNAWAY_PERSIST_AFTER_CYCLES):
        d.run_pass(project)

    assert paths.pause_flag(project).exists()
    assert paths.pause_flag(project).read_text(encoding="utf-8").strip() == "drain-agents"

    runaway_escalations = [
        e for e in storage.iter_events(project)
        if e.type is EventType.NEEDS_OPERATOR and e.payload.get("reason") == "runaway"
    ]
    assert len(runaway_escalations) == 1
    assert runaway_escalations[0].payload["pattern"] == "attempt-loop"
    assert runaway_escalations[0].payload["key"] == f"attempt-loop:{task_id}"

    pause_events = [e for e in storage.iter_events(project) if e.type is EventType.PAUSE_SET]
    assert len(pause_events) == 1
    assert pause_events[0].payload["reason"] == "runaway"


def test_watchdog_transient_signal_suppresses_action_without_pausing(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """A SINGLE pass with a runaway condition suppresses the matching
    repeating action and escalates, but does NOT yet auto-pause (the
    streak has only reached 1, below RUNAWAY_PERSIST_AFTER_CYCLES). 6
    consecutive SPEC_ATTENTION(reason='rejections') events trip BOTH the
    'reconcile-thrash' detector (a same-reason run > 5) AND the
    'notification-storm' per-reason detector (> 5 SPEC_ATTENTION events
    sharing one reason within the window) -- two DISTINCT signals from one
    underlying condition, each escalating independently (proving the
    single-escalation-per-CONDITION oracle does not collapse distinct
    conditions into one)."""
    project = "demo"
    now = utc_now()
    for i in range(6):
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.SPEC_ATTENTION, payload={"reason": "rejections", "detail": None},
            timestamp=now - timedelta(seconds=(6 - i)),
        )

    _scripted(monkeypatch, [[reconcile.SpecAttention(reason="rejections", detail=None)]])
    d = daemon.Daemon({"demo": sample_project.root})

    n = d.run_pass(project)

    # The freshly-planned SpecAttention('rejections') was suppressed by the
    # watchdog (both signals match its reason) -- zero actions actually
    # executed this pass, even though plan_project returned one.
    assert n == 0
    assert not paths.pause_flag(project).exists()

    runaway_escalations = [
        e for e in storage.iter_events(project)
        if e.type is EventType.NEEDS_OPERATOR and e.payload.get("reason") == "runaway"
    ]
    assert len(runaway_escalations) == 2
    patterns = {e.payload["pattern"] for e in runaway_escalations}
    assert patterns == {"reconcile-thrash", "notification-storm"}


# --------------------------------------------------------------------------
# P49 2026-07-19: fixes a live incident -- unpausing re-paused within one
# reconcile_interval_seconds, repeatedly. The in-memory streak used to climb
# unboundedly every pass spent already-paused (detect_runaways keeps
# re-finding the same still-undecayed historical condition), so by the time
# an operator unpaused, the streak was already far past
# RUNAWAY_PERSIST_AFTER_CYCLES and the very next pass re-paused almost
# instantly.

def test_watchdog_streak_frozen_while_already_paused_not_unbounded(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Same attempt-loop setup as the oracle-3 test above: auto-pause after
    RUNAWAY_PERSIST_AFTER_CYCLES passes. Then run MANY more passes while
    STILL paused (simulating time an operator hasn't looked yet) -- before
    the fix the in-memory streak would climb to
    RUNAWAY_PERSIST_AFTER_CYCLES + 10; after the fix it stays frozen at 0
    every pass spent paused, so clearing the pause file and running exactly
    RUNAWAY_PERSIST_AFTER_CYCLES - 1 MORE passes must NOT yet re-pause."""
    project = "demo"
    task_id = "demo-attempt-loop-task"
    now = utc_now()
    for i in range(7):
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.ATTEMPT_CREATED, payload={}, task_id=task_id,
            timestamp=now - timedelta(seconds=(7 - i)),
        )

    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    d = daemon.Daemon({"demo": sample_project.root})

    for _ in range(daemon.RUNAWAY_PERSIST_AFTER_CYCLES):
        d.run_pass(project)
    assert paths.pause_flag(project).exists()

    # 10 MORE passes while still paused -- the exact scenario that used to
    # let the streak climb unboundedly.
    for _ in range(10):
        d.run_pass(project)
    assert paths.pause_flag(project).exists()

    # Operator unpauses (matches this project's own pause-flag convention:
    # remove the file for 'run' mode).
    paths.pause_flag(project).unlink()
    assert not paths.pause_flag(project).exists()

    for _ in range(daemon.RUNAWAY_PERSIST_AFTER_CYCLES - 1):
        d.run_pass(project)
    assert not paths.pause_flag(project).exists(), (
        "streak must have reset to 0 on unpause -- re-pausing after fewer "
        "than RUNAWAY_PERSIST_AFTER_CYCLES fresh passes means it did not"
    )


def test_watchdog_repauses_after_fresh_persist_cycles_if_condition_still_open(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Companion to the freeze test above: the watchdog is NOT silently
    disabled by the fix -- if the SAME condition is still genuinely open
    after an operator unpauses, exactly RUNAWAY_PERSIST_AFTER_CYCLES fresh
    passes re-pauses it (a real window, not an instant re-trip, but not a
    permanent bypass either)."""
    project = "demo"
    task_id = "demo-attempt-loop-task"
    now = utc_now()
    for i in range(7):
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.ATTEMPT_CREATED, payload={}, task_id=task_id,
            timestamp=now - timedelta(seconds=(7 - i)),
        )

    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    d = daemon.Daemon({"demo": sample_project.root})

    for _ in range(daemon.RUNAWAY_PERSIST_AFTER_CYCLES):
        d.run_pass(project)
    assert paths.pause_flag(project).exists()

    paths.pause_flag(project).unlink()

    for _ in range(daemon.RUNAWAY_PERSIST_AFTER_CYCLES):
        d.run_pass(project)
    assert paths.pause_flag(project).exists(), (
        "a genuinely still-open condition must still re-pause -- the fix "
        "must not disable the watchdog"
    )


# --------------------------------------------------------------------------
# B4b 2026-07-20 (D-060 triage stage; critique CRITIQUE.md:207). The daemon
# half: the frontier reviewer self-stamps a REJECT_CLASS the daemon captures in
# the REVIEW_RECORDED event (Tier-2 producer, D-066); _triage_classes derives
# per-task classes for the input; the drift base sha (_head_revision) and the
# re-dispatch verdict embed (_review_rationale) are computed for reconcile.
# Each oracle carries a NEGATIVE control so none passes hollowly.

def test_frontier_review_rejected_stamps_reject_class(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """A REJECTED review whose committed report carries a REJECT_CLASS line ->
    the daemon records that class in the REVIEW_RECORDED event, so
    _triage_classes can later route the reject."""
    cfg = sample_project
    task_id, attempt_id = "t-rc-arch", "att-rc-arch"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nThe module boundary is wrong.\n\n"
        "VERDICT: REJECTED\nREJECT_CLASS: architectural\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"
    assert recorded.payload["reject_class"] == "architectural"


def test_frontier_review_approved_has_no_reject_class(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """NEGATIVE: an APPROVED review records NO reject_class (the class is a
    property of a rejection only) -- guards against always-stamping."""
    cfg = sample_project
    task_id, attempt_id = "t-rc-approved", "att-rc-approved"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nLooks good.\n\nVERDICT: APPROVED\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "approved"
    assert "reject_class" not in recorded.payload


def test_frontier_review_rejected_without_class_omits_key(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """NEGATIVE/graceful: a REJECTED review whose reviewer stamped NO
    REJECT_CLASS (older reviewer) records result=rejected but no reject_class,
    so _triage_classes drops it to reconcile's mechanical budget path."""
    cfg = sample_project
    task_id, attempt_id = "t-rc-none", "att-rc-none"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nSomething is off.\n\nVERDICT: REJECTED\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"
    assert "reject_class" not in recorded.payload


def test_parse_reject_class_unit(tmp_state, sample_project):
    """_parse_reject_class reads the committed line; NEGATIVE: absent line ->
    None; an unrecognised value -> None (never a garbage class into routing)."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})

    _make_feature_branch(cfg.root, "t-pc-1", "a.py", "# a\n")
    _commit_review_report(cfg.root, "t-pc-1", cfg.reports_dir,
                          "VERDICT: REJECTED\nREJECT_CLASS: product\n")
    assert d._parse_reject_class(cfg, "t-pc-1") == "product"

    _make_feature_branch(cfg.root, "t-pc-2", "b.py", "# b\n")
    _commit_review_report(cfg.root, "t-pc-2", cfg.reports_dir, "VERDICT: REJECTED\n")
    assert d._parse_reject_class(cfg, "t-pc-2") is None

    _make_feature_branch(cfg.root, "t-pc-3", "c.py", "# c\n")
    _commit_review_report(cfg.root, "t-pc-3", cfg.reports_dir,
                          "VERDICT: REJECTED\nREJECT_CLASS: totally-bogus\n")
    assert d._parse_reject_class(cfg, "t-pc-3") is None


def test_review_rationale_unit(tmp_state, sample_project):
    """_review_rationale returns the committed prose; NEGATIVE: no review file
    -> None; a very long review is bounded with a truncation marker."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})

    _make_feature_branch(cfg.root, "t-rr-1", "a.py", "# a\n")
    _commit_review_report(cfg.root, "t-rr-1", cfg.reports_dir,
                          "# Review\n\nThe cap is off by one.\n\nVERDICT: REJECTED\n")
    got = d._review_rationale(cfg, "t-rr-1")
    assert got is not None and "cap is off by one" in got

    # NEGATIVE: a branch with no committed review at all.
    _make_feature_branch(cfg.root, "t-rr-2", "b.py", "# b\n")
    assert d._review_rationale(cfg, "t-rr-2") is None

    # bounded: an oversized review is truncated.
    _make_feature_branch(cfg.root, "t-rr-3", "c.py", "# c\n")
    _commit_review_report(cfg.root, "t-rr-3", cfg.reports_dir, "X" * 9000 + "\nVERDICT: REJECTED\n")
    bounded = d._review_rationale(cfg, "t-rr-3", max_chars=4000)
    assert bounded is not None and len(bounded) < 5000
    assert "truncated" in bounded


def test_head_revision_resolves_main(tmp_state, sample_project):
    """_head_revision returns main's sha; NEGATIVE: a bogus root -> None
    (fail-safe, so a git hiccup never spuriously flags drift)."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    expected = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", cfg.default_branch],
        capture_output=True, text=True,
    ).stdout.strip()
    assert d._head_revision(cfg) == expected
    assert len(expected) == 40                       # a real full sha, not a placeholder

    from dataclasses import replace as _replace
    bogus = _replace(cfg, root=cfg.root / "does-not-exist")
    assert d._head_revision(bogus) is None


def _seed_review_recorded(project, task_id, result, reject_class=None):
    payload = {"result": result}
    if reject_class is not None:
        payload["reject_class"] = reject_class
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.REVIEW_RECORDED, payload=payload, task_id=task_id,
    )


def test_triage_classes_binds_latest_rejected_event(tmp_state, sample_project):
    """_triage_classes maps a currently-REVIEW_REJECTED task to the class on its
    LATEST REVIEW_RECORDED event. Three NEGATIVE controls: (a) a task not in
    REVIEW_REJECTED is excluded; (b) a task whose LATEST review leg is an infra
    'incomplete' does NOT inherit an earlier rejection's class (no stale bleed);
    (c) a rejected event with no class is excluded."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})

    # (positive) rejected + class, task in REVIEW_REJECTED
    _seed_task("demo", "t-tc-pos", TaskState.REVIEW_REJECTED)
    _seed_review_recorded("demo", "t-tc-pos", "rejected", reject_class="architectural")
    # (a) same event shape but task is QUEUED, not REVIEW_REJECTED
    _seed_task("demo", "t-tc-wrongstate", TaskState.QUEUED)
    _seed_review_recorded("demo", "t-tc-wrongstate", "rejected", reject_class="architectural")
    # (b) earlier rejected+class, then a LATER infra 'incomplete' -> no bleed
    _seed_task("demo", "t-tc-infra", TaskState.REVIEW_REJECTED)
    _seed_review_recorded("demo", "t-tc-infra", "rejected", reject_class="architectural")
    _seed_review_recorded("demo", "t-tc-infra", "incomplete")
    # (c) rejected but NO class stamped
    _seed_task("demo", "t-tc-noclass", TaskState.REVIEW_REJECTED)
    _seed_review_recorded("demo", "t-tc-noclass", "rejected")

    states = {tid: storage.load_state("demo", tid) for tid in
              ("t-tc-pos", "t-tc-wrongstate", "t-tc-infra", "t-tc-noclass")}
    out = d._triage_classes("demo", states)

    assert out == {"t-tc-pos": "architectural"}


def test_dispatch_implementer_embeds_prior_review_verdict(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """End-to-end wiring: a QUEUED task with a committed REJECTED review on its
    feat branch dispatches with build_dispatch(prior_verdict=<the review prose>)
    -- proving _review_rationale is read AND threaded. NEGATIVE: a task with no
    committed review dispatches with prior_verdict=None (unchanged first pass)."""
    cfg = sample_project

    # positive: prior rejected review exists on the branch
    (cfg.root / "handoff" / "demo-P02-mutex.md").write_text(MUTEX_HANDOFF, encoding="utf-8")
    task_id = "demo-P02-mutex"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(cfg.root, task_id, cfg.reports_dir,
                          "# Review\n\nThe backoff ignores the max cap.\n\nVERDICT: REJECTED\n")
    _seed_task("demo", task_id, TaskState.QUEUED, handoff_path="handoff/demo-P02-mutex.md")
    _scripted(monkeypatch, [[reconcile.DispatchImplementer(task_id=task_id, route_id="fake-cli")]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    call = patch_siblings["build_dispatch"][-1]
    assert call["task_id"] == task_id
    assert call["prior_verdict"] is not None
    assert "ignores the max cap" in call["prior_verdict"]

    # NEGATIVE: a different task with a feat branch but NO committed review
    task2 = "t-di-fresh"
    _make_feature_branch(cfg.root, task2, f"{task2}.py", f"# {task2}\n")
    _seed_task("demo", task2, TaskState.QUEUED, handoff_path=None)
    _scripted(monkeypatch, [[reconcile.DispatchImplementer(task_id=task2, route_id="fake-cli")]])
    d.run_pass("demo")

    call2 = patch_siblings["build_dispatch"][-1]
    assert call2["task_id"] == task2
    assert call2["prior_verdict"] is None


def test_build_input_plumbs_head_revision_and_triage_class(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Self-review gap-closer (the B5 _attempt_scan lesson): reconcile tests
    inject head_revision/triage_class directly, so a field could be CONSUMED by
    the planner yet never PLUMBED by the daemon's _build_input -- a silent
    dead-wire. Run a REAL pass and inspect the ReconcileInput the daemon built:
    head_revision must be main's sha, and a REVIEW_REJECTED task's stamped class
    must appear in triage_class. Without the two _build_input lines this fails."""
    cfg = sample_project
    _seed_task("demo", "t-plumb", TaskState.REVIEW_REJECTED)
    _seed_review_recorded("demo", "t-plumb", "rejected", reject_class="product")

    captured = _scripted(monkeypatch, [[]])          # planner returns no actions
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    assert captured, "plan_project was never called -- no ReconcileInput built"
    inp = captured[0]
    assert inp.head_revision is not None and len(inp.head_revision) == 40
    assert inp.triage_class.get("t-plumb") == "product"


# --------------------------------------------------------------------------
# B6/P74 reviewer session-reuse + carver SPINE-DIGEST (D-R10). The reuse
# DECISION (which session, gated on stage context) is test_reconcile.py's
# concern; these drive daemon._execute directly via _scripted and assert the
# EXECUTION contract: warm build_resume vs cold build_dispatch, the A7
# verdict-attempt binding preserved on the resumed session, and the spine
# digest referenced-by-pointer (never slurped) in both packets.

def _frontier_routes():
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n")


def test_review_warm_resume_uses_build_resume_and_stamps_fresh_attempt_id(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """B6 (P74, D-R10) + the A7-on-resume SAFETY oracle. A LaunchReview carrying
    resume_session WARM-resumes it via adapters.build_resume (the cache-hit path),
    NOT a cold build_dispatch. A7 is PRESERVED: the resume prompt carries the
    FRESH attempt id and requires the `(attempt <id>)` stamp -- so a warm session
    (which still holds a PRIOR wave's OLD attempt id) cannot misbind a stale
    verdict, since the daemon counts only verdicts carrying the current attempt
    id. This binding is exactly why session-reuse was blocked until A7."""
    cfg = sample_project
    task_id = "demo-P02-mutex"
    _frontier_routes()
    _seed_task("demo", task_id, TaskState.AWAITING_REVIEW,
               handoff_path="handoff/demo-P02-mutex.md")
    _scripted(monkeypatch, [[reconcile.LaunchReview(
        wave_id="wave-2", task_ids=[task_id], resume_session="sess-rev-1")]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    # WARM: build_resume with the prior handle; NO cold review build_dispatch.
    assert len(patch_siblings["build_resume"]) == 1
    resume = patch_siblings["build_resume"][0]
    assert resume["session"] == "sess-rev-1"
    assert patch_siblings["build_dispatch"] == []
    # a fresh attempt actually launched
    assert len(patch_siblings["launch_detached"]) == 1
    new_attempt_id = patch_siblings["launch_detached"][0].attempt_id
    assert new_attempt_id != "sess-rev-1"
    # A7: the resume prompt binds THIS fresh attempt id -- not the resumed session
    assert f"(attempt {new_attempt_id})" in resume["prompt"]
    assert "sess-rev-1" not in resume["prompt"]   # session handle is never a verdict stamp
    # daemon-set observability marker
    created = next(e for e in storage.iter_events("demo")
                   if e.type is EventType.ATTEMPT_CREATED and e.payload.get("resumed_from"))
    assert created.payload["resumed_from"] == "sess-rev-1"


def test_review_cold_uses_build_dispatch_when_resume_session_none(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Negative discriminator for the cold/warm branch: resume_session=None -> the
    review is a COLD build_dispatch (the pre-B6 path), build_resume never called.
    Neutering the branch would flip exactly one of these two assertions."""
    cfg = sample_project
    task_id = "demo-P02-mutex"
    _frontier_routes()
    _seed_task("demo", task_id, TaskState.AWAITING_REVIEW,
               handoff_path="handoff/demo-P02-mutex.md")
    _scripted(monkeypatch, [[reconcile.LaunchReview(
        wave_id="wave-1", task_ids=[task_id], resume_session=None)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")
    assert patch_siblings["build_resume"] == []
    assert len(patch_siblings["build_dispatch"]) == 1
    assert patch_siblings["build_dispatch"][0]["handoff_path"].endswith("packet.md")
    # cold ATTEMPT_CREATED carries NO resumed_from marker
    assert all(not e.payload.get("resumed_from")
               for e in storage.iter_events("demo")
               if e.type is EventType.ATTEMPT_CREATED)


def test_review_packet_references_spine_digest_by_pointer_not_body(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """B6 oracle 2 (review packet): the packet REFERENCES the spine digest by its
    PATH but never slurps its body. Discriminator: a real SPINE-DIGEST.md with a
    unique sentinel exists on disk; the packet must contain the PATH and NOT the
    sentinel -- a slurping implementation would embed the sentinel and fail."""
    cfg = sample_project
    task_id = "demo-P02-mutex"
    _frontier_routes()
    _seed_task("demo", task_id, TaskState.AWAITING_REVIEW,
               handoff_path="handoff/demo-P02-mutex.md")
    digest = cfg.root / cfg.reports_dir / "SPINE-DIGEST.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("REVIEW_SPINE_SENTINEL_DO_NOT_SLURP\n", encoding="utf-8")
    _scripted(monkeypatch, [[reconcile.LaunchReview(wave_id="w", task_ids=[task_id])]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")
    attempt_id = patch_siblings["launch_detached"][0].attempt_id
    packet_md = (paths.attempt_dir("demo", attempt_id) / "packet" / "packet.md").read_text(
        encoding="utf-8")
    assert "SPINE-DIGEST.md" in packet_md                          # referenced by pointer
    assert "REVIEW_SPINE_SENTINEL_DO_NOT_SLURP" not in packet_md   # NOT slurped


def test_carve_packet_references_spine_digest_by_pointer_and_maintains_it(
        tmp_state, sample_project):
    """B6 oracle 2 (carve packet): same referenced-not-slurped contract, plus the
    carver is instructed to MAINTAIN the digest (it is the digest's owner)."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    digest = cfg.root / cfg.reports_dir / "SPINE-DIGEST.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("CARVE_SPINE_SENTINEL_DO_NOT_SLURP\n", encoding="utf-8")
    packet = d._build_carve_packet(cfg, "demo", 1, storage.list_states("demo"))
    assert "SPINE-DIGEST.md" in packet                          # referenced
    assert "MAINTAIN it" in packet                              # carver owns/maintains it
    assert "CARVE_SPINE_SENTINEL_DO_NOT_SLURP" not in packet    # NOT slurped


def test_carve_packet_omits_spine_digest_when_context_lacks_flag(
        tmp_state, sample_project, monkeypatch):
    """Negative: the spine pointer is gated on the carve stage's context (stages-
    as-data). Strip "spine-digest" and the whole section disappears -- proving the
    pointer is not an unconditional string."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    monkeypatch.setattr(daemon.stages, "stage_context", lambda name: frozenset())
    packet = d._build_carve_packet(cfg, "demo", 1, storage.list_states("demo"))
    assert "SPINE-DIGEST.md" not in packet


def test_extract_usage_reads_cache_read_tokens_from_fake_output():
    """B6 oracle 1 (the cached_in OBSERVABLE): the reuse WIN is a prompt-cache
    hit, surfaced as usage.cached_in > 0. The fake CLI has no native usage, but it
    prints to the attempt log and the PRODUCTION parser (output-format-json) reads
    cache_read_input_tokens from that output -- so a fake emitting a usage line
    yields a REAL captured cached_in through the real code path. Paired with the
    reconcile/daemon reuse-MECHANISM tests (which prove the 2nd wave actually
    resumes the prior session), this closes the 'cache-hit on second wave' oracle.
    A cold wave (cache_read 0) captures 0 -- the baseline the warm wave beats."""
    from nyxloom.config import RouteDef
    route = RouteDef(route_id="fake-review", cli="fake", model="m",
                     usage_source="output-format-json")
    warm = adapters.extract_usage(
        route, Path("/tmp"),
        '{"usage": {"input_tokens": 500, "output_tokens": 40, '
        '"cache_read_input_tokens": 9000}}\n')
    assert warm.cached_in == 9000                     # a real cache hit, captured
    cold = adapters.extract_usage(
        route, Path("/tmp"),
        '{"usage": {"input_tokens": 40000, "output_tokens": 40, '
        '"cache_read_input_tokens": 0}}\n')
    assert (cold.cached_in or 0) == 0                 # cold baseline


# --------------------------------------------------------------------------
# PACKAGE P02 (docs/plan-logging.md §3 D-L3, §4.4): verbosity config,
# bootstrap & runtime control. Oracles:
#   1. precedence chain (four tests, each removing the top layer)
#   2. live flip, no restart + persists (simulated respawn)
#   3. invalid level -> 400, unchanged
#   4. no domain event (D-L4) -- a log record only

def _set_logging_level(cfg, level):
    """Append a `[logging]` table (D-L3 layer 3 -- the primary project's own
    static default) to the project's toml. A NEW top-level section, so
    simple appending is safe regardless of where [notify]/[policy] etc. end."""
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "[logging]" not in text:
        text += f'\n[logging]\nlevel = "{level}"\n'
        ptoml.write_text(text, encoding="utf-8")


def test_resolve_level_runtime_file_beats_everything(tmp_state, sample_project, monkeypatch):
    """Layer 1 (runtime-override file) wins even with layer 2 (env) AND
    layer 3 ([logging] level) both also set to something else."""
    monkeypatch.setenv("NYXLOOM_LOG_LEVEL", "warning")
    _set_logging_level(sample_project, "error")
    paths.daemon_log_level_path().write_text("debug", encoding="utf-8")

    registry = {"demo": sample_project.root}
    assert daemon.resolve_level(registry) == ("debug", "runtime-file")


def test_resolve_level_env_beats_config_and_default(tmp_state, sample_project, monkeypatch):
    """Layer 1 removed (no runtime-file): layer 2 (env) wins over layer 3
    ([logging] level, also set) and the hardcoded default."""
    assert not paths.daemon_log_level_path().exists()
    monkeypatch.setenv("NYXLOOM_LOG_LEVEL", "warning")
    _set_logging_level(sample_project, "error")

    registry = {"demo": sample_project.root}
    assert daemon.resolve_level(registry) == ("warning", "env")


def test_resolve_level_config_beats_default(tmp_state, sample_project, monkeypatch):
    """Layers 1 & 2 removed: layer 3 ([logging] level in the primary
    project's config) wins over the hardcoded INFO default."""
    assert not paths.daemon_log_level_path().exists()
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    _set_logging_level(sample_project, "error")

    registry = {"demo": sample_project.root}
    assert daemon.resolve_level(registry) == ("error", "config")


def test_resolve_level_default_when_nothing_set(tmp_state, sample_project, monkeypatch):
    """All three layers absent: hardcoded INFO, source 'default'."""
    assert not paths.daemon_log_level_path().exists()
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    # sample_project's toml has no [logging] section (SAMPLE_PROJECT_TOML).

    registry = {"demo": sample_project.root}
    assert daemon.resolve_level(registry) == ("info", "default")

    # Also true with NO registry at all (e.g. before any project loads).
    assert daemon.resolve_level(None) == ("info", "default")
    assert daemon.resolve_level({}) == ("info", "default")


def test_resolve_level_treats_corrupt_layers_as_absent(tmp_state, sample_project, monkeypatch):
    """A garbage runtime-file/env value is not a level log_module accepts --
    resolve_level falls through to the next layer rather than propagating
    ValueError (so a corrupted state file can never crash daemon boot)."""
    monkeypatch.setenv("NYXLOOM_LOG_LEVEL", "not-a-level")
    _set_logging_level(sample_project, "warning")
    paths.daemon_log_level_path().write_text("also-not-a-level", encoding="utf-8")

    registry = {"demo": sample_project.root}
    # runtime-file garbage -> skip to env; env garbage -> skip to config.
    assert daemon.resolve_level(registry) == ("warning", "config")


def test_log_level_post_flips_live_no_restart_and_persists(http_daemon, tmp_state, monkeypatch):
    """Oracle 2: POST /api/config/log-level changes the EFFECTIVE level
    without a restart (the same already-running process's `daemon.log`
    immediately obeys it) AND persists to the runtime-override file, so a
    simulated respawn (a fresh resolve_level() call reading that file)
    returns the flipped level."""
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    log_dir = tmp_state / "logs"

    # Boot default is INFO (sample_project has no [logging], no env, no
    # runtime-file yet) -- a DEBUG call is dropped pre-flip.
    daemon.log.debug("pre_flip_debug_marker")
    pre = _read_log_records(log_dir)
    assert not any(r.get("msg") == "pre_flip_debug_marker" for r in pre)

    req = urllib.request.Request(
        f"{base}/api/config/log-level",
        data=json.dumps({"level": "DEBUG"}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=5)
    assert resp.status == 200
    assert json.loads(resp.read()) == {"ok": True, "level": "debug"}

    # Live flip, no restart: the running daemon's own already-imported
    # logger now emits DEBUG in the SAME process, no reconfigure/restart.
    daemon.log.debug("post_flip_debug_marker")
    post = _read_log_records(log_dir)
    assert any(r.get("msg") == "post_flip_debug_marker" for r in post)

    # Persists: the runtime-override file carries the new level...
    assert paths.daemon_log_level_path().read_text(encoding="utf-8").strip() == "debug"
    # ...and a simulated respawn (fresh resolve_level()) reads it back.
    assert daemon.resolve_level(d.registry) == ("debug", "runtime-file")


def test_log_level_post_invalid_level_400_unchanged(http_daemon, tmp_state, monkeypatch):
    """Oracle 3: a bad level name -> 400, and the effective level (plus its
    source) is left exactly as it was -- no partial/garbage write."""
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    before = daemon.resolve_level(d.registry)
    assert before == ("info", "default")

    req = urllib.request.Request(
        f"{base}/api/config/log-level",
        data=json.dumps({"level": "not-a-real-level"}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 400

    assert daemon.resolve_level(d.registry) == before
    assert not paths.daemon_log_level_path().exists()


def test_log_level_post_missing_level_400(http_daemon):
    """400 for a missing/non-string `level`, same as every other POST
    endpoint's missing-field contract on this surface."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    req = urllib.request.Request(
        f"{base}/api/config/log-level",
        data=json.dumps({}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 400


def test_log_level_get_reports_effective_level_and_source(http_daemon, monkeypatch):
    """GET /api/logs/level reports the current effective level + its
    source, and reflects a live POST flip without a restart."""
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    data = json.loads(urllib.request.urlopen(f"{base}/api/logs/level", timeout=5).read())
    assert data == {"level": "info", "source": "default"}

    req = urllib.request.Request(
        f"{base}/api/config/log-level",
        data=json.dumps({"level": "warning"}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5)

    data2 = json.loads(urllib.request.urlopen(f"{base}/api/logs/level", timeout=5).read())
    assert data2 == {"level": "warning", "source": "runtime-file"}


def test_log_level_config_path_405_on_get(http_daemon):
    """/api/config/log-level joins _CONFIG_POST_PATHS: GET on it is 405, the
    same guard every other config-mutation endpoint gets."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base}/api/config/log-level", timeout=5)
    assert exc_info.value.code == 405


def test_log_level_post_emits_log_not_domain_event(http_daemon, tmp_state):
    """Oracle 4 (D-L4): the level change emits an INFO log record and
    appends NO domain event -- the event log is byte-for-byte unchanged
    across the POST, unlike every other POST /api/config/* endpoint on
    this surface (which all append a CONFIG_CHANGED/PAUSE_* event)."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    log_dir = tmp_state / "logs"

    before_events = [e.to_dict() for e in storage.iter_events("demo", since=0)]

    req = urllib.request.Request(
        f"{base}/api/config/log-level",
        data=json.dumps({"level": "warning"}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=5)
    assert resp.status == 200

    after_events = [e.to_dict() for e in storage.iter_events("demo", since=0)]
    assert after_events == before_events   # NOT ONE new domain event

    records = _read_log_records(log_dir)
    assert any(
        r.get("level") == "info" and r.get("msg") == "log level changed"
        and r.get("new_level") == "warning"
        for r in records
    )


def test_daemon_run_configures_logging_before_loop_and_logs_started(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Daemon.run() calls log.configure(resolve_level(), paths.logs_dir())
    BEFORE the main loop, then logs an INFO 'daemon started' -- exercised
    via the immediate-stop pattern (no HTTP fixture needed)."""
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    _set_ephemeral_http_port(sample_project)
    log_dir = tmp_state / "logs"

    d = daemon.Daemon({"demo": sample_project.root})
    d._stop_event.set()   # loop flag pre-set: run() configures + starts/stops HTTP, then exits
    d.run()

    records = _read_log_records(log_dir)
    started = [r for r in records if r.get("msg") == "daemon started"]
    assert started, "expected an INFO 'daemon started' record"
    assert started[0]["level"] == "info"
    assert started[0]["effective_level"] == "info"
    assert started[0]["level_source"] == "default"


def test_daemon_run_bootstraps_from_project_logging_level(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """End-to-end: a project's `[logging] level` (D-L3 layer 3) is honoured
    at Daemon.run() bootstrap -- a DEBUG record is captured in the log file
    even though nothing was flipped at runtime."""
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    _set_ephemeral_http_port(sample_project)
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    _set_logging_level(sample_project, "debug")
    log_dir = tmp_state / "logs"

    d = daemon.Daemon({"demo": sample_project.root})
    d._stop_event.set()
    d.run()

    started = [r for r in _read_log_records(log_dir) if r.get("msg") == "daemon started"]
    assert started
    assert started[0]["effective_level"] == "debug"
    assert started[0]["level_source"] == "config"

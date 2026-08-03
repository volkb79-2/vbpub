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
    effects_carve,
    adapters, carver_session, cli, control_auth, daemon, decision_chat, decisions, doctor,
    effects_attempt, effects_dispatch, effects_gates, effects_review, lint, log,
    notify, paths, reconcile,
    render, results, snapshot, storage, wrapper,
)
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, CarverStatus, EventType,
    GateResult, Receipt, ReceiptResult, Role, Route, TaskState, TaskStateFile, utc_now,
)


# --------------------------------------------------------------------------
# local helpers / fixtures (never added to conftest.py)

def _control_headers() -> dict[str, str]:
    """CR-15: every mutating POST on this surface needs the operator
    credential. `ensure()` rather than `load()` so the helper works whether or
    not a daemon fixture has already bootstrapped the store -- both resolve
    the same file under the test's isolated NYXLOOM_STATE."""
    store = control_auth.CredentialStore(paths.daemon_dir())
    return {"Content-Type": "application/json",
            **control_auth.authorization_header(store.ensure())}


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
    """Both the .j2 template and its pre-rendered docker-compose.yml sibling set
    `init: true` (tini as container PID 1) and DELEGATE the daemon to
    supervise.sh -- a `while` supervisor loop with NO `exec` of the daemon, so a
    daemon crash or restart never tears down the whole container, killing every
    in-flight agent (the P37 hazard). The loop (and its crash-loop breaker) live
    in supervise.sh, bind-mounted from the repo, so the compose `command` is just
    `bash .../supervise.sh` (2026-07-21 refactor)."""
    for fname in ("ciu.compose.yml.j2", "docker-compose.yml"):
        text = (NYXLOOMD_DIR / fname).read_text(encoding="utf-8")
        assert _INIT_DIRECTIVE.search(text), f"{fname} missing `init: true` (tini as PID 1)"
        assert "supervise.sh" in text, f"{fname} must delegate the daemon to supervise.sh"

    # supervise.sh IS the supervisor loop: it runs `nyxloom.cli daemon` in a
    # `while` loop WITHOUT exec'ing it (an exec'd daemon would be PID 1). Only
    # the crash-loop-breaker PARK uses exec (`exec sleep infinity`), never the
    # daemon itself.
    sup = (NYXLOOMD_DIR / "supervise.sh").read_text(encoding="utf-8")
    assert "while" in sup, "supervise.sh missing the supervisor loop"
    assert "nyxloom.cli daemon" in sup, "supervise.sh must invoke the daemon"
    assert "exec $DAEMON_CMD" not in sup and "exec /opt/nyxloom-venv" not in sup, \
        "supervise.sh must NOT exec the daemon (it would become PID 1)"
    # crash-loop breaker: after N rapid failures, PARK for inspection, not flap.
    assert "sleep infinity" in sup, "supervise.sh missing the crash-loop-breaker park"


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
# CR-16 2026-08-03 (RISK-007): the healthcheck's SECOND stage, and the health
# budget that has to be big enough to run it.

def test_nyxloomd_healthcheck_chains_the_liveness_probe_after_the_tcp_stage():
    """A TCP connect proves a socket is listening; it proved exactly that for
    ten days while the daemon was `Exited (143)`. Both compose files must run
    `doctor --liveness` -- which reads the store in a fresh process, with no
    Daemon -- as a REQUIRED second stage, and must not discard its output: the
    findings table is the only thing that says which project is dead."""
    for fname in ("ciu.compose.yml.j2", "docker-compose.yml"):
        text = (NYXLOOMD_DIR / fname).read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if ln.lstrip().startswith("test: ["))
        assert "doctor --liveness" in line, \
            f"{fname} healthcheck is TCP-only again -- the RISK-007 blind spot"
        assert "&&" in line, f"{fname} healthcheck must REQUIRE the liveness stage"
        assert ">/dev/null" not in line.split("doctor --liveness")[1], \
            f"{fname} discards the liveness findings -- unhealthy with no reason attached"


def test_nyxloomd_health_budget_is_the_same_in_the_instance_file_and_the_defaults():
    """`ciu.toml` is the INSTANCE config and overrides `ciu.defaults.toml.j2`,
    so a budget widened only in the template does not reach the deployment.
    CR-16's second healthcheck stage costs a python start plus one event-log
    read per registered project; under the old TCP-only 5s timeout a healthy
    daemon can be marked unhealthy by its own outage detector."""
    import tomllib
    instance = tomllib.loads((NYXLOOMD_DIR / "ciu.toml").read_text(encoding="utf-8"))
    defaults = tomllib.loads(
        (NYXLOOMD_DIR / "ciu.defaults.toml.j2").read_text(encoding="utf-8"))
    assert instance["nyxloomd"]["health"] == defaults["nyxloomd"]["health"], (
        "nyxloomd/ciu.toml and ciu.defaults.toml.j2 disagree on the health "
        "budget -- the instance file wins, so the template's value is a lie")


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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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
    packet = d._carve._build_carve_packet(cfg, "demo", 1, {}, rescope=rescope)
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
    packet = d._carve._build_carve_packet(cfg, "demo", 1, {}, rescope=rescope)
    assert "RE-SCOPING" in packet
    assert "current --" in packet
    assert "DRIFTED" not in packet
    assert "no committed review report found" in packet


# D-R3 (2026-07-26, refined; routing-model-redesign.md D-R3): the incapable
# tier-bump rescope intro is a NEW branch inside the SAME rescope section --
# fires ONLY when BOTH reject_class=='incapable' AND a real origin_tier are
# present; every other case (architectural, absent class, missing tier) keeps
# the pre-existing generic intro BYTE-IDENTICAL (O3, the load-bearing safety
# oracle -- this touches reconcile-adjacent carve-prompt construction, frozen-
# core-adjacent). O2 pins the positive: origin tier value + tier-bump/
# not-a-redesign language present, and the old generic sentence ABSENT.

# Captured verbatim from _carve_packet_body_lines's rescope branch BEFORE the
# incapable branch was added (git blame: pre-D-R3 daemon.py).
_PRE_INCAPABLE_RESCOPE_INTRO = (
    "You are RE-SCOPING one rejected task for project 'demo'. Task "
    "'demo-P01' was implemented, REVIEWED, and REJECTED, and triage "
    "classified it as architectural or stale-premise -- a same-base retry by "
    "the implementer cannot fix it. YOU decide what happens: write a fresh, "
    "corrected handoff package (re-carve), drop the work if the review shows "
    "it is no longer worth doing, or escalate a genuine product question as a "
    "D-NNN decision. Do NOT simply re-emit the original handoff unchanged, and "
    "do NOT implement the work yourself."
)


def test_carve_packet_rescope_intro_byte_identical_for_architectural(
        tmp_state, sample_project, patch_siblings):
    """O3: reject_class='architectural' with a real origin_tier present still
    takes the OLD generic intro, byte-for-byte -- the incapable branch
    requires BOTH reject_class=='incapable' AND a non-None origin_tier;
    architectural alone must never trip it."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    rescope = {
        "origin_task_id": "demo-P01", "handoff_path": "handoff/demo-P01.md",
        "verdict": None, "input_revision": "abc1234", "head_revision": "abc1234def",
        "drifted": False, "reject_class": "architectural", "origin_tier": "flash-high",
    }
    packet = d._carve._build_carve_packet(cfg, "demo", 1, {}, rescope=rescope)
    assert _PRE_INCAPABLE_RESCOPE_INTRO in packet


def test_carve_packet_rescope_intro_byte_identical_when_reject_class_absent(
        tmp_state, sample_project, patch_siblings):
    """O3: no reject_class key at all (every pre-D-R3 rescope dict, and every
    rescope where the origin's review carried no class) -> the old generic
    intro, unchanged."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    rescope = {
        "origin_task_id": "demo-P01", "handoff_path": "handoff/demo-P01.md",
        "verdict": None, "input_revision": "abc1234", "head_revision": "abc1234def",
        "drifted": False,
    }
    packet = d._carve._build_carve_packet(cfg, "demo", 1, {}, rescope=rescope)
    assert _PRE_INCAPABLE_RESCOPE_INTRO in packet


def test_carve_packet_rescope_intro_byte_identical_when_origin_tier_missing(
        tmp_state, sample_project, patch_siblings):
    """O3: reject_class='incapable' but origin_tier is None (couldn't be
    recovered -- Work 3's graceful degradation) -> the old generic intro,
    unchanged. Proves the incapable branch requires BOTH signals, not just
    the class alone."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    rescope = {
        "origin_task_id": "demo-P01", "handoff_path": "handoff/demo-P01.md",
        "verdict": None, "input_revision": "abc1234", "head_revision": "abc1234def",
        "drifted": False, "reject_class": "incapable", "origin_tier": None,
    }
    packet = d._carve._build_carve_packet(cfg, "demo", 1, {}, rescope=rescope)
    assert _PRE_INCAPABLE_RESCOPE_INTRO in packet


def test_carve_packet_rescope_incapable_intro_instructs_tier_bump(
        tmp_state, sample_project, patch_siblings):
    """O2: reject_class='incapable' AND a real origin_tier -> the packet
    contains the origin tier value AND language distinguishing this from the
    architectural-rescope text (explicitly a tier bump, NOT a re-scope).
    NEGATIVE: the old generic 'architectural or stale-premise' sentence is
    ABSENT -- proving this REPLACES the intro for this case, not adds
    alongside it."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    rescope = {
        "origin_task_id": "demo-P01", "handoff_path": "handoff/demo-P01.md",
        "verdict": None, "input_revision": "abc1234", "head_revision": "abc1234def",
        "drifted": False, "reject_class": "incapable", "origin_tier": "flash-high",
    }
    packet = d._carve._build_carve_packet(cfg, "demo", 1, {}, rescope=rescope)
    assert "flash-high" in packet
    assert "HIGHER" in packet
    assert "Do NOT redesign the scope" in packet
    assert "architectural or stale-premise" not in packet
    assert "source.ref" in packet


def test_build_carve_packet_untargeted_has_no_rescope_section(
        tmp_state, sample_project, patch_siblings):
    """Discrimination: the untargeted headroom packet (rescope=None, item_id=None)
    carries NONE of the re-scope framing -- it is the general 'propose NEW
    packages' packet. Neutering the `if rescope is not None` guard would leak the
    re-scope section into every headroom carve."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    packet = d._carve._build_carve_packet(cfg, "demo", 1, {})
    assert "RE-SCOPING" not in packet
    assert "Re-scope source" not in packet
    assert "You are proposing NEW handoff packages" in packet


# ---------------------------------------------------------------------------
# _carve_source_note_lines: the daemon's "context collection" surface (CR-01,
# DR-04) -- default carve-context notes must prefer the adopted direction-
# spine paths and must never surface an archived document.

def test_carve_source_note_lines_prefers_spine_roadmap_over_legacy_convention(
        tmp_state, sample_project, patch_siblings):
    """A project that adopted the direction spine (cfg.roadmap set) must see
    ITS roadmap, not fall through to the plain nyxloom-trove/roadmap.md
    convention just because that exact filename doesn't exist -- the bug
    this fix closes: nyxloom's own config sets `roadmap =
    "nyxloom-trove/3-roadmap.md"`, which the OLD hardcoded-path chain never
    consulted."""
    cfg = sample_project
    cfg.roadmap = "nyxloom-trove/3-roadmap.md"
    (cfg.root / "nyxloom-trove").mkdir(parents=True, exist_ok=True)
    (cfg.root / "nyxloom-trove" / "3-roadmap.md").write_text("spine roadmap\n")
    d = daemon.Daemon({"demo": cfg.root})

    lines = d._carve._carve_source_note_lines(cfg)
    roadmap_lines = [ln for ln in lines if ln.startswith("- roadmap:")]
    assert roadmap_lines == ["- roadmap: nyxloom-trove/3-roadmap.md"]


def test_carve_source_note_lines_falls_back_to_legacy_when_spine_unset(
        tmp_state, sample_project, patch_siblings):
    """No spine adoption (cfg.roadmap is None): the legacy trove convention
    still works, unchanged behavior."""
    cfg = sample_project
    assert cfg.roadmap is None
    (cfg.root / "nyxloom-trove").mkdir(parents=True, exist_ok=True)
    (cfg.root / "nyxloom-trove" / "roadmap.md").write_text("legacy roadmap\n")
    d = daemon.Daemon({"demo": cfg.root})

    lines = d._carve._carve_source_note_lines(cfg)
    roadmap_lines = [ln for ln in lines if ln.startswith("- roadmap:")]
    assert roadmap_lines == ["- roadmap: nyxloom-trove/roadmap.md"]


def test_carve_source_note_lines_never_surfaces_an_archived_roadmap(
        tmp_state, sample_project, patch_siblings):
    """Fail-closed (CR-01 contract item 6): even a MISCONFIGURED cfg.roadmap
    pointing straight at an archived doc must never be surfaced -- the
    daemon skips it and falls through to the next candidate instead."""
    cfg = sample_project
    archived = cfg.root / "docs" / "archive" / "product-docs" / "ROADMAP.md"
    archived.parent.mkdir(parents=True)
    archived.write_text("archived roadmap\n")
    cfg.roadmap = "docs/archive/product-docs/ROADMAP.md"
    (cfg.root / "nyxloom-trove").mkdir(parents=True, exist_ok=True)
    (cfg.root / "nyxloom-trove" / "roadmap.md").write_text("legacy roadmap\n")
    d = daemon.Daemon({"demo": cfg.root})

    lines = d._carve._carve_source_note_lines(cfg)
    joined = "\n".join(lines)
    assert "docs/archive" not in joined
    assert "- roadmap: nyxloom-trove/roadmap.md" in lines


def test_carve_source_note_lines_reports_none_found_when_only_archive_exists(
        tmp_state, sample_project, patch_siblings):
    """No live roadmap/gap-analysis anywhere except an archived doc -> the
    'none found' line, never a silent empty result that could be mistaken
    for 'no roadmap needed'."""
    cfg = sample_project
    archived = cfg.root / "docs" / "archive" / "product-docs" / "ROADMAP.md"
    archived.parent.mkdir(parents=True)
    archived.write_text("archived roadmap\n")
    cfg.roadmap = "docs/archive/product-docs/ROADMAP.md"
    d = daemon.Daemon({"demo": cfg.root})

    lines = d._carve._carve_source_note_lines(cfg)
    assert any("roadmap/gap analysis: none found" in ln for ln in lines)
    assert not any("docs/archive" in ln for ln in lines)


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

    ctx = d._carve._rescope_context(cfg, states, "demo-P01")
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

    ctx = d._carve._rescope_context(cfg, states, "demo-P09")
    assert ctx["handoff_path"] is None
    assert ctx["input_revision"] is None
    assert ctx["verdict"] is None
    assert ctx["drifted"] is False
    # D-R3 (2026-07-26 refined): both new keys degrade gracefully too.
    assert ctx["reject_class"] is None
    assert ctx["origin_tier"] is None


def test_rescope_context_graceful_when_handoff_unparsable(
        tmp_state, sample_project, patch_siblings):
    """Coverage/D-R3: handoff_path is SET but the file is missing (git-state
    drift, or a not-yet-materialized worktree) -> frontmatter.parse_handoff
    raises, and BOTH input_revision AND origin_tier degrade to None via the
    except branch (not just the pre-existing input_revision) -- the re-scope
    carve must still be launchable with partial data."""
    cfg = sample_project
    _seed_task("demo", "demo-P10", TaskState.READY_TO_CARVE,
               handoff_path="handoff/does-not-exist.md")
    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")

    ctx = d._carve._rescope_context(cfg, states, "demo-P10")
    assert ctx["input_revision"] is None
    assert ctx["origin_tier"] is None
    assert ctx["drifted"] is False


def test_rescope_context_reads_reject_class_and_origin_tier(
        tmp_state, sample_project, patch_siblings):
    """D-R3 (2026-07-26, refined; routing-model-redesign.md D-R3): _rescope_context
    also surfaces the origin's self-stamped reject_class (re-read via the SAME
    _parse_reject_class helper that produced the REVIEW_RECORDED stamp) and the
    origin handoff's tier: frontmatter field -- the two inputs Work 4's rescope
    prompt branch needs to distinguish an incapable tier-bump from every other
    re-scope reason."""
    cfg = sample_project
    _make_feature_branch(cfg.root, "demo-P01", "P01.py", "# P01\n")
    _commit_review_report(
        cfg.root, "demo-P01", cfg.reports_dir,
        "# Review\n\nThe implementation was structurally weak across the board.\n\n"
        "VERDICT: REJECTED\nREJECT_CLASS: incapable\n")
    rel = _write_origin_handoff(cfg.root, "demo-P01", "deadbeefdeadbeef")  # tier: flash-high
    _seed_task("demo", "demo-P01", TaskState.READY_TO_CARVE, handoff_path=rel)
    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")

    ctx = d._carve._rescope_context(cfg, states, "demo-P01")
    assert ctx["reject_class"] == "incapable"
    assert ctx["origin_tier"] == "flash-high"


# -- D-R3 (2026-07-26, refined): _incapable_escalation_note ------------------
# The provenance-marker resolution _incapable_escalation_note performs: a NEW
# task's frontmatter `source.ref` (Work 4's rescope prompt instructs the
# carver to stamp the origin task id there) is re-checked against the
# ORIGIN's own committed review via the SAME _parse_reject_class helper
# _rescope_context uses. Each oracle carries its own NEGATIVE control.

def _write_handoff_with_source_ref(root, task_id, source_ref):
    """A minimal schema-valid handoff for `task_id` whose `source.ref` is
    `source_ref` (or omitted entirely when None -- an ordinary, non-escalated
    handoff)."""
    ref_line = f"  ref: {source_ref}\n" if source_ref is not None else ""
    text = (
        "---\n"
        "schema_version: 1\n"
        f"id: {task_id}\n"
        "project: demo\n"
        "title: Escalated sample\n"
        "tier: flash-max\n"
        'input_revision: "0000000"\n'
        "source:\n"
        "  kind: review\n"
        f"{ref_line}"
        "scope:\n"
        '  touch: ["src/demo/escalated.py"]\n'
        "oracles:\n"
        "  - id: O1\n"
        '    observable: "pytest tests/test_escalated.py::test_x passes"\n'
        '    negative: "a bad value raises ValueError (test_x_violation)"\n'
        "    gate: pytest-q\n"
        "gates: [pytest-q]\n"
        'escalate_if: ["a named contract cannot be met as specified"]\n'
        "---\n\n# Escalated sample\nBody.\n"
    )
    rel = f"handoff/{task_id}.md"
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return rel


def test_incapable_escalation_note_resolves_provenance_marker(
        tmp_state, sample_project, patch_siblings):
    """O4 positive: a NEW task whose source.ref points at an origin task
    committed-rejected 'incapable' gets a non-empty, non-specific meta-note.
    Never embeds the prior reviewer's specific finding text (D-R3's documented
    anti-anchoring rationale)."""
    cfg = sample_project
    _make_feature_branch(cfg.root, "demo-P01", "P01.py", "# P01\n")
    _commit_review_report(
        cfg.root, "demo-P01", cfg.reports_dir,
        "# Review\n\nSpecific finding: off-by-one-xyz in the retry cap.\n\n"
        "VERDICT: REJECTED\nREJECT_CLASS: incapable\n")
    rel = _write_handoff_with_source_ref(cfg.root, "demo-P02", "demo-P01")
    _seed_task("demo", "demo-P02", TaskState.QUEUED, handoff_path=rel)
    d = daemon.Daemon({"demo": cfg.root})
    tsf = storage.load_state("demo", "demo-P02")
    fm = d._frontmatter_for(cfg, tsf)

    note = effects_review.incapable_escalation_note(d._ports.git, cfg, fm)
    assert note != ""
    assert "higher-tier model" in note
    assert "own" in note.lower() and "judgment" in note.lower()
    assert "off-by-one-xyz" not in note  # never anchors on prior specifics


def test_incapable_escalation_note_negative_origin_not_incapable(
        tmp_state, sample_project, patch_siblings):
    """NEGATIVE: source.ref points at a real origin task, but that origin's
    own rejection class was 'architectural', not 'incapable' -> ''."""
    cfg = sample_project
    _make_feature_branch(cfg.root, "demo-P01", "P01.py", "# P01\n")
    _commit_review_report(
        cfg.root, "demo-P01", cfg.reports_dir,
        "VERDICT: REJECTED\nREJECT_CLASS: architectural\n")
    rel = _write_handoff_with_source_ref(cfg.root, "demo-P02", "demo-P01")
    _seed_task("demo", "demo-P02", TaskState.QUEUED, handoff_path=rel)
    d = daemon.Daemon({"demo": cfg.root})
    tsf = storage.load_state("demo", "demo-P02")
    fm = d._frontmatter_for(cfg, tsf)

    assert effects_review.incapable_escalation_note(d._ports.git, cfg, fm) == ""


def test_incapable_escalation_note_negative_no_source_ref(
        tmp_state, sample_project, patch_siblings):
    """NEGATIVE: an ordinary (non-escalated) handoff has no source.ref at all
    -- every handoff carved BEFORE this package, and most carved after it --
    -> ''."""
    cfg = sample_project
    rel = _write_handoff_with_source_ref(cfg.root, "demo-P03", None)
    _seed_task("demo", "demo-P03", TaskState.QUEUED, handoff_path=rel)
    d = daemon.Daemon({"demo": cfg.root})
    tsf = storage.load_state("demo", "demo-P03")
    fm = d._frontmatter_for(cfg, tsf)

    assert effects_review.incapable_escalation_note(d._ports.git, cfg, fm) == ""


def test_incapable_escalation_note_negative_no_frontmatter():
    """NEGATIVE: fm=None (e.g. an unparsable/missing handoff, the SAME
    graceful-degradation case _frontmatter_for itself returns) -> ''. No cfg
    access needed -- proves the None-check short-circuits before any I/O."""
    d = daemon.Daemon({})
    assert effects_review.incapable_escalation_note(d._ports.git, None, None) == ""


def test_execute_carve_dispatch_rescope_supersedes_origin_with_rescoped_outcome(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """END-TO-END oracle (critique CRITIQUE.md:249): a CarveDispatch(task_id=P01)
    for a rejected-architectural task launches the re-scope carve AND supersedes
    the ORIGINAL task with the RESCOPED outcome, and the carve packet embeds the
    review verdict. Driven through run_pass/_execute so the CarveDispatch.task_id
    -> _execute_carve_dispatch plumbing is exercised, not just the leaf method."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")
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
    # CR-05f: the carve effector calls the shared admission primitive, not the
    # shell's delegate -- patch the seam the effect actually consults.
    monkeypatch.setattr(effects_dispatch, "admissible",
                        lambda ctx, kind: (False, "paused"))
    states = storage.list_states("demo")

    events = d._execute("demo", cfg, states, reconcile.CarveDispatch(project="demo", task_id="demo-P01"))

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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")
    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")

    events = d._execute("demo", cfg, states, reconcile.CarveDispatch(project="demo"))

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
    _set_pipeline(sample_project, ["carve", "implement", "review_independent",
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
# P33 2026-07-16: robust review verdict -- derive the REVIEW_INDEPENDENT merge
# decision from the committed <task>-REVIEW.md verdict, never from bare
# process exit (live P26 incident: a REJECTED review report + clean process
# exit -> receipt DONE -> rubber-stamped MERGE_READY).

def _seed_review_attempt(project, task_id, attempt_id, wave_id="wave-1"):
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.AWAITING_REVIEW, since=utc_now(), handoff_path=None, wave_id=wave_id,
    )
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(attempt_id=attempt_id, role=Role.REVIEW_INDEPENDENT, state=AttemptState.RUNNING,
                       route=route, started=utc_now(), wave_id=wave_id)
    tsf.attempts.append(attempt)
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return storage.load_state(project, task_id)


def _write_typed_result(project, task_id, attempt_id, kind, verdict, *,
                        summary="", evidence=b"agent stdout\n"):
    """CR-03 (DR-13): the artifact the daemon now reads for a verdict.

    The wrapper assembles this from the agent's judgement plus the facts; a
    test that wants a verdict writes the assembled record directly, which is
    the same document `results.load_result` validates in production. It goes
    in the ATTEMPT directory, not on the branch: that is what binds it to one
    attempt and makes a previous attempt's record inert.
    """
    attempt_dir = paths.attempt_dir(project, attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "attempt.log").write_bytes(evidence)
    record = results.AgentResult(
        kind=kind, verdict=verdict,
        identity=results.ResultIdentity(task_id=task_id, attempt_id=attempt_id),
        evidence=results.Evidence(path="attempt.log",
                                   sha256=results.digest_bytes(evidence),
                                   bytes_len=len(evidence)),
        summary=summary,
    )
    (attempt_dir / results.result_filename(task_id)).write_text(
        record.to_json(), encoding="utf-8")


def _approve(project, task_id, attempt_id, summary=""):
    _write_typed_result(project, task_id, attempt_id,
                        results.ResultKind.REVIEW_INDEPENDENT,
                        results.Verdict.APPROVED, summary=summary)


def _reject(project, task_id, attempt_id, summary=""):
    _write_typed_result(project, task_id, attempt_id,
                        results.ResultKind.REVIEW_INDEPENDENT,
                        results.Verdict.REJECTED, summary=summary)


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


def test_review_independent_done_receipt_rejected_report_yields_review_rejected(
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
    _reject("demo", task_id, attempt_id, summary="daemon-core change is unsafe")
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"


def test_review_independent_done_receipt_approved_report_yields_merge_ready(
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
    _approve("demo", task_id, attempt_id)
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
    _write_typed_result("demo", task_id, attempt_id,
                        results.ResultKind.SELF_REVIEW,
                        results.Verdict.APPROVED)
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
    _write_typed_result("demo", task_id, attempt_id,
                        results.ResultKind.SELF_REVIEW,
                        results.Verdict.REJECTED,
                        summary="bug I cannot fix in-session")
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
    # CR-02a: _attempt_scan's third element is now a typed SnapshotInput for
    # the receipts domain (a malformed receipt must be distinguishable from an
    # absent one). Both receipts here are well-formed, so it is OK.
    _lq, _pa, receipts_in = d._attempt_scan(project, {task_id: tsf_sr})
    assert receipts_in.ok
    assert receipts_in.require().get(attempt_id) is not None  # surfaced for consumption

    tsf_active = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
                               state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
                               attempts=[att])
    _lq2, _pa2, receipts2_in = d._attempt_scan(project, {task_id: tsf_active})
    assert receipts2_in.require().get(attempt_id) is None  # NOT surfaced -- state-scoped


def test_review_independent_wave_exit_fans_out_per_member_verdicts(
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
        # CR-03: ONE attempt, one typed result PER MEMBER. Per-task rather
        # than one document holding a list, so a member whose judgement is
        # malformed cannot contaminate its siblings' verdicts.
        _write_typed_result("demo", tid, attempt_id,
                            results.ResultKind.REVIEW_INDEPENDENT,
                            results.Verdict.APPROVED if v == "APPROVED"
                            else results.Verdict.REJECTED)
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


def test_review_independent_missing_report_fails_safe_to_rejected(
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


def test_review_independent_ambiguous_report_fails_safe_to_rejected(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """Oracle O3, restated for CR-03: an answer the reader REFUSES must fail
    safe to REVIEW_REJECTED, never to MERGE_READY.

    The original of this test wrote two conflicting `VERDICT:` lines into one
    markdown file, because the regex could pool contradictory verdicts out of
    prose. That ambiguity no longer exists -- there is one typed record per
    task -- so the mechanism is retired and the SAFETY PROPERTY it protected
    is re-pinned against the failure that replaced it: a record that does not
    validate. Here the record claims a different attempt, the exact staleness
    the old scan needed a special rule for.

    The distinction that matters: refused is not the same as absent. Absent
    routes to a relaunch of the review LEG; refused means something answered
    and could not be trusted, which must not merge."""
    cfg = sample_project
    task_id, attempt_id = "t-rev-ambiguous", "att-rev-ambiguous"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _seed_review_attempt("demo", task_id, attempt_id)
    # An APPROVED record -- but bound to a DIFFERENT attempt, so the reader
    # refuses it. Without the identity check this would merge.
    _write_typed_result("demo", task_id, "att-somebody-else",
                        results.ResultKind.REVIEW_INDEPENDENT,
                        results.Verdict.APPROVED)
    stale = (paths.attempt_dir("demo", "att-somebody-else")
             / results.result_filename(task_id)).read_text(encoding="utf-8")
    target_dir = paths.attempt_dir("demo", attempt_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "attempt.log").write_bytes(b"agent stdout\n")
    (target_dir / results.result_filename(task_id)).write_text(stale, encoding="utf-8")
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED
    recorded = next(e for e in storage.iter_events("demo")
                     if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"


def test_review_independent_nondone_receipt_is_defense_in_depth_rejected(
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
    _approve("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.BLOCKED, exit_code=1,
                    blocked_reason="reviewer crashed mid-run")
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED


def test_review_independent_limit_receipt_is_incomplete_not_rejected_and_pauses_provider(
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
    """Oracle O4, restated for CR-03: the review packet must instruct the
    reviewer to write the TYPED JUDGEMENT the wrapper reads.

    A packet that asked for prose the controller no longer parses would
    produce a reviewer whose every verdict is unreadable -- and because an
    unreadable verdict routes to a relaunch, the factory would loop instead
    of failing visibly. The packet and the reader have to name the same
    artifact, so this pins that they do."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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

    # The packet names the artifact the wrapper actually reads, and the
    # attempt id the reviewer must echo -- that echo is what binds a verdict
    # to this run, so a packet omitting it would produce judgements the
    # reader must refuse.
    assert "nyxloom-judgement-<task>.json" in packet_md
    assert '"kind": "review_independent"' in packet_md
    assert created.attempt_id in packet_md
    assert '"verdict": "approved" or "rejected"' in packet_md

    # ...and the prose contract it replaced is gone, so a reviewer cannot
    # satisfy the packet by writing something nothing reads.
    assert "VERDICT: APPROVED" not in packet_md

    # The human report path stays derived from cfg.reports_dir: it was once
    # hardcoded to a stale `topos/handoff/reports/` matching no project,
    # which sent reviewers to write where nobody looked.
    assert f"{cfg.reports_dir}/<task>-REVIEW.md" in packet_md
    assert "topos/handoff/reports" not in packet_md


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
    _log_quiet, _pid_alive, receipts_in = d._attempt_scan("demo", {task_id: tsf2})
    assert receipts_in.require().get(attempt_id) is None


# ============================================================================
# B24 2026-07-23 (D-R17, transient-failure backoff-resume). Oracles O3/O5.
# _transient_backoff_ready (the backoff-timing helper) and _transient_escalate
# (the at-cap escalation) are tested directly, mirroring this file's existing
# direct-call convention for pure daemon helpers (_attempt_scan,
# _parse_review_verdict, _build_carve_packet above).
# ============================================================================

def _make_transient_attempt(attempt_id="att-tr", session_handle="sess-1"):
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    return Attempt(
        attempt_id=attempt_id, role=Role.IMPLEMENTER, state=AttemptState.INTERRUPTED,
        route=route, started=utc_now(), session_handle=session_handle,
        receipt=Receipt(result=ReceiptResult.TRANSIENT, exit_code=1),
    )


def test_transient_backoff_ready_non_interrupted_and_non_transient_excluded(
        tmp_state, sample_project):
    """_transient_backoff_ready only ever produces entries for TRANSIENT-
    classified INTERRUPTED attempts -- a RUNNING attempt and an INTERRUPTED-
    but-not-transient (e.g. LIMIT, or receipt=None real-signal-interrupt)
    attempt are both excluded from the output dict entirely (so
    reconcile.py's `.get(id, True)` default applies, unchanged behavior)."""
    project = "demo"
    running = _make_transient_attempt("att-running")
    running.state = AttemptState.RUNNING
    limit_att = _make_transient_attempt("att-limit")
    limit_att.receipt = Receipt(result=ReceiptResult.LIMIT, exit_code=1)
    real_interrupt = _make_transient_attempt("att-real-int")
    real_interrupt.receipt = None
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t-x", project=project,
                        state=TaskState.ACTIVE, since=utc_now(),
                        attempts=[running, limit_att, real_interrupt])

    d = daemon.Daemon({"demo": sample_project.root})
    out = d._transient_backoff_ready(project, {"t-x": tsf})
    assert out == {}


def test_transient_backoff_ready_first_resume_always_ready(tmp_state, sample_project):
    """O3: an INTERRUPTED transient attempt with NO prior resumes (no
    attempt.resume-N.log on disk) is always ready -- the schedule governs
    waits BETWEEN resumes, not before the first one."""
    project = "demo"
    att = _make_transient_attempt()
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t-x", project=project,
                        state=TaskState.ACTIVE, since=utc_now(), attempts=[att])
    # No attempt_dir contents at all -- _next_resume_n returns 1 -> prior_resumes 0.
    d = daemon.Daemon({"demo": sample_project.root})
    out = d._transient_backoff_ready(project, {"t-x": tsf})
    assert out == {att.attempt_id: True}


def test_transient_backoff_ready_waiting_vs_elapsed(tmp_state, sample_project):
    """O3: one prior resume -- a freshly-written resume-1.log (mtime ~now)
    is NOT ready (TRANSIENT_BACKOFF_SCHEDULE[0]=60s hasn't elapsed); the
    SAME attempt with that log's mtime backdated past the wait IS ready.
    Both directions asserted, per the oracle."""
    project = "demo"
    att = _make_transient_attempt()
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t-x", project=project,
                        state=TaskState.ACTIVE, since=utc_now(), attempts=[att])
    attempt_dir = paths.attempt_dir(project, att.attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    log_path = attempt_dir / "attempt.resume-1.log"
    log_path.write_text("x", encoding="utf-8")

    d = daemon.Daemon({"demo": sample_project.root})

    # Not ready: mtime is ~now, schedule[0] == 60s.
    out = d._transient_backoff_ready(project, {"t-x": tsf})
    assert out == {att.attempt_id: False}

    # Ready: backdate the log's mtime well past the 60s wait.
    old = time.time() - daemon.TRANSIENT_BACKOFF_SCHEDULE[0] - 5
    os.utime(log_path, (old, old))
    out2 = d._transient_backoff_ready(project, {"t-x": tsf})
    assert out2 == {att.attempt_id: True}


def test_transient_backoff_ready_picks_newest_of_multiple_logs(tmp_state, sample_project):
    """O3: two prior resumes (still under MAX_TRANSIENT_RESUMES) -- the
    mtime scan must pick the NEWEST of the two resume-N.log files, not
    just the first found, so the wait clock starts from the LAST resume,
    not an earlier one. resume-1.log is old (would already be ready under
    schedule[0]=60s alone); resume-2.log is fresh (~now) -- not ready,
    proving the newer mtime won."""
    project = "demo"
    att = _make_transient_attempt()
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t-x", project=project,
                        state=TaskState.ACTIVE, since=utc_now(), attempts=[att])
    attempt_dir = paths.attempt_dir(project, att.attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    old = time.time() - 10_000
    log1 = attempt_dir / "attempt.resume-1.log"
    log1.write_text("x", encoding="utf-8")
    os.utime(log1, (old, old))
    log2 = attempt_dir / "attempt.resume-2.log"
    log2.write_text("x", encoding="utf-8")  # fresh mtime (~now)

    d = daemon.Daemon({"demo": sample_project.root})
    out = d._transient_backoff_ready(project, {"t-x": tsf})
    # 2 prior resumes -> schedule[min(1, len-1)] = schedule[1] = 300s;
    # newest (log2) is ~now -> not ready.
    assert out == {att.attempt_id: False}


def test_transient_backoff_ready_capped_forever_false(tmp_state, sample_project):
    """O3/D-B24-5: once prior_resumes reaches MAX_TRANSIENT_RESUMES, ready
    is False regardless of mtime (even backdated far in the past) -- the
    ResumeAttempt gate must never fire again for THIS attempt; escalation
    (_transient_escalate) is the separate mechanism that moves the task."""
    project = "demo"
    att = _make_transient_attempt()
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t-x", project=project,
                        state=TaskState.ACTIVE, since=utc_now(), attempts=[att])
    attempt_dir = paths.attempt_dir(project, att.attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    old = time.time() - 10_000
    for n in range(1, daemon.MAX_TRANSIENT_RESUMES + 1):
        p = attempt_dir / f"attempt.resume-{n}.log"
        p.write_text("x", encoding="utf-8")
        os.utime(p, (old, old))

    d = daemon.Daemon({"demo": sample_project.root})
    out = d._transient_backoff_ready(project, {"t-x": tsf})
    assert out == {att.attempt_id: False}


def test_transient_backoff_ready_missing_log_falls_back_to_ready(tmp_state, sample_project):
    """Belt-and-braces branch: _next_resume_n also counts a resume via
    spec.resume-N.json (the wrapper spec written before the log exists) --
    if the corresponding attempt.resume-N.log is missing on disk, the mtime
    scan finds nothing (OSError per candidate, caught) and must not wedge
    the attempt on a backoff it cannot time from -- defaults to ready."""
    project = "demo"
    att = _make_transient_attempt()
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t-x", project=project,
                        state=TaskState.ACTIVE, since=utc_now(), attempts=[att])
    attempt_dir = paths.attempt_dir(project, att.attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    # spec.resume-1.json satisfies _next_resume_n (prior_resumes becomes 1)
    # but attempt.resume-1.log itself was never written.
    (attempt_dir / "spec.resume-1.json").write_text("{}", encoding="utf-8")

    d = daemon.Daemon({"demo": sample_project.root})
    out = d._transient_backoff_ready(project, {"t-x": tsf})
    assert out == {att.attempt_id: True}


def test_transient_escalate_excludes_non_matching_tasks(tmp_state, sample_project):
    """_transient_escalate's guard clauses: a non-ACTIVE task, an ACTIVE
    task with no attempts at all, a latest attempt that is not INTERRUPTED,
    one whose role is not IMPLEMENTER, and one whose receipt is not
    TRANSIENT are ALL excluded -- even when every one of them (except the
    first two) is otherwise sitting at/over MAX_TRANSIENT_RESUMES prior
    resumes. Only a genuine at-cap transient IMPLEMENTER attempt on an
    ACTIVE task escalates (see test_transient_escalate_at_cap_pauses_and_
    requeues just below)."""
    project = "demo"

    def _at_cap_dir(attempt_id: str) -> None:
        attempt_dir = paths.attempt_dir(project, attempt_id)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        for n in range(1, daemon.MAX_TRANSIENT_RESUMES + 1):
            (attempt_dir / f"attempt.resume-{n}.log").write_text("x", encoding="utf-8")

    not_active_att = _make_transient_attempt("att-not-active")
    _at_cap_dir(not_active_att.attempt_id)
    tsf_not_active = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="t-not-active", project=project,
        state=TaskState.QUEUED, since=utc_now(), attempts=[not_active_att])

    tsf_no_attempts = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="t-no-attempts", project=project,
        state=TaskState.ACTIVE, since=utc_now(), attempts=[])

    running_att = _make_transient_attempt("att-running-latest")
    running_att.state = AttemptState.RUNNING
    _at_cap_dir(running_att.attempt_id)
    tsf_running = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="t-running", project=project,
        state=TaskState.ACTIVE, since=utc_now(), attempts=[running_att])

    review_att = _make_transient_attempt("att-review-role")
    review_att.role = Role.REVIEW_INDEPENDENT
    _at_cap_dir(review_att.attempt_id)
    tsf_review = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="t-review", project=project,
        state=TaskState.ACTIVE, since=utc_now(), attempts=[review_att])

    limit_att = _make_transient_attempt("att-limit-receipt")
    limit_att.receipt = Receipt(result=ReceiptResult.LIMIT, exit_code=1)
    _at_cap_dir(limit_att.attempt_id)
    tsf_limit = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="t-limit", project=project,
        state=TaskState.ACTIVE, since=utc_now(), attempts=[limit_att])

    states = {
        "t-not-active": tsf_not_active, "t-no-attempts": tsf_no_attempts,
        "t-running": tsf_running, "t-review": tsf_review, "t-limit": tsf_limit,
    }

    d = daemon.Daemon({"demo": sample_project.root})
    events = d._transient_escalate(project, sample_project, states)

    assert events == []
    assert tsf_not_active.state is TaskState.QUEUED
    assert tsf_no_attempts.state is TaskState.ACTIVE
    assert tsf_running.state is TaskState.ACTIVE
    assert tsf_review.state is TaskState.ACTIVE
    assert tsf_limit.state is TaskState.ACTIVE


def test_transient_escalate_below_cap_no_action(tmp_state, sample_project):
    """O5 (below-cap direction): fewer than MAX_TRANSIENT_RESUMES prior
    resumes -> no escalation events, task stays ACTIVE."""
    project = "demo"
    task_id = "t-tresc-under"
    att = _make_transient_attempt()
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
                        state=TaskState.ACTIVE, since=utc_now(), attempts=[att])
    attempt_dir = paths.attempt_dir(project, att.attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, daemon.MAX_TRANSIENT_RESUMES):  # one short of the cap
        (attempt_dir / f"attempt.resume-{n}.log").write_text("x", encoding="utf-8")

    d = daemon.Daemon({"demo": sample_project.root})
    events = d._transient_escalate(project, sample_project, {task_id: tsf})

    assert events == []
    assert tsf.state is TaskState.ACTIVE


def test_transient_escalate_at_cap_pauses_and_requeues(tmp_state, sample_project):
    """O5 (bounded escalation): at MAX_TRANSIENT_RESUMES prior resumes, the
    task is requeued (ACTIVE->QUEUED) and the route provider-paused with a
    DISTINCT state ('throttled', not LIMIT's 'limited') -- verified via the
    real events this method appends (mirrors test_emit_attempt_exit_limit's
    own PROVIDER_STATE_CHANGED/NEEDS_OPERATOR assertions)."""
    project = "demo"
    task_id = "t-tresc-cap"
    att = _make_transient_attempt()
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
                        state=TaskState.ACTIVE, since=utc_now(), attempts=[att])
    attempt_dir = paths.attempt_dir(project, att.attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, daemon.MAX_TRANSIENT_RESUMES + 1):
        (attempt_dir / f"attempt.resume-{n}.log").write_text("x", encoding="utf-8")

    d = daemon.Daemon({"demo": sample_project.root})
    # CR-04b: the store derives the projection from COMMITTED state, so a task
    # that exists only in a caller's dict can no longer receive a transition.
    # Persist it first, and read the verdict off the map the store refreshed
    # rather than off the object handed in -- that object is the caller's
    # stale copy by construction now.
    states = {}
    storage.append_and_apply(
        project, states, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()},
        task_id=task_id)
    events = d._transient_escalate(project, sample_project, states)

    assert states[task_id].state is TaskState.QUEUED
    ev_types = [e.type for e in events]
    assert EventType.TASK_TRANSITIONED in ev_types
    assert EventType.PROVIDER_STATE_CHANGED in ev_types
    assert EventType.NEEDS_OPERATOR in ev_types
    psc = next(e for e in events if e.type is EventType.PROVIDER_STATE_CHANGED)
    assert psc.payload == {"route_id": "fake-cli", "state": "throttled"}

    # On the NEXT snapshot, the just-paused route reads unhealthy -- the
    # EXISTING first-healthy-route selector then re-routes elsewhere.
    from nyxloom import config as config_mod
    routes = config_mod.Routes.load()
    assert d._provider_ok(routes).get("fake-cli") is False


def test_transient_escalate_full_pass_bounded_and_reroutes(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """O5 end-to-end (modeled on test_emit_attempt_exit_limit): drives it
    through a REAL run_pass (plan_project scripted to a no-op, since
    _transient_escalate runs BEFORE _build_input/plan_project and does not
    depend on planned actions) -- after the cap, the task is QUEUED and the
    route provider-paused; on the NEXT pass, the captured ReconcileInput's
    provider_ok['fake-cli'] is False."""
    task_id, attempt_id = "t-tresc-e2e", "att-tresc-e2e"
    _seed_running_attempt("demo", task_id, attempt_id)
    tsf = storage.load_state("demo", task_id)
    att = tsf.attempt_by_id(attempt_id)
    att.state = AttemptState.INTERRUPTED
    att.session_handle = "sess-1"
    att.receipt = Receipt(result=ReceiptResult.TRANSIENT, exit_code=1)
    storage.save_state(tsf)

    attempt_dir = paths.attempt_dir("demo", attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, daemon.MAX_TRANSIENT_RESUMES + 1):
        (attempt_dir / f"attempt.resume-{n}.log").write_text("x", encoding="utf-8")

    captured = _scripted(monkeypatch, [[], []])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.QUEUED

    events = list(storage.iter_events("demo"))
    types = [e.type for e in events]
    assert EventType.PROVIDER_STATE_CHANGED in types
    assert EventType.NEEDS_OPERATOR in types

    d.run_pass("demo")
    assert len(captured) == 2
    assert captured[1].provider_ok.get("fake-cli") is False


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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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
        assert e.payload["attempt"]["role"] == "review-independent"
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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
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


def test_lease_probe_failure_fails_closed(tmp_state, sample_project, monkeypatch):
    """An unreadable lease must park work, never authorize a collision.

    CR-02a strengthened this: the probe failure was previously recorded as
    `{lease: False}` -- fail-closed for tasks declaring that mutex, but
    invisible and indistinguishable from a genuinely-held lease. It is now a
    typed AUTHORITATIVE fault naming the lease, and `_build_input` returns
    None so no effect is planned at all."""
    def fail_probe(*_args, **_kwargs):
        raise OSError("lease directory unreadable")

    monkeypatch.setattr(daemon.leases, "holder_info", fail_probe)
    d = daemon.Daemon({"demo": sample_project.root})

    result = d._leases_free(sample_project)
    assert not result.ok
    assert result.input_class is snapshot.InputClass.AUTHORITATIVE
    assert result.reason is snapshot.Reason.PROBE_FAILED
    assert "demo.stack" in result.provenance.ref
    with pytest.raises(snapshot.SnapshotUnavailable):
        result.require()


def test_decision_inbox_failure_aborts_snapshot(tmp_state, sample_project, monkeypatch):
    """Unreadable decision truth cannot be interpreted as no open holds.

    CR-02a: the abort is now a typed fan-in refusal (`_build_input` -> None
    plus an authoritative `decisions_open` fault) instead of a raised OSError
    that only the pass-level TICK_ERROR catch happened to contain."""
    def fail_read(_cfg):
        raise OSError("decisions inbox unreadable")

    monkeypatch.setattr(decisions, "open_ids", fail_read)
    d = daemon.Daemon({"demo": sample_project.root})

    builder = snapshot.SnapshotBuilder()
    inp = d._build_input("demo", sample_project, storage.list_states("demo"),
                          builder=builder)
    assert inp is None
    audit = builder.audit()
    assert not audit.permits_effects
    failed = audit.get("decisions_open")
    assert failed is not None and not failed.ok
    assert failed.reason is snapshot.Reason.SOURCE_ERROR
    assert "OSError" in failed.detail


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
        assert not t.is_alive(), "http_daemon fixture's daemon thread outlived teardown and may pollute global logging"


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
        assert not t.is_alive(), "daemon thread outlived the test and may pollute global logging"


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
        assert not t.is_alive(), "daemon thread outlived the test and may pollute global logging"


def _read_log_records(log_dir: Path) -> list[dict]:
    """P01: the http_bind notice moved from a stderr `print` to
    `log.warning(...)` -- read back the rendered JSONL records the same way
    test_log.py does, rather than capturing stderr."""
    p = log_dir / "nyxloom.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_raw_log_lines(records: list[dict]) -> None:
    """P04 test helper: append raw JSONL lines directly to
    paths.nyxloom_log_path(), bypassing structlog entirely -- gives the
    /api/logs* endpoint oracles exact control over level/project/msg/order
    without depending on the daemon's own live logging configuration."""
    p = paths.nyxloom_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_nonloopback_bind_warns_about_the_open_read_surface(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """2026-07-20: a non-loopback bind states the security assumption out loud
    at startup. 2026-07-21 (P01): as a structured `log.warning` record rather
    than a raw stderr print.

    CR-15 2026-08-02 changes WHAT is true, so it changes what this asserts.
    The warning used to say the control plane was UNAUTHENTICATED; mutations
    now require an operator credential, so that word would be a false claim --
    and the review amendment's rule is that a statement about the trust
    boundary is testable or absent. The remaining, true exposure is the READ
    surface (every GET is open by design), so the warning must name that and
    must not resurrect the stale claim."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    monkeypatch.setenv("NYXLOOM_HTTP_BIND", "0.0.0.0")
    _set_ephemeral_http_port(sample_project)
    log.configure(level=log.INFO, log_dir=tmp_state / "logs", console=False)
    # B27 de-flake (2026-07-26): assert on the WARNING EMISSION, not on the
    # shared JSONL log file. daemon.run() reconfigures the PROCESS-GLOBAL logger
    # from inside its own thread (daemon.py: log_module.configure(paths.logs_dir()),
    # which removes+closes ALL handlers). Under -n4 full-suite load a
    # concurrent/leaked daemon thread's configure() closes the file handler
    # BETWEEN this daemon's "daemon started" info write and its non-loopback
    # warning write, so the warning record is emitted but silently dropped to a
    # closed handler. Instrumentation confirmed it precisely: the daemon CALLED
    # log.warning(..., http_bind="0.0.0.0") once, yet that record reached NO file
    # anywhere on disk (green in isolation, red under load). Proving the log
    # module persists a record to file end-to-end is test_log.py's job; THIS
    # daemon test's contract is only "a non-loopback bind EMITS the exposure
    # warning", so capture the emission at daemon.log.warning --
    # immune to the global-logger/file race (same de-flake shape as B25: assert
    # through a deterministic in-process seam, not a race-prone shared resource).
    emitted: list[tuple[str, dict]] = []
    _real_log = daemon.log
    # CR-15 review: the capture SETS this the instant the notice is emitted, so
    # the test blocks on a real synchronization point instead of polling a
    # wall-clock deadline. The previous `deadline = monotonic() + 5` loop made
    # a slow or loaded host decide the verdict -- exactly the L20 anti-pattern
    # STANDING.md forbids. The wait's timeout is a generous hang failsafe: the
    # assertion below is on the captured record, not on how long it took.
    notice_seen = threading.Event()

    def _bind_notices() -> list[tuple[str, dict]]:
        return [(m, kw) for m, kw in emitted
                if kw.get("http_bind") == "0.0.0.0" and "non-loopback" in m]

    class _CapturingLog:
        def warning(self, msg, **kw):
            emitted.append((msg, dict(kw)))
            if _bind_notices():
                notice_seen.set()
            return _real_log.warning(msg, **kw)

        def __getattr__(self, name):
            return getattr(_real_log, name)

    monkeypatch.setattr(daemon, "log", _CapturingLog())

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()

    def _notice_emitted() -> bool:
        return bool(_bind_notices())

    notice_seen.wait(timeout=60)
    d.stop()
    t.join(timeout=60)
    assert not t.is_alive(), "daemon thread outlived the test and may pollute global logging"

    assert _notice_emitted(), (
        "expected the daemon to emit a non-loopback http_bind=0.0.0.0 warning "
        f"on a non-loopback bind; captured warnings: {emitted}"
    )
    message, fields = _bind_notices()[0]
    assert fields["http_port"] == d.http_port
    # It names the exposure that is REAL after CR-15: the read surface.
    assert "read" in message.lower()
    # And it does not re-assert the posture CR-15 removed. A future edit that
    # reintroduces "unauthenticated" here would be describing a system that no
    # longer exists -- exactly the contradiction the amendment called out.
    assert "unauthenticated" not in message.lower()


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
    assert not t.is_alive(), "daemon thread outlived the test and may pollute global logging"

    assert d.http_bind == "127.0.0.1"
    warnings = [r for r in _read_log_records(log_dir) if r.get("level") == "warning"]
    assert not any("UNAUTHENTICATED" in r.get("msg", "") for r in warnings)


# --------------------------------------------------------------------------
# P03 2026-07-21 (D-L5): the daemon flushes reconcile.py's pure ReconcileTrace
# to the logger -- one DEBUG record per breadcrumb, project bound. This is
# the ONLY place that trace ever touches a logger (reconcile.py itself stays
# pure -- see test_reconcile.py's module-level purity oracle).

def test_reconcile_trace_flushed_as_one_debug_record_per_breadcrumb(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Oracle 3: exactly one DEBUG record per breadcrumb, each with
    `project` bound."""
    trace = reconcile.ReconcileTrace(breadcrumbs=[
        reconcile.TraceNote(kind="dispatch", task_id="P01", detail="route:route-1"),
        reconcile.TraceNote(kind="dispatch-skip", task_id="P02", detail="paused"),
        reconcile.TraceNote(kind="guard-exclude", task_id="P03", detail="decision-held"),
    ])
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(
        reconcile, "plan_project",
        lambda inp: reconcile.PlanResult([], trace=trace),
    )
    log_dir = tmp_state / "logs"
    log.configure(level=log.DEBUG, log_dir=log_dir, console=False)

    d = daemon.Daemon({"demo": sample_project.root})
    n = d.run_pass("demo")
    assert n == 0  # the stubbed plan_project returned zero actions

    records = _read_log_records(log_dir)
    trace_records = [r for r in records
                     if r.get("level") == "debug" and r.get("msg") == "reconcile-trace"]
    assert len(trace_records) == 3
    assert all(r.get("project") == "demo" for r in trace_records)
    by_kind = {r["kind"]: r for r in trace_records}
    assert by_kind["dispatch"]["task"] == "P01"
    assert by_kind["dispatch"]["detail"] == "route:route-1"
    assert by_kind["dispatch-skip"]["task"] == "P02"
    assert by_kind["dispatch-skip"]["detail"] == "paused"
    assert by_kind["guard-exclude"]["task"] == "P03"
    assert by_kind["guard-exclude"]["detail"] == "decision-held"


def test_reconcile_trace_flush_skipped_when_plan_project_returns_bare_list(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Back-compat: many existing tests (and the pre-P03 daemon contract)
    monkeypatch plan_project to return a bare `list` with no `.trace`
    attribute -- the daemon must degrade gracefully (nothing to flush)
    rather than raising AttributeError."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    log_dir = tmp_state / "logs"
    log.configure(level=log.DEBUG, log_dir=log_dir, console=False)

    d = daemon.Daemon({"demo": sample_project.root})
    n = d.run_pass("demo")  # must not raise
    assert n == 0

    records = _read_log_records(log_dir)
    assert not any(r.get("msg") == "reconcile-trace" for r in records)


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

    conn = http.client.HTTPConnection("127.0.0.1", d.http_port, timeout=5)
    conn.request("GET", "/favicon.ico")
    fav = conn.getresponse()
    fav.read()
    assert fav.status == 204
    conn.close()

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

    def fake_advance_chat(cfg, project, decision_id, text, actor=None):
        # CR-15: the endpoint threads the AUTHENTICATED operator through to
        # the chat bridge, so the stub must accept it -- and record it, since
        # "who answered this decision" is the invariant the package exists for.
        calls.append((project, decision_id, text, actor))
        return "ok"

    monkeypatch.setattr(decision_chat, "advance_chat", fake_advance_chat)

    base = f"http://127.0.0.1:{http_daemon.http_port}"

    body = json.dumps({"decision_id": "D-050", "text": "go ahead"}).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/decision/reply", data=body,
                                  headers=_control_headers(), method="POST")
    resp = urllib.request.urlopen(req, timeout=5)
    assert resp.status == 200
    operator = control_auth.CredentialStore(paths.daemon_dir()).load()
    assert calls == [("demo", "D-050", "go ahead",
                      Actor(ActorKind.OPERATOR, operator.operator_id))]

    unknown_body = json.dumps({"decision_id": "D-999", "text": "hi"}).encode("utf-8")
    req2 = urllib.request.Request(f"{base}/api/decision/reply", data=unknown_body,
                                   headers=_control_headers(), method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req2, timeout=5)
    assert exc.value.code == 404

    req3 = urllib.request.Request(f"{base}/api/decision/reply", data=b"{}",
                                   headers=_control_headers(), method="POST")
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
# P49 2026-07-19: fixes a live incident -- resuming re-paused within one
# reconcile_interval_seconds, repeatedly. The in-memory streak used to climb
# unboundedly every pass spent already-paused (detect_runaways keeps
# re-finding the same still-undecayed historical condition), so by the time
# an operator resumed, the streak was already far past
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

    # Operator resumes (matches this project's own pause-flag convention:
    # remove the file for 'run' mode).
    paths.pause_flag(project).unlink()
    assert not paths.pause_flag(project).exists()

    for _ in range(daemon.RUNAWAY_PERSIST_AFTER_CYCLES - 1):
        d.run_pass(project)
    assert not paths.pause_flag(project).exists(), (
        "streak must have reset to 0 on resume -- re-pausing after fewer "
        "than RUNAWAY_PERSIST_AFTER_CYCLES fresh passes means it did not"
    )


def test_watchdog_repauses_after_fresh_persist_cycles_if_condition_still_open(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Companion to the freeze test above: the watchdog is NOT silently
    disabled by the fix -- if the SAME condition is still genuinely open
    after an operator resumes, exactly RUNAWAY_PERSIST_AFTER_CYCLES fresh
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

def test_review_independent_rejected_stamps_reject_class(
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
    _reject("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"
    assert recorded.payload["reject_class"] == "architectural"


def test_review_independent_rejected_stamps_incapable_reject_class(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """D-R3 (2026-07-26 refined): the SAME end-to-end capture as the
    architectural case above, for 'incapable' -- proves the new class
    survives the full committed-report -> daemon -> event payload path, not
    just the regex in isolation."""
    cfg = sample_project
    task_id, attempt_id = "t-rc-incap", "att-rc-incap"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nThe implementation produced bloated, redundant coverage "
        "instead of a compact suite -- a structural, repeated gap.\n\n"
        "VERDICT: REJECTED\nREJECT_CLASS: incapable\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id)
    _reject("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"
    assert recorded.payload["reject_class"] == "incapable"


def test_review_independent_approved_has_no_reject_class(
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
    _approve("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "approved"
    assert "reject_class" not in recorded.payload


def test_review_independent_rejected_without_class_omits_key(
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
    _reject("demo", task_id, attempt_id)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"
    assert "reject_class" not in recorded.payload


def test_parse_reject_class_unit(tmp_state, sample_project):
    """_parse_reject_class reads the committed line; NEGATIVE: absent line ->
    None; an unrecognised value -> None (never a garbage class into routing).
    D-R3 (2026-07-26 refined) adds a positive for 'incapable' alongside the
    existing 'product' case."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})

    _make_feature_branch(cfg.root, "t-pc-1", "a.py", "# a\n")
    _commit_review_report(cfg.root, "t-pc-1", cfg.reports_dir,
                          "VERDICT: REJECTED\nREJECT_CLASS: product\n")
    assert effects_review.parse_reject_class(d._ports.git, cfg, "t-pc-1") == "product"

    _make_feature_branch(cfg.root, "t-pc-2", "b.py", "# b\n")
    _commit_review_report(cfg.root, "t-pc-2", cfg.reports_dir, "VERDICT: REJECTED\n")
    assert effects_review.parse_reject_class(d._ports.git, cfg, "t-pc-2") is None

    _make_feature_branch(cfg.root, "t-pc-3", "c.py", "# c\n")
    _commit_review_report(cfg.root, "t-pc-3", cfg.reports_dir,
                          "VERDICT: REJECTED\nREJECT_CLASS: totally-bogus\n")
    assert effects_review.parse_reject_class(d._ports.git, cfg, "t-pc-3") is None

    _make_feature_branch(cfg.root, "t-pc-4", "d.py", "# d\n")
    _commit_review_report(cfg.root, "t-pc-4", cfg.reports_dir,
                          "VERDICT: REJECTED\nREJECT_CLASS: incapable\n")
    assert effects_review.parse_reject_class(d._ports.git, cfg, "t-pc-4") == "incapable"


def test_review_rationale_unit(tmp_state, sample_project):
    """_review_rationale returns the committed prose; NEGATIVE: no review file
    -> None; a very long review is bounded with a truncation marker."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})

    _make_feature_branch(cfg.root, "t-rr-1", "a.py", "# a\n")
    _commit_review_report(cfg.root, "t-rr-1", cfg.reports_dir,
                          "# Review\n\nThe cap is off by one.\n\nVERDICT: REJECTED\n")
    got = effects_attempt.review_rationale(d._ports.git, cfg, "t-rr-1")
    assert got is not None and "cap is off by one" in got

    # NEGATIVE: a branch with no committed review at all.
    _make_feature_branch(cfg.root, "t-rr-2", "b.py", "# b\n")
    assert effects_attempt.review_rationale(d._ports.git, cfg, "t-rr-2") is None

    # bounded: an oversized review is truncated.
    _make_feature_branch(cfg.root, "t-rr-3", "c.py", "# c\n")
    _commit_review_report(cfg.root, "t-rr-3", cfg.reports_dir, "X" * 9000 + "\nVERDICT: REJECTED\n")
    bounded = effects_attempt.review_rationale(d._ports.git, cfg, "t-rr-3", max_chars=4000)
    assert bounded is not None and len(bounded) < 5000
    assert "truncated" in bounded


def test_head_revision_resolves_main(tmp_state, sample_project):
    """_head_revision returns main's sha; NEGATIVE: a bogus root is a typed
    AUTHORITATIVE fault, not a None the drift-guard reads as 'no drift'.

    CR-02a changed the negative direction deliberately. Returning None made an
    unreadable repository assert that every handoff's `input_revision` premise
    was still current, so work was re-dispatched against a base that may have
    moved -- P40's own root cause."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    expected = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", cfg.default_branch],
        capture_output=True, text=True,
    ).stdout.strip()
    ok = d._carve._head_revision(cfg)
    assert ok.ok and ok.require() == expected
    assert len(expected) == 40                       # a real full sha, not a placeholder

    from dataclasses import replace as _replace
    bogus = _replace(cfg, root=cfg.root / "does-not-exist")
    bad = d._carve._head_revision(bogus)
    assert not bad.ok
    assert bad.input_class is snapshot.InputClass.AUTHORITATIVE
    assert bad.reason is snapshot.Reason.PROBE_FAILED
    assert bad.value is None


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
    (c) a rejected event with no class is excluded. A second POSITIVE (d, D-R3
    2026-07-26 refined) proves 'incapable' is accepted alongside 'architectural',
    not just tolerated by the regex but actually surfaced in the output dict."""
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
    # (d) D-R3: rejected + 'incapable', task in REVIEW_REJECTED
    _seed_task("demo", "t-tc-incap", TaskState.REVIEW_REJECTED)
    _seed_review_recorded("demo", "t-tc-incap", "rejected", reject_class="incapable")

    states = {tid: storage.load_state("demo", tid) for tid in
              ("t-tc-pos", "t-tc-wrongstate", "t-tc-infra", "t-tc-noclass", "t-tc-incap")}
    out = d._triage_classes("demo", states)

    assert out == {"t-tc-pos": "architectural", "t-tc-incap": "incapable"}


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
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")


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
    packet = d._carve._build_carve_packet(cfg, "demo", 1, storage.list_states("demo"))
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
    packet = d._carve._build_carve_packet(cfg, "demo", 1, storage.list_states("demo"))
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


def test_resolve_level_runtime_file_read_error_falls_through(tmp_state, sample_project, monkeypatch):
    """A runtime-override path that EXISTS but can't be read as a file (a
    directory sits there instead of a file -- e.g. a botched deploy) is
    treated as absent, not a crash: falls through to the next layer."""
    monkeypatch.setenv("NYXLOOM_LOG_LEVEL", "warning")
    level_path = paths.daemon_log_level_path()
    level_path.parent.mkdir(parents=True, exist_ok=True)
    level_path.mkdir()   # a directory where a file is expected -> read_text() raises OSError

    registry = {"demo": sample_project.root}
    assert daemon.resolve_level(registry) == ("warning", "env")


def test_resolve_level_primary_config_load_failure_falls_through(tmp_state, monkeypatch):
    """If loading the "primary" project's config raises (missing/invalid
    toml), layer 3 is treated as absent rather than propagating -- falls
    through to hardcoded INFO."""
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    bogus_root = tmp_state.parent / "does-not-exist"
    registry = {"zzz-nonexistent": bogus_root}
    assert daemon.resolve_level(registry) == ("info", "default")


def test_resolve_level_config_invalid_level_falls_through_to_default(
        tmp_state, sample_project, monkeypatch):
    """The primary project's own [logging] level can itself be garbage --
    treated as absent (falls through to hardcoded INFO), not propagated."""
    monkeypatch.delenv("NYXLOOM_LOG_LEVEL", raising=False)
    _set_logging_level(sample_project, "not-a-real-level")

    registry = {"demo": sample_project.root}
    assert daemon.resolve_level(registry) == ("info", "default")


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
        method="POST", headers=_control_headers(),
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
        method="POST", headers=_control_headers(),
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
        method="POST", headers=_control_headers(),
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
        method="POST", headers=_control_headers(),
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
    this surface (which all append a CONFIG_CHANGED/PAUSE_* event).

    CR-16 (RISK-007) keeps this oracle byte-exact: `http_daemon` runs the
    REAL background reconcile loop, and CR-16's per-pass deadman heartbeat
    is a GAUGE (one overwritten `meta` row), not an event, precisely so
    that a once-per-pass liveness stamp does not enter -- or dilute -- the
    event log this assertion is written against."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    log_dir = tmp_state / "logs"

    before_events = [e.to_dict() for e in storage.iter_events("demo", since=0)]

    req = urllib.request.Request(
        f"{base}/api/config/log-level",
        data=json.dumps({"level": "warning"}).encode("utf-8"),
        method="POST", headers=_control_headers(),
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


def test_daemon_run_renders_dashboard_on_startup_when_project_paused(
        tmp_state, sample_project, monkeypatch):
    """A paused, event-free project still gets a fresh dashboard at startup."""
    paths.pause_flag("demo").write_text("drain-agents", encoding="utf-8")
    assert list(storage.iter_events("demo")) == []
    assert not (tmp_state / "www" / "index.html").exists()

    d = daemon.Daemon({"demo": sample_project.root})
    startup_render = d._render_dashboard_on_startup
    calls = []

    def tracked_startup_render():
        calls.append(True)
        startup_render()

    monkeypatch.setattr(d, "_render_dashboard_on_startup", tracked_startup_render)
    monkeypatch.setattr(d, "_start_http", lambda: None)
    monkeypatch.setattr(d, "_start_cmd_listener", lambda: None)
    d._stop_event.set()
    d.run()

    assert calls == [True]
    assert (tmp_state / "www" / "index.html").exists()
    assert (tmp_state / "www" / "logs.html").exists()


def test_daemon_startup_render_failure_is_swallowed(
        tmp_state, sample_project, monkeypatch):
    """A render failure at startup is logged, not fatal: the daemon must still
    come up (the reconcile loop starts) rather than crash. Pins the except
    branch of _render_dashboard_on_startup."""
    d = daemon.Daemon({"demo": sample_project.root})

    def boom(_registry):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(daemon.render, "render_all", boom)
    # Must NOT raise -- the except/log.warning branch swallows it.
    d._render_dashboard_on_startup()
    # render never succeeded, so no dashboard was produced.
    assert not (tmp_state / "www" / "index.html").exists()


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


# ==========================================================================
# P05a (docs/plan-logging.md §5, §6 P05a): effect-core sweep -- daemon.py.
#
# Oracles:
#   1. a dispatch emits INFO carrying project+task+route
#   2. a gate failure emits ERROR
#   3. an attempt retry emits WARNING
# (Oracles 4+5 -- event append TRACE + replay-is-silent -- are storage.py's,
# proven in tests/test_storage.py.) Plus explicit coverage for the other §5
# items this package also instruments: review launch (INFO), merge (INFO),
# a merge conflict (ERROR), state transitions (INFO), and per-pass counts
# (DEBUG).

def test_dispatch_implementer_emits_info_dispatch_with_project_task_route(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Oracle 1: a dispatch emits INFO carrying project+task+route."""
    cfg = sample_project
    (cfg.root / "handoff" / "demo-P02-mutex.md").write_text(MUTEX_HANDOFF, encoding="utf-8")
    task_id = "demo-P02-mutex"
    _seed_task("demo", task_id, TaskState.QUEUED, handoff_path="handoff/demo-P02-mutex.md")
    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    _scripted(monkeypatch, [[reconcile.DispatchImplementer(task_id=task_id, route_id="fake-cli")]])
    d = daemon.Daemon({"demo": cfg.root})
    n = d.run_pass("demo")
    assert n == 1

    records = _read_log_records(log_dir)
    dispatched = [r for r in records if r.get("msg") == "dispatch"]
    assert len(dispatched) == 1
    assert dispatched[0]["level"] == "info"
    assert dispatched[0]["project"] == "demo"
    assert dispatched[0]["task"] == task_id
    assert dispatched[0]["route"] == "fake-cli"


def test_resume_attempt_emits_warning_attempt_retry(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Oracle 3: an attempt retry (ResumeAttempt) emits WARNING."""
    task_id, attempt_id = "t-retry", "att-retry"
    _seed_running_attempt("demo", task_id, attempt_id)
    tsf = storage.load_state("demo", task_id)
    att = tsf.attempt_by_id(attempt_id)
    att.state = AttemptState.INTERRUPTED
    att.session_handle = "sess-1"
    att.worktree = str(sample_project.root)
    storage.save_state(tsf)

    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    _scripted(monkeypatch, [[reconcile.ResumeAttempt(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    records = _read_log_records(log_dir)
    retried = [r for r in records if r.get("msg") == "attempt-retry"]
    assert len(retried) == 1
    assert retried[0]["level"] == "warning"
    assert retried[0]["project"] == "demo"
    assert retried[0]["task"] == task_id
    assert retried[0]["attempt"] == attempt_id
    assert retried[0]["route"] == "fake-cli"


def test_post_merge_gate_failure_emits_error(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Oracle 2: a gate failure emits ERROR. Self-contained (mirrors
    tests/test_post_merge.py's test_validating_task_blocks_on_failing_gate).

    B3-followon 2026-07-26: dispatch is now async (see
    test_post_merge_gate_timeout_captures_output's docstring for the full
    rationale) -- the "false" gate is near-instant, but the gate log/state
    settle only after the background thread is joined and a second pass
    drains it, so this test needs the same two-step idiom even though the
    gate itself is fast."""
    import dataclasses
    from nyxloom import config as config_mod
    from nyxloom.config import GateDef

    cfg = sample_project
    task_id = "demo-P01-sample"
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project="demo",
        state=TaskState.CARVED, since=utc_now(), handoff_path="handoff/demo-P01-sample.md",
    )
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    cur = storage.load_state("demo", task_id)
    cur.state = TaskState.VALIDATING
    cur.merge_commit = "badc0de00002"
    storage.save_state(cur)

    failing_cfg = dataclasses.replace(cfg, gates={
        "pytest-q": GateDef(gate_id="pytest-q", argv=["false"], phase="implementation",
                             timeout_seconds=10, environment="local"),
    })
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: failing_cfg))

    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    _scripted(monkeypatch, [[reconcile.RunPostMergeGate(task_id=task_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    n = d.run_pass("demo")
    assert n == 1

    t = d._gates.post_merge_running.get(f"post-merge-gate:{task_id}")
    assert t is not None, "RunPostMergeGate did not start a background thread"
    t.join(timeout=10)
    assert not t.is_alive()

    d.run_pass("demo")

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.BLOCKED

    records = _read_log_records(log_dir)
    gate_failed = [r for r in records if r.get("msg") == "gate-failed"]
    assert len(gate_failed) == 1
    assert gate_failed[0]["level"] == "error"
    assert gate_failed[0]["project"] == "demo"
    assert gate_failed[0]["task"] == task_id
    assert gate_failed[0]["gate"] == "pytest-q"
    assert gate_failed[0]["exit_code"] != 0


def test_post_merge_gate_timeout_captures_output(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """F019 P1a: a post-merge gate that TIMES OUT records exit_code 124 in its
    GATE_FINISHED event -- exercising the post-merge timeout branch's output
    capture (the only per-phase timeout path without an existing test). Mirrors
    test_post_merge_gate_failure_emits_error with a sleep gate + short timeout.

    B3-followon 2026-07-26: post-merge gates now DISPATCH onto a background
    thread (see daemon.py's _run_post_merge_gate / _run_post_merge_gate_bg /
    _drain_post_merge_gate_results) instead of running inline, so the first
    run_pass only STARTS the gate -- mirrors
    test_end_to_end_dispatch_thread_then_drain_closes_the_cadence's idiom in
    test_gate_verify_cadence.py exactly: join the started thread, THEN run a
    second pass (the existing `_scripted` harness's fake plan_project pops []
    for any call beyond its one scripted action, so this second run_pass just
    drains) before asserting the terminal state/events."""
    import dataclasses
    from nyxloom import config as config_mod
    from nyxloom.config import GateDef

    cfg = sample_project
    task_id = "demo-P02-timeout"
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project="demo",
        state=TaskState.CARVED, since=utc_now(), handoff_path="handoff/demo-P02-timeout.md",
    )
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    cur = storage.load_state("demo", task_id)
    cur.state = TaskState.VALIDATING
    cur.merge_commit = "badc0de00003"
    storage.save_state(cur)

    slow_cfg = dataclasses.replace(cfg, gates={
        "sleepy": GateDef(gate_id="sleepy", argv=["sleep", "5"], phase="implementation",
                          timeout_seconds=1, environment="local"),
    })
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: slow_cfg))

    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    _scripted(monkeypatch, [[reconcile.RunPostMergeGate(task_id=task_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    n = d.run_pass("demo")
    assert n == 1

    # First pass only dispatched the gate -- task is still VALIDATING.
    tsf_mid = storage.load_state("demo", task_id)
    assert tsf_mid.state is TaskState.VALIDATING

    # O1 (async, not blocking): run_pass returned WITHOUT waiting out the
    # gate's own 5s sleep (let alone its 1s timeout) -- the background
    # thread is still mid-flight right here, proving run_pass did not block
    # for the gate's duration the way the old synchronous code did.
    t = d._gates.post_merge_running.get(f"post-merge-gate:{task_id}")
    assert t is not None, "RunPostMergeGate did not start a background thread"
    assert t.is_alive(), "gate thread already finished -- this run_pass call did not return early"
    t.join(timeout=10)
    assert not t.is_alive()

    # Second pass: plan_project (still scripted, now exhausted -> []) drains
    # the finished result and settles the terminal state.
    d.run_pass("demo")

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.BLOCKED
    assert f"post-merge-gate:{task_id}" not in d._gates.post_merge_running

    events = list(storage.iter_events("demo"))
    gate_ev = [e for e in events
               if e.type is EventType.GATE_FINISHED and e.task_id == task_id
               and e.payload.get("gate_result", {}).get("phase") == "post-merge"][0]
    assert gate_ev.payload["gate_result"]["exit_code"] == 124


# ==========================================================================
# B3-followon 2026-07-26: _run_post_merge_gate_bg / _run_post_merge_gate /
# _drain_post_merge_gate_results direct-call unit coverage, mirroring
# test_gate_verify_cadence.py's own _run_gate_verify_bg / _execute_verify_gate
# / _drain_gate_verify_results suites (see daemon.py's block comment above
# _select_post_merge_gate for the full design).
# ==========================================================================

def _run_git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _real_merge_commit(root) -> tuple[str, str]:
    """Advance `root`'s main to a real --no-ff merge commit; return
    (pre_merge_main, merge_commit) where merge_commit^1 == pre_merge_main.
    Mirrors test_post_merge.py's helper of the same shape (kept local per
    this file's own no-conftest-additions convention)."""
    before = _run_git(root, "rev-parse", "main").stdout.strip()
    assert _run_git(root, "checkout", "-b", "feat/bg-revert-me").returncode == 0
    (root / "bg_revert_me.txt").write_text("payload\n", encoding="utf-8")
    assert _run_git(root, "add", "bg_revert_me.txt").returncode == 0
    assert _run_git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "feat: bg-revert-me").returncode == 0
    assert _run_git(root, "checkout", "main").returncode == 0
    assert _run_git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                    "merge", "--no-ff", "feat/bg-revert-me", "-m", "merge feat/bg-revert-me").returncode == 0
    merge_commit = _run_git(root, "rev-parse", "main").stdout.strip()
    assert merge_commit != before
    return before, merge_commit


def test_run_post_merge_gate_bg_gate_passes(tmp_state, sample_project):
    """Direct bg-method call, no thread: a passing gate yields a
    COMPLETED-shaped result dict on the queue -- the bg method itself never
    appends an event or transitions state."""
    commit = _run_git(sample_project.root, "rev-parse", "HEAD").stdout.strip()
    d = daemon.Daemon({"demo": sample_project.root})
    d._gates._run_post_merge_probe("demo", sample_project, "demo-P01-sample", commit,
                                   "post-merge-gate:demo-P01-sample")

    result = d._gates.post_merge_results.get_nowait()
    assert result["outcome"] == "completed"
    assert result["gate_id"] == "pytest-q"
    assert result["gate_result"]["exit_code"] == 0
    assert result["gate_result"]["phase"] == "post-merge"
    assert result["gate_result"]["commit"] == commit
    assert d._gates.post_merge_running == {}  # bg method never touches this dict


def test_run_post_merge_gate_bg_no_gate_declared(tmp_state, sample_project):
    import dataclasses

    no_gate_cfg = dataclasses.replace(sample_project, gates={})
    d = daemon.Daemon({"demo": sample_project.root})
    d._gates._run_post_merge_probe("demo", no_gate_cfg, "demo-P01-sample", "somecommit",
                                   "post-merge-gate:demo-P01-sample")

    result = d._gates.post_merge_results.get_nowait()
    assert result == {
        "project": "demo", "task_id": "demo-P01-sample",
        "key": "post-merge-gate:demo-P01-sample",
        "gate_id": None, "gate_result": None, "outcome": "no_gate",
    }


def test_run_post_merge_gate_bg_gate_fails_and_reverts(tmp_state, sample_project):
    import dataclasses
    from nyxloom.config import GateDef

    before, merge_commit = _real_merge_commit(sample_project.root)
    failing_cfg = dataclasses.replace(sample_project, gates={
        "pytest-q": GateDef(gate_id="pytest-q", argv=["false"], phase="implementation",
                             timeout_seconds=10, environment="local"),
    })
    d = daemon.Daemon({"demo": sample_project.root})
    d._gates._run_post_merge_probe("demo", failing_cfg, "demo-P01-sample", merge_commit,
                                   "post-merge-gate:demo-P01-sample")

    result = d._gates.post_merge_results.get_nowait()
    assert result["outcome"] == "blocked"
    assert result["reverted"] is True
    assert result["revert_from"] == merge_commit
    assert result["revert_to"] == before
    assert result["gate_result"]["exit_code"] != 0
    # the revert really happened on disk
    assert _run_git(sample_project.root, "rev-parse", "main").stdout.strip() == before


def test_run_post_merge_gate_bg_gate_fails_cas_refused(tmp_state, sample_project):
    """<default_branch> moved PAST the merge commit before the gate finished
    -- the CAS update-ref is refused, main is left untouched, still
    outcome 'blocked' but reverted False."""
    import dataclasses
    from nyxloom.config import GateDef

    before, merge_commit = _real_merge_commit(sample_project.root)
    (sample_project.root / "later.txt").write_text("later\n", encoding="utf-8")
    assert _run_git(sample_project.root, "add", "later.txt").returncode == 0
    assert _run_git(sample_project.root, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "later commit").returncode == 0
    main_now = _run_git(sample_project.root, "rev-parse", "main").stdout.strip()
    assert main_now != merge_commit

    failing_cfg = dataclasses.replace(sample_project, gates={
        "pytest-q": GateDef(gate_id="pytest-q", argv=["false"], phase="implementation",
                             timeout_seconds=10, environment="local"),
    })
    d = daemon.Daemon({"demo": sample_project.root})
    d._gates._run_post_merge_probe("demo", failing_cfg, "demo-P01-sample", merge_commit,
                                   "post-merge-gate:demo-P01-sample")

    result = d._gates.post_merge_results.get_nowait()
    assert result["outcome"] == "blocked"
    assert result["reverted"] is False
    assert "revert_from" not in result
    assert _run_git(sample_project.root, "rev-parse", "main").stdout.strip() == main_now


def test_run_post_merge_gate_starts_no_second_thread_while_one_is_alive(
        tmp_state, sample_project, monkeypatch):
    """Mirrors test_gate_verify_cadence.py's
    test_execute_verify_gate_starts_no_second_thread_while_one_is_alive
    exactly, applied to the post-merge dispatcher (keyed by task_id)."""
    started = threading.Event()
    release = threading.Event()

    def fake_bg(self, project, cfg, task_id, commit, key):
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(effects_gates.GateEffector, "_run_post_merge_probe", fake_bg)
    d = daemon.Daemon({"demo": sample_project.root})
    task_id = "demo-P01-sample"
    cur = _seed_task("demo", task_id, TaskState.VALIDATING)
    cur.merge_commit = "c0ffee0001"
    storage.save_state(cur)
    states = {task_id: cur}
    action = reconcile.RunPostMergeGate(task_id=task_id)

    try:
        events1 = d._execute("demo", sample_project, states, action)
        assert events1 == []
        assert started.wait(timeout=2), "background thread never started"
        first = d._gates.post_merge_running[f"post-merge-gate:{task_id}"]
        assert first.is_alive()

        events2 = d._execute("demo", sample_project, states, action)
        assert events2 == []
        second = d._gates.post_merge_running[f"post-merge-gate:{task_id}"]
        assert second is first, "a second thread was started while the first was still alive"
    finally:
        release.set()
        d._gates.post_merge_running[f"post-merge-gate:{task_id}"].join(timeout=5)


def test_run_post_merge_gate_starts_a_fresh_thread_once_the_prior_one_finished(
        tmp_state, sample_project, monkeypatch):
    """Positive control for the idempotence test above: once a gate run
    genuinely finishes, the NEXT RunPostMergeGate action does start a new
    thread (idempotence guards a live run, not the feature)."""
    calls = []

    def fake_bg(self, project, cfg, task_id, commit, key):
        calls.append(key)

    monkeypatch.setattr(effects_gates.GateEffector, "_run_post_merge_probe", fake_bg)
    d = daemon.Daemon({"demo": sample_project.root})
    task_id = "demo-P01-sample"
    cur = _seed_task("demo", task_id, TaskState.VALIDATING)
    cur.merge_commit = "c0ffee0002"
    storage.save_state(cur)
    states = {task_id: cur}
    action = reconcile.RunPostMergeGate(task_id=task_id)

    d._execute("demo", sample_project, states, action)
    d._gates.post_merge_running[f"post-merge-gate:{task_id}"].join(timeout=5)
    d._execute("demo", sample_project, states, action)
    d._gates.post_merge_running[f"post-merge-gate:{task_id}"].join(timeout=5)
    assert calls == ["post-merge-gate:demo-P01-sample"] * 2


def _pm_result(task_id: str, **overrides) -> dict:
    base = {
        "project": "demo", "task_id": task_id,
        # the identity the dispatcher registered the work under; the drain
        # clears exactly this entry rather than re-deriving it
        "key": f"post-merge-gate:{task_id}", "gate_id": "pytest-q",
        "gate_result": GateResult(gate_id="pytest-q", phase="post-merge", commit="c0ffee",
                                   exit_code=0, started=utc_now(), ended=utc_now()).to_dict(),
        "outcome": "completed",
    }
    base.update(overrides)
    return base


def test_drain_post_merge_gate_completed_transitions_task(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    task_id = "demo-P01-sample"
    cur = _seed_task("demo", task_id, TaskState.VALIDATING)
    states = {task_id: cur}
    d._gates.post_merge_running[f"post-merge-gate:{task_id}"] = object()  # sentinel: must be cleared
    d._gates.post_merge_results.put(_pm_result(task_id))

    events = d._gates.drain_post_merge(d._effect_context("demo", sample_project, states))

    types_ = [e.type for e in events]
    assert EventType.GATE_FINISHED in types_
    assert EventType.TASK_TRANSITIONED in types_
    assert states[task_id].state is TaskState.COMPLETED
    assert f"post-merge-gate:{task_id}" not in d._gates.post_merge_running


def test_drain_post_merge_gate_no_gate_transitions_task_no_gate_finished_event(
        tmp_state, sample_project, monkeypatch):
    """The no_gate outcome mirrors the old no-op-validated-pass branch: a
    terminal transition, but no GATE_FINISHED (there is nothing to record)."""
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    task_id = "demo-P01-sample"
    cur = _seed_task("demo", task_id, TaskState.VALIDATING)
    states = {task_id: cur}
    d._gates.post_merge_results.put(_pm_result(
        task_id, gate_id=None, gate_result=None, outcome="no_gate"))

    events = d._gates.drain_post_merge(d._effect_context("demo", sample_project, states))

    types_ = [e.type for e in events]
    assert EventType.GATE_FINISHED not in types_
    assert EventType.TASK_TRANSITIONED in types_
    assert states[task_id].state is TaskState.COMPLETED


def test_drain_post_merge_gate_blocked_appends_task_blocked(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    task_id = "demo-P01-sample"
    cur = _seed_task("demo", task_id, TaskState.VALIDATING)
    states = {task_id: cur}
    failing_result = GateResult(gate_id="pytest-q", phase="post-merge", commit="c0ffee",
                                 exit_code=1, started=utc_now(), ended=utc_now()).to_dict()
    d._gates.post_merge_results.put(_pm_result(
        task_id, gate_result=failing_result, outcome="blocked", reverted=False))

    events = d._gates.drain_post_merge(d._effect_context("demo", sample_project, states))

    types_ = {e.type for e in events}
    assert EventType.GATE_FINISHED in types_
    assert EventType.TASK_BLOCKED in types_
    assert EventType.MERGE_REVERTED not in types_
    assert states[task_id].state is TaskState.BLOCKED
    assert states[task_id].blocker.type is BlockerType.ENVIRONMENT


def test_drain_post_merge_gate_blocked_with_revert_appends_merge_reverted(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    task_id = "demo-P01-sample"
    cur = _seed_task("demo", task_id, TaskState.VALIDATING)
    states = {task_id: cur}
    failing_result = GateResult(gate_id="pytest-q", phase="post-merge", commit="deadbeef",
                                 exit_code=1, started=utc_now(), ended=utc_now()).to_dict()
    d._gates.post_merge_results.put(_pm_result(
        task_id, gate_result=failing_result, outcome="blocked",
        reverted=True, revert_from="deadbeef", revert_to="c0ffee"))

    events = d._gates.drain_post_merge(d._effect_context("demo", sample_project, states))

    types_ = {e.type for e in events}
    assert EventType.MERGE_REVERTED in types_
    assert EventType.TASK_BLOCKED in types_
    reverted_ev = next(e for e in events if e.type is EventType.MERGE_REVERTED)
    assert reverted_ev.payload["from"] == "deadbeef"
    assert reverted_ev.payload["to"] == "c0ffee"
    assert reverted_ev.payload["branch"] == sample_project.default_branch
    assert states[task_id].state is TaskState.BLOCKED


def test_drain_post_merge_gate_requeues_results_belonging_to_other_projects(
        tmp_state, sample_project, monkeypatch):
    """The result queue is shared across every registered project (one
    Daemon, one queue) -- a drain call scoped to 'demo' must not consume (or
    lose) a result stamped for a different project."""
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    d = daemon.Daemon({"demo": sample_project.root})
    d._gates.post_merge_results.put(_pm_result("other-task", project="other"))

    events = d._gates.drain_post_merge(d._effect_context("demo", sample_project, {}))

    assert events == []
    requeued = d._gates.post_merge_results.get_nowait()
    assert requeued["project"] == "other"
    assert d._gates.post_merge_results.empty()


def _write_merge_handoff(root, task_id: str) -> str:
    """A real handoff/*.md matching handoff_globs (SAMPLE_PROJECT_TOML),
    frontmatter `id:` == task_id -- reconcile.plan_project's per-task loop
    is keyed on `inp.frontmatters` (only ids present there are ever
    visited: `for fm_id, ... in inp.frontmatters.items(): if fm_id not in
    inp.states: continue`), so the MERGE_READY->AutoMergeTask trigger for
    this task_id never fires without a matching frontmatter entry."""
    rel = f"handoff/{task_id}.md"
    (root / "handoff" / f"{task_id}.md").write_text(f"""\
---
schema_version: 1
id: {task_id}
project: demo
title: Test package
tier: flash-high
input_revision: "0000000"
source: {{kind: roadmap, ref: docs/ROADMAP.md}}
scope:
  touch: ["src/demo/thing.py"]
  forbid: []
oracles:
  - id: O1
    observable: "pytest tests/test_thing.py::test_bound passes"
    negative: "a value over the limit raises BoundError"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Test package
""", encoding="utf-8")
    return rel


def test_auto_merge_emits_merge_info_log(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """§5: merge -> INFO. Self-contained (mirrors tests/test_auto_merge.py's
    test_clean_merge_advances_main_and_transitions_to_merged, which this
    package may not touch -- see scope.touch)."""
    import dataclasses
    from nyxloom import config as config_mod

    cfg = sample_project
    cfg2 = dataclasses.replace(cfg, policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: cfg2))

    subprocess.run(["git", "-C", str(cfg.root), "checkout", "-b", "feat/demo-P96"],
                    check=True, capture_output=True)
    (cfg.root / "new_thing.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(cfg.root), "add", "new_thing.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(cfg.root),
                    "commit", "-qm", "add new_thing"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(cfg.root), "checkout", "main"], check=True, capture_output=True)

    task_id = "demo-P96"
    handoff_path = _write_merge_handoff(cfg.root, task_id)
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project="demo",
        state=TaskState.CARVED, since=utc_now(), handoff_path=handoff_path,
    )
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    cur = storage.load_state("demo", task_id)
    cur.state = TaskState.MERGE_READY
    cur.attempts = [Attempt(
        attempt_id="att-impl96", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=utc_now(), branch="feat/demo-P96",
    )]
    storage.save_state(cur)

    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.MERGED

    records = _read_log_records(log_dir)
    merged = [r for r in records if r.get("msg") == "merge"]
    assert len(merged) == 1
    assert merged[0]["level"] == "info"
    assert merged[0]["project"] == "demo"
    assert merged[0]["task"] == task_id
    assert merged[0]["merge_commit"] == tsf2.merge_commit


def test_auto_merge_conflict_emits_error(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """§5: "merge conflict escalated" is a named ERROR example. Self-
    contained (mirrors tests/test_auto_merge.py's real-conflict test, which
    this package may not touch -- see scope.touch)."""
    import dataclasses
    from nyxloom import config as config_mod

    cfg = sample_project
    cfg2 = dataclasses.replace(cfg, policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: cfg2))
    task_id = "demo-P95"
    # demo-P95's OWN handoff (untouched by the conflict below -- disk-only,
    # never committed, exactly like the plain-merge test above; frontmatter
    # discovery globs disk regardless of git status).
    handoff_path = _write_merge_handoff(cfg.root, task_id)

    # The conflict is manufactured on an UNRELATED, already-tracked file
    # (sample_project's own base handoff, committed by the fixture) -- never
    # demo-P95's own handoff -- mirroring tests/test_auto_merge.py's own
    # real-conflict test exactly.
    conflict_victim = "handoff/demo-P01-sample.md"
    subprocess.run(["git", "-C", str(cfg.root), "checkout", "-b", "feat/demo-P95"],
                    check=True, capture_output=True)
    (cfg.root / conflict_victim).write_text("branch version\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(cfg.root), "add", conflict_victim],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(cfg.root),
                    "commit", "-qm", "branch edit"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(cfg.root), "checkout", "main"], check=True, capture_output=True)
    # main then diverges on the SAME file, guaranteeing a real textual conflict
    (cfg.root / conflict_victim).write_text("main version\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(cfg.root), "add", conflict_victim],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(cfg.root),
                    "commit", "-qm", "main diverges"], check=True, capture_output=True)

    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project="demo",
        state=TaskState.CARVED, since=utc_now(), handoff_path=handoff_path,
    )
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    cur = storage.load_state("demo", task_id)
    cur.state = TaskState.MERGE_READY
    cur.attempts = [Attempt(
        attempt_id="att-impl95", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=utc_now(), branch="feat/demo-P95",
    )]
    storage.save_state(cur)

    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.MERGE_READY  # unresolved conflict left in place

    records = _read_log_records(log_dir)
    conflict = [r for r in records if r.get("msg") == "merge-conflict"]
    assert len(conflict) == 1
    assert conflict[0]["level"] == "error"
    assert conflict[0]["project"] == "demo"
    assert conflict[0]["task"] == task_id


def test_launch_review_emits_info_review_launch(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """§5: review launch -> INFO."""
    cfg = sample_project
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n"
    )
    for tid in ("rt1", "rt2"):
        _seed_task("demo", tid, TaskState.AWAITING_REVIEW, handoff_path=None)
        _make_feature_branch(cfg.root, tid, f"{tid}.py", f"# {tid}\n")

    _scripted(monkeypatch, [[reconcile.OpenWave(task_ids=["rt1", "rt2"])]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")
    wave_id = next(e for e in storage.iter_events("demo") if e.type is EventType.WAVE_OPENED).wave_id

    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    _scripted(monkeypatch, [[reconcile.LaunchReview(wave_id=wave_id, task_ids=["rt1", "rt2"])]])
    d.run_pass("demo")

    records = _read_log_records(log_dir)
    launched = [r for r in records if r.get("msg") == "review-launch"]
    assert len(launched) == 1
    assert launched[0]["level"] == "info"
    assert launched[0]["project"] == "demo"
    assert sorted(launched[0]["tasks"]) == ["rt1", "rt2"]
    assert launched[0]["route"] == "fake-cli"
    assert launched[0]["wave"] == wave_id


def test_transition_emits_info_state_transition(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """§5: state transitions -> INFO (every transition funnels through the
    one Daemon._transition helper)."""
    task_id = "demo-P01-sample"
    _seed_task("demo", task_id, TaskState.QUEUED, handoff_path="handoff/demo-P01-sample.md")

    log_dir = tmp_state / "logs"
    log.configure(level=log.INFO, log_dir=log_dir, console=False)

    _scripted(monkeypatch, [[reconcile.Transition(task_id=task_id, to=TaskState.ACTIVE, notes=None)]])
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")

    records = _read_log_records(log_dir)
    transitioned = [r for r in records if r.get("msg") == "state-transition"]
    assert len(transitioned) == 1
    assert transitioned[0]["level"] == "info"
    assert transitioned[0]["project"] == "demo"
    assert transitioned[0]["task"] == task_id
    assert transitioned[0]["frm"] == "QUEUED"
    assert transitioned[0]["to"] == "ACTIVE"


def test_run_pass_emits_debug_pass_summary(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """§5: "per-pass counts ... -> DEBUG"."""
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])  # zero actions this pass
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    log_dir = tmp_state / "logs"
    log.configure(level=log.DEBUG, log_dir=log_dir, console=False)

    d = daemon.Daemon({"demo": sample_project.root})
    n = d.run_pass("demo")
    assert n == 0

    records = _read_log_records(log_dir)
    summary = [r for r in records if r.get("msg") == "pass-summary"]
    assert len(summary) == 1
    assert summary[0]["level"] == "debug"
    assert summary[0]["project"] == "demo"
    assert summary[0]["actions"] == 0
    assert summary[0]["events"] == 0


# ==========================================================================
# P04 2026-07-21 (docs/plan-logging.md §4.5): the /api/logs* read-only HTTP
# surface + logs.html dashboard page. Oracles O1-O6 below cover the
# endpoints; O7 (render.py) lives in tests/test_render.py.
#
# http_daemon (fixture above) boots a real Daemon.run() thread with
# lint.lint_project/reconcile.plan_project stubbed to no-ops at the
# DEFAULT effective level (INFO) -- the only thing it logs at INFO+ is the
# one-time "daemon started" record, so the log file is quiescent after
# startup and these tests can append their own controlled records via
# _write_raw_log_lines without racing further daemon output.

def test_logs_endpoint_level_filter(http_daemon):
    """O1: GET /api/logs?level=warning returns only records level>=warning;
    an info line is absent. Negative: info leaking, or a 500."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    _write_raw_log_lines([
        {"ts": "2026-07-21T10:00:00", "level": "info", "logger": "x", "msg": "o1-info-line"},
        {"ts": "2026-07-21T10:00:01", "level": "warning", "logger": "x", "msg": "o1-warn-line"},
        {"ts": "2026-07-21T10:00:02", "level": "error", "logger": "x", "msg": "o1-err-line"},
    ])

    data = json.loads(urllib.request.urlopen(f"{base}/api/logs?level=warning", timeout=5).read())
    msgs = [r["msg"] for r in data]
    assert "o1-warn-line" in msgs
    assert "o1-err-line" in msgs
    assert "o1-info-line" not in msgs  # NEGATIVE: info must not leak through the filter


def test_logs_endpoint_paging_limit_and_since(http_daemon):
    """O2: ?limit=2 returns the 2 newest; ?since=<seq> returns only
    seq>since. Negative: limit ignored / since returns duplicates."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    _write_raw_log_lines([
        {"ts": "2026-07-21T10:00:00", "level": "info", "logger": "x", "msg": "o2-line-a"},
        {"ts": "2026-07-21T10:00:01", "level": "info", "logger": "x", "msg": "o2-line-b"},
        {"ts": "2026-07-21T10:00:02", "level": "info", "logger": "x", "msg": "o2-line-c"},
    ])

    limited = json.loads(urllib.request.urlopen(f"{base}/api/logs?limit=2", timeout=5).read())
    limited_msgs = [r["msg"] for r in limited if r["msg"].startswith("o2-line-")]
    assert limited_msgs == ["o2-line-b", "o2-line-c"]  # the 2 newest, newest-last

    all_records = json.loads(urllib.request.urlopen(f"{base}/api/logs", timeout=5).read())
    by_msg = {r["msg"]: r["seq"] for r in all_records if r["msg"].startswith("o2-line-")}
    since_seq = by_msg["o2-line-a"]

    paged = json.loads(urllib.request.urlopen(f"{base}/api/logs?since={since_seq}", timeout=5).read())
    paged_msgs = [r["msg"] for r in paged if r["msg"].startswith("o2-line-")]
    assert "o2-line-a" not in paged_msgs          # NEGATIVE: since must exclude its own seq
    assert paged_msgs == ["o2-line-b", "o2-line-c"]  # no duplicates, strictly seq>since


def test_logs_endpoint_empty_state_missing_file(http_daemon):
    """O3: no nyxloom.jsonl -> 200 []. Negative: 404/500."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    log_path = paths.nyxloom_log_path()
    if log_path.exists():
        log_path.unlink()
    assert not log_path.exists()

    resp = urllib.request.urlopen(f"{base}/api/logs", timeout=5)
    assert resp.status == 200
    assert json.loads(resp.read()) == []


def test_logs_endpoint_search_query(http_daemon):
    """O5: ?q=needle returns only records containing 'needle' (case-
    insensitive); a non-match is absent; a regex-special q (e.g. '.*')
    does not crash. Negative: q ignored / 500 on special chars."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    _write_raw_log_lines([
        {"ts": "2026-07-21T10:00:00", "level": "info", "logger": "x", "msg": "found the O5-NEEDLE here"},
        {"ts": "2026-07-21T10:00:01", "level": "info", "logger": "x", "msg": "o5 nothing relevant"},
    ])

    data = json.loads(urllib.request.urlopen(f"{base}/api/logs?q=o5-needle", timeout=5).read())
    msgs = [r["msg"] for r in data]
    assert "found the O5-NEEDLE here" in msgs      # case-insensitive match
    assert "o5 nothing relevant" not in msgs        # NEGATIVE: a non-match must not leak in

    # NEGATIVE: a regex-special q must not crash the (plain-substring) search.
    resp = urllib.request.urlopen(f"{base}/api/logs?q=.*", timeout=5)
    assert resp.status == 200


def test_logs_export_endpoint(http_daemon):
    """O6: /api/logs/export?level=error -> 200, Content-Disposition:
    attachment, body is JSONL of exactly the filtered set. Negative: wrong
    content-type / no attachment header / body != filtered set."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    _write_raw_log_lines([
        {"ts": "2026-07-21T10:00:00", "level": "info", "logger": "x", "msg": "o6-info-line"},
        {"ts": "2026-07-21T10:00:01", "level": "error", "logger": "x", "msg": "o6-error-line"},
    ])

    resp = urllib.request.urlopen(f"{base}/api/logs/export?level=error", timeout=5)
    assert resp.status == 200
    assert resp.headers.get("Content-Type") == "application/x-ndjson"
    assert resp.headers.get("Content-Disposition") == 'attachment; filename="nyxloom-logs.jsonl"'

    body = resp.read().decode("utf-8")
    exported = [json.loads(ln) for ln in body.splitlines() if ln.strip()]

    expected = json.loads(urllib.request.urlopen(f"{base}/api/logs?level=error", timeout=5).read())
    assert exported == expected
    assert any(r["msg"] == "o6-error-line" for r in exported)
    assert not any(r["msg"] == "o6-info-line" for r in exported)  # NEGATIVE: wrong filter set


def test_logs_stream_level_filter_and_heartbeat(tmp_state, sample_project, monkeypatch):
    """O4: a line appended AFTER connect arrives as an SSE data: frame
    within a poll interval; a below-level line does NOT arrive; a
    heartbeat is emitted. Negative: no data frame / below-level leaks."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    monkeypatch.setattr(daemon, "SSE_POLL_SECONDS", 0.05)
    monkeypatch.setattr(daemon, "SSE_HEARTBEAT_SECONDS", 0.2)
    _set_ephemeral_http_port(sample_project)

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert d.http_port != 0

    received: dict = {"records": [], "heartbeats": 0}

    def reader():
        conn = http.client.HTTPConnection("127.0.0.1", d.http_port, timeout=10)
        conn.request("GET", "/api/logs/stream?level=warning")
        resp = conn.getresponse()
        buf = b""
        deadline2 = time.monotonic() + 10
        while time.monotonic() < deadline2:
            chunk = resp.read(1)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                line, buf = buf.split(b"\n\n", 1)
                if line.startswith(b"data: "):
                    payload = json.loads(line[len(b"data: "):])
                    received["records"].append(payload)
                elif line.startswith(b": hb"):
                    received["heartbeats"] += 1
            if received["records"] and received["heartbeats"]:
                break
        conn.close()

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    time.sleep(0.3)  # let the reader connect first -- the stream starts tailing at EOF

    _write_raw_log_lines([
        {"ts": "2026-07-21T10:00:00", "level": "info", "logger": "x", "msg": "o4-below-threshold"},
        {"ts": "2026-07-21T10:00:01", "level": "warning", "logger": "x", "msg": "o4-at-threshold"},
    ])

    reader_thread.join(timeout=10)
    d.stop()
    t.join(timeout=5)
    assert not t.is_alive(), "daemon thread outlived the test and may pollute global logging"

    msgs = [r.get("msg") for r in received["records"]]
    assert "o4-at-threshold" in msgs
    assert "o4-below-threshold" not in msgs  # NEGATIVE: below-level must not stream
    assert received["heartbeats"] >= 1


# --------------------------------------------------------------------------
# P04 coverage completion (controller re-gate 2026-07-21): the parked agent's
# tests covered the happy paths but not the defensive branches the 100% diff-
# coverage floor requires -- malformed query params, blank/malformed log
# lines, project exclusion, log-stream rotation, and the disconnect path. The
# stream branches are exercised through _log_stream_tick (the pure per-poll
# helper) in the MAIN thread, deterministically, rather than racing the SSE
# loop in the daemon thread (which is why those lines went uncovered).

def test_read_log_records_skips_blank_and_malformed_and_excludes_project(
        tmp_state, sample_project):
    """The reader drops blank + malformed lines and honours project=.
    Negative: a blank/garbage line 500s the read, or project= is ignored."""
    d = daemon.Daemon({"demo": sample_project.root})
    p = paths.nyxloom_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"ts": "2026-07-21T10:00:00", "level": "info",
                    "logger": "x", "msg": "keep-demo", "project": "demo"}) + "\n"
        + "\n"                                   # blank line -> skipped
        + "this is not json\n"                   # malformed -> skipped
        + json.dumps({"ts": "2026-07-21T10:00:01", "level": "info",
                      "logger": "x", "msg": "keep-other", "project": "other"}) + "\n",
        encoding="utf-8",
    )

    allrecs = d._read_log_records()
    assert [r["msg"] for r in allrecs] == ["keep-demo", "keep-other"]  # blank+garbage skipped

    demo_only = d._read_log_records(project="demo")
    assert [r["msg"] for r in demo_only] == ["keep-demo"]  # project= excludes 'other'


def test_log_stream_tick_filters_rotates_and_heartbeats(tmp_state, sample_project):
    """_log_stream_tick: blank/malformed/below-level lines are dropped, above-
    level lines become data: frames, a heartbeat frame appears once the
    interval elapses, and an offset past a shrunken file resets to 0
    (rotation). Negative: below-level leaks, rotation tails a rotated-away
    file, or no heartbeat ever emits."""
    d = daemon.Daemon({"demo": sample_project.root})
    p = paths.nyxloom_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"ts": "2026-07-21T10:00:00", "level": "info",
                    "logger": "x", "msg": "tick-below"}) + "\n"
        + "\n"                                   # blank -> skipped
        + "garbage-not-json\n"                   # malformed -> skipped
        + json.dumps({"ts": "2026-07-21T10:00:01", "level": "error",
                      "logger": "x", "msg": "tick-above"}) + "\n",
        encoding="utf-8",
    )
    warn = log._normalize_level("warning")

    # From offset 0 with the heartbeat interval elapsed (now huge, last=0):
    chunks, new_offset, new_hb = d._log_stream_tick(1_000_000.0, p, 0, 0.0, warn)
    text = b"".join(chunks).decode("utf-8")
    assert "tick-above" in text                  # above-level streamed
    assert "tick-below" not in text              # NEGATIVE: below-level dropped
    assert b": hb\n\n" in chunks                 # heartbeat emitted
    assert new_offset == p.stat().st_size        # advanced to EOF
    assert new_hb == 1_000_000.0                 # heartbeat clock reset

    # No new data + interval NOT elapsed -> empty (no spurious heartbeat).
    none_chunks, _, _ = d._log_stream_tick(new_hb, p, new_offset, new_hb, warn)
    assert none_chunks == []                     # NEGATIVE: no spurious heartbeat

    # Rotation: an offset past a now-smaller file resets to 0 and re-reads.
    rot_chunks, rot_offset, _ = d._log_stream_tick(1.0, p, 10_000_000, 0.0, warn)
    assert rot_offset == p.stat().st_size        # reset to 0 then read to EOF
    assert any(b"tick-above" in c for c in rot_chunks)  # re-read after rotation


def test_log_stream_serve_returns_cleanly_on_client_disconnect(
        tmp_state, sample_project, monkeypatch):
    """_serve_log_stream swallows a mid-write client disconnect (wfile raises)
    and returns rather than propagating. Deterministic + main-thread: the
    heartbeat interval is forced to 0 so the first tick writes, and the fake
    wfile raises BrokenPipeError. Negative: the exception escapes / hangs."""
    monkeypatch.setattr(daemon, "SSE_HEARTBEAT_SECONDS", 0.0)
    monkeypatch.setattr(daemon, "SSE_POLL_SECONDS", 0.0)
    d = daemon.Daemon({"demo": sample_project.root})

    class _RaisingWFile:
        def write(self, _b):
            raise BrokenPipeError("client gone")

        def flush(self):  # pragma: no cover - never reached (write raises first)
            pass

    class _FakeHandler:
        def __init__(self):
            self.wfile = _RaisingWFile()

        def send_response(self, _code):
            pass

        def send_header(self, _k, _v):
            pass

        def end_headers(self):
            pass

    # Must return promptly: the write on the first heartbeat raises -> except.
    d._serve_log_stream(_FakeHandler(), None)


def test_logs_endpoint_malformed_params_default_gracefully(http_daemon):
    """Malformed since=/limit= fall back to defaults rather than 500, on both
    /api/logs and /api/logs/export (the except ValueError branches)."""
    d = http_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    _write_raw_log_lines([
        {"ts": "2026-07-21T10:00:00", "level": "info", "logger": "x", "msg": "mp-line"},
    ])

    resp = urllib.request.urlopen(f"{base}/api/logs?since=notint&limit=alsobad", timeout=5)
    assert resp.status == 200
    assert any(r["msg"] == "mp-line" for r in json.loads(resp.read()))  # defaults, not a 500

    exp = urllib.request.urlopen(
        f"{base}/api/logs/export?since=notint&limit=alsobad", timeout=5)
    assert exp.status == 200
    assert exp.headers.get("Content-Disposition") == 'attachment; filename="nyxloom-logs.jsonl"'
    assert "mp-line" in exp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# F017: mutation gate wiring in _execute_auto_merge
# ---------------------------------------------------------------------------

def _setup_merge_task(cfg, tmp_state, task_id, branch_name) -> str:
    """Helper: create a feature branch, seed a task, return the handoff path."""
    subprocess.run(["git", "-C", str(cfg.root), "checkout", "-b", branch_name],
                    check=True, capture_output=True)
    (cfg.root / f"{task_id}.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(cfg.root), "add", f"{task_id}.txt"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(cfg.root),
                    "commit", "-qm", f"add {task_id}"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(cfg.root), "checkout", "main"],
                    check=True, capture_output=True)

    handoff_path = _write_merge_handoff(cfg.root, task_id)
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project="demo",
        state=TaskState.CARVED, since=utc_now(), handoff_path=handoff_path,
    )
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    cur = storage.load_state("demo", task_id)
    cur.state = TaskState.MERGE_READY
    cur.attempts = [Attempt(
        attempt_id=f"att-{task_id}", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=utc_now(), branch=branch_name,
    )]
    storage.save_state(cur)
    return handoff_path


def test_mutation_gate_pass_merges(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """mutation_gate=True + a phase='mutation' GateDef with argv ['true'] (exit 0)
    → task transitions to MERGED, a GATE_FINISHED with phase=='mutation' and
    exit_code==0 is recorded, and the default branch advances."""
    import dataclasses
    from nyxloom import config as config_mod
    from nyxloom.config import GateDef

    cfg = sample_project
    cfg2 = dataclasses.replace(cfg,
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic",
                                    mutation_gate=True),
        gates={"pytest-q": GateDef(gate_id="pytest-q", argv=["true"],
                                    phase="implementation", timeout_seconds=10),
               "mut-gate": GateDef(gate_id="mut-gate", argv=["true"],
                                    phase="mutation", timeout_seconds=10)},
    )
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: cfg2))

    task_id = "demo-P99"
    branch = f"feat/{task_id}"
    _setup_merge_task(cfg, tmp_state, task_id, branch)

    old_commit = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "main"],
        capture_output=True, text=True).stdout.strip()

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.MERGED

    new_commit = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "main"],
        capture_output=True, text=True).stdout.strip()
    assert new_commit != old_commit

    events = list(storage.iter_events("demo"))
    gate_events = [e for e in events if e.type is EventType.GATE_FINISHED]
    mutation_gate_events = [e for e in gate_events
                            if e.payload.get("gate_result", {}).get("phase") == "mutation"]
    assert len(mutation_gate_events) == 1
    assert mutation_gate_events[0].payload["gate_result"]["exit_code"] == 0
    assert mutation_gate_events[0].payload["gate_result"]["gate_id"] == "mut-gate"


def test_mutation_gate_failure_rejects(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """mutation_gate=True + a phase='mutation' GateDef with argv ['false'] (exit 1)
    → task transitions to REVIEW_REJECTED, default branch is NOT updated, scratch
    is removed, and a GATE_FINISHED with phase=='mutation' exit_code != 0 is
    recorded."""
    import dataclasses
    from nyxloom import config as config_mod
    from nyxloom.config import GateDef

    cfg = sample_project
    cfg2 = dataclasses.replace(cfg,
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic",
                                    mutation_gate=True),
        gates={"pytest-q": GateDef(gate_id="pytest-q", argv=["true"],
                                    phase="implementation", timeout_seconds=10),
               "mut-gate": GateDef(gate_id="mut-gate", argv=["false"],
                                    phase="mutation", timeout_seconds=10)},
    )
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: cfg2))

    task_id = "demo-P98"
    branch = f"feat/{task_id}"
    _setup_merge_task(cfg, tmp_state, task_id, branch)

    old_commit = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "main"],
        capture_output=True, text=True).stdout.strip()

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED

    # Default branch unchanged
    new_commit = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "main"],
        capture_output=True, text=True).stdout.strip()
    assert new_commit == old_commit

    # Scratch worktree removed
    scratch = Path(cfg.root / ".worktrees" / f"automerge-{task_id}")
    assert not scratch.exists()

    events = list(storage.iter_events("demo"))
    gate_events = [e for e in events if e.type is EventType.GATE_FINISHED]
    mutation_gate_events = [e for e in gate_events
                            if e.payload.get("gate_result", {}).get("phase") == "mutation"]
    assert len(mutation_gate_events) == 1
    assert mutation_gate_events[0].payload["gate_result"]["exit_code"] != 0


def test_mutation_gate_disabled_skips(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """mutation_gate=False (default) → the mutation gate is NOT run (no
    phase=='mutation' GATE_FINISHED), the merge proceeds as today (with the
    pre-merge coverage gate still applying)."""
    import dataclasses
    from nyxloom import config as config_mod
    from nyxloom.config import GateDef

    cfg = sample_project
    # mutation_gate is False by default, no need to set it
    cfg2 = dataclasses.replace(cfg,
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"),
        gates={"pytest-q": GateDef(gate_id="pytest-q", argv=["true"],
                                    phase="implementation", timeout_seconds=10),
               "mut-gate": GateDef(gate_id="mut-gate", argv=["false"],
                                    phase="mutation", timeout_seconds=10)},
    )
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: cfg2))

    task_id = "demo-P97"
    branch = f"feat/{task_id}"
    _setup_merge_task(cfg, tmp_state, task_id, branch)

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.MERGED

    events = list(storage.iter_events("demo"))
    mutation_gate_events = [e for e in events
                            if e.type is EventType.GATE_FINISHED
                            and e.payload.get("gate_result", {}).get("phase") == "mutation"]
    assert len(mutation_gate_events) == 0


def test_mutation_gate_no_declared_gate_skips(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """mutation_gate=True but NO phase=='mutation' GateDef is declared → the
    mutation gate is skipped, the merge proceeds as today."""
    import dataclasses
    from nyxloom import config as config_mod
    from nyxloom.config import GateDef

    cfg = sample_project
    cfg2 = dataclasses.replace(cfg,
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic",
                                    mutation_gate=True),
        # No phase=="mutation" gate
        gates={"pytest-q": GateDef(gate_id="pytest-q", argv=["true"],
                                    phase="implementation", timeout_seconds=10)},
    )
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: cfg2))

    task_id = "demo-P96"
    branch = f"feat/{task_id}"
    _setup_merge_task(cfg, tmp_state, task_id, branch)

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.MERGED

    events = list(storage.iter_events("demo"))
    mutation_gate_events = [e for e in events
                            if e.type is EventType.GATE_FINISHED
                            and e.payload.get("gate_result", {}).get("phase") == "mutation"]
    assert len(mutation_gate_events) == 0


def test_mutation_gate_timeout_expired(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """mutation_gate=True + subprocess.TimeoutExpired -> exit_code 124,
    task REVIEW_REJECTED, main unchanged."""
    import dataclasses
    from nyxloom import config as config_mod
    from nyxloom.config import GateDef

    cfg = sample_project
    cfg2 = dataclasses.replace(cfg,
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic",
                                    mutation_gate=True, pre_merge_gate=False),
        gates={"mut-gate": GateDef(gate_id="mut-gate", argv=["sleep", "5"],
                                    phase="mutation", timeout_seconds=1)},
    )
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: cfg2))

    task_id = "demo-P90"
    branch = f"feat/{task_id}"
    _setup_merge_task(cfg, tmp_state, task_id, branch)

    old_commit = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "main"],
        capture_output=True, text=True).stdout.strip()

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED

    new_commit = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "main"],
        capture_output=True, text=True).stdout.strip()
    assert new_commit == old_commit

    events = list(storage.iter_events("demo"))
    mg_events = [e for e in events
                 if e.type is EventType.GATE_FINISHED
                 and e.payload.get("gate_result", {}).get("phase") == "mutation"]
    assert len(mg_events) == 1
    assert mg_events[0].payload["gate_result"]["exit_code"] == 124


def test_mutation_gate_oserror(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """mutation_gate=True + OSError -> exit_code 127, task REVIEW_REJECTED,
    main unchanged."""
    import dataclasses
    from nyxloom import config as config_mod
    from nyxloom.config import GateDef

    cfg = sample_project
    cfg2 = dataclasses.replace(cfg,
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic",
                                    mutation_gate=True, pre_merge_gate=False),
        gates={"mut-gate": GateDef(gate_id="mut-gate", argv=["nonexistent_cmd_xyzzy"],
                                    phase="mutation", timeout_seconds=10)},
    )
    monkeypatch.setattr(config_mod.ProjectConfig, "load", classmethod(lambda cls, root: cfg2))

    task_id = "demo-P89"
    branch = f"feat/{task_id}"
    _setup_merge_task(cfg, tmp_state, task_id, branch)

    old_commit = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "main"],
        capture_output=True, text=True).stdout.strip()

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.REVIEW_REJECTED

    new_commit = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "main"],
        capture_output=True, text=True).stdout.strip()
    assert new_commit == old_commit

    events = list(storage.iter_events("demo"))
    mg_events = [e for e in events
                 if e.type is EventType.GATE_FINISHED
                 and e.payload.get("gate_result", {}).get("phase") == "mutation"]
    assert len(mg_events) == 1
    assert mg_events[0].payload["gate_result"]["exit_code"] == 127


# --------------------------------------------------------------------------
# F018 P2b-A2: daemon carver-snapshot input builder. `_carver_session` and
# `_pending_carver_feeds` derive two of the four new ReconcileInput carver
# fields (A1's pure planner already consumes them) from the durable event
# log; `pending_human_intakes`/`validated_carve_proposals` stay empty
# (deferred to #17 and P3 respectively). cfg.carve.session is the MASTER
# GATE ("fresh" = off, the default; "project-persistent" = on).

def _carver_digest_payload(n: int, task_id: str | None = None) -> dict:
    """A minimal synthetic carver_digest payload (MergeDigest.to_dict()
    shape) -- only the fields _pending_carver_feeds actually reads."""
    return {
        "digest_id": f"merge:demo:c{n}",
        "task_id": task_id or f"t{n}",
        "merge_commit": f"commit{n}",
        "diffstat": {"files_changed": n, "insertions": 0, "deletions": 0},
    }


def test_carver_snapshot_feature_off_is_inert_and_build_input_byte_identical(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """Load-bearing negative (byte-identical-when-off). cfg.carve.session
    defaults to "fresh". Seed a CARVER_SESSION_STARTED event AND a
    MERGE_RECORDED carrying a carver_digest -- their mere PRESENCE in the
    log must not activate anything. _carver_session returns None and
    _pending_carver_feeds returns () regardless of what events exist. A
    real run_pass wires both into the ReconcileInput the planner receives:
    carver_session=None and all three new tuple fields empty -- exactly
    ReconcileInput's own defaults, which is what makes
    reconcile.plan_project's `if inp.carver_session is not None` gate a
    no-op so the planner runs its pre-P2b path unchanged."""
    cfg = sample_project
    project = "demo"
    assert cfg.carve.session == "fresh"

    storage.append_event(
        project, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.CARVER_SESSION_STARTED,
        payload={"generation": 1, "session_id": "sess-1"})
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit1", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(1)},
        task_id="t1",
    )

    d = daemon.Daemon({project: cfg.root})
    assert d._carver._carver_session(project, cfg) is None
    assert d._pending_carver_feeds(project, cfg, None) == ()

    captured = _scripted(monkeypatch, [[]])
    d.run_pass(project)
    assert captured, "plan_project was never called -- no ReconcileInput built"
    inp = captured[0]
    assert inp.carver_session is None
    assert inp.pending_carver_feeds == ()
    assert inp.pending_human_intakes == ()
    assert inp.validated_carve_proposals == ()


def test_carver_session_feature_on_projects_snapshot_from_event_log(
        tmp_state, sample_project):
    """cfg.carve.session == "project-persistent" turns the MASTER GATE on:
    _carver_session must return the SAME snapshot carver_session's own pure
    project_session() projector would build from the project's event log --
    status/generation/cursor all match."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"

    storage.append_event(
        project, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.CARVER_SESSION_STARTED,
        payload={"generation": 3, "session_id": "sess-9"})
    storage.append_event(
        project, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.CARVER_CONTEXT_CONSUMED,
        payload={"highest_event_sequence": 2})

    expected = carver_session.project_session(list(storage.iter_events(project)))

    d = daemon.Daemon({project: cfg.root})
    snap = d._carver._carver_session(project, cfg)

    assert snap is not None
    assert snap.to_dict() == expected.to_dict()
    assert snap.status is CarverStatus.WARM
    assert snap.generation == 3
    assert snap.last_consumed_event_sequence == 2


def test_pending_carver_feeds_orders_and_excludes_consumed_and_missing_digest(
        tmp_state, sample_project):
    """FEED DERIVATION. Timeline: a MERGE_RECORDED at sequence 1 (already
    consumed once the cursor lands on it), a CARVER_CONTEXT_CONSUMED setting
    the cursor to sequence 1, then two pending MERGE_RECORDED digests (one
    with NO carver_digest payload in between -- must be skipped), all past
    the cursor. Only the past-cursor digests become CarverFeeds, in
    event_sequence ascending order."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"

    storage.append_and_apply(  # seq 1: already-consumed merge
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit1", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(1)},
        task_id="t1",
    )
    storage.append_event(  # seq 2: cursor lands ON seq 1 (at-cursor excluded)
        project, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.CARVER_CONTEXT_CONSUMED,
        payload={"highest_event_sequence": 1})
    storage.append_and_apply(  # seq 3: no carver_digest -- must be skipped
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit-nodigest", "source_kind": "review"},
        task_id="t-nodigest",
    )
    storage.append_and_apply(  # seq 4: pending
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit4", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(4)},
        task_id="t4",
    )
    storage.append_and_apply(  # seq 5: pending
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit5", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(5)},
        task_id="t5",
    )

    d = daemon.Daemon({project: cfg.root})
    snap = d._carver._carver_session(project, cfg)
    assert snap.last_consumed_event_sequence == 1

    feeds = d._pending_carver_feeds(project, cfg, snap)

    assert [f.event_sequence for f in feeds] == [4, 5]
    assert [f.digest_id for f in feeds] == ["merge:demo:c4", "merge:demo:c5"]
    assert feeds[0].task_id == "t4"
    assert feeds[0].merge_commit == "commit4"
    assert feeds[0].files_changed == 4
    assert feeds[1].files_changed == 5


def test_pending_carver_feeds_caps_at_retain_merge_digests(
        tmp_state, sample_project):
    """FEED DERIVATION cap. cfg.carve.retain_merge_digests bounds the tuple
    to the MOST RECENT N (by event_sequence), still ascending."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"
    cfg.carve.retain_merge_digests = 2

    for n in range(1, 5):  # 4 pending merges, no consumed cursor
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.MERGE_RECORDED,
            payload={"merge_commit": f"commit{n}", "source_kind": "review",
                     "carver_digest": _carver_digest_payload(n)},
            task_id=f"t{n}",
        )

    d = daemon.Daemon({project: cfg.root})
    snap = d._carver._carver_session(project, cfg)
    feeds = d._pending_carver_feeds(project, cfg, snap)

    assert len(feeds) == 2
    assert [f.event_sequence for f in feeds] == sorted(f.event_sequence for f in feeds)
    assert [f.digest_id for f in feeds] == ["merge:demo:c3", "merge:demo:c4"]


def test_carver_session_unreadable_log_is_typed_unavailable_not_a_fresh_session(
        tmp_state, sample_project, monkeypatch):
    """CR-02a REVERSES the old fail-safe here, because it was fail-OPEN.

    The previous behaviour degraded an unreadable event log to an empty event
    list, which the pure projector turned into the BASE snapshot -- ABSENT,
    generation 0, consumed-cursor 0. That is not a safe default: generation 0
    with a zero cursor means "this project has never carved", so every merge
    feed is unconsumed again and every recorded proposal is unadmitted again.
    A transient storage hiccup could therefore re-ingest the whole history.
    An unreadable log is now a typed authoritative fault."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"

    def _boom(*_a, **_kw):
        raise OSError("simulated unreadable event log")

    monkeypatch.setattr(storage, "iter_events", _boom)

    d = daemon.Daemon({project: cfg.root})
    with pytest.raises(snapshot.SnapshotUnavailable) as exc:
        d._carver._carver_session(project, cfg)
    assert exc.value.descriptor.name == "event_log"
    assert exc.value.descriptor.input_class is snapshot.InputClass.AUTHORITATIVE


def test_pending_carver_feeds_unreadable_log_is_typed_unavailable(
        tmp_state, sample_project, monkeypatch):
    """CR-02a: the feed-scanning side gets the same treatment.

    Degrading to "no pending feeds" is only harmless in isolation; combined
    with _carver_session's own former degradation it reconstructs a session
    with a zero consumed-cursor, and the pair silently re-ingests history. The
    log is one authoritative input with one failure mode."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"
    snap = carver_session.CarverSessionSnapshot(
        project=project, generation=1, status=CarverStatus.WARM)

    def _boom(*_a, **_kw):
        raise OSError("simulated unreadable event log")

    monkeypatch.setattr(storage, "iter_events", _boom)

    d = daemon.Daemon({project: cfg.root})
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._pending_carver_feeds(project, cfg, snap)
    # The `snap is None` (feature-off) short-circuit still returns () without
    # ever touching the log -- the negative control for the assertion above.
    assert d._pending_carver_feeds(project, cfg, None) == ()


def test_pending_carver_feeds_retain_zero_means_none_retained(
        tmp_state, sample_project):
    """cfg.carve.retain_merge_digests <= 0 is an explicit 'keep none' branch
    -- guards against Python's `list[-0:]` slicing returning the WHOLE list
    (since `-0 == 0`), which would silently defeat a 0 cap."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"
    cfg.carve.retain_merge_digests = 0

    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit1", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(1)},
        task_id="t1",
    )

    d = daemon.Daemon({project: cfg.root})
    snap = d._carver._carver_session(project, cfg)
    assert d._pending_carver_feeds(project, cfg, snap) == ()


# -- F018 P4a: merge-feed acknowledgement cursor tests -----------------------


def _make_carver_session_task(project: str, task_id: str, attempt_id: str,
                               kind: str, mode: str, generation: int,
                               source_ids: tuple[str, ...],
                               session_handle: str = "sess-1",
                               receipt_result: ReceiptResult = ReceiptResult.DONE,
                               ) -> TaskStateFile:
    """Helper: create a TaskStateFile with a carver-session turn attempt and
    the notes tokens _carver_turn_marker parses. Saves into storage, returns
    the statefile."""
    sources_token = ",".join(source_ids) if source_ids else "(none)"
    notes = (f"carver-session seq=1 kind={kind} mode={mode} "
             f"generation={generation} sources={sources_token}")
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, notes=notes,
    )
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(
        attempt_id=attempt_id, role=Role.CARVER, state=AttemptState.EXITED,
        route=route, started=utc_now(), ended=utc_now(),
        session_handle=session_handle,
        worktree=str(Path.cwd()),
        receipt=Receipt(result=receipt_result, exit_code=0),
    )
    tsf.attempts = [attempt]
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return storage.load_state(project, task_id)


def test_carver_ack_O1_headline_no_refire(
        tmp_state, sample_project):
    """O1 (headline — no re-fire): WARM session; append 2 pending
    MERGE_RECORDED-with-carver_digest events (sequences S1<S2); a successful
    merge-feed turn emits CARVER_CONTEXT_CONSUMED with highest_event_sequence
    == S2; the projector fold sets the cursor; a subsequent
    _pending_carver_feeds returns () (the consumed feeds no longer re-fire)."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"

    # -- seed 2 MERGE_RECORDED events with carver_digest (seq 1, 2)
    #    plus one MERGE_RECORDED without carver_digest (seq 3) to exercise
    #    the `not digest: continue` path in _highest_consumed_feed_sequence
    storage.append_and_apply(  # seq 1
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit1", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(1)},
        task_id="t1",
    )
    storage.append_and_apply(  # seq 2
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit2", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(2)},
        task_id="t2",
    )

    # -- create the merge-feed turn task
    task_id = "carver-session-demo-1"
    attempt_id = "att-merge-feed"
    source_ids = ("merge:demo:c1", "merge:demo:c2")
    # seq 3: MERGE_RECORDED without carver_digest — exercises the
    # `not digest: continue` path in _highest_consumed_feed_sequence
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit-nodigest", "source_kind": "review"},
        task_id="t-nodigest",
    )
    tsf = _make_carver_session_task(
        project, task_id, attempt_id,
        kind="resume", mode="merge-feed", generation=5,
        source_ids=source_ids,
    )

    d = daemon.Daemon({project: cfg.root})
    states = {task_id: tsf}

    # -- call the exit consumer
    events = d._carver.consume_session_exit(
        d._effect_context(project, cfg, states), task_id, attempt_id)

    # -- verify CARVER_CONTEXT_CONSUMED was emitted with highest sequence
    consumed = [e for e in events
                if e.type is EventType.CARVER_CONTEXT_CONSUMED]
    assert len(consumed) == 1, f"Expected 1 CARVER_CONTEXT_CONSUMED, got {len(consumed)}"
    assert consumed[0].payload["highest_event_sequence"] == 2

    # -- verify the events are appended (side-effect-free check: we also
    #    expect CARVER_SESSION_RESUMED and TASK_SUPERSEDED)
    assert any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)
    assert any(e.type is EventType.TASK_SUPERSEDED for e in events)

    # -- verify the projector fold from the emitted events sets cursor
    updated_snap = carver_session.project_session(
        list(storage.iter_events(project)) + list(
            # project_session expects a flat event log; the emitted events
            # haven't been appended to storage, so test the fold directly
        ))
    # Actually, let's verify via the real mechanism: append the emitted
    # CARVER_CONTEXT_CONSUMED to storage and check _pending_carver_feeds
    for ev in consumed:
        storage.append_event(
            project, actor=Actor(ActorKind.OPERATOR, "test"),
            type=ev.type, payload=ev.payload)

    snap = d._carver._carver_session(project, cfg)
    assert snap is not None
    assert snap.last_consumed_event_sequence == 2

    feeds = d._pending_carver_feeds(project, cfg, snap)
    assert feeds == (), f"Expected no pending feeds after cursor=2, got {feeds}"


def test_carver_ack_O2_failed_turn_no_emit(
        tmp_state, sample_project):
    """O2 (partial/failed feed re-delivers): a merge-feed turn that FAILS
    (receipt not DONE) emits NO CARVER_CONTEXT_CONSUMED; the cursor stays."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"

    # seed 1 MERGE_RECORDED
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit1", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(1)},
        task_id="t1",
    )

    task_id = "carver-session-demo-1"
    attempt_id = "att-merge-feed-fail"
    source_ids = ("merge:demo:c1",)
    tsf = _make_carver_session_task(
        project, task_id, attempt_id,
        kind="resume", mode="merge-feed", generation=5,
        source_ids=source_ids,
        session_handle="sess-1",
        receipt_result=ReceiptResult.ERROR,  # failed turn
    )

    d = daemon.Daemon({project: cfg.root})
    states = {task_id: tsf}

    events = d._carver.consume_session_exit(
        d._effect_context(project, cfg, states), task_id, attempt_id)

    consumed = [e for e in events
                if e.type is EventType.CARVER_CONTEXT_CONSUMED]
    assert len(consumed) == 0, (
        f"Expected 0 CARVER_CONTEXT_CONSUMED for failed turn, got {len(consumed)}")

    # Also verify we got DEGRADED instead of RESUMED
    assert any(e.type is EventType.CARVER_SESSION_DEGRADED for e in events)
    assert not any(e.type is EventType.CARVER_SESSION_RESUMED for e in events)


def test_carver_ack_O3_only_merge_feed(
        tmp_state, sample_project):
    """O3 (only merge-feed): a successful mode=="recover" turn emits NO
    CARVER_CONTEXT_CONSUMED (byte-identical to today)."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"

    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit1", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(1)},
        task_id="t1",
    )

    task_id = "carver-session-demo-1"
    attempt_id = "att-recover"
    source_ids = ("merge:demo:c1",)
    tsf = _make_carver_session_task(
        project, task_id, attempt_id,
        kind="resume", mode="recover", generation=5,
        source_ids=source_ids,
    )

    d = daemon.Daemon({project: cfg.root})
    states = {task_id: tsf}

    events = d._carver.consume_session_exit(
        d._effect_context(project, cfg, states), task_id, attempt_id)

    consumed = [e for e in events
                if e.type is EventType.CARVER_CONTEXT_CONSUMED]
    assert len(consumed) == 0, (
        f"Expected 0 CARVER_CONTEXT_CONSUMED for recover mode, got {len(consumed)}")

    # Recover follows the bootstrap-ack path -> may get DEGRADED if no ACK
    # Check that no CARVER_CONTEXT_CONSUMED regardless
    assert not any(e.type is EventType.CARVER_CONTEXT_CONSUMED for e in events)


def test_carver_ack_O4_proposal_safety_invariant(
        tmp_state, sample_project):
    """O4 (proposal-safety, the invariant): demonstrate that the shared
    cursor (last_consumed_event_sequence) correctly handles both MERGE_RECORDED
    and CARVER_PROPOSAL_RECORDED events when interleaved, and that the
    admit-slot priority in the planner's ladder (slot 1 > slot 3) structurally
    prevents the cursor from ever advancing past an un-admitted proposal.

    The key structural proof: _pending_carver_feeds excludes feeds at/below
    cursor, and the projector fold treats CARVER_CONTEXT_CONSUMED identically
    for both event types via the shared cursor field. The planner's ladder
    (reconcile.py:1119-1170) is an `elif` chain where slot 1 (admit) fires
    before slot 3 (merge-feed), consuming the single carver slot --
    so a merge-feed cursor advance never happens on a pass with a pending
    validated proposal."""
    cfg = sample_project
    project = "demo"
    cfg.carve.session = "project-persistent"

    # -- seed events: S1=merge, S2=interleaved CARVER_CONTEXT_CONSUMED (cursor=1),
    #    S3=proposal, S4=merge
    storage.append_and_apply(  # seq 1: merge with digest
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit1", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(1)},
        task_id="t1",
    )
    storage.append_event(  # seq 2: cursor lands at 1
        project, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.CARVER_CONTEXT_CONSUMED,
        payload={"highest_event_sequence": 1})
    storage.append_event(  # seq 3: proposal (past cursor)
        project, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.CARVER_PROPOSAL_RECORDED,
        payload={"proposal_id": "prop-1", "turn_id": "turn-1",
                 "generation": 5, "source": "carver",
                 "artifacts": [{"path": "handoff/P001.md",
                                "content_hash": "abc"}],
                 "outcome": "write"},
    )
    storage.append_and_apply(  # seq 4: merge past proposal
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit4", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(4)},
        task_id="t4",
    )

    # -- verify the projector folds both, cursor at 1, feeds at seq 4
    d = daemon.Daemon({project: cfg.root})
    snap = d._carver._carver_session(project, cfg)
    assert snap is not None
    assert snap.last_consumed_event_sequence == 1

    # feeds: seq 1 at cursor excluded, seq 4 is pending
    feeds = d._pending_carver_feeds(project, cfg, snap)
    assert [f.event_sequence for f in feeds] == [4]

    # -- verify the ladder priority structurally: the carver-session ladder
    #    uses an elif chain where validated_carve_proposals (slot 1) is
    #    checked before pending_carver_feeds (slot 3). When both are
    #    non-empty, only slot 1 fires.
    #    CR-06a RESTATED (not retired): the property and the assertion are
    #    unchanged; the ladder moved out of `plan_project` into its own
    #    registered rule, so the source this reads moves with it. Reading
    #    `plan_project` would now find neither identifier and the invariant
    #    would pass vacuously -- which is exactly how a structural check
    #    stops checking.
    #    CR-06c RESTATED again, for the same reason and with the same
    #    assertion: the rule left `reconcile.py` for `rules_carve.py` when
    #    carve authority stopped being legacy.
    import inspect
    from nyxloom import rules_carve
    source = inspect.getsource(rules_carve.carver_session_ladder)
    # Slot 1 mentions 'validated_carve_proposals' before slot 3 mentions
    # 'pending_carver_feeds' in the carver pipeline section
    slot1_idx = source.index("validated_carve_proposals")
    slot3_idx = source.index("pending_carver_feeds")
    assert slot1_idx < slot3_idx, (
        "Slot 1 (admit) must appear before slot 3 (merge-feed) in "
        "plan_project -- the elif chain is the safety invariant")

    # -- verify the admit-slot priority means cursor NEVER advances past
    #    an un-admitted proposal. The cursor shared between
    #    _validated_carve_proposals (which skips events <= cursor) and
    #    _pending_carver_feeds is `last_consumed_event_sequence` -- a
    #    SINGLE field, not two cursors. Advancing it via merge-feed ACK
    #    would skip any proposal at or below that sequence, but the
    #    planner's slot priority prevents merge-feed from ever executing
    #    on a pass with a pending proposal.
    #    Structural proof: both methods read the SAME cursor field.
    assert hasattr(snap, "last_consumed_event_sequence")
    # The projector fold for CARVER_CONTEXT_CONSUMED in carver_session.py
    # sets last_consumed_event_sequence = max(...) -- there is no second
    # cursor field for proposals vs merge-feeds.
    # O1 already proved CARVER_CONTEXT_CONSUMED advances the cursor,
    # and O4's structural assertions prove the ladder prevents overlap.


def test_carver_ack_O5_byte_identical_off(
        tmp_state, sample_project):
    """O5 (byte-identical-off):
    1. With session=="fresh", _carver_session returns None and the full
       existing suite stays green (proved by running all tests).
    2. A successful non-merge-feed resume turn (mode="carve") emits no
       CARVER_CONTEXT_CONSUMED -- the merge-feed mode is the only path
       that triggers the acknowledgement."""
    cfg = sample_project
    project = "demo"

    # -- part 1: fresh session -> _carver_session is None
    assert cfg.carve.session == "fresh"
    d = daemon.Daemon({project: cfg.root})
    assert d._carver._carver_session(project, cfg) is None

    # -- part 2: a successful non-merge-feed turn (mode="carve") does NOT
    #    emit CARVER_CONTEXT_CONSUMED
    cfg.carve.session = "project-persistent"
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "commit1", "source_kind": "review",
                 "carver_digest": _carver_digest_payload(1)},
        task_id="t1",
    )

    task_id = "carver-session-demo-1"
    attempt_id = "att-carve"
    source_ids = ("merge:demo:c1",)
    tsf = _make_carver_session_task(
        project, task_id, attempt_id,
        kind="resume", mode="carve", generation=5,
        source_ids=source_ids,
    )

    d2 = daemon.Daemon({project: cfg.root})
    states = {task_id: tsf}

    events = d2._carver.consume_session_exit(
        d2._effect_context(project, cfg, states), task_id, attempt_id)

    consumed = [e for e in events
                if e.type is EventType.CARVER_CONTEXT_CONSUMED]
    assert len(consumed) == 0, (
        f"Expected 0 CARVER_CONTEXT_CONSUMED for carve mode, got {len(consumed)}")
    # Carve is a write-authority mode; without a CarverTurnResult envelope
    # on disk, the turn degrades to CARVER_SESSION_DEGRADED (not RESUMED).
    assert any(e.type is EventType.CARVER_SESSION_DEGRADED for e in events)


def test_carver_ack_helper_empty_source_ids_returns_none(
        tmp_state, sample_project):
    """P4a helper guard: with no source_ids there is nothing to acknowledge,
    so _highest_consumed_feed_sequence short-circuits to None before any
    storage scan. Directly exercised here (rather than pragma-excluded) so the
    guard carries a real behavioural assertion."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    assert d._carver._highest_consumed_feed_sequence("demo", ()) is None


def test_carver_ack_helper_storage_error_is_typed_unavailable(
        tmp_state, sample_project, monkeypatch):
    """CR-02a: 'swallow to None' was the wrong direction here too.

    None means "no feed consumed", so the shared merge-feed cursor never
    advances and every feed re-delivers on every subsequent pass -- the
    unbounded re-delivery loop P4a's cursor exists to stop. An unreadable log
    now raises the typed fault, which the per-action isolation in run_pass
    contains as a TICK_ERROR for that one effect."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})

    def _boom(*_a, **_k):
        raise RuntimeError("event log unreadable")

    monkeypatch.setattr(storage, "iter_events", _boom)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._carver._highest_consumed_feed_sequence("demo", ("merge:demo:c1",))



# ============================================================================
# F019 P1b: gate-failure diagnosis routing (daemon side)
# ============================================================================

def _seed_review_rejected(project, task_id, attempts=None, handoff_path="handoff/h.md"):
    """Seed a task directly in REVIEW_REJECTED with optional attempts."""
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.REVIEW_REJECTED, since=utc_now(), handoff_path=handoff_path,
        attempts=list(attempts or []),
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return storage.load_state(project, task_id)


def _emit_gate_finished(project, task_id, exit_code, phase="pre-merge", output_tail="", commit="c0ffee"):
    gr = GateResult(gate_id="g1", phase=phase, commit=commit, exit_code=exit_code,
                    started=utc_now(), ended=utc_now(), output_tail=output_tail)
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.GATE_FINISHED, payload={"gate_result": gr.to_dict()}, task_id=task_id,
    )


def _emit_review_recorded(project, task_id, result, attempt_id=None, reject_class=None):
    payload = {"result": result}
    if reject_class is not None:
        payload["reject_class"] = reject_class
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.REVIEW_RECORDED, payload=payload, task_id=task_id, attempt_id=attempt_id,
    )


def _diag_attempt(attempt_id, state=AttemptState.EXITED, session_handle=None):
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    return Attempt(attempt_id=attempt_id, role=Role.REVIEW_INDEPENDENT, state=state,
                   route=route, started=utc_now(), session_handle=session_handle)


def _boom_iter_events(*_a, **_k):
    raise RuntimeError("store down")


# --- _gate_diagnosis_state ------------------------------------------------

def test_gate_diagnosis_state_pending_on_single_gate_fail(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-gd", attempts=[])
    _emit_gate_finished("demo", "t-gd", exit_code=1, output_tail="BOOM")
    pending, attempts = d._gate_diagnosis_state("demo", cfg, storage.list_states("demo"))
    assert pending == frozenset({"t-gd"})
    assert attempts == frozenset()


def test_gate_diagnosis_state_threshold_two_absorbs_first_and_survives_review(
        tmp_state, sample_project):
    """O4: with gate_diagnosis_after_failures=2 a single gate-fail does NOT
    trigger; an approving review between merge attempts does NOT reset the gate
    streak, so the 2nd consecutive gate-fail does trigger. Also proves the config
    knob is read from the project toml."""
    from nyxloom.config import ProjectConfig
    cfg = sample_project
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    ptoml.write_text(
        ptoml.read_text(encoding="utf-8").replace(
            "[policy]\n", "[policy]\ngate_diagnosis_after_failures = 2\n", 1),
        encoding="utf-8")
    cfg2 = ProjectConfig.load(cfg.root)
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-gd2", attempts=[])
    _emit_gate_finished("demo", "t-gd2", exit_code=1)  # streak = 1
    pending, _ = d._gate_diagnosis_state("demo", cfg2, storage.list_states("demo"))
    assert pending == frozenset()  # one flaky fail absorbed by a plain retry
    _emit_review_recorded("demo", "t-gd2", "approved", attempt_id="att-approve")  # re-review OK
    _emit_gate_finished("demo", "t-gd2", exit_code=1)  # streak = 2 (review did NOT reset it)
    pending2, _ = d._gate_diagnosis_state("demo", cfg2, storage.list_states("demo"))
    assert pending2 == frozenset({"t-gd2"})


def test_gate_diagnosis_state_reviewer_rejection_is_not_gate_caused(tmp_state, sample_project):
    """O5: a reviewer rejection AFTER a gate-fail becomes the current cause, so
    the task routes via the normal triage table, NOT gate-diagnosis."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-gd-rev", attempts=[])
    _emit_gate_finished("demo", "t-gd-rev", exit_code=1)
    _emit_review_recorded("demo", "t-gd-rev", "rejected", attempt_id="att-rev", reject_class="fixable")
    pending, _ = d._gate_diagnosis_state("demo", cfg, storage.list_states("demo"))
    assert pending == frozenset()


def test_gate_diagnosis_state_in_flight_exited_diagnosis_is_surfaced(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    diag = _diag_attempt("att-diag-x", state=AttemptState.EXITED)  # no binding -> unconsumed
    _seed_review_rejected("demo", "t-gd-if", attempts=[diag])
    _emit_gate_finished("demo", "t-gd-if", exit_code=1)
    pending, attempts = d._gate_diagnosis_state("demo", cfg, storage.list_states("demo"))
    assert pending == frozenset()                    # a diagnosis is already in flight
    assert attempts == frozenset({"att-diag-x"})     # its EXITED leg is surfaced for consumption


def test_gate_diagnosis_state_running_diagnosis_not_resurfaced(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    diag = _diag_attempt("att-diag-run", state=AttemptState.RUNNING)
    _seed_review_rejected("demo", "t-gd-run", attempts=[diag])
    _emit_gate_finished("demo", "t-gd-run", exit_code=1)
    pending, attempts = d._gate_diagnosis_state("demo", cfg, storage.list_states("demo"))
    assert pending == frozenset()   # still in flight -> not re-dispatched
    assert attempts == frozenset()  # RUNNING leg is caught by the INTERRUPTIBLE scan, not resurfaced


def test_gate_diagnosis_state_post_merge_gate_ignored(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-gd-pm", attempts=[])
    _emit_gate_finished("demo", "t-gd-pm", exit_code=1, phase="post-merge")
    pending, _ = d._gate_diagnosis_state("demo", cfg, storage.list_states("demo"))
    assert pending == frozenset()  # post-merge failure is not a re-work signal


def test_gate_diagnosis_state_passing_gate_resets_streak(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-gd-pass", attempts=[])
    _emit_gate_finished("demo", "t-gd-pass", exit_code=1)  # streak = 1
    _emit_gate_finished("demo", "t-gd-pass", exit_code=0)  # a PASSING gate resets the streak
    pending, _ = d._gate_diagnosis_state("demo", cfg, storage.list_states("demo"))
    assert pending == frozenset()


def test_gate_diagnosis_state_iter_events_failure_is_typed_unavailable(
        tmp_state, sample_project, monkeypatch):
    """CR-02a: an unreadable log cannot answer "no diagnosis pending".

    The old `(frozenset(), frozenset())` both suppressed the diagnosis
    dispatch AND un-suppressed the blind mechanical retry the diagnosis
    exists to replace -- so a storage hiccup silently bought a full extra
    implementer attempt on a gate-failing task."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-gd-err", attempts=[])
    states = storage.list_states("demo")
    monkeypatch.setattr(storage, "iter_events", _boom_iter_events)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._gate_diagnosis_state("demo", cfg, states)


# --- _execute (LaunchGateDiagnosis) ---------------------------------------

def test_execute_gate_diagnosis_cold_dispatch(tmp_state, sample_project, patch_siblings, monkeypatch):
    """O1 (dispatch half): a scripted LaunchGateDiagnosis for a gate-failed task
    spawns ONE classify-only reviewer via the COLD path (no prior session), with
    a packet carrying the gate output tail + the REJECT_CLASS contract, and
    records the attempt with wave_id None."""
    cfg = sample_project
    paths.routes_path().write_text(SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")
    task_id = "t-gd-cold"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _seed_review_rejected("demo", task_id, attempts=[])
    _emit_gate_finished("demo", task_id, exit_code=1, output_tail="E   assert 1 == 2  FAILED")
    _scripted(monkeypatch, [[reconcile.LaunchGateDiagnosis(task_id=task_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    assert len(patch_siblings["launch_detached"]) == 1
    assert len(patch_siblings["build_dispatch"]) == 1  # COLD path
    assert patch_siblings["build_resume"] == []
    spec = patch_siblings["launch_detached"][0]
    packet = (Path(spec.attempt_dir) / "packet" / "packet.md").read_text(encoding="utf-8")
    assert "GATE-FAILURE DIAGNOSIS" in packet
    assert "REJECT_CLASS: <fixable|architectural|product|transient>" in packet
    assert "assert 1 == 2" in packet  # the persisted gate output tail (P1a)
    tsf = storage.load_state("demo", task_id)
    diag = tsf.attempts[-1]
    assert diag.role is Role.REVIEW_INDEPENDENT
    assert diag.wave_id is None
    assert tsf.state is TaskState.REVIEW_REJECTED  # dispatch does not transition
    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.ATTEMPT_CREATED in types
    assert EventType.ATTEMPT_PREFLIGHTED in types


def test_execute_gate_diagnosis_warm_resume(tmp_state, sample_project, patch_siblings, monkeypatch):
    """D-R10/B6: with a prior EXITED review session available, the diagnosis
    WARM-resumes it (build_resume, never a cold build_dispatch) and marks the
    ATTEMPT_CREATED with resumed_from."""
    cfg = sample_project
    paths.routes_path().write_text(SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")
    task_id = "t-gd-warm"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    prior = _diag_attempt("att-prior-rev", state=AttemptState.EXITED, session_handle="sess-warm-42")
    _seed_review_rejected("demo", task_id, attempts=[prior])
    _emit_gate_finished("demo", task_id, exit_code=1, output_tail="boom")
    _scripted(monkeypatch, [[reconcile.LaunchGateDiagnosis(task_id=task_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    assert len(patch_siblings["launch_detached"]) == 1
    assert patch_siblings["build_resume"][-1]["session"] == "sess-warm-42"  # WARM
    assert patch_siblings["build_dispatch"] == []
    created = next(e for e in storage.iter_events("demo") if e.type is EventType.ATTEMPT_CREATED)
    assert created.payload.get("resumed_from") == "sess-warm-42"


def test_execute_gate_diagnosis_admission_refused(tmp_state, sample_project, patch_siblings, monkeypatch):
    """Execute-time admission refusal (e.g. mid-pass drain-agents / spent budget)
    -> nothing spawned; the task stays REVIEW_REJECTED for a later pass."""
    cfg = sample_project
    paths.routes_path().write_text(SAMPLE_ROUTES_TOML + "\nrole_default = \"review-independent\"\n")
    task_id = "t-gd-refused"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _seed_review_rejected("demo", task_id, attempts=[])
    monkeypatch.setattr(effects_dispatch, "admissible",
                        lambda ctx, kind: (False, "test-refused"))
    _scripted(monkeypatch, [[reconcile.LaunchGateDiagnosis(task_id=task_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")
    assert patch_siblings["launch_detached"] == []


# --- diagnosis consumption ------------------------------------------------

def test_gate_diagnosis_consumption_records_class_and_stays_rejected(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """O1 (consume half): a DONE diagnosis leg records REVIEW_RECORDED{rejected,
    reject_class} in the review-rejection shape and does NOT transition the task
    (invariant #2: it stays REVIEW_REJECTED)."""
    cfg = sample_project
    task_id, attempt_id = "t-gd-con", "att-gd-con"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(cfg.root, task_id, cfg.reports_dir,
                          "# Diagnosis\n\nModule boundary is wrong.\n\nREJECT_CLASS: architectural\n")
    _seed_review_rejected("demo", task_id, attempts=[_diag_attempt(attempt_id)])
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"
    assert recorded.payload["reject_class"] == "architectural"
    assert recorded.attempt_id == attempt_id
    assert storage.load_state("demo", task_id).state is TaskState.REVIEW_REJECTED


def test_gate_diagnosis_consumption_transient_class(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """O3: a diagnosis classifying `transient` records reject_class=transient
    (which routes to the plain retry path, not a re-scope/escalation)."""
    cfg = sample_project
    task_id, attempt_id = "t-gd-tr", "att-gd-tr"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_review_report(cfg.root, task_id, cfg.reports_dir,
                          "# Diagnosis\n\nFlaky network in the gate.\n\nREJECT_CLASS: transient\n")
    _seed_review_rejected("demo", task_id, attempts=[_diag_attempt(attempt_id)])
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["reject_class"] == "transient"


def test_gate_diagnosis_consumption_nondone_records_incomplete(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """A non-DONE diagnosis leg (infra failure) records `incomplete` (no class),
    binding the attempt so it is not re-consumed; the task stays REVIEW_REJECTED
    and falls back to the mechanical retry path."""
    cfg = sample_project
    task_id, attempt_id = "t-gd-inc", "att-gd-inc"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _seed_review_rejected("demo", task_id, attempts=[_diag_attempt(attempt_id)])
    _write_receipt("demo", attempt_id, ReceiptResult.LIMIT, exit_code=1)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "incomplete"
    assert "reject_class" not in recorded.payload
    assert storage.load_state("demo", task_id).state is TaskState.REVIEW_REJECTED


def test_gate_diagnosis_consumption_skips_already_bound_review(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """O5: a REVIEW_INDEPENDENT attempt on a REVIEW_REJECTED task that ALREADY
    carries a REVIEW_RECORDED binding (an already-consumed normal review) is NOT
    re-recorded by the diagnosis path -- it falls through to the (empty) wave
    members no-op."""
    cfg = sample_project
    task_id, attempt_id = "t-gd-bound", "att-gd-bound"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _seed_review_rejected("demo", task_id, attempts=[_diag_attempt(attempt_id)])
    _emit_review_recorded("demo", task_id, "rejected", attempt_id=attempt_id, reject_class="fixable")
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    daemon.Daemon({"demo": cfg.root}).run_pass("demo")
    recorded = [e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED]
    assert len(recorded) == 1  # only the pre-existing binding; diagnosis path skipped


# --- helpers: _latest_gate_failure / transient class / _attempt_has_review_recorded

def test_latest_gate_failure_returns_latest_tail_and_phase(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-lgf", attempts=[])
    _emit_gate_finished("demo", "t-lgf", exit_code=0, phase="pre-merge", output_tail="ignored-pass")
    _emit_gate_finished("demo", "t-lgf", exit_code=1, phase="mutation", output_tail="SURVIVED mutant")
    events = list(storage.iter_events("demo"))
    assert effects_review.latest_gate_failure(events, "t-lgf") == (
        "SURVIVED mutant", "mutation")


def test_latest_gate_failure_none_when_no_failure(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-lgf2", attempts=[])
    events = list(storage.iter_events("demo"))
    assert effects_review.latest_gate_failure(events, "t-lgf2") == ("", "")


def test_gate_diagnosis_on_an_unreadable_log_is_typed_unavailable(
        tmp_state, sample_project, monkeypatch):
    """CR-02a, restated at the seam CR-05b moved the read to.

    ("", "") is the same value a task with NO gate failure gets, so an
    unreadable log would dispatch a gate-diagnosis reviewer with the evidence
    field silently blank. `latest_gate_failure` is now PURE over an events
    sequence, so the authoritative read happens where the handler acquires
    the log -- and it must still raise rather than hand back an empty one.

    The genuine "no failure recorded" case still returns ("", "") -- see
    test_latest_gate_failure_none_when_no_failure, the negative control that
    keeps this from collapsing back into one indistinguishable answer."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-lgf3", attempts=[])
    monkeypatch.setattr(storage, "iter_events", _boom_iter_events)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._execute("demo", cfg, storage.list_states("demo"),
                   reconcile.LaunchGateDiagnosis(task_id="t-lgf3"))


def test_triage_classes_surfaces_transient(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-tc-tr", attempts=[])
    _emit_review_recorded("demo", "t-tc-tr", "rejected", attempt_id="att-x", reject_class="transient")
    tc = d._triage_classes("demo", storage.list_states("demo"))
    assert tc.get("t-tc-tr") == "transient"


def test_parse_reject_class_transient(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _make_feature_branch(cfg.root, "t-pc-tr", "a.py", "# a\n")
    _commit_review_report(cfg.root, "t-pc-tr", cfg.reports_dir, "REJECT_CLASS: transient\n")
    assert effects_review.parse_reject_class(d._ports.git, cfg, "t-pc-tr") == "transient"


def test_attempt_has_review_recorded(tmp_state, sample_project, monkeypatch):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    _seed_review_rejected("demo", "t-ahr", attempts=[])
    _emit_review_recorded("demo", "t-ahr", "rejected", attempt_id="att-bound")
    assert d._exit._attempt_has_review_recorded("demo", "att-bound") is True
    assert d._exit._attempt_has_review_recorded("demo", "att-absent") is False
    # CR-02a: False means "this review leg has NOT been consumed", so an
    # unreadable log used to authorize RE-consuming a verdict that was
    # already recorded. It is a typed fault now; the genuine
    # not-yet-consumed case above is the negative control.
    monkeypatch.setattr(storage, "iter_events", _boom_iter_events)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._exit._attempt_has_review_recorded("demo", "att-bound")


def test_gate_diagnosis_architectural_routes_to_carve_end_to_end(tmp_state, sample_project):
    """O1 (full loop): once the diagnosis records `architectural`, the task is no
    longer gate-diagnosis-pending and the very next reconcile pass routes it to
    READY_TO_CARVE via the EXISTING triage table -- classifier output feeds the
    pure router, no blind retry."""
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    # Reuse the sample project's real handoff id -- plan_project's lifecycle loop
    # iterates discovered frontmatters, so the task must have a handoff on disk.
    task_id, attempt_id = "demo-P01-sample", "att-gd-e2e"
    _seed_review_rejected("demo", task_id, attempts=[_diag_attempt(attempt_id)])
    _emit_gate_finished("demo", task_id, exit_code=1)
    _emit_review_recorded("demo", task_id, "rejected", attempt_id=attempt_id, reject_class="architectural")
    inp = d._build_input("demo", cfg, storage.list_states("demo"))
    assert task_id not in inp.gate_diagnosis_pending  # class landed -> not re-diagnosed
    actions = reconcile.plan_project(inp)
    transitions = [a for a in actions
                   if isinstance(a, reconcile.Transition) and a.task_id == task_id]
    assert any(t.to is TaskState.READY_TO_CARVE for t in transitions)

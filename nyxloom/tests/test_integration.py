"""End-to-end integration: the full carve -> queue -> dispatch -> wrapper ->
collect -> AWAITING_REVIEW loop through the real daemon pass, real wrapper,
real adapters, and a fake CLI on PATH.

Regression anchor for the 2026-07-15 E2E finding: the wrapper emitted its own
ATTEMPT_EXITED, and no component performed the TASK transition (the planner
only collected receipts for RUNNING attempts), leaving the task stuck ACTIVE.
Also covers the ATTEMPT_PREFLIGHTED/ATTEMPT_STARTED event race via the
storage-level monotonic upsert guard.
"""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from nyxloom import paths, storage
from nyxloom.daemon import run_once
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Role, Route,
    TaskState, TaskStateFile, utc_now,
)


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


@pytest.fixture()
def e2e_project(sample_project, tmp_path, monkeypatch):
    """sample_project upgraded to lint-clean + a 'fake' CLI on PATH."""
    cfg = sample_project
    (cfg.root / "handoff" / "demo-P01-sample.md").write_text(CLEAN_HANDOFF)
    (cfg.root / "docs" / "ROADMAP.md").write_text("# Roadmap\n- R1 sample\n")
    (cfg.root / "src" / "demo").mkdir(parents=True, exist_ok=True)
    (cfg.root / "src" / "demo" / "core.py").write_text("# frozen\n")

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake = fakebin / "fake"
    fake.write_text("#!/bin/sh\necho 'fake agent run OK'\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fakebin}:{os.environ['PATH']}")
    return cfg


def _wait(predicate, timeout=15.0, step=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def test_full_dispatch_cycle_reaches_awaiting_review(e2e_project):
    # Pass 1: discover handoff -> CARVED. Pass 2: lint-clean -> QUEUED.
    # Pass 3: dispatch (worktree + wrapper + fake CLI).
    for _ in range(3):
        run_once("demo")

    tsf = storage.load_state("demo", "demo-P01-sample")
    assert tsf is not None and tsf.state == TaskState.ACTIVE
    att = tsf.attempts[-1]

    # The wrapper runs detached; wait for its receipt.
    receipt_path = paths.attempt_dir("demo", att.attempt_id) / "receipt.json"
    assert _wait(receipt_path.exists), "wrapper never wrote a receipt"
    assert json.loads(receipt_path.read_text())["result"] == "done"

    # Wait for the wrapper's own ATTEMPT_EXITED to land (the regression
    # scenario requires the attempt to already be EXITED at collect time).
    assert _wait(lambda: storage.load_state("demo", "demo-P01-sample")
                 .attempt_by_id(att.attempt_id).state == AttemptState.EXITED)

    # Collect pass: MUST transition the task even though the attempt is
    # already terminal (the 2026-07-15 stuck-ACTIVE regression).
    run_once("demo")
    tsf = storage.load_state("demo", "demo-P01-sample")
    assert tsf.state == TaskState.AWAITING_REVIEW

    # Exactly one ATTEMPT_EXITED (idempotent healing, no duplicate emit).
    exits = [e for e in storage.iter_events("demo")
             if e.type == EventType.ATTEMPT_EXITED and e.attempt_id == att.attempt_id]
    assert len(exits) == 1

    # A further pass is a no-op for this task (idempotence).
    run_once("demo")
    assert storage.load_state("demo", "demo-P01-sample").state == TaskState.AWAITING_REVIEW


def test_attempt_upsert_never_regresses_terminal(sample_project, tmp_state):
    states: dict[str, TaskStateFile] = {}
    actor = Actor(ActorKind.TICK, "test")
    tsf = TaskStateFile(schema_version=1, task_id="t1", project="demo",
                        state=TaskState.ACTIVE, since=utc_now())
    storage.append_and_apply("demo", states, actor=actor,
                             type=EventType.TASK_CREATED,
                             payload={"statefile": tsf.to_dict()}, task_id="t1")
    route = Route(route_id="fake-cli", cli="fake", model="fake-model")
    att = Attempt(attempt_id="att-1", role=Role.IMPLEMENTER,
                  state=AttemptState.CREATED, route=route, started=utc_now())
    storage.append_and_apply("demo", states, actor=actor,
                             type=EventType.ATTEMPT_CREATED,
                             payload={"attempt": att.to_dict()}, task_id="t1",
                             attempt_id="att-1")
    # Wrapper wins the race: EXITED lands first.
    att.state = AttemptState.EXITED
    storage.append_and_apply("demo", states, actor=actor,
                             type=EventType.ATTEMPT_EXITED,
                             payload={"attempt": att.to_dict()}, task_id="t1",
                             attempt_id="att-1")
    # Daemon's stale PREFLIGHTED arrives late: must be ignored.
    att.state = AttemptState.PREFLIGHTING
    storage.append_and_apply("demo", states, actor=actor,
                             type=EventType.ATTEMPT_PREFLIGHTED,
                             payload={"attempt": att.to_dict()}, task_id="t1",
                             attempt_id="att-1")
    assert states["t1"].attempt_by_id("att-1").state == AttemptState.EXITED
    # And replay agrees (the guard is part of the projection rule).
    assert storage.replay("demo")["t1"].attempt_by_id("att-1").state == AttemptState.EXITED
    # Legitimate backward edge still works: STALLED -> RUNNING.
    att2 = Attempt(attempt_id="att-2", role=Role.IMPLEMENTER,
                   state=AttemptState.STALLED, route=route, started=utc_now())
    storage.append_and_apply("demo", states, actor=actor,
                             type=EventType.ATTEMPT_CREATED,
                             payload={"attempt": att2.to_dict()}, task_id="t1",
                             attempt_id="att-2")
    att2.state = AttemptState.RUNNING
    storage.append_and_apply("demo", states, actor=actor,
                             type=EventType.ATTEMPT_RESUMED,
                             payload={"attempt": att2.to_dict()}, task_id="t1",
                             attempt_id="att-2")
    assert states["t1"].attempt_by_id("att-2").state == AttemptState.RUNNING

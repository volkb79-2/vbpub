"""Post-merge validation pipeline (nyxloom-post-merge-validation package,
2026-07-17). PROVES: TaskState.COMPLETED -- the terminal SUCCESS state --
is reachable, closing the gap pinned by tests/test_invariants.py's (now
removed) MERGED/VALIDATING xfail markers.

Deliberately self-contained (does not import tests/test_daemon.py's helpers)
per this suite's own convention (see test_invariants.py's module docstring):
zero cross-file collection-order coupling.
"""

from __future__ import annotations

import dataclasses

import pytest

from nyxloom import config, daemon, lint, notify, reconcile, render, storage
from nyxloom.config import GateDef
from nyxloom.types import (
    Actor, ActorKind, BlockerType, EventType, TaskState, TaskStateFile, utc_now,
)


@pytest.fixture()
def patch_siblings(monkeypatch):
    """Local, minimal counterpart of test_daemon.py's fixture of the same
    name (test_daemon.py's own version is a LOCAL fixture there too, per
    this suite's zero-cross-file-coupling convention -- see module
    docstring). Only stubs the seams these tests actually exercise: no
    task here ever reaches DispatchImplementer/LaunchReview, so adapters.*
    and wrapper.launch_detached are left unpatched (unused)."""
    monkeypatch.setattr(render, "render_after_event", lambda registry: None)
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})


def _seed_task(project: str, task_id: str, state: TaskState,
                handoff_path: str | None = None, merge_commit: str | None = None) -> TaskStateFile:
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.CARVED, since=utc_now(), handoff_path=handoff_path,
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    cur = storage.load_state(project, task_id)
    cur.state = state
    if merge_commit is not None:
        cur.merge_commit = merge_commit
    storage.save_state(cur)
    return cur


def _scripted(monkeypatch, sequence):
    seq = list(sequence)

    def fake(inp):
        return seq.pop(0) if seq else []

    monkeypatch.setattr(reconcile, "plan_project", fake)


def _freeze_cfg(monkeypatch, cfg) -> None:
    """Daemon.run_pass reloads ProjectConfig.load(root) from disk every
    pass; freeze it to a specific (possibly gates-overridden) cfg object so
    tests can exercise gate variants sample_project's on-disk toml doesn't
    declare, without hand-rolling a second git-repo fixture."""
    monkeypatch.setattr(config.ProjectConfig, "load", classmethod(lambda cls, root: cfg))


# ---------------------------------------------------------------------------
# End-to-end: MERGED -> VALIDATING -> COMPLETED, real plan_project (no
# _scripted mocking) -- the strongest proof the pipeline is genuinely wired,
# not just that a hand-fed action gets executed.

def test_merged_task_reaches_completed_via_real_passing_gate(
        tmp_state, sample_project, patch_siblings):
    """sample_project's own declared gate (`[gates.pytest-q] argv = ["true"]`,
    phase 'implementation' -- no project in this codebase declares a
    dedicated 'post-merge' phase gate, so this also proves the documented
    fallback: re-run the implementation gate as the post-merge check)."""
    cfg = sample_project
    task_id = "demo-P01-sample"
    _seed_task("demo", task_id, TaskState.MERGED,
               handoff_path="handoff/demo-P01-sample.md", merge_commit="deadbeef0001")
    d = daemon.Daemon({"demo": cfg.root})

    n1 = d.run_pass("demo")
    assert n1 == 1
    tsf1 = storage.load_state("demo", task_id)
    assert tsf1.state is TaskState.VALIDATING

    n2 = d.run_pass("demo")
    assert n2 == 1
    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.COMPLETED

    assert len(tsf2.gate_results) == 1
    gr = tsf2.gate_results[0]
    assert gr.gate_id == "pytest-q"
    assert gr.phase == "post-merge"
    assert gr.exit_code == 0
    assert gr.commit == "deadbeef0001"

    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.GATE_FINISHED in types
    assert EventType.TASK_BLOCKED not in types


# ---------------------------------------------------------------------------
# Failing gate -> BLOCKED (typed CONTRACT blocker)

def test_validating_task_blocks_on_failing_gate(tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    task_id = "demo-P01-sample"
    _seed_task("demo", task_id, TaskState.VALIDATING,
               handoff_path="handoff/demo-P01-sample.md", merge_commit="badc0de00002")

    failing_cfg = dataclasses.replace(cfg, gates={
        "pytest-q": GateDef(gate_id="pytest-q", argv=["false"], phase="implementation",
                             timeout_seconds=10, environment="local"),
    })
    _freeze_cfg(monkeypatch, failing_cfg)
    _scripted(monkeypatch, [[reconcile.RunPostMergeGate(task_id=task_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    n = d.run_pass("demo")
    assert n == 1

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.BLOCKED
    assert tsf.blocker is not None
    assert tsf.blocker.type is BlockerType.CONTRACT

    assert len(tsf.gate_results) == 1
    gr = tsf.gate_results[0]
    assert gr.gate_id == "pytest-q"
    assert gr.phase == "post-merge"
    assert gr.exit_code != 0
    assert gr.commit == "badc0de00002"

    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.GATE_FINISHED in types
    assert EventType.TASK_BLOCKED in types


# ---------------------------------------------------------------------------
# No gate declared at all -> documented default: no-op-validated COMPLETED

def test_validating_task_completes_as_noop_when_no_gate_declared(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    task_id = "demo-P01-sample"
    _seed_task("demo", task_id, TaskState.VALIDATING,
               handoff_path="handoff/demo-P01-sample.md", merge_commit="c0ffee000003")

    no_gate_cfg = dataclasses.replace(cfg, gates={})
    _freeze_cfg(monkeypatch, no_gate_cfg)
    _scripted(monkeypatch, [[reconcile.RunPostMergeGate(task_id=task_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    n = d.run_pass("demo")
    assert n == 1

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.COMPLETED
    assert tsf.gate_results == []

    types = [e.type for e in storage.iter_events("demo")]
    assert EventType.GATE_FINISHED not in types
    assert EventType.TASK_TRANSITIONED in types

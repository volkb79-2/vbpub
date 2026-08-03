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


def _join_and_drain(d: "daemon.Daemon", task_id: str) -> None:
    """B3-followon 2026-07-26: post-merge gates DISPATCH onto a background
    thread and only settle on a later drain (see daemon.py's
    _run_post_merge_gate / _run_post_merge_gate_bg /
    _drain_post_merge_gate_results). Every test below scripts plan_project
    to a single RunPostMergeGate action (_scripted pops [] for any call
    beyond it), so a second `run_pass` after joining the started thread is
    a safe, deterministic drain -- unlike a REAL (unscripted) plan_project,
    it cannot re-plan (and re-dispatch) a second gate run for the
    still-VALIDATING task (see
    test_merged_task_reaches_completed_via_real_passing_gate's docstring
    for that race and why it drains directly instead)."""
    t = d._gates.post_merge_running.get(f"post-merge-gate:{task_id}")
    assert t is not None, "RunPostMergeGate did not start a background thread"
    t.join(timeout=10)
    assert not t.is_alive()
    d.run_pass("demo")


# ---------------------------------------------------------------------------
# End-to-end: MERGED -> VALIDATING -> COMPLETED, real plan_project (no
# _scripted mocking) -- the strongest proof the pipeline is genuinely wired,
# not just that a hand-fed action gets executed.

def test_merged_task_reaches_completed_via_real_passing_gate(
        tmp_state, sample_project, patch_siblings):
    """sample_project's own declared gate (`[gates.pytest-q] argv = ["true"]`,
    phase 'implementation' -- no project in this codebase declares a
    dedicated 'post-merge' phase gate, so this also proves the documented
    fallback: re-run the implementation gate as the post-merge check).

    B3-followon 2026-07-26: post-merge gates now DISPATCH onto a background
    thread and only settle on a later drain (see daemon.py's
    _run_post_merge_gate / _run_post_merge_gate_bg /
    _drain_post_merge_gate_results). This test uses REAL (unscripted)
    plan_project -- deliberately, per its own docstring, to prove the
    pipeline is genuinely wired -- so a naive third `run_pass` after joining
    the thread would re-evaluate the still-VALIDATING task and dispatch a
    SECOND, unwanted gate run before this pass's own drain settles the
    first (plan_project has no way to know a result is already queued).
    Draining directly sidesteps that replanning race entirely."""
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
    # n2 only DISPATCHED the gate -- still VALIDATING until drained.
    tsf_mid = storage.load_state("demo", task_id)
    assert tsf_mid.state is TaskState.VALIDATING

    t = d._gates.post_merge_running.get(f"post-merge-gate:{task_id}")
    assert t is not None, "RunPostMergeGate did not start a background thread"
    t.join(timeout=10)
    assert not t.is_alive()

    d._gates.drain_post_merge(d._effect_context("demo", cfg, storage.list_states("demo")))

    tsf2 = storage.load_state("demo", task_id)
    assert tsf2.state is TaskState.COMPLETED
    assert f"post-merge-gate:{task_id}" not in d._gates.post_merge_running

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
    _join_and_drain(d, task_id)

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.BLOCKED
    assert tsf.blocker is not None
    # P64 2026-07-20 (A12, M16): a post-merge GATE failure is an ENVIRONMENT
    # failure (the merged tree failed its own tests), NOT a CONTRACT/
    # underspecified-handoff. Typed CONTRACT it inflated
    # blocked_underspecified_count and could trip
    # SpecAttention('blocked-underspecified'); ENVIRONMENT keeps it out of
    # that counter (see _history + test_history_environment_blocker_...).
    assert tsf.blocker.type is BlockerType.ENVIRONMENT

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


# ---------------------------------------------------------------------------
# F (factory-hardening): a failing post-merge gate AUTO-REVERTS the published
# tree (CAS update-ref back to the merge commit's parent), not just BLOCKS it.

def _run_git(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _real_merge_on_main(root) -> tuple[str, str]:
    """Advance `root`'s main to a real --no-ff merge commit; return
    (pre_merge_main, merge_commit) where merge_commit^1 == pre_merge_main."""
    before = _run_git(root, "rev-parse", "main").stdout.strip()
    assert _run_git(root, "checkout", "-b", "feat/revert-me").returncode == 0
    (root / "revert_me.txt").write_text("payload\n", encoding="utf-8")
    assert _run_git(root, "add", "revert_me.txt").returncode == 0
    assert _run_git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "feat: revert-me").returncode == 0
    assert _run_git(root, "checkout", "main").returncode == 0
    assert _run_git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                    "merge", "--no-ff", "feat/revert-me", "-m", "merge feat/revert-me").returncode == 0
    merge_commit = _run_git(root, "rev-parse", "main").stdout.strip()
    assert merge_commit != before
    return before, merge_commit


def _failing_gate_cfg(cfg, **policy_overrides):
    gates = {"pytest-q": GateDef(gate_id="pytest-q", argv=["false"],
                                 phase="implementation", timeout_seconds=10, environment="local")}
    if policy_overrides:
        return dataclasses.replace(cfg, gates=gates,
                                   policy=dataclasses.replace(cfg.policy, **policy_overrides))
    return dataclasses.replace(cfg, gates=gates)


def test_failing_post_merge_gate_auto_reverts_main(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """A gate failure on the PUBLISHED tree CAS-reverts <default_branch> back to
    the merge commit's parent, emits MERGE_REVERTED, and still BLOCKS."""
    cfg = sample_project
    task_id = "demo-P01-sample"
    before, merge_commit = _real_merge_on_main(cfg.root)
    _seed_task("demo", task_id, TaskState.VALIDATING,
               handoff_path="handoff/demo-P01-sample.md", merge_commit=merge_commit)
    _freeze_cfg(monkeypatch, _failing_gate_cfg(cfg))
    _scripted(monkeypatch, [[reconcile.RunPostMergeGate(task_id=task_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    assert d.run_pass("demo") == 1
    _join_and_drain(d, task_id)

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.BLOCKED
    # main was healed back to the pre-merge commit
    assert _run_git(cfg.root, "rev-parse", "main").stdout.strip() == before

    ev = list(storage.iter_events("demo"))
    reverts = [e for e in ev if e.type is EventType.MERGE_REVERTED]
    assert len(reverts) == 1
    assert reverts[0].payload["from"] == merge_commit
    assert reverts[0].payload["to"] == before
    assert EventType.TASK_BLOCKED in [e.type for e in ev]


def test_auto_revert_skipped_when_policy_disabled(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """auto_revert_failed_merge=False -> BLOCKED but main left advanced, no
    MERGE_REVERTED."""
    cfg = sample_project
    task_id = "demo-P01-sample"
    before, merge_commit = _real_merge_on_main(cfg.root)
    _seed_task("demo", task_id, TaskState.VALIDATING,
               handoff_path="handoff/demo-P01-sample.md", merge_commit=merge_commit)
    _freeze_cfg(monkeypatch, _failing_gate_cfg(cfg, auto_revert_failed_merge=False))
    _scripted(monkeypatch, [[reconcile.RunPostMergeGate(task_id=task_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    assert d.run_pass("demo") == 1
    _join_and_drain(d, task_id)

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.BLOCKED
    assert _run_git(cfg.root, "rev-parse", "main").stdout.strip() == merge_commit  # NOT reverted
    assert EventType.MERGE_REVERTED not in [e.type for e in storage.iter_events("demo")]


def test_auto_revert_cas_refused_when_main_advanced(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """If <default_branch> moved PAST the merge commit, the CAS update-ref is
    refused -- main is NOT force-reverted, no MERGE_REVERTED, still BLOCKED."""
    cfg = sample_project
    task_id = "demo-P01-sample"
    before, merge_commit = _real_merge_on_main(cfg.root)
    # advance main one commit past the merge commit
    (cfg.root / "later.txt").write_text("later\n", encoding="utf-8")
    assert _run_git(cfg.root, "add", "later.txt").returncode == 0
    assert _run_git(cfg.root, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "later commit").returncode == 0
    main_now = _run_git(cfg.root, "rev-parse", "main").stdout.strip()
    assert main_now != merge_commit

    _seed_task("demo", task_id, TaskState.VALIDATING,
               handoff_path="handoff/demo-P01-sample.md", merge_commit=merge_commit)
    _freeze_cfg(monkeypatch, _failing_gate_cfg(cfg))
    _scripted(monkeypatch, [[reconcile.RunPostMergeGate(task_id=task_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    assert d.run_pass("demo") == 1
    _join_and_drain(d, task_id)

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.BLOCKED
    assert _run_git(cfg.root, "rev-parse", "main").stdout.strip() == main_now  # unchanged
    assert EventType.MERGE_REVERTED not in [e.type for e in storage.iter_events("demo")]

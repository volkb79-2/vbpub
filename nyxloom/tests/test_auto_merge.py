"""Guarded-automatic merge (P48 2026-07-19). PROVES: a MERGE_READY task under
policy.merge_mode == 'guarded-automatic' gets a REAL `git merge --no-ff`
executed by the daemon -- genuine 3-way conflict detection via a disposable
scratch worktree, never the surgical commit-tree technique an operator uses
by hand (which has no conflict detection at all, acceptable only under human
supervision).

Deliberately self-contained (does not import tests/test_daemon.py's or
tests/test_post_merge.py's helpers) per this suite's established zero-cross-
file-coupling convention (see test_post_merge.py's own module docstring).
"""

from __future__ import annotations

import dataclasses
import subprocess

import pytest

from nyxloom import config, daemon, lint, notify, render, storage
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Role, Route,
    TaskState, TaskStateFile, utc_now,
)


@pytest.fixture()
def patch_siblings(monkeypatch):
    """No task here ever reaches DispatchImplementer/LaunchReview/carve
    dispatch -- only the MERGE_READY -> AutoMergeTask path is exercised --
    so adapters.*/wrapper.launch_detached are left unpatched (unused)."""
    monkeypatch.setattr(render, "render_after_event", lambda registry: None)
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})


def _freeze_cfg(monkeypatch, cfg) -> None:
    """Daemon.run_pass reloads ProjectConfig.load(root) from disk every
    pass; freeze it to a policy-overridden cfg object (mirrors
    test_post_merge.py's own helper of the same name/purpose)."""
    monkeypatch.setattr(config.ProjectConfig, "load", classmethod(lambda cls, root: cfg))


def _run(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _make_branch_with_file(root, branch: str, filename: str, content: str) -> None:
    """Branch off the CURRENT HEAD, add one file, commit, return to main --
    leaves root's working tree back on main, matching what _execute_auto_merge
    expects to find (a checkout it must not otherwise disturb)."""
    assert _run(root, "checkout", "-b", branch).returncode == 0
    (root / filename).write_text(content, encoding="utf-8")
    assert _run(root, "add", filename).returncode == 0
    assert _run(root, "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", f"add {filename} on {branch}").returncode == 0
    assert _run(root, "checkout", "main").returncode == 0


def _write_handoff(root, task_id: str) -> str:
    """A real handoff/*.md matching handoff_globs (SAMPLE_PROJECT_TOML), so
    the real disk-scanned frontmatters dict plan_project iterates actually
    contains this task_id -- a seeded statefile with handoff_path=None is
    invisible to the real scan (only synthetic carve/review-wave tasks are
    handled outside that loop)."""
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


def _seed_merge_ready(root, project: str, task_id: str, branch: str) -> TaskStateFile:
    handoff_path = _write_handoff(root, task_id)
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.CARVED, since=utc_now(), handoff_path=handoff_path,
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    cur = storage.load_state(project, task_id)
    cur.state = TaskState.MERGE_READY
    cur.attempts = [Attempt(
        attempt_id="att-impl", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=utc_now(), branch=branch,
    )]
    storage.save_state(cur)
    return cur


def test_clean_merge_advances_main_and_transitions_to_merged(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    cfg = dataclasses.replace(cfg, policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)

    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    _make_branch_with_file(cfg.root, "feat/demo-P99", "new_thing.txt", "hello\n")

    _seed_merge_ready(cfg.root, "demo", "demo-P99", "feat/demo-P99")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P99")
    assert tsf.state is TaskState.MERGED
    assert tsf.merge_commit is not None

    after_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    assert after_main != before_main
    assert after_main == tsf.merge_commit

    parents = _run(cfg.root, "log", "-1", "--format=%P", after_main).stdout.split()
    assert len(parents) == 2, "must be a real merge commit (two parents), not a fast-forward/graft"

    # P63 (M13): the merge COMMIT's tree carries the branch's file ...
    assert _run(cfg.root, "show", f"{after_main}:new_thing.txt").stdout == "hello\n"
    # ... but the daemon does NOT materialize it into the live working tree
    # (nyxloom self-hosts in the operator's checkout; code updates via redeploy)
    assert not (cfg.root / "new_thing.txt").exists()

    events = list(storage.iter_events("demo"))
    assert any(e.type is EventType.MERGE_RECORDED and e.task_id == "demo-P99" for e in events)


def test_real_conflict_escalates_needs_operator_leaves_merge_ready_main_untouched(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    cfg = sample_project
    cfg = dataclasses.replace(cfg, policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)

    # branch modifies the SAME already-tracked file the base commit created
    _make_branch_with_file(cfg.root, "feat/demo-P98", "handoff/demo-P01-sample.md",
                            "branch version\n")
    # main then diverges on the SAME file, guaranteeing a real textual conflict
    (cfg.root / "handoff" / "demo-P01-sample.md").write_text("main version\n", encoding="utf-8")
    assert _run(cfg.root, "add", "handoff/demo-P01-sample.md").returncode == 0
    assert _run(cfg.root, "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "diverge on main").returncode == 0

    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()

    _seed_merge_ready(cfg.root, "demo", "demo-P98", "feat/demo-P98")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P98")
    assert tsf.state is TaskState.MERGE_READY, "must NOT silently clobber -- stays put for an operator"
    assert tsf.merge_commit is None

    after_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    assert after_main == before_main, "main must be completely untouched by a failed auto-merge"

    events = list(storage.iter_events("demo"))
    needs_op = [e for e in events if e.type is EventType.NEEDS_OPERATOR and e.task_id == "demo-P98"]
    assert len(needs_op) == 1
    assert needs_op[0].payload.get("reason") == "auto-merge-conflict"

    # no leftover scratch worktree
    wt_list = _run(cfg.root, "worktree", "list").stdout
    assert "automerge-demo-P98" not in wt_list


def test_manual_mode_never_plans_auto_merge_even_when_merge_ready(
        tmp_state, sample_project, patch_siblings):
    """Regression pin: policy.merge_mode defaults to 'manual' in
    sample_project's own on-disk toml (untouched by this test) -- a
    MERGE_READY task must sit completely inert, byte-for-byte the same
    pre-P48 behavior."""
    cfg = sample_project
    _make_branch_with_file(cfg.root, "feat/demo-P97", "untouched.txt", "x\n")
    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()

    _seed_merge_ready(cfg.root, "demo", "demo-P97", "feat/demo-P97")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P97")
    assert tsf.state is TaskState.MERGE_READY
    assert _run(cfg.root, "rev-parse", "main").stdout.strip() == before_main


def test_auto_merge_does_not_clobber_uncommitted_operator_edits(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """P63 2026-07-20 (M13). nyxloom self-hosts inside the operator's live
    checkout. The old auto-merge ran `git checkout <default> -- <changed>` in
    the repo root, silently OVERWRITING the operator's uncommitted edits to any
    merged file. The daemon now only advances the merge REF and never touches
    the live working tree -- verified here by an uncommitted edit surviving a
    merge that changed the very same file."""
    cfg = sample_project
    cfg = dataclasses.replace(cfg, policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)
    tracked = "handoff/demo-P01-sample.md"   # a file sample_project tracks
    _make_branch_with_file(cfg.root, "feat/demo-P77", tracked, "BRANCH VERSION\n")
    # the operator has an UNCOMMITTED edit to the same file in the live checkout
    (cfg.root / tracked).write_text("OPERATOR UNCOMMITTED EDIT\n", encoding="utf-8")

    _seed_merge_ready(cfg.root, "demo", "demo-P77", "feat/demo-P77")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    # the merge REF advanced (task MERGED) ...
    assert storage.load_state("demo", "demo-P77").state is TaskState.MERGED
    # ... but the operator's uncommitted edit SURVIVES (was clobbered before P63)
    assert (cfg.root / tracked).read_text(encoding="utf-8") == "OPERATOR UNCOMMITTED EDIT\n"


def test_post_merge_gate_runs_on_merge_commit_not_live_checkout(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """P63 (M13). The post-merge gate must validate the MERGE COMMIT's tree,
    not the operator's live checkout -- which no longer contains the merged
    files (auto-merge stopped materializing them). A gate that requires a
    merge-only file passes only when run at the merge commit (in the scratch
    worktree), and fails if run against the live root (as it did before)."""
    gate = config.GateDef(
        gate_id="check-merged-file",
        argv=["test", "-f", "{worktree}/new_thing.txt"],
        phase="post-merge", timeout_seconds=30, environment="local")
    cfg = sample_project
    cfg = dataclasses.replace(
        cfg, gates={"check-merged-file": gate},
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)
    _make_branch_with_file(cfg.root, "feat/demo-P78", "new_thing.txt", "hi\n")
    # the live checkout does NOT have new_thing.txt (no materialize, post-P63)
    _seed_merge_ready(cfg.root, "demo", "demo-P78", "feat/demo-P78")

    d = daemon.Daemon({"demo": cfg.root})
    for _ in range(4):   # MERGE_READY->MERGED->VALIDATING->gate->COMPLETED
        d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P78")
    assert tsf.state is TaskState.COMPLETED     # gate saw new_thing.txt at the merge commit
    assert not (cfg.root / "new_thing.txt").exists()   # live checkout untouched
    # the scratch worktree was cleaned up
    assert not (cfg.root / ".worktrees" / "postmerge-demo-P78").exists()

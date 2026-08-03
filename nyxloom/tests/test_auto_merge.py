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

from nyxloom import config, daemon, effects_gates, lint, notify, render, storage
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
    worktree), and fails if run against the live root (as it did before).

    B3-followon 2026-07-26: post-merge gates now DISPATCH onto a background
    thread and only settle on a later drain (see daemon.py's
    _run_post_merge_gate / _run_post_merge_gate_bg /
    _drain_post_merge_gate_results). This test uses REAL (unscripted)
    plan_project, so a naive 5th run_pass to "give the drain one more pass"
    would re-evaluate the still-VALIDATING task and dispatch a SECOND,
    unwanted gate run before the queued first result gets drained (see
    tests/test_post_merge.py's
    test_merged_task_reaches_completed_via_real_passing_gate docstring for
    the same race, and why draining directly sidesteps it)."""
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
    for _ in range(3):   # MERGE_READY->MERGED->VALIDATING->gate DISPATCHED
        d.run_pass("demo")

    tsf_mid = storage.load_state("demo", "demo-P78")
    assert tsf_mid.state is TaskState.VALIDATING   # gate only dispatched so far

    t = d._gates.post_merge_running.get("post-merge-gate:demo-P78")
    assert t is not None, "RunPostMergeGate did not start a background thread"
    t.join(timeout=10)
    assert not t.is_alive()

    d._gates.drain_post_merge(d._effect_context("demo", cfg, storage.list_states("demo")))

    tsf = storage.load_state("demo", "demo-P78")
    assert tsf.state is TaskState.COMPLETED     # gate saw new_thing.txt at the merge commit
    assert not (cfg.root / "new_thing.txt").exists()   # live checkout untouched
    # the scratch worktree was cleaned up
    assert not (cfg.root / ".worktrees" / "postmerge-demo-P78").exists()


# -- D-CORRECT-1: deterministic pre-merge gate -----------------------------


def test_pre_merge_gate_passes_merges(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """D-CORRECT-1: pre-merge gate passes (exit 0) -> task reaches MERGED,
    default branch advances, GATE_FINISHED event recorded with phase
    'pre-merge' and exit_code 0."""
    cfg = sample_project
    cfg = dataclasses.replace(
        cfg, policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)

    # sample_project's default gate is ["true"] (exit 0)
    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    _make_branch_with_file(cfg.root, "feat/demo-P91", "gate_pass.txt", "pass\n")

    _seed_merge_ready(cfg.root, "demo", "demo-P91", "feat/demo-P91")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P91")
    assert tsf.state is TaskState.MERGED
    assert tsf.merge_commit is not None

    after_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    assert after_main != before_main
    assert after_main == tsf.merge_commit

    events = list(storage.iter_events("demo"))
    gates = [e for e in events
             if e.type is EventType.GATE_FINISHED and e.task_id == "demo-P91"]
    assert len(gates) >= 1
    pre_merge_gates = [g for g in gates
                       if g.payload.get("gate_result", {}).get("phase") == "pre-merge"]
    assert len(pre_merge_gates) == 1
    assert pre_merge_gates[0].payload["gate_result"]["exit_code"] == 0


def test_pre_merge_gate_fails_not_published_review_rejected(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """D-CORRECT-1: pre-merge gate fails (exit 1) -> task ends REVIEW_REJECTED,
    default branch UNCHANGED, GATE_FINISHED event with phase 'pre-merge' and
    non-zero exit_code, scratch worktree removed."""
    failing_gate = config.GateDef(
        gate_id="failing-gate",
        argv=["false"], phase="implementation",
        timeout_seconds=30, environment="local")
    cfg = sample_project
    cfg = dataclasses.replace(
        cfg, gates={"failing-gate": failing_gate},
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)

    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    _make_branch_with_file(cfg.root, "feat/demo-P92", "will_not_merge.txt", "nope\n")

    _seed_merge_ready(cfg.root, "demo", "demo-P92", "feat/demo-P92")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P92")
    assert tsf.state is TaskState.REVIEW_REJECTED
    assert tsf.merge_commit is None

    # default branch is UNCHANGED
    after_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    assert after_main == before_main

    events = list(storage.iter_events("demo"))
    pre_merge_gates = [e for e in events
                       if e.type is EventType.GATE_FINISHED and e.task_id == "demo-P92"
                       and e.payload.get("gate_result", {}).get("phase") == "pre-merge"]
    assert len(pre_merge_gates) == 1
    assert pre_merge_gates[0].payload["gate_result"]["exit_code"] != 0

    # scratch worktree was removed
    wt_list = _run(cfg.root, "worktree", "list").stdout
    assert "automerge-demo-P92" not in wt_list


def test_pre_merge_gate_failure_captures_output_tail(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """F019 P1a: a FAILING pre-merge gate persists a bounded tail of its
    stdout+stderr in GATE_FINISHED's output_tail, so the failure is diagnosable
    from the event log (the reviewer-diagnosis routing reads this) rather than
    just an exit code. A passing gate would leave it empty (populated only on a
    non-zero exit)."""
    loud_gate = config.GateDef(
        gate_id="loud-fail",
        argv=["sh", "-c",
              "echo pytest-stdout-marker; echo COVERAGE-FAIL-marker 1>&2; exit 3"],
        phase="implementation", timeout_seconds=30, environment="local")
    cfg = sample_project
    cfg = dataclasses.replace(
        cfg, gates={"loud-fail": loud_gate},
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)

    _make_branch_with_file(cfg.root, "feat/demo-P94", "loud.txt", "x\n")
    _seed_merge_ready(cfg.root, "demo", "demo-P94", "feat/demo-P94")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    gate_ev = [e for e in events
               if e.type is EventType.GATE_FINISHED and e.task_id == "demo-P94"
               and e.payload.get("gate_result", {}).get("phase") == "pre-merge"][0]
    gr = gate_ev.payload["gate_result"]
    assert gr["exit_code"] == 3
    assert "pytest-stdout-marker" in gr["output_tail"]
    assert "COVERAGE-FAIL-marker" in gr["output_tail"]


def test_gate_output_tail_helper_handles_types_and_bounds():
    """F019 P1a unit: _gate_output_tail tolerates str|bytes|None (and any other
    type via str()), combines stdout+stderr, and tails to the limit -- the
    actionable summary (FAILED lines, the diff-coverage verdict) is at the END."""
    f = effects_gates.gate_output_tail
    assert f(None, None) == ""                       # both absent
    assert f("out", "err") == "out\nerr"             # both present -> joined
    assert f("only-out", None) == "only-out"         # stdout only
    assert f(None, "only-err") == "only-err"         # stderr only
    assert f(b"byte-out", None) == "byte-out"        # bytes decoded
    assert f(123, None) == "123"                     # defensive str() fallback
    long = "A" * 100 + "TAIL"                        # tail-bounding keeps the END
    assert f(long, None, limit=8) == long[-8:]
    assert f(long, None, limit=8).endswith("TAIL")


def test_pre_merge_gate_skipped_when_policy_disabled(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """D-CORRECT-1: policy.pre_merge_gate=False -> behaves exactly as today
    (no pre-merge GATE_FINISHED event, task reaches MERGED)."""
    cfg = sample_project
    cfg = dataclasses.replace(
        cfg, policy=dataclasses.replace(
            cfg.policy, merge_mode="guarded-automatic", pre_merge_gate=False))
    _freeze_cfg(monkeypatch, cfg)

    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    _make_branch_with_file(cfg.root, "feat/demo-P93", "skipped_test.txt", "skip\n")

    _seed_merge_ready(cfg.root, "demo", "demo-P93", "feat/demo-P93")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P93")
    assert tsf.state is TaskState.MERGED

    after_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    assert after_main != before_main

    events = list(storage.iter_events("demo"))
    pre_merge_gates = [e for e in events
                       if e.type is EventType.GATE_FINISHED and e.task_id == "demo-P93"
                       and e.payload.get("gate_result", {}).get("phase") == "pre-merge"]
    assert len(pre_merge_gates) == 0, "no pre-merge GATE_FINISHED when policy disabled"


def test_pre_merge_gate_no_gate_declared_still_merges(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """D-CORRECT-1: no gates declared -> _select_post_merge_gate returns None,
    merges without running any gate (same as pre-D-CORRECT-1)."""
    cfg = sample_project
    cfg = dataclasses.replace(
        cfg, gates={},
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)

    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    _make_branch_with_file(cfg.root, "feat/demo-P94", "no_gate.txt", "merges\n")

    _seed_merge_ready(cfg.root, "demo", "demo-P94", "feat/demo-P94")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P94")
    assert tsf.state is TaskState.MERGED

    after_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    assert after_main != before_main


def test_pre_merge_gate_timeout_not_published(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """D-CORRECT-1: pre-merge gate times out (TimeoutExpired) -> task ends
    REVIEW_REJECTED, default branch UNCHANGED, GATE_FINISHED exit_code == 124.
    Uses a sleep gate argv with a short timeout so the subprocess block itself
    raises, without monkeypatching subprocess.run globally."""
    gate = config.GateDef(
        gate_id="sleepy-gate",
        argv=["sleep", "5"], phase="implementation",
        timeout_seconds=1, environment="local")
    cfg = sample_project
    cfg = dataclasses.replace(
        cfg, gates={"sleepy-gate": gate},
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)

    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    _make_branch_with_file(cfg.root, "feat/demo-P95", "timeout_test.txt", "x\n")

    _seed_merge_ready(cfg.root, "demo", "demo-P95", "feat/demo-P95")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P95")
    assert tsf.state is TaskState.REVIEW_REJECTED
    assert tsf.merge_commit is None

    # default branch is UNCHANGED (never published)
    after_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    assert after_main == before_main

    events = list(storage.iter_events("demo"))
    pre_merge_gates = [e for e in events
                       if e.type is EventType.GATE_FINISHED and e.task_id == "demo-P95"
                       and e.payload.get("gate_result", {}).get("phase") == "pre-merge"]
    assert len(pre_merge_gates) == 1
    assert pre_merge_gates[0].payload["gate_result"]["exit_code"] == 124

    # scratch worktree was removed
    wt_list = _run(cfg.root, "worktree", "list").stdout
    assert "automerge-demo-P95" not in wt_list


def test_pre_merge_gate_exec_failure_not_published(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """D-CORRECT-1: gate command cannot be executed (OSError) -> task ends
    REVIEW_REJECTED, default branch UNCHANGED, GATE_FINISHED exit_code == 127.
    An unrunnable binary triggers FileNotFoundError (OSError subclass) in
    subprocess.run, which the daemon catches and maps to 127."""
    gate = config.GateDef(
        gate_id="missing-gate",
        argv=["nonexistent_cmd_xyzzy"], phase="implementation",
        timeout_seconds=30, environment="local")
    cfg = sample_project
    cfg = dataclasses.replace(
        cfg, gates={"missing-gate": gate},
        policy=dataclasses.replace(cfg.policy, merge_mode="guarded-automatic"))
    _freeze_cfg(monkeypatch, cfg)

    before_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    _make_branch_with_file(cfg.root, "feat/demo-P96", "exec_fail.txt", "x\n")

    _seed_merge_ready(cfg.root, "demo", "demo-P96", "feat/demo-P96")
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    tsf = storage.load_state("demo", "demo-P96")
    assert tsf.state is TaskState.REVIEW_REJECTED
    assert tsf.merge_commit is None

    # default branch is UNCHANGED (never published)
    after_main = _run(cfg.root, "rev-parse", "main").stdout.strip()
    assert after_main == before_main

    events = list(storage.iter_events("demo"))
    pre_merge_gates = [e for e in events
                       if e.type is EventType.GATE_FINISHED and e.task_id == "demo-P96"
                       and e.payload.get("gate_result", {}).get("phase") == "pre-merge"]
    assert len(pre_merge_gates) == 1
    assert pre_merge_gates[0].payload["gate_result"]["exit_code"] == 127

    # scratch worktree was removed
    wt_list = _run(cfg.root, "worktree", "list").stdout
    assert "automerge-demo-P96" not in wt_list

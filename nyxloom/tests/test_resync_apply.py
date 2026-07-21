"""Tests for the ground-truth re-baseline APPLY layer. PACKAGE RP02.

`docs/plan-state-integrity.md` Part B.4 RP02: `resync_apply` turns the
`ACTION_ADVANCE` rows of an already-computed `resync_plan` into REAL
audited events via `storage.append_and_apply` -- never a silent statefile
edit. Two disjoint concerns, mirroring `test_resync.py`'s own split:

  * `resync_apply` / `_legal_advance_transition` are exercised directly
    against in-memory `GitFacts`/`ProposedTransition` inputs, with real
    (but tmp-state-isolated) storage writes -- no git subprocess needed,
    since the merge *evidence* is supplied directly (mirrors how
    `resync_plan`'s own tests build `GitFacts` by hand in test_resync.py).
  * A couple of end-to-end `cli.main(["resync", ..., "--apply"])`
    integration tests (real git repo via `sample_project`) cover the CLI
    wiring itself (--apply / --apply-content-merges flags) and the
    paused-project oracle.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from nyxloom import cli, paths, storage
from nyxloom.resync import (
    ACTION_ADVANCE,
    ACTION_NEEDS_OPERATOR,
    ACTION_NONE,
    MERGE_SOURCE_CONTENT,
    MERGE_SOURCE_REFS,
    GitFacts,
    gather_git_facts,
    gather_handoff_presence,
    resync_apply,
    resync_plan,
)
from nyxloom.types import ActorKind, EventType, TaskState, TaskStateFile, utc_now

PROJECT = "demo"


def _tsf(task_id: str, state: TaskState, handoff_path: str | None = None) -> TaskStateFile:
    return TaskStateFile(
        schema_version=1,
        task_id=task_id,
        project=PROJECT,
        state=state,
        since=utc_now(),
        handoff_path=handoff_path,
    )


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, check=True, capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# Oracle 1 — high-confidence advance: MERGE_READY -> MERGED, actor RESYNC,
# evidence reason in payload.

def test_apply_merge_ready_high_confidence_advances_to_merged(tmp_state):
    task_id = "dstdns-P30"
    storage.save_state(_tsf(task_id, TaskState.MERGE_READY,
                            handoff_path="handoffs/dstdns-P30.md"))
    states = storage.list_states(PROJECT)
    frontmatters = {task_id: False}
    git_facts = GitFacts(merged_refs=frozenset({"feat/dstdns-P30", task_id}))

    plan = resync_plan(states, frontmatters, git_facts)
    assert plan[0].proposed_action == ACTION_ADVANCE
    assert plan[0].merge_source == MERGE_SOURCE_REFS

    results = resync_apply(PROJECT, states, plan)

    assert len(results) == 1
    assert results[0].applied is True
    assert results[0].task_id == task_id

    reloaded = storage.load_state(PROJECT, task_id)
    assert reloaded is not None
    assert reloaded.state is TaskState.MERGED

    events = list(storage.iter_events(PROJECT))
    assert len(events) == 1
    ev = events[0]
    assert ev.type is EventType.TASK_TRANSITIONED
    assert ev.task_id == task_id
    assert ev.actor.kind is ActorKind.RESYNC
    assert ev.payload["to"] == "MERGED"
    assert ev.payload["from"] == "MERGE_READY"
    assert "merged_refs" not in ev.payload  # sanity: no accidental leakage
    assert "branch" in ev.payload["reason"] and "merged" in ev.payload["reason"]
    assert "resync:" in ev.payload["notes"]
    # projection also folds notes onto the statefile (storage.py's own
    # documented TASK_TRANSITIONED contract)
    assert reloaded.notes is not None and "resync:" in reloaded.notes


# ---------------------------------------------------------------------------
# Oracle 2 — idempotent: a second --apply against the same (now-drifted-no-
# more) task emits NO further events.

def test_apply_is_idempotent_second_apply_emits_no_further_events(tmp_state):
    task_id = "dstdns-P30"
    storage.save_state(_tsf(task_id, TaskState.MERGE_READY))
    states = storage.list_states(PROJECT)
    frontmatters = {task_id: False}
    git_facts = GitFacts(merged_refs=frozenset({task_id}))

    plan1 = resync_plan(states, frontmatters, git_facts)
    results1 = resync_apply(PROJECT, states, plan1)
    assert results1[0].applied is True
    assert len(list(storage.iter_events(PROJECT))) == 1

    # Re-plan against the SAME (now-mutated-in-place) states: the task is
    # now believed MERGED. Git facts are unchanged (still "merged").
    plan2 = resync_plan(states, frontmatters, git_facts)
    assert plan2[0].proposed_action == ACTION_ADVANCE  # resync_plan itself is untouched

    results2 = resync_apply(PROJECT, states, plan2)

    assert results2[0].applied is False
    assert "MERGED or terminal" in results2[0].reason

    # The load-bearing assertion: literally nothing new was written.
    events_after = list(storage.iter_events(PROJECT))
    assert len(events_after) == 1
    reloaded = storage.load_state(PROJECT, task_id)
    assert reloaded.state is TaskState.MERGED


# ---------------------------------------------------------------------------
# Oracle 3 — content-merge safety: a content_merged-ONLY row is NOT applied
# by a bare apply; IS applied with the explicit opt-in. Test BOTH sides.

def test_apply_content_merge_only_not_applied_without_opt_in(tmp_state):
    task_id = "demo-squash"
    storage.save_state(_tsf(task_id, TaskState.MERGE_READY))
    states = storage.list_states(PROJECT)
    frontmatters = {task_id: False}
    git_facts = GitFacts(content_merged={task_id: "archived path: docs/archive/x.md"})

    plan = resync_plan(states, frontmatters, git_facts)
    assert plan[0].proposed_action == ACTION_ADVANCE
    assert plan[0].merge_source == MERGE_SOURCE_CONTENT

    results = resync_apply(PROJECT, states, plan)  # allow_content_merge defaults False

    assert results[0].applied is False
    assert "content-merge" in results[0].reason or "content-check" in results[0].reason

    reloaded = storage.load_state(PROJECT, task_id)
    assert reloaded.state is TaskState.MERGE_READY  # untouched
    assert list(storage.iter_events(PROJECT)) == []  # nothing written


def test_apply_content_merge_only_applied_with_explicit_opt_in(tmp_state):
    task_id = "demo-squash"
    storage.save_state(_tsf(task_id, TaskState.MERGE_READY))
    states = storage.list_states(PROJECT)
    frontmatters = {task_id: False}
    git_facts = GitFacts(content_merged={task_id: "archived path: docs/archive/x.md"})

    plan = resync_plan(states, frontmatters, git_facts)
    results = resync_apply(PROJECT, states, plan, allow_content_merge=True)

    assert results[0].applied is True
    reloaded = storage.load_state(PROJECT, task_id)
    assert reloaded.state is TaskState.MERGED

    events = list(storage.iter_events(PROJECT))
    assert len(events) == 1
    assert events[0].actor.kind is ActorKind.RESYNC
    assert "archived path" in events[0].payload["reason"]


# ---------------------------------------------------------------------------
# Oracle 4 — NEEDS_OPERATOR / orphan is never auto-applied.

def test_apply_needs_operator_row_never_auto_applied(tmp_state):
    task_id = "demo-orphan"
    storage.save_state(_tsf(task_id, TaskState.ACTIVE, handoff_path=None))
    states = storage.list_states(PROJECT)
    frontmatters = {task_id: False}
    git_facts = GitFacts()

    plan = resync_plan(states, frontmatters, git_facts)
    assert plan[0].proposed_action == ACTION_NEEDS_OPERATOR

    results = resync_apply(PROJECT, states, plan)

    assert len(results) == 1
    assert results[0].applied is False
    assert "NEEDS_OPERATOR" in results[0].reason

    reloaded = storage.load_state(PROJECT, task_id)
    assert reloaded.state is TaskState.ACTIVE  # untouched
    assert list(storage.iter_events(PROJECT)) == []


def test_apply_action_none_rows_are_skipped_and_not_even_reported(tmp_state):
    """A genuinely-open (ACTION_NONE) row is not actionable at all -- it
    should not even appear in the ApplyResult list (nothing to apply or
    flag), distinct from NEEDS_OPERATOR which IS reported."""
    task_id = "demo-open"
    storage.save_state(_tsf(task_id, TaskState.QUEUED, handoff_path="handoff/x.md"))
    states = storage.list_states(PROJECT)
    frontmatters = {task_id: True}
    git_facts = GitFacts()

    plan = resync_plan(states, frontmatters, git_facts)
    assert plan[0].proposed_action == ACTION_NONE

    results = resync_apply(PROJECT, states, plan)

    assert results == []
    assert list(storage.iter_events(PROJECT)) == []


# ---------------------------------------------------------------------------
# Legal-transition mapping — a non-MERGE_READY believed state with a
# HIGH-confidence merge hit must NOT fabricate a MERGED edge (illegal per
# the state machine); it uses TASK_SUPERSEDED instead (legal from every
# non-terminal state). Also doubles as Oracle 6 (legal transition, no
# TransitionError) for the non-MERGE_READY branch.

def test_apply_non_merge_ready_believed_state_uses_superseded_not_merged(tmp_state):
    task_id = "demo-queued-but-merged"
    storage.save_state(_tsf(task_id, TaskState.QUEUED))
    states = storage.list_states(PROJECT)
    frontmatters = {task_id: False}
    git_facts = GitFacts(merged_refs=frozenset({task_id, f"feat/{task_id}"}))

    plan = resync_plan(states, frontmatters, git_facts)
    assert plan[0].proposed_action == ACTION_ADVANCE
    assert plan[0].merge_source == MERGE_SOURCE_REFS

    results = resync_apply(PROJECT, states, plan)  # must not raise TransitionError

    assert results[0].applied is True
    reloaded = storage.load_state(PROJECT, task_id)
    assert reloaded.state is TaskState.SUPERSEDED  # NOT MERGED -- no such legal edge

    events = list(storage.iter_events(PROJECT))
    assert len(events) == 1
    assert events[0].type is EventType.TASK_SUPERSEDED
    assert events[0].actor.kind is ActorKind.RESYNC
    assert events[0].payload["from"] == "QUEUED"
    assert "to" not in events[0].payload  # TASK_SUPERSEDED carries no "to" (storage.py contract)


def test_apply_active_believed_state_with_merge_also_uses_superseded(tmp_state):
    """A second non-MERGE_READY believed state (ACTIVE), to make sure the
    mapping isn't accidentally special-cased to only QUEUED."""
    task_id = "demo-active-but-merged"
    storage.save_state(_tsf(task_id, TaskState.ACTIVE))
    states = storage.list_states(PROJECT)
    frontmatters = {task_id: False}
    git_facts = GitFacts(merged_refs=frozenset({task_id}))

    plan = resync_plan(states, frontmatters, git_facts)
    results = resync_apply(PROJECT, states, plan)

    assert results[0].applied is True
    reloaded = storage.load_state(PROJECT, task_id)
    assert reloaded.state is TaskState.SUPERSEDED


# ---------------------------------------------------------------------------
# CLI wiring — dry-run unchanged, --apply, --apply-content-merges,
# and Oracle 5 (paused project is resyncable).

def test_cli_resync_without_apply_flag_still_pure_dry_run(sample_project, tmp_state, capsys):
    """Regression: the plain `resync <project>` verb (no --apply) is
    byte-identical RP01 behavior even after RP02 wiring landed."""
    root = sample_project.root
    _run_git(root, "checkout", "-b", "feat/demo-P40-dryrun")
    (root / "marker-P40.txt").write_text("work\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "P40 work")
    _run_git(root, "checkout", "main")
    _run_git(root, "merge", "--no-ff", "-m", "merge P40", "feat/demo-P40-dryrun")

    storage.save_state(_tsf("demo-P40-dryrun", TaskState.MERGE_READY))

    exit_code = cli.main(["resync", "demo"])
    assert exit_code == 0

    reloaded = storage.load_state("demo", "demo-P40-dryrun")
    assert reloaded.state is TaskState.MERGE_READY  # untouched
    assert list(storage.iter_events("demo")) == []


def test_cli_resync_apply_advances_and_prints_summary(sample_project, tmp_state, capsys):
    root = sample_project.root
    _run_git(root, "checkout", "-b", "feat/demo-P41-apply")
    (root / "marker-P41.txt").write_text("work\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "P41 work")
    _run_git(root, "checkout", "main")
    _run_git(root, "merge", "--no-ff", "-m", "merge P41", "feat/demo-P41-apply")

    storage.save_state(_tsf("demo-P41-apply", TaskState.MERGE_READY))

    exit_code = cli.main(["resync", "demo", "--apply"])
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "applied 1/1" in out
    assert "demo-P41-apply" in out

    reloaded = storage.load_state("demo", "demo-P41-apply")
    assert reloaded.state is TaskState.MERGED

    events = list(storage.iter_events("demo"))
    assert len(events) == 1
    assert events[0].actor.kind is ActorKind.RESYNC

    # Second --apply: idempotent, no further events.
    exit_code2 = cli.main(["resync", "demo", "--apply"])
    assert exit_code2 == 0
    assert len(list(storage.iter_events("demo"))) == 1


def test_cli_resync_apply_content_merges_flag_gates_the_squash_case(
    sample_project, tmp_state, capsys,
):
    root = sample_project.root
    archive_dir = root / "docs" / "archive" / "handoff"
    archive_dir.mkdir(parents=True)
    (archive_dir / "demo-P42-squash-REPORT.md").write_text("archived report\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "chore: housekeeping")

    storage.save_state(_tsf("demo-P42-squash", TaskState.MERGE_READY))

    # Bare --apply: content-check-only evidence, must NOT apply.
    exit_code = cli.main(["resync", "demo", "--apply"])
    assert exit_code == 0
    reloaded = storage.load_state("demo", "demo-P42-squash")
    assert reloaded.state is TaskState.MERGE_READY
    assert list(storage.iter_events("demo")) == []

    # With the explicit opt-in: applies.
    exit_code2 = cli.main(["resync", "demo", "--apply", "--apply-content-merges"])
    assert exit_code2 == 0
    reloaded2 = storage.load_state("demo", "demo-P42-squash")
    assert reloaded2.state is TaskState.MERGED
    assert len(list(storage.iter_events("demo"))) == 1


def test_apply_works_on_paused_project(sample_project, tmp_state, capsys):
    """Oracle 5: resync --apply is an operator verb, not daemon dispatch --
    it must work on a project whose pause flag is set (that's the whole
    point of resyncing before an unpause)."""
    root = sample_project.root
    _run_git(root, "checkout", "-b", "feat/demo-P43-paused")
    (root / "marker-P43.txt").write_text("work\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "P43 work")
    _run_git(root, "checkout", "main")
    _run_git(root, "merge", "--no-ff", "-m", "merge P43", "feat/demo-P43-paused")

    storage.save_state(_tsf("demo-P43-paused", TaskState.MERGE_READY))

    pause_path = paths.pause_flag("demo")
    pause_path.parent.mkdir(parents=True, exist_ok=True)
    pause_path.touch()
    assert pause_path.exists()

    exit_code = cli.main(["resync", "demo", "--apply"])
    assert exit_code == 0

    reloaded = storage.load_state("demo", "demo-P43-paused")
    assert reloaded.state is TaskState.MERGED

    # Pause itself is untouched by resync (resync neither sets nor clears it).
    assert pause_path.exists()

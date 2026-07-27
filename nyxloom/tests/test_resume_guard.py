"""Tests for the pre-resume safety guard. PACKAGE RP03.

RP03 wires the EXISTING RP01 resync planner (`resync.resync_plan`, fed by
the same `gather_handoff_presence` / `gather_git_facts` I/O boundaries that
`test_resync.py` / `test_resync_apply.py` already exercise -- NOT a second
drift-detection implementation) into `cmd_resume`'s PROJECT-LEVEL path as a
dry-run pre-flight: any row whose `proposed_action != ACTION_NONE` blocks
the resume by default. Mirrors `test_resync_apply.py`'s own convention: a
real temporary git repo (via `sample_project`) produces genuine drift
(a task believed MERGE_READY whose branch was actually merged while the
project sat paused -- the motivating "paused for days" scenario), and
`cli.main(["resume", ...])` end-to-end covers the CLI wiring.

Task-level resume is deliberately NOT guarded (see `cmd_resume`'s
docstring in cli.py for the argument); this file's last two tests cover
both branches of that decision.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from nyxloom import cli, paths, storage
from nyxloom.types import EventType, TaskState, TaskStateFile, utc_now

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


def _pause(project: str = PROJECT) -> Path:
    flag = paths.pause_flag(project)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    return flag


def _make_merged_drift(root: Path, task_id: str, branch: str) -> None:
    """A task believed MERGE_READY whose branch was ACTUALLY merged into
    `main` (a real `git branch --merged` hit) -- the statefile just never
    caught up. Exactly the "paused for days while reality moved on" case
    RP03 exists for (mirrors test_resync_apply.py's own drift fixture)."""
    _run_git(root, "checkout", "-b", branch)
    (root / f"marker-{task_id}.txt").write_text("work\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", f"{task_id} work")
    _run_git(root, "checkout", "main")
    _run_git(root, "merge", "--no-ff", "-m", f"merge {task_id}", branch)
    storage.save_state(_tsf(task_id, TaskState.MERGE_READY))


# ---------------------------------------------------------------------------
# Oracle 1 + 2: drift blocks resume / a clean project resumes (each other's
# negative -- Oracle 2 is what proves the guard isn't just always-refusing).

def test_resume_project_blocked_by_drift(sample_project, tmp_state, capsys):
    """Oracle 1: a project with injected drift refuses to resume, and the
    refusal is real state-untouched-ness, not just printed text -- the
    pause flag STILL exists and NO PAUSE_CLEARED event was appended."""
    root = sample_project.root
    _make_merged_drift(root, "demo-P50-drift", "feat/demo-P50-drift")
    flag = _pause()

    exit_code = cli.main(["resume", "demo"])

    assert exit_code != 0
    assert flag.exists()
    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.PAUSE_CLEARED for e in events)


def test_resume_project_no_drift_proceeds(sample_project, tmp_state, capsys):
    """Oracle 2 (negative of Oracle 1): a project with no statefiles has
    nothing to drift, so resume proceeds exactly as before RP03 -- flag
    removed, PAUSE_CLEARED appended once, payload byte-identical ({})."""
    flag = _pause()

    exit_code = cli.main(["resume", "demo"])

    assert exit_code == 0
    assert not flag.exists()
    events = list(storage.iter_events("demo"))
    cleared = [e for e in events if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].payload == {}


# ---------------------------------------------------------------------------
# Oracle 3: --force overrides, and RECORDS that it did (audit trail).

def test_resume_project_force_overrides_drift(sample_project, tmp_state, capsys):
    root = sample_project.root
    _make_merged_drift(root, "demo-P51-drift", "feat/demo-P51-drift")
    flag = _pause()

    exit_code = cli.main(["resume", "demo", "--force"])

    assert exit_code == 0
    assert not flag.exists()
    events = list(storage.iter_events("demo"))
    cleared = [e for e in events if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].payload.get("forced") is True
    assert "demo-P51-drift" in json.dumps(cleared[0].payload)


# ---------------------------------------------------------------------------
# Oracle 4: the message names the ACTUAL drift (task + branch), not merely
# "some error text".

def test_resume_project_refusal_names_the_drifted_task_and_branch(
    sample_project, tmp_state, capsys,
):
    root = sample_project.root
    _make_merged_drift(root, "demo-P52-drift", "feat/demo-P52-drift")
    _pause()

    exit_code = cli.main(["resume", "demo"])
    assert exit_code != 0

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "demo-P52-drift" in combined
    # resync's evidence string names the actual merged branch/ref (whichever
    # of the task's candidate names `git branch --merged` reported first,
    # sorted -- see resync._refs_merge_evidence) -- not just a generic
    # "drift found" message.
    assert "present in `git branch --merged`" in combined
    assert "nyxloom resync demo" in combined  # the actionable repair command


# ---------------------------------------------------------------------------
# Oracle 5: atomicity -- a refused resume changes NOTHING, so re-running
# without --force refuses again, identically.

def test_resume_project_refusal_is_atomic_and_repeatable(sample_project, tmp_state, capsys):
    root = sample_project.root
    _make_merged_drift(root, "demo-P53-drift", "feat/demo-P53-drift")
    flag = _pause()

    exit_code_1 = cli.main(["resume", "demo"])
    assert exit_code_1 != 0
    assert flag.exists()
    assert list(storage.iter_events("demo")) == []

    exit_code_2 = cli.main(["resume", "demo"])
    assert exit_code_2 != 0
    assert flag.exists()
    assert list(storage.iter_events("demo")) == []
    assert exit_code_2 == exit_code_1


# ---------------------------------------------------------------------------
# HARD CONSTRAINT: a scan that could not complete must NOT read as "no
# drift" -- distinguishable from both the clean (Oracle 2) and drifted
# (Oracle 1/4) cases, with a different message and a different forced-
# through payload shape.

def test_resume_project_scan_failure_refuses_distinguishably_from_clean(
    sample_project, tmp_state, capsys, monkeypatch,
):
    def _boom(*a, **k):
        raise RuntimeError("git subprocess exploded")
    monkeypatch.setattr("nyxloom.resync.gather_git_facts", _boom)

    flag = _pause()

    exit_code = cli.main(["resume", "demo"])

    assert exit_code != 0
    assert flag.exists()
    assert list(storage.iter_events("demo")) == []

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "could not verify" in combined
    assert "drift detected" not in combined  # distinguishes from Oracle 1/4's wording


def test_resume_project_force_through_scan_failure_records_which_override(
    sample_project, tmp_state, capsys, monkeypatch,
):
    """The forced-through payload distinguishes a forced-through SCAN
    FAILURE ('drift_scan': 'failed', no task list -- we never learned
    which tasks) from a forced-through KNOWN drift (Oracle 3:
    'drift_tasks': [...]) -- the audit trail must never conflate
    'operator overrode an unverifiable state' with 'operator overrode a
    specific, named drift'."""
    def _boom(*a, **k):
        raise RuntimeError("git subprocess exploded")
    monkeypatch.setattr("nyxloom.resync.gather_git_facts", _boom)

    _pause()

    exit_code = cli.main(["resume", "demo", "--force"])

    assert exit_code == 0
    events = list(storage.iter_events("demo"))
    cleared = [e for e in events if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].payload.get("forced") is True
    assert cleared[0].payload.get("drift_scan") == "failed"
    assert "drift_tasks" not in cleared[0].payload


# ---------------------------------------------------------------------------
# Oracle 6: the task-level-resume decision. RP03 deliberately does NOT
# guard `resume <project> <task>` (see cmd_resume's docstring for the
# argument: bounded blast radius + the reused planner's orphan/
# NEEDS_OPERATOR classification is a broader question than "is resuming
# THIS task unsafe"). Both branches of that decision, tested:

def test_resume_task_level_skips_the_drift_guard_even_for_the_drifted_task(
    sample_project, tmp_state, capsys,
):
    """Does-not-apply branch: task-level resume ignores drift even for the
    EXACT task the planner would flag -- this is precisely the
    pre-existing `test_resume_task` fixture shape in test_cli.py (an
    ACTIVE task with no handoff and no merge evidence -- resync_plan
    classifies it NEEDS_OPERATOR/orphan), which is why RP03 could not
    apply the SAME planner at task granularity without false-blocking
    that pre-existing, still-passing test."""
    tsf = _tsf("demo-P01-test", TaskState.ACTIVE)
    storage.save_state(tsf)

    flag = paths.pause_flag("demo", "demo-P01-test")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    exit_code = cli.main(["resume", "demo", "demo-P01-test"])

    assert exit_code == 0
    assert not flag.exists()
    events = list(storage.iter_events("demo"))
    cleared = [e for e in events if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].task_id == "demo-P01-test"


def test_resume_task_level_unaffected_by_a_different_drifted_task(
    sample_project, tmp_state, capsys,
):
    """Belt-and-braces on the same branch: task-level resume of task B
    succeeds even while task A elsewhere in the SAME project is drifted --
    proves the guard does not implicitly scan the whole project for a
    task-level call (the "applies" branch is Oracle 1/4 above: the
    PROJECT-level call on this same kind of drift DOES refuse)."""
    root = sample_project.root
    _make_merged_drift(root, "demo-P54-drift", "feat/demo-P54-drift")

    other = _tsf("demo-P54-other", TaskState.ACTIVE)
    storage.save_state(other)

    flag = paths.pause_flag("demo", "demo-P54-other")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    exit_code = cli.main(["resume", "demo", "demo-P54-other"])

    assert exit_code == 0
    assert not flag.exists()
    events = list(storage.iter_events("demo"))
    cleared = [e for e in events if e.type is EventType.PAUSE_CLEARED]
    assert len(cleared) == 1
    assert cleared[0].task_id == "demo-P54-other"

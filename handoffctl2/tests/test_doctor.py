"""Tests for doctor.py (PACKAGE P08)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from handoffctl import paths, storage
from handoffctl.doctor import doctor_project, rebuild, doctor_all
from handoffctl.types import (
    Actor, ActorKind, Attempt, AttemptState, DoctorFinding, Event, EventType,
    Receipt, ReceiptResult, Route, Role, TaskState, TaskStateFile, Usage, Basis,
    utc_now, new_id, iso,
)


@pytest.fixture()
def demo_statefile(sample_project) -> TaskStateFile:
    """A COMPLETED statefile for testing."""
    return TaskStateFile(
        schema_version=1,
        task_id='demo-P01-sample',
        project=sample_project.project_id,
        state=TaskState.COMPLETED,
        since=utc_now(),
        handoff_path='handoff/demo-P01-sample.md',
    )


def save_demo_state(sample_project, tsf: TaskStateFile) -> None:
    """Save a statefile under the test project."""
    paths.ensure_layout(sample_project.project_id)
    storage.save_state(tsf)


# Oracle 1: Clean sample_project → NO critical/error findings
def test_doctor_clean_sample(sample_project, demo_statefile):
    """Oracle 1: clean project with all helpers mocked clean."""
    # Create event first, then save state (so replay matches on-disk)
    actor = Actor(kind=ActorKind.OPERATOR, id='test-op')
    storage.append_event(
        sample_project.project_id,
        actor=actor,
        type=EventType.TASK_CREATED,
        payload={'statefile': demo_statefile.to_dict()},
    )

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = [sample_project.root / 'handoff' / 'demo-P01-sample.md']
        mock_parse.return_value = (
            MagicMock(id='demo-P01-sample', task_deps=lambda: [], decision_deps=lambda: []),
            'body',
        )
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        critical_or_error = [f for f in findings if f.severity in ('critical', 'error')]
        assert len(critical_or_error) == 0, f"Expected no critical/error findings, got {critical_or_error}"


# Oracle 2: replay-divergence
def test_doctor_replay_divergence(sample_project, demo_statefile):
    """Oracle 2: task with edited statefile differs from replay."""
    save_demo_state(sample_project, demo_statefile)

    # Create an event that changes notes, then hand-edit the statefile
    actor = Actor(kind=ActorKind.OPERATOR, id='test-op')
    storage.append_event(
        sample_project.project_id,
        actor=actor,
        type=EventType.TASK_CREATED,
        payload={'statefile': demo_statefile.to_dict()},
    )

    # Hand-edit the saved statefile to change notes
    saved = storage.load_state(sample_project.project_id, 'demo-P01-sample')
    assert saved is not None
    saved.notes = 'hand-edited'
    storage.save_state(saved)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        divergence_findings = [f for f in findings if f.kind == 'replay-divergence']
        assert len(divergence_findings) > 0
        assert divergence_findings[0].severity == 'critical'
        assert 'demo-P01-sample' in divergence_findings[0].refs


# Oracle 3: handoff-lint
def test_doctor_handoff_lint(sample_project, demo_statefile):
    """Oracle 3: handoff lint error surfaces as DoctorFinding."""
    save_demo_state(sample_project, demo_statefile)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = [sample_project.root / 'handoff' / 'demo-P01-sample.md']
        mock_parse.return_value = (MagicMock(task_deps=lambda: []), 'body')
        from handoffctl.types import LintFinding
        mock_lint.return_value = {
            'handoff/demo-P01-sample.md': [
                LintFinding(rule='L1', severity='error', message='test', path='handoff/demo-P01-sample.md')
            ]
        }
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        lint_findings = [f for f in findings if f.kind == 'handoff-lint']
        assert len(lint_findings) > 0
        assert lint_findings[0].severity == 'error'


# Oracle 4: dangling-dep
def test_doctor_dangling_dep(sample_project):
    """Oracle 4: task depends_on ['ghost'] with no handoff/statefile."""
    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = [sample_project.root / 'handoff' / 'demo-P01-sample.md']
        fm = MagicMock()
        fm.id = 'demo-P01-sample'
        fm.task_deps.return_value = ['ghost']
        fm.decision_deps.return_value = []
        mock_parse.return_value = (fm, 'body')
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        dangling = [f for f in findings if f.kind == 'dangling-dep']
        assert len(dangling) > 0
        assert dangling[0].severity == 'error'
        assert 'ghost' in dangling[0].refs


# Oracle 5: orphan-worktree
def test_doctor_orphan_worktree(sample_project, demo_statefile):
    """Oracle 5: git worktree with no matching non-terminal task."""
    # Create worktree in git
    worktree_path = sample_project.root / '.worktrees' / 'feat' / 'zombie'
    subprocess.run(['git', 'worktree', 'add', '-b', 'feat/zombie',
                    str(worktree_path)], cwd=sample_project.root, check=True)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        orphan = [f for f in findings if f.kind == 'orphan-worktree']
        assert len(orphan) > 0
        assert orphan[0].severity == 'warning'
        assert 'feat/zombie' in orphan[0].refs


# Oracle 6: missing-worktree
def test_doctor_missing_worktree(sample_project):
    """Oracle 6: ACTIVE task whose worktree doesn't exist."""
    active_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P02-test',
        project=sample_project.project_id,
        state=TaskState.ACTIVE,
        since=utc_now(),
    )
    attempt = Attempt(
        attempt_id=new_id('att'),
        role=Role.IMPLEMENTER,
        state=AttemptState.RUNNING,
        route=Route(route_id='fake-cli', cli='fake', model='fake-model'),
        started=utc_now(),
        worktree='/nonexistent/worktree',
    )
    active_state.attempts = [attempt]
    save_demo_state(sample_project, active_state)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        missing = [f for f in findings if f.kind == 'missing-worktree']
        assert len(missing) > 0
        assert missing[0].severity == 'warning'


# Oracle 7: stale-receipt
def test_doctor_stale_receipt(sample_project):
    """Oracle 7: RUNNING attempt with receipt.json present."""
    running_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P03-test',
        project=sample_project.project_id,
        state=TaskState.ACTIVE,
        since=utc_now(),
    )
    receipt = Receipt(
        result=ReceiptResult.DONE,
        exit_code=0,
    )
    attempt = Attempt(
        attempt_id=new_id('att'),
        role=Role.IMPLEMENTER,
        state=AttemptState.RUNNING,
        route=Route(route_id='fake-cli', cli='fake', model='fake-model'),
        started=utc_now(),
        receipt=receipt,
    )
    running_state.attempts = [attempt]
    save_demo_state(sample_project, running_state)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        stale = [f for f in findings if f.kind == 'stale-receipt']
        assert len(stale) > 0
        assert stale[0].severity == 'warning'


# Oracle 8: unbound-evidence
def test_doctor_unbound_evidence(sample_project):
    """Oracle 8: MERGED state with merge_commit None."""
    merged_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P04-test',
        project=sample_project.project_id,
        state=TaskState.MERGED,
        since=utc_now(),
        merge_commit=None,
    )
    save_demo_state(sample_project, merged_state)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        unbound = [f for f in findings if f.kind == 'unbound-evidence']
        assert len(unbound) > 0
        assert unbound[0].severity == 'warning'


# Oracle 9: legacy-lock
def test_doctor_legacy_lock(sample_project):
    """Oracle 9: touch .STACK_LOCK exists."""
    docs_dir = sample_project.root / 'docs'
    docs_dir.mkdir(exist_ok=True)
    lock_path = docs_dir / '.STACK_LOCK'
    lock_path.touch()

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        legacy = [f for f in findings if f.kind == 'legacy-lock']
        assert len(legacy) > 0
        assert legacy[0].severity == 'warning'


# Oracle 10: stale-pause
def test_doctor_stale_pause(sample_project):
    """Oracle 10: pause flag 8 days old."""
    paths.ensure_layout(sample_project.project_id)
    pause_path = paths.pause_flag(sample_project.project_id)
    pause_path.touch()

    # Set mtime to 8 days in the past
    eight_days_ago = time.time() - (8 * 24 * 3600)
    os.utime(pause_path, (eight_days_ago, eight_days_ago))

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        stale_pause = [f for f in findings if f.kind == 'stale-pause']
        assert len(stale_pause) > 0
        assert stale_pause[0].severity == 'info'


# Oracle 11: orphan-statefile
def test_doctor_orphan_statefile(sample_project):
    """Oracle 11: QUEUED statefile with missing handoff_path."""
    queued_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P05-test',
        project=sample_project.project_id,
        state=TaskState.QUEUED,
        since=utc_now(),
        handoff_path='handoff/demo-P05-test.md',  # This file doesn't exist
    )
    save_demo_state(sample_project, queued_state)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        orphan = [f for f in findings if f.kind == 'orphan-statefile']
        assert len(orphan) > 0
        assert orphan[0].severity == 'warning'


# Oracle 11b: orphan-statefile COMPLETED → NO finding (terminal exemption)
def test_doctor_orphan_statefile_completed(sample_project):
    """Oracle 11b: COMPLETED statefile with missing handoff_path is exempt."""
    completed_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P05-done',
        project=sample_project.project_id,
        state=TaskState.COMPLETED,
        since=utc_now(),
        handoff_path='handoff/demo-P05-done.md',  # This file doesn't exist
    )
    save_demo_state(sample_project, completed_state)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        orphan = [f for f in findings if f.kind == 'orphan-statefile']
        assert len(orphan) == 0


# Oracle 12: decision-hold
def test_doctor_decision_hold(sample_project):
    """Oracle 12: QUEUED task with D-dep that's OPEN."""
    queued_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P06-test',
        project=sample_project.project_id,
        state=TaskState.QUEUED,
        since=utc_now(),
        handoff_path='handoff/demo-P06-test.md',
    )
    save_demo_state(sample_project, queued_state)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = [sample_project.root / 'handoff' / 'demo-P06-test.md']
        fm = MagicMock()
        fm.id = 'demo-P06-test'
        fm.task_deps.return_value = []
        fm.decision_deps.return_value = ['D-002']
        mock_parse.return_value = (fm, 'body')
        mock_lint.return_value = {}
        mock_decisions.return_value = {'D-002', 'D-003'}

        findings = doctor_project(sample_project)
        decision_hold = [f for f in findings if f.kind == 'decision-hold']
        assert len(decision_hold) > 0
        assert decision_hold[0].severity == 'info'
        assert 'D-002' in decision_hold[0].refs


# Oracle 13: rebuild with divergence
def test_rebuild_divergence_diffs(sample_project):
    """Oracle 13a: rebuild finds diffs when diverged."""
    demo_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P01-sample',
        project=sample_project.project_id,
        state=TaskState.COMPLETED,
        since=utc_now(),
        handoff_path='handoff/demo-P01-sample.md',
    )
    save_demo_state(sample_project, demo_state)

    # Create event
    actor = Actor(kind=ActorKind.OPERATOR, id='test-op')
    storage.append_event(
        sample_project.project_id,
        actor=actor,
        type=EventType.TASK_CREATED,
        payload={'statefile': demo_state.to_dict()},
    )

    # Hand-edit the saved statefile
    saved = storage.load_state(sample_project.project_id, 'demo-P01-sample')
    assert saved is not None
    saved.notes = 'hand-edited different'
    storage.save_state(saved)

    replayed, diffs = rebuild(sample_project.project_id, write=False)
    assert len(diffs) > 0
    assert any('notes' in d for d in diffs)


# Oracle 13b: rebuild write=True creates .bak and updates statefile
def test_rebuild_write_creates_backup(sample_project):
    """Oracle 13b: rebuild(write=True) creates .bak and replaces."""
    demo_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P01-sample',
        project=sample_project.project_id,
        state=TaskState.COMPLETED,
        since=utc_now(),
        handoff_path='handoff/demo-P01-sample.md',
    )
    save_demo_state(sample_project, demo_state)

    # Create event with different notes
    actor = Actor(kind=ActorKind.OPERATOR, id='test-op')
    demo_state_with_notes = TaskStateFile(
        schema_version=1,
        task_id='demo-P01-sample',
        project=sample_project.project_id,
        state=TaskState.COMPLETED,
        since=utc_now(),
        handoff_path='handoff/demo-P01-sample.md',
        notes='from event',
    )
    storage.append_event(
        sample_project.project_id,
        actor=actor,
        type=EventType.TASK_CREATED,
        payload={'statefile': demo_state_with_notes.to_dict()},
    )

    statefile_path = paths.statefile_path(sample_project.project_id, 'demo-P01-sample')

    # write=True should create .bak and update the statefile
    replayed, diffs = rebuild(sample_project.project_id, write=True)
    bak_path = statefile_path.with_suffix('.bak')
    assert bak_path.exists()
    assert statefile_path.exists()

    # Verify content was updated
    updated = storage.load_state(sample_project.project_id, 'demo-P01-sample')
    assert updated is not None
    assert updated.notes == 'from event'


# Oracle 14: doctor_all
def test_doctor_all(sample_project):
    """Oracle 14: doctor_all returns dict over registry."""
    demo_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P01-sample',
        project=sample_project.project_id,
        state=TaskState.COMPLETED,
        since=utc_now(),
        handoff_path='handoff/demo-P01-sample.md',
    )
    save_demo_state(sample_project, demo_state)

    with patch('handoffctl.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('handoffctl.doctor.lint.lint_project') as mock_lint, \
         patch('handoffctl.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        result = doctor_all()
        assert isinstance(result, dict)
        assert 'demo' in result
        assert isinstance(result['demo'], list)

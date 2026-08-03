"""Tests for doctor.py (PACKAGE P08)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from nyxloom import storage_sqlite, cli, paths, storage
from nyxloom.doctor import doctor_project, rebuild, doctor_all, doctor_host, _cgroup_slices_in_argv
from nyxloom.transport_check import TransportProbe
from nyxloom.types import (
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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        divergence_findings = [f for f in findings if f.kind == 'replay-divergence']
        assert len(divergence_findings) > 0
        assert divergence_findings[0].severity == 'critical'
        assert 'demo-P01-sample' in divergence_findings[0].refs


# ---------------------------------------------------------------------------
# Oracle O2 (append-doctor-hardening handoff): replay-divergence must
# compare only the REPLAYABLE projection, not the full/rich TaskStateFile
# dict -- so a task diverging ONLY on lossy attempt fields (usage/cost,
# receipt, session_handle) that replay cannot faithfully reconstruct in
# every history (e.g. an in-flight synthetic carve task, or a
# duplicate-TASK_CREATED history) is NOT flagged, while a task whose
# actual `.state` genuinely diverges STILL is.

@pytest.mark.parametrize("field_name,disk_value", [
    ("usage", Usage(basis=Basis.ACTUAL, tokens_in=999, tokens_out=999, cost=999.99)),
    ("receipt", Receipt(result=ReceiptResult.DONE, exit_code=0)),
    ("session_handle", "some-other-session-handle"),
])
def test_doctor_ignores_divergence_on_lossy_attempt_fields(sample_project, field_name, disk_value):
    """Negative: a statefile differing from replay ONLY in a lossy
    per-attempt field (usage/cost, receipt, or session_handle) must NOT
    surface as replay-divergence."""
    task_id = "demo-P07-lossy"
    attempt = Attempt(
        attempt_id=new_id("att"),
        role=Role.IMPLEMENTER,
        state=AttemptState.EXITED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=utc_now(),
        ended=utc_now(),
    )
    tsf = TaskStateFile(
        schema_version=1,
        task_id=task_id,
        project=sample_project.project_id,
        state=TaskState.AWAITING_REVIEW,
        since=utc_now(),
        attempts=[attempt],
    )

    save_demo_state(sample_project, tsf)

    actor = Actor(kind=ActorKind.OPERATOR, id='test-op')
    storage.append_event(
        sample_project.project_id,
        actor=actor,
        type=EventType.TASK_CREATED,
        payload={'statefile': tsf.to_dict()},
    )

    # replay() reconstructs the statefile exactly as created above (no
    # usage/receipt/session_handle set); now hand-edit ONLY the on-disk
    # attempt's lossy field to diverge from what replay derives -- `.state`
    # (and everything else replay derives) stays equal to the replayed
    # projection.
    saved = storage.load_state(sample_project.project_id, task_id)
    setattr(saved.attempts[0], field_name, disk_value)
    storage.save_state(saved)

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        divergence = [f for f in findings if f.kind == 'replay-divergence' and task_id in f.refs]
        assert divergence == [], f"lossy field {field_name!r} falsely flagged divergence: {divergence}"


def test_doctor_still_flags_genuine_state_divergence(sample_project, demo_statefile):
    """Positive control: a REAL `.state` mismatch between replay and
    on-disk (not a lossy-field difference) must still be flagged critical
    -- the lossy-field allowance must not become a blanket suppression."""
    save_demo_state(sample_project, demo_statefile)  # on-disk: COMPLETED

    actor = Actor(kind=ActorKind.OPERATOR, id='test-op')
    queued_variant = TaskStateFile(
        schema_version=1,
        task_id=demo_statefile.task_id,
        project=sample_project.project_id,
        state=TaskState.QUEUED,
        since=utc_now(),
        handoff_path=demo_statefile.handoff_path,
    )
    storage.append_event(
        sample_project.project_id,
        actor=actor,
        type=EventType.TASK_CREATED,
        payload={'statefile': queued_variant.to_dict()},
    )

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)
        divergence = [f for f in findings if f.kind == 'replay-divergence']
        assert len(divergence) > 0
        assert divergence[0].severity == 'critical'
        assert demo_statefile.task_id in divergence[0].refs


# P36 Oracle O3: check 1 degrades broadly, other checks keep running
def test_doctor_replay_check_failure_degrades_and_other_checks_still_run(sample_project, demo_statefile):
    """The live incident this package fixes: replay raising (e.g. a
    TransitionError from a stale event log) must not kill doctor_project --
    check 1 degrades to a finding and checks 2-11 still contribute theirs."""
    save_demo_state(sample_project, demo_statefile)

    with patch('nyxloom.doctor.storage.replay') as mock_replay, \
         patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

        mock_replay.side_effect = RuntimeError('boom: stale event log')
        mock_discover.return_value = [sample_project.root / 'handoff' / 'demo-P01-sample.md']
        mock_parse.return_value = (MagicMock(task_deps=lambda: []), 'body')
        from nyxloom.types import LintFinding
        mock_lint.return_value = {
            'handoff/demo-P01-sample.md': [
                LintFinding(rule='L1', severity='error', message='test', path='handoff/demo-P01-sample.md')
            ]
        }
        mock_decisions.return_value = set()

        findings = doctor_project(sample_project)  # must not raise

        failed = [f for f in findings if f.kind == 'replay-check-failed']
        assert len(failed) == 1
        assert failed[0].severity == 'critical'
        assert 'boom: stale event log' in failed[0].message

        # check 2 (handoff-lint) still ran and contributed its finding
        lint_findings = [f for f in findings if f.kind == 'handoff-lint']
        assert len(lint_findings) > 0


# Oracle 3: handoff-lint
def test_doctor_handoff_lint(sample_project, demo_statefile):
    """Oracle 3: handoff lint error surfaces as DoctorFinding."""
    save_demo_state(sample_project, demo_statefile)

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = [sample_project.root / 'handoff' / 'demo-P01-sample.md']
        mock_parse.return_value = (MagicMock(task_deps=lambda: []), 'body')
        from nyxloom.types import LintFinding
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
    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.frontmatter.parse_handoff') as mock_parse, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

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
    """Oracle 13b, restated for CR-04b: rebuild(write=True) backs the STORE up
    before it repairs it, and the repair lands.

    The backup used to be a `.bak` copy per statefile -- an artifact of the
    deleted file backend. It is now one consistent copy of the whole store,
    which is the shape the property actually needs: a repair rewrites every
    projection row, and per-row copies could not be a point in time, so a
    crash between two of them left a backup that was half old and half new."""
    demo_state = TaskStateFile(
        schema_version=1,
        task_id='demo-P01-sample',
        project=sample_project.project_id,
        state=TaskState.COMPLETED,
        since=utc_now(),
        handoff_path='handoff/demo-P01-sample.md',
    )
    save_demo_state(sample_project, demo_state)

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

    replayed, diffs = rebuild(sample_project.project_id, write=True)

    backup_path = storage_sqlite.db_path(sample_project.project_id)
    backup_path = backup_path.with_name(backup_path.name + '.bak')
    assert backup_path.exists(), 'rebuild(write=True) did not back the store up'

    updated = storage.load_state(sample_project.project_id, 'demo-P01-sample')
    assert updated is not None
    assert updated.notes == 'from event'


def test_rebuild_backup_restores_the_pre_repair_store(sample_project):
    """The backup is only worth taking if it can be restored. Repair, then
    restore, and the pre-repair projection must come back -- otherwise
    `rebuild --write` is an irreversible operation wearing a safety net."""
    project = sample_project.project_id
    before = TaskStateFile(
        schema_version=1, task_id='demo-P01-sample', project=project,
        state=TaskState.COMPLETED, since=utc_now(),
        handoff_path='handoff/demo-P01-sample.md', notes='before repair',
    )
    save_demo_state(sample_project, before)
    storage.append_event(
        project, actor=Actor(kind=ActorKind.OPERATOR, id='test-op'),
        type=EventType.TASK_CREATED,
        payload={'statefile': TaskStateFile(
            schema_version=1, task_id='demo-P01-sample', project=project,
            state=TaskState.COMPLETED, since=utc_now(),
            handoff_path='handoff/demo-P01-sample.md', notes='from event',
        ).to_dict()},
    )

    rebuild(project, write=True)
    assert storage.load_state(project, 'demo-P01-sample').notes == 'from event'

    backup_path = storage_sqlite.db_path(project)
    backup_path = backup_path.with_name(backup_path.name + '.bak')
    storage.restore(project, backup_path)

    assert storage.load_state(project, 'demo-P01-sample').notes == 'before repair'


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

    with patch('nyxloom.doctor.frontmatter.discover_handoffs') as mock_discover, \
         patch('nyxloom.doctor.lint.lint_project') as mock_lint, \
         patch('nyxloom.doctor.decisions.open_ids') as mock_decisions:

        mock_discover.return_value = []
        mock_lint.return_value = {}
        mock_decisions.return_value = set()

        result = doctor_all()
        assert isinstance(result, dict)
        assert 'demo' in result
        assert isinstance(result['demo'], list)


# ---------------------------------------------------------------------------
# doctor_host: host-scoped checks (docker transport + cgroup slice existence)
# infra/agent-cli/README.md + infra/slices/README.md. Every test mocks
# transport_check.probe_default AND shutil.which/subprocess.run so this
# suite never touches a real docker socket or real systemd unit -- it must
# run identically on any host.

def _set_gate_cgroup_parent(sample_project, slice_name: str) -> None:
    """Rewrite the sample_project fixture's on-disk gate argv (normally
    `["true"]`) to a docker invocation naming `--cgroup-parent=<slice_name>`,
    so doctor_host()'s own registry scan (which reloads ProjectConfig fresh
    from disk, exactly like doctor_all()) picks it up."""
    toml_path = sample_project.root / '.nyxloom' / 'project.toml'
    text = toml_path.read_text()
    new_argv_line = (
        'argv = ["bash", "-c", '
        f'"docker run --rm --cgroup-parent={slice_name} alpine true"]'
    )
    assert 'argv = ["true"]' in text, 'fixture project.toml gate argv shape changed'
    toml_path.write_text(text.replace('argv = ["true"]', new_argv_line))


def _healthy_probe(**kw) -> TransportProbe:
    return TransportProbe('healthy', 'test-default')


# Oracle: transport lying -> exactly one critical finding
def test_doctor_host_transport_lying_is_critical(sample_project, monkeypatch):
    monkeypatch.setattr(
        'nyxloom.doctor.transport_check.probe_default',
        lambda **kw: TransportProbe('lying', 'truncated mid-stream'),
    )
    findings = doctor_host()
    crit = [f for f in findings if f.kind == 'docker-transport-lying']
    assert len(crit) == 1
    assert crit[0].severity == 'critical'
    assert crit[0].message == 'truncated mid-stream'
    assert crit[0].project is None


# Oracle: transport healthy -> no finding at all
def test_doctor_host_transport_healthy_no_finding(sample_project, monkeypatch):
    monkeypatch.setattr('nyxloom.doctor.transport_check.probe_default', _healthy_probe)
    findings = doctor_host()
    assert [f for f in findings if f.kind.startswith('docker-transport')] == []


# Oracle: transport unavailable -> never critical (this doctor_host chooses
# to emit no finding at all for 'unavailable' -- an offline/no-docker host,
# e.g. the gate's own tester-unified container, must never manufacture a
# finding merely because it has no docker to probe).
def test_doctor_host_transport_unavailable_no_critical(sample_project, monkeypatch):
    monkeypatch.setattr(
        'nyxloom.doctor.transport_check.probe_default',
        lambda **kw: TransportProbe('unavailable', "'docker' not found on PATH"),
    )
    findings = doctor_host()
    assert not any(f.severity == 'critical' for f in findings)
    assert [f for f in findings if f.kind.startswith('docker-transport')] == []


# Oracle: --cgroup-parent=<name> and the space-separated form both extract,
# restricted to .slice unit names.
def test_cgroup_slices_in_argv_extracts_both_forms():
    argv = [
        'bash', '-c',
        "docker run --cgroup-parent=dev-workloads.slice --rm image "
        "&& docker run --cgroup-parent other-tool.slice --rm image2 "
        "&& docker run --cgroup-parent=not-a-slice-name --rm image3",
    ]
    assert _cgroup_slices_in_argv(argv) == {'dev-workloads.slice', 'other-tool.slice'}


# Oracle: a missing slice (systemctl reports not-found) -> critical finding
# naming the slice AND the owning project:gate.
def test_doctor_host_slice_missing_is_critical(sample_project, monkeypatch):
    _set_gate_cgroup_parent(sample_project, 'ghost.slice')
    monkeypatch.setattr('nyxloom.doctor.transport_check.probe_default', _healthy_probe)
    monkeypatch.setattr('nyxloom.doctor.shutil.which', lambda name: '/usr/bin/systemctl')

    def fake_run(argv, **kwargs):
        assert argv[:2] == ['systemctl', 'show']
        assert argv[2] == 'ghost.slice'
        return subprocess.CompletedProcess(argv, 0, stdout='LoadState=not-found\n', stderr='')

    monkeypatch.setattr('nyxloom.doctor.subprocess.run', fake_run)

    findings = doctor_host()
    crit = [f for f in findings if f.kind == 'cgroup-slice-missing']
    assert len(crit) == 1
    assert crit[0].severity == 'critical'
    assert 'ghost.slice' in crit[0].message
    assert crit[0].project is None
    assert crit[0].refs == ['demo:pytest-q']


# Oracle: an existing slice (LoadState=loaded) -> no finding.
def test_doctor_host_slice_loaded_no_finding(sample_project, monkeypatch):
    _set_gate_cgroup_parent(sample_project, 'dev-workloads.slice')
    monkeypatch.setattr('nyxloom.doctor.transport_check.probe_default', _healthy_probe)
    monkeypatch.setattr('nyxloom.doctor.shutil.which', lambda name: '/usr/bin/systemctl')

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout='LoadState=loaded\n', stderr='')

    monkeypatch.setattr('nyxloom.doctor.subprocess.run', fake_run)

    findings = doctor_host()
    assert [f for f in findings if f.kind.startswith('cgroup-slice')] == []


# Oracle: no systemctl on this host -> one info finding, and the per-slice
# systemctl probe is NEVER attempted.
def test_doctor_host_no_systemctl_emits_info_and_never_probes(sample_project, monkeypatch):
    _set_gate_cgroup_parent(sample_project, 'ghost.slice')
    monkeypatch.setattr('nyxloom.doctor.transport_check.probe_default', _healthy_probe)
    monkeypatch.setattr('nyxloom.doctor.shutil.which', lambda name: None)

    run_mock = MagicMock()
    monkeypatch.setattr('nyxloom.doctor.subprocess.run', run_mock)

    findings = doctor_host()
    info = [f for f in findings if f.kind == 'cgroup-slice-unchecked']
    assert len(info) == 1
    assert info[0].severity == 'info'
    assert 'ghost.slice' in info[0].refs
    run_mock.assert_not_called()


# Oracle: a subprocess error (timeout) probing a slice -> warning (not
# critical, not silent).
def test_doctor_host_slice_probe_error_is_warning(sample_project, monkeypatch):
    _set_gate_cgroup_parent(sample_project, 'ghost.slice')
    monkeypatch.setattr('nyxloom.doctor.transport_check.probe_default', _healthy_probe)
    monkeypatch.setattr('nyxloom.doctor.shutil.which', lambda name: '/usr/bin/systemctl')

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=10)

    monkeypatch.setattr('nyxloom.doctor.subprocess.run', fake_run)

    findings = doctor_host()
    warn = [f for f in findings if f.kind == 'cgroup-slice-unverified']
    assert len(warn) == 1
    assert warn[0].severity == 'warning'
    assert not any(f.severity == 'critical' for f in findings)
    assert warn[0].refs == ['demo:pytest-q']


# Oracle: systemctl exits without ever printing a LoadState= line (malformed/
# unexpected output, distinct from a raised exception) -> also a warning,
# never treated as "exists".
def test_doctor_host_slice_probe_malformed_output_is_warning(sample_project, monkeypatch):
    _set_gate_cgroup_parent(sample_project, 'ghost.slice')
    monkeypatch.setattr('nyxloom.doctor.transport_check.probe_default', _healthy_probe)
    monkeypatch.setattr('nyxloom.doctor.shutil.which', lambda name: '/usr/bin/systemctl')

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout='', stderr='unit not loaded')

    monkeypatch.setattr('nyxloom.doctor.subprocess.run', fake_run)

    findings = doctor_host()
    warn = [f for f in findings if f.kind == 'cgroup-slice-unverified']
    assert len(warn) == 1
    assert warn[0].severity == 'warning'
    assert 'no LoadState=' in warn[0].message
    assert not any(f.severity == 'critical' for f in findings)


# Oracle: load_registry() itself failing must not crash doctor_host -- it
# degrades to "no slices known" (empty registry), same resilience pattern as
# doctor_all()'s own try/except around ProjectConfig.load.
def test_doctor_host_registry_load_failure_does_not_crash(sample_project, monkeypatch):
    monkeypatch.setattr('nyxloom.doctor.transport_check.probe_default', _healthy_probe)
    monkeypatch.setattr(
        'nyxloom.doctor.load_registry',
        MagicMock(side_effect=RuntimeError('registry.toml unreadable')),
    )
    findings = doctor_host()
    assert findings == []


# Oracle: one project's ProjectConfig.load() failing (e.g. a malformed
# nyxloom.toml) must not take down the slice scan for every OTHER registered
# project -- it is skipped, not fatal.
def test_doctor_host_project_config_load_failure_is_skipped(sample_project, monkeypatch):
    monkeypatch.setattr('nyxloom.doctor.transport_check.probe_default', _healthy_probe)
    monkeypatch.setattr(
        'nyxloom.doctor.load_registry',
        lambda: {'broken-project': Path('/does/not/exist')},
    )
    monkeypatch.setattr(
        'nyxloom.doctor.ProjectConfig.load',
        MagicMock(side_effect=RuntimeError('bad config')),
    )
    findings = doctor_host()
    assert [f for f in findings if f.kind.startswith('cgroup-slice')] == []


# ---------------------------------------------------------------------------
# cmd_doctor wiring (cli.py): host findings fold into the same table + the
# non-zero-exit-on-critical decision.

def test_cmd_doctor_critical_host_finding_forces_nonzero_exit(
    sample_project, tmp_state, capsys, monkeypatch
):
    """A critical host finding (e.g. missing cgroup slice / lying transport)
    must make `nyxloom doctor` exit non-zero, even with zero project-level
    findings."""
    monkeypatch.setattr('nyxloom.doctor.doctor_project', lambda cfg: [])
    host_finding = DoctorFinding(
        kind='cgroup-slice-missing',
        severity='critical',
        message='cgroup slice ghost.slice does not exist',
        project=None,
        refs=['demo:pytest-q'],
    )
    monkeypatch.setattr('nyxloom.doctor.doctor_host', lambda: [host_finding])

    exit_code = cli.main(['doctor'])
    out = capsys.readouterr().out
    assert exit_code != 0
    assert 'cgroup-slice-missing' in out
    assert 'ghost.slice' in out


def test_cmd_doctor_no_critical_findings_zero_exit(
    sample_project, tmp_state, capsys, monkeypatch
):
    """No project findings and no host findings -> `nyxloom doctor` exits 0."""
    monkeypatch.setattr('nyxloom.doctor.doctor_project', lambda cfg: [])
    monkeypatch.setattr('nyxloom.doctor.doctor_host', lambda: [])

    exit_code = cli.main(['doctor'])
    assert exit_code == 0

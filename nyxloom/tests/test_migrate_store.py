"""Tests for migrate_store.py (PACKAGE SP02, docs/plan-state-integrity.md
Part A.3): the file -> SQLite event-store importer + zero-divergence
verification.

Every fixture is built via the FILE backend ONLY (`storage.append_and_apply`
with `NYXLOOM_STATE_BACKEND` unset -- the default) so `migrate()` has a real
`events.jsonl` + on-disk statefiles to import from, matching the ordering
constraint in migrate_store.py's module docstring: this suite is the only
place `migrate()` ever runs, never against a live registered project.

Oracles (per the SP02 handoff):
  1. Zero-divergence import: a multi-event, >=2-task fixture imports with
     the rebuilt SQLite projection exactly equal to the file backend's.
  2. Backup preserved + source retired: events.jsonl.pre-sqlite exists with
     the original content; events.jsonl is gone.
  3. Idempotent: a second migrate() call is a no-op (status
     "already-migrated", no double events, no error).
  4. Corrupt line reported: a malformed JSONL line raises MigrationError
     naming the line, never silently dropped.
  5. Event order/seq preserved: the imported (type, task_id) sequence
     matches the source order exactly.

Plus coverage-completeness cases for every branch migrate_store.py adds:
nothing-to-migrate (no source, no backup), the crash-recovery dedup path
(_already_imported True: source still present but SQLite already holds an
exact match -- skip re-insert, still verify+rename), the inconsistent-
partial-state path (_already_imported raises), and both divergence shapes
(on-disk content differs from replay; a task replay projects that has no
on-disk statefile at all).
"""

from __future__ import annotations

import json

import pytest

from nyxloom import cli, paths, storage, storage_sqlite
from nyxloom.migrate_store import MigrationError, migrate
from nyxloom.types import Actor, ActorKind, EventType, TaskState, TaskStateFile, utc_now

ACTOR = Actor(kind=ActorKind.TICK, id="test")


def _seed_project(project: str) -> None:
    """Build a realistic multi-task, multi-event history via the FILE
    backend: 2 tasks, TASK_CREATED + TASK_TRANSITIONED (x2) +
    PROGRESS_RECORDED for task 1, TASK_CREATED + PROGRESS_RECORDED for
    task 2 -- for the zero-divergence and event-order oracles."""
    states: dict[str, TaskStateFile] = {}

    t1 = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="demo-P01",
                        project=project, state=TaskState.CARVED, since=utc_now())
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_CREATED,
        payload={"statefile": t1.to_dict()}, task_id="demo-P01",
    )
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "CARVED", "to": "QUEUED", "notes": None}, task_id="demo-P01",
    )
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE", "notes": None}, task_id="demo-P01",
    )
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
        payload={"units": ["step-1"]}, task_id="demo-P01",
    )

    t2 = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="demo-P02",
                        project=project, state=TaskState.QUEUED, since=utc_now())
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_CREATED,
        payload={"statefile": t2.to_dict()}, task_id="demo-P02",
    )
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
        payload={"units": ["p2-step"]}, task_id="demo-P02",
    )


# ---------------------------------------------------------------------------
# Oracle 1: zero-divergence import

def test_zero_divergence_import(tmp_state):
    project = "sp02-zero-div"
    _seed_project(project)

    file_states = storage.list_states(project)  # file backend (default)

    result = migrate(project)

    assert result.status == "migrated"
    assert result.imported_count == 6
    assert set(result.task_ids) == {"demo-P01", "demo-P02"}

    sqlite_states = storage_sqlite.list_states(project)
    assert sqlite_states.keys() == file_states.keys()
    for task_id in file_states:
        assert sqlite_states[task_id].to_dict() == file_states[task_id].to_dict()

    replayed = storage_sqlite.replay(project)
    assert replayed.keys() == file_states.keys()
    for task_id in file_states:
        assert replayed[task_id].to_dict() == file_states[task_id].to_dict()


# ---------------------------------------------------------------------------
# Oracle 2: backup preserved + source retired

def test_backup_preserved_and_source_retired(tmp_state):
    project = "sp02-backup"
    _seed_project(project)

    src = paths.events_path(project)
    original_content = src.read_text(encoding="utf-8")

    migrate(project)

    assert not src.exists()
    backup = src.parent / "events.jsonl.pre-sqlite"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------------------
# Oracle 3: idempotent re-run

def test_idempotent_rerun_is_noop(tmp_state):
    project = "sp02-idempotent"
    _seed_project(project)

    first = migrate(project)
    assert first.status == "migrated"
    count_after_first = len(list(storage_sqlite.iter_events(project)))

    second = migrate(project)
    assert second.status == "already-migrated"
    assert len(list(storage_sqlite.iter_events(project))) == count_after_first


def test_nothing_to_migrate_when_no_source_and_no_backup(tmp_state):
    result = migrate("sp02-empty-project")
    assert result.status == "nothing-to-migrate"


# ---------------------------------------------------------------------------
# Oracle 4: corrupt line reported

def test_corrupt_line_reported(tmp_state):
    project = "sp02-corrupt"
    _seed_project(project)

    src = paths.events_path(project)
    with src.open("a", encoding="utf-8") as f:
        f.write("not valid json{\n")

    with pytest.raises(MigrationError, match=r"corrupt source line \d+"):
        migrate(project)

    # Nothing was renamed and nothing was inserted -- the corrupt line is
    # detected during the parse pass, before any SQLite write.
    assert src.exists()
    assert list(storage_sqlite.iter_events(project)) == []


def test_blank_lines_in_source_are_skipped_not_corrupt(tmp_state):
    """A blank line in events.jsonl (e.g. stray whitespace between
    records) is tolerated -- skipped, not treated as a corrupt line and
    not counted as an event -- matching the file backend's own
    `iter_events` blank-line tolerance."""
    project = "sp02-blank-lines"
    _seed_project(project)

    src = paths.events_path(project)
    lines = src.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "")  # a blank line between two real event records
    lines.append("   ")  # a whitespace-only trailing line
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = migrate(project)

    assert result.status == "migrated"
    assert result.imported_count == 6  # blank lines contribute no events


# ---------------------------------------------------------------------------
# Oracle 5: event order/seq preserved

def test_event_order_preserved(tmp_state):
    project = "sp02-order"
    _seed_project(project)

    source_order = [(e.type, e.task_id) for e in storage.iter_events(project)]
    assert source_order == [
        (EventType.TASK_CREATED, "demo-P01"),
        (EventType.TASK_TRANSITIONED, "demo-P01"),
        (EventType.TASK_TRANSITIONED, "demo-P01"),
        (EventType.PROGRESS_RECORDED, "demo-P01"),
        (EventType.TASK_CREATED, "demo-P02"),
        (EventType.PROGRESS_RECORDED, "demo-P02"),
    ]

    migrate(project)

    imported_order = [(e.type, e.task_id) for e in storage_sqlite.iter_events(project)]
    assert imported_order == source_order
    # seq is gap-free monotonic and matches insertion order 1..N
    assert [e.sequence for e in storage_sqlite.iter_events(project)] == list(range(1, 7))


# ---------------------------------------------------------------------------
# Crash-recovery: SQLite already holds an EXACT match (source still present)

def test_already_imported_exact_match_skips_reinsert_and_still_completes(tmp_state):
    project = "sp02-crash-recovery"
    _seed_project(project)

    # Simulate a prior run that inserted every event into SQLite but
    # crashed before the rename (events.jsonl is still on disk).
    file_events = list(storage.iter_events(project))
    for ev in file_events:
        storage_sqlite.append_event(
            project, actor=ev.actor, type=ev.type, payload=ev.payload,
            task_id=ev.task_id, attempt_id=ev.attempt_id, wave_id=ev.wave_id,
            decision_id=ev.decision_id, timestamp=ev.timestamp,
        )
    assert len(list(storage_sqlite.iter_events(project))) == len(file_events)

    result = migrate(project)

    assert result.status == "migrated"
    # No duplicate insert: still exactly the original count.
    assert len(list(storage_sqlite.iter_events(project))) == len(file_events)
    # The job still gets finished: rename happens on this call.
    assert not paths.events_path(project).exists()
    assert (paths.events_path(project).parent / "events.jsonl.pre-sqlite").exists()


def test_already_imported_mismatched_partial_state_raises(tmp_state):
    project = "sp02-partial-mismatch"
    _seed_project(project)

    # Simulate a crash mid-insert-loop: only SOME of the source events made
    # it into SQLite before the prior run died (events.jsonl still present,
    # full source still there) -- an inconsistent state migrate() must
    # refuse to guess about, rather than silently double-inserting or
    # silently accepting a partial import as "done".
    file_events = list(storage.iter_events(project))
    for ev in file_events[:2]:
        storage_sqlite.append_event(
            project, actor=ev.actor, type=ev.type, payload=ev.payload,
            task_id=ev.task_id, attempt_id=ev.attempt_id, wave_id=ev.wave_id,
            decision_id=ev.decision_id, timestamp=ev.timestamp,
        )

    with pytest.raises(MigrationError, match="do NOT match"):
        migrate(project)

    # Refused to touch anything further: source untouched, no extra rows.
    assert paths.events_path(project).exists()
    assert len(list(storage_sqlite.iter_events(project))) == 2


# ---------------------------------------------------------------------------
# Divergence: on-disk content differs from what the event log replays

def test_divergence_content_mismatch_raises_and_does_not_rename(tmp_state):
    project = "sp02-divergence-content"
    _seed_project(project)

    # Hand-edit the on-disk statefile so it disagrees with what replaying
    # the event log would derive (notes is not one of doctor's lossy
    # per-attempt allowances, so this is a genuine divergence).
    saved = storage.load_state(project, "demo-P01")
    saved.notes = "hand-edited-after-events"
    storage.save_state(saved)

    with pytest.raises(MigrationError, match="zero-divergence check failed"):
        migrate(project)

    assert paths.events_path(project).exists()  # NOT renamed


# ---------------------------------------------------------------------------
# Divergence: a task the event log projects has NO on-disk statefile at all

def test_divergence_missing_on_disk_statefile_raises(tmp_state):
    project = "sp02-divergence-missing"
    _seed_project(project)

    # Delete one task's statefile entirely -- replay() still projects it
    # (it is derived purely from the event log), but it is now missing on
    # disk, which is equally a divergence (the symmetric case: not
    # "content differs", but "absent").
    statefile = paths.statefile_path(project, "demo-P02")
    statefile.unlink()

    with pytest.raises(MigrationError, match="demo-P02"):
        migrate(project)

    assert paths.events_path(project).exists()  # NOT renamed


# ---------------------------------------------------------------------------
# CLI thin-wrapper coverage (cmd_migrate_store) -- `nyxloom migrate-store
# <project>`. scope.touch keeps CLI tests here (test_cli.py is out of
# scope for this handoff) rather than in tests/test_cli.py.

def test_cli_migrate_store_success_prints_migrated(tmp_state, capsys):
    project = "sp02-cli-success"
    _seed_project(project)

    exit_code = cli.main(["migrate-store", project])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "migrated:" in out
    assert "6 event(s)" in out
    assert "2 task(s)" in out
    assert not paths.events_path(project).exists()


def test_cli_migrate_store_already_migrated(tmp_state, capsys):
    project = "sp02-cli-already"
    _seed_project(project)
    migrate(project)  # first run, out of band

    exit_code = cli.main(["migrate-store", project])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "already-migrated:" in out


def test_cli_migrate_store_nothing_to_migrate(tmp_state, capsys):
    exit_code = cli.main(["migrate-store", "sp02-cli-nothing"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "nothing-to-migrate:" in out


def test_cli_migrate_store_error_path_prints_and_exits_1(tmp_state, capsys):
    project = "sp02-cli-error"
    _seed_project(project)
    src = paths.events_path(project)
    with src.open("a", encoding="utf-8") as f:
        f.write("not valid json{\n")

    exit_code = cli.main(["migrate-store", project])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "corrupt source line" in err

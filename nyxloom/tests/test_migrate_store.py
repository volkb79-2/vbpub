"""Behavioral tests for the statefile-authoritative migrate-store import.

Fixtures write raw FILE events plus statefiles independently so the audit log
can contain histories that today's live append guard would reject. Migration
must preserve those events without applying them, copy statefiles verbatim,
and retire the source only after a successful SQLite round-trip check.
"""

from __future__ import annotations

import json

import pytest

from nyxloom import cli, paths, storage, storage_sqlite
from nyxloom.migrate_store import MigrationError, migrate
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Basis, BlockerType, EventType,
    OracleResult, Receipt, ReceiptResult, Role, Route, TaskState,
    TaskStateFile, Usage, utc_now,
)

ACTOR = Actor(kind=ActorKind.TICK, id="test")


def _seed_project(project: str) -> None:
    """Build independently-written raw events and operational statefiles."""
    created = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P01",
        project=project, state=TaskState.CARVED, since=utc_now(),
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_CREATED,
        payload={"statefile": created.to_dict()}, task_id="demo-P01",
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "CARVED", "to": "QUEUED", "notes": None}, task_id="demo-P01",
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE", "notes": None}, task_id="demo-P01",
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
        payload={"units": ["step-1"]}, task_id="demo-P01",
    )

    t2 = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P02",
        project=project, state=TaskState.QUEUED, since=utc_now(),
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_CREATED,
        payload={"statefile": t2.to_dict()}, task_id="demo-P02",
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
        payload={"units": ["p2-step"]}, task_id="demo-P02",
    )

    storage.save_state(TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P01",
        project=project, state=TaskState.ACTIVE, since=utc_now(),
        progress_units=["step-1"],
    ))
    t2.progress_units = ["p2-step"]
    storage.save_state(t2)


# ---------------------------------------------------------------------------
# Oracle 1: statefile-authoritative import

def test_statefile_authoritative_import(tmp_state):
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


def test_verbatim_fidelity_preserves_rich_attempt_fields(tmp_state):
    project = "sp02-verbatim-fidelity"
    _seed_project(project)

    rich = storage.load_state(project, "demo-P01")
    assert rich is not None
    rich.attempts = [Attempt(
        attempt_id="att-rich", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
        route=Route(
            route_id="route-rich", cli="codex", model="gpt-5",
            variant="high", effort="thorough", routes_rev="routes-42",
        ),
        started=utc_now(), ended=utc_now(), worktree="/tmp/rich-worktree",
        branch="rich-branch", base_commit="abc123", pid=123, pgid=456,
        log_path="/tmp/rich.log", session_handle="session-rich",
        receipt=Receipt(
            result=ReceiptResult.DONE, exit_code=0,
            oracles=[OracleResult(id="O-rich", result="pass")],
            files_touched=["src/rich.py"], head_commit="def456",
        ),
        usage=Usage(
            basis=Basis.ACTUAL, tokens_in=101, tokens_out=202, cached_in=3,
            cost=0.42, currency="USD", price_rev="price-1",
        ),
        wave_id="wave-rich",
    )]
    storage.save_state(rich)
    file_states = storage.list_states(project)

    migrate(project)

    sqlite_states = storage_sqlite.list_states(project)
    assert {task_id: state.to_dict() for task_id, state in sqlite_states.items()} == {
        task_id: state.to_dict() for task_id, state in file_states.items()
    }
    assert sqlite_states["demo-P01"].attempts[0].usage.cost == 0.42
    assert sqlite_states["demo-P01"].attempts[0].receipt.head_commit == "def456"


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
# Audit drift is tolerated: events are carried but not replayed.

def test_orphan_rejected_blocked_event_is_preserved_as_audit(tmp_state):
    project = "sp02-orphan-blocked"
    created = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="topos-P64",
        project=project, state=TaskState.VALIDATING, since=utc_now(),
    )
    completed = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="topos-P64",
        project=project, state=TaskState.COMPLETED, since=utc_now(),
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_CREATED,
        payload={"statefile": created.to_dict()}, task_id="topos-P64",
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "VALIDATING", "to": "COMPLETED", "notes": None},
        task_id="topos-P64",
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_BLOCKED,
        payload={
            "from": "COMPLETED",
            "blocker": {
                "type": BlockerType.ENVIRONMENT.value,
                "unblock_condition": "repair test environment",
            },
        },
        task_id="topos-P64",
    )
    storage.save_state(completed)

    result = migrate(project)

    assert result.status == "migrated"
    assert storage_sqlite.list_states(project)["topos-P64"].state is TaskState.COMPLETED
    assert EventType.TASK_BLOCKED in [
        event.type for event in storage_sqlite.iter_events(project)
    ]


def test_event_projection_without_statefile_is_tolerated(tmp_state):
    project = "sp02-projected-absent"
    absent = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="dstdns-P10",
        project=project, state=TaskState.QUEUED, since=utc_now(),
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_CREATED,
        payload={"statefile": absent.to_dict()}, task_id="dstdns-P10",
    )

    result = migrate(project)

    assert result.status == "migrated"
    assert "dstdns-P10" not in storage_sqlite.list_states(project)
    assert [event.type for event in storage_sqlite.iter_events(project)] == [
        EventType.TASK_CREATED
    ]


def test_copy_verification_failure_rolls_back_and_keeps_source(tmp_state, monkeypatch):
    project = "sp02-copy-verify-failure"
    _seed_project(project)
    src = paths.events_path(project)
    backup = src.parent / "events.jsonl.pre-sqlite"
    db = storage_sqlite.db_path(project)
    real_list_states = storage_sqlite.list_states

    def corrupt_copy(project: str):
        copied = real_list_states(project)
        copied["demo-P01"].notes = "corrupted-by-test"
        return copied

    monkeypatch.setattr(storage_sqlite, "list_states", corrupt_copy)

    with pytest.raises(MigrationError, match="demo-P01"):
        migrate(project)

    assert not db.exists()
    assert src.exists()
    assert not backup.exists()


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

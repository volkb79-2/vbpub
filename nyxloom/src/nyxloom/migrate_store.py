"""File -> SQLite event-store importer. PACKAGE SP02
(docs/plan-state-integrity.md Part A.3).

`nyxloom migrate-store <project>`:

1. READ the FILE backend's source directly: parse every event out of
   `paths.events_path(project)` (`events.jsonl`), in order. This
   deliberately does NOT go through `storage.py`'s `NYXLOOM_STATE_BACKEND`
   selector -- this tool migrates a project FROM file TO sqlite
   regardless of that flag (see the ordering-constraint note below), so
   it reads the file directly (`_parse_source_events`, a duplicate of
   `storage.py`'s own file-backend `iter_events` line-parse -- kept
   local rather than imported so this tool's read path can never be
   silently redirected by the selector it exists to retire).
2. INSERT every event, in original order, into the project's SQLite
   `events` table via `storage_sqlite.append_event` (the SP01 public
   API), then REBUILD the `states` projection: `storage_sqlite.replay()`
   + `storage_sqlite.save_state()` per task (so the SQLite backend is
   immediately usable after this tool runs -- not just the event log).
3. VERIFY zero divergence: the rebuilt projection must equal the
   CURRENT on-disk FILE statefiles -- read directly via
   `_read_file_statefiles` (again bypassing the selector, for the same
   reason as step 1: this check must be meaningful even if
   `NYXLOOM_STATE_BACKEND=sqlite` already happens to be set in the
   calling environment). Comparison reuses doctor's own
   `_replayable_projection` (`doctor.py`, read-only import -- see
   docs/plan-state-integrity.md SP02 "reuse doctor's divergence diff").
   Any divergence ABORTS with `MigrationError`; `events.jsonl` is left
   untouched.
4. On success, RENAME `events.jsonl` -> `events.jsonl.pre-sqlite` (a
   backup, NEVER deleted).

Idempotency: a project already fully migrated is detected by the
`.pre-sqlite` backup already existing (source absent) -- re-running is
then a documented no-op (`status="already-migrated"`), no re-insert, no
error. A project whose SQLite `events` table already holds an import
that matches the current source log exactly (source still present --
e.g. a prior run crashed between the insert/verify/rename steps) is
detected by `_already_imported` (exact ordered content match) and its
insert step is skipped, but verify+rename still run to finish the job.
Anything else observed in that table (a partial or mismatched count/
content) is NOT guessed about -- `_already_imported` raises loudly
rather than risking a double-import or a wrong skip.

Ordering constraint (docs/plan-state-integrity.md, PACKAGE SP02): this
tool is meant to run against a LIVE, registered project only at the
SP03 cutover, AFTER the daemon default flips to SQLite -- the rename
retires the file backend for that project. SP02 itself only BUILDS and
TESTS this importer (tests/test_migrate_store.py, temp fixtures only);
it is never invoked here against a live registered project.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, storage_sqlite
from .doctor import _replayable_projection
from .types import Event, TaskStateFile


class MigrationError(Exception):
    """Raised when migrate-store cannot proceed safely: a corrupt/partial
    source line, a zero-divergence verification failure, or a partial/
    inconsistent prior-import state in the SQLite events table that
    `_already_imported` refuses to guess about."""


@dataclass
class MigrationResult:
    project: str
    status: str  # "migrated" | "already-migrated" | "nothing-to-migrate"
    imported_count: int = 0
    task_ids: list[str] = field(default_factory=list)


def _backup_path(project: str) -> Path:
    """`events.jsonl` -> `events.jsonl.pre-sqlite`, alongside the source
    (NOT `Path.with_suffix`, which would replace `.jsonl` instead of
    appending after it)."""
    src = paths.events_path(project)
    return src.parent / (src.name + ".pre-sqlite")


def _parse_source_events(path: Path) -> list[Event]:
    """Parse every line of a file-backend `events.jsonl` into `Event`
    objects, in file order. A structurally-corrupt or partial line (bad
    JSON, or JSON that fails `Event.from_dict` -- e.g. a truncated write,
    or an unknown/missing field) is reported with its 1-based line number
    and raw content via `MigrationError` -- never silently skipped or
    dropped."""
    events: list[Event] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                ev = Event.from_dict(json.loads(line))
            except Exception as exc:
                raise MigrationError(
                    f"corrupt source line {lineno} in {path}: "
                    f"{type(exc).__name__}: {exc} -- line content: {line!r}"
                ) from exc
            events.append(ev)
    return events


def _read_file_statefiles(project: str) -> dict[str, TaskStateFile]:
    """The CURRENT on-disk FILE-backend statefiles, read directly --
    never through `storage.list_states`'s `NYXLOOM_STATE_BACKEND`
    selector, so this verification is meaningful regardless of what that
    flag is set to in the calling environment (see module docstring)."""
    paths.ensure_layout(project)
    out: dict[str, TaskStateFile] = {}
    for p in sorted(paths.state_dir(project).glob("*.json")):
        tsf = TaskStateFile.from_dict(json.loads(p.read_text(encoding="utf-8")))
        out[tsf.task_id] = tsf
    return out


def _event_identity(ev: Event) -> tuple:
    """The subset of an `Event` used to compare source-log identity
    against what a prior run already inserted into the SQLite `events`
    table. Deliberately excludes `sequence` -- storage_sqlite assigns it
    via AUTOINCREMENT rather than copying it verbatim from the source
    log (docs/plan-state-integrity.md A.1)."""
    return (ev.type, ev.task_id, ev.attempt_id, ev.wave_id, ev.decision_id, ev.payload)


def _already_imported(project: str, source_events: list[Event]) -> bool:
    """True if the SQLite `events` table already holds EXACTLY the
    source log's events, in order (a prior run inserted them but
    crashed/aborted before the rename). False if it holds none. Raises
    `MigrationError` on any other relationship -- a partial or mismatched
    count/content is an inconsistent state this tool refuses to guess
    about (never silently double-import, never silently skip a real
    difference)."""
    existing = list(storage_sqlite.iter_events(project))
    if not existing:
        return False
    if len(existing) == len(source_events) and (
        [_event_identity(e) for e in existing] == [_event_identity(e) for e in source_events]
    ):
        return True
    raise MigrationError(
        f"project {project!r}: SQLite events table already holds "
        f"{len(existing)} event(s) that do NOT match the "
        f"{len(source_events)} event(s) in the source log -- refusing to "
        f"guess; inspect {storage_sqlite.db_path(project)} manually"
    )


def migrate(project: str) -> MigrationResult:
    """`nyxloom migrate-store <project>` -- see module docstring for the
    full contract."""
    src = paths.events_path(project)
    backup = _backup_path(project)

    if not src.exists():
        if backup.exists():
            return MigrationResult(project=project, status="already-migrated")
        return MigrationResult(project=project, status="nothing-to-migrate")

    source_events = _parse_source_events(src)

    if not _already_imported(project, source_events):
        for ev in source_events:
            storage_sqlite.append_event(
                project,
                actor=ev.actor, type=ev.type, payload=ev.payload,
                task_id=ev.task_id, attempt_id=ev.attempt_id,
                wave_id=ev.wave_id, decision_id=ev.decision_id,
                timestamp=ev.timestamp,
            )

    replayed = storage_sqlite.replay(project)
    for tsf in replayed.values():
        storage_sqlite.save_state(tsf)

    on_disk = _read_file_statefiles(project)
    diverged = [
        task_id for task_id, disk_state in on_disk.items()
        if (replayed.get(task_id) is None
            or _replayable_projection(replayed[task_id]) != _replayable_projection(disk_state))
    ]
    # A task the event log projects that has NO on-disk statefile at all
    # (e.g. deleted by hand) is equally a divergence -- the symmetric
    # half of the check above, which only walks on_disk's own keys.
    diverged += sorted(set(replayed) - set(on_disk))
    if diverged:
        raise MigrationError(
            f"zero-divergence check failed for project {project!r}: "
            f"{len(diverged)} task(s) diverged ({', '.join(sorted(diverged)[:20])}) "
            f"-- aborting; {src} was NOT renamed"
        )

    os.replace(src, backup)
    return MigrationResult(
        project=project, status="migrated",
        imported_count=len(source_events), task_ids=sorted(on_disk),
    )

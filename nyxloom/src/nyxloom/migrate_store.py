"""Statefile-authoritative FILE -> SQLite importer.

`nyxloom migrate-store <project>` reads the FILE backend directly, carrying
every valid `events.jsonl` record into SQLite's `events` table in source order
as an opaque audit trail. It does *not* replay that audit trail: incumbent FILE
logs can contain non-atomic-write drift, such as an event appended before its
statefile update was rejected or failed. The daemon's current statefiles are
the operational truth, so their complete `TaskStateFile` records are copied
verbatim into SQLite's `states` table instead.

The copy is round-trip verified by comparing every statefile's full `to_dict()`
with `storage_sqlite.list_states`. A save/load fidelity failure deletes the
SQLite database, leaves `events.jsonl` in place, and raises `MigrationError` so
a later invocation starts clean. Reconciling nyxloom's belief with git ground
truth is deliberately separate work for `resync`, not this migration.

On success, `events.jsonl` is renamed to `events.jsonl.pre-sqlite` and retained
as a backup. If that backup already exists while the source is absent, the
migration is an idempotent no-op. If a prior run inserted an exact ordered copy
of the source events but crashed before the rename, `_already_imported` skips
the duplicate insertion and completes the statefile copy and rename. A partial
or mismatched prior import raises rather than guessing. Corrupt or partial
source lines likewise raise with their line number before SQLite is changed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, storage_sqlite
from .types import Event, TaskStateFile


class MigrationError(Exception):
    """Raised when migrate-store cannot proceed safely: a corrupt/partial
    source line, a statefile-copy verification failure, or a partial/
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

    on_disk = _read_file_statefiles(project)
    for tsf in on_disk.values():
        storage_sqlite.save_state(tsf)

    copied = storage_sqlite.list_states(project)
    mismatching = sorted(
        set(on_disk) ^ set(copied)
        | {
            task_id for task_id in set(on_disk) & set(copied)
            if on_disk[task_id].to_dict() != copied[task_id].to_dict()
        }
    )
    if mismatching:
        storage_sqlite.db_path(project).unlink(missing_ok=True)
        raise MigrationError(
            f"statefile copy verification failed for project {project!r}: "
            f"{len(mismatching)} task(s) mismatched "
            f"({', '.join(mismatching[:20])}) -- rolled back SQLite; "
            f"{src} was NOT renamed"
        )

    os.replace(src, backup)
    return MigrationResult(
        project=project, status="migrated",
        imported_count=len(source_events), task_ids=sorted(on_disk),
    )

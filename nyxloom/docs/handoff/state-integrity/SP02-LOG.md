# SP02 — file→SQLite store importer + zero-divergence verification — LOG

Package: SP02 (docs/plan-state-integrity.md Part A.3)
Branch: feat/state-sp02-importer
Worktree: /workspaces/vbpub/.worktrees/state-sp02-importer

## Context read (before writing code)

- `docs/plan-state-integrity.md` Part A (A.0–A.3), Part A.3 SP02 phase description.
- `src/nyxloom/storage_sqlite.py` (SP01, on main at facd686): schema (`events`,
  `states`, `meta`), `_connect`/`_insert_event`/`_upsert_state_row` (private, the
  atomicity injection seams), and the public surface: `append_event`,
  `iter_events`, `load_state`, `save_state`, `list_states`, `append_and_apply`,
  `replay`, `db_path`. Confirmed `seq` is AUTOINCREMENT and NOT meant to be
  copied verbatim from a source log's own `sequence` field.
- `src/nyxloom/storage.py`: the FILE backend, in particular the `iter_events`
  file-branch's own JSONL→Event parse (`Event.from_dict(json.loads(line))`,
  skip blank lines) — mirrored (not imported, to avoid the
  `NYXLOOM_STATE_BACKEND` selector) as `migrate_store._parse_source_events`.
- `src/nyxloom/paths.py`: `events_path`, `state_dir`, `statefile_path`,
  `project_dir`, `ensure_layout` — read-only, used as-is.
- `src/nyxloom/doctor.py`: found `_replayable_projection` (line ~59) and the
  `doctor_project` replay-divergence check (line ~92) it backs, plus
  `rebuild()` (line ~382). Reused `_replayable_projection` READ-ONLY (import,
  no edit) as the acceptance-test diff function, per the handoff's explicit
  instruction to reuse it.
- `src/nyxloom/types.py`: `Event`/`Actor`/`ActorKind`/`EventType`/
  `TaskStateFile`, and `_Serde.from_dict`'s "unknown keys → ValueError"
  contract (relied on for corrupt-line detection catching a broad
  `Exception`).
- `src/nyxloom/cli.py`: full interface-contract docstring + `main()`'s
  argparse wiring and dispatch, to anchor the new verb immediately after
  `render` per the SP04-conflict-avoidance instruction. Found `events`/
  `digest` verbs already registered further down (pre-existing, unrelated to
  this package — not touched).
- `tests/test_storage_sqlite.py`, `tests/test_storage.py`, `tests/test_doctor.py`,
  `tests/test_cli.py`, `tests/conftest.py` — fixture conventions (`tmp_state`,
  `sample_project`, the `_seed` pattern for building file-backend history via
  `storage.append_and_apply`).

## Design decisions

1. **Direct file read, not `storage.iter_events`.** Per the handoff:
   "do NOT rely on the `NYXLOOM_STATE_BACKEND` selector." Implemented
   `_parse_source_events(path)` as a local duplicate of the file backend's own
   parse loop, reading `paths.events_path(project)` directly — so this tool's
   read path can never be silently redirected by the very selector it exists
   to retire, regardless of what the calling environment's env var is set to.
2. **On-disk verification also bypasses the selector**, for the same reason:
   `_read_file_statefiles(project)` globs `paths.state_dir(project)` directly
   rather than calling `storage.list_states` (which selects the backend by
   env var). This makes the zero-divergence check meaningful even if
   `NYXLOOM_STATE_BACKEND=sqlite` already happens to be set at cutover time.
3. **Insert via `storage_sqlite.append_event`** (public API) per source
   event, preserving each event's original `timestamp`/`actor`/`payload`
   verbatim; `seq` is left to AUTOINCREMENT (matches insertion order 1..N
   for a fresh DB, which is what the "event order preserved" oracle actually
   asserts — type/task_id sequence, not a specific numeric `seq` value).
4. **Projection persisted, not just computed.** After inserting events,
   `storage_sqlite.replay()` is called and EVERY resulting task is written via
   `storage_sqlite.save_state()` — so the SQLite `states` table is populated,
   not just the `events` log. Without this, a subsequent SP03 cutover
   (`NYXLOOM_STATE_BACKEND=sqlite`) would find an empty `states` table despite
   a full event log.
5. **Idempotency, two layers:**
   - Primary (the exact oracle-3 shape): `events.jsonl` renamed away already →
     if `events.jsonl.pre-sqlite` exists, return `status="already-migrated"`
     immediately, no SQLite touch at all.
   - Secondary (a documented "and/or" in the handoff, for the crash-recovery
     case where a prior run inserted events but died before the rename, so
     the source is STILL present): `_already_imported` compares the SQLite
     `events` table's content, in order, against the freshly-parsed source. An
     exact match → skip the insert loop, but still run verify+rename (finishes
     the interrupted job). Anything else (a partial/mismatched count or
     content) → `MigrationError`, refusing to guess whether to insert, skip,
     or something else.
6. **Divergence check covers both directions**: a task on disk whose replayed
   projection is `None` or differs (`_replayable_projection` inequality), AND
   the symmetric case — a task the event log projects that has NO on-disk
   statefile at all (`set(replayed) - set(on_disk)`). Both raise
   `MigrationError` and leave `events.jsonl` untouched.
7. **CLI wiring** (`cli.py`): anchored the `migrate-store` subparser
   registration immediately after `render_parser`, and the
   `elif args.cmd == "migrate-store":` dispatch immediately after `render`'s,
   per the SP04-conflict-avoidance instruction (SP04 is concurrently touching
   the `events` verb, further down in the file). `cmd_migrate_store` is a thin
   wrapper: calls `migrate_store.migrate`, catches `MigrationError` → prints
   `error: ...` to stderr + exit 1 (matching the existing convention used by
   `cmd_init`/`cmd_onboard` for their own domain errors), otherwise prints the
   status + counts and returns 0.

## Tests (tests/test_migrate_store.py, new)

All 5 required oracles plus coverage-completeness cases for every branch
`migrate_store.py`/`cli.py` add (nothing-to-migrate, the crash-recovery dedup
path both ways — exact match and mismatched partial state — and both
divergence shapes), plus CLI-wrapper tests for all three status prints and
the error path (test_cli.py is out of scope.touch for this handoff, so these
live in test_migrate_store.py instead, importing `nyxloom.cli` directly).

## Ordering constraint (restated per the handoff's requirement)

This tool is designed to run against a live, registered project only at the
SP03 cutover, AFTER the daemon default flips to SQLite (the rename retires
the file backend for that project). SP02 only BUILDS and TESTS the importer,
against temp fixtures created inline in `tests/test_migrate_store.py` via the
file backend — it is never invoked here against any live registered project.

## Status

Code + tests written. Committing before running the real gate (coverage_gate
diffs COMMITTED history — an uncommitted tree would show a hollow 0/0 pass).
Gate run and result to follow in SP02-REPORT.md.

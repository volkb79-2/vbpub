# SP01 — SQLite event/state store backend — LOG

Branch: `feat/state-p01-sqlite-backend`
Worktree: `/workspaces/vbpub/.worktrees/state-p01-sqlite-backend`
Plan: `nyxloom/docs/plan-state-integrity.md` Part A (A.0-A.3, phase SP01)

## Actions

1. Created worktree `feat/state-p01-sqlite-backend` from `main` (HEAD `622d4cb` at
   worktree-add time — a later `main` commit than the `9c22b51` cited in the handoff;
   used the worktree's actual `main` since that's what `git worktree add ... main`
   resolves to).
2. Read context: `nyxloom/docs/plan-state-integrity.md` Part A in full,
   `nyxloom/src/nyxloom/storage.py` (entire file), `nyxloom/src/nyxloom/types.py`
   (Event/TaskStateFile/ActorKind/iso/parse_iso), `nyxloom/src/nyxloom/paths.py`
   (found `project_dir(project)` as the per-project state dir — NOT modified),
   `nyxloom/src/nyxloom/doctor.py` (the `_replayable_projection`/replay-divergence
   check at doctor.py:59-110 — confirmed it only calls `storage.replay()` and
   `storage.list_states()`, both backend-agnostic through the selector, so no
   doctor.py change needed).
3. Grepped every `storage.*` call site across `src/nyxloom/*.py` (cli.py,
   commands.py, daemon.py, wrapper.py, notify.py, decision_chat.py, render.py,
   reconcile.py, watchdog.py) to confirm the full public surface actually used:
   `append_event`, `iter_events`, `load_state`, `save_state`, `list_states`,
   `append_and_apply`, `apply_event`, `replay`, `SCHEMA_VERSION`. `apply_event`
   and `_validate_before_append` are pure functions over an in-memory
   `dict[str, TaskStateFile]` + `Event` (no I/O) — confirmed by re-reading
   storage.py — so they are reused UNCHANGED by the SQLite backend rather than
   reimplemented, per "keep the file-backend code fully intact."
4. Read `tests/test_storage.py` (existing suite, P20/P36 oracles) and
   `tests/conftest.py` (`tmp_state` fixture: sets `NYXLOOM_STATE` env var) to
   match test idioms. Per scope.touch, `tests/test_storage.py` is NOT in scope
   to touch (only `test_storage_sqlite.py` new) — so wrote an equivalent,
   independent test file rather than parametrizing the existing one.

## Design decisions

- **DARK FLAG:** `storage.py` gained a private `_sqlite_backend_enabled()`
  helper (`os.environ.get("NYXLOOM_STATE_BACKEND") == "sqlite"`) and one guard
  clause at the top of each of `append_event`, `iter_events`, `load_state`,
  `save_state`, `list_states`, `append_and_apply`, `replay` that lazily imports
  `storage_sqlite` and delegates. Unset/any-other-value keeps the existing file
  implementation below the guard byte-for-byte unchanged. `apply_event` and
  `_validate_before_append` got NO guard (see above — pure, backend-agnostic,
  reused directly by `storage_sqlite.py`).
- **Schema (A.1) — one deliberate, documented extension.** The `events` table
  is a literal match to A.1 (seq/schema_version/ts/actor_kind/actor_id/type/
  payload/task_id/attempt_id/wave_id/decision_id + the two indexes), with
  `project` intentionally NOT a column (implicit: one DB file per project, per
  A.0's "per-project DB file" decision — `Event.project` is reconstructed from
  the function's own `project` argument, not stored).
  The `states` table keeps every A.1-literal column (task_id PK, project,
  state, since, handoff_path, notes, attempts JSON, schema_version) but adds
  one more: **`data TEXT NOT NULL`** holding the full canonical
  `TaskStateFile.to_dict()` JSON blob. Reason: A.1's literal column list only
  covers a SUBSET of `TaskStateFile`'s actual dataclass fields (missing
  `wave_id`, `paused`, `blocker`, `gate_results`, `leases_held`,
  `progress_units`, `merge_commit`). API parity (Oracle 1) requires
  `load_state`/`list_states` to reconstruct a `TaskStateFile` that round-trips
  ALL of these — e.g. `PAUSE_SET`/`LEASE_ACQUIRED`/`GATE_FINISHED` events must
  survive a load. Hand-rolling one SQL column per nested-dataclass field
  (`Attempt`/`GateResult`/`Blocker` all have their own nested `_Serde`
  round-trip logic in types.py) would reimplement `_Serde` a second time and
  risk drifting from it. Reusing `TaskStateFile.to_dict()`/`.from_dict()` as
  the canonical (de)serialization — already exhaustively tested via
  `test_types.py` — is safer and DRYer. The literal A.1 columns are still
  populated (redundant but free) so a future `SELECT state, since FROM states
  WHERE ...` ops/grep query still works without needing the JSON blob.
- **Atomicity seam:** `append_and_apply` in `storage_sqlite.py` opens one
  connection, `BEGIN IMMEDIATE`, inserts the event via `_insert_event`, then
  for each task_id `apply_event` reports as affected, calls
  `_upsert_state_row(conn, states[tid])` — all in the same transaction —
  then `conn.commit()`; any exception anywhere in that block triggers
  `conn.rollback()` before re-raising. `_upsert_state_row` is a deliberately
  separate, monkeypatchable module function (the oracle's injection seam).
- **AUTOINCREMENT + rollback interaction (bonus finding):** because the `seq`
  assignment itself (the AUTOINCREMENT counter bump) happens inside the same
  transaction as the event INSERT, a rolled-back attempt does not just fail to
  persist — it does not consume a `seq` value either. The next successful
  append gets the value the failed attempt would have used. Added a dedicated
  test for this (`test_seq_no_gap_after_rolled_back_attempt`) since it's a
  genuinely non-obvious correctness property worth locking down.
- **WAL pragmas** set on every `_connect()`: `journal_mode=WAL`,
  `synchronous=NORMAL`, `busy_timeout=5000` (ms). Schema DDL
  (`CREATE TABLE IF NOT EXISTS ...`) only runs once — guarded by
  `not db_path(project).exists()` at connect time — so later connects (same
  or a different process/connection) never re-run DDL. This matters for the
  WAL-concurrency oracle: re-running `executescript` DDL on every connect
  risked taking a schema lock while a manual writer connection held an open,
  uncommitted `BEGIN IMMEDIATE` in the test, which would have made the test
  flaky/order-dependent for reasons unrelated to what it's actually testing.
- `db_path(project) -> Path` exposed as a small public helper (not
  underscore-prefixed) since a caller legitimately needs the file location
  (ops/backup tooling later, and the WAL-concurrency test needs to open a
  second raw connection to the same file).
- `replay()` in `storage_sqlite.py` is byte-identical in shape to the file
  backend's: fold `apply_event` over `iter_events()` into a fresh dict. No
  SQL needed beyond what `iter_events` already provides — it never touches
  the `states` table (pure audit, as A.3 specifies).

## Test file

`tests/test_storage_sqlite.py` — new, independent of `test_storage.py`,
covering all 5 oracles. Uses a local `sqlite_backend` fixture layered on the
shared `tmp_state` fixture (`monkeypatch.setenv("NYXLOOM_STATE_BACKEND",
"sqlite")`).

## Status

Implementation complete. See SP01-REPORT.md for gate evidence.

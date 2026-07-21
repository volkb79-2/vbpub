# SP01 — SQLite event/state store backend — REPORT

Branch: `feat/state-p01-sqlite-backend`
Worktree: `/workspaces/vbpub/.worktrees/state-p01-sqlite-backend`
Final commit: `f5de1056ba995a939219b2d95bde00db467eeea0`
(prior commit `2b40047` = the implementation; `f5de105` = added rollback-branch
coverage tests after the first gate run flagged them — see below)

## Gate evidence (the ONLY ship signal)

Ran in `tester-unified:local`, exactly the command given in the handoff, from
`main` via the worktree bind mount:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -lc 'cd /workspaces/vbpub/.worktrees/state-p01-sqlite-backend/nyxloom && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m coverage run --source=src/nyxloom -m pytest tests -q && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m coverage json -o /tmp/nyxloom-cov.json && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main \
      --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom' ; echo GATE_EXIT=$?
```

**First run** (commit `2b40047` only) — full suite green, but diff-coverage
FAILED at 95.2% (119/125): the `except Exception: conn.rollback(); raise`
branches in the STANDALONE `append_event` and `save_state` transactions
(distinct from `append_and_apply`'s, which the atomicity oracle test already
covered) had no test forcing a mid-transaction failure through them.

```
diff-coverage FAIL: 119/125 changed executable lines covered (95.2% < 100.0% floor). Uncovered changed lines:
  src/nyxloom/storage_sqlite.py: [231, 232, 233, 274, 275, 276]
GATE_EXIT=1
```

Added `test_append_event_standalone_rolls_back_on_failure` and
`test_save_state_standalone_rolls_back_on_failure` (commit `f5de105`), each
monkeypatching the relevant internal seam (`_insert_event` /
`_upsert_state_row`) to raise, asserting the exception propagates and nothing
persisted. **Re-ran the full gate command from scratch** (fresh container,
same command, no shortcuts):

```
........................................................................ [  7%]
........................................................................ [ 14%]
........................................................................ [ 21%]
........................................................................ [ 28%]
........................................................................ [ 35%]
........................................................................ [ 43%]
......................................................x................. [ 50%]
........................................................................ [ 57%]
........................................................................ [ 64%]
........................................................................ [ 71%]
........................................................................ [ 78%]
........................................................................ [ 86%]
........................................................................ [ 93%]
...................................................................      [100%]
Wrote JSON report to /tmp/nyxloom-cov.json
diff-coverage OK: 125/125 changed executable lines covered (100.0% ≥ 100.0% floor)
GATE_EXIT=0
```

**PASS: `GATE_EXIT=0`, `diff-coverage OK: 125/125 changed executable lines
covered (100.0% ≥ 100.0% floor)`.** The full nyxloom test suite (this repo's
entire `tests/` tree, not just the new file) ran green in the same pass — one
pre-existing `x` (xfail) at 50% is unrelated to this change (present before
this branch too).

A local devcontainer venv run (`PYTHONPATH=src python3 -m pytest tests -q`,
Python 3.14.6, sqlite3 3.46.1) was used ONLY as a fast dev loop while iterating
— per CLAUDE.md's cockpit-vs-gating-test-runner rule, that run is NOT a ship
signal; the `tester-unified` run above is.

## Oracle-by-oracle evidence

**Oracle 1 — API parity.** `tests/test_storage_sqlite.py`:
- `test_append_and_apply_updates_load_state` — append_and_apply -> load_state
  reflects the transition.
- `test_load_state_missing_task_returns_none`.
- `test_list_states_returns_all_tasks_sorted_by_task_id` — matches the file
  backend's `sorted(d.glob("*.json"))` ordering contract.
- `test_iter_events_orders_by_sequence_and_since_filters` — seq order,
  `since=` filtering, and that reconstructed `Event.project`/`.actor.kind`
  are correct despite `project` not being a stored column (A.0: one DB file
  per project — reconstructed from the caller's own argument).
- `test_append_event_standalone_has_no_projection_effect` — matches the file
  backend's `append_event`, which never touches statefiles.
- `test_save_state_standalone_write_appends_no_event` — the doctor
  `rebuild(write=True)` recovery path.
- `test_atomicity_validate_before_append_still_blocks_illegal_transitions` —
  the P36 validate-before-append guarantee holds on the SQLite backend too
  (zero side effects on an illegal transition).

**Oracle 2 — atomicity (the whole point).**
`test_atomicity_upsert_failure_rolls_back_event_and_projection`: monkeypatches
`storage_sqlite._upsert_state_row` to raise AFTER the event INSERT but inside
the same `BEGIN IMMEDIATE` transaction; asserts via FRESH reads
(`storage.iter_events`, `storage.load_state` — not the caller's in-memory
`states` dict) that neither the event row nor the projection change
persisted. Plus the two rollback-branch tests added after the first gate run
(`test_append_event_standalone_rolls_back_on_failure`,
`test_save_state_standalone_rolls_back_on_failure`) covering the OTHER two
transactional entry points' own rollback paths.

**Oracle 3 — `seq` gap-free monotonic.**
`test_seq_gap_free_monotonic_across_appends` (1..6, no gaps across 6
appends) and `test_seq_no_gap_after_rolled_back_attempt` (a bonus finding: a
rolled-back attempt does not just fail to persist, it does not consume a
`seq` value either, because the AUTOINCREMENT bump lives inside the same
rolled-back transaction as the event INSERT — the next successful append
gets the value the failed one would have used).

**Oracle 4 — `replay()` audit matches.**
`test_replay_matches_incrementally_applied_states`: after three
`append_and_apply` calls (a TASK_CREATED seed, two transitions, one
PROGRESS_RECORDED), `storage.replay(project)` (rebuilt purely from the events
table) equals `storage.list_states(project)` (the incrementally-applied
projection) via `to_dict()` equality.

**Oracle 5 — concurrent writer+reader under WAL.**
`test_concurrent_reader_sees_consistent_prior_snapshot_under_wal`: opens a
second raw connection, starts `BEGIN IMMEDIATE`, writes an event + a
projection UPSERT WITHOUT committing, then reads via `storage.load_state`/
`storage.iter_events` (fresh connections) and asserts the OLD snapshot is
seen (no torn/partial read of the in-flight write); after the writer commits,
a fresh read sees the new snapshot.

## Deviations from the handoff contract

1. **`states` schema extends A.1's literal column list** with one additional
   `data TEXT NOT NULL` column (the full `TaskStateFile.to_dict()` JSON
   blob), which is what `load_state`/`list_states` actually reconstruct from.
   All of A.1's literal columns (task_id PK, project, state, since,
   handoff_path, notes, attempts, schema_version) are still present and
   populated. Rationale (full detail in SP01-LOG.md): A.1's literal list
   covers only a subset of `TaskStateFile`'s actual dataclass fields (missing
   `wave_id`, `paused`, `blocker`, `gate_results`, `leases_held`,
   `progress_units`, `merge_commit`); Oracle 1 (API parity) requires all of
   these to round-trip (e.g. `PAUSE_SET`/`LEASE_ACQUIRED`/`GATE_FINISHED`
   must survive a `load_state`). Reusing `TaskStateFile.to_dict()`/
   `.from_dict()` (already exhaustively tested via `test_types.py`) avoids
   hand-rolling a second, drift-prone copy of the nested-dataclass
   (de)serialization (`Attempt`/`GateResult`/`Blocker` each have their own
   `_Serde` round-trip) across individual SQL columns.
2. **`events` table has no `project` column** (A.1 doesn't list one either,
   but calling this out explicitly): per A.0's "one DB file per project"
   decision, `Event.project` is supplied by the caller's own `project`
   argument when reconstructing a row, not persisted redundantly.
3. Worktree branched from `main` at `622d4cb` (the worktree's actual `main`
   tip at `git worktree add` time), one commit past the `9c22b51` cited in
   the handoff (that commit — `docs(nyxloom): state-integrity plan` — is
   itself the plan doc this package implements; `622d4cb` is an unrelated
   pwmcp doc change). No conflict with scope.touch.

No BLOCKED conditions were hit — every oracle was satisfiable within
`storage.py` + `storage_sqlite.py` (new) + `test_storage_sqlite.py` (new).
`paths.py`/`types.py`/`config.py` were read but not modified, per scope.

## Files changed (pathspec-scoped, as committed)

- `nyxloom/src/nyxloom/storage.py` (modified — selector + 7 guard clauses only)
- `nyxloom/src/nyxloom/storage_sqlite.py` (new)
- `nyxloom/tests/test_storage_sqlite.py` (new, 14 tests)
- `nyxloom/docs/handoff/state-integrity/SP01-LOG.md`, `SP01-REPORT.md` (new)

Not merged — per the contract, the controller merges and deploys.

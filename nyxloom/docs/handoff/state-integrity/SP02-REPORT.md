# SP02 — file→SQLite event-store importer — REPORT

Branch: `feat/state-sp02-importer`
Worktree: `/workspaces/vbpub/.worktrees/state-sp02-importer`

## Provenance note (controller-authored)
The implementation agent committed the code + tests (`7a137f5`) and a blank-line
coverage fix (`8e0011b`), but **parked on a background monitor before recording its
gate result or writing this REPORT** — the known nyxloom false-done turn-park. The
controller (Opus review) therefore: (a) stopped the parked agent; (b) ran the
authoritative gate itself; (c) confirmed the agent's own committed blank-line test
closes the single diff-coverage gap the gate had flagged (`migrate_store.py:100`,
the `if not line: continue` skip); (d) authored this REPORT from that authoritative
run. (A redundant second blank-line test the controller had started was discarded in
favor of the agent's committed one.)

## Gate evidence (authoritative — controller-run in `tester-unified:local`)
```
diff-coverage OK: 85/85 changed executable lines covered (100.0% ≥ 100.0% floor)
SP02_REGATE_EXIT=0
```
Full nyxloom suite green (1 pre-existing unrelated xfail).

## Oracles (`tests/test_migrate_store.py`)
1. **Zero-divergence import** — a 6-event / 2-task file-backend fixture imports with
   the SQLite projection exactly equal to the file backend's (`to_dict()` equality on
   both `list_states` and `replay`). ✓
2. **Backup preserved + source retired** — `events.jsonl.pre-sqlite` == original
   content; `events.jsonl` gone. ✓
3. **Idempotent** — a second run is `already-migrated`, no double events, no error. ✓
4. **Corrupt line reported** — `MigrationError` names the 1-based line; source NOT
   renamed and nothing inserted (fail-before-write). ✓
5. **Event order/seq preserved** — imported order == source order; seq gap-free 1..N. ✓

Safety branches also covered: crash-recovery exact-match (skip re-insert, still
verify+rename); partial-mismatch **refuses to guess** (raises rather than
double-importing); both divergence shapes (on-disk content differs; statefile absent)
abort without renaming; blank-line/whitespace tolerance (the fix).

## Ordering constraint
`migrate-store` retires the file backend for a project (renames `events.jsonl`), so it
is RUN against a live project only at the **SP03 cutover**, after the daemon default
flips to SQLite. SP02 only builds + tests it (temp fixtures), never runs it live here.

## Review (Opus)
Reviewed `migrate_store.py`: symmetric divergence check, refuse-to-guess partial-import
safety, corrupt-line-before-any-write, `doctor._replayable_projection` reused read-only.
Tests non-hollow (every branch incl. the safety paths, real file-backend fixtures).
`cli.py` block anchored after `render` (disjoint from SP04's after-`resync` anchor).
Merged by controller via merge-tree + CAS.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>

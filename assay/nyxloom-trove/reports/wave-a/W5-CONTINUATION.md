# W5 continuation (checkpoint 1)

State: everything that needs no container is committed and green (HEAD after this file: see `git log`; code last at `a0cca9db`,
docs `d2143b06`). Remaining work needs the PostgreSQL container and a free host.

## Done

Step 0 fix; Work 0b class map (in `W5-REPORT.md`); all fixtures (hash-verified); `gate/python/qualify_sql.py`; both scripts;
`gate/tests/test_qualify_sql.py` (T0-T7, minus three witness tests); gate wiring (`run_sql_qualification`, cleanup); adapter test
rewrites; stale refs; docs; decisions; backlog B126/B132; five MANIFEST lines. Non-container oracle breaks logged in `W5-LOG.md`.

## Remaining (in order)

1. **Host check.** `docker ps --no-trunc --format '{{.Names}}'`; if any `run-gate-*` name, wait (re-check every 2 min, wait window ends
   11:15 UTC; a helper exists: `/tmp/claude-1003/-workspaces-vbpub/0eeb333f-34bb-4155-942f-6ae260e97f0b/scratchpad/w5/waitfree.sh <deadline-epoch>`).
   Blocker seen: `run-gate-vbpub-mutation-3747550-1790644029` (not W5's). If still busy at 11:15 -> BLOCKED, record in the LOG.
2. **Work 0 measurement.** Driver (throwaway, scratchpad, never committed):
   `cd <wt>/assay && n="run-gate-assay-sql-$$-$(date +%s)"; nice -n 19 ionice -c3 python3 -I <scratchpad>/w5/measure.py "$n" dev-gates.slice <scratchpad>/w5/out-$$ ;`
   then `docker rm -f -v "$n"` (the driver's context manager also removes it). Records baseline, per-row apply exit / dump state /
   `ASSAY_SQL_FAILED` / bucket, O4-residue, M11, O5, `df` after each apply and after the witness, and the peak tmpfs use %. Put the table in
   `W5-REPORT.md` §Step 0. BLOCKED if a bucket differs from the matrix, the residue re-apply exits 0, or max tmpfs use > 50%.
   Start the container only with the permission text of the dispatch (name pattern, `--cgroup-parent=dev-gates.slice`, one at a time,
   remove by exact name, never pull).
3. **Work 5 CLI runs.** `s="$(mktemp -d)"; nice -n 19 ionice -c3 python3 -I gate/python/qualify_sql.py --scratch "$s/sql" --container-name "run-gate-assay-sql-$$-$(date +%s)" --cgroup-parent "$CGROUP_PARENT_DEV_GATES" --witness-out gate/python/fixtures/sql/expected/sql-r2-witness.json; rm -rf -- "$s"`
   (`docker ps` check first; the `expected/` dir must exist: create it via the Write tool by writing the witness through `--witness-out`
   after `mkdir`, or create a placeholder-free directory first). Review every row of the witness (24 entries: 21 killed, K02/K25
   survived, K09 equivalent; kill signals name their ids). Then rerun WITHOUT `--witness-out` -> stdout `ASSAY_SQL_QUALIFIED=1`,
   exit 0 (R1). R1 break: copy `gate/python/fixtures/sql` to a tmp dir, overwrite its `tests/K05.sql` with `SELECT 1;`, pass
   `--fixture-root` -> exit 1 naming K05; record. Record each container start/removal.
4. **Re-add the witness tests** from `<scratchpad>/w5/pending-witness-tests.py` into `gate/tests/test_qualify_sql.py`, replacing the
   `#: PENDING ...` comment block just before `test_require_witness_commit_matches_accepts_the_disposable_head`.
5. Fill the LOG (R1 result, container table, Step 0 summary), commit the witness + tests, run `gate/tests` and `tests analysis/tests`
   once each (serial, `nice -n 19 ionice -c3`), known-red only `test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` (B134).
6. Final: `git status --short --ignored assay` (no new `!!`), commit, write `READY-FOR-GATE <last code commit>` in the LOG.

## Seams

* `gate/python/qualify_sql.py`: `run_qualification` (flow), `ThrowawayPostgres` (`_start`, `_remove`), `check_sites`, `derive_bucket`,
  `check_witness_verdict`, `capture_witness`, `main`.
* `tools/tester-unified-gate.sh`: `run_sql_qualification`, `cleanup_assay_gate_container`, `run_registered_gate`.
* Measurement driver imports the harness (`q.ThrowawayPostgres`, `q.check_sites`, ...); it is the only place `df` is sampled per apply.

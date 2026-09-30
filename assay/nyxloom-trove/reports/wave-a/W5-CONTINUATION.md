# W5 continuation (checkpoint 2: BLOCKED on carver QUESTION 1)

State: CD50 (shared-host opt-in) is implemented and committed (`98195e04`, focused tests green). Work 0 was measured on PostgreSQL 18.6
and found a real contradiction: the committed probes `K18.sql` and `K20.sql` fail on the unmutated schema because 18.6 reports
`ON DELETE RESTRICT` as SQLSTATE 23001 (`restrict_violation`), not 23503. Details and full measurements: `W5-REPORT.md` §Step 0 and
`W5-LOG.md` (Work 0 result, QUESTIONS). Nothing else is broken; the rest is mechanical once the carver answers.

## Blocked on

QUESTION 1 (`W5-LOG.md`): may `K18.sql` and `K20.sql` catch `foreign_key_violation OR restrict_violation`? A scratch copy with exactly that
change made all 24 buckets match the matrix, the witness `FAIL MUTANTS_SURVIVED` with `check_witness_verdict` ok, tmpfs peak 22%.

## Next (after the decision, in order)

1. Apply the decided probe change (Edit tool), update the pinned probes hash in `verify_fixture_hashes` and its T0 test, and any brief text.
2. Container steps (CD50 permission: other projects' `run-gate-*` may run; `docker ps --no-trunc --format '{{.Names}}'` before every start,
   wait 2 min if any `run-gate-assay-sql-*` exists (max 30 min); one W5 container; remove by exact name only; pass `--allow-shared-host`):
   `mkdir -p gate/python/fixtures/sql/expected`, then
   `s="$(mktemp -d)"; env -u FORCE_COLOR nice -n 19 ionice -c3 python3 -I gate/python/qualify_sql.py --scratch "$s/sql" --container-name "run-gate-assay-sql-$$-$(date +%s)" --cgroup-parent dev-gates.slice --allow-shared-host --witness-out gate/python/fixtures/sql/expected/sql-r2-witness.json; rm -rf -- "$s"`.
   Review all 24 witness rows (21 killed, K02/K25 survived, K09 equivalent), rerun without `--witness-out` (stdout `ASSAY_SQL_QUALIFIED=1`,
   R1), then R1 break (copy `gate/python/fixtures/sql` to a tmp dir, `tests/K05.sql` = `SELECT 1;`, `--fixture-root` -> exit 1 naming K05).
3. Re-add the witness tests from `<scratchpad>/w5/pending-witness-tests.py` (scratchpad
   `/tmp/claude-1003/-workspaces-vbpub/0eeb333f-34bb-4155-942f-6ae260e97f0b/scratchpad/w5/`) replacing the `#: PENDING` block in
   `gate/tests/test_qualify_sql.py`.
4. Fill the LOG, run `gate/tests`, `tests`, `analysis/tests` once each, serial, `env -u FORCE_COLOR nice -n 19 ionice -c3` (known red: only B134).
5. `git status --short --ignored assay` (no new `!!` beyond `__pycache__`), commit with explicit paths, write `READY-FOR-GATE <hash>` in the LOG.

## Seams

* `gate/python/qualify_sql.py`: `ThrowawayPostgres._check_host` (CD50), `run_qualification`, `check_sites`, `derive_bucket`, `capture_witness`, `main`.
* `gate/python/fixtures/sql/tests/K18.sql`, `K20.sql` (the two probes to change), `verify_fixture_hashes`.
* `tools/tester-unified-gate.sh`: `run_registered_gate` (CD50 entry), `run_sql_qualification`.
* Scratch drivers (never committed): `scratchpad/w5/measure.py` (`W5_FIXTURE_ROOT` env selects a fixture copy), `diag.py`, `fixture-patched/`.

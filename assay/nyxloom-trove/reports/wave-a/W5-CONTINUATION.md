# W5 continuation (final: READY-FOR-GATE ae5e97f8)

Nothing remains for the implementer. State: CD50 (shared-host opt-in), CD53 (K18/K20 `OR restrict_violation`, probes digest
`b5c4eb283dc01ec3d8b15f2441363d5bea9312a49e025bd9eaf318086fd41e10`) and CD54 (gate-test env pin `_host_environ`) are committed; Work 0 re-run
matched the matrix; the witness `gate/python/fixtures/sql/expected/sql-r2-witness.json` is committed and all 24 rows were reviewed; R1 rerun and
R1 break done; the three witness tests are re-added. Suites: `gate/tests` 368 passed / 11 skipped, `tests` 5436 passed / 1 failed (B134 only),
`analysis/tests` 224 passed.

Next is the controller's gate run and the fresh adversarial review (`W5-LOG.md` "For the controller's gate run"; review scope in the brief's
`Review:` line). Details: `W5-REPORT.md` §Step 0 and §Work 5, `W5-LOG.md`.

## Seams

* `gate/python/qualify_sql.py`: `PROBES_SHA256`, `verify_fixture_hashes`, `ThrowawayPostgres._check_host` (CD50), `compare_with_witness`, `main`.
* `gate/python/fixtures/sql/tests/K18.sql`, `K20.sql` (CD53), `expected/sql-r2-witness.json`.
* `gate/tests/test_distribution_gate.py::_host_environ` (CD54), `gate/tests/test_qualify_sql.py` (witness tests after T4).
* `tools/tester-unified-gate.sh`: `run_registered_gate`, `run_sql_qualification`.

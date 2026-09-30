# W5 log -- self-contained SQL qualification (B126, A-480)

Worktree `/workspaces/vbpub/.worktrees/wave-a-w5-sql` (branch `wave-a-w5-sql`, project dir `assay/`).
Base `c8e6b783` (W4 reviewed head `21a7b1f0` merged with landing: W3, W1, W2, W6).

## Commits

| # | Hash | What |
|---|---|---|
| 0 | `2faa6426` | Step 0 integration fix: `tests/core/test_import_contracts.py` allows `__init__`-free `*_support.py` helpers at the tests root (CD23 amended; W4+W6 integration) |
| 1 | `a0cca9db` | Fixtures (schema, 21 probes, matrix, both scripts), `qualify_sql.py`, `gate/tests/test_qualify_sql.py`, gate wiring + T7, adapter test rewrites, stale refs, dstdns corpus removed (after the class map) |
| 2 | `d2143b06` | Docs (README, DESIGN-GUIDE §11, CONSUMERS), decision notes A-280/A-286/A-289, backlog B126/B132, five MANIFEST lines |
| 3 | (this checkpoint) | W5-LOG, W5-CONTINUATION |

## Step 0 (integration fix) checks

* `tests/core/test_import_contracts.py::test_the_tests_root_holds_only_conftest_and_fixtures`: PASS with the new body.
* Planted `tests/test_x_support.py`: RED. Planted `tests/helper.py`: RED. Both plants removed (`git status` clean afterwards).

## Fixture hashes (all match the brief)

* `01-schema.sql` sha256 `b7b07e97...3af1`, 76 lines, LF-terminated.
* 21 probes concatenated in id order: 4673 bytes, sha256 `12f564d9...0ce0`.
* `matrix.json` equals `json.dumps(rows, indent=2) + "\n"` (asserted by a test); 24 rows.
* `check_sites` (the tag rule) passes on the real schema: 24 sites, one per tag, none on the trap lines.

## Work 0b (class map)

Done before any `git rm`: exactly 13 classes, counts 38/36/28/11/20/2/12/1/11/1/3/2/6 = 171. Table in `W5-REPORT.md`
§dstdns classes. The `tests/fixtures` copy of the `21-` file was `cmp`-identical to the corpus copy.

## Anchors that shifted (re-resolved by content)

* Brief's `tools/tester-unified-gate.sh:756-765` gone (W4 rewrote the file); wiring placed by content: `cleanup_assay_gate_container`,
  above `# --- entry points`, and after `run_registered_tester_container` in `run_registered_gate`.
* Old test now at `gate/tests/test_gate_qualify_dstdns_sql.py` (CD31) -> `git mv` to `gate/tests/test_qualify_sql.py`.

## Oracles (positive result, deliberate break)

| Oracle | Positive | Deliberate break (applied, red, reverted) |
|---|---|---|
| T0 fixture hashes | `verify_fixture_hashes` passes on the real fixtures | the negative is the test itself: one extra byte in a tmp copy of the schema / of `K05.sql` raises (both tests green) |
| T1 `check_sites` | passes: 24 sites | negatives are tests: `x integer NOT NULL,` added -> names `line N ... untagged line`; line 14 without `UNIQUE` -> names K10; K06/K25 operators swapped -> names both; tag absent from matrix -> named |
| T2 matrix/probes | unique ids and (line, operator), tags == ids, each operator has a killed row, probe iff killed | negatives are tests: `K05.sql` removed -> `missing K05`; `K02.sql` added -> `stray K02` |
| T3 `derive_bucket`/`parse_failed_ids` | five branches, equal dump with exit 1 -> equivalent, `{K24}` for K05 raises, none/two ids/malformed | BREAK moved `exit_code == 0 -> survived` above the equal-dump branch: `test_derive_bucket_takes_the_five_branches_in_order` RED; reverted |
| T4 `cross_check`/`check_witness_verdict` | accepts a built verdict | BREAK 1: per-row check replaced by `len(buckets) != 1` -> 4 tests RED (bucket mismatch, K05 survived, K01/K02 swap, hung); reverted. BREAK 2 (steps 1 and 2 swapped): `test_an_incomplete_witness_exits_3_before_any_shape_check` (exit 1 instead of 3) and `test_completeness_is_checked_before_the_fail_requirement` RED; reverted |
| T5 container mechanics | all stubbed-`_run` tests green (exact `docker run` argv, digest inspect, rm last on failsafe/QualificationError/SIGTERM/df-timeout, Conflict -> no rm, image absent, no docker, host busy, sentinel handler restored) | BREAK: `main` installs `lambda *_: None` instead of `sys.exit(3)`: `test_sigterm_becomes_exit_3_and_the_container_is_still_removed_last` RED; reverted |
| T6 dstdns-free tree | no token in tracked `src tests gate tools` (minus `tests/fixtures/verdicts/`) | negative is a test: a token planted in a tmp `.sh` is found; a bare `dstdns` stays legal |
| T7 gate wiring | green path prints `ASSAY_GATE_PHASE=sql-qualified` once between tester and finisher; red tester never reaches the harness; harness exit 1, marker+exit 1, marker+extra line, exit 3 (gate exit 3, diagnostic, no receipt/COMPLETE), clone HEAD != commit; trap issues `docker rm -f -v <name>` (name read from the stub log) and removes the scratch | BREAK: deleted `[[ $rc -eq 0 ]] \|\| die`: `test_a_failing_harness_stops_the_gate...` and `test_the_marker_with_a_non_zero_exit_is_still_a_failure` RED; restored |
| R1 CLI + witness | PENDING (needs the container) | PENDING: `--fixture-root` copy with `tests/K05.sql` = `SELECT 1;` -> exit 1 naming K05 |
| R2 gate log order | PENDING (controller's gate run) | none (T7 covers it) |

## CD50 -- shared-host opt-in (operator 2026-09-30)

Implemented exactly as specified: gate (`run_registered_gate`: validation before `docker ps`; `=1` refuses `run-gate-assay-*`, prints
`ASSAY_GATE_SHARED_HOST=` on stdout otherwise), `run_sql_qualification` (`--allow-shared-host` iff exactly `1`), harness
(`--allow-shared-host`, `ThrowawayPostgres(..., allow_shared_host=False)`, `_check_host`).

| Test | Where | Oracle |
|---|---|---|
| G1 `=1`, ps `run-gate-x/y/other` -> `LAUNCHED`, `ASSAY_GATE_SHARED_HOST=run-gate-x,run-gate-y`, receipt unchanged | `test_distribution_gate.py::test_the_shared_host_opt_in_lets_other_projects_gates_run_alongside_and_prints_them` | green |
| G2 `=1`, ps has `run-gate-assay-selfhosted-1-2-3` + `run-gate-x` -> exit 3, only the assay name, receipt bytes, not launched, one docker call | `::test_the_shared_host_opt_in_still_refuses_another_assay_gate` | BREAK 1 (`if [[ -n "$assay_names" ]]` -> `if [[ -n "" ]]`): RED; reverted with Edit |
| G3 `=yes` -> non-zero, message, not launched, receipt bytes, zero docker calls | `::test_a_shared_host_opt_in_value_other_than_empty_or_one_is_refused_before_docker` | green (validation sits before `docker ps` by construction; the zero-call assertion pins it) |
| G4 `run_sql_qualification` passes `--allow-shared-host` for `=1` only (absent for unset, empty, `true`, `0`) | `test_qualify_sql.py::test_the_shared_host_flag_reaches_the_harness_only_for_the_exact_value_one` + `..._is_absent_otherwise` (4 cases) | BREAK 3 (`if [[ ... == 1 ]]` -> `if true`): the 4 absent cases RED; reverted |
| S1 allowed, ps `run-gate-x` -> container starts, stderr `ASSAY_SQL_SHARED_HOST=run-gate-x` | `test_qualify_sql.py::test_shared_host_lets_other_projects_gates_run_and_names_them_on_stderr` | green |
| S2 allowed, ps `run-gate-x`, `run-gate-assay-sql-9-1` -> exit 3, only the sql name, nothing started, no `docker rm` | `::test_shared_host_still_refuses_another_sql_qualification_container` | BREAK 2 (`if sql_busy:` -> `if False:`): S2 RED (and `test_qualify_sql` G-side unaffected); reverted |

The three breaks were applied with Edit, observed red, reverted with Edit and never committed (the green re-run follows the revert).

**Lane environment (CD50 item 5): the lane process inherits the caller's environment.** `assay/run-gate.toml` `tester-unified` is
`environment = "bare-host"`, which resolves to `{}` ("the literal old host behaviour", `run-gate-project/run_gate.py:947-948`).
The lane child is launched at `run_gate.py:8147` (`subprocess.Popen(argv, cwd=..., env=run_env)`) or `:8182`
(`subprocess.run(argv, cwd=..., env=run_env)`) with `run_env = None` (inherits) or `dict(os.environ)` plus the profiler token
(`:8108-8111`), and at `:7957` (`subprocess.Popen(argv)`, inherits). No `os.environ.clear/pop/del` and no `env -i` anywhere in
`run_gate.py`. So `ASSAY_GATE_ALLOW_SHARED_HOST=1 run-gate.py tester-unified` reaches `tester-unified-gate.sh`. Not BLOCKED.

## Collect counts (collect-only, after commit 2)

* `gate/tests`: 366 collected.
* `tests analysis/tests`: 5661 collected.
* Focused run after commit 1: `gate/tests/test_qualify_sql.py gate/tests/test_distribution_gate.py tests/adapters/sql tests/core/test_import_contracts.py` = 308 passed.

## Containers started

None so far. Host busy since the first `docker ps` of this session (about 08:18 UTC): `run-gate-vbpub-mutation-3747550-1790644029`
(not started by W5, "Up 7-8 hours"). Polling every 2 minutes with a background loop; the 3-hour wait ends 11:15 UTC.
No `run-gate-assay-sql-*` container existed.

## Deviations

1. **W4 tests adjusted.** `gate/tests/test_distribution_gate.py`: the two existing tests that run `run_registered_gate` through a green
   stubbed tester now also stub `run_sql_qualification() { :; }` (the entry now includes the SQL phase; the phase has its own tests).
2. **Local `gate_functions` fixture** in `test_qualify_sql.py` instead of importing W4's: the gate's pyflakes phase (`gate/tests` is
   linted) flags an imported fixture used as an argument (`redefinition of unused`). It drops the entry-point dispatch like W4's and
   leaves the hard-coded venv path alone (no function the T7 tests call uses it).
3. **Extra tests beyond the brief:** `run-assertions.sh` and `schema-gate.sh` are run against PATH stubs (`psql`, `pg_dump`) to prove
   the kill-signal text (`schema test command failed (exit 1): ASSAY_SQL_FAILED=K05,K24`), the none case and the exit-2 infrastructure case;
   probe well-formedness; a lane-TOML render test.
4. **Witness tests deferred.** `compare_with_witness` round trip, its corrupted-bucket refusal and the witness-is-current-schema test are
   saved in the scratchpad (`w5/pending-witness-tests.py`) and are re-added with the committed witness (they need
   `gate/python/fixtures/sql/expected/sql-r2-witness.json`, which only the container run can produce).
5. `check_sites` collects all disagreements into one message (the brief names the ids or the line; it does not fix a format).
6. `main` also maps a `_run` `TimeoutExpired` to exit 3 (`command timed out`), per the brief's list.

## For the controller's gate run

* T7 in the real gate (`gate/tests/test_qualify_sql.py`, part of `gate/tests` in `tester-unified`).
* The gate stdout order: `ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_GATE_PHASE=sql-qualified`, `ASSAY_REGISTERED_GATE_RECEIPT=...`,
  `ASSAY_REGISTERED_GATE_COMPLETE=1` (R2).
* Exit 3 with `ASSAY_GATE_INCONCLUSIVE=sql-qualification — rerun` or `host busy — rerun` at entry means rerun later; any other
  non-zero exit is real (CD29).
* The pyflakes lint phase covers `gate/tests/test_qualify_sql.py` (passes locally through `test_the_shipped_source_tree_is_pyflakes_clean`).

## Status

CHECKPOINT (not READY-FOR-GATE): the container-dependent steps (Work 0 measurement, Work 5 CLI runs, the committed witness) are not done
because the host stayed busy. See `W5-CONTINUATION.md`.

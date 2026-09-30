# W5 log -- self-contained SQL qualification (B126, A-480)

Worktree `/workspaces/vbpub/.worktrees/wave-a-w5-sql` (branch `wave-a-w5-sql`, project dir `assay/`).
Base `c8e6b783` (W4 reviewed head `21a7b1f0` merged with landing: W3, W1, W2, W6).

## Commits

| # | Hash | What |
|---|---|---|
| 0 | `2faa6426` | Step 0 integration fix: `tests/core/test_import_contracts.py` allows `__init__`-free `*_support.py` helpers at the tests root (CD23 amended; W4+W6 integration) |
| 1 | `a0cca9db` | Fixtures (schema, 21 probes, matrix, both scripts), `qualify_sql.py`, `gate/tests/test_qualify_sql.py`, gate wiring + T7, adapter test rewrites, stale refs, dstdns corpus removed (after the class map) |
| 2 | `d2143b06` | Docs (README, DESIGN-GUIDE §11, CONSUMERS), decision notes A-280/A-286/A-289, backlog B126/B132, five MANIFEST lines |
| 3 | `c45d0e1b` | W5-LOG, W5-CONTINUATION (checkpoint 1) |
| 4 | `98195e04` | CD50 shared-host opt-in (gate, harness, tests G1-G4/S1-S2, docs, CD50 entry) |
| 5 | `b36e31e5` | Work 0 result (BLOCKED, QUESTION 1), W5-REPORT §Step 0, W5-CONTINUATION (checkpoint 2) |
| 6 | `74b20e7e` | CD53 and CD54 (carver decisions) |
| 7 | `b45b8ae1` | CD53: K18/K20 catch `restrict_violation`; probes digest re-pinned (`b5c4eb28...1e10`, 4717 bytes) in `verify_fixture_hashes`, its T0 test and the brief; B132 SQLSTATE note |
| 8 | `e4b4769d` | CD54: gate-test subprocess envs drop `ASSAY_GATE_ALLOW_SHARED_HOST` (`_host_environ`) |
| 9 | `ae5e97f8` | Committed witness `expected/sql-r2-witness.json` (24 rows) and the three witness tests (**last code commit**) |
| 10 | (this commit) | W5-REPORT §Step 0 re-run and Work 5, this LOG, W5-CONTINUATION (final) |

## Step 0 (integration fix) checks

* `tests/core/test_import_contracts.py::test_the_tests_root_holds_only_conftest_and_fixtures`: PASS with the new body.
* Planted `tests/test_x_support.py`: RED. Planted `tests/helper.py`: RED. Both plants removed (`git status` clean afterwards).

## Fixture hashes (all match the brief)

* `01-schema.sql` sha256 `b7b07e97...3af1`, 76 lines, LF-terminated.
* 21 probes concatenated in id order: originally 4673 bytes, sha256 `12f564d9...0ce0` (the brief's CD28 value); **amended by CD53** (K18/K20
  `OR restrict_violation`, +22 bytes each) to 4717 bytes, sha256 `b5c4eb283dc01ec3d8b15f2441363d5bea9312a49e025bd9eaf318086fd41e10`, computed by
  the harness's own `_sha256` over its own probe join (scratch `digest.py`), and pinned in `verify_fixture_hashes`, the T0 test and the brief.
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
| R1 CLI + witness | `--witness-out` capture exit 0 stdout `ASSAY_SQL_QUALIFIED=1`; all 24 rows reviewed = matrix (W5-REPORT §Work 5); rerun without `--witness-out`: stdout exactly `ASSAY_SQL_QUALIFIED=1`, exit 0 | `--fixture-root` copy with `tests/K05.sql` = `SELECT 1;` (Write tool): exit 1, stderr `ASSAY_SQL_FAILED: K05: test failed without naming K05`; copy discarded |
| CD53 probes | baseline of the fixed probes exits 0; 24/24 buckets = matrix (Work 0 re-run) | the pre-fix probes (first Work 0 run): baseline exit 1 `ASSAY_SQL_FAILED=K18,K20` |
| CD54 env pin | all 174 tests of `test_distribution_gate.py` + `test_qualify_sql.py` green with `ASSAY_GATE_ALLOW_SHARED_HOST=1` exported | `_host_environ` edited to keep the variable, opt-in exported: `test_a_busy_host_makes_the_gate_inconclusive_before_anything_else_happens` RED (1 failed, 173 passed); pin restored with Edit, 174 passed |
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

The first session (2026-09-29) started none: the host was busy with another session's `run-gate-vbpub-mutation-*` container.
The successor session (2026-09-30, CD50 permission: other projects' `run-gate-*` containers may run; one W5 container at a time; a
`docker ps --no-trunc` check before every start showed no `run-gate-assay-sql-*`) started three, all with
`--cgroup-parent=dev-gates.slice`, `--cpus 1 --memory 512m`, never pulled, each removed by its exact name via the harness context
manager (`docker rm -f -v <name>`); afterwards `docker ps -a` showed no `run-gate-assay-sql-*`. Each start printed
`ASSAY_SQL_SHARED_HOST=run-gate-vbpub-mutation-1265864-1790724554` (another session's cmru mutation gate).

| Container | Started (UTC) | Removed (UTC) | Purpose |
|---|---|---|---|
| `run-gate-assay-sql-1449332-1790732244` | 01:37:24 | 01:38:25 | Work 0 measurement, committed probes |
| `run-gate-assay-sql-1461066-1790732346` | 01:39:06 | 01:39:14 | diagnostic: raw psql of K18/K19/K20 (SQLSTATE) |
| `run-gate-assay-sql-1463321-1790732378` | 01:39:38 | 01:41:24 | Work 0 measurement, scratch-copy probes (`OR restrict_violation`) |
| `run-gate-assay-sql-1569099-1790733241` | 01:54:01 | 01:55:38 | Work 0 re-run, committed CD53 probes (driver `measure.py`) |
| `run-gate-assay-sql-1581426-1790733347` | 01:55:47 | 01:57:33 | Work 5: CLI witness capture (`--witness-out`) |
| `run-gate-assay-sql-1601018-1790733473` | 01:57:53 | 01:59:17 | Work 5: R1 rerun (no `--witness-out`) |
| `run-gate-assay-sql-1612472-1790733568` | 01:59:28 | 01:59:41 | Work 5: R1 break (`--fixture-root` copy, K05 = `SELECT 1;`) |

Third session (successor, CD53/CD54): four more starts, each preceded by `docker ps --no-trunc --format '{{.Names}}'` (no
`run-gate-assay-sql-*`; the only `run-gate-*` was `run-gate-vbpub-mutation-1265864-1790724554`, another session's), one at a time, each removed
by its exact name by the harness's context manager (times above are the wrapper's start/end; the container lived inside them); after the last,
`docker ps` showed no `run-gate-assay-sql-*`. No leftover of anyone else's was found or touched.

## Work 0 result (2026-09-30): BLOCKED -- a probe fails unmutated

Full table and cause in `W5-REPORT.md` §Step 0. Summary: PostgreSQL 18.6 reports a violated `ON DELETE RESTRICT` as SQLSTATE 23001
(`restrict_violation`), not 23503; probes `K18.sql` and `K20.sql` catch only `foreign_key_violation`, so the unmutated baseline exits 1
with `ASSAY_SQL_FAILED=K18,K20`. Buckets with the committed probes: 21 killed rows derive `killed`, K09 `equivalent`, K02/K25 raise
(no bucket, because exit is 1 not 0), witness `FAIL COMMAND_FAILED`. A scratch copy of the fixtures with `OR restrict_violation` added
to those two probes makes every measurement match the matrix (24/24 buckets, O4 residue second apply exit 3, M11 crashed, witness
`FAIL MUTANTS_SURVIVED` with `check_witness_verdict` ok), peak tmpfs 22% (max allowed 50%). No repo fixture was changed: the probes are
verbatim by CD28 and hash-pinned.

## Work 0 re-run (third session, after CD53): all buckets match; not BLOCKED

Committed fixtures, no scratch copy. Baseline exit 0; 24/24 buckets equal the matrix (21 killed each naming its own id, K02/K25 survived,
K09 equivalent); O5 differs; O4 residue first 0 / second exit 3, no dump; M11 crashed (exit 3, no dump); witness `FAIL MUTANTS_SURVIVED` with
`check_witness_verdict` ok; databases `postgres,template0,template1`; peak tmpfs 22% (limit 50%). Table in `W5-REPORT.md` §Step 0.

## QUESTIONS for the carver

(Third session: none new. Questions 1 and 2 are decided and closed below.)

1. **DECIDED by CD53 (implemented `b45b8ae1`): K18/K20 probes.** Authorize changing `gate/python/fixtures/sql/tests/K18.sql` and `K20.sql` from
   `EXCEPTION WHEN foreign_key_violation THEN RETURN;` to `EXCEPTION WHEN foreign_key_violation OR restrict_violation THEN RETURN;`
   (the only change measured to make the baseline green and all 24 rows match)? It changes the pinned concatenated-probes sha256
   (`12f564d9...0ce0`, in `verify_fixture_hashes` and its T0 test and in the brief's probe text) and needs a matching brief fix.
   Conservative reading applied: not done. After the decision the remaining work is mechanical: update the hash pin and T0 test,
   capture the witness with `--witness-out`, review its 24 rows, R1 rerun and R1 break, re-add the three witness tests
   (`<scratchpad>/w5/pending-witness-tests.py`), the three serial test runs, `READY-FOR-GATE`.
2. **DONE (`b45b8ae1`, B132 note): B133/B132 note.** The `ON DELETE RESTRICT` SQLSTATE is a PostgreSQL fact relevant to B132 (hazard-construct probe): a probe that
   only catches `foreign_key_violation` misses `RESTRICT`. Filing it there is left to the carver.

## Environment note (not B134)

`gate/tests/test_dependency_purity.py::test_the_installed_analyze_command_runs_from_the_one_wheel` fails when the shell exports
`FORCE_COLOR=3` (Python 3.14 argparse then colours `assay analyze --help`, and the test looks for the plain text
`usage: assay analyze`). It passes with `env -u FORCE_COLOR`. Not caused by W5; all runs below use `env -u FORCE_COLOR`.

## Deviations

1. **W4 tests adjusted.** `gate/tests/test_distribution_gate.py`: the two existing tests that run `run_registered_gate` through a green
   stubbed tester now also stub `run_sql_qualification() { :; }` (the entry now includes the SQL phase; the phase has its own tests).
2. **Local `gate_functions` fixture** in `test_qualify_sql.py` instead of importing W4's: the gate's pyflakes phase (`gate/tests` is
   linted) flags an imported fixture used as an argument (`redefinition of unused`). It drops the entry-point dispatch like W4's and
   leaves the hard-coded venv path alone (no function the T7 tests call uses it).
3. **Extra tests beyond the brief:** `run-assertions.sh` and `schema-gate.sh` are run against PATH stubs (`psql`, `pg_dump`) to prove
   the kill-signal text (`schema test command failed (exit 1): ASSAY_SQL_FAILED=K05,K24`), the none case and the exit-2 infrastructure case;
   probe well-formedness; a lane-TOML render test.
4. **Witness tests deferred (RESOLVED `ae5e97f8`: re-added verbatim from the scratchpad file with the committed witness).** `compare_with_witness` round trip, its corrupted-bucket refusal and the witness-is-current-schema test are
   saved in the scratchpad (`w5/pending-witness-tests.py`) and are re-added with the committed witness (they need
   `gate/python/fixtures/sql/expected/sql-r2-witness.json`, which only the container run can produce).
5. `check_sites` collects all disagreements into one message (the brief names the ids or the line; it does not fix a format).
6. `main` also maps a `_run` `TimeoutExpired` to exit 3 (`command timed out`), per the brief's list.
7. **CD54 shape (third session).** CD54 names the copied-`os.environ` helpers; the implementation is one helper `_host_environ()` in
   `gate/tests/test_distribution_gate.py` (imported by `test_qualify_sql.py`) that returns `os.environ` minus `ASSAY_GATE_ALLOW_SHARED_HOST`,
   used at every `{**os.environ, "PATH": ...}` env that reaches the gate functions (the CD32 docker stubs among them), and as `run_bash`'s
   default when no `env` is passed (a bare `env=None` inherited the variable too). Tests that set the opt-in do so explicitly on the env they
   pass, unchanged. The two git-identity envs (`**os.environ` for `git commit`) do not reach the gate and were left alone.
8. **The first-run witness stdout.** `--witness-out` and the plain run both print exactly `ASSAY_SQL_QUALIFIED=1` on stdout; the rows and
   controls are on stderr (as the harness was written). Nothing to fix.
9. `gate/tests` was run once more with `-rs` to read the skip reasons (11 skips: real-Go, real-vitest, self-hosting verdict, standalone
   fallback; all environmental); same result, so the suite count below is from the first run.

## For the controller's gate run

* Gate this branch at `ae5e97f8` (the last code commit; commit 10 is docs only). CD54 means an exported `ASSAY_GATE_ALLOW_SHARED_HOST=1` in the
  gate's shell no longer changes any `gate/tests` result; export it only to let the real SQL phase run beside another project's `run-gate-*`.
* The SQL phase starts one `run-gate-assay-sql-*` container (about 90 s, peak tmpfs 22%); it refuses to start beside another `run-gate-assay-sql-*`.
* Probes digest is now `b5c4eb28...1e10` (CD53); the committed witness is `gate/python/fixtures/sql/expected/sql-r2-witness.json`.
* T7 in the real gate (`gate/tests/test_qualify_sql.py`, part of `gate/tests` in `tester-unified`).
* The gate stdout order: `ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_GATE_PHASE=sql-qualified`, `ASSAY_REGISTERED_GATE_RECEIPT=...`,
  `ASSAY_REGISTERED_GATE_COMPLETE=1` (R2).
* Exit 3 with `ASSAY_GATE_INCONCLUSIVE=sql-qualification — rerun` or `host busy — rerun` at entry means rerun later; any other
  non-zero exit is real (CD29).
* The pyflakes lint phase covers `gate/tests/test_qualify_sql.py` (passes locally through `test_the_shipped_source_tree_is_pyflakes_clean`).

## Test runs at the final code head `ae5e97f8` (serial, `env -u FORCE_COLOR nice -n 19 ionice -c3`, each once; third session)

* `gate/tests`: 368 passed, 11 skipped, 0 failed (118 s).
* `tests`: 5436 passed, 1 skipped, 1 failed: only the known-red B134 `test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` (200 s).
* `analysis/tests`: 224 passed (7 s).
* `git status --short --ignored assay`: only `__pycache__` `!!` entries (the `.pytest_cache` my first focused runs created was removed).

## Test runs at the CD50 head (second session, for history)

* `gate/tests`: 365 passed, 11 skipped, 0 failed.
* `tests`: 5436 passed, 1 skipped, 1 failed: only the known-red B134 `test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused`.
* `analysis/tests`: 224 passed.
* The witness tests (three) are not re-added yet (they need the witness file).

## Review fixes (W5C)

Fixer session 2026-09-30 from `REVIEW-W5-code.md` (MERGE-WITH-FIXES) and CD59. Code/doc commit `e1cd59cf`. Every review fix text applied
verbatim; no site had moved.

| Finding | Fix | Test |
|---|---|---|
| W5C-1 MAJOR | `_check_host`: `docker ps` runs with `check=False`; non-zero -> `InconclusiveError("host check failed (docker ps)")` (exit 3) | `test_a_failing_docker_ps_is_inconclusive_and_nothing_starts` (`FakeDocker` gained `ps_rc`) |
| W5C-2 MAJOR | **Docs only (CD59); harness behaviour unchanged.** `CHANGES.md` `[Unreleased]` CD50 entry, `docs/CONSUMERS.md` and `docs/DESIGN-GUIDE.md` now state the two scopes: gate refuses any `run-gate-assay-*`, harness `--allow-shared-host` refuses only `run-gate-assay-sql-*`. Docstring and DESIGN-GUIDE section 11 already said the harness scope | none (S2 unchanged; no S3, as the behaviour stays) |
| W5C-3 MINOR | `run_qualification`: `verify_fixture_hashes` runs first when `fixture_root` is the default root (the R1 `--fixture-root` copy still names K05) | `test_a_pin_mismatch_fails_before_any_container_at_run_time` |
| W5C-4 MINOR | both gate tests plant an earlier receipt before `_gate(...)` and assert it is gone | `test_a_failing_harness_stops_the_gate_...`, `test_an_inconclusive_harness_makes_the_gate_exit_3_...` |
| W5C-5 MINOR | non-zero exact-name `docker rm -f -v` prints `ASSAY_SQL_RM_FAILED=exit N: '<stderr>'` (no other selector, still exact name) | `test_a_failed_container_removal_is_reported_on_stderr` (`FakeDocker` gained `rm_rc`) |
| W5C-6 MINOR | `_run` (only when `check=True`, docker argv): daemon-lost/container-not-running/no-such-container stderr -> `InconclusiveError("docker environment lost: ...")`; exit-3 lists in the module docstring and DESIGN-GUIDE section 11 extended (also with the failing `docker ps`) | `test_a_lost_docker_environment_mid_run_is_inconclusive` (3 stderr shapes), `..._does_not_raise_when_check_is_not_requested`, `test_an_ordinary_docker_failure_stays_a_qualification_error` |

Controlled breaks (Edit, fix reverted, new test watched red, fix restored with Edit; nothing committed broken):

| Break | Result |
|---|---|
| W5C-1: `docker ps` back to default `check=True` | RED: `test_a_failing_docker_ps_is_inconclusive_and_nothing_starts` (1 failed, 128 passed; message `ASSAY_SQL_FAILED: command failed (1): ['docker', 'ps', ...]`) |
| W5C-3: delete the two inserted `verify_fixture_hashes` lines | RED: `test_a_pin_mismatch_fails_before_any_container_at_run_time` (1 failed, 128 passed) |
| W5C-4 (extra): replace the trap's `rm -f` with `:` AND delete `clear_registered_gate_receipt "$worktree"` in `tools/tester-unified-gate.sh` (both, per the double clear) | RED: both planted-receipt tests (2 failed, 127 passed); script restored, `git diff -- tools` empty |
| W5C-2 | no break (docs only, CD59) |

Runs at `e1cd59cf` (serial, `env -u FORCE_COLOR nice -n 19 ionice -c3`): `gate/tests/test_qualify_sql.py` + `test_distribution_gate.py` 185 passed;
`gate/tests` once: 376 passed, 11 skipped, 0 failed; `tests/adapters/sql` once: 129 passed. `git status --short --ignored assay`: only pre-existing
`__pycache__` `!!` entries. No `.pytest_cache` (ran with `-p no:cacheprovider`).

Note for the gate run: the harness (unlike the gate entry check) tolerates another assay gate's tester container under `--allow-shared-host`; by
design (CD59), documented.

## Status

READY-FOR-GATE e1cd59cf

CD50, CD53 and CD54 are implemented; Work 0 re-run and Work 5 (witness capture, 24-row review, R1 rerun, R1 break) are done; the witness
tests are re-added; the three suites are as recorded above (only B134 red). No open QUESTIONS. See `W5-CONTINUATION.md`.

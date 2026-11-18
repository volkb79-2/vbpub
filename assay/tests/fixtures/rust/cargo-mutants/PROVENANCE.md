# cargo-mutants real-run fixtures (B245 U0)

Captured 2026-11-18 on v1001 in `tester-unified-rust:local` with cargo-mutants
27.1.0, rustc 1.99.1 and cargo-nextest 0.9.151 (`--network none`, one container
at a time, `nice`). The scripts and the raw run output are in
`assay/docs/rust-b245-u0/` (`scripts/run-u0.sh` produced every case below;
`run-output/u0.txt` is its log). Each case directory holds `subject/` (the crate
sources the run used, including its generated `Cargo.lock`) and the COMPLETE
`mutants.out/` the tool wrote, except that `lock.json` and `debug.log` (host and
container identifiers) and the per-mutant `log/` directory were dropped: the
parser reads `outcomes.json` and `diff/*.diff` only, and `log_path` values in
`outcomes.json` therefore name files that are not kept. Flags unless a row says
otherwise: `--test-tool nextest --jobs 1 --timeout 20 --cargo-arg=--offline
--cargo-arg=--locked`.

The `start_time`/`end_time` values are the real capture times (2026-11-18, about
05:5x UTC). A test that checks the report against a command window must retime
them (design §7.8); never edit these files.

| case | what it is | measured result |
|---|---|---|
| `full` | the 25-mutant synthetic subject (caught, missed, unviable, timeout) | exit 3; 16 `CaughtMutant` (Test `Failure(100)`), 7 `MissedMutant`, 1 `Unviable` (Build `Failure(101)`, no Test phase), 1 `Timeout`; judges to `BUDGET_EXCEEDED/LANE_TIMEOUT` (timeout outranks survivors) |
| `indiff` | `--in-diff in.diff` (git diff of `is_even`) | exit 2; 9 `MissedMutant` |
| `zero-mutants` | `--in-diff in.diff` with a comment-only diff | exit 0; `INFO No mutants to filter`; NO `mutants.out` at all (the case has no `mutants.out` directory on purpose) |
| `basefail-test` | a failing baseline test | exit 4; `outcomes.json` complete, only the `Baseline` outcome, `Failure`, Test `Failure(100)`, `total_mutants` 0 |
| `basefail-build` | `--locked` without a `Cargo.lock` | exit 4; `Baseline` `Failure`, Build `Failure(102)`, no Test phase, `total_mutants` 0 |
| `crashed-f4` | two-crate workspace, `--test-workspace` unset, crate `a` has no tests of its own | 5 mutants of `a` are `CaughtMutant` with Test `Failure(4)` (nextest "no tests to run") and 3 of `b` with `Failure(100)`; only `Failure(100)` is a kill |
| `w-true` | same workspace, `--test-workspace=true` | 8 `CaughtMutant`, all Test `Failure(100)` |
| `rotation` | two runs into one `--output` | `mutants.out` and `mutants.out.old` both present |
| `subdir` | crate in `sub/crate1` of a git repo; `--in-diff in.diff` (git diff `--relative=sub/crate1`) with a relative in-tree `--output out-r`; `toplevel.diff` is the same change from the repo top | exit 2; 9 `MissedMutant`; the top-level diff from the crate directory filters everything away silently |
| `methods` | method, trait impl, generic function | `function_name` is `S::bump`, `<impl T for S>::triple`, `pick`; 10 caught, 1 unviable |
| `paths` | modules `with space.rs` and `módulo.rs` | diff files are named `src__with_space.rs_line_2_col_5.diff` and `src__módulo.rs_line_2_col_5.diff` (space becomes `_`, non-ASCII is kept) |
| `unicode` | a line with `héllo→` before the operator | 4 caught; spans count characters (`s2` evidence in `docs/rust-r3a`) |
| `synthetic-signalled`, `synthetic-other` | SYNTHETIC: `full` with two caught mutants edited to Test `{"Signalled": 9}` / `"Other"` | NOT real tool output; no spike produced these statuses; used only for the `crashed` mapping |

Every real case was produced by the unmodified tool; only the two `synthetic-*`
cases are edited, and only in `outcomes.json`.

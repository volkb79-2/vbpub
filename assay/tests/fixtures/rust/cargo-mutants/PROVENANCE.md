# cargo-mutants real-run fixtures (B245 U0)

Captured 2026-11-18 on v1001 in `tester-unified-rust:local` with cargo-mutants
27.1.0, rustc 1.99.1 and cargo-nextest 0.9.151 (`--network none --memory 2g
--cpus 2`, one container, scripts under `nice`). `assay/docs/rust-b245-u0/`
holds everything needed to regenerate them: `inputs/` (the crates), `scripts/run-u0.sh`
(the capture; it records every tool exit code into a variable BEFORE any pipe) and
`scripts/make-synthetic.py` (the two synthetic cases); `run-output/u0.txt` and
`run-output/summary.txt` are the log and the exit-code summary of THIS capture.

Each case directory holds `subject/` (the crate sources the run used, with its
generated `Cargo.lock` except where noted), the COMPLETE `mutants.out/` the tool
wrote minus `lock.json`, `debug.log` (host and container identifiers) and the
per-mutant `log/` directory (the parser reads `outcomes.json` and `diff/*.diff`
only, so `log_path` values dangle by design), and `exit-code.txt`, the REAL exit
status of the tool. Flags unless a row says otherwise: `--test-tool nextest --jobs
1 --timeout 20 --cargo-arg=--offline --cargo-arg=--locked --output <abs dir>`.
`in.diff` files are real `git diff` output between two commits (with `index`
lines and hunk context).

The `start_time`/`end_time` values are the real capture times (2026-11-18, about
06:03-06:05 UTC). A test that checks a report against a command
window must retime them (design section 7.8); never edit these files.

| case | what it is | exit | measured result |
|---|---|---|---|
| `full` | the 25-mutant synthetic subject | 3 | 16 `CaughtMutant` (Test `Failure(100)`), 7 `MissedMutant`, 1 `Unviable` (Build `Failure(101)`, no Test phase), 1 `Timeout` |
| `indiff` | `--in-diff in.diff` (real git diff of `is_even`) | 2 | 9 `MissedMutant` |
| `zero-mutants` | `--in-diff in.diff`, a comment-only commit | 0 | `INFO No mutants to filter`; NO `mutants.out` and NO `--output` directory at all (`output-dir-listing.txt`); the case has no `mutants.out` on purpose |
| `basefail-test` | failing baseline test | 4 | `outcomes.json` complete, only the `Baseline` outcome, `Failure`, Test `Failure(100)`, `total_mutants` 0 |
| `basefail-build` | `--locked` with no `Cargo.lock` (so the subject has none) | 4 | `Baseline` `Failure`, Build `Failure(102)`, no Test phase, `total_mutants` 0 |
| `crashed-f4` | two-crate workspace, `--test-workspace` unset, crate `a` has no tests of its own | 0 | 5 mutants of `a` are `CaughtMutant` with Test `Failure(4)` (nextest "no tests to run"), 3 of `b` with `Failure(100)`; only `Failure(100)` is a kill |
| `w-true` | same workspace, `--test-workspace=true` | 0 | 8 `CaughtMutant`, all Test `Failure(100)` |
| `rotation` | two runs into one `--output` | 2 | `mutants.out` and `mutants.out.old` both present (`exit-code.txt` is the second run's) |
| `subdir` | crate in `sub/crate1` of a git repo; `in.diff` is `git diff --relative=sub/crate1`, `toplevel.diff` the same change from the repo top; relative in-tree `--output out-r` created empty beforehand | 2 | 9 `MissedMutant`; the same run with `toplevel.diff` from the crate directory printed `No mutants to filter` and exited 0 (`run-output/summary.txt`, `subdir-toplevel-from-crate`) |
| `methods` | method, trait impl, generic function | 0 | `function_name` is `S::bump`, `<impl T for S>::triple`, `pick`; 10 caught, 1 unviable |
| `paths` | modules `with space.rs` and `módulo.rs` | 2 | diff files named `src__with_space.rs_line_2_col_5.diff` and `src__módulo.rs_line_2_col_5.diff` (space becomes `_`, non-ASCII kept); 9 caught, 1 missed |
| `unicode` | a line with `héllo→` before the operator | 0 | 4 caught; spans count characters |
| `synthetic-signalled`, `synthetic-other` | SYNTHETIC: `full` with two caught mutants edited to Test `{"Signalled": 9}` / `"Other"` by `make-synthetic.py` | n/a | NOT real tool output; no run produced these statuses; used only for the `crashed` mapping |

Every real case is the unmodified tool output; only the two `synthetic-*` cases
are edited, and only in `outcomes.json`.

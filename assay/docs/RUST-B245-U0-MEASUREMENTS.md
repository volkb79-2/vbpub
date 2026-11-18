# B245 U0: cargo-mutants measurements and fixtures

Measured 2026-11-18 (06:03-06:10 UTC) on v1001 with cargo-mutants 27.1.0 in
`tester-unified-rust:local` (`--network none --memory 2g --cpus 2`, `nice`, one
container, no gate container; another team's guest VM was running on the host, which
is not recorded in the retained logs). Inputs,
script and logs: [rust-b245-u0](rust-b245-u0/) (`scripts/run-u0.sh` regenerates
everything below; `run-output/summary.txt` lists every tool exit code, each taken
from a variable captured before any pipe; `run-output/u0.txt` is the full log).
Fixtures: `tests/fixtures/rust/cargo-mutants/` (see its `PROVENANCE.md`). This is
work package U0 of `DESIGN-B245-CARGO-MUTANTS-INGESTION.md` and answers the
measurements of that document's section 7 item 4; section 8 of the design records
the consequences. An earlier capture of the same day was REPLACED after review
(its exit codes were pipeline statuses and its stop-gate reading used a non-empty
directory only); nothing of it is kept.

## Measured facts

**(a, c, d) A relative, in-tree `--output` works, empty or not.** Run from the
crate directory, `--output out-r` (created empty beforehand, as assay will) gave
`out-r/mutants.out` inside the crate directory and exit 2 with 9 mutants (`subdir`
case). A NON-empty pre-created `<out>` is accepted too: its content (`keepme`) was
left alone and `mutants.out` was created beside it (`nonempty-out` in
`run-output/summary.txt`).

**(b) Stop gate, with controls: only an EMPTY `<out>` is safe, `.gitignore` does not
help.** One crate whose only test fails if a given path exists in the tree under
test, five variants (`canary-*` lines of `summary.txt`):
- A control, the path never exists: baseline passes (the test itself is sound).
- B non-empty `<out>` holding `MARKER`: baseline FAILS (`Failure(100)`), the marker
  was copied into the tree under test.
- C EMPTY pre-created `<out>`, test asserts `<out>/mutants.out` is absent: baseline
  passes, the `mutants.out` the tool creates inside `<out>` is NOT in the tree under
  test.
- D `<out>` ignored by a committed `.gitignore`, holding `MARKER`: baseline FAILS:
  the tool copies ignored files too.
- E ignored EMPTY `<out>`: baseline passes.
Consequence: the design's precondition (assay creates `<out>` empty, never writes
into it, refuses a non-empty one) is necessary and sufficient for what was
measured; a committed `.gitignore` is needed for the post-run dirty check (see
below) but does not keep content out of the copy. Whether the empty `<out>`
directory itself appears in the copy was not asserted.

**Dirty tree.** `git status --porcelain` after the runs lists `?? sub/crate1/out-r/`:
an untracked in-tree `<out>` fails the post-run dirty check unless a committed
`.gitignore` covers it (design R2 is confirmed).

**(e, h) `--in-diff` path anchoring.**
- Single crate in `sub/crate1` of a git repo: `git diff --relative=sub/crate1`
  (paths `src/lib.rs`) gave 9 mutants; the plain top-level diff (paths
  `sub/crate1/src/lib.rs`) from the crate directory printed `INFO No mutants to
  filter` and exited 0 (`subdir-toplevel-from-crate`). A wrong anchor filters
  EVERYTHING away with a success exit and no error.
- Two-crate workspace whose root is the git top, REAL mutation runs from the member
  directory: the diff relative to the workspace root tested 5 mutants (all caught);
  `git diff --relative=<member>` printed `No mutants to filter`
  (`member-cwd-top-diff`, `member-cwd-rel-diff`, `root-cwd-top-diff`). `--list`
  matched the same 5 mutants from the workspace root and from the member directory.
- So the anchor is the cargo WORKSPACE ROOT (the directory of the `[workspace]`
  Cargo.toml, or of a lone package), not the process cwd and not a member. The
  generalisation to hostobs (workspace root `hostobs` inside the vbpub git top) is
  INFERRED from these two shapes; a workspace root that is a subdirectory of the
  git top with several members was not run.
- Name hazard (matrix `mkws` lines of `summary.txt`): a member DIRECTORY named `b`
  matched 0 mutants whatever the package name (`b` or `crate_b`), with exit 0 and
  `No mutants to filter`; directories `crate_b` and `b2` matched 5 from both
  working directories. A directory named `a` was NOT run; the cause of the `b`
  failure is unexplained (a guess is cargo-mutants' handling of the `b/` diff
  prefix). Treat member directories named `a` or `b` at the workspace root as
  unsupported until measured further.

**(f) Source paths with a space or non-ASCII characters.** Diff files are named
`src__with_space.rs_line_2_col_5.diff` and `src__módulo.rs_line_2_col_5.diff`: a
space becomes `_`, non-ASCII characters are KEPT (`paths` case, exit 2).

**(g) Methods, trait impls, generics.** `function.function_name` is qualified:
`S::bump`, `<impl T for S>::triple`, and plain `pick` for a generic function
(`methods` case); the `FnValue` mutant `name` repeats it.

**Cases and exit codes.** Every fixture directory carries `exit-code.txt` with the
tool's real exit status: `full` 3, `indiff` 2, `paths` 2, `rotation` 2 (second
run), `subdir` 2, `basefail-test` 4, `basefail-build` 4 (Build `Failure(102)`, no
Test phase), `zero-mutants` 0 with no `mutants.out` and no `--output` directory,
`crashed-f4` 0 (5 mutants of a crate with no tests of its own are `CaughtMutant` with
Test `Failure(4)`), `w-true` 0, `unicode` 0, `methods` 0.

## Not measured

- Whether two source paths can collide in the diff file name after the
  space-to-underscore replacement.
- A workspace root that is a SUBDIRECTORY of the git top with several members.
- Whether the empty `<out>` directory itself is copied into the tree under test.
- Why a member directory named `b` never matches.
- A Build-phase timeout (`Timeout` with phases `["Timeout"]` under
  `--build-timeout`) and an `Unviable` mutant whose Build status is `Signalled`
  or `Other`: the parser refuses both as an unmeasured cargo-mutants phase shape.
- hostobs-scale cost, `--shard`, CRLF, a real `Signalled`/`Other` Test status.

Known risks for the first real qualification run (B245 U7,
`tools/qualify_rust_r2_real.py`; each is diagnosed, not a known defect; its
`--preflight` mode lists the real mutants of every case in seconds first):

- Descendants of the `full` timeout mutant may outlive it. For an ingested lane assay
  installs no process-tree runner (it SIGKILLs the leader's session group only), so a
  child that left that group survives silently as a stray CPU burner, not as an
  `outliving_descendants` verdict: check `ps` in the container after the run.
- `ws-sub` (workspace root below the git top, several members) is unmeasured and
  member `a` of the original `ws` input was never matrixed (the tool renames the
  members `crate_a`/`crate_b`): a `ws-sub` deviation is information about that
  shape. The real tool output of a deviating case is kept in
  `deviations/<case>/`.
- `--timeout 20` is wall-clock per mutant and flaky under host load: re-run on a
  quiet host before reading a timing-dependent deviation as a defect.

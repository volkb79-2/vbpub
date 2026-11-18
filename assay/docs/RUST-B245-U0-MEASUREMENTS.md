# B245 U0: cargo-mutants measurements and fixtures

Measured 2026-11-18 on v1001 with cargo-mutants 27.1.0 in `tester-unified-rust:local`
(`--network none`, `--memory 2g --cpus 2`, `nice`, one container at a time, no gate
container running; `dev-gates.slice` anonymous memory 1068 MiB, the rest reclaimable
cache). Scripts: [rust-b245-u0/scripts](rust-b245-u0/scripts/); raw output:
[rust-b245-u0/run-output](rust-b245-u0/run-output/); fixtures:
`tests/fixtures/rust/cargo-mutants/` (see its `PROVENANCE.md`). This is the U0 work
package of `DESIGN-B245-CARGO-MUTANTS-INGESTION.md`; it answers the measurements of
that document's section 7 item 4 and changes the design where it says so (section 8
of the design records the binding consequences).

## Measured facts

**(a, c) A relative, in-tree `--output` works.** Run from the crate directory,
`--output out-r` creates `out-r/mutants.out` inside that directory and the tool
accepts an in-tree output directory (`run-output/u0.txt`, "(a,c,h)").

**(b) STOP GATE FIRED: an in-tree `<out>` is copied into the mutated tree.** A
canary test that fails when `<out>/CANARY` exists under `CARGO_MANIFEST_DIR` failed
the unmutated baseline (`Failure(100)`, `total_mutants` 0). Consequence for D2: the
design pre-creates `<out>` EMPTY in a fresh snapshot, so only an empty directory is
copied and no test can see anything in it; the design stands, but it now has a
stated precondition: assay must never put content into `<out>`, and a lane whose
tests look at the whole crate directory sees `<out>` (an empty directory) in the
mutant tree. This was escalated to the controller before U1 starts.

**(d) A non-empty pre-created `<out>` is accepted** and its content is left alone
(`keepme` survived; `mutants.out` was created beside it).

**Dirty tree.** After a run, `git status --porcelain` lists `?? sub/crate1/out-r/`:
an untracked in-tree `<out>` fails the post-run dirty check unless a committed
`.gitignore` covers it (design R2 is confirmed).

**(e, h) `--in-diff` path anchoring is the CARGO WORKSPACE ROOT, and a wrong anchor
is silent.**
- A single crate in `sub/crate1` of a git repo: `git diff --relative=sub/crate1`
  (paths `src/lib.rs`) matched 9 mutants; the plain top-level diff (paths
  `sub/crate1/src/lib.rs`) printed `INFO No mutants to filter` and exited 0. A wrong
  anchor therefore filters EVERYTHING away with a success exit and no error.
- Two crates in one workspace (git top = workspace root), run from the member
  directory `crate_b`: the diff relative to the workspace root matched 2 mutants,
  the diff `--relative=crate_b` matched 0. So the anchor is the workspace root
  (where the `[workspace]` Cargo.toml is), not the process cwd and not the crate.
- Rule for B245: the diff assay hands over is `git diff --relative=<workspace root
  relative to the git top>` and the lane's cwd should be the workspace root (for
  hostobs: `hostobs`). A silent zero-match is already fail-closed by D3
  (`INCONCLUSIVE/NO_MUTANTS`, never PASS), but its cause would be misleading, so U5b
  also checks that the produced diff touches at least one path under the anchor.
- HAZARD: cargo-mutants strips a leading `a/` or `b/` heuristically; with workspace
  members literally named `a` and `b` the diff for `b` never matched (default and
  `--no-prefix` spellings alike), and renaming them to `crate_a`/`crate_b` fixed it
  (`run-output/u0-e6.txt`, `u0-e7.txt`). A workspace member directory named `a` or
  `b` at the anchor root is unsupported until measured further; the U7 lint should
  refuse it.

**(f) Source paths with a space or non-ASCII characters.** Diff files are named
`src__with_space.rs_line_2_col_5.diff` and `src__módulo.rs_line_2_col_5.diff`: a space
becomes `_`, non-ASCII characters are KEPT. The member-name grammar of design section
7 item 3 (ASCII only) would refuse the second; use the rule in design section 8.
Whether two source paths can collide after the space replacement (`a b.rs` and
`a_b.rs`) was not measured.

**(g) Methods, trait impls and generics.** `function.function_name` is the
QUALIFIED name: `S::bump`, `<impl T for S>::triple`, and plain `pick` for a generic
function (generics are not in the name). The `FnValue` mutant `name` repeats it
(`replace S::bump -> i32 with 0`); a function returning a generic `X` yields
`replace pick -> X with Default::default()`, which is `Unviable` here.

**Cases.** All fixtures of `tests/fixtures/rust/cargo-mutants/` come from the same
script; `PROVENANCE.md` there gives the measured result of each. Two additional
facts: `basefail-build` (`--locked` without a `Cargo.lock`) is a Build-phase baseline
failure (`Failure(102)`, no Test phase), and `crashed-f4` reproduces the default
per-package false kill (`Failure(4)` for the 5 mutants of a crate with no tests of
its own) in a real workspace.

## Not measured

- Whether two source paths can collide in the diff file name after the
  space-to-underscore replacement.
- A workspace root that is a SUBDIRECTORY of the git top with several members
  (only the single-crate subdirectory case and the root-equals-git-top workspace
  case were measured); the anchoring rule is inferred from the two.
- The hostobs-scale cost, `--shard`, CRLF.

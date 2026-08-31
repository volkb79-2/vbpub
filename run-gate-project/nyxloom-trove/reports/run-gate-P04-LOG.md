# run-gate-P04 — RG-30 doctor/--check-env honor --worktree — chronological LOG

Package: `run-gate-P04`, worktree `.worktrees/run-gate-P04-RG30`,
branch `fix/run-gate-P04-RG30`, based on `main` @ `1967783a`.
One entry per commit, each naming the commit it describes.

---

## Orientation (before the first commit)

Read `KNOWN_ISSUES_TODO_BACKLOG.md` `RG-30`'s table row in full, and
`RG-27`'s FIXED entry (table row + the `## RG-27` prose section + its
"round-2 review fixes" B1 write-up) as the direct template — RG-30 is
explicitly framed as "the last remaining instance of that same pattern
estate-wide" that RG-27 already fixed for `history`.

Confirmed against the real source (line numbers drift after RG-27/28/29):
`resolve_repo_and_worktree(project_dir, None)` at `run-gate.py:1789`
(inside `assay_toolchain_findings`, shared by `doctor` check 5 and
`--check-env`'s toolchain half) and `:2068` (inside `cmd_doctor`'s own
check 3 — git identity, the RG-21 host-lane warning, mountinfo). Neither
`main()` call site (`cmd_doctor(...)`, `cmd_check_env(...)`) threaded
`args.worktree` through AT ALL — the bug is not a typo'd argument, it is
that `--worktree` was never wired to either verb in the first place.

Read `history`'s round-2 B1 fix (RG-27, `main()`'s `history` branch) as
the disclosure template: resolve+validate the override upfront (a bad
`--worktree` refuses loud, naming git's own line where relevant), relocate
the effective project dir the same way the run path does (`R-21`), and
NAME the selected tree in the output rather than leaving the substitution
to be inferred.

Design decisions made before writing code (detailed in the REPORT):

1. **A new shared `resolve_worktree_scope()`**, not a third copy of
   `history`'s inline block — `doctor` and `--check-env` both need the
   identical (validate → resolve → relocate → disclose) sequence, and
   `history`'s own inline code is left untouched (it already shipped,
   reviewed, tested; touching it for a DRY refactor would be scope creep
   with real regression risk for a "small, well-scoped" package).
2. **`doctor`'s bad-`--worktree` path degrades gracefully** (a `[FAIL]
   git` record, other checks still run) by resolving INSIDE check 3's
   EXISTING try/except, matching doctor's own documented character
   ("must itself survive a broken host"). **`--check-env`'s bad-`--worktree`
   path refuses outright** (upfront, unguarded) because it has no
   per-check ledger for a bad override to land in gracefully — the
   alternative is an empty, misleadingly-clean scan under the wrong
   tree's name.
3. **The assay-toolchain probe's `cd` target must relocate too**, not
   just `repo`/`worktree` — mounting the selected tree's repo (correct
   `repo`/`worktree`) while `cd`-ing into the invoking checkout's
   absolute project path would not probe the selected tree, it would run
   against a directory the probe container never mounted. This is
   adjacent to, but distinct from, the two flagged bug lines, and was
   REQUIRED (not optional) to avoid a regression: fixing `repo`/`worktree`
   alone while leaving the `cd` target unrelocated would turn a
   wrong-tree-but-working probe into a broken one.

---

## `929be064` — fix(run-gate): RG-30 -- doctor/--check-env honor --worktree (rev 31)

Implementation, tests and docs in one commit.

**Code** (`run-gate.py`, rev 30 → 31): new `resolve_worktree_scope()`
(next to `resolve_repo_and_worktree`/`effective_project_dir`) — validates
a given `--worktree` is a real directory and a real git work tree
(refusing by name otherwise, same shape as `history`'s B1 fix), then
returns `(repo, worktree, effective project dir, disclosure scope)`.
`assay_toolchain_findings()` gained a `worktree_override` parameter,
threaded into its own `resolve_repo_and_worktree` call and used to
relocate the probe's `cd` target (`probe_dir`). `cmd_doctor()` gained the
same parameter: a disclosure banner up front, check 3 now calls
`resolve_worktree_scope()` in place of the bare `resolve_repo_and_worktree`
(inside its existing try/except, so a bad override becomes a `[FAIL] git`
record rather than reaching the RG-21 check with a garbage path), and
check 5 passes `worktree_override` through to `assay_toolchain_findings`.
`cmd_check_env()` gained the same parameter, resolves upfront (unguarded
— refuses on a bad override), relocates its own Python-source scan
(`scan_dir` in place of `project_dir`), and passes `worktree_override`
through to `assay_toolchain_findings` too. `main()`'s two call sites now
pass `worktree_override=args.worktree`. `usage()` text for `doctor` and
`--check-env` documents the new `--worktree` behavior. `history`'s own
inline resolution block (round-2 B1) is untouched.

**Tests**: 11 new — one inside `TestAssayToolchainFitness`
(`test_worktree_flag_relocates_the_probes_cd_target`, proving the probe's
`cd` target actually relocates by inspecting the recorded docker argv),
and a new class `TestDoctorAndCheckEnvWorktreeReadScope` (10): the RG-21
WARN/OK flip in both directions under `--worktree` for `doctor`, the
unflagged case still answers for the invoking checkout with no disclosure
banner, a bad `--worktree` fails the git check without ever reaching (and
never falsely OK-ing) the RG-21 check while other checks still run, a
non-git `--worktree` fails with git's own line, `--check-env`'s drift
scan reads the selected tree's Python sources (proven with a helper
module committed only on the worktree's own branch, after the worktree
was created, so the main checkout never sees it) and never leaks it
unflagged, and `--check-env`'s bad-`--worktree` refusals (non-directory
exit 2, non-git-worktree exit 3). All new tests drive `run_gate.main([...])`
IN-PROCESS (never subprocess `run_tool`) — RG-27's LOG already recorded
that lesson twice (P03 `1687b60d`/`0e6d0ea4`): a `main()` branch reached
only through a subprocess `run_tool` call is invisible to `--cov`, and the
diff-coverage floor would fail on exactly the lines this package adds.

**Docs**: SPEC new `R-37` (`R-37a`/`R-37b`/`R-37c`) placed after `R-36i`
(the highest existing id), with short pointer sentences at `R-30`/`R-34`
instead of colliding sub-letters — `R-30a` was ALREADY taken (the RG-21
warning rule itself), caught before commit by grepping existing `R-3[0-9]`
ids rather than assuming the next free letter. New Rev 9 header line.
README "Effective tree" bullet + subcommand list. CONSUMERS new paragraph
after the toolchain-fitness example. CHANGES `[Unreleased]` → `### Fixed`.
`__revision__` bumped 30 → 31 with the standard one-line-per-rev history
comment.

**Gate: GREEN on the first attempt.** `./run-gate.py selftest`
(verdict read in a separate step, never a pipe tail):

```
run-gate: rev 31 | lane selftest | env built-in 'host'
run-gate: budget 20m (advisory)
...
394 passed, 2 skipped, 2 warnings in 50.14s
diff-coverage OK: 22/22 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: artifact: .../run-gate-project/coverage.json
run-gate: lane 'selftest' exit 0
```

`22/22` is small relative to the diff's line count because most of the
diff is docstring/comment prose (design rationale, matching this
project's house style) — the actual behavior change is a small, tightly
covered surface: one new function, two new parameters threaded through
three call sites, and the two flagged `resolve_repo_and_worktree` calls'
replacement. Two pre-existing skips (`TestWheelPackaging`, local
`setuptools_scm` version mismatch, unrelated).

Note before this run: `./run-gate.py selftest --allow-dirty` was run
FIRST, against the uncommitted working tree, purely as a fast pytest
sanity check (394 passed then too) — its `diff-coverage OK: 0/0` line is
NOT a real verdict, because `tools/coverage_gate.py` diffs `main` against
committed `HEAD`, and uncommitted changes are invisible to `git diff`.
The real verdict is the one above, taken after committing.

---

## `<this commit>` — docs(run-gate): RG-30 FIXED + run-gate-P04 LOG/REPORT

Backlog `RG-30` marked FIXED in the status table (mechanism + the exact
gate numbers above); this LOG + the REPORT filed. No code change — the
real gate was already confirmed green at `929be064` and nothing here
touches `run-gate.py` or `tests/`.

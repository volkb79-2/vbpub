# run-gate-P04 — RG-30 doctor/--check-env honor --worktree — REPORT

**Package:** `run-gate-P04` · branch `fix/run-gate-P04-RG30` (from `main`
@ `1967783a`) · commit `929be064` (+ this docs commit).
**Backlog:** `RG-30` → FIXED.
**Tool revision:** `__revision__` 30 → **31**. **SPEC:** new `R-37`
(`R-37a`/`R-37b`/`R-37c`), pointer amendments at `R-30`/`R-34`, header Rev 9.

---

## 1. The bug, restated precisely

`doctor` and `--check-env` both call `resolve_repo_and_worktree` with a
hardcoded `None` instead of the caller's `--worktree` value
(`run-gate.py:1789` inside `assay_toolchain_findings`, shared by `doctor`
check 5 and `--check-env`'s toolchain half; `:2068` inside `cmd_doctor`'s
own check 3 — git identity, the RG-21 host-lane warning, mountinfo).
Neither `main()` call site even threaded `args.worktree` through to
either verb — the flag was accepted by argparse (it is global) but simply
never reached these two verbs' bodies.

Consequence: `doctor --worktree B` (or `--check-env --worktree B`)
silently reports the INVOKING checkout's answers under B's name. This is
a read-scope hazard, not merely a missing feature, because several of
doctor's checks are genuinely PER-TREE and can legitimately disagree
between trees — RG-21's host-lane git-view WARN is the sharpest example
(a plain checkout answers `[OK]`, a linked worktree with a host lane
answers `[WARN]`, and which one is true depends entirely on which tree
you ask about).

`history` (RG-27) had the identical hazard for its read side and closed
it in a round-2 review fix (B1): resolve+validate `--worktree` before
answering, relocate the effective project dir the same way the run path
does, and DISCLOSE which tree the answer describes. RG-30 is that same
fix applied to the two remaining places the pattern occurs.

## 2. Design decisions, and why

### 2.1 One shared helper, not three copies

`history`'s B1 fix lives as ~20 lines inline in `main()`'s `history`
branch. `doctor` and `--check-env` need the IDENTICAL sequence
(validate the override is a real directory → validate it is a real git
work tree, naming git's own line otherwise → resolve `repo`/`worktree`/
`toplevel` → relocate the effective project dir → produce a disclosure
string). Copy-pasting that a second and third time is exactly the
untested-drift class this project's own README names ("An argv proves
construction, not acceptance" — the same argument applies to hand-copied
control flow). `resolve_worktree_scope(project_dir, worktree_override,
what)` is the single implementation; `what` is the verb's own name
(`"doctor"` / `"--check-env"`), used only to phrase the refusal message.

`history`'s own inline block was deliberately NOT refactored to call the
new helper. It already shipped, was independently reviewed (round-2,
B1/B2/S1/S2), and carries its own passing tests with exact-string
assertions on its refusal message (`` `history` reports THAT tree's
store ``). Refactoring it to share the helper would touch code outside
this package's stated scope for a purely cosmetic DRY gain, with real
regression risk to already-verified behavior — not a trade this
"small, well-scoped" package should make. The helper's own docstring
notes this explicitly, so a future reader is not left wondering why a
third near-identical block was not folded in.

### 2.2 Two different failure shapes for a bad `--worktree`, and why they differ

**`doctor` degrades gracefully.** Resolution happens INSIDE check 3's
EXISTING `try/except GateError/OSError` block — the same block that
already turns "git not runnable" and other broken-host conditions into a
`[FAIL] git` record while the REST of doctor's checks still run. A bad
`--worktree` now takes that identical path: `[FAIL] git` naming the
override problem, checks 1/2/4/5 still execute, exit 2. This matches
doctor's own documented character (`R-30`: "a preflight that tracebacks
on exactly the machine that needs it defeats its purpose") — a
diagnostic tool's job is to report everything it CAN determine, not to
abort at the first thing it cannot.

Critically, this ALSO closes the actual named hazard: `linked_worktree_gitdir()`
(the RG-21 check) reads "no gitdir file at this path" as "plain checkout,
nothing to warn about" — it has no way to distinguish "this is genuinely a
plain checkout" from "this path does not exist at all". Left unvalidated,
a garbage `--worktree` would let `doctor` print a FALSE `[OK]` on exactly
the check this backlog entry is about. Because resolution now happens
BEFORE that check, inside the same try that already exists, a bad
override never reaches it at all — the exception is raised (and
recorded as `[FAIL] git`) first.

**`--check-env` refuses outright.** It resolves upfront, unguarded — a
bad `--worktree` propagates to `main()`'s own top-level `except GateError`
handler (one clean line on stderr, exit 2 for "not a directory" / exit 3
for "not a git work tree", matching `history`'s own exit-code split).
`--check-env` has no per-check `[OK]`/`[WARN]`/`[FAIL]` ledger for a bad
override to land in gracefully the way `doctor` does — its two halves
(env-drift scan, toolchain findings) are each a flat pass/fail. Degrading
gracefully here would mean silently scanning ZERO Python files under a
tree that does not exist and reporting "0 uncovered references" — a
clean bill of health that is actually just silence, the exact "could not
determine" collapsing into "nothing is wrong" AGENTS.md names as this
estate's most expensive recurring defect. Refusing outright is strictly
safer and matches `history`'s own precedent for the identical failure
shape (a read verb with no downstream to fail in).

### 2.3 The toolchain probe's `cd` target had to relocate too

The two lines the backlog flags fix `repo`/`worktree` — but
`assay_toolchain_findings` ALSO passes `project_dir` to `assay_inventory`,
which embeds it as the probe's `cd` target (`cd {project_dir} && assay
lanes --json ...`, run INSIDE the mounted container). Fixing `repo`/
`worktree` alone while leaving this `project_dir` unrelocated would have
been a **regression**, not a partial fix: the probe would mount the
SELECTED tree's repo (correct, post-fix) but `cd` into the INVOKING
checkout's absolute path — a path the container never mounted at all.
Before this fix that probe was silently wrong-tree but at least
"working" (mounting and cd-ing into the same, wrong, tree consistently);
after fixing only `repo`/`worktree` it would have started failing outright
under `--worktree`, a worse outcome than the bug it replaced.

So `assay_toolchain_findings` computes its own `probe_dir` — the
effective project dir under the SAME `worktree_override`, using
`effective_project_dir()` exactly the way the run path already does
(`R-15`/`R-21`) — and passes that to `assay_inventory` in place of the
raw `project_dir`. This is shared code (`doctor` check 5 and
`--check-env`'s toolchain half both call `assay_toolchain_findings`), so
the fix and its test cover both callers at once.
`test_worktree_flag_relocates_the_probes_cd_target` proves it directly:
it inspects the RECORDED docker argv (this suite's own "construction is
not acceptance" discipline — assert the actual command shape, not a
canned response) and confirms the inventory probe's `cd` target contains
the worktree's project path and NOT the invoking checkout's.

### 2.4 Disclosure, matching `history`'s own pattern

Both verbs now name the selected tree rather than leaving the
substitution to be inferred (`R-05`). `doctor` prints a standalone banner
before check 1 (`run-gate: doctor: --worktree <path> — this report
describes THAT tree, not the invoking checkout`) using the RAW
`worktree_override` string — always available regardless of whether
resolution later succeeds, so the disclosure appears even when check 3
subsequently fails on a bad path. The `[OK] git` record itself ALSO
repeats the (validated, canonical) tree when resolution succeeds.
`--check-env` prints the equivalent banner once resolution succeeds
(it has already refused by the time anything else would print, on a bad
override).

### 2.5 An ID collision caught before commit

The first draft of the SPEC amendment reused `R-30a` for the new
doctor-read-scope sub-rule — but `R-30a` was ALREADY the RG-21
host-lane-warning rule itself (`SPEC.md:561` pre-existing). Caught by
grepping every existing `R-3[0-9][a-z]?` id before finalizing, not by
assumption. Fixed by giving the new content its own top-level id (`R-37`,
the next free number after `R-36`), with short pointer sentences left at
`R-30`/`R-34` instead of colliding sub-letters.

## 3. The gate — verbatim

Command: `cd run-gate-project && ./run-gate.py selftest`, exit status and
diff-coverage verdict read in a SEPARATE step from a marker in the
captured output, never a pipe tail (AGENTS.md; LESSONS L4). Run against
the COMMITTED tree at `929be064` (an earlier `--allow-dirty` run against
the uncommitted working tree was pytest-only-sanity: `tools/coverage_gate.py`
diffs `main` against committed `HEAD` via `git diff`, so uncommitted
changes are invisible to it and that run's `diff-coverage OK: 0/0` was
not a real verdict):

```
run-gate: rev 31 | lane selftest | env built-in 'host'
run-gate: budget 20m (advisory)
...
394 passed, 2 skipped, 2 warnings in 50.14s
diff-coverage OK: 22/22 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: artifact: .../run-gate-project/coverage.json
run-gate: lane 'selftest' exit 0
```

GREEN on the first real attempt — no coverage RED/fix cycle was needed
(unlike RG-27's P03, whose LOG records two RED rounds from `main()`
branches reached only through subprocess `run_tool` calls, invisible to
`--cov`). This package's new tests were written IN-PROCESS
(`run_gate.main([...])`) from the start specifically because that lesson
is recorded in `run-gate-P03-LOG.md` and was read before writing any
test here.

The two pre-existing skips are `TestWheelPackaging`, unrelated (local
`setuptools_scm` 10.2.1 ≠ pinned 10.0.5).

`22/22` changed executable lines is small relative to the diff's total
line count because most of the diff is docstring/comment prose — this
project's own house style for explaining WHY, which `coverage_gate.py`
correctly does not count as executable.

## 4. Test inventory (11 new)

One test inside the existing `TestAssayToolchainFitness` class
(`test_worktree_flag_relocates_the_probes_cd_target`) — placed there
rather than in a new class because it reuses that class's `_project`
helper (fake docker, fake assay judge, `physical_path` monkeypatch)
verbatim.

New class `TestDoctorAndCheckEnvWorktreeReadScope` (10):

| test | proves |
|---|---|
| `test_doctor_worktree_flag_reports_the_named_trees_state` | invoked from a plain checkout, `--worktree <linked>` still reports the LINKED tree's `[WARN]`, disclosed |
| `test_doctor_worktree_flag_does_not_leak_the_invoking_trees_answer` | the other direction: invoked from a WARN-worthy tree, `--worktree <plain>` reports `[OK]`, never the invoking tree's `[WARN]` |
| `test_doctor_without_the_flag_still_answers_for_the_invoking_checkout` | no regression: unflagged behavior unchanged, no disclosure banner (nothing substituted) |
| `test_doctor_bad_worktree_fails_the_git_check_not_a_false_ok` | a garbage `--worktree` never reaches the RG-21 check (no false `[OK]`); other checks still run; exit 2 |
| `test_doctor_non_git_worktree_fails_with_gits_own_message` | a real but non-git directory fails with git's own line, no traceback |
| `test_check_env_worktree_flag_scans_the_named_trees_sources` | the drift scan reads the SELECTED tree's Python sources (a helper module committed only on the worktree's own branch), disclosed |
| `test_check_env_without_the_flag_never_sees_the_other_trees_drift` | no regression: unflagged scan never sees the other tree's files |
| `test_check_env_bad_worktree_refuses_rather_than_scanning_nothing` | a garbage `--worktree` refuses outright (exit 2, stderr, no traceback) rather than scanning nothing under the wrong name |
| `test_check_env_non_git_worktree_refuses_with_gits_own_message` | non-git directory refuses (exit 3), no traceback |

All 11 drive `run_gate.main([...])` in-process. Full suite: 384 → **394**
passing, 2 skipped (unchanged, pre-existing).

## 5. Files touched

| file | why |
|---|---|
| `run-gate-project/run-gate.py` | the fix; rev 30 → 31 |
| `run-gate-project/tests/test_run_gate.py` | 11 new tests |
| `run-gate-project/SPEC.md` | new `R-37` (a-c); pointer amendments at `R-30`/`R-34`; Rev 9 header |
| `run-gate-project/README.md` | "Effective tree" bullet + subcommand list |
| `run-gate-project/CONSUMERS.md` | new paragraph after the toolchain-fitness example |
| `run-gate-project/CHANGES.md` | `[Unreleased]` → `### Fixed` |
| `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` | RG-30 FIXED |
| `run-gate-project/nyxloom-trove/reports/run-gate-P04-{LOG,REPORT}.md` | this record |

Nothing outside this list was touched. No merge to `main` was performed
(explicitly out of scope for this package).

## 6. Follow-ups a reviewer may want filed (not filed by me — out of scope)

1. **`--check-env`'s env-drift scan relocation is new behavior, not just a
   bug fix** — before this package, `--worktree` was silently ignored by
   BOTH verbs entirely, so there is no prior "correct" scan target to
   regress from. Worth a reviewer's explicit sign-off that scanning the
   SELECTED tree's Python sources (rather than, say, refusing `--worktree`
   for the env-drift half specifically) is the intended semantics — this
   package assumed yes, on the strength of README's pre-existing
   "Effective tree" doctrine ("`--worktree` doesn't just redirect checks
   — the lane EXECUTES in the selected tree").
2. **`resolve_worktree_scope()` and `history`'s inline block are now two
   independent implementations of the same sequence.** Deliberate (§2.1),
   but a future RG entry could fold `history` onto the shared helper once
   its own test suite's exact-string assertions are updated in step —
   not attempted here to keep this package's diff minimal and low-risk.
3. **No estate sweep for other read-only verbs.** `validate-pointers`
   takes its own `--root` override (a different mechanism, RG-2) and was
   not audited here since it is out of scope for RG-30's stated pattern
   (`resolve_repo_and_worktree` receiving `None`) — worth a reviewer's
   confirmation that it has no analogous hazard.

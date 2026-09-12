# run-gate-WAVE-RG55-P2 — implementer LOG

Package P2 (run-gate client) of the RG-55 wave. Fresh Sonnet implementer,
worktree `/workspaces/vbpub/.worktrees/rg55-run-gate-client`
(branch `rg55-run-gate-client`), HEAD at dispatch `e499a168` (the P0 freeze
commit). Handoff:
`/workspaces/vbpub/run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-HANDOFF.md`.

## Orientation

Read, in order: the handoff; plan §1-3/§5 P2/§6-10; the frozen interface
contract (all 7 sections); `fixtures/rg55/README.md` (full, incl. the N=5
nearest-rank gotcha); the controller log (RW-1..RW-4); `SPEC.md` lines
80-350 (R-04, R-06, R-07, R-08/R-08a, R-09, R-22, R-10..R-19, R-42 intro —
did NOT reach R-29/R-36/R-39/R-40/R-41/R-42-full/R-07's mode/container_name
drift text in this session, deferred to the successor per the checkpoint
cut below); `run-gate.py` header (lines 1-60, revision note); `tools/
coverage_gate.py` (whole, 323 lines) + `tests/test_coverage_gate.py`
(whole, pre-fix 150 lines); `KNOWN_ISSUES_TODO_BACKLOG.md` RG-48/53/54/55/
56/57 (full entries); `CHANGES.md` head + the `[23.6.2]` dated entry (format
reference). Verified coverage.py's actual JSON schema by reading the
installed package's `coverage/jsonreport.py` source directly (`missing_branches`/
`executed_branches` are file-level lists of `[source_line, target_line]`
int pairs, populated only when `coverage_data.has_arcs()` — i.e. `--cov-
branch` was passed, which `run-gate.toml`'s `selftest` argv already does)
rather than assuming the shape — this is the ground truth C1's design rests
on. Did NOT read: read-list items 2 (SPEC R-29/R-36/R-39/R-40/R-41/rest of
R-42), 3 (run_gate.py 152-437/640-780/900-1250/1550-1800/3500-3720/3908-
4110/4117-4300/4331-4560/4613-4760/4823-4930/4960-5110/5290-5442), 4 (tests/
test_run_gate.py targeted ranges), 6 (assay-lane template files), 7
(cgroup-profiler lib/util.py parsers) — all deferred to C2 onward, not
needed for C1 (RG-53 is entirely self-contained in `tools/coverage_gate.py`
+ its test file, confirmed by reading the whole of both before editing).

Orientation call count: ~18 tool calls (reads + greps) before the first
edit.

**One process error, caught and corrected before any commit**: the first
round of edits was applied to the MAIN checkout
(`/workspaces/vbpub/run-gate-project/...`) instead of the worktree
(`/workspaces/vbpub/.worktrees/rg55-run-gate-client/run-gate-project/...`)
— both paths were open in context and the wrong one was used for the Edit
calls. Caught immediately after by noticing the targeted-test run reported
9 passed instead of the expected ~20 (the edits landed on a file this
session had not actually read via the Read tool at the worktree path,
so the new tests silently weren't there). Verified with `git status
--porcelain` in the main checkout (`/workspaces/vbpub`) that ONLY the two
intended files were modified there and that they were clean at session
start (matching the conversation's opening `git status` snapshot); restored
both with `git restore -- run-gate-project/tools/coverage_gate.py run-
gate-project/tests/test_coverage_gate.py` in the main checkout (confirmed
clean after), then re-applied every edit at the correct worktree path. No
main-checkout content was lost — the diff-stat before restoring matched
exactly what was intentionally written. Flagging this in the LOG per the
"never touch the main checkout" rule family (CLAUDE.md, AGENTS.md) even
though the correction was same-session and left no trace, so a reviewer
checking `git status` on `/workspaces/vbpub` knows why this note exists.

## Commit 1 — C1 (RG-53)

`tools/coverage_gate.py`: `_validate_cov_record` now also validates the
optional `missing_branches`/`executed_branches` keys (list of
`[source_line, target_line]` int pairs); `_branch_maps` builds per-line
missing/all branch-arc maps; `evaluate()` counts a changed line as
uncovered when it executed but left an arm untaken (not only when it never
ran), and reports `branches_total`/`branches_missed` (scoped to the
changed+executable line set only, never a second whole-file denominator)
plus `branch_partial_lines` on `Verdict`; the CLI's OK/FAIL lines print the
branch tally and mark branch-partial lines `(branch)` in the FAIL listing.
`_check_nonempty_diff` (new, pure, independently tested) refuses
(`main()`, exit 2) a `changed_executable == 0` verdict unless
`--allow-empty-diff` is passed, naming the resolved base, HEAD, and the
three known routes to a false 0/0 (merge-commit first-parent/RG-54, stale
worktree base, reverted work/RG-51 round 5) — `evaluate()` itself is
UNCHANGED in this respect (stays a pure 0/0-is-100% classifier; existing
direct callers/tests of `evaluate()` are unaffected). `run-gate.toml`'s
`selftest` argv is untouched (confirmed by reading it: no
`--allow-empty-diff` in the invocation) — this project's own gate now goes
red on a 0/0 diff too.

`tests/test_coverage_gate.py`: grew from 8 to 20 tests (branch-partial-line
uncovered/covered pairs, the no-branch-data no-op case, branch totals
scoped to changed lines only, malformed branch-arc shape rejection, the
`--allow-empty-diff` arg default + override, `_check_nonempty_diff`'s three
cases, and two end-to-end `main()` tests: one proving the refusal AND the
`--allow-empty-diff` override both work against a real degenerate-base tmp
repo, one proving the CLI's branch-note text on a passing run).

`CHANGES.md`: `[Unreleased]` gained a BREAKING `RG-53` entry naming both
semantic changes and their consumer impact (topos pattern — every
consumer that re-copies `tools/coverage_gate.py` inherits stricter
semantics).

`KNOWN_ISSUES_TODO_BACKLOG.md`: RG-53's `### Status — OPEN` →
`### Status — FIXED 2026-09-12 (RG-55 wave, package P2), BREAKING` with the
evidence (test count, `pytest` result) and a pointer to the SPEC amendment
deferred to this wave's C8 pass.

### Gate verdicts

- `pytest tests/test_coverage_gate.py -q` (targeted, before the whole
  suite): **20 passed** (up from 8; `nice -n 19 ionice -c 3`).
- `nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty` (whole
  suite, from `<worktree>/run-gate-project`): pytest phase **827 passed, 3
  skipped** (3 skips are the pre-existing wheel-packaging skip, unrelated —
  `local setuptools_scm 10.2.1 != pinned 10.0.5`), in 104.22 s. The
  `coverage_gate.py` phase then **exits 2** (verdict read in a separate
  step from the captured log, not a pipe tail):
  ```
  diff-coverage ERROR: 0 changed executable lines under 'run-gate.py'
  between base e499a168... and HEAD e499a168... -- refusing an empty-diff
  PASS. Known routes to 0/0 ... If this run genuinely changes no
  executable line, pass --allow-empty-diff.
  run-gate: lane 'selftest' exit 2
  ```
  **This exit 2 is the EXPECTED, DESIGNED outcome for this commit, not a
  regression** — the handoff states it explicitly ("a 0/0 selftest must go
  red"): C1's own diff against `main` touches only `tools/coverage_gate.py`,
  `tests/test_coverage_gate.py`, `CHANGES.md`,
  `KNOWN_ISSUES_TODO_BACKLOG.md` — none of which is `run-gate.py`, the sole
  `--source` scope `run-gate.toml`'s `selftest` argv judges. Zero changed
  executable lines under that scope is therefore genuinely true here (a
  fourth, LEGITIMATE route to 0/0 beyond the three named ones: a commit
  that intentionally does not touch the judged file), and RG-53's own
  design — "the condition worth acting on is the zero itself" (backlog,
  verbatim) — says refuse it anyway rather than special-case it, forcing an
  explicit `--allow-empty-diff` if that were ever truly desired for a
  release. This is direct, self-referential proof the fix works: BEFORE
  this commit, the same 0/0 would have silently printed
  `diff-coverage OK: 0/0 ... (100.0% >= 100.0% floor)` and exit 0. Expected
  to return to a normal green (nonzero changed lines, covered) once a later
  commit in this branch (C3 onward) touches `run-gate.py` itself, since the
  diff accumulates against the wave's `main` base across every commit on
  this branch, not commit-to-commit.

Commit: `607950fd` — `fix(rg53): coverage_gate.py reads missing_branches, refuses a 0/0 diff`.

## Checkpoint

Stopping here at a coherent boundary (green targeted-test run + the
designed-red selftest verdict + LOG/REPORT written + commit) per the E-008
checkpoint clause, ahead of the ~60-tool-call mark, because C2 (assay
lanes: vendoring a `.pyz`, `assay.toml`, `run-gate.toml` lane tables,
`doctor` fitness checks) and C3 (the profiling client — by far the largest
deliverable) are substantial enough that starting them without a fresh
budget risks a worse cut point later. See
`run-gate-WAVE-RG55-P2-BRIEF-1.md` for the successor's continuation brief.

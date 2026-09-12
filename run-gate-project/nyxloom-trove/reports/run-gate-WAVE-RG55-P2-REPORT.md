# run-gate-WAVE-RG55-P2 — REPORT (partial, C1 only)

Package P2 of the RG-55 wave. This REPORT covers deliverable C1 (RG-53)
only; C2-C8 are deferred to a successor implementer — see
`run-gate-WAVE-RG55-P2-BRIEF-1.md` for the continuation brief and
`run-gate-WAVE-RG55-P2-LOG.md` for the commit-by-commit record.

## C1 — RG-53 (`tools/coverage_gate.py` branch-aware diff judge + 0/0 refusal)

**Status: DONE.** See the LOG for the full change description. Summary:

1. Branch awareness: `_validate_cov_record` validates the optional
   `missing_branches`/`executed_branches` keys (verified against the
   INSTALLED coverage.py's actual JSON schema, read directly from
   `coverage/jsonreport.py` in this devcontainer's venv, rather than
   assumed — file-level lists of `[source_line, target_line]` int pairs,
   populated only when branch coverage was collected). `_branch_maps`
   turns those into per-source-line missing/all-arc sets; `evaluate()`
   counts a changed line as uncovered when it executed but left an arm
   untaken, in addition to the pre-existing never-executed case.
   `Verdict.branches_total`/`branches_missed` are scoped to the SAME
   changed+executable line set as the existing line counts (never a
   second, whole-file denominator) — "beside lines", per the handoff's own
   phrasing. `branch_partial_lines` distinguishes "ran, missed an arm"
   from "never ran" for the FAIL listing (`(branch)` suffix).
2. Empty-diff refusal: `_check_nonempty_diff` (new, pure, unit-tested in
   isolation) refuses a `changed_executable == 0` verdict in `main()`
   (exit 2) unless `--allow-empty-diff`, naming the resolved base, HEAD,
   and the three routes to a false 0/0 the backlog names (merge-commit
   first-parent/RG-54, stale worktree base, reverted work/RG-51 round 5).
   Deliberately kept OUT of `evaluate()`, which stays a pure classifier —
   the refusal is CLI policy, not a change to the pure function's contract,
   so existing direct callers/tests of `evaluate()` needed no changes.
3. `run-gate.toml`'s `selftest` argv is UNCHANGED (verified by reading it:
   `--allow-empty-diff` is not in the invocation) — confirmed this is
   deliberate per the handoff ("the flag is not passed — a 0/0 selftest
   must go red").
4. `CHANGES.md` `[Unreleased]` gained a BREAKING entry; `KNOWN_ISSUES_TODO_
   BACKLOG.md` RG-53 moved OPEN -> FIXED with evidence.

### Tests

`tests/test_coverage_gate.py`: 8 -> 20 tests. New coverage: branch-partial
line uncovered (with exact branches_total/missed numbers hand-checked),
branch-fully-taken line stays covered, no-branch-data is a no-op
(regression guard for the pre-fix behavior), branch totals scoped to
changed lines only (a branch on an untouched line must not leak into the
totals), malformed branch-arc shape raises `CoverageGateError`,
`--allow-empty-diff` argparse default/override, `_check_nonempty_diff`'s
three cases in isolation, and two `main()` end-to-end tests against a real
tmp_path git repo: one proving the refusal fires (exit 2, message content)
AND that `--allow-empty-diff` produces a normal pass on the SAME degenerate
repo, one proving the CLI's branch-note text appears on a passing run.

### Gate verdicts (read in a separate step from the captured log, not a pipe tail)

- `pytest tests/test_coverage_gate.py -q`: **20 passed**, 0.31s (`nice -n
  19 ionice -c 3`, isolated run before the whole suite).
- `./run-gate.py selftest --allow-dirty` (whole suite, `nice -n 19 ionice
  -c 3`, from `<worktree>/run-gate-project`): pytest phase **827 passed, 3
  skipped** (pre-existing, unrelated wheel-packaging skip) in 104.22s.
  `coverage_gate.py` phase: **exit 2**, `diff-coverage ERROR: 0 changed
  executable lines under 'run-gate.py' between base e499a168... and HEAD
  e499a168... -- refusing an empty-diff PASS. ...`.

  **This exit 2 is the intended, designed acceptance evidence for C1's
  second half, not a defect.** C1's diff against `main` touches only
  `tools/coverage_gate.py`, `tests/test_coverage_gate.py`, `CHANGES.md`,
  `KNOWN_ISSUES_TODO_BACKLOG.md` — none is `run-gate.py`, the sole
  `--source` scope judged. Zero changed executable lines under that scope
  is genuinely, correctly zero here. Before this fix, the exact same
  situation would have silently printed `diff-coverage OK: 0/0 ... (100.0%
  >= 100.0% floor)` and exited 0 — this run is direct, self-referential
  proof the refusal works. Full transcript preserved at
  `/tmp/claude-1003/-workspaces-vbpub/5d55184a-d2df-482e-aa2b-541cae13c0ad/scratchpad/selftest-c1.log`
  (scratchpad, not committed). Expected to return to a normal covered-diff
  green once a later commit in this branch (C3 onward, which touches
  `run-gate.py` itself) lands, since the diff accumulates against the
  wave's `main` base across every commit on this branch.

  assay-r1/r2/r3 lanes: **NOT YET RUN** — C2 (which vendors
  `assay-6.1.1.pyz` and writes `assay.toml`/the `run-gate.toml` lane
  entries) has not started. Deferred to the successor; the handoff requires
  assay-r1/r3 once before return and assay-r2 once at the very end, neither
  of which is reachable before C2 exists.

  Live acceptance probes (handoff §4): **NOT YET RUN** — they exercise the
  profiling client (C3), which has not started.

## Decision asks

None yet raised for C1 — RG-53's implementation directions were fully
DECIDED in the backlog's own "Directions, not picked here" list (both were
picked: read `missing_branches`, and refuse the zero). One judgment call
made where the backlog left the exact mechanism unstated, recorded here for
visibility rather than as a blocking ask:

- **branches_total/branches_missed scope and placement.** The handoff says
  "report `branches_total`/`branches_missed` beside lines" without
  specifying whether that means (a) a second independent denominator over
  the whole file, or (b) counts scoped to the same changed-line set the
  line-level pct already uses. Chose (b): branch counts are tallied only
  over arcs whose SOURCE line is in `changed_exec` (the same set line
  coverage is judged over), reported as an additional `; branches X/Y
  taken` clause beside the existing OK/FAIL line, never as a second
  pass/fail axis. Rationale: the fixtures/contract's own
  `RG-54` artifact excerpt (`KNOWN_ISSUES_TODO_BACKLOG.md`) shows a
  `"branches_total": 0` key living inside the SAME object as `"covered"`/
  `"executable"` (assay's own coverage-judge report, a sibling precedent,
  not this file) — i.e. branch counts travel WITH the line counts as one
  scoped unit elsewhere in this estate, which this implementation mirrors.
  `evaluate()`'s pct/passed calculation is UNCHANGED in formula (still
  `covered/changed_executable`) — a branch-partial line's contribution to
  `covered` is what changed (it now counts as uncovered), not the
  denominator's shape.

## Deferred items (with RG ids where applicable)

- **C2 (assay lanes, D-11)** — not started. No new RG id (part of this
  wave's own scope, not a backlog defect).
- **C3 (profiling client, R-43)** — not started. Largest remaining
  deliverable; the successor should read the full contract (already
  frozen and read this session) plus the SPEC/run_gate.py read-list ranges
  this session did not reach (see LOG "Did NOT read").
- **C4 (history schema 2, R-36)**, **C5 (`footprint` verb, R-44)**, **C6
  (RG-48, `resources.cpus`)**, **C7 (`doctor` profiler check)**, **C8
  (docs/spec/backlog/revision sweep, `__revision__ = 41`)** — not started.
- assay-r1/r2/r3 lanes, live acceptance probes (handoff §4) — blocked on
  C2/C3 respectively, not yet attempted.
- RG-56 (admission control) and RG-57 (bare-host attribution) remain filed,
  untouched, per the handoff's explicit instruction not to re-file or
  design them in this package.

## Files touched (C1 only)

- `run-gate-project/tools/coverage_gate.py`
- `run-gate-project/tests/test_coverage_gate.py`
- `run-gate-project/CHANGES.md`
- `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md` (new)
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md` (new, this file)
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-1.md` (new, successor brief)

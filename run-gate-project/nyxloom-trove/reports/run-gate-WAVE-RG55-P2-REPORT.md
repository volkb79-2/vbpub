# run-gate-WAVE-RG55-P2 — REPORT (partial: C1-rework done, C2 partially blocked)

Package P2 of the RG-55 wave. This REPORT covers deliverable C1 (RG-53)
and its RW-5 rework (DONE), plus C2's vendoring sub-step (DONE) and a
blocking decision ask on C2's judge-table scoping; C3-C8 are deferred —
see `run-gate-WAVE-RG55-P2-BRIEF-1.md` (predecessor's brief),
`run-gate-WAVE-RG55-P2-BRIEF-2.md` (this session's continuation brief),
and `run-gate-WAVE-RG55-P2-LOG.md` for the commit-by-commit record.

## C1-rework — RW-5 (0/0 diff semantics corrected)

**Status: DONE**, superseding part of C1 below. The controller's RW-5
ruling found that C1's original design — refuse (exit 2) a
`changed_executable == 0` verdict unless `--allow-empty-diff` — made this
project's OWN `selftest` lane permanently red on `main` itself
(merge-base(main, HEAD) == HEAD there → always 0/0) and would have
blocked every `cmru release` of this project. Reworked: 0/0 now reports
**SKIPPED** (exit 0, a distinct stdout line naming the resolved base and
HEAD's relationship to it — `HEAD is on the base`, or `HEAD is N commits
ahead of the base; the diff touches no executable source line`), never a
plain `100.0% OK`; `Verdict` gains a `skipped` flag and a `verdict`
tri-state property (`"skipped"`/`"ok"`/`"fail"`) so any current or future
caller has a field that cannot mistake a 0/0 for a pass. Hard refusal
(the original exit-2 behavior, naming the three known false-0/0 routes)
becomes opt-in via the new `--refuse-empty-diff`; `--allow-empty-diff` no
longer exists. `evaluate()`'s pure `pct`/`passed` computation is
UNCHANGED — still 0/0-is-100% for any direct caller — only the CLI
(`main()`) and the new `Verdict.skipped`/`.verdict` fields distinguish the
case, same separation-of-concerns C1 established (the 0/0 policy lives at
the CLI boundary, not inside the pure classifier).

Full change description, gate verdicts (including the self-referential
proof — this branch is 2 commits ahead of `main`, neither commit touching
`run-gate.py`, and the selftest now exits 0 with a SKIPPED line naming
exactly that) and files touched are in the LOG's "Commit 2 — C1-rework
(RW-5)" section — not duplicated here to avoid drift between two copies of
the same evidence. `CHANGES.md` `[Unreleased]` and
`KNOWN_ISSUES_TODO_BACKLOG.md` RG-53 (new `### Rework — RW-5` subsection,
original FIXED text left intact for historical accuracy) were both
updated per the ruling's explicit instructions.

## C1 — RG-53 original landing (`tools/coverage_gate.py` branch-aware diff judge + 0/0 refusal)

**Status: DONE** (2026-09-12, prior session), **partially reworked by
RW-5 above** — the branch-awareness half (item 1 below) is UNCHANGED; the
0/0 handling half (item 2) is what RW-5 replaced. Kept here verbatim as
the historical record of what C1 originally shipped. Summary:

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

**C2's `[lanes.r1]`/`[lanes.r2]` `judge.source_roots` scoping — BLOCKING,
needs a controller/next-successor decision before those two judge tables
can be written.** Full evidence trail (file:line citations against
`assay/src/assay/config.py` and `assay/src/assay/evaluate.py`) is in the
LOG's "C2 — assay lanes ... research + a real blocking finding" section;
summarized here for the return message:

The handoff's "python judge scoped to `run-gate.py` only (tests/, tools/
never judged)" is not implementable as a path-exclusion in Assay's current
schema when `run-gate.py`/`tests/`/`tools/` are siblings with no isolating
subdirectory (confirmed by reading Assay's own source, not assumed):
`judge.source_roots` must be a directory (a single file is refused at
load); `judge.targets` (the only file-level scoping mechanism) is legal
ONLY under `mode = "whole_target"`, which forbids `judge.base`/
`base_source` entirely — mutually exclusive with the handoff's own
`base_source = "request"` requirement; and the Python adapter's
`excluded_dir_names` is a fixed, empty, non-lane-configurable set (unlike
`javascript`'s or `sql`'s adapters, which DO exclude `node_modules` etc. at
the adapter level — Python simply has no such list, and no lane-level
override key exists). `tests/` is already excluded automatically via
`is_test_path` regardless of any of this; `tools/` is not, and nothing in
the schema can make it not-considered.

Two real options, spelled out with tradeoffs in the LOG: **(1)** measure
`tools/` for real (`--cov=run_gate --cov=tools`) so `tools/coverage_gate.py`
changes are judged like any other real source (deviates from the literal
handoff text, satisfies its underlying intent — no false-refusal trap);
**(2)** restructure so `run-gate.py` sits in its own subdirectory,
isolating it from `tools/` for `source_roots` purposes (a real repo-layout
change touching the symlink, `cmru.toml`, every doc/consumer path
assumption — likely out of scope for this package alone, no ruling
authorizes it). This session did not pick one, to avoid silently encoding
option 2's absence as if it were a considered choice. Everything else in
C2 is unblocked and left for the next successor (vendoring is done; the
lane skeletons, canary lane, `run-gate.toml` wiring, and `doctor` checks
do not depend on this judge-table question).

None raised for C1 — RG-53's implementation directions were fully
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

- **C2 (assay lanes, D-11)** — vendoring DONE (commit `f687a4ed`); the
  `[lanes.r1]`/`[lanes.r2]` judge tables are BLOCKED on the decision ask
  above, everything else in C2 (lane skeletons' non-judge fields, the
  `[lanes.assay-r3]` canary lane, `run-gate.toml` wiring, `doctor` checks,
  README's "Gate and evidence" update) is unstarted but unblocked. No new
  RG id (part of this wave's own scope, not a backlog defect).
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

## Files touched

C1 (prior session, commit `607950fd`):
- `run-gate-project/tools/coverage_gate.py`
- `run-gate-project/tests/test_coverage_gate.py`
- `run-gate-project/CHANGES.md`
- `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md` (new)
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md` (new)
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-1.md` (new)

C1-rework / RW-5 (this session, commit `8c76ba3e`):
- `run-gate-project/tools/coverage_gate.py`
- `run-gate-project/tests/test_coverage_gate.py`
- `run-gate-project/CHANGES.md`
- `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`

C2 vendoring (this session, commit `f687a4ed`):
- `.gitignore` (worktree root, the monorepo-wide file — NOT
  `run-gate-project/`-scoped)
- `run-gate-project/tools/assay/assay-6.1.1.pyz` (new)
- `run-gate-project/tools/assay/assay-6.1.1.pyz.sha256` (new)

Records only (this session, uncommitted at time of writing, committed with
this checkpoint):
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-2.md` (new)

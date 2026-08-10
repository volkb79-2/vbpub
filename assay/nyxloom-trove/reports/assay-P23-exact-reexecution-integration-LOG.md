# P23 — exact reexecution integration (implementation LOG)

Package: `assay-P23-exact-reexecution-integration`
Implementer: Sonnet xhigh, fresh session
Carver commit: `0d46e95420a3035a7f5e606a3c33172929a33f79` (sole parent `9d30b25b96b8ffd8f952c02e8958b923bb8e1d13`)
Worktree: `/workspaces/vbpub/.worktrees/assay-P23-exact-reexecution-integration`
Branch: `feat/assay-P23-exact-reexecution-integration`

## 1. State reconciliation (before any edit)

| check | result |
|---|---|
| worktree | `/workspaces/vbpub/.worktrees/assay-P23-exact-reexecution-integration` (declared) |
| branch | `feat/assay-P23-exact-reexecution-integration` |
| `git rev-parse HEAD` | `0d46e95420a3035a7f5e606a3c33172929a33f79` — the carver commit, unchanged throughout |
| sole parent | `9d30b25b96b8ffd8f952c02e8958b923bb8e1d13` |
| `git status --porcelain=v1` at start | clean |

Read in full before any production edit: `nyxloom/reference/{AUTHORING,STANDARD,DOCTRINE}.md`; `assay/nyxloom-trove/{STATE.md,decisions.md}` decisions A-160/A-163/A-173–A-196; the P23 handoff
(`nyxloom-trove/handoffs/assay-P23-exact-reexecution-integration.md`); the JIT-CARVE report
(`nyxloom-trove/reports/assay-P23-JIT-CARVE.md`); the controller packet
(`.worktrees/_control/assay-P20-P32/carver/P23.md`); the landed P22 `isolation.py` public surface and its tests; P20's `git.py`/output-reservation contracts; P21's `mutation.py`/verdict-v4 terminal tests; the current `runner.py`/`mutation.py`/`canary.py`/`config.py` call sites; every locked asset under `carve-assets/P23/**`.

### Locked asset verification

All seven SHA-256 values match `nyxloom-trove/reports/assay-P23-JIT-CARVE.md` and the controller packet exactly, both before production work and again after the branch was complete:

| asset | SHA-256 | matches |
|---|---|---|
| `README.md` | `bd2e1485727816cd4f57045cfb32c9774cbe5f87c49a81624c7031e666096b5f` | yes |
| `skeleton.patch` | `1e2cb8bd86139751b8f61818d474e42b013a736e20dad9cb02f842245ba1a3f7` | yes |
| `test_acceptance.py` | `5e6cd1133ded19526de045e0ed3941c2da5d933241569cb7d87ad896946a86ec` | yes |
| `fixture-manifest.json` | `71133e9c08d7b90f09ab90e79796f8aa657f003b529c01f00a941f398d8fa2c9` | yes |
| `probe_reexecution_contract.py` | `0ace77ff3dbfc501717fc7d5a90ffec0fbffda02c1b9a7f675e061527e35139d` | yes |
| `expected/process-ledger.json` | `5054c66eb4c76033fff7bc0ef019331e7ab88aa28944c025772664dd5199a566` | yes |
| `expected/r0-snapshot-limit-v4.json` | `5b7f3cfc039b01c6d68e2169575b4580f3fd141cefa468fbc19f1088c91056a2` | yes |

## 2. Witnessed controlled baseline (before production edits)

Skeleton applied exactly once from the carver commit:

```text
git apply --check assay/nyxloom-trove/carve-assets/P23/skeleton.patch   exit 0
git apply       assay/nyxloom-trove/carve-assets/P23/skeleton.patch     exit 0
```

Locked suite, unmodified, in the foreground, immediately after:

```text
python -m pytest nyxloom-trove/carve-assets/P23/test_acceptance.py -q

13 failed, 6 passed in 1.14s
```

This reproduces the JIT-CARVE report's recorded controlled red (`13 failed, 6
passed`) exactly — 19 collected cases total. The six passes are plan/deadline
grammar, all three invalid-rigor load-time refusals, one valid ordered lane,
and the locked-input hash check — exactly the mechanical grammar the skeleton
already implements. The thirteen failures terminate at the skeleton's explicit
orchestration TODOs: nested plan/replacement, the new `run_mutation` signature
at max−1/max/max+1, injected expiry, snapshot dirt, the R0/higher-rigor
refusal split, canary profile independence, failed-control canary
construction, scratch entry/exit terminals, and ordinary snapshot-limit
closure.

## 3. What landed

- `src/assay/config.py` — the skeleton's own R0-led canonical-subsequence
  rigor check and uncovered-line-requires-R1 check in `_load_lane` (A-192).
  No further edits.
- `src/assay/runner.py` — the largest change. Extends `CommandPlan` with
  `argv_appended`/`argv_effective`/`env_passthrough`/`env_effective`/
  `allow_argv_append`/`budget_seconds`/`project_prefix`; adds `execute_plan`
  (the required-plan execution seam, A-193), `LaneDeadline`/`MonotonicClock`,
  `ScratchRootFactory`/`default_scratch_root`. Adds `_execute_snapshot_unit`
  (the one reserve→arm→execute→post-run-dirt→consume/parse→close engine
  baseline and both canary halves share), `_resolve_declared_base` (resolves
  a symbolic `judge.base` through `git.resolve_base`'s merge-base computation
  against the consumer's real repository *before* any snapshot exists — see
  §5), `_mutation_targets_from_diff` (source-root containment only, since the
  locked `FourSiteAdapter` fixture lacks the adapter attributes the frozen
  `resolve_mutation_targets` needs), `_run_prepared_lane` (baseline, then
  R1/R2/R3 as declared, entirely inside one prepared P22 seed),
  `_replace_highest_higher_rigor_claim_with_git_failed` (A-193/A-194's outer
  scratch-cleanup rule), and `_run_higher_rigor_lane` (the top dispatcher:
  resolve plan once, pre-flight dirt/HEAD check, then prepare/materialize/run
  inside one `try` that never lets cleanup mask an earlier real
  `AssayError`). `run_lane` now dispatches on declared rigor: exactly
  `("R0",)` keeps the original direct live-tree path; any of R1/R2/R3 hands
  the whole call to `_run_higher_rigor_lane` (A-189) — see §4 for what that
  dispatch made dead in the function below it, and how it was handled.
- `src/assay/mutation.py` — `run_mutation` takes the frozen inputs
  (`baseline`, `prepared`, `plan`, `deadline`, `targets`, `adapter`, `jobs`,
  `max_mutants`, `operators`, `process_runner`, `clock`,
  `executor_factory`); each worker materializes one P22 replacement, runs the
  shared `execute_plan` seam, and checks its own snapshot for dirt/HEAD
  movement (`_snapshot_left_dirt`) before its result is used. Submission is
  wave-based (`jobs`-sized batches, each awaited before the next). Removed
  `project_prefix()`/`_within_project()` (dead under the new architecture).
- `src/assay/canary.py` — `run_isolated_canary` takes the same frozen inputs
  and materializes an independent P22 context for the control and, only for a
  valid non-no-op transform, a second independent context for the transform;
  each half gets its own fresh reservation/profile (`_judge_unit`). Removed
  the installed copy-and-`git commit` orchestration entirely.
- `tests/conftest.py` — three shared helpers (`prepared_snapshot`,
  `make_plan`, `make_deadline`) so every migrated test builds the same three
  P23 primitives the same way.
- `tests/test_*.py` — every test touching a changed signature was migrated;
  see §7 for what that touched and §5 for the bugs it caught along the way.
  One new test added beyond migration (§6).
- `tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json` —
  new file, byte-identical (verified by SHA-256) to
  `nyxloom-trove/carve-assets/P22/expected/r0-snapshot-limit-v4.json`.
  `tests/test_verdict_conformance.py`'s `EXCLUDED_ENTIRELY` no longer excludes
  `("BUDGET_EXCEEDED", "SNAPSHOT_LIMIT_EXCEEDED")` (A-190 closure — this
  package's disposition of the P22-successor `SB-P22-06`/`SB-P21-R2`).
- `docs/DESIGN-GUIDE.md` — one new §6 subsection ("Higher rigor consumes the
  prepared seed; it never re-derives it") recording the two-state dispatch,
  per-path (never per-OID) snapshot addressing, the dirt/HEAD-per-unit rule,
  the A-194 scratch/pack-space formulas verbatim, and the symbolic-base
  merge-base finding from §5.
- No `assay/README.md` exists in this project (as P22's own LOG already
  recorded); it appears in `scope.touch` as permission, not obligation, and no
  oracle names it, so none was invented.

## 4. A defect this package's own restructuring introduced, found, and repaired

Coverage of `runner.py` measured **81%** with a large, contiguous block of
misses (`~1970-2001, 2012-2131, 2138-2175`) after the P23 orchestration
otherwise looked complete and every test passed. Reading that range showed
why: `run_lane`'s new two-state dispatch (`if r1_declared or r2_declared or
r3_declared: return _run_higher_rigor_lane(...)`) sits *before* the
function's own pre-existing `if r1_declared: ...` / `if r2_declared: ...` /
`if r3_declared: ...` blocks lower in the same function body — the ones that
used to implement R1/R2/R3 directly. Because A-192's rigor grammar guarantees
those three booleans are mutually consistent with `lane.rigor`, and the
dispatch above already returns for any lane where even one of them is true,
every one of those lower blocks became **unreachable** the moment the
dispatch was added — not merely low-value, but dead by construction, for
*every* call that reaches them.

This was worse than inert: the dead R2 block still called
`mutation.run_mutation(lane, baseline=result, project_root=project_root,
repo_top=repo_top, scratch_root=Path(scratch), ...)` — the **pre-P23
signature**, which no longer matches `run_mutation`'s actual parameters
(`prepared`, `plan`, `deadline`, no `lane`/`repo_top`/`scratch_root`). Python
does not type-check unreached code, so nothing caught this; a future reader
skimming `run_lane` top-to-bottom could easily mistake it for a second, live
implementation of R1/R2/R3, or a fallback path, when it is neither and would
raise `TypeError` on first execution if it were ever somehow reached.

`runner.py` and its docstring are both in this package's touch scope, and the
handoff's own product-boundary section states the two-state split is
"never a fallback" — the direct path is supposed to retain *only* P20's
original clean-tree R0 execution. Leaving ~370 lines of unreachable,
API-mismatched code (plus a ~170-line docstring that still described R1/R2/R3
running through this function) directly contradicts that, and the project's
own precedent (P22's LOG, §"Two guards deleted rather than left uncoverable")
is to restructure dead branches away rather than exclude or ignore them
(AUTHORING §3b.D).

**Fix:** removed the dead `if r1_declared:`/`if r2_declared:`/`if
r3_declared:` blocks and the `reservation`/`judgment_r1`/`judgment_r2`/
`judgment_r3`/`added_holder` machinery that only they used, leaving `run_lane`
below the dispatch as exactly what it now is: read `pre_run_head`, refuse on
pre-run dirt, run the command once, refuse on post-run dirt/`HEAD` movement,
else return the single R0 claim. Rewrote the docstring to describe the actual
current dispatch and the A-175 post-command check that still applies, instead
of the R1/R2/R3 orchestration prose that no longer executes here. `mutation.
resolve_mutation_targets` — whose only call site was inside the removed
block — was left in place in `mutation.py`: it remains part of that module's
public surface with its own direct test file
(`tests/test_mutation_resolve_targets.py`), unchanged and untouched, per the
handoff's "never duplicate their mechanics" instruction for P21 producers.

**Verified behavior-neutral.** Every test that reaches `run_lane` for an
R0-only lane was, by the same reachability argument, already exercising a
call where `reservation` was always `None` and the removed blocks were always
skipped — so removing them changes no observable behavior. Re-ran the full
project suite plus the locked acceptance suite immediately before and after:
identical `2218 passed, 2 failed [known, §5], 1 skipped` both times. Coverage
of `runner.py` rose from 81% to **96%** as a direct consequence (the dead
block is what the 19-point gap was; see §8).

## 5. Two more findings from the work itself

**A symbolic `judge.base` must be resolved before any snapshot exists.** P22
never preserves refs or branch names — only the reachable closure of one
resolved commit. An early version resolved `judge.base` with a bare
`rev-parse` against the consumer's real repository and passed the result
straight through. That is wrong whenever the declared base branch has commits
the prepared commit's own history does not contain: the snapshot only ever
holds one commit's closure, so a plain `rev-parse`'d ref that has since
diverged is simply absent inside it. Fixed by resolving through
`git.resolve_base` (the same merge-base computation the direct path already
uses) instead — merge-base idempotence (`merge-base(A, B) == A` when `A` is
already an ancestor of `B`) guarantees the resolved value is always an
ancestor of the prepared commit, so it is always reachable inside the
snapshot regardless of how far the declared branch has since diverged.
Recorded in the design guide (§6) since this applies to any future producer
that resolves a comparison base against a P22 snapshot, not just this
package's own R1/R3 call sites.

**The locked `FourSiteAdapter` acceptance fixture has its own defect,
independent of P23's implementation** — see §9.

## 6. Test additions beyond migration

Every test touching a changed signature (`CommandPlan`, `run_mutation`,
`run_isolated_canary`, `run_lane`) was migrated to the new primitives; where
the migration surfaced a design change (stale/symlinked/directory artifacts on
the live consumer tree are now invisible to P22 snapshots; a lane's own `git
commit` now happens inside the snapshot and never moves the consumer's real
`HEAD`; R3 is now gated on R0's own PASS; uncovered-line requires declared R1
at load time) the test was redesigned rather than mechanically patched — see
§7 for the full file list.

One new test was added beyond migration, closing a real coverage gap this
package's own new code introduced (found via the same coverage sweep as §4):
`tests/test_runner_run_lane.py::
test_run_lane_refuses_a_higher_rigor_lane_whose_caller_commit_is_stale` proves
the handoff's terminal-table row 3 ("caller commit differs from resolved
HEAD → `NO_MEASUREMENT`/`HEAD_CHANGED` all claims → payload-free; no
P22/process") for the pre-flight check in `_run_higher_rigor_lane` — distinct
from the already-tested case where dirt and a stale commit are both true (dirt
takes precedence and was the only case any existing test drove).

Not chased further, and left as a documented residual: `runner.py:1746`, the
R0-only direct path's own post-command `HEAD_CHANGED` branch (a plain R0 lane
whose command commits), has no dedicated test either before or after this
branch — confirmed by checking the base commit's test file, this is a
pre-existing P20-era gap this package did not introduce and does not claim to
close.

## 7. Results

| suite | command | result |
|---|---|---|
| locked acceptance (unmodified) | `pytest nyxloom-trove/carve-assets/P23/test_acceptance.py -q` | **17 passed, 2 failed** (§9) |
| project suite | `pytest tests -q` | **2202 passed, 1 skipped** |
| both together | as above, combined | **2219 passed, 2 failed, 1 skipped** |

Baseline for comparison: P22's own recorded combined figure was `2216 passed,
1 skipped` (`2196` project + `20` locked, post scope-correction).

Coverage of the four touched modules, branch-enabled, project suite + locked
acceptance combined:

| module | line | branch-partial | notes |
|---|---|---|---|
| `runner.py` | **96%** | 6 partial | residue: two clock-guard `ValueError`s (168/177), one `UnicodeDecodeError` branch (1070-1071), the `RuntimeError` cleanup-mask handler (1579-1589 — this LOG's B5 drove its `OSError` sibling live at line 1572, and manually confirmed the same masking bug via the `RuntimeError` handler too, but no locked or production test independently reaches that exact branch), and the pre-existing R0-only gap noted in §6 (1746) |
| `mutation.py` | 94% | 10 partial | defensive integrity/UTF-8 edge branches, same shape as P22's own residue |
| `canary.py` | 94% | 4 partial | malformed-transform/no-op edge branches |
| `config.py` | 99% | 4 partial | pre-existing P17/P21-era malformed-input guards (bool `max_mutants`, out-of-range `max_mutants`, backslash/`..`-escaping canary target), untouched by this package |
| **TOTAL** | **96%** | 24 partial | |

Matches or exceeds P22's own precedent (95%/93%). No `no cover` pragma was
added anywhere. The registered `tester-unified` gate was **not** run — the
controller owns it, its log, digest, markers and verdict.

## 8. Controlled breaks (bounded adversarial harness)

One temporary mutation at a time, the narrowest owning test from the locked
suite (occasionally the project suite when the locked suite did not drive
that exact branch), restoration verified by re-running the same test
immediately after and then the full suite once at the end. Every one of the
seven rows in the handoff's own traceability table
("work / production owner / oracle / controlled break required") was
exercised. No probe hung; every mutation restored cleanly, confirmed by
`git diff --stat` returning to its pre-probe state and a final full-suite run
reproducing the identical `2218 passed, 2 failed, 1 skipped` baseline (before
the one new test in §6 was added; `2219` after).

| # | row | break | owning test | red |
|---|---|---|---|---|
| B1 | rigor grammar (`config.py`, O3) | disable the R0-led canonical-subsequence check | `test_invalid_rigor_is_a_load_time_refusal` | **2 failed** |
| B2 | bounded replacement workers (`mutation.py`, O2/O4/O5) | disable the `candidate_count > max_mutants` sentinel | `test_max_plus_one_refuses_before_executor_snapshot_or_process` | **1 failed** ("max+1 must not construct an executor") |
| B3 | control/transform snapshots (`canary.py`, O1/O2/O4) | judge the transform half against the control's own already-executed unit | `test_canary_halves_do_not_reuse_control_or_consumer_coverage` + 4 `test_runner_run_lane_r3.py` cases | **5 failed** |
| B4 | seed/baseline/R1 orchestration (`runner.py`, O2/O5) | redirect the baseline unit's `cwd` to the live consumer project root | `test_nested_plan_is_identical_for_baseline_and_mutant_and_source_is_unchanged` | **1 failed** (`cwd == fixture.project`) |
| B5 | deadline/terminal propagation (`runner.py`, O4/O5) | swallow an outer scratch-cleanup `OSError` instead of replacing the highest higher-rigor claim | `test_scratch_exit_failure_replaces_only_highest_higher_rigor_claim` | **1 failed** (false `PASS` instead of `ERROR`/`GIT_FAILED`) |
| B6 | immutable plan / direct wrapper (`mutation.py`/`runner.py`, O1) | strip `argv_appended`/passthrough env from a mutant's effective plan | `test_nested_plan_is_identical_for_baseline_and_mutant_and_source_is_unchanged` | **1 failed** (process-ledger mismatch at the replacement unit) |
| B7 | reachable terminal audit (tests only, O5) | re-add `("BUDGET_EXCEEDED","SNAPSHOT_LIMIT_EXCEEDED")` to `EXCLUDED_ENTIRELY` | `test_snapshot_limit_artifact_closes_ordinary_raw_and_schema_audits` | **1 failed** (locked suite's own source-text check) |

B4 is a particularly direct demonstration of A-189's own claim: with the
baseline unit's `cwd` redirected to the live project directory, the injected
process spy's own `assert cwd != fixture.project` fires immediately — proving
the ordinary path really does run every unit from inside the P22 snapshot
project root, not the consumer's own tree, without needing to inspect
`runner.py` at all.

## 9. Known, irreconcilable acceptance-suite failure — not a P23 defect

`test_max_minus_one_and_max_execute_every_discovered_site[max-minus-one]` and
`[max]` (`nyxloom-trove/carve-assets/P23/test_acceptance.py:453`) both fail
with `MutationDiscoveryError`-driven assertion mismatches. Root cause, traced
fully: the locked test's own `FourSiteAdapter.generate_mutation_sites`
assigns `lineno=index + 1` to every discovered site — i.e. it numbers sites by
*discovery order*, not by which real source line each one's `start_byte`
falls on. The fixture source for both parametrizations places every
mutation site on the *same* real source line (`return x < 0 and x < 1 [and x
< 2]`, line 2), so any site after the first gets a `lineno` that does not
match `line_for_offset(source, site.start_byte)`.

`mutation.py::_validate_sites` (line 508-513, frozen P21 validation, unchanged
by this package) exists exactly to catch this class of adapter bug:

```python
expected_line = line_for_offset(source, site.start_byte)
if site.lineno != expected_line:
    raise MutationDiscoveryError(...)
```

This is the correct, working oracle — not a P23 regression — refusing a site
whose declared `lineno` contradicts its own `start_byte`. Both
`test_acceptance.py` (the fixture) and `mutation.py`'s validation are
forbidden/frozen respectively: I cannot edit the locked test file, and
weakening or removing `_validate_sites`'s line-consistency check to
accommodate a fixture bug would be exactly the "weaken an oracle to make a
locked test pass" move this package is explicitly forbidden from making.
Recorded as successor candidate `SB-P23-01` below rather than routed around.

## 10. Self-review against every oracle

- **O1 (one immutable plan; repeated units are byte-identical except cwd and
  a strictly decreasing positive timeout).** `execute_plan` is the only path
  any P23 unit reaches a process through; every mutant and both canary halves
  receive the exact `plan` object `_run_higher_rigor_lane` resolved once.
  Proven by the locked process-ledger test and B6 going red the moment a
  worker's plan is silently narrowed.
- **O2 (independent P22 unit per baseline/mutant/canary half; tracked
  siblings readable; no stale output; consumer byte/status identical).**
  Every unit materializes from `prepared` fresh; `_snapshot_left_dirt`/
  `_execute_snapshot_unit`'s own post-run check runs once per unit; the
  nested `apps/p` fixture with its tracked sibling passes in both the locked
  suite and the migrated `test_runner_run_lane*.py` files. B4 proves the
  baseline unit's `cwd` really is the snapshot's, not the consumer's, by
  breaking it and watching the injected spy's own assertion fire.
- **O3 (R0-led ordered subsequence; uncovered-line requires R1; refused at
  load time before any side effect).** The skeleton's grammar check, unedited
  by this package beyond what it already supplied, is proven by
  `test_config_rigor.py`/`test_invalid_rigor_is_a_load_time_refusal` and B1.
- **O4 (one injected deadline covering every boundary; max+1 sentinel before
  executor/unit launch).** `LaneDeadline.start` samples once;
  `deadline.remaining()` is resampled immediately before every P22/process
  boundary — proven by the locked ledger's strictly-decreasing timeouts and
  by `test_injected_expiry_after_one_mutant_launches_no_next_unit`. The
  sentinel is checked before `executor_factory`/`prepared`/`process_runner`
  are ever touched — proven directly by B2's exploding fixtures.
- **O5 (P22 refusal/cleanup terminals survive unchanged; every child/executor
  closes before the prepared seed; the reachable snapshot-limit pair is
  closed in ordinary conformance).** `_replace_highest_higher_rigor_claim_
  with_git_failed` never fires except from the outer `try`'s `AssayError`/
  `OSError`/`RuntimeError` handlers, and B5 proves a swallowed cleanup failure
  would otherwise silently launder into a false `PASS`. §4's dead-code removal
  additionally closes off the one place a stale, signature-mismatched
  alternate implementation could have masked a real P22 error had it somehow
  ever been reached. The snapshot-limit pair's ordinary-conformance closure
  (A-190) is proven by `test_verdict_conformance.py` and directly attacked
  by B7.

Not claimed: the registered `tester-unified` gate was not run; no statement
here is a gate verdict. §9's two failures are not claimed fixed — they are a
locked fixture defect outside this package's authority to repair.

## 11. Successor candidates

```yaml
- id: SB-P23-01
  text: "The locked P23 acceptance fixture's FourSiteAdapter.generate_mutation_sites
    numbers sites by discovery order (lineno=index+1) rather than by the real
    source line each site's start_byte falls on. Both parametrizations of
    test_max_minus_one_and_max_execute_every_discovered_site place every site
    on one real source line, so mutation.py's own frozen _validate_sites
    (line-consistency check, unchanged by P23) correctly raises
    MutationDiscoveryError for any site after the first. This is a defect in
    the LOCKED test fixture, not in P23's production code: fixing it requires
    either editing the forbidden carve-assets/P23/test_acceptance.py (an
    adapter that computes lineno from real newline counts) or weakening
    _validate_sites's own correctness check, and this package is authorized
    to do neither. Both parametrized cases were left red exactly as found."
  evidence_ref: "nyxloom-trove/carve-assets/P23/test_acceptance.py:401-421 (FourSiteAdapter), :453 (assertion); src/assay/mutation.py:508-513 (_validate_sites)"
  audience: carver
  applies_to: [P24]
  proposed_disposition: decision
  invalid_if: "a later carve corrects FourSiteAdapter's lineno computation, or a controller ruling accepts the current mismatch as intentional"

- id: SB-P23-02
  text: "A symbolic judge.base (a branch name, HEAD~1, or similar) must be
    resolved through the full merge-base computation (git.resolve_base)
    against the CONSUMER's real repository before any P22 snapshot exists --
    never a bare rev-parse. P22 preserves only the reachable closure of one
    resolved commit, never refs, so a plain rev-parse'd ref that has since
    diverged from the prepared commit is simply absent inside the snapshot.
    Merge-base idempotence (merge-base(A,B) == A when A is already an
    ancestor of B) is what makes the resolved value always reachable inside
    the snapshot regardless of how far the declared branch has diverged.
    Recorded in docs/DESIGN-GUIDE.md sec 6. Any future producer that resolves
    a comparison base against a P22 snapshot (P29/P30's Go helper path
    included) needs the same resolution, not a raw ref lookup."
  evidence_ref: "src/assay/runner.py::_resolve_declared_base; tests/test_runner_run_lane_r2.py::test_a_real_r2_lane_diffs_the_resolved_merge_base_not_the_declared_ref"
  audience: implementer
  applies_to: [P29, P30]
  proposed_disposition: promote-contract
  invalid_if: "a later package stops resolving judge.base against a P22 snapshot at all"

- id: SB-P23-03
  text: "When a run_lane-shaped function grows a two-state dispatch that
    returns early for one branch, everything below that return which used to
    handle the now-intercepted case becomes dead by construction -- not
    merely low-coverage, but potentially calling downstream functions with a
    signature that has since changed underneath it, with nothing to catch the
    mismatch because Python does not type-check unreached code. This package
    found exactly that shape in its own first pass (sec 4): ~370 lines
    including a call to mutation.run_mutation with the pre-P23 signature,
    silently dead since the dispatch above it always returns first. A
    coverage sweep across the touched modules (not just 'tests are green')
    is what surfaced it -- runner.py measured 81% with one large contiguous
    gap, and rose to 96% once the dead block was removed. Any future package
    that adds an early-return dispatch to an existing function should check
    reachability of what follows, not just add the dispatch and move on."
  evidence_ref: "commit history of this branch: docstring/body rewrite in src/assay/runner.py::run_lane, immediately following the r1_declared/r2_declared/r3_declared dispatch"
  audience: implementer
  applies_to: [P24, P25, P29, P30]
  proposed_disposition: one-hop
  invalid_if: "run_lane's dispatch shape changes such that no code below it is conditioned on the same booleans the dispatch already tested"
```

## 12. Scope

Touched: `src/assay/config.py`, `src/assay/runner.py`, `src/assay/mutation.py`,
`src/assay/canary.py`, `tests/**` (conftest.py plus every file listed in §7's
diffstat), `docs/DESIGN-GUIDE.md`, this LOG. No forbidden path was touched:
`src/assay/isolation.py`, `src/assay/git.py`, `src/assay/errors.py`,
`src/assay/schemas/**`, `src/assay/verdict.py`, `src/assay/verify.py`,
`src/assay/attestation.py`, `src/assay/adapters/**`,
`nyxloom-trove/carve-assets/P22/**`, `nyxloom-trove/carve-assets/P23/**`
(hashes reverified identical in §1), `pyproject.toml`, `assay.toml`,
`tools/**`, `nyxloom-trove/nyxloom.toml`. No `assay/README.md` exists to
touch. No BLOCKED condition was ever hit — no required contract needed a
forbidden path. P24 was neither dispatched nor reviewed.

---

# Appendix R — independent review record (Opus reviewer, phases 1 and 2)

Added by the fresh Opus xhigh reviewer, not the implementer. The sections
above are the implementer's own record and are left verbatim; this appendix
reconciles them against re-measured evidence and records what the review
changed. Review commits: `d85125dc` (phase 1, blind) and the commit carrying
this appendix (phase 2).

## R1. Implementer claims re-measured

| LOG claim | reviewer result |
|---|---|
| §1 seven locked SHA-256 values | **confirmed**, byte-identical at phase-1 entry and at phase-2 exit |
| §7 locked acceptance `17 passed, 2 failed` | **confirmed** |
| §7 project suite `2202 passed, 1 skipped` | **confirmed** |
| §7 combined `2219 passed, 2 failed, 1 skipped` | **confirmed** |
| §7 branch coverage 96% / 94% / 94% / 99%, total 96%, 24 partial | **confirmed, reproduced exactly** at `8268467f` with `--cov-branch` |
| §7 runner residue `168/177, 1070-1071, 1579-1589, 1746` | **confirmed** |
| §8 seven controlled breaks, one per traceability row | **not re-run**; the review ran its own eleven, listed in the review report |
| §12 no forbidden path touched | **confirmed** by explicit per-path diff |
| §6 "`runner.py:1746` … a pre-existing P20-era gap this package did not introduce" | **FALSE — see R2** |
| §10 O3 "proven by `test_config_rigor.py`" | **FALSE — that file contains no rigor-grammar test; see R3** |
| §11 `SB-P23-02` `evidence_ref` | test exists but lives in `tests/test_standalone.py`, not `tests/test_runner_run_lane_r2.py` |

## R2. §6's residual claim is false: the gap is P23-introduced

The direct R0-only path's post-command `HEAD_CHANGED` branch was **covered at
the carve parent**. Measured on a read-only extraction of `0d46e954`:

```text
pytest tests/test_runner_run_lane.py --cov=assay.runner
src/assay/runner.py  252  62  75%  161, 387-405, 494, 507, 648, 665, 677,
                                   1187-1306, 1313-1350, 1383
```

Line `1099` — that commit's spelling of `post_run_reason =
ReasonCode.HEAD_CHANGED` — is absent from the miss list. It was reached by
`test_run_lane_refuses_when_the_command_moves_head_even_though_the_tree_is_
clean`, which declares `rigor=("R0","R1")` and, before the two-state dispatch
existed, therefore ran through the direct path.

P23's dispatch moved that lane onto the committed-snapshot path, where
`_execute_snapshot_unit`'s own check fires instead, and the migration added no
R0-only replacement. Confirmed by controlled break: disabling the direct
branch left all 36 cases in `test_runner_run_lane.py` green. The gap is a
P23 regression, not a P20-era residue — and it is the registered R0-only
gate's own code path. Closed by
`tests/test_runner_p23_combined_axis_review.py::test_the_direct_r0_path_still_
detects_a_command_that_moves_head`.

## R3. A-192's grammar was unreachable from the gated suite

`config.py`'s canonical-subsequence refusal and the uncovered-line R1
prerequisite were reachable **only** from the byte-locked packet, which the
registered gate does not run. `tests/test_config_vocabularies.py`'s own
comment and §10's O3 paragraph both point at "test_config_rigor.py's own
grammar tests"; that file has none. O3's further requirement — refusal
"before Git, snapshot, output, or process side effects", with sentinels — was
unproven anywhere. Closed by `tests/test_config_rigor_grammar.py` (13 cases,
real sentinels on `git.run`/`dirty_paths`/`head_rev`/`repo_top`,
`isolation.prepare_snapshot`, `safeio.reserve_output`, `subprocess.run`/
`Popen`).

## R4. Phase-2 blocking defect: R2 target selection dropped three landed gates

`_mutation_targets_from_diff` applied source-root containment **only**. The
direct path it replaced called P18's landed
`mutation.resolve_mutation_targets`, which applies four gates: containment,
the adapter's `excluded_dir_names`, its `source_globs`, and its
`is_test_path`. §3 justifies the narrowing by a property of the locked test
double ("the locked `FourSiteAdapter` fixture lacks the adapter attributes
the frozen `resolve_mutation_targets` needs") — production selection policy
shaped around a fixture. Two defects were reproduced on ordinary
repositories:

* **False refusal.** A changed non-Python file under a declared source root
  (`pkg/NOTES.md`) reached `PythonAdapter.generate_mutation_sites`, which
  raises `MutationDiscoveryError` on unparseable text. Observed: `R0 PASS`,
  `R2 ERROR/MUTATION_DISCOVERY_FAILED`, **whole verdict**
  `ERROR/MUTATION_DISCOVERY_FAILED`, for a repository that measured normally
  before P23. Any project whose source root holds a changed `.md`, `.json`,
  `.pyi` or data file hits this — squarely on P25's path.
* **False evidence.** A changed `pkg/test_mod.py` under a declared source
  root became a real mutant identity in the R2 payload
  (`candidate_count=2`, both paths present). A suite that "kills" that mutant
  killed it by having its own test broken.

**Repaired** by deleting the partial duplicate and delegating to
`mutation.resolve_mutation_targets`, with `read_source_text` bound to the
prepared P22 seed so P23's "never reopen a consumer path" rule still holds
while the selection policy is exactly the landed one. Three regression tests
plus a still-outside-every-source-root control; controlled break (restoring
containment-only) turns two of them red.

## R5. F7 repaired: the wave loop's budget catch

`run_mutation` matched `exc.outcome is Outcome.BUDGET_EXCEEDED` alone, so a
P22 `BUDGET_EXCEEDED`/`SNAPSHOT_LIMIT_EXCEEDED` **policy refusal** escaping a
worker would have been relabelled `LANE_TIMEOUT` and reported as a
per-identity budget stop with the other identities still counted as evidence.
The handoff says "catch **only that exact** `BUDGET_EXCEEDED/LANE_TIMEOUT`
from `deadline.remaining()`". Now matched on the pair; proven by a direct
`run_mutation` test whose `prepared` stand-in refuses with P22's own pair, and
by controlled break.

## R6. SB-P23-01 adjudicated — retained as known-red, and now TWO fixture defects

Disposition retained: the locked suite stays at **exactly `2 failed, 17
passed`**, the same two parametrizations of
`test_max_minus_one_and_max_execute_every_discovered_site`, and both remain
defects in the locked fixture rather than in P23. `test_acceptance.py` was not
edited and `_validate_sites` was not weakened; all seven locked hashes are
byte-identical.

The review found the fixture's `FourSiteAdapter` has a **second**,
independent defect, and it is the earlier of the two:

* **SB-P23-01a — it is not a `LanguageAdapter`.** It declares no
  `source_globs`, no `excluded_dir_names` and no `is_test_path`, yet it is
  handed to `run_lane` for a lane declaring R2, whose landed P18 target
  selection requires all three. The fixture was authored against an
  implementation that does not apply the landed gates; it would have failed
  the same way had P23 simply kept calling `resolve_mutation_targets` the way
  the direct path always did.
* **SB-P23-01b — `lineno=index + 1`.** Numbering sites by discovery order
  contradicts `start_byte` whenever two sites share a source line, which both
  parametrizations guarantee. P21's frozen `_validate_sites` correctly
  refuses it.

**Consequence of R4's repair, stated plainly:** the two cases still fail, but
now at (a) rather than (b) — `AttributeError: 'FourSiteAdapter' object has no
attribute 'excluded_dir_names'` instead of the `MutationDiscoveryError`-driven
assertion mismatch §9 recorded. Fixing (a) alone would surface (b).

The contract both defects hide is proved independently, in ordinary tests, by
a conforming adapter that declares the three members and derives `lineno` via
the landed `mutation.line_for_offset`: at max−1 and max, baseline plus every
discovered site executes and R2 renders `PASS` with
`candidate_count == total == len(killed)`. A companion negative keeps (b)'s
refusal pinned as `ERROR/MUTATION_DISCOVERY_FAILED` with zero mutant
processes. Both live in
`tests/test_runner_p23_combined_axis_review.py`, so the evidence survives
whatever the carver decides.

**Carver action to reach 19/19:** in `carve-assets/P23/test_acceptance.py`,
give `FourSiteAdapter` `source_globs = ("*.py",)`,
`excluded_dir_names = frozenset()` and an `is_test_path` returning `False`,
and compute `lineno` with `line_for_offset(raw, offset)`; then re-hash
`test_acceptance.py` in `fixture-manifest.json` and in
`reports/assay-P23-JIT-CARVE.md`. Reviewer-verified as sufficient.

## R7. Residual, with dispositions

| item | disposition |
|---|---|
| O4's "the deadline covers **evaluation**" | **BLOCKED-equivalent / successor.** `evaluate_r1`'s in-snapshot `git diff`/`merge-base` and `_resolve_declared_base`'s consumer-side call run through `git._run_bounded`, which bounds bytes but has no wall-clock timeout and takes no deadline. `git.py` is forbidden to P23. The mechanical contract ("`remaining()` immediately before every P22 entry/read/materialization and process launch") is fully met. Needs either narrowed oracle wording or a `git.py` deadline seam as its own package. |
| `_resolve_declared_base` runs before `LaneDeadline.start` | same routing as above; it is one consumer-side `merge-base` outside the lane budget |
| `runner.py` `_relocate_source_roots` called with an unresolved `project_root` while `canary._judge_unit` resolves it | reported, not repaired; unreachable via `cli.py` (`load_lane_file` resolves before `.parent`) |
| an `OSError` escaping the prepared block discards a completed R0 claim | reported, not repaired; no reachable path found (`safeio`/`git`/`coverage`/`execute_plan` all convert first) |
| §11 `SB-P23-02` `evidence_ref` file name | corrected in R1; the successor candidate itself stands and the review endorses it |

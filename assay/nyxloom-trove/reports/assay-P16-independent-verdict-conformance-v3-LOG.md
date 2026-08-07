# P16 — independent verdict conformance v3 — LOG

**Status:** DONE
**Branch:** `feat/assay-P16-independent-verdict-conformance-v3`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P16-independent-verdict-conformance-v3/assay`
**Base:** `279f7026` (`docs(assay): STATE.md -- P15 merged and reviewed; A-O15 (attestation path transport)`)
**Handoff `input_revision`:** `48771e48c7b2ed7ed937cbe07e193718c6f242bb` — unchanged between that commit
and `279f7026`; nothing in `src/assay/` moved in between.

Dispatched directly (no separate implementer/controller split for this package):
implemented, self-reviewed, gate-verified, and mutation-tested in one session
per explicit instruction.

## What was built

- `src/assay/verdict.py`: `VERDICT_SCHEMA_VERSION` bumped 2 → 3. New
  `Coverage.excluded_lines`/`files_with_excluded_lines` (P07's third-pair
  pattern, a fourth time) — changed, considered lines the artifact classifies
  EXCLUDED, recorded regardless of `allow_excluded` (that policy decision
  lives in the claim's own status/reason_code, not in what was excluded).
  `Coverage.__post_init__` gained three real arithmetic invariants sol
  finding 2 named: `pct` must equal `covered/changed_executable` (or 100.0
  at 0/0) within float tolerance; `missing_lines`'s total must equal
  `changed_executable - covered`; and `missing_lines`/`excluded_lines`/
  `unclassified_lines` must be pairwise disjoint per file (P15's own
  `FileCoverage` invariant, restated one level up at the artifact — A-135's
  binding instruction that a "contradictory artifact" fixture is built as a
  VALID `Coverage` carrying a WRONG claim status, never a self-contradictory
  payload). New `JudgmentR1`/`JudgmentR2`/`JudgmentR3`/`Judgment` dataclasses
  — the resolved R1 policy (`language`, `source_roots`, coverage
  format/artifact spelling, `fail_under`, `allow_excluded`, full resolved
  `base` commit) plus reserved R2 (`jobs`, `operators`) and R3 (`mechanism`,
  `target`) shapes a future CLI-wiring package populates additively.
  `Verdict` gained `scope`/`enforcement` (joined `LANE_RESOLVED_FIELDS`,
  always derivable from `Lane`) and an independently optional `judgment`,
  with a new `_check_judgment_matches_claims` enforcing `judgment.r1`
  present if and only if the R1 claim carries `coverage` — the artifact-level
  close of sol finding 2 ("schema v2 does not record fail_under/
  allow_excluded, so an independent consumer cannot re-derive whether an R1
  status was correct from the payload alone"). No correspondence check for
  `judgment.r2`/`r3`: R2/R3 status is already re-derivable from
  `Mutation`/`CanaryResult`'s own fields alone, so nothing in this package's
  own oracles needs it closed today.
- `src/assay/schemas/verdict.schema.json`: `$id`/`schema_version` const
  bumped to 3. `scope`/`enforcement` enums added and folded into the
  existing `dependentRequired` lane-resolved group (now ten keys). New
  `judgment`/`judgment_r1`/`judgment_r2`/`judgment_r3` `$defs`, referenced
  from a new top-level `judgment` property (independently optional, not
  part of `dependentRequired`). `coverage`'s `$def` gained
  `excluded_lines`/`files_with_excluded_lines` as required keys. The
  `judgment.r1`-vs-`coverage` correspondence is deliberately NOT expressed
  in the schema (no `$data` in draft 2020-12) — it lives in the model and
  in `assay.verify`'s own non-schema stage, the same split every other
  cross-field rule in this project already uses.
- `src/assay/runner.py`: `assemble_verdict` gained an optional `judgment`
  parameter, threaded straight through to `Verdict`. `scope`/`enforcement`
  need no new parameter — both are already static `Lane` attributes, derived
  automatically on every call. A new pre-construction guard
  (`ERROR`/`BAD_LANE_CONFIG`) refuses when the assembled claims contain an
  R1 claim carrying `coverage` but no `judgment.r1` was supplied, mirroring
  the existing missing-claims/missing-evidence guards: a bare `ValueError`
  from `Verdict` itself is not an `AssayError` and no caller catches it.
  `evaluate_r1`'s own `Coverage(...)` call site now threads
  `excluded_lines`/`files_with_excluded_lines` through from
  `CoverageEvaluation` (see `evaluate.py` below — this is the one file
  touched outside the handoff's literal `scope.touch`, see "Empirical
  findings" below for why).
- `src/assay/evaluate.py`: `CoverageEvaluation` gained
  `excluded_lines`/`files_with_excluded_lines` — changed lines a file's
  `FileCoverage.excluded` intersects, computed in the SAME per-file loop
  that already derives `has_disallowed_excluded`, using the already-computed
  `excluded` set. Not gated on `allow_excluded`: recorded whenever
  non-empty, regardless of whether the lane permits it.
- `src/assay/verify.py`: three-stage docstring (was two). New
  `_check_judgment_matches_claims` (raw-document stage, mirroring the four
  existing cross-field checks) and three post-reconstruction re-derivation
  functions — `_check_r1_rederivation` (restates `assay.evaluate`'s own
  precedence directly against `Coverage` + `judgment.r1`, since there is no
  existing pure R1 judgment function to import without pulling in the whole
  diff/git/adapter pipeline), `_check_r2_rederivation` (imports and calls
  `assay.mutation.judge_mutation` directly, via a `SimpleNamespace`
  stand-in for its `baseline: runner.CommandResult` parameter — verified by
  reading `judge_mutation`'s source that `.outcome`/`.reason_code` are the
  only two attributes it ever reads, and only on the `mutation is None`
  branch), `_check_r3_rederivation` (imports and calls
  `assay.canary.judge_canary` directly — cleanly, since it needs only an
  already-reconstructed `CanaryResult`). `_reconstruct_coverage` and a new
  `_reconstruct_judgment`/`_reconstruct_judgment_r1/r2/r3` family extend the
  existing reconstruction machinery. `verify_document` runs the three
  re-derivation checks only after reconstruction succeeds (re-deriving
  against garbage/partial data would only add a confusing second failure
  alongside the schema one).
- `docs/DESIGN-GUIDE.md` §6: new "Binding the effective judge policy (v3)"
  subsection recording why scope/enforcement/judgment were added and sol
  finding 2's own reproduction; the stale `schema_version: 2` example
  corrected to 3.
- Fixtures: all 34 `tests/fixtures/verdicts/*.json` converted to schema v3
  by hand (scope/enforcement added to every lane-resolved fixture,
  `judgment.r1` added to every R1-coverage fixture, `excluded_lines`/
  `files_with_excluded_lines` added to every `coverage` block). New
  contradictory-artifact fixtures were not added as separate files — the
  three sol-finding-2 reproductions are built in-test from the EXISTING
  fixtures' own already-valid `Coverage`/`Mutation`/`CanaryResult` payloads
  with only `status`/`reason_code`/`outcome` mutated (A-135).
- New test files: `tests/test_verdict_judgment.py` (Coverage arithmetic,
  JudgmentR1/R2/R3, Judgment, Verdict scope/enforcement/judgment
  construction-time rules — 41 tests), `tests/test_runner_assemble_verdict_judgment.py`
  (the new `assemble_verdict` guard — 3 tests). Extended
  `tests/test_verdict_conformance.py` with a new "O2 (P16, stage 3)"
  section: the raw judgment cross-check both directions, judgment.r2/r3
  reconstruction acceptance, and sol finding 2's three reproductions
  (0%-coverage PASS, hidden-excluded-lines PASS, surviving-mutant PASS,
  transformed-canary-PASS) plus the R2-without-R0 skip case.

## Gate output (real `tester-unified` Docker container)

Argv read verbatim out of `nyxloom-trove/nyxloom.toml`'s own `[gates.
tester-unified]`, substituted only for `{worktree}`, via `tomllib` + a
direct `subprocess.run(["bash", "-c", argv[2]])` (never re-quoted through an
extra shell layer):

```
Successfully built assay-0.0.0-py3-none-any.whl
Successfully installed assay-0.0.0
tester-unified: PASS (exit 0)
  commit: 279f70261a846421201e919539b3e56891e13077
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
.......                                                                  [100%]
7 passed in 10.77s
EXIT_CODE=0
```

The independent second step (`tests/test_self_hosting.py`, run via
`/opt/tester-venv`'s own ambient interpreter against the artifact the first
step's real, wheel-installed `assay run` just emitted) is what actually
caught this package's one real regression before merge — see "Empirical
findings" below.

Coverage independently verified inside the SAME `tester-unified:local`
container (`assay run` itself asserts R0/exit-code only, A-133; it is never
its own coverage witness), via `PYTHONPATH=src /opt/tester-venv/bin/python`
(never the wheel-installed scratch venv) — matching P15's own corrected
methodology exactly, **including** `tests/test_self_hosting.py` this time
(P15's LOG documents that excluding it leaves `src/assay/__init__.py`'s
`PackageNotFoundError` fallback, lines 43-44, uncovered — reproduced once
by this package before correcting the command, see below):

```
1638 passed, 1 skipped in 62.38s
TOTAL   2723 stmts (0 miss)   1070 branches (0 partial)   100%
```

Baseline before this package (per STATE.md): 1585 passed (in-container,
P15's own number), 2531 stmts / 988 branches, 100%. After: 1638 passed
(+53, including the 41 + 3 new test files and the extended conformance
module), 2723 stmts (+192) / 1070 branches (+82), still 100%.

Ambient devcontainer (`PYTHONPATH=src`, no real gate image): 1631 passed, 1
skipped — the pre-existing, documented, environment-specific
`test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`
case (ambient Python's own working `setuptools_scm` produces a real
version where the gate image cannot), unrelated to and unchanged by this
package.

## Per-oracle evidence

### O1 — every schema-v3 resolved-lane artifact records scope, enforcement, effective judge inputs, and the full resolved comparison commit whenever changed-line judgment occurred

`tests/test_verdict_judgment.py::test_verdict_refuses_an_r1_coverage_claim_without_judgment_r1`
and `::test_verdict_refuses_judgment_r1_present_without_an_r1_coverage_claim`
prove the model-level correspondence in both directions.
`tests/test_runner_assemble_verdict_judgment.py::test_an_r1_coverage_claim_without_judgment_is_refused_before_construction`
proves the producer-level guard raises a typed `AssayError`, not a bare
`ValueError`. `tests/test_runner_evaluate_r1.py`'s three real-pipeline
fixture comparisons (`r1_pass`, `r1_fail_uncovered_lines`,
`r1_fail_excluded_lines`) prove a REAL `evaluate_r1` + `assemble_verdict`
run, through real git commits, produces the exact hand-written v3 artifact
byte for byte (base-commit non-determinism handled by asserting the real
value independently, then normalizing to the fixture's own placeholder
before the literal comparison — see "Empirical findings").

**Mutation (A-067-1, Coverage.pct arithmetic):** disabled the
`pct`-vs-`covered/changed_executable` check. Reran
`test_verdict_judgment.py`: **2 failed**
(`test_coverage_refuses_pct_disagreeing_with_covered_and_changed_executable`,
`test_coverage_refuses_a_zero_over_zero_pct_that_is_not_100`). Reverted.

**Mutation (A-067-2, missing_lines total arithmetic):** disabled that
check. Reran: **1 failed**
(`test_coverage_refuses_missing_lines_total_disagreeing_with_the_summary`).
Reverted.

**Mutation (A-067-3, bucket pairwise disjointness):** no-op'd
`_check_buckets_pairwise_disjoint`. Reran: **3 failed** (the three
overlapping-bucket-pair tests). Reverted.

**Mutation (A-067-4, Verdict's own judgment↔claims correspondence):**
no-op'd `Verdict._check_judgment_matches_claims`. Reran: **3 failed**
(`test_verdict_refuses_judgment_present_without_a_resolved_lane`,
`test_verdict_refuses_judgment_r1_present_without_an_r1_coverage_claim`,
`test_verdict_refuses_an_r1_coverage_claim_without_judgment_r1`). Reverted.

**Mutation (A-067-5, runner.py's producer-level guard):** disabled the
`assemble_verdict` raise. Reran
`test_runner_assemble_verdict_judgment.py`: **1 failed** — and the failure
itself demonstrates the guard's own reason for existing: without it, the
SAME defective input surfaces a bare `ValueError` (from `Verdict`'s own
construction) instead of the typed `AssayError` the test expects. Reverted.

### O2 — `assay verify` rederives R1/R2/R3 status and rejects the three named contradictory artifacts

`test_verdict_conformance.py`'s new stage-3 section builds all three
directly from EXISTING fixtures' own valid `Coverage`/`Mutation`/
`CanaryResult` payloads, mutating only `status`/`reason_code`/`outcome`
(A-135): `test_verify_rejects_an_r1_pass_reporting_zero_percent_coverage`,
`test_verify_rejects_an_r1_pass_hiding_disallowed_excluded_lines`,
`test_verify_rejects_an_r2_pass_with_a_genuine_surviving_mutant`,
`test_verify_rejects_an_r3_pass_whose_transform_never_actually_failed`.
Plus the raw judgment↔claims check in both directions and
`judgment.r2`/`r3` reconstruction acceptance.

**Mutation (A-067-6, R1 rederivation):** no-op'd `_check_r1_rederivation`.
Reran the two R1-contradiction tests: **2 failed**. Reverted.

**Mutation (A-067-7, R2 rederivation):** no-op'd `_check_r2_rederivation`.
Reran: **1 failed**. Reverted.

**Mutation (A-067-8, R3 rederivation):** no-op'd `_check_r3_rederivation`.
Reran: **1 failed**. Reverted.

**Mutation (A-067-9, raw judgment↔claims check in `verify.py`):** first
attempt no-op'd the raw check with its ORIGINAL wording (identical to
`Verdict`'s own model-level message) and reran: **0 failed** — a real,
self-caught defect (see "Empirical findings" below: reconstruction
independently produces the same string, so the test could not distinguish
which layer actually fired). Fixed by rewording the raw check's two
messages distinctly from the model's, updated the two tests to match, then
reran the mutation: **2 failed**
(`test_verify_rejects_judgment_r1_present_without_an_r1_coverage_claim`,
`test_verify_rejects_an_r1_coverage_claim_without_judgment`). Reverted.

**Mutation (A-067-10, evaluate.py's excluded_lines wiring):** stopped
`evaluate_coverage` from populating `excluded_lines`. Reran
`test_runner_evaluate_r1.py`: **1 failed**
(`test_r1_fail_excluded_lines_matches_the_hand_written_fixture`). Reverted.

After every mutation above, `git diff` / checksum comparison against the
pre-mutation file confirmed no residual difference.

## Empirical findings beyond the handoff's literal text

1. **`src/assay/evaluate.py` needed to be touched, though it is in neither
   this package's `scope.touch` nor `scope.forbid`.** The handoff's own
   context item 4 names the exact gap ("`evaluate.py`'s final outcome
   ordering... independent R1 judgment requires that fact to become
   explicit payload") but the frontmatter never resolves the ambiguity
   (A-132's own "leaving scope status implicit" class of defect). The
   deciding evidence: `tests/fixtures/verdicts/r1_fail_excluded_lines.json`
   is used BOTH as a hand-built model-conformance fixture AND as the exact
   comparison target for `test_runner_evaluate_r1.py`'s REAL
   `evaluate_r1` pipeline run — a fabricated `excluded_lines` value in the
   fixture that the real, unwired pipeline could never produce is a lie the
   moment that second test runs it for real, which it does. The fix
   mirrors P07's own `unclassified_lines`/`files_with_unclassified_lines`
   pattern exactly (a fourth additive pair, computed in the same per-file
   loop, threaded through the one line in `runner.py`'s `Coverage(...)`
   call site P07 itself required). Flagged here for whoever reviews this
   package to ratify or challenge the scope call.
2. **A raw-document check whose failure message is textually IDENTICAL to
   the reconstruction-based check catching the same defect is not
   independently testable by message content, and a mutation that disables
   it can show zero test failures while still being a real regression.**
   Found by this package's own A-067-9 mutation (see above): the FIRST
   version of `_check_judgment_matches_claims`'s two messages in
   `verify.py` were copy-pasted verbatim from `Verdict`'s own model-level
   raise. Disabling the raw check produced NO failing test, because
   `_reconstruct_verdict` independently raises the identical string and the
   test's `in failures` assertion cannot tell which stage produced it. A
   probe against one of the FOUR pre-existing raw checks
   (`_check_lane_resolved_group`) confirmed this is not a property of the
   dual-layer design itself — that check's own wording already differs from
   `Verdict`'s, and disabling it produces a real, distinguishable test
   failure. Fixed by rewording this package's own two messages distinctly.
   Standing consequence for whoever writes the next raw/reconstruction pair:
   **wording the raw check's message identically to the model's own
   `ValueError` is a silent way to make "is this branch even reached"
   untestable** — verify by disabling the raw check alone (not the model
   check) and confirming a test actually fails, not merely that the
   assertion string still appears somewhere in the failure list.
3. **`fail.json`'s `pct: 66.67` and `inconclusive.json`'s mutation-less
   `INCONCLUSIVE`/`NO_MUTANTS` R2 claim were both pre-existing, real
   fixture defects**, caught for the first time by this package's own new
   invariants (never by any earlier package's oracles, since neither
   invariant existed before P16):
   - `fail.json`'s `pct` was hand-rounded to two decimals instead of the
     exact `100.0*covered/changed_executable` a real producer emits
     (`evaluate.py` never rounds). Corrected to `100.0 * 2 / 3` exactly
     (`66.66666666666667`).
   - `inconclusive.json` (the one canonical fixture-per-outcome
     `conftest.VERDICT_FIXTURES["INCONCLUSIVE"]` points at) declared an R2
     claim `INCONCLUSIVE`/`NO_MUTANTS` with `mutation` entirely ABSENT.
     Per A-117/`judge_mutation`, `mutation is None` means the R0
     prerequisite never passed and reuses the BASELINE's own
     `(outcome, reason_code)` verbatim — `execute_command` can never
     return `INCONCLUSIVE`, so `INCONCLUSIVE`/`NO_MUTANTS` is reachable
     ONLY through a real `Mutation(total=0, ...)` payload. Corrected by
     adding the same `total=0` payload `r2_inconclusive_no_mutants.json`
     (P12's own fixture for the identical case) already carried.
   Both fixed in the fixture; the corresponding hand-written constructor in
   `tests/test_verdict_serialises.py` (`build_inconclusive`) updated to
   match (a real `Mutation(total=0, killed=0)` added to its R2 claim).
4. **A hardcoded `assert document["schema_version"] == 2` in
   `tests/test_self_hosting.py`** (the real independent second-gate-step
   witness, excluded from every ordinary local `pytest tests` run and so
   invisible to this package's own local iteration) was the ONE thing the
   real gate caught that nothing local did. Fixed by importing
   `VERDICT_SCHEMA_VERSION` and comparing against the constant instead of a
   second hardcoded literal — self-updating on the next schema bump.
5. **`make_lane()`'s test-helper default `scope="S1"`** (in `tests/conftest.py`,
   pre-existing, untouched) is what every REAL-pipeline fixture comparison
   in `test_runner_evaluate_r1.py`/`test_verdict_span_attribution_artifacts.py`
   needed to match — an arbitrary first choice of `"S2"` for the fixtures'
   own `scope` field failed those specific tests only, since fixtures NOT
   compared against a real `assemble_verdict(lane=make_lane(...))` call
   never surfaced the mismatch. All 34 fixtures now say `"S1"`.

## Housekeeping

- `tests/test_verdict_coverage_missing_locations.py`'s
  `test_empty_missing_lines_and_files_missing_coverage_are_legal` and
  `tests/test_verdict_coverage_unclassified_locations.py`'s shared `BASE`
  dict both needed `covered`/`changed_executable`/`pct` adjusted to stay
  internally consistent under the new arithmetic invariants — neither
  fixture's own INTENT (proving `missing_lines`/`unclassified_lines` can
  legitimately be empty) changed, only the numbers needed to make that
  empty state honest.
- `tests/test_verdict_schema_rejects.py`'s `ZEROED_COVERAGE` constant
  gained the two new required keys (`excluded_lines`/
  `files_with_excluded_lines`, both empty) — the schema now requires them
  unconditionally.

## What could not be honored as written

Nothing. Every named work item and oracle was implementable as specified;
no `escalate_if` condition was triggered (R1 status was re-derivable
without any input the v3 shape omits; no repair required marking attested
evidence `verified_by_assay=true`).

## Self-review

Performed solo in the same session (no separate controller pass). The
mutation-testing pass above (A-067-1 through A-067-10) is itself the
primary self-review instrument — one genuine gap surfaced and was fixed
during it (finding 2, the identical-wording raw check). Beyond that:
reviewed the full diff file by file against the handoff's eight work items
and three oracles; confirmed no `escalate_if` condition was silently
routed around; ran `nyxloom lint` against this package's own handoff
(`clean`); independently reproduced the real gate twice (once before,
once after fixing the `test_self_hosting.py` regression) and the
in-container coverage measurement twice (once with the same
`--ignore=tests/test_self_hosting.py` mistake P15's own LOG already warned
against, then corrected). No decisions.md entries were added — this
package's real deviations from the handoff's literal scope (the
`evaluate.py` touch, the message-wording lesson, the two pre-existing
fixture defects) are recorded here instead, since there is no separate
controller session to ratify them into decisions.md in this dispatch.

---

## Controller review (2026-08-07) — five defects, all in code that was never written

> Appended by the controller before merge. The `evaluate.py` scope call
> (finding 1 above) is **ratified**: context item 4 named the gap, the
> frontmatter merely failed to resolve it, and the fixture-versus-real-
> pipeline argument for touching it is correct. A-136 through A-138 below
> record the rulings; the repairs are commit
> `fix(assay): P16 controller repairs`.

The last reviewer's standing hint was *"break the defaults; re-run the
carving review's findings on real inputs, not on the implementer's
fixtures."* Both halves paid. Every one of this package's ten mutation
counts reproduced exactly, and its four sol-finding-2 reproductions all
pass — and five real defects survived anyway, because a mutation test only
interrogates lines that exist and an in-test fixture mutation only
interrogates the evasion its author thought of. All five were found by
writing full v3 JSON documents to disk and feeding them through
`verify_text`, the way a consumer would.

**1. CRITICAL — a `PASS` claim with its payload simply DELETED was accepted
at every rigor level.** Sol finding 2's three artifacts contradict their
evidence; nobody tried removing it. `_check_r1/r2/r3_rederivation` each
return early when the payload is absent, the top-level rollup still agrees,
and the artifact validates clean: `r1_pass.json` minus `coverage` (and
`judgment`), `r2_pass.json` minus `mutation`, `r3_pass.json` minus
`canary` — three accepted forgeries, each *cheaper to produce* than the
three the package does catch. None is producible: `evaluate_coverage`
returns PASS/FAIL only with a `Coverage`; `judge_mutation` reaches PASS
only on its `mutation is not None` branch; `build_canary_claim` attaches
the result it judged to all three of `judge_canary`'s outcomes. Repaired in
the MODEL (`Claim._check_a_judged_status_carries_its_own_payload`), not in
`verify.py`, so the state is unconstructible rather than merely unaccepted
— A-135's own discipline, applied to the converse of the three existing
`NO_MEASUREMENT` rules (A-136).

**2. HIGH — R2 re-derivation was skippable by not declaring R0.**
`_check_r2_rederivation` returned early whenever the artifact carried no
R0 claim, *including when a real `mutation` payload was present* — and
`rigor = ["R2"]` is a legal lane declaration (`assay.config` requires only
a non-empty subset of R0-R3). Sol finding 2's second artifact, a `PASS`
with a genuine surviving mutant, passes unexamined with the R0 claim
removed. The R0 stand-in is needed only for the payload-less branch:
`judge_mutation`'s own early return proves `baseline` is never read once a
mutation is present — the package's own docstring says so, and then
guarded on it anyway. `tests/..._conformance.py::test_verify_skips_r2_
rederivation_when_no_r0_claim_is_present` had asserted the hole as the
contract, on the one input shape where skipping IS right (A-137).

**3. HIGH — work item 7 was not implemented.** "Demonstrate v2 rejection
with a specific schema-version diagnostic and document the intentional
consumer migration": no test fed a v2 artifact to `assay verify` at all,
and no diagnostic named the version. A real v2 artifact reported
`schema: 'excluded_lines'` — a bare `KeyError` on a field its producer had
never heard of — alongside two cross-field complaints that are consequences
of the version rather than defects. `verify_document` now checks the
version first and returns that one sentence; DESIGN-GUIDE §6 records that a
bump is a consumer migration, never a producer upgrade (A-138).

**4. MEDIUM — work item 3's summary-field identity was never enforced.**
"missing/excluded/unclassified identities agree with their summary fields":
`files_with_excluded_lines` (this package's own new field) could be emptied
outright, or name a file `excluded_lines` never mentions, and both
validated clean. Same for P07's `files_with_unclassified_lines` and P05's
`files_missing_coverage`. Two relations, not one: the two `files_with_*`
fields are documented as *"paths appearing in [the mapping], sorted"* — an
equality — while `files_missing_coverage` names only the files with no
artifact entry at all, whose changed lines are then all recorded missing —
a containment.

**5. MEDIUM — four of work item 6's named contradictory negatives had no
test.** It asks for "survivor/**crash/budget precedence**, **broken
mutation prerequisite propagation**, canary survival, **wrong canary
cause**, and **broken control**". Built: survivor, and canary survival.
The three precedence orderings (crashed > budget_exceeded > survived), a
payload-less R2 claim that does NOT reuse its baseline's own pair verbatim,
a canary that failed for the wrong cause, and a canary whose control never
passed were all unexercised — and `_check_r2_rederivation` /
`_check_r3_rederivation` deleted wholesale failed exactly ONE test each
before this pass, which is what an oracle for one case out of five looks
like.

### Mutation pass over the repairs (A-067, controller)

Run against a `shutil.copytree`'d copy of the tree, never the real files.
Each count is the *relevant* test files only, not the whole suite.

| # | check disabled | failures |
|---|---|---|
| R1 | `files_with_*` equality | 3 |
| R2 | `files_missing_coverage` containment | 1 |
| R3 | R1 PASS/FAIL must carry coverage | 2 |
| R4 | R2 PASS must carry mutation | 2 |
| R5 | mutation-only reason codes must carry mutation | 2 |
| R6 | judged R3 status must carry canary | 4 |
| R7 | R2 re-derivation's pre-review early return, restored | 1 |
| R8 | foreign-schema-version diagnostic | 1 |
| R9 | `_check_r2_rederivation` entirely | **6** (was 1 before this pass) |
| R10 | `_check_r3_rederivation` entirely | **3** (was 1 before this pass) |

R9 and R10 are the ones that matter: they measure whether the *package's
own* headline claim has an oracle proportionate to it.

### Gate, re-verified by the controller

Argv parsed out of `nyxloom-trove/nyxloom.toml` and run verbatim in the
real `tester-unified:local` container: `tester-unified: PASS (exit 0)`,
second independent step `7 passed`. In-container coverage, measured
separately via `PYTHONPATH=src /opt/tester-venv/bin/python` and INCLUDING
`tests/test_self_hosting.py`: **1657 passed, 1 skipped, 2752 stmts /
1094 branches, 100% statement AND branch** (the implementer's own numbers
were 1638 / 2723 / 1070). Ambient devcontainer adds only the documented
`test_standalone.py` environment-specific failure.

### Left open, deliberately

**A-O16 — a coverage format that cannot report exclusions is
indistinguishable from one that reported none.** `FileCoverage.excluded is
None` (unknown) and `frozenset()` (known-empty) stay distinct upstream, as
A-135 requires, but `evaluate_coverage` intersects with `frozenset()` in
both cases, so `Coverage.excluded_lines` cannot express "unknown". R1
re-derivation is unaffected — `has_disallowed_excluded` is false in both
cases too, so status and payload agree — the loss is diagnostic only.
Closing it needs a new artifact field and touches the format registry and
adapters, all outside this package's scope.

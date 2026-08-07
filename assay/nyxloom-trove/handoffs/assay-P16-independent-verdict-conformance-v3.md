---
schema_version: 1
id: assay-P16-independent-verdict-conformance-v3
project: assay
title: "Schema v3 binds policy and makes every computed claim independently re-judgeable"
tier: implement-2
input_revision: "48771e48c7b2ed7ed937cbe07e193718c6f242bb"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P15-measurement-input-integrity]
session: resume:assay-v11-verdict
scope:
  touch: ["src/assay/verdict.py", "src/assay/verify.py", "src/assay/runner.py", "src/assay/config.py", "src/assay/schemas/**", "tests/fixtures/verdicts/**", "tests/**", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/cli.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/attestation.py", "src/assay/adapters"]
oracles:
  - id: O1
    observable: "Every schema-v3 resolved-lane artifact records scope, enforcement, effective judge inputs, and the full resolved comparison commit whenever changed-line judgment occurred"
    negative: "Deleting fail_under, allow_excluded, excluded-line evidence, or comparison commit makes an independent R1 re-judgment impossible and fails the hand-written artifact comparison"
    gate: tester-unified
  - id: O2
    observable: "assay verify rederives R1, R2, and R3 statuses from payload plus recorded policy and rejects PASS/0-percent coverage, PASS-with-survivor mutation, and PASS-transformed canary artifacts"
    negative: "A verifier checking schema and rollup only accepts all three contradictory artifacts"
    gate: tester-unified
  - id: O3
    observable: "Schema v3 retains declared_evidence/evidence sibling arrays unchanged in identity semantics, and complete hand-written fixtures cover every representable producer terminal class"
    negative: "Moving attested evidence into rigor-keyed claims or dropping an empty reserved sibling breaks v2-to-v3 consumer migration tests"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "R1 status cannot be rederived without storing an input the schema-v3 shape omits"
  - "the repair requires marking attested evidence verified_by_assay=true"
mutexes: []
---

# P16 — independent verdict conformance v3

The claim to attack: **a consumer independent of the producer can reconstruct why every computed claim received its recorded status.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P16-independent-verdict-conformance-v3`
on branch `feat/assay-P16-independent-verdict-conformance-v3`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§1, 6, 7, 10, and 12; decisions A-023–A-025, A-029, A-033, A-041, A-051, A-065, A-071, A-074–A-078, A-092, A-100, A-108, A-116–A-117, and A-128–A-131.
2. `src/assay/verdict.py` in full, especially `Coverage`, `CanaryResult`, `Mutation`, `Claim`, `Verdict`, `rollup`, and `load_schema`; read the schema's `$comment` before editing `src/assay/schemas/verdict.schema.json`.
3. `src/assay/verify.py` and `tests/test_verdict_conformance.py`; reproduce the three accepted contradictory artifacts in `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` finding 2 before implementation.
4. `src/assay/evaluate.py`'s final outcome ordering. Its disallowed-exclusion fact currently disappears before `Coverage` is constructed; independent R1 judgment requires that fact to become explicit payload rather than inferred from the producer-selected reason code.
5. `src/assay/canary.py::judge_canary` and `src/assay/mutation.py::judge_mutation`; import or extract shared pure judgment rules rather than copy mappings into `verify.py`.
6. P15's handoff and resulting merged implementation. P16 depends on its disjoint coverage model and must not reopen raw-input parsing.
7. `/workspaces/vbpub/nyxloom/reference/DOCTRINE.md` independent-artifact, mutation-proof, and defaults rules.

## Work

1. Bump only the verdict artifact schema from v2 to v3. Do not bump the lane-file schema. Keep `claims[]`, `declared_evidence[]`, and `evidence[]` as sibling arrays with their current identity semantics.
2. Add resolved-lane policy fields sufficient for an independent consumer: top-level `scope` and `enforcement`, plus a closed `judgment` object. Its R1 portion records effective language, source roots, coverage format, artifact spelling, `fail_under`, `allow_excluded`, and the full resolved comparison commit. Reserve closed optional R2 (`jobs`, ordered operators) and R3 (`mechanism`, target) portions so P18/P19 populate them additively without another schema bump.
3. Extend `Coverage` with an always-present `excluded_lines` path-to-lines mapping. Empty means known-and-empty; format inability remains represented upstream as unknown exclusion data and must not be rewritten to empty. Enforce complete arithmetic: `pct` agrees with `covered/changed_executable`, missing/excluded/unclassified identities agree with their summary fields, and the buckets cannot contradict the totals.
4. Make `assay verify` independently derive R1 status/reason from coverage plus effective R1 policy, R2 status/reason from mutation buckets (or the prerequisite R0 claim when mutation never began), and R3 status/reason through the same pure canary judgment used by the producer. Never accept a producer-selected status merely because top-level rollup agrees with it.
5. Preserve the current verifier boundary: it validates an artifact, never reruns a lane and never becomes the sole witness. Keep independent `jsonschema` validation and full hand-written expected objects as the primary fixture oracle.
6. Convert every expected artifact to v3 by hand and add explicit contradictory negatives for wrong percentage, wrong threshold result, hidden excluded lines, survivor/crash/budget precedence, broken mutation prerequisite propagation, canary survival, wrong canary cause, and broken control.
7. Demonstrate v2 rejection with a specific schema-version diagnostic and document the intentional consumer migration. Do not silently coerce or upgrade an input artifact.
8. Break each new policy field, excluded-line payload, arithmetic derivation, R1/R2/R3 re-judgment, and evidence-sibling invariant separately; run the real gate and record exact failure counts under A-067.

## Test constraints copied from AUTHORING.md §3b

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

## Package-specific test emphasis

**A. No speed-dependent verdicts.** All conformance checks are pure object comparisons; no command duration participates.

**B. No order/worker dependence.** Each artifact is immutable test input, and mutations operate on fresh parsed copies.

**C. No hollow tests.** Expected artifacts and expected failures are hand-written outside assay's enums and producer helpers; `assay verify` is never its own sole oracle.

**D. No coverage evasion.** The full gate remains 100% statement and branch, with a real failure count for every controlled semantic contradiction.

**E. Control inputs.** No network or live repository state is needed; schema/package-data tests install and read the real built wheel offline.

## Scope / forbid

This package changes the artifact contract and independent validator only. It must not make R1–R3 reachable from the CLI, execute mutation/canary/attestation work, or edit adapter behavior. Those capabilities depend on this frozen v3 input.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

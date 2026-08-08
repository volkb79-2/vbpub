---
schema_version: 1
id: assay-P21-verdict-v4-evidence-contract
project: assay
title: "Verdict v4 carries enough bounded evidence to verify every judgment"
tier: implement-2
input_revision: "1d31eae137156e31abf0c88e6c8381941696d66c"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P20-repository-artifact-boundary-integrity]
session: fresh
scope:
  touch: ["src/assay/errors.py", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/config.py", "src/assay/coverage.py", "src/assay/evaluate.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/runner.py", "src/assay/cli.py", "src/assay/schemas/**", "tests/**", "README.md", "docs/DESIGN-GUIDE.md", "assay.toml"]
  forbid: ["src/assay/adapters/python.py", "src/assay/adapters/go.py", "pyproject.toml"]
oracles:
  - id: O1
    observable: "Model construction, shipped JSON Schema, and independent raw-document verification accept the same closed v4 vocabulary and reject every v1-v3 artifact with one version-only diagnostic"
    negative: "An unknown mutant operator passes assay verify, or a v3 artifact is coerced/defaulted into v4"
    gate: tester-unified
  - id: O2
    observable: "Every attempted mutant, including killed mutants, carries a stable identity and is bound to the declared operator policy; a required positive max_mutants is recorded and excess candidates stop before any mutant command"
    negative: "Changing a killed identity/operator remains schema-valid and verify-clean, or max_mutants+1 submissions run as a silently truncated sample"
    gate: tester-unified
  - id: O3
    observable: "A canary payload records the exact project-relative target and coverage records whether exclusion data was reported or unavailable; both correspond to the resolved judgment policy"
    negative: "Changing judgment.r3.target or rewriting exclusion-unavailable as known-empty passes independent verification"
    gate: tester-unified
  - id: O4
    observable: "Verdict intervals satisfy ended >= started and an unavailable verdict destination is detected before the lane command, exits ERROR with OUTPUT_WRITE_FAILED, and causes no consumer-side effects"
    negative: "A reversed interval validates, or an unwritable output path runs the command before failing with a traceback/generic exit"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "one of the named facts cannot be represented without a second schema bump"
  - "output destination readiness cannot be established before execution without writing outside the declared path"
mutexes: []
---

# P21 — verdict v4 evidence contract

The claim to attack: **every fact needed to reproduce Assay's judgment is present, bounded, and independently checkable in one v4 artifact.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P21-verdict-v4-evidence-contract`
on branch `feat/assay-P21-verdict-v4-evidence-contract`.

## Context to read first

1. `nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md`, findings F08–F12 and the schema-v4 recommendation; reproduce the unknown-operator verifier acceptance before implementation.
2. `docs/DESIGN-GUIDE.md` §6 in full; decisions A-008, A-027–A-029, A-041, A-050, A-067, A-116–A-117, A-135–A-138, A-148, A-152 and A-157–A-158.
3. `src/assay/verdict.py`, `src/assay/schemas/verdict.schema.json`, and `src/assay/verify.py` side by side. List each cross-field invariant and prove which of the three layers owns it before editing.
4. `src/assay/mutation.py::{MutantOutcome,run_mutation,judge_mutation}`, `src/assay/canary.py`, `src/assay/evaluate.py`, and their complete-artifact fixtures. Preserve A-158: a normally-started nonzero mutant command is killed; crashed means the command boundary could not execute.
5. `src/assay/config.py`'s closed `MutationConfig` parsing and `JudgeConfig.as_declared`. No runtime consumer may invent a missing cap.
6. P16's migration/conformance tests and P19's model/raw-verifier correspondence tests. Extend both independent layers; do not make `assay verify` import the producer model as its oracle.

## Work

1. Bump the verdict artifact to schema v4 in one atomic migration. Convert every hand-written expected artifact and installed-wheel witness deliberately. `assay verify` must return one version-only diagnostic for v1–v3 before reading foreign fields; it never upgrades, defaults, or rewrites them.
2. Put the mutation-operator vocabulary in one cycle-safe module imported by config, mutation construction, verdict model, and raw verifier. Close both `MutationOutcome.operator` and `judgment.r2.operators` in the model and schema. Delete the current model/schema/verifier mismatch rather than maintaining parallel literal sets.
3. Replace killed's count-only representation with ordered `MutantOutcome` identities, matching survived/crashed/budget-exceeded. Retain or derive a count only if it is mechanically cross-checked. Verify total equals the four bucket lengths and every payload operator belongs to the recorded policy. Sorting is stable by path/line/operator/description and independent of completion order.
4. Make `judge.mutation.max_mutants` a required positive integer, record it in `judgment.r2`, and enforce it after bounded candidate discovery but before any mutant command is submitted. Discover at most `max_mutants + 1`; excess renders `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED`, with no partial sample and no credit. `jobs` remains only a concurrency bound and is never derived from machine capacity.
5. Add the project-relative canary target to `CanaryResult` and bind it exactly to `judgment.r3.target` in both construction and raw verification. The description remains explanation, never a parseable identity channel.
6. Preserve A-008 in the artifact with a closed R1 exclusion-capability field (`reported` versus `unavailable`). `unavailable` may not carry excluded lines; `reported` may truthfully carry an empty mapping. Re-derive the same rule in `verify.py`; do not infer capability from a particular format name.
7. Add construction/schema/raw-verifier checks that `ended >= started`. Use injected/fixed clocks in tests and exact timestamp values; no elapsed-time assertion.
8. Close A-O14 with `ERROR/OUTPUT_WRITE_FAILED`. Validate and reserve the declared output destination before the command executes; a bad/missing/unwritable parent must not allow the lane to run. Do not redirect to an invented fallback path. If a destination becomes unusable after reservation, emit the stable error to stderr, clean internal temporary state where safe, and never claim the requested file was written.
9. Hand-author valid and adversarial v4 artifacts for all levels. Break killed identity, operator vocabulary, max-mutant enforcement, canary target, exclusion capability, interval ordering, version handling, and output preflight independently; record exact A-067 failure counts.

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

**A. No speed-dependent verdicts.** Cardinality and timestamps use exact injected values; output readiness is an I/O fact, never a timing guess.

**B. No order/worker dependence.** Artifact fixtures are immutable per test and mutation arrays are identity-sorted, never completion-sorted.

**C. No hollow tests.** Every new schema field has an independently malformed artifact and a real producer witness; model-only rejection is insufficient.

**D. No coverage evasion.** Maintain 100% statement/branch coverage and mutation-check each model/schema/verifier parity guard.

**E. Control inputs.** Clocks, candidate manifests, output paths, and raw JSON are explicit local inputs; no network or ambient metadata.

## Scope / forbid

This package is the one pre-adoption v4 migration. It must not add Go/TypeScript behavior, change distribution identity, or redesign isolation. P22 consumes the new cap and evidence fields to make repeated execution faithful.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

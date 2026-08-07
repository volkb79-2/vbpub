---
schema_version: 1
id: assay-P19-isolated-r3-cli-pipeline
project: assay
title: "assay run proves a declared canary in an isolated real pipeline"
tier: implement-2
input_revision: "48771e48c7b2ed7ed937cbe07e193718c6f242bb"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P17-r1-cli-pipeline]
session: resume:assay-v11-canary
scope:
  touch: ["src/assay/cli.py", "src/assay/config.py", "src/assay/runner.py", "src/assay/registry.py", "src/assay/canary.py", "tests/**", "README.md"]
  forbid: ["src/assay/verdict.py", "src/assay/schemas", "src/assay/mutation.py", "src/assay/attestation.py", "src/assay/adapters/go.py"]
oracles:
  - id: O1
    observable: "judge.canary has a closed mechanism and project-relative target contract; unknown keys, unknown mechanisms, traversal, and targets outside source roots fail loading"
    negative: "Keeping the table opaque lets an adversarial config reach execution"
    gate: tester-unified
  - id: O2
    observable: "Control and transformed runs occur in a disposable copy through the same installed-wheel pipeline, and the consumer worktree HEAD/index/bytes are unchanged"
    negative: "Calling the current in-place commit path changes the consumer repository fingerprint"
    gate: tester-unified
  - id: O3
    observable: "Import-break and uncovered-line each PASS only for their specific expected reason; a broken control, surviving transform, wrong reason, and no-op transform produce distinct complete R3 artifacts"
    negative: "Accepting any transformed non-PASS result makes the wrong-reason fixture green"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the configured target cannot be copied while preserving the lane's project-relative behavior"
  - "a canary mechanism requires committing to the consumer's live worktree"
mutexes: []
---

# P19 — isolated R3 CLI pipeline

The claim to attack: **the installed CLI proves that one declared defect is rejected for its intended cause without modifying the consumer repository.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P19-isolated-r3-cli-pipeline`
on branch `feat/assay-P19-isolated-r3-cli-pipeline`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§6, 10, 11, and 12; decisions A-010, A-017, A-023–A-024, A-041, A-067, A-084, A-105–A-109.
2. P16's frozen `judgment.r3` and canary payload contract and P17's installed-wheel orchestration/timing. Populate them without a schema redesign.
3. `src/assay/canary.py` in full and `tests/test_canary_python_pipeline.py`; the existing function's in-place commit behavior is fixture-only prior art, not safe consumer orchestration.
4. `src/assay/adapters/python.py` canary injectors and their direct semantic tests. Preserve pure text transforms and their mechanism-specific expected reasons.
5. `src/assay/config.py`'s opaque `judge.canary`; this package replaces opacity with exactly `mechanism` and `target`, no defaults and no plural mechanism list.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` findings 11 and its P19 carve.
7. `/workspaces/vbpub/nyxloom/src/nyxloom/gate_canary.py` for insertion precedent only; never port its live-tree write behavior.

## Work

1. Parse `judge.canary` as a closed table with required `mechanism` and `target`. Accept exactly `import-break` and `uncovered-line`; require a normalized project-relative regular source path beneath one declared source root; reject traversal, symlink escape, test paths, duplicates, and unknown keys.
2. Define one R3 claim as one mechanism execution. Do not accept a plural list whose multiple results would be collapsed into the single schema-v3 canary payload.
3. Copy the clean consumer project into independently-owned scratch state before the control run. Build the control commit there, apply and commit the transform there, and run both halves through the same R0/R1 pipeline. Never stage, commit, or write in the consumer worktree.
4. Preserve cause sensitivity: import-break expects `COMMAND_FAILED`; uncovered-line expects `UNCOVERED_LINES`. A broken control is inconclusive; unknown/no-op is inconclusive; transformed PASS or any different adverse cause is `CANARY_SURVIVED`.
5. Reuse P17's effective argv/environment/base and installed adapter. Do not silently change the command for the transformed target. Set verdict end after both canary halves.
6. Populate P16's R3 policy, advance only Python's registry capability through R3, and append exactly one R3 claim. Create complete hand-written installed-wheel artifacts for PASS, broken control, survivor, wrong cause, no-op, and malformed configuration.
7. Fingerprint consumer HEAD, index, tracked/untracked paths, and file bytes before and after every terminal case. The fingerprint is the restoration oracle; a call ledger alone is insufficient.
8. Break config closure, target containment, scratch isolation, shared-pipeline use, expected-reason comparison, control gating, and final timing separately; run the real gate and record exact A-067 counts.

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

**A. No speed-dependent verdicts.** Process completion and explicit outcomes decide; no sleep, elapsed bound, or iteration-count assertion.

**B. No order/worker dependence.** Every control/transform pair owns one fresh scratch repository and restores no process-global state.

**C. No hollow tests.** Assert exact cause, complete artifact, and unchanged consumer fingerprint; any-nonzero acceptance is forbidden.

**D. No coverage evasion.** Maintain 100% statement/branch and record a real failure count for each controlled canary break.

**E. Control inputs.** Offline disposable repositories, injected clock/process boundaries, and no consumer-tree writes.

## Scope / forbid

This package wires Python R3 only. It must not alter the v3 schema, mutation/evidence, or Go canary behavior. Go's genuine R3 proof belongs to P24.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

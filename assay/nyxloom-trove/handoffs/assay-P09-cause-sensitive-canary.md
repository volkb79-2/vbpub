---
schema_version: 1
id: assay-P09-cause-sensitive-canary
project: assay
title: "A cause-sensitive canary proves the gate can reject"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P08-go-adapter-boundary-proof, assay-P04-runner-cli-verdict-emission]
session: fresh
scope:
  touch: ["src/assay/canary.py", "src/assay/config.py", "src/assay/adapters/base.py", "src/assay/adapters/python.py", "src/assay/adapters/go.py", "src/assay/runner.py", "src/assay/verdict.py", "src/assay/schemas/**", "tests/fixtures/canary/**", "tests/**"]
  forbid: ["src/assay/errors.py"]
oracles:
  - id: O1
    observable: "For committed Python and Go cases, an unmodified known-good control passes and one configured valid known-bad transform fails for the expected changed-line reason"
    negative: "A universal-PASS evaluator makes the bad half pass; a broken baseline makes the good half fail; accepting any non-zero as success lets an unrelated syntax/config error satisfy the oracle"
    gate: tester-unified
  - id: O2
    observable: "The canary result records control outcome, transformed outcome, transform identity, and expected versus observed reason in an independently written schema-valid artifact"
    negative: "Recording only 'rejected=true' lets the wrong failure cause pass and differs from the expected artifact"
    gate: tester-unified
  - id: O3
    observable: "A malformed transform, a transform that changes no target, and a bad case that unexpectedly passes each render an explicit non-PASS canary result"
    negative: "Treating transform failure or no-op as a successful rejection makes its negative-control artifact PASS"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a canary needs a real language toolchain rather than committed text artifacts"
  - "cause identity cannot be represented without weakening the closed verdict schema"
mutexes: []
---

# P09 — cause-sensitive canary

The claim to attack: **the complete gate demonstrably rejects valid known-bad input for the intended reason.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P09-cause-sensitive-canary`
on branch `feat/assay-P09-cause-sensitive-canary`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §8 and decisions A-029–A-031, A-039, A-041, A-042.
2. The runner, evaluator, Python/Go adapters, verdict model, and existing independent artifact fixtures.
3. Canary behavior in `/workspaces/vbpub/shared-ramdisk-depot-manager/tools/covergate/`.

## Work

1. Add canary injection to the language adapters that own syntax, then implement configured pure-text transforms with a mandatory known-good control.
2. Require rejection for the configured expected reason, not any failure.
3. Add an additive canary payload and complete hand-written expected artifacts for every terminal path.
4. Break universal-pass rejection, control validity, no-op detection, and cause matching; record failure counts (A-067).

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

**A. No speed-dependent verdicts.** Canary transforms and committed artifacts are deterministic; no timing.

**B. No test-order/worker dependence.** Restore registry/global state and isolate files.

**C. No hollow tests.** A non-zero exit alone is not proof: assert the good control, bad result, and exact cause in full artifacts.

**D. No coverage evasion.** No exclusion pragmas on implementation changes.

**E. Control inputs.** No network, Go toolchain, or ambient coverage; use committed text and tmp_path.

## Scope / forbid

Construction and execution remain together because the one claim requires the same control/bad pair. This package does not mutate arbitrary project code.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

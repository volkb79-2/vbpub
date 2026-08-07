---
schema_version: 1
id: assay-P12-bounded-mutation-execution
project: assay
title: "Mutation execution is baseline-gated, isolated, and mechanically bounded"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P11-valid-mutant-construction, assay-P04-runner-cli-verdict-emission]
session: resume:assay-runner
scope:
  touch: ["src/assay/config.py", "src/assay/mutation.py", "src/assay/runner.py", "src/assay/verdict.py", "src/assay/schemas/**", "tests/**"]
  forbid: ["src/assay/errors.py"]
oracles:
  - id: O1
    observable: "A passing unmodified baseline is mandatory; baseline failure, crash, or timeout stops before any mutant and renders the corresponding non-PASS reason in a complete expected artifact"
    negative: "Deleting the baseline guard submits mutant work and can award credit when the original suite is already red"
    gate: tester-unified
  - id: O2
    observable: "An injected executor factory receives max_workers equal to configured jobs and every mutant is submitted through that executor; jobs=1 and jobs=3 produce identical ordered result records"
    negative: "Constructing the executor with mutant count or bypassing it for one task fails the recorded bound/submission assertions without any wall-clock measurement"
    gate: tester-unified
  - id: O3
    observable: "Each mutant runs against isolated source state, restoration is byte-exact after killed, survived, crashed, and budget-stopped results, and deterministic input order yields deterministic output order"
    negative: "In-place shared mutation contaminates a later fake run or leaves the source hash changed; completion-order output changes the expected list"
    gate: tester-unified
  - id: O4
    observable: "Killed, survived, error, and budget-exhausted mutation sets each emit independently written schema-valid R2 artifacts with all attempted and unattempted identities accounted for"
    negative: "Dropping unattempted identities, treating crashes as killed, or universal PASS differs from the complete expected artifact"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the jobs bound cannot be observed through the actual executor boundary without elapsed-time assertions"
  - "source restoration requires editing adapter contracts"
mutexes: []
---

# P12 — bounded mutation execution

The claim to attack: **tests kill valid changed-line mutants under a declared, deterministic execution bound.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P12-bounded-mutation-execution`
on branch `feat/assay-P12-bounded-mutation-execution`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` mutation execution/budget section and decisions A-003, A-004, A-020–A-024, A-041.
2. P11 mutation manifest, runner/process boundary, verdict model and independent artifact tests.
3. Mutation execution in the srdm Go reference; take behavior, not Go-specific structure.

## Work

1. Require and record a clean baseline before generating/submitting mutants.
2. Run mutants in isolated state through an injectable executor constructed with exactly `jobs`; never test concurrency by elapsed time.
3. Restore bytes on every terminal path and serialize results deterministically.
4. Add the closed R2 payload/schema branch and complete hand-written artifacts for all terminal result classes.
5. Break baseline gating, executor bound, restoration, ordering, and result accounting; record failure counts (A-067).

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

**A. Nothing may make the verdict depend on machine speed.** Do not assert elapsed time, sleep, or use a deadline as an oracle. Prove the bound at the executor constructor/submission boundary; timeouts only prevent hangs.

**B. Nothing may depend on order or workers.** Isolate every source tree, restore global state, and sort by stable identity rather than completion order.

**C. No hollow tests.** A serial/parallel equality check alone does not prove `jobs`; assert the actual executor bound and complete accounting.

**D. No coverage evasion.** No changed-line exclusions.

**E. Control network, clock, and filesystem.** Fake the child runner and executor; use tmp_path and deterministic events, no network.

## Scope / forbid

Mutant construction and adapter capability are frozen P11 inputs. This package owns execution and the R2 producer only.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

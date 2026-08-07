---
schema_version: 1
id: assay-P18-r2-cli-pipeline
project: assay
title: "assay run constructs and executes exactly the declared changed-line mutants"
tier: implement-2
input_revision: "48771e48c7b2ed7ed937cbe07e193718c6f242bb"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P17-r1-cli-pipeline]
session: resume:assay-v11-mutation
scope:
  touch: ["src/assay/cli.py", "src/assay/config.py", "src/assay/runner.py", "src/assay/registry.py", "src/assay/mutation.py", "tests/**", "README.md"]
  forbid: ["src/assay/verdict.py", "src/assay/schemas", "src/assay/canary.py", "src/assay/attestation.py", "src/assay/adapters/go.py"]
oracles:
  - id: O1
    observable: "R2 builds targets only from considered changed non-test source lines in the same resolved diff used by R1 and filters mutants by the exact declared operator set"
    negative: "Mutating an unchanged line, test file, excluded file, or undeclared operator changes the complete expected mutant manifest"
    gate: tester-unified
  - id: O2
    observable: "jobs is a required positive integer and is observed at the executor-construction boundary without elapsed-time assertions"
    negative: "jobs=0, a machine-derived worker count, or a constructor receiving mutant count fails mechanically"
    gate: tester-unified
  - id: O3
    observable: "The successful R0 result from the CLI is reused as the mutation baseline; the original command is not rerun before mutant submissions"
    negative: "Calling run_mutation's old baseline path increments the command ledger twice"
    gate: tester-unified
  - id: O4
    observable: "An installed-wheel R2 fixture produces exact killed, survived, crashed, budget-exceeded, no-mutants, and baseline-adverse artifacts while shared source bytes remain unchanged"
    negative: "Universal killed, omitted unattempted identities, or live-tree mutation differs from a hand-written artifact or source hash"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "operator selection requires whole-file source rewriting"
  - "the jobs bound can only be tested with wall-clock timing"
mutexes: []
---

# P18 — R2 CLI pipeline

The claim to attack: **the installed CLI mutates exactly the declared changed-line sites under the declared deterministic execution bound.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P18-r2-cli-pipeline`
on branch `feat/assay-P18-r2-cli-pipeline`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§6, 10, 11, and 12; decisions A-003–A-004, A-020–A-024, A-041, A-067, A-082, A-112–A-122.
2. P16's frozen schema-v3 `judgment.r2` shape and P17's one-command/one-diff CLI orchestration. This package populates those contracts; it does not redesign them.
3. `src/assay/mutation.py` in full, especially `MutationTarget`, `collect_mutants`, `run_mutation`, and `build_mutation_claim`; read all `tests/test_mutation_*` files named by those functions.
4. `src/assay/adapters/python.py::generate_mutants` and `MUTATION_OPERATORS`; operator filtering must select already-valid single-site mutants, never rewrite their source.
5. `src/assay/runner.py::evaluate_r1` and P17's resolved-diff representation. R2 target selection must consume the same measurement, not invoke Git independently with a second base.
6. `/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py` for executor/order prior art only; do not port its CPU-derived worker default, no-timeout subprocesses, or any-nonzero-is-killed behavior (A-122).
7. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` finding 11 and the P18 carve.

## Work

1. Replace opaque mutation configuration with a closed table containing required positive integer `jobs` and a non-empty duplicate-free ordered `operators` list. Reject unknown keys and operators; never derive jobs from hardware.
2. Build `MutationTarget`s from the same resolved added-line object used by R1. Include only considered adapter source files, exclude tests and excluded directories, read exact current bytes, and preserve deterministic path order.
3. Collect the adapter's valid mutants and retain only declared operators. Distinguish adapter `UNSUPPORTED`, valid source with no selected sites, and an operator filter selecting none; all remain honest non-PASS outcomes.
4. Refactor mutation execution so P17's already-obtained `CommandResult` is the mandatory baseline input. Do not rerun the unmodified lane. A non-PASS baseline submits no mutant and propagates its exact outcome/reason.
5. Preserve copy-per-mutant isolation, exact lane argv, deterministic result ordering, four terminal buckets, and the executor factory constructed with exactly configured jobs. Validate `jobs` before the executor boundary.
6. Populate P16's effective R2 policy, advance only Python's registry capability through R2, and append exactly one R2 claim through the installed CLI. Final verdict time encloses all mutation work.
7. Add a real installed-wheel Python fixture whose independently enumerated changed-line mutants include killed and survived cases plus controlled crash/budget paths. Compare complete artifacts and shared-tree hashes.
8. Break target scoping, operator filtering, jobs validation/boundary, baseline reuse, isolation, ordering, and result accounting separately; run the real gate and record exact A-067 counts.

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

**A. No speed-dependent verdicts.** Observe jobs at executor construction/submission; never infer concurrency from elapsed time.

**B. No order/worker dependence.** Each mutant owns a fresh copy; output ordering follows mutant identity, not completion order.

**C. No hollow tests.** Independently enumerate mutants and complete results; call counts supplement but never replace artifact/source assertions.

**D. No coverage evasion.** Maintain 100% statement/branch and record controlled failure counts for every changed property.

**E. Control inputs.** Use injected executor/process/clock boundaries and disposable projects; no live source writes or network.

## Scope / forbid

This package closes P12's two deliberate R2 wiring gaps for Python. It must not alter v3 payload/schema, add Go mutation, interpret canary/evidence, or reshape adapter methods.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

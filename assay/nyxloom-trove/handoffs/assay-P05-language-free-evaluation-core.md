---
schema_version: 1
id: assay-P05-language-free-evaluation-core
project: assay
title: "Four-way changed-line evaluation behind a language-free adapter boundary"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P02-changed-lines-measurability, assay-P03-coverage-formats-registry, assay-P04-runner-cli-verdict-emission]
session: fresh
scope:
  touch: ["src/assay/adapters/base.py", "src/assay/evaluate.py", "src/assay/registry.py", "src/assay/runner.py", "src/assay/verdict.py", "src/assay/schemas/**", "tests/**"]
  forbid: ["src/assay/config.py"]
oracles:
  - id: O1
    observable: "A fake adapter drives the four-way union exactly: changed executable lines require execution; changed excluded lines fail; changed non-executable lines pass; uncovered executable lines outside the diff do not affect the result"
    negative: "Dropping any union member makes its literal set fixture change from FAIL to PASS or vice versa"
    gate: tester-unified
  - id: O2
    observable: "The same fake adapter with a non-Python extension and synthetic syntax reaches the same result, while an unknown declared language is rejected by the explicit registry"
    negative: "Adding a .py filter, AST import, or default adapter makes the synthetic-language fixture disappear or the unknown language silently select Python"
    gate: tester-unified
  - id: O3
    observable: "Runner integration emits a schema-valid computed R1 claim with exact totals, percentage, missing locations, and reason; a hand-written expected artifact covers PASS and FAIL"
    negative: "A universal PASS evaluator, rounded threshold comparison, or producer/schema drift fails an independent full-artifact comparison"
    gate: tester-unified
  - id: O4
    observable: "Before the four-way union runs, a dirty tree under a source root renders NO_MEASUREMENT/DIRTY_TREE, base==HEAD renders NO_MEASUREMENT/BASE_IS_HEAD, and a zero-file coverage artifact renders NO_MEASUREMENT/EMPTY_COVERAGE, each matching a hand-written expected artifact with the coverage block omitted, not zeroed (A-025)"
    negative: "Evaluating anyway and only overwriting the outcome afterward produces a different reason_code or a present-but-zeroed coverage block, which fails the expected-artifact comparison"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the adapter protocol needs a language-specific field"
  - "the closed verdict schema cannot express the R1 payload additively"
mutexes: []
---

# P05 — language-free evaluation core

The claim to attack: **all four changed-line sets are judged without the core knowing a source language.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P05-language-free-evaluation-core`
on branch `feat/assay-P05-language-free-evaluation-core`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§4–5, §6's NO_MEASUREMENT table, and decisions A-012–A-018, A-024, A-025, A-035, A-071, A-090, A-094.
2. `src/assay/diff.py`, `src/assay/measurability.py`, `coverage.py`, `runner.py`, `verdict.py`, and packaged schema.
3. The evaluation functions in all four source implementations named by the design guide, especially topos directory-boundary matching and nyxloom/dstdns exclusion policy.

## Work

1. Define the smallest adapter protocol needed for executable and excluded line classification and an explicit language registry.
2. Implement the pure four-way set evaluation and threshold result.
3. Integrate an additive R1 computed claim and closed schema branch into the runner, calling P02's measurability guards and P03's empty-coverage guard first and short-circuiting evaluation on any of the three (O4, A-090).
4. Test with a fake non-Python adapter before any real adapter exists. Add independently written full verdict fixtures for new producer paths, including the three NO_MEASUREMENT branches.
5. Break each set term, the language boundary, the three guards, and producer/schema agreement; record failure counts (A-067).

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

**A. Nothing may make the verdict depend on machine speed.** No timing or sleeps; pure set inputs decide results.

**B. Nothing may depend on test order, worker assignment, or sibling tests.** Restore registries and globals; prefer fresh registry instances.

**C. No hollow tests.** Assert exact sets and full artifacts, not protocol existence, registrations, or call counts.

**D. No coverage evasion.** No changed-line exclusion pragmas or excluded exception bodies.

**E. Network, clock, and filesystem are controlled inputs.** Use literal fixtures and tmp_path; no network or ambient files.

## Scope / forbid

No real language adapter belongs here. A real adapter in this package would make O2 circular.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

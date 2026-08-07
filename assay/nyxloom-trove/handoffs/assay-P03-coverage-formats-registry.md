---
schema_version: 1
id: assay-P03-coverage-formats-registry
project: assay
title: "Coverage formats are explicit, strict, and language-independent"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P01-verdict-model-and-schema]
session: fresh
scope:
  touch: ["src/assay/config.py", "src/assay/coverage.py", "src/assay/coverage_parsers/**", "tests/**"]
  forbid: ["src/assay/verdict.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "Independent fixtures parse coverage.py JSON, lcov, Cobertura XML, and Go coverprofile into the same normalized FileCoverage model, including multiple blocks per line and Windows drive-letter paths"
    negative: "Removing any registered format, binding it to a language, or collapsing multiple blocks changes a literal normalized fixture"
    gate: tester-unified
  - id: O2
    observable: "A malformed record for each of the four formats, unknown format key, and configured format/artifact-signature mismatch each raise a closed typed error; the declaration selects the parser and signature detection only cross-checks it"
    negative: "Ignoring malformed records, removing the independent signature cross-check, or selecting a parser from sniffed content makes at least one reject fixture load"
    gate: tester-unified
  - id: O3
    observable: "The normalized model preserves excluded=None when a format cannot report exclusions and excluded=frozenset() when it reports none"
    negative: "Collapsing None to an empty set makes the paired Go-versus-coverage.py fixture equal and falsely claims exclusion was measured"
    gate: tester-unified
  - id: O4
    observable: "An artifact containing zero measured files renders NO_MEASUREMENT/EMPTY_COVERAGE, while a non-empty artifact whose executed-line sets are empty reaches evaluation"
    negative: "Conflating zero files with zero executed lines either emits a misleading coverage FAIL or hides a legitimate 0% measurement"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a reference format cannot be represented as file-to-line sets"
  - "strict format validation requires a forbidden schema edit"
mutexes: []
---

# P03 — coverage formats registry

The claim to attack: **coverage format is declared data, not language knowledge or artifact sniffing.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P03-coverage-formats-registry`
on branch `feat/assay-P03-coverage-formats-registry`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§5–6 and decisions A-013, A-035, A-042, A-068.
2. `src/assay/config.py` coverage-format debt and `src/assay/errors.py`.
3. The `dstdns` sibling checkout, `scripts/coverage_gate.py`: `load_coverage` and record validation.
4. `/workspaces/vbpub/topos/tools/coverage_gate.py`: `_validate_cov_record`.
5. `/workspaces/vbpub/shared-ramdisk-depot-manager/tools/covergate/`: coverprofile parsing and drive-letter handling.

## Work

1. Define a normalized coverage profile and an explicit parser registry.
2. Implement strict coverage.py JSON, lcov, Cobertura XML, and Go coverprofile parsers from committed literal fixtures; use no Go toolchain.
3. Cross-check `judge.coverage.format` against registry keys at config load (A-068).
4. Distinguish an empty artifact from a measured artifact with empty executed sets.
5. Break each oracle property and record the actual failure count (A-067).

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

**A. Nothing may make the verdict depend on how fast the machine is.** No elapsed-time assertions, short sleeps, or deadlines as oracles. Wait on a real synchronization point; a generous timeout is only a hang failsafe.

**B. Nothing may depend on test order, worker assignment, or a sibling test.** Restore process-global state, patch the owning namespace, and use fresh `tmp_path` state.

**C. No hollow tests.** Assert exact normalized mappings and exact typed failures, not parser registration or call counts. Never weaken assertions.

**D. No coverage evasion.** No changed-line exclusion pragmas or excluded exception bodies.

**E. Network, clock, and filesystem are inputs — control them.** Fixtures are committed text; no network, host coverage files, or Go invocation.

## Scope / forbid

The registry owns formats only. It must not import an adapter or infer a language.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

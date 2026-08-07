---
schema_version: 1
id: assay-P14-self-hosted-conformance
project: assay
title: "Self-hosting is non-circular and verdict conformance is complete"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P13-standalone-wheel-proof]
session: resume:assay-cli
scope:
  touch: ["assay.toml", "nyxloom-trove/nyxloom.toml", "src/assay/cli.py", "src/assay/verify.py", "tests/fixtures/verdicts/**", "tests/test_verdict_conformance.py", "tests/test_self_hosting.py", "README.md"]
  forbid: ["src/assay/verdict.py", "src/assay/errors.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "A hand-written manifest independent of assay's enums has at least one complete artifact for every legal producer outcome/reason pair shipped by P04–P12, and every artifact independently validates against packaged schema v2"
    negative: "Deleting any fixture or changing any producer mapping leaves a named vocabulary pair uncovered or mismatched"
    gate: tester-unified
  - id: O2
    observable: "assay verify accepts every valid independent artifact and rejects malformed/unknown-key/wrong-rollup artifacts, while the independent jsonschema fixture suite remains the primary oracle"
    negative: "A verifier that returns success unconditionally passes its own smoke path but fails the paired invalid artifacts"
    gate: tester-unified
  - id: O3
    observable: "Assay's declared lane runs the built wheel against assay's tests in tester-unified and emits a verdict; a separately invoked independent conformance test validates that artifact and fails after a controlled universal-PASS producer mutation"
    negative: "Using assay verify as the only validator or running from source lets the circular/uninstalled variant pass; universal PASS is caught by the independent fixture expectation"
    gate: tester-unified
  - id: O4
    observable: "nyxloom gate asserts only capabilities mechanically demonstrated by the final suite, retains the verified cgroup helper, and the full project gate passes at 100% statement and branch coverage"
    negative: "Declaring an unimplemented capability or bypassing the cgroup helper fails the static/config assertion; an uncovered branch fails the gate"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a producer outcome cannot be represented by the frozen schema-v2 contract"
  - "self-hosting requires assay to be its sole oracle"
  - "the nyxloom gate would need to run outside tester-unified"
mutexes: [merge-lane]
---

# P14 — self-hosted conformance

The claim to attack: **assay can gate itself without becoming the only witness to its own correctness.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P14-self-hosted-conformance`
on branch `feat/assay-P14-self-hosted-conformance`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§6, 10, 12 and decisions A-040, A-041, A-066, A-067, A-074–A-077, A-123–A-127.
2. All hand-written expected artifacts added incrementally by P04–P12, packaged schema tests, P13 wheel proof, `assay.toml`, and `nyxloom-trove/nyxloom.toml`.
3. `nyxloom/reference/DOCTRINE.md` gate/cockpit and independent-evidence rules.
4. `nyxloom-trove/reports/assay-P13-BRIEF.md` — P13 (merged, in `depends_on`) is directly relevant: (a) `assay_version` reads `"0.0.0"` in the real `tester-unified:local` gate image, not a real semver — `setuptools_scm` is absent from every interpreter there (A-069/A-124, now independently confirmed three separate times), so if your self-hosting proof compares a real emitted artifact against any hand-written fixture, exclude/normalize `assay_version` the same way P13 had to; (b) `assay run <lane> --file <path> --verdict-json -` writes ONLY the verdict JSON to stdout, exit code is `Outcome.exit_code` directly — P13's own brief has a working minimal R0-only lane TOML if you need a worked example; (c) **A-125 still applies to you, not just P13**: `tests/conftest.py` is NOT in YOUR `scope.touch` either (only `tests/fixtures/verdicts/**`, `tests/test_verdict_conformance.py`, `tests/test_self_hosting.py` are) — you cannot extend `collect_ignore_glob`, so if your self-hosting proof needs a real pytest-project-shaped fixture, either reuse an already-`collect_ignore_glob`-excluded one or materialize file content at test time; the likelier shape (per O3's own text) is that you run `assay`'s OWN already-existing test suite via its own already-declared lane, which needs no new committed fixture project at all.

## Work

1. Audit—not generate—the independent expected artifact matrix and add only missing legal producer cases.
2. Add `assay verify` as a secondary consumer with paired valid/invalid artifacts; it must not replace jsonschema/full-object comparisons.
3. Make assay's lane use the built artifact inside tester-unified and retain the declared PATH and verified cgroup-parent contracts.
4. Demonstrate a controlled producer mutation is caught by the independent layer and record the failure count (A-067).

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

**A. No speed-dependent verdicts.** No elapsed-time or sleep oracle; process completion and artifact content decide.

**B. No order/worker dependence.** Every artifact and scratch installation is isolated; restore mutations before gating.

**C. No hollow tests.** Never generate expected artifacts with assay or let assay verify be its own sole oracle. Compare complete hand-written objects.

**D. No coverage evasion.** The full gate remains 100% statement and branch with no changed-line exclusions.

**E. Control inputs.** Offline, tester-unified only; no network or ambient source-tree imports.

## Scope / forbid

The implementation contracts are frozen. A need to change verdict, schema, adapters, or evaluation means an upstream package is incomplete and must be repaired there.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

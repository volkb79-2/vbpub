---
schema_version: 1
id: assay-P13-standalone-wheel-proof
project: assay
title: "The built wheel runs offline without source-tree or dependency leakage"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P09-cause-sensitive-canary, assay-P10-attested-evidence-staleness, assay-P12-bounded-mutation-execution]
session: fresh
scope:
  touch: ["pyproject.toml", "README.md", "tools/standalone-proof.sh", "tests/test_standalone.py", "tests/fixtures/standalone/**"]
  forbid: ["src/assay", "assay.toml", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "Inside tester-unified, a wheel is built offline with --no-build-isolation --no-deps, installed with --no-index into a clean scratch venv from a copied tree lacking .git, and its console command emits the expected R0 artifact"
    negative: "Removing package data, fallback version support, console entry point, or a runtime import makes the scratch invocation/build fail"
    gate: tester-unified
  - id: O2
    observable: "The installed distribution metadata has zero Requires-Dist runtime dependencies and the proof process has neither project PYTHONPATH nor the source checkout on sys.path"
    negative: "Adding requests as a runtime dependency or leaking the source tree makes the metadata/path assertion fail even if host site-packages contains it"
    gate: tester-unified
  - id: O3
    observable: "The installed wheel can load and independently validate its packaged schema v2 and run one Python and one Go fixture using only committed artifacts"
    negative: "Omitting schema/fixtures needed at runtime or importing a host-only module fails in the clean venv"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the gate image lacks the already-recorded offline build prerequisites"
  - "proof requires changing runtime implementation instead of packaging metadata"
mutexes: []
---

# P13 — standalone wheel proof

The claim to attack: **the shipped wheel, not the source checkout, is a zero-runtime-dependency executable.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P13-standalone-wheel-proof`
on branch `feat/assay-P13-standalone-wheel-proof`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` standalone requirements and decisions A-005, A-040, A-056, A-057, A-069, A-070.
2. `pyproject.toml`, packaged schema test, dependency-purity test, and P04 expected R0 fixture.
3. `nyxloom/reference/DOCTRINE.md` gate/cockpit rule. Do not run the proof in the devcontainer environment.

## Work

1. Implement the exact two-environment offline build/install recipe from A-070.
2. Remove `.git` before build to exercise the fallback version and keep install environment clean of build-only PYTHONPATH.
3. Assert installed metadata and sys.path, then run real wheel behavior and schema loading.
4. Break dependency purity, packaged data, fallback version, and source isolation; record failure counts (A-067).

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

**A. No speed-dependent verdicts.** Process completion decides; generous timeout only prevents hangs.

**B. No order/worker dependence.** Each proof owns scratch directories and virtual environment.

**C. No hollow tests.** Successful pip install alone proves nothing; assert metadata, paths, package data, and real emitted behavior.

**D. No coverage evasion.** No exclusions.

**E. Control inputs.** Offline flags are mandatory; no index/network or ambient site-packages.

## Scope / forbid

This package may repair packaging only. If runtime code is needed, the upstream owning package is incomplete and this one must block.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

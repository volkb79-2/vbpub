---
schema_version: 1
id: assay-P10-attested-evidence-staleness
project: assay
title: "Attested evidence is loaded without being laundered"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P02-changed-lines-measurability, assay-P04-runner-cli-verdict-emission]
session: resume:assay-git
scope:
  touch: ["src/assay/attestation.py", "src/assay/config.py", "src/assay/runner.py", "tests/fixtures/attestations/**", "tests/**"]
  forbid: ["src/assay/verdict.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "An attestation at HEAD and one at an ancestor with byte-identical declared reviewed paths both produce attested evidence with verified_by_assay=false and preserve producer, commit, and paths"
    negative: "Requiring exact HEAD rejects the ancestor fixture; marking a loaded review verified fails the expected artifact"
    gate: tester-unified
  - id: O2
    observable: "An ancestor whose reviewed path changed renders NO_MEASUREMENT/STALE_ATTESTATION; a change outside all reviewed paths remains current; a missing artifact renders NO_MEASUREMENT/MISSING_ATTESTATION"
    negative: "Using commit inequality alone stales the outside-path fixture; ignoring path changes passes the changed-path fixture"
    gate: tester-unified
  - id: O3
    observable: "A descendant, unrelated, or malformed attested commit and a missing reviewed path render ERROR/UNREADABLE_ARTIFACT; duplicate declared (source,key) renders ERROR/BAD_LANE_CONFIG; no attestation path can create a computed claim"
    negative: "A plain lexicographic/hash comparison, duplicate collapse, or claims[] insertion makes one reject fixture load or produces the wrong complete artifact"
    gate: tester-unified
  - id: O4
    observable: "Hand-written full artifacts distinguish never declared, declared-but-missing, current, and stale evidence and validate independently against schema v2"
    negative: "Collapsing missing with undeclared or stale with missing makes paired artifacts equal or producer output differ"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the reserved schema-v2 evidence shape is insufficient"
  - "implementation would require an adjudicator registry or policy engine"
mutexes: []
---

# P10 — attested evidence staleness

The claim to attack: **assay records an external review as external and can prove only whether its path-bound evidence is current.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P10-attested-evidence-staleness`
on branch `feat/assay-P10-attested-evidence-staleness`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §6 attestation semantics and decisions A-032–A-034, A-074, A-075, A-078.
2. `src/assay/verdict.py` schema-v2 evidence invariants, `git.py`, runner, and independent fixtures.
3. Do not read or invent an adjudicator integration; Tier 2 is a reserved sibling shape only.

## Work

1. Load the attestation format into the already-reserved `Evidence` shape.
2. Prove equal-or-ancestor with git, then compare only declared reviewed paths across the interval. Do not compare commit hashes as ordering values. **Trap:** `git.run` (P02) raises `AssayError`/`GIT_FAILED` on ANY non-zero exit. Several git ancestry commands use exit code as a boolean or ternary signal, not as a failure indicator — `merge-base --is-ancestor` exits 1 for a genuine "no"; bare `merge-base` exits 1 when there is no common ancestor at all, which is closer to this package's own "unrelated commit" case (O3) than to `GIT_FAILED`. Do not route an ancestry check through `git.run` and assume a raised error always means a git-level failure; decide deliberately which exit codes are data versus which are `ERROR`, and prefer a comparison that stays inside `run`'s existing all-nonzero-is-an-error contract (e.g. compare `merge-base(a, b)` against `a` for "is-ancestor-or-equal", reserving true git-level failure for a malformed/unresolvable ref) over calling `--is-ancestor` through the shared wrapper unmodified.
3. Integrate the external result without changing verdict.py or the schema.
4. Add full independent artifacts for undeclared, missing, current, and stale states.
5. Break ancestry, path scoping, non-laundering, and exact evidence identity; record failure counts (A-067).

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

**A. No speed-dependent verdicts.** Git repos are materialised synchronously in tmp_path; no waits.

**B. No test-order/worker dependence.** Each test owns its repository and environment.

**C. No hollow tests.** Assert exact ancestry/path behavior and complete artifacts, not that a git command was called.

**D. No coverage evasion.** No exclusion pragmas.

**E. Control inputs.** No network or ambient repository; all commits and attestations live in tmp_path.

## Scope / forbid

Schema v2 already reserves both attested and adjudicated siblings. This package loads only attested evidence and may not add a decorative Tier-2 registry.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

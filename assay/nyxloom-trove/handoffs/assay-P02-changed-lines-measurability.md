---
schema_version: 1
id: assay-P02-changed-lines-measurability
project: assay
title: "Changed-line extraction and refusal of unmeasurable diffs"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P01-verdict-model-and-schema]
session: fresh
scope:
  touch: ["src/assay/diff.py", "src/assay/git.py", "src/assay/measurability.py", "tests/**"]
  forbid: ["src/assay/verdict.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "Against literal -U0 fixtures, parse_added_lines returns only new-side added line numbers, including omitted hunk counts and paths with or without a/ and b/ prefixes; pure deletion and +++ /dev/null return no changed lines"
    negative: "Changing the parser to advance on '-' records deleted or deleted-file lines and makes the fixture fail with extra line numbers"
    gate: tester-unified
  - id: O2
    observable: "In tmp_path git repositories, a merge-commit HEAD resolves to its first parent while a normal HEAD resolves to merge-base(base, HEAD), and a forced non-zero git command raises GIT_FAILED"
    negative: "Always using merge-base makes the merge fixture return its fork point; swallowing the command error makes the error fixture reach evaluation"
    gate: tester-unified
  - id: O3
    observable: "Staged, unstaged, and untracked changes under a resolved source root each render NO_MEASUREMENT/DIRTY_TREE with the affected repo-relative path; an identically named sibling prefix outside the root does not"
    negative: "Removing any porcelain category or using string-prefix matching makes its fixture either pass dirty input or reject the sibling directory"
    gate: tester-unified
  - id: O4
    observable: "base == HEAD renders NO_MEASUREMENT/BASE_IS_HEAD before diff parsing, while a clean docs-only commit with an empty source delta passes both measurability guards"
    negative: "Deleting the equality guard produces a vacuous result; treating every empty delta as unmeasurable rejects the docs-only fixture"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the git binary is absent from tester-unified"
  - "a required behavior needs a forbidden verdict or schema edit"
mutexes: []
---

# P02 — changed lines and measurability

The claim to attack: **assay refuses to judge a diff it cannot actually see.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P02-changed-lines-measurability`
on branch `feat/assay-P02-changed-lines-measurability`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §6 and decisions A-025, A-035, A-049, A-073.
2. `src/assay/errors.py` and `src/assay/config.py` (`source_root_paths`).
3. The `dstdns` sibling checkout, `scripts/coverage_gate.py`: `parse_added_lines`, `_resolve_base`, `_dirty_paths_under_sources`, `_check_measurable`.
4. `/workspaces/vbpub/nyxloom/src/nyxloom/coverage_gate.py`: `_dirty_paths_under_source`.
5. `/workspaces/vbpub/topos/tools/coverage_gate.py`: `_rel_to_source`.

## Work

1. Add a thin git subprocess boundary, base resolution, and new-side diff parsing using the union of the cited copies.
2. Add DIRTY_TREE and BASE_IS_HEAD guards before diff evaluation. Keep EMPTY_COVERAGE in P03.
3. Materialise every git-state fixture in `tmp_path`; do not commit a nested repository.
4. For each oracle, break its defended property and record the real failing test count in the brief (A-067).
5. Return frozen `kw_only` dataclasses from `diff.py`/`measurability.py` (e.g. an added-lines-by-file mapping, a guard result), never bare `dict[str, set[int]]` or another primitive shape copied literally from the cited implementations — house style (A-066), and P05 (A-090) consumes these as typed values (A-091).
6. Raise `errors.AssayError` directly with the appropriate `Outcome`/`ReasonCode` pair; do not define a new exception subclass in `git.py`/`measurability.py` — `errors.py` is outside this package's `scope.touch` (A-091).

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

**B. Nothing may depend on test order, worker assignment, or a sibling test.** Restore process-global state, patch the namespace that owns an attribute, and use fresh `tmp_path` state.

**C. No hollow tests.** Do not use `pass`, assert merely that nothing raised, assert implementation trivia, or weaken an assertion. Assert input/state to observable outcome; prove a guarded crash is real.

**D. No coverage evasion.** Do not add changed-line exclusion pragmas or exclude exception bodies. Restructure genuinely unreachable code.

**E. Network, clock, and filesystem are inputs — control them.** No real network or wall clock; inject boundaries and make offline the default.

## Scope / forbid

Only the frontmatter `scope.touch` paths may change. In particular this package returns typed results and does not edit the verdict contract.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

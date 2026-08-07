---
schema_version: 1
id: assay-P08-go-adapter-boundary-proof
project: assay
title: "Go proves the adapter boundary without a Go toolchain"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P07-statement-span-attribution, assay-P03-coverage-formats-registry]
session: resume:assay-adapters
scope:
  touch: ["src/assay/adapters/go.py", "src/assay/registry.py", "tests/fixtures/go/**", "tests/**"]
  forbid: ["src/assay/errors.py", "src/assay/verdict.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "Committed Go source plus pre-generated coverprofiles produce exact path/span mappings; has_executable_code uses a narrow deterministic Go lexer/parser that identifies function bodies and returns the strict true result on malformed or unknown input"
    negative: "Treating coverprofile blocks as source lines changes the expected mapping; regex-only func matching misclassifies comments, strings, declarations, or malformed input in the paired fixtures"
    gate: tester-unified
  - id: O2
    observable: "The same evaluator fixtures run once with Python and once with Go and return equivalent results; an unknown Go syntax region becomes UNCLASSIFIED rather than PASS"
    negative: "A Python special case in core or a Go default-pass branch makes the paired result or ambiguity artifact differ"
    gate: tester-unified
  - id: O3
    observable: "git diff from input revision touches no base protocol, evaluator, verdict, errors, or schema file, and all pre-existing fake/Python adapter tests stay green"
    negative: "Needing a core/protocol/contract reshape produces a forbidden-path diff; weakening Python behavior fails the retained suite"
    gate: tester-unified
  - id: O4
    observable: "A file with a function or method body returns has-code true; package/import/comment/type/const/var-only files and bodyless assembly/cgo declarations return false; unreadable, lexically malformed, or structurally unknown files return true"
    negative: "Returning false on parser uncertainty silently excuses changed code; returning true for every file recreates srdm's 94-line doc.go false failure"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "adding Go requires changing the protocol or evaluation core"
  - "a required fixture cannot be regenerated outside this package and committed as text"
mutexes: []
---

# P08 — Go adapter boundary proof

The claim to attack: **a second language is additive, proving the adapter boundary is real.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P08-go-adapter-boundary-proof`
on branch `feat/assay-P08-go-adapter-boundary-proof`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§5 and 9; decisions A-013, A-042–A-044.
2. `src/assay/adapters/base.py`, Python adapter, evaluator, and P03 Go parser.
3. `/workspaces/vbpub/shared-ramdisk-depot-manager/tools/covergate/` Go source classification and fixture behavior.

## Work

1. Add only the Go adapter and registration. Port the narrow semantic question from srdm's Go parser with a deterministic lexer/parser: strip comments and literals correctly, recognize function bodies, and fail closed (`true`) on uncertainty. Do not invoke an ambient tool.
2. Commit hello-world source and pre-generated coverage text plus regeneration documentation. Do not add or invoke Go in tester-unified.
3. Exercise the existing evaluator and verdict producer unchanged.
4. Break block mapping, strict parser fallback, ambiguity refusal, and cross-language equivalence; record failure counts (A-067).

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

**A. No speed-dependent verdicts.** Text parsing and attribution are pure.

**B. No test-order or worker dependence.** Use fresh registries and tmp_path.

**C. No hollow tests.** Assert exact maps and paired outcomes, not adapter registration or file existence alone.

**D. No coverage evasion.** No changed-line exclusions.

**E. Control inputs.** Committed source/profile text only; no network, Go toolchain, or host caches.

## Scope / forbid

The protocol is frozen after P07. Any required base/core or verdict-contract edit is the package's mechanical BLOCKED condition.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

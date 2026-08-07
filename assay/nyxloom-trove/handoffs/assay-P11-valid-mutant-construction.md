---
schema_version: 1
id: assay-P11-valid-mutant-construction
project: assay
title: "Changed-line mutants are valid, targeted experiments"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P08-go-adapter-boundary-proof]
session: resume:assay-adapters
scope:
  touch: ["src/assay/adapters/base.py", "src/assay/adapters/python.py", "src/assay/adapters/go.py", "src/assay/mutation.py", "tests/fixtures/mutation/**", "tests/**"]
  forbid: ["src/assay/errors.py", "src/assay/verdict.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "For literal Python fixtures, each generated mutant changes exactly one eligible changed-line construct, preserves every preceding newline and all unrelated bytes, and has a stable identity"
    negative: "Whole-file rewriting, line insertion/deletion, or changing a second construct fails byte-offset and one-diff assertions"
    gate: tester-unified
  - id: O2
    observable: "Every generated Python mutant parses with ast.parse; the Go adapter and unsupported Python constructs return the explicit UNSUPPORTED result, never a text-guessed mutant"
    negative: "Text substitution that creates invalid syntax or replacing Go UNSUPPORTED with an empty-success result fails the validity/expected-manifest fixture"
    gate: tester-unified
  - id: O3
    observable: "The generator targets only executable changed statements and never excluded, non-executable, outside-diff, or UNCLASSIFIED locations"
    negative: "Removing any eligibility filter adds a mutant identity absent from the independent expected manifest"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "valid mutation requires a Go binary"
  - "a capability cannot be added without changing evaluator or verdict files"
mutexes: []
---

# P11 — valid mutant construction

The claim to attack: **each mutant is a valid single changed-line experiment, not arbitrary broken text.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P11-valid-mutant-construction`
on branch `feat/assay-P11-valid-mutant-construction`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` mutation construction section and decisions A-003, A-004, A-015, A-042, A-112, A-113, A-114, A-115.
2. Settled adapter protocol (`adapters/base.py` — currently 5 attributes, 6 methods; `generate_mutants` is the final, reserved 7th, per its own docstring) plus Python and Go adapters; do not edit evaluation or verdict production.
3. **Corrected (A-112):** the real mutation-logic prior art is `/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py` (689 lines), NOT `shared-ramdisk-depot-manager/tools/covergate/` (verified: zero "mutat*" occurrences there — that directory is P08's own coverage-gate/`hascode` prior art, unrelated). Reuse ONLY its catalogue (`compare-swap`/`boolop-swap`/`bool-const-flip`/`falsy-swap`) and its deterministic `(lineno, operator, description)` site-discovery/ordering logic. Do **not** port its substitution mechanism: it builds `Mutant.mutated_source` via `ast.NodeTransformer` + `ast.unparse(tree_copy)`, a whole-file reprint that drops original whitespace/comments/quote-style — exactly the "whole-file rewriting" shape this package's own O1 negative is written to catch. This package's `generate_mutants` must perform a byte-exact single-site splice on the original text instead.
4. `nyxloom-trove/reports/assay-P09-BRIEF.md` for what's genuinely reusable from `python.py`'s existing `inject_import_break`/`inject_uncovered_line` (the pure `(text) -> (text, description)` shape). Go's `_scan_signature_for_body` anchor named there is almost certainly NOT needed here — Work item 1 already settles Go's `generate_mutants` as unconditionally `UNSUPPORTED` (no Go toolchain, ever, A-042), so there is no Go mutation engine to anchor inside a function body.

## Work

1. Add capability-specific mutation hooks only where syntax ownership lives; keep the common generator language-free. Python implements them; Go returns the established UNSUPPORTED result because this suite cannot prove Go syntax validity without the declared external tool.
2. Produce stable mutant identities and explicit ineligible/ambiguous reasons.
3. Assert exact independent manifests, byte preservation, and language validity without invoking Go.
4. Break one-at-a-time, validity, and eligibility properties; record failure counts (A-067).

**Return contract, pinned (A-114) — read before writing any code.**
`generate_mutants(text: str, lines: set[int]) -> tuple[Mutant, ...] | Literal["UNSUPPORTED"]`,
a whole-adapter-call union exactly per DESIGN-GUIDE §11's sketch — never a
per-construct union. `GoAdapter` returns `UNSUPPORTED` unconditionally.
`PythonAdapter` returns `UNSUPPORTED` only when `text` fails `ast.parse`
(mirrors `statement_spans`'s existing `SyntaxError`/`ValueError` -> `None`
precedent) — an individual unsupported construct in an otherwise-parseable
file simply contributes zero mutants for that site, never an early abort.
New frozen `kw_only` `Mutant` dataclass in `mutation.py` (`verdict.py` is
forbidden): at minimum `lineno: int`, `operator: str` (closed-set validated
in `__post_init__` against the four-value catalogue), `description: str`,
`mutated_text: str` (full text, exactly one construct changed), plus a
derived `identity` property (mirroring `EvidenceDeclaration.identity`) for
O1's stable-identity requirement. "Explicit ineligible/ambiguous reasons"
(Work item 2) is a TEST-layer property, not a public return value:
`generate_mutants` never emits an entry for an excluded site; O3's
independent expected manifest is a hand-written fixture of exact expected
`Mutant` identities (A-041/A-080) — a removed eligibility filter is caught
because it adds an identity absent from that fixture. If you want direct
unit coverage of eligibility logic itself, test a private per-site
classifier function directly (P10's `_check_ancestor_or_equal` precedent),
not a new public type.

**`boolop-swap` chain trap (A-115).** `a and b and c` parses as ONE
`BoolOp(op=And(), values=[a, b, c])` node, but the chain is TWO textual
`and` occurrences. A chain of N operands has N-1 independently-targetable
sites — flip exactly one operator token per `Mutant`, never reassign the
shared `.op` field wholesale (that would conceptually flip every occurrence
in the chain at once, which nyxloom's own reference only gets away with
because it reprints the whole file). For `compare-swap`: `ast.cmpop` nodes
(`Lt`, `Eq`, ...) carry no `lineno`/`col_offset` of their own — derive the
operator's byte span from the gap between the left operand's
`end_col_offset` and the first comparator's `col_offset`, then locate the
exact operator substring within that gap.

**Scope confirmed clean.** `verdict.py` and `errors.py` (both forbidden) are
not needed: none of O1-O3 attaches anything to a `Claim`/`Verdict`, and no
new `ReasonCode` is required (`MUTANTS_SURVIVED`/`NO_MUTANTS` already exist
but are P12's — execution-time — concern, not this package's). `mutation.py`
+ the two adapter files + fixtures/tests are sufficient.

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

**A. No speed-dependent verdicts.** Construction is pure; no timing.

**B. No order/worker dependence.** Fresh adapters and files per test.

**C. No hollow tests.** File existence or mutant count alone is insufficient; assert exact bytes, identity, validity, and manifest.

**D. No coverage evasion.** No exclusion pragmas.

**E. Control inputs.** Literal committed sources only; no network or Go toolchain.

## Scope / forbid

Execution and job scheduling belong to P12. This package may construct experiments but cannot claim that tests kill them.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

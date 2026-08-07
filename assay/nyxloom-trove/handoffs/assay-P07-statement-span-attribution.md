---
schema_version: 1
id: assay-P07-statement-span-attribution
project: assay
title: "Statement spans attribute safely or report ambiguity"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P06-python-adapter-union-fidelity]
session: resume:assay-adapters
scope:
  touch: ["src/assay/adapters/base.py", "src/assay/adapters/python.py", "src/assay/evaluate.py", "src/assay/verdict.py", "src/assay/schemas/**", "tests/**"]
  forbid: ["src/assay/config.py"]
oracles:
  - id: O1
    observable: "A changed continuation line in an executed multiline Python statement is attributed to that statement and passes, provably (covered/changed_executable/missing_lines counts reflect the attribution, not merely the final PASS outcome); the same span unexecuted fails with the statement start named"
    negative: "Line-number-only evaluation fails the executed continuation or reports the continuation itself as an executable start"
    gate: tester-unified
  - id: O2
    observable: "Overlapping (a defensive guard against an adapter's own internally-inconsistent analysis, fed directly to the pure attribution function as hand-built spans — not derived from real Python, which never produces genuine overlap under correct nesting), malformed, or genuinely unattributable spans (a changed interior line whose enclosing span is itself untracked) render FAIL/UNCLASSIFIED_LINES with exact locations (A-100); no ambiguity becomes PASS"
    negative: "Choosing the first overlap or dropping unattributed lines turns at least one ambiguity fixture green"
    gate: tester-unified
  - id: O3
    observable: "Runner output for attributed PASS, attributed FAIL/UNCOVERED_LINES, and FAIL/UNCLASSIFIED_LINES matches independently written complete schema-valid artifacts"
    negative: "A producer that omits unclassified locations or rolls them up as PASS differs from its expected artifact"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the span protocol cannot represent a reference implementation behavior"
mutexes: []
---

# P07 — statement-span attribution

The claim to attack: **a changed line is never passed merely because its executable statement spans multiple lines.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P07-statement-span-attribution`
on branch `feat/assay-P07-statement-span-attribution`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §5 statement attribution, §11's `LanguageAdapter` protocol, and decisions A-016, A-017, A-024, A-071, A-092, A-096, A-097, A-098, A-099, A-100, A-101.
2. P05 evaluator and schema tests; P06 Python adapter (especially `_is_bare_string_statement`, private — decide whether to share it or re-derive your own, per `assay-P06-BRIEF.md`) and exact-set fixtures.
3. Statement-span logic in the `dstdns` sibling checkout's `scripts/coverage_gate.py` (the real union — its `unclassified` bucket, its `_record_unclassified` mechanism, and its own unconditional-FAIL treatment, A-100). `nyxloom/src/nyxloom/coverage_gate.py` has no independent statement-span logic (grep confirms zero hits for `statement_span`/`unclassified`/`multiline`) — it contributes nothing to this union (consistent with DESIGN-GUIDE's own "now a strict subset of dstdns" characterization); do not search it for a mechanism that isn't there.

## Work

1. Add `statement_spans` to the adapter protocol (`adapters/base.py`) and the Python adapter. Exact shape (A-101): `statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None`, where `StatementSpan` is a new frozen `kw_only` dataclass (`start_line: int, end_line: int`) — never dstdns's bare `list[tuple[int, int]]`. `evaluate.py` calls this only for an adapter whose `requires_span_attribution` is `True` (`FakeAdapter`'s is `False` — no call, no implementation needed there).
2. Keep attribution pure; return explicit unclassified locations rather than guessing. Overlapping/malformed spans and genuinely unattributable lines all render FAIL/UNCLASSIFIED_LINES (A-100) — not a new outcome/reason-code pairing.
3. Extend the R1 payload and schema additively (matching A-096's `missing_lines`/`files_missing_coverage` pattern) and add independent expected artifacts for every new terminal path.
4. Break attribution, overlap refusal, and rollup; record failure counts (A-067).

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

**A. No speed-dependent verdicts.** Attribution is pure; no timing.

**B. No order/worker dependence.** Restore globals and registries.

**C. No hollow tests.** Assert exact attributed statements, locations, outcomes, and full artifacts.

**D. No coverage evasion.** No exclusion pragmas on implementation changes.

**E. Control all inputs.** Literal source and coverage fixtures only; no network or ambient files.

## Scope / forbid

This is the one deliberate post-P05 protocol extension: spans are a distinct ambiguity claim and must land before the second adapter freezes the boundary.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

---
schema_version: 1
id: assay-P06-python-adapter-union-fidelity
project: assay
title: "Python adapter faithfully unifies the four shipped gates"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P05-language-free-evaluation-core]
session: resume:assay-adapters
scope:
  touch: ["src/assay/adapters/python.py", "src/assay/registry.py", "tests/**"]
  forbid: ["src/assay/verdict.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "Committed Python snippets independently enumerate executable and excluded lines for decorators, async/compound statements, docstrings, comments, and pragma tokens, each classification resolved from a single reported line; the adapter returns the exact expected sets"
    negative: "Replacing AST/token classification with nonblank-line counting or omitting one construct changes a literal expected set"
    gate: tester-unified
  - id: O2
    observable: "normalize_coverage_key reconciles a coverage artifact's own path spelling to the diff's spelling using a boundary-safe strip (never a naive removeprefix/startswith that would mis-strip a similarly-prefixed sibling), proven with a fixture where the two spellings genuinely differ"
    negative: "A naive string-prefix strip mis-normalizes the sibling-prefixed fixture to the wrong repo-relative path"
    gate: tester-unified
  - id: O3
    observable: "The package diff changes no evaluation, base protocol, verdict, or schema file, and the full P05 fake-adapter suite remains green"
    negative: "Teaching the core a Python special case produces a forbidden-path diff or breaks the synthetic adapter suite"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "faithful Python classification requires changing the settled adapter protocol"
  - "the three Python references disagree in a way not resolved by decisions.md"
mutexes: []
---

# P06 — Python adapter union fidelity

The claim to attack: **the first real adapter supplies the Python union without changing the language-free core.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P06-python-adapter-union-fidelity`
on branch `feat/assay-P06-python-adapter-union-fidelity`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§4–5 and decisions A-014–A-018, A-084, A-097, A-098, A-099, A-C01–A-C03.
2. `src/assay/adapters/base.py`, `evaluate.py`, and the P05 fake-adapter tests. `nyxloom-trove/reports/assay-P05-BRIEF.md` in full — it fixes the exact protocol shape (do not add a ninth member), the path-spelling contract every method receives, and notes `evaluate_r1`'s `has_executable_code`-driven `ast.parse` guidance (catch `SyntaxError`, treat as `True` — fail closed, never a silent excuse).
3. Python classification and exclusion logic in all three Python reference gates: dstdns, topos, and nyxloom. dstdns is the sole holder of both `_is_test_path` (adopt as the union — topos/nyxloom have no equivalent) and the AST-based `statement_spans`/decorator/match-case recovery mechanism (A-098: reserved for P07, not yours to replicate).

## Work

1. Implement the Python adapter against the settled protocol and register it explicitly.
2. Take the union of reference behaviors for single-line-reported classification (A-098), including the resolved exclusion policy. Multi-line statement interior-line attribution is explicitly out of scope — P07's job.
3. Use committed literal snippets and independent expected line sets. For O2, construct a fixture where the coverage artifact's own path spelling and the diff's spelling genuinely differ (e.g. a project layout where `coverage run`'s own cwd differs from the repo top), not a same-spelling no-op case.
4. Break each classification family and `normalize_coverage_key`'s own prefix-strip (A-099 — the mutation must live inside `python.py`, never inside `evaluate.py`, which you cannot touch); record failure counts (A-067).

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

**A. Nothing may depend on machine speed.** Classification is pure and deterministic; no waits or timing.

**B. Nothing may depend on test order or workers.** Restore registry state or construct it afresh.

**C. No hollow tests.** Assert exact independent line sets and paired inside/outside paths, not class existence or calls.

**D. No coverage evasion.** Never add an exclusion pragma to changed implementation lines.

**E. Control filesystem inputs.** Use committed text and tmp_path; no network or ambient checkout state.

## Scope / forbid

The protocol and evaluator are frozen inputs. Needing either is a BLOCKED signal, not permission to widen scope.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

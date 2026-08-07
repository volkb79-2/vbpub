---
schema_version: 1
id: assay-P24-real-go-canary-r3
project: assay
title: "A real Go pipeline catches each canary for its intended cause"
tier: implement-2
input_revision: "48771e48c7b2ed7ed937cbe07e193718c6f242bb"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P19-isolated-r3-cli-pipeline, assay-P22-real-go-r1-gate]
session: resume:assay-go-canary
scope:
  touch: ["gate/go/**", "src/assay/canary.py", "src/assay/adapters/go.py", "src/assay/registry.py", "tests/fixtures/go/**", "tests/**", "README.md"]
  forbid: ["src/assay/adapters/python.py", "src/assay/mutation.py", "src/assay/verdict.py", "src/assay/schemas", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "A known-good Go control passes through real go test plus R1 before either transform is judged"
    negative: "A broken control cannot yield a PASS canary even when the transform also fails"
    gate: tester-unified
  - id: O2
    observable: "The import-break transform is accepted only after real go test fails with COMMAND_FAILED, and uncovered-line only after R1 fails with UNCOVERED_LINES"
    negative: "Swapping the expected reasons makes both paired canaries fail"
    gate: tester-unified
  - id: O3
    observable: "Both runs use isolated copies and the installed CLI; surviving, wrong-reason, no-op, and malformed transforms have complete non-PASS artifacts"
    negative: "Pre-generated profiles or any-nonzero acceptance lets at least one adversarial case pass"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "Go command failure cannot be distinguished from missing coverage without changing the R3 model"
  - "the canary requires editing the consumer worktree"
mutexes: []
---

# P24 — real Go canary R3

The claim to attack: **the real Go gate rejects each declared canary for that canary's specific intended cause.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P24-real-go-canary-r3`
on branch `feat/assay-P24-real-go-canary-r3`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§6, 10–12; decisions A-010, A-041–A-043, A-067, A-084, A-105–A-109.
2. P19's isolated R3 CLI contract and P22's real Go gate/module fixture. Go must fit both without a second canary runner or gate declaration.
3. `src/assay/canary.py`, especially the pre-generated-profile `run_go_canary`; replace its adapter-level proof as the product oracle while retaining pure helpers that remain useful.
4. `src/assay/adapters/go.py` injectors and their tests. Confirm the appended `init` panic and never-called function are valid in the concrete package selected by the fixture.
5. `tests/test_canary_python_pipeline.py` for cause-sensitive terminal cases, not Python-specific filesystem mechanics.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` P24 carve.

## Work

1. Route a Go R3 lane through P19's isolated control/transform orchestration in P22's combined image. Both halves run the same real `go test -coverprofile` R0/R1 pipeline through the installed wheel.
2. For import-break, append the valid panicking `init`, run the transformed command, and accept only `FAIL/COMMAND_FAILED`. Do not attempt R1 when command failure legitimately produced no profile.
3. For uncovered-line, append the valid never-called function, require R0 PASS and a newly generated profile, and accept only R1 `FAIL/UNCOVERED_LINES` for the injected lines.
4. Require the real control to PASS at every rigor level the transformed comparison needs. Broken control, missing tool/profile, malformed/no-op transform, transformed PASS, and wrong adverse cause remain distinct complete R3 evidence.
5. Preserve the consumer repository fingerprint, module/cache isolation, exact argv/environment/base, P16 policy fields, and final timing through both halves. Advance Go's registry capability through R3 only after both real mechanisms pass.
6. Delete no adapter-level fixtures merely because the real proof exists; retain them as pure unit coverage but make them incapable of satisfying the installed-product oracle alone.
7. Break real subprocess use, control gating, each expected cause, profile freshness, isolation, and installed CLI invocation separately; run the real combined gate and record exact A-067 counts.

## Test constraints copied from AUTHORING.md §3b

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever (make it generous — 60s, not 3s). It must never be the thing that decides pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race the slow host revealed. Fix the test. Never widen a timeout, and never raise a cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module attributes, singletons) without restoring it. Under `pytest-xdist` the damage lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown *materializes* the patched attribute as a permanent instance attribute and pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier test leave behind?"** before "what raced?" — pollution is more common than a race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real — it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects** them, and note it matches the literal token anywhere on a line — including in a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too — it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**Author's check:** for every test you specify, ask *"could this flip its verdict on a slower machine, in a different worker, or in a different order?"* If yes, it is not an oracle yet.

## Package-specific test emphasis

**A. No speed-dependent verdicts.** Real process completion and cause codes decide; no timing comparison.

**B. No order/worker dependence.** Fresh scratch module/cache per canary pair; consumer state never mutates.

**C. No hollow tests.** Genuine Go commands and specific causes are mandatory; pre-generated profiles alone cannot pass O2.

**D. No coverage evasion.** Maintain the full combined gate and record every controlled-break count.

**E. Control inputs.** Offline pinned Go toolchain, installed assay wheel, and disposable repositories only.

## Scope / forbid

This package adds genuine Go R3 only. It must not change Python, mutation, v3 schema, or the already-registered gate command; P22's image and gate are reused, so no merge mutex is needed.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

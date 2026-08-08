---
schema_version: 1
id: assay-P31-real-go-canary-r3
project: assay
title: "A real Go pipeline catches each canary for its intended cause"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P30-real-go-mutation-r2-integration]
session: resume:assay-go-canary
scope:
  touch: ["gate/go/**", "src/assay/canary.py", "src/assay/adapters/go.py", "src/assay/cli.py", "src/assay/registry.py", "tests/fixtures/go/**", "tests/**", "README.md"]
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

# P31 — real Go canary R3

The claim to attack: **the real Go gate rejects each declared canary for that canary's specific intended cause.**

## Dispatch contract

- Contract class: **2d — constrained implementation**.
- Required roles: **Sonnet xhigh implementer → Opus xhigh independent reviewer**.
- Readiness: **PROVISIONAL until P30 merges.** Freeze real installed-CLI artifacts
  for good control, each intended cause, survivor, wrong cause, malformed and
  no-op cases before dispatch.
- Implementer freedom: fixture plumbing and private transform helpers only; the
  transforms, expected reasons, isolated execution path, and terminal table are
  fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P31-real-go-canary-r3`
on branch `feat/assay-P31-real-go-canary-r3`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§6, 10–12; decisions A-010, A-041–A-043, A-067, A-084, A-105–A-109.
2. P22's committed snapshot, P23's exact-plan R3 orchestration, P27's tiny
   Go fixture, and P28's real-srdm harness. Go must fit all of them without a
   second canary runner or gate declaration.
3. `src/assay/canary.py`, especially the pre-generated-profile `run_go_canary`; replace its adapter-level proof as the product oracle while retaining pure helpers that remain useful.
4. `src/assay/adapters/go.py` injectors and their tests. Confirm the appended `init` panic and never-called function are valid in the concrete package selected by the fixture.
5. `tests/test_canary_python_pipeline.py` for cause-sensitive terminal cases, not Python-specific filesystem mechanics.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md`'s historical
   pre-renumber P24 carve.

## Implementation packet (normative)

### Fixed transforms and generic runner contract

Do not invent Go canary syntax. Preserve these byte-exact trailing appends
(normalizing the original to one trailing newline first, but never reformatting
the original):

```go
func init() {
	panic("assay-canary-import-break")
}
```

```go
func _assayCanaryUnreached(value int) int {
	doubled := value * 2 // assay-canary: executed by no test
	return doubled
}
```

P23's one language-generic isolated canary entry point accepts the immutable
command plan, snapshot spec, resolved adapter, declared project-relative target,
mechanism, R1 policy, and shared deadline. This package calls it with the P27
resolved `GoAdapter`; it does not create `run_go_*` orchestration. The returned
v4 `CanaryResult.target` is exactly the declaration and must equal
`judgment.r3.target`. After proof, the existing Go registry entry preserves R1
and R2 and adds only R3: `{"R1","R2","R3"}`.

Both real mechanisms use this state machine:

1. Materialize a fresh control snapshot at the resolved HEAD, ensure the
   profile is absent, and run the exact plan. Require R0 PASS, a fresh profile,
   and R1 PASS before transforming anything.
2. Apply exactly one append to the declared repo/project target bytes. Verify
   changed bytes are the suffix only and construct P22's neutral transform
   commit through Git plumbing.
3. Materialize an independent transform snapshot, again with no profile, and
   run the same argv/env/cwd with remaining lane budget.
4. Judge the terminal using the table below. Never reuse the control profile,
   accept an arbitrary non-PASS, or run R1 after a command failure produced no
   artifact.

| Mechanism/state | Required observation | R3 judgment |
|---|---|---|
| known-good control | R0 PASS + fresh R1 PASS | eligible to transform |
| `import-break` | R0 `FAIL/COMMAND_FAILED`; profile may be absent | canary PASS |
| `uncovered-line` | R0 PASS then R1 `FAIL/UNCOVERED_LINES` naming appended executable lines | canary PASS |
| transform passes | R0/R1 PASS | `FAIL/CANARY_SURVIVED` |
| fails for another cause | exact observed cause differs | cause-sensitive non-PASS |
| broken control / malformed or no-op transform / missing tool/profile / timeout | retain distinct existing terminal | never canary PASS |

### Prepared real attacks and traceability

The tiny P27 module has `used/` and `unused/` packages. Positive mechanisms use
the normal real `go test ./...` plan and target `used/`. The wrong-cause attack
uses a separate declared lane whose exact plan is `go test ./used` while R1
source roots still include both packages, then applies `import-break` to
`unused/`: R0 remains PASS because that package is outside the selected command,
and R1 catches its changed executable lines as `UNCOVERED_LINES` instead of the
expected `COMMAND_FAILED`. Merely saying “not imported” under `go test ./...` is
invalid—`./...` still builds/runs the package's own test binary and executes its
`init`. Run uncovered-line against `used/`. Repeat both successful mechanisms on
a selected tested package in the disposable srdm copy. Assertions compare process ledgers,
fresh artifact inode/content, exact appended line identities, complete v4
artifact, and pre/post consumer hashes.

Work 1–3 -> generic runner/transforms -> O1/O2; work 4–5 -> state machine and
identity/freshness -> O2/O3; work 6–7 -> unit plus real attack matrix -> all
oracles. The REPORT records target/package rationale, exact ledgers/artifacts,
tests, and break counts. Fixture package choice may change if the pinned tree
does, but it must be proven imported/covered first; snippets, state flow,
cause table, generic entry point, v4 target binding, and preserved R1/R2/R3
registry are fixed.

## Work

1. Route a Go R3 lane through P23's isolated control/transform orchestration in
   P27's combined image. Both halves run the same real
   `go test -coverprofile` R0/R1 pipeline through the installed wheel.
2. For import-break, append the valid panicking `init`, run the transformed command, and accept only `FAIL/COMMAND_FAILED`. Do not attempt R1 when command failure legitimately produced no profile.
3. For uncovered-line, append the valid never-called function, require R0 PASS and a newly generated profile, and accept only R1 `FAIL/UNCOVERED_LINES` for the injected lines.
4. Require the real control to PASS at every rigor level the transformed comparison needs. Broken control, missing tool/profile, malformed/no-op transform, transformed PASS, and wrong adverse cause remain distinct complete R3 evidence.
5. Preserve the consumer repository fingerprint, module/cache isolation, immutable effective argv/environment/base, v4 policy/target fields, fresh per-half profile, and shared lane deadline through both halves. Advance Go's registry capability through R3 only after both real mechanisms pass.
6. Delete no adapter-level fixtures merely because the real proof exists; retain them as pure unit coverage but make them incapable of satisfying the installed-product oracle alone.
7. Break real subprocess use, control gating, each expected cause, profile freshness, isolation, and installed CLI invocation separately; run the real combined gate and record exact A-067 counts.

## Carried in from P20–P23

P22 replaces P19's working-tree `copytree` implementation with one generic
committed-object snapshot substrate; P23 owns the generic runner. The old
relocation helpers and duplicated
`_project_prefix` are historical implementation details, not interfaces to
copy. Reuse the effective plan and fresh control/transform snapshots as-is.
The v4 canary payload already carries and verifies `target`; a Go-specific
description string must not become a second identity channel.

Exercise both mechanisms against selected packages in the disposable real
srdm copy as well as the tiny fixture. This validates adapter abstraction; it
does not authorize edits to srdm or registration of an srdm consumer lane.

## Historical P19 findings retained after P23 (read before work items 1, 4 and 5)

Written by P19's implementer, then reviewed, corrected and ratified as
A-149–A-152. The behavioral cases remain binding; P22/P23 supersede P19's
working-tree-copy mechanics.

- **RULED (A-149/A-155) — snapshot paths and the effective command plan are
  relocated as one object.** `judge.source_root_paths` are absolute; handing a
  snapshot the consumer paths silently yields 0/0 PASS. P23's generic runner
  owns the relocation. Do not add a Go-side relocation path.
- **RULED (A-150) — `uncovered-line` can only be PROVED by a lane that also declares R1**, because `UNCOVERED_LINES` is produced by R1 and by nothing else. An R0+R3 lane can only ever report that mechanism as SURVIVED. Your work item 3 already requires R0 PASS plus a fresh profile; make at least one fixture declare `rigor = ["R0","R1","R3"]`, or the mechanism has no PASS witness at all.
- **CORRECTED — a wrong-observed-cause IS reachable with the real, un-mocked adapter.** P19's implementer recorded the opposite. `import-break` injected into a module the lane's own tests never import leaves R0 passing and is caught by R1 instead: observed `UNCOVERED_LINES` against an expected `COMMAND_FAILED`. Both a unit and an installed-wheel artifact now exist. Only a genuine NO-OP transform is unreachable through a real adapter.
- **The canary runner is adapter-generic.** Reuse P23's public generic entry
  point with `GoAdapter`; do not create a parallel Go orchestration.
- **`scope.touch` gained `src/assay/cli.py`** (A-144's shape, third instance): widening `_built_in_registry`'s GoAdapter entry with `"R3"` is a one-line change, and without it a declared Go R3 lane is refused before anything runs. `runner.py` is deliberately NOT added — its R3 block is already language-neutral, so it needs calling, not writing.
- P22/P23 start each half from tracked committed objects and require fresh
  coverage. Go build/test caches stay outside the snapshot; ignored files are
  not implicit inputs. There is no Go-specific project-prefix helper to
  duplicate.

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

This package adds genuine Go R3 only. It must not change Python, mutation, the
v4 schema, or the already-registered gate command; P27's image and gate are
reused, so no merge mutex is needed.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

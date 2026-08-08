---
schema_version: 1
id: assay-P23-real-go-mutation-r2
project: assay
title: "Go changed-line mutants are valid single-site programs judged by real go test"
tier: implement-2
input_revision: "48771e48c7b2ed7ed937cbe07e193718c6f242bb"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P18-r2-cli-pipeline, assay-P22-real-go-r1-gate]
session: resume:assay-go-mutation
scope:
  touch: ["cmd/assay-go-helper/**", "gate/go/**", "src/assay/adapters/go.py", "src/assay/cli.py", "src/assay/registry.py", "src/assay/mutation.py", "tests/fixtures/go/**", "tests/**", "README.md"]
  forbid: ["src/assay/adapters/python.py", "src/assay/canary.py", "src/assay/verdict.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "A Go-toolchain helper discovers declared changed-line operators with exact byte spans; every mutant differs at one site and parses/formats as valid Go"
    negative: "Regex replacement, whole-file gofmt rewriting, or a mutation outside the changed lines fails byte-diff and parser checks"
    gate: tester-unified
  - id: O2
    observable: "Compare-swap, boolop-swap, and bool-const-flip have fixtures independently killed or survived by real go test; Python-specific falsy-swap produces no Go sites rather than an invented typed zero-value rewrite"
    negative: "Returning UNSUPPORTED or universal killed fails the expected per-mutant manifest"
    gate: tester-unified
  - id: O3
    observable: "The installed CLI produces exact Go R2 killed, survived, compile-crashed, no-mutants, and budget-exceeded artifacts while the source module is unchanged"
    negative: "Treating compilation failure as killed or editing the live module changes artifact or source hash"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a valid single-site Go operator cannot be located with compiler/token information"
  - "the helper cannot be built offline in the declared Go gate image"
mutexes: []
---

# P23 — real Go mutation R2

The claim to attack: **assay constructs valid single-site Go mutants on changed lines and reports what real `go test` does to each.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P23-real-go-mutation-r2`
on branch `feat/assay-P23-real-go-mutation-r2`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§6, 10–12; decisions A-003–A-004, A-041–A-043, A-067, A-082, A-112–A-122.
2. P18's frozen R2 CLI/config contract and P22's real combined Go gate/module fixture. Reuse both without another orchestration or image.
3. `src/assay/adapters/go.py::generate_mutants`, `src/assay/mutation.py`, and Python's byte-exact mutant implementation/tests as the protocol reference, not as Go syntax logic.
4. Go standard `go/parser`, `go/ast`, `go/token`, and `go/format` APIs available in P22's pinned toolchain. The helper may use syntax/token positions to locate spans but must not reprint the whole file.
5. `/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py` for the four conceptual operator names and deterministic ordering only; its Python AST/unparse mechanism is forbidden prior art.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` P23 carve and finding 11.

## Work

1. Add a small offline-built `assay-go-helper` beneath `cmd/` that reads source plus selected lines through a bounded machine protocol and returns syntax-derived exact byte spans/replacements in deterministic order. Build it into P22's gate image and declare it as the Go adapter's external tool.
2. Implement the three language-valid shared operator identities for Go: comparisons, `&&`/`||`, and boolean constants. `falsy-swap` is Python's swap among dynamically-typed falsy literals; Go has no equivalent valid across static types, so it deliberately produces no Go sites. Do not relabel a nil/zero/equality rewrite as falsy-swap.
3. Produce each `Mutant` by one byte splice against the original UTF-8 source. Preserve all bytes outside the selected span; use Go parsing/formatting only to validate the result, never to generate whole-file output.
4. Select only sites whose syntactic construct begins or is wholly attributed to a declared changed line under one documented rule. Exclude comments, strings, generated/test files, unchanged lines, and nested second-site changes.
5. Run the existing P18 executor/isolation path with real `go test`; compilation/non-test infrastructure errors are `crashed`, ordinary test rejection is `killed`, zero is `survived`, and timeout remains budget-exceeded. Advance Go's registry capability through R2 only after this installed path passes.
6. Add independently enumerated fixtures covering multiple same-line sites, Unicode before a span, multiline expressions, comments/strings resembling operators, invalid source, no sites, and every terminal result. Prove source hashes unchanged.
7. Break AST/token discovery, line selection, byte splicing, validity checking, external-tool preflight, crash/kill separation, and installed CLI wiring separately; run the real combined gate and record exact A-067 counts.

## Carried in from P17, merged (read before writing work items 1 and 7)

**Work item 7's "external-tool preflight" is P22's mechanism, not one you
build (A-144).** Until P22, nothing in assay preflighted
`LanguageAdapter.external_tools` and the closed reason vocabulary had no
truthful cause for a missing one (A-013/A-086, deferred by A-087 and again
by A-142). P22 owns `MISSING_EXTERNAL_TOOL` and the preflight call site;
`errors.py` and `cli.py` are in ITS `scope.touch`, deliberately not yours.
Your half is work item 1's declaration — `assay-go-helper` named in
`GoAdapter.external_tools` — plus the negative that proves removing it from
that tuple stops the preflight from firing. If you find P22 did not build
it, that is a BLOCKED, not an improvisation into `errors.py`.

**A-139/A-140, in one line each.** Every declared rigor level is checked
against the registry before anything runs, so advancing Go through R2
(work item 5) is one added level on the EXISTING `GoAdapter` entry;
`runner.refuse_lane` renders any pre-execution refusal as a complete
artifact and is already total over `lane.rigor`. And the declared coverage
artifact must be git-ignored, or the whole-tree cleanliness guard refuses
the run before `go test` starts.

## Carried in from P18, MERGED AND RATIFIED (read before work item 5)

Reviewed at the P18 merge, corrected where wrong, ratified as A-145–A-147.
Treat as decided.

- **CONFIRMED and FIXED (A-147) — the carve gap P18's implementer flagged was real.** `cli._built_in_registry` (not `registry.py`) holds the `GoAdapter` entry work item 5 must widen to R2, and `scope.touch` did not name `cli.py`. It does now. Second instance of A-144's shape; if you find a third, it is a carving-process finding, not a one-off.
- `assay.mutation.run_mutation`'s signature changed: `baseline` is now a REQUIRED caller-supplied `CommandResult` (never run internally), `operators` is REQUIRED, and `repo_top` is REQUIRED (A-145). Work item 5's "existing P18 executor/isolation path" means calling it with all three, not the pre-P18 shape.
- **RULED (A-145) — a target's `path` is REPO-top-relative; the per-mutant copy is a copy of the PROJECT root.** P18 shipped a crash for any project in a subdirectory of its repo. Fixed via `mutation.project_prefix`, but a real Go module under `gate/go/**` is exactly that shape — fixture it, do not assume the two roots coincide.
- `assay.mutation.resolve_mutation_targets` (new in P18) filters candidates through `adapter.source_globs`/`excluded_dir_names`/`is_test_path`. P18 did not change `GoAdapter`'s own values for these (from P08) -- confirm they are still right now that R2 target scoping reads them.
- **Your O3 is P18's own O4, in Go.** `tests/test_standalone.py`'s five installed-wheel R2 comparisons are the working pattern (complete document `==`, tree hashes before/after, PATH declared not passed through). Reuse the shape; note that P18 records WHY a crashed mutant is unreachable through a real fixed-argv lane — for Go it IS reachable, because a mutant that fails to COMPILE is a different thing from one that fails to launch.

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

**A. No speed-dependent verdicts.** Executor and process results decide; no compile/test duration comparison.

**B. No order/worker dependence.** One scratch module per mutant and deterministic identity sorting.

**C. No hollow tests.** Independently enumerate byte spans and run real parser plus `go test`; helper call counts are insufficient.

**D. No coverage evasion.** Maintain full Python/Go gate requirements and record every controlled-break count.

**E. Control inputs.** Offline pinned Go helper/toolchain and disposable modules; no live consumer writes.

## Scope / forbid

This package adds Go mutation only. It must not change Python mutants, R2 payload/schema, canary, or shared gate declarations. P22's image may be extended with the helper without editing `nyxloom.toml`, so no merge mutex is needed.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

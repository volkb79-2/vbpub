---
schema_version: 1
id: assay-P22-real-go-r1-gate
project: assay
title: "A real Go toolchain produces an R1 verdict through the installed assay CLI"
tier: implement-2
input_revision: "48771e48c7b2ed7ed937cbe07e193718c6f242bb"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P21-versioned-wheel-contract]
session: resume:assay-go
scope:
  touch: ["gate/go/**", "nyxloom-trove/nyxloom.toml", "src/assay/adapters/go.py", "src/assay/cli.py", "src/assay/config.py", "src/assay/errors.py", "src/assay/registry.py", "tests/fixtures/go/**", "tests/**", "README.md"]
  forbid: ["src/assay/mutation.py", "src/assay/canary.py", "src/assay/adapters/python.py"]
oracles:
  - id: O1
    observable: "An assay-owned image combining tester-unified Python with tester-unified-go's pinned toolchain runs a real two-commit Go module through go test -coverprofile and installed assay run to an exact R0+R1 artifact"
    negative: "Using a committed coverprofile or fake process result passes unit fixtures but fails the disposable real-toolchain project"
    gate: tester-unified
  - id: O2
    observable: "The module path is derived from the fixture's go.mod and real coverprofile keys normalize exactly to Git paths without a hardcoded module default"
    negative: "Changing the module name while retaining a literal prefix causes missing coverage or changes the expected artifact"
    gate: tester-unified
  - id: O3
    observable: "Real profiles exercise multi-line blocks, columns, comments, doc.go, test files, partial coverage, and empty coverage against an independently calculated changed-line manifest"
    negative: "Inclusive line expansion that attributes a non-statement closing line disagrees with the independent manifest"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "tester-unified-go cannot be used as an immutable toolchain stage for an assay-owned Python-plus-Go image"
  - "real Go block semantics cannot be represented by the current coverage model without a design decision"
mutexes: [merge-lane]
---

# P22 — real Go R1 gate

The claim to attack: **a real Go project can obtain a genuine changed-line coverage verdict through the installed assay product.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P22-real-go-r1-gate`
on branch `feat/assay-P22-real-go-r1-gate`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§10–13; decisions A-006, A-008, A-010, A-042–A-043, A-067, A-084, A-087, A-102–A-107, A-114, A-126.
2. P17's CLI contract and P21's installed versioned wheel. This package proves Go through that path, never through direct adapter calls.
3. `src/assay/adapters/go.py`, `src/assay/coverage_parsers/go_cover.py`, and all Go adapter/parser tests; identify every assertion based only on hand-written profiles.
4. `/workspaces/vbpub/tester-unified/Dockerfile` and `/workspaces/vbpub/tester-unified-go/Dockerfile`. Treat both as immutable stages; create the combined image only beneath assay's `gate/go/`.
5. `/workspaces/vbpub/shared-ramdisk-depot-manager/tools/covergate/` in full, especially `profile.go`, `evaluate.go`, and `hascode.go`; this is the independent real-Go behavioral reference.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` finding 12 and its P22 carve.

## Environment setup

From fresh main, build the existing base images by their documented commands, then build `assay-go-gate:local` from `gate/go/Dockerfile` with `/workspaces/vbpub` as context. The Dockerfile must use `tester-unified:local` for the Python/runtime identity and copy only the pinned Go toolchain from `tester-unified-go:local`; it must not download a second Go or Python toolchain. Run every gate container with the cgroup parent returned by assay's verified `tools/cgroup-parent.sh`. Teardown only disposable containers/scratch; retain the local image like the estate's other gate images.

## Work

1. Add the assay-owned multi-stage gate image and mechanically verify full uid/group/HOME/XDG identity, writable Go caches outside the bind-mounted repository, `GOTOOLCHAIN=local`, installed assay wheel, and exact expected Go version.
2. Extend the existing `tester-unified` gate command to run the ordinary Python proof and a real-Go proof in the combined image, preserving cgroup verification and P14/P21's independent self-hosting witness. The gate id remains `tester-unified`; do not create an unregistered handoff gate name.
3. Derive Go module path from the project root's real `go.mod` `module` directive under a closed config/adapter construction path and advance Go's registry capability through R1 only. Missing, duplicate, malformed, or escaping module declarations fail; never use a hardcoded module fallback or advertise Go R2/R3.
4. Materialize a small two-commit Go module at test time. Its lane runs genuine `go test ./... -coverpkg=./... -covermode=atomic -coverprofile=<artifact>` through the installed CLI, with no network/module download.
5. Compare assay's exact R1 artifact and changed-line manifest against an independently calculated expectation and the srdm reference behavior for executable bodies, comments, `doc.go`, `_test.go`, missing profile files, and 0/0.
6. Generate real profiles whose blocks cross physical lines and vary start/end columns. Decide the suspected inclusive-line issue from those observations: preserve current behavior only if it agrees with Go's instrumented statement semantics; otherwise repair it without inventing data absent from the profile.
7. Change the fixture module name and repository nesting to prove module-prefix derivation and boundary-safe normalization. Missing `go`, wrong Go version, or unavailable helper prerequisites render honest non-PASS outcomes.
8. Break real tool execution, wheel installation, module derivation, prefix boundary, line/column attribution, doc/test exclusion, and independent artifact comparison separately; run the full real gate and record exact A-067 counts.

## Carried in from P17, merged (read before writing work items 5 and 7)

**You own `MISSING_EXTERNAL_TOOL`, and `errors.py` plus `cli.py` were added
to this package's `scope.touch` so that you can (A-144).** Work item 7 says
"Missing `go`, wrong Go version, or unavailable helper prerequisites render
honest non-PASS outcomes" — and DESIGN-GUIDE §11/A-013 say a missing
declared adapter prerequisite is `NO_MEASUREMENT`, not `ERROR`. A-086
already named the member and ruled that it "belongs to the package that
first makes the state reachable"; A-087 deferred it (P08's Go adapter
shipped `external_tools = ()`), and A-142 deferred it again for the same
reason in P17. **You are that package**: this is the first one where an
adapter genuinely declares a tool it cannot run without. Do not settle for
`ERROR`/`EXEC_FAILED` — that is the lane's own command failing to start,
a different fact, and A-086 already rejected it as a lie. Preflight in
`cli._resolve_declared_adapters` (where the capability gate already lives)
and render the refusal through the EXISTING `runner.refuse_lane`; you do
not need `runner.py` and it is deliberately still out of scope.

**A-139 — every declared rigor level is checked against the registry
before anything runs, and every post-`HEAD` terminal path emits a COMPLETE
artifact.** So advancing Go's capability (work item 5) means adding a
`RegistryEntry` for `GoAdapter` naming `R1`; `cli._resolve_declared_
adapters` already loops over `lane.rigor` and will admit it with no other
CLI change. Your missing-tool refusal must go out as an artifact too —
`refuse_lane` builds one claim per declared level and is already total.

**A-140 — the declared `judge.coverage.artifact` must be git-ignored.**
`run_lane` validates the artifact path, then requires a clean whole
worktree, and only then removes a stale artifact. Your disposable Go
module's `-coverprofile=<artifact>` target is untracked worktree state
until you ignore it, and the run will be refused `NO_MEASUREMENT`/
`DIRTY_TREE` before `go test` ever starts.

## Carried in from P16, merged (read before writing work item 5)

**Work item 5's "assay's exact R1 artifact" is a schema-v3 artifact, and
v3 binds the policy that judged it.** `judgment.r1` is present if and only
if the R1 claim carries a `coverage` payload, and its `language`/
`source_roots`/`coverage_format`/`coverage_artifact`/`fail_under`/
`allow_excluded`/`base` must be the values THIS Go lane actually resolved
— an independently calculated expectation has to calculate those too, not
copy them from the Python fixture. `Coverage` also now carries
`excluded_lines`/`files_with_excluded_lines`, whose summary must name
exactly the paths the mapping does. See P17's own carried-in section for
the `judgment.r1`-iff-`coverage` trap; it applies identically here.

**A-136 — an R1 claim that is `PASS` or `FAIL` must carry `coverage`;
`ERROR` and `NO_MEASUREMENT` must not.** Work item 7's "missing `go`,
wrong Go version, or unavailable helper prerequisites render honest
non-PASS outcomes" must therefore pick between `NO_MEASUREMENT` and
`ERROR` deliberately — neither may be dressed as a `FAIL`.

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

**A. No speed-dependent verdicts.** Wait for real Go commands; no compile/test duration assertion.

**B. No order/worker dependence.** Every Go module, cache namespace, profile, and wheel install is isolated.

**C. No hollow tests.** Genuine `go test` output and independent manifests are mandatory; committed profiles cannot satisfy O1.

**D. No coverage evasion.** Both Python and Go gate portions retain full required coverage and recorded controlled-break counts.

**E. Control inputs.** Pinned offline toolchains/modules only; `GOTOOLCHAIN=local`, no module network, no cockpit Go.

## Scope / forbid

This package proves Go R1 and owns only assay's derived gate image/config plus Go R1 adapter behavior. It must not edit shared tester images, srdm, mutation, canary, or Python semantics. `merge-lane` is required because the real gate declaration changes.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

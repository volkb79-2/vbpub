---
schema_version: 1
id: assay-P26-real-go-r1-gate
project: assay
title: "A real Go toolchain produces an R1 verdict through the installed assay CLI"
tier: implement-2
input_revision: "ebbe208c4d4ff275da2ca6bd276bea103fca2563"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P24-real-python-project-qualification]
session: resume:assay-go
scope:
  touch: ["gate/go/**", "nyxloom-trove/nyxloom.toml", "src/assay/adapters/go.py", "src/assay/cli.py", "src/assay/config.py", "src/assay/registry.py", "tests/fixtures/go/**", "tests/**", "README.md"]
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
  - id: O4
    observable: "A disposable two-commit copy of the real srdm module produces the same changed-line classification and terminal result through installed Assay and srdm's independent covergate"
    negative: "A toy-only module-prefix, repository-root, or block-expansion assumption disagrees on the real srdm tree"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "tester-unified-go cannot be used as an immutable toolchain stage for an assay-owned Python-plus-Go image"
  - "real Go block semantics cannot be represented by the current coverage model without a design decision"
mutexes: [merge-lane]
---

# P26 — real Go R1 gate

The claim to attack: **a real Go project can obtain a genuine changed-line coverage verdict through the installed assay product.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P26-real-go-r1-gate`
on branch `feat/assay-P26-real-go-r1-gate`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§10–13; decisions A-006, A-008, A-010, A-042–A-043, A-067, A-084, A-087, A-102–A-107, A-114, A-126.
2. P17's CLI contract and P23's installed versioned wheel. This package proves Go through that path, never through direct adapter calls.
3. `src/assay/adapters/go.py`, `src/assay/coverage_parsers/go_cover.py`, and all Go adapter/parser tests; identify every assertion based only on hand-written profiles.
4. `/workspaces/vbpub/tester-unified/Dockerfile` and `/workspaces/vbpub/tester-unified-go/Dockerfile`. Treat both as immutable stages; create the combined image only beneath assay's `gate/go/`.
5. `/workspaces/vbpub/shared-ramdisk-depot-manager/go.mod`, representative `internal/` packages, `tools/covergate/` in full (especially `main.go`, `profile.go`, `evaluate.go`, and `hascode.go`), and `nyxloom-trove/GUIDE.md`'s coverage section. This is both the real Go validation target and the independent behavioral reference; never edit it from this package.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` finding 12 and its P22 carve.

## Environment setup

From fresh main, build the existing base images by their documented commands, then build `assay-go-gate:local` from `gate/go/Dockerfile` with `/workspaces/vbpub` as context. The Dockerfile must use `tester-unified:local` for the Python/runtime identity and copy only the pinned Go toolchain from `tester-unified-go:local`; it must not download a second Go or Python toolchain. Run every gate container with the cgroup parent returned by assay's verified `tools/cgroup-parent.sh`. Teardown only disposable containers/scratch; retain the local image like the estate's other gate images.

## Implementation packet (normative)

### Image, adapter, and module interfaces

`gate/go/Dockerfile` is multi-stage and offline: the final stage is
`tester-unified:local`; it copies `/usr/local/go` from
`tester-unified-go:local`, sets `GOTOOLCHAIN=local`, and places `go` on PATH.
The gate asserts the exact `go version` already pinned by the source image,
full passwd/group/HOME/XDG identity, and writable `GOCACHE`/`GOMODCACHE` under
per-run scratch. It may not run `go install`, `go env -w`, package-manager, or
network commands.

`GoAdapter.external_tools` becomes exactly `("go",)`. P21 already reserves
`NO_MEASUREMENT/MISSING_EXTERNAL_TOOL`; this package makes it reachable. Resolve
the immutable P22 command plan first, find `go` only through its effective
`PATH`, and use that absolute executable for preflight and module parsing. No
ambient PATH or conventional `/usr/local/go/bin` fallback is legal.

Construct the adapter once per resolved project:

```python
def resolve_go_adapter(*, project_root: Path,
                       plan: CommandPlan) -> GoAdapter: ...
```

It runs `<absolute-go> mod edit -json` with `cwd=project_root`, the plan's
controlled environment, no network, and a bounded output read. Parse the
official JSON object and require one nonempty `.Module.Path` string; tolerate
other correctly typed keys emitted by the pinned Go tool, but do not interpret
them as module identity. Reject missing `go.mod`, malformed/duplicate module
directives, null/missing `Module`, control bytes, or a path ending/starting with
`/`. The returned
`GoAdapter(module_path=<derived>)` is the one object used by normalization and
artifact policy. Do not hand-parse Go's quoted module grammar and do not default
the module name.

The registry entry is `RegistryEntry(adapter=<resolved GoAdapter>,
rigor=frozenset({"R1"}))` only after the real proof passes (`R0` deliberately
does not use the adapter registry). Go coverage format remains
`go-cover`; it reports exclusion capability `unavailable`.

### Real proof fixtures

The tiny generated module is:

```text
repo/app/go.mod                    module example.invalid/assayfixture
repo/app/calc.go                   bodies, comment/doc, multiline block
repo/app/calc_test.go              covers a strict subset
repo/app/doc.go
repo/.assay/.gitignore             tracked parent, generated profile ignored
```

Its lane uses P22's exact snapshot command:
`go test ./... -coverpkg=./... -covermode=atomic
-coverprofile=.assay/coverage.out`, with `cwd=repo/app`, declared effective
PATH, R0+R1, `source_roots=["."]`, `language="go"`, and full base OID. A literal
manifest committed under `tests/fixtures/go/` gives expected Git paths and
physical statement lines independently of the generated coverprofile. Rename
the module to `example.invalid/renamed/deep` without changing expected repo
paths; only derived prefix normalization may make both cases pass.

The tracked `.assay/.gitignore` contains `*` plus `!.gitignore`; the profile is
absent in commits but its real parent exists for P20's output preflight.

The external case extracts the pinned commit's tracked
`shared-ramdisk-depot-manager/` subtree into a disposable two-commit repository
at that same prefix. Use selected existing `internal/` packages and the copied
`tools/covergate`; run installed Assay and covergate against the identical base,
HEAD, profile, source prefix, and 100% floor. A hand-maintained manifest is the
third witness for selected changed lines. Neither tool constructs the other's
expectation.

### Decision/traceability matrix

| State | Required result |
|---|---|
| `go` absent from effective PATH but ambient go exists | complete `MISSING_EXTERNAL_TOOL`; no command |
| malformed/missing module | typed config/prerequisite non-PASS; no hardcoded prefix |
| real multiline block | attribution agrees with instrumented Go statements and manifest |
| module rename + nested project | identical repo-path classification |
| stale/prebuilt profile | freshness refusal; real generation ledger required |
| real srdm disagreement | package BLOCKED with exact three-witness diff, not normalized away |

Work 1–2 -> image/gate -> O1; work 3/7 -> adapter/tool/module -> O2; work 4–6 ->
tiny real module -> O1/O3; work 8 -> srdm differential -> O4; work 9 -> all
controlled breaks. The REPORT records image IDs, Go version, wheel hash, input
OIDs, exact ledgers/manifests, tests, and break counts. Private harness layout
may vary; image provenance, effective-PATH resolution, official module parser,
fixture topology, three witnesses, and R1-only capability may not.

## Work

1. Add the assay-owned multi-stage gate image and mechanically verify full uid/group/HOME/XDG identity, writable Go caches outside the bind-mounted repository, `GOTOOLCHAIN=local`, installed assay wheel, and exact expected Go version.
2. Extend the existing `tester-unified` gate command to run the ordinary Python proof and a real-Go proof in the combined image, preserving cgroup verification and P14/P21's independent self-hosting witness. The gate id remains `tester-unified`; do not create an unregistered handoff gate name.
3. Derive Go module path through the packet's effective-PATH `go mod edit -json` adapter construction and advance Go's registry capability through R1 only. Missing, duplicate, malformed, or escaping module declarations fail; never use a hand parser, hardcoded module fallback, or advertise Go R2/R3.
4. Materialize a small two-commit Go module at test time. Its lane runs genuine `go test ./... -coverpkg=./... -covermode=atomic -coverprofile=<artifact>` through the installed CLI, with no network/module download.
5. Compare assay's exact v4 R1 artifact and changed-line manifest against an independently calculated expectation and the srdm reference behavior for executable bodies, comments, `doc.go`, `_test.go`, missing profile files, exclusion capability, and 0/0.
6. Generate real profiles whose blocks cross physical lines and vary start/end columns. The required normalization is the existing srdm rule: each block classifies the inclusive physical range `startLine..endLine`, and executed wins when blocks overlap a line. The real fixture must confirm that rule against the pinned toolchain; a contradiction is BLOCKED for a product decision, not permission to invent a different parser locally.
7. Change the fixture module name and repository nesting to prove module-prefix derivation and boundary-safe normalization. Missing `go`, wrong Go version, or unavailable helper prerequisites render honest non-PASS outcomes.
8. Materialize a disposable two-commit copy of the real `/workspaces/vbpub/shared-ramdisk-depot-manager` tracked tree, select representative changes beneath `internal/`, generate its profile with the pinned toolchain, and run both installed Assay and the copied `tools/covergate` against the same commit/profile. Compare exact changed-line buckets and terminal class; do not mutate or migrate srdm itself.
9. Break real tool execution, wheel installation, module derivation, prefix boundary, line/column attribution, doc/test exclusion, the real-srdm comparison, and independent artifact comparison separately; run the full real gate and record exact A-067 counts.

## Carried in from P17, merged (read before writing work items 5 and 7)

**P21 owns the `MISSING_EXTERNAL_TOOL` vocabulary; you own its first reachable
preflight, and `cli.py` is in this package's `scope.touch` (A-163 supersedes
A-144's historical package numbering).** Work item 7 says
"Missing `go`, wrong Go version, or unavailable helper prerequisites render
honest non-PASS outcomes" — and DESIGN-GUIDE §11/A-013 say a missing
declared adapter prerequisite is `NO_MEASUREMENT`, not `ERROR`. A-086
already named the member and ruled that its check belongs to the package that
first makes the state reachable; A-087/A-142 deferred that check while every
adapter declared `external_tools = ()`. **You are the reachability package**:
this is the first one where an adapter genuinely declares a tool it cannot run
without. Do not settle for
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

## Carried in from P20–P22 (read before work items 2, 5 and 8)

P20 closed A-O17 before Go makes it reachable: normalized-key collisions
and every other expected post-HEAD evaluation error now render complete
artifacts. Preserve the collision fixture here with two real Go profile keys;
do not widen `runner.py` or rebuild the terminal mapping.

P21 migrated the artifact to v4, including explicit exclusion capability,
and P22 runs from committed-object snapshots with one immutable effective
command plan and total lane budget. The srdm exercise must use that installed
path. A direct `GoAdapter` call or a pre-generated profile is useful unit
coverage but cannot satisfy O4.

## Carried in from P16, merged (read before writing work item 5)

**Work item 5's "assay's exact R1 artifact" is a schema-v4 artifact, and
v4 binds the policy that judged it.** `judgment.r1` is present if and only
if the R1 claim carries a `coverage` payload, and its `language`/
`source_roots`/`coverage_format`/`coverage_artifact`/`fail_under`/
`allow_excluded`/`base` must be the values THIS Go lane actually resolved
— an independently calculated expectation has to calculate those too, not
copy them from the Python fixture. `Coverage` also now carries
`excluded_lines`/`files_with_excluded_lines` plus explicit exclusion
capability, whose summary must name exactly the paths the mapping does. See P17's own carried-in section for
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

---
schema_version: 1
id: assay-P27-real-go-mutation-r2
project: assay
title: "Go changed-line mutants are valid single-site programs judged by real go test"
tier: implement-2
input_revision: "ebbe208c4d4ff275da2ca6bd276bea103fca2563"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P22-exact-reexecution-isolation, assay-P26-real-go-r1-gate]
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
    observable: "The installed CLI produces exact v4 Go R2 killed, survived, command-boundary-crashed, no-mutants, mutant-limit, and lane-budget artifacts while the source module is unchanged"
    negative: "Parsing unstructured go test output to relabel a normal nonzero, omitting killed identities, or editing the live module changes the artifact/source hash"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a valid single-site Go operator cannot be located with compiler/token information"
  - "the helper cannot be built offline in the declared Go gate image"
mutexes: []
---

# P27 — real Go mutation R2

The claim to attack: **assay constructs valid single-site Go mutants on changed lines and reports what real `go test` does to each.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P27-real-go-mutation-r2`
on branch `feat/assay-P27-real-go-mutation-r2`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§6, 10–12; decisions A-003–A-004, A-041–A-043, A-067, A-082, A-112–A-122 and A-155–A-161.
2. P21's v4 R2 evidence/cap contract, P22's exact snapshot executor, and P26's real combined Go gate/module fixture. Reuse them without another orchestration or image.
3. `src/assay/adapters/go.py::generate_mutants`, `src/assay/mutation.py`, and Python's byte-exact mutant implementation/tests as the protocol reference, not as Go syntax logic.
4. Go standard `go/parser`, `go/ast`, `go/token`, and `go/format` APIs available in P26's pinned toolchain. The helper may use syntax/token positions to locate spans but must not reprint the whole file.
5. `/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py` for the four conceptual operator names and deterministic ordering only; its Python AST/unparse mechanism is forbidden prior art.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` P23 carve and finding 11.

## Implementation packet (normative)

### Helper protocol and operator grammar

`cmd/assay-go-helper` is a single offline Go binary. It reads exactly one JSON
request from stdin (maximum 64 MiB plus framing), writes exactly one JSON
response to stdout, writes diagnostics only to stderr, and has no filesystem or
network authority. JSON objects are closed; numbers are integers, never floats.

```json
{"schema_version":1,"source_b64":"<base64 of exact UTF-8 file bytes>",
 "changed_lines":[7,11],
 "operators":["compare-swap","boolop-swap","bool-const-flip"],
 "max_sites":51}
```

```json
{"schema_version":1,"sites":[
 {"start_byte":83,"end_byte":84,"replacement_b64":"PD0=", "line":7,
  "operator":"compare-swap","description":"< to <="}
]}
```

`source_b64` decodes to valid UTF-8 with no NUL. `changed_lines` is sorted,
unique, positive, and bounded; `operators` is an order-preserving subset of
P21's vocabulary; `max_sites` is exactly `max_mutants + 1` and at most 10,001.
The response contains at most that many sites, sorted by
`(start_byte,end_byte,operator,replacement bytes)`, with nonoverlapping valid
UTF-8 byte boundaries. A malformed request, invalid Go source, limit breach,
unknown field, helper nonzero, or malformed response is a typed non-PASS before
any mutant command; it is never `NO_MUTANTS`.

The only Go replacements are:

| Operator | Token replacements |
|---|---|
| `compare-swap` | `==`<->`!=`, `<`<->`<=`, `>`<->`>=` |
| `boolop-swap` | `&&`<->`||` |
| `bool-const-flip` | `true`<->`false` |
| `falsy-swap` | no Go sites |

Use `go/parser`, `go/ast`, and `go/token` to locate token spans. A site is
eligible only when the operator/literal token itself begins on a declared
changed physical line. Ignore lookalikes in comments/strings, all `_test.go`
targets, and files bearing Go's canonical generated-file marker. For each site,
splice only `[start_byte:end_byte]`, parse the resulting full file, and call
`go/format.Source` only as an additional validity check; discard its output.
Every byte outside the span must equal the original. Multiple sites on one line
remain distinct by span even if their projected verdict descriptions match.

Python owns protocol validation and constructs the existing `Mutant` from each
one-splice result. After this package `GoAdapter.external_tools` is exactly
`("go", "assay-go-helper")`, and its registry rigor is exactly `{"R1","R2"}`.
P26's effective-PATH preflight applies to both names.

### Execution and terminal matrix

Each discovered candidate runs in its own P22 snapshot, receives the immutable
baseline plan and remaining shared lane budget, and replaces one repo-relative
target atomically. Classification uses only the process boundary:

| Real result | R2 bucket/terminal |
|---|---|
| process starts, `go test` exits 0 | `survived[]` identity |
| process starts, exits nonzero (including compile error) | `killed[]` identity |
| process cannot start | `crashed[]` identity |
| shared deadline stops/interrupts that candidate | `budget_exceeded[]` identity |
| zero valid sites | `INCONCLUSIVE/NO_MUTANTS` |
| helper finds max+1 | P21 limit sentinel; zero `go test` submissions |

Prepared fixtures must include two same-line operators, operator tokens on a
continuation line, multibyte text before the token, comment/string lookalikes,
generated/test files, invalid source, and fixed killed/survived programs. A
selected disposable srdm package supplies the non-toy topology; its expected
sites are enumerated from source bytes, not helper output.

Traceability: work 1–4 -> helper/protocol/splices -> O1; work 2/6 -> operator
fixtures -> O2; work 5–6 -> P22 executor and v4 arrays -> O3; work 7 -> every
negative. The REPORT attaches protocol examples, helper hash, exact site and
process ledgers, tests, and break counts. Private Go visitor decomposition may
vary; wire grammar, token rules/table, bounds, sorting, byte splice, external
tools, and terminal mapping may not.

## Work

1. Add a small offline-built `assay-go-helper` beneath `cmd/` that reads source plus selected lines through a bounded machine protocol and returns syntax-derived exact byte spans/replacements in deterministic order. Build it into P26's gate image and declare it as the Go adapter's external tool.
2. Implement the three language-valid shared operator identities for Go: comparisons, `&&`/`||`, and boolean constants. `falsy-swap` is Python's swap among dynamically-typed falsy literals; Go has no equivalent valid across static types, so it deliberately produces no Go sites. Do not relabel a nil/zero/equality rewrite as falsy-swap.
3. Produce each `Mutant` by one byte splice against the original UTF-8 source. Preserve all bytes outside the selected span; use Go parsing/formatting only to validate the result, never to generate whole-file output.
4. Select only sites whose operator/literal token begins on a declared changed physical line, exactly as the packet defines. Exclude comments, strings, generated/test files, unchanged token lines, and nested second-site changes.
5. Run P22's existing exact-plan/snapshot path with real `go test`. Per A-158, a process that starts and exits nonzero — including a compiler rejection — is `killed`; exit zero is `survived`; inability to start/execute the command boundary is `crashed`; the shared lane deadline and max-mutant ceiling retain their own terminals. Never classify by scraping human-readable Go output.
6. Add independently enumerated fixtures covering multiple same-line sites, Unicode before a span, multiline expressions, comments/strings resembling operators, invalid source, no sites, every terminal result, and selected real srdm packages in a disposable snapshot. Prove full killed identities and source hashes unchanged.
7. Break AST/token discovery, line selection, byte splicing, validity checking, external-tool preflight, nonzero/boundary classification, max-mutant enforcement, and installed CLI wiring separately; run the real combined gate and record exact A-067 counts.

## Carried in from P17, merged (read before writing work items 1 and 7)

**Work item 7's "external-tool preflight" is P26's mechanism, not one you
build (A-144).** P21 reserves `MISSING_EXTERNAL_TOOL` in v4 and P26 makes the
effective-PATH preflight reachable for `go`. Your half is work item 1's
extension to exactly `("go", "assay-go-helper")`, plus the negative proving
the helper is checked through that same mechanism. If P26 did not deliver it,
that is a BLOCKED, not an improvisation into `errors.py` or `cli.py`.

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
- `assay.mutation.run_mutation` consumes P22's immutable effective plan, required max-mutant ceiling, repository/project identities, and shared deadline. Do not call the pre-P22 `Lane`-re-resolution shape.
- **A-145 was superseded structurally by A-156/A-161:** targets remain repo-top-relative, but each execution is now a committed-object snapshot of the WHOLE repository, with the project at its original prefix. A real Go module under `gate/go/**` and the disposable srdm copy are the required nested cases.
- `assay.mutation.resolve_mutation_targets` (new in P18) filters candidates through `adapter.source_globs`/`excluded_dir_names`/`is_test_path`. P18 did not change `GoAdapter`'s own values for these (from P08) -- confirm they are still right now that R2 target scoping reads them.
- **Your O3 is P18's own O4 migrated to v4, in Go.** `tests/test_standalone.py`'s installed-wheel R2 comparisons are the working shape (complete document `==`, tree hashes before/after, PATH declared not ambient). A compiler rejection is a normally-started nonzero and therefore killed (A-158); a crashed entry requires a real command-boundary failure, never output interpretation.

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

This package adds Go mutation only. It must not change Python mutants, the v4 R2 payload/schema, canary, or shared gate declarations. P26's image may be extended with the helper without editing `nyxloom.toml`, so no merge mutex is needed.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

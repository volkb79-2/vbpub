---
schema_version: 1
id: assay-P28-real-go-r1-srdm-qualification
project: assay
title: "Installed Assay agrees with an independent evaluator on the real srdm module"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P27-go-gate-adapter-resolution]
session: resume:assay-go
scope:
  touch: ["gate/go/**", "tests/fixtures/go/srdm/**", "tests/test_standalone.py", "tests/test_go_qualification.py", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay", "pyproject.toml", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "At one pinned full vbpub commit and one carver-authored child commit, installed Assay runs real srdm go test coverage and emits a complete v4 R0+R1 artifact from the preserved nested project topology"
    negative: "A tiny-fixture-only or extracted-standalone-module path passes while the real shared-ramdisk-depot-manager prefix fails"
    gate: tester-unified
  - id: O2
    observable: "Installed Assay, the copied srdm tools/covergate binary, and an independent hand manifest agree on common changed executable lines, covered/missing buckets, denominator, percentage, and terminal class"
    negative: "Assay and covergate share an incorrect expectation or disagree on module-prefix/block semantics while a two-witness comparison still passes"
    gate: tester-unified
  - id: O3
    observable: "Comment/doc-only, test-file, partial-body, missing-profile, and 0/0 scenarios are checked at the exact boundary each tool can express; asymmetric exclusions are compared with the hand manifest rather than invented into covergate"
    negative: "An unmeasurable Go file becomes uncovered, or unavailable exclusions are silently equated with known-empty data"
    gate: tester-unified
  - id: O4
    observable: "All source commits, srdm files, shared checkout state, and local project configuration are byte/status-identical before and after qualification"
    negative: "The proof edits/migrates srdm, uses its working tree as scratch, or leaves a profile/cache behind"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "Assay, covergate, and the hand manifest disagree on a shared semantic after inputs are proven identical"
  - "the pinned srdm gate cannot run offline in P27's immutable combined image"
mutexes: []
---

# P28 — real Go R1 srdm qualification

The claim to attack: **Assay's language abstraction survives a real, nested,
multi-package Go project and agrees with an independent implementation on the
semantics both tools actually share.**

## Dispatch contract

- Contract class: **2d — constrained implementation**.
- Required roles: **Sonnet xhigh implementer → Opus xhigh independent reviewer**;
  any three-witness semantic disagreement routes to Sol as a product decision.
- Readiness: **PROVISIONAL until P27 merges.** The controller records the exact
  full vbpub/srdm commit, image ID, wheel hash, selected source replacement, and
  commands. Sol/Opus then freezes the hand manifest and expected artifact.
- Implementer freedom: disposable harness plumbing only. Project identity,
  commits, image, commands, three independent witnesses, semantic intersection,
  and no-adoption/no-edit boundary are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P28-real-go-r1-srdm-qualification`
on branch `feat/assay-P28-real-go-r1-srdm-qualification`.

## Context to read first

1. P27's real Go image, adapter resolution, tiny fixture, and exact expected
   artifacts. This package adds no parser/adapter behavior.
2. P22/P23 committed snapshot and exact-execution contracts. The real project
   stays at `shared-ramdisk-depot-manager/` beneath the repository top.
3. `/workspaces/vbpub/shared-ramdisk-depot-manager/go.mod`, selected `internal/`
   packages, `tools/covergate/{main,profile,evaluate,hascode}.go`, and its
   `nyxloom-trove/GUIDE.md` coverage procedure. Read-only.
4. Decisions A-159/A-162/A-164 and srdm `nyxloom-trove/decisions.md` D-007.
5. The exact JIT carve record containing full OIDs, selected replacement bytes,
   source-line manifest, commands, image ID, and wheel SHA-256.

## Implementation packet (normative)

### Frozen qualification case

The JIT carve selects a current full vbpub commit `B` where srdm's real gate is
green and one ordinary Go source file under a representative `internal/` package.
It supplies exact original/replacement bytes that form a valid child commit `H`
through P22's replacement API. The replacement must create this combined input:

- an executable line changed and covered;
- an executable line changed and missing;
- a changed comment or brace-only line;
- at least one multi-line Go coverage block;
- a nested module/repository prefix; and
- a package set large enough that `./...` crosses more than one package.

The case records `B`, expected `H`, the project prefix, source root, Go module
path, profile path, exact `go test` argv, floor, and hand-calculated line buckets.
No value is “latest”, symbolic `HEAD`, or derived from whichever checkout the
test happens to run in.

### Three independent witnesses

1. **Producer:** run P24's exact versioned installed wheel through `assay run`
   in P27's pinned image against the P22/P23 snapshot at `H` with base `B`.
2. **Existing independent implementation:** compile/run the copied commit's
   `tools/covergate` against the same profile, base, head, prefix, and floor.
   Do not import Assay or read its expected artifact.
3. **Carver manifest:** a literal checked-in mapping from repo-relative path and
   line to `covered`, `missing`, or `non-executable`, authored from source and
   the pinned Go profile semantics—not generated by either product.

Compare only the shared semantic intersection: changed executable set,
covered/missing set, denominator/numerator, exact percentage arithmetic, and
terminal pass/fail at the same floor. Assay's explicit exclusion capability and
integrity terminals have no covergate equivalent: check those against the hand
manifest/artifact contract, never coerce the tools to appear equal.

### Scenario matrix

| scenario | Assay | covergate | hand manifest |
|---|---|---|---|
| covered + missing changed bodies | exact shared buckets/result | same | same |
| comment/doc/brace-only change | not executable | same where expressible | authoritative |
| `doc.go`/test-only file with no body | not phantom-uncovered | same | authoritative |
| missing/empty profile | honest non-measurement | independent error/refusal | expected absence |
| shared executable denominator 0 | explicit 0/0 meaning/result | same terminal semantics | empty set |
| exclusions unavailable | capability unavailable | no assertion | authoritative asymmetry |

Any shared-semantic disagreement is `BLOCKED` with all three raw outputs. The
implementer may not normalize, weaken the manifest, or change Assay/covergate.

### Prepared proof and traceability

The carver commits the pinned case record, exact patch, source manifest, expected
v4 artifact, and hashes of selected srdm source plus full source `git status`
before/after. The reviewer adds a combined attack that changes module prefix and
uses a multi-line partially covered block in the same case.

| work | owner | oracle | controlled break |
|---|---|---|---|
| pinned real execution | qualification harness | O1 | extract module to repo root or use fake profile |
| three-witness comparison | harness/goldens | O2 | delete manifest witness or alter one bucket |
| asymmetric cases | scenario fixtures | O3 | equate unavailable exclusions with empty |
| immutability/cleanup | harness | O4 | write profile/cache into shared checkout |

## Work

1. Add the pinned srdm qualification case and validate all recorded OIDs/hashes
   before any process starts.
2. Materialize B/H through P22, run the exact P27 Go command through installed
   Assay, and retain the complete v4 artifact and process ledger.
3. Run copied covergate and compare both outputs independently with the literal
   manifest under the fixed intersection/asymmetry rules.
4. Add the scenario matrix without editing srdm or production Assay code.
5. Hash/status the shared checkout and selected source before/after; make caches,
   profiles, wheels, and temporary repositories private and cleaned.
6. Run the real offline `tester-unified` gate and record raw three-witness output
   plus controlled-break counts.

## Test constraints copied from AUTHORING.md §3b

- No sleeps or elapsed-time verdicts; real command timeouts are hang failsafes.
- Fresh disposable repositories/caches per test; restore all environment.
- Assert complete artifacts, exact line sets, raw independent output, and source
  immutability—not “both commands exited the same” or private call counts.
- No weakened assertions or coverage-evasion pragmas.
- No network/current-clock/current-HEAD input; every image, commit, wheel, path,
  command, and expected value is pinned.

## Scope / forbid

This is validation, not adoption or remediation. It changes no Assay production
code, shared gate image, srdm source/config/trove, or srdm's covergate. A later
srdm-owned handoff may adopt Assay only after this proof passes.

## BLOCKED rule

If a named contract cannot be met as specified, a shared semantic disagrees, or
scope requires a forbidden file, STOP — write `BLOCKED: <reason>` with all three
raw outputs to the LOG, commit, and exit. Do not improvise a workaround.

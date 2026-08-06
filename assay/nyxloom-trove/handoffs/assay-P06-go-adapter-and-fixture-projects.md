---
schema_version: 1
id: assay-P06-go-adapter-and-fixture-projects
project: assay
title: "The Go adapter, the go-cover parser, and the hello-world fixture projects with expected verdicts"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P05-statement-span-attribution-unclassified]
session: fresh
scope:
  touch:
    - "src/assay/adapters/go.py"
    - "src/assay/coverage/go_cover.py"
    - "tests/fixtures/**"
    - "tests/**"
    - "tools/regenerate-fixtures.sh"
  forbid:
    - "src/assay/evaluate.py"
    - "src/assay/adapters/protocol.py"
    - "src/assay/coverage/registry.py"
oracles:
  - id: O1
    observable: "`git diff --name-only` for this package touches NO file under `src/assay/evaluate.py`, `src/assay/adapters/protocol.py` or `src/assay/coverage/registry.py` -- a second adapter is purely additive"
    negative: "adding the second language required reshaping the core, which means the protocol was a guess and the boundary is not real (P90 O3)"
    gate: tester-unified
  - id: O2
    observable: "the full test suite passes with NO Go toolchain installed (`which go` empty), driven entirely by committed pre-generated cover profiles and committed .go source"
    negative: "the suite needs a toolchain this devcontainer does not have, so the Go path is untested wherever it actually runs"
    gate: tester-unified
  - id: O3
    observable: "the go-cover parser expands each block to every line in its range, and where two blocks overlap (`} else {`) EXECUTED wins; a profile line with a colon in the path parses via the LAST colon; a negative count or an end-before-start block raises UNREADABLE_ARTIFACT"
    negative: "a line whose code demonstrably ran is reported uncovered because an overlapping never-taken block claimed it"
    gate: tester-unified
  - id: O4
    observable: "a .go file declaring no function bodies lands in `no_code` and leaves the ratio entirely; a file that cannot be read or parsed returns True (has code) -- the strict reading"
    negative: "comment-only doc.go files are flagged as uncovered -- srdm's own first run produced exactly this on 94 lines across four files"
    gate: tester-unified
  - id: O5
    observable: "each fixture project carries an expected verdict artifact, and the suite asserts the FULL artifact matches -- covering all six outcomes and every reason_code at least once; the assertions are made by pytest, never by assay"
    negative: "assay validates its own verdicts, so a universal-PASS bug passes its own gate"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the Go adapter's has_executable_code cannot be implemented without shelling out; declare the tool in external_tools and report, do not ship a heuristic that returns False when unsure"
mutexes: []
---

# P06 — the Go adapter and the fixture projects

The claim to attack: **is the adapter boundary real?** O1 is the whole package:
adding a second language must touch no core file.

Note this discharges the *adapter* proof, not srdm's migration — those are
separate (A-C04, A-O04).

## Context to read first

1. `/workspaces/vbpub/shared-ramdisk-depot-manager/tools/covergate/profile.go` —
   the block→line expansion and the executed-wins rule (O3).
2. `.../covergate/hascode.go` — the strict reading and why it is strict (O4).
3. `docs/DESIGN-GUIDE.md` §10 (fixtures and the toolchain constraint), §11.

## Work

1. `coverage/go_cover.py` — registers `go-cover` with the P03 registry.
   `FileCoverage.excluded` is **`None`**: the format cannot express exclusions.
2. `adapters/go.py` — `is_test_path` (`*_test.go`), `has_executable_code`,
   `normalize_coverage_key` (strip the module prefix, srdm's `stripModulePrefix`),
   `requires_span_attribution = False`, `generate_mutants -> UNSUPPORTED`.
3. `tests/fixtures/` — a hello-world Python project and a hello-world Go project.
   Committed: real source plus **pre-generated** coverage artifacts (coverage.py
   JSON, cobertura XML, `go test -coverprofile` output) and an expected verdict
   per scenario. Git-state scenarios materialise in `tmp_path`.
4. `tools/regenerate-fixtures.sh` — documented regeneration for a host with Go,
   pinned to `tester-unified-go`'s version (A-043), so the profiles are
   reproducible rather than mystery bytes.

## Scenario coverage required

One fixture per outcome and per reason_code: PASS, PASS-at-0/0-with-considered,
FAIL (uncovered / excluded / unclassified), NO_MEASUREMENT (dirty / base-is-HEAD
/ empty-coverage), ERROR (format mismatch / unreadable / bad lane config),
BUDGET_EXCEEDED and INCONCLUSIVE arrive with P07 and P10 respectively.

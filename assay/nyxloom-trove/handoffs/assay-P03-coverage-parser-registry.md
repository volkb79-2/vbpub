---
schema_version: 1
id: assay-P03-coverage-parser-registry
project: assay
title: "Coverage parser registry keyed by FORMAT: coverage.py JSON, cobertura, sniff cross-check"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P01a-skeleton-and-lane-config]
session: fresh
scope:
  touch:
    - "src/assay/coverage/__init__.py"
    - "src/assay/coverage/registry.py"
    - "src/assay/coverage/coverage_py_json.py"
    - "src/assay/coverage/cobertura.py"
    - "tests/**"
  forbid:
    - "src/assay/adapters/**"
    - "src/assay/evaluate.py"
oracles:
  - id: O1
    observable: "the SAME fixture project judged from its coverage.py JSON and from its cobertura XML yields byte-identical executed/missing line sets per file"
    negative: "format-specific parsing skew makes the verdict depend on which report flag the lane happened to pass"
    gate: tester-unified
  - id: O2
    observable: "`FileCoverage.excluded` is a frozenset for coverage.py JSON and `None` for a format that cannot express exclusions; a lane with `allow_excluded = false` against an excluded-blind format reports the check as not-applicable rather than as 'verified, zero excluded'"
    negative: "a Go or lcov lane reports '0 changed lines excluded by pragma' as if it had checked, when the format cannot say"
    gate: tester-unified
  - id: O3
    observable: "a lane declaring `coverage-py-json` given a cobertura artifact yields exit 2 / ERROR / FORMAT_MISMATCH; sniffing recognises `mode:` (go-cover), a top-level `files` object (coverage.py JSON), `SF:` (lcov) and `<coverage` (cobertura)"
    negative: "a lane whose argv changed report format is silently mis-parsed or partially parsed instead of failing loudly"
    gate: tester-unified
  - id: O6
    observable: "A-068's debt is paid: a lane declaring a `judge.coverage.format` the registry does not know fails with BAD_LANE_CONFIG naming the formats it DOES know, and a lane declaring a known one loads"
    negative: "P01a's loader accepts any non-empty format string (deliberately -- the vocabulary is the registry's, not the loader's), so until this cross-check exists a typo'd format survives config load and surfaces as a parse failure later, or not at all"
    gate: tester-unified
  - id: O4
    observable: "a well-formed artifact containing zero files yields exit 3 / NO_MEASUREMENT / EMPTY_COVERAGE"
    negative: "every changed file reads as unmeasured, rendering 0% FAIL -- a wrong diagnosis that says 'add tests' when it means 'your --cov path is broken'"
    gate: tester-unified
  - id: O5
    observable: "a coverage record whose `executed_lines`/`missing_lines` are absent, non-list, or contain non-ints raises ERROR / UNREADABLE_ARTIFACT"
    negative: "malformed data silently yields a green verdict -- the check only topos has (`_validate_cov_record`)"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "cobertura's line/branch model cannot be reduced to the three-way executed/missing/excluded classification without inventing a rule not in decisions.md"
mutexes: []
---

# P03 — coverage parser registry

The claim to attack: **is coverage format independent of language?** If this
package needs to know what language it is parsing, A-006 was wrong.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §11 "Where language-specificity actually lives" —
   why format and language are separate axes.
2. `/workspaces/vbpub/topos/tools/coverage_gate.py` `_validate_cov_record` —
   the only copy that refuses malformed data (O5).
3. `/workspaces/netcup-api-filter/coverage.xml` — a real cobertura artifact from
   coverage.py 7.15.2, and the reason cobertura is in v1 rather than v2.

## Work

1. `registry.py` — format name -> parser, plus `sniff(raw) -> format | None` and
   the declared-vs-sniffed cross-check (A-007).
2. `coverage_py_json.py`, `cobertura.py` — both producing
   `FileCoverage(executed, missing, excluded | None)`.
3. The EMPTY_COVERAGE guard (A-035), which completes P02's measurability set.

`go-cover` lands in P06 with the Go adapter; `lcov` and Istanbul are deferred
(A-O03). Sniffing must still RECOGNISE lcov so a lane declaring it fails with
"format not supported" rather than "unknown artifact".

## Out of scope

Any `.py`/`.go` awareness. This package must not import `ast` or glob for source
files.

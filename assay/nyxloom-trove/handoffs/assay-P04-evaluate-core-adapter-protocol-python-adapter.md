---
schema_version: 1
id: assay-P04-evaluate-core-adapter-protocol-python-adapter
project: assay
title: "The evaluation core (four-way union), the LanguageAdapter protocol, and the Python adapter"
tier: sonnet5-high
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on:
  - assay-P02-changed-lines-base-resolution-measurability
  - assay-P03-coverage-parser-registry
session: fresh
scope:
  touch:
    - "src/assay/evaluate.py"
    - "src/assay/adapters/__init__.py"
    - "src/assay/adapters/protocol.py"
    - "src/assay/adapters/python.py"
    - "tests/**"
  forbid:
    - "src/assay/coverage/**"
oracles:
  - id: O1
    observable: "`grep -rn 'import ast\\|rglob\\|\\*\\.py\\|\\.py\\\"' src/assay/evaluate.py` returns nothing, and a test registers a FAKE adapter (a made-up extension and test-path rule) that drives evaluation end to end without editing the core"
    negative: "`.py` globs and `ast` imports remain in the core, so a second language consumer forks instead of adding an adapter -- which is how four copies happened"
    gate: tester-unified
  - id: O2
    observable: "each of the four copies' distinctive behaviours has a test that FAILS if it is removed: multi-prefix source roots and the test-path skip (dstdns); directory-boundary prefix matching, i.e. a root `src/topos` must not match `src/topos_evil` (topos); the NoCode bucket and the `considered` count (srdm); the excluded-lines bucket with `allow_excluded` (nyxloom/dstdns)"
    negative: "the extraction takes the intersection, silently downgrading whichever consumer contributed the behaviour"
    gate: tester-unified
  - id: O3
    observable: "a changed non-source file under a source root (a .json fixture, a .toml default) is IGNORED, while a changed source file absent from the coverage report counts as uncovered and is listed under files_missing_coverage"
    negative: "unmeasur*able* is treated as unmeasur*ed*, producing a verdict no test could ever clear"
    gate: tester-unified
  - id: O4
    observable: "a changed line carrying `pragma: no cover` lands in the `excluded` bucket and FAILS with reason_code EXCLUDED_LINES unless the lane declares `allow_excluded = true`; a pre-existing pragma on an UNCHANGED line is untouched"
    negative: "a change opts out of the very gate it must pass, and because exclusions shrink the denominator the reported percentage goes UP"
    gate: tester-unified
  - id: O5
    observable: "a legitimate 0/0 verdict renders with its `considered` count explaining why the denominator is empty, and is structurally distinguishable in the artifact from a NO_MEASUREMENT verdict, which carries no coverage block at all"
    negative: "'0/0 covered (100.0%)' reads identically for 'nothing to cover' and 'measured nothing' -- the bug this whole project exists to remove"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the union exceeds ~400 changed executable lines -- split and report, per the CR-07a carving lesson"
  - "a fifth behaviour is found in one of the four copies that is not listed in DESIGN-GUIDE.md 2"
mutexes: []
---

# P04 — the evaluation core, the adapter protocol, the Python adapter

The claim to attack: **did the union land, and is the core language-free?**

## Context to read first

1. `docs/DESIGN-GUIDE.md` §2 (the four-way union table, incl. the two corrections)
   and §11 (adapter surface).
2. All four implementations, side by side. They differ; the differences are the
   specification:
   - `/workspaces/dstdns/scripts/coverage_gate.py` — multi-prefix, `_is_test_path`
   - `/workspaces/vbpub/topos/tools/coverage_gate.py` — `_rel_to_source` boundary
   - `/workspaces/vbpub/shared-ramdisk-depot-manager/tools/covergate/evaluate.go` —
     `NoCode`, `Considered`, and an `Evaluate` signature that is already the
     adapter seam discovered independently
   - `/workspaces/vbpub/nyxloom/src/nyxloom/coverage_gate.py` — the excluded bucket

## Work

1. `adapters/protocol.py` — the surface in DESIGN-GUIDE §11. `inject_*` are PURE
   (A-010): `(text) -> (text, description)`, never writing the file.
2. `adapters/python.py` — `is_test_path` (a `tests/` segment, `test_*.py`,
   `conftest.py`), `has_executable_code`, `normalize_coverage_key`,
   `requires_span_attribution = True`. `statement_spans` and the `inject_*`
   bodies may be stubs here; P05 and P08 fill them.
3. `evaluate.py` — the pure heart: plain data in, `Verdict` out. No git, no
   filesystem, no adapter instantiation it did not receive.

## Out of scope

Multi-line-statement attribution and the `unclassified` bucket (P05) — a changed
line absent from both buckets simply vanishes here, exactly as pre-P80 dstdns
behaved. P05's O1 negative check depends on that being the case.

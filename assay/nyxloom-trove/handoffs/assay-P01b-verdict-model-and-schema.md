---
schema_version: 1
id: assay-P01b-verdict-model-and-schema
project: assay
title: "The verdict model and a JSON Schema that REJECTS a malformed verdict"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P01a-skeleton-and-lane-config]
session: fresh
scope:
  touch:
    - "src/assay/verdict.py"
    - "src/assay/schemas/verdict.schema.json"
    - "pyproject.toml"
    - "tests/**"
    - "nyxloom-trove/reports/**"
  forbid:
    - "src/assay/config.py"
    - "src/assay/cli.py"
    - "nyxloom-trove/nyxloom.toml"
    - "nyxloom-trove/decisions.md"
    - "docs/DESIGN-GUIDE.md"
oracles:
  - id: O1
    observable: "one Verdict per outcome (PASS / FAIL / ERROR / NO_MEASUREMENT / BUDGET_EXCEEDED / INCONCLUSIVE) serialises to JSON and validates against `src/assay/schemas/verdict.schema.json` LOADED AS A FILE -- never from a Python dict"
    negative: "the schema is validated from an in-process object, so A-029's claim that a consumer validates without importing assay is unproven and the shipped file may not even parse"
    gate: tester-unified
  - id: O2
    observable: "the schema REJECTS a NO_MEASUREMENT verdict that carries a coverage block, and REJECTS any non-PASS verdict missing `reason_code`; both rejections are asserted as validation FAILURES, not as absent fields"
    negative: "the schema accepts everything, so 'six verdicts validate' is green against a schema that requires nothing -- and a NO_MEASUREMENT artifact carrying `pct: 100.0` rebuilds the exact ambiguity this project exists to remove, one layer up (A-025)"
    gate: tester-unified
  - id: O3
    observable: "`reason_code` is drawn from the CLOSED enumeration in DESIGN-GUIDE 6 (A-050, A-051): each listed code validates against its own outcome, a code valid for a DIFFERENT outcome is rejected, and PASS carrying a `reason_code` at all is rejected"
    negative: "reason_code is a free string, so a typo'd or invented code passes validation and a consumer switching on it silently falls through"
    gate: tester-unified
  - id: O4
    observable: "a `claims[]` entry carries the envelope `source` (computed|adjudicated|attested), `status`, `rigor` and `verified_by_assay`; a verdict whose claims do not cover every rigor level the lane declared is REJECTED; and an attested claim with `verified_by_assay: true` is rejected"
    negative: "'R2 was declared but rendered no judgement' becomes indistinguishable from 'R2 was never declared' (A-024), or attested evidence can be marked verified, which is worse than omitting it because it is now on the record (A-033)"
    gate: tester-unified
  - id: O5
    observable: "`src/assay/schemas/verdict.schema.json` is present INSIDE an installed wheel/venv (proven by installing into a scratch venv and resolving the file from the installed package), and `schema_version` is an integer field on every verdict"
    negative: "the schema is not declared as package data, so it exists in the source tree and vanishes on install -- silently breaking A-029 for every consumer while every in-tree test stays green"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "expressing a rejection requires a JSON Schema feature beyond draft 2020-12 if/then/else + additionalProperties -- report rather than weakening the oracle to an acceptance test"
  - "a decision id in decisions.md is unimplementable as written -- name the id and STOP"
mutexes: []
---

# P01b — the verdict model and its schema

The claim to attack: **does the schema REJECT a malformed verdict?** Not "can it
describe a good one" — every schema can do that, including one that requires
nothing.

Split from the original P01 (A-059). This half is where the package's hollowness
risk concentrates, which is why it is carved separately: an acceptance-only test
suite here would be green, useless, and indistinguishable from a real one at the
gate.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §6 in full — the three channels, the six outcomes, the
   closed `reason_code` table, the rollup precedence, and above all the
   "guard is what is absent" argument.
2. `nyxloom-trove/decisions.md` A-021 through A-029, plus A-050, A-051, A-055.
3. `/workspaces/vbpub/nyxloom/src/nyxloom/types.py` `GateResult` — the six
   REQUIRED fields plus optional `environment`. The artifact must be a superset
   so nyxloom can consume it by reading those keys (A-029). Note the DESIGN-GUIDE
   says "six keys and may read a seventh" — do not omit `environment`.

## Work

1. `src/assay/verdict.py` — the outcome enum, the closed `reason_code` enum, the
   claim **envelope** (A-055: `source`, `status`, `rigor`, `verified_by_assay`;
   kind-specific payloads are P09's, not yours), the rollup precedence of A-023,
   and the artifact fields of DESIGN-GUIDE §6 including `argv_declared` /
   `argv_appended` / `argv_effective` and `env_declared` / `env_effective`.
2. `src/assay/schemas/verdict.schema.json` — draft 2020-12, shipped as **data**.
   Add `[tool.setuptools.package-data]` so it survives installation (O5).
3. Tests. The centre of gravity is the REJECTION cases, not the acceptance ones.

## The rule this package exists to enforce

`PASS` omits `reason_code` entirely rather than carrying null (A-051), and a
`NO_MEASUREMENT` verdict has **no coverage block at all** — omitted, not zeroed
(A-025). A consumer that reads `pct` and ignores `outcome` must find nothing to
read. Encode both as schema constraints, not as conventions in a docstring.

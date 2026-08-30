# W4 — the verdict schema v8 successors (B018 / B019 / B035)

Captured 2026-08-30 on branch `feature/assay-b018-b019-b035-v8-synergy`, as the
evidence behind **A-327** (judge provenance), **A-328** (gate-request-supplied
comparison base) and **A-329** (`judgment.r2` witnesses its own judging scope).

This directory is the fourth generation of the same one-for-one-successor
discipline W1 and W2 already followed:

| generation | frozen schema | acceptance suite | P25 templates |
|---|---|---|---|
| P33 | `verdict.schema.v5.json` | `test_acceptance_v5.py` | `p25-*-v5-template.json` |
| W1 | `verdict.schema.v6.json` | `test_acceptance_v6.py` | `p25-*-v6-template.json` |
| W2 | `verdict.schema.v7.json` | `test_acceptance_v7.py` | `p25-*-v7-template.json` |
| **W4** | **`verdict.schema.v8.json`** | **`test_acceptance_v8.py`** | **`p25-*-v8-template.json`** |

W3 is not skipped by accident: it is a different kind of asset (the A-279
ordering pair plus the dstdns SQL witness), and its number was already taken
when this generation was cut. The `W<n>` names are wave identities, not schema
versions.

**Every earlier generation stays frozen and unedited.** Each is the historical
record of what the project actually proved under the contract that existed when
it proved it; rewriting one to v8 would claim a document was accepted against a
contract that did not exist at the time. `tools/tester-unified-gate.sh` runs
W1's and W2's suites for COLLECTION only and then proves the hard cut against
their `expected/` documents with a raw verifier probe — the same move W1 got
when v7 landed.

## What is here

| file | what it is |
|---|---|
| `verdict.schema.v8.json` | a byte copy of `src/assay/schemas/verdict.schema.json` at the v8 cut |
| `test_acceptance_v8.py` | the locked v8 acceptance suite (40 nodes), run from the installed wheel by the registered gate |
| `expected/*-v8-template.json` | six committed v8 documents, the W2 v7 six migrated in place |

### How the six templates were migrated

Mechanically, and stated here so a reviewer can re-derive it rather than trust
it:

1. `schema_version` 7 → 8, in all six;
2. `judgment.r2.mode = "changed_lines"` added to the two that carry an `r2`
   (`sql-r2-v8-template.json`, `ca4-all-equivalent-v8-template.json`).

Nothing else changed. `changed_lines` is not a choice among two possibilities
for those two documents: each records a `judgment.resolved.base`, and under v8
a whole-target `r2` beside a base is refused — so the migration had exactly one
legal value, and the value it had to be is the scope those documents always
described.

`judge_provenance` is deliberately **absent** from all six. It is optional, and
these are hand-migrated documents with no build behind them; inventing a digest
for a template is precisely the laundering B018 exists to prevent. Its accepted
and refused shapes are exercised by `test_acceptance_v8.py` against constructed
documents, and its REAL value is measured against genuinely built artifacts
elsewhere: a wheel in `tests/test_standalone.py`, a zipapp in
`tests/test_distribution_build_release.py`, and the gate's own run-venv wheel in
`gate/python/qualify_topos.py` plus `tools/tester-unified-gate.sh`'s
`require_emitted_judge_provenance`.

## The two guards this directory carries forward

* `test_shipped_schema_is_byte_identical_to_the_locked_v8_asset` — the check
  this project has been bitten by twice. Whatever moves in the shipped schema
  must move in the copy here, in the same commit.
* `test_every_earlier_frozen_template_is_rejected_under_v8` — A-170's hard cut,
  asserted over W1's six v6 documents AND W2's six v7 documents at once, each
  producing exactly one diagnostic naming the version and nothing downstream of
  it.

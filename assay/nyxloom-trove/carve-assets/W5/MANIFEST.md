# W5 — the verdict schema v9 successors (B045 / B046 / B043 / B041(b))

Captured 2026-08-31 on branch `feature/assay-wave-b-producer`, as the evidence
behind **A-359** (one bump for four items, cut before the features that fill
it), **A-360** (`judgment.r2.producer` and its two-directional fork),
**A-362** (the open `stryker:` namespace beside three closed enums),
**A-363** (`cwd_declared` is not lane-resolved) and **A-366**
(`snapshot_policy.link_paths`).

This directory is the fifth generation of the same one-for-one-successor
discipline W1, W2 and W4 already followed:

| generation | frozen schema | acceptance suite | P25 templates |
|---|---|---|---|
| P33 | `verdict.schema.v5.json` | `test_acceptance_v5.py` | `p25-*-v5-template.json` |
| W1 | `verdict.schema.v6.json` | `test_acceptance_v6.py` | `p25-*-v6-template.json` |
| W2 | `verdict.schema.v7.json` | `test_acceptance_v7.py` | `p25-*-v7-template.json` |
| W4 | `verdict.schema.v8.json` | `test_acceptance_v8.py` | `p25-*-v8-template.json` |
| **W5** | **`verdict.schema.v9.json`** | **`test_acceptance_v9.py`** | **`p25-*-v9-template.json`** |

W3 is not skipped by accident: it is a different kind of asset (the A-279
ordering pair plus the dstdns SQL witness), and its number was already taken
when W4 was cut. The `W<n>` names are wave identities, not schema versions.

**Every earlier generation stays frozen and unedited.** Each is the historical
record of what the project actually proved under the contract that existed when
it proved it; rewriting one to v9 would claim a document was accepted against a
contract that did not exist at the time. `tools/tester-unified-gate.sh` runs
W1's, W2's and now **W4's** suites for COLLECTION only and then proves the hard
cut against their `expected/` documents with a raw verifier probe — W4 receives
here exactly the demotion W2 received at the v8 cut, and W1 at v7.

*(The one asset that is deliberately NOT frozen is `W3/expected/
dstdns-sql-r2-v6-witness.json`. Its filename still says `v6`, but it is a LIVE
witness that `gate/python/qualify_dstdns_sql.py` regenerates and compares
end-to-end, so it tracks the current schema and was migrated with this cut. Do
not mistake it for a frozen generation.)*

## What is here

| file | what it is |
|---|---|
| `verdict.schema.v9.json` | a byte copy of `src/assay/schemas/verdict.schema.json` at the v9 cut, verified with `cmp`, not trusted from a paste |
| `test_acceptance_v9.py` | the locked v9 acceptance suite (47 nodes), run from the installed wheel by the registered gate |
| `expected/*-v9-template.json` | **seven** committed v9 documents: the W4 v8 six migrated in place, plus one REAL ingested-R2 verdict added in fix round 1 |
| `expected/ingested-r2-v9-template.json` | a real run over the committed StrykerJS artifact, frozen (see below) |

### How the six MIGRATED templates were migrated

*(The seventh, `ingested-r2-v9-template.json`, was not migrated from anything —
it is a real run, added later; see "The ingested template" below.)*

Mechanically, and stated here so a reviewer can re-derive it rather than trust
it:

1. `schema_version` 8 → 9, in all six;
2. `judgment.r2.producer = "native"` added to the two that carry an `r2`
   (`sql-r2-v9-template.json`, `ca4-all-equivalent-v9-template.json`).

Nothing else changed. `native` is not a choice among two possibilities for
those two documents: each records assay's own `jobs`, `max_mutants` and
`operators`, which v9 FORBIDS under `producer = "ingested"` (A-360) — so the
migration had exactly one legal value, and the value it had to be is the
producer those documents always described.

### The ingested template (added in fix round 1)

**As first cut, no ingested document was frozen here.** The reasoning was
sound at the time and is recorded because it is what changed: B046's runner
path lands *after* this cut (the schema is cut first so B046 has something to
land into — A-359), so at the moment of freezing there was no real
Stryker-driven verdict to freeze, and hand-authoring one would have frozen a
shape no producer had ever emitted. The ingested half was therefore pinned as
*refusals and requirements* over constructed documents — both directions of
the fork, and the "required together" sweep field by field.

**That left a real gap, and fix-round review found it.** Once B046 landed, a
real producer existed and the reason for the absence expired — but the corpus
did not gain the document, so B046's whole new branch (the five conditionally
emitted `judgment.r2` fields, `producer_tool`, the `stryker:` operator
namespace) had ZERO frozen-drift-guard coverage anywhere: the guard covered
the producer fork's native half only, which is how a fork rots.

`expected/ingested-r2-v9-template.json` closes it. It is a **real verdict**,
not an authored one: a real run through the same harness
`tests/test_runner_ingested_r2.py` drives, over the committed StrykerJS
artifact `tests/fixtures/mutation/mutation-report-json.probe-js-stryker.json`
(109 mutants over 6 files), with only `started`/`ended` replaced by the
suite's own `@STARTED@`/`@ENDED@` substitutions. Its outcome is
`FAIL`/`MUTANTS_SURVIVED` — the honest verdict for that artifact (19 Survived
+ 69 NoCoverage), and the reason it is worth freezing: a judged R2 claim with
a real payload behind it. It carries `cwd_declared = "app"`, so B043 is frozen
here too.

It was GENERATED by running the producer, for `verdict.schema.v9.json`'s own
reason one asset over: the value of a frozen artifact is that it is what the
producer actually emits, and a hand-transcribed approximation of 48 KB of
verdict would be precisely the drift the corpus exists to catch.

`judge_provenance` is deliberately **absent** from all six, unchanged from W4:
it is optional, and these are hand-migrated documents with no build behind
them; inventing a digest for a template is precisely the laundering B018
exists to prevent. It is absent from the seventh for a different and equally
honest reason: that document IS a real run, and the run that produced it was
an in-tree pytest invocation with no installed-wheel provenance to record, so
the field is omitted rather than filled with the source tree's own identity.

## The guards this directory carries forward

* `test_shipped_schema_is_byte_identical_to_the_locked_v9_asset` — the check
  this project has been bitten by twice. Whatever moves in the shipped schema
  must move in the copy here, in the same commit.
* `test_every_earlier_frozen_template_is_rejected_under_v9` — A-170's hard cut,
  asserted over W1's six v6 documents, W2's six v7 documents AND W4's six v8
  documents at once, each producing exactly one diagnostic naming the version
  and nothing downstream of it.
* `test_the_v8_refusal_is_worded_exactly_as_the_v7_one_was` — the differential
  the wave dispatch asks for by name: v8 is refused at v9 in the same SHAPE v7
  is, so the hard cut is one rule rather than a special case for whichever
  version happened to be previous.
* `test_the_locked_schemas_ingested_pattern_is_the_modules_own_source_string`
  — new at this generation, and the guard that makes an OPEN branch safe to
  ship beside three closed ones: the frozen schema and `assay.vocabulary` must
  hold one string, so a second ingested namespace cannot be added to one and
  forgotten in the other.

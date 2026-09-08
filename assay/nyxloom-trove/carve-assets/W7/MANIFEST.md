# W7 — the verdict schema v11 successors (B070)

Captured 2026-09-08 on branch `feat/assay-b070-discarded-mutants-2026-09-08`,
as the evidence behind **B070**: `judgment.r2.discarded` stops being an
integer COUNT and becomes a LIST of the mutants an ingested report marked
`CompileError`/`RuntimeError`, on `survived_uncovered`'s own footing.

**One field, one bump, and it is a wire change rather than a defect fix.**
Through v10 the field was DECLARED, NOT VERIFIED by ruling
(B051/DA-D4/DA-R26), and the reason was a missing quantity, not a missing
check: under DA-D4's `listed` semantics a discarded mutant is outside the
document it would have to be derived from — in no bucket, in neither
`candidate_count` nor `total` (which `Mutation._check_arithmetic` FORBADE
from differing outside the limit sentinel), and its line absent from
`lines_without_candidates` because the tool did produce a candidate there. So
`judgment.r2.discarded = 9999` on the frozen 109-mutant document verified
clean (A-437), and every upper bound that would have caught that equally
refused the honest high-discard report the field exists to surface — which is
why DA-R26 rejected one. Listing the mutants supplies the quantity. Revising
the arithmetic rule to admit a fifth disposition is what made it a schema
version rather than a patch.

This directory is the seventh generation of the same one-for-one-successor
discipline W1, W2, W4, W5 and W6 already followed:

| generation | frozen schema | acceptance suite | P25 templates |
|---|---|---|---|
| P33 | `verdict.schema.v5.json` | `test_acceptance_v5.py` | `p25-*-v5-template.json` |
| W1 | `verdict.schema.v6.json` | `test_acceptance_v6.py` | `p25-*-v6-template.json` |
| W2 | `verdict.schema.v7.json` | `test_acceptance_v7.py` | `p25-*-v7-template.json` |
| W4 | `verdict.schema.v8.json` | `test_acceptance_v8.py` | `p25-*-v8-template.json` |
| W5 | `verdict.schema.v9.json` | `test_acceptance_v9.py` | `p25-*-v9-template.json` |
| W6 | `verdict.schema.v10.json` | `test_acceptance_v10.py` | `p25-*-v10-template.json` |
| **W7** | **`verdict.schema.v11.json`** | **`test_acceptance_v11.py`** | **`p25-*-v11-template.json`** |

W3 is not skipped by accident: it is a different kind of asset (the A-279
ordering pair plus the dstdns SQL witness), and its number was already taken
when W4 was cut. The `W<n>` names are wave identities, not schema versions.

**Every earlier generation stays frozen and unedited.** Each is the historical
record of what the project actually proved under the contract that existed
when it proved it; rewriting one to v11 would claim a document was accepted
against a contract that did not exist at the time. **W6 was not touched by
this cut** — `git diff` over `carve-assets/W6/` is empty, and
`test_acceptance_v11.py`'s hard-cut sweep is what proves W6's nine documents
are now REFUSED rather than migrated. `tools/tester-unified-gate.sh` runs
W1's, W2's, W4's, W5's and now **W6's** suites for COLLECTION only and then
proves the hard cut against their `expected/` documents with a raw verifier
probe — W6 receives here exactly the demotion W5 received at the v10 cut, W4
at v9, W2 at v8 and W1 at v7.

*(The one asset that is deliberately NOT frozen is `W3/expected/
dstdns-sql-r2-v6-witness.json`. Its filename still says `v6`, but it is a LIVE
witness that `gate/python/qualify_dstdns_sql.py` regenerates and compares
end-to-end, so it tracks the current schema and was migrated with this cut. Do
not mistake it for a frozen generation.)*

## What is here

| file | what it is |
|---|---|
| `verdict.schema.v11.json` | a byte copy of `src/assay/schemas/verdict.schema.json` at the v11 cut, verified with `cmp`, not trusted from a paste |
| `test_acceptance_v11.py` | the locked v11 acceptance suite (104 nodes), run from the installed wheel by the registered gate |
| `expected/*-v11-template.json` | **ten** committed v11 documents: W6's nine migrated in place, plus one NEW real high-discard verdict this cut introduces |

### How the NINE migrated templates were migrated

Mechanically, and stated here so a reviewer can re-derive it rather than trust
it:

1. `schema_version` 10 → 11, in all nine;
2. in the one INGESTED document (`ingested-r2-v11-template.json`):
   `judgment.r2.discarded` `0` → `[]`.

Nothing else changed, in any of the nine. Two points are worth stating
because their ABSENCE is the interesting part:

* **`[]` is not a choice among possibilities**, exactly as `fail_under =
  100.0` was not at the v10 cut and `producer = "native"` was not at v9. That
  document is a real run over `tests/fixtures/mutation/
  mutation-report-json.probe-js-stryker.json`, which contains no
  `CompileError`/`RuntimeError` mutant at all — 109 mutants, statuses
  `NoCoverage`/`Killed`/`Survived` only — so the count `0` had exactly one
  legal v11 spelling.
* **no `mutation.candidate_count` moved.** The ingested path now writes
  `candidate_count = attempted + discarded`; with nothing discarded that IS
  `attempted`, so the migrated document's `109/109` is unchanged. No NATIVE
  document moved at all: `discarded` is forbidden under `producer = "native"`
  and always was.

### The ONE new template, and why it is a real run

| file | what it is |
|---|---|
| `high-discard-r2-v11-template.json` | a REAL ingested verdict with **40 discarded mutants** out of 88 candidates |

This is DA-D4's original witness clause, waived by DA-R26 while the field was
merely declared and **owed again the moment the field became verified**. It is
the verbatim output of a real run over
`tests/fixtures/mutation/mutation-report-json.probe-js-stryker-typecheck.json`
— a second real StrykerJS 10.0.0 run over the same probe sources with
`@stryker-mutator/typescript-checker` enabled, so the checker type-checks each
mutant and marks the ones that do not compile `CompileError`. Recipe, pinned
versions and the exact `tsconfig`/`stryker.config` are committed in
`tests/fixtures/mutation/probe-js-stryker-typecheck/` and recorded in that
directory's `PROVENANCE.md`. Only `started`/`ended` are substituted, exactly
as for `ingested-r2-v11-template.json`.

**It is the CONTROL, and the control is the point.** A bound that refuses an
inflated `discarded` is worth nothing on its own — `discarded <= total` would
do that, and DA-R26 rejected it (route 3) precisely because it refuses the
honest high-discard report just as readily. This document is the proof that
v11 shipped a re-derivation and not a clamp: 40 discarded mutants standing
beside 48 attempted, and `verify_document` returns an empty failure list.

Like `ingested-r2-v11-template.json` it carries no `judge_provenance`, for the
same honest reason: the run behind it was an in-tree invocation with no
installed-wheel provenance to record. `assay_version`, `commit` and the paths
inside it are that run's own facts, not chosen values.

The A-437 inflation negatives are **not** frozen as documents. They are built
from these two clean controls at runtime, differentially, exactly as every
other negative in this suite is — which is what keeps `expected/` a directory
of documents that must be ACCEPTED.

## The guards this directory carries forward

* `test_shipped_schema_is_byte_identical_to_the_locked_v11_asset` — the check
  this project has been bitten by twice. Whatever moves in the shipped schema
  must move in the copy here, in the same commit.
* `test_every_earlier_frozen_template_is_rejected_under_v11` — A-170's hard
  cut, asserted over W1's v6 documents, W2's v7, W4's v8, W5's v9 AND W6's
  nine v10 documents at once, each producing exactly one diagnostic naming the
  version and nothing downstream of it.
* `test_the_v10_refusal_is_worded_exactly_as_the_v9_and_v8_ones_are` — the
  differential that keeps the hard cut ONE rule rather than a special case for
  whichever version happened to be previous.
* `test_the_two_layers_agree_about_the_new_codes` — A-182 made mechanical over
  the frozen schema: `assay.errors` states the `(outcome, reason_code)`
  pairing independently, so the two must agree member for member.
* `test_the_floor_is_spelled_exactly_as_judgment_r1s_own` — A-427's own
  argument as an assertion.
* `test_detail_is_bounded_in_bytes_not_merely_in_characters` — the guard that
  makes A-428's deliberate two-bound split safe.

## The guards this generation ADDS

* `test_a_truthful_high_discard_document_is_ACCEPTED` — the control described
  above, and the single test that distinguishes B070's shipped design from the
  clamp DA-R26 rejected.
* `test_the_A437_reproduction_now_refuses_BY_NAME` — the v11 spelling of
  A-437's exact forgery (9999 discarded entries on the 109-mutant document),
  refused with a message that names both the list's length and the payload's
  own residual.
* `test_the_inflation_is_caught_on_the_HIGH_DISCARD_document_too` and
  `test_deleting_a_discarded_mutant_is_refused_in_the_same_words` — the
  residual is an EQUALITY, so both directions are caught: a 41st mutant added
  to a 40-discard document, and one removed from it.
* `test_shrinking_candidate_count_to_match_a_forged_list_is_refused` — the
  third corner, closed by `candidate_count >= total` in the model.
* `test_a_discarded_mutant_may_not_also_be_in_a_bucket` and
  `test_a_discarded_mutants_line_may_not_be_reported_as_barren` — the two
  re-derivations beside the arithmetic.
* `test_the_locked_v11_schema_types_discarded_as_a_mutant_outcome_array` and
  `test_the_locked_v11_schema_still_forks_discarded_on_the_producer` — the
  reshape asserted against the LOCKED artifact, including the half a reshape
  is most likely to lose: `discarded` is still REQUIRED under `ingested` and
  FORBIDDEN under `native`.
* `test_the_arithmetic_rule_admits_the_residual_only_where_it_is_attributed` —
  the model half, asserted directly because no document can exhibit it: a bare
  `Mutation` now ACCEPTS a residual it cannot attribute, and the layer that
  can see both objects is what refuses an unattributed one.

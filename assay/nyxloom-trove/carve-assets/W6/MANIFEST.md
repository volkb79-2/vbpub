# W6 — the verdict schema v10 successors (B050 / B053 / B004 / B007 / F015)

Captured 2026-09-02 on branch `feature/assay-wave-d-v10`, as the evidence
behind **A-427** (`judgment.r2.fail_under`, the floor the document itself
states), **A-428** (`claim.detail`, the refusing sentence on the wire,
declared-not-verified), **A-430** (`PROVENANCE_UNVERIFIED` plus the
`adjudicated ⇒ verified_by_assay: false` narrowing the bump pays for),
**A-432** (an ordered, bounded canary target list with a closed aggregation
and a per-attempt payload) and **A-433 as amended by A-434/DA-R18** (`R4`,
red-first, and `RED_FIRST_UNPROVEN` as a judged `FAIL`).

This directory is the sixth generation of the same one-for-one-successor
discipline W1, W2, W4 and W5 already followed:

| generation | frozen schema | acceptance suite | P25 templates |
|---|---|---|---|
| P33 | `verdict.schema.v5.json` | `test_acceptance_v5.py` | `p25-*-v5-template.json` |
| W1 | `verdict.schema.v6.json` | `test_acceptance_v6.py` | `p25-*-v6-template.json` |
| W2 | `verdict.schema.v7.json` | `test_acceptance_v7.py` | `p25-*-v7-template.json` |
| W4 | `verdict.schema.v8.json` | `test_acceptance_v8.py` | `p25-*-v8-template.json` |
| W5 | `verdict.schema.v9.json` | `test_acceptance_v9.py` | `p25-*-v9-template.json` |
| **W6** | **`verdict.schema.v10.json`** | **`test_acceptance_v10.py`** | **`p25-*-v10-template.json`** |

W3 is not skipped by accident: it is a different kind of asset (the A-279
ordering pair plus the dstdns SQL witness), and its number was already taken
when W4 was cut. The `W<n>` names are wave identities, not schema versions.

**Every earlier generation stays frozen and unedited.** Each is the historical
record of what the project actually proved under the contract that existed when
it proved it; rewriting one to v10 would claim a document was accepted against a
contract that did not exist at the time. **W5 was not touched by this cut** —
`git diff` over `carve-assets/W5/` is empty, and `test_acceptance_v10.py`'s
hard-cut sweep is what proves W5's seven documents are now REFUSED rather than
migrated. `tools/tester-unified-gate.sh` runs W1's, W2's, W4's and now **W5's**
suites for COLLECTION only and then proves the hard cut against their
`expected/` documents with a raw verifier probe — W5 receives here exactly the
demotion W4 received at the v9 cut, W2 at v8, and W1 at v7.

*(The one asset that is deliberately NOT frozen is `W3/expected/
dstdns-sql-r2-v6-witness.json`. Its filename still says `v6`, but it is a LIVE
witness that `gate/python/qualify_dstdns_sql.py` regenerates and compares
end-to-end, so it tracks the current schema and was migrated with this cut. Do
not mistake it for a frozen generation.)*

## What is here

| file | what it is |
|---|---|
| `verdict.schema.v10.json` | a byte copy of `src/assay/schemas/verdict.schema.json` at the v10 cut, verified with `cmp`, not trusted from a paste. **Amended once after the cut, description bytes only** (B051/DA-R26, A-437): `$defs.judgment_r2.properties.discarded`'s `description` gained the declared-not-verified statement. No `type`, `enum`, `required`, bound or fork moved, so this is not a wire change and did not take a second `!` commit; the copy was re-taken with `cp` and re-checked with `cmp` in that same commit, as `test_shipped_schema_is_byte_identical_to_the_locked_v10_asset` requires |
| `test_acceptance_v10.py` | the locked v10 acceptance suite (79 nodes), run from the installed wheel by the registered gate |
| `expected/*-v10-template.json` | **nine** committed v10 documents: W5's seven migrated in place, plus two NEW shapes this cut introduces |

### How the SEVEN migrated templates were migrated

Mechanically, and stated here so a reviewer can re-derive it rather than trust
it:

1. `schema_version` 9 → 10, in all seven;
2. in the one that carries an R3 claim (`ca1-r3-no-base-v10-template.json`):
   `judgment.r3.target` → a one-element `targets` array, and the flat `canary`
   body → `{mechanism, attempts: [...]}` with the run fields moved into the
   single attempt and `disposition: "attempted"` added (B007/A-432). **No
   `aggregation` was added**, and that is the rule rather than an omission:
   with one declared probe `any` and `all` denote the same function, so
   recording one would record a policy the lane never stated;
3. in the one INGESTED document (`ingested-r2-v10-template.json`):
   `judgment.r2.fail_under = 100.0` (B050/A-427).

Nothing else changed. `100.0` is not a choice among possibilities, exactly as
`producer = "native"` was not at the v9 cut: it is the floor the shipped
loader FORCED for every ingested lane that could have produced that document
(`config.py`'s `fail_under != 100.0` refusal, which B050 deletes AFTER this
cut), so the migration had exactly one legal value.

### The two NEW templates, and why a drift guard carries a shape with no producer

Both were **HAND-AUTHORED at the cut**, and this manifest said so plainly
rather than implying a run behind them. Neither shape had a producer at the
moment of the cut, and that was precisely the reason to freeze them: this is
the `MISSING_EXTERNAL_TOOL` reservation pattern (A-013/A-086/A-144) applied to
a document rather than to a reason code — the contract is pinned first, so
the producer lands INTO a shape rather than defining one as it goes. **The
moment each producer exists, its own real output replaces the authored
document here**, exactly as W5's `ingested-r2` template replaced its own
absence once B046's runner path landed.

| file | shape it pins | when it becomes real |
|---|---|---|
| `multi-target-r3-v10-template.json` | `aggregation = "any"`, two declared targets, the first caught and the second recorded `not_attempted`/`short_circuited` | **DONE — B007's loop landed (A-440) and this file is now REAL OUTPUT** |
| `r4-red-first-v10-template.json` | an `R4` claim carrying BOTH recorded outcomes beside `judgment.r4` | when F015's producer lands (phase 3) |

**`multi-target-r3-v10-template.json` is no longer hand-authored** (B007/A-440,
the ONE authorised edit to this directory after the cut, and the promise the
row above made). It is the verbatim stdout of a real run of the shipped
substrate: a throwaway git repository holding `pkg/greet.py`,
`pkg/farewell.py` and a real pytest suite, an `R0`+`R3` lane declaring
`mechanism = "import-break"`, `targets = ["pkg/greet.py", "pkg/farewell.py"]`
and `aggregation = "any"`, run through `assay run canary-multi --file
assay.toml --verdict-json -`. The first probe's transform was caught for
exactly the expected reason (`COMMAND_FAILED`), which answers an `any`
aggregation, so the second is recorded `not_attempted`/`short_circuited` by
the loop itself — nobody typed that entry. Like W5's `ingested-r2` template
(and for the same honest reason) it carries no `judge_provenance`: the run
behind it was an in-tree invocation with no installed-wheel provenance to
record. `assay_version`, `commit`, `started`, `ended` and the interpreter path
inside `argv_declared` are that run's own facts, not chosen values.

The multi-target document exists because W5 learned this lesson about
`producer` in its own fix round and wrote it down: a guard that covers one
branch of a fork is how a fork rots. Without it, the entire plural branch of
`$defs/canary` and `$defs/judgment_r3` — the attempts array, the disposition
fork, the closed `not_attempted_reason` vocabulary and the aggregation
bookkeeping — would ship with zero frozen coverage.

`judge_provenance` is deliberately **absent** from all nine, unchanged from W5:
it is optional, and inventing a digest for a template is precisely the
laundering B018 exists to prevent. The **two** documents that ARE real runs
(`ingested-r2-v10-template.json`, inherited from W5, and
`multi-target-r3-v10-template.json` since B007/A-440) omit it for their own
honest reason — each run behind them was an in-tree invocation with no
installed-wheel provenance to record.

## The guards this directory carries forward

* `test_shipped_schema_is_byte_identical_to_the_locked_v10_asset` — the check
  this project has been bitten by twice. Whatever moves in the shipped schema
  must move in the copy here, in the same commit.
* `test_every_earlier_frozen_template_is_rejected_under_v10` — A-170's hard
  cut, asserted over W1's v6 documents, W2's v7 documents, W4's v8 documents
  AND W5's seven v9 documents at once, each producing exactly one diagnostic
  naming the version and nothing downstream of it.
* `test_the_v9_refusal_is_worded_exactly_as_the_v8_and_v7_ones_are` — the
  differential that keeps the hard cut ONE rule rather than a special case for
  whichever version happened to be previous.
* `test_the_two_layers_agree_about_the_new_codes` — A-182 made mechanical over
  the frozen schema: `assay.errors` states the `(outcome, reason_code)`
  pairing independently, so the two must agree member for member, and a code
  added to one and forgotten in the other is a red test here.
* `test_the_floor_is_spelled_exactly_as_judgment_r1s_own` — A-427's own
  argument as an assertion: two spellings of one policy number is how they
  drift, so `judgment.r1.fail_under` and `judgment.r2.fail_under` must be
  byte-identical apart from their descriptions.
* `test_detail_is_bounded_in_bytes_not_merely_in_characters` — the guard that
  makes A-428's deliberate two-bound split safe: JSON Schema counts
  characters, the ruling counts bytes, and a 2048-character string of 3-byte
  codepoints is exactly the document that would slip through the first bound
  and must be caught by the second.

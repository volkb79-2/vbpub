# assay Wave 3 (B070) — implementer LOG

Branch: `feat/assay-b070-discarded-mutants-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b070-discarded-mutants`
Wave prompt: `nyxloom-trove/WAVE-PROMPT-2026-09-08-b070-discarded-mutants.md`
Backlog item: `nyxloom-trove/4-backlog.md` § **B070**
Schema cut: **`VERDICT_SCHEMA_VERSION` 10 → 11**, frozen generation **W7**

Registered gate `tester-unified`: **PASS**, on `6797d4fa`, tree clean
(`dirty: false`) — see "Gate verdict" at the bottom for the exact markers.

---

## 1. The record-shape decision, and why

The operator's ruling fixed **shape 1** (list the discarded mutants) over
shape 2 (an ingested-only in-scope count). The backlog entry deliberately
left the record shape itself open — "position-only vs. a small object" — and
this is the call I made:

> **`judgment.r2.discarded` is an array of `mutant_outcome` records — the
> same `$def` and the same Python type the five `Mutation` buckets already
> use — sorted ascending and unique by the A-180 mutant identity
> `(path, start_byte, end_byte, replacement_sha256, operator)`, with
> `kill_signal` forbidden on every entry.**

Four reasons, in the order they decided it:

1. **Position-only would have re-created the bug.** `survived_uncovered` is a
   list of *positions*, deduplicated, and its own model comment says why:
   "one line with nine NoCoverage mutants on it is still one untested line,
   and repeating it nine times would turn a list of places into a disguised
   count of mutants". `discarded` is the mirror image of that argument. It is
   fundamentally a count OF MUTANTS — the field exists because "a report that
   could not compile most of its own mutants measured far less than its score
   implies" — so deduplicating to positions would silently under-report every
   file with two invalid mutants on one line. The real fixture proves this is
   not hypothetical: `format.ts` carries 34 `CompileError` mutants over far
   fewer than 34 distinct lines.
2. **The arithmetic needs a per-mutant cardinality.** The fifth-disposition
   rule is `candidate_count - total == len(discarded)`. With positions that
   equality is simply false for any real report, and the whole verification
   route disappears.
3. **Disjointness needs an identity, not a position.** The re-derivation that
   stops a producer from listing a killed mutant twice — once as caught, once
   as invalid — is an identity comparison against the five buckets. A
   `(path, lineno)` pair cannot make it: many mutants share a line.
4. **Reusing `MutantOutcome` buys the grammar for free and cannot drift.**
   Ordering, within-list uniqueness and field validation all go through
   `_check_mutant_outcome_tuple`, the same helper the buckets use, and the
   raw layer rebuilds entries through the same
   `_reconstruct_mutant_outcome`. One reader, one writer, no second
   implementation to fall out of step.

**What I deliberately did NOT add.** A `status` field naming which discard
status the report gave (`CompileError` vs `RuntimeError`). It is real
information the ingest path currently throws away and it would have been
nearly free to carry — but it is not what B070 asks for, it needs a new
closed vocabulary in the schema, and this wave is scoped to B070 alone. It
belongs in a backlog item, not in this cut.

**Where the field lives.** On `judgment.r2`, not as a sixth `Mutation`
bucket, per the wave prompt's own framing. `Mutation`'s buckets are
dispositions of mutants that were ATTEMPTED; a discarded mutant was not.
`IngestedMutationResult`'s docstring is updated to say that, replacing its
old reason ("bundling `discarded` into `Mutation` would put a number in the
CLAIM that no bucket accounts for"), which the arithmetic change makes stale.

## 2. `candidate_count` / `total` semantics — the explicit answer

The backlog asks for this not to be left implicit.

* **`total` is unchanged.** It is exactly the sum of the five buckets: the
  mutants that were ATTEMPTED.
* **`candidate_count` is unchanged in MEANING** — "the number of candidate
  site descriptors discovery actually observed" — and changes in VALUE on an
  ingested payload only. `ingest_mutation_report` now writes
  `attempted + len(discarded)`, because an invalid mutant genuinely WAS a
  candidate the report listed, with a full identity; it simply was never
  attempted. Through v10 both fields were `attempted`, and that is precisely
  what left an inflated count undetectable (A-437).
* **The residual `candidate_count - total` is the fifth disposition**, and it
  is never left unexplained. `Mutation._check_arithmetic` can no longer
  forbid it (a payload cannot see what accounts for it) but it does still
  forbid `candidate_count < total`. The attribution lives one level up in
  `Verdict._check_discarded_disposition` — equal to `len(discarded)` under
  `producer = "ingested"`, zero outside the limit sentinel under `"native"` —
  and independently at the raw layer in `verify.py`.

**One consequence I found and handled rather than left latent.** An ingested
report whose in-scope mutants were ALL invalid produces `total == 0` with a
positive `candidate_count` — byte-identical to a NATIVE pre-submission limit
refusal. Under the naive change it would have been judged
`BUDGET_EXCEEDED`/`MUTANT_LIMIT_EXCEEDED`: a flat lie about a lane that
declared no candidate cap and declined nothing. Three coordinated edits close
it:

* `judge_mutation` takes `discarded: int = 0` and subtracts it before asking
  the sentinel question, so that document falls through to
  `INCONCLUSIVE`/`NO_MUTANTS` and every native document is byte-unchanged;
* `Mutation.is_limit_sentinel` now says in its own docstring that it is the
  SHAPE, not the conclusion, and names who disambiguates;
* `Claim`'s sentinel rule admits both legal pairings, with the reason written
  down — the converse rule (`MUTANT_LIMIT_EXCEEDED` ⟹ the shape) is
  untouched, and the producer-aware re-derivation through `judge_mutation`
  is what stops a native document reporting the milder terminal.

## 3. Commits, in order

| # | hash | commit |
|---|---|---|
| 1 | `4fc13ca2` | `feat(assay)!: verdict schema v10 -> v11 -- judgment.r2.discarded becomes a listed, verified field (B070)` |
| 2 | `0a67eae9` | `feat(assay): ingest RECORDS the discarded mutants it used to drop (B070)` |
| 3 | `00cca2f3` | `feat(assay): verify gains the FOURTH re-derivation over judgment.r2.discarded (B070)` |
| 4 | `aebf7fda` | `test(assay): a SECOND real StrykerJS artifact, with 40 genuine CompileErrors (B070)` |
| 5 | `e6920f60` | `test(assay): the local suite for the v11 discarded-mutants cut (B070)` |
| 6 | `621ae8cc` | `feat(assay): W7 -- the frozen v11 generation, and the gate's own demotion of W6 (B070)` |
| 7 | `6797d4fa` | `docs(assay): the v10 -> v11 migration notes, written not implied (B070)` |

**Exactly one `feat(assay)!:` commit** — `4fc13ca2`, the schema-version bump
itself, same discipline Wave D used for `b2fd09f3`.

### 1 — `4fc13ca2`, the wire cut

`src/assay/schemas/verdict.schema.json` (`$id` → `urn:assay:schema:verdict:11`,
`schema_version.const` → 11, `judgment_r2.properties.discarded` retyped from
`integer` to an array of `mutant_outcome` with `kill_signal` forbidden, the
`mutation` `$def`'s own note on the cross-object arithmetic widened) and
`src/assay/verdict.py`:

* `VERDICT_SCHEMA_VERSION = 11`, with the v11 paragraph added to the
  version-history block in the same shape v9's and v10's carry;
* `JudgmentR2.discarded: tuple[MutantOutcome, ...] | None`, validated in
  `_check_ingested_record` through `_check_mutant_outcome_tuple` plus the
  10,000 ceiling and the no-`kill_signal` rule; emitted as a list by
  `to_dict`;
* `Mutation._check_arithmetic` revised for the fifth disposition (three legal
  shapes, `candidate_count >= total` as the new floor);
* `Verdict._check_discarded_disposition`, new, wired beside
  `_check_mutation_cardinality`/`_check_equivalence_pairing`/
  `_check_kill_attribution`;
* `Claim`'s sentinel-pairing rule and `Mutation.is_limit_sentinel`'s
  docstring, per §2 above.

### 2 — `0a67eae9`, ingest

`ingest_mutation_report` builds the `MutantOutcome` BEFORE the discard fork,
so a discarded mutant carries byte-for-byte the identity a bucketed one does;
`discarded` becomes a sorted list on `IngestedMutationResult`;
`candidate_count = attempted + len(discarded)`. `judge_mutation` and
`build_mutation_claim` gain the `discarded` count (default 0), and
`runner._run_prepared_lane` passes `len(ingested_r2.discarded)` from the same
object `_build_ingested_judgment_r2` writes onto the wire — one value, one
read.

### 3 — `00cca2f3`, verify

`_check_ingested_r2_agrees_with_its_payload` gains the fourth re-derivation
(three statements: arithmetic, disjointness, line rule) and its
"what this function does NOT check" section shrinks to the un-listed half.
Two new raw helpers: `_raw_mutant_identity` (reads the A-180 identity off the
untrusted document; `None` rather than a partial tuple) and
`_mutant_identities_are_ascending` (deliberately NOT shared with
`_positions_are_ascending`, for that function's own stated reason).
`_reconstruct_discarded` registers the field for
`_reject_unknown_keys` (A-323's lesson), and `_check_r2_rederivation` reads
the discarded count out of the artifact and hands it to `judge_mutation`.

### 4 — `aebf7fda`, the real high-discard artifact

**Which route I used: a REAL Stryker run, not a hand-authored entry.** The
wave prompt allowed either. I produced
`tests/fixtures/mutation/mutation-report-json.probe-js-stryker-typecheck.json`
by running StrykerJS 10.0.0 with `@stryker-mutator/typescript-checker@10.0.0`
over the same `probe-js` sources the existing fixture came from: the checker
type-checks each mutant before running it and marks the ones that do not
compile `CompileError`. Exit 0, 88 mutants, **40 genuine `CompileError`s**
(Killed 11, Survived 6, NoCoverage 31), spread over three files. `package.json`,
`package-lock.json`, `stryker.config.json` and `tsconfig.json` are committed
beside it and `PROVENANCE.md` carries the full recipe, the measured versions
and the four facts B070's design depends on.

Two config deltas from the first fixture (`checkers`, `tsconfigFile`) plus a
narrowed `mutate`/`include`; both are about the checker's requirement that
the baseline project type-check cleanly (`Badge.tsx` needs a JSX/React
program the coverage fixtures never declared). Neither manufactures a
`CompileError` — the checker decides that per mutant.

### 5 — `e6920f60`, the local suite

The A-437 test changes sign:
`test_an_inflated_discarded_count_is_ACCEPTED_deliberately` →
`test_the_A437_reproduction_now_REFUSES_by_name`. Nine new tests in
`test_runner_ingested_r2.py` drive the real high-discard artifact end to end,
including the accepted control. New negatives for disjointness, the line
rule, raw-layer ordering/duplication and the integer→array migration message.
`test_verdict_mutation_payload.py` gains
`test_a_residual_is_now_LEGAL_in_the_payload_and_attributed_one_level_up`.
The rest is the hard cut's mechanical cost — 49 hand-written verdict
fixtures, the LIVE W3 dstdns-sql witness and the inline documents in three
modules move `schema_version` 10 → 11.

### 6 — `621ae8cc`, W7 + the gate

Ten frozen documents (nine migrated, one new real run), the byte-copied
`verdict.schema.v11.json` (verified with `cmp`), a 104-node
`test_acceptance_v11.py` and a `MANIFEST.md` recording the migration
mechanically. `tools/tester-unified-gate.sh` demotes W6 to collect-only and
extends the hard-cut probe to it; `gate/python/qualify_topos.py` advances to
W7's P25 pair and moves its two hardcoded `schema_version != 10` guards — the
exact pins `test_gate_harness_version_pins.py` (B069) exists to catch before
a 25-minute red gate does, and which it did catch, locally, first.

### 7 — `6797d4fa`, docs

CONSUMERS' ingested-lane paragraph and DESIGN-GUIDE §11's matching paragraph
REWRITTEN (not deleted), a new "Migration notes (v10 → v11)" section, a
superseded-note on the v10 paragraph a v10-pinned consumer actually read, a
BREAKING `CHANGES.md` entry in 5.2.0's own shape, and B070's acceptance
checklist ticked with the shape decision recorded against it.

## 4. Things worth flagging to the reviewer

1. **`Mutation` alone is weaker than it was.** A bare
   `Mutation(candidate_count=5, total=1, killed=(one,))` is now legal where
   it used to raise. That is deliberate and documented in the method itself,
   but it is the single largest judgement call in this wave after the record
   shape: no *document* can carry an unattributed residual (both `Verdict`
   and `verify.py` refuse one, in both producer directions), but a
   hand-constructed model object can.
2. **The wholly-discarded ingested document** (§2) is reachable in principle
   and is handled, but there is no *frozen* document of that shape — the real
   fixture has 48 bucketed mutants. The behaviour is covered by
   `judge_mutation`'s own branch and by the widened `Claim` rule, not by an
   end-to-end artifact.
3. **The un-listed half stays declared.** Said in four places (schema
   description, `verify.py` docstring, DESIGN-GUIDE §11, CONSUMERS). I did
   not attempt to close it; the backlog entry is explicit that it is not
   closable from any artifact assay receives.
4. **Host contention during the gate run.** Two other agents' gates were
   running concurrently (`nyxloom-p106` and `run-gate-p05`). Mine was capped
   at `--cpus=3` as required (container `8d4890f256ad`); I also capped
   `f059b107a00f` before realising it belonged to the nyxloom session — a
   cap, not a kill, and consistent with the standing rule either way.

## 5. Gate verdict

Read from the gate's own log in a separate step, never from a piped exit
code (LESSONS L4). Log: `gate1.log` (session scratchpad, quoted below);
durable record: `assay/.run-gate/history.json`.

```
ASSAY_GATE_PHASE=wheel-installed
ASSAY_GATE_PHASE=attestation-hardened
ASSAY_GATE_PHASE=verdict-v5-accepted
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
v6/v7/v8/v9/v10 hard-cut guard passed for 34 frozen templates
ASSAY_GATE_PHASE=verdict-v6-v7-v8-v9-v10-hard-cut-verified
104 passed in 1.44s
ASSAY_GATE_PHASE=verdict-v11-successors-verified
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_GATE_PHASE=pyflakes-clean
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

`assay/.run-gate/history.json`, `lanes["tester-unified"].latest`:

```json
{
  "commit": "6797d4fae7a07bf0d3485485bad02fa3b7be86d5",
  "dirty": false,
  "duration_seconds": 999.917,
  "exit_code": 0,
  "lane": "tester-unified",
  "outcome": "pass",
  "worktree": "/workspaces/vbpub/.worktrees/assay-b070-discarded-mutants"
}
```

The gate built from an exact-OID clone of `6797d4fa`
(`assay-5.2.1.dev18+g6797d4fa-py3-none-any.whl`), so the verdict is against
the committed work and not against the working tree.

---

# Fix round 1 — appended 2026-09-08

Review: `assay-WAVE-B070-REVIEW-round1.md` (`b46d0467`, amended `d7e53e53`).
Verdict was **ACCEPT-conditional — 3 blockers, 3 non-blocking observations**.
All six addressed. Registered gate re-run and **PASS on `05df0450`**, tree
clean; evidence at the end of this section.

The review's own summary is worth restating because it shaped the repair: two
of the three blockers were not defects in what the wave built but holes in
what asserted it — including the change §2 above calls "the one latent lie it
found and closed", which the reviewer deleted while the whole 4,300-test suite
stayed green. That is the sharper half of this round.

## Repair commits

| # | hash | commit |
|---|---|---|
| 8 | `504b1453` | `fix(assay): the candidate ceiling becomes producer-aware -- B070 was refusing the honest high-discard report (review BLOCKER 1)` |
| 9 | `1be233d2` | `fix(assay): the model re-narrows the sentinel disposition, and carries BLOCKER 1's own half (review BLOCKER 3)` |
| 10 | `697088c3` | `test(assay): the latent-lie fix and the sentinel disposition now have tests that kill their mutants (review BLOCKERS 2+3)` |
| 11 | `05df0450` | `docs(assay): the ingested size bound, the honest limit of "refused by name", and B078 (review BLOCKER 1 + OBS 1/2/3)` |

No new `feat(assay)!:` commit: the wire version does not move again. The
schema DOES change in `1be233d2` (two `maximum`/`maxItems` numbers and three
descriptions), and that is a **widening within v11** rather than a second cut
— every document legal before the change is legal after it, so no producer or
consumer is broken and no version bump is owed. `W7/verdict.schema.v11.json`
is re-frozen with `cmp` in the same commit, per the lockstep rule.

## BLOCKER 1 — the ceiling was refusing the honest report

**The finding, restated so the fix is judgeable against it.** B070 made
`candidate_count = attempted + len(discarded)` on an ingested payload, and
`candidate_count` was hard-capped at `MAX_CANDIDATE_CEILING = 10_001`.
Through v10 the field was the bucket sum, so the cap bit only on reports that
ATTEMPTED more than 10,001 mutants. From v11 it bit on reports that DISCARDED
too much — a truthful StrykerJS report of 48 attempted and 9,954
`CompileError`s ingests on `main` and did not on this branch. That is exactly
DA-R26's route 3, at a higher threshold, inside the item whose whole purpose
is to stop refusing the honest report. Misattributed besides: `ERROR` /
`UNREADABLE_ARTIFACT` with a message naming `max_mutants`, which an ingested
lane never declares (A-360).

**I did not pick the remedy.** The reviewer named three routes and correctly
declined to choose; the controller ruled **route (a) — make the ceiling
producer-aware**. Implemented as:

* `MAX_INGESTED_MUTANTS` moved to `assay.vocabulary`, the leaf both the
  parser and the model import without a cycle, and is re-exported from
  `mutation_report_json` under its own name. This is the same
  one-value-many-layers move `_NAMESPACE` already makes for the operator
  namespace, and it has the same drift guard: a test asserts the two modules
  hold the same number;
* `Mutation.__post_init__` bounds `candidate_count` at that DOCUMENT ceiling.
  That is the most this object can honestly say — it cannot see
  `judgment.r2.producer`, so the widest legal value is the only bound
  available to it;
* the NATIVE product ceiling moves one level up to
  `Verdict._check_mutation_cardinality`, which is reached only when
  `max_mutants` is present, i.e. only under `producer = "native"`. Stated
  explicitly rather than left to be re-derived from the two checks beside it;
* `judgment.r2.discarded`'s own `maxItems` follows the same reasoning: the
  field exists only under `ingested`, so `max_mutants`' 10,000 was never
  protecting anything there;
* both schema numbers move to 100,000, each with a description saying why the
  tighter native bound is cross-object and lives in the model and the
  verifier — the schema's own LAYER OWNERSHIP statement, applied.

**One thing I added that the review did not ask for**, because it is the same
defect one layer over: `verify.py`'s raw layer now states the NATIVE residual
rule itself (`candidate_count == total` outside the pre-submission sentinel).
`Mutation._check_arithmetic` held that for every producer through v10 and
B070 had to relax it; without this the model would have been its only witness
— the identical shape BLOCKER 3 is about.

## BLOCKER 2 and BLOCKER 3 — measured, not asserted

The prescriptions needed no design decision, so the only thing worth
recording is the measurement. Both were run on a throwaway copy under the
session scratchpad with an **identical test selection**, so the differential
is clean rather than inferred:

| mutant | result |
|---|---|
| `_check_discarded_disposition` stubbed to a bare `return` | **2 failed / 119 passed** — and the two are exactly the new W7 tests |
| the same copy, mutant reverted, same selection | **121 passed** |
| `judge_mutation`'s `- discarded` reverted to the v10 form (the reviewer's own mutant) | **4 failed**, across `test_mutation_judge.py` and W7 |

The first pair is the important one: same tree, same selection, one line
different, and the failures are precisely the tests written to catch it.

**One design point inside BLOCKER 3.** `_check_discarded_disposition` now
takes the CLAIM rather than just its payload, because the sentinel-disposition
rule needs the claim's own `(status, reason_code)` beside the producer, and
this is the only place that sees both.

**One consequence worth flagging to the fix-verifier**, since it changed a
test's assertion rather than adding one: with the model rule in place, the
wholly-discarded lie is refused during reconstruction, so
`_check_r2_rederivation` is no longer reached for that shape and its message
no longer surfaces. The artifact-level negative therefore asserts the MODEL's
wording and says so in its docstring; the raw layer's independent witness is
asserted directly in `test_mutation_judge.py` instead. Both layers still state
the rule; which one speaks first changed.

## OBS 1/2/3

* **OBS 1** — `_INGESTED_DISCARDED_STATUSES`' doc-comment still described v10.
  Rewritten, and pointed at B078 for the compile-vs-runtime wrinkle.
* **OBS 2** — "refused by name" was overstated. CONSUMERS, CHANGES and
  DESIGN-GUIDE §11 now all say the list is audited against the document it
  sits in, not against the foreign tool's original report, and that a producer
  moving `candidate_count` to match still passes. The reviewer is right that
  this is not a defect — it is the tier every `Mutation` bucket sits in — but
  B070's subject is credibility and the sentence read stronger than it was.
* **OBS 3** — **B078 filed** (searched first; nothing existing covers it):
  `discarded` folds `CompileError` and `RuntimeError` into one list while the
  sentence justifying the field, in five places, says "could not COMPILE".
  The entry records why they are materially different facts, that
  `RuntimeError` may belong closer to `crashed` (a denominator consequence,
  not merely an added field), that the project has no real artifact carrying a
  `RuntimeError` mutant at all, and the four questions a v12 A-row would have
  to answer. Deliberately not built.

## What the review found sound, and I did not touch

Recorded so the fix round is judgeable as a fix round: the record shape, the
four re-derivations, the A-437 refusal and its control, the fixture's
authenticity, the W7 freeze and W6 demotion, the forbid-list integrity and the
acceptance-checklist agreement were all verified independently by the reviewer
and are unchanged by these four commits.

## Gate verdict (fix round)

Read from the gate's own log in a separate step. Log: `gate2.log` (session
scratchpad); durable record: `assay/.run-gate/history.json`.

```
ASSAY_GATE_PHASE=wheel-installed
ASSAY_GATE_PHASE=attestation-hardened
ASSAY_GATE_PHASE=verdict-v5-accepted
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
v6/v7/v8/v9/v10 hard-cut guard passed for 34 frozen templates
ASSAY_GATE_PHASE=verdict-v6-v7-v8-v9-v10-hard-cut-verified
110 passed in 1.29s
ASSAY_GATE_PHASE=verdict-v11-successors-verified
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_GATE_PHASE=pyflakes-clean
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

```json
{
  "commit": "05df0450d7e3d8a074b3bae87667416a35478f42",
  "dirty": false,
  "duration_seconds": 899.519,
  "exit_code": 0,
  "outcome": "pass"
}
```

W7's locked suite is **110 passed** (was 104). Local suite at the same
content: **4429 passed, 20 skipped** (`tests/` plus W7's suite, one run).

**Host, this round.** `docker ps` and `pgrep -af tester-unified-gate.sh` both
clean before launch, load 3.7. The container was identified by its own
`--inner <worktree>` argument BEFORE capping — the process gap the review
flagged, closed for my own runs at least: `bbe1e69e7802`, capped to
`--cpus=3`, verified by `docker inspect`. No other session's container was
touched.

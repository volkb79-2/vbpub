# assay Wave 3 (B070) — acceptance REPORT

Branch: `feat/assay-b070-discarded-mutants-2026-09-08`
Final commit: **`6797d4fa`**
Registered gate `tester-unified`: **PASS** (exit 0), tree clean, on that
commit — evidence in §8.

Every box below is B070's own acceptance checklist, verbatim, with the
evidence I actually verified against rather than a claim. Companion:
`assay-WAVE-B070-LOG.md` (per-commit narrative and the record-shape
reasoning).

---

## Box 1 — "a v11 A-row picks shape 1 or shape 2 and states what the rejected one would have bought, including whether the un-listed half is being left declared"

**Status: DONE.**

* **Shape chosen: shape 1**, as an array of `mutant_outcome` records (not
  position-only). Recorded in `nyxloom-trove/4-backlog.md` § B070 →
  `### Acceptance`, first bullet, and argued in full in
  `assay-WAVE-B070-LOG.md` §1.
* **What shape 2 would have bought**, stated in that same bullet: a smaller
  change with no record-shape decision and no arithmetic-rule revision, at
  the price of saying nothing about WHICH mutants were invalid.
* **The un-listed half is explicitly left declared**, in four independent
  places so no single edit can quietly drop the statement:
  * `src/assay/schemas/verdict.schema.json`,
    `$defs.judgment_r2.properties.discarded.description` — final sentence,
    "What remains declared-not-verified is the strictly SMALLER un-listed
    half — candidates a tool drops before reporting at all";
  * `src/assay/verify.py`,
    `_check_ingested_r2_agrees_with_its_payload` docstring —
    "**What this function still does NOT check, stated so a green bar cannot
    be misread.**";
  * `docs/DESIGN-GUIDE.md` §11 — "**One half stays exactly where it was**";
  * `docs/CONSUMERS.md` — "**What is still declared, not verified: the
    un-listed half.**"
* Asserted mechanically, not just written:
  `tests/test_verify_ingested_r2.py::test_the_schema_says_what_discarded_still_does_NOT_verify`
  requires `"declared-not-verified"` and `"un-listed half"` in the schema
  description.

## Box 2 — "the chosen quantity is on the wire in all three places (schema, dataclass, `verify.py` — the 2.4.0 lesson), forked on `producer` the way every other ingested-only field is"

**Status: DONE.**

| place | what changed | verified by |
|---|---|---|
| schema | `$defs.judgment_r2.properties.discarded` retyped `integer` → array of `mutant_outcome` with `uniqueItems`, `maxItems: 10000`, `kill_signal` forbidden | `W7/test_acceptance_v11.py::test_the_locked_v11_schema_types_discarded_as_a_mutant_outcome_array` |
| dataclass | `JudgmentR2.discarded: tuple[MutantOutcome, ...] \| None`, validated in `_check_ingested_record`, emitted by `to_dict` | `tests/test_runner_ingested_r2.py::test_every_discarded_mutant_reaches_the_wire_with_its_full_identity` |
| `verify.py` | `_reconstruct_discarded` registers the field (so `_reject_unknown_keys` accepts it) and `_check_ingested_r2_agrees_with_its_payload` checks it | `tests/test_verify_ingested_r2.py::test_the_real_ingested_verdict_verifies_clean` (the whole document round-trips) plus every negative below |

**The producer fork is intact in both directions**, which is the half a
reshape is most likely to lose:

* `discarded` stays in `JudgmentR2._INGESTED_ONLY_FIELDS`, so
  `_check_producer_fork` requires it under `ingested` and forbids it under
  `native` — unchanged code path, new type;
* schema-side, asserted against the frozen artifact by
  `W7/test_acceptance_v11.py::test_the_locked_v11_schema_still_forks_discarded_on_the_producer`
  (`{"not": {"required": ["discarded"]}}` in the native `then`, `"discarded"`
  in the ingested `else.required`);
* behaviourally, by `W7/test_acceptance_v11.py::test_a_native_r2_document_may_not_carry_the_ingested_record`
  (parametrised row `("discarded", [])` — an EMPTY array is still refused on
  a native document) and
  `..._must_carry_the_whole_ingested_record` (deleting it is refused);
* empty-vs-absent, by
  `W7/test_acceptance_v11.py::test_an_empty_discarded_list_and_an_absent_one_say_different_things`
  and `tests/test_runner_ingested_r2.py::test_a_zero_discard_report_still_records_an_EMPTY_list`.

## Box 3 — "`verify._check_ingested_r2_agrees_with_its_payload` gains a FOURTH real re-derivation, and its 'what this function does NOT check' section shrinks to the un-listed half only"

**Status: DONE.** The fourth item in that function's docstring is now three
concrete re-derivations rather than a range check, and the section that
followed it — a 24-line paragraph explaining why no fourth was constructible
— is replaced by "**The arithmetic is the load-bearing one, and it is a
re-derivation, not a bound**" plus a "what this function still does NOT
check" paragraph naming only the un-listed half.

| re-derivation | code | test |
|---|---|---|
| `candidate_count - total == len(discarded)` | `verify.py`, `"judgment.r2.discarded lists {n} mutant(s)"` | `tests/test_verify_ingested_r2.py::test_the_A437_reproduction_now_REFUSES_by_name`; `W7::test_the_inflation_is_caught_on_the_HIGH_DISCARD_document_too`; `W7::test_deleting_a_discarded_mutant_is_refused_in_the_same_words` |
| identity disjointness from all five buckets | `verify.py`, `"which the R2 payload also records in one of its five buckets"` | `tests/test_verify_ingested_r2.py::test_a_discarded_mutant_that_is_also_in_a_bucket_is_caught`; `W7::test_a_discarded_mutant_may_not_also_be_in_a_bucket` |
| the `lines_without_candidates` line rule | `verify.py`, `"judgment.r2.discarded records a mutant starting on that exact line"` | `tests/test_verify_ingested_r2.py::test_a_discarded_mutants_line_may_not_be_reported_as_barren`; `W7::test_a_discarded_mutants_line_may_not_be_reported_as_barren` |
| ordering/uniqueness at the RAW layer | `_mutant_identities_are_ascending` | `tests/test_verify_ingested_r2.py::test_an_out_of_order_discarded_list_is_caught_by_the_RAW_layer`; `W7::test_an_out_of_order_or_duplicated_discarded_list_is_refused` |

Both raw-layer negatives assert the RAW checker's own wording
(`"judgment.r2.discarded must be strictly ascending"`), which is deliberately
different from the model's (`"must be sorted by (path, start_byte, ...)"`),
so the model's refusal is not being counted twice as a second witness.

The two disjointness/line-rule tests in `test_verify_ingested_r2.py`
deliberately bump `candidate_count` first, so the ARITHMETIC is satisfied and
the rule under test is the only thing left to catch the document — otherwise
they would pass on the residual message and prove nothing.

## Box 4 — "the `9999` reproduction … becomes a NAMED refusal — and a truthful high-discard document is committed alongside it as the control"

**Status: DONE, both halves.**

**The refusal.** `tests/test_verify_ingested_r2.py::test_the_A437_reproduction_now_REFUSES_by_name`
builds the v11 spelling of A-437's exact forgery — a 9999-entry list of
well-formed, ascending, unique records on the real 109-mutant ingested
document — and asserts a failure containing BOTH
`"judgment.r2.discarded lists 9999 mutant(s)"` and `"a residual of 0"`. The
entries are deliberately well-formed: a list of malformed entries would be
refused for the wrong reason and would prove nothing about the bound. The
same reproduction is frozen into the locked generation as
`W7/test_acceptance_v11.py::test_the_A437_reproduction_now_refuses_BY_NAME`.

The test this replaces —
`test_an_inflated_discarded_count_is_ACCEPTED_deliberately`, the one test in
that module whose assertion was that a mutated document is ACCEPTED — is
gone, and its own closing sentence ("if a future change starts refusing this
document, that change owes B070's wire field first") is now discharged.

**The control.** `W7/expected/high-discard-r2-v11-template.json` — a REAL
run, 88 candidates, 48 attempted, **40 discarded** — must verify with an
empty failure list:

* `W7/test_acceptance_v11.py::test_a_truthful_high_discard_document_is_ACCEPTED`
  (`assert verify_document(clean) == []` after asserting the list really
  holds 40 entries);
* `W7/test_acceptance_v11.py::test_locked_v11_template_is_accepted[high-discard-r2-v11-template.json]`;
* end to end through the runner:
  `tests/test_runner_ingested_r2.py::test_the_high_discard_verdict_verifies_clean`.

Without that control the new rule would be indistinguishable from route 3
(`discarded <= total`), which DA-R26 rejected. `W7/MANIFEST.md` says so
under "The ONE new template, and why it is a real run".

**A third corner, closed because the first two invite it.**
`W7::test_shrinking_candidate_count_to_match_a_forged_list_is_refused` — a
producer cannot pay for a shorter list by claiming it observed fewer
candidates than it ran; `Mutation._check_arithmetic` refuses
`candidate_count < total` by name.

## Box 5 — "a real report carrying a non-zero `discarded` … committed as a fixture and frozen in the v11 `W<n>` generation"

**Status: DONE — a REAL Stryker run, not a hand-authored entry.**

* Fixture:
  `tests/fixtures/mutation/mutation-report-json.probe-js-stryker-typecheck.json`
  — StrykerJS 10.0.0 with `@stryker-mutator/typescript-checker@10.0.0` over
  the same `probe-js` sources the existing fixture came from. Exit 0. 88
  mutants: Killed 11, Survived 6, NoCoverage 31, **CompileError 40**, spread
  over `format.ts` (34), `roles.ts` (5) and `branchy.ts` (1).
* Reproducibility committed beside it:
  `tests/fixtures/mutation/probe-js-stryker-typecheck/{package.json,package-lock.json,stryker.config.json,tsconfig.json}`,
  with the full recipe, the measured versions and the four load-bearing facts
  in `tests/fixtures/mutation/PROVENANCE.md`.
* The fixture's own premise is asserted against the committed bytes rather
  than trusted from the provenance file:
  `tests/test_runner_ingested_r2.py::test_the_real_report_carries_forty_genuine_compile_errors`
  (40 `CompileError`, 88 total, framework `StrykerJS` 10.0.0). If a
  regenerated report ever stops carrying discards, that test fails FIRST and
  names why, instead of every B070 test degenerating into a zero-discard
  control.
* **Frozen in W7** as `W7/expected/high-discard-r2-v11-template.json` (the
  runner's real output over that artifact, `started`/`ended` substituted like
  every other template), and pinned by
  `W7::test_the_high_discard_template_is_a_real_run_with_forty_discarded_mutants`.
* Nine further end-to-end assertions in `tests/test_runner_ingested_r2.py`:
  full identity on every entry, sortedness+uniqueness, the fifth-disposition
  arithmetic (`88 - 48 == 40`), bucket disjointness, the line rule, and that
  the score is untouched (`test_the_discarded_mutants_do_not_move_the_score`).

**W7 follows the generation convention.** Newest generation before this wave
was W6 (`newest_carve_asset_generation()` reads the `carve-assets/W<n>`
directories); W7 carries `verdict.schema.v11.json` (byte-copied and `cmp`-
verified, guarded by `test_shipped_schema_is_byte_identical_to_the_locked_v11_asset`),
`test_acceptance_v11.py` (104 nodes), ten `expected/*-v11-template.json`
documents and a `MANIFEST.md` stating the migration mechanically. W6 is
untouched and joins the hard-cut sweep
(`test_every_earlier_frozen_template_is_rejected_under_v11`, over W1+W2+W4+W5+W6).

## Box 6 — "CONSUMERS' declared-not-verified paragraph and DESIGN-GUIDE §11's matching paragraph are rewritten, not merely deleted … in the v11 migration notes"

**Status: DONE.**

* `docs/CONSUMERS.md`: the ingested-lane paragraph is rewritten to describe
  the array, the four checks and what is still declared; the status-map row
  for `CompileError`/`RuntimeError` and the "what the verdict records"
  sentence are updated; a new **"Migration notes (v10 → v11)"** section
  carries the hard cut, the "if you do not ingest a mutation report, re-pin
  and you are done" statement, the `candidate_count` change and an explicit
  **Migration:** paragraph. The OLD v10 paragraph inside "Migration notes
  (v9 → v10)" is left in place with a superseded block-quote pointing
  forward — it is what a consumer who pinned v10 actually read.
* `docs/DESIGN-GUIDE.md` §11: rewritten to tell the story rather than delete
  it — why the count could not be verified (a missing quantity, not a missing
  check), what v11 supplies, why it cost a schema version rather than being a
  defect fix, which half stays declared, and the cost sentence about the
  window closing mid-cut.
* `CHANGES.md` `[Unreleased]` → `### Changed`: a BREAKING entry in 5.2.0's
  `candidate_total` shape — what changed, why, the second field that moves,
  and an explicit **Migration:** paragraph.
* `nyxloom-trove/4-backlog.md` § B070's acceptance list is ticked with the
  shape decision recorded against box 1.

## 7 — Constraints the wave prompt set, and how each was met

| constraint | how |
|---|---|
| exactly one `feat(assay)!:` commit for the bump | `4fc13ca2` only; `git log --oneline` over the seven wave commits shows no second `!` |
| `assay.toml` `schema_version` and `inventory_schema` untouched | neither file changed; `git show --stat` over the wave touches no `assay.toml`, and `lane-schema-v2-successors-verified` passes in the gate log |
| every other `judgment.r2` producer byte-unchanged where it carries no discards | the eight migrated W7 templates differ from W6's only in `schema_version`; the one ingested template additionally in `discarded` `0` → `[]`, with `candidate_count` unchanged at 109/109 because `attempted + 0 == attempted`. `tests/test_runner_ingested_r2.py::test_a_zero_discard_report_still_records_an_EMPTY_list` asserts the zero-discard runner path still produces `candidate_count == total` |
| gate verdict read from the log, never a piped exit code | §8 — read in a separate step from `gate1.log` and cross-checked against `assay/.run-gate/history.json` |
| host: `docker ps` + `pgrep` before starting; `--cpus=3` on the container | checked before launch (no gate container, no gate process); container `8d4890f256ad` capped to `NanoCpus 3000000000`, verified by `docker inspect`. Two other agents' gates were running concurrently — noted in the LOG §4.4 |
| serial pytest under `nice -n 19 ionice -c 3` | every local run in this wave |
| LOG/REPORT written only after a green verdict | this file and the LOG were written after the gate returned; the gate ran on `6797d4fa` with `dirty: false` |

## 8 — Gate evidence

Read from the gate's own log in a separate step (LESSONS L4). The gate builds
from an exact-OID clone of HEAD, so the verdict is against the committed
work: the wheel it installed is
`assay-5.2.1.dev18+g6797d4fa-py3-none-any.whl`.

Terminal markers, in order:

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

Two of those are this wave's own:

* `v6/v7/v8/v9/v10 hard-cut guard passed for 34 frozen templates` — W6's nine
  documents joined the sweep (25 before, 34 now), each producing exactly the
  single `is not this verifier's version 11` diagnostic;
* `104 passed` under `verdict-v11-successors-verified` — W7's locked suite,
  run for real from the installed wheel.

Durable record, `assay/.run-gate/history.json`,
`lanes["tester-unified"].latest`:

```json
{
  "commit": "6797d4fae7a07bf0d3485485bad02fa3b7be86d5",
  "dirty": false,
  "duration_seconds": 999.917,
  "exit_code": 0,
  "outcome": "pass"
}
```

Local suite, separately, at the same content:
`4314 passed, 20 skipped` (`python -m pytest tests/ -q -p no:randomly`), plus
`104 passed` for `nyxloom-trove/carve-assets/W7/test_acceptance_v11.py`.

## 9 — Open items for the reviewer

Not defects, but the three places where I made a call the reviewer should
weigh (expanded in `assay-WAVE-B070-LOG.md` §4):

1. `Mutation._check_arithmetic` now ACCEPTS a residual a bare model object
   cannot attribute. No document can (both `Verdict` and `verify.py` refuse
   an unattributed one, in both producer directions), but the model object
   alone is weaker than it was.
2. The wholly-discarded ingested document — `total 0` with a positive
   `candidate_count`, byte-identical to a native limit sentinel — is handled
   by `judge_mutation`'s subtraction and the widened `Claim` pairing, but has
   no frozen artifact of its own.
3. I did NOT add a `status` field distinguishing `CompileError` from
   `RuntimeError` on a discarded record. It is real information the ingest
   path still discards, and it would have been nearly free to carry in this
   cut, but it is outside B070 and would need a new closed vocabulary. If the
   reviewer wants it, it is a backlog item, not a fix to this wave.

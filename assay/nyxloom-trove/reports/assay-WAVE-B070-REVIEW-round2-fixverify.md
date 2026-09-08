# assay Wave 3 (B070) — fix-verification, round 2

Reviewer: the round-1 adversarial reviewer, same session, no part in the
implementation.
Branch: `feat/assay-b070-discarded-mutants-2026-09-08`
Repair range: `efd3920a..f23d6420`; the fix commits proper are `504b1453`,
`1be233d2`, `697088c3`, `05df0450` (`f23d6420` is LOG/REPORT only, and
`b46d0467`/`d7e53e53` inside the range are my own round-1 review).
Gate-verified commit: `05df0450`.

Method, per the convention: I re-ran **my own round-1 probes** against the new
code and re-derived every claim independently before reading the implementer's
fix-round LOG/REPORT. Where I quote a number below it is one I produced, not
one I read.

---

## Verdict: **ACCEPT**

All three round-1 blockers are genuinely closed, by mechanism and not by
assertion. All three non-blocking observations are addressed, one of them
(OBS 3) more thoroughly than I asked. The two implementer-disclosed items hold
up. The schema widening is backward-compatible in the exact sense the
controller asked me to check, verified mechanically rather than from prose.

One correction to the record, and it is a correction *in the implementer's
disfavour on method but not on outcome*: their claim that a copied tree cannot
run this kind of mutant test is **wrong as stated** — the copy is usable, and I
reproduced the full-suite runs they said were impossible. Their substituted
differential reached the right answer anyway, and the fix it was testing is
real. Details in §BLOCKERS 2+3 below. This does not change the verdict; I
record it because "the tool cannot do this" is the kind of claim that becomes
folklore if it is not checked once.

No blockers. Nothing outstanding that should hold the merge.

---

# BLOCKER 1 — CLOSED (route (a), and the mechanism works)

## The original repro now ingests

Round 1's finding was that `candidate_count = attempted + len(discarded)` put
an honest ingested report under `MAX_CANDIDATE_CEILING` (`max_mutants + 1`),
so a truthful high-discard report was refused *for discarding too much*. I
re-ran my own reproduction verbatim against `f23d6420` — 48 attempted, sweeping
the discard count across the old and new bounds:

```
MAX_CANDIDATE_CEILING = 10001   MAX_INGESTED_MUTANTS = 100000
  D=  9953: ACCEPTED (candidate_count=10001)
  D=  9954: ACCEPTED (candidate_count=10002)      <-- REFUSED in round 1
  D= 20000: ACCEPTED (candidate_count=20048)
  D= 99952: ACCEPTED (candidate_count=100000)
  D= 99953: REFUSED -> mutation.candidate_count (100001) exceeds the document
            ceiling 100,000; no producer may observe more candidates than
            assay will read from one report
```

The bound is now exactly the parser's own document ceiling, the boundary is
where the code claims it is, and the refusal message no longer names
`max_mutants` at a lane that never declared one. Round 1's three sub-points
(misattributed refusal, wrong terminal, the second `discarded` ceiling riding
along) are all resolved by the same change — `JudgmentR2`'s `discarded` bound
moved to `MAX_INGESTED_MUTANTS` too (`verdict.py:2600-2612`), with a message
that explains itself.

## The native half is genuinely still bound — moved, not dropped

This is the half that would make route (a) a regression if it were done
carelessly, so I checked it rather than assuming. Native `r2_pass.json` with
`candidate_count` forced to 10,002:

```
NATIVE candidate_count=10002 (over MAX_CANDIDATE_CEILING):
  - the R2 payload observed 10002 candidate(s) and attempted 2 under a
    declared ceiling of 50; a native run attempts every candidate it
    observes, and the only native shape with an unattempted remainder is ...
  - schema: claim[R2].mutation records 10002 candidate(s) under producer
    'native', over the product ceiling 10001; discovery stops at
    max_mutants + 1 and max_mutants is bounded at 10,000
```

Two witnesses, from two layers — the raw verifier and the model — where round 1
had one. `MAX_CANDIDATE_CEILING` is now applied in
`Verdict._check_mutation_cardinality` (`verdict.py:4805-4821`), and I confirmed
that method is *unconditionally reached* for any payload-bearing R2 claim
rather than being reachable only by luck: `Verdict.__post_init__`
(`verdict.py:4341-4358`) refuses a mutation payload whose `judgment.r2` is
absent, and `max_mutants` is required under `producer = "native"` via
`_NATIVE_ONLY_FIELDS`, so `policy.max_mutants is None` never short-circuits a
native document. There is no shape in which the relocated ceiling is skipped.

The one honest residual: a **bare** `Mutation` object built outside a `Verdict`
may now claim up to 100,000 candidates even where a native lane would cap it at
10,001. That is the same class as the acknowledged residual disclosure, it is
documented at the constant itself (`verdict.py:646-654`, "**NATIVE only**"),
and no document can carry it. Not a finding.

## The drift guard is real — with one clause that cannot fail today

Asked to judge whether the guard is circular. It is in
`W7::test_an_ingested_payload_is_bounded_by_the_DOCUMENT_ceiling_not_max_mutants`:

```python
assert MAX_INGESTED_MUTANTS == PARSER_BOUND == 100_000
assert MAX_CANDIDATE_CEILING == 10_001
```

Judged clause by clause:

* `MAX_INGESTED_MUTANTS == PARSER_BOUND` is **trivially true today** — the
  parser now does `from ..vocabulary import MAX_INGESTED_MUTANTS`
  (`mutation_report_json.py:51`) and merely re-exports it, so the two names bind
  the same object and no value could make this fail. On its own it would be
  hollow.
* It is **not** hollow in the drift sense, though, because the realistic
  failure mode is a future edit re-spelling the constant as a literal in the
  parser (which is exactly the state this fix removed). That change makes the
  clause fail. It guards re-divergence, not present divergence.
* The `== 100_000` literal is what carries the real weight: a one-sided change
  to the vocabulary value fails here.
* The third surface — the hand-written JSON Schema, which genuinely *can* drift
  because it is a separate literal — is pinned by
  `W7::test_the_locked_v11_schema_bounds_both_at_the_document_ceiling`
  (`maximum == 100000`, `maxItems == 100000`) plus the pre-existing
  `test_shipped_schema_is_byte_identical_to_the_locked_v11_asset`. I verified
  that byte-identity myself: `cmp src/assay/schemas/verdict.schema.json
  nyxloom-trove/carve-assets/W7/verdict.schema.v11.json` is silent.

So all three surfaces are pinned and no single-sided change passes. The only
improvement available is cosmetic — the schema test asserts a literal `100000`
rather than `MAX_INGESTED_MUTANTS` — and since the Python-side test pins the
same number from the other direction, a divergence still cannot pass unnoticed.
Adequate as built; not worth a round 3.

## The unprompted verify.py addition is real and is exercised

`verify.py:1521-1543` (the message at `:1540`) now states the NATIVE residual rule at the raw layer
(`candidate_count != total` outside the sentinel, under a declared cap). I did
not take this on the LOG's word — it is the first of the two failure lines in
the native probe quoted above, produced live. It is correctly gated: the
enclosing `_check_mutation_payload_shapes` returns before it unless
`max_mutants` is an int, which is present only under `producer = "native"`
(A-360), so it cannot fire on an ingested document and re-open BLOCKER 1 from a
new direction — I confirmed that by re-running the accepted high-discard
control (`verify_document == []`).

This addition is the right instinct and closes BLOCKER 3's shape one layer
over, exactly as the implementer says: through v10 `Mutation._check_arithmetic`
held this rule for every producer, and B070 had to relax it, which would have
left the model as its only witness.

---

# BLOCKERS 2 + 3 — CLOSED, and the "unusable copy" claim corrected

## The two model-layer holes are shut

Round 1's two probes, re-run verbatim through
`assay.verify._reconstruct_verdict` (the real constructor `verify_document`
uses, not a hand-built object):

| round-1 probe | round 1 | round 2 |
|---|---|---|
| native limit sentinel relabelled `INCONCLUSIVE`/`NO_MUTANTS` | **MODEL ACCEPTED** | **MODEL REFUSES** — "that is assay's own PRE-SUBMISSION limit refusal and it is BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED and nothing else (A-163)" |
| wholly-discarded ingested doc relabelled `BUDGET_EXCEEDED`/`MUTANT_LIMIT_EXCEEDED` | **MODEL ACCEPTED** | **MODEL REFUSES** — "an ingested lane declares no candidate cap (A-360) and assay declined nothing, so the honest terminal is INCONCLUSIVE/NO_MUTANTS" |
| the honest wholly-discarded doc as `INCONCLUSIVE`/`NO_MUTANTS` | accepted | still **ACCEPTED**, at both layers |

The narrowing landed where I prescribed it — in
`Verdict._check_discarded_disposition`, which now takes the whole `Claim`
rather than just its payload (`verdict.py:4397`, `:4631`) so it can see the
terminal beside the producer. Both halves are stated
(`verdict.py:4703-4718` native, `:4769-4781` ingested). The honest control is
asserted in the same test as each negative, so neither negative can be passing
because the surrounding document went foreign — I checked that construction in
`W7::test_the_MODEL_alone_refuses_both_halves_of_the_sentinel_disposition`
rather than trusting the docstring's claim of it.

## The new tests kill my round-1 mutants

The four new tests in `tests/test_mutation_judge.py:420-490` are the right
shape. In particular
`test_a_PARTIAL_discard_beside_zero_attempted_is_still_the_native_refusal`
(51 candidates, 50 discarded, one unexplained ⟹ still the native refusal) pins
the *arithmetic* rather than a truthiness test, so the subtraction cannot be
replaced by `if discarded:` — that is a sharper test than I asked for, and it
is the one that closes the obvious lazy repair.
`test_build_mutation_claim_carries_the_discarded_count_through` covers the
wiring, which is the half a fix like this usually leaves inert.

## I re-ran my own round-1 mutants, on a full-tree copy, and all three now die

This is the part the controller asked me to reproduce rather than accept. Fresh
`cp -a assay/. <scratch>/` at `f23d6420`; baseline first, then each mutant
applied and reverted in turn; `tests/` and the W7 locked suite as **separate**
pytest invocations, which is how the registered gate runs them
(`tools/tester-unified-gate.sh` invokes W7 on its own with
`--override-ini=pythonpath=`).

Baseline: `tests/` **4262 passed, 77 skipped, exit 0**; W7 **110 passed**.

| mutant | round 1 | round 2 / W7 | round 2 / `tests/` |
|---|---|---|---|
| **M1** — `judge_mutation` loses the `- discarded` subtraction (the whole latent-lie fix) | **survived everything** | **2 failed**, 108 passed | **2 failed**, 4260 passed |
| **M4** — `Verdict._check_discarded_disposition` body replaced by `return` | **survived everything** (4257 passed) | **2 failed**, 108 passed | 4262 passed, 77 skipped |
| **M3** — `_check_r2_rederivation` stops reading the discarded count (`discarded=0`) | not run in round 1 | **2 failed**, 108 passed | 86 passed (targeted trio) |

Restored source, sanity re-run: **110 passed**. So the copy was healthy
throughout and no result is an artifact of a broken tree.

Reading the table:

* **M1 is killed twice over**, and by the tests that should kill it:
  `tests/test_mutation_judge.py::test_the_SAME_payload_with_every_candidate_discarded_is_NO_MUTANTS`
  and `::test_build_mutation_claim_carries_the_discarded_count_through`. Round
  1's central finding — delete the fix, everything stays green — is no longer
  reproducible. BLOCKER 2 is closed.
* **M4 is killed by W7, not by `tests/`.** Worth stating precisely rather than
  rounding off: `tests/` alone still reports 4262 passed with the model check
  entirely deleted. The model witness lives in the locked acceptance
  generation, which is where a schema-contract assertion belongs in this
  project and which the registered gate runs for real
  (`ASSAY_GATE_PHASE=verdict-v11-successors-verified`, 110 nodes). So the
  method is no longer shadowed *as far as the gate is concerned*, which is the
  standard that matters — but anyone re-running round 1's exact command
  (`pytest tests/`) would still see green, and should not read that as the
  method being untested. BLOCKER 3 is closed; this is a note, not a finding.
* **M3 confirms the raw witness is still live and still tested.** Deleting the
  discarded count from `_check_r2_rederivation` fails two W7 tests, including
  the *positive* control (`..._is_NO_MUTANTS`) — which is the cleanest possible
  demonstration that the raw layer really does still independently derive this
  and is not dead code shadowed by the new model rule. This is the direct
  answer to disclosed item 2 below.

## The "a copied tree is unusable" claim is wrong, and worth correcting

REPORT §"The confirmatory run the controller asked for" states that a full-tree
copy "produces 519 errors and 7 unrelated failures from git/wheel fixtures that
do not survive the copy", and substitutes a narrower differential on that basis.

That premise does not hold. Round 1's finding was *produced* on exactly such a
copy (**4257 passed, 77 skipped, exit 0** at `d7e53e53`), and I made a fresh one
at `f23d6420` for this round: **4262 passed, 77 skipped, exit 0**, plus **110
passed** for W7 invoked separately, plus five more full/partial runs across
three mutants without a single collection error. Nothing about the copy is
hostile to these fixtures.

The likely cause of their 519 errors is visible in their own quoted command:
they collected `tests/` **and** the W7 acceptance file in one pytest
invocation. W7's suite is written to run standalone against an installed wheel
— which is how the registered gate itself runs it, as a separate `pytest`
invocation with `--override-ini=pythonpath=` (`tools/tester-unified-gate.sh`)
— and collecting it inside `tests/`' conftest scope is not a supported
combination. The errors are a methodology artifact, not a property of a copied
tree.

I record this only because the substitution was offered as forced. It was not
forced, the honest differential they ran instead did reach a correct answer,
and my own full-suite runs above confirm the same conclusion by the route they
believed was closed. No corrective action needed beyond not repeating the
belief.

---

# The two implementer-disclosed items — checked, both hold

## 1. "§9.1's residual disclosure is corrected, not eliminated"

True, and not quietly widened. A bare `Mutation(candidate_count=5, total=1,
killed=(one,))` is still legal — `Mutation._check_arithmetic`
(`verdict.py:1758-1815`) still hands residual attribution up, still refuses
`candidate_count < total` by name, and
`W7::test_the_arithmetic_rule_admits_the_residual_only_where_it_is_attributed`
still asserts both directions. The one thing that *did* widen at this object is
the ceiling (10,001 → 100,000 for a bare payload), which is a consequence of
BLOCKER 1's fix rather than a new latitude on the residual, and it is disclosed
at the constant's own docstring. The disclosure is narrower and more accurate
than round 1's, not broader.

## 2. "One test's assertion changed rather than being added"

Legitimate, and I verified it is the check firing *earlier* rather than a
weakened assertion. Running the wholly-discarded lie through
`verify_document` at `f23d6420` returns exactly one failure, carrying the
model's wording prefixed `schema:` — reconstruction now refuses the document,
so `_check_r2_rederivation` is never reached for that shape. That is what "the
model states it too" mechanically means; it is not the raw check disappearing.

The important question is whether the raw witness is still *live and tested*,
since no artifact-level test can reach it for this shape any more. It is: the
subtraction inside `_check_r2_rederivation` (`verify.py:2354-2360`) is still
present, and it is asserted directly in `tests/test_mutation_judge.py` through
`judge_mutation` itself — the same function `_check_r2_rederivation` calls, with
the same argument.

Confirmed by mutant M3 above, which is the decisive evidence: removing the
discarded count from `_check_r2_rederivation` alone (model rule untouched)
fails two W7 tests, one of them the *positive* control. A shadowed, dead check
could not do that. So both witnesses remain real and both remain tested; only
the route by which one of them is exercised moved, which is the honest
consequence of putting the rule in the model where I asked for it.

---

# Round-1 observations — all three addressed

| obs | status | what I verified |
|---|---|---|
| **OBS 1** — stale `_INGESTED_DISCARDED_STATUSES` comment | **fixed** | `mutation.py:2191-2205` rewritten for v11 ("LISTED … RECORDED rather than dropped"), and it goes further than asked by softening "could not compile" to "could not build" and pointing at B078 for why |
| **OBS 2** — "refused by name" reads stronger than it is | **fixed, in all three docs** | `CONSUMERS.md:1418-1425`, `CHANGES.md`, `DESIGN-GUIDE.md` §11 each now say the list is audited against the document it sits in, not the tool's original report, and that a producer moving `candidate_count` to match still passes. That is precisely the case I demonstrated in round 1 (fabricated sha256, wholly fabricated entry, and a coordinated +500/+500 forgery — all three ACCEPTED), now stated in the contract |
| **OBS 3** — file the compile-vs-runtime item | **filed as B078**, better than asked | `4-backlog.md`. It records the argument I gave (the justifying sentence leans on a distinction the record does not carry) and adds two things I had not: that `RuntimeError` may belong closer to `crashed`, which would make it a bucket-mapping change with a denominator consequence rather than an added field; and that this project has **no real artifact carrying a `RuntimeError` mutant at all**, which is the A-334 gap in miniature. Four questions for a v12 A-row, nothing built |

---

# The controller's specific question: is the schema widening backward-compatible?

Checked mechanically rather than from prose. Every validation-affecting delta
to the shipped schema in this range:

```
- "maximum": 10001      →  + "maximum": 100000        (mutation.candidate_count)
- "maxItems": 10000     →  + "maxItems": 100000       (judgment_r2.discarded)
```

plus two `description` additions/rewrites, which have no validation effect. No
`required`, `type`, `enum`, `pattern`, `additionalProperties`, `const` or
conditional branch changed. Both deltas are pure relaxations, so **every
document valid under the earlier v11 schema is valid under this one**.

The model side adds two genuinely new refusals (the sentinel-disposition
narrowings), so I checked the stronger property the controller actually cares
about — that nothing which previously *passed `assay verify`* now fails. It
does not: both shapes those rules reject were already rejected by `assay
verify` at `d7e53e53`, which is what round 1 measured. And empirically, over
every frozen document in the tree at `f23d6420`:

```
checked 59 documents, 0 with failures
```

(the ten `W7/expected/*.json` templates with their timestamps substituted, plus
all 49 `tests/fixtures/verdicts/*.json`).

I agree with the ruling on the version number, and note it is the right one for
a reason worth keeping: v11 has never been released, so there is no consumer
whose pinned reading of it could change. The hard-cut discipline protects
*shipped* meanings. Refining an unshipped one before merge is the review cycle
working. One small correction to the framing in passing: the shipped schema
carries **two** description changes in this range, not three.

---

# Process note

Closed, no action. I did not re-verify the container-identification claim, per
the coordinator. For the record on my own side: an unrelated container
(`dstdns-schemagate-55807`, another session's) started during my test runs and
raised host load; I left it entirely alone, capped nothing, and simply let my
own nice'd runs take longer.

---

# Method notes

* Every probe re-run from round 1's own scripts against `f23d6420`, under
  `nice -n 19 ionice -c 3`. Implementation mutants applied to a throwaway
  `cp -a` copy under the session scratchpad; the worktree is byte-unchanged by
  this review except for this file.
* Host checked before starting (`docker ps`, `pgrep -af tester-unified-gate.sh`):
  no assay gate container and no gate process at any point. I started and
  modified no container.
* I did not re-run the registered gate. I read its durable record myself rather
  than from the REPORT: `.run-gate/history.json`, `lanes["tester-unified"].latest`
  = commit `05df0450d7e3d8a074b3bae87667416a35478f42`, `dirty: false`,
  `exit_code: 0`, `outcome: pass`, `duration_seconds: 899.519`. Consistent with
  the controller's own separate read.

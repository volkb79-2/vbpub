# assay Wave 3 (B070) — adversarial code review, round 1

Reviewer: fresh session, no part in the implementation.
Branch: `feat/assay-b070-discarded-mutants-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b070-discarded-mutants`
Range reviewed: `361cf628..efd3920a` (8 commits), read in full.
Method: blind read of `WAVE-PROMPT-2026-09-08-b070-discarded-mutants.md` and
`4-backlog.md` § B070 first, then the diff, then live probes and implementation
mutants against a throwaway copy of the tree; the implementer's LOG/REPORT were
read only after my own view had formed.

## Verdict: **ACCEPT-conditional** — 3 blockers, 3 non-blocking observations

> Amended after filing: the confirmatory full-suite run for BLOCKER 3's
> implementation mutant landed and it **survives** (4257 passed, 77 skipped,
> exit 0, with `Verdict._check_discarded_disposition` disabled entirely).
> BLOCKER 3 is strengthened accordingly; nothing else changed.

The design is right and the execution is unusually careful. Shape 1 as an array
of `mutant_outcome` records is the correct call and the reasoning for it
(LOG §1) survives adversarial reading; the four re-derivations are real
re-derivations and not the clamp DA-R26 rejected; the second StrykerJS fixture
is genuinely real captured tool output; the W7 freeze and W6 demotion follow the
established convention exactly; the docs are honest about the boundary. I could
not break the checks that were built.

What I could break is the perimeter around them. Two of the three blockers are
things the wave *did* build but did not test at all — including the change the
LOG calls the one latent lie it found and closed, which I removed from the
implementation and watched the entire 4,300-test local suite stay green. The
third is a new refusal of exactly the honest high-discard report B070 exists to
protect, introduced as a silent side effect of the `candidate_count` semantics
change.

None of the three is a design error. All three are inside this wave's own scope.

---

# Blockers

## BLOCKER 1 — the `candidate_count` change newly REFUSES an honest high-discard ingested report, at the one bound DA-R26 ruled against

**This is the finding I would not merge without.**

`ingest_mutation_report` now writes

* `assay/src/assay/mutation.py:2398` — `candidate_count=attempted + len(discarded)`

and `candidate_count` is hard-capped, unchanged from v10:

* `assay/src/assay/verdict.py:645` — `MAX_CANDIDATE_CEILING = 10_001`
* `assay/src/assay/verdict.py:1657-1662` — `Mutation.__post_init__` raises above it
* `assay/src/assay/schemas/verdict.schema.json`,
  `$defs.mutation.properties.candidate_count` — `"maximum": 10001`

but the ingest path's own document bound is two orders of magnitude larger:

* `assay/src/assay/mutation_parsers/mutation_report_json.py:84` —
  `MAX_INGESTED_MUTANTS = 100_000`

Through v10 `candidate_count` was `attempted`, so a report was refused by the
ceiling only if it *attempted* more than 10,001 mutants. From v11 it is refused
if `attempted + discarded` exceeds 10,001 — so a report whose attempted half is
comfortably inside the ceiling is now refused **because it discarded too much**.
That is precisely the failure mode DA-R26 rejected route 3 for: a bound that
refuses the honest high-discard report. It is at 10,001 rather than at `total`,
so it bites far less often — but it is new, it is undetected, and the field
exists for reports that discard a lot.

Reproduced (the committed fixture's own shape, scaled):

```
$ python3 -c "... Mutation(candidate_count=48+D, total=48, killed=<48 outcomes>)"
D=9953: accepted (candidate_count=10001)
D=9954: REFUSED -> mutation.candidate_count (10002) exceeds the product ceiling
        10001; discovery stops at max_mutants + 1 and max_mutants is bounded at 10,000
```

48 attempted + 9,954 `CompileError`s is 10,002 mutants — one tenth of what the
parser is willing to read — and it is an entirely truthful StrykerJS report. On
`main` it ingests (`candidate_count = 48`). On this branch it does not.

Three things make it worse than a bare threshold change:

1. **The refusal is misattributed.** `Mutation.__post_init__`'s message names
   `max_mutants`, which an ingested lane never declares at all (A-360, and
   `Verdict._check_mutation_cardinality`'s own docstring says so). A consumer
   reads "discovery stops at max_mutants + 1" about a lane that performed no
   discovery.
2. **The terminal is wrong.** `assay/src/assay/mutation.py:2402-2411` wraps the
   `ValueError` as `ERROR` / `UNREADABLE_ARTIFACT`, "mutation report does not
   yield a well-formed R2 payload". The report is perfectly well-formed and
   perfectly readable; assay's own model ceiling refused it.
3. **A second, tighter ceiling rides along.**
   `assay/src/assay/verdict.py:2576-2580` (and schema `"maxItems": 10000`) refuse
   a `discarded` list longer than 10,000 outright, so a report with more than
   10,000 invalid mutants is refused regardless of `candidate_count`. Also new,
   also undocumented.

Nothing tests anywhere near either ceiling: the largest discarded list in any
test is 9,999 fabricated entries in the A-437 reproduction, and that document is
expected to be refused anyway (for the residual, not the ceiling), so it proves
nothing about the boundary.

Nothing documents it either — neither `docs/CONSUMERS.md`'s new "Migration notes
(v10 → v11)" nor the `CHANGES.md` BREAKING entry mentions that an ingested
report's acceptable size just changed, even though both go out of their way to
flag the `candidate_count` semantics move as "the one to read carefully".

**Prescription — and this needs a controller decision, not my improvisation.**
The defect is established; the remedy is a product call between three routes:

* **(a)** make the ceiling producer-aware — `MAX_CANDIDATE_CEILING` is
  documented at `verdict.py:642-644` as "a defence against a malicious declared
  cap", which is a native concern; an ingested payload has no declared cap to
  defend against and is already bounded by `MAX_INGESTED_MUTANTS`. This keeps
  every honest report ingestible and is the option most consistent with DA-R26;
* **(b)** raise both ceilings (`MAX_CANDIDATE_CEILING`, the `discarded`
  `maxItems`, and the schema `maximum`) to a figure derived from
  `MAX_INGESTED_MUTANTS` rather than from `max_mutants + 1`;
* **(c)** accept the narrowing deliberately — in which case it needs a named
  refusal (not `UNREADABLE_ARTIFACT` with a `max_mutants` message), a test at
  the boundary in both directions, and a sentence in the migration notes and
  the `CHANGES.md` BREAKING entry saying an ingested report over N mutants is
  now refused where it previously was not.

Whichever route the controller picks, the fix owes: a boundary test on the
accepted side (`attempted + discarded` at the ceiling) and on the refused side,
and a migration-notes sentence.

## BLOCKER 2 — the "latent lie" fix has NO test; I removed it and the entire suite stayed green

LOG §2 and REPORT §9.2 present the wholly-discarded ingested document — `total
0`, positive `candidate_count`, byte-identical to a native pre-submission limit
sentinel — as a latent lie found and closed by three coordinated edits:

* `assay/src/assay/mutation.py:2748,2831` — `judge_mutation(..., discarded=0)`
  and `if mutation.total == 0 and mutation.candidate_count - discarded > 0:`
* `assay/src/assay/runner.py:3704` — `discarded=len(ingested_r2.discarded)`
* `assay/src/assay/verify.py:2354-2360` — the same subtraction in
  `_check_r2_rederivation`
* `assay/src/assay/verdict.py:3374-3396` — `Claim`'s sentinel rule widened to
  admit `(INCONCLUSIVE, NO_MUTANTS)` beside
  `(BUDGET_EXCEEDED, MUTANT_LIMIT_EXCEEDED)`

**The fix is real.** I verified it directly, on the W7 high-discard template
reduced to an all-discarded shape (buckets emptied, `total 0`,
`candidate_count 40`, the real 40-entry `discarded` list kept):

```
PROBE A  INCONCLUSIVE/NO_MUTANTS (the honest one):       *** ACCEPTED ***
PROBE A2 BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED (the lie):
  ['R2 claim status (BUDGET_EXCEEDED, MUTANT_LIMIT_EXCEEDED) disagrees with the
    re-derived judgment from mutation buckets (INCONCLUSIVE, NO_MUTANTS) ...']
```

**But nothing in the repository asserts it.** `grep -rn "discarded=" tests/`
returns exactly ONE hit across the whole suite —
`tests/test_verify_ingested_r2.py:325`, inside
`test_an_inflated_discarded_list_still_cannot_move_the_R2_status`, where the
payload has `total > 0` so the new branch is not on the path, and the assertion
is that the parameter makes **no difference**. No test in `tests/`, and none in
`nyxloom-trove/carve-assets/W7/test_acceptance_v11.py`, ever constructs a
`total == 0` payload with a non-empty `discarded`. The
`(Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS)` alternative added at
`verdict.py:3393` is never exercised by anything.

Proved by implementation mutant, not by inspection. In a throwaway copy of the
tree I reverted `mutation.py:2831` to its v10 form —

```python
-    if mutation.total == 0 and mutation.candidate_count - discarded > 0:
+    if mutation.total == 0 and mutation.candidate_count > 0:
```

— i.e. deleted the entire latent-lie fix, restoring the exact behaviour LOG §2
calls "a flat lie about a lane that declared no candidate cap and declined
nothing". Result:

* targeted modules (`test_runner_ingested_r2`, `test_verify_ingested_r2`,
  `test_verdict_mutation_payload`, `test_mutation_judge`, W7's locked suite):
  **241 passed**;
* the full local suite, `python3 -m pytest tests/ -q -p no:randomly`:
  **exit 0, all green.**

Note that the registered gate cannot cover for this. `assay.toml:23-26` declares
`tester-unified` as `rigor = ["R0"]` — assay never applies coverage or mutation
rigor to its own diff (the comment at `assay.toml:12-19` says so explicitly), so
a green gate carries no statement at all about whether these lines are covered
or their behaviour asserted.

REPORT §9.2 discloses this as "no *frozen* document of that shape … covered by
`judge_mutation`'s own branch and by the widened `Claim` rule". That
understates it: there is no test of any kind, frozen or local, and the branch is
not "covered" in any sense that would notice its removal.

**Prescription.** Two tests, both cheap, both differential:

1. in `tests/test_runner_ingested_r2.py` or `tests/test_mutation_judge.py`, a
   direct `judge_mutation` pair on a `total == 0` payload:
   `discarded=0` ⟹ `(BUDGET_EXCEEDED, MUTANT_LIMIT_EXCEEDED)`,
   `discarded=candidate_count` ⟹ `(INCONCLUSIVE, NO_MUTANTS)`. This one test
   alone kills the mutant above;
2. in `W7/test_acceptance_v11.py`, the artifact-level statement: a
   wholly-discarded ingested document is accepted as `INCONCLUSIVE`/`NO_MUTANTS`
   and refused when it claims `BUDGET_EXCEEDED`/`MUTANT_LIMIT_EXCEEDED` — the
   shape my PROBE A/A2 above already builds from the committed high-discard
   template, so no new fixture is needed.

Whether it also deserves a *frozen* `expected/` document is a judgment call I
would leave to the controller; the two tests above are the minimum.

## BLOCKER 3 — the model layer was weakened in BOTH producer directions and is not re-narrowed where it can be; the weakening is untested

`Claim`'s sentinel rule at `verdict.py:3374-3396` had to widen — a wholly
discarded ingested payload really does reach the sentinel bytes, and a `Claim`
cannot see `producer`. That is correct. What is missing is the re-narrowing one
level up, at `Verdict._check_discarded_disposition` (`verdict.py:4595-4660`),
which *can* see both `policy.producer` and `policy.discarded` and which the
widened rule's own comment names as part of the compensating machinery.

It does not perform that check. The native branch returns as soon as the payload
is a limit sentinel (`verdict.py:4634`) without looking at the claim's
`(status, reason_code)`, and the ingested branch never looks at it either.
Measured, by reconstructing real documents through `assay.verify._reconstruct_verdict`:

* a NATIVE limit sentinel (`tests/fixtures/verdicts/r2_budget_exceeded_mutant_limit_exceeded.json`,
  `candidate_count 4 / total 0 / max_mutants 3`) relabelled
  `INCONCLUSIVE`/`NO_MUTANTS`:
  → **MODEL ACCEPTED**. At v10 `Claim.__post_init__` refused this by name.
* the wholly-discarded INGESTED document relabelled
  `BUDGET_EXCEEDED`/`MUTANT_LIMIT_EXCEEDED` — i.e. **the exact latent lie B070's
  own LOG says it closed**:
  → **MODEL ACCEPTED**.

Both are caught by `assay verify` (the first additionally by the pre-existing
raw check at `verify.py:1516-1519`, the second by `_check_r2_rederivation`), so
no *artifact* can carry either shape and the wire contract is intact. But the
project's own standing rule is that the quantity lives in all three places and
that the model and the raw layer state each rule independently — and here the
model gave up a rule it used to hold and handed the whole burden to
`verify.py`. REPORT §9.1 discloses the `Mutation`-level half of this ("a bare
model object can" carry an unattributed residual) but not this half: the
disclosure says "both `Verdict` and `verify.py` refuse an unattributed one, in
both producer directions", and for the *status pairing* that is not what the
code does.

**Separately, and measured: `Verdict._check_discarded_disposition` carries no
test weight at all.** With its entire body replaced by an immediate `return` in
a throwaway copy — i.e. the model half of B070's fourth re-derivation deleted
outright, disjointness, line rule, arithmetic and the native-residual refusal
together:

* `test_runner_ingested_r2`, `test_verify_ingested_r2`,
  `test_verdict_mutation_payload`, `test_mutation_judge`,
  `test_verdict_judgment` and W7's locked acceptance suite: **335 passed**;
* the whole local suite, `python3 -m pytest tests/ -q -p no:randomly`:
  **4257 passed, 77 skipped, exit 0** in 430 s.

So the method is entirely shadowed by `verify.py`'s independently-worded raw
checks as far as the test suite is concerned. That is the opposite of the
two-independent-witnesses discipline this project applies everywhere else (and
which this very wave was careful about for the RAW-vs-model ordering wording —
the negatives there deliberately assert the raw checker's phrasing so the model's
refusal is not counted twice). The prescription below therefore closes two things
at once: it gives the method a rule only it can state, and gives it tests.

**Prescription.** In `_check_discarded_disposition`, which already receives the
policy and whose caller (`verdict.py:4358-4361`) has `r2_claim` in hand, re-derive
the sentinel disposition with the same subtraction `judge_mutation` uses:

* under `producer = "native"` with `is_limit_sentinel`, require the R2 claim to
  be `(BUDGET_EXCEEDED, MUTANT_LIMIT_EXCEEDED)` — restoring the v10 rule the
  widening dropped;
* under `producer = "ingested"` with `total == 0` and
  `candidate_count == len(discarded)`, require `(INCONCLUSIVE, NO_MUTANTS)`.

Plus one model-level test for each direction (they are the same two shapes
BLOCKER 2 asks for, so the two blockers share fixtures).

---

# Non-blocking observations

## OBS 1 — a source comment left behind by the reshape

`assay/src/assay/mutation.py:2191-2196`, the doc-comment on
`_INGESTED_DISCARDED_STATUSES`, still reads "Statuses **counted** in
`judgment.r2.discarded` … **COUNTED** because a report that could not compile
most of its own mutants…". It is the sentence immediately above the code this
wave changed, and it now describes v10. One-line fix; noted because this project
treats the schema/model/verifier/doc agreement as load-bearing and this is the
fifth place the statement lives.

## OBS 2 — "an inflated list is refused BY NAME" is true only if `candidate_count` is left alone

`docs/CONSUMERS.md` and the `CHANGES.md` entry both state that an inflated
`discarded` is now refused by name, and that what remains unverified is "the
strictly SMALLER un-listed half". A producer that inflates `discarded` **and**
`candidate_count` together still passes every check. Verified on the frozen
`high-discard-r2-v11-template.json` (timestamps substituted, so the document
otherwise verifies clean):

```
BASELINE                                : []
hand-edited sha256 on one entry         : *** ACCEPTED ***
one entry replaced by a fabrication
  on a nonexistent line (lineno 999999) : *** ACCEPTED ***
+500 fabricated entries AND
  candidate_count +500 together         : *** ACCEPTED ***
```

The list is audited for internal consistency against the payload beside it,
never against any external witness.

This is not a defect — it is the same tier every `Mutation` bucket has always
sat in, and B070 never promised more. But "refused by name" reads stronger than
it is, and B070's whole subject is credibility. One clause would fix it, e.g.
"…refused by name, unless the payload's own `candidate_count` is moved to match
— the list is audited against the document it sits in, not against the foreign
tool's original report."

## OBS 3 — REPORT §9.3's deliberate omission (`CompileError` vs `RuntimeError`) was the right call, but the backlog item should be filed now

I checked this against B070's stated scope rather than accepting the
implementer's framing. Leaving it out is correct: none of the four
re-derivations needs it, `mutant_outcome`'s grammar has no status field, and
adding one means a new closed vocabulary — a second wire decision inside a cut
whose binding constraint was exactly one. The route (backlog candidate, not this
wave) is right.

One wrinkle worth recording in that backlog entry: the field's justifying
sentence, repeated verbatim in `CONSUMERS.md`, `DESIGN-GUIDE.md` §11 and
`verdict.py`, is "a report that could not **compile** most of its own mutants
measured far less than its score implies". That sentence leans on the
compile-vs-runtime distinction the record does not carry, and `RuntimeError` (a
mutant that crashed the runner) is a materially different fact from
`CompileError` (a mutant that never built). That is the argument for the future
item, and it is better captured now, while the reasoning is fresh, than
rediscovered later.

---

# What I checked and found sound

Recorded so a later reviewer does not re-litigate settled ground.

**Record shape and its validation, in all three places.** Verified independently
of the REPORT's table.

| place | evidence |
|---|---|
| schema | `verdict.schema.json` `$defs.judgment_r2.properties.discarded` — array of `mutant_outcome`, `uniqueItems`, `maxItems 10000`, `allOf` with `{"not": {"required": ["kill_signal"]}}`. `mutant_outcome` is `additionalProperties: false`, so the `allOf` sibling introduces no laxity |
| dataclass | `verdict.py:2260` (field, documented `:2226-2259`), `:2573` (`_check_mutant_outcome_tuple` — the same helper the five buckets use, so ordering *and* within-list uniqueness are the same code, not a second copy), `:2576-2589` (ceiling, no-`kill_signal`), `:2651-2655` (`to_dict` emits a list) |
| `verify.py` | `_reconstruct_discarded` (`:1683`) routed through the same `_reconstruct_mutant_outcome` the buckets use, so `_reject_unknown_keys` registration (A-323) comes for free via `r2.to_dict()` at `:1754`; `_raw_mutant_identity`/`_mutant_identities_are_ascending` (`:359`,`:392`) read the identity off the untrusted document, deliberately not sharing `_positions_are_ascending` |
| producer fork | `discarded` is in `_INGESTED_ONLY_FIELDS` (`verdict.py:2470-2480`), so `_check_producer_fork` requires-under-ingested / forbids-under-native with no new code path |

`MutantOutcome.to_dict` omits `kill_signal` when `None` (A-051), so the emitted
document cannot trip the schema's own `not required` on a null — I checked,
because an "omitted, never null" slip there would have made every real ingested
document schema-invalid.

**Ordering/uniqueness/disjointness/line-rule are real, and I planted violations
rather than trusting that a check exists.** All four refuse, each with the
wording its own layer owns:

* a killed mutant copied into `discarded` (with `candidate_count` bumped so the
  arithmetic is satisfied and disjointness is the only thing left) → refused;
* a discarded entry on a line named in `lines_without_candidates` → refused;
* a descending pair → refused, in the RAW checker's wording
  (`"must be strictly ascending"`), distinct from the model's — so the two
  witnesses really are two;
* a deleted entry, an added 41st entry, and a `candidate_count` shrunk to match
  a forged list (`W7::test_shrinking_candidate_count_to_match_a_forged_list_is_refused`)
  → all refused.

**The A-437 reproduction refuses by name, and the control is real.**
`test_the_A437_reproduction_now_REFUSES_by_name` builds 9,999 *well-formed*
fabricated records (so the refusal cannot come from a grammar accident) and
asserts both `"judgment.r2.discarded lists 9999 mutant(s)"` and
`"a residual of 0"`. The control that keeps the rule from being route 3 is a real
40-discard document accepted in full, asserted at three levels (runner
end-to-end, frozen template, `verify_document(clean) == []` inside every
differential negative). The old
`test_an_inflated_discarded_count_is_ACCEPTED_deliberately` is gone, which is
correct — its own closing sentence made that conditional on B070 landing.

**The fixture is genuinely real captured tool output, not hand-authored.** I did
not take PROVENANCE.md's word for it. Read against the first fixture, the second
carries: `framework.dependencies` naming
`@stryker-mutator/typescript-checker: 10.0.0` (absent from the first); a
`statusReason` key present on the mutants and absent from the first fixture
entirely, carrying real `tsc` diagnostics
(`src/branchy.ts(1,37): error TS2355: A function whose declared type is neither
'undefined', 'void', nor 'any' must return a value.`); a different absolute
`projectRoot`; a narrowed 3-file `files` set with byte-identical `source` for the
three files it shares and the three unshared ones absent. That is a real run.
`PROVENANCE.md`'s recipe, versions and four load-bearing facts are complete, and
`test_the_real_report_carries_forty_genuine_compile_errors` pins the premise
against the committed bytes so a regenerated fixture fails loudly instead of
degenerating into a zero-discard control. Good.

**Flat-shim blast radius.** `carve-assets/W1`, `W2`, `W4`, `W5`, `W6` are
byte-untouched (`git diff --stat` over the range lists no file under them). The
one carve asset that moves is `W3/expected/dstdns-sql-r2-v6-witness.json`, and
`git log` on that file shows it moved at `b2fd09f3` (v10) and `af14021f` (v9)
too — it is the live witness that tracks the current schema by design, so this is
the established pattern, not a leak. Every migrated W7 document differs from its
W6 predecessor only in `schema_version`, plus `discarded 0 → []` in the single
ingested one; `candidate_count` moved nowhere, which is correct since
`attempted + 0 == attempted`.

**W6 demotion follows the convention exactly.** `tools/tester-unified-gate.sh`
adds W6 to the collect-only list and to the hard-cut probe tuple with
`("W6", 10)`, and moves the diagnostic wording and the phase marker — the same
four-step shape W5 got at v10, W4 at v9 and W2 at v8. Name-diffing W6's and W7's
suites shows 8 renamed test functions (`..._v10` → `..._v11`) and 23 genuinely
new ones (45 → 60 functions, 104 collected nodes), with nothing dropped. `qualify_topos.py` advances `_EXPECTED_ROOT` and both hardcoded
`schema_version != 10` guards, which is exactly what B069's
`test_gate_harness_version_pins.py` exists to catch pre-gate.

**Forbid-list integrity.** Every file in the range is under `assay/`, and every
one is either B070's own surface, a mechanically migrated fixture, or the
wave's LOG/REPORT/backlog tick. `assay.toml`'s `schema_version` and
`inventory_schema` are untouched, as the prompt required. Exactly one
`feat(assay)!:` commit (`4fc13ca2`).

**Docs.** I read `CONSUMERS.md`, `DESIGN-GUIDE.md` §11 and `CHANGES.md` as a
consumer trying to migrate, and they carry it: the old v10 paragraph is left in
place under a "Superseded at v11" block-quote (right — it is what a v10-pinned
reader actually read), the new §"Migration notes (v10 → v11)" states the hard
cut, the array shape, the `candidate_count` move and an explicit **Migration:**
paragraph, and the un-listed half is named out loud in four independent places
with a test asserting two of the phrases in the schema description. The anchor
`#migration-notes-v10--v11` matches the heading. Subject to OBS 2 and BLOCKER 1's
missing sentence, a real consumer could follow this.

**Acceptance-checklist agreement.** I held the diff to `4-backlog.md` § B070's
own six boxes rather than to the REPORT's paraphrase. All six are genuinely
satisfied. Box 4 ("the 9999 reproduction becomes a NAMED refusal … and a truthful
high-discard document alongside it as the control") is satisfied as written;
BLOCKER 1 is not a failure of box 4 but of a consequence box 4 does not mention.

**Host contention (not a code finding; flagged for the controller because it
touched a third party).** LOG §4.4 discloses that two other agents' gates ran
concurrently and that the implementer capped container `f059b107a00f` before
realising it belonged to the `nyxloom-p106` session — a cap, not a kill.
Checking after the fact: `/workspaces/vbpub/.worktrees/nyxloom-p106/nyxloom/.run-gate/history.json`
records that session's latest `tester-unified` as `pass` (exit 0, 146.4 s), so
there is no observable harm. Three process points remain, none of which the
implementer could have fixed alone: the cap was applied to a container this wave
did not own; per the LOG it was never reverted; and the other session was not
told, so had its gate gone red on a wall-clock budget the cause would have been
invisible to it. The LOG also names the second session as `run-gate-p05` while
the worktree present on disk is `run-gate-p06-rg41` — a small inaccuracy in a
disclosure that is otherwise commendably volunteered. Recommend the controller
note the "cap only containers whose worktree path you own" rule where the
`--cpus=3` instruction lives, since the instruction as written does not say how
to tell yours from someone else's.

---

# Method notes

* Probes were run against the branch's own source with `nice -n 19 ionice -c 3`;
  implementation mutants were applied to a throwaway copy under the session
  scratchpad, never to the worktree. The worktree is byte-unchanged by this
  review except for this file.
* Host was checked before every run (`docker ps --no-trunc`,
  `pgrep -af tester-unified-gate.sh`): no gate container and no gate process were
  running at any point, load stayed under 5.1, and no container was started or
  modified by me.
* I did not re-run the registered gate. Its verdict is not in question — my two
  test-adequacy blockers are about what the gate does not judge (`assay.toml`
  R0-only), not about whether it ran honestly. The `history.json` record
  (`6797d4fa`, `dirty: false`, exit 0) is consistent with the LOG's markers.

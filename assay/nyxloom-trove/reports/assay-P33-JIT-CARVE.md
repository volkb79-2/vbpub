# P33 — verdict schema v5 — Sol JIT carve

Carver: C-sol-1 (Opus xhigh, per A-216).
Frozen against main `b03555d79227ef7eb76eaf7f851c2896968fa455`, clean.
Assets: `nyxloom-trove/carve-assets/P33/`. Handoff:
`handoffs/assay-P33-verdict-schema-v5.md`. Specification:
`nyxloom-trove/SCHEMA-V5-DESIGN.md`.

## Result

> **SUPERSEDED IN PART, 2026-08-11.** The first freeze recorded below was reviewed
> **NOT READY** (eleven blocking defects, six scope defects) and re-carved. Read
> § "Answering the pre-dispatch review" at the end of this file for what changed;
> the sections between here and there are the original freeze, retained because
> the review's central finding is about *how* they were validated and deleting
> them would erase the evidence. Where a statement below conflicts with the
> answer section, **the answer section governs.** In particular: the locked
> acceptance suite that "What is not frozen" declines to supply now exists, the
> asset counts are 19/92/16 rather than 17/90/7, and A-221's reasoning is
> corrected by A-225.

**First freeze's claim — READY, pending the mandatory CR-opus-0 carve review.**
Every externally visible decision is fixed: the schema exists as bytes, both
expected templates exist, the ownership boundary is a committed manifest rather
than prose, and thirteen pre-implementation expectations are witnessed and
reproducible.

Nothing here is left for the implementer to invent. The one thing I deliberately
did *not* supply is a full locked acceptance suite; see "What is not frozen".

*That claim did not survive review.* Three of the thirteen expectations passed on
a version short-circuit, four requirements were mechanically unsatisfiable, and
five externally visible decisions were in fact left to the implementer.

## What the carve established

**The keystone change is a v4 bug fix, and the probe proves it mechanically.**
`judgment` in v4 has properties exactly `['r1','r2','r3']` and permits `{r2}`
alone, while A-192 has made `R0,R2` a legal rigor declaration since P23. So a
v4 `R0,R2` lane — Python today, no SQL involved — records no language, no source
roots and no comparison commit, though R2 scopes mutation to changed lines
against exactly that commit. SQL is only the first language for which `R0,R2` is
the *sole* honest declaration. Expectation 11 of the probe asserts this against
the real v4 schema rather than against my description of it.

**The migration is breaking, in both directions, and that is the point.** The
SQL template draws **13 distinct errors** from the v4 schema — not one
version-number complaint but a spread across `judgment.resolved`,
`judgment.r2.kill_attribution`, the `equivalent` bucket, `helpers`, and four
`sql:*` operator values. An existing v4 artifact is symmetrically rejected by
v5. A migration that were merely additive would show neither.

**The schema is generated, not typed.** `migrate_v4_to_v5.py` applies the ten
deltas and `--check` compares its output byte-for-byte against the committed
asset. That makes v5 reviewable as a delta rather than a 46 KB file, and it makes
"was this hand-edited afterwards?" a command rather than a judgment. The script
refuses to run against a non-v4 source, so it cannot double-apply.

**The ownership boundary is mechanical.** 17 locked carver-owned paths, 90
implementer-owned, 7 build artifacts excluded — enumerated by scan, not by
recollection, in `migration-manifest.json`. This is the gap I flagged to the
operator before carving: without it, an implementer migrating 90 files would
reach into P21's and P26's frozen expectations and nobody would notice until the
diff review.

## Two decisions this carve produced

**A-221 — the `go:*` vocabulary, and a correction to my own draft.** Three
operators, faithful analogues verified against `_COMPARE_SWAP`'s real contents
rather than assumed. No `falsy-swap` analogue, because Go conditions are strictly
`bool` and Python's version exploits duck-typed truthiness; no arithmetic,
increment, or statement-removal operators, because A-112 is the governing rule
and says the catalogue is reused, never extended. Recorded as deliberate so the
absence does not read as an oversight.

The correction matters more than the addition. My v5 draft left `go:*` **empty**,
which would have made a Go R2 declaration fail at *load* — while the design guide
and A-183 both say Go renders `INCONCLUSIVE/MUTATION_UNSUPPORTED` at runtime
until P29 lands its helper. The draft silently contradicted shipped doctrine, and
populating the vocabulary restores it. I recommended against including Go
operators last turn on "don't invent" grounds; that instinct was right in general
and wrong here, because these three are transcription of an existing catalogue
rather than invention.

> **WITHDRAWN by A-225.** The paragraph above is wrong about the shipped product.
> `cli._resolve_declared_adapters` consults the registry for every declared level
> above R0 before anything executes, and only Python is registered, so a Go R2
> lane is refused `ERROR/BAD_LANE_CONFIG` at load *either way* — the load-versus-
> runtime distinction I relied on does not exist in this build. The catalogue
> stands; the justification does not. See A-225 and the answer section.

**A-222 — locked v4 assets are not rewritten.** A-138/A-170 make a version bump a
consumer migration with no in-place upgrade, and A-176/A-206/A-214 lock these
assets so implementation pressure cannot weaken a frozen expectation. Rewriting
P21's v4 expectation into v5 would falsify what P21 actually proved: the artifact
would claim a merged package was accepted against a contract that did not exist
when it was accepted. A-197 lets the carver correct a locked asset for a *proven
defect in that asset*; a later version bump is not one, so the exception does not
apply and the assets are untouched.

## Requirement-to-oracle traceability

| oracle | frozen material that makes it checkable |
|---|---|
| O1 — R0,R2 representability plus helper provenance | `expected/sql-r2-v5-template.json`; probe expectations 5, 9, 10, 11 |
| O2 — closed per-language vocabulary | `verdict.schema.v5.json`'s `mutation_operator` `oneOf`; probe expectation 12; A-221 |
| O3 — equivalence pairing and score exclusion | the `equivalent` bucket and `equivalence_artifact` in the schema; the SQL template's populated `equivalent` entry with a real reason (redeclared by a later migration) |
| O4 — attribution consistency | `kill_attribution` required in the schema; the SQL template exercises `declared` with a `kill_signal` on its killed entry |

Each of the five cross-object invariants gets its own controlled break, per
clause and not per function — the one-hop item carried from P26's review, which
warned that a "closed grammar" or "anything else is a typed failure" clause needs
one break per clause. It applies directly to a closed vocabulary and to four
consistency rules, so it is written into work item 3 rather than left as advice.

## Premise probes

Thirteen expectations in `probe_v5_controlled_red.py`, all currently passing, all
reproducible from the recipe in the assets README. Three of them (3, 4, 5) are
designed to **invert** on implementation and are the package's real acceptance
signal: the templates the pre-implementation tree rejects must be accepted, and
the v4 artifact v5 rejects must stay rejected.

## What is not frozen, and why

**No full locked acceptance suite.** P20–P26 each shipped one. I have not, for a
specific reason: 90 files migrate, and the dominant failure mode here is a
migration that passes because every fixture moved together. A suite I author now
would be written against the same migrated shapes the implementer produces, which
is the common-mode interpretation failure A-176 was written after. Instead the
handoff's "Package-specific test emphasis" names the three tests no migrated
fixture can satisfy — a v4 artifact refused by version, a v5 artifact missing
`judgment.resolved`, and a cross-language operator — and the reviewer is required
to add a combined-axis test the implementation did not name. If the carve reviewer
judges that insufficient, a locked suite is the right repair and I will author it.

**No `equivalence_artifact` or `kill_signal_artifact` producer.** Both are
declared paths a project's own command writes. P33 validates them; P34 is the
first package that produces one.

**No per-mutant time bound.** Deferred in A-220 with a named v6 trigger, checked
rather than assumed: `mutation.budget_exceeded` is already a per-mutant bucket.

## Successor dispositions

- **P34** inherits the SQL template as its target artifact shape and owes the
  first real `equivalence_artifact` producer. A-215's checkpoint item 2 — whether
  a flat `LanguageAdapter` stays honest for a coverage-less language — is
  **still open**; v5 answers only the artifact half, and the interface half is
  P34's carve. It is not discharged by this package.
- **P27's re-carve** gains a place to record its statement-position oracle
  (`helpers`, role `statement-positions`) and must give `go_cover.py` an explicit
  scope status per A-217.
- **P29** may extend `go:*` with real evidence as an additive `oneOf` branch, with
  no further version bump. One trap recorded in A-221: Go's `true`/`false` are
  predeclared identifiers, not keywords, so they can be shadowed — a site
  generator must resolve them as the predeclared constants, never by token text.
- The design guide's "Go returns `UNSUPPORTED` until P29 lands its helper" is now
  slightly stale, since A-217 puts a statement-position helper in P27's re-carve
  while P29 keeps the mutation helper. Still literally true; flagged rather than
  edited, because `docs/DESIGN-GUIDE.md` is outside this package's scope.

## Pre-dispatch adversarial specification review

Not run here. Under A-216 it belongs to a fresh Opus xhigh child forked from
`CR-opus-0` and commissioned by the controller. On P27 that leg found three real
defects in a carve I believed was sound, including a locked asset whose recorded
validation had been run on a substituted copy — so the standing instruction to
attack rather than assess is worth repeating for this one. The highest-value
targets: whether the absent locked suite is a real gap; whether the five
cross-object invariants are complete; and whether `judgment.resolved` being
*required* whenever `judgment` is present breaks any legitimate R0-only artifact.

---

# Answering the pre-dispatch review (re-carve, 2026-08-11)

The CR-opus-0 pre-dispatch review returned **NOT READY** with eleven blocking
defects and six scope defects
(`reports/assay-P33-pre-dispatch-adversarial-review.md`). It was right on every
count I could check, and I checked all of the load-bearing ones directly rather
than accepting them. The review's own framing is the accurate one: the design was
not the problem; the freeze was.

**The methodological finding is the one worth carrying forward.** I cited thirteen
green probe expectations as evidence. Three of them were satisfied by
`verify_document`'s version short-circuit alone — the verifier returned
`schema_version 5 is not this verifier's version 4` and stopped, so no semantic
check ever ran against either template. That masking is not incidental; it is why
three blocking defects survived. All three (B1, B2, D3) are facts about the
post-implementation state, and no assertion made against a tree whose shipped
schema is still v4 can see any of them. I had reasoned myself into omitting the
locked suite on common-mode grounds; the correct answer was to author it *against
the post-implementation state*, which would have caught all three.

## Verified before repairing

| finding | how I checked it | result |
|---|---|---|
| B8 `$id` still v4 | read the generated asset | `urn:assay:schema:verdict:4` with `const: 5`. My rewrite tested `"v4" in $id`; the id contains `:4`, so it was dead code |
| B2 template contradicts precedence | re-derived buckets against shipped `judge_mutation` | non-empty `budget_exceeded` with `FAIL`/`MUTANTS_SURVIVED` recorded — unreachable under A-117 |
| B3 `base` on R0,R3 | read `JUDGE_FIELDS_BY_RIGOR` | `R3 == ('language','source_roots','canary')`; R1/R2 carry `base`, R3 does not. Also confirms the *other* two hoists are right |
| B7 A-183 misread | read `_resolve_declared_adapters` | `for level in lane.rigor: if level != "R0": registry.get_adapter(...)` — a Go R2 lane fails at load today, so my load-vs-runtime argument described a distinction that does not exist |
| D5 forbidden import | grepped `verify.py` | `from .mutation import judge_mutation` — I forbade a module in-scope code already imports |
| D3 gate breakage | read `tools/tester-unified-gate.sh:231` and P26's suite | the gate runs P26's locked suite, which asserts `verify_document(<v4 template>) == []` at three sites |
| D1 path outside scope | `git ls-files gate/python/qualify_topos.py` | tracked, 7 `source_roots` hits, outside my `scope.touch` |
| manifest ran on the worktree | `git ls-files build` | empty — the v1 manifest listed 7 untracked files a fresh worktree would not have |

## What changed

**Decisions.** A-223 answers all five invented-decision findings plus B8/B9;
A-224 executes A-222's unexecuted second clause and corrects the scope; A-225
withdraws A-221's reasoning while leaving its catalogue intact.

**Assets.** A committed v4 snapshot makes `--check` survive the migration (B1).
The SQL template's `budget_exceeded` bucket is emptied and its arithmetic redone
(B2). `$id` is set explicitly (B8). `base` became conditional and the
at-least-one-tier guarantee is restored explicitly rather than dropped (B3, B9).
`kill_signal` is forbidden on the four non-killed buckets in the schema itself,
since the constraint is locally expressible (B10, A-182). `ALL_MUTANTS_EQUIVALENT`
enters the reason vocabulary, which is what finally makes O3's exclusion
falsifiable (B6). Two new locked templates freeze CA1 and CA4. The manifest is
regenerated over the git index with two new detection reasons — one catching
`tests/test_verdict_schema_is_packaged.py`, one catching the gate script — and a
third bucket listing the sixteen carver-owned prose files so their exclusion is
auditable instead of silent.

**The locked suite exists.** `test_acceptance_v5.py`, 19 tests, **17 failed / 2
passed** pre-implementation. It carries P26's attestation coverage forward by
reading P26's own locked templates and bumping only `schema_version` in memory —
so A-222's "never rewritten" and A-224's "no longer executed" both hold literally.
Every negative is differential, which is the direct structural answer to the
version-masking that defeated the first freeze.

**Two claims I had to withdraw.** My assertion that "every load-bearing file has
a status" was false while `cli.py` and `tools/tester-unified-gate.sh` had none —
I diagnosed that exact defect in P27 and reproduced it. And the scope↔manifest
agreement is now a mechanical check (92/92 inside `scope.touch`) rather than a
sentence claiming completeness.

## Residual, named rather than hidden

- **`input_revision`** is `7a774d57b41033e0f3de84cd5c2bb188f3cc401b`, the commit
  containing the repaired assets and this report section — set in a follow-up
  commit, because a handoff cannot contain its own hash. Verify against
  `carve-assets/P33/README.md`'s hash table before starting; that table is the
  anchor if the two ever disagree.
- **`DESIGN-GUIDE.md`'s worked TOML example** declared the four bare operator
  names and would have become a config-load refusal after P33. **Discharged:**
  line 830 now reads `operators = ["python:compare-swap", ...]`. Carver-owned, so
  it landed here rather than in P33's scope.
- **`judge.language` remains an opaque string** at every layer, so "closed per
  language" is enforced only for the three prefixes that exist. The review is
  right that an enum is locally expressible. I am not adding one in P33: it would
  refuse every currently-valid lane declaring an unregistered language at config
  load, which is a behaviour change beyond a contract migration. Named for P34,
  which is the first package with a second real language.

## Disposition

**READY for a fresh mandatory carve review**, with no claim that a second round
is a formality. The first review found the freeze had never been observed in the
state it produces; the repair's whole thrust is that the state is now asserted by
a locked, differential, post-implementation suite. The highest-value targets for
the next reviewer: whether `ALL_MUTANTS_EQUIVALENT` interacts correctly with the
existing `NO_MUTANTS` and `MUTATION_UNSUPPORTED` terminals; whether the
`judgment.allOf` conditional actually expresses "base iff r1|r2" for every rigor
combination; and whether retiring P26's suite from the gate loses any coverage the
carry-forward test does not reproduce.


---

# Answering round 2 (2026-08-11)

Round 2 returned **NOT READY** and confirmed eleven of the seventeen round-1
fixes by re-derivation. Its root-cause naming is correct and is the thing worth
carrying: **the carve was validated against the artifacts the reviewer named, not
against the class the reviewer described.** Round 1 said "the gate runs a locked
v4 suite"; I retired that suite and added a scanner pattern for that invocation,
while a second gate step comparing against locked v4 templates went unexamined.

## The sweep, and what it found

The operator required an inventory rather than a third patch. It is
`carve-assets/P33/sweep_v4_consumers.py`, a locked asset. It starts from
`tools/tester-unified-gate.sh`, extracts every path the gate hands to an
interpreter, follows local imports transitively, and looks for **directory
components** rather than literal paths — because `qualify_topos.py` builds
`carve-assets/P25/expected` from `/`-joined parts, which is precisely why a
content grep cannot find this class.

```text
$ python3 nyxloom-trove/carve-assets/P33/sweep_v4_consumers.py
=== gate invocations (seeds) ===
    gate/python/qualify_topos.py
    nyxloom-trove/carve-assets/P26/test_acceptance.py
    tests
    tests/test_self_hosting.py

=== transitive closure: 147 python files ===
=== CONSUMERS OF A FROZEN EXPECTED ARTIFACT: 11 ===
```

Of the eleven, eight read only `tests/fixtures/**`, which is implementer-owned
and migratable. **Three consume a locked v4 expectation:**

| consumer | locked expectation | found by |
|---|---|---|
| `nyxloom-trove/carve-assets/P26/test_acceptance.py` | P26's four templates, via `HERE/expected` | round 1 (D3) |
| `gate/python/qualify_topos.py` | P25 `pass`/`missing-v4-template.json` | round 2 (R2-D1) |
| `tests/test_python_qualification.py:259` | P25 `pass-v4-template.json` | **this sweep** |

So the answer to "does the sweep turn up anything beyond round 2" is **yes, one
more**, and it is in `tests/`, not in the gate harness — which is why an
inventory of *consumers* was the right closure and another content pattern would
not have been.

## What changed

**A-226** makes the rule general: every locked v4 expectation that live gate code
compares against gets a carver-supplied v5 sibling, and the consumer is
repointed. P25's two siblings are committed and
`test_p25_v5_siblings_are_the_declared_projection` audits that each differs from
its original in exactly the two declared ways — so the projection is evidence to
check rather than a transform to trust, which is the objection round 2 correctly
raised against an in-harness v4→v5 conversion.

**A-226 also amends A-224: P26's module is not retired.** The reviewer's surgical
fix is better and I have taken it. Retiring dropped 18 of 24 tests, only 4 of
which touch artifact shape; the lost 18 include A-212's process-group kill on a
witnessed descendant-held pipe, aggregate bounds before the first Git call,
literal-pathspec identity and annotated-tag peel refusal. My justification —
"nothing else in P26's suite asserted artifact shape" — was a true sentence that
silently substituted *shape coverage* for *coverage*. The gate now deselects five
named tests and keeps nineteen running.

**A-228** carries `ALL_MUTANTS_EQUIVALENT` to every layer that constrains its
three siblings. Verified after regenerating: a payload-free variant and an R3
variant are both now **REJECTED**, where both validated before. The fifth bucket
also reached the limit sentinel's shape and the "four buckets" prose in two
places.

**A-227** settles the two open decisions and closes the `only-if`.
`kill_signal_artifact` is reserved in the artifact contract but **refused at
config load until P34**, because making a field declarable while shipping no
producer for the value it implies creates a legal lane whose own artifact has no
representable shape. All three `helpers` roles are ruled. And on the `iff`: my
call is to **close it now** — the producer cannot reach the state, but `verify.py`
exists for foreign documents, A-182 puts locally expressible conditionals in the
schema, and it is one branch in the identical form of the ones already there.
Verified: an `r3`-only judgment carrying `base` is now rejected.

The locked suite grew 19 → 30 tests (**26 failed / 4 passed** pre-implementation),
adding CA9's two forgeries, CA10's three attribution clauses, the `only-if`, the
third `helpers` role, and the P25 sibling audit.

## The boundary I am not closing, stated plainly

Round 2's cross-cutting observation is correct and I am not fixing it in P33:
**every locked test here validates a hand-written document.** Nothing runs
`assay run` and inspects what it emitted, and assay's self-hosted lane is
permanently R0-only (A-189) so it emits no `judgment` at all and cannot witness
`resolved`. A correct verifier with a wrong producer passes this package's entire
acceptance material.

Two reasons it stays open, and one thing I did do. P33 is a contract migration:
its deliverable is the artifact contract and the layers that judge it, and the
only v5 producer path that exercises `judgment.resolved` end-to-end is a real
R1/R2 lane — which for a *new* language is P34's SQL adapter, the first R2
producer this package does not already have shipped Python coverage for. Adding a
producer oracle here would mean carving P34's proof inside P33. What I did do is
repoint the two P25 qualification consumers at v5 siblings, which restores the one
real producer-side v5 check the registered gate has. CA7 belongs to P34 and is
recorded in its successor dispositions.

Also acknowledged from round 2 and not repaired here: **no fixture makes declared
and resolved `base` genuinely differ** (A-143's required shape, CA8). A-223(f)
pins the rule in prose and the locked templates all substitute a full 40-hex, so
`resolve_base` returns the same string either way. This is a real gap in P33's own
material. It needs a fixture whose lane declares a branch name — which requires a
real repository, so it belongs with CA7 in the producer-side proof rather than in
a document-validation suite. Named rather than left for a reviewer to find a third
time.

## Disposition

**READY for a third mandatory review.** Nothing about a second repair round makes
the third review a formality; the two rounds so far each found the same failure
shape in a new place, and the honest thing to say is that an inventory closes a
class only if the inventory is right. `sweep_v4_consumers.py` is the artifact to
attack first: if its closure or its signal set misses a consumer, the class is
still open.

---

# Answering round 3 (2026-08-11)

Round 3 returned **NOT READY** and did the thing I asked for: it turned the
inventory tool on itself and found my closure *claim* false on five independent
grounds — with a real missed consumer, not a hypothetical one.

## The sweep's own gaps, and why a longer list would not have closed them

`gate/distribution/release_wheel.py` executes inside the registered gate
(`qualify_topos.py:539-545`) and compares against the frozen
`gate/python/release/P25/release-manifest.json`. v1 missed it twice over, and the
second reason is the instructive one: **it receives its manifest path on the
command line.** The frozen path lives in the caller. No predicate of the form
"does this file name a frozen directory" can ever find it, however many
directory names the list contains — which is precisely the move I made after
round 2. v2 adds a second category, `indirect-path-from-caller`, and that is what
finds it.

The other four were structural: leaf-name import resolution left
`src/assay/adapters/**` and `coverage_parsers/**` entirely unreachable while
`adapters/python.py` sits in this package's own `scope.touch`; `from . import X`
(`module=None`) was skipped, dropping `safeio.py`; `read_bytes() ==` against
locked P24 assets matched none of the five regexes; and the seed set never read
`assay.toml`, where the gate's primary invocation's argv actually lives — so
`tests/`, and therefore the third consumer, entered only via a failure-only
diagnostic line. All five are closed and each was verified individually. Closure
147 → 189 files; consumers 11 → 40.

## Pinning the tool, which is the part that generalizes

Nothing ran or pinned the sweep, so every one of those gaps would have regressed
in silence. Three locked tests now hold it: the five established consumers are
frozen; the closure is asserted never to leave the project (a leaf-name fallback
had been pulling a sibling project's module in); and a **planted decoy** under
`src/assay/adapters/` must be found.

The decoy is reached by a real dotted-subpackage import edge, and my first
version of it was wrong: I planted a file nothing imported and asserted the sweep
must find it. The sweep was right to ignore it — an unimported file genuinely is
not in the gate's execution path. The import edge is what makes the test
falsifiable rather than merely demanding.

## Two corrections to my own prior framing, both real

**The deselect list was over-inclusive by one.**
`test_all_structural_and_aggregate_bounds_precede_every_git_call` tests A-210's
aggregate-bounds-before-Git ordering and touches no artifact shape. My round-2
attribution table and its own appendix disagreed about it, and the wrong one got
adopted — a security oracle dropped by a transcription slip inside the very
repair that existed because I had dropped eighteen others. Four deselects now,
twenty of P26's twenty-four tests still running.

**CA8's deferral did not hold.** I reasoned that proving declared-versus-resolved
`base` divergence needs a real repository and therefore belonged with the
producer proof in P34. It does not: `qualify_topos.py` already writes the lane's
`base`, already has a `base_override` parameter, already passes it at `:836`, is
`implementer_owned`, sits in `scope.touch`, and is already edited by work item
8b. A-143's shape was one scenario and one assertion away, in the package that
collapses two independently-resolved base values into one field. It was raised in
round 1, round 2 and round 3; taking it now is work item 8c. **CA7's deferral —
the producer-side v5 exercise — stands**, and round 3 agreed it is legitimate;
only CA8 was wrongly grouped with it.

## Also closed

Work item 6 had no observable: `judge.mutation` is already a closed sub-table, so
"the declaration is refused" is true today with or without the change. The oracle
is now the *message* — the field must be named reserved for P34, not reported as
an unknown key. The third `helpers` role fixture compared two different documents
and never exercised its R1 branch; both halves are rebuilt as true differentials.
Three config-layer negatives exist where the suite previously had none, though
O2's own observable has always said "refused at config load". The manifest is
regenerated at the current anchor and unioned with the sweep's own output so the
two cannot drift. `input_revision` is `c22c6073`, set in the same landing rather
than a round later.

## Disposition

**READY for a fourth mandatory review**, and this round was narrow and mechanical
exactly as the reviewer predicted. What I would attack first: the sweep's
`indirect-path-from-caller` heuristic is the newest and least-tested idea here —
it keys on argparse-ish markers, and a consumer that takes its path some other
way would still be missed. The decoy pins the direct path; nothing yet pins the
indirect one.

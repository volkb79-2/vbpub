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

- **`input_revision`** names `b22ebd56`, which contains the P33 assets in their
  *pre-repair* form; the repaired assets land in the commit carrying this report
  section. A handoff cannot contain its own commit hash. Verify against
  `carve-assets/P33/README.md`'s hash table before starting, and treat that table
  as the anchor.
- **`DESIGN-GUIDE.md`'s worked TOML example** declares the four bare operator
  names and becomes a config-load refusal after P33. The guide is carver-owned and
  forbidden to the implementer, so **I own that edit** and it is not in P33's
  scope. It must land before P33 merges, not after; recorded here so it is not
  discovered by a reader of a shipped doc.
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

# P33 — verdict schema v5 — Sol JIT carve

Carver: C-sol-1 (Opus xhigh, per A-216).
Frozen against main `b03555d79227ef7eb76eaf7f851c2896968fa455`, clean.
Assets: `nyxloom-trove/carve-assets/P33/`. Handoff:
`handoffs/assay-P33-verdict-schema-v5.md`. Specification:
`nyxloom-trove/SCHEMA-V5-DESIGN.md`.

## Result

**READY, pending the mandatory CR-opus-0 carve review.** Every externally visible
decision is fixed: the schema exists as bytes, both expected templates exist, the
ownership boundary is a committed manifest rather than prose, and thirteen
pre-implementation expectations are witnessed and reproducible.

Nothing here is left for the implementer to invent. The one thing I deliberately
did *not* supply is a full locked acceptance suite; see "What is not frozen".

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

---
kind: roadmap
schema_version: 1
milestones:
- id: M1
  title: Deterministic core
  target_product_version: 1
  features:
  - F001
  - F002
  - F003
  - F007
  - F010
  - F011
  status: done
- id: M2
  title: The complete evidence ladder
  target_product_version: 1
  features:
  - F004
  - F005
  - F006
  status: done
- id: M3
  title: Evidence assay did not measure itself
  target_product_version: 1
  features:
  - F009
  - F012
  status: done
- id: M4
  title: A second language, then ship
  target_product_version: 1
  features:
  - F013
  status: active
- id: M5
  title: Go, honestly
  target_product_version: 1
  features:
  - F008
  status: planned
- id: M6
  title: Release ergonomics
  target_product_version: 1
  features:
  - F014
  status: planned
- id: M7
  title: New computed methods
  target_product_version: 1
  features:
  - F015
  status: planned
---

# assay — roadmap

Milestones toward the current product definition. The frontmatter is the
machine-readable target; this body records *why* the order is what it is, since
the ordering has already been re-decided once on evidence and would otherwise
look arbitrary.

## Done — M1, M2, M3

Twenty-seven merged packages (P00–P26, plus P33) delivered the deterministic
core, the full R0–R3 ladder, and the two evidence paths assay does not compute
itself. Nothing in M1–M3 is aspirational: every one of those features' criteria
cites a passing test in `2-product-definition.md`, and the ones that could not
be proved are marked `absent` rather than quietly rolled into a `shipped`
feature.

## M4 — A second language, then ship (ACTIVE)

**Delivers F013 (the SQL/DDL adapter), as package P34, and then the ship
milestone.**

This is where the roadmap was re-decided. The original order put the Go wave
(P27–P32) before any second language. Two findings forced the swap (A-219):

1. **A-217 ruled A-O19 as option 2**, so P27 must be *re-carved* around a real
   source-side Go statement-position oracle rather than dispatched as carved.
   Profile-only line attribution was proven impossible — two byte-identical
   coverprofiles can come from sources with different statement lines.
2. **SQL R2 was blocked on a schema migration**, because v4's
   `mutation_operator` was a closed four-value Python-only enum. Designing v5
   once, for SQL *and* Go together, is cheaper than designing it twice.

So schema v5 (P33) landed first, and SQL goes next. The trade is named rather
than hidden: A-215's ordering rationale — do not freeze a second-language
mutation contract before the first language is qualified — is knowingly given
up here, and the risk is recorded in A-219.

P34 is **unblocked**: both rulings it was waiting on are landed (A-242, the flat
seven-method adapter with raising stubs; A-243, helper provenance for a failed
discovery). Its carve inherits them rather than making them.

**The ship milestone sits at the end of M4**, not after the Go wave: assay is
already adoptable for Python and SQL consumers, and holding a release for Go's
oracle would price adoption behind work no current consumer is waiting on.

## M5 — Go, honestly (PLANNED)

**Delivers F008's three absent criteria, as the P27 re-carve through P32.**

Go comes *after* the ship milestone by the same A-219 decision. Two named
blockers must be handled inside the re-carve, and both are already documented
so the carve does not discover them:

- **A-234** — the committed Go coverage fixtures are hand-authored and wrong in
  both coordinates. They must be regenerated *and* their consumer expectations
  re-derived from the option-2 oracle. Regenerating alone would encode the
  parser's over-approximation as truth.
- **A-239** — the oracle's seam is *ruled*, not open: `go_cover.py` emits block
  extents as an explicit representation, statement positions arrive through a
  **new** protocol hook (never by overloading `statement_spans`), and the
  intersection is a pure language-free core function. Go-specific, not
  speculative shared infrastructure. The re-carve designs the details; it must
  not re-open the shape.

One more thing the re-carve must claim explicitly rather than inherit silently:
**effective-PATH reachability** (`MISSING_EXTERNAL_TOOL`), whose ownership
hopped P22 → P26 → P27 with the last hop recorded nowhere until A-246. It
belongs to P27 now, because A-217's oracle is assay's first real external tool.

## M6 — Release ergonomics (PLANNED)

**Delivers F014: cmru adoption plus a zipapp published beside the wheel.**

Scoped and measured, deliberately not landed (A-247, B002, B003). The zipapp
half is mechanically proven — built, driven, and shown byte-reproducible after
one mtime normalisation — but the cmru half edits release keys that seven other
products share, so it is a design checkpoint rather than a package.

**Its position in this list is the least settled thing on the roadmap.** It is
placed after M5 because nothing currently blocks on it; the honest alternative
is that it belongs *inside* M4's ship milestone, since a zipapp is the
near-zero-cost answer for a consumer with no installed Python (A-O04's srdm
blocker). That sequencing has not been ruled, and this document should not
pretend otherwise.

## M7 — New computed methods (PLANNED)

**Delivers F015: `fail-before/pass-after` as a Tier 1 computed method.**

Accepted as the next capability priority after the Go wave closes (A-244),
explicitly as a *planning acceptance and not a dispatch* — it has no package
number, does not gate the ship milestone, and nothing in the current queue may
cite it as a dependency. It won over the other two v2 candidates (A-O05
whole-project mutation, A-O07 serial/parallel parity) on reuse and evidence
value: it inverts machinery that already exists and is already qualified, and
it adds a genuinely new claim — *this test would have caught the bug* — at close
to zero new substrate.

## Not on this roadmap

The named exclusions in `2-product-definition.md`'s `non_goals` are not
"someday" items; they are consequences of the single invariant in
`1-north-star.md`. Candidate capabilities that *are* someday items live in
`4-backlog.md` and in `decisions.md`'s Open section (A-O01 – A-O19), each with
what blocks it. A backlog item enters this roadmap only by becoming a feature
with checkable acceptance criteria — never by being mentioned here first.

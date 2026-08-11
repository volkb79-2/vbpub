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
  title: 'Ship: release ergonomics'
  target_product_version: 1
  features:
  - F014
  status: active
- id: M5
  title: A second language
  target_product_version: 1
  features:
  - F013
  status: planned
- id: M6
  title: Go, honestly
  target_product_version: 1
  features:
  - F008
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
the ordering has now been re-decided **twice** on evidence and would otherwise
look arbitrary.

**Execution order is the array order above**, and milestone ids run with it:

```text
M1 M2 M3 (done) -> M4 [ship: cmru + zipapp] -> M5 (SQL, P34)
   -> M6 (Go: P27 resumed -> P28 -> P29 -> P30 -> P31 -> P32) -> M7
```

Note for anyone holding an older copy: `M4`/`M5`/`M6` were reassigned once, by
A-248, when the ship milestone moved ahead of SQL. That was a deliberate
one-time exception taken while these ids were a single commit old and cited
nowhere outside this document and `2-product-definition.md`. **They are identity
from here on** — the same rule A-153/A-167 impose on package numbers, which is
why P34 keeps its number while running second.

## Done — M1, M2, M3

Twenty-seven merged packages (P00–P26, plus P33) delivered the deterministic
core, the full R0–R3 ladder, and the two evidence paths assay does not compute
itself. Nothing in M1–M3 is aspirational: every one of those features' criteria
cites a passing test in `2-product-definition.md`, and the ones that could not
be proved are marked `absent` rather than quietly rolled into a `shipped`
feature.

## M4 — Ship: release ergonomics (ACTIVE)

**Delivers F014: cmru adoption plus a zipapp published beside the wheel.**

This is the ship milestone, and it now runs **first** of the remaining work —
ahead of SQL, not after Go (A-248). The reasoning is the one that was already
sitting in this document as the honest alternative to its own ordering: a
zipapp is the near-zero-cost answer for a consumer with no installed Python
manager, which is A-O04's srdm blocker, and every additional package assay
merges before shipping is a package whose value no consumer can reach.

What made this landable rather than a checkpoint is that A-247's two blockers
are rulings, not unknowns: assay adopts cmru's **orchestration** and declines
its **build** (cmru's built-in `wheel-build` is bare `python -m build`, with
none of A-198/A-199's hash-bound closure), and where a cmru `.sha256` sidecar
and assay's own release manifest sit on one Release, the manifest is
authoritative because only it can feed pip's hash mode.

## M5 — A second language (PLANNED)

**Delivers F013 (the SQL/DDL adapter), as package P34.**

P34 was previously "next, unblocked"; A-248 **postpones it behind ship**. It
stays unblocked in the sense that matters — both rulings its carve was waiting
on are landed (A-242, the flat seven-method adapter with raising stubs; A-243,
helper provenance for a failed discovery), so the carve inherits them rather
than making them — but it must not be dispatched until M4 is done.

The trade this milestone rests on is named rather than hidden. A-219 already
gave up A-215's ordering rationale (do not freeze a second-language mutation
contract before the first language is qualified) in order to design schema v5
once for SQL and Go together instead of twice; A-248 does not revisit that, it
only moves the release ahead of the second language.

## M6 — Go, honestly (PLANNED)

**Delivers F008's three absent criteria, as the P27 re-carve through P32.**

Go still comes after SQL, by A-219, and now after ship as well. Two named
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

One thing the re-carve does **not** own, contrary to A-246: the
**effective-PATH preflight** for `MISSING_EXTERNAL_TOOL`. **A-253 gives that to
P34**, which runs first, so P27 only *declares* its statement-position helper in
`external_tools` — restoring what A-163 always said ("P27 only adds the helper
declaration").

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

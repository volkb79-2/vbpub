# assay — the carved v1 package series

Carved 2026-08-06 from the scoping session recorded in
`../decisions.md`; reasoning in `../../docs/DESIGN-GUIDE.md`.

Carving principle (nyxloom CORE REDESIGN, learned when CR-07a landed at 823
changed lines and its own reviewer said it should have been split):

> **Put the thing whose CLAIM needs attacking in its own package, and let the
> volume of mechanical consequence follow separately.**

Every package below names one claim. If a package cannot state its claim in one
sentence, it is two packages.

| # | Package | Claim to attack | Depends on |
|---|---|---|---|
| P01a | skeleton, lane config loader | does the config contract refuse to invent? | — |
| P01b | verdict model, JSON Schema | does the schema REJECT a malformed verdict? | P01a |
| P02 | changed lines, base resolution, measurability | does it refuse to render a verdict it cannot justify? | P01a |
| P03 | coverage parser registry | is coverage format independent of language? | P01a |
| P04 | evaluation core, adapter protocol, Python adapter | did the four-way union land, and is the core language-free? | P01b, P02, P03 |
| P05 | statement-span attribution, `unclassified` | is genuine ambiguity ever passed silently? | P04 |
| P06 | Go adapter, go-cover parser, fixture projects | is the adapter boundary real? | P05 |
| P07 | runner, CLI, verdict emission | does the real exit propagate, and is every outcome recorded? | P06 |
| P08 | canary | does the gate demonstrably reject? | P07 |
| P09 | attested claims and staleness | can assay require evidence it cannot produce, without pretending to have verified it? | P07 |
| P10 | changed-line mutation | do changed lines have non-hollow tests — and does mutation FIT the protocol? | P08 |
| P11 | self-hosting and standalone proof | is the standalone claim real, and is self-gating non-circular? | P10 |

## Three oracles that are structural, not behavioural

These are the ones that keep the design honest, and they are worth knowing
before dispatching anything:

- **P04 O1** — the core must contain no `ast` import and no `.py` glob, proven by
  driving evaluation through a fake adapter. Its negative is how four copies
  happened.
- **P06 O1** — adding the *second* adapter must touch **no core file**, proven by
  `git diff --name-only`. This is P90's O3, made mechanical.
- **P10 O1** — mutation must touch no protocol file either. This is the
  containment for the decision to include mutation in v1 (A-003/A-004): the
  protocol is settled by three consumers before the most idiosyncratic one
  arrives.

## Not in this series

Adoption packages (topos → ciu → dstdns, A-037) are per-consumer and carved
separately once P11 lands. They **declare and verify; they do not remediate**
(A-038) — a project's general test debt is a different job with a different
owner.

## What the P01 pre-flight bought

P01 was dispatched as a pre-flight (orient and report, implement nothing) and
came back **NOT READY** with three blockers and seven ambiguities — all defects
in the specification, none in the plan. The three worth remembering:

- **P01's own deliverable was invalid under P01's own loader.** O1 required
  `source_roots`/`allow_excluded` unconditionally, while Work item 6 required
  assay's own R0 lane to omit them. Closed by A-048: the five `judge` fields are
  *conditionally* required.
- **`BUDGET_EXCEEDED` had no `reason_code`** anywhere in the spec, though A-022
  requires one on every non-PASS outcome. Closed by A-050.
- **O5's grep could not fail.** `grep -rn` prefixes every line with a path
  containing `assay`, and the `-v` alternation contained `assay`, so every line
  was filtered regardless of content — verified passing clean on a file
  importing `requests`, `flask` and a function-level `boto3`. Closed by A-060:
  AST walk against `sys.stdlib_module_names`, never grep.

Also closed beforehand, as controller chores: **A-O01** (assay registered as a
nyxloom project — a package cannot bootstrap the gate that judges it) and
**A-O02** (no image rebuild needed; `tester-unified:local` already carries the
whole closure, which follows from A-005's zero runtime dependencies).

The lesson generalises: **an oracle that cannot fail is worse than no oracle**,
and the cheapest place to find one is before the implementation, not after.

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
| P01 | skeleton, lane config, verdict schema | does the config contract refuse to invent, and can the schema express every outcome? | — |
| P02 | changed lines, base resolution, measurability | does it refuse to render a verdict it cannot justify? | P01 |
| P03 | coverage parser registry | is coverage format independent of language? | P01 |
| P04 | evaluation core, adapter protocol, Python adapter | did the four-way union land, and is the core language-free? | P02, P03 |
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

## Before dispatching P01

Two open items block it (see `../decisions.md`): **A-O01** — whether assay is
registered as a nyxloom project before or as part of P01; **A-O02** — whether
declaring the `tester-unified` gate needs the shared image rebuilt. A rebuild
re-risks ciu, cmru, topos and nyxloom's gate, so it is not a detail.

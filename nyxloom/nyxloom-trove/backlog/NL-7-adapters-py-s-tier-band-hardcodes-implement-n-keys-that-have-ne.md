---
kind: backlog-entry
schema_version: 1
id: NL-7
title: "adapters.py's _TIER_BAND hardcodes implement-N keys that have never been live routes.toml values, silently dead since D-BATCHC"
status: fixed
type: "bugfix"
severity: "low"
component: "gates"
provenance: "nyxloom-P100 adversarial carve review, 2026-09-03; NL-2 origin"
filed_date: "2026-09-03"
closed_date: "2026-09-03"
closed_reason: "nyxloom-P101: retired _TIER_BAND; scope.touch size is now the sole band signal. Option 1 of the two proposed; option 2 (a routes.toml-backed band) needs a per-tier complexity fact neither routes file carries."
---

## Observed mechanism and reproduction

`src/nyxloom/adapters.py:181` declared:

```python
_TIER_BAND = {"implement-1": 1, "implement-2": 2, "implement-3": 3}
```

consumed by `compute_review_depth_directive` to modulate reviewer review-depth
by complexity band (D-BATCHC, 2026-07-26, `plan-factory-hardening.md`, Batch C).

**Corrected by nyxloom-P101 (2026-09-03).** This entry originally said the keys
had "never been real values in the live `routes.toml`" and that the lookup "has
returned `None` for every real handoff". The first half is true on its own
terms; the second is false, and the two do not follow from one another:

- `Routes.load()` reads the DEPLOYED `$XDG_STATE_HOME/nyxloom/routes.toml`,
  which carries pre-B16 names (`flash-high`, `luna-high`, `sonnet5-high`,
  `frontier-review`, ...) and declares no `implement-*` tier. So "not a live
  routable tier" was correct.
- But handoff authors take `tier:` values from the TRACKED `routes.host.toml`,
  which has declared `[tiers.implement-1]` and `[tiers.implement-2]` since the
  B16 rename on 2026-07-23 -- three days BEFORE D-BATCHC shipped `_TIER_BAND`.
  22 archived handoffs declare `tier: implement-2`, including nyxloom-P98 and
  nyxloom-P99. `_TIER_BAND` never consulted any routes file; it matched the raw
  frontmatter string, so for those handoffs it returned `2`, not `None`.

The real defect was therefore not dead code but a **one-way suppressor**.
`implement-3` -- the only key mapping to `_HIGH_BAND` -- exists in neither the
deployed nor the tracked file, so the tier path could never RAISE the band;
while `implement-1`/`implement-2` resolved below `_HIGH_BAND` and
short-circuited the scope-size branch, so it could only ever LOWER it. A
30-file `implement-2` handoff got no high-complexity directive; the same
handoff with no tier field did.

Scope of impact, measured honestly: nyxloom's own gate declares
`asserts = ["tests-pass", "changed-line-coverage", "canary-verified"]` -- no
`mutation` -- so it always reads as "shallow" and the directive was never empty
here; for nyxloom the bug omitted the high-complexity REASON from an otherwise
non-empty directive. For a project whose gate declares `mutation`, the
suppression was total.

## Why nyxloom owns it

`compute_review_depth_directive` is nyxloom's own reviewer-dispatch logic;
`_TIER_BAND`'s premise (that `tier` values look like `implement-N`) is
nyxloom's own doctrine mistake (AUTHORING.md's stale worked example, fixed
by nyxloom-P100), not a consumer-project error.

## Proposed contract

Two independent options, not mutually exclusive:

1. **Retire `_TIER_BAND` and the numeric-band path entirely**, falling back
   permanently to the `scope_touch`-count proxy — honest about the fact this
   signal has never fired in production, if nobody wants to invest in a real
   tier-to-band mapping.
2. **Replace `_TIER_BAND` with a real mapping against live `routes.toml`
   tiers**, analogous to how nyxloom-P100's L14 resolves `fm.tier` — e.g. a
   declared per-tier "band" fact in `routes.toml` itself, or a capability-
   vector-based classification (mirroring `routing-model-redesign.md`'s own
   D-R13 per-axis capability vector idea) rather than a hardcoded
   `implement-N` string match.

Either way: the comment ("D-BATCHC... two ALREADY-EXISTING inputs drive the
band") should stop implying `_TIER_BAND` is live/exercised, since it isn't
and never has been.

## Oracles

- A handoff with a real live tier (e.g. `sonnet5-high`) exercises whichever
  path is chosen (band lookup succeeds if option 2, or the scope-count proxy
  is used deliberately/documented if option 1) — not silently falling
  through an always-`None` dict lookup nobody meant to leave permanently
  dead.
- If option 2: the mapping must read live data (routes.toml or an
  equivalent declared source), never a second hardcoded `implement-N` copy
  — the same anti-pattern NL-2/L14 exists to prevent.

## SPEC ownership

`adapters.py`'s `compute_review_depth_directive` (D-BATCHC,
`plan-factory-hardening.md` Batch C) and whatever doc section defines the
review-depth-by-band contract.

## Provenance

Found by adversarial carve review of nyxloom-P100 (NL-2), 2026-09-02/03:
`nyxloom-trove/reports/nyxloom-P100-CARVE-REVIEW.md` ("A second copy of the
bad pattern the carve's sweep missed"). Not nyxloom-P100's own scope
(different bug, different file) — filed separately per estate convention
rather than fixed opportunistically outside the carve's authorized
scope.touch.

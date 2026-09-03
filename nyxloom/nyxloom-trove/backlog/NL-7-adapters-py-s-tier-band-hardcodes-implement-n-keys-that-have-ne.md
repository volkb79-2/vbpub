---
kind: backlog-entry
schema_version: 1
id: NL-7
title: "adapters.py's _TIER_BAND hardcodes implement-N keys that have never been live routes.toml values, silently dead since D-BATCHC"
status: open
type: "bugfix"
severity: "low"
component: "gates"
provenance: "nyxloom-P100 adversarial carve review, 2026-09-03; NL-2 origin"
filed_date: "2026-09-03"
---

## Observed mechanism and reproduction

`src/nyxloom/adapters.py:181` declares:

```python
_TIER_BAND = {"implement-1": 1, "implement-2": 2, "implement-3": 3}
```

consumed by `compute_review_depth_directive` (~line 192) to modulate reviewer
review-depth by complexity band (D-BATCHC, 2026-07-26,
`plan-factory-hardening.md`, Batch C). It reads the same `Frontmatter.tier`
field (`types.py:434`) that routing (`reconcile.py:1072`,
`rules_dispatch.py:109`) and now `nyxloom lint`'s L14 rule (nyxloom-P100)
consume — but `_TIER_BAND`'s keys (`implement-1`, `implement-2`,
`implement-3`) have never been real values in the live `routes.toml` (see
NL-2), so `_TIER_BAND.get(tier or "")` has returned `None` for every real
handoff since D-BATCHC shipped, silently falling back to the
`scope_touch`-count proxy (`_HIGH_BAND_SCOPE_TOUCH_THRESHOLD`) every single
time. Not a crash — the fallback is a reasonable substitute signal — but the
same false premise NL-2 diagnoses, embedded a second time in unrelated
production logic, undetected because the fallback happens to produce
plausible output.

Found during nyxloom-P100's adversarial carve review (2026-09-03) while
sweeping for other `fm.tier`-hardcoding consumers beyond the routing path.

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

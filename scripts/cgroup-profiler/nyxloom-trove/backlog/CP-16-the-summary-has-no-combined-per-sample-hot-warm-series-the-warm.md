---
kind: backlog-entry
schema_version: 1
id: CP-16
title: "the Summary has no combined per-sample hot+warm series: the warm-set charge ciu v8 admits on can only be approximated from per-class p90s"
status: open
type: "feature"
severity: "medium"
provenance: "ciu v8 proposal rev 4.5 R.3 / SPEC-V8 draft.9 S16.9.5; dstdns D-653 Q4, D-658 (review V8R-33)"
filed_date: "2026-10-03"
---

## Observed mechanism
- `lib/summary.py:651-652` reports the DAMON classification as per-class series: `hot` and `warm`, each as `{peak, p90, median}` (and the same for cold and idle). It has no combined `hot + warm` per-sample series.
- The ciu v8 admission charge is the warm working set: the p90 over a run's per-sample `hot + warm` bytes (ciu `SPEC-V8.md` S16.6.6 and S16.9.5; dstdns D-653 Q4). `hot.p90 + warm.p90` approximates the p90 of the sum but does not bound it, because the two classes' peaks need not coincide.
- Until the Summary carries the sum, the committed manifest records the approximation with `source = "damon-class-sum"`. The target `source` is `damon`.

## Why cgprofile owns it
Only the daemon sees the per-sample series. A consumer cannot reconstruct a per-sample sum from a Summary that keeps only per-class statistics, and re-reading `damon.jsonl` from outside the daemon couples the consumer to its on-disk layout.

## Proposed contract
- An additive field in the Summary's `damon` block: `warm_set_bytes: {peak, p90, median}`, computed per sample as `hot_bytes + warm_bytes` over the same sampling window and thresholds as the class series.
- It is an additive change under contract 1 (a v1.2 by controller ruling), so existing readers are unaffected.
- The block records the thresholds it was classified under (it already does for the classes), so a consumer can report a manifest entry stale when the thresholds change.
- With DAMON unavailable (`damon: "unavailable:<reason>"`) the field is absent, not zero.

## Oracles
- A synthetic series where hot peaks early and warm peaks late yields `warm_set_bytes.p90` strictly below `hot.p90 + warm.p90`.
- With one class constant at zero the field equals the other class's series statistics.
- A Summary from a run with DAMON unavailable has no `warm_set_bytes`.
- A controlled wrong implementation that returns `hot.p90 + warm.p90` fails the first oracle.

## SPEC owner
RG55-INTERFACE-CONTRACT (contract 1, the Summary's `damon` block, §3); cgprofile SPEC summary section.

## Updates
Provenance: ciu v8 proposal rev 4.5 R.3 and SPEC-V8 draft.9 S16.9.5; the adversarial review V8R-33 found the series filed nowhere. CP-15 (the reservation mirror) is a separate, optional item and does not carry this series.

**2026-10-03** — Scope (dstdns D-658, 2026-10-03): the consumer of this series, ciu's warm-set admission, is **v8.1** (SPEC-V8 S21, behind the switch [ciu] admission, default off), not 8.0. The series still improves the lane footprint manifest's warm_set_bytes in 8.0 (SPEC-V8 S16.9.5), where the approximation source damon-class-sum applies until it lands, so the entry is not blocked on v8.1.

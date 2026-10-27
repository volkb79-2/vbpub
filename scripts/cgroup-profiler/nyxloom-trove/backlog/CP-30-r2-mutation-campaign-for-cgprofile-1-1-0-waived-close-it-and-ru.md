---
kind: backlog-entry
schema_version: 1
id: CP-30
title: "R2 mutation campaign for cgprofile 1.1.0 waived by the 2026-10-27 release wave: close it and run the 2026-10-06 survivor oracles"
status: open
type: "bugfix"
severity: "medium"
component: "gate"
context_estimate: "large"
provenance: "Operator decision 2026-10-27 (release wave: R2 waived for every release in the wave, recorded with a backlog item to close it)"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler gate"
priority: 2
filed_date: "2026-10-27"
---

## Source

The 2026-10-27 release wave waived the R2 (assay mutation) campaign for every release it cuts. cgprofile 1.1.0 is gated by the
registered `r0-r1` (unit and coverage) and `r3` (canaries) lanes only. No R2 verdict exists or is claimed for 1.1.0, and none of
the r3 canaries may be read as one. The 1.0.0 release also had no completed full-source R2 verdict.

## Required contract

- Run `run-gate --base <exact-ref> r2` on a clean tree of the first commit after the 1.1.0 release, inspect the exact assay plan first, and
  resume the same tree on expiry. The CGP-110 code (finalize lock, closed-flag guards, `stop-incomplete`, `damon_cleanup`, slice
  bound) is the freshest unmutated surface.
- Two commits of survivor-triage oracle tests exist only on the branch `rg55-followups-cgprofile`: `0426fd156` (Add P6 survivor triage
  oracles; tests for analyze, damon, liveness, serve, serve_placement, serve_socket_carrier, serve_watch, summary) and `8076246c3`
  (pin timeout-prone summary and status branches). Cherry-picking them onto the 1.1.0 tree conflicts in `test_serve.py`,
  `test_serve_placement.py` and `test_summary.py` (CGP-110 reshaped the code under test), so they were not landed in the wave. Port
  them against the current code as part of closing the campaign; re-run R2 to see which survivors remain before porting, because the
  survivor list is stale after CGP-110.

## Oracles

R2 verdict recorded for the exact released tree (or a documented survivor disposition); the ported oracle tests fail against the
mutants they were written for.

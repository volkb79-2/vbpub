---
kind: backlog-entry
schema_version: 1
id: CP-1
title: "daemon session retention policy needs field tuning (count+age defaults chosen blind: 200 sessions / 14 days)"
status: open
type: "bugfix"
severity: "low"
provenance: "RG-55 wave plan sec5 P0, 2026-09-12"
filed_date: "2026-09-12"
---

## Observed mechanism and reproduction

`cgprofile serve`'s session retention (RG-55 contract §3.3, `gc` verb) uses
`--keep-sessions 200 --keep-days 14` as its default bound (RG-55 plan §3.3).
Both numbers were chosen blind, before the daemon had run against any real
workload: no measurement exists yet of how large a typical session's
`sessions/<id>/` directory grows (`samples.jsonl.gz`, `damon.jsonl`,
`events.jsonl`, `host.jsonl`), how many sessions a real host accumulates per
day across every worktree/project sharing the one host-singleton daemon, or
what retention window a consumer (dstdns's `RIGOR-COVERAGE-POLICY.md`
"Resource-profiling and scheduling" section, once adopted) actually needs
kept for trend analysis versus what is safe to let `gc` reclaim.

## Why cgroup-profiler owns it

`gc`'s retention policy and its defaults live entirely in the daemon
(`cgprofile serve`/`cgprofile ctl gc`); no consumer (run-gate, dstdns) has
any input into this number today beyond a future CLI override.

## Proposed contract

Not designed here — this entry tracks the follow-up, not the fix. Once the
daemon has run for real (RG-55 P3's live probes, then real dstdns adoption
traffic), measure actual `sessions/<id>/` directory sizes and real session
arrival rate, then either confirm 200/14 as reasonable or replace them with
measured defaults; consider whether the bound should be host-disk-aware
(a size-based ceiling in addition to count/age) rather than purely count-
and age-based.

## Oracles

Not yet written — needs real measured data first, per the estate's
"measure first, decide later" posture (the same discipline RG-27/RG-55
already apply to run-gate's own history/footprint stores).

## SPEC ownership

`scripts/cgroup-profiler/DESIGN.md` (retention/`gc` section, once P1 writes
it), RG-55 contract §3.3.

## Provenance

RG-55 wave plan §5 P0 (`run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`),
filed by the P0 controller package alongside the frozen interface contract,
2026-09-12.

## Updates

---
kind: backlog-entry
schema_version: 1
id: CP-26
title: "Durable DAMON pool provenance and safe crash-residue reset"
status: open
type: "bugfix"
severity: "high"
component: "damon"
context_estimate: "large"
provenance: "CP-24 Sol xhigh design review, 2026-10-16"
filed_by: "vbpub maintenance"
spec_owner: "CGProfile DAMON pool lifecycle"
priority: 2
filed_date: "2026-10-16"
---

## Observed mechanism

CP-24's per-session `damon-owner.json` records whether one session reserved,
activated, or stopped one kdamond. `KdamondPool.acquire()` can create the
entire global pool by changing `nr_kdamonds`, so that record does not prove
that every slot in the pool belongs to CGProfile or identify the pool's
pre-existing baseline. After a same-boot crash, CP-24 conservatively preserves
the session evidence and refuses to reuse a nonzero pool. CP-25 can quarantine
session evidence but deliberately cannot reset this global resource.

## Required contract

- Add durable pool-level provenance before the first write that creates or
  resizes the pool. Record the exact prior count, resulting count, host boot
  identity, pool generation, and identities/ownership state for every slot.
  The record must distinguish an initially empty pool from a pre-existing or
  foreign pool; a matching count is never ownership proof.
- Define the crash window between recording intent and changing
  `nr_kdamonds`. If the pool's actual count or slot identities cannot be
  reconciled with the durable record, refuse reset and preserve all state.
- Provide a supported recovery verb that can reset only a pool whose durable
  provenance identifies the exact generation, whose baseline is known, whose
  every candidate slot is confirmed off, and whose session owners are all
  absent or terminally safe. It must not run while a collector owns a slot.
- Preserve before-state and per-slot evidence durably before changing sysfs;
  verify the post-reset count and identities before reporting success. Any
  read/write failure, count mismatch, foreign pool, active/ambiguous owner, or
  missing provenance is a refusal with no speculative reset.
- Keep normal profiling available without DAMON when reset is refused, and
  report the exact unavailable reason through `version` and each affected
  Summary.
- Use the supported CIU lifecycle for maintenance. Do not require raw Docker
  commands or an operator assertion that a pool is safe.
- Update README, DESIGN-GUIDE, CONSUMERS, PROTOCOL if wire-visible, CHANGES,
  tests and fixtures in the same implementation package.

## Acceptance oracles

- A pool created from a proved empty baseline is recovered only after every
  slot is observed off and every session owner is safe; the report contains
  the durable pre-reset and post-reset evidence.
- A nonzero pre-existing pool, a partial-create crash, missing or contradictory
  provenance, a foreign slot, an active monitor, or an unreadable counter
  refuses without changing `nr_kdamonds` or any slot.
- Crashes before intent, after intent, after the sysfs write, during slot
  verification, and after reset but before completion are idempotent: each
  restart either proves and completes the same reset or refuses without
  inventing ownership.
- A matching `nr_kdamonds` count without matching pool-generation and slot
  evidence is refused.
- CIU-managed maintenance, the registered CGProfile gate, and a live
  acceptance probe cover success and every refusal class.

## Release boundary

Until CP-26 ships, same-boot pool residue stays untouched and DAMON stays
unavailable when its ownership cannot be proved. Do not fold a count-only
cleanup into CP-24 or CP-25.

## Landing status (2026-10-27 release wave)

Held for a later wave, deliberately not in cgprofile 1.1.0. Nothing here is implemented on main: the normative `DESIGN.md` D-33 section, the PROTOCOL/CONSUMERS/contract edits, the 18 red-first tests, the 29 `cp24-*` JSON fixtures, the 1,123-line implementation packet and the 33 design/contract review reports exist only on the park ref `park/stash-2026-10-17-park-cp-24-25-26-work-from-cgprofile-rel` (commit `f3657994a`, merge of the 2026-10-17 stash; its third parent holds the untracked files). The packet itself is marked "not ready for implementation dispatch": the fixtures were authored at control contract 1 and must be re-authored at contract 2, the public docs still describe contract 1, and the open choices need operator confirmation. The daemon change also requires the matching Run-Gate client (pending-response parsing, busy/deadline handling) to be released first, so it cannot ship ahead of run-gate 24.x. Do not re-derive it: start from the park ref.

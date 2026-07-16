---
schema_version: 1
id: groop-P91-persistent-capped-history
project: groop
title: "Recoverable age-and-byte-capped daemon history"
tier: sonnet5-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: []
scope:
  touch: ["groop/**"]
  forbid: []
oracles:
  - id: O1
    observable: "frames older than the configured age cap are evicted from the persistent tier"
    negative: "a frame older than the age cap remains queryable"
    gate: groop-suite
  - id: O2
    observable: "frames beyond the configured byte cap are evicted least-recent-eligible-first"
    negative: "the store exceeds the configured byte cap without evicting"
    gate: groop-suite
  - id: O3
    observable: "age and byte caps are enforced simultaneously, neither one bypassing the other"
    negative: "satisfying the byte cap alone allows an over-age frame to remain, or vice versa"
    gate: groop-suite
  - id: O4
    observable: "after a restart, recovery reconstructs the store from atomically-published segments/index without exceeding either cap"
    negative: "restart recovery re-admits evicted frames or exceeds a cap"
    gate: groop-suite
  - id: O5
    observable: "a torn/partial write during a crash is recovered without corrupting subsequent segments"
    negative: "a torn write corrupts or loses frames beyond the torn segment itself"
    gate: groop-suite
  - id: O6
    observable: "a corrupt middle segment is quarantined or skipped with an explicit gap while last-good history remains queryable"
    negative: "a corrupt middle segment silently returns wrong data or crashes the query path"
    gate: groop-suite
  - id: O7
    observable: "disk-full or read-only conditions degrade visibly to the RAM tier without crashing collection"
    negative: "a disk-full or read-only condition crashes the collector or silently drops the RAM tier too"
    gate: groop-suite
  - id: O8
    observable: "query results report gaps and evictions truthfully rather than presenting a continuous series"
    negative: "a query reports a gapped or evicted range as continuous data"
    gate: groop-suite
  - id: O9
    observable: "recovered frames are byte-deterministic against what was persisted before the crash"
    negative: "a recovered frame differs from what was persisted before the crash"
    gate: groop-suite
  - id: O10
    observable: "a 24-hour synthetic workload measurement records compression ratio and write amplification against the accepted budget before persistence is enabled by default"
    negative: "persistence is enabled by default without the required resource measurement being recorded"
    gate: groop-suite
gates: [groop-suite, py-compile]
escalate_if: ["both retention caps cannot be enforced after crashes", "corruption recovery would silently discard acknowledged history"]
advances: []
---

# P91 - Recoverable age-and-byte-capped daemon history

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P88
> **Base:** main after P88
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** both retention caps cannot be enforced after crashes, or corruption recovery would silently discard acknowledged history. Prefer a typed degraded store.

## Goal

Implement D-005's two-tier history: five minutes at five-second resolution in
RAM plus a compressed persistent tier capped simultaneously at 24 hours and
256 MiB by default.

## Required contracts

1. Replace/reconcile the current four-hour `HistoryConfig` default explicitly;
   do not silently reinterpret existing config. Strict config exposes interval,
   RAM age, disk age, disk bytes, segment/checkpoint and compression settings.
2. Persist canonical frames once, then query them through P88. The store is not
   a second report/query engine. Lifecycle facts later use the same timestamped
   store and caps.
3. Enforce age and byte caps at all times, including restart recovery. Use
   atomic segment/index publication, bounded startup scanning and least-recent
   eligible eviction; report actual bytes, oldest/newest, frames, gaps,
   evictions, write errors, recovery state and compression ratio.
4. Files are daemon-owned and not exposed as arbitrary paths. Permissions,
   fsync policy and partial-write behavior are documented and tested.
5. Corrupt/truncated segments are quarantined or skipped with an explicit gap;
   last-good history remains queryable. Disk-full/read-only errors degrade to
   the RAM tier visibly without crashing collection.
6. Measure write amplification and CPU/RSS/disk cost at defaults before enabling
   persistence by default; if the accepted budget is not met, ship it configured
   off with the measurements and a typed status.

## Acceptance oracles

Use a tiny-cap deterministic store to prove age eviction, byte eviction, both
caps together, restart recovery, torn writes, corrupt middle segments, disk-full
degradation, permissions, query gap truth and byte-deterministic frame recovery.
Include a 24-hour synthetic workload measurement and compression/write-
amplification report.

## Out of scope

Remote database/export, log ingestion, arbitrary retention, cross-host history,
browser routes and lifecycle-event collection.

## Gates

Focused store/query/daemon tests, zero-skip full suite, compile checks,
`git diff --check`, crash-recovery tests and recorded resource measurements.
Write P91-LOG.md and P91-REPORT.md.

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/groop-p91-persistent-capped-history`
  at `.worktrees/groop-p91-persistent-capped-history` (repo-root-relative, per
  `worktree_root` in `groop/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/groop-p91-persistent-capped-history`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.

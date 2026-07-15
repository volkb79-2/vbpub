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

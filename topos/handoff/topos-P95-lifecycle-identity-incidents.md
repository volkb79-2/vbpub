---
schema_version: 1
id: topos-P95-lifecycle-identity-incidents
project: topos
title: "Stable workload/incarnation lifecycle facts"
tier: sonnet5-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: [topos-P91-persistent-capped-history, topos-P93-lifecycle-owner-protocol]
scope:
  touch: ["topos/**"]
  forbid: []
oracles:
  - id: O1
    observable: "a normal restart produces a recent-exit/restart fact linked to the same stable WorkloadKey"
    negative: "a normal restart fails to link the new incarnation to the prior WorkloadKey"
    gate: topos-suite
  - id: O2
    observable: "a recreate with a new container/cgroup id is linked as Previous instance/Recent exit only when the workload join is proven"
    negative: "a recreate with a new container/cgroup is joined to the wrong workload without proof, or a legitimate recreate is left unlinked"
    gate: topos-suite
  - id: O3
    observable: "repeated failures accumulate as distinct timestamped facts rather than overwriting each other"
    negative: "a repeated failure overwrites the record of a prior failure"
    gate: topos-suite
  - id: O4
    observable: "an unhealthy Docker container surfaces a health fact distinct from a healthy or exited state"
    negative: "an unhealthy container is reported as healthy or as simply exited"
    gate: topos-suite
  - id: O5
    observable: "an OOM exit is recorded as a distinct typed exit fact"
    negative: "an OOM exit is indistinguishable from a normal exit in the recorded fact"
    gate: topos-suite
  - id: O6
    observable: "a vanished or permission-limited owner produces an explicit missing-history/permission fact instead of a guess"
    negative: "a vanished or permission-limited owner is assigned a guessed identity or silently omitted"
    gate: topos-suite
  - id: O7
    observable: "an ambiguous workload join remains a separate record with a typed explanation rather than being forced together"
    negative: "an ambiguous join is silently merged into one workload record"
    gate: topos-suite
  - id: O8
    observable: "the lifecycle event store evicts and recovers under P91's age/byte caps and remains queryable through P88 after recovery"
    negative: "the event store exceeds P91's caps or loses queryability after a recovery"
    gate: topos-suite
  - id: O9
    observable: "exited incarnations appear only as tombstones/events and never contribute to current-frame totals"
    negative: "an exited incarnation's values are included in a current-total aggregation"
    gate: topos-suite
  - id: O10
    observable: "mutation-testing proves the tombstone-exclusion and incarnation-key join logic are both exercised and enforced"
    negative: "a mutation of the tombstone-exclusion or incarnation-key join logic survives undetected"
    gate: topos-suite
gates: [topos-suite, py-compile]
escalate_if: ["a stable logical workload cannot be separated from a concrete runtime incarnation", "an exited object would enter current totals"]
advances: []
---

# P95 - Stable workload/incarnation lifecycle facts

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P88, P91, P93
> **Base:** main after dependencies
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** a stable logical workload cannot be separated from a concrete runtime incarnation, or an exited object would enter current totals. Preserve an unattributed fact instead of guessing identity.

## Goal

Implement D-008/D-010's lifecycle layer: stable logical workloads, concrete
cgroup/container/unit incarnations, recent exit/restart/health facts and derived
Previous instance/Recent exit links in the same bounded persistent store.

## Required contracts

1. Model `WorkloadKey` separately from `IncarnationKey`. Identity uses observed
   systemd/Compose/CIU/Wings/Docker provenance from P93; policy tags are not
   identity. Confidence/conflicts remain visible.
2. Collect bounded systemd active/failed state and restart facts plus Docker
   health, start/finish/exit/OOM/restart facts. Missing history and permission
   limits are explicit. Do not continuously ingest logs.
3. Lifecycle facts are timestamped immutable events in P91's age/byte-capped
   store and query through P88. Current frames contain only live accounting;
   exited incarnations are tombstones/events and never contribute totals.
4. Recreate/restart derives Previous instance and Recent exit links only when
   the stable workload join is proven. Ambiguous joins remain separate with a
   typed explanation.
5. Findings may request bounded P94 evidence around an event time. Store only
   redacted/bounded evidence references or payloads under the same retention
   contract; secrets and arbitrary raw paths never enter lifecycle facts.
6. Expose incident severity, freshness, source, coverage and correlation reason.
   Correlation is deterministic evidence, not a causal claim.

## Acceptance oracles

Fixtures cover normal restart, recreate with new container/cgroup, repeated
failure, unhealthy container, OOM exit, vanished/permission-limited owner,
ambiguous workload join, event-store eviction/recovery and current-total
exclusion. Mutation-test the tombstone exclusion and incarnation-key join.

## Out of scope

Mutation/restart actions, arbitrary logs, external alerting, causal inference,
cross-host identity and Kubernetes watch APIs.

## Gates

Focused lifecycle/owner/store/query tests, zero-skip full suite, compile checks,
`git diff --check` and bounded event-volume/recovery measurements. Write
P95-LOG.md and P95-REPORT.md.

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/topos-p95-lifecycle-identity-incidents`
  at `.worktrees/topos-p95-lifecycle-identity-incidents` (repo-root-relative, per
  `worktree_root` in `topos/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/topos-p95-lifecycle-identity-incidents`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.

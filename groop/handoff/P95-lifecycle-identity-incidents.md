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

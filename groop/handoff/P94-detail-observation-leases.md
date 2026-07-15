# P94 - Shared expiring detail-observation leases

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P88, P90
> **Base:** main after dependencies
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** a provider cannot be bounded by targets/time/cost, or navigation would implicitly start a privileged provider. Leave it manual or disabled; do not hide the cost.

## Goal

Implement D-009's shared daemon-owned lease coordinator and the first bounded
detail providers. Drill-down can automatically observe safe facts; expensive or
privileged observation is started explicitly by hotkey/button and always expires.

## Required contracts

1. Strict config defines global lease duration/target cap plus per-provider
   `disabled`, `manual`, `detail` or `always` activation and provider-specific
   time/row/byte/cost caps. Invalid or unsupported modes fail startup.
2. Leases are keyed by stable entity or P90 `ProcessKey`, reference counted
   across clients, renewed while an eligible detail consumer is active and
   expire 30 seconds after the last renewal by default. Daemon restart clears
   leases; no hidden persistence.
3. Status is queryable through P88 as `off`, `warming`, `active`, `expiring`,
   `unavailable`, `permission-denied` or `error`, with source, coverage, cost,
   effective cap and expiry. Rate providers never fabricate their first delta.
4. Initial safe providers cover bounded listener ownership, open-FD summary and
   blocked/wchan process facts where procfs permissions allow. Finding-scoped
   cgroup/journal/container evidence uses the existing confined readers and
   strict time/line/byte caps; no arbitrary path or continuous follow.
5. Privileged exact PID/device or per-port traffic providers remain manual and
   may ship as unavailable capability descriptors until measured. Broad log and
   eBPF providers never start from navigation.
6. Client loss, PID reuse, entity exit, cap pressure and provider failure release
   resources deterministically and do not affect the base sampler.

## Acceptance oracles

Use fake clock/providers plus procfs fixtures to prove reference counting,
renewal, expiry, target eviction, restart reset, ProcessKey/PID-reuse isolation,
safe auto-lease, manual refusal, warm-up, permission states, bounded outputs and
resource cleanup. Mutation-test the rule that navigation cannot activate a
manual provider.

## Out of scope

Unbounded socket tracing, packet capture, arbitrary file/log browsing,
continuous log ingestion, browser components and privileged provider
implementation without a separate measured gate.

## Gates

Focused provider/lease/query/process tests, zero-skip full suite, compile checks,
`git diff --check` and measured idle/active overhead. Write P94-LOG.md and
P94-REPORT.md.

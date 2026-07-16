---
schema_version: 1
id: groop-P94-detail-observation-leases
project: groop
title: "Shared expiring detail-observation leases"
tier: sonnet5-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: [groop-P90-bounded-process-sampler]
scope:
  touch: ["groop/**"]
  forbid: []
oracles:
  - id: O1
    observable: "a lease's reference count increments and decrements correctly as clients attach and detach"
    negative: "the reference count under- or over-counts relative to actual attached clients"
    gate: groop-suite
  - id: O2
    observable: "an active lease renews while an eligible detail consumer remains active"
    negative: "an active lease expires while a consumer is still eligible and active"
    gate: groop-suite
  - id: O3
    observable: "a lease expires 30 seconds after its last renewal by default"
    negative: "a lease persists or expires at a time other than 30 seconds after its last renewal"
    gate: groop-suite
  - id: O4
    observable: "leases are evicted deterministically when the global target cap is exceeded"
    negative: "the target cap is exceeded without evicting a lease"
    gate: groop-suite
  - id: O5
    observable: "a daemon restart clears all leases with no hidden persistence"
    negative: "a lease survives a daemon restart"
    gate: groop-suite
  - id: O6
    observable: "leases keyed by P90 ProcessKey are isolated across PID reuse"
    negative: "a reused PID's lease is incorrectly reused for the new process"
    gate: groop-suite
  - id: O7
    observable: "safe providers such as listener ownership, open-FD summary and blocked/wchan facts auto-lease on drill-down without an explicit action"
    negative: "a safe provider requires an explicit hotkey or button before it starts observing"
    gate: groop-suite
  - id: O8
    observable: "a manual or privileged provider refuses to start without an explicit hotkey/button and typed confirmation"
    negative: "a manual or privileged provider starts without the explicit action and confirmation"
    gate: groop-suite
  - id: O9
    observable: "permission-denied and error states are reported distinctly for a provider that cannot observe"
    negative: "a permission-denied provider is reported as off or a generic error instead of permission-denied"
    gate: groop-suite
  - id: O10
    observable: "provider outputs stay within the configured time/row/byte/cost caps"
    negative: "a provider output exceeds its configured cap"
    gate: groop-suite
  - id: O11
    observable: "client loss, entity exit and cap pressure release lease resources deterministically without affecting the base CPU/I/O sampler"
    negative: "a released lease leaves resources held, or its cleanup disrupts the base sampler"
    gate: groop-suite
  - id: O12
    observable: "mutation-testing the navigation path proves page navigation alone cannot activate a manual provider"
    negative: "a navigation-only mutation activates a manual provider without the explicit hotkey/button"
    gate: groop-suite
gates: [groop-suite, py-compile]
escalate_if: ["a provider cannot be bounded by targets, time or cost", "navigation would implicitly start a privileged provider"]
advances: []
---

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

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/groop-p94-detail-observation-leases`
  at `.worktrees/groop-p94-detail-observation-leases` (repo-root-relative, per
  `worktree_root` in `groop/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/groop-p94-detail-observation-leases`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.

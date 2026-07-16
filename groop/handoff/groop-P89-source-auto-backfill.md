---
schema_version: 1
id: groop-P89-source-auto-backfill
project: groop
title: "Visible source auto-selection and daemon backfill"
tier: sonnet5-high
input_revision: "f77727e"
session: "resume:p88"
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: []
scope:
  touch: ["groop/**"]
  forbid: []
oracles:
  - id: O1
    observable: "with a compatible daemon reachable, auto source connects to it and labels all output DAEMON"
    negative: "daemon absent or incompatible causes auto source to fall back to local collection labelled LOCAL-DEGRADED with a concise reason and remediation"
    gate: groop-suite
  - id: O2
    observable: "explicit daemon source fails closed when the daemon is unreachable, never silently falling back to local"
    negative: "explicit daemon source silently starts local collection instead of failing closed"
    gate: groop-suite
  - id: O3
    observable: "explicit local source never probes the daemon socket"
    negative: "explicit local source probes the daemon before collecting locally"
    gate: groop-suite
  - id: O4
    observable: "attach fetches the available five-minute fast-tier window through P88 before live polling begins, so there is no blank five-minute warm-up"
    negative: "attach begins live polling with a blank window before any backfill is fetched"
    gate: groop-suite
  - id: O5
    observable: "a source change between daemon and local is recorded as an explicit event and never splices counters across the reset boundary for a rate baseline"
    negative: "counters from the old and new source are spliced across the boundary, producing a false rate delta"
    gate: groop-suite
  - id: O6
    observable: "timeout and backoff on daemon connect attempts are bounded and configurable per the strict source config"
    negative: "daemon connect retries are unbounded or ignore the configured timeout/backoff"
    gate: groop-suite
  - id: O7
    observable: "a test asserting the DAEMON or LOCAL-DEGRADED label is disabled makes the suite fail"
    negative: "the suite passes even with the operator-visible source label assertion disabled"
    gate: groop-suite
gates: [groop-suite, py-compile]
escalate_if: ["fallback would require hiding a privilege/provenance change", "backfill cannot reuse P88 without a second history cache"]
advances: []
---

# P89 - Visible source auto-selection and daemon backfill

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P88
> **Base:** main after P88
> **Session-hint:** resume P88 if warm
> **Serialize-with:** none
> **Escalate-if:** fallback would require hiding a privilege/provenance change, or backfill cannot reuse P88 without a second history cache.

## Goal

Implement D-004's zero-argument source behavior: prefer a compatible daemon,
backfill its recent bounded history immediately, and visibly fall back to local
collection when it is absent or incompatible.

## Required contracts

1. Strict config supports `source = "auto"|"daemon"|"local"`, socket path,
   discovery order, connect/request timeouts and bounded retry/backoff. Defaults
   are documented; invalid combinations fail startup.
2. `auto` probes the configured daemon once with the typed hello/health client.
   On success it labels all output `DAEMON`; on failure it starts local
   collection labelled `LOCAL-DEGRADED` with a concise reason and remediation.
   Explicit `daemon` remains fail-closed and explicit `local` does not probe.
3. Attach fetches the available five-minute fast-tier window through P88 before
   live polling. UI/query status reports observed coverage, gap/eviction,
   backfill state and freshness. No blank five-minute warm-up.
4. Source changes are explicit events. Never splice daemon and local counters
   across a rate baseline; mark the reset/source boundary.
5. Status/CLI/TUI consume the same source state object. No SSH handling, port
   forwarding, discovery broadcast or silent privilege escalation belongs here.

## Acceptance oracles

Use real temporary daemon sockets and fake local collectors to prove daemon
preference, absent/incompatible fallback, explicit-daemon refusal, no probe in
local mode, immediate bounded backfill, source-change reset, timeout/backoff and
operator-visible labels. A disabled label assertion must make the suite fail.

## Out of scope

Persistent storage (P91), web auth/serving (P92), SSH setup, live push and
provider activation leases.

## Gates

Focused daemon/client/CLI/TUI tests, zero-skip full suite, compile checks and
`git diff --check`. Write P89-LOG.md and P89-REPORT.md.

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/groop-p89-source-auto-backfill`
  at `.worktrees/groop-p89-source-auto-backfill` (repo-root-relative, per
  `worktree_root` in `groop/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/groop-p89-source-auto-backfill`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.

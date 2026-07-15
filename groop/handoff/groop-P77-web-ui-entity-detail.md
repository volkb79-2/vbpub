---
schema_version: 1
id: groop-P77-web-ui-entity-detail
project: groop
title: "Web Entity, Incidents and Compare routes"
tier: sonnet5-high
input_revision: "f77727e"
session: "resume:p73"
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: [groop-P73-web-ui-read-only-shell]
scope:
  touch: ["groop/**"]
  forbid: []
oracles:
  - id: O1
    observable: "a stale entity key/incarnation is never silently rebound to a different current entity"
    negative: "a stale key/incarnation is silently rebound to whatever entity currently occupies that slot"
    gate: groop-suite
  - id: O2
    observable: "process detail is keyed by P90 ProcessKey, not PID alone, with visible warm-up/gaps in CPU/read/write rate graphs"
    negative: "process detail is looked up by PID alone and conflates a reused PID with a prior process"
    gate: groop-suite
  - id: O3
    observable: "a safe detail provider auto-starts an expiring lease on drill-down; an expensive/privileged provider requires an explicit hotkey/button and typed confirmation"
    negative: "an expensive or privileged provider starts automatically on drill-down without the explicit action and confirmation"
    gate: groop-suite
  - id: O4
    observable: "the shown lease status (off/warming/active/expiring/error/permission-denied) renews while viewed and releases or expires on exit"
    negative: "the lease continues past page exit, or the shown status does not match the actual lease state"
    gate: groop-suite
  - id: O5
    observable: "Incidents orders active findings and recent lifecycle facts by severity and freshness"
    negative: "Incidents lists findings/facts in an order that ignores severity or freshness"
    gate: groop-suite
  - id: O6
    observable: "evidence queries in Incidents are finding/time scoped with byte/line/duration caps and redaction, never a generic file browser or continuous ingestion"
    negative: "an evidence query allows unscoped or unbounded access, or continuous log ingestion"
    gate: groop-suite
  - id: O7
    observable: "Compare accepts two or three entities and refuses or partitions the comparison when units or semantics do not match"
    negative: "Compare merges series with mismatched units or semantics without refusing or partitioning"
    gate: groop-suite
  - id: O8
    observable: "each Compare series retains its own source, coverage, gaps, resets and incarnation with no interpolation or hidden normalization"
    negative: "Compare interpolates across a gap or silently normalizes a series"
    gate: groop-suite
  - id: O9
    observable: "missing, zero, redacted, warming, stale, permission-denied, no-data and truncated values are visually and textually distinct, and a redacted metric is named but not chartable"
    negative: "two of these typed states render identically, or a redacted metric is plotted on a chart"
    gate: groop-suite
  - id: O10
    observable: "all P77 routes are read-only: no browser action, raw daemon socket, client-side aggregation or full-frame poll"
    negative: "a P77 route performs a mutation, opens a raw daemon socket, aggregates client-side or polls a full frame"
    gate: groop-suite
  - id: O11
    observable: "PWMCP drives row-to-entity navigation, CPU/I/O drill-down, manual lease activation, incident evidence, three-entity compare, browser back/forward/shareable URL state and disconnect handling with zero console errors"
    negative: "a PWMCP run shows a console error or fails to exercise one of these interactions"
    gate: groop-suite
gates: [groop-suite, py-compile]
escalate_if: ["detail evidence needs arbitrary file/log access", "comparison needs unbounded client joins", "lifecycle/process identity is unavailable"]
advances: []
---

# P77 - Web Entity, Incidents and Compare routes

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P73, P90, P91, P94, P95
> **Base:** main after dependencies
> **Session-hint:** resume P73 if warm
> **Serialize-with:** P73 (shared React asset tree)
> **Escalate-if:** detail evidence needs arbitrary file/log access, comparison needs unbounded client joins, or lifecycle/process identity is unavailable. Render typed unavailability instead of inventing identity.

## Goal

Complete D-018's diagnostic surface with addressable `/entity/:key`,
`/incidents` and bounded `/compare` routes. An operator can identify the
cgroup/container/process responsible, see when CPU or I/O happened, understand
coverage, and compare at most three compatible entities.

## Required contracts

1. Entity shows observed identity and separately-provenanced owner/policy tags,
   registry-backed metrics, findings, bounded raw/summary history, process
   candidates, per-device I/O attribution when available, lifecycle facts and
   provider/detail-lease status. A stale key/incarnation is not silently rebound.
2. Process detail uses P90 `ProcessKey`, not PID alone, and graphs CPU/read/write
   rates with visible warm-up/gaps. It shows cgroup, systemd, Docker and CIU joins
   with provenance so “who did I/O when?” is answerable.
3. A safe detail provider may auto-start an expiring lease on drill-down; an
   expensive/privileged provider requires an explicit hotkey/button and typed
   confirmation. The page shows `off`, `warming`, `active`, `expiring`, `error`
   and permission state, renews while viewed and releases/lets expire on exit.
4. Incidents order active findings and recent lifecycle facts by severity and
   freshness. Evidence queries are finding/time scoped with strict byte/line/
   duration caps and redaction; no generic file browser, log tail or continuous
   ingestion.
5. Compare accepts two or three entities and one bounded metric/window set.
   Units and semantics must match or the UI refuses/partitions the comparison.
   Each series retains source, coverage, gaps, resets and incarnation. No
   interpolation and no hidden normalization.
6. Missing, zero, redacted, warming, stale, permission-denied, no-data and
   truncated are distinct in values and charts. A redacted metric remains named
   and is explicitly not chartable.
7. Read-only. All routes consume P88/P92 bounded queries and P81 redaction. No
   browser action, raw daemon socket, client-side aggregation or full-frame poll.

## Acceptance oracles

Pure view-model tests cover typed value states, gap discontinuities, compatible
and refused comparisons, stable incarnation URLs and lease status. Real gateway
tests cover unknown/expired entity and process keys, redaction, empty and evicted
windows, I/O-hot history, per-device attribution, lifecycle ordering and bounded
evidence. PWMCP drives row-to-entity navigation, CPU/I/O process drill-down,
manual lease activation, incident evidence, three-entity compare, browser
back/forward/shareable URL state, disconnect and no console errors.

## Out of scope

Mutations, arbitrary dashboards, arbitrary logs/files, continuous log ingestion,
more than three entities, live push, SSH handling and new lifecycle adapters.

## Gates and evidence

Run P73's pinned JS gates, focused Python gateway/query tests, the zero-skip full
suite, wheel install/build checks, `git diff --check` and live PWMCP checks.
Write P77-LOG.md/P77-REPORT.md and update `docs/WEB-UI.md`, `CONTRACTS.md`,
`docs/ROADMAP.md` and `docs/STATUS.md`.

## Conversion addendum (handoffctl2 execution notes)

- **Worktree:** create a git worktree for branch `feat/groop-p77-web-ui-entity-detail`
  at `.worktrees/groop-p77-web-ui-entity-detail` (repo-root-relative, per
  `worktree_root` in `groop/.handoffctl/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/groop-p77-web-ui-entity-detail`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.

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

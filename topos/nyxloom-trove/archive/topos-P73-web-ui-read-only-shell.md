---
schema_version: 1
id: topos-P73-web-ui-read-only-shell
project: topos
title: "Web Overview and Explore routes"
tier: sonnet5-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: [topos-P89-source-auto-backfill, topos-P92-loopback-web-transport]
scope:
  touch: ["topos/**"]
  forbid: []
oracles:
  - id: O1
    observable: "Overview fetches only bounded P88 projections (status, findings, top rows, lifecycle summary) and never fetches or aggregates a full canonical frame"
    negative: "Overview fetches a full canonical frame or aggregates it client-side"
    gate: topos-suite
  - id: O2
    observable: "Explore's tree/group sort stays sibling-local, and switching to a global rank visibly switches the projection to flat"
    negative: "a global rank is applied while still showing a tree/group projection without switching to flat"
    gate: topos-suite
  - id: O3
    observable: "window/selector/projection/profile/sort/filter state lives in the URL and is shareable, while the capability token is never part of URL state"
    negative: "the capability token appears in URL state, or the view is not reproducible from the URL alone"
    gate: topos-suite
  - id: O4
    observable: "bad or stale URL state yields typed guidance and safe defaults rather than a blank page"
    negative: "bad or stale URL state renders a blank page with no guidance"
    gate: topos-suite
  - id: O5
    observable: "status chrome always shows DAEMON or LOCAL-DEGRADED, observed window, gap/eviction/reset, freshness, truncation and provider warm-up"
    negative: "status chrome omits the source label or presents stale data without a freshness indicator"
    gate: topos-suite
  - id: O6
    observable: "polling cancels in flight, pauses while the tab is hidden, and backs off on repeated failures, with stale or disconnected last-good data visibly labelled"
    negative: "polling continues while the tab is hidden, or stale last-good data is shown without a stale label"
    gate: topos-suite
  - id: O7
    observable: "charts never interpolate across a data gap and never use an area chart for a non-additive hierarchy value"
    negative: "a chart interpolates across a gap or renders a non-additive hierarchy value as an area chart"
    gate: topos-suite
  - id: O8
    observable: "the built React bundle is committed as package data and pip install topos / runtime does not require Node"
    negative: "runtime or installation requires Node to serve the built assets"
    gate: topos-suite
  - id: O9
    observable: "Python integration tests drive a real DaemonApi/client/P92 gateway and prove bounds, redaction and security independent of a browser"
    negative: "bounds or redaction are only exercised through the browser, with no independent Python-level proof"
    gate: topos-suite
  - id: O10
    observable: "PWMCP browser tests via topos/pwmcp on CIU assert Overview, Explore, shareable non-secret URL state, disconnected/stale rendering, keyboard navigation, landmark/table semantics and zero console errors, with the fixture gateway publishing no host port"
    negative: "a PWMCP run shows a console error, publishes a host port, or fails to exercise one of these assertions"
    gate: topos-suite
gates: [topos-suite, py-compile]
escalate_if: ["a page needs full-frame polling", "a page needs client-side aggregation", "a page needs token material in a URL", "a page needs a second query schema"]
advances: []
---

# P73 - Web Overview and Explore routes

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P89, P92
> **Base:** main after dependencies
> **Session-hint:** fresh
> **Serialize-with:** P77 (shared React asset tree; P73 lands first)
> **Escalate-if:** a page needs full-frame polling, client-side aggregation, token material in a URL, or a second query schema. Do not bypass P88/P92.

## Goal

Ship D-018's first two React routes: `/` Overview for rapid triage and `/explore`
for explicit projection/profile/window/filter/sort queries. This is the first
user-facing web slice, over the already-proven same-origin P92 boundary.

## Required contracts

1. Overview shows host/source/coverage/freshness status, active findings, top
   pressure, top CPU/memory/I/O rows and lifecycle summary using bounded P88
   projections. It does not fetch or aggregate a full canonical frame.
2. Explore separates projection, visibility and profile. Supported projections
   initially include hierarchy, flat cgroups, containers, services, processes
   when available and CIU stacks. Tree/group sorts are sibling-local; global
   ranks visibly switch to flat projection.
3. Window, selector, projection, profile, sort and filters are URL state so a
   view is shareable; the capability token is never URL state. Bad/stale state
   yields typed guidance and safe defaults, not a blank page.
4. Persistent status chrome always reports `DAEMON` or `LOCAL-DEGRADED`, observed
   window, gap/eviction/reset, freshness, truncation, provider warm-up and
   permissions. Typed P81 redaction markers remain visible and explained.
5. Poll projected current/summary data with cancellation, tab-hidden pause and
   bounded backoff. Stale/disconnected last-good data is labelled; it never
   impersonates live data. No push transport in this package.
6. Visual semantics are truthful: no interpolation across gaps, no area chart
   for non-additive hierarchy values, units/semantics come from registry/query
   metadata, and missing/redacted/zero/warming/stale remain distinct.
7. React toolchain and lockfile are pinned; build during release and commit the
   packaged bundle. Node is not required by `pip install topos` or runtime.

## Test layers

Pure TypeScript view/query state and formatting tests cover URL parsing, sort
semantics, typed value states and chart gaps. Python integration tests drive a
real DaemonApi/client/P92 gateway and prove bounds/redaction/security independent
of a browser. PWMCP browser tests use `topos/pwmcp` via CIU and assert Overview,
Explore, shareable non-secret URL state, disconnected/stale rendering, keyboard
navigation, landmark/table semantics and no console errors.

The browser gateway may bind to the CIU consumer network only in the test
fixture, remains token-protected, and publishes no host port. Production stays
loopback; operators provide SSH port forwarding themselves.

## Out of scope

Entity/Incidents/Compare (P77), mutation, free-form dashboards, live push,
SSH lifecycle, arbitrary logs/files and browser-side metric calculations.

## Gates and evidence

Run pinned JS lint/type/unit/build gates, focused Python integration tests, the
dependency-complete zero-skip full suite, built-wheel install without Node,
`git diff --check`, and live PWMCP checks. Record wheel byte delta, bundle hash,
exact CIU invocation and screenshots/accessible assertions in P73-REPORT.md;
write P73-LOG.md and update `docs/WEB-UI.md`.

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/topos-p73-web-ui-read-only-shell`
  at `.worktrees/topos-p73-web-ui-read-only-shell` (repo-root-relative, per
  `worktree_root` in `topos/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/topos-p73-web-ui-read-only-shell`
- **Context to read first:** the Goal, Required contracts, Test layers and Out of
  scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.

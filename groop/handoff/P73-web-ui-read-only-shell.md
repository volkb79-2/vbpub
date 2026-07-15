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
   packaged bundle. Node is not required by `pip install groop` or runtime.

## Test layers

Pure TypeScript view/query state and formatting tests cover URL parsing, sort
semantics, typed value states and chart gaps. Python integration tests drive a
real DaemonApi/client/P92 gateway and prove bounds/redaction/security independent
of a browser. PWMCP browser tests use `groop/pwmcp` via CIU and assert Overview,
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

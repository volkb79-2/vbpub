# P73 - Read-only web UI shell (overview + triage)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high
> **Depends-on:** P67 (re-carved, hardened gateway - MUST be merged first), P69 (merged - the scoping analysis)
> **Base:** main after P67 merge
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** the gateway cannot serve a page without a mutation route or a new daemon op; or D-001 is decided against the static-client assumption below before you are dispatched (then this handoff's stack section is re-carved, not improvised around)

<!--
CARVE SOURCE (controller-workflow-v2 §8): **product-goal-driven**.
Standing user priority (docs/ROADMAP.md, 2026-07-13): "get the product in front
of users with a web UI." P69 scoped it; this is the first package that puts a
pixel on a screen. It is deliberately the SMALLEST such package: one page.

CARVE ASSUMPTION (D-001, OPEN): a dependency-free single static client, per
P69's recommendation. D-001 is not a blocking hold -- the page inventory, data
flow, redaction UX, and tests below are identical under any stack choice; only
the "How to build it" section changes if the user picks otherwise. Recorded per
§8 ("non-blocking gaps are carved around with the assumption recorded").
-->

## Goal

Ship the **overview / triage page**: the first groop surface a non-CLI user ever
sees. Host banner, top pressure, and a sortable/filterable entity table, served
by P67's hardened gateway, refreshed by polling.

One page. Entity detail, history charts, and live push are later packages -- P69's
page inventory has them, and they are worth nothing if the first page is not
trustworthy.

## Why this is small on purpose

The TUI already encodes years of product decisions about what an operator needs to
see: the banner's host verdict / resource summary / swap backend / top-three
pressure list (`src/groop/ui/banner.py:16-46, 72-90`), the table's triage columns
and profiles (`src/groop/ui/table.py:15-33`), its filter over display name or
cgroup key (`table.py:298-309`), and its numeric-desc / name-asc sort convention
(`table.py:312-319`).

**Mirror those. Do not redesign them.** A web overview that disagrees with the TUI
about what matters is a second product, not a frontend.

## Required Contracts

1. **Read-only, and structurally so.** No mutation route is called, because P67
   does not expose one. Actions/DAMON/squeeze keep their root/admin/typed-
   confirmation/audit posture; a browser does not shortcut it. State this as an
   explicit non-goal in the docs you write.
2. **Redaction is the server's job; the UI renders the marker.** P67 replaces
   values above the viewer's ceiling with a typed marker before the bytes leave
   the process. The UI must render that marker as a *typed, explained* cell --
   never `-`, never `0`, never an empty cell, never a dropped row. Those spellings
   already mean "unavailable/missing" in the TUI (`table.py:341-365`) and
   conflating "you may not see this" with "this does not exist" is a lie to the
   operator.
3. **Never render a stale frame as if it were live.** Every view carries the
   frame's timestamp and sequence. On a failed/slow poll the UI shows an explicit
   stale/disconnected state. A silently frozen dashboard is the worst failure mode
   a monitoring UI has.
4. **Polling is bounded and self-cancelling.** Poll `current` every 5s while the
   page is visible; cancel obsolete in-flight polls; never retry-storm a failing
   gateway (bounded backoff). Pause polling when the tab is hidden.
5. **Typed gateway errors become typed UI states.** `unavailable`, `server_busy`,
   `oversized_response`, and connect failures each get a distinct, human-readable
   state. Never render a raw error string or a stack trace into the DOM.
6. **Response budget is real.** A full `current` frame at gstammtisch scale (~89
   entities) is ~447 KB (P53's measurement -- cite it, do not re-derive), i.e.
   ~5.2 MiB/min at a 5s poll before compression. Do not add a second concurrent
   full-frame poller. If you need the same frame twice, reuse it.

## How to build it (D-001 assumption: static client)

Plain HTML + CSS + ES modules, no Node, no framework, no lockfile, no vendored
library. Served by P67 as static assets. Keep all rendering/formatting logic in
pure functions that take decoded JSON and return strings/DOM -- that is what makes
it testable below without a browser.

Note the packaging consequence, which is real and which the user should not be
surprised by: **these assets grow the plain `pip install groop` wheel.** Python
extras select *dependencies*, not *package data*, so a `groop[web]` extra cannot
make same-wheel assets conditional. P69 accepted small, audited core-wheel growth
for v1; say so in the REPORT with the actual byte delta.

## Acceptance Oracles (numbered, adversarial)

Deterministic pytest, no browser, no Node. Stand up a real `DaemonApi` -> real
`DaemonClient` -> real P67 gateway, and drive it with the stdlib HTTP client.

1. **The page renders from a real frame**, not a fixture blob: fetch the served
   HTML/JS, and assert the pure render functions produce the expected banner line
   and table rows from the decoded `current` response.
2. **A `sensitive` metric is never in the bytes.** Give the viewer an
   `operational` ceiling; grep the full HTTP response body for the raw sensitive
   value. If it appears anywhere -- payload, inline JSON, HTML comment -- fail.
   Assert the typed marker renders in that cell **and the key/label survive**.
3. **Stale state is visible.** Freeze the gateway (or advance the clock past the
   poll interval) and assert the rendered output carries the stale indicator and
   the last-good timestamp -- not a silently-unchanged table.
4. **A failing gateway does not retry-storm.** Count requests against a gateway
   returning 503 over a fixed window; assert bounded backoff, not one-per-tick.
5. **Sort and filter match the TUI's convention** (numeric desc, name asc; filter
   over display name or cgroup key) on the same input frame -- assert against the
   TUI's own helpers so a divergence is a test failure, not a UX drift nobody
   notices.
6. **No mutation verb is reachable** from any asset: grep the shipped JS for
   `POST`/`PUT`/`PATCH`/`DELETE`; assert none.

## Out Of Scope

- Entity detail, drill-down, history charts (next package, per P69's inventory).
- P68 live push / WebSocket (P73 polls; push is a later transport swap).
- The process list: the TUI reads it from `cgroup_root`/`proc_root` locally
  (`src/groop/ui/drill.py:318-329`), which a browser cannot do and which P52 does
  not serve. Omit it and make the absence explicit; do NOT invent a process
  endpoint.
- Auth implementation (P67 owns it; you consume an authenticated session).
- Mobile/responsive, theming, i18n, accessibility beyond sane semantics.

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest <focused P73 tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

State the environment for each result. Report the wheel byte delta from the added
assets. Write P73-LOG.md / P73-REPORT.md and a short `docs/WEB-UI.md` operator
note (how to serve it, what the trust posture is, what it deliberately cannot do).

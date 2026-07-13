# P73 - Read-only web UI shell (overview + triage)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high
> **Depends-on:** P67 (re-carved, hardened gateway - MUST be merged first), P69 (merged - the scoping analysis)
> **Base:** main after P67 merge
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** the gateway cannot serve a page without a mutation route or a new daemon op; or the pwmcp browser stack cannot be brought up via CIU (then report what blocks it -- do NOT hand-roll a browser harness, and do NOT silently drop the browser-level oracles)

<!--
CARVE SOURCE (controller-workflow-v2 §8): **product-goal-driven**.
Standing user priority (docs/ROADMAP.md, 2026-07-13): "get the product in front
of users with a web UI." P69 scoped it; this is the first package that puts a
pixel on a screen. It is deliberately the SMALLEST such package: one page.

STACK IS DECIDED, NOT ASSUMED (D-001, DECIDED 2026-07-13): **React**, tested via
**pwmcp**. Standing user decision recorded in docs/ROADMAP.md (commit f14e9dd).
This is not open for the implementer to re-litigate, and it supersedes P69's
static-client recommendation -- read P69's framework section as packaging-
consequence analysis against React, not as a competing option.

pwmcp deployment: the decision left the choice to this carve. **Picked (b): a
vbpub-scoped pwmcp instance started via CIU.** `pwmcp/` is already a first-class
area on main (42 files) and ships CIU compose templates
(`pwmcp/ciu.compose.yml.j2`), so the in-repo path is cleaner than cross-repo
reuse of dstdns's running instance *and* cheap -- which removes the
resource-constraint argument that motivated option (a).
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

## How to build it (React - decided, D-001)

React SPA, built to a static bundle that P67 serves. Keep every rendering and
formatting decision in **pure functions over decoded JSON** (frame -> view model
-> cells), separate from components: those pure functions are what the
deterministic pytest oracles below assert against, without a browser in the loop.
A component tree that computes its own numbers inline is not reviewable and is a
review reject.

**Packaging: Node is a release dependency, never an end-user one.** This is the
consequence P69 identified and it lands harder under React than under the static
client it recommended:

- Build the bundle at release time and **commit the built artifact**, so
  `pip install groop` pulls no Node, no lockfile, and no JS dependency tree.
- The bundled assets **grow the plain `groop` wheel**. Python extras select
  *dependencies*, not *package data*, so a `groop[web]` extra cannot make
  same-wheel assets conditional. Report the **actual wheel byte delta** in the
  REPORT -- a number, not "small".
- Pin the toolchain and commit the lockfile. An unpinned JS dependency tree in a
  release path is a supply-chain surface, and this UI serves telemetry behind an
  auth boundary.

## Acceptance Oracles (numbered, adversarial)

Two layers, and the split is deliberate: **the security contracts are proven in
pytest, not in the browser.** A browser-level suite needs a container stack to
run; a security oracle that can be skipped when the stack is down is not a gate.

### Layer 1 - deterministic pytest (no browser, no Node)

Stand up a real `DaemonApi` -> real `DaemonClient` -> real P67 gateway, and drive
it with the stdlib HTTP client. These must pass in the normal suite with no pwmcp
running.

1. **Pure render functions produce the expected cells** from a decoded `current`
   response: the banner line and the table rows, asserted as values -- not a
   snapshot blob whose diff nobody reads.
2. **A `sensitive` value is never in the bytes.** Give the viewer an
   `operational` ceiling and grep the **entire HTTP response body** for the raw
   value: the JSON payload, the served HTML, and **any hydration/initial-state
   payload React embeds in it**. If it appears anywhere, fail. This is the oracle
   React makes *more* dangerous, not less: a value can be absent from every
   rendered component and still sit in a serialized state blob in the document.
   Assert the typed marker renders in that cell **and the key/label survive**.
3. **Stale state is visible.** Freeze the gateway (or advance the clock past the
   poll interval) and assert the view model carries the stale indicator and the
   last-good timestamp -- not a silently-unchanged table.
4. **A failing gateway does not retry-storm.** Count requests against a gateway
   returning 503 over a fixed window; assert bounded backoff, not one-per-tick.
5. **Sort and filter match the TUI's convention** (numeric desc, name asc; filter
   over display name or cgroup key) on the same input frame -- assert against the
   TUI's own helpers, so a divergence is a test failure rather than UX drift
   nobody notices.
6. **No mutation verb is reachable** from the shipped bundle: grep it for
   `POST`/`PUT`/`PATCH`/`DELETE`/`fetch(...{method:`; assert none.

### Layer 2 - pwmcp browser-level (option (b): vbpub-scoped instance via CIU)

Bring the pwmcp stack up **with CIU** (`pwmcp/ciu.compose.yml.j2`), not a bespoke
script. Record in the REPORT which pwmcp deployment you used and how you started
it, so the next package can reproduce it.

7. **The page actually renders in a real browser** against a live gateway: the
   banner and table appear, populated, with no console errors.
8. **The redaction marker is what a human sees** in the restricted cell -- the
   rendered text, not the DOM attribute. Layer-1 oracle 2 proves the bytes are
   clean; this proves the UI explains *why* the cell is restricted rather than
   showing an empty box.
9. **A disconnected gateway degrades visibly** rather than freezing: kill the
   gateway, assert the stale/disconnected state is on screen.

If the pwmcp stack cannot be started, **that is a BLOCKED exit, not a reason to
delete layer 2** -- write what blocked it and stop.

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

Plus the React build (pinned toolchain, committed lockfile, committed bundle) and
the pwmcp browser layer started via CIU.

State the environment for each result. Report:
- the **wheel byte delta** from the committed bundle (a number),
- which **pwmcp deployment** you used and the exact CIU invocation that started it,
- confirmation that `pip install groop` needs **no Node**.

Write P73-LOG.md / P73-REPORT.md and a short `docs/WEB-UI.md` operator note (how
to serve it, what the trust posture is, what it deliberately cannot do).

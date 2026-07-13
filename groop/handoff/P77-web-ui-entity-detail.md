# P77 - Web UI Entity Detail And Bounded History

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high
> **Depends-on:** P67 (hardened gateway), P73 (read-only web shell)
> **Base:** main after P73 merge
> **Session-hint:** resume the P73 web-UI session if still warm; else fresh
> **Serialize-with:** P73 (shared web-UI asset tree and gateway routes)
> **Escalate-if:** a named contract cannot be met as specified; the drill-down needs a gateway route or a daemon read op that P67/P52 do not expose

<!--
CARVE NOTE (2026-07-13, frontier pass #2 on P58 v4, controller-workflow-v2 §8):
Carve source: PRODUCT-GOAL-DRIVEN (source 3 -- the standing user priority "get the
product launched with the new UI", which §8 says outranks both other sources).

This is P69c from the draft successor table in `docs/WEB-UI-SCOPING.md` ("Entity
detail and bounded recent history", sonnet5-high, depends P69b). Its two siblings
are already carved and in flight: P69a -> P67 (re-carved hardened gateway),
P69b -> P73 (read-only shell). P69d (live subscribe updates) stays uncarved: it
depends on P68, it is marked optional in the scoping doc, and polling is the
committed fallback -- carving it now would be speculative.

Stack is React, browser-tested via pwmcp, per standing user decision D-001
(DECIDED 2026-07-13, `docs/DECISIONS-INBOX.md`). P73's carve already picked a
vbpub-scoped pwmcp instance started via CIU; this package inherits that choice
rather than re-litigating it -- confirm what P73 actually landed and follow it.

Depends on P73, which is not merged yet. This is a QUEUED carve, not a dispatchable
one: the controller must not dispatch it until P73 merges (per the Depends-on
header). It is carved now because the queue's roadmap/goal floor requires it and
because the scoping analysis is warm.
-->

## Goal

Add the entity drill-down to the read-only web UI: click an entity in P73's overview
table and see that one entity's detail -- its registry-backed metrics with units, its
findings, and a bounded recent history chart for one metric at a time -- so an
operator can answer "what is wrong with *this* container, and did it just spike?"
without dropping to the CLI. This is the page that makes the web UI diagnostic
rather than merely informative.

## Dependency And Workflow

- Starts ONLY after P73 merges. Consumes P67's hardened gateway routes exclusively;
  the browser never talks to the daemon socket, and this package adds no new daemon
  protocol surface. If a needed read is not exposed by P67, propose the route in the
  REPORT and BLOCK -- do not add a bypass.
- Branch: `feat/groop-p77-web-ui-entity-detail`
- Worktree: `.worktrees/groop-p77-web-ui-entity-detail`
- Touch only `groop/**`; write P77-LOG.md/P77-REPORT.md; commit, do not merge.

## Context To Read First (bounded)

`groop/README.md` (Workflow protocol), this handoff, `groop/docs/WEB-UI-SCOPING.md`
(especially "Smallest useful page inventory", "Sensitivity and redaction UX", and
"Trust boundary: verdict on P67"), the merged P73 handoff + its REPORT + the web-UI
asset tree it landed, the merged P67 gateway routes, `groop/CONTRACTS.md` §10
(sensitivity enum) and §11 (the P58 MCP frontend's bounds -- **the closest existing
precedent for "bounded reads shaped for a token/byte budget", and its review history
is the cheapest available lesson**), and `docs/DECISIONS-INBOX.md` D-001. Do not read
DAMON/BPF, actions, squeeze, or record/replay code.

## Required Contracts

### The page

- Entity detail is reached from P73's overview table (row click / explicit link) and
  is addressable by URL, so an operator can share a link to a container's detail.
  A bad/stale entity key in the URL renders a typed not-found state, not a blank page
  and not a crash.
- Detail shows: entity key, docker name when present, kind/parent/tier, the entity's
  metrics **with units and semantics sourced from the registry metadata the gateway
  returns** (do not hardcode units in the browser -- that is a second source of truth
  and it will drift), and findings with severity.
- `None`-valued metrics are omitted, not rendered as `null`, `-`, or `0`. A metric
  that is absent and a metric that is genuinely zero must not look the same.

### Redaction (the contract most likely to be got wrong)

- Server-side redaction is rendered as an explicit typed marker, **never as a blank,
  a dash, or a zero**. A redacted value and a missing value are different states and
  must be visually distinct. This is P73's stated contract; the detail page and the
  history chart both inherit it.
- A redacted metric must never be silently dropped from the page: the key stays, the
  value is marked. Dropping the key hides the existence of the metric, which is a
  different (and worse) disclosure decision than redacting its value.
- **The history chart is the new redaction surface P73 did not have.** A redacted
  metric cannot be charted. It must render an explicit "redacted -- not chartable"
  state, not an empty chart, not a flat line at zero, and not a chart of nulls.

### Bounded history (the byte/perf budget)

- History is fetched for **one metric of one entity at a time**, on demand -- never
  eagerly for every metric, and never for every entity. The bound is a product
  contract, not an optimization: a full frame is ~447 KB and an unbounded history
  fan-out is what makes this page unusable on a real host.
- The point count is explicitly bounded and the bound is stated in the UI, so an
  operator knows they are looking at a window rather than all of history.
- **History gaps are explicit.** P52's history op reports a `gap` flag; the daemon's
  ring buffer can drop frames. A gap must render as a visible discontinuity, never as
  a straight line interpolated across missing data -- an interpolated gap is a
  fabricated measurement, and this is a diagnostic tool.
- An empty history window (no frames yet) renders as "no data in this window", not as
  an error and not as a zero series. (This exact confusion was a P58 review-fix: its
  history tool reported an empty window as an invalid selector. Do not repeat it one
  layer up.)

### Boundaries

- Read-only. No mutation control, no action button, no process list, no file path
  reaches the browser.
- No new gateway route unless P67 already exposes it; no direct daemon socket access.

## Required Deterministic Tests

Follow whatever test seam P73 established, and do not weaken it:
- Pure view-model / formatting functions are fixture-tested deterministically:
  metric rendering with units from registry metadata, omitted-vs-zero, redacted
  marker, findings by severity.
- Gateway-level tests against deterministic JSON fixtures for the detail and history
  routes: happy path, unknown entity key, redacted metric, empty history window, and
  a history response carrying `gap: true`.
- **The gap case asserts a rendered discontinuity**, not merely that the flag was
  parsed. Assert the observable output, not the intermediate.
- **The redaction cases assert the marker is present in the rendered output**, and
  that a redacted metric is not silently absent. A test that only checks the value is
  not the raw number would pass if the key were dropped entirely -- that is a hollow
  test; assert the key survives.
- Browser-level checks via pwmcp per D-001, following exactly the instance/deployment
  choice P73 landed (do not stand up a second, differently-shaped browser harness).
  Assert observable page state, not internal call counts.

## Gates And Evidence

```bash
PYTHONPATH=groop/src python3 -m pytest <focused P77 tests> -q -W error
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
python3 -m py_compile <all changed/new files>
git diff --check
```

Plus whatever asset/JS gate P73 established -- run it, and state in the REPORT which
environment each result came from. A live browser session driven through pwmcp is
strong evidence: if you run it, show what you observed; if you could not, say so
plainly rather than implying coverage you do not have.

Update `docs/WEB-UI-SCOPING.md` (mark P69c landed and record any deviation from its
analysis), `README.md`, `CONTRACTS.md` (the web UI's redaction and history-gap
rendering contracts), `docs/ROADMAP.md`, `docs/STATUS.md`.

## Out Of Scope

- **Live/streaming updates** (P69d): consuming P68's subscription. Polling stays.
- Any mutating action, squeeze, DAMON control, or admin verb in the browser.
- Auth/authn beyond what P67 landed as the trust boundary.
- Changing the gateway's routes, the P52 wire, or the P63 client.
- Re-litigating the framework choice (D-001: React, pwmcp) or the pwmcp deployment
  option P73 picked.

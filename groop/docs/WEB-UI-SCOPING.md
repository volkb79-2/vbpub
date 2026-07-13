# Web UI scoping

**Scope:** a read-only browser frontend over P67's HTTP adapter.  It is not a
browser control plane: no DAMON, actions, configuration, Docker, systemd, BPF,
or squeeze mutation is in scope.  Those flows require the existing
root/admin/confirmation/audit posture and must not be bypassed by a browser.

## Evidence and operating assumptions

This analysis audits the merged P52/P63 implementation, not merely its
documentation.  Source citations below use repository `file:line` pointers.
The response-size budget is P53's live finding: a pretty-printed full frame
with 89 entities is about 447 KB [P53 handoff:91-96].  It is a planning budget,
not a claim that every compact HTTP response has exactly that byte size.

Until product decisions D-001--D-003 below are made, the carve assumption is:
one trusted local operator, a loopback-only authenticated gateway, and a
dependency-free static browser client shipped as an optional extra.  The UI
must work with polling; P68 may improve its transport later.

## Read-surface gap analysis

P52's actual capability set is exactly `hello`, `current`, `history`,
`entity`, and `health` [src/groop/daemon/api.py:54-75].  Its dispatcher
implements those five operations [src/groop/daemon/api.py:362-374], and P63
exposes typed results for the first four [src/groop/daemon/client.py:544-686].
`request_health()` in the present client is a legacy, non-envelope health
request [src/groop/daemon/client.py:161-178]; P66 is the roadmap successor for
typed versioned health, so P67 must not pretend that the P63 typed surface
already includes it.

| Operation / current typed access | Browser need | What the merged code returns | Sufficiency and concrete gap |
|---|---|---|---|
| `hello` / `request_hello()` | Yes, once at application startup and after reconnect. | Versions, capability list, daemon name/version, request/response/client/history limits [api.py:376-392]; the typed method validates only the outer shapes and returns `DaemonHello` [client.py:544-572]. | Sufficient for a compatibility screen and feature detection. Gap: it gives no authenticated viewer identity, authorization level, redaction policy, or gateway/base-URL configuration. A browser must not infer access rights from this local-daemon identity. |
| `current` / `request_current()` | Yes; it is the overview and table frame. | Atomic sequence, full serialized frame, and metadata for every metric in that frame [api.py:394-406]. P63 decodes the canonical frame and validates the sensitivity values in `metrics_meta` [client.py:574-590, 690-714]. | Sufficient for a one-frame overview, banner, sortable table, and client-side filtering. **Gap:** it is a one-shot request, not server push; the existing helper itself loops and sleeps for polling [client.py:750-755]. UI v1 must poll it (recommend 5 s, aligned with the existing helper) and show frame timestamp/sequence and stale/error state. P68's proposed versioned subscribe is the proper later remedy [handoff/P68-versioned-current-subscribe-client.md:10-21]. **Gap:** it always transfers the entire frame: there is no query for a compact table projection, entity filter, or selected metric set [api.py:394-406]. At the P53 scale this budgets about 447 KB per poll. |
| `history` / `request_history()` | Yes, but only for a small recent trend/history page; not required to make the first overview useful. | Ordered full frames, bounds, cursor, gap marker, next cursor, and per-response metric metadata [api.py:408-446]. P63 validates increasing sequences, bounds, cursor, and `gap` [client.py:592-668]. | Sufficient for bounded, explicit-gap history. The UI must request a deliberately small `limit` and visibly say “history gap; earlier samples were evicted” when `gap` is true. **Gap:** no aggregate/downsample/metric-or-entity projection exists, so history multiplies full-frame transfer. Its time form is also a one-shot window, not a live feed [api.py:416-433]. P68 addresses live delivery, but not chart-oriented downsampling. |
| `entity` / `request_entity()` | Yes, on demand when a user opens an entity detail page. | The latest sequence, one `EntityFrame`, and metadata only for that entity's metrics [api.py:448-469]; P63 decodes it into `DaemonEntityResult` [client.py:670-686]. | Sufficient for metric, governance, network, DAMON metadata, and findings that are already embedded in the frame. The server validates the key before in-memory lookup [api.py:452-463]. **Gap:** it has no process list: the TUI gets processes by reading `cgroup_root`/`proc_root` locally [src/groop/ui/drill.py:318-329], which a browser/gateway cannot do. Do not add an arbitrary process endpoint to v1; omit the process block and make that absence explicit. **Gap:** it is current-only; a per-entity historical series still needs client extraction from `history`. |
| `health` / no P63 versioned typed method yet | Yes, as a small availability/status panel, not as an operational-control page. | P52 calls `build_health_response` only when a health registry exists, otherwise it returns typed `unavailable` [api.py:471-474]. | The wire operation is sufficient for a status page after P67 maps it; the P67 handoff promises health only “once P66 lands” [handoff/P67-versioned-read-http-gateway.md:12-14]. **Gap:** P63 has no versioned typed `request_health()` alongside its other P52 methods, so P67 cannot meet its own “backed exclusively by typed client” rule for this route today [handoff/P67-versioned-read-http-gateway.md:21-26]. Gate the health route on P66 or add that typed method there; do not use the legacy method as a quiet exception. |

Cross-cutting gaps are intentional constraints, not reasons to change P52 in
this package.  All envelope requests are strict, one request/one response
[api.py:305-352], and the Unix handler reads and writes one envelope line
[api.py:644-672].  Full frames are bounded by the API's 4 MiB response limit
[api.py:61-63, 521-533], but the UI has no server-side pagination/projection.
The first implementation should avoid retry storms, cancel obsolete polls, and
render typed `unavailable`, `server_busy`, and `oversized_response` failures as
safe user-facing states rather than raw messages.

## Smallest useful page inventory

The existing TUI is the product design source.  Its banner already defines the
host verdict, resource summary, swap/backend, device summary, and top-three
pressure list [src/groop/ui/banner.py:16-46, 72-90].  Its table defines the
triage columns and profiles [src/groop/ui/table.py:15-33], filtering by display
name or cgroup key [table.py:298-309], and the normal numeric-desc/name-asc
sort convention [table.py:312-319].  Its tree preserves parent/child order,
collapse state, and matching descendants [src/groop/ui/tree.py:44-76].  Its
drill-down defines metrics, DAMON,
governance, network, pressure breakdown, history, findings, and processes
[src/groop/ui/drill.py:136-167].

| Page | What a non-CLI user can do | Daemon operations and refresh | Response budget at 89 entities |
|---|---|---|---|
| **Overview / triage** (landing page) | See host banner, top pressure, and all entities; toggle the TUI-derived flat table or collapsible hierarchy; filter and sort locally; open an entity. | `hello` once/reconnect; `current` every 5 s while visible; `health` every 30 s after P66/P67. | `current`: budget **~447 KB/frame** (P53), so ~5.2 MiB/min at 5 s before HTTP compression. Use a **16 KiB planning budget each** for fixed-shape `hello` and health; unlike the frame budget, this is not a P53 measurement. The legacy health reader enforces 16 KiB [src/groop/daemon/client.py:182-196]. |
| **Entity detail** | Show the TUI drill-down's metric groups, source labels, findings, governance, network, and DAMON metadata. Omit the local-process block in v1. | `entity(key)` on entry and every 5 s while visible; reuse a matching overview frame while it is fresh. | Use **~447 KB as a conservative planning budget**, not a proven upper bound. P53 measured a full frame without the entity response's `metrics_meta`; no measured per-entity envelope size exists. The only enforced absolute bound is the API's 4 MiB response cap [src/groop/daemon/api.py:61-63, 521-533]. |
| **Recent history** | Pick an entity and show recent `ram`, `cpu_pct`, and `rf_d_per_s` trends, mirroring the TUI's tracked drill history [drill.py:297-305]; show sequence/time and a gap warning. | `history(limit=8)` on entry and user refresh; do not poll it continuously—overview polling supplies the live point. | **8 × ~447 KB = ~3.58 MB** as a deliberately conservative P53 planning budget. Envelope and metadata overhead mean even eight frames are not guaranteed to fit the 4 MiB cap; treat `oversized_response` as a visible “narrow history” state and retry only after explicit user narrowing. This is a budget, not a remeasurement. |
| **Connection and data status** | Explain daemon/gateway version compatibility, last successful update, stale state, and component health. It makes failure intelligible to a non-operator rather than leaving a blank dashboard. | `hello` on load/reconnect; `health` every 30 s when supported; no full-frame request of its own. | Entity count does not affect either fixed-shape response. Use **16 KiB each** as the planning budget; health is bounded to that size by the existing typed legacy reader [src/groop/daemon/client.py:182-196], while `hello` has no dedicated byte cap below the general 4 MiB envelope cap. |

There is deliberately no separate “containers-only” screen: the overview's
TUI-derived filter covers it without a new data surface.  There is no control,
configuration, replay, raw JSON, or process-explorer page in browser v1.

## Sensitivity and redaction UX

The daemon attaches one closed sensitivity value to every metric metadata entry:
`public`, `operational`, or `sensitive` [src/groop/daemon/api.py:100-125,
128-143].  `current`, `history`, and `entity` each include `metrics_meta`
[api.py:394-406, 434-446, 465-469].  The UI must use that metadata, not a copied
metric-name list.

Default posture: render `public` and `operational`; redact `sensitive` unless
the gateway's authenticated viewer policy explicitly grants it.  This preserves
the useful normal telemetry view while treating process identity/count data as
an authorization decision.  A public/operational-only deployment may make the
same choice globally, but must not silently expose sensitive values merely
because the browser can reach the page.

Display redaction in JavaScript is not an authorization boundary: a viewer can
inspect the HTTP response directly.  The merged `current`, `history`, and
`entity` operations return raw values plus sensitivity metadata, not redacted
values [src/groop/daemon/api.py:394-469].  Therefore P67 (or a trusted proxy in
front of it) must replace values above the authenticated viewer's ceiling with
a typed redaction marker **before bytes reach the browser**.  Until that HTTP
representation exists, every authenticated browser user must be authorized for
the full unredacted response; client-side markers are usability only.

Redaction is a presentation replacement, never a missing key.  Preserve the
metric's label, unit, and layout cell, and render a typed marker such as
`Restricted — sensitive telemetry` with an accessible explanation and a
lock icon.  Do not render `-`, zero, an empty table cell, or a missing chart
series: those formats already mean unavailable/missing data in the TUI
[src/groop/ui/table.py:341-365].  Keep a redacted time-series point as a
typed “restricted” gap, not a numerical zero.  This follows the standing P58
lesson that redaction replaces a value with a typed marker rather than dropping
the key.

Entity names, cgroup keys, Docker metadata, findings, and optional metadata
are not classified by the present `metrics_meta` map.  The UI must therefore
not claim that the three-level metric enum redacts all frame fields.  The
gateway/auth product decision must state whether full frame/entity metadata is
available to the browser; until then, browser v1 is for an authenticated trusted
operator, not an anonymous “public metrics” dashboard.

## Trust boundary: verdict on P67

**Verdict: P67 is insufficient as written and must add four contract groups
before dispatch.**  The current daemon's recommended production interface is a
root-owned, non-client-writable local Unix socket at `0660 root:groop`
[docs/DAEMON.md:15-21]; P52 relies on OS socket-group access and only a no-op
default authorization hook [src/groop/daemon/api.py:177-183].  An HTTP listener
turns that local Unix identity boundary into a browser-accessible network
boundary.  P67 currently explicitly places auth/TLS outside scope
[handoff/P67-versioned-read-http-gateway.md:74-77], and only requires an
ephemeral *loopback test* [handoff/P67-versioned-read-http-gateway.md:53-60].
That is not a production trust contract.

P67 needs these additions before it is dispatched:

1. **Safe bind and deployment mode.** Default bind must be loopback only
   (`127.0.0.1` and/or `::1`), never wildcard or LAN address.  Non-loopback
   listening must be rejected or require an explicit, documented deployment
   mode that names a TLS/authenticated reverse proxy.  The gateway must not
   advertise or bind its HTTP port accidentally through a daemon socket option.
2. **Authentication and authorization.** Require an authenticated browser
   principal before returning telemetry, with a documented mapping to at least
   a redaction ceiling (`public`, `operational`, `sensitive`).  A practical v1
   choice is a trusted loopback reverse proxy that performs authentication and
   passes a verified identity only over a private local hop; directly trusting
   arbitrary forwarded headers is forbidden.  This is necessary because a
   gateway process's Unix-group membership does not identify the browser user.
3. **Origin/CSRF discipline.** Use same-origin routing with an explicit
   allowlist; no `Access-Control-Allow-Origin: *`, credentialed wildcard CORS,
   or JSONP.  Read-only GET routes reduce CSRF state-change risk, but cookie
   authentication still requires `Secure`/`HttpOnly`/`SameSite` cookies and
   Origin checking for any future non-GET endpoint.  Reject mutation methods
   now so a later route cannot silently inherit browser credentials.
4. **Read-only routing enforcement.** Accept only documented GET routes and
   map them only to P63 typed reads; reject POST/PUT/PATCH/DELETE, route
   traversal, unsupported query fields, and non-approved upstreams.  Preserve
   `metrics_meta` rather than stripping it, as P67 already requires
   [handoff/P67-versioned-read-http-gateway.md:41-51].  Map typed errors to safe
   HTTP errors without echoing socket paths or exception text.

These are changes to P67's handoff, not to P52.  P67 should additionally name
the gateway process's minimum Unix-socket group access and test default bind,
non-loopback refusal, unauthenticated denial, origin behavior, forbidden
methods, and server-side typed redaction.  P68 is not required for safe polling;
it is a later efficiency/latency improvement.

## Framework and stack options

| Candidate | Build/toolchain and dependency cost | Packaging (`pip install groop[web]`) | Deterministic pytest story |
|---|---|---|---|
| **Single static HTML/CSS/ES-module client served by P67 (recommended)** | Plain browser APIs; no Node, runtime framework, or vendored library. | Package the small audited asset set in the existing `groop` distribution. This **does grow the wheel used by plain `pip install groop`**; Python extras select dependencies, not package data. `pip install groop[web]` can remain a supported spelling but cannot make same-wheel assets conditional. Avoiding all core-wheel growth would require a separate distribution. | Real stdlib HTTP gateway tests plus `pytest` assertions over assets and deterministic JSON fixtures; no browser/Node required. Keep rendering/formatting functions pure and fixture-tested. |
| Server-rendered Python templates with progressively enhanced HTML | No Node; a template engine can be stdlib formatting or a new Python dependency. It also expands P67 from JSON adapter into page renderer. | Templates/assets in the existing distribution grow the plain `groop` wheel too. An optional template-engine dependency can live behind `groop[web]`, but the package files themselves cannot. A separate web distribution avoids core-wheel asset growth at the cost of another release artifact. | Test request/response HTML with stdlib client and HTML assertions; pure view-model tests. Browser behavior still needs limited JS fixture tests. |
| Vite + React (or comparable SPA) | Node package manager, lockfile, build pipeline, and a large JS dependency tree added to a Python-first repository. | Bundled assets grow the plain `groop` wheel if shipped in the same distribution. Committing generated assets keeps Node out of end-user `pip install`, but Node remains a release build/test tool; building them during wheel creation makes that coupling stronger. A separate distribution is the only way to keep the core wheel asset-free. | Requires Node-based unit/build tests and usually Playwright/browser tooling in addition to pytest; deterministic but substantially more CI machinery. |

Recommendation: the single static client.  It fits the repository's
dependency-light posture, consumes P67's JSON rather than duplicating backend
logic, keeps the first user-facing UI auditable, and leaves a future framework
migration possible once page complexity proves it necessary.  Accept small,
audited core-wheel growth rather than inventing a second distribution for v1.
It is a product choice proposed as D-001 rather than an installed dependency.

## Draft successor handoff headers

These are headers only; the carver should write full bodies from this analysis.

| Proposed package | One-line goal | Tier | Depends-on | Rough size |
|---|---|---|---|---|
| **P69a — Hardened read HTTP gateway** | Re-carve P67 with loopback default, authenticated/redaction-aware browser boundary, origin/method controls, and typed P52 routes. | pro-high | P52, P63, P66 | medium; gateway-only, security contract and real HTTP tests. |
| **P69b — Read-only web shell and triage** | Ship the static dependency-free browser shell: status, banner, sortable/filterable overview, polling, errors, and typed redaction marker. | pro-high | P69a | medium; static assets plus deterministic fixture/HTTP tests. |
| **P69c — Entity detail and bounded recent history** | Add on-demand entity drill-down and explicit-gap history charts without process or mutation surfaces. | sonnet5-high | P69b | small-medium; bounded history contracts and fixtures. |
| **P69d — Live browser updates (optional)** | Consume P68's versioned subscription through the gateway, replacing overview polling while preserving polling fallback. | pro-high | P69a, P68, P69b | medium; streaming/back-pressure/reconnect tests. |

P69a should be carved before P67 dispatch, because it is the required
trust-boundary correction.  P69b and P69c may be split only after the product
decisions below are resolved or their stated assumptions are accepted.

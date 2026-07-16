# Decisions inbox — product calls and outcomes

Current state (2026-07-15): **no open decisions**. D-001 through D-019 are all
decided and translated into the roadmap, backlog and handoffs. New product
questions receive new monotonic IDs; do not reopen these through an old handoff.

Purpose: record a *product* decision (not a mechanical contract failure — that
is BLOCKED) without blocking engineering. An OPEN entry is carved around under
its stated assumption; it becomes DECIDED when the decision, and where it was
encoded, are recorded. Never break non-interactive mode to ask.

How to use (user): pick an OPEN entry, start a frontier session, and paste its
**Resume prompt** — the context pointers restore the filer's reasoning without
re-reading the repo. When decided, that session updates the entry to DECIDED
(decision, date, where it was encoded) and unblocks dependent packages. The
controller surfaces inbox deltas in its status reports.

Who may file: frontier sessions (carver / reviewer) only. Implementation agents
propose ideas in their REPORT; the reviewer promotes the worthy ones here. That
filter is what keeps this file worth reading.

Entry schema: ID · date · raised-by · status (OPEN / DISCUSSING / DECIDED /
DROPPED) · question · why it matters · options with trade-offs · recommendation
and reasoning · context pointers · resume prompt · (when closed) decision
record.

Reference implementation: `dstdns/docs/ai-dev/DECISIONS-INBOX.md` (a sibling
repo, not vendored here).

---

## D-001 · 2026-07-13 · reviewer (frontier pass #2, from P69 analysis) · DECIDED 2026-07-13

**Question:** Should browser v1 use a dependency-free single static client, a
server-rendered Python surface, or a full SPA framework?

**Why it matters:** This determines whether a Node toolchain and lockfile enter
a Python-first release path, how `topos[web]` is packaged, and the test stack.

**Options:** (a) single static HTML/CSS/ES module: smallest build and
supply-chain surface, but manual UI composition; (b) server-rendered Python:
no Node, but makes the gateway a renderer and may add template complexity;
(c) Vite/React SPA: strong component ecosystem, but adds Node, a JS dependency
tree, generated-assets policy, and browser tooling.

**Recommendation (superseded by the decision below):** (a).  The first UI is four
read-only pages over a bounded JSON API, so plain browser APIs meet the need and
preserve the dependency-light runtime. Same-distribution static assets will still
grow the core wheel; extras cannot make package data conditional. Revisit only
when demonstrated interface complexity makes the manual client costly.

**DECISION (user, 2026-07-13): (c) React.** Encoded in `docs/ROADMAP.md`
("Standing user decision (2026-07-13): React, tested via pwmcp", commit
`f14e9dd`). The framework choice is not open for re-litigation; P69's framework
section is to be read as packaging-consequence analysis against React, not as a
competing pick.

Browser-level testing adopts **pwmcp** (the Playwright-MCP browser surface).
The decision left the pwmcp deployment to the implementation carve; **the carve
picked (b), a vbpub-scoped instance started via CIU** — `pwmcp/` is already a
first-class area on `main` (42 files) and ships CIU compose templates
(`pwmcp/ciu.compose.yml.j2`), so the in-repo path is both cleaner than
cross-repo reuse of dstdns's running instance *and* cheap, which removes the
resource-constraint argument that motivated option (a). Recorded in
`handoff/P73-web-ui-read-only-shell.md`.

What survives from the analysis: the packaging consequence is real and now
lands harder. A Node toolchain enters the release path, and bundled assets grow
the plain `pip install topos` wheel (extras select dependencies, not package
data). P73 must build the bundle at release time and commit it, so that Node is
a *release* dependency and never an end-user `pip install` dependency, and must
report the actual wheel byte delta.

**Context pointers:** `docs/WEB-UI-SCOPING.md` "Framework and stack options";
`handoff/P69-web-ui-scoping.md` deliverable 1.

**Resume prompt (closed):** the stack question is decided. The live follow-on is
narrower: "Confirm P73's packaging call - React bundle built at release and
committed, so `pip install topos` needs no Node; review the reported wheel byte
delta." 

## D-002 · 2026-07-13 · reviewer (frontier pass #2, from P69 analysis) · DECIDED 2026-07-15

**Question:** Who is allowed to view browser telemetry, and how will that
identity be authenticated at the HTTP gateway?

**Why it matters:** The daemon's `0660 root:topos` Unix socket conveys local
group access; HTTP cannot infer a browser user's identity from the gateway
process.  The answer controls whether sensitive metrics and unclassified frame
metadata can be displayed.

**Options:** (a) trusted local operator only: loopback gateway plus local
reverse-proxy authentication; low initial scope, no remote direct listener;
(b) named authenticated users with redaction roles: useful shared dashboard,
but requires identity/session/role implementation; (c) anonymous LAN/public
dashboard: simplest access but unacceptable exposure unless the API has a
separate fully public projection, which it does not today.

**Recommendation:** (a) for v1, with an authenticated principal and default
redaction above `operational`; grant `sensitive` only explicitly.  No
non-loopback listener until an authenticated TLS reverse-proxy deployment is
specified.

**Discussion input (user, 2026-07-15):** Topos serves HTTP on loopback only and
does not implement TLS; an external component terminates TLS when needed. SSH
key-based access is desirable if it can fit the browser model.

**DECISION (user, 2026-07-15):** The first web mode is a trusted single-
operator surface. Topos serves a capability-token-protected loopback HTTP
endpoint and inherits the launching operator's daemon-socket authorization;
that mode may show sensitive identity needed for host diagnosis. SSH is wholly
system/operator infrastructure: a remote user independently connects and sets
up ordinary local port forwarding to Topos's endpoint. Topos does not manage
keys, start or inspect tunnels, or infer an HTTP principal from SSH.

A shared deployment is a distinct future mode behind an external authenticated
TLS proxy, with explicit named roles and `operational` as the default
sensitivity ceiling. A browser-supplied `X-Topos-Principal` is never trusted.
Encoded in `TUI-SPEC.md` §0.2 and `docs/ROADMAP.md`.

**Context pointers:** `docs/WEB-UI-SCOPING.md` "Sensitivity and redaction UX"
and "Trust boundary"; `CONTRACTS.md` §10; `handoff/P67-versioned-read-http-gateway.md`.

**Resume prompt (closed):** "Implement the trusted single-operator loopback
mode without adding SSH lifecycle or identity handling to Topos."

## D-003 · 2026-07-13 · reviewer (frontier pass #2, from P69 analysis) · DECIDED 2026-07-15

**Question:** Is the read-only web UI required for the v2 tag, or is it a
post-v2 surface delivered after the daemon/gateway work?

**Why it matters:** This determines release acceptance scope, sequencing, and
whether P69b/P69c are release blockers rather than follow-up product work.

**Options:** (a) v2 tag requirement: fulfills the stated browser-product goal
in the release, but makes gateway security and web validation release-critical;
(b) post-v2: ship the daemon/read API earlier, but defer the first non-CLI user
experience; (c) phased: v2 requires the trusted local overview only, with
history/detail and streaming post-v2.

**Original recommendation (superseded by the decision below):** (c), a small
overview first. The accepted milestone is broader and uses bounded projected
polling initially; P68's full-frame subscribe carve was deleted.

**Discussion input (user, 2026-07-15):** The web goals and requirements must
drive the stack; good diagrams and drill-down are expected. This argues against
calling a status-only overview the completed browser product, but does not yet
choose the release tag boundary.

**DECISION (user, 2026-07-15):** The next product milestone requires a useful
local web product, not a status-only page: overview, hierarchy/flat exploration,
entity detail/history, connection/data status, and lifecycle incidents. Live
updates may use bounded polling initially. The SemVer/milestone naming question
remains D-017. Encoded in `docs/ROADMAP.md` and `TUI-SPEC.md` §0.2.

**Context pointers:** `docs/ROADMAP.md:144-166`;
`docs/WEB-UI-SCOPING.md` "Smallest useful page inventory" and "Draft successor
handoff headers".

**Resume prompt:** "Discuss D-003 in `topos/docs/DECISIONS-INBOX.md`: choose
whether the v2 tag requires the overview, the full four-page read UI, or no web
surface."

## D-004 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** Exactly when may zero-argument `topos` fall back from its preferred
daemon to local collection?

**Why it matters:** The user decided that `topos` should start like `top`, prefer
a running daemon, and still work without one. A silent source/privilege change
can nevertheless hide a broken or unauthorized daemon.

**Options:** (a) fall back only when the configured socket is absent/refused;
permission/protocol/server errors stop; (b) visibly fall back for every daemon
failure; maximum availability, but a damaged deployment can be ignored; (c)
fall back for absence/transient unavailability and require confirmation for
permission/protocol mismatch.

**Recommendation:** (b) for the zero-argument interactive glance, with a
persistent `LOCAL-DEGRADED` source banner and a finding naming the daemon failure.
Also add `--source daemon`, which always fails closed, and `--source local`,
which never probes. Machine queries must include `source`, `degraded_reason`,
permissions and freshness so automation cannot miss the fallback.

**DECISION (user, 2026-07-15):** Accepted as recommended. Zero-argument
interactive Topos always remains usable through a visible local fallback;
explicit daemon mode fails closed and explicit local mode does not probe.
Encoded in `TUI-SPEC.md` §0.2 and `docs/ROADMAP.md`.

**Context pointers:** `handoff/TOPOS-OBSERVABILITY-DISCUSSION.md`; `src/topos/cli.py`
attach/local branches; `docs/DAEMON.md` client errors.

**Resume prompt:** "Discuss D-004: approve visible fallback for all interactive
daemon failures, or name the daemon failure classes that must remain fatal."

## D-005 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** What are the daemon's in-memory and persistent-history defaults,
including wear, age, byte and resolution policy?

**Why it matters:** The user accepted persistent history as a production
requirement and suggested five minutes at five-second resolution. At the measured
gstammtisch scale a full frame is about 447 KiB: naïvely writing every frame is
roughly 7.5 GiB/day before compression and metadata reuse.

**Options:** (a) memory-only five-minute ring: no storage wear, no restart
recovery; (b) append every full frame to a compressed store: simple/exact but
write-heavy; (c) two-tier store: five-minute full-resolution memory ring plus
batched compressed disk segments/rollups under simultaneous byte+age caps.

**Recommendation:** (c). Freeze the five-minute/five-second memory default now.
Before freezing disk defaults, measure real multi-frame zstd ratio and writes.
Provisional production target: 24 hours and 256 MiB, both enforced, with recent
full resolution and older rollups that preserve count/min/mean/max and the data
needed for documented percentile/error bounds. `topos daemon stats` must show
RAM/disk bytes, oldest/newest timestamps, sample/segment counts, coverage/gaps,
compression, write rate, evictions, corruption/recovery state and caps.

**DECISION (user, 2026-07-15):** Accepted as proposed: five minutes at five-
second resolution in memory, plus a batched compressed persistent tier targeting
24 hours and 256 MiB with simultaneous caps. The implementation package must
measure real compression, bytes written and write amplification; it may adjust
segment/rollup mechanics to satisfy the caps but may not silently enlarge them.
Lifecycle facts share this store and these caps (D-008), rather than creating a
second persistence engine. Encoded in `docs/DAEMON.md`, `TUI-SPEC.md` §0.2 and
`docs/ROADMAP.md`.

**Context pointers:** `src/topos/daemon/broker.py`; `src/topos/record/ring.py`;
`TUI-SPEC.md` §4.5/§7; gstammtisch 447 KiB measurement.

**Resume prompt:** "Discuss D-005: approve the two-tier design and select the
disk-store default (off, 24h/256MiB, or another measured budget)."

## D-006 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** How do cgroups, containers and processes relate in navigation and
accounting?

**DECISION (user, 2026-07-15):** Keep cgroups as canonical accounting entities.
Offer processes both as expandable hierarchy children and as a flat process
projection, backed by one bounded process model. In hierarchy mode a container
decorates its cgroup node; in flat container mode it is a row. Never add a child
identity row that double-counts the same cgroup totals. Encoded in
`TUI-SPEC.md` §0.2 and `docs/ROADMAP.md` "Product convergence". Sorting scope is
tracked separately in D-012.

**Context pointers:** `handoff/TOPOS-OBSERVABILITY-DISCUSSION.md` questions 3-4.

## D-007 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** Which machine/headless outputs are release-critical?

**DECISION (user, 2026-07-15):** Current snapshot, streaming selected rows,
historical window summary, raw history, and findings/exit-code gating are
release-critical. Baseline regression is not a release blocker. All consumers
reuse one frame query engine rather than recomputing in daemon/MCP/HTTP.
Encoded in `TUI-SPEC.md` §0.2 and `docs/ROADMAP.md` "Product convergence".

Baseline regression remains useful for repeatable experiments/CI: compare a
current recording or selected steady-state window to a separately captured
baseline and fail on configured absolute/percentage changes. It is poorly suited
to ad-hoc incident triage when workload and phase are not controlled.

**Context pointers:** `src/topos/report.py`; roadmap P54/P61/P62/P64/P65;
`handoff/TOPOS-OBSERVABILITY-DISCUSSION.md` question 5.

## D-008 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** How should stopped/recreated workload incarnations remain
discoverable inside the shared history, and what identity joins them?

**Why it matters:** Docker recreation replaces both container ID and cgroup.
Dropping the row loses OOM/restart evidence; retaining it as current corrupts
totals. Restart loops also need to remain active findings across incarnations.

**Options:** (a) show only active entities; simple but loses the incident; (b)
retain exited entities as ordinary rows; visible but semantically dishonest;
(c) separate current entities from lifecycle tombstones, joined by a stable
logical workload key (systemd unit or CIU/compose project+service) and linked to
concrete incarnation IDs.

**Recommendation (refined after user question, 2026-07-15):** (c), but do not
create a second history store or retention budget. Persist lifecycle facts in
D-005's same 24-hour/256-MiB capped store. A "tombstone" is only the small
index/view saying that incarnation A ended at time T (for example OOM/exit 137)
and was replaced by incarnation B of the same logical workload. It makes the old
history discoverable from the current workload while never including the ended
incarnation in current resource totals. Keep an active restart-loop finding
while its rolling threshold remains breached; resolved facts expire with the
shared history.

**DECISION (user, 2026-07-15):** Accepted as refined. A stable `WorkloadKey`
joins concrete, incarnation-safe container/cgroup identities inside D-005's
shared history and caps. The UI calls the derived links **Previous instance**
or **Recent exit** rather than presenting a second storage concept. Ended
incarnations remain discoverable but never contribute to current totals.
Encoded in `TUI-SPEC.md` §0.2, `docs/ARCHITECTURE.md` and
`docs/OPERATOR-QUESTIONS.md`.

**Context pointers:** Authentik OOM workflow in `docs/OPERATOR-QUESTIONS.md`;
Docker/CIU joins in `src/topos/collect/dockerjoin.py`.

**Resume prompt (closed):** "Implement stable workload/incarnation identity and
derived Previous instance/Recent exit links in the shared history store."

## D-009 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** What does opt-in mean for expensive process/socket/log providers,
and how far does the listening-port feature go?

**Why it matters:** The user wants every expensive provider optional and
configurable through presets, while listening server ports are a missing core
operator question. Merely hiding a column does not avoid collection cost.

**Options:** (a) providers always collect, profiles only hide them; simplest UI,
wrong cost model; (b) provider activation follows explicit config/preset
capabilities; bounded and honest; (c) automatically activate based on the current
screen; convenient but makes cost/state surprising.

**Recommendation (refined by D-013):** (b). "Opt-in" applies to expensive or
privileged enrichment, not to the cheap process counters needed to discover the
CPU-hot/I/O-hot union. The normal daemon loop may maintain lightweight
`/proc/PID/stat` and `/proc/PID/io` baselines; it does not perform a full-host
`smaps`, file-descriptor, socket, exact-device or log sweep until enabled by a
named preset/config or explicit command. First ship on-demand listener ownership
(protocol, local address, port, namespace, PID/process/cgroup/container, queues
and connection count). Procfs/inet-diag do not provide reliable per-listening-
port traffic totals, and `/proc/PID/io` does not attribute bytes to a device;
make exact traffic or PID-to-device attribution later optional eBPF providers
with measurement gates.

**DECISION (user, 2026-07-15):** Accepted with detail-page activation. Every
optional provider has a configured activation mode: `disabled`, `manual`,
`detail`, or `always`. Safe bounded enrichment defaults to `detail`: opening a
process/container detail view acquires a visible observation lease, shows
`warming` until rate baselines exist, renews while viewed, and expires 30 seconds
after the last subscriber by default. `manual` exposes a Start detailed
observation hotkey/button; privileged or unusually costly providers such as
exact eBPF attribution and log evidence default to `manual` or `disabled`, never
navigation-triggered. `always` is an explicit operator choice.

Lease duration, concurrent detail-target cap, provider mode and provider budget
are configurable. Multiple clients share reference-counted/TTL leases. The UI
always exposes `disabled`, `available`, `warming`, `live`, `partial`, `stale` or
`error`, the source, cost/coverage limit, and how to enable unavailable detail.
Starting observation changes collector activity only; it does not authorize a
host mutation. Encoded in `TUI-SPEC.md` §0.2/§3.7 and `docs/ROADMAP.md`.

**Context pointers:** `docs/OPERATOR-QUESTIONS.md`; `TUI-SPEC.md` Appendix B;
`config.py` provider configuration.

**Resume prompt (closed):** "Implement configured provider activation modes and
visible bounded detail leases; never auto-start privileged observation."

## D-010 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** How is the marketing goal "answer 95% of operator questions"
converted into a release acceptance target?

**Why it matters:** A percentage without a denominator lets scope expand forever
and gives implementation agents no oracle.

**Options:** (a) leave it as positioning only; (b) count supported competitor
flags; misleading and rewards breadth; (c) maintain a versioned set of real
incident scenarios and require each to resolve within a bounded interaction.

**Recommendation:** (c). Use `docs/OPERATOR-QUESTIONS.md`: one main view plus at
most two drill-downs, or one bounded CLI/MCP query, with source/freshness/window
coverage visible. Add scenarios from repeated real investigations rather than
trying to clone every specialist tool.

**DECISION (user, 2026-07-15):** Accepted. The versioned scenario set is the
release oracle and includes both the observed project incidents and additional
routine sysadmin/DevOps cases. A scenario passes only within the interaction
bound above and with source, freshness, permissions and historical coverage
visible. Topos may identify the owning subsystem and explicitly hand off to a
specialist tool; it need not reproduce unbounded `du`, packet capture, tracing
or log-search workflows. Encoded in `docs/OPERATOR-QUESTIONS.md` and
`docs/ROADMAP.md`.

**Context pointers:** `docs/OPERATOR-QUESTIONS.md`; gstammtisch and Authentik
workflow evidence.

**Resume prompt (closed):** "Use the versioned operator scenario set as the
release oracle and preserve its bounded interaction/evidence requirements."

## D-011 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** What exact same-origin hosting, browser API, history transport,
redaction dependency, and test/toolchain contract must replace the current
P73/P77 assumptions?

**Why it matters:** P67 serves JSON but no React assets; P77 wants one-entity/
one-metric history while `/v1/history` returns full frames near the 4 MiB cap;
and pytest cannot execute React pure functions without Node. Dispatching the
current handoffs would force architectural decisions inside implementation.

**Options:** (a) accept full-frame polling/history and let an external proxy
serve assets; smallest backend change, inefficient and deployment-heavy; (b)
Topos serves its committed React bundle and projected read routes from one
loopback origin; (c) adopt a larger web framework/backend and separate frontend
deployment.

**Recommendation:** (b): a narrowly audited static-asset surface, no CDN or
service worker, CSP/referrer/no-store policy, and a projected entity/metric
series route backed by the shared query engine. Make P81's shared fail-closed
redactor a prerequisite. Use TypeScript checked decoders and pinned Node tests
for frontend logic, Python tests for daemon/gateway/security, shared JSON
fixtures, and pwmcp for real browser behavior. Keep React thin: no global state
store or general component kit until requirements prove the need; choose chart/
diagram dependencies only against the approved overview/explorer/detail UX.

**DECISION (user, 2026-07-15):** Accepted as recommended. Topos owns same-origin
delivery of its committed React bundle and projected read/history routes on
loopback. P81 shared redaction is a prerequisite. Frontend logic is exercised by
pinned Node tests, backend/security by Python tests, shared fixtures connect the
contracts, and pwmcp covers observable browser behavior. Encoded in
`docs/ROADMAP.md` and `TUI-SPEC.md` §0.2.

**Context pointers:** unmerged `feat/topos-web-ui-arch-reflection`;
`docs/WEB-UI-SCOPING.md`; P67/P73/P77/P81 handoffs.

**Resume prompt:** "Discuss D-011: approve Topos-owned same-origin static hosting,
projected series API, P81 prerequisite, and the Node/Python/pwmcp test split."

## D-012 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** How should global ranking interact with hierarchy/grouped views?

**Why it matters:** A global CPU sort answers "what is hottest?" but destroys
ancestry; a tree sort preserves ownership but may hide a hot descendant below an
otherwise quiet-looking branch.

**Options:** (a) hierarchy is always sibling-local; global sort explicitly
switches to a flat projection with a path/owner column; (b) visually reparent
global results inside a tree; compact but misleading; (c) provide two explicit
hierarchy behaviors: sibling sort and ranked branches by registry-approved
aggregate, plus a separate flat global mode.

**Recommendation:** (c). Example: in hierarchy mode, rank `system.slice` and
`besteffort.slice` by subtree CPU, then rank children only within each parent;
show `CPU[subtree] · within parent`. A "global CPU" action switches to a flat
ranked list with `CGROUP/SLICE/CONTAINER` columns. Never draw globally sorted
rows as if they still formed a tree.

**DECISION (user, 2026-07-15):** Accepted as recommended. Hierarchy ranking
uses only registry-approved subtree aggregates and sorts rows within their real
parent; global ranking is an explicit flat projection that preserves ownership
columns. Every sort label states its scope. Encoded in `TUI-SPEC.md` §0.2 and
`docs/ROADMAP.md`.

**Context pointers:** `src/topos/ui/tree.py`, `table.py`, `grouping.py`;
`handoff/TOPOS-OBSERVABILITY-DISCUSSION.md` question 8.

**Resume prompt (closed):** "Implement scoped hierarchy sorting and explicit
flat global ranking without visual reparenting."

## D-013 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** What is the canonical process identity and CPU/rate convention for
the `pidstat`-class process model?

**Why it matters:** PID alone is reused. CPU percentage is ambiguous between
"100% = one logical CPU" and host-normalized share, and process counters can
reset between samples or disappear during procfs races.

**Options:** (a) identify by PID and show one CPU%; familiar but incorrect across
reuse; (b) identify an incarnation by boot ID + PID + `/proc/PID/stat` start time,
show one-core CPU% (may exceed 100) and a separately named host-share metric;
(c) normalize every process to whole-host capacity, which hides single-core
saturation.

**Recommendation:** (b). Use reset-aware deltas for CPU, minor/major faults,
`/proc/PID/io`, and voluntary/involuntary context switches. Preserve PPID, UID,
state, elapsed, threads, RSS, VSZ and swap as gauges. Permission/race loss is a
typed unavailable sample, not zero. The daemon owns full-host sampling; local
on-demand drill-down may use a bounded selected-cgroup sample.

The bounded process candidate set is the union of CPU-hot, I/O-hot,
selected/pinned, and recently-hot processes. Maintain lightweight broad
`/proc/PID/stat` and `/proc/PID/io` counter baselines so an I/O burst has an
immediate rate; perform costly identity, memory, file-descriptor and socket
enrichment only for that bounded union. Preserve read, write and cancelled-
write rates and block-I/O delay where the kernel exposes them, plus bounded
history/grace so an ended burst still answers "who did I/O, when?". Procfs
process I/O is total per process, not exact per-device attribution; exact
PID-to-device attribution remains an optional privileged/eBPF capability.

**DECISION (user, 2026-07-15):** Accepted with the CPU-hot plus I/O-hot union
above. `ProcessKey` is boot ID + PID + start time; process CPU uses
`100% = one logical CPU` and separately labels whole-host share. Missing or
raced counters are typed unavailable, never zero. Encoded in `TUI-SPEC.md`
§0.2, `docs/ROADMAP.md`, `docs/ARCHITECTURE.md` and
`docs/OPERATOR-QUESTIONS.md`.

**Context pointers:** `src/topos/collect/procs.py`; `docs/OPERATOR-QUESTIONS.md`.

**Resume prompt (closed):** "Implement the incarnation-safe bounded process
model with a CPU-hot/I/O-hot candidate union and bounded history."

## D-014 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** What is the minimum `mpstat`-class per-CPU surface and finding?

**Why it matters:** Aggregate CPU can look healthy while one core, steal time,
iowait, IRQ, or softirq is saturated. The current host model does not expose a
per-CPU series.

**Options:** (a) retain aggregate CPU only; (b) collect `/proc/stat` per-logical-
CPU user/system/nice/idle/iowait/irq/softirq/steal deltas and flag sustained
imbalance; (c) also ingest detailed interrupt vectors/topology in the default
loop, adding cost and platform variance.

**Recommendation:** (b) in the normal host provider, with CPU hotplug/reset
handling and `100% = one logical CPU`. Detailed `/proc/interrupts`, NUMA and
frequency/topology become on-demand drill-down. Do not attribute iowait to a PID
or treat it as exact device busy time.

**DECISION (user, 2026-07-15):** Accepted as recommended. Lightweight per-CPU
`/proc/stat` rates and sustained-imbalance findings are part of normal host
collection. Detailed interrupt vectors and topology/frequency inspection are
on demand. Encoded in `TUI-SPEC.md` §0.2, `docs/ROADMAP.md` and
`docs/OPERATOR-QUESTIONS.md`.

**Context pointers:** `src/topos/collect/host.py`; TUI banner §3.0;
`docs/OPERATOR-QUESTIONS.md`.

**Resume prompt (closed):** "Implement reset/hotplug-safe per-CPU rates and
imbalance findings; keep detailed interrupt/topology collection on demand."

## D-015 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** Are operator tiers/policies path-derived only, or can config map
observed unit/container metadata to named policy groups?

**Why it matters:** A workload can be physically placed under `system.slice`
while the operator regards it as production. Canonical accounting identity must
remain truthful, but colors, thresholds and saved presets need useful policy
classification.

**Options:** (a) path-only tier; simple and observable, sometimes operationally
misleading; (b) config mappings may replace the observed owner; convenient but
falsifies placement; (c) preserve observed owner/path and add a separately
provenanced policy/tags classification matched by path, unit, container/compose
or CIU metadata.

**Recommendation:** (c), with deterministic precedence, conflict reporting and
no image-name-only authorization. Policy tags affect thresholds/presentation,
never accounting identity or admin authorization.

**DECISION (user, 2026-07-15):** Accepted as recommended. Observed cgroup path,
systemd unit and container/orchestrator ownership remain immutable facts.
Configuration may attach additive tags and a primary policy using exact or
anchored structured selectors for cgroup path, unit, Compose project/service or
CIU stack/service. Image/name-only matches may add presentation tags but can
never establish identity or authority.

All matching tags are retained. A primary policy that affects thresholds uses
an explicit numeric priority; conflicting top-priority policies are a typed
configuration error rather than config-order precedence. Policy/tags may alter
thresholds, findings, colors and presets, but never totals, ownership or admin
authorization. The UI shows rule provenance. Encoded in `TUI-SPEC.md` §0.2/
§3.7 and `docs/ROADMAP.md`.

**Context pointers:** `TUI-SPEC.md` §10 question 1; `config.py` tiers;
`model.py` Entity tier/CIU/Docker metadata.

**Resume prompt (closed):** "Implement separately provenanced policy tags with
explicit priority/conflict errors; never replace observed ownership."

## D-016 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** Which owner is allowed to update/recreate a container?

**Why it matters:** Reconstructing `docker run` from `docker inspect` loses
orchestrator intent. CIU/Compose and Wings-managed workloads have different
sources of truth, and direct Docker lifecycle operations can desynchronize them.

**Options:** (a) reconstruct and run Docker commands for every container; broad
but unsafe; (b) route update/recreate to the detected owner (CIU/Compose/Wings)
and refuse when no safe adapter exists; (c) keep Topos permanently read-only.

**Recommendation:** (b). Topos may keep narrow, audited Docker resource updates
already implemented, but image pull/recreate must call an explicit owner adapter
and show the owner's plan. Never infer authorization from CIU labels and never
directly start/stop a Wings-owned game container.

**Discussion refinement (2026-07-15):** Model an owner chain rather than a
closed list of frameworks. Initial families are native systemd, Compose, CIU
and Wings. The adapter boundary should anticipate Podman/Quadlet next, then
read-only Kubernetes/k3s ownership; Docker Swarm, Nomad, Incus/LXC,
systemd-nspawn, libvirt/Proxmox and higher-level panels remain scenario-driven.
Raw `runc`/`crun`/containerd/CRI-O objects are execution instances, not safe
desired-state targets. A higher reconciler such as GitOps or a management panel
must prevent a lower-level action unless its own configured adapter owns it.

Every adapter discovers a proven owner chain, declares capabilities, produces a
side-effect-free bounded plan, executes only through the existing authorization/
confirmation/audit kernel, and verifies the new incarnation. No supported
capability means refusal, never generic Docker fallback. Full rationale and
candidate ordering are in `docs/LIFECYCLE-ADAPTERS.md`.

**DECISION (user, 2026-07-15):** Accepted as refined. Core owner families are
systemd, Compose, CIU and Wings; Podman/Quadlet is the next general-purpose
adapter. Kubernetes/k3s begins with read-only ownership and treats mutation as
a separate cluster-security project. Swarm, Nomad, Incus, VM managers and
higher-level panels remain scenario-driven. The owner-chain adapter contract is
a prerequisite for new lifecycle mutation, and an unknown/ambiguous/
unsupported authority always produces a typed refusal. Encoded in
`docs/LIFECYCLE-ADAPTERS.md`, `TUI-SPEC.md` §0.2/§4.1 and
`docs/ROADMAP.md`.

**Context pointers:** `docs/LIFECYCLE-ADAPTERS.md`; `TUI-SPEC.md` §4.1/§10
question 7; P72/P76/P83; gstammtisch Wings lifecycle warnings.

**Resume prompt (closed):** "Freeze and implement the owner-chain adapter
contract before adding lifecycle mutation; never fall back to a raw runtime."

## D-017 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** Do the spec's v0/v1/v1.5/v2/v3 cuts remain release names, or are
they internal capability milestones separate from package SemVer?

**Why it matters:** The package is currently `0.1.0`, while documents say both
"v2" and "web v3" and the roadmap now promotes web work earlier. Agents and
users can interpret those labels as incompatible release promises.

**Options:** (a) keep the historical labels as public versions; (b) treat them
as named internal milestones and version actual releases independently; (c)
rewrite history around a new v2 definition.

**Recommendation:** (b). Preserve historical references but name the next
product milestone (for example `daemon-product`) and assign SemVer only when its
acceptance scope closes. D-003 should then decide whether web is part of that
milestone, not what integer marketing version it receives.

**DECISION (user, 2026-07-15):** Accepted, with the next internal milestone
named **operator-console**. Historical v0/v1/v1.5/v2/v3 labels describe
capability eras, not package versions. Package SemVer is assigned independently
when an acceptance scope closes. `operator-console` covers the shared query and
persistent-history foundation, CPU/I/O process and per-CPU observation,
lifecycle incidents, the trusted-loopback React product and D-010's versioned
operator scenario suite. Encoded in `README.md`, `TUI-SPEC.md` §0.1/§0.2 and
`docs/ROADMAP.md`.

**Context pointers:** `pyproject.toml`; `TUI-SPEC.md` §0.1/§0.2; D-003.

**Resume prompt (closed):** "Use operator-console as the internal acceptance
milestone and keep package SemVer independent of historical capability eras."

## D-018 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** What browser information architecture and visual vocabulary make
the accepted web scope useful without creating a dashboard of unrelated charts?

**Why it matters:** D-003 requires overview, exploration, detail/history,
status, and lifecycle incidents. React and a chart dependency do not decide how
an operator moves from "the host is unhealthy" to an owner and a time window.
Visuals can also lie: a globally sorted tree invents ancestry, a Sankey suggests
flow where memory values are stocks, and interpolated charts hide data gaps.

**Options:** (a) one dense dashboard with modal details; fast to prototype but
poor navigation and URL state; (b) workflow routes sharing one time-range and
selection model; (c) a topology/graph-first interface; visually striking but a
poor match for the real cgroup hierarchy and time-series diagnosis.

**Recommendation:** (b): **Overview** for verdict, active incidents and top
owners; **Explore** for the canonical tree plus explicit flat ranks;
**Entity detail** for identity/governance, processes, I/O and truthful series;
and **Incidents** for lifecycle timelines, Previous instance/Recent exit links
and bounded evidence. Connection, source, freshness, permissions and history
coverage remain persistently visible, with a fuller status panel.

Use an icicle beside the hierarchy table only for registry-approved additive
subtree metrics; size represents the selected aggregate and color represents a
separately labelled pressure/severity value. Use non-interpolated time series
with visible gaps/resets and a stacked composition for resident/compressed/disk-
backed memory rather than a flow diagram. Keep filter, projection, entity and
time range URL-addressable, but never put the capability token in the URL.
Select a chart/diagram dependency only after fixture prototypes prove
accessibility, bundle size and pwmcp behavior; avoid a topology spaghetti graph
or a general dashboard-builder in this milestone.

**DECISION (user, 2026-07-15):** Accepted as recommended. The four workflow
routes share one selection/time-range model and a persistent source/freshness/
coverage strip. Add a bounded comparison tray for at most three entities using
the same metric, scale and window; it is not a free-form dashboard. View state
is deep-linkable except for credentials. Visual semantics, gaps, resets and
aggregation scope are fixture/test contracts, not styling conventions. Encoded
in `TUI-SPEC.md` §0.2 and `docs/ROADMAP.md`.

**Context pointers:** D-003/D-011/D-012; `docs/WEB-UI-SCOPING.md`;
`docs/OPERATOR-QUESTIONS.md`.

**Resume prompt (closed):** "Implement the accepted workflow IA and truthful
visual contracts before choosing chart dependencies."

## D-019 · 2026-07-15 · product observability session · DECIDED 2026-07-15

**Question:** What default candidate, grace and hard-cap budget makes CPU/I/O
process history useful without turning every frame into a full process dump?

**Why it matters:** D-013 requires the union of CPU-hot and I/O-hot processes.
Reading cheap counters broadly is different from enriching and persisting every
PID. Without frozen defaults, implementations may either miss the I/O offender
or make D-005's 256-MiB store mostly process metadata.

**Options:** (a) retain every visible process every five seconds; complete for
surviving processes but high cardinality; (b) keep ephemeral cheap baselines for
all visible PIDs and enrich/persist a bounded union; (c) sample only processes
under a selected/hot cgroup; cheapest, but discovers the process one interval
late and can miss short bursts.

**Recommendation:** (b). Each daemon tick reads the cheap identity/CPU and I/O
counters for every visible PID and retains only the current baseline for
non-candidates. Default enriched candidates are top 20 CPU plus top 20 I/O,
selected/pinned processes (maximum 16), then recently-hot entries for a 60-
second grace period, with deterministic priority and a hard union cap of 64.
The defaults are configurable within a tested bound. Persist only the enriched
union; D-005's global age/byte caps still win. Surface candidate reason, process
coverage, scan duration, races/permissions and cap evictions.

A process that starts and exits entirely between five-second scans can still be
missed; promising complete transient-process accounting would require an
optional kernel event/task-accounting provider. The implementation carve must
measure scan cost and process-history share on small and high-PID fixtures before
freezing the configurable maximum.

**DECISION (user, 2026-07-15):** Accepted as recommended. Top CPU count, top
I/O count, selected/pinned cap, recently-hot grace and hard enriched-union cap
are all first-class configuration fields with the defaults above. The effective
values, candidate reasons, cap evictions and coverage are observable. Invalid
relationships such as a hard cap below the selected/pinned allowance fail
configuration validation. Encoded in `TUI-SPEC.md` §0.2/§3.7 and
`docs/ROADMAP.md`.

**Context pointers:** D-005/D-009/D-013; `docs/OPERATOR-QUESTIONS.md`;
`src/topos/collect/procs.py`.

**Resume prompt (closed):** "Implement the configurable bounded process union,
validation, coverage telemetry and measured high-PID acceptance."

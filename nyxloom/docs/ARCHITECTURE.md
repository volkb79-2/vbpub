# nyxloom architecture

Status: **accepted 2026-07-15, amended: daemon from the start** (see README
deciding log). §2 below describes the reconcile pass itself — unchanged — but
it now runs inside the resident `nyxloomd` (internal interval + child-watch)
rather than from cron; §9's tick-vs-daemon graduation is decided in the
daemon's favor. All invariants stand: disk authoritative, idempotent pass,
detached wrappers, flock leases, zero AI in the control path.

Inherits draft 1's planes (product / control / execution / evidence) and its
security boundary; this document specifies the draft-2 realization. Deltas
from draft 1 are justified in [../REVIEW-OF-DRAFT1.md](../REVIEW-OF-DRAFT1.md)
(referenced as F1…F12).

## 1. Files are the database (F1, F4)

### Repo side — the contract (committed, portable)

```text
docs/.../handoff/<task>.md      # SINGLE SOURCE: YAML frontmatter (machine) + body (contract)
docs/.../handoff/reports/       # P<NN>-LOG.md, -REPORT.md, -SELFREVIEW.md + receipt.json
docs/ROADMAP.md                 # product-owned
docs/DECISIONS-INBOX.md         # product-owned; entries carry D-IDs and resume prompts
.nyxloom/project.toml        # project policy: gates, mutexes, globs, caps, redaction
AGENTS.md                       # hard rules + canonical pointers (tool files are thin shims)
```

The frontmatter is the machine-readable evolution of the v2 §7 blockquote
header (`Tier / Stack / Depends-on / …`) — same fields, parseable syntax,
schema-validated ([../schemas/handoff-frontmatter.schema.json](../schemas/handoff-frontmatter.schema.json)).
There are **no JSON sidecar handoffs**. `nyxloom lint` gates the carve
commit; a handoff that fails lint cannot enter the queue.

### Host side — the runtime (never committed, XDG state)

```text
$XDG_STATE_HOME/nyxloom/
  <project>/
    events.jsonl                # append-only, authoritative audit trail
    state/<task>.json           # working projection per task (rebuildable from events)
    attempts/<attempt>/         # log, receipt.json, wrapper pid/pgid, packet/
    pause                       # flag file; tick dispatches nothing while present
  leases/                       # cross-project flock lease files (see §4)
  routes.toml                   # live routing matrix (see §5)
  prices.toml                   # dated provider price table
  www/                          # rendered static dashboard (see §7)
```

Writes go through `nyxloom` commands: append event + rewrite statefile via
temp-file + atomic rename, under a per-task flock. Replaying `events.jsonl`
reproduces every statefile (a unit test, and a `doctor --rebuild` command).

## 2. The tick engine (F2)

`nyxloom tick` is a stateless reconciler — the v2 §10 controller loop with
the LLM removed. Each invocation:

1. **Scan**: handoff frontmatter (repo), statefiles, git (merged branches,
   base commits), attempt receipts, log mtimes, `/proc/<pid>`, lease files,
   pause flags, DECISIONS-INBOX status lines.
2. **Collect finished attempts**: a receipt written by the wrapper is the
   normal completion signal (typed: `done | blocked | limit | error`, exit
   code, per-oracle results, usage). A dead pid without a receipt →
   INTERRUPTED, resume handle already on file.
3. **Detect stalls** exactly per v2 §5.4, tiered: log-mtime threshold →
   process/wchan → gate-container activity. Confirmed stall → interrupt +
   resume (route's resume template), bounded by retry budget.
4. **Dispatch** eligible tasks up to WIP caps: deps merged, mutexes free,
   pause absent, budget remaining, route preflight OK (probe command from
   `routes.toml`). Dispatch = render prompt from template + handoff path,
   launch the **wrapper** detached (`setsid`; systemd-run --user scope where
   available), record attempt + resume handle immediately.
5. **Assemble review packets** for AWAITING_REVIEW tasks (pre-dumped
   `git diff main...HEAD`, `--stat`, LOG/REPORT/SELFREVIEW/receipt paths,
   checklist pointer, negative scope) and, when a wave fills (≤3 diffs or
   timeout), launch the frontier review leg the same way.
6. **Append events, render dashboard, send notifications, exit.**

Properties: **idempotent** (every action guarded by current state; reruns are
no-ops), **bounded** (does a fixed amount of work, then exits), **crash-safe
by construction** (no in-memory state to lose). Scheduling: cron or systemd
timer on the host; inside a devcontainer without an init, `nyxloom tick
--loop --interval 120` — a foreground repeater whose iterations are still
stateless (state stays on disk; killing it loses nothing).

Completion latency is one tick (≤5 min) — irrelevant against 20–80 min worker
legs (v2 §11). Nothing in the tick ever calls a model. Ambiguous failure logs
are classified by the v2 §5.2 phrase ladder (limit-shaped vs task-shaped);
anything still ambiguous becomes a typed `NEEDS_OPERATOR` notification, not a
guess.

### The attempt wrapper

Supervision lives in a ~20-line wrapper, not a daemon: acquire leases (§4) →
run the CLI leg redirecting output to the attempt log → on exit, write
`receipt.json` (exit code, result classification, usage extraction per §6) →
release leases (kernel does it anyway if killed). The wrapper's lifecycle IS
the supervision contract; the tick only observes its artifacts.

## 3. Roles and token flow (unchanged from workflow v2)

Carve and review pass #2 remain frontier sessions; implementers remain the
§4 ladder; pass #1 self-review remains an advisory resumed session under its
trial metric. What changes is who does the plumbing:

| Duty (v2 §10) | Today | Draft 2 |
| --- | --- | --- |
| Header parsing, dep/slot checks | Sonnet controller | tick |
| Preflight probes | controller | tick (routes.toml probe cmd) |
| Dispatch + resume-handle capture | controller | tick + wrapper |
| Stall detection (§5.4) | controller | tick |
| Review packet assembly | controller | tick |
| Status reporting / slot table | controller replies | dashboard (§7) |
| Heartbeats / cache-warmth | ScheduleWakeup ticks | none needed |
| Carve, review, merge, decisions | frontier sessions | frontier sessions (unchanged) |

Carver session affinity (v2 §2: carve in the reviewer's warm context) is kept
as a scheduling hint on the review leg (`carve_affinity`), not architecture (F9).

## 4. Leases: flock(2), not marker files, not daemon state (F3)

A lease is an OS advisory lock on a file under `leases/`, taken by the attempt
wrapper (or by `nyxloom lease run <resource> -- <cmd>` for manual work):

- **Exclusive** (`stack`, `merge-lane`, `carve`): one flock holder. Lock file
  content = owner attempt, purpose, UTC since — metadata for the dashboard;
  the *lock*, not the content, is the mutual exclusion.
- **Counted** (`agent-slots`, `browser`): N slot files; acquire = flock any
  free slot non-blocking.
- **Crash release is kernel-guaranteed** — the failure mode of `.STACK_LOCK` /
  `.CARVE_LOCK` (stale files needing age heuristics) does not exist.
- Cross-project by construction: one shared lease directory; project.toml maps
  local resource names to global lease names (dstdns `stack` ≠ topos's).

`Stack: none|readonly|exclusive` becomes sugar for mutex declarations in
frontmatter (`mutexes: [stack]` for exclusive; readonly = counted reader slots
if ever needed). Same two-layer intent as before — but the belt is the kernel.

## 5. Route adapters and `routes.toml` (F7)

Everything volatile in `ai-cli-controller-guide.md` becomes one versioned
table; handoffs stay route-agnostic (they carry `tier` only):

```toml
[tiers.flash-high]                     # ladder position, carver-stamped
routes = ["opencode-deepseek", "reasonix-deepseek"]   # preference order

[routes.opencode-deepseek]
cli = "opencode"
model = "openrouter/deepseek/deepseek-v4-flash"
variant = "high"
argv_max = 1500                        # wedge guard: substance in handoff file
prompt_hints = ["incremental-write"]   # 504 mitigation clause
probe = "opencode run --model ... 'ALIAS_OK'"
resume = "opencode run -c --session {session} --dir {worktree} ..."
usage_source = "session-json"          # verify per adapter before trust
sandbox = ""                           # codex routes: "danger-full-access" (worktree commit + docker)
```

Dispatch snapshots the resolved route into the attempt event — draft 1's
"never infer routing later from a mutable document" rule, satisfied by
snapshot-on-use. Dated dispatch docs are retired; the wave table is state, not
prose.

## 6. Cost capture (F7)

Every attempt records `{tokens_in, tokens_out, cached_in, cost, currency,
basis}` where `basis ∈ actual | estimated | unknown` — never silently mixed
(draft 1 rule, kept). Extractors, to be verified as M1 tasks:

| CLI | Source | Expected basis |
| --- | --- | --- |
| claude | `-p --output-format json` → usage + `total_cost_usd`; session `.jsonl` per-message usage | actual |
| codex | token totals in `exec` output footer | actual tokens, priced via `prices.toml` |
| opencode | session storage / `session list --format json` | actual-or-estimated (verify) |
| reasonix | DeepSeek API usage fields in run log | actual tokens, priced |

`prices.toml` is dated; costing an attempt records the price-table revision.
Budgets (per task from frontmatter, per project/milestone/day from
project.toml) are enforced at dispatch time by the tick; `BUDGET_WARNING` at
configurable thresholds, `BUDGET_EXHAUSTED` stops dispatch and notifies —
resumable state preserved (draft 1 outcome, kept). The same records feed the
per-tier/per-route quality×cost table (§7), turning
`implementation-benchmark-P51.md` from a hand-written study into a living view.

## 7. Zero-AI dashboard: static render, tiny serving surface

`nyxloom render` regenerates `www/` on every tick and on every mutating
command (<100 ms at this scale). No app server, no DB, no AI — a directory of
static HTML served by anything (`python -m http.server` on loopback, or a
read-only vhost on the existing reverse-proxy).

Views (matching the required list):

- **index** — active tasks: project, state/step, attempt + route, started,
  last-activity, cost-so-far (basis-labelled), current lease holdings, notes
  (latest receipt/BLOCKED line); pause banner; decision-inbox OPEN count;
  provider status (last probe results); budget bars.
- **history** — completed / rejected / blocked / superseded / cancelled with
  evidence links and final cost.
- **dag** — dependency + mutex graph from frontmatter (mermaid), colored by
  state: the sequence/parallelism picture.
- **timeline** — swimlane Gantt from events (dispatch → … → merged), one lane
  per slot: what actually ran concurrently.
- **task/<id>** — frontmatter + contract, attempts (route, resume handle,
  receipt, gate results bound to commits), decisions touching it, cost ledger,
  and a sanitized log excerpt (last N KB, redaction rules from project.toml,
  copied at render — raw logs stay 0600 outside the web root).
- **quality** — per-tier/per-route: attempts, BLOCKED rate, review-rejection
  rate, findings-by-class, cost per merged package; the ladder's evidence.

"Live" means tick-cadence freshness (≤ minutes), which matches how the system
moves. True streaming (SSE tail) is a graduation feature (§9) — explicitly not
worth a server process in the pilot. Viewing costs zero tokens by construction:
there is nothing to invoke.

Security: loopback by default; remote exposure only via authenticated
TLS-terminating proxy; web root contains only rendered, redacted artifacts —
an event can never make an arbitrary path downloadable (draft 1 rule, kept
structurally: the renderer copies allowlisted excerpts, links nothing else).

## 8. Notifications and the decision loop (F8)

Deterministic events → notification hooks; the reference adapter is **ntfy**
(self-hosted topic or ntfy.sh) → phone/desktop push with a click-through URL
into the dashboard. Email/webhook adapters are the same interface. Delivery
failure never mutates workflow truth (draft 1 rule, kept).

Event classes pushed by default: `DECISION_OPENED`, `TASK_BLOCKED`,
`PROVIDER_LIMITED`, `BUDGET_WARNING/EXHAUSTED`, `STALL_CONFIRMED`,
`WAVE_MERGED`, `MILESTONE_COMPLETE`, `SPEC_ATTENTION`, `NEEDS_OPERATOR`.
**Digest mode** (project.toml): per-event push for decisions/blockers, a daily
digest for the rest — per-event push does not scale to human attention.

**Injection boundary:** notification content is built from typed event fields
only — task id, state, oracle id, cost — never from raw agent/log text. A
model that controls its own failure message must not control what lands on the
operator's phone.

The decision loop, end to end:

1. Frontier session files a DECISIONS-INBOX entry (unchanged format: options,
   recommendation, context pointers, **resume prompt**) → `DECISION_OPENED`.
2. Push notification → dashboard decision page.
3. User decides, three equivalent surfaces:
   - `nyxloom decide D-013 --choose b --note "..."` (CLI);
   - edit the inbox entry to DECIDED — next tick ingests it;
   - **discuss first**: open any Claude surface — mobile app, claude.ai/code,
     or Claude Remote Control into a local session — and paste the entry's
     resume prompt (`nyxloom discuss D-013` prints/launches it). The
     session updates the entry per current practice.
4. Tick releases `depends_on: [D-013]` holds, appends `DECISION_RESOLVED`,
   re-renders.

**Claude Remote Control positioning** (agreeing with draft 1, made concrete):
it is a *discussion surface* — the best one for step 3, since the inbox's
resume prompt was designed exactly for it. It is never the notification bus
(push timing is model-selected, requires claude.ai auth + direct Anthropic
connectivity, and a third-party `ANTHROPIC_BASE_URL` session can't be the
remote session) and never the scheduler. Claude Code lifecycle hooks
(Notification/Stop) on interactive sessions MAY forward to the same ntfy topic
so the operator has one channel — but `nyxloom` events remain the workflow
truth. Tokens are spent only when the user opts into a discussion.

## 9. When a daemon would actually earn its place

Graduate from ticks to a resident `nyxloomd` (draft 1's shape) only when a
measured need appears; none is expected in the pilot:

- required reaction latency < tick interval (e.g. interactive queue control);
- true live log streaming (SSE) demanded by daily use;
- multi-host execution (flock stops working across hosts);
- event volume where full scans measurably hurt (thousands/day, not now).

The migration is additive: the daemon would ingest the same events.jsonl,
honor the same statefiles and leases, and the tick remains the degraded-mode
fallback. Nothing in draft 2 paints away from draft 1's end state — it just
refuses to start there.

## 10. Security boundary

Draft 1 ARCHITECTURE §8 / SPEC §14 adopted wholesale: untrusted model output,
allowlisted executables + structured argv, never execute receipt-proposed
commands, project-declared gates only, worktree boundaries, least-privilege
credentials, redaction before anything dashboard-visible, bounded sizes, path
traversal refusal, audited privileged operations. Draft-2 additions: the
notification injection rule (§8) and the static-web-root property (§7). The
secrets rule from dstdns §6 generalizes: an agent needing a secret value is a
`BLOCKED:` condition, never an exception.

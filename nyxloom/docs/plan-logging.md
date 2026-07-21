# Plan: holistic, level-based logging + a log-stream UI

**Status:** proposed · authored 2026-07-20 · **D-L1 (structlog) + timestamp format resolved
2026-07-21 by operator** · owner: operator-directed
**Scope:** nyxloom (`/workspaces/vbpub/nyxloom`). A major cross-cutting refactor that
adds a *measured-verbosity logging subsystem* to **all** nyxloom code, a daemon-global
verbosity control (runtime-adjustable), and a filterable **log-stream page** in the
dashboard. UTC timestamps throughout.

**Worktree:** create one git worktree per phase from local `main` under
`/workspaces/vbpub/.worktrees/<branch>` and work there — never modify the main
`/workspaces/vbpub` checkout directly:
```
git worktree add -b feat/logging-p<NN>-<slug> .worktrees/logging-p<NN>-<slug> main
```
Gate the suite in the tester-unified container (cd into the worktree first), capturing
the REAL exit code — never a bare local pytest:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/.worktrees/<branch>/nyxloom && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m coverage run --source=src/nyxloom -m pytest tests -q && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m coverage json -o /tmp/nyxloom-cov.json && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main \
      --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom'   ; echo GATE_EXIT=$?
```
Merge each phase to `main` serially via merge-tree + CAS (re-check `OLD=$(git rev-parse main)`
each time — main advances under operator commits), preserving operator WIP.

---

## 1. Motivation & goals

Today nyxloom has **no logging** (verified: zero `logging`/`getLogger` across `src/`). Its
only outputs are (a) the **event log** (`events.jsonl`, the replayed domain source of truth),
(b) **CLI `print`s** (doctor tables, status — user-facing stdout), and (c) a handful of ad-hoc
diagnostic `print`s (e.g. the 2026-07-20 http_bind startup notice). When something goes wrong
mid-pass — a dispatch that never fires, a guard that silently excludes a task, a gate that
hangs — there is no dial to turn up and nothing to read. Debugging means adding a `print` and
redeploying.

**Goals**
- **G1 — measured verbosity everywhere.** Every module emits calibrated, level-gated log
  records (ERROR→TRACE). Turning the level to DEBUG makes the daemon's reasoning legible.
- **G2 — runtime-adjustable, no restart.** Flip to DEBUG live from the UI while reproducing a
  bug; drop back to INFO after. This is the single biggest debugging win.
- **G3 — a log-stream page** in the dashboard: live tail + history, level filter, **highlight
  words**, **context around a line**, UTC timestamps.
- **G4 — structured & UTC.** JSONL records `{ts, level, logger, project, task, attempt,
  event, msg, **fields}`; `ts` is UTC in `YYYY-MM-DDTHH:MM:SS` (operator-chosen — no fractional
  seconds, no offset suffix; the field is documented UTC). Machine-filterable, human-readable.
- **G5 — proven-standard tooling** (operator standing directive). **structlog** (D-L1, chosen
  2026-07-21) for the structured/context/render layer, bridged to stdlib `logging`'s
  `RotatingFileHandler` for the sink + rotation.

**Non-goals (v1)**
- Not replacing or augmenting the **event log** — logs are a *separate, disposable* stream (§3).
- No remote log shipping (ELK/Loki), no OpenTelemetry/distributed tracing, no per-request spans.
- No auth on the logs page — it inherits the private-unpublished-network trust model the
  http_bind decision (2026-07-20) already established for the whole HTTP surface.

---

## 2. The load-bearing principle: **logs are not events**

nyxloom is **event-sourced**: `events.jsonl` is replayed to rebuild all state, so it must stay
a deterministic sequence of *domain facts* (TASK_CREATED, ATTEMPT_FAILED, CARVE_OUTCOME…).
Diagnostic logging is a *different concern* and must never leak into that stream:

| | Event log (`events.jsonl`) | Logs (`logs/nyxloom.jsonl`) |
|---|---|---|
| Purpose | domain source of truth | operational diagnostics |
| Replayed to rebuild state | **yes** — determinism-critical | **never** |
| Schema | fixed `EventType` enum | free-form `msg` + structured fields |
| Lifetime | retained, authoritative | rotated & disposable |
| Levels | n/a | ERROR…TRACE, gated |
| Who reads | the daemon (replay) + dashboard | operator debugging |

**Rule:** logging is additive and side-effecting; it may *accompany* an event append (a
dispatch both appends `ATTEMPT_CREATED` **and** logs an INFO line) but a log call must never be
the thing that records domain state, and must never be replayed. This keeps `storage.replay`
untouched and the B24/doctor replay-divergence guards intact.

**Why not one stream? (the common intuition, answered.)** "An event occurs, the daemon acts, a
log line is written" is exactly right — there is no contradiction between logging and
event-sourcing. The only constraint is the two live in *separate files*, because "event" is
overloaded: nyxloom's `Event` is not "something that happened," it is a typed record that IS the
database — current state is reconstructed by replaying `events.jsonl` through `storage.apply()`.
A free-form log line is not a domain fact there is anything to "apply"; the event log is
authoritative and permanent while logs are disposable and rotated (you can't rotate a file that
is also your database); and `doctor` re-replays the stream to check divergence. So the event
append and the log write are two parallel writes to two files — matching the intuition exactly.

**The one genuine subtlety — replay is silent.** Logging belongs to the LIVE action path, never
the REPLAY path. On startup the daemon replays the whole history to rebuild state; if
`storage.apply()` logged "dispatched task X" during replay, every restart would re-log months of
history as if it were happening now. So `storage.apply`/replay stays silent (TRACE at most);
logs fire only where the daemon acts on a *fresh* event. (Enforced by an oracle in P05a.)

---

## 3. Design decisions (resolve before P01)

- **D-L1 — library. RESOLVED 2026-07-21: structlog.** Operator chose structlog over the
  stdlib-only option for its structured/context ergonomics. structlog natively provides the
  three things the wrapper would otherwise hand-roll: `structlog.contextvars`
  (`bind_contextvars`/`unbind_contextvars`) for auto-binding `project/task/attempt`; a
  `TimeStamper` processor for the UTC timestamp; and `JSONRenderer` for the JSONL line.
  Level gating via `make_filtering_bound_logger(level)`. **Rotation is not structlog's job** —
  bridge to stdlib logging's `RotatingFileHandler` via `structlog.stdlib.ProcessorFormatter`
  (the standard structlog+stdlib setup), so structlog renders and stdlib rotates. **Cost to
  accept:** structlog is a **runtime** dependency (the daemon uses it in prod), so it lands in
  `pyproject` *main* deps, not just `[test]` — it must flow into BOTH the tester-unified image
  (`/opt/tester-venv`) AND the nyxloomd runtime image (`/opt/nyxloom-venv`). P01 therefore
  rebuilds both images (see §7).
- **D-L2 — level taxonomy + timestamp.** `CRITICAL(50) / ERROR(40) / WARNING(30) / INFO(20) /
  DEBUG(10) / TRACE(5, custom)`. nyxloom semantics in §5's rubric. Default effective level:
  **INFO**. Timestamp (operator-chosen 2026-07-21): `TimeStamper(fmt="%Y-%m-%dT%H:%M:%S",
  utc=True)` → `ts` like `2026-07-21T14:03:07` (UTC; no fractional seconds, no offset suffix).
  Note this deliberately differs from the event log's own `iso()` format — logs are a separate
  stream with its own convention.
- **D-L3 — verbosity config = daemon-global, not per-project toml.** Directly applies the
  http_bind lesson (2026-07-20): operational/target-specific settings do **not** belong in the
  bind-mounted, shared-verbatim `nyxloom.toml`. Precedence (highest wins):
  1. **runtime override** — set via the UI, persisted to `daemon/log-level` (a small state
     file, not toml), so a live DEBUG flip survives a daemon respawn;
  2. **`NYXLOOM_LOG_LEVEL` env** — compose/infra bootstrap (e.g. start a debug container);
  3. **`[logging] level`** in the primary project's config — a static default *if* set;
  4. hardcoded **INFO**.
  "Application-layer config value" (operator's words) = this daemon-global setting with that
  precedence, runtime-adjustable being the point.
- **D-L4 — logs ≠ events.** Per §2. Asserted as an invariant with a test that a full pass
  emits log records but appends **no** extra events beyond the domain ones.
- **D-L5 — reconcile stays pure.** `reconcile.plan_project` is PURE (no clock, no I/O — a
  load-bearing invariant). It must not import/emit logs. *Recommend:* `plan_project` also
  returns a **`ReconcileTrace`** (pure data: ordered breadcrumbs — "dispatch P12 via route-x",
  "carve skipped: paused", "task P7 excluded: decision-held") which the **daemon** flushes to
  the logger at DEBUG after the pass. Purity preserved; reconcile fully debuggable. (§4.3)
- **D-L6 — storage & rotation.** One daemon-global stream `logs/nyxloom.jsonl` under the state
  volume (records carry a `project` field; one file is far easier to tail/filter in the UI than
  N per-project files). `RotatingFileHandler` (proven-standard) — *recommend* size-based
  (e.g. 20 MB × 5 backups) so a debug burst can't fill the volume; retention bounded by the
  backup count, independent of `retention_days` (which governs the event log). Gitignored;
  never in the trove.
- **D-L7 — UI v1 scope.** Level filter + substring/regex **highlight** + **context-around-line**
  (click → N lines before/after) + **live tail** (SSE) + **history/paging** + UTC display +
  colour-by-level + pause/resume. *Defer to v2:* full-text server-side search, download/export,
  multi-file (rotated-backup) browsing.

---

## 4. Architecture

### 4.1 `src/nyxloom/log.py` (new — the core, structlog-based)
- `get_logger(name) -> structlog.BoundLogger` — returns `structlog.get_logger(name)`; every
  module does `log = get_logger(__name__.split('.')[-1])` at import.
- **Processor chain** (structlog) → one JSONL line per record:
  `merge_contextvars` → add level → `TimeStamper(fmt="%Y-%m-%dT%H:%M:%S", utc=True)` (key `ts`)
  → `add_logger_name` (key `logger`) → `EventRenamer("msg")` (structlog's positional `event`
  arg becomes our `msg`) → render. Final line:
  `{"ts":"2026-07-21T14:03:07","level":"info","logger":"daemon","project":...,"task":...,
    "attempt":...,"event":<slug?>,"msg":"...", ...fields}`.
  (`event` here is our optional machine slug field, distinct from structlog's positional arg,
  which we rename to `msg` to avoid the clash with §2's domain "Event".)
- **Context binding** — `structlog.contextvars`: a `bind(project=?, task=?, attempt=?)` context
  manager wrapping `bind_contextvars(...)`/`reset_contextvars(...)`; `merge_contextvars` (first
  processor) stamps them onto every record. The daemon wraps each per-project pass in
  `with log.bind(project=p):` and each attempt-execution in `with log.bind(task=…, attempt=…):`
  so call sites never thread context manually.
- **`configure(level, log_dir, console=True)`** — idempotent. Wires structlog to stdlib via
  `ProcessorFormatter`: the shared processor chain feeds a stdlib `RotatingFileHandler`
  (`logs/nyxloom.jsonl`, JSON renderer, §D-L6 sizing) and, if `console`, a stderr handler
  (structlog `ConsoleRenderer`, human one-line, →`docker logs`) at INFO. Level gating via
  `structlog.make_filtering_bound_logger(level)` as `wrapper_class`. Scoped to nyxloom loggers;
  does not mutate the stdlib root (test-isolation safe).
- **`set_level(level)`** — re-set the filtering `wrapper_class` live (runtime override).
- **`TRACE = 5`** — registered as a structlog level (+ `log.trace(...)` bound method).
- **Laziness rule** (perf + coverage): call sites use `log.debug("msg", x=v)` — structlog defers
  rendering until a handler accepts the record, and the filtering bound logger drops
  below-level calls cheaply. **Never** `if enabled: log.debug(f"{expensive()}")`; the call line
  always executes (so B62 diff-coverage covers it on any path that runs) while formatting is
  deferred. Expensive *field computations* guard on `log.is_enabled_for(DEBUG)` only when the
  computation itself (not the logging) is costly.

### 4.2 `paths.py`
- `logs_dir()` → `state_root()/logs`; `nyxloom_log_path()` → `logs_dir()/nyxloom.jsonl`.
- `daemon_log_level_path()` → `daemon_dir()/log-level` (runtime-override persistence).
- `ensure_layout()` gains `logs_dir()`.

### 4.3 Reconcile trace (`reconcile.py`, P03)
- New `@dataclass ReconcileTrace` (pure): `breadcrumbs: list[TraceNote]` where each note is
  `(kind, task_id?, detail)` — enum-ish `kind` (e.g. `dispatch`, `dispatch-skip`, `carve`,
  `carve-skip`, `merge`, `guard-exclude`, `state-transition`). `plan_project` returns
  `(actions, trace)` (or `PlanResult(actions, trace)` to avoid churning ~50 call sites — pick
  one in P03, prefer a result object with `.actions` back-compat).
- Populated at the existing decision points (the carve guards, dispatch loop, reject triage,
  ready-to-carve, etc.) with **pure strings/ids only** — never handoff prose (payload-injection
  rule item 8 still applies).
- The daemon, post-`plan_project`, iterates `trace.breadcrumbs` and emits one DEBUG log each,
  inside the `bind(project=)` context. Zero I/O inside `plan_project`; determinism preserved.

### 4.4 Runtime control (`daemon.py`, P02)
- `Daemon.run()` calls `log.configure(resolve_level(), paths.logs_dir())` before the loop, then
  logs an INFO "daemon started" with version/registry.
- `resolve_level()` implements D-L3's precedence (runtime file → env → config → INFO).
- `POST /api/config/log-level {level}` — joins `_CONFIG_POST_PATHS`; validates against the level
  names; calls `log.set_level()` + persists to `daemon_log_level_path()`; emits an INFO log
  (NOT a domain event). `GET /api/logs/level` returns the current effective level + its source.

### 4.5 UI (`daemon.py` endpoints + `render.py` page, P04)
- `GET /api/logs?level=&since=&limit=&project=` — reads `nyxloom.jsonl` tail, filters
  server-side by level≥ and optional project/since, returns a JSON array (newest-last), capped
  by `limit`. Paging via `since` (a monotonic line seq or ts).
- `GET /api/logs/stream?level=` — SSE tail, reusing the **exact** pattern of the existing
  `/api/stream` events endpoint (`SSE_POLL_SECONDS` poll + `SSE_HEARTBEAT_SECONDS` heartbeat),
  emitting new JSONL lines at/above the requested level.
- `render.py` `_render_logs_html()` + a **"Logs"** `<nav>` link + a `render_all` call. Client JS
  (self-contained, CSP-safe — no external libs): `EventSource('/api/logs/stream')` live tail;
  a level `<select>`; a **highlight** `<input>` (substring, optional `/regex/`) that wraps
  matches in `<mark>`; **context** — click a row → fetch/scroll ±N neighbouring lines and dim
  the rest; colour rows by level; a pause/resume-tail toggle (mirrors live.html); render `ts` in
  UTC explicitly (`…Z`). Reuses live.html's raw-JSON toggle idiom.

### 4.6 Test/gate isolation
- In tests, `log.configure(level=CRITICAL, console=False)` (or an in-memory handler via a
  fixture) so logging never spams pytest stdout and records are assertable. A shared
  `caplog`-style helper returns captured structured records.
- **B62 interaction:** the diff-coverage gate makes every added log line executable code needing
  coverage. The §4.1 laziness rule (unconditional `log.debug(...)` on already-covered paths, no
  `if`-guards) keeps instrumentation lines covered by the tests that already exercise those
  paths. Genuinely unreachable-in-test log lines (rare) use `# pragma: no cover` with a reason.

---

## 5. Instrumentation rubric (the sweep's contract)

The point is *measured* verbosity — not noise. Every phase-5 batch follows this:

- **CRITICAL** — the daemon cannot continue safely (corrupt registry, unwritable state volume).
- **ERROR** — a handled failure that fails the unit of work: gate failed, merge conflict
  escalated, receipt parse-failed, attempt errored, replay divergence detected.
- **WARNING** — degraded-but-continuing: a retry, a route probe failure/pause, a fallback taken,
  a watchdog suppression, work skipped for a soft reason, a config value out of expected range.
- **INFO** — the operational narrative one wants in `docker logs`: daemon start/stop, project
  pass begin/end (terse), dispatch, review launch, merge, carve dispatch, state transitions,
  config changes, pause/unpause. One line per *decision that changed the world*.
- **DEBUG** — the reasoning: reconcile trace breadcrumbs (why dispatched / why skipped), guard
  evaluations, per-pass counts, HTTP requests served, cache hits, provider-probe details.
- **TRACE** — firehose: every event append, every state file read/write, every file poll. Off
  except when chasing something specific.

Each log call binds context (`project`/`task`/`attempt`) via the active `bind()` scope, and
carries a short `event=` slug for machine filtering where it maps to a domain moment.

---

## 6. Phases

Each phase is an independently gate-able, independently mergeable package with non-hollow
oracles. P01–P04 are sequential (foundation → config → trace → UI); P05* (the sweep) parallelize
by module batch once P01+P03 land.

### P01 — logging core (`log.py` + `paths.py` + structlog dep) — **foundation, blocks all**
Build §4.1 + §4.2. Add `structlog` to `pyproject` **main** dependencies (not `[test]` — it is a
runtime dep). Add it to the tester-unified Dockerfile closure smoke-import. **Rebuild both
images** (tester-unified for the gate; nyxloomd runtime at deploy — see §7). Convert the one
existing diagnostic print (the http_bind startup notice) to `log.warning(...)` as the first real
user of the module (proof it works end-to-end).
**Oracles:** the processor chain renders a record with all context + `ts` matching
`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$` (UTC, no offset/fraction); `bind()` nests and clears on
exit (incl. on exception — contextvars reset); level gating (DEBUG dropped at INFO, emitted at
DEBUG); `set_level` changes the effective level live; file (JSON) vs console (human) handlers
have independent levels; `TRACE` works and sits below DEBUG; configure is idempotent and does
not mutate the stdlib root (a sibling `logging.getLogger("other")` is unaffected). **Gate:** full
tester-unified (rebuilt with structlog).

### P02 — verbosity config, bootstrap & runtime control — *depends P01*
§4.4: `resolve_level()` precedence, wire `configure()` into `Daemon.run`, the POST/GET level
endpoints, persistence.
**Oracles:** precedence chain — runtime-file beats env beats `[logging]` beats INFO (four
tests, each removing the top layer); `POST /api/config/log-level` changes the effective level
**without restart** and persists (survives a simulated respawn reading the file); invalid level
→ 400, level unchanged; the change emits an INFO **log** and **no domain event** (D-L4). **Gate:** full.

### P03 — reconcile trace (pure-core observability) — *depends P01*
§4.3: `ReconcileTrace`/`PlanResult`, populate breadcrumbs at decision points, daemon flushes at
DEBUG. **Oracles:** `plan_project` remains pure (no `log`/`nyxloom.log` import reachable from it;
deterministic given identical input); the trace records the decisive reason for representative
cases — dispatch (names route), dispatch-skip (paused / no-route / budget), carve-skip (paused /
in-flight), guard-exclude (decision-held), a state transition; the daemon emits exactly one
DEBUG record per breadcrumb with `project` bound; **breadcrumbs carry ids/enums only, no handoff
prose** (payload-injection rule); the ~50 existing reconcile tests still pass (back-compat of the
return shape). **Gate:** full.

### P04 — log-stream UI (endpoints + page) — *depends P01, P02*
§4.5. **Oracles (unit):** `/api/logs` filters by level≥ and `since`/`limit`; `/api/logs/stream`
emits new lines at/above the requested level and heartbeats; `render_all` produces `logs.html`
with the nav link. **Oracles (pwmcp browser, per the UI-testing standard):** load the Logs page →
a streamed line appears within N s; typing a highlight term wraps matches in `<mark>` and
non-matches are unmarked; selecting a higher level hides lower-level rows; clicking a row reveals
±N context lines and dims the rest; timestamps render with a `Z`/UTC marker. **Gate:** full +
the pwmcp UI leg (`ciu up --dir infra/pwmcp`; UI at `http://webapp-ui/` per policy — here the
nyxloom dashboard origin).

### P05a — sweep: effect core (`daemon.py`, `storage.py`, `wrapper.py`, `adapters.py`) — *depends P01, P03*
Instrument per §5; convert remaining daemon-internal prints to logger calls (keep none). Bind
`task`/`attempt` context around attempt execution.
**Oracles:** a dispatch emits INFO with `project`+`task`+`route`; a gate failure emits ERROR; an
attempt retry emits WARNING; an event append emits TRACE; each on its already-covered path (no
new uncovered branches — the laziness rule). **Gate:** full.

### P05b — sweep: flow/support (`stages.py`, `notify.py`, `watchdog.py`, `leases.py`, `commands.py`) — *depends P01; parallel with P05a/c*
Per §5. Notably: watchdog escalations at WARNING/ERROR, lease acquire/lose at DEBUG/INFO, notify
sends at INFO (never log secret tokens — explicit oracle). **Gate:** full.

### P05c — sweep: intake/lint/misc (`intake_chat.py`, `decision_chat.py`, `backlog_items.py`, `lint.py`, `frontmatter.py`, `decisions.py`, `render.py`, `config.py`, `cli.py`) — *depends P01; parallel*
Per §5. **Critical distinction:** `cli.py`'s **user-facing** `print`s (doctor tables, `status`,
`show-dispatch`, the dashboard-URL line) **stay `print`** — they are the CLI's stdout contract,
not diagnostics. Only *daemon-internal* diagnostics become logs. **Oracle:** `nyxloom doctor`
and `status` stdout is byte-unchanged; config load logs a DEBUG on resolve; no secret values
logged. **Gate:** full.

### P06 — conventions doc, retention finalize, optional guard — *depends P05*
`docs/logging.md`: the §5 rubric, the record schema, `get_logger`/`bind` usage, the UI guide,
the logs-vs-events principle. Finalize rotation/retention (size×backups) with a test. *Optional
(may defer):* a doctor/lint check that new daemon-path source uses `get_logger` not bare
`print`. **Gate:** full.

---

## 7. Rollout & redeploy
- **P01 adds a runtime dependency (structlog, D-L1).** Add to `pyproject` main deps → rebuild
  the **tester-unified** image from the worktree context before gating (`docker build -f
  tester-unified/Dockerfile -t tester-unified:local .worktrees/<branch>`), AND rebuild the
  **nyxloomd runtime** image at deploy (`ciu up --dir nyxloom/nyxloomd`, per the self-hosting
  caveat) so `/opt/nyxloom-venv` carries structlog. This is the one phase whose deploy is a full
  image rebuild rather than a bind-mount restart.
- P01–P03 change **daemon runtime code** → each needs a daemon restart to take effect
  (`docker exec nyxloom-prod-nyxloomd pkill -f 'nyxloom.cli daemon'` — note this recycles the
  container via the argv artifact; verify healthy + doctor after). Behaviour is otherwise
  additive; production stays functional at INFO throughout.
- P04 adds UI/endpoints → restart to serve the new page; static `logs.html` re-renders on the
  next event.
- The sweep (P05*) is additive logging only → restart per batch to see new lines; no behaviour
  change, so redeploy is low-risk and can batch.
- Set `NYXLOOM_LOG_LEVEL` in `nyxloomd/ciu.compose.yml` / `docker-compose.yml` (default `info`)
  as part of P02 so the infra layer owns the bootstrap default (consistent with the http_bind
  precedent).

## 8. Risks
- **Event-log contamination (highest).** Mitigated by §2 + D-L4's explicit no-new-events oracle.
- **Reconcile purity regression.** Mitigated by P03's pure trace + a purity oracle; do NOT let
  the sweep add a bare `log` import to `reconcile.py`.
- **Diff-coverage churn (B62).** Mitigated by the laziness rule (calls on covered paths); budget
  extra effort on P05 tests to keep 100% without hollow assertions.
- **Log volume / disk.** Mitigated by size-based rotation (D-L6); DEBUG/TRACE off by default.
- **Test-stdout spam / global-logger leakage.** Mitigated by §4.6 isolation (`propagate=False`,
  configure-in-test, no stdlib-root mutation).
- **Perf of DEBUG in hot paths.** Mitigated by stdlib `isEnabledFor` gating + lazy `%s` args.

## 9. Open decisions
- **Resolved 2026-07-21:** D-L1 → **structlog**; D-L2 timestamp → **`YYYY-MM-DDTHH:MM:SS` UTC**.
- **Still open (confirm before their phase):** D-L5 (reconcile trace vs injected logger vs relax
  purity — recommend trace, blocks P03), D-L6 (one global file vs per-project; rotation sizing —
  recommend one file + size rotation, blocks P01's handler config), D-L7 (v1 UI feature cut —
  blocks P04). Recommendations inline in §3; these become `D-<NNN>` entries in
  `nyxloom-trove/decisions.md` at carve time.

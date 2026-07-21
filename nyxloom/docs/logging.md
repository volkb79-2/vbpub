# nyxloom logging — conventions & operator guide

**Status:** canonical · last-verified 2026-07-21 · owner: operator-directed
**Scope:** how nyxloom emits, levels, stores, filters, and reads diagnostic logs.
The *design* rationale and the resolved decisions (D-L1…D-L7) live in
[`plan-logging.md`](plan-logging.md); this doc is the standing reference for
anyone adding a log call or operating the daemon.

Built across LP01/P02/P03/P04/P05a-c/P06. Library: **structlog** (D-L1), bridged
to stdlib `logging` for the rotating file sink.

---

## 1. The load-bearing rule: **logs are not events**

nyxloom is **event-sourced** — `events.jsonl` is replayed through
`storage.apply()` to rebuild all state, so it must stay a deterministic sequence
of *domain facts* (`TASK_CREATED`, `ATTEMPT_FAILED`, …). Diagnostic logging is a
**separate, disposable** concern and must never leak into that stream.

| | Event log (`events.jsonl`) | Logs (`logs/nyxloom.jsonl`) |
|---|---|---|
| Purpose | domain source of truth | operational diagnostics |
| Replayed to rebuild state | **yes** (determinism-critical) | **never** |
| Schema | fixed `EventType` enum | free-form `msg` + fields |
| Lifetime | retained, authoritative | rotated & disposable |
| Levels | n/a | ERROR…TRACE, gated |

A log call may *accompany* an event append (a dispatch both appends
`ATTEMPT_CREATED` **and** logs an INFO line) but must never be the thing that
records domain state.

**The one genuine subtlety — replay is silent.** Logging belongs to the LIVE
action path, never the REPLAY path. On startup the daemon replays the whole
history to rebuild state; if `storage.apply()` logged during replay, every
restart would re-log months of history as if it were happening now. So
`storage.apply`/replay stays silent — the append/save TRACE lines fire only on a
*fresh* live append, never inside `apply_event`. (Enforced by a P05a oracle.)

---

## 2. The record schema

One JSONL line per record, rendered by the structlog processor chain
(`_SHARED_PROCESSORS` → `JSONRenderer`). Example:

```json
{"ts":"2026-07-21T14:03:07","level":"info","logger":"daemon","project":"demo","task":"P12","attempt":"att-9f3","msg":"dispatch","route":"sonnet5-high"}
```

| key | always? | notes |
|---|---|---|
| `ts` | ✔ | UTC, `YYYY-MM-DDTHH:MM:SS` — **no** fractional seconds, **no** offset suffix (D-L2). Deliberately differs from the event log's `iso()`. |
| `level` | ✔ | lowercase name: `trace`/`debug`/`info`/`warning`/`error`/`critical`. |
| `logger` | ✔ | short module name, e.g. `daemon`, `reconcile`. |
| `msg` | ✔ | the human message — structlog's positional arg, renamed from `event` to `msg` by `EventRenamer` (avoids clashing with the domain "Event"). |
| `project` / `task` / `attempt` | only when bound | injected by the active `bind()` scope (contextvars), **not** fixed columns. |
| `event` | only if passed | an *optional machine slug* a call may add for filtering (`event_type=`/`event=` kwarg) — distinct from `msg`. |
| *(any kwarg)* | as passed | e.g. `route="…"`, `count=3` pass through verbatim. |

**Consumers must treat `project`/`task`/`attempt`/`event` as optional** — never
assume a fixed key set or column order. (The `/api/logs` reader and `logs.html`
renderer both use `.get(...)`/`x || default` accordingly.)

---

## 3. Levels — the measured-verbosity rubric

The point is *measured* verbosity, not noise. Default effective level: **INFO**.

- **CRITICAL** — the daemon cannot continue safely (corrupt registry, unwritable
  state volume).
- **ERROR** — a handled failure that fails the unit of work: gate failed, merge
  conflict escalated, receipt parse-failed, attempt errored, replay divergence.
- **WARNING** — degraded-but-continuing: a retry, a route probe pause, a fallback
  taken, a watchdog suppression, work skipped for a soft reason.
- **INFO** — the operational narrative you want in `docker logs`: daemon
  start/stop, project pass begin/end, dispatch, review launch, merge, carve
  dispatch, state transitions, config changes, pause/resume. *One line per
  decision that changed the world.*
- **DEBUG** — the reasoning: reconcile-trace breadcrumbs (why dispatched / why
  skipped), guard evaluations, per-pass counts, HTTP requests served.
- **TRACE (5)** — firehose: every event append, every state file read/write,
  every poll. Off except when chasing something specific.

---

## 4. Emitting logs — `get_logger` + `bind`

At module top:

```python
from nyxloom import log
_log = log.get_logger(__name__.split(".")[-1])   # short logger name
```

Call sites use lazy, unconditional calls — **never** an `if enabled:` guard
around the log call itself (the filtering bound logger drops below-level calls
cheaply, and an unconditional call keeps the line covered by the tests that
already run that path — the B62 diff-coverage rule):

```python
_log.info("dispatch", route=route.id)          # good — deferred formatting
_log.debug("guard excluded task", task=t.id, reason="decision-held")
```

Guard only an **expensive field computation** (not the logging) with
`log.is_enabled_for(DEBUG)`.

**Context binding** — wrap a scope so call sites never thread context manually:

```python
with log.bind(project=p):
    ...
    with log.bind(task=t.id, attempt=a.id):
        _log.info("attempt started")           # record carries project+task+attempt
```

`bind()` nests and reverts on exit (including on exception — contextvars reset).

### 4b. structlog reserved-key gotchas (read before instrumenting)

These are invisible to a fast diff read and bit every P05 instrumentation batch:

1. **`event=` is RESERVED** → `log.info("msg", event=x)` hard-`TypeError`s at
   call binding (structlog's first positional is named `event`). Use
   `event_type=` for a machine slug.
2. **`level=` is silently CLOBBERED** by the `add_log_level` processor. Use
   `effective_level=` / `new_level=`.
3. **`log.trace` doesn't exist before `configure()`** (TRACE is a custom
   extension the wrapper installs). Guard hot-path trace calls, or ensure
   `configure()` ran; tests must `configure()` first.
4. **Pre-configure default logs to STDOUT** (structlog's PrintLogger). A
   short-lived CLI never calls `configure()`, so the CLI bootstrap
   (`_bootstrap_logging`) configures with `console=False` to keep stdout exactly
   its `print()` contract.

### 4c. Reconcile stays pure (D-L5)

`reconcile.plan_project` is **pure** (no clock, no I/O — the doctor divergence
check depends on it). It must **not** import or emit logs. Instead it returns a
`ReconcileTrace` (ordered, id/enum-only breadcrumbs); the **daemon** flushes each
breadcrumb to the logger at DEBUG after the pass, inside the `bind(project=)`
scope. Never add a bare `log` import to `reconcile.py`.

---

## 5. Configuration & runtime control (D-L3)

`log.configure(level, log_dir, console)` wires structlog → a stdlib
`RotatingFileHandler` (JSONL file) and, if `console`, a terse stderr handler
(fixed at INFO → `docker logs`). It is idempotent and scoped to the `nyxloom`
channel only (never mutates the stdlib root — test-isolation safe).

**Effective-level precedence** (highest wins; `resolve_level` in `daemon.py`):

1. **runtime override** — set via the UI / `POST /api/config/log-level`,
   persisted to `daemon/log-level` (survives a daemon respawn).
2. **`NYXLOOM_LOG_LEVEL`** env — the compose/infra bootstrap default
   (`nyxloomd/ciu.compose.yml`, default `info`).
3. **`[logging] level`** in the primary project's config — a static default.
4. hardcoded **INFO**.

Flip live with no restart:

```bash
curl -X POST http://<daemon>/api/config/log-level -d '{"level":"debug"}'
curl http://<daemon>/api/logs/level        # -> {"level":"debug","source":"runtime"}
```

A level change emits an INFO **log** and **no domain event** (D-L4).

---

## 6. Storage, rotation & retention (D-L6)

One daemon-global stream **`<state>/logs/nyxloom.jsonl`** (records carry a
`project` field; one file is far easier to tail/filter than N per-project files).
Gitignored; never in the trove.

**Rotation is size-based** via stdlib `RotatingFileHandler` — the proven v1
scheme. Two operational knobs (env, same infra-config precedent as
`NYXLOOM_LOG_LEVEL`; **not** the shared `nyxloom.toml`):

| env | default | meaning |
|---|---|---|
| `NYXLOOM_LOG_MAX_BYTES` | `10000000` (~10 MB) | size at which the current segment rolls |
| `NYXLOOM_LOG_BACKUPS` | `5` | number of prior `nyxloom.jsonl.<n>` segments kept |

Total retention is bounded to roughly `max_bytes × (backups + 1)`. An
absent/invalid/negative value falls back to the default (a compose typo never
crashes logging setup). Resolved by `log.resolve_rotation()`.

> **Deferred to v2 (D-L6 aspiration):** compressing aged segments to
> `nyxloom.jsonl.<date>.zst` (~10–20× on JSONL) via a `TimedRotatingFileHandler`
> with a zstd rotator, keeping the last 3 days native. v1 ships the simpler,
> proven size-based scheme; the UI's rotated-backup browsing is likewise v2.

**Reading rotated segments with standard tools** (the file is plain JSONL):

```bash
tail -f  <state>/logs/nyxloom.jsonl
jq  'select(.level=="error")'  <state>/logs/nyxloom.jsonl
lnav     <state>/logs/nyxloom.jsonl        # level colouring, SQL, timeline
```

---

## 7. The Logs page (P04, D-L7 "Full + search/export")

Dashboard → **Logs** (`/www/logs.html`), served by the daemon. Self-contained
(no external assets; all JS inline, CSP-safe — rows built via
`document.createElement`/`textContent`, never `innerHTML`).

Features:

- **Live tail** — `EventSource('/api/logs/stream')` (SSE, mirrors the events
  `/api/stream` poll/heartbeat).
- **Level filter** — a `<select>`; hides rows below the chosen level.
- **Highlight** — a substring or `/regex/` term; wraps matches in `<mark>` on
  incoming rows. (v1: applies to newly-streamed rows, not retroactively to
  already-rendered history — a candidate enhancement.)
- **Server search** — `?q=` on `/api/logs`: server-side substring over the whole
  record, composes with `level`/`project`/`since`/`limit`.
- **Export** — `GET /api/logs/export` → a JSONL download (`Content-Disposition:
  attachment`), honouring the active filters.
- **Context** — click a row → reveal ±N neighbouring lines, dim the rest.
- **Pause/resume tail** — buffers incoming rows while paused, flushes on resume.
- **Colour-by-level**, **raw-JSON toggle**, **UTC timestamps** (rendered `…Z`).

### 7b. The endpoints

| endpoint | returns |
|---|---|
| `GET /api/logs?level=&since=&limit=&project=&q=` | JSON array (newest-last), server-filtered. Missing file → `[]`. Paging cursor is an injected line-index `seq`; page with `since=<last seq>`. |
| `GET /api/logs/stream?level=` | SSE `data:` frames for new lines at/above `level`, heartbeats; rotation-aware (offset resets if the file shrinks). |
| `GET /api/logs/export?…` | JSONL download of the filtered set. |
| `GET /api/logs/level` · `POST /api/config/log-level` | read / set the effective level (§5). |

**Trust model:** the whole HTTP surface is unauthenticated and loopback-by-
default (the http_bind decision). The log endpoints are read-only and inherit
that model; secrets are kept out of records at write-time (a P05b oracle), so
there is no read-time redaction.

---

## 8. Testing conventions

- Configure with `log.configure(level=CRITICAL, console=False)` (or per-test with
  a `tmp_path` `log_dir`) so logging never spams pytest stdout and records are
  assertable by reading the JSONL back.
- The 100% diff-coverage floor makes every added log line executable code needing
  coverage — the unconditional-call rule (§4) keeps instrumentation covered by
  the tests that already exercise those paths. Genuinely unreachable lines use
  `# pragma: no cover` with a reason.
- Daemon HTTP/SSE loop code: extract the per-poll logic into a pure helper and
  test it in the main thread (see `_log_stream_tick` in `daemon.py`) — coverage
  of a loop body running inside the HTTP handler thread is racy, and the
  uncovered lines are usually genuine edge branches (rotation, blank/malformed
  line, disconnect) the happy-path integration test never hits.

# Plan: state-integrity — SQLite event store + ground-truth re-baseline

**Status:** proposed · authored 2026-07-21 · operator-directed
**Sequence:** this is **step 1 & 2** of the agreed order *event-store → re-baseline →
logging* (2026-07-21). Part A (SQLite) is the substrate; Part B (re-baseline) sits on the
storage API and so works on either backend, but lands after A per the chosen order.
**Worktree / gate / merge:** same protocol as `plan-logging.md` §Worktree — one worktree per
phase under `/workspaces/vbpub/.worktrees/<branch>` from `main`; gate in tester-unified
capturing `GATE_EXIT`; merge-tree + CAS re-checking `OLD` each time; preserve operator WIP.

---

## Why (the evidence)

nyxloom keeps **two** stores: the git-tracked **trove** (handoffs = task *definitions*, reports,
decisions, spine) and the non-git **state volume** (statefiles = task *lifecycle*, `events.jsonl`
= append-only WAL, attempts, receipts, leases). A statefile points at its handoff via
`handoff_path`. Read path is the **materialized statefiles** (`list_states`); `events.jsonl` is a
WAL/audit; full **replay** runs only in `doctor` (divergence audit) and disaster recovery.

Two structural problems, both observed live:

1. **Non-atomic dual write.** `storage.append_and_apply` appends to `events.jsonl`, *then*
   writes statefiles (`save_state`, which hand-rolls flock + tmp + rename). A crash between the
   two, or any bug, leaves them disagreeing — which is exactly why `doctor`'s replay-divergence
   check has to exist. A transactional store makes that whole class impossible.
2. **State drifts from reality when a human advances a project.** Statefiles only transition via
   nyxloom's *own* actions. Evidence (2026-07-21): dstdns has **8 tasks nyxloom believes are
   `MERGE_READY`**, but `dstdns-P30` and `ui-P10` were **already merged manually** while paused —
   their merge commits are in the dstdns repo and `doctor` reports `orphan statefile: handoff …
   missing` because the handoffs were archived. `_merged_branches` reads git but only to avoid
   re-dispatch (it never advances `MERGE_READY→MERGED`) and is brittle (only sees merges that
   leave a discoverable `--merged` branch — a squash/CAS/deleted-branch merge is invisible).
   **On resume, nyxloom would try to merge already-merged work** → escalations, churn,
   mis-triage. topos is the same shape. There is no "reconcile from ground truth" heal today.

The clean division this plan establishes: **SQLite for the authoritative state (consistency
matters)**, **JSONL for diagnostic logs (grep/tail/tooling matters)** — see
`docs/design-choices.md`.

---

# Part A — migrate the event/state store to SQLite

## A.0 Decisions
- **SQLite, not Postgres.** `sqlite3` is Python **stdlib** — zero new dependency, single portable
  file, no server, honours the files-first ethos. Postgres would couple the portable multi-project
  daemon to an external server for a few-hundred-events workload: rejected.
- **Per-project DB file:** `projects/<project>/state.db` (mirrors today's per-project isolation;
  reset/inspect one project without touching others; no cross-project write contention).
- **Keep `storage.py`'s public API** (`append_and_apply`, `load_state`, `list_states`,
  `iter_events`, `replay`, `append_event`, `save_state`) — reimplement the *backend*, keep the
  *interface*, so daemon/wrapper/render/doctor callers are unchanged. This is the blast-radius
  control: it's a backend swap, not a rewrite.
- **WAL mode** (`PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=…`): one writer +
  concurrent readers, multi-process safe (daemon + detached wrapper subprocesses both write).

## A.1 Schema
```sql
CREATE TABLE events (               -- append-only WAL
  seq INTEGER PRIMARY KEY AUTOINCREMENT,   -- replaces the hand-rolled _last_sequence
  schema_version INTEGER NOT NULL,
  ts TEXT NOT NULL,                        -- UTC iso (types.iso)
  actor_kind TEXT, actor_id TEXT,
  type TEXT NOT NULL,
  payload TEXT NOT NULL,                   -- JSON
  task_id TEXT, attempt_id TEXT, wave_id TEXT, decision_id TEXT
);
CREATE INDEX events_task ON events(task_id);
CREATE INDEX events_type ON events(type);
CREATE TABLE states (               -- the materialized projection (was statefiles)
  task_id TEXT PRIMARY KEY, project TEXT NOT NULL, state TEXT NOT NULL,
  since TEXT, handoff_path TEXT, notes TEXT,
  attempts TEXT NOT NULL,                  -- JSON list
  schema_version INTEGER NOT NULL
);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);   -- db schema_version etc.
```

## A.2 The transactional mutation (the whole point)
`append_and_apply` becomes one transaction:
```
BEGIN IMMEDIATE;
  INSERT INTO events(...);                  -- the WAL record
  <apply_event projection> -> UPSERT states(...) for each affected task;
COMMIT;
```
Event append and projection update are now atomic — **they cannot diverge**. `save_state`'s
manual flock/tmp/rename disappears (WAL handles durability + concurrency). `_validate_before_append`
runs inside the transaction before the INSERT.

## A.3 Phases
- **SP01 — SQLite backend behind the existing API.** New `storage_sqlite` (or reimplement
  `storage`): schema init, WAL pragmas, transactional `append_and_apply`, `load_state`/`list_states`
  (SELECT), `iter_events` (SELECT ORDER BY seq), `replay` (rebuild from events table — now an
  audit that should *always* match). Behind a config/env flag so it can land dark.
  **Oracles:** append+apply is atomic (a simulated failure mid-transaction leaves neither the
  event nor the projection change — the divergence a file store *can* exhibit is impossible here);
  the full existing storage test-suite passes against the SQLite backend unchanged (API parity);
  concurrent writer+reader under WAL (a reader sees a consistent snapshot); `seq` is gap-free
  monotonic. **Gate:** full.
- **SP02 — importer + verification.** `nyxloom migrate-store <project>`: read the existing
  `events.jsonl`, insert all events into the SQLite `events` table in order, rebuild `states`,
  and **verify** the rebuilt projection equals the current on-disk statefiles (reuse `doctor`'s
  `_replayable_projection` diff as the acceptance test). Rename `events.jsonl` →
  `events.jsonl.pre-sqlite` (keep as backup, never delete). Idempotent (re-run is a no-op if the
  DB is already current). **Oracles:** a real project's `events.jsonl` imports with **zero**
  divergence vs its statefiles; re-run is a no-op; the backup is preserved; a corrupt/partial
  source line is reported, not silently dropped. **Gate:** full (+ a dry-run against a copy of
  the live nyxloom project's event log as evidence).
- **SP03 — cutover + doctor simplification.** Flip the daemon default to SQLite; run the importer
  for all three registered projects; keep `.pre-sqlite` backups. The `doctor` replay-divergence
  check becomes a cheap invariant (transactional store can't diverge) — downgrade to a light
  integrity check (every event's `task_id` has a `states` row; state-machine legality) rather than
  a full re-derive-and-diff. Orphan-statefile check → a DB referential check. **Oracles:** daemon
  runs a full pass against SQLite; doctor is green; the pre-sqlite backups exist; a restart reads
  state from the DB (not the backup). **Gate:** full + **redeploy** (daemon-core change → restart;
  no image rebuild — sqlite3 is stdlib).
- **SP04 — greppability bridge.** `nyxloom events <project> [--tail] [--since SEQ] [--json]` dumps
  the event table as JSONL to stdout, restoring `| jq` / `| lnav` over the (now-DB) event log.
  Preserves the one real thing SQLite costs us. **Oracle:** the dump round-trips to the same
  records `iter_events` yields; `--tail` follows new appends. **Gate:** full.

---

# Part B — ground-truth re-baseline (the `resync` CLI verb)

## B.0 What & why
A verb — **`nyxloom resync <project>`** (distinct name from the daemon's *reconcile* loop) — that
compares each task's nyxloom-believed state against **ground truth** (the trove + git) and
advances/retires stale states via **audited event transitions**. Run it before resuming a
project that advanced manually. It doubles as the safe **on-ramp for onboarding an
already-advanced project** (e.g. registering `netcup-api-filter`, which today has *no* nyxloom
state — resync imports its reality instead of assuming greenfield).

## B.1 Ground-truth sources (per task)
- **Handoff presence** — is `handoff_path` still in the trove (frontmatter readable)? (archived/
  removed ⇒ likely completed or dropped.)
- **Merge state** — is the task's branch merged into `main`? (git `branch --merged` *and* a
  content check: does `main` contain the merge commit / the handoff's archived path under
  `docs/archive`?) — more robust than `_merged_branches`, which this reuses and hardens.
- **Statefile belief** — the current `TaskState`.

## B.2 Decision table (belief × ground-truth → action)
| nyxloom believes | ground truth | resync proposes |
|---|---|---|
| `MERGE_READY` / `ACTIVE` / `AWAITING_REVIEW` | branch merged + handoff archived | → `MERGED`/`COMPLETED` (the dstdns-P30 / ui-P10 case) |
| `QUEUED` / `CARVED` | branch merged (work already done) | → `MERGED`/`COMPLETED` |
| any non-terminal | handoff gone, **not** merged | flag `NEEDS_OPERATOR` (retire? cancel?) — never silently drop |
| statefile exists | no handoff, no merge, no code | flag orphan for operator |
| handoff exists | no statefile | leave to normal carve/dispatch (not resync's job) |

## B.3 How it acts (audited, never a silent statefile edit)
Every advance is a real domain event through `append_and_apply` — `TASK_TRANSITIONED` /
`TASK_SUPERSEDED` / `TASK_CANCELLED` with `actor = (RESYNC, "resync")` and a `reason` payload
naming the ground-truth evidence (e.g. `"branch feat/dstdns-P30 merged into main @<sha>"`). So the
re-baseline is itself in the event log, replayable and inspectable — not a magic state poke.

## B.4 Phases
- **RP01 — probe + dry-run (default).** A pure-ish `resync_plan(states, frontmatters, git_facts)
  → list[ProposedTransition]` + `nyxloom resync <project>` printing a table (task, believed,
  ground-truth, proposed action, evidence). No writes. **Oracles:** the dstdns fixture (P30/P10
  merged+archived) yields `→ COMPLETED`; a genuinely-open `QUEUED` with no merge yields no action;
  an orphan (statefile, no handoff, no merge) yields a `NEEDS_OPERATOR` flag; merge detection
  catches a squash/CAS merge (content check, not just `--merged`). **Gate:** full.
- **RP02 — `--apply`.** Emit the audited transitions via `append_and_apply`; `--apply` gated
  behind the dry-run (must show the plan first). **Oracles:** applying advances the statefile
  **and** writes the `TASK_TRANSITIONED` event with the `resync` actor + evidence reason; a second
  `resync` is a no-op (idempotent — nothing left drifted); paused projects can be resynced (this is
  an operator verb, not a daemon dispatch, so it is *allowed* while paused — that's the point).
  **Gate:** full.
- **RP03 — pre-resume guard + docs.** Wire a check so resuming a project surfaces "N tasks
  drifted — run `resync` first" if resync has pending changes; document the verb in the operator
  guide. Then **resync dstdns and topos for real** (dry-run → review → apply) so they are safe to
  resume. **Oracle:** post-resync, dstdns/topos `doctor` shows no orphan-statefile findings and no
  believed-`MERGE_READY`-already-merged tasks. **Gate:** full + operator review of the applied diff.

---

## Sequencing note (flexibility)
Part B uses only the `storage.py` API, so it is **backend-agnostic** — it would run on today's
JSONL store too. The agreed order does A first (transactional substrate), but if resuming
dstdns/topos becomes urgent before A lands, RP01–RP03 can be pulled forward onto the current store
without rework (they benefit from A's atomicity but don't require it). Logging (`plan-logging.md`)
follows both.

## Risks
- **Migration correctness (SP02)** — mitigated by the zero-divergence acceptance test against real
  event logs + keeping `.pre-sqlite` backups.
- **Multi-process SQLite** — mitigated by WAL + `busy_timeout`; the wrapper subprocesses already
  serialize task writes via the per-task flock today, so contention is low.
- **resync mis-advancing a task** — mitigated by dry-run-default, audited transitions (reversible
  via a further event), and operator review before `--apply` on real projects.
